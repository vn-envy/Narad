import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import docling_skill
import email_skill
import finance_skill
import http_skill
import local_skill
import ml_intern_skill
import shell_skill
import webwright_skill


class ToolSmokeTests(unittest.TestCase):
    def test_document_and_email_tools_work_in_safe_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            doc_path = Path(tmpdir) / "note.txt"
            doc_path.write_text("Narad tool smoke test\nSecond line", encoding="utf-8")

            extracted = docling_skill.extract_document(str(doc_path))
            self.assertEqual(extracted["status"], "ok")
            self.assertIn("Narad tool smoke test", extracted["content"])

            preview = email_skill.compose_email(
                to="user@example.com",
                subject="Smoke Test",
                body="This is a preview only.",
            )
            self.assertEqual(preview["status"], "ok")
            self.assertEqual(preview["preview"]["subject"], "Smoke Test")

            dry_run = email_skill.send_email(
                to="user@example.com",
                subject="Smoke Test",
                body="This is a preview only.",
                dry_run=True,
            )
            self.assertEqual(dry_run["status"], "preview")
            self.assertTrue(dry_run["preview"]["dry_run"])

    def test_local_filesystem_tools_support_safe_previews(self) -> None:
        with tempfile.TemporaryDirectory(dir=str(Path.home())) as tmpdir:
            root = Path(tmpdir)
            (root / "photo.jpg").write_text("img", encoding="utf-8")
            (root / "notes.txt").write_text("text", encoding="utf-8")

            scan = local_skill.scan_directory(str(root))
            self.assertEqual(scan["status"], "ok")
            self.assertEqual(scan["item_count"], 2)

            plan = local_skill.organize_by_type(str(root), dry_run=True)
            self.assertEqual(plan["status"], "ok")
            self.assertTrue(plan["dry_run"])
            self.assertGreaterEqual(len(plan["plan"]), 2)

    def test_shell_skill_reads_and_executes_in_home_subdir(self) -> None:
        with tempfile.TemporaryDirectory(dir=str(Path.home())) as tmpdir:
            root = Path(tmpdir)
            file_path = root / "hello.txt"
            file_path.write_text("shell smoke", encoding="utf-8")

            read = shell_skill.read_file(str(file_path))
            self.assertEqual(read["status"], "ok")
            self.assertIn("shell smoke", read["content"])

            result = shell_skill.run_shell("pwd", working_dir=str(root), timeout_s=5)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["exit_code"], 0)
            self.assertIn(str(root), result["stdout"])

    def test_finance_tool_uses_temp_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            finance_skill._DB_PATH = Path(tmpdir) / "finance.db"  # type: ignore[attr-defined]

            no_data = finance_skill.get_financial_context()
            self.assertEqual(no_data["status"], "ok")
            self.assertFalse(no_data["has_data"])

            budget = finance_skill.set_budget("Food", 8000)
            self.assertEqual(budget["status"], "ok")

            budget_status = finance_skill.get_budget_status()
            self.assertEqual(budget_status["status"], "ok")
            self.assertEqual(budget_status["total_budget"], 8000.0)
            self.assertIn("over_budget", budget_status)

            goals = finance_skill.get_goals()
            self.assertEqual(goals["status"], "ok")

    def test_search_last30days_returns_richer_envelope(self) -> None:
        reddit_payload = {
            "data": {
                "children": [{
                    "data": {
                        "title": "Narad is getting traction",
                        "permalink": "/r/ai/comments/123",
                        "selftext": "People like the dashboard and memory model.",
                        "score": 42,
                        "num_comments": 11,
                        "subreddit": "ai",
                    }
                }]
            }
        }
        hn_payload = {
            "hits": [{
                "title": "Show HN: Narad tooling stack",
                "url": "https://example.com/hn",
                "story_text": "Interesting routing and tool patterns.",
                "points": 18,
                "num_comments": 7,
                "author": "alice",
            }]
        }
        github_payload = {
            "items": [{
                "full_name": "narad-ai/narad",
                "html_url": "https://github.com/narad-ai/narad",
                "description": "Multi-agent local-first assistant",
                "stargazers_count": 90,
                "open_issues_count": 12,
                "language": "Python",
            }]
        }
        nitter_html = '<div class="tweet-content media-body">Narad memory stack looks promising.</div>'

        def fake_http(method: str, url: str, headers=None, body=None, timeout_s=30):
            if "reddit.com/search.json" in url:
                return {"status": "ok", "body_json": reddit_payload}
            if "hn.algolia.com" in url:
                return {"status": "ok", "body_json": hn_payload}
            if "api.github.com/search/repositories" in url:
                return {"status": "ok", "body_json": github_payload}
            if "nitter.net/search" in url:
                return {"status": "ok", "body": nitter_html}
            return {"status": "error", "message": "unexpected url"}

        with patch.object(http_skill, "http_request", side_effect=fake_http):
            payload = http_skill.search_last30days("Narad")

        self.assertEqual(payload["status"], "ok")
        self.assertIn("source_breakdown", payload)
        self.assertIn("engagement_signals", payload)
        self.assertIn("judge_summary", payload)
        self.assertIn("coverage_gaps", payload)
        self.assertTrue(payload["citations"])
        self.assertIn("github", payload["source_breakdown"])

    def test_web_task_refuses_irreversible_actions(self) -> None:
        payload = webwright_skill.web_task("Submit this job application for me")
        self.assertEqual(payload["status"], "needs_confirmation")
        self.assertTrue(payload["requires_confirmation"])

    def test_ml_intern_preview_returns_command(self) -> None:
        with patch.object(ml_intern_skill, "_find_ml_intern", return_value="/usr/local/bin/ml-intern"):
            payload = ml_intern_skill.run_ml_experiment(
                "Train a compact classifier on this dataset",
                model="Qwen/Qwen3-Coder-30B-A3B-Instruct",
                dry_run=True,
            )
        self.assertEqual(payload["status"], "preview")
        self.assertTrue(payload["requires_confirmation"])
        self.assertIn("ml-intern", payload["command"])
        self.assertIn("--model", payload["command"])
        self.assertIn("--max-iterations", payload["command"])
        self.assertIn("readiness", payload)

    def test_ml_intern_reports_preview_only_without_hf_token(self) -> None:
        with patch.object(ml_intern_skill, "_find_ml_intern", return_value="/usr/local/bin/ml-intern"), patch.dict("os.environ", {}, clear=True):
            status = ml_intern_skill.inspect_ml_intern_status(model="Qwen/Qwen3-Coder-30B-A3B-Instruct")
        self.assertTrue(status["available"])
        self.assertFalse(status["ready"])
        self.assertTrue(status["preview_only"])
        self.assertIn("HF_TOKEN", " ".join(status["env_requirements"]))


if __name__ == "__main__":
    unittest.main()
