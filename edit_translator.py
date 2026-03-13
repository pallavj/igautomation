"""
Translate natural language image edit requests into processor parameter overrides.
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
