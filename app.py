"""
InstaFlow – Content creation app.
Processes photos/videos and generates captions. Client downloads and posts manually.
"""
import os
import threading
import uuid
from datetime import datetime
from functools import wraps

from dotenv import load_dotenv
from flask import (Flask, abort, flash, jsonify, redirect,
                   render_template, request, send_from_directory, url_for)

load_dotenv()

from models import Client, Post, Strategy, ContentPlanItem, db
import processor as proc
import generator as gen
import image_generator as imggen
import strategy_generator as sg
import edit_translator as et

# ── App setup ─────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production")
_db_path = os.environ.get(
    "DATABASE_URL",
    f"sqlite:///{os.path.join(os.path.dirname(os.path.abspath(__file__)), 'automation.db')}"
)
app.config["SQLALCHEMY_DATABASE_URI"] = _db_path
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024   # 500 MB

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000")

db.init_app(app)

with app.app_context():
    db.create_all()
    # Schema migrations — safely add new columns to existing tables
    with db.engine.connect() as _conn:
        for _sql in [
            "ALTER TABLE posts ADD COLUMN plan_item_id INTEGER",
            "ALTER TABLE posts ADD COLUMN posted_at DATETIME",
        ]:
            try:
                _conn.execute(db.text(_sql))
                _conn.commit()
            except Exception:
                pass  # Column already exists — safe to ignore


# ── Helpers ───────────────────────────────────────────────────────────────────

def client_or_404(token: str) -> Client:
    c = Client.query.filter_by(token=token).first()
    if not c:
        abort(404)
    return c


def client_prefs(client: Client) -> dict:
    return {
        "image_brightness":   client.image_brightness,
        "image_contrast":     client.image_contrast,
        "image_saturation":   client.image_saturation,
        "image_warmth":       client.image_warmth,
        "image_filter":       client.image_filter,
        "image_crop":         client.image_crop,
        "reel_max_duration":  client.reel_max_duration,
        "reel_trim_strategy": client.reel_trim_strategy,
        "caption_tone":       client.caption_tone,
        "caption_hashtags":   client.caption_hashtags,
        "caption_emoji":      client.caption_emoji,
        "caption_length":     client.caption_length,
    }


def process_post_async(app_ctx, post_id: int):
    """Run media processing + caption generation in a background thread."""
    with app_ctx:
        post = Post.query.get(post_id)
        if not post:
            return
        client = post.client
        prefs = client_prefs(client)
        try:
            orig_path = os.path.join(proc.UPLOAD_FOLDER, post.original_filename)
            if post.media_type == "image":
                processed_name = proc.process_image(orig_path, prefs)
            else:
                processed_name = proc.process_video(orig_path, prefs)

            caption = gen.generate_caption(post.brief or "", prefs)

            post.processed_filename = processed_name
            post.caption = caption
            post.status = "ready_for_review"
        except Exception as e:
            post.status = "failed"
            post.error_message = str(e)
        db.session.commit()


# ── Admin ─────────────────────────────────────────────────────────────────────

@app.route("/admin", methods=["GET"])
def admin():
    clients = Client.query.all()
    return render_template("admin.html", clients=clients)


@app.route("/admin/clients", methods=["POST"])
def create_client():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    if not name or not email:
        flash("Name and email are required.", "error")
        return redirect(url_for("admin"))
    if Client.query.count() >= 5:
        flash("Maximum 5 clients reached.", "error")
        return redirect(url_for("admin"))
    client = Client(name=name, email=email)
    db.session.add(client)
    db.session.commit()
    flash(f"Client '{name}' created. Share this URL: {BASE_URL}/client/{client.token}", "success")
    return redirect(url_for("admin"))


@app.route("/admin/clients/<int:client_id>/delete", methods=["POST"])
def delete_client(client_id):
    client = Client.query.get_or_404(client_id)
    db.session.delete(client)
    db.session.commit()
    flash("Client deleted.", "success")
    return redirect(url_for("admin"))


