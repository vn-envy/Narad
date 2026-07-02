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
        self.assertIn("tool_families", payload)
        self.assertIn("startup_checks", payload)
        self.assertIn("ml_intern", payload)
        self.assertIn("context_policy", payload)
        self.assertIn("profiles", payload["context_policy"])
        self.assertIn("preview_only", payload["ml_intern"])
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
