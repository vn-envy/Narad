from __future__ import annotations

import sys
import unittest
from pathlib import Path

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop
import narad_paths  # noqa: F401  — registers all phase dirs; must precede phase imports

# isort: split
from runtime_contract import canonical_agent_names, collect_runtime_contract, health_payload


class RuntimeContractTests(unittest.TestCase):
    def test_canonical_agents_are_exactly_four(self) -> None:
        self.assertEqual(
            canonical_agent_names(),
            ["Matsya", "Rama", "Krishna", "Parashurama"],
        )

    def test_capabilities_payload_has_expected_shape(self) -> None:
        payload = collect_runtime_contract()
        self.assertEqual(payload["architecture"]["canonical_agent_count"], 4)
        self.assertEqual(len(payload["agents"]), 4)
        self.assertIn("providers", payload)
        self.assertIn("exa", payload["providers"])
        self.assertIn("xai", payload["providers"])
        self.assertIn("typesafe", payload["providers"])
        self.assertEqual(
            payload["providers"]["typesafe"]["provider"],
            "typesafe",
        )
        self.assertNotIn("tinyfish", payload["providers"])
        self.assertIn("model_roles", payload)
        self.assertIn("orchestrator", payload["model_roles"])
        self.assertIn("multimodal", payload["model_roles"])
        self.assertEqual(
            set(payload["model_roles"]["workers"]),
            {"matsya", "rama", "krishna", "parashurama"},
        )
        self.assertIn("tool_families", payload)
        self.assertIn("startup_checks", payload)
        self.assertIn("context_policy", payload)
        self.assertIn("profiles", payload["context_policy"])
        self.assertIn("computer", payload["tool_families"])
        self.assertIn("browser", payload["tool_families"]["computer"])
        self.assertIn("desktop", payload["tool_families"]["computer"])
        self.assertIn("contexts", payload["tool_families"]["computer"]["browser"])
        self.assertIn("signed_in", payload["tool_families"]["computer"]["browser"]["contexts"])
        self.assertIn("phone", payload["tool_families"])
        self.assertTrue(payload["tool_families"]["phone"]["android_only"])
        self.assertNotIn("security", payload["tool_families"])
        self.assertTrue(payload["tool_families"]["attachments"]["available"])
        self.assertTrue(payload["tool_families"]["attachments"]["folder_uploads"])
        self.assertIn("resilience", payload["providers"]["xai"])
        self.assertIn("ready", payload["providers"]["local-model-runtime"])
        self.assertIn("model_tag", payload["providers"]["local-model-runtime"])
        self.assertEqual(
            payload["providers"]["xai"]["resilience"]["fallback_model"],
            "deepseek/deepseek-flash",
        )
        self.assertIn("skill_tool_validation", payload)
        self.assertEqual(
            payload["skill_tool_validation"]["summary"]["invalid_workflow_stages"],
            0,
        )
        for agent in payload["agents"]:
            self.assertIn("discipline", agent)
            self.assertIn("degraded_tool_families", agent)

    def test_health_payload_reflects_canonical_architecture(self) -> None:
        payload = health_payload()
        self.assertEqual(payload["architecture"]["canonical_agent_count"], 4)
        self.assertEqual(
            payload["architecture"]["agent_names"],
            ["Matsya", "Rama", "Krishna", "Parashurama"],
        )
        self.assertIn(payload["status"], {"healthy", "degraded"})


if __name__ == "__main__":
    unittest.main()
