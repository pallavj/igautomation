from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import secrets

db = SQLAlchemy()


class Client(db.Model):
    __tablename__ = "clients"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(200), nullable=False)
    token = db.Column(db.String(64), unique=True, nullable=False, default=lambda: secrets.token_urlsafe(32))

    # Instagram credentials (kept for future use)
    instagram_user_id = db.Column(db.String(100), nullable=True)
    instagram_username = db.Column(db.String(100), nullable=True)
    access_token = db.Column(db.Text, nullable=True)
    token_expires_at = db.Column(db.DateTime, nullable=True)

    # Preferences
    image_brightness = db.Column(db.Float, default=1.0)
    image_contrast = db.Column(db.Float, default=1.0)
    image_saturation = db.Column(db.Float, default=1.0)
    image_warmth = db.Column(db.String(20), default="neutral")
    image_filter = db.Column(db.String(30), default="none")
    image_crop = db.Column(db.String(10), default="4:5")

    reel_max_duration = db.Column(db.Integer, default=60)
    reel_trim_strategy = db.Column(db.String(20), default="trim")
    reel_add_subtitles = db.Column(db.Boolean, default=False)

    caption_tone = db.Column(db.String(30), default="casual")
    caption_hashtags = db.Column(db.Boolean, default=True)
    caption_emoji = db.Column(db.Boolean, default=True)
    caption_length = db.Column(db.String(20), default="medium")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    posts = db.relationship("Post", backref="client", lazy=True, cascade="all, delete-orphan")
    strategies = db.relationship("Strategy", backref="client", lazy=True, cascade="all, delete-orphan")

    @property
    def is_connected(self):
        return self.access_token is not None


class Post(db.Model):
    __tablename__ = "posts"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)

    # Status: pending_processing | ready_for_review | posted | failed
    status = db.Column(db.String(30), default="pending_processing")

    brief = db.Column(db.Text, nullable=True)
    caption = db.Column(db.Text, nullable=True)
    media_type = db.Column(db.String(10), nullable=True)   # image / video

    original_filename = db.Column(db.String(300), nullable=True)
    processed_filename = db.Column(db.String(300), nullable=True)

    instagram_container_id = db.Column(db.String(100), nullable=True)
    instagram_post_id = db.Column(db.String(100), nullable=True)
    error_message = db.Column(db.Text, nullable=True)

    # Link back to checklist item if created from strategy
    plan_item_id = db.Column(db.Integer, db.ForeignKey("content_plan_items.id"), nullable=True)

    # Video intelligence
    vibe_prompt = db.Column(db.Text, nullable=True)   # e.g. "golden hour, warm and dreamy"

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    approved_at = db.Column(db.DateTime, nullable=True)
    posted_at = db.Column(db.DateTime, nullable=True)


class Strategy(db.Model):
    __tablename__ = "strategies"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)

    week_of = db.Column(db.String(20), nullable=False)        # e.g. "2026-03-10"
    objectives = db.Column(db.Text, nullable=False)           # raw client input
    business_context = db.Column(db.Text, nullable=True)      # industry, audience, etc.
    strategy_text = db.Column(db.Text, nullable=True)         # Claude's strategy narrative
    # Status: generating | draft | confirmed
    status = db.Column(db.String(20), default="generating")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    confirmed_at = db.Column(db.DateTime, nullable=True)

    items = db.relationship("ContentPlanItem", backref="strategy", lazy=True, cascade="all, delete-orphan")


class ContentPlanItem(db.Model):
    __tablename__ = "content_plan_items"

    id = db.Column(db.Integer, primary_key=True)
    strategy_id = db.Column(db.Integer, db.ForeignKey("strategies.id"), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)

    day = db.Column(db.String(20), nullable=False)            # e.g. "Monday"
    title = db.Column(db.String(200), nullable=False)         # short post title
    description = db.Column(db.Text, nullable=False)          # what the post should be
    brief = db.Column(db.Text, nullable=False)                # pre-filled brief for creation
    suggested_type = db.Column(db.String(20), default="image") # image / video / generated

    # Status: pending | in_progress | done
    status = db.Column(db.String(20), default="pending")
    post_id = db.Column(db.Integer, nullable=True)            # linked post once created

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    posts = db.relationship("Post", backref="plan_item", lazy=True,
                            foreign_keys="Post.plan_item_id")
