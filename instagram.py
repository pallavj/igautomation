"""Instagram Graph API wrapper."""
import requests
import time
import os

GRAPH_URL = "https://graph.instagram.com/v18.0"
AUTH_URL = "https://api.instagram.com/oauth/authorize"
TOKEN_URL = "https://api.instagram.com/oauth/access_token"
LONG_TOKEN_URL = f"{GRAPH_URL}/access_token"


def get_auth_url(app_id: str, redirect_uri: str, state: str) -> str:
    """Build the Instagram OAuth URL."""
    scopes = "instagram_business_basic,instagram_business_content_publish"
    return (
        f"{AUTH_URL}?client_id={app_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope={scopes}"
        f"&response_type=code"
        f"&state={state}"
    )


def exchange_code_for_token(app_id: str, app_secret: str, redirect_uri: str, code: str) -> dict:
    """Exchange auth code for short-lived token, then upgrade to long-lived."""
    # Step 1: short-lived token
    resp = requests.post(TOKEN_URL, data={
        "client_id": app_id,
        "client_secret": app_secret,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "code": code,
    })
    resp.raise_for_status()
    short = resp.json()

    # Step 2: long-lived token (valid 60 days)
    resp2 = requests.get(LONG_TOKEN_URL, params={
        "grant_type": "ig_exchange_token",
        "client_secret": app_secret,
        "access_token": short["access_token"],
    })
    resp2.raise_for_status()
    long = resp2.json()

    # Step 3: fetch user info
    user_resp = requests.get(f"{GRAPH_URL}/me", params={
        "fields": "id,username",
        "access_token": long["access_token"],
    })
    user_resp.raise_for_status()
    user = user_resp.json()

    return {
        "access_token": long["access_token"],
        "expires_in": long.get("expires_in", 5183944),
        "user_id": user["id"],
        "username": user.get("username", ""),
    }


def refresh_long_lived_token(access_token: str) -> dict:
    """Refresh a long-lived token before it expires."""
    resp = requests.get(f"{GRAPH_URL}/refresh_access_token", params={
        "grant_type": "ig_refresh_token",
        "access_token": access_token,
    })
    resp.raise_for_status()
    return resp.json()


def publish_image(user_id: str, access_token: str, image_url: str, caption: str) -> str:
    """Create and publish an image post. Returns the Instagram post ID."""
    # Create container
    container_resp = requests.post(f"{GRAPH_URL}/{user_id}/media", params={
        "image_url": image_url,
        "caption": caption,
        "access_token": access_token,
    })
    container_resp.raise_for_status()
    container_id = container_resp.json()["id"]

    # Publish
    publish_resp = requests.post(f"{GRAPH_URL}/{user_id}/media_publish", params={
        "creation_id": container_id,
        "access_token": access_token,
    })
    publish_resp.raise_for_status()
    return publish_resp.json()["id"]


def publish_reel(user_id: str, access_token: str, video_url: str, caption: str) -> str:
    """Create and publish a Reel. Returns the Instagram post ID."""
    # Create container
    container_resp = requests.post(f"{GRAPH_URL}/{user_id}/media", params={
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "share_to_feed": "true",
        "access_token": access_token,
    })
    container_resp.raise_for_status()
    container_id = container_resp.json()["id"]

    # Poll until ready (Instagram processes video server-side)
    for _ in range(30):
        status_resp = requests.get(f"{GRAPH_URL}/{container_id}", params={
            "fields": "status_code,status",
            "access_token": access_token,
        })
        status_resp.raise_for_status()
        status = status_resp.json().get("status_code")
        if status == "FINISHED":
            break
        if status == "ERROR":
            raise RuntimeError("Instagram video processing failed")
        time.sleep(10)
    else:
        raise RuntimeError("Timeout waiting for Instagram video processing")

    # Publish
    publish_resp = requests.post(f"{GRAPH_URL}/{user_id}/media_publish", params={
        "creation_id": container_id,
        "access_token": access_token,
    })
    publish_resp.raise_for_status()
    return publish_resp.json()["id"]
