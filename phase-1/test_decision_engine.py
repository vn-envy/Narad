from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]
import narad_paths  # noqa: F401

# isort: split
from decision_engine import DecisionQuestion, JevDecisionProvider, jev_status


class DecisionEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        import privacy_gateway

        home = Path(tempfile.mkdtemp(prefix="narad-jev-privacy-"))
        for patcher in (
            patch.dict(os.environ, {"NARAD_PII_DETECTOR": "rules"}),
            patch.object(privacy_gateway, "_privacy_dir", lambda profile_id=None: home),
            patch.object(privacy_gateway, "_family_terms", lambda: {"asha sharma": ("PERSON", "Asha Sharma")}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        privacy_gateway._terms_cache.update(key=None, pattern=None, labels={}, checked=0.0)
        self.addCleanup(privacy_gateway._terms_cache.update, checked=0.0)

    def test_jev_state_goes_through_the_privacy_gateway(self) -> None:
        captured: dict = {}
        provider = JevDecisionProvider(api_key="test-key")

        def fake_post(payload, headers):
            captured["payload"] = payload
            return httpx.Response(
                200,
                request=httpx.Request("POST", "https://api.typesafe.ai/v1/systemone"),
                json={"model": "jev-latest", "answers": {"risky": {"type": "noul", "noul": 0.1}}},
            )

        with patch.object(provider, "_post", side_effect=fake_post):
            result = provider.evaluate(
                decision_id="gateway_v1",
                state={"task": "Pay Asha Sharma via asha@okaxis"},
                questions={"risky": DecisionQuestion("noul", "Risky?")},
                record_cost=False,
            )
        self.assertTrue(result.available)
        self.assertNotIn("Asha", str(captured["payload"]))
        self.assertNotIn("okaxis", str(captured["payload"]))

        with patch.dict(os.environ, {"NARAD_PII_DETECTOR": "openmed"}), \
             patch("privacy_gateway.redactor_ready", return_value=False), \
             patch.object(provider, "_post") as never_called:
            blocked = provider.evaluate(
                decision_id="gateway_v1",
                state={"task": "Pay Asha Sharma"},
                questions={"risky": DecisionQuestion("noul", "Risky?")},
                record_cost=False,
            )
        self.assertEqual(blocked.status, "privacy_blocked")
        never_called.assert_not_called()

    def test_loopback_decision_server_is_local(self) -> None:
        # A Laya server on the Mac receives the real state: nothing leaves.
        captured: dict = {}
        provider = JevDecisionProvider(api_key="local", base_url="http://127.0.0.1:8090")

        def fake_post(payload, headers):
            captured["payload"] = payload
            return httpx.Response(
                200,
                request=httpx.Request("POST", "http://127.0.0.1:8090/v1/systemone"),
                json={"model": "laya", "answers": {"risky": {"type": "noul", "noul": 0.9}}},
            )

        with patch.object(provider, "_post", side_effect=fake_post):
            result = provider.evaluate(
                decision_id="local_v1",
                state={"task": "Send Asha Sharma's blood report to the clinic"},
                questions={"risky": DecisionQuestion("noul", "Risky?")},
                record_cost=False,
            )
        self.assertTrue(result.available)
        self.assertIn("Asha Sharma", str(captured["payload"]))

    def test_jev_parses_typed_answers_and_redacts_state(self) -> None:
        captured: dict = {}
        provider = JevDecisionProvider(api_key="test-key")

        def fake_post(payload, headers):
            captured["payload"] = payload
            captured["headers"] = headers
            return httpx.Response(
                200,
                request=httpx.Request("POST", "https://api.typesafe.ai/v1/systemone"),
                json={
                    "model": "jev-latest",
                    "usage": {"input_tokens": 100, "output_tokens": 0},
                    "answers": {
                        "route": {
                            "type": "choice",
                            "choice": "Matsya",
                            "confidence": 0.94,
                            "probabilities": {"Matsya": 0.94, "Rama": 0.06},
                        },
                        "risky": {"type": "noul", "noul": 0.1},
                        "quality": {
                            "type": "score",
                            "score": 8.4,
                            "confidence": 0.87,
                            "legend": {"0": "bad", "10": "great"},
                            "probabilities": {"8": 0.6, "9": 0.4},
                        },
                    },
                },
            )

        with patch.object(provider, "_post", side_effect=fake_post):
            result = provider.evaluate(
                decision_id="test_v1",
                state={"email": "person@example.com", "password": "do-not-send"},
                questions={
                    "route": DecisionQuestion("choice", "Owner?", {"Matsya": None, "Rama": None}),
                    "risky": DecisionQuestion("noul", "Risky?"),
                    "quality": DecisionQuestion("score", "Quality?", ["bad", "great"]),
                },
                record_cost=False,
            )

        self.assertTrue(result.available)
        self.assertEqual(result.answers["route"].value, "Matsya")
        self.assertFalse(result.answers["risky"].value)
        self.assertAlmostEqual(result.answers["risky"].confidence, 0.8)
        self.assertEqual(result.answers["quality"].value, 8.4)
        self.assertEqual(captured["payload"]["state"]["email"], "[EMAIL]")
        self.assertEqual(captured["payload"]["state"]["password"], "[REDACTED]")
        self.assertNotIn("test-key", str(captured["payload"]))
        self.assertEqual(captured["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(result.estimated_cost_usd, 0.0000042)

    def test_sensitive_state_is_blocked_without_opt_in(self) -> None:
        provider = JevDecisionProvider(api_key="test-key")
        result = provider.evaluate(
            decision_id="sensitive_v1",
            state={"task": "Review my medical record and prescription"},
            questions={"allow": DecisionQuestion("noul", "Proceed?")},
            record_cost=False,
        )
        self.assertEqual(result.status, "privacy_blocked")

    def test_status_never_exposes_key(self) -> None:
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "top-secret"}, clear=False):
            status = jev_status()
        self.assertTrue(status["available"])
        self.assertNotIn("top-secret", str(status))


if __name__ == "__main__":
    unittest.main()
