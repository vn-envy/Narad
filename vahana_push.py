"""
Vahana Web Push — self-hosted push to each family member's own phones.

No push vendor account: the host Mac signs every push with its own VAPID key
(RFC 8292) and the browser's push service carries the end-to-end encrypted
payload (RFC 8291) to the phone.

  Keys     NARAD_HOME/config/vapid.json (0600, generated on first use, never
           committed). The VAPID subject is NARAD_VAPID_SUBJECT (a mailto: or
           https: URL) or a harmless default.
  Devices  profiles/<id>/push_devices.json (0600), one row per subscribed
           browser. A device is bound to the profile's session epoch, so "sign
           out everywhere" or a PIN change also stops pushes to old phones.
  Sending  A small worker pool: a slow or dead push service never blocks the
           event loop or a delivery. 404/410 from the push service drops the
           subscription for good; other failures are recorded on the device.

Only well-known push services are accepted as endpoints (the server POSTs to
them), so a subscription cannot point the Mac at a local address.
NARAD_PUSH_HOSTS adds exact hostnames for another browser's service.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as _wait_futures
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import profile_context
from narad_config import CONFIG_DIR

log = logging.getLogger("narad.vahana.push")

VAPID_PATH: Path = CONFIG_DIR / "vapid.json"
DEVICES_FILE = "push_devices.json"
DEFAULT_SUBJECT = "mailto:narad@example.com"
MAX_DEVICES_PER_PROFILE = 10
DEFAULT_TTL_SECONDS = 12 * 60 * 60
SEND_TIMEOUT_SECONDS = 10

_PUSH_HOSTS = frozenset({
    "fcm.googleapis.com",             # Chrome and Edge on Android and desktop
    "android.googleapis.com",         # legacy GCM endpoints
    "updates.push.services.mozilla.com",
    "web.push.apple.com",
})
_PUSH_HOST_SUFFIXES = (".push.services.mozilla.com", ".notify.windows.com", ".push.apple.com")

_KEY_LOCK = threading.Lock()
_DEVICE_LOCK = threading.Lock()
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="vahana-push")
_PENDING: set[Future] = set()
_PENDING_LOCK = threading.Lock()
_signer_cache: tuple[str, Any] | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    text = str(value or "").strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON readable only by this user, replacing the file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


# ── Worker pool ───────────────────────────────────────────────────────────────

def submit(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future:
    """Run fn on the push pool. Exceptions are logged, never raised to the caller."""

    def guarded() -> Any:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # a push must never break a delivery
            log.warning("Vahana push task failed: %s", exc)
            return None

    future = _POOL.submit(guarded)
    with _PENDING_LOCK:
        _PENDING.add(future)
    future.add_done_callback(_forget)
    return future


def _forget(future: Future) -> None:
    with _PENDING_LOCK:
        _PENDING.discard(future)


def drain(timeout: float = 15.0) -> None:
    """Wait for queued pushes (tests, and a clean shutdown)."""
    with _PENDING_LOCK:
        pending = list(_PENDING)
    if pending:
        _wait_futures(pending, timeout=timeout)


# ── VAPID keys ────────────────────────────────────────────────────────────────

def vapid_subject() -> str:
    subject = os.environ.get("NARAD_VAPID_SUBJECT", "").strip()
    if subject.startswith(("mailto:", "https://")) and len(subject) <= 200:
        return subject
    return DEFAULT_SUBJECT


def _generate_vapid() -> dict[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    private_raw = key.private_numbers().private_value.to_bytes(32, "big")
    public_raw = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return {"public_key": _b64url(public_raw), "private_key": _b64url(private_raw), "created_at": _now()}


def load_vapid() -> dict[str, str]:
    """The host's VAPID key pair, generated into vapid.json on first use."""
    with _KEY_LOCK:
        try:
            keys = json.loads(VAPID_PATH.read_text(encoding="utf-8"))
            if len(_b64url_decode(keys["public_key"])) == 65 and len(_b64url_decode(keys["private_key"])) == 32:
                return keys
            log.warning("Vahana: %s is malformed; generating a new VAPID key pair", VAPID_PATH)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError, binascii.Error) as exc:
            log.warning("Vahana: could not read %s (%s); generating a new VAPID key pair", VAPID_PATH, exc)
        keys = _generate_vapid()
        _write_private_json(VAPID_PATH, keys)
        log.info("Vahana: VAPID key pair created at %s", VAPID_PATH)
        return keys


def public_key() -> str:
    return load_vapid()["public_key"]


def webpush_available() -> bool:
    try:
        import pywebpush  # noqa: F401
    except ImportError:
        return False
    return True


