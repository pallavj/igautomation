"""
Publer API integration for scheduling/publishing Instagram posts.

Publer is a social media scheduling platform with a public REST API that supports
Instagram auto-publishing for Business/Creator accounts.

Docs: https://publer.com/docs
Auth: Bearer-API token from Publer Settings > Integrations > API
"""
import os
import mimetypes
import requests


PUBLER_BASE = "https://publer.com/api/v1"


def _headers(api_key: str, workspace_id: str) -> dict:
    return {
        "Authorization": f"Bearer-API {api_key}",
        "Publer-Workspace-Id": workspace_id,
    }


def upload_media(api_key: str, workspace_id: str, media_path: str) -> str:
    """
    Upload a media file to Publer and return its media ID.
    Supports images (JPG, PNG, WEBP) and videos (MP4, MOV).
    """
    mime, _ = mimetypes.guess_type(media_path)
    if not mime:
        ext = os.path.splitext(media_path)[1].lower()
        mime = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".mp4": "video/mp4",
            ".mov": "video/quicktime",
        }.get(ext, "application/octet-stream")

    with open(media_path, "rb") as f:
        resp = requests.post(
            f"{PUBLER_BASE}/media/upload",
            headers=_headers(api_key, workspace_id),
            files={"file": (os.path.basename(media_path), f, mime)},
            timeout=180,  # videos can be large
        )

    if not resp.ok:
        raise RuntimeError(
            f"Publer media upload failed ({resp.status_code}): {resp.text[:300]}"
        )

    data = resp.json()
    # Publer returns the media object; "id" is the key we need
    media_id = data.get("id") or data.get("media_id")
    if not media_id:
        raise RuntimeError(f"Publer upload response missing media ID: {data}")
    return str(media_id)


def schedule_post(
    api_key: str,
    workspace_id: str,
    account_id: str,
    caption: str,
    media_path: str,
    scheduled_at: str,   # ISO 8601 with timezone, e.g. "2026-03-25T14:30:00+05:30"
    media_type: str,     # "image" or "video"
) -> dict:
    """
    Upload media to Publer, then schedule an Instagram post.
    Returns the Publer API response dict.

    Raises RuntimeError on any API failure.
    """
    # Step 1 — upload media
    media_id = upload_media(api_key, workspace_id, media_path)

    # Step 2 — schedule post
    ig_content_type = "photo" if media_type == "image" else "reel"
    publer_media_type = "image" if media_type == "image" else "video"

    payload = {
        "bulk": {
            "state": "scheduled",
            "posts": [
                {
                    "networks": {
                        "instagram": {
                            "type": ig_content_type,
                            "text": caption,
                            "media": [
                                {"id": media_id, "type": publer_media_type}
                            ],
                        }
                    },
                    "accounts": [
                        {"id": account_id, "scheduled_at": scheduled_at}
                    ],
                }
            ],
        }
    }

    resp = requests.post(
        f"{PUBLER_BASE}/posts/schedule",
        headers={
            **_headers(api_key, workspace_id),
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )

    if not resp.ok:
        raise RuntimeError(
            f"Publer scheduling failed ({resp.status_code}): {resp.text[:400]}"
        )

    return resp.json()


def get_accounts(api_key: str, workspace_id: str) -> list:
    """
    Fetch the list of connected social accounts in this Publer workspace.
    Returns a list of account dicts with at least 'id' and 'name'.
    """
    resp = requests.get(
        f"{PUBLER_BASE}/accounts",
        headers=_headers(api_key, workspace_id),
        timeout=15,
    )
    if not resp.ok:
        raise RuntimeError(
            f"Publer accounts fetch failed ({resp.status_code}): {resp.text[:300]}"
        )
    return resp.json().get("accounts", [])
