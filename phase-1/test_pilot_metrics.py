"""Pilot metrics: count-only turn records, feedback, voice, consent, routes, and the /chat hook."""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import server
from fastapi.testclient import TestClient

import family_profiles
import onboarding
import pilot_metrics
import pilot_scorecard
import profile_context

_READINESS = {
    "model_ready": True,
    "research_ready": False,
    "connected_model_providers": [],
    "connected_search_providers": [],
    "connected_subscriptions": [],
    "local_model_ready": False,
}
# Text that must never reach a metrics file.
_SECRETS = ("SECRET-PROMPT", "SECRET-REPLY", "SECRET-TOOL-ARG", "SECRET-PREVIEW", "SECRET-DELTA")


def _event(event_type: str, /, **data) -> str:
    return json.dumps({"type": event_type, "data": data})


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Isolated(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.patches = [
            patch.object(profile_context, "PROFILES_DIR", self.root / "profiles"),
            patch.object(pilot_scorecard, "OPS_DIR", self.root / "ops"),
            patch.object(family_profiles, "FAMILY_PROFILES_PATH", self.root / "family_profiles.json"),
            patch.object(family_profiles, "PROFILE_SESSION_SECRET_PATH", self.root / "profile_secret"),
            patch.object(onboarding, "ONBOARDING_PATH", self.root / "onboarding.json"),
            patch.object(onboarding, "_connection_readiness", return_value=_READINESS),
        ]
        for active in self.patches:
            active.start()

    def tearDown(self) -> None:
        for active in reversed(self.patches):
            active.stop()
        self.tempdir.cleanup()

    def _metrics_text(self) -> str:
        return "\n".join(
            path.read_text(encoding="utf-8")
            for path in (self.root / "profiles").rglob("*")
            if path.is_file() and ("metrics" in path.parts or path.name == "consent.json")
        )


class TurnRecorderTests(_Isolated):
    def _recorder(self, clock: _Clock, **kwargs) -> pilot_metrics.TurnRecorder:
        return pilot_metrics.TurnRecorder(
            profile_id="asha", session_id="0f6a2c1e-7a55-4b9e-9d0c-1b2c3d4e5f60",
            clock=clock, wall=lambda: 1_790_000_000.0, **kwargs,
        )

    def test_answered_turn_records_counts_latencies_and_no_text(self) -> None:
        clock = _Clock()
        recorder = self._recorder(clock, workflow_run_id="wf-123", attachments=2, images=1)
        steps = [
            (0.4, _event("context_budget", predicted_input_tokens=900)),
            (0.1, _event("avatar_start", avatar="Matsya", task="SECRET-PROMPT")),
            (0.2, _event("step_event", avatar="Matsya", kind="tool_call", tool="_web_search",
                         preview="SECRET-TOOL-ARG")),
            (0.3, _event("step_event", avatar="Matsya", kind="tool_result", tool="_web_search",
                         preview="{'status': 'ok', 'summary': 'SECRET-PREVIEW'}")),
            (0.1, _event("step_event", avatar="Krishna", kind="tool_call", tool="_send_email", preview="x")),
            (0.1, _event("step_event", avatar="Krishna", kind="tool_result", tool="_send_email",
                         preview="{'status': 'preview', 'message': 'DRY RUN SECRET-PREVIEW'}")),
            (0.1, _event("step_event", avatar="Matsya", kind="tool_result", tool="_browser_fill",
                         preview="{'status': 'preview', 'summary': 'filled'}")),
            (0.1, _event("step_event", avatar="Matsya", kind="tool_result", tool="_computer_use",
                         preview="{'status': 'confirmation_required', 'summary': 'x'}")),
            (0.1, _event("avatar_start", avatar="Krishna", task="SECRET-PROMPT")),
            (0.5, _event("text_delta", delta="SECRET-DELTA ")),
            (0.2, _event("narad_synthesis", text="SECRET-REPLY")),
            (0.1, _event("andon", reason="EMPTY_RESULT")),
            (0.1, _event("workflow_updated", workflow_run_id="wf-123", workflow_id="travel",
                         run={"current_stage_id": "compare", "status": "active", "summary": "SECRET-REPLY"})),
            (0.2, _event("done", session_id="s")),
        ]
        outputs = []
        for delay, payload in steps:
            clock.now += delay
            outputs.append(recorder.observe(payload))
        self.assertEqual(outputs[:-1], [payload for _, payload in steps[:-1]])  # passed through untouched
        done = json.loads(outputs[-1])
        self.assertEqual(done["data"]["turn_id"], recorder.turn_id)

        record = recorder.finish()
        self.assertIsNone(recorder.finish())  # written once
        self.assertEqual(record["outcome"], "answered")
        self.assertEqual(record["latency_ms"], {"first_event": 400, "first_text": 2000, "done": 2600})
        self.assertEqual(record["avatars"], ["matsya", "krishna"])
        self.assertEqual(record["avatar_calls"], 2)
        self.assertEqual(record["tool_calls"], 2)
        self.assertEqual(record["tools"], {"web_search": 1, "send_email": 1})
        self.assertEqual(record["approvals"], {"requested": 3, "needed": 1, "unclassified": 1})
        self.assertEqual(record["andon_alerts"], 1)
        self.assertEqual(record["workflow"], {"run_id": "wf-123", "workflow_id": "travel",
                                              "stage": "compare", "status": "active"})
        self.assertEqual(record["inputs"], {"attachments": 2, "images": 1})
        # Streamed, then sent whole: the longer of the two, never their sum.
        self.assertEqual(record["reply_chars"], max(len("SECRET-DELTA "), len("SECRET-REPLY")))
        stored = self._metrics_text()
        self.assertIn(recorder.turn_id, stored)
        for secret in _SECRETS:
            self.assertNotIn(secret, stored)

    def test_text_reset_drops_routing_chatter_from_the_reply_length(self) -> None:
        recorder = self._recorder(_Clock())
        recorder.observe(_event("text_delta", delta="Let me ask Matsya."))
        recorder.observe(_event("text_reset"))
        recorder.observe(_event("text_delta", delta="Answer."))
        self.assertEqual(recorder.reply_chars, len("Answer."))
        recorder.observe(_event("narad_synthesis", text="Answer. With more."))
        self.assertEqual(recorder.reply_chars, len("Answer. With more."))

    def test_outcomes_for_errors_empty_answers_and_stops(self) -> None:
        crashed = self._recorder(_Clock())
        crashed.observe(_event("error", message="SECRET-REPLY"))
        self.assertEqual(crashed.finish()["outcome"], "error")

        empty = self._recorder(_Clock())
        empty.observe(_event("done", session_id="s"))
        record = empty.finish()
        self.assertEqual((record["outcome"], record["reason"]), ("error", "empty"))

        stopped = self._recorder(_Clock())
        stopped.observe(_event("narad_synthesis", text="partial"))
        self.assertEqual(stopped.finish(cancelled=True)["outcome"], "stopped")

        unfinished = self._recorder(_Clock())
        unfinished.observe(_event("narad_synthesis", text="partial"))
        self.assertEqual(unfinished.finish()["reason"], "no_done")

    def test_record_allowlist_drops_free_text(self) -> None:
        cleaned = pilot_metrics._clean({"ok": "answered", "bad": "a sentence with spaces", "n": 3,
                                        "nested": ["fine_id", "two words"], "two words": 1})
        self.assertEqual(cleaned["ok"], "answered")
        self.assertEqual(cleaned["bad"], "invalid")
        self.assertEqual(cleaned["nested"], ["fine_id", "invalid"])
        self.assertIn("invalid", cleaned)

    def test_turn_egress_counts_only_this_turns_window_and_sources(self) -> None:
        ledger = self.root / "profiles" / "asha" / "privacy" / "egress.jsonl"
        ledger.parent.mkdir(parents=True)
        start = time.time() - 30

        def row(offset: float, **fields) -> str:
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(start + offset))
            return json.dumps({"ts": stamp, "source": "agent", "tier": "trusted", "blocked": "", **fields})

        ledger.write_text("\n".join([
            row(-120),                                   # before the turn
            row(2), row(3, tier="redact"), row(4, source="memory"),
            row(5, blocked="policy", tier="blocked"),
            row(6, source="tapas"),                      # background learner, not the turn
            row(400),                                    # after the turn
        ]) + "\n")
        counts = pilot_metrics.turn_egress("asha", start, start + 10)
        self.assertEqual(counts, {"trusted": 2, "redact": 1, "web": 0, "blocked": 1, "cloud_llm_calls": 2})

    def test_turn_egress_matches_the_exact_turn_id_and_falls_back_for_old_rows(self) -> None:
        ledger = self.root / "profiles" / "asha" / "privacy" / "egress.jsonl"
        ledger.parent.mkdir(parents=True)
        start = time.time() - 30

        def row(offset: float, **fields) -> str:
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(start + offset))
            return json.dumps({"ts": stamp, "source": "agent", "tier": "trusted", "blocked": "", **fields})

        ledger.write_text("\n".join([
            row(1, turn_id="aaaa1111"),                               # this turn
            row(2, turn_id="aaaa1111", source="guru_grader", tier="redact"),  # any source of this turn
            row(3, turn_id="aaaa1111", source="search", tier="web"),
            row(3, turn_id="bbbb2222"),                               # a turn running alongside
            row(4, turn_id="bbbb2222", tier="redact"),
            row(5),                                                   # an old unstamped row: window fallback
            row(6, source="tapas"),                                   # unstamped learner: excluded
            row(60, turn_id="aaaa1111", source="memory"),             # stamped, after the window: still this turn's
        ]) + "\n")
        counts = pilot_metrics.turn_egress("asha", start, start + 10, turn_id="aaaa1111")
        self.assertEqual(counts, {"trusted": 3, "redact": 1, "web": 1, "blocked": 0, "cloud_llm_calls": 2})
        recorder = pilot_metrics.TurnRecorder(profile_id="asha", session_id="s", turn_id="aaaa1111")
        self.assertEqual(recorder.turn_id, "aaaa1111")

    def test_turn_queue_observes_every_put_and_writes_when_the_task_ends(self) -> None:
        async def scenario() -> list[str]:
            queue = pilot_metrics.start_turn(profile_id="asha", session_id="sess-1")

            async def run() -> None:
                await queue.put(_event("avatar_start", avatar="Rama", task="SECRET-PROMPT"))
                queue.put_nowait(_event("narad_synthesis", text="SECRET-REPLY"))
                await queue.put(_event("done", session_id="sess-1"))
                await queue.put(None)

            task = asyncio.create_task(run())
            queue.watch(task)
            await task
            await asyncio.sleep(0)  # let the done callback run
            items = []
            while not queue.empty():
                items.append(queue.get_nowait())
            return items

        items = asyncio.run(scenario())
        self.assertIsNone(items[-1])
        self.assertIn("turn_id", json.loads(items[-2])["data"])
        rows = pilot_metrics.read_jsonl(pilot_metrics.metrics_dir("asha") / "turns.jsonl")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["outcome"], "answered")
        self.assertEqual(rows[0]["avatars"], ["rama"])

    def test_cancelled_task_is_recorded_as_stopped(self) -> None:
        async def scenario() -> None:
            queue = pilot_metrics.start_turn(profile_id="asha", session_id="sess-2")

            async def run() -> None:
                await queue.put(_event("avatar_start", avatar="Krishna"))
                await asyncio.sleep(10)

            task = asyncio.create_task(run())
            queue.watch(task)
            await asyncio.sleep(0.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0)

        asyncio.run(scenario())
        rows = pilot_metrics.read_jsonl(pilot_metrics.metrics_dir("asha") / "turns.jsonl")
        self.assertEqual(rows[0]["outcome"], "stopped")


