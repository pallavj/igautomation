# InstaFlow – Instagram Automation

A self-hosted web app that lets up to 5 clients upload photos/videos, auto-edit them, auto-generate captions, and post to Instagram with one click.

---

## What it does

| Step | What happens |
|------|--------------|
| 1 | Client visits their unique private URL |
| 2 | One-time Instagram OAuth (connects their Creator account) |
| 3 | Client sets preferences (filter, crop, caption tone, Reel duration…) |
| 4 | Client uploads a photo or video + writes a brief |
| 5 | App auto-edits the photo (brightness, contrast, warmth, filter, crop) |
| 6 | App converts video to 9:16 Reels format using FFmpeg |
| 7 | Claude generates a caption based on the brief + preferences |
| 8 | Client reviews the preview, edits caption if needed, clicks **Approve** |
| 9 | App posts to Instagram via the Graph API |

---

## Requirements

- Python 3.10+
- ffmpeg (for video processing)
- A **Meta Developer App** with the Instagram product added
- An **Anthropic API key**
- Each client needs an **Instagram Creator or Business account**
- A **public HTTPS URL** (required by Instagram for OAuth and media delivery)
  - For local testing: [ngrok](https://ngrok.com/) (`ngrok http 5000`)
  - For production: any VPS / cloud server behind a domain

---

## Quick start

```bash
# 1. Clone / download the project
cd instagram-automation

# 2. Run setup
chmod +x setup.sh && ./setup.sh

# 3. Fill in your credentials
nano .env

# 4. Start the server
source venv/bin/activate
python app.py

# 5. Open the admin panel
open http://localhost:5000/admin
```

---

## Setting up your Meta App

1. Go to [developers.facebook.com](https://developers.facebook.com/) → Create App → Business type
2. Add the **Instagram** product
3. Under Instagram → Settings, add your **redirect URI**: `https://yourdomain.com/auth/callback`
4. Required permissions: `instagram_business_basic`, `instagram_business_content_publish`
5. Copy your **App ID** and **App Secret** into `.env`

> During development, add yourself as a Test User in the Meta App dashboard.

---

## Environment variables (`.env`)

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Random string for Flask sessions |
| `BASE_URL` | Your public URL (no trailing slash) |
| `META_APP_ID` | From Meta Developer App |
| `META_APP_SECRET` | From Meta Developer App |
| `ANTHROPIC_API_KEY` | From console.anthropic.com |

---

## Client flow

1. You create a client in the Admin panel (`/admin`)
2. The app gives you a unique URL like `/client/abc123xyz…`
3. You send that URL to the client — it's their permanent portal
4. They connect Instagram once (OAuth button on their dashboard)
5. They set preferences once (or update anytime)
6. From then on: upload → review → click Approve → posted

---

## Image editing options

- **Brightness** (0.5–1.5)
- **Contrast** (0.5–1.5)
- **Saturation** (0.5–1.5)
- **Colour temperature**: Neutral / Warm / Cool
- **Style filter**: None / Vivid / Matte / Mono (B&W)
- **Crop ratio**: 4:5 Portrait / 1:1 Square / Original

## Reels options

- **Max duration**: 5–90 seconds
- **Trim strategy**: Auto-trim OR flag for manual trimming
- Output: H.264 + AAC, 1080×1920 (9:16), 30fps

## Caption options

- **Tone**: Casual / Professional / Fun / Inspirational
- **Length**: Short / Medium / Long (story)
- **Hashtags**: On/Off
- **Emojis**: On/Off

---

## Deploying to production

Any Linux server works. Recommended stack:

```
nginx (reverse proxy) → gunicorn → app.py
```

Install gunicorn: `pip install gunicorn`
Run: `gunicorn -w 4 -b 0.0.0.0:5000 app:app`

Make sure `BASE_URL` in `.env` matches your public HTTPS domain, as Instagram needs to fetch your media files to publish them.
