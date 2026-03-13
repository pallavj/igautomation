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
