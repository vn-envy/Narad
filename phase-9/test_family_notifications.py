from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_root = next(path for path in Path(__file__).resolve().parents if (path / "narad_paths.py").exists())
sys.path[:0] = [str(_root)]
import family_profiles
import kala_scheduler
import narad_paths  # noqa: F401
import onboarding
import profile_context
import vahana


@pytest.fixture(autouse=True)
def isolated_family(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(family_profiles, "FAMILY_PROFILES_PATH", tmp_path / "family_profiles.json")
    monkeypatch.setattr(family_profiles, "PROFILE_SESSION_SECRET_PATH", tmp_path / "profile_secret")
    monkeypatch.setattr(profile_context, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(onboarding, "ONBOARDING_PATH", tmp_path / "onboarding.json")
    # health_skill lives in phase-1, importable once narad_paths has run.
    monkeypatch.setattr("health_skill._DB_PATH", tmp_path / "health.db")
    monkeypatch.setattr(kala_scheduler, "HEALTH_DB", tmp_path / "health.db")
    monkeypatch.setattr(vahana, "INBOX_DIR", tmp_path / "inbox")
    monkeypatch.setenv("NTFY_URL", "https://ntfy.example")
    monkeypatch.setenv("NTFY_TOPIC", "narad")
    karma: list[tuple[str, str]] = []
    stub = SimpleNamespace(
        log_karma=lambda *args, **kwargs: karma.append((profile_context.current_profile_id(), args[3]))
    )
    with patch.dict(sys.modules, {"karma_log": stub}):
        yield karma


class _Response:
    status = 200

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _deliver_and_capture(**kwargs) -> tuple[dict, list[str]]:
    urls: list[str] = []

    def fake_urlopen(request, timeout=0):
        urls.append(request.full_url)
        return _Response()

    with patch.object(vahana.urllib.request, "urlopen", side_effect=fake_urlopen):
        result = vahana.deliver(kind="reminder", title="Medication", body="Take it", **kwargs)
        vahana.drain_pushes()  # pushes are sent off-thread
    return result, urls


def test_ntfy_push_uses_a_private_topic_per_profile(isolated_family) -> None:
    _, alice_urls = _deliver_and_capture(user_id="alice")
    _, again_urls = _deliver_and_capture(user_id="alice")
    result, bob_urls = _deliver_and_capture(user_id="bob")

    assert result["pushed"] is True
    alice_topic = alice_urls[0].rsplit("/", 1)[1]
    bob_topic = bob_urls[0].rsplit("/", 1)[1]
    assert again_urls == alice_urls
    assert alice_topic != bob_topic
    assert alice_topic.startswith("narad-alice-") and bob_topic.startswith("narad-bob-")
    assert len(alice_topic.rsplit("-", 1)[1]) >= 20
    assert "https://ntfy.example/narad" not in alice_urls + bob_urls
    stored = json.loads((profile_context.PROFILES_DIR / "alice" / "ntfy.json").read_text(encoding="utf-8"))
    assert stored["topic"] == alice_topic

    inbox = vahana.load_inbox("bob")
    assert inbox[0]["profile_id"] == "bob"
    assert vahana.load_inbox("alice")[0]["profile_id"] == "alice"
    assert [profile for profile, _ in isolated_family] == ["alice", "alice", "bob"]


def test_ntfy_never_falls_back_to_the_shared_topic(isolated_family) -> None:
    result, urls = _deliver_and_capture(user_id="Not A Profile!")
    assert urls == []
    assert result["pushed"] is False
    assert vahana.load_inbox("Not A Profile!")[0]["profile_id"] is None

    (profile_context.PROFILES_DIR / "carol").mkdir(parents=True)
    (profile_context.PROFILES_DIR / "carol" / "ntfy.json").write_text('{"topic": "narad"}', encoding="utf-8")
    result, urls = _deliver_and_capture(user_id="carol")
    assert urls == []
    assert result["pushed"] is False


def _add_reminder(profile_id: str, med_name: str, schedule: str) -> None:
    from health_skill import set_medication_reminder

    with profile_context.profile_scope(profile_id):
        set_medication_reminder(med_name, "1 tablet", schedule)


def test_medication_reminders_fire_for_every_profile_to_that_profile() -> None:
    family_profiles.create_profile("Alice", "2468")
    _add_reminder("default", "Metformin", "8am")
    _add_reminder("alice", "Thyroxine", "7am")
    delivered: list[dict] = []
    state: dict = {}
    now = datetime(2026, 9, 23, 9, 0)

    with patch.object(vahana, "deliver", side_effect=lambda **kw: delivered.append(kw) or {"status": "ok"}):
        assert kala_scheduler._fire_due_reminders(now, state) == 2
        assert kala_scheduler._fire_due_reminders(now, state) == 0

    by_profile = {item["user_id"]: item for item in delivered}
    assert set(by_profile) == {"default", "alice"}
    assert by_profile["default"]["title"] == "Medication: Metformin"
    assert by_profile["alice"]["title"] == "Medication: Thyroxine"
    assert by_profile["alice"]["data"]["profile_id"] == "alice"
    # Both reminders have id 1 in their own databases; de-duplication is per profile.
    assert state["delivered"]["2026-09-23"] == ["med:alice:1:07:00", "med:default:1:08:00"]


def test_owner_reminders_already_sent_before_upgrade_do_not_repeat() -> None:
    _add_reminder("default", "Metformin", "8am")
    state = {"delivered": {"2026-09-23": ["med:1:08:00"]}}
    with patch.object(vahana, "deliver") as deliver:
        assert kala_scheduler._fire_due_reminders(datetime(2026, 9, 23, 9, 0), state) == 0
    deliver.assert_not_called()


def test_scheduler_does_not_bootstrap_the_profile_registry() -> None:
    assert kala_scheduler._family_profile_ids() == ["default"]
    assert not family_profiles.FAMILY_PROFILES_PATH.exists()
