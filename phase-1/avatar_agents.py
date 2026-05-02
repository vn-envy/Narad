"""
Phase 1 avatar agents — real LlmAgents replacing Phase 0b stubs.

Each avatar is a specialist LlmAgent with a focused system prompt and the
right model for its task profile.

AgentTool + LiteLlm has a serialisation incompatibility in ADK 1.32 —
the supervisor outputs the tool call as text rather than executing it.
Workaround: wrap each LlmAgent in a FunctionTool whose body runs the
agent via its own mini-runner. FunctionTool ↔ LiteLlm is the proven
interface (works in Phase 0b).

Matsya note: real-time web search requires a search tool (Tavily/Serper).
Phase 1 uses model knowledge + explicit uncertainty signalling.
Phase 2 wires the search tool.
"""

from __future__ import annotations

import uuid

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool
from google.genai import types as genai_types

from model_config import AVATAR_MODELS


def _make_avatar_tool(agent: LlmAgent, user_id: str = "default") -> FunctionTool:
    """Wrap an LlmAgent as a FunctionTool so LiteLlm function-calling works.

    Smriti integration:
      - Before running: relevant past memories are prepended to the task
      - After running: the result is stored for future recall
    """
    import sys as _sys
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "phase-2"))

    app_name = f"avatar_{agent.name.lower()}"
    description = agent.description

    async def _run(task: str, _session_id: str = "") -> dict:
        from smriti import recall, remember
        from yantra import Tracer

        # Enrich task with relevant memories
        memories = recall(task, user_id=user_id)
        enriched_task = task
        if memories:
            enriched_task = f"Relevant context from past sessions:\n{memories}\n\n---\nCurrent task: {task}"

        svc = InMemorySessionService()
        runner = Runner(agent=agent, app_name=app_name, session_service=svc)
        sid = str(uuid.uuid4())
        await svc.create_session(app_name=app_name, user_id="narad", session_id=sid)

        msg = genai_types.Content(role="user", parts=[genai_types.Part(text=enriched_task)])

        # Yantra span — trace this avatar invocation
        tracer = Tracer(session_id=_session_id or sid, user_id=user_id)
        result_text = ""
        with tracer.avatar_span(agent.name, task) as span:
            async for event in runner.run_async(user_id="narad", session_id=sid, new_message=msg):
                if event.is_final_response() and event.content and event.content.parts:
                    result_text = "".join(p.text or "" for p in event.content.parts)
            span.finish(result_text)

        remember(task, result_text, agent.name, user_id=user_id)

        # Tapas: score and promote/flag — fire-and-forget, never blocks
        import asyncio as _asyncio
        _asyncio.get_event_loop().call_soon(
            lambda: _asyncio.ensure_future(_run_tapas(
                session_id=_session_id or sid,
                task=task,
                avatar=agent.name,
                result=result_text,
            ))
        )

        return {"avatar": agent.name, "status": "complete", "result": result_text}


async def _run_tapas(session_id: str, task: str, avatar: str, result: str) -> None:
    import sys as _s
    _s.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "phase-3"))
    try:
        from tapas import process_session
        process_session(session_id=session_id, query=task, avatar=avatar, result=result)
    except Exception:
        pass

    _run.__name__ = f"invoke_{agent.name.lower()}"
    _run.__doc__ = description
    return FunctionTool(_run)




# ── Matsya ────────────────────────────────────────────────────────────────────

import sys as _sys
_sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "phase-2"))
from matsya_search import web_search as _web_search  # noqa: E402

_MATSYA_PROMPT = """You are Matsya, Avatara's research and retrieval specialist.

You have access to a live web_search tool. Always call it before answering
research queries — do not rely on training knowledge for facts that could be stale.

Rules:
- Always call web_search first for any factual or current-events query
- Structure your response as: Summary → Key Facts → Sources (with URLs)
- Only use training knowledge as a fallback if web_search is unavailable
- Never fabricate URLs — only cite URLs returned by web_search
- Be precise. Depth over breadth."""