def _signer() -> Any:
    """A py_vapid signer for the current key, parsed once per key."""
    global _signer_cache
    private_key = load_vapid()["private_key"]
    if _signer_cache is None or _signer_cache[0] != private_key:
        from py_vapid import Vapid

        _signer_cache = (private_key, Vapid.from_raw(private_key.encode("ascii")))
    return _signer_cache[1]


# ── Subscriptions ─────────────────────────────────────────────────────────────

def _allowed_push_host(host: str) -> bool:
    host = host.lower().rstrip(".")
    extra = {item.strip().lower() for item in os.environ.get("NARAD_PUSH_HOSTS", "").split(",") if item.strip()}
    return host in _PUSH_HOSTS or host in extra or host.endswith(_PUSH_HOST_SUFFIXES)


def validate_subscription(subscription: Any) -> dict[str, Any]:
    """A browser PushSubscription.toJSON() → {endpoint, keys}; ValueError if unusable."""
    if not isinstance(subscription, dict):
        raise ValueError("subscription must be an object")
    endpoint = str(subscription.get("endpoint") or "").strip()
    if len(endpoint) > 1024:
        raise ValueError("subscription endpoint is too long")
    parts = urlsplit(endpoint)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("subscription endpoint must be an https URL")
    if parts.port not in (None, 443) or not _allowed_push_host(parts.hostname):
        raise ValueError("subscription endpoint is not a known push service")
    keys = subscription.get("keys") if isinstance(subscription.get("keys"), dict) else {}
    p256dh = str(keys.get("p256dh") or "").strip()
    auth = str(keys.get("auth") or "").strip()
    try:
        client_key = _b64url_decode(p256dh)
        auth_secret = _b64url_decode(auth)
    except (ValueError, binascii.Error):
        raise ValueError("subscription keys are not base64url")
    if len(client_key) != 65 or client_key[0] != 4 or len(auth_secret) != 16:
        raise ValueError("subscription keys have the wrong size")
    return {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}}


def _devices_path(profile_id: str) -> Path:
    # Resolved at call time so tests can point PROFILES_DIR elsewhere; reads
    # never create a profile folder.
    return Path(profile_context.PROFILES_DIR) / profile_context.validate_profile_id(profile_id) / DEVICES_FILE


def _read_devices(profile_id: str) -> list[dict[str, Any]]:
    try:
        rows = json.loads(_devices_path(profile_id).read_text(encoding="utf-8")).get("devices")
    except (OSError, ValueError, AttributeError):
        return []
    return [row for row in rows if isinstance(row, dict) and row.get("endpoint")] if isinstance(rows, list) else []


def _write_devices(profile_id: str, devices: list[dict[str, Any]]) -> None:
    profile_context.profile_root(profile_id)
    _write_private_json(_devices_path(profile_id), {"devices": devices})


def _current_epoch(profile_id: str) -> int | None:
    try:
        from family_profiles import session_epoch

        return session_epoch(profile_id)
    except Exception:
        return None


def _device_id(endpoint: str) -> str:
    import hashlib

    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:16]


def public_device(device: dict[str, Any]) -> dict[str, Any]:
    """What the profile's own devices list shows (never the encryption keys)."""
    return {
        "id": device.get("id") or _device_id(str(device.get("endpoint") or "")),
        "label": device.get("label") or "Phone",
        "endpoint": device.get("endpoint"),
        "service": urlsplit(str(device.get("endpoint") or "")).hostname or "",
        "created_at": device.get("created_at"),
        "last_success_at": device.get("last_success_at"),
        "last_error": device.get("last_error"),
    }


def list_devices(profile_id: str) -> list[dict[str, Any]]:
    return _read_devices(profile_id)


def live_devices(profile_id: str) -> list[dict[str, Any]]:
    """Devices subscribed under the profile's current session epoch.

    Devices from before a PIN change or "sign out everywhere" are dropped:
    the phone re-registers the next time it opens Narad signed in."""
    epoch = _current_epoch(profile_id)
    with _DEVICE_LOCK:
        devices = _read_devices(profile_id)
        if epoch is None:
            return devices
        live = [row for row in devices if int(row.get("epoch") or 0) == epoch]
        if len(live) != len(devices):
            _write_devices(profile_id, live)
            log.info("Vahana: dropped %d signed-out device(s) for %s", len(devices) - len(live), profile_id)
    return live


