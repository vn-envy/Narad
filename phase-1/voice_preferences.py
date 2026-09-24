"""
Per-profile voice preferences, stored as profiles/<id>/voice.json.

  reply_language     "en" | "hi" | "auto" — what voice mode asks Narad to answer in
  script             "devanagari" | "roman" — how Hindi replies are written
  keep_voice_on_mac  bool — speech-to-text and read-aloud use local engines only
                     (default off: Sarvam, once rated trusted, is far better in Hindi)

Reads never create directories, so the TTS/STT tier checks that consult this
on every request cost one small file read.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

import profile_context

REPLY_LANGUAGES = ("en", "hi", "auto")
SCRIPTS = ("devanagari", "roman")
DEFAULTS: dict[str, Any] = {
    "reply_language": "en",
    "script": "devanagari",
    "keep_voice_on_mac": False,
}
_LOCK = threading.Lock()


def _path(profile_id: str | None) -> Path:
    safe_id = profile_context.validate_profile_id(profile_id or profile_context.current_profile_id())
    return profile_context.PROFILES_DIR / safe_id / "voice.json"


def _clean(raw: dict[str, Any]) -> dict[str, Any]:
    prefs = dict(DEFAULTS)
    if raw.get("reply_language") in REPLY_LANGUAGES:
        prefs["reply_language"] = raw["reply_language"]
    if raw.get("script") in SCRIPTS:
        prefs["script"] = raw["script"]
    if isinstance(raw.get("keep_voice_on_mac"), bool):
        prefs["keep_voice_on_mac"] = raw["keep_voice_on_mac"]
    return prefs


def load(profile_id: str | None = None) -> dict[str, Any]:
    """The profile's preferences (the current request's profile by default)."""
    try:
        raw = json.loads(_path(profile_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    return _clean(raw if isinstance(raw, dict) else {})


def save(updates: dict[str, Any], profile_id: str | None = None) -> dict[str, Any]:
    """Merge validated `updates` into the profile's preferences; ValueError on bad values."""
    if updates.get("reply_language") not in (None, *REPLY_LANGUAGES):
        raise ValueError(f"reply_language must be one of {', '.join(REPLY_LANGUAGES)}")
    if updates.get("script") not in (None, *SCRIPTS):
        raise ValueError(f"script must be one of {', '.join(SCRIPTS)}")
    if updates.get("keep_voice_on_mac") is not None and not isinstance(updates["keep_voice_on_mac"], bool):
        raise ValueError("keep_voice_on_mac must be true or false")
    pid = profile_context.validate_profile_id(profile_id or profile_context.current_profile_id())
    with _LOCK:
        prefs = load(pid)
        prefs.update({key: value for key, value in updates.items() if key in DEFAULTS and value is not None})
        path = profile_context.profile_root(pid) / "voice.json"
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(json.dumps(prefs, indent=2, sort_keys=True), encoding="utf-8")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        temporary.replace(path)
    return prefs


def cloud_voice_allowed(profile_id: str | None = None) -> bool:
    """False when the person asked to keep their voice on the Mac."""
    try:
        return not load(profile_id)["keep_voice_on_mac"]
    except ValueError:  # an invalid profile id: stay local
        return False
