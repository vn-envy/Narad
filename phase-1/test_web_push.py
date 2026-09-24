"""Web Push, lock-screen privacy, quiet hours and care circles (Vahana, Stage B).

Pushes go through the real pywebpush encryption and VAPID signing; only the
HTTP POST to the push service is stubbed, and the tests decrypt what the
phone would receive.
"""

from __future__ import annotations

import base64
import json
import os
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import http_ece
import pytest
import requests
import server
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

import care_circle
import family_profiles
import kala_scheduler
import onboarding
import profile_context
import vahana
import vahana_push
import workflow_engine

_READINESS = {
    "model_ready": True,
    "research_ready": False,
    "connected_model_providers": [],
    "connected_search_providers": [],
    "connected_subscriptions": [],
    "local_model_ready": False,
}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Phone:
    """A browser push subscription whose payloads the test can decrypt."""

    def __init__(self, name: str) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.auth = os.urandom(16)
        self.endpoint = f"https://fcm.googleapis.com/fcm/send/{name}-{os.urandom(6).hex()}"

    @property
    def subscription(self) -> dict:
        public = self.key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        return {"endpoint": self.endpoint, "keys": {"p256dh": _b64url(public), "auth": _b64url(self.auth)}}

    def read(self, body: bytes) -> dict:
        return json.loads(http_ece.decrypt(body, private_key=self.key, auth_secret=self.auth, version="aes128gcm"))


