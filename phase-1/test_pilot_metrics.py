"""Pilot metrics: count-only turn records, feedback, voice, consent, routes, and the /chat hook."""

from __future__ import annotations

import asyncio
import json
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
        self.assertEqual(counts, {"trusted": 2, "redact": 1, "blocked": 1, "cloud_llm_calls": 2})

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
        self.assertTrue(everyone["profiles"]["default"]["needs_consent"])

    def test_chat_turn_writes_one_count_only_record(self) -> None:
        async def fake_run(req, session_id, queue):
            try:
                await queue.put(_event("avatar_start", avatar="Krishna", task="SECRET-PROMPT"))
                await queue.put(_event("step_event", avatar="Krishna", kind="tool_call",
                                       tool="compose_email", preview="SECRET-TOOL-ARG"))
                await queue.put(_event("narad_synthesis", text="SECRET-REPLY"))
                await queue.put(_event("done", session_id=session_id))
            finally:
                await queue.put(None)

        alice = self._headers("alice", "2468")
        with patch.object(server, "_run_agent_task", fake_run), \
                patch.object(server, "_agent_runtime_unavailable_reason", return_value=None), \
                patch("model_registry.provider_available_for_model", return_value=True), \
                patch.object(server, "_check_rate_limit", return_value=True):
            with self.client.stream("POST", "/chat", headers=alice,
                                    json={"query": "SECRET-PROMPT please", "session_id": "chat-1"}) as response:
                self.assertEqual(response.status_code, 200)
                body = "".join(chunk for chunk in response.iter_text())
        server._active_tasks.pop(("alice", "chat-1"), None)
        self.assertIn("SECRET-REPLY", body)  # the stream itself is untouched
        self.assertIn('"turn_id"', body)

        turns = pilot_metrics.metrics_dir("alice") / "turns.jsonl"
        for _ in range(100):
            if turns.exists():
                break
            time.sleep(0.02)
        rows = pilot_metrics.read_jsonl(turns)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["outcome"], "answered")
        self.assertEqual(rows[0]["session_id"], "chat-1")
        self.assertEqual(rows[0]["tools"], {"compose_email": 1})
        stored = self._metrics_text()
        for secret in _SECRETS:
            self.assertNotIn(secret, stored)
        self.assertNotIn("please", stored)


if __name__ == "__main__":
    unittest.main()
