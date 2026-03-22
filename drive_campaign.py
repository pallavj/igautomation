"""
drive_campaign.py — Standalone Drive-to-Instagram campaign tool.

Runs as a separate Flask app (default port 5001). Does NOT modify
the main InstaFlow app or database at all.

Usage:
  python drive_campaign.py

Then open http://localhost:5001 in your browser.

What it does:
  1. You paste a public Google Drive folder URL + event name + posting window
  2. It scans the folder: reads a guidelines doc + downloads all photos/videos
  3. Processes every file (colour grade, crop, generate caption with guidelines context)
  4. Shows an approval UI: preview each post, edit caption, pick a schedule time
  5. Approve → schedules directly to Instagram via Publer
     Skip → removes from the queue

State is stored in campaign_state.json in the same directory — safe to delete to start fresh.

Environment variables needed (in .env or Render):
  ANTHROPIC_API_KEY  — for caption generation
  GOOGLE_API_KEY     — for Google Drive API access
  PUBLER_API_KEY     — Publer API key
  PUBLER_WORKSPACE_ID
  PUBLER_ACCOUNT_ID  — Publer account ID for the Instagram profile
"""

import json
import math
import os
import sys
import threading
import uuid
from datetime import datetime, timedelta

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template_string, request, url_for, send_file

load_dotenv()

# Reuse modules from the main app (same directory)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import drive_sync as ds
import processor as proc
import generator as gen
import scheduler as sched

# ── Config ─────────────────────────────────────────────────────────────────────

PORT        = int(os.environ.get("CAMPAIGN_PORT", 5001))
STATE_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "campaign_state.json")
WORK_DIR    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "campaign_work")
os.makedirs(WORK_DIR, exist_ok=True)

PUBLER_API_KEY      = os.environ.get("PUBLER_API_KEY", "")
PUBLER_WORKSPACE_ID = os.environ.get("PUBLER_WORKSPACE_ID", "")
PUBLER_ACCOUNT_ID   = os.environ.get("PUBLER_ACCOUNT_ID", "")

# ── App ────────────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "campaign-secret")

# In-memory scan state (cleared on restart; persists to STATE_FILE)
scan_state = {
    "status":   "idle",   # idle | scanning | done | error
    "progress": "",
    "error":    "",
}

# ── State helpers ──────────────────────────────────────────────────────────────

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def state_posts(state: dict) -> list:
    return state.get("posts", [])


def get_post(state: dict, post_id: str) -> dict | None:
    for p in state.get("posts", []):
        if p["id"] == post_id:
            return p
    return None


# ── Schedule time suggestion ───────────────────────────────────────────────────

def suggest_schedule_times(n: int, window_days: int, post_time: str) -> list[str]:
    """
    Return n ISO datetime strings spread evenly over window_days,
    at post_time (HH:MM) each day, starting tomorrow.
    """
    if n == 0:
        return []
    hour, minute = map(int, post_time.split(":"))
    start = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    start += timedelta(days=1)  # start tomorrow

    if n == 1:
        return [start.strftime("%Y-%m-%dT%H:%M")]

    # Spread: interval = window_days / (n-1) days between posts
    interval_hours = (window_days * 24) / max(n - 1, 1)
    times = []
    for i in range(n):
        t = start + timedelta(hours=interval_hours * i)
        times.append(t.strftime("%Y-%m-%dT%H:%M"))
    return times


# ── Background scan ────────────────────────────────────────────────────────────

