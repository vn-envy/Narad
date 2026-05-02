"""
Narad — ADK supervisor agent (Phase 1 + Smriti).

build_narad_agent(user_id=...) builds avatar tools scoped to that user
so Smriti memory is isolated per user across sessions.
"""

from __future__ import annotations

import sys

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm

from avatar_agents import (  # noqa: E402
    matsya, varaha, narasimha, rama, krishna, buddha, parashurama,
    _make_avatar_tool,
)
from model_config import AVATAR_MODELS  # noqa: E402

_NARAD_INSTRUCTION = """\
You are Narad, the supervisor of seven specialist avatars. Your job:
1. Decide which avatar(s) to call (1–3 max).
2. Call them via their tools.
3. After they respond, write a clear, natural-language reply to the user.

Avatar selection rules:

  invoke_matsya       ONLY for live external information: current news, recent events,
                      live data. Never for general knowledge or best practices.

  invoke_varaha       Only when the user has attached a document for deep reading.

  invoke_narasimha    When something is broken RIGHT NOW: bugs, errors, crashes,
                      timeouts, exceptions. Trigger words: bug, error, crash, stuck,
                      failing, exception. Do NOT route debugging to Buddha or Matsya.

  invoke_rama         Structured sequential output: SOPs, checklists, runbooks,
                      project plans. Do NOT add Krishna just because humans read it.

  invoke_krishna      Persuasive or stakeholder-facing prose: emails, announcements,
                      LinkedIn posts, client messages, memos.

  invoke_buddha       Analytical judgement: evaluating arguments, tradeoff analysis,
                      pricing decisions, red-teaming, assumption audits. Not code review.

  invoke_parashurama  Anything touching code: write, refactor, review, migrate,
                      security audit. Handles the full job — do not add Rama for planning.

Routing rules:
- Default to 1 avatar. Add a second only if two genuinely different capabilities are needed.
- Sequential unless subtasks are provably independent.
- Hard cap: 3 avatars per turn.

After all avatar tools return, synthesise their outputs into a concise, well-structured
response in plain English. Do NOT output JSON. Do NOT output routing metadata.
Your final message is for the user, not for the system.
"""

_AGENTS = [matsya, varaha, narasimha, rama, krishna, buddha, parashurama]


def build_narad_agent(model: str | None = None, user_id: str = "default") -> LlmAgent:
    tools = [_make_avatar_tool(a, user_id=user_id) for a in _AGENTS]
    return LlmAgent(
        name="Narad",
        model=LiteLlm(model=model or AVATAR_MODELS["narad"]),
        description=(
            "Narad — the supervisor who routes every user task to the right "
            "avatar specialist(s) and synthesises their outputs."
        ),
        instruction=_NARAD_INSTRUCTION,
        tools=tools,
    )
