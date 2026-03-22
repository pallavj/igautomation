"""
Google Drive public folder sync.

Accesses Google Drive folders shared as "Anyone with the link can view".
Requires GOOGLE_API_KEY env var — a server-side API key from Google Cloud Console
(no OAuth needed; key only needs Drive API enabled).

Folder listing and file downloads both use the Drive API v3.
"""
import os
import re
import requests

DRIVE_API   = "https://www.googleapis.com/drive/v3"
GOOGLE_KEY  = os.environ.get("GOOGLE_API_KEY", "")

# MIME types we care about
IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
VIDEO_MIMES = {"video/mp4", "video/quicktime", "video/x-msvideo",
               "video/x-matroska", "video/x-m4v"}
TEXT_MIMES  = {"text/plain"}
GDOC_MIME   = "application/vnd.google-apps.document"


# ── Folder ID extraction ───────────────────────────────────────────────────────

def extract_folder_id(url_or_id: str) -> str | None:
    """
    Parse a Google Drive share URL and return the folder ID.
    Also accepts a bare folder ID string.

    Supported URL forms:
      https://drive.google.com/drive/folders/FOLDER_ID
      https://drive.google.com/drive/folders/FOLDER_ID?usp=sharing
    """
    patterns = [
        r"/folders/([a-zA-Z0-9_-]+)",
        r"id=([a-zA-Z0-9_-]+)",
    ]
    for pat in patterns:
        m = re.search(pat, url_or_id)
        if m:
            return m.group(1)
    # Bare ID (alphanumeric, 25–44 chars typical for Drive IDs)
    stripped = url_or_id.strip()
    if re.match(r"^[a-zA-Z0-9_-]{20,}$", stripped):
        return stripped
    return None


# ── Folder listing ─────────────────────────────────────────────────────────────

def list_folder_files(folder_id: str) -> list[dict]:
    """
    Return all non-trashed files in a public Drive folder.
    Each dict has: id, name, mimeType, size (optional), modifiedTime.

    Raises RuntimeError on API error (e.g. key missing, folder not public).
    """
    if not GOOGLE_KEY:
        raise RuntimeError(
            "GOOGLE_API_KEY environment variable is not set. "
            "Add it to your .env file or Render environment."
        )

    files, page_token = [], None
    while True:
        params = {
            "q":         f"'{folder_id}' in parents and trashed = false",
            "key":       GOOGLE_KEY,
            "fields":    "nextPageToken,files(id,name,mimeType,size,modifiedTime)",
            "pageSize":  100,
        }
        if page_token:
            params["pageToken"] = page_token

        resp = requests.get(f"{DRIVE_API}/files", params=params, timeout=20)
        if not resp.ok:
            raise RuntimeError(
                f"Drive API error {resp.status_code}: {resp.text[:400]}"
            )

        data = resp.json()
        files.extend(data.get("files", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return files


# ── File reading / downloading ─────────────────────────────────────────────────

def download_file(file_id: str, dest_path: str) -> None:
    """Download a binary file (image/video) from public Drive to dest_path."""
    if not GOOGLE_KEY:
        raise RuntimeError("GOOGLE_API_KEY not set.")

    resp = requests.get(
        f"{DRIVE_API}/files/{file_id}",
        params={"alt": "media", "key": GOOGLE_KEY},
        stream=True,
        timeout=180,
    )
    resp.raise_for_status()

    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    with open(dest_path, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=8192):
            fh.write(chunk)


def read_text_file(file_id: str) -> str:
    """Download and return contents of a plain .txt file from public Drive."""
    if not GOOGLE_KEY:
        raise RuntimeError("GOOGLE_API_KEY not set.")

    resp = requests.get(
        f"{DRIVE_API}/files/{file_id}",
        params={"alt": "media", "key": GOOGLE_KEY},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.text


def read_google_doc(file_id: str) -> str:
    """Export a Google Doc as plain text."""
    if not GOOGLE_KEY:
        raise RuntimeError("GOOGLE_API_KEY not set.")

    resp = requests.get(
        f"{DRIVE_API}/files/{file_id}/export",
        params={"mimeType": "text/plain", "key": GOOGLE_KEY},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.text


# ── Guidelines detection ───────────────────────────────────────────────────────

_GUIDE_NAMES = {"guidelines", "theme", "brief", "description", "notes", "style"}

def find_guidelines(files: list[dict]) -> str:
    """
    Scan folder file list for a guidelines document.
    Priority: files whose stem matches _GUIDE_NAMES, then any .txt / Google Doc.
    Returns the text content, or '' if nothing found.
    """
    def _read(f: dict) -> str:
        try:
            if f["mimeType"] == GDOC_MIME:
                return read_google_doc(f["id"])
            return read_text_file(f["id"])
        except Exception:
            return ""

    # Priority pass
    for f in files:
        stem = os.path.splitext(f["name"].lower())[0]
        if stem in _GUIDE_NAMES and f["mimeType"] in TEXT_MIMES | {GDOC_MIME}:
            text = _read(f)
            if text:
                return text.strip()

    # Fallback: any text file
    for f in files:
        if f["mimeType"] in TEXT_MIMES | {GDOC_MIME}:
            text = _read(f)
            if text:
                return text.strip()

    return ""


# ── Convenience helpers ────────────────────────────────────────────────────────

def is_media(mime: str) -> bool:
    return mime in IMAGE_MIMES or mime in VIDEO_MIMES

def media_type(mime: str) -> str:
    return "video" if mime in VIDEO_MIMES else "image"

def file_extension(name: str, mime: str) -> str:
    """Best-effort extension from filename, falling back to MIME type."""
    _, ext = os.path.splitext(name)
    if ext:
        return ext.lower()
    return {
        "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
        "video/mp4": ".mp4", "video/quicktime": ".mov",
    }.get(mime, "")
