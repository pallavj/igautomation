"""
Translate natural language requests into processor/FFmpeg parameter overrides.
Uses Claude Haiku for fast, cheap inference.
"""
import json
import re
import anthropic

_client = anthropic.Anthropic()


def translate_edit_request(request: str, current_prefs: dict) -> dict:
    """
    Given a natural language edit request and the current processing prefs,
    return a dict of parameter overrides to merge before reprocessing.

    Only keys that need to change are returned.
    """
    prompt = f"""You translate a user's photo edit request into JSON parameter overrides for an image processor.

Current settings:
- brightness: {current_prefs.get('image_brightness', 1.0)} (range 0.5–1.5, 1.0 = neutral)
- contrast: {current_prefs.get('image_contrast', 1.0)} (range 0.5–1.5, 1.0 = neutral)
- saturation: {current_prefs.get('image_saturation', 1.0)} (range 0.5–1.5, 1.0 = neutral)
- warmth: {current_prefs.get('image_warmth', 'neutral')} (options: neutral, warm, cool)
- filter: {current_prefs.get('image_filter', 'none')} (options: none, vivid, matte, mono)
- crop: {current_prefs.get('image_crop', '4:5')} (options: 4:5, 1:1, original)

User's request: "{request}"

Return ONLY a JSON object containing just the keys that should change.
Key names must be exactly: image_brightness, image_contrast, image_saturation, image_warmth, image_filter, image_crop

Examples:
"make it brighter"                    → {{"image_brightness": 1.35}}
"a bit more contrast"                 → {{"image_contrast": 1.25}}
"black and white"                     → {{"image_filter": "mono"}}
"warmer tones"                        → {{"image_warmth": "warm"}}
"don't crop, show the whole image"    → {{"image_crop": "original"}}
"square crop"                         → {{"image_crop": "1:1"}}
"less cropped and warmer"             → {{"image_crop": "original", "image_warmth": "warm"}}
"more vivid and saturated"            → {{"image_filter": "vivid", "image_saturation": 1.3}}
"softer, more matte look"             → {{"image_filter": "matte", "image_saturation": 0.8}}
"darker and more dramatic"            → {{"image_brightness": 0.8, "image_contrast": 1.3}}
"reset to neutral"                    → {{"image_brightness": 1.0, "image_contrast": 1.0, "image_saturation": 1.0, "image_warmth": "neutral", "image_filter": "none"}}

JSON only, no explanation:"""

    message = _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )

    text = message.content[0].text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {}


def translate_video_vibe(vibe: str) -> dict:
    """
    Translate a natural language vibe/feel prompt into FFmpeg color grading parameters.
    Returns a dict of filter settings consumed by process_video().
    """
    prompt = f"""You translate a video "vibe" or "feel" description into FFmpeg color grading parameters.

Vibe description: "{vibe}"

Return ONLY a JSON object with any of these keys that differ from defaults:
- eq_brightness: float, -0.3 to 0.3 (0 = neutral, positive = brighter)
- eq_contrast: float, 0.7 to 1.5 (1 = neutral, higher = more contrast)
- eq_saturation: float, 0.0 to 2.5 (1 = neutral, 0 = black & white, higher = vivid)
- eq_gamma: float, 0.7 to 1.4 (1 = neutral, lower = darker shadows, higher = lifted/airy)
- hue_shift: int, -30 to 30 (0 = no shift, positive = warmer/yellower, negative = cooler/bluer)
- vignette: bool (true = dark edges for cinematic look)
- grain: float, 0 to 0.05 (0 = no grain, 0.03 = subtle film grain)
- speed: float, 0.75 to 1.5 (1 = normal, 0.8 = slow-mo feel, 1.25 = energetic)

Examples:
"golden hour, warm and dreamy"   → {{"eq_brightness": 0.05, "eq_saturation": 1.2, "eq_gamma": 1.1, "hue_shift": 12, "vignette": true}}
"energetic gym reel, punchy"     → {{"eq_contrast": 1.3, "eq_saturation": 1.4, "speed": 1.15}}
"moody dark cinematic"           → {{"eq_brightness": -0.1, "eq_contrast": 1.25, "eq_saturation": 0.85, "eq_gamma": 0.85, "vignette": true, "grain": 0.025}}
"soft airy and pastel"           → {{"eq_brightness": 0.1, "eq_contrast": 0.85, "eq_saturation": 0.7, "eq_gamma": 1.2}}
"black and white editorial"      → {{"eq_saturation": 0.0, "eq_contrast": 1.2, "grain": 0.02}}
"vibrant tropical"               → {{"eq_saturation": 1.8, "eq_brightness": 0.05, "hue_shift": 5}}
"slow-mo beach vibes"            → {{"eq_brightness": 0.08, "eq_saturation": 1.1, "hue_shift": 8, "speed": 0.8}}
"cool blue minimal"              → {{"eq_saturation": 0.8, "eq_contrast": 1.1, "hue_shift": -12}}

JSON only, no explanation:"""

    message = _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )

    text = message.content[0].text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {}


def generate_video_tagline(brief: str, vibe: str = "") -> str:
    """
    Generate a short punchy text overlay for a video from the post brief.
    Returns a 1–2 line tagline (max ~8 words per line) suitable for burning into a Reel.
    """
    vibe_note = f" The video vibe is: {vibe}." if vibe else ""
    prompt = f"""Write a short, punchy text overlay for an Instagram Reel.{vibe_note}

Post brief: "{brief}"

Rules:
- Maximum 8 words per line, 1 or 2 lines total
- No hashtags, no punctuation at the end
- Impactful, scroll-stopping, conversational
- Could be a question, a bold statement, or a hook
- Do NOT include quotation marks in your answer

Examples of good overlays:
"Ever felt more alive near the sea"
"This changed my morning routine"
"You need to try this"
"The best kept secret in Goa"
"Why your skin needs this now"

Reply with ONLY the overlay text, nothing else:"""

    message = _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=80,
        messages=[{"role": "user", "content": prompt}],
    )

    return message.content[0].text.strip().strip('"').strip("'")
