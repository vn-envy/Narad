"""Test-wide safety net for Anumati approvals.

Every test gets its own approval store and records notifications instead of
calling Vahana, so a test run on the host Mac never writes proposals into real
profiles or pushes an approval request to a family phone. Tests that need the
store or the notifications request the ``anumati_home`` fixture by name.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import anumati


@pytest.fixture(autouse=True)
def anumati_home(tmp_path, monkeypatch):
    root = tmp_path / "anumati"
    notifications: list[dict] = []

    def _deliver(**kwargs):
        notifications.append(kwargs)
        return {"status": "ok", "event_id": f"test-{len(notifications)}", "pushed": False}

    monkeypatch.setattr(anumati, "_db_path", lambda profile_id: root / profile_id / "anumati.db")
    monkeypatch.setattr(anumati, "_vahana_deliver", _deliver)
    return SimpleNamespace(root=root, notifications=notifications)