# ── Client dashboard ──────────────────────────────────────────────────────────

@app.route("/client/<token>")
def dashboard(token):
    client = client_or_404(token)
    posts = Post.query.filter_by(client_id=client.id).order_by(Post.created_at.desc()).all()
    # Latest confirmed strategy for checklist shortcut
    latest_strategy = Strategy.query.filter_by(
        client_id=client.id, status="confirmed"
    ).order_by(Strategy.created_at.desc()).first()
    return render_template("dashboard.html", client=client, posts=posts,
                           token=token, latest_strategy=latest_strategy)


# ── Preferences ───────────────────────────────────────────────────────────────

@app.route("/client/<token>/preferences", methods=["GET", "POST"])
def preferences(token):
    client = client_or_404(token)
    if request.method == "POST":
        f = request.form
        client.image_brightness   = float(f.get("image_brightness", 1.0))
        client.image_contrast     = float(f.get("image_contrast", 1.0))
        client.image_saturation   = float(f.get("image_saturation", 1.0))
        client.image_warmth       = f.get("image_warmth", "neutral")
        client.image_filter       = f.get("image_filter", "none")
        client.image_crop         = f.get("image_crop", "4:5")
        client.reel_max_duration  = int(f.get("reel_max_duration", 60))
        client.reel_trim_strategy = f.get("reel_trim_strategy", "trim")
        client.reel_add_subtitles = f.get("reel_add_subtitles") == "on"
        client.caption_tone       = f.get("caption_tone", "casual")
        client.caption_hashtags   = f.get("caption_hashtags") == "on"
        client.caption_emoji      = f.get("caption_emoji") == "on"
        client.caption_length     = f.get("caption_length", "medium")
        db.session.commit()
        flash("Preferences saved!", "success")
        return redirect(url_for("dashboard", token=token))
    return render_template("preferences.html", client=client, token=token)


# ── Upload ────────────────────────────────────────────────────────────────────

@app.route("/client/<token>/upload", methods=["GET", "POST"])
def upload(token):
    client = client_or_404(token)
    if request.method == "GET":
        plan_item_id = request.args.get("plan_item_id", type=int)
        prefill_brief = request.args.get("brief", "")
        prefill_type = request.args.get("type", "image")
        plan_item = None
        if plan_item_id:
            plan_item = ContentPlanItem.query.filter_by(
                id=plan_item_id, client_id=client.id
            ).first()
        return render_template("upload.html", client=client, token=token,
                               plan_item=plan_item, prefill_brief=prefill_brief,
                               prefill_type=prefill_type)

    file = request.files.get("media")
    brief = request.form.get("brief", "").strip()
    plan_item_id = request.form.get("plan_item_id", type=int)

    if not file or file.filename == "":
        flash("Please select a file.", "error")
        return redirect(url_for("upload", token=token))

    original_ext = os.path.splitext(file.filename)[1].lower()
    original_name = f"orig_{uuid.uuid4().hex}{original_ext}"
    save_path = os.path.join(proc.UPLOAD_FOLDER, original_name)
    file.save(save_path)

    if proc.is_image(original_name):
        media_type = "image"
    elif proc.is_video(original_name):
        media_type = "video"
    else:
        os.remove(save_path)
        flash("Unsupported file type. Use JPG/PNG for images or MP4/MOV for videos.", "error")
        return redirect(url_for("upload", token=token))

    post = Post(
        client_id=client.id,
        brief=brief,
        media_type=media_type,
        original_filename=original_name,
        status="pending_processing",
        plan_item_id=plan_item_id,
    )
    db.session.add(post)
    db.session.commit()

    thread = threading.Thread(
        target=process_post_async,
        args=(app.app_context(), post.id),
        daemon=True,
    )
    thread.start()

    flash("Upload received! Processing your media…", "success")
    return redirect(url_for("dashboard", token=token))


