from __future__ import annotations

import sys
import unittest
from pathlib import Path

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop
import narad_paths  # noqa: F401  — registers all phase dirs; must precede phase imports

# isort: split
from server import _dharma_gate


class DharmaInputGateTests(unittest.TestCase):
    def test_ssn_rule_ignores_identifiers_that_contain_the_letters(self) -> None:
        for query in (
            "Rename className to classnames in the React component",
            "The grossness of that bug report",
            "Toggle crossNetwork in the config",
            "Add an assnMap helper",
        ):
            self.assertIsNone(_dharma_gate(query), query)

    def test_sensitive_identifier_requests_are_still_blocked(self) -> None:
        for query in (
            "my SSN is 123-45-6789",
            "Store both SSNs for the kids",
            "What is my social security number?",
            "What's my passport number?",
        ):
            self.assertEqual(
                _dharma_gate(query), "I can't collect sensitive personal identifiers.", query
            )


if __name__ == "__main__":
    unittest.main()
