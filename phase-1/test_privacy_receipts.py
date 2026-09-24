"""Privacy receipts: every egress row written during a chat turn carries its turn id,
work after the turn does not, and a turn's receipt holds counts, never values."""
from __future__ import annotations

import asyncio
import contextvars
import json
import sys
import threading
from pathlib import Path

import pytest

_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "narad_paths.py").exists())
sys.path[:0] = [str(_ROOT)]
import narad_paths  # noqa: E402, F401
import privacy_gateway as gw  # noqa: E402
import profile_context  # noqa: E402
import smriti_indexer  # noqa: E402

TURN = "c0ffee0123456789"
FAMILY = {term: ("PERSON", "Asha Sharma") for term in ("asha sharma", "asha", "sharma")}
# Values that must never appear in a receipt or the ledger.
VALUES = ("Asha", "Sharma", "98765", "43210", "asha@example.com", "clinic")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("NARAD_PROVIDER_TIERS", raising=False)
    monkeypatch.setenv("NARAD_PII_DETECTOR", "rules")
    monkeypatch.setattr(profile_context, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(gw, "_family_terms", lambda: dict(FAMILY))
    monkeypatch.setattr(gw, "_stores", {})
    gw._terms_cache.update(key=None, pattern=None, labels={}, checked=0.0)


def _ledger(tmp_path: Path, profile: str = "default") -> list[dict]:
    path = tmp_path / "profiles" / profile / "privacy" / "egress.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _in_turn(func, *args, **kwargs):
    """Run *func* the way /chat runs a turn: in a copied context carrying its id."""
    context = contextvars.copy_context()
    context.run(gw.set_turn_id, TURN)
    return context.run(func, *args, **kwargs)


# ── Stamping ──────────────────────────────────────────────────────────────────

def test_rows_in_the_turn_are_stamped_and_work_outside_it_is_not(tmp_path: Path) -> None:
    async def turn() -> None:
        gw.record_egress(model="anthropic/claude-sonnet-5", source="agent", tier=gw.TRUSTED)
        await asyncio.to_thread(  # sync tools and embeddings run on worker threads
            gw.record_egress, model="gemini/text-embedding-004", source="memory", tier=gw.TRUSTED
        )
        await asyncio.create_task(asyncio.to_thread(  # an avatar's own task inherits the turn
            gw.record_egress, model="deepseek/deepseek-chat", source="agent", tier=gw.REDACT
        ))
        # A learner scheduled the way avatar_agents schedules Tapas and Sankalpa.
        done = asyncio.get_running_loop().create_future()

        async def learner() -> None:
            await asyncio.to_thread(gw.record_egress, model="deepseek/deepseek-reasoner",
                                    source="tapas", tier=gw.REDACT)
            done.set_result(None)

        asyncio.get_running_loop().call_soon(
            lambda: asyncio.ensure_future(learner()), context=gw.context_outside_turn()
        )
        await done
        # A plain thread starts with an empty context.
        thread = threading.Thread(target=gw.record_egress, kwargs={
            "model": "openai/text-embedding-3-small", "source": "memory", "tier": gw.TRUSTED,
        })
        thread.start()
        thread.join()
        assert gw.current_turn_id() == TURN  # background work never cleared the turn's own id

    _in_turn(asyncio.run, turn())

    rows = _ledger(tmp_path)
    assert [(row["source"], row.get("turn_id")) for row in rows] == [
        ("agent", TURN), ("memory", TURN), ("agent", TURN), ("tapas", None), ("memory", None),
    ]
    assert gw.current_turn_id() is None  # nothing leaked into the caller's context


def test_index_refresh_scheduled_in_a_turn_is_not_stamped_and_stays_in_the_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def embed_episodes(user_id: str) -> None:
        gw.guard_texts("gemini/text-embedding-004", ["an episode"], source="memory")

    monkeypatch.setattr(smriti_indexer, "ensure_user_episode_index", embed_episodes)
    monkeypatch.setattr(smriti_indexer, "ensure_wiki_fts", lambda *_args: None)

    def schedule() -> None:
        with profile_context.profile_scope("asha"):
            gw.record_egress(model="anthropic/claude-sonnet-5", source="agent", tier=gw.TRUSTED)
            assert smriti_indexer.schedule_index_refresh("asha")

    _in_turn(schedule)
    assert smriti_indexer.wait_for_index_refresh("asha", timeout_s=5)

    rows = _ledger(tmp_path, "asha")
    assert [(row["source"], row.get("turn_id")) for row in rows] == [("agent", TURN), ("memory", None)]
    assert _ledger(tmp_path, "default") == []  # not filed under the owner


# ── Search and web tools ──────────────────────────────────────────────────────

def test_tool_egress_counts_placeholders_and_never_stores_the_words(tmp_path: Path) -> None:
    _in_turn(gw.record_tool_egress, "web_search", {"query": "<PERSON_1> clinic timings <PHONE_2>"})
    _in_turn(gw.record_tool_egress, "browse_url", {"url": "https://example.com/clinic"})
    gw.record_tool_egress("extract_document", {"path": "/tmp/report.pdf"})  # stays on the Mac

    rows = _ledger(tmp_path)
    assert [(r["provider"], r["tier"], r["source"], r["entities"]) for r in rows] == [
        ("exa", gw.WEB, "search", {"PERSON": 1, "PHONE": 1}),
        ("website", gw.WEB, "web", {}),
    ]
    assert all(row["turn_id"] == TURN for row in rows)
    text = (tmp_path / "profiles" / "default" / "privacy" / "egress.jsonl").read_text()
    assert "clinic" not in text and "example.com" not in text


def test_outbound_tools_keep_placeholders_in_their_arguments() -> None:
    assert {"enrich_web_research", "firecrawl_extract", "web_search", "browse_url"} <= gw.OUTBOUND_TOOLS


# ── Receipts ──────────────────────────────────────────────────────────────────

def test_receipt_summarises_this_turn_as_counts_only(tmp_path: Path) -> None:
    def turn() -> None:
        # The brain is a redact-tier provider: names and numbers are swapped.
        gw.guard_texts("deepseek/deepseek-chat",
                       ["Book Asha Sharma at the clinic, call 98765 43210 or asha@example.com"],
                       source="agent")
        gw.guard_texts("deepseek/deepseek-chat", ["Asha asked again"], source="agent")
        gw.record_egress(model="gemini/text-embedding-004", source="memory", tier=gw.TRUSTED, chars=40)
        gw.record_tool_egress("web_search", {"query": "<PERSON_1> clinic"})
        gw.record_egress(model="xai/grok-4.6", source="agent", tier=gw.BLOCKED, blocked="policy")
        gw.record_egress(model="ollama/gemma", source="agent", tier=gw.LOCAL)

    _in_turn(turn)
    gw.record_egress(model="anthropic/claude-sonnet-5", source="tapas", tier=gw.TRUSTED)  # not this turn

    receipt = gw.privacy_receipt(TURN)
    assert receipt == {
        "v": 1,
        "turn_id": TURN,
        "stayed_local": False,
        "destinations": [
            {"provider": "deepseek", "tier": "redact", "sources": ["answer"], "calls": 2,
             "replaced": {"PERSON": 2, "PHONE": 1, "EMAIL": 1}},
            {"provider": "gemini", "tier": "trusted", "sources": ["memory"], "calls": 1, "replaced": {}},
            {"provider": "exa", "tier": "web", "sources": ["search"], "calls": 1, "replaced": {"PERSON": 1}},
        ],
        "refused": {"policy": 1},
    }
    serialized = json.dumps(receipt)
    for value in VALUES:
        assert value not in serialized
    assert "<PERSON" not in serialized


def test_a_turn_with_nothing_sent_stayed_local(tmp_path: Path) -> None:
    _in_turn(gw.record_egress, model="ollama/gemma", source="agent", tier=gw.LOCAL)
    receipt = gw.privacy_receipt(TURN)
    assert receipt["stayed_local"] is True
    assert receipt["destinations"] == []
    assert gw.privacy_receipt("") == {**receipt, "turn_id": None}


def test_receipt_drops_anything_that_is_not_a_token(tmp_path: Path) -> None:
    path = tmp_path / "profiles" / "default" / "privacy" / "egress.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "ts": "2026-09-24T10:00:00+0530", "turn_id": TURN, "source": "agent", "tier": "redact",
        "provider": "a provider called Asha", "entities": {"Asha Sharma": 2}, "blocked": "",
    }) + "\n")
    receipt = gw.privacy_receipt(TURN)
    assert "Asha" not in json.dumps(receipt)
    assert receipt["destinations"][0]["provider"] == "unknown"
