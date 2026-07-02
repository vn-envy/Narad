import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop
import narad_paths  # noqa: F401  — registers all phase dirs; must precede phase imports

# isort: split
import hyperframes_skill


class MediaEnvelopeTests(unittest.TestCase):
    def test_hyperframes_unavailable_returns_structured_envelope(self) -> None:
        with patch.object(hyperframes_skill, "_check_hyperframes", return_value=None):
            payload = hyperframes_skill.create_video_hyperframes("<html></html>", duration_seconds=5)
        self.assertEqual(payload["status"], "unavailable")
        self.assertIn("summary", payload)
        self.assertIn("ui", payload)


if __name__ == "__main__":
    unittest.main()
