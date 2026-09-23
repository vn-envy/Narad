"""Google Workspace tools with scoped OAuth and preview-first writes."""
from __future__ import annotations

import json
import mimetypes
import urllib.parse
import uuid
from pathlib import Path


def _service_access(service: str, write: bool = False) -> bool:
    try:
        from google_workspace import status
        return bool(status()["services"][service]["write" if write else "read"])
    except Exception:
        return False


def search_google_mail(query: str = "", max_results: int = 20) -> dict:
    """Search the connected Gmail account and return metadata and snippets."""
    if not _service_access("gmail"):
        return {"status": "unconfigured", "message": "Connect Gmail read access in System -> Connections.", "messages": []}
    from google_workspace import api_request
    params = urllib.parse.urlencode({"q": query, "maxResults": min(max(1, max_results), 50)})
    listing = api_request(f"https://gmail.googleapis.com/gmail/v1/users/me/messages?{params}")
    messages = []
    for item in listing.get("messages", []):
        detail = api_request(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{item['id']}?"
            "format=metadata&metadataHeaders=From&metadataHeaders=To&metadataHeaders=Subject&metadataHeaders=Date"
        )
        headers = {h.get("name", "").lower(): h.get("value", "") for h in detail.get("payload", {}).get("headers", [])}
        messages.append({
            "id": detail.get("id"), "thread_id": detail.get("threadId"),
            "from": headers.get("from", ""), "to": headers.get("to", ""),
            "subject": headers.get("subject", "(no subject)"), "date": headers.get("date", ""),
            "snippet": detail.get("snippet", ""),
        })
    return {"status": "ok", "query": query, "message_count": len(messages), "messages": messages}


def search_google_drive(query: str = "", max_results: int = 25) -> dict:
    """Search files visible to the connected Google Drive account."""
    if not _service_access("drive"):
        return {"status": "unconfigured", "message": "Connect Google Drive read access in System -> Connections.", "files": []}
    from google_workspace import api_request
    filters = ["trashed = false"]
    if query.strip():
        filters.append(f"fullText contains '{query.strip().replace(chr(39), chr(92) + chr(39))}'")
    params = urllib.parse.urlencode({
        "q": " and ".join(filters), "pageSize": min(max(1, max_results), 100),
        "orderBy": "modifiedTime desc",
        "fields": "files(id,name,mimeType,modifiedTime,size,webViewLink,owners(displayName,emailAddress))",
    })
    payload = api_request(f"https://www.googleapis.com/drive/v3/files?{params}")
    files = payload.get("files", [])
    return {"status": "ok", "query": query, "file_count": len(files), "files": files}


def upload_google_drive(local_path: str, folder_id: str = "", dry_run: bool = True) -> dict:
    """Upload a local file to Drive; preview by default and require confirmation."""
    path = Path(local_path).expanduser().resolve()
    if not path.is_file():
        return {"status": "error", "message": f"File not found: {path}"}
    preview = {"path": str(path), "name": path.name, "size_bytes": path.stat().st_size, "folder_id": folder_id or "My Drive"}
    if dry_run:
        return {"status": "preview", "message": "File not uploaded. Confirm before calling with dry_run=False.", "preview": preview}
    if not _service_access("drive", write=True):
        return {"status": "unconfigured", "message": "Connect Google Drive write access in System -> Connections.", "preview": preview}
    try:
        from dharma import gate_action
        verdict = gate_action("google_drive_upload", avatar="Matsya", detail=str(path), metadata={"size_bytes": path.stat().st_size})
        if not verdict.allowed:
            return {"status": "blocked", "message": "; ".join(verdict.reasons), "preview": preview}
    except Exception as exc:
        return {"status": "blocked", "message": f"Dharma gate unavailable ({exc}) - refusing upload.", "preview": preview}
    from google_workspace import api_raw_request
    boundary = f"narad-{uuid.uuid4().hex}"
    metadata: dict = {"name": path.name}
    if folder_id:
        metadata["parents"] = [folder_id]
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(metadata)}\r\n"
        f"--{boundary}\r\nContent-Type: {mime}\r\n\r\n"
    ).encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    result = api_raw_request(
        "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,name,mimeType,webViewLink",
        body=body, content_type=f"multipart/related; boundary={boundary}",
    )
    return {"status": "ok", "message": f"Uploaded {path.name} to Google Drive.", "file": result, "preview": preview}


def create_google_photos_picker() -> dict:
    """Create a session where the user chooses exactly which photos Narad may read."""
    if not _service_access("photos"):
        return {"status": "unconfigured", "message": "Connect Google Photos in System -> Connections."}
    from google_workspace import api_request
    session = api_request("https://photospicker.googleapis.com/v1/sessions", method="POST", payload={})
    return {"status": "ok", "session_id": session.get("id"), "picker_uri": session.get("pickerUri"), "session": session}


def get_google_photos_selection(session_id: str, max_results: int = 50) -> dict:
    """Read metadata for photos explicitly selected in a Photos Picker session."""
    if not _service_access("photos"):
        return {"status": "unconfigured", "message": "Connect Google Photos in System -> Connections.", "media_items": []}
    from google_workspace import api_request
    params = urllib.parse.urlencode({"sessionId": session_id, "pageSize": min(max(1, max_results), 100)})
    payload = api_request(f"https://photospicker.googleapis.com/v1/mediaItems?{params}")
    items = payload.get("mediaItems", [])
    return {"status": "ok", "media_item_count": len(items), "media_items": items, "next_page_token": payload.get("nextPageToken")}
