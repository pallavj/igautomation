"""Weekly content strategy generation using Claude."""
import json
import anthropic
import os

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

DAYS = ["Monday", "Wednesday", "Friday"]  # 3 posts per week


def generate_strategy(objectives: str, business_context: str, client_name: str) -> str:
    """
    Generate a weekly content strategy narrative from business objectives.
    Returns the strategy text (markdown-friendly).
    """
    prompt = f"""You are an expert Instagram content strategist.

A client called "{client_name}" has shared their weekly objectives and business context below.
Write a clear, actionable weekly content strategy for Instagram — 3 posts this week.

The strategy should cover:
- The overarching weekly theme and why it fits their objectives
- The content angles and approach for the week
- What types of posts will work best and why
- Any specific messaging pillars to emphasise

Keep it concise (200-300 words), practical, and specific to their business. No fluff.

Business Context:
{business_context or "Not provided"}

Weekly Objectives:
{objectives}

Write the strategy now."""

    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text.strip()


def generate_checklist(objectives: str, business_context: str,
                        strategy_text: str, client_name: str) -> list[dict]:
    """
    Generate 3 specific content plan items from the strategy.
    Returns a list of dicts with keys: day, title, description, brief, suggested_type.
    """
    prompt = f"""You are an expert Instagram content strategist.

Based on the weekly strategy below, create exactly 3 specific, actionable post ideas for Instagram.
One post for each of: Monday, Wednesday, Friday.

Each post idea must be immediately actionable — the client should know exactly what to create.

Return ONLY a valid JSON array with exactly 3 objects. Each object must have:
- "day": one of "Monday", "Wednesday", "Friday"
- "title": short punchy post title (max 8 words)
- "description": what this post is about and why it works (2-3 sentences)
- "brief": the exact brief to pass to the caption writer and image creator (2-4 sentences, specific and descriptive — mention visuals, tone, key message)
- "suggested_type": one of "image", "video", "generated" (use "generated" if no real photo is needed and AI can create it)

Client: {client_name}
Business Context: {business_context or "Not provided"}
Objectives: {objectives}

Strategy:
{strategy_text}

Return ONLY the JSON array, no other text."""

    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    items = json.loads(raw)

    # Ensure exactly 3 items with correct days
    for i, item in enumerate(items[:3]):
        item["day"] = DAYS[i]

    return items[:3]
