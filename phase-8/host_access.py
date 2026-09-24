"""Who may touch the Narad host: the owner's shell, and the files a profile may use.

Every family profile reaches the same avatars, so the tools themselves must
check the caller. The active profile comes from ``profile_scope`` (set by the
server's auth middleware and carried into worker threads by contextvars).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from narad_config import ARTIFACTS_DIR, ATTACHMENTS_DIR, CONFIG_DIR, PROFILES_DIR
from profile_context import current_profile_id

# Per-profile captures and generated runs live under these ARTIFACTS_DIR folders.
_PROFILE_ARTIFACT_ROOTS = ("computer-use", "phone-use", "runs")


def caller_is_owner() -> bool:
    """True only when the active profile is the Narad owner."""
    from family_profiles import get_profile

    try:
        return bool((get_profile(current_profile_id()) or {}).get("is_owner"))
    except Exception:
        return False


def owner_only(tool: str) -> dict | None:
    """A blocked result for non-owners, or None when the owner is calling."""
    if caller_is_owner():
        return None
    return {
        "status": "blocked",
        "message": f"{tool} runs commands on the Narad host, so only the Narad owner can use it.",
    }


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _attachment_root(profile_id: str) -> Path:
    # Mirrors chat_attachments._owner_key: uploads are filed by a hash of the id.
    return ATTACHMENTS_DIR / "content" / hashlib.sha256(profile_id.encode("utf-8")).hexdigest()[:16]


def path_access_error(path: str | Path) -> str | None:
    """Why the active profile may not read or upload ``path`` (None: allowed).

    Narad's config directory (API token, session secret, provider keys, PIN
    hashes) is never readable by a tool, and no profile may reach another
    profile's files. The owner may otherwise use any path; family members are
    confined to their own profile folder, uploads, and captures.
    """
    resolved = Path(path).expanduser().resolve()
    profile_id = current_profile_id()
    if _within(resolved, CONFIG_DIR):
        return "Narad's configuration and secrets are never available to tools"
    own_roots = [PROFILES_DIR / profile_id, _attachment_root(profile_id)]
    shared_roots = [PROFILES_DIR, ATTACHMENTS_DIR / "content"]
    for name in _PROFILE_ARTIFACT_ROOTS:
        own_roots.append(ARTIFACTS_DIR / name / profile_id)
        shared_roots.append(ARTIFACTS_DIR / name)
    if any(_within(resolved, root) for root in shared_roots) and not any(
        _within(resolved, root) for root in own_roots
    ):
        return "That file belongs to another family profile"
    if caller_is_owner() or any(_within(resolved, root) for root in own_roots):
        return None
    return "Family profiles can only use their own Narad files and uploads"
