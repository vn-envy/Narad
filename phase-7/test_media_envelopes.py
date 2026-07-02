import unittest
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent / "skills"))
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
