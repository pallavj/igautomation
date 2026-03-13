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


def process_video(original_path: str, prefs: dict) -> str:
    """
    Convert a video to Instagram Reels format (9:16, 1080x1920, H.264+AAC).
    Trims to max_duration if needed.
    Returns the filename (not full path) of the processed video.
    """
    max_dur = min(int(prefs.get("reel_max_duration", 60)), 90)
    trim_strategy = prefs.get("reel_trim_strategy", "trim")

    duration = get_video_duration(original_path)

    if duration > max_dur and trim_strategy == "flag":
        raise ValueError(
            f"Video is {duration:.0f}s but max is {max_dur}s. "
            "Please trim the video before uploading."
        )

    out_name = f"reel_{uuid.uuid4().hex}.mp4"
    out_path = os.path.join(PROCESSED_FOLDER, out_name)

    # Build ffmpeg filter:
    # 1. Scale so the shortest side fills 1080/1920
    # 2. Pad to exactly 1080x1920 with black bars if needed (keeps aspect ratio)
    vf = (
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", original_path,
    ]

    if duration > max_dur:
        cmd += ["-t", str(max_dur)]

    cmd += [
        "-vf", vf,
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
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg error: {result.stderr[-500:]}")

    return out_name