class PushService:
    """Stands in for FCM: records each POST and answers with a chosen status."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.status: dict[str, int] = {}

    def post(self, url, data=None, headers=None, timeout=None, **_):
        self.sent.append({"url": url, "data": data, "headers": {k.lower(): v for k, v in (headers or {}).items()}, "timeout": timeout})
        status = self.status.get(url, 201)
        return SimpleNamespace(status_code=status, reason="", text="", headers={})

    def to(self, phone: Phone) -> list[dict]:
        return [phone.read(item["data"]) for item in self.sent if item["url"] == phone.endpoint]


@pytest.fixture
def family(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(server, "_AUTH_MODE", "strict")
    monkeypatch.setattr(server, "_login_failures", {})
    monkeypatch.setattr(family_profiles, "FAMILY_PROFILES_PATH", tmp_path / "family_profiles.json")
    monkeypatch.setattr(family_profiles, "PROFILE_SESSION_SECRET_PATH", tmp_path / "profile_secret")
    monkeypatch.setattr(profile_context, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(onboarding, "ONBOARDING_PATH", tmp_path / "onboarding.json")
    monkeypatch.setattr(onboarding, "_connection_readiness", lambda *a, **k: _READINESS)
    monkeypatch.setattr(vahana, "INBOX_DIR", tmp_path / "inbox")
    monkeypatch.setattr(vahana_push, "VAPID_PATH", tmp_path / "config" / "vapid.json")
    monkeypatch.setattr(workflow_engine, "WORKFLOW_DB", tmp_path / "workflows.db")
    monkeypatch.delenv("NTFY_URL", raising=False)
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    monkeypatch.delenv("NARAD_VAPID_SUBJECT", raising=False)
    monkeypatch.setitem(sys.modules, "karma_log", SimpleNamespace(log_karma=lambda *a, **k: None))
    # Midday on the Mac: outside the default quiet hours unless a test says otherwise.
    monkeypatch.setattr(vahana, "_clock", lambda: datetime(2026, 9, 24, 12, 0).astimezone())
    service = PushService()
    monkeypatch.setattr(requests, "post", service.post)
    family_profiles.update_profile("default", pin="8642")
    family_profiles.create_profile("Asha", "2468")
    family_profiles.create_profile("Ravi", "1357")
    yield SimpleNamespace(service=service, client=TestClient(server.app))
    vahana.drain_pushes()


def _headers(client: TestClient, user_id: str, pin: str) -> dict[str, str]:
    response = client.post("/profiles/login", json={"user_id": user_id, "pin": pin})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _subscribe(user_id: str, phone: Phone) -> None:
    vahana_push.add_device(user_id, phone.subscription, "Android phone · Chrome")


def _deliver(**kwargs) -> dict:
    result = vahana.deliver(**kwargs)
    vahana.drain_pushes()
    return result


# ── VAPID keys ───────────────────────────────────────────────────────────────

def test_vapid_keys_are_generated_once_and_private(family) -> None:
    assert not vahana_push.VAPID_PATH.exists()
    keys = vahana_push.load_vapid()
    mode = stat.S_IMODE(vahana_push.VAPID_PATH.stat().st_mode)
    assert mode == 0o600
    assert len(base64.urlsafe_b64decode(keys["public_key"] + "==")) == 65
    assert vahana_push.public_key() == keys["public_key"]
    assert vahana_push.load_vapid()["private_key"] == keys["private_key"]
    assert vahana_push.vapid_subject() == "mailto:narad@example.com"


def test_vapid_subject_comes_from_the_environment(family, monkeypatch) -> None:
    monkeypatch.setenv("NARAD_VAPID_SUBJECT", "mailto:owner@example.com")
    assert vahana_push.vapid_subject() == "mailto:owner@example.com"
    monkeypatch.setenv("NARAD_VAPID_SUBJECT", "not a url")
    assert vahana_push.vapid_subject() == "mailto:narad@example.com"


def test_public_key_route_needs_a_session(family) -> None:
    client = family.client
    assert client.get("/push/vapid-public-key").status_code == 401
    asha = _headers(client, "asha", "2468")
    payload = client.get("/push/vapid-public-key", headers=asha).json()
    assert payload == {"public_key": vahana_push.public_key(), "available": True}


# ── Subscriptions ────────────────────────────────────────────────────────────

def test_subscriptions_are_isolated_between_profiles(family) -> None:
    client = family.client
    asha, ravi = _headers(client, "asha", "2468"), _headers(client, "ravi", "1357")
    phone = Phone("asha")

    created = client.post("/push/subscribe", json={"subscription": phone.subscription, "device_label": "Pixel"}, headers=asha)
    assert created.status_code == 200, created.text
    assert created.json()["device"]["label"] == "Pixel"
    assert "keys" not in created.json()["device"]
    assert stat.S_IMODE((profile_context.PROFILES_DIR / "asha" / "push_devices.json").stat().st_mode) == 0o600

    assert [d["endpoint"] for d in client.get("/push/devices", headers=asha).json()["devices"]] == [phone.endpoint]
    assert client.get("/push/devices", headers=ravi).json()["devices"] == []

    # Ravi can neither remove Asha's phone nor act as Asha.
    gone = client.request("DELETE", "/push/subscribe", json={"endpoint": phone.endpoint}, headers=ravi)
    assert gone.json() == {"status": "not_found", "removed": False}
    assert len(vahana_push.list_devices("asha")) == 1
    spoofed = client.post(
        "/push/subscribe", json={"user_id": "asha", "subscription": Phone("x").subscription}, headers=ravi
    )
    assert spoofed.status_code == 403

    removed = client.request("DELETE", "/push/subscribe", json={"endpoint": phone.endpoint}, headers=asha)
    assert removed.json() == {"status": "removed", "removed": True}
    assert vahana_push.list_devices("asha") == []


@pytest.mark.parametrize("endpoint", [
    "http://fcm.googleapis.com/fcm/send/x",
    "https://127.0.0.1/push",
    "https://localhost:11434/api/generate",
    "https://fcm.googleapis.com.evil.example/x",
    "https://user:pw@fcm.googleapis.com/x",
    "https://fcm.googleapis.com:8443/x",
])
def test_subscribe_refuses_endpoints_that_are_not_push_services(family, endpoint) -> None:
    subscription = {**Phone("x").subscription, "endpoint": endpoint}
    with pytest.raises(ValueError):
        vahana_push.validate_subscription(subscription)
    asha = _headers(family.client, "asha", "2468")
    assert family.client.post("/push/subscribe", json={"subscription": subscription}, headers=asha).status_code == 400


def test_subscribe_refuses_malformed_keys(family) -> None:
    subscription = Phone("x").subscription
    subscription["keys"]["auth"] = _b64url(b"short")
    with pytest.raises(ValueError):
        vahana_push.validate_subscription(subscription)


def test_a_phone_handed_to_someone_else_moves_to_their_profile(family) -> None:
    phone = Phone("shared")
    _subscribe("asha", phone)
    _subscribe("ravi", phone)
    assert vahana_push.list_devices("asha") == []
    assert [d["endpoint"] for d in vahana_push.list_devices("ravi")] == [phone.endpoint]


def test_signing_out_everywhere_stops_pushes_to_old_phones(family) -> None:
    phone = Phone("lost")
    _subscribe("asha", phone)
    family_profiles.revoke_sessions("asha")
    _deliver(kind="reminder", title="Call the bank", body="About the card", user_id="asha")
    assert family.service.sent == []
    assert vahana_push.list_devices("asha") == []


# ── Delivery ─────────────────────────────────────────────────────────────────

def test_deliver_pushes_to_every_device_of_that_profile_only(family) -> None:
    pixel, tablet, ravis = Phone("pixel"), Phone("tablet"), Phone("ravi")
    _subscribe("asha", pixel)
    _subscribe("asha", tablet)
    _subscribe("ravi", ravis)

    result = _deliver(kind="reminder", title="Water the plants", body="Balcony pots", user_id="asha", priority="high")

    assert result["pushed"] is True and result["push"] == {"channel": "web_push", "queued": True, "devices": 2}
    assert len(family.service.to(pixel)) == 1 and len(family.service.to(tablet)) == 1
    assert family.service.to(ravis) == []
    payload = family.service.to(pixel)[0]
    assert set(payload) == {"title", "body", "url", "tag", "kind", "id", "unread"}
    assert payload["id"] == result["event_id"]
    assert payload["url"] == f"/?activity={result['event_id']}"
    assert payload["unread"] == 1
    sent = family.service.sent[0]
    assert sent["headers"]["authorization"].startswith("vapid t=")
    assert sent["headers"]["urgency"] == "high"
    assert int(sent["headers"]["ttl"]) > 0
    assert sent["timeout"] == vahana_push.SEND_TIMEOUT_SECONDS
    # The inbox copy is the source of truth and holds the full text.
    assert vahana.load_inbox("asha")[0]["body"] == "Balcony pots"
    assert vahana_push.list_devices("asha")[0]["last_success_at"]


def test_a_gone_subscription_is_dropped_and_errors_are_kept(family) -> None:
    gone, broken, fine = Phone("gone"), Phone("broken"), Phone("fine")
    for phone in (gone, broken, fine):
        _subscribe("asha", phone)
    family.service.status[gone.endpoint] = 410
    family.service.status[broken.endpoint] = 500

    result = _deliver(kind="reminder", title="Water the plants", body="", user_id="asha")

    assert result["pushed"] is True
    endpoints = {d["endpoint"]: d for d in vahana_push.list_devices("asha")}
    assert gone.endpoint not in endpoints
    assert "500" in endpoints[broken.endpoint]["last_error"]
    assert endpoints[fine.endpoint]["last_error"] is None
    family.service.status[fine.endpoint] = 404
    _deliver(kind="reminder", title="Again", body="", user_id="asha")
    assert fine.endpoint not in {d["endpoint"] for d in vahana_push.list_devices("asha")}


def test_a_push_failure_never_breaks_delivery(family, monkeypatch) -> None:
    _subscribe("asha", Phone("pixel"))

    def explode(*args, **kwargs):
        raise requests.ConnectionError("push service down")

    monkeypatch.setattr(requests, "post", explode)
    result = _deliver(kind="reminder", title="Still delivered", body="", user_id="asha")
    assert result["status"] == "ok"
    assert vahana.load_inbox("asha")[0]["title"] == "Still delivered"
    assert "ConnectionError" in vahana_push.list_devices("asha")[0]["last_error"]


def test_approval_requests_use_the_callers_deep_link_and_share_a_tag(family) -> None:
    phone = Phone("pixel")
    _subscribe("asha", phone)
    request = _deliver(
        user_id="asha", kind="approval_request", title="Send the email to the school?",
        body="To: office@example.com", data={"proposal_id": "p-42", "url": "/?approval=p-42"}, priority="high",
    )
    _deliver(
        user_id="asha", kind="approval_result", title="Email sent", body="Approved on your phone",
        data={"proposal_id": "p-42", "url": "/?approval=p-42"},
    )
    first, second = family.service.to(phone)
    assert request["event_id"] == first["id"]
    assert first["url"] == "/?approval=p-42" and first["kind"] == "approval_request"
    assert first["tag"] == second["tag"] == "approval-p-42"
    assert first["title"] == "Narad needs your OK"
    assert vahana.load_inbox("asha")[1]["kind"] == "approval_request"


def test_only_same_origin_deep_links_reach_the_phone(family) -> None:
    phone = Phone("pixel")
    _subscribe("asha", phone)
    for url in ("https://evil.example/x", "//evil.example/x", "javascript:alert(1)"):
        result = _deliver(user_id="asha", kind="reminder", title="x", body="", data={"url": url})
        assert family.service.to(phone)[-1]["url"] == f"/?activity={result['event_id']}"


# ── Lock screen and quiet hours ──────────────────────────────────────────────

def test_the_lock_screen_shows_generic_text_by_default(family) -> None:
    phone = Phone("pixel")
    _subscribe("asha", phone)
    _deliver(user_id="asha", kind="medicine_reminder", title="Medication: Thyroxine", body="Take Thyroxine 50mcg")
    _deliver(user_id="asha", kind="approval_request", title="Pay the electricity bill?", body="Rs 2,340")
    _deliver(user_id="asha", kind="triage", title="Mail triage: 3 unread", body="From the clinic")

    shown = family.service.to(phone)
    assert [(p["title"], p["body"]) for p in shown] == [
        ("Narad has a reminder for you", "Open Narad to see it."),
        ("Narad needs your OK", "Open Narad to review it."),
        ("Narad has an update for you", "Open Narad to see it."),
    ]
    assert "Thyroxine" not in json.dumps(shown)
    assert vahana.load_inbox("asha")[-1]["body"] == "Take Thyroxine 50mcg"

    vahana.update_preferences("asha", {"lock_screen_details": True})
    _deliver(user_id="asha", kind="medicine_reminder", title="Medication: Thyroxine", body="Take Thyroxine 50mcg")
    assert family.service.to(phone)[-1]["title"] == "Medication: Thyroxine"
    assert family.service.to(phone)[-1]["body"] == "Take Thyroxine 50mcg"


def test_preferences_default_validate_and_stay_per_profile(family) -> None:
    client = family.client
    asha, ravi = _headers(client, "asha", "2468"), _headers(client, "ravi", "1357")
    defaults = client.get("/notifications/preferences", headers=asha).json()
    assert defaults["lock_screen_details"] is False
    assert defaults["quiet_hours"] == {"enabled": True, "start": "22:00", "end": "07:00"}

    saved = client.put(
        "/notifications/preferences",
        json={"quiet_hours": {"start": "23:30"}, "lock_screen_details": True, "timezone": "Asia/Kolkata"},
        headers=asha,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["quiet_hours"] == {"enabled": True, "start": "23:30", "end": "07:00"}
    assert client.get("/notifications/preferences", headers=ravi).json()["lock_screen_details"] is False
    assert client.put("/notifications/preferences", json={"quiet_hours": {"start": "25:00"}}, headers=asha).status_code == 400
    assert client.put("/notifications/preferences", json={"timezone": "Mars/Base"}, headers=asha).status_code == 400
    assert client.put("/notifications/preferences", json={"user_id": "ravi", "lock_screen_details": True}, headers=asha).status_code == 403


def test_quiet_hours_hold_everything_but_urgent_and_your_own_medicine(family, monkeypatch) -> None:
    phone = Phone("pixel")
    _subscribe("asha", phone)
    vahana.update_preferences("asha", {"timezone": "Asia/Kolkata"})
    night = datetime(2026, 9, 24, 23, 15, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    monkeypatch.setattr(vahana, "_clock", lambda: night)

    held = _deliver(user_id="asha", kind="reminder", title="Pay rent", body="", priority="high")
    assert held["pushed"] is False and held["push"]["held"] == "quiet_hours"
    assert vahana.load_inbox("asha")[0]["title"] == "Pay rent"  # waits silently in the inbox
    assert _deliver(user_id="asha", kind="health_alert", title="x", body="", priority="urgent")["pushed"] is True
    on_time = _deliver(user_id="asha", kind="medicine_reminder", title="Medication: Thyroxine", body="", data={"late": False})
    assert on_time["pushed"] is True
    late = _deliver(user_id="asha", kind="medicine_reminder", title="Medication: Thyroxine", body="", data={"late": True})
    assert late["pushed"] is False

    vahana.update_preferences("asha", {"medicine_in_quiet_hours": False})
    assert _deliver(user_id="asha", kind="medicine_reminder", title="x", body="")["pushed"] is False
    vahana.update_preferences("asha", {"quiet_hours": {"enabled": False}})
    assert _deliver(user_id="asha", kind="reminder", title="x", body="")["pushed"] is True
    assert len(family.service.to(phone)) == 3


def test_quiet_hours_windows_that_cross_midnight(family) -> None:
    prefs = vahana.load_preferences("asha")
    ist = timezone(timedelta(hours=5, minutes=30))
    prefs["timezone"] = "Asia/Kolkata"
    at = lambda hour, minute=0: datetime(2026, 9, 24, hour, minute, tzinfo=ist)  # noqa: E731
    assert vahana.in_quiet_hours(prefs, at(22, 0))
    assert vahana.in_quiet_hours(prefs, at(3, 0))
    assert not vahana.in_quiet_hours(prefs, at(7, 0))
    assert not vahana.in_quiet_hours(prefs, at(12, 0))
    prefs["quiet_hours"] = {"enabled": True, "start": "13:00", "end": "15:00"}
    assert vahana.in_quiet_hours(prefs, at(14, 0)) and not vahana.in_quiet_hours(prefs, at(15, 0))


# ── ntfy fallback ────────────────────────────────────────────────────────────

class _NtfyResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


def test_ntfy_is_only_a_fallback_for_profiles_without_web_push(family, monkeypatch) -> None:
    monkeypatch.setenv("NTFY_URL", "https://ntfy.example")
    monkeypatch.setenv("NTFY_TOPIC", "narad")
    posted: list = []
    monkeypatch.setattr(
        vahana.urllib.request, "urlopen", lambda request, timeout=0: posted.append(request) or _NtfyResponse()
    )

    fallback = _deliver(user_id="ravi", kind="medicine_reminder", title="Medication: Metformin", body="Take it")
    assert fallback["push"]["channel"] == "ntfy" and fallback["pushed"] is True
    assert posted[0].full_url.startswith("https://ntfy.example/narad-ravi-")
    # ntfy shows on the lock screen too, so it gets the same generic text.
    assert posted[0].headers["Title"] == "Narad has a reminder for you"
    assert posted[0].headers["Tags"] == "narad"
    assert b"Metformin" not in posted[0].data

    phone = Phone("ravi")
    _subscribe("ravi", phone)
    web = _deliver(user_id="ravi", kind="medicine_reminder", title="Medication: Metformin", body="Take it")
    assert web["push"]["channel"] == "web_push"
    assert len(posted) == 1 and len(family.service.to(phone)) == 1


# ── Care circles ─────────────────────────────────────────────────────────────

def test_only_the_subject_grants_and_revokes(family) -> None:
    client = family.client
    asha, ravi, owner = (
        _headers(client, "asha", "2468"), _headers(client, "ravi", "1357"), _headers(client, "default", "8642")
    )
    grant = {"grants": [{"carer": "ravi", "kinds": ["medicine_reminder", "health_alert"]}]}

    saved = client.put("/care-circle", json=grant, headers=asha)
    assert saved.status_code == 200, saved.text
    assert saved.json()["grants"][0]["carer_name"] == "Ravi"
    assert care_circle.carers_for("asha", "medicine_reminder") == ["ravi"]
    assert care_circle.carers_for("asha", "task_done") == []

    # The owner cannot share someone else's notifications, by body or on the Mac.
    assert client.put("/care-circle", json={**grant, "user_id": "asha"}, headers=owner).status_code == 403
    host = client.put("/care-circle", json={"grants": []}, headers={"X-Narad-Profile-ID": "asha"})
    assert host.status_code in (401, 403)
    assert care_circle.carers_for("asha", "medicine_reminder") == ["ravi"]
    # The carer cannot widen what they were given.
    assert client.put("/care-circle", json={**grant, "user_id": "asha"}, headers=ravi).status_code == 403

    for bad in (
        [{"carer": "asha", "kinds": ["task_done"]}],
        [{"carer": "nobody", "kinds": ["task_done"]}],
        [{"carer": "ravi", "kinds": ["everything"]}],
        [{"carer": "", "kinds": ["task_done"]}],
    ):
        assert client.put("/care-circle", json={"grants": bad}, headers=asha).status_code == 400, bad

    shared = client.get("/care-circle/shared-with-me", headers=ravi).json()["shared"]
    assert shared == [{
        "subject": "asha", "subject_name": "Asha",
        "kinds": ["medicine_reminder", "health_alert"], "granted_at": shared[0]["granted_at"],
    }]
    assert client.get("/care-circle/shared-with-me", headers=owner).json()["shared"] == []

    revoked = client.put("/care-circle", json={"grants": []}, headers=asha)
    assert revoked.json()["grants"] == []
    assert client.get("/care-circle/shared-with-me", headers=ravi).json()["shared"] == []


def test_a_carer_can_leave_the_circle(family) -> None:
    client = family.client
    asha, ravi, owner = (
        _headers(client, "asha", "2468"), _headers(client, "ravi", "1357"), _headers(client, "default", "8642")
    )
    client.put("/care-circle", json={"grants": [
        {"carer": "ravi", "kinds": ["task_done"]}, {"carer": "default", "kinds": ["task_done"]},
    ]}, headers=asha)

    assert client.delete("/care-circle/shared-with-me/asha", headers=ravi).json() == {"status": "left", "subject": "asha"}
    assert client.delete("/care-circle/shared-with-me/asha", headers=ravi).status_code == 404
    assert care_circle.carers_for("asha", "task_done") == ["default"]
    assert [g["carer"] for g in client.get("/care-circle", headers=asha).json()["grants"]] == ["default"]
    assert client.delete("/care-circle/shared-with-me/asha", headers=owner).status_code == 200


def test_carers_get_only_the_title_and_a_short_summary(family) -> None:
    care_circle.set_circle("asha", [{"carer": "ravi", "kinds": ["medicine_reminder", "approval_request"]}])
    ravis_phone, ashas_phone = Phone("ravi"), Phone("asha")
    _subscribe("ravi", ravis_phone)
    _subscribe("asha", ashas_phone)
    long_body = "Take Thyroxine 50mcg on an empty stomach.\nDr. note: dose raised after the last TSH test " + "x" * 400

    result = _deliver(
        user_id="asha", kind="medicine_reminder", title="Medication: Thyroxine", body=long_body,
        data={"reminder_id": 3, "slot": "07:00", "profile_id": "asha", "url": "/?approval=secret"},
    )

    assert result["shared_with"] == ["ravi"]
    copy = vahana.load_inbox("ravi")[0]
    assert copy["title"] == "Medication: Thyroxine"
    assert copy["body"] == "Take Thyroxine 50mcg on an empty stomach."
    assert copy["data"] == {"shared_from": "asha", "shared_from_name": "Asha"}
    assert copy["shared_from"] == "asha" and copy["kind"] == "medicine_reminder"
    assert "TSH" not in json.dumps(copy)
    pushed = family.service.to(ravis_phone)[0]
    assert pushed["title"] == "Narad has a family update"
    assert pushed["url"] == f"/?activity={copy['id']}"
    assert pushed["tag"] == copy["id"]

    approval = _deliver(
        user_id="asha", kind="approval_request", title="Pay the school fees?", body="Rs 18,000 to Example School",
        data={"proposal_id": "p-9", "url": "/?approval=p-9"}, priority="high",
    )
    assert approval["shared_with"] == ["ravi"]
    fyi = vahana.load_inbox("ravi")[0]
    assert fyi["body"] == "Asha has something waiting for their OK. Only Asha can decide it."
    assert "p-9" not in json.dumps(fyi) and "18,000" not in json.dumps(fyi)
    # Kinds that were not shared stay private.
    assert _deliver(user_id="asha", kind="task_done", title="Path complete", body="")["shared_with"] == []
    assert len(vahana.load_inbox("ravi")) == 2


def test_shared_copies_follow_the_carers_own_settings(family, monkeypatch) -> None:
    care_circle.set_circle("asha", [{"carer": "ravi", "kinds": ["medicine_reminder", "health_alert"]}])
    ravis_phone, ashas_phone = Phone("ravi"), Phone("asha")
    _subscribe("ravi", ravis_phone)
    _subscribe("asha", ashas_phone)
    vahana.update_preferences("ravi", {"lock_screen_details": True})
    for profile in ("asha", "ravi"):
        vahana.update_preferences(profile, {"timezone": "Asia/Kolkata"})
    night = datetime(2026, 9, 24, 22, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    monkeypatch.setattr(vahana, "_clock", lambda: night)

    # Asha's own on-time medicine buzzes her phone; Ravi's copy waits for morning.
    _deliver(user_id="asha", kind="medicine_reminder", title="Medication: Thyroxine", body="Take it", data={"late": False})
    assert len(family.service.to(ashas_phone)) == 1
    assert family.service.to(ravis_phone) == []
    assert vahana.load_inbox("ravi")[0]["title"] == "Medication: Thyroxine"

    monkeypatch.setattr(vahana, "_clock", lambda: night.replace(hour=12))
    _deliver(user_id="asha", kind="health_alert", title="Medicine reminder not opened: Thyroxine", body="Not opened yet")
    shown = family.service.to(ravis_phone)[0]
    assert shown["title"] == "Medicine reminder not opened: Thyroxine"  # Ravi allows details


# ── Reminders that feed the circle ───────────────────────────────────────────

def test_medicine_reminders_fire_for_their_profile_with_a_shareable_kind(family, monkeypatch) -> None:
    from health_skill import set_medication_reminder

    monkeypatch.setattr("health_skill._DB_PATH", family_profiles.FAMILY_PROFILES_PATH.parent / "health.db")
    monkeypatch.setattr(kala_scheduler, "HEALTH_DB", family_profiles.FAMILY_PROFILES_PATH.parent / "health.db")
    monkeypatch.setenv("NARAD_DOSE_FOLLOWUP_MINUTES", "60")
    care_circle.set_circle("asha", [{"carer": "ravi", "kinds": ["medicine_reminder", "health_alert"]}])
    with profile_context.profile_scope("asha"):
        set_medication_reminder("Thyroxine", "50mcg", "7am")
    state: dict = {}

    assert kala_scheduler._fire_due_reminders(datetime(2026, 9, 24, 7, 5), state) == 1
    reminder = vahana.load_inbox("asha")[0]
    assert reminder["kind"] == "medicine_reminder" and reminder["data"]["late"] is False
    assert vahana.load_inbox("ravi")[0]["title"] == "Medication: Thyroxine"
    assert vahana.load_inbox("default") == []

    # Not opened an hour later: one health alert, to Asha and her carer.
    assert kala_scheduler._fire_dose_follow_ups(datetime(2026, 9, 24, 7, 30), state) == 0
    assert kala_scheduler._fire_dose_follow_ups(datetime(2026, 9, 24, 8, 6), state) == 1
    alert = vahana.load_inbox("asha")[0]
    assert alert["kind"] == "health_alert" and "not been opened" in alert["body"]
    assert vahana.load_inbox("ravi")[0]["kind"] == "health_alert"
    assert kala_scheduler._fire_dose_follow_ups(datetime(2026, 9, 24, 9, 0), state) == 0


def test_an_opened_reminder_needs_no_follow_up(family, monkeypatch) -> None:
    monkeypatch.setenv("NARAD_DOSE_FOLLOWUP_MINUTES", "60")
    state: dict = {}
    result = vahana.deliver(user_id="asha", kind="medicine_reminder", title="Medication: Thyroxine", body="")
    kala_scheduler._schedule_dose_follow_up(state, datetime(2026, 9, 24, 7, 0), "asha", "Thyroxine", "07:00", result)
    vahana.mark_read("asha", [result["event_id"]])
    assert kala_scheduler._fire_dose_follow_ups(datetime(2026, 9, 24, 8, 1), state) == 0
    assert state["dose_follow_ups"] == {}

    monkeypatch.setenv("NARAD_DOSE_FOLLOWUP_MINUTES", "0")
    kala_scheduler._schedule_dose_follow_up(state, datetime(2026, 9, 24, 7, 0), "asha", "Thyroxine", "07:00", result)
    assert state["dose_follow_ups"] == {}


def test_a_finished_path_is_a_task_done(family) -> None:
    care_circle.set_circle("asha", [{"carer": "ravi", "kinds": ["task_done"]}])
    run = workflow_engine.start_workflow_run(
        "travel", user_id="asha",
        inputs={"origin": "Delhi", "destination": "Goa", "dates": "10-12 Nov", "travelers": "2", "budget": "INR 60,000"},
    )
    workflow_engine._notify_completed(run)
    done = vahana.load_inbox("asha")[0]
    assert done["kind"] == "task_done"
    assert done["data"]["workflow_run_id"] == run.run_id
    assert vahana.load_inbox("ravi")[0]["body"] == f"{run.title} is complete."


# ── The service worker route ─────────────────────────────────────────────────

def test_the_service_worker_is_public_and_never_cached(family) -> None:
    response = family.client.get("/sw.js")
    built = Path(server.__file__).resolve().parent.parent / "phase-4" / "frontend" / "dist" / "sw.js"
    if not built.exists():
        assert response.status_code == 404
        return
    assert response.status_code == 200
    assert response.headers["cache-control"].startswith("no-cache")
    assert "javascript" in response.headers["content-type"]
