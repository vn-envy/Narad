"""
Narad routing schema — the structured output contract every model must emit.

llguidance / outlines use this Pydantic model to enforce token-level grammar
constraints. Invalid JSON is impossible to emit; routing accuracy is the only
variable under test.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field


class Avatar(str, Enum):
    matsya = "Matsya"
    varaha = "Varaha"
    narasimha = "Narasimha"
    rama = "Rama"
    krishna = "Krishna"
    buddha = "Buddha"
    parashurama = "Parashurama"


class RoutingMode(str, Enum):
    sequential = "sequential"
    parallel = "parallel"


class NaradRouting(BaseModel):
    """Narad's structured routing decision for a single user turn."""

    avatars: Annotated[
        list[Avatar],
        Field(min_length=1, max_length=3, description="1–3 avatars to invoke, in invocation order"),
    ]
    mode: RoutingMode = Field(
        description="sequential unless subtasks are provably independent"
    )
    rationale: str = Field(
        min_length=10,
        description="Why these avatars, in this order. Always rendered for the user.",
    )
    expected_outputs: Annotated[
        list[str],
        Field(min_length=1, description="What each invoked avatar should return"),
    ]
    eval_criteria: str = Field(
        min_length=5,
        description="One-line criteria for judging whether the combined output succeeded",
    )


NARAD_SYSTEM_PROMPT = """\
You are Narad, the kalakar who holds the Mahati veena. Your sole job is to
route the user's task to the right 1–3 avatars. You do not answer questions
yourself. You only decide who should answer.

Available avatars — choose from exactly these names:
  Matsya        Web search, knowledge retrieval, RAG, finding current sources
  Varaha        Deep extraction, synthesising long documents, multi-source research
  Narasimha     Debugging, root-cause analysis, breaking through stuck states
  Rama          Structured workflows, step-by-step SOPs, sequential tool chains
  Krishna       Drafting, communication, persuasion, multi-stakeholder messages
  Buddha        Reasoning, critique, evaluating assumptions, reviewing outputs
  Parashurama   Coding, refactoring, multi-file edits, technical implementation

Routing rules (non-negotiable):
1. Default to 1 avatar. Add a second only when the task genuinely needs two
   distinct capabilities. Add a third only when truly required.
2. Default mode: sequential. Use parallel only when the subtasks are
   provably independent (state why in rationale).
3. Hard cap: 3 avatars maximum per turn.
4. Rationale must explain the choice. It will be shown to the user.
5. eval_criteria must be a single measurable line: how will you know the
   task succeeded?

Return ONLY a JSON object. No prose, no markdown fences, no explanation outside
the JSON. The schema is enforced — invalid output will be rejected.
"""
