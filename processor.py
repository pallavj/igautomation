"""Image editing and video-to-Reels processing."""
import os
import subprocess
import uuid
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

_base = os.path.dirname(__file__)
UPLOAD_FOLDER    = os.environ.get("UPLOAD_FOLDER",    os.path.join(_base, "static", "uploads"))
PROCESSED_FOLDER = os.environ.get("PROCESSED_FOLDER", os.path.join(_base, "static", "processed"))

# Ensure directories exist at import time
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(PROCESSED_FOLDER, exist_ok=True)

ALLOWED_IMAGE = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_VIDEO = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}

# Instagram Reels: 1080x1920, max 90s, H.264, AAC
REEL_WIDTH = 1080
REEL_HEIGHT = 1920

# Instagram image ratios
CROP_RATIOS = {
    "1:1":      (1, 1),
    "4:5":      (4, 5),
    "original": None,
}


def _ext(filename: str) -> str:
    return os.path.splitext(filename)[1].lower()


def is_image(filename: str) -> bool:
    return _ext(filename) in ALLOWED_IMAGE


def is_video(filename: str) -> bool:
    return _ext(filename) in ALLOWED_VIDEO


# ── Image processing ──────────────────────────────────────────────────────────

def apply_warmth(img: Image.Image, warmth: str) -> Image.Image:
    """Shift colour temperature."""
    if warmth == "warm":
        r, g, b = img.split() if img.mode == "RGB" else img.convert("RGB").split()
        r = r.point(lambda x: min(255, int(x * 1.08)))
        b = b.point(lambda x: max(0, int(x * 0.92)))
        return Image.merge("RGB", (r, g, b))
    elif warmth == "cool":
        r, g, b = img.split() if img.mode == "RGB" else img.convert("RGB").split()
        r = r.point(lambda x: max(0, int(x * 0.92)))
        b = b.point(lambda x: min(255, int(x * 1.08)))
        return Image.merge("RGB", (r, g, b))
    return img


def apply_filter(img: Image.Image, filter_name: str) -> Image.Image:
    """Apply a stylistic filter."""
    if filter_name == "vivid":
        img = ImageEnhance.Color(img).enhance(1.4)
        img = ImageEnhance.Contrast(img).enhance(1.1)
    elif filter_name == "matte":
        img = ImageEnhance.Color(img).enhance(0.8)
        img = ImageEnhance.Brightness(img).enhance(1.05)
        # Lift shadows slightly
        img = img.point(lambda x: int(x * 0.85 + 30))
    elif filter_name == "mono":
        img = ImageOps.grayscale(img).convert("RGB")
    return img


def crop_to_ratio(img: Image.Image, ratio: str) -> Image.Image:
    """Centre-crop image to the given ratio string."""
    dims = CROP_RATIOS.get(ratio)
    if dims is None:
        return img
    rw, rh = dims
    w, h = img.size
    target_ratio = rw / rh
    current_ratio = w / h
    if current_ratio > target_ratio:
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    else:
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))
    return img


def process_image(original_path: str, prefs: dict) -> str:
    """
    Apply client preferences to an image.
    Returns the filename (not full path) of the processed image.
    """
    img = Image.open(original_path).convert("RGB")

    # Crop first so edits apply to final composition
    img = crop_to_ratio(img, prefs.get("image_crop", "4:5"))

    # Tone adjustments
    img = ImageEnhance.Brightness(img).enhance(prefs.get("image_brightness", 1.0))
    img = ImageEnhance.Contrast(img).enhance(prefs.get("image_contrast", 1.0))
    img = ImageEnhance.Color(img).enhance(prefs.get("image_saturation", 1.0))

    # Warmth
    img = apply_warmth(img, prefs.get("image_warmth", "neutral"))

    # Stylistic filter
    img = apply_filter(img, prefs.get("image_filter", "none"))

    # Mild sharpening always helps after resize
    img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=80, threshold=3))

    out_name = f"processed_{uuid.uuid4().hex}.jpg"
    out_path = os.path.join(PROCESSED_FOLDER, out_name)
    img.save(out_path, "JPEG", quality=92, optimize=True)
    return out_name


# ── Video processing ──────────────────────────────────────────────────────────

# ── Multi-clip stitching ───────────────────────────────────────────────────────

