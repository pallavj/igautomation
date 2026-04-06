"""AI image generation using DALL-E 3 (OpenAI)."""
import os
import uuid
import requests as http_requests
from openai import OpenAI

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
    return _client


# Photorealism-first style descriptors.
# Key rule: NEVER ask DALL-E to render text — it can't do it reliably.
# Text is added separately by Pillow after generation.
STYLE_PROMPTS = {
    "vivid": (
        "vibrant lifestyle photograph, bold saturated colours, "
        "professional camera, sharp focus, authentic real people"
    ),
    "natural": (
        "candid authentic photograph, soft natural window light, "
        "shot on iPhone 15 Pro, real life moment, no filters, "
        "genuine unposed expression"
    ),
    "editorial": (
        "high-end editorial photography, clean minimal composition, "
        "professional studio or natural light, magazine quality, "
        "sharp and polished"
    ),
    "cinematic": (
        "cinematic photography, moody atmospheric light, "
        "golden hour or blue hour, shallow depth of field, "
        "film photography aesthetic, dramatic but real"
    ),
    "flat": (
        "flat lay photography, overhead bird's-eye shot, "
        "clean neutral surface, soft natural daylight, "
        "product styling, precisely arranged"
    ),
}

# Appended to every prompt regardless of style
_NO_TEXT = (
    "No text, no words, no letters, no numbers, no watermarks, "
    "no captions, no typography anywhere in the image."
)


def generate_image(prompt: str, style: str = "vivid", save_folder: str = None) -> str:
    """
    Generate an image from a text prompt using DALL-E 3.
    Returns the saved filename (not full path).

    The prompt is intentionally kept free of any text instructions —
    text overlays are added afterwards by processor.add_image_text_overlay().
    """
    from processor import UPLOAD_FOLDER
    folder = save_folder or UPLOAD_FOLDER
    os.makedirs(folder, exist_ok=True)

    style_desc = STYLE_PROMPTS.get(style, STYLE_PROMPTS["vivid"])

    # Build a clean photorealism-first prompt
    full_prompt = (
        f"Photograph: {prompt}. "
        f"{style_desc}. "
        f"Aspect ratio 4:5, Instagram portrait format. "
        f"{_NO_TEXT}"
    )

    client = _get_client()
    response = client.images.generate(
        model="dall-e-3",
        prompt=full_prompt,
        size="1024x1024",
        quality="hd",          # upgraded from "standard" for sharper results
        style="natural",       # always use "natural" to avoid over-illustrated look
        n=1,
    )

    image_url = response.data[0].url
    img_data = http_requests.get(image_url, timeout=30).content

    filename = f"generated_{uuid.uuid4().hex}.jpg"
    save_path = os.path.join(folder, filename)
    with open(save_path, "wb") as f:
        f.write(img_data)

    return filename