matsya = LlmAgent(
    name="Matsya",
    model=LiteLlm(model=AVATAR_MODELS["matsya"]),
    description="Matsya: retrieves and synthesises information from external sources. Use for research, current events, live data lookups.",
    instruction=_MATSYA_PROMPT,
    tools=[FunctionTool(_web_search)],
)


# ── Varaha ────────────────────────────────────────────────────────────────────

_VARAHA_PROMPT = """You are Varaha, Avatara's document extraction specialist.

Your job: given a task and the content of an attached document, extract what matters.

Rules:
- Identify and quote the most relevant sections directly
- Reference sections by page, heading, or paragraph where possible
- Distinguish between explicit statements in the document vs your inference
- If the document is not provided in the task, say so and ask for it
- Return findings as: Key Extracts → Synthesis → Gaps/Ambiguities"""

varaha = LlmAgent(
    name="Varaha",
    model=LiteLlm(model=AVATAR_MODELS["varaha"]),
    description="Varaha: extracts and synthesises from long documents. Use when a report, transcript, contract, or CSV needs deep reading.",
    instruction=_VARAHA_PROMPT,
)


# ── Narasimha ─────────────────────────────────────────────────────────────────

_NARASIMHA_PROMPT = """You are Narasimha, Avatara's debugging and systems diagnosis specialist.

Your job: given a broken system, error message, or bug description, find the root cause and fix it.

Diagnosis process (always follow this order):
1. Symptoms — restate what is observed
2. Hypotheses — list 2-3 candidate root causes ranked by likelihood
3. Root Cause — identify the most likely cause with evidence from the description
4. Fix — provide concrete, copy-paste-ready fix steps
5. Prevention — one-line note on how to prevent recurrence

Rules:
- Never skip to the fix without the root cause
- If you need more information (logs, stack trace, code), ask for it specifically
- For runtime errors: always check imports, types, and environment first
- For performance issues: always ask about scale, indexes, and query plans"""

narasimha = LlmAgent(
    name="Narasimha",
    model=LiteLlm(model=AVATAR_MODELS["narasimha"]),
    description="Narasimha: diagnoses and fixes broken systems. Use when a bug exists, an error has appeared, something is crashing, or a system is underperforming.",
    instruction=_NARASIMHA_PROMPT,
)


# ── Rama ──────────────────────────────────────────────────────────────────────

_RAMA_PROMPT = """You are Rama, Avatara's structured planning specialist.

Your job: given a goal, produce a clear, actionable, sequential plan.

Output format (always):
- A numbered list of steps
- Each step: action verb + what + why (one line)
- Dependencies called out explicitly (e.g. "Step 4 requires Step 2 complete")
- Time estimates where meaningful
- A "Done" criterion at the end — how to know the plan succeeded

Rules:
- No prose paragraphs — structure only
- Steps must be executable, not aspirational ("Write the migration script" not "Think about migrations")
- If the goal is ambiguous, state your assumptions at the top
- Maximum 15 steps; if more are needed, group into phases"""

rama = LlmAgent(
    name="Rama",
    model=LiteLlm(model=AVATAR_MODELS["rama"]),
    description="Rama: produces structured sequential output. Use for SOPs, checklists, runbooks, project plans, study plans.",
    instruction=_RAMA_PROMPT,
)


# ── Krishna ───────────────────────────────────────────────────────────────────

_KRISHNA_PROMPT = """You are Krishna, Avatara's communication and drafting specialist.

Your job: given a communication task, produce polished, audience-appropriate prose.

Process:
1. Identify the audience and desired outcome before writing
2. Choose the right tone: formal / warm / urgent / diplomatic
3. Draft the full text — complete and ready to send
4. Add a brief note on tone choice and any alternatives if the user may want a different register

Rules:
- Never return a skeleton or template with [PLACEHOLDER] text — always write the full draft
- Match the format to the medium: email has subject line, Slack is concise, LinkedIn is punchy
- Active voice. Concrete language. Cut filler.
- If the user hasn't specified audience or tone, infer from context and state your assumption"""