def _normalize_clip(path: str, max_dur: int = 0) -> str:
    """
    Normalize a single clip to 1080x1920 H.264 30fps AAC.
    Optionally trims to max_dur seconds.
    Returns path to a temp file in PROCESSED_FOLDER.
    """
    out_path = os.path.join(PROCESSED_FOLDER, f"_norm_{uuid.uuid4().hex}.mp4")
    cmd = ["ffmpeg", "-y", "-i", path]
    if max_dur and max_dur > 0:
        cmd += ["-t", str(max_dur)]
    cmd += [
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k", "-r", "30",
        out_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg normalize error: {result.stderr[-300:]}")
    return out_path


def _stitch_concat(paths: list, fade: bool = False) -> str:
    """
    Concat clips via FFmpeg concat demuxer.
    fade=True adds a 0.5s fade-to-black at the end of each clip before joining.
    Returns path to stitched temp file.
    """
    if fade:
        faded = []
        try:
            for p in paths:
                dur = get_video_duration(p)
                out_path = os.path.join(PROCESSED_FOLDER, f"_fade_{uuid.uuid4().hex}.mp4")
                fade_st = max(0.0, dur - 0.5)
                cmd = [
                    "ffmpeg", "-y", "-i", p,
                    "-vf", f"fade=t=out:st={fade_st:.3f}:d=0.5",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                    "-c:a", "aac", "-b:a", "128k",
                    out_path,
                ]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    raise RuntimeError(f"FFmpeg fade error: {result.stderr[-300:]}")
                faded.append(out_path)
            paths = faded
        except Exception:
            for p in faded:
                try: os.remove(p)
                except OSError: pass
            raise

    list_path = os.path.join(PROCESSED_FOLDER, f"_list_{uuid.uuid4().hex}.txt")
    out_path  = os.path.join(PROCESSED_FOLDER, f"_stitched_{uuid.uuid4().hex}.mp4")
    try:
        with open(list_path, "w") as fh:
            for p in paths:
                fh.write(f"file '{p}'\n")
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
               "-i", list_path, "-c", "copy", out_path]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg concat error: {result.stderr[-300:]}")
    finally:
        try: os.remove(list_path)
        except OSError: pass
        if fade:
            for p in paths:
                try: os.remove(p)
                except OSError: pass

    return out_path


def _stitch_xfade(paths: list, xfade_dur: float = 0.5) -> str:
    """
    Stitch clips with smooth crossfade transitions using FFmpeg xfade filter.
    Returns path to stitched temp file.
    """
    if len(paths) == 1:
        return paths[0]

    durations = [get_video_duration(p) for p in paths]
    cmd = ["ffmpeg", "-y"]
    for p in paths:
        cmd += ["-i", p]

    # Build chained xfade for video
    filter_parts = []
    prev_label  = "[0:v]"
    cumulative  = 0.0
    for i in range(1, len(paths)):
        cumulative += durations[i - 1] - xfade_dur
        out_label = "[vout]" if i == len(paths) - 1 else f"[v{i}]"
        filter_parts.append(
            f"{prev_label}[{i}:v]xfade=transition=fade"
            f":duration={xfade_dur}:offset={cumulative:.3f}{out_label}"
        )
        prev_label = f"[v{i}]"

    # Simple audio concat
    audio_in = "".join(f"[{i}:a]" for i in range(len(paths)))
    filter_parts.append(f"{audio_in}concat=n={len(paths)}:v=0:a=1[aout]")

    out_path = os.path.join(PROCESSED_FOLDER, f"_stitched_{uuid.uuid4().hex}.mp4")
    cmd += [
        "-filter_complex", ";".join(filter_parts),
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k", "-r", "30",
        out_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg xfade error: {result.stderr[-300:]}")
    return out_path


def stitch_videos(clip_paths: list, trim_per_clip: int = 0,
                  transition: str = "cut") -> str:
    """
    Stitch multiple video clips into one video ready for process_video().

    Args:
        clip_paths:    Absolute paths in desired playback order.
        trim_per_clip: Max seconds to use from each clip (0 = full clip).
        transition:    "cut" | "fade" | "crossfade"

    Returns the path to a temp stitched file in PROCESSED_FOLDER.
    """
    if len(clip_paths) == 1:
        return clip_paths[0]

    # Normalise every clip to 1080x1920 H.264 so concat works cleanly
    normalized = []
    try:
        for path in clip_paths:
            normalized.append(_normalize_clip(path, max_dur=trim_per_clip))

        if transition == "crossfade":
            return _stitch_xfade(normalized)
        else:
            return _stitch_concat(normalized, fade=(transition == "fade"))
    finally:
        for p in normalized:
            try: os.remove(p)
            except OSError: pass

def get_video_duration(path: str) -> float:
    """Return duration in seconds using ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def _has_audio(path: str) -> bool:
    """Return True if the video file has at least one audio stream."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
        capture_output=True, text=True
    )
    return "audio" in result.stdout


