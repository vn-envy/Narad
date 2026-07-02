from __future__ import annotations

import asyncio
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop
import narad_paths  # noqa: F401  — registers all phase dirs; must precede phase imports

# isort: split
from context_governor import RuntimeEpoch, build_context_plan, count_text_tokens, should_rollover_epoch
from model_registry import get_model_profile, select_escalation

import conversation_memory
from smriti_core import recall_context


class ContextGovernorTests(unittest.TestCase):
    def test_deepseek_profile_uses_explicit_override(self) -> None:
        profile = get_model_profile("deepseek/deepseek-v4-flash", long_running=True)
        self.assertEqual(profile.max_context_tokens, 1_048_565)
        self.assertGreater(profile.hard_input_budget_tokens, 900_000)
        self.assertGreater(profile.soft_target_tokens, 600_000)

    def test_fallback_selection_prefers_larger_available_window(self) -> None:
        with patch.dict(os.environ, {"GOOGLE_API_KEY": "set"}, clear=False):
            fallback = select_escalation(
                "deepseek/deepseek-v4-flash",
                required_input_tokens=900_000,
            )
        self.assertIsNotNone(fallback)
        self.assertEqual(fallback.provider, "google")

    def test_should_rollover_epoch_when_turns_and_tokens_keep_growing(self) -> None:
        plan = build_context_plan(
            model="deepseek/deepseek-v4-flash",
            plane_specs=[
                {"key": "system", "content": "", "hard": True, "priority": 1, "token_estimate": 12_000},
                {"key": "current", "content": "x " * 20_000, "hard": True, "priority": 0},
            ],
            long_running=True,
        )
        epoch = RuntimeEpoch(
            epoch_id="epoch-1",
            model="deepseek/deepseek-v4-flash",
            turn_count=12,
            last_prompt_tokens=plan.soft_target_tokens + 100,
            peak_prompt_tokens=plan.hard_input_budget_tokens + 100,
        )
        reasons = should_rollover_epoch(epoch, plan, max_turns=12)
        self.assertIn("epoch_turn_limit_reached", reasons)
        self.assertIn("epoch_last_prompt_exceeds_soft_target", reasons)
        self.assertIn("epoch_peak_prompt_exceeds_hard_budget", reasons)

    def test_rehydration_query_is_token_budgeted_and_preserves_current_turn(self) -> None:
        fake_turns = [
            {"role": "user", "text": "Please continue the multi-step Narad architecture work " * 30},
            {"role": "assistant", "text": "Here is a detailed response with file references /tmp/demo.py and https://example.com/a " * 30},
            {"role": "user", "text": "Also preserve the code context from /Users/test/project/app.py " * 25},
        ]
        fake_state = {
            "thread_summary": "We were implementing context budgeting, runtime epochs, and artifact references. " * 12,
            "last_trace_session_id": "trace-123",
            "last_assistant_preview": "Added a governor, but the server path still needed rollover support.",
            "avatars": ["Matsya", "Parashurama"],
        }
        with patch.object(conversation_memory, "load_thread", return_value=fake_turns), patch.object(
            conversation_memory, "load_working_state", return_value=fake_state
        ):
            text, meta = conversation_memory.build_rehydration_query(
                user_id="default",
                session_id="sess-1",
                current_query="Continue from the previous context and finish the runtime wiring.",
                model="deepseek/deepseek-v4-flash",
                token_budget=420,
                return_metadata=True,
            )

        self.assertIn("[CURRENT USER TURN]", text)
        self.assertIn("Continue from the previous context", text)
        self.assertEqual(meta["token_budget"], 420)
        self.assertLessEqual(
            count_text_tokens("deepseek/deepseek-v4-flash", text),
            520,
        )
        self.assertIsInstance(meta["artifact_references"], list)

    def test_recall_context_respects_token_budget(self) -> None:
        fake_smriti = types.SimpleNamespace(
            recall=lambda query, user_id="default": "semantic memory " * 200,
            recall_exact=lambda query, user_id="default", avatar="": "exact memory " * 200,
        )

        async def _fake_project_context(*args, **kwargs):
            return "project context " * 220

        fake_smriti_v2 = types.SimpleNamespace(get_project_context=_fake_project_context)
        fake_sutra_engine = types.SimpleNamespace(
            format_for_injection=lambda sutras: "[SUTRAS]\n" + ("transferable rule\n" * 120),
            get_active_sutras=lambda avatar, task=None: [{"avatar": avatar or "Narad"}],
        )
        fake_sankalpa = types.SimpleNamespace(
            format_for_injection=lambda items: "[SANKALPA]\n" + ("commitment\n" * 120),
            get_active_sankalpas=lambda user_id, avatar=None: [{"id": "s1"}],
        )

        with patch.dict(
            sys.modules,
            {
                "smriti": fake_smriti,
                "smriti_v2": fake_smriti_v2,
                "sutra_engine": fake_sutra_engine,
                "sankalpa": fake_sankalpa,
            },
        ):
            packet = asyncio.run(
                recall_context(
                    "continue the narad context compaction work",
                    user_id="default",
                    avatar="Parashurama",
                    token_budget=360,
                    model="deepseek/deepseek-v4-flash",
                )
            )

        self.assertIsInstance(packet["provenance"], list)
        self.assertLessEqual(
            count_text_tokens("deepseek/deepseek-v4-flash", packet["context"]),
            460,
        )
        self.assertIsInstance(packet["compaction_applied"], list)


if __name__ == "__main__":
    unittest.main()