def run_scan(drive_url: str, event_name: str, window_days: int,
             post_time: str, client_prefs: dict):
    """
    Background thread: downloads + processes all media from Drive folder,
    generates captions, assigns suggested schedule times, writes state file.
    """
    global scan_state
    scan_state["status"]   = "scanning"
    scan_state["progress"] = "Connecting to Google Drive…"

    try:
        # 1. Extract folder ID
        folder_id = ds.extract_folder_id(drive_url)
        if not folder_id:
            raise ValueError(f"Couldn't extract a folder ID from: {drive_url}")

        # 2. List all files
        scan_state["progress"] = "Listing folder contents…"
        files = ds.list_folder_files(folder_id)
        media_files = [f for f in files if ds.is_media(f["mimeType"])]

        if not media_files:
            raise ValueError("No photos or videos found in this Drive folder.")

        # 3. Read guidelines
        scan_state["progress"] = "Reading guidelines document…"
        guidelines = ds.find_guidelines(files)

        # 4. Suggest schedule times
        times = suggest_schedule_times(len(media_files), window_days, post_time)

        # 5. Process each file
        posts = []
        for idx, f in enumerate(media_files):
            brief = os.path.splitext(f["name"])[0]  # filename without extension = brief
            scan_state["progress"] = f"Processing {idx+1}/{len(media_files)}: {brief[:50]}…"

            try:
                ext  = ds.file_extension(f["name"], f["mimeType"])
                orig_name = f"camp_orig_{uuid.uuid4().hex}{ext}"
                orig_path = os.path.join(WORK_DIR, orig_name)

                # Download
                ds.download_file(f["id"], orig_path)

                mtype = ds.media_type(f["mimeType"])

                # Process
                if mtype == "image":
                    processed_name = proc.process_image(orig_path, client_prefs)
                else:
                    processed_name = proc.process_video(orig_path, client_prefs)

                # Caption — weave guidelines in as context
                full_brief = brief
                if guidelines:
                    full_brief = (
                        f"{brief}\n\n"
                        f"[Community guidelines / theme for all posts from this event:\n"
                        f"{guidelines[:800]}]"
                    )
                caption = gen.generate_caption(full_brief, client_prefs)

                posts.append({
                    "id":                   uuid.uuid4().hex,
                    "drive_file_id":        f["id"],
                    "filename":             f["name"],
                    "brief":                brief,
                    "media_type":           mtype,
                    "original_path":        orig_path,
                    "processed_filename":   processed_name,
                    "caption":              caption,
                    "suggested_schedule_at": times[idx] if idx < len(times) else times[-1],
                    "status":               "pending",   # pending | approved | skipped
                    "publer_result":        None,
                })

            except Exception as e:
                # Don't abort whole scan for one bad file
                posts.append({
                    "id":                   uuid.uuid4().hex,
                    "drive_file_id":        f["id"],
                    "filename":             f["name"],
                    "brief":                os.path.splitext(f["name"])[0],
                    "media_type":           ds.media_type(f["mimeType"]),
                    "original_path":        None,
                    "processed_filename":   None,
                    "caption":              "",
                    "suggested_schedule_at": times[idx] if idx < len(times) else "",
                    "status":               "error",
                    "error":                str(e),
                    "publer_result":        None,
                })

        state = {
            "event_name":    event_name,
            "drive_url":     drive_url,
            "guidelines":    guidelines,
            "window_days":   window_days,
            "post_time":     post_time,
            "scanned_at":    datetime.now().isoformat(),
            "posts":         posts,
        }
        save_state(state)
        scan_state["status"]   = "done"
        scan_state["progress"] = f"Done — {len(posts)} posts ready for review."

    except Exception as e:
        scan_state["status"] = "error"
        scan_state["error"]  = str(e)
        scan_state["progress"] = f"Error: {e}"


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    state = load_state()
    pending = [p for p in state_posts(state) if p["status"] == "pending"]
    approved = [p for p in state_posts(state) if p["status"] == "approved"]
    skipped  = [p for p in state_posts(state) if p["status"] == "skipped"]

    has_publer = bool(PUBLER_API_KEY and PUBLER_ACCOUNT_ID)

    return render_template_string(INDEX_HTML,
        state=state,
        pending=pending,
        approved=approved,
        skipped=skipped,
        scan_state=scan_state,
        has_publer=has_publer,
    )


@app.route("/scan", methods=["POST"])
def start_scan():
    drive_url   = request.form.get("drive_url", "").strip()
    event_name  = request.form.get("event_name", "Untitled Event").strip()
    window_days = int(request.form.get("window_days", 7))
    post_time   = request.form.get("post_time", "18:00").strip()

    tone       = request.form.get("caption_tone", "fun")
    length     = request.form.get("caption_length", "medium")
    hashtags   = request.form.get("caption_hashtags") == "on"
    emoji      = request.form.get("caption_emoji") == "on"
    warmth     = request.form.get("image_warmth", "neutral")
    img_filter = request.form.get("image_filter", "none")

    client_prefs = {
        "caption_tone":      tone,
        "caption_length":    length,
        "caption_hashtags":  hashtags,
        "caption_emoji":     emoji,
        "image_brightness":  1.0,
        "image_contrast":    1.0,
        "image_saturation":  1.0,
        "image_warmth":      warmth,
        "image_filter":      img_filter,
        "image_crop":        "4:5",
        "reel_max_duration": 60,
        "reel_trim_strategy":"trim",
    }

    if not drive_url:
        return redirect(url_for("index"))

    global scan_state
    scan_state = {"status": "scanning", "progress": "Starting…", "error": ""}

    thread = threading.Thread(
        target=run_scan,
        args=(drive_url, event_name, window_days, post_time, client_prefs),
        daemon=True,
    )
    thread.start()

    return redirect(url_for("index"))


