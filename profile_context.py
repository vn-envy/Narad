"""Request-local profile identity and profile-scoped storage helpers."""

from __future__ import annotations

import contextvars
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from narad_config import PROFILES_DIR

_SAFE_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
_current_profile_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "narad_profile_id", default="default"
)


def validate_profile_id(profile_id: str) -> str:
    value = str(profile_id or "default").strip().lower()
    if not _SAFE_PROFILE_ID.fullmatch(value):
        raise ValueError("profile id must use lowercase letters, numbers, dashes, or underscores")
    return value


def current_profile_id() -> str:
    return _current_profile_id.get()


def set_current_profile(profile_id: str) -> contextvars.Token[str]:
    return _current_profile_id.set(validate_profile_id(profile_id))


def reset_current_profile(token: contextvars.Token[str]) -> None:
    _current_profile_id.reset(token)


@contextmanager
def profile_scope(profile_id: str) -> Iterator[str]:
    safe_id = validate_profile_id(profile_id)
    token = set_current_profile(safe_id)
    try:
        yield safe_id
    finally:
        reset_current_profile(token)


def profile_root(profile_id: str | None = None) -> Path:
    safe_id = validate_profile_id(profile_id or current_profile_id())
    path = PROFILES_DIR / safe_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def profile_data_path(
    filename: str,
    *,
    profile_id: str | None = None,
    legacy_default: Path | None = None,
) -> Path:
    """Return an isolated path, preserving pre-family data for `default`."""
    safe_id = validate_profile_id(profile_id or current_profile_id())
    if safe_id == "default" and legacy_default is not None:
        return legacy_default
    return profile_root(safe_id) / filename


def profile_relative_path(*parts: str, profile_id: str | None = None) -> Path:
    return profile_root(profile_id) / Path(*parts)