class FeedbackConsentSummaryTests(_Isolated):
    def test_feedback_validation_and_storage(self) -> None:
        record = pilot_metrics.record_feedback("asha", session_id="s-1", turn_id="a1b2c3d4e5f60718",
                                               rating="down", reason="too_slow")
        self.assertEqual(record["rating"], "down")
        bad = [
            {"session_id": "s-1", "turn_id": "a1b2c3d4", "rating": "meh"},
            {"session_id": "s-1", "turn_id": "a1b2c3d4", "rating": "up", "reason": "it said my name wrong"},
            {"session_id": "has spaces", "turn_id": "a1b2c3d4", "rating": "up"},
            {"session_id": "s-1", "rating": "up"},
            {"session_id": "s-1", "turn_id": "not hex!", "rating": "up"},
            {"session_id": "s-1", "message_index": -1, "rating": "up"},
        ]
        for kwargs in bad:
            with self.assertRaises(ValueError, msg=kwargs):
                pilot_metrics.record_feedback("asha", **kwargs)
        self.assertEqual(len(pilot_metrics.read_jsonl(pilot_metrics.metrics_dir("asha") / "feedback.jsonl")), 1)

    def test_voice_records_counts_only(self) -> None:
        pilot_metrics.record_voice("stt", engine="sarvam", ok=True, profile_id="asha", audio_bytes=48_000)
        pilot_metrics.record_voice("tts", engine=None, ok=False, profile_id="asha", chars=120)
        rows = pilot_metrics.read_jsonl(pilot_metrics.metrics_dir("asha") / "voice.jsonl")
        self.assertEqual([(r["kind"], r["engine"], r["ok"]) for r in rows],
                         [("stt", "sarvam", True), ("tts", None, False)])

    def test_consent_accept_withdraw_and_version(self) -> None:
        self.assertTrue(pilot_metrics.consent_status("asha")["needs_consent"])
        with self.assertRaises(ValueError):
            pilot_metrics.record_consent("asha", version="2020-01-01", accepted=True)
        accepted = pilot_metrics.record_consent("asha", version=pilot_metrics.CONSENT_VERSION, accepted=True)
        self.assertFalse(accepted["needs_consent"])
        withdrawn = pilot_metrics.record_consent("asha", version=pilot_metrics.CONSENT_VERSION, accepted=False)
        self.assertTrue(withdrawn["needs_consent"])
        stored = json.loads((self.root / "profiles" / "asha" / "consent.json").read_text())
        self.assertEqual(len(stored["history"]), 1)

    def test_the_apps_reason_chips_are_the_reasons_feedback_accepts(self) -> None:
        trust = Path(__file__).resolve().parents[1] / "phase-4" / "frontend" / "src" / "lib" / "trust.ts"
        block = trust.read_text(encoding="utf-8").split("export const FEEDBACK_REASONS", 1)[1].split("]\n", 1)[0]
        self.assertEqual(tuple(re.findall(r"id: '([a-z_]+)'", block)), pilot_metrics.FEEDBACK_REASONS)

    def test_consent_document_carries_the_current_version(self) -> None:
        doc = Path(__file__).resolve().parents[1] / "docs" / "PILOT_CONSENT_AND_METRICS.md"
        self.assertIn(f"Consent version: {pilot_metrics.CONSENT_VERSION}", doc.read_text(encoding="utf-8"))

    def test_profile_summary_math(self) -> None:
        now = time.time()
        folder = pilot_metrics.metrics_dir("asha")
        folder.mkdir(parents=True)

        def turn(turn_id: str, session: str, age_s: float, outcome: str, done_ms: int, **extra) -> dict:
            return {"t": now - age_s, "turn_id": turn_id, "session_id": session, "outcome": outcome,
                    "latency_ms": {"first_event": 50, "first_text": done_ms // 2, "done": done_ms},
                    "approvals": extra.get("approvals", {"requested": 0, "needed": 0, "unclassified": 0}),
                    "egress": extra.get("egress", {})}

        turns = [
            turn("a1", "s1", 5000, "answered", 1000),
            turn("a2", "s1", 4000, "answered", 3000, approvals={"requested": 2, "needed": 1, "unclassified": 0},
                 egress={"trusted": 2}),
            turn("a3", "s2", 3700, "error", 9000),          # abandoned: nothing after it for an hour
            turn("a4", "s3", 100, "stopped", 500),           # too recent to call abandoned
            turn("a5", "s4", 3000, "answered", 2000),
            turn("old", "s0", 9 * 86400, "answered", 1),     # outside the 7-day window
        ]
        (folder / "turns.jsonl").write_text("\n".join(json.dumps(t) for t in turns) + "\n")
        feedback = [
            {"t": now - 10, "session_id": "s1", "turn_id": "a2", "rating": "up"},
            {"t": now - 5, "session_id": "s1", "turn_id": "a2", "rating": "down", "reason": "wrong"},  # latest wins
            {"t": now - 5, "session_id": "s4", "message_index": 0, "rating": "up"},
        ]
        (folder / "feedback.jsonl").write_text("\n".join(json.dumps(f) for f in feedback) + "\n")

        summary = pilot_metrics.profile_summary("asha", now=now)
        self.assertEqual(summary["turns"], 5)
        self.assertEqual(summary["outcomes"], {"answered": 3, "error": 1, "stopped": 1})
        self.assertEqual(summary["task_success"], {"successes": 2, "rate": 0.4})
        self.assertEqual(summary["time_to_done_ms"]["p50"], 2000)
        self.assertEqual(summary["abandonment"], {"sessions": 4, "abandoned": 1, "rate": 0.25})
        self.assertEqual(summary["approvals"], {"requested": 2, "needed": 1, "unclassified": 0, "unneeded": 1})
        self.assertEqual(summary["feedback"]["up"], 1)
        self.assertEqual(summary["feedback"]["down"], 1)
        self.assertEqual(summary["feedback"]["reasons"], {"wrong": 1})
        self.assertEqual(summary["turn_egress"]["turns_all_local"], 4)


class ScorecardTests(_Isolated):
    def test_weekly_scorecard_gates_and_owner_view(self) -> None:
        now = time.time()
        ops = self.root / "ops"
        ops.mkdir()
        (ops / "uptime.jsonl").write_text("\n".join(
            json.dumps({"t": now - i * 300, "status": "up"}) for i in range(8 * 288)
        ) + "\n")
        (ops / "backup.jsonl").write_text(json.dumps({"t": now - 3600, "ts": "x", "ok": True}) + "\n")
        (ops / "backup_drill.jsonl").write_text(json.dumps({"t": now - 5, "ts": "x", "passed": True}) + "\n")
        family_profiles.create_profile("Asha", "2468")
        pilot_metrics.record_consent("asha", version=pilot_metrics.CONSENT_VERSION, accepted=True)
        folder = pilot_metrics.metrics_dir("asha")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "turns.jsonl").write_text(json.dumps({
            "t": now - 60, "turn_id": "t1", "session_id": "s1", "outcome": "answered", "tool_calls": 0,
            "latency_ms": {"first_event": 10, "first_text": 900, "done": 4000},
            "approvals": {"requested": 0, "needed": 0, "unclassified": 0}, "egress": {"trusted": 1},
        }) + "\n")
        ledger = self.root / "profiles" / "asha" / "privacy" / "egress.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(json.dumps({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now - 60)), "source": "kala_scheduler.medication",
            "tier": "trusted", "blocked": "",
        }) + "\n")

        card = pilot_scorecard.weekly_scorecard(now=now)
        self.assertTrue(all(card["gates"].values()), card["gates"])  # a drill run seconds ago counts
        self.assertEqual(card["totals"]["first_text_p50_no_tools_ms"], 900)
        self.assertNotIn("by_source", card["profiles"]["asha"]["egress"])  # private to the person
        self.assertIn("by_source", pilot_metrics.profile_summary("asha", now=now)["egress"])
        markdown = pilot_scorecard.scorecard_markdown(card)
        self.assertIn("| asha | current | 1 |", markdown)
        self.assertNotIn("FAIL", markdown)
        self.assertNotIn("medication", markdown)