@app.route("/scan_status")
def scan_status():
    return jsonify(scan_state)


@app.route("/reset", methods=["POST"])
def reset():
    global scan_state
    scan_state = {"status": "idle", "progress": "", "error": ""}
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
    return redirect(url_for("index"))


@app.route("/post/<post_id>/media")
def serve_media(post_id):
    state = load_state()
    post  = get_post(state, post_id)
    if not post or not post.get("processed_filename"):
        return "Not found", 404
    media_path = os.path.join(proc.PROCESSED_FOLDER, post["processed_filename"])
    if not os.path.exists(media_path):
        return "File missing", 404
    return send_file(media_path)


@app.route("/post/<post_id>/approve", methods=["POST"])
def approve_post(post_id):
    state = load_state()
    post  = get_post(state, post_id)
    if not post:
        return "Not found", 404

    scheduled_at = request.form.get("scheduled_at", "").strip()
    caption      = request.form.get("caption", post.get("caption", "")).strip()

    post["caption"]      = caption
    post["scheduled_at"] = scheduled_at

    if PUBLER_API_KEY and PUBLER_ACCOUNT_ID and scheduled_at:
        try:
            media_path = os.path.join(proc.PROCESSED_FOLDER, post["processed_filename"])
            result = sched.schedule_post(
                api_key      = PUBLER_API_KEY,
                workspace_id = PUBLER_WORKSPACE_ID,
                account_id   = PUBLER_ACCOUNT_ID,
                caption      = caption,
                media_path   = media_path,
                scheduled_at = scheduled_at + ":00+00:00",  # assume UTC
                media_type   = post["media_type"],
            )
            post["status"]        = "approved"
            post["publer_result"] = "scheduled"
        except Exception as e:
            post["status"]        = "approved"
            post["publer_result"] = f"Publer error: {e}"
    else:
        post["status"] = "approved"
        post["publer_result"] = "no_publer" if not PUBLER_API_KEY else "no_time"

    save_state(state)
    return redirect(url_for("index") + "#review")


@app.route("/post/<post_id>/skip", methods=["POST"])
def skip_post(post_id):
    state = load_state()
    post  = get_post(state, post_id)
    if post:
        post["status"] = "skipped"
        save_state(state)
    return redirect(url_for("index") + "#review")


@app.route("/post/<post_id>/unskip", methods=["POST"])
def unskip_post(post_id):
    state = load_state()
    post  = get_post(state, post_id)
    if post:
        post["status"] = "pending"
        save_state(state)
    return redirect(url_for("index") + "#review")


# ── HTML ───────────────────────────────────────────────────────────────────────

INDEX_HTML = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Drive Campaign — InstaFlow</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #f8f8fb; color: #1a1a2e; min-height: 100vh; }
.topbar { background: #7c3aed; color: white; padding: 14px 32px;
          display: flex; align-items: center; gap: 16px; }
.topbar h1 { font-size: 18px; font-weight: 700; }
.topbar .sub { font-size: 13px; opacity: 0.8; }
.page { max-width: 1100px; margin: 0 auto; padding: 32px 24px; }
.card { background: white; border: 1px solid #e5e7eb; border-radius: 12px;
        padding: 24px; margin-bottom: 20px; }
