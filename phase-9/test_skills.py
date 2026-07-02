import unittest
from pathlib import Path

import skills


class SkillRegistryTests(unittest.TestCase):
    def test_core_task_types_cover_all_four_agents(self) -> None:
        expected = {
            "bug": ["reproduce", "hypothesize", "instrument", "fix", "verify"],
            "financial_model": ["extract_inputs", "validate", "model", "interpret", "disclaimer"],
            "document_review": ["extract", "structure", "findings", "gaps", "synthesis"],
            "research": ["frame", "search", "triangulate", "gaps", "synthesise"],
            "file_cleanup": ["scan", "categorize", "preview", "confirm", "execute", "report"],
            "finance_import": ["import", "review", "reconcile", "baseline", "goals"],
            "health_log": ["capture", "confirm", "store", "summary"],
            "financial_decision": ["data", "steelman", "scenarios", "verdict"],
            "presentation_create": ["brief", "outline", "structure", "design_audit", "build"],
            "video_create": ["brief", "script", "design_redesign", "build"],
            "symptom_check": ["collect", "red_flag_check", "assessment", "triage", "disclaimer"],
            "mental_health_check": ["screen", "support", "resources", "professional_gate"],
        }

        for task_type, phases in expected.items():
            with self.subTest(task_type=task_type):
                self.assertEqual(skills.get_skill_for_task_type(task_type), phases)

    def test_build_skill_prompt_block_mentions_new_skill_families(self) -> None:
        block = skills.build_skill_prompt_block()
        for skill_name in (
            "financial_model",
            "document_review",
            "file_cleanup",
            "health_log",
            "financial_decision",
            "presentation_create",
            "video_create",
            "symptom_check",
        ):
            with self.subTest(skill_name=skill_name):
                self.assertIn(skill_name, block)

    def test_registry_source_no_longer_mentions_retired_agents(self) -> None:
        source = Path(skills.__file__).read_text(encoding="utf-8")
        for retired in ("Varaha", "Narasimha", "Buddha", "Vamana"):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, source)


if __name__ == "__main__":
    unittest.main()
