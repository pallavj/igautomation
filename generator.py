"""Caption generation using the Anthropic Claude API."""
import anthropic
import os

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

TONE_GUIDES = {
    "casual":        "Write in a relaxed, friendly, conversational tone. Like texting a friend.",
    "professional":  "Write in a polished, authoritative tone. Clear, confident, no fluff.",
    "fun":           "Be playful, witty, and a little cheeky. Make people smile.",
    "inspirational": "Be motivating and uplifting. Use evocative language that moves people.",
}

LENGTH_GUIDES = {
    "short":  "Keep the caption under 50 words.",
    "medium": "Aim for 60–120 words.",
    "long":   "Write 130–220 words — tell a story.",
}


def generate_caption(brief: str, prefs: dict) -> str:
    """
    Generate an Instagram caption from a brief and client preferences.
    Returns the caption string.
    """
    tone = prefs.get("caption_tone", "casual")
    length = prefs.get("caption_length", "medium")
    use_hashtags = prefs.get("caption_hashtags", True)
    use_emoji = prefs.get("caption_emoji", True)

    hashtag_instr = (
        "End with 5–10 relevant hashtags on a new line."
        if use_hashtags else
        "Do NOT include any hashtags."
    )
    emoji_instr = (
        "Use a few relevant emojis naturally within the text."
        if use_emoji else
        "Do NOT use any emojis."
    )

    prompt = f"""You are an expert Instagram content writer.

Write an Instagram caption based on the brief below.

Tone: {TONE_GUIDES.get(tone, TONE_GUIDES["casual"])}
Length: {LENGTH_GUIDES.get(length, LENGTH_GUIDES["medium"])}
Hashtags: {hashtag_instr}
Emojis: {emoji_instr}

Brief:
{brief}

Output ONLY the caption text. No preamble, no explanation."""

    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text.strip()


def extract_title_quote(brief: str) -> tuple[str, str]:
    """
    Extract a short post title and a punchy one-line quote from a brief.
    Used to add clean Pillow-rendered text overlays to generated images.

    Returns (title, quote). Both strings safe to render as image overlay text.
    Returns ('', '') if brief is empty or extraction fails.

    Title: 2–5 words, impactful, can end with a period.
    Quote: single line, max 55 chars, a hook or statement from the brief.
    """
    if not brief or not brief.strip():
        return ("", "")

    prompt = f"""You are a social media content designer.
Given the post brief below, extract TWO pieces of text to overlay on an Instagram image.

Rules:
- TITLE: 2–5 words. Bold and impactful. Can end with a period. No quotation marks.
- QUOTE: One single line, max 55 characters. A punchy statement or hook from the brief. No hashtags.

Output EXACTLY in this format with no other text:
TITLE: <title here>
QUOTE: <quote here>

Brief:
{brief[:600]}"""

    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=80,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        title, quote = "", ""
        for line in text.splitlines():
            if line.startswith("TITLE:"):
                title = line.replace("TITLE:", "").strip().strip('"\'')
            elif line.startswith("QUOTE:"):
                quote = line.replace("QUOTE:", "").strip().strip('"\'')
        return (title[:60], quote[:60])
    except Exception:
        return ("", "")