# ── AI Image Generation ───────────────────────────────────────────────────────

def generate_post_async(app_ctx, post_id: int, image_prompt: str, image_style: str):
    """Generate image via DALL-E, then run editing + caption in background."""
    with app_ctx:
        post = Post.query.get(post_id)
        if not post:
            return
        client = post.client
        prefs = client_prefs(client)
        try:
            # Step 1: generate image
            generated_filename = imggen.generate_image(image_prompt, style=image_style)
            post.original_filename = generated_filename

            # Step 2: apply image editing preferences
            orig_path = os.path.join(proc.UPLOAD_FOLDER, generated_filename)
            processed_name = proc.process_image(orig_path, prefs)

            # Step 3: generate caption
            caption = gen.generate_caption(post.brief or image_prompt, prefs)

            post.processed_filename = processed_name
            post.caption = caption
            post.status = "ready_for_review"
        except Exception as e:
            post.status = "failed"
            post.error_message = str(e)
        db.session.commit()


@app.route("/client/<token>/generate", methods=["POST"])
def generate_post(token):
    client = client_or_404(token)
    image_prompt = request.form.get("image_prompt", "").strip()
    image_style  = request.form.get("image_style", "vivid")
    brief        = request.form.get("brief", "").strip()
    plan_item_id = request.form.get("plan_item_id", type=int)

    if not image_prompt:
        flash("Please describe the image you want to generate.", "error")
        return redirect(url_for("upload", token=token))

    post = Post(
        client_id=client.id,
        brief=brief or image_prompt,
        media_type="image",
        original_filename=None,
        status="pending_processing",
        plan_item_id=plan_item_id,
    )
    db.session.add(post)
    db.session.commit()

    thread = threading.Thread(
        target=generate_post_async,
        args=(app.app_context(), post.id, image_prompt, image_style),
        daemon=True,
    )
    thread.start()

    flash("Generating your image… this takes about 15 seconds.", "success")
    return redirect(url_for("dashboard", token=token))


# ── Preview, edit & download ──────────────────────────────────────────────────

@app.route("/client/<token>/post/<int:post_id>")
def preview(token, post_id):
    client = client_or_404(token)
    post = Post.query.filter_by(id=post_id, client_id=client.id).first_or_404()
    return render_template("preview.html", client=client, post=post, token=token)


@app.route("/client/<token>/post/<int:post_id>/update", methods=["POST"])
def update_post(token, post_id):
    client = client_or_404(token)
    post = Post.query.filter_by(id=post_id, client_id=client.id).first_or_404()
    post.caption = request.form.get("caption", post.caption)
    db.session.commit()
    flash("Caption saved.", "success")
    return redirect(url_for("preview", token=token, post_id=post_id))


@app.route("/client/<token>/post/<int:post_id>/regenerate", methods=["POST"])
def regenerate_caption(token, post_id):
    client = client_or_404(token)
    post = Post.query.filter_by(id=post_id, client_id=client.id).first_or_404()
    try:
        prefs = client_prefs(client)
        brief = request.form.get("brief", post.brief or "")
        post.brief = brief
        post.caption = gen.generate_caption(brief, prefs)
        db.session.commit()
        flash("Caption regenerated!", "success")
    except Exception as e:
        flash(f"Could not regenerate caption: {e}", "error")
    return redirect(url_for("preview", token=token, post_id=post_id))


@app.route("/client/<token>/post/<int:post_id>/reprocess", methods=["POST"])
def reprocess_post(token, post_id):
    """Re-edit a photo using a natural language description of what to change."""
    client = client_or_404(token)
    post = Post.query.filter_by(id=post_id, client_id=client.id).first_or_404()

    if post.media_type != "image" or not post.original_filename:
        flash("Re-editing is only available for photos.", "error")
        return redirect(url_for("preview", token=token, post_id=post_id))

    edit_request = request.form.get("edit_request", "").strip()
    if not edit_request:
        flash("Please describe what you'd like to change.", "error")
        return redirect(url_for("preview", token=token, post_id=post_id))

    post.status = "pending_processing"
    post.error_message = None
    db.session.commit()

    thread = threading.Thread(
        target=reprocess_post_async,
        args=(app.app_context(), post.id, edit_request),
        daemon=True,
    )
    thread.start()

    flash("Re-editing your photo… refresh in a moment.", "info")
    return redirect(url_for("preview", token=token, post_id=post_id))


