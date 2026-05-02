"""
Narad — ADK supervisor agent (Phase 1).

Same interface as Phase 0b but avatar tools are now real LlmAgents
wrapped with AgentTool instead of FunctionTool stubs.
"""

from __future__ import annotations

import sys

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "phase-0a"))
from narad_schema import NARAD_SYSTEM_PROMPT  # noqa: E402

from avatar_agents import AVATAR_AGENT_TOOLS  # noqa: E402
from model_config import AVATAR_MODELS  # noqa: E402

_NARAD_INSTRUCTION = f"""{NARAD_SYSTEM_PROMPT}

You have access to 7 specialist avatar tools. Call the right tool(s) based on your routing decision.
- You may call up to 3 tools per turn.
- For sequential tasks: call one at a time, passing each result forward in the next call.
- For parallel tasks: call them simultaneously.
- After all avatars respond, synthesise their outputs into a single coherent response for the user.
"""


def build_narad_agent(model: str | None = None) -> LlmAgent:
    return LlmAgent(
        name="Narad",
        model=LiteLlm(model=model or AVATAR_MODELS["narad"]),
        description=(
            "Narad — the supervisor who routes every user task to the right "
            "avatar specialist(s) and synthesises their outputs."
        ),
        instruction=_NARAD_INSTRUCTION,
        tools=AVATAR_AGENT_TOOLS,
    )