def add_device(profile_id: str, subscription: Any, label: str = "", *, user_agent: str = "") -> dict[str, Any]:
    """Subscribe one browser for this profile only.

    A browser has one push subscription per site, so if it was registered by
    another profile (a shared or handed-down phone) it moves here: one
    person's notifications never keep arriving on someone else's phone."""
    profile_id = profile_context.validate_profile_id(profile_id)
    clean = validate_subscription(subscription)
    endpoint = clean["endpoint"]
    device = {
        "id": _device_id(endpoint),
        **clean,
        "label": " ".join(str(label or "").split())[:60] or "Phone",
        "user_agent": str(user_agent or "")[:200],
        "epoch": _current_epoch(profile_id) or 0,
        "created_at": _now(),
        "last_success_at": None,
        "last_error": None,
    }
    with _DEVICE_LOCK:
        root = Path(profile_context.PROFILES_DIR)
        others = sorted(path.parent.name for path in root.glob(f"*/{DEVICES_FILE}")) if root.exists() else []
        for other in others:
            if other == profile_id:
                continue
            try:
                rows = _read_devices(other)
            except ValueError:
                continue
            kept = [row for row in rows if row.get("endpoint") != endpoint]
            if len(kept) != len(rows):
                _write_devices(other, kept)
                log.info("Vahana: a device moved from profile %s to %s", other, profile_id)
        devices = [row for row in _read_devices(profile_id) if row.get("endpoint") != endpoint]
        devices.append(device)
        _write_devices(profile_id, devices[-MAX_DEVICES_PER_PROFILE:])
    return device


def remove_device(profile_id: str, endpoint: str) -> bool:
    with _DEVICE_LOCK:
        devices = _read_devices(profile_id)
        kept = [row for row in devices if row.get("endpoint") != endpoint]
        if len(kept) == len(devices):
            return False
        _write_devices(profile_id, kept)
        return True


def _record_result(profile_id: str, endpoint: str, error: str | None) -> None:
    with _DEVICE_LOCK:
        devices = _read_devices(profile_id)
        for row in devices:
            if row.get("endpoint") == endpoint:
                if error:
                    row["last_error"] = f"{_now()} {error}"[:160]
                else:
                    row["last_success_at"] = _now()
                    row["last_error"] = None
                _write_devices(profile_id, devices)
                return


# ── Sending ───────────────────────────────────────────────────────────────────

def _status_code(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None) or getattr(response, "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def send_to_device(
    profile_id: str,
    device: dict[str, Any],
    payload: dict[str, Any],
    *,
    urgency: str = "normal",
    ttl: int = DEFAULT_TTL_SECONDS,
) -> str:
    """One encrypted push. Returns sent | gone | error | unavailable. Blocking."""
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        log.warning("Vahana: pywebpush is not installed; Web Push is off (inbox copy is safe)")
        return "unavailable"
    endpoint = str(device.get("endpoint") or "")
    try:
        webpush(
            subscription_info={"endpoint": endpoint, "keys": device.get("keys") or {}},
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            vapid_private_key=_signer(),
            vapid_claims={"sub": vapid_subject()},
            ttl=int(ttl),
            timeout=SEND_TIMEOUT_SECONDS,
            headers={"Urgency": urgency},
        )
    except WebPushException as exc:
        status = _status_code(exc)
        if status in (404, 410):
            remove_device(profile_id, endpoint)
            log.info("Vahana: push service says a %s device is gone (%s); unsubscribed", profile_id, status)
            return "gone"
        _record_result(profile_id, endpoint, f"push service answered {status or 'with an error'}")
        log.warning("Vahana: push to a %s device failed (%s)", profile_id, status)
        return "error"
    except Exception as exc:
        _record_result(profile_id, endpoint, type(exc).__name__)
        log.warning("Vahana: push to a %s device failed (%s)", profile_id, type(exc).__name__)
        return "error"
    _record_result(profile_id, endpoint, None)
    return "sent"


def push_to_profile(
    profile_id: str,
    payload: dict[str, Any],
    *,
    urgency: str = "normal",
    ttl: int = DEFAULT_TTL_SECONDS,
) -> int:
    """Queue one push per live device of the profile; returns how many were queued."""
    devices = live_devices(profile_id)
    for device in devices:
        submit(send_to_device, profile_id, device, payload, urgency=urgency, ttl=ttl)
    return len(devices)


def send_test(profile_id: str, endpoint: str | None = None) -> list[dict[str, Any]]:
    """Push a test notification now and report each device's result (blocking)."""
    payload = {
        "title": "Narad",
        "body": "Test notification: notifications work on this phone.",
        "url": "/?activity=test",
        "tag": "narad-test",
        "kind": "system",
        "id": f"test-{uuid.uuid4().hex[:8]}",
    }
    devices = [row for row in live_devices(profile_id) if not endpoint or row.get("endpoint") == endpoint]
    results = []
    for device in devices:
        status = send_to_device(profile_id, device, payload, urgency="high", ttl=600)
        results.append({**public_device(device), "status": status})
    return results
