"""
Narad — ADK supervisor agent (Phase 0b).

Narad uses GPT-4o via LiteLlm to decide which avatar tool(s) to call.
The 7 avatar tools are injected at construction time. Narad calls them
sequentially or in parallel based on its routing decision.

In Phase 1 each FunctionTool stub is replaced by a real LlmAgent sub-agent.
The supervisor interface stays identical — that is the architectural bet.
"""

from __future__ import annotations

import os
import sys

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm

# Add phase-0a to path so we can reuse NARAD_SYSTEM_PROMPT
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "phase-0a"))
from narad_schema import NARAD_SYSTEM_PROMPT  # noqa: E402

from avatar_tools import AVATAR_TOOLS  # noqa: E402

_NARAD_INSTRUCTION = f"""{NARAD_SYSTEM_PROMPT}

You have access to 7 tools — one per avatar. Call the right tool(s) based
on your routing decision. You may call up to 3 tools per turn.
For sequential tasks: call them one at a time, passing each result forward.
For parallel tasks: call them simultaneously.
After all avatar tools have returned, synthesise their outputs into a single
coherent response for the user.
"""


def build_narad_agent(model: str = "openai/gpt-4o") -> LlmAgent:
    """Construct the Narad supervisor agent backed by the given model."""
    return LlmAgent(
        name="Narad",
        model=LiteLlm(model=model),
        description=(
            "Narad — the supervisor who routes every user task to the right "
            "avatar specialist(s) and synthesises their outputs."
        ),
        instruction=_NARAD_INSTRUCTION,
        tools=AVATAR_TOOLS,
    )