class PilotRouteTests(_Isolated):
    def setUp(self) -> None:
        super().setUp()
        self.patches += [
            patch.object(server, "_AUTH_MODE", "strict"),
            patch.object(server, "_login_failures", {}),
        ]
        for active in self.patches[-2:]:
            active.start()
        self.client = TestClient(server.app)
        family_profiles.update_profile("default", pin="8642")
        family_profiles.create_profile("Alice", "2468")

    def _headers(self, user_id: str, pin: str) -> dict[str, str]:
        response = self.client.post("/profiles/login", json={"user_id": user_id, "pin": pin})
        self.assertEqual(response.status_code, 200, response.text)
        return {"Authorization": f"Bearer {response.json()['token']}"}

    def test_feedback_route_stores_in_the_callers_profile(self) -> None:
        self.assertEqual(self.client.post("/feedback", json={"session_id": "s", "turn_id": "abcdef12",
                                                             "rating": "up"}).status_code, 401)
        alice = self._headers("alice", "2468")
        created = self.client.post("/feedback", headers=alice, json={
            "session_id": "s-1", "turn_id": "abcdef1234567890", "rating": "down", "reason": "incomplete",
        })
        self.assertEqual(created.status_code, 201, created.text)
        self.assertTrue((self.root / "profiles" / "alice" / "metrics" / "feedback.jsonl").exists())
        self.assertFalse((self.root / "profiles" / "default" / "metrics" / "feedback.jsonl").exists())
        bad = self.client.post("/feedback", headers=alice, json={"session_id": "s-1", "turn_id": "abcdef12",
                                                                 "rating": "up", "reason": "free text here"})
        self.assertEqual(bad.status_code, 400)

    def test_members_see_their_own_metrics_and_the_owner_sees_everyone(self) -> None:
        alice = self._headers("alice", "2468")
        own = self.client.get("/pilot/metrics", headers=alice)
        self.assertEqual(own.status_code, 200, own.text)
        self.assertEqual(own.json()["scope"], "self")
        self.assertEqual(own.json()["summary"]["profile"], "alice")
        for scope in ("profiles", "all"):
            self.assertEqual(self.client.get(f"/pilot/metrics?scope={scope}", headers=alice).status_code, 403)

        owner = self._headers("default", "8642")
        household = self.client.get("/pilot/metrics", headers=owner).json()
        self.assertEqual(household["scope"], "profiles")
        self.assertEqual(set(household["profiles"]), {"default", "alice"})
        self.assertIn("consent", household["profiles"]["alice"])
        card = self.client.get("/pilot/metrics?scope=all", headers=owner).json()["scorecard"]
        self.assertIn("uptime_99_waking", card["gates"])
        markdown = self.client.get("/pilot/metrics?scope=all&format=markdown", headers=owner)
        self.assertEqual(markdown.status_code, 200)
        self.assertIn("# Narad pilot scorecard", markdown.text)
        self.assertEqual(self.client.get("/pilot/metrics?scope=bogus", headers=owner).status_code, 400)

    def test_consent_routes(self) -> None:
        alice = self._headers("alice", "2468")
        self.assertTrue(self.client.get("/consent", headers=alice).json()["needs_consent"])
        wrong = self.client.post("/consent", headers=alice, json={"version": "old"})
        self.assertEqual(wrong.status_code, 409)
        accepted = self.client.post("/consent", headers=alice, json={"version": pilot_metrics.CONSENT_VERSION})
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertFalse(accepted.json()["needs_consent"])
        with_doc = self.client.get("/consent?document=true", headers=alice).json()
        self.assertIn(pilot_metrics.CONSENT_VERSION, with_doc["document_markdown"])
        self.assertEqual(self.client.get("/consent?scope=all", headers=alice).status_code, 403)
        owner = self._headers("default", "8642")
        everyone = self.client.get("/consent?scope=all", headers=owner).json()
        self.assertFalse(everyone["profiles"]["alice"]["needs_consent"])
        # The owner is always considered consented.
        self.assertFalse(everyone["profiles"]["default"]["needs_consent"])
        self.assertTrue(everyone["profiles"]["default"]["owner"])

    def test_consent_screen_reads_part_a_from_the_document(self) -> None:
        alice = self._headers("alice", "2468")
        english = self.client.get("/consent?part=a", headers=alice).json()
        self.assertTrue(english["enforced"])
        sheet = english["sheet"]
        self.assertEqual((sheet["lang"], sheet["languages"]), ("en", ["en", "hi"]))
        self.assertIn("### What Narad is", sheet["markdown"])
        self.assertIn("What left my Mac", sheet["markdown"])
        # Printed-copy lines and the owner's checklist stay off the screen.
        self.assertNotIn("______", sheet["markdown"])
        self.assertNotIn("owner checklist", sheet["markdown"])
        self.assertNotIn("<!--", sheet["markdown"])
        hindi = self.client.get("/consent?part=a&lang=hi", headers=alice).json()["sheet"]
        self.assertEqual(hindi["lang"], "hi")
        self.assertIn("नारद", hindi["markdown"])
        self.assertIn("अनुवाद", hindi["markdown"])  # it says it is a translation
        self.assertNotIn("______", hindi["markdown"])
        fallback = self.client.get("/consent?part=a&lang=xx", headers=alice).json()["sheet"]
        self.assertEqual(fallback["lang"], "en")

    def _chat(self, headers: dict[str, str], query: str, session_id: str, fake_run=None) -> tuple[int, str]:
        async def default_run(req, session_id, queue):
            try:
                await queue.put(_event("narad_synthesis", text="an answer"))
                await queue.put(_event("done", session_id=session_id))
            finally:
                await queue.put(None)

        with patch.object(server, "_run_agent_task", fake_run or default_run), \
                patch.object(server, "_agent_runtime_unavailable_reason", return_value=None), \
                patch("model_registry.provider_available_for_model", return_value=True), \
                patch.object(server, "_check_rate_limit", return_value=True):
            with self.client.stream("POST", "/chat", headers=headers,
                                    json={"query": query, "session_id": session_id}) as response:
                body = "".join(chunk for chunk in response.iter_text())
                status = response.status_code
        for key in [key for key in server._active_tasks if key[1] == session_id]:
            server._active_tasks.pop(key, None)
        return status, body

    def test_members_without_consent_get_403_on_chat_uploads_and_voice(self) -> None:
        alice = self._headers("alice", "2468")
        status, body = self._chat(alice, "plan my week", "chat-c1")
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["code"], "consent_required")
        self.assertEqual(json.loads(body)["current_version"], pilot_metrics.CONSENT_VERSION)
        upload = self.client.post("/chat/attachments", headers=alice, data={"relative_paths": "[]"},
                                  files=[("files", ("notes.txt", b"hello", "text/plain"))])
        self.assertEqual((upload.status_code, upload.json()["code"]), (403, "consent_required"))
        for method, path, kwargs in (
            ("post", "/voice/tts", {"json": {"text": "hello"}}),
            ("post", "/voice/stt", {"files": [("audio", ("a.webm", b"xx", "audio/webm"))]}),
            ("get", "/voice/status", {}),
        ):
            response = getattr(self.client, method)(path, headers=alice, **kwargs)
            self.assertEqual(response.status_code, 403, path)
            self.assertEqual(response.json()["code"], "consent_required", path)
        self.assertFalse((self.root / "profiles" / "alice" / "metrics" / "turns.jsonl").exists())

        # After accepting, the same requests go through.
        self.client.post("/consent", headers=alice, json={"version": pilot_metrics.CONSENT_VERSION})
        status, body = self._chat(alice, "plan my week", "chat-c2")
        self.assertEqual(status, 200)
        self.assertIn("an answer", body)
        upload = self.client.post("/chat/attachments", headers=alice, data={"relative_paths": "[]"},
                                  files=[("files", ("notes.txt", b"hello", "text/plain"))])
        self.assertNotEqual(upload.status_code, 403)
        self.assertNotEqual(self.client.get("/voice/status", headers=alice).status_code, 403)

        # Withdrawing stops processing again.
        self.client.post("/consent", headers=alice, json={"version": pilot_metrics.CONSENT_VERSION,
                                                          "accepted": False})
        self.assertEqual(self._chat(alice, "plan my week", "chat-c3")[0], 403)

    def test_the_owner_is_exempt_and_the_setting_can_turn_the_check_off(self) -> None:
        owner = self._headers("default", "8642")
        status, _ = self._chat(owner, "plan my week", "chat-o1")
        self.assertEqual(status, 200)
        self.assertNotEqual(self.client.get("/voice/status", headers=owner).status_code, 403)

        alice = self._headers("alice", "2468")
        with patch.dict("os.environ", {"NARAD_REQUIRE_CONSENT": "off"}):
            status, _ = self._chat(alice, "plan my week", "chat-o2")
            self.assertEqual(status, 200)
            self.assertFalse(self.client.get("/consent", headers=alice).json()["enforced"])
        self.assertEqual(self._chat(alice, "plan my week", "chat-o3")[0], 403)

    def test_someone_in_crisis_is_answered_even_before_consent(self) -> None:
        alice = self._headers("alice", "2468")

        async def must_not_run(req, session_id, queue):  # no model, no agent task
            raise AssertionError("a crisis message reached the agent")

        status, body = self._chat(alice, "मैं मरना चाहता हूँ", "chat-x1", fake_run=must_not_run)
        self.assertEqual(status, 200)
        events = [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]
        kinds = [event["type"] for event in events]
        self.assertEqual(kinds, ["crisis_support", "narad_synthesis", "privacy_receipt", "done"])
        reply = events[1]["data"]["text"]
        self.assertIn("14416", reply)
        self.assertIn("शुक्रिया", reply)
        self.assertTrue(events[2]["data"]["stayed_local"])
        self.assertEqual(events[3]["data"]["session_id"], "chat-x1")  # the thread carries on
        karma = (self.root / "profiles" / "alice" / "karma_mutations.jsonl").read_text(encoding="utf-8")
        row = json.loads(karma.splitlines()[-1])
        self.assertEqual((row["action"], row["metadata"]), (
            "input_gate", {"verdict": "care", "kind": "suicide", "language": "hi"},
        ))
        self.assertNotIn("मरना", karma)
        self.assertFalse((self.root / "profiles" / "alice" / "metrics" / "turns.jsonl").exists())

    def test_chat_turn_writes_one_count_only_record(self) -> None:
        import privacy_gateway

        seen: dict[str, str | None] = {}

        async def fake_run(req, session_id, queue):
            try:
                # Runs as the real task does, inside the turn: its egress rows are stamped.
                seen["turn_id"] = privacy_gateway.current_turn_id()
                privacy_gateway.record_egress(model="anthropic/claude-sonnet-5", source="agent", tier="trusted")
                await queue.put(_event("avatar_start", avatar="Krishna", task="SECRET-PROMPT"))
                await queue.put(_event("step_event", avatar="Krishna", kind="tool_call",
                                       tool="compose_email", preview="SECRET-TOOL-ARG"))
                await queue.put(_event("narad_synthesis", text="SECRET-REPLY"))
                await queue.put(_event("done", session_id=session_id))
            finally:
                await queue.put(None)

        alice = self._headers("alice", "2468")
        self.client.post("/consent", headers=alice, json={"version": pilot_metrics.CONSENT_VERSION})
        status, body = self._chat(alice, "SECRET-PROMPT please", "chat-1", fake_run=fake_run)
        self.assertEqual(status, 200)
        self.assertIn("SECRET-REPLY", body)  # the stream itself is untouched
        done = next(json.loads(line[5:]) for line in body.splitlines() if '"done"' in line)
        turn_id = done["data"]["turn_id"]
        self.assertEqual(seen["turn_id"], turn_id)  # one id: the done event and the egress rows
        self.assertIsNone(privacy_gateway.current_turn_id())  # never leaks outside the turn

        turns = pilot_metrics.metrics_dir("alice") / "turns.jsonl"
        for _ in range(100):
            if turns.exists():
                break
            time.sleep(0.02)
        rows = pilot_metrics.read_jsonl(turns)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["turn_id"], turn_id)
        self.assertEqual(rows[0]["outcome"], "answered")
        self.assertEqual(rows[0]["session_id"], "chat-1")
        self.assertEqual(rows[0]["tools"], {"compose_email": 1})
        self.assertEqual(rows[0]["egress"]["trusted"], 1)  # matched by the exact turn id
        egress = (self.root / "profiles" / "alice" / "privacy" / "egress.jsonl").read_text()
        self.assertEqual(json.loads(egress)["turn_id"], turn_id)
        stored = self._metrics_text()
        for secret in _SECRETS:
            self.assertNotIn(secret, stored)
        self.assertNotIn("please", stored)

        # The phone rates the answer with that turn id; a reason chip, never text.
        rated = self.client.post("/feedback", headers=alice, json={
            "session_id": "chat-1", "turn_id": turn_id, "rating": "down", "reason": "language",
        })
        self.assertEqual(rated.status_code, 201, rated.text)
        feedback = pilot_metrics.read_jsonl(pilot_metrics.metrics_dir("alice") / "feedback.jsonl")
        self.assertEqual((feedback[-1]["turn_id"], feedback[-1]["reason"]), (turn_id, "language"))
        summary = pilot_metrics.profile_summary("alice")
        self.assertEqual(summary["feedback"]["down"], 1)


if __name__ == "__main__":
    unittest.main()
