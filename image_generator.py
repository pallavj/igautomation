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


STYLE_PROMPTS = {
    "vivid":      "vivid, bold colours, high contrast, professionally lit",
    "natural":    "natural lighting, realistic, true-to-life",
    "editorial":  "editorial photography style, magazine quality, clean and minimal",
    "cinematic":  "cinematic, moody, film grain, dramatic lighting",
    "flat":       "flat lay photography, overhead shot, clean white background, product styling",
}


def generate_image(prompt: str, style: str = "vivid", save_folder: str = None) -> str:
    """
    Generate an image from a text prompt using DALL-E 3.
    Returns the saved filename (not full path).
    """
    from processor import UPLOAD_FOLDER
    folder = save_folder or UPLOAD_FOLDER
    os.makedirs(folder, exist_ok=True)

    style_suffix = STYLE_PROMPTS.get(style, "")
    full_prompt = f"{prompt}. {style_suffix}. Square format, suitable for Instagram."

    client = _get_client()
    response = client.images.generate(
        model="dall-e-3",
        prompt=full_prompt,
        size="1024x1024",
        quality="standard",
        style="vivid" if style in ("vivid", "cinematic", "editorial") else "natural",
        n=1,
    )

    image_url = response.data[0].url

    # Download and save the image locally
    img_data = http_requests.get(image_url, timeout=30).content
    filename = f"generated_{uuid.uuid4().hex}.jpg"
    save_path = os.path.join(folder, filename)
    with open(save_path, "wb") as f:
        f.write(img_data)

    return filename