def reprocess_post_async(app_ctx, post_id: int, edit_request: str):
    """Translate NL edit request → param overrides → reprocess image."""
    with app_ctx:
        post = Post.query.get(post_id)
        if not post:
            return
        client = post.client
        base_prefs = client_prefs(client)
        try:
            overrides = et.translate_edit_request(edit_request, base_prefs)
            merged_prefs = {**base_prefs, **overrides}
            orig_path = os.path.join(proc.UPLOAD_FOLDER, post.original_filename)
            processed_name = proc.process_image(orig_path, merged_prefs)
            post.processed_filename = processed_name
            post.status = "ready_for_review"
        except Exception as e:
            post.status = "ready_for_review"  # Keep existing image visible
            post.error_message = str(e)
        db.session.commit()


@app.route("/client/<token>/post/<int:post_id>/download")
def download_media(token, post_id):
    client = client_or_404(token)
    post = Post.query.filter_by(id=post_id, client_id=client.id).first_or_404()
    if not post.processed_filename:
        abort(404)
    ext = os.path.splitext(post.processed_filename)[1]
    download_name = f"{client.name.replace(' ', '_')}_post{ext}"
    return send_from_directory(
        proc.PROCESSED_FOLDER,
        post.processed_filename,
        as_attachment=True,
        download_name=download_name,
    )


@app.route("/client/<token>/post/<int:post_id>/mark_done", methods=["POST"])
def mark_done(token, post_id):
    client = client_or_404(token)
    post = Post.query.filter_by(id=post_id, client_id=client.id).first_or_404()
    post.status = "posted"
    post.posted_at = datetime.utcnow()
    # Mark linked checklist item as done
    if post.plan_item_id:
        item = ContentPlanItem.query.get(post.plan_item_id)
        if item and item.client_id == client.id:
            item.status = "done"
    db.session.commit()
    flash("Marked as posted!", "success")
    return redirect(url_for("dashboard", token=token))


@app.route("/client/<token>/post/<int:post_id>/discard", methods=["POST"])
def discard_post(token, post_id):
    client = client_or_404(token)
    post = Post.query.filter_by(id=post_id, client_id=client.id).first_or_404()
    post.status = "failed"
    post.error_message = "Discarded by client"
    db.session.commit()
    flash("Post discarded.", "info")
    return redirect(url_for("dashboard", token=token))


# ── Serve processed media ─────────────────────────────────────────────────────

@app.route("/media/<filename>")
def serve_media(filename):
    return send_from_directory(proc.PROCESSED_FOLDER, filename)


# ── Status polling (AJAX) ─────────────────────────────────────────────────────

@app.route("/client/<token>/post/<int:post_id>/status")
def post_status(token, post_id):
    client = client_or_404(token)
    post = Post.query.filter_by(id=post_id, client_id=client.id).first_or_404()
    return jsonify({"status": post.status, "error": post.error_message})


# ── Strategy ──────────────────────────────────────────────────────────────────

def generate_strategy_async(app_ctx, strategy_id: int):
    """Generate strategy narrative in background thread."""
    with app_ctx:
        s = Strategy.query.get(strategy_id)
        if not s:
            return
        client = s.client
        try:
            strategy_text = sg.generate_strategy(s.objectives, s.business_context, client.name)
            s.strategy_text = strategy_text
            s.status = "draft"
        except Exception as e:
            s.strategy_text = f"Could not generate strategy: {e}"
            s.status = "draft"
        db.session.commit()