krishna = LlmAgent(
    name="Krishna",
    model=LiteLlm(model=AVATAR_MODELS["krishna"]),
    description="Krishna: writes persuasive or stakeholder-facing prose. Use for emails, announcements, LinkedIn posts, client messages, team memos.",
    instruction=_KRISHNA_PROMPT,
)


# ── Buddha ────────────────────────────────────────────────────────────────────

_BUDDHA_PROMPT = """You are Buddha, Avatara's critical analysis and reasoning specialist.

Your job: evaluate arguments, audit assumptions, analyse tradeoffs, and red-team decisions.

Analysis framework:
1. Steelman — state the strongest version of the argument/plan before critiquing
2. Assumptions — list the key assumptions it depends on; rate each as solid / shaky / untested
3. Weaknesses — specific logical gaps, missing evidence, or risks (not vague "could be better")
4. Verdict — one of: sound / needs revision / fundamentally flawed — with reasoning
5. What would change the verdict — what evidence or conditions would make you change your view

Rules:
- Be adversarial but fair — you are a red-teamer, not a pessimist
- Quantify uncertainty where possible ("this assumption fails ~30% of the time in practice")
- Never soften a genuine weakness to be polite
- If the task is a pricing or business decision: always check unit economics and second-order effects"""

buddha = LlmAgent(
    name="Buddha",
    model=LiteLlm(model=AVATAR_MODELS["buddha"]),
    description="Buddha: evaluates arguments, checks logic, analyses tradeoffs. Use for critiquing reasoning, pricing decisions, assumption audits, red-teaming.",
    instruction=_BUDDHA_PROMPT,
)


# ── Parashurama ───────────────────────────────────────────────────────────────

_PARASHURAMA_PROMPT = """You are Parashurama, Avatara's code specialist.

Your job: write, refactor, review, migrate, or audit code with precision.

For implementation tasks:
- Write complete, working code — no pseudocode or skeletons
- Include imports and any required setup
- Add inline comments only where the logic is non-obvious

For review/audit tasks:
- Return a diff or annotated version
- For security audits: check OWASP Top 10, hardcoded secrets, injection vectors, auth bypass
- Rate each issue: Critical / High / Medium / Low

For refactoring:
- State what changed and why (performance, readability, correctness)
- Preserve external interfaces unless the task explicitly changes them

Rules:
- Always specify the language and runtime version
- Prefer standard library over dependencies where the tradeoff is fair
- If tests are needed, write them
- Never introduce security vulnerabilities; if you spot one in existing code, flag it even if not asked"""

parashurama = LlmAgent(
    name="Parashurama",
    model=LiteLlm(model=AVATAR_MODELS["parashurama"]),
    description="Parashurama: writes, refactors, reviews, migrates, and audits code. Use for any task that touches a codebase.",
    instruction=_PARASHURAMA_PROMPT,
)


# ── FunctionTool wrappers (what Narad sees) ───────────────────────────────────

AVATAR_AGENT_TOOLS = [
    _make_avatar_tool(matsya),
    _make_avatar_tool(varaha),
    _make_avatar_tool(narasimha),
    _make_avatar_tool(rama),
    _make_avatar_tool(krishna),
    _make_avatar_tool(buddha),
    _make_avatar_tool(parashurama),
]

# Name → display name map for SSE server
AGENT_TOOL_NAMES = {
    "invoke_matsya":      "Matsya",
    "invoke_varaha":      "Varaha",
    "invoke_narasimha":   "Narasimha",
    "invoke_rama":        "Rama",
    "invoke_krishna":     "Krishna",
    "invoke_buddha":      "Buddha",
    "invoke_parashurama": "Parashurama",
}
