"""The PWA's speech splitter (phase-4/frontend/src/lib/speech-segments.ts).

The frontend has no test runner. node ≥ 22.6 strips TypeScript types itself, so
these cases run the module as written with no npm dependency; they are skipped
where node is missing or older.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
_MODULE = _r / "phase-4" / "frontend" / "src" / "lib" / "speech-segments.ts"

# Runs every case in one node process: {kind, text | chunks, options, spoken}.
_DRIVER = """
import * as seg from %s
const cases = JSON.parse(await new Promise(resolve => {
  let data = ''
  process.stdin.on('data', chunk => { data += chunk })
  process.stdin.on('end', () => resolve(data))
}))
const results = cases.map(c => {
  const options = c.options ?? {}
  if (c.kind === 'split') return seg.splitForSpeech(c.text, options)
  if (c.kind === 'remaining') return seg.remainingSegments(c.text, c.spoken, options)
  const splitter = new seg.SpeechSplitter(options)
  const segments = []
  const readyAfter = []
  for (const chunk of c.chunks) {
    const ready = splitter.push(chunk)
    readyAfter.push(ready.length)
    segments.push(...ready)
  }
  segments.push(...splitter.finish())
  return { segments, readyAfter }
})
process.stdout.write(JSON.stringify(results))
"""

ONE = {"minChars": 1}  # boundary cases: no merging of short pieces

REPORT = (
    "Your HbA1c is 8.2 today. Dr. Sharma sees you at 10:30 tomorrow. The bill is "
    "₹1,200.50 in total. Eat light, e.g. dal and rice, vs. heavy food. Details are at "
    "https://example.com/report.pdf?id=3 for later."
)
LONG_FIRST = (
    "Namaste, this is a fairly long opening sentence that keeps going with details about "
    "your appointment, the clinic address and the documents you need to carry, and it only "
    "ends much later than a normal sentence would."
)


def _chunks(text: str, size: int) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)]


def _node_command() -> list[str] | None:
    node = shutil.which("node")
    if not node:
        return None
    try:
        version = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10).stdout
        major, minor = (int(part) for part in version.strip().lstrip("v").split(".")[:2])
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if (major, minor) < (22, 6):
        return None
    return [node, "--experimental-strip-types", "--no-warnings"]


class SpeechSegmentsTest(unittest.TestCase):
    CASES: dict[str, dict] = {
        "devanagari": {"kind": "split", "options": ONE, "text": (
            "आपकी रिपोर्ट तैयार है। क्या आप कल सुबह दस बजे आ सकते हैं? "
            "डॉक्टर ने कहा है कि दवा जारी रखें॥ धन्यवाद!"
        )},
        "no_false_splits": {"kind": "split", "options": ONE, "text": REPORT},
        "hinglish": {"kind": "split", "options": ONE,
                     "text": "Aapki report ready hai. Kal 10:30 baje aana, Dr. Sharma milenge."},
        "hindi_abbreviations": {"kind": "split", "options": ONE,
                                "text": "कल सुबह 10:30 बजे डॉ. शर्मा से मिलिए। फीस रु. 500 है।"},
        "am_pm": {"kind": "split", "options": ONE,
                  "text": "Come at 10 a.m. tomorrow. Leave by 5 p.m. Carry water."},
        "lists_and_lines": {"kind": "split", "options": ONE,
                            "text": "Here is the plan:\n- Take rest\n- Drink water\n1. Call Mr. Verma\n2) Book the test"},
        "code_tables_markdown": {"kind": "split", "options": ONE, "text": (
            "## Steps\nRun this:\n```bash\nnpm run build. Then test.\n```\n"
            "| Day | Dose |\n|---|---|\n| Mon | 1 |\n**Done** — _all_ set."
        )},
        "hindi_notes": {"kind": "split", "options": {"lang": "auto", "minChars": 1},
                        "text": "यह रहा कोड:\n```\nx = 1\n```"},
        "links": {"kind": "split", "options": ONE,
                  "text": "See [the guide](https://example.com/g) or https://example.com/a, https://example.com/b."},
        "emoji_and_emphasis": {"kind": "split", "options": ONE, "text": "✅ **Booked** your slot 🎉"},
        "lowercase_continues": {"kind": "split", "options": ONE,
                                "text": "We need rice, dal, etc. and some ghee. Wait... what about milk? Yes!"},
        "initials_and_numbers": {"kind": "split", "options": ONE,
                                 "text": "Go to Gate No. 5 today. A. P. J. Abdul Kalam spoke here."},
        "short_pieces_merge": {"kind": "split", "text": "Sure! Here is what I found about your report."},
        "cap": {"kind": "split", "options": {"totalChars": 500}, "text": " ".join(
            f"Sentence number {n} is here to make this reply long enough for the cap." for n in range(1, 51)
        )},
        "stream_report": {"kind": "stream", "chunks": _chunks(REPORT, 3)},
        "whole_report": {"kind": "split", "text": REPORT},
        "stream_decimal": {"kind": "stream", "options": ONE,
                           "chunks": ["The dose is 8.", "2 mg today. Next", " dose tomorrow."]},
        "stream_long_first": {"kind": "stream", "chunks": _chunks(LONG_FIRST, 5)},
        "remaining": {"kind": "remaining", "options": ONE, "spoken": ["first sentence HERE"],
                      "text": "First sentence here. Second sentence here. Third one is new."},
    }

    @classmethod
    def setUpClass(cls) -> None:
        command = _node_command()
        if command is None:
            raise unittest.SkipTest("node >= 22.6 (TypeScript type stripping) is not available")
        with tempfile.TemporaryDirectory() as tmp:
            driver = Path(tmp) / "driver.mjs"
            driver.write_text(_DRIVER % json.dumps(_MODULE.as_uri()), encoding="utf-8")
            proc = subprocess.run(
                [*command, str(driver)],
                input=json.dumps(list(cls.CASES.values())),
                capture_output=True, text=True, timeout=60,
            )
        if proc.returncode != 0:
            raise AssertionError(f"node failed: {proc.stderr[-2000:]}")
        cls.results = dict(zip(cls.CASES, json.loads(proc.stdout)))

    def test_devanagari_danda_and_question_marks(self) -> None:
        self.assertEqual(self.results["devanagari"], [
            "आपकी रिपोर्ट तैयार है।",
            "क्या आप कल सुबह दस बजे आ सकते हैं?",
            "डॉक्टर ने कहा है कि दवा जारी रखें॥",
            "धन्यवाद!",
        ])

    def test_decimals_times_amounts_abbreviations_and_urls_stay_whole(self) -> None:
        self.assertEqual(self.results["no_false_splits"], [
            "Your HbA1c is 8.2 today.",
            "Dr. Sharma sees you at 10:30 tomorrow.",
            "The bill is ₹1,200.50 in total.",
            "Eat light, e.g. dal and rice, vs. heavy food.",
            "Details are at the link on screen for later.",
        ])
        self.assertEqual(self.results["hinglish"], [
            "Aapki report ready hai.", "Kal 10:30 baje aana, Dr. Sharma milenge.",
        ])
        self.assertEqual(self.results["hindi_abbreviations"], [
            "कल सुबह 10:30 बजे डॉ. शर्मा से मिलिए।", "फीस रु. 500 है।",
        ])
        self.assertEqual(self.results["am_pm"], ["Come at 10 a.m. tomorrow.", "Leave by 5 p.m.", "Carry water."])
        self.assertEqual(self.results["lowercase_continues"], [
            "We need rice, dal, etc. and some ghee.", "Wait... what about milk?", "Yes!",
        ])
        self.assertEqual(self.results["initials_and_numbers"], [
            "Go to Gate No. 5 today.", "A. P. J. Abdul Kalam spoke here.",
        ])

    def test_newlines_and_list_items_end_segments(self) -> None:
        self.assertEqual(self.results["lists_and_lines"], [
            "Here is the plan:", "Take rest.", "Drink water.", "Call Mr. Verma.", "Book the test.",
        ])

    def test_code_tables_and_markdown_are_not_read_out(self) -> None:
        self.assertEqual(self.results["code_tables_markdown"], [
            "Steps.", "Run this:", "I've put the code on screen.", "The table is on screen.", "Done — all set.",
        ])
        self.assertEqual(self.results["hindi_notes"], ["यह रहा कोड:", "कोड स्क्रीन पर है।"])
        self.assertEqual(self.results["links"], ["See the guide or the links on screen."])
        self.assertEqual(self.results["emoji_and_emphasis"], ["Booked your slot."])

    def test_short_pieces_join_the_next_sentence(self) -> None:
        self.assertEqual(self.results["short_pieces_merge"], ["Sure! Here is what I found about your report."])

    def test_long_replies_end_with_rest_on_screen(self) -> None:
        segments = self.results["cap"]
        self.assertEqual(segments[-1], "The rest is on screen.")
        self.assertLessEqual(sum(len(s) for s in segments[:-1]), 500)

    def test_streaming_splits_like_the_whole_text(self) -> None:
        self.assertEqual(self.results["stream_report"]["segments"], self.results["whole_report"])

    def test_a_decimal_split_across_chunks_waits(self) -> None:
        result = self.results["stream_decimal"]
        self.assertEqual(result["segments"], ["The dose is 8.2 mg today.", "Next dose tomorrow."])
        self.assertEqual(result["readyAfter"][0], 0)  # "8." alone is not a sentence end

    def test_long_first_sentence_breaks_early_at_a_comma(self) -> None:
        result = self.results["stream_long_first"]
        first = result["segments"][0]
        self.assertTrue(first.endswith(","), first)
        self.assertGreaterEqual(len(first), 60)
        ready_at = next(i for i, n in enumerate(result["readyAfter"]) if n)
        self.assertLessEqual(ready_at * 5, 130)  # spoken after ~120 streamed characters
        self.assertEqual(" ".join(result["segments"]), LONG_FIRST)

    def test_final_text_never_repeats_what_was_spoken(self) -> None:
        self.assertEqual(self.results["remaining"], ["Second sentence here.", "Third one is new."])


if __name__ == "__main__":
    unittest.main()