@app.route("/client/<token>/strategy", methods=["GET", "POST"])
def strategy_form(token):
    client = client_or_404(token)
    if request.method == "GET":
        strategies = Strategy.query.filter_by(client_id=client.id).order_by(
            Strategy.created_at.desc()
        ).all()
        return render_template("strategy_form.html", client=client, token=token,
                               strategies=strategies)

    objectives = request.form.get("objectives", "").strip()
    business_context = request.form.get("business_context", "").strip()

    if not objectives:
        flash("Please describe your objectives for this week.", "error")
        return redirect(url_for("strategy_form", token=token))

    week_of = datetime.utcnow().strftime("%Y-%m-%d")
    s = Strategy(
        client_id=client.id,
        week_of=week_of,
        objectives=objectives,
        business_context=business_context,
        status="generating",
    )
    db.session.add(s)
    db.session.commit()

    thread = threading.Thread(
        target=generate_strategy_async,
        args=(app.app_context(), s.id),
        daemon=True,
    )
    thread.start()

    return redirect(url_for("strategy_review", token=token, strategy_id=s.id))


@app.route("/client/<token>/strategy/<int:strategy_id>/review", methods=["GET"])
def strategy_review(token, strategy_id):
    client = client_or_404(token)
    s = Strategy.query.filter_by(id=strategy_id, client_id=client.id).first_or_404()
    return render_template("strategy_review.html", client=client, token=token, strategy=s)


@app.route("/client/<token>/strategy/<int:strategy_id>/status")
def strategy_status(token, strategy_id):
    client = client_or_404(token)
    s = Strategy.query.filter_by(id=strategy_id, client_id=client.id).first_or_404()
    return jsonify({"status": s.status, "strategy_text": s.strategy_text or ""})


@app.route("/client/<token>/strategy/<int:strategy_id>/save", methods=["POST"])
def strategy_save(token, strategy_id):
    client = client_or_404(token)
    s = Strategy.query.filter_by(id=strategy_id, client_id=client.id).first_or_404()
    s.strategy_text = request.form.get("strategy_text", s.strategy_text)
    db.session.commit()
    flash("Strategy saved.", "success")
    return redirect(url_for("strategy_review", token=token, strategy_id=strategy_id))


@app.route("/client/<token>/strategy/<int:strategy_id>/confirm", methods=["POST"])
def strategy_confirm(token, strategy_id):
    client = client_or_404(token)
    s = Strategy.query.filter_by(id=strategy_id, client_id=client.id).first_or_404()

    # Clear any existing items
    ContentPlanItem.query.filter_by(strategy_id=s.id).delete()
    db.session.commit()

    try:
        items = sg.generate_checklist(
            s.objectives, s.business_context, s.strategy_text, client.name
        )
        for item in items:
            cpi = ContentPlanItem(
                strategy_id=s.id,
                client_id=client.id,
                day=item["day"],
                title=item["title"],
                description=item["description"],
                brief=item["brief"],
                suggested_type=item.get("suggested_type", "image"),
            )
            db.session.add(cpi)
        s.status = "confirmed"
        s.confirmed_at = datetime.utcnow()
        db.session.commit()
        flash("Content plan ready! Click any post to start creating.", "success")
    except Exception as e:
        flash(f"Error generating content plan: {e}", "error")
        return redirect(url_for("strategy_review", token=token, strategy_id=strategy_id))

    return redirect(url_for("checklist", token=token, strategy_id=strategy_id))


@app.route("/client/<token>/strategy/<int:strategy_id>/checklist")
def checklist(token, strategy_id):
    client = client_or_404(token)
    s = Strategy.query.filter_by(id=strategy_id, client_id=client.id).first_or_404()
    items = ContentPlanItem.query.filter_by(strategy_id=s.id).order_by(
        ContentPlanItem.id
    ).all()
    return render_template("checklist.html", client=client, token=token,
                           strategy=s, items=items)


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