def _wrap_text(text: str, max_chars: int = 32) -> str:
    """Word-wrap text to max_chars per line."""
    words = text.split()
    lines, current = [], ""
    for word in words:
        test = (current + " " + word).strip()
        if len(test) <= max_chars:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return "\n".join(lines)


def process_video(original_path: str, prefs: dict,
                  vibe_params: dict = None, overlay_text: str = None) -> str:
    """
    Convert a video to Instagram Reels format (9:16, 1080x1920, H.264+AAC).
    Optionally applies colour grading from vibe_params and burns in a text overlay.
    Trims to max_duration if needed.
    Returns the filename (not full path) of the processed video.
    """
    max_dur = min(int(prefs.get("reel_max_duration", 60)), 90)
    trim_strategy = prefs.get("reel_trim_strategy", "trim")
    vibe = vibe_params or {}

    duration = get_video_duration(original_path)

    if duration > max_dur and trim_strategy == "flag":
        raise ValueError(
            f"Video is {duration:.0f}s but max is {max_dur}s. "
            "Please trim the video before uploading."
        )

    out_name = f"reel_{uuid.uuid4().hex}.mp4"
    out_path = os.path.join(PROCESSED_FOLDER, out_name)

    # ── Build video filter chain ───────────────────────────────────────────────
    filters = []

    # 1. Scale + crop to 9:16 Reels format (always applied)
    filters.append(
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920"
    )

    # 2. Colour grading via FFmpeg eq filter
    brightness  = float(vibe.get("eq_brightness", 0.0))
    contrast    = float(vibe.get("eq_contrast", 1.0))
    saturation  = float(vibe.get("eq_saturation", 1.0))
    gamma       = float(vibe.get("eq_gamma", 1.0))
    if brightness != 0.0 or contrast != 1.0 or saturation != 1.0 or gamma != 1.0:
        filters.append(
            f"eq=brightness={brightness:.3f}"
            f":contrast={contrast:.3f}"
            f":saturation={saturation:.3f}"
            f":gamma={gamma:.3f}"
        )

    # 3. Hue shift for warm/cool toning
    hue_shift = int(vibe.get("hue_shift", 0))
    if hue_shift != 0:
        filters.append(f"hue=h={hue_shift}")

    # 4. Cinematic vignette
    if vibe.get("vignette", False):
        filters.append("vignette=PI/5")

    # 5. Subtle film grain  (alls 0–30 mapped from grain 0–0.05)
    grain = float(vibe.get("grain", 0.0))
    if grain > 0:
        strength = max(1, int(grain * 600))   # 0.02 → 12, 0.05 → 30
        filters.append(f"noise=alls={strength}:allf=t+u")

    # 6. Text overlay — positioned in Instagram-safe zone (above bottom 14%)
    textfile_path = None
    if overlay_text:
        wrapped = _wrap_text(overlay_text, max_chars=32)
        textfile_path = os.path.join(
            PROCESSED_FOLDER, f"_txt_{uuid.uuid4().hex}.txt"
        )
        with open(textfile_path, "w") as fh:
            fh.write(wrapped)
        # y=h*0.74 keeps text well above the IG grid crop zone
        filters.append(
            f"drawtext=textfile={textfile_path}"
            ":fontsize=52"
            ":fontcolor=white"
            ":x=(w-text_w)/2"
            ":y=h*0.74"
            ":box=1"
            ":boxcolor=black@0.55"
            ":boxborderw=22"
            ":line_spacing=12"
        )

    # 7. Speed adjustment (setpts)
    speed = float(vibe.get("speed", 1.0))
    speed = max(0.75, min(1.5, speed))   # clamp to safe range
    if speed != 1.0:
        pts_factor = 1.0 / speed
        filters.append(f"setpts={pts_factor:.4f}*PTS")

    vf = ",".join(filters)

    # ── Build ffmpeg command ───────────────────────────────────────────────────
    cmd = ["ffmpeg", "-y", "-i", original_path]

    if duration > max_dur:
        cmd += ["-t", str(max_dur)]

    cmd += ["-vf", vf]

    # Audio speed adjustment (atempo must be 0.5–2.0)
    has_audio = _has_audio(original_path)
    if speed != 1.0 and has_audio:
        cmd += ["-filter:a", f"atempo={speed:.3f}"]
    elif not has_audio:
        cmd += ["-an"]

    cmd += [
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        "-r", "30",
        out_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    # Clean up temp text file
    if textfile_path and os.path.exists(textfile_path):
        try:
            os.remove(textfile_path)
        except OSError:
            pass

    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg error: {result.stderr[-500:]}")

    return out_name