.card-title { font-size: 15px; font-weight: 700; color: #1a1a2e; margin-bottom: 16px; }
label { display: block; font-size: 13px; font-weight: 600; color: #374151;
        margin-bottom: 6px; }
input[type=text], input[type=url], input[type=time], input[type=number],
input[type=datetime-local], select, textarea {
  width: 100%; padding: 9px 12px; border: 1px solid #d1d5db; border-radius: 8px;
  font-size: 14px; color: #1a1a2e; background: white; }
textarea { resize: vertical; line-height: 1.6; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.grid3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; }
.form-group { margin-bottom: 14px; }
.btn { display: inline-flex; align-items: center; gap: 6px; padding: 9px 18px;
       border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer;
       border: none; text-decoration: none; transition: all 0.15s; }
.btn-primary { background: #7c3aed; color: white; }
.btn-primary:hover { background: #6d28d9; }
.btn-green { background: #059669; color: white; }
.btn-green:hover { background: #047857; }
.btn-ghost { background: transparent; border: 1px solid #d1d5db; color: #374151; }
.btn-ghost:hover { background: #f3f4f6; }
.btn-red { background: #ef4444; color: white; }
.btn-red:hover { background: #dc2626; }
.btn-sm { padding: 6px 12px; font-size: 12px; }
.badge { display: inline-flex; align-items: center; gap: 4px; padding: 3px 10px;
         border-radius: 20px; font-size: 12px; font-weight: 600; }
.badge-pending  { background: #fef9c3; color: #854d0e; }
.badge-approved { background: #dcfce7; color: #166534; }
.badge-skipped  { background: #f3f4f6; color: #6b7280; }
.badge-error    { background: #fee2e2; color: #991b1b; }
.callout { border-radius: 10px; padding: 14px 18px; margin-bottom: 20px;
           display: flex; gap: 12px; align-items: flex-start; }
.callout-purple { background: #f5f3ff; border: 1px solid #ddd6fe; }
.callout-green  { background: #f0fdf4; border: 1px solid #bbf7d0; }
.callout-blue   { background: #eff6ff; border: 1px solid #bfdbfe; }
.callout-yellow { background: #fefce8; border: 1px solid #fde68a; }
.progress-bar { height: 4px; background: #ddd6fe; border-radius: 2px; overflow: hidden;
                margin-top: 10px; }
.progress-bar-inner { height: 100%; background: #7c3aed; width: 0;
                      animation: progress-pulse 1.5s ease-in-out infinite; }
@keyframes progress-pulse { 0%{width:15%} 50%{width:75%} 100%{width:15%} }

/* Post cards */
.posts-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
              gap: 20px; }
.post-card { background: white; border: 1px solid #e5e7eb; border-radius: 12px;
             overflow: hidden; }
.post-card.approved { border-color: #86efac; }
.post-card.skipped  { opacity: 0.55; }
.post-card.error    { border-color: #fca5a5; }
.media-wrap { position: relative; background: #f3f4f6; aspect-ratio: 4/5;
              display: flex; align-items: center; justify-content: center;
              overflow: hidden; }
.media-wrap img, .media-wrap video { width: 100%; height: 100%; object-fit: cover; }
.media-wrap .no-media { font-size: 36px; }
.card-body { padding: 16px; }
.filename { font-size: 11px; color: #9ca3af; margin-bottom: 4px;
            overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.brief { font-size: 13px; font-weight: 600; color: #1a1a2e; margin-bottom: 10px;
         line-height: 1.4; }
.caption-area { font-size: 12px; line-height: 1.6; color: #374151; min-height: 80px;
                border: 1px solid #e5e7eb; border-radius: 6px; padding: 8px;
                resize: vertical; width: 100%; background: #fafafa; }
.time-row { display: flex; gap: 8px; align-items: center; margin: 10px 0; }
.time-row input { flex: 1; font-size: 12px; padding: 7px 10px; }
.action-row { display: flex; gap: 8px; margin-top: 10px; }
.action-row .btn { flex: 1; justify-content: center; font-size: 12px; }
.status-bar { font-size: 12px; color: #6b7280; margin-top: 8px; }
.section-header { display: flex; align-items: center; justify-content: space-between;
                  margin-bottom: 16px; }
.section-header h2 { font-size: 17px; font-weight: 700; }
hr { border: none; border-top: 1px solid #e5e7eb; margin: 28px 0; }
.stats-row { display: flex; gap: 20px; margin-bottom: 20px; flex-wrap: wrap; }
.stat { background: white; border: 1px solid #e5e7eb; border-radius: 10px;
        padding: 14px 20px; text-align: center; min-width: 100px; }
.stat-num  { font-size: 26px; font-weight: 800; color: #7c3aed; }
.stat-label { font-size: 12px; color: #6b7280; margin-top: 2px; }
</style>
</head>
<body>

<div class="topbar">
  <div>
    <div class="h1" style="font-size:18px;font-weight:700">🎵 Drive Campaign — InstaFlow</div>
    <div class="sub">Drive → process → approve → Publer → Instagram</div>
  </div>
  {% if state %}
  <div style="margin-left:auto;display:flex;gap:10px;align-items:center">
    <span style="font-size:14px;opacity:0.9">📂 {{ state.event_name }}</span>
    <form method="POST" action="/reset"
          onsubmit="return confirm('Clear all campaign data and start fresh?')">
      <button class="btn btn-ghost btn-sm" style="color:white;border-color:rgba(255,255,255,0.4)"
              type="submit">↺ New Campaign</button>
    </form>
  </div>
  {% endif %}
</div>

<div class="page">

{% if not has_publer %}
<div class="callout callout-yellow">
  <span>⚠️</span>
  <div>
    <strong>Publer not configured</strong> — set PUBLER_API_KEY, PUBLER_WORKSPACE_ID,
    and PUBLER_ACCOUNT_ID in your .env file to enable direct scheduling.
    You can still approve posts and copy captions manually.
  </div>
</div>
{% endif %}

{# ── SCANNING STATE ── #}
{% if scan_state.status == 'scanning' %}
<div class="card" id="scanCard">
  <div class="card-title">⏳ Scanning Drive folder…</div>
  <p style="font-size:14px;color:#6b7280" id="progressText">{{ scan_state.progress }}</p>
  <div class="progress-bar"><div class="progress-bar-inner"></div></div>
  <p style="font-size:12px;color:#9ca3af;margin-top:12px">
    Downloading and processing all media files — this may take a minute for large folders.
  </p>
</div>
<script>
setInterval(async () => {
  const r = await fetch('/scan_status');
  const d = await r.json();
  document.getElementById('progressText').textContent = d.progress;
  if (d.status === 'done' || d.status === 'error') window.location.reload();
}, 2000);
</script>

{# ── SCAN ERROR ── #}
{% elif scan_state.status == 'error' %}
<div class="card" style="border-color:#fca5a5">
  <div class="card-title" style="color:#991b1b">❌ Scan failed</div>
  <p style="color:#ef4444;font-size:14px">{{ scan_state.error }}</p>
  <p style="font-size:13px;color:#6b7280;margin-top:10px">
    Common causes: GOOGLE_API_KEY not set, folder isn't set to "Anyone with the link can view",
    or the URL is wrong.
  </p>
  <form method="POST" action="/reset" style="margin-top:16px">
    <button class="btn btn-ghost" type="submit">↺ Try again</button>
  </form>
</div>

{# ── SETUP FORM (no state yet or after reset) ── #}
{% elif not state %}
<div class="callout callout-purple">
  <span>💡</span>
  <div>
    <strong>How this works:</strong> paste a public Google Drive folder link below.
    The folder should contain your event photos/videos — name each file as a natural-language
    description of what's in it (e.g. <em>"kickass bassist covering Bollywood song"</em>).
    Add a <code>guidelines.txt</code> file for brand voice / posting style.
  </div>
</div>

<div class="card">
  <div class="card-title">📂 Set up your event campaign</div>
  <form method="POST" action="/scan" id="setupForm">

    <div class="form-group">
      <label>Google Drive folder URL</label>
      <input type="url" name="drive_url" required
             placeholder="https://drive.google.com/drive/folders/…"
             style="font-size:14px"/>
      <p style="font-size:12px;color:#9ca3af;margin-top:4px">
        The folder must be shared as "Anyone with the link can view"
      </p>
    </div>

    <div class="grid2">
      <div class="form-group">
        <label>Event name</label>
        <input type="text" name="event_name" required placeholder="e.g. Jazz Night — March 2026"/>
      </div>
      <div class="form-group">
        <label>Spread posts over (days)</label>
        <input type="number" name="window_days" value="7" min="1" max="30"/>
        <p style="font-size:12px;color:#9ca3af;margin-top:4px">
          Posts will be suggested at even intervals across this window
        </p>
      </div>
    </div>

    <div class="form-group">
      <label>Preferred posting time (local)</label>
      <input type="time" name="post_time" value="18:00" style="max-width:160px"/>
    </div>

    <hr style="margin:20px 0"/>
    <div style="font-size:14px;font-weight:700;margin-bottom:14px">✍️ Caption style</div>

    <div class="grid3">
      <div class="form-group">
        <label>Tone</label>
        <select name="caption_tone">
          <option value="fun">Fun &amp; Playful</option>
          <option value="casual">Casual &amp; Friendly</option>
          <option value="inspirational">Inspirational</option>
          <option value="professional">Professional</option>
        </select>
      </div>
      <div class="form-group">
        <label>Length</label>
        <select name="caption_length">
          <option value="short">Short (&lt;50 words)</option>
          <option value="medium" selected>Medium (60–120 words)</option>
          <option value="long">Long (story format)</option>
        </select>
      </div>
      <div class="form-group">
        <label>Photo colour</label>
        <select name="image_warmth">
          <option value="neutral">Neutral</option>
          <option value="warm">Warm (golden tones)</option>
          <option value="cool">Cool (blue tones)</option>
        </select>
      </div>
    </div>

    <div style="display:flex;gap:20px;margin-bottom:16px">
      <label style="display:flex;gap:8px;align-items:center;cursor:pointer;font-weight:400">
        <input type="checkbox" name="caption_hashtags" checked/> Include hashtags
      </label>
      <label style="display:flex;gap:8px;align-items:center;cursor:pointer;font-weight:400">
        <input type="checkbox" name="caption_emoji" checked/> Include emojis
      </label>
    </div>

    <button class="btn btn-primary" type="submit" id="submitBtn"
            style="width:100%;justify-content:center;padding:14px;font-size:15px">
      🔍 Scan Drive Folder &amp; Process All Posts
    </button>
    <div id="submitNote" style="display:none;text-align:center;font-size:13px;
                                color:#6b7280;margin-top:12px">
      Scanning and processing… this window will update automatically.
    </div>
  </form>
  <script>
  document.getElementById('setupForm').addEventListener('submit', function() {
    document.getElementById('submitBtn').disabled = true;
    document.getElementById('submitBtn').textContent = '⏳ Starting scan…';
    document.getElementById('submitNote').style.display = 'block';
  });
  </script>
</div>

{# ── REVIEW UI (state exists and scan is done) ── #}
{% else %}

{# Stats #}
<div class="stats-row">
  <div class="stat">
    <div class="stat-num">{{ pending|length }}</div>
    <div class="stat-label">Pending</div>
  </div>
  <div class="stat">
    <div class="stat-num" style="color:#059669">{{ approved|length }}</div>
    <div class="stat-label">Approved</div>
  </div>
  <div class="stat">
    <div class="stat-num" style="color:#9ca3af">{{ skipped|length }}</div>
    <div class="stat-label">Skipped</div>
  </div>
</div>

{% if state.guidelines %}
<div class="callout callout-blue" style="margin-bottom:20px">
  <span>📋</span>
  <div>
    <strong>Guidelines loaded</strong> — used as context for all captions.<br/>
    <span style="font-size:12px;color:#3b82f6">{{ state.guidelines[:200] }}{% if state.guidelines|length > 200 %}…{% endif %}</span>
  </div>
</div>
{% endif %}

{# ── PENDING posts ── #}
{% if pending %}
<div id="review" class="section-header" style="margin-top:8px">
  <h2>⏳ Pending Approval ({{ pending|length }})</h2>
</div>
<div class="posts-grid">
{% for post in pending %}
<div class="post-card" id="card-{{ post.id }}">
  <div class="media-wrap">
    {% if post.processed_filename %}
      {% if post.media_type == 'image' %}
        <img src="/post/{{ post.id }}/media" alt="{{ post.brief }}" loading="lazy"/>
      {% else %}
        <video src="/post/{{ post.id }}/media" controls></video>
      {% endif %}
    {% else %}
      <div class="no-media">❌</div>
    {% endif %}
  </div>
  <div class="card-body">
    <div class="filename">📄 {{ post.filename }}</div>
    <div class="brief">{{ post.brief }}</div>

    <form method="POST" action="/post/{{ post.id }}/approve">
      <div class="form-group" style="margin-bottom:8px">
        <textarea class="caption-area" name="caption" rows="5">{{ post.caption }}</textarea>
      </div>
      <div class="time-row">
        <span style="font-size:12px;color:#6b7280;white-space:nowrap">📅 Schedule:</span>
        <input type="datetime-local" name="scheduled_at"
               value="{{ post.suggested_schedule_at }}"
               min="{{ now }}"/>
      </div>
      {% if not has_publer %}
      <p style="font-size:11px;color:#f59e0b;margin-bottom:8px">
        ⚠️ Publer not configured — approving will mark this as done without scheduling
      </p>
      {% endif %}
      <div class="action-row">
        <button class="btn btn-green" type="submit"
                onclick="this.textContent='⏳…';this.disabled=true">
          ✅ Approve{% if has_publer %} &amp; Schedule{% endif %}
        </button>
      </div>
    </form>

    <form method="POST" action="/post/{{ post.id }}/skip" style="margin-top:8px">
      <button class="btn btn-ghost btn-sm" type="submit" style="width:100%;justify-content:center">
        ↷ Skip this post
      </button>
    </form>
  </div>
</div>
{% endfor %}
</div>
{% endif %}

{# ── APPROVED posts ── #}
{% if approved %}
<hr/>
<div class="section-header">
  <h2 style="color:#059669">✅ Approved ({{ approved|length }})</h2>
</div>
<div class="posts-grid">
{% for post in approved %}
<div class="post-card approved">
  <div class="media-wrap" style="aspect-ratio:4/5">
    {% if post.processed_filename %}
      {% if post.media_type == 'image' %}
        <img src="/post/{{ post.id }}/media" loading="lazy"/>
      {% else %}
        <video src="/post/{{ post.id }}/media" controls style="width:100%;height:100%;object-fit:cover"></video>
      {% endif %}
    {% endif %}
  </div>
  <div class="card-body">
    <div class="brief">{{ post.brief }}</div>
    {% if post.scheduled_at %}
    <div style="font-size:12px;color:#059669;margin-bottom:6px">
      📅 {{ post.scheduled_at[:16].replace('T',' ') }}
    </div>
    {% endif %}
    {% if post.publer_result == 'scheduled' %}
      <span class="badge badge-approved">✓ Scheduled on Publer</span>
    {% elif post.publer_result == 'no_publer' %}
      <span class="badge" style="background:#fef9c3;color:#854d0e">✓ Approved (no Publer)</span>
    {% elif post.publer_result and post.publer_result.startswith('Publer error') %}
      <div style="font-size:11px;color:#dc2626;margin-top:4px">{{ post.publer_result }}</div>
    {% endif %}
    <div style="font-size:12px;color:#6b7280;margin-top:8px;line-height:1.5">
      {{ post.caption[:120] }}{% if post.caption|length > 120 %}…{% endif %}
    </div>
  </div>
</div>
{% endfor %}
</div>
{% endif %}

{# ── SKIPPED posts ── #}
{% if skipped %}
<hr/>
<div class="section-header">
  <h2 style="color:#9ca3af">↷ Skipped ({{ skipped|length }})</h2>
</div>
<div class="posts-grid">
{% for post in skipped %}
<div class="post-card skipped">
  <div class="media-wrap" style="aspect-ratio:1/1">
    {% if post.processed_filename and post.media_type == 'image' %}
      <img src="/post/{{ post.id }}/media" loading="lazy"/>
    {% else %}
      <div class="no-media">↷</div>
    {% endif %}
  </div>
  <div class="card-body">
    <div class="brief">{{ post.brief }}</div>
    <form method="POST" action="/post/{{ post.id }}/unskip" style="margin-top:10px">
      <button class="btn btn-ghost btn-sm" type="submit">↩ Restore</button>
    </form>
  </div>
</div>
{% endfor %}
</div>
{% endif %}

{% if not pending and not approved and not skipped %}
<div class="card" style="text-align:center;padding:60px">
  <div style="font-size:48px;margin-bottom:16px">📭</div>
  <p style="color:#6b7280">No posts found. Check that your Drive folder has photos or videos.</p>
  <form method="POST" action="/reset" style="margin-top:16px">
    <button class="btn btn-ghost" type="submit">↺ Start over</button>
  </form>
</div>
{% endif %}

{% endif %}{# end state check #}
</div>{# end page #}
</body>
</html>
"""

# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\n  🎵 Drive Campaign tool running at http://localhost:{PORT}\n")
    print(f"  Make sure GOOGLE_API_KEY is set in your .env file.\n")
    if not PUBLER_API_KEY:
        print("  ⚠️  PUBLER_API_KEY not set — scheduling will be disabled.\n")
    app.run(debug=False, port=PORT, threaded=True)
