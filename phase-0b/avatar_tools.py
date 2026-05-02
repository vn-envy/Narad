"""
Seven avatar stub tools for Phase 0b.

Each tool is a plain Python function decorated with @adk_tool. In Phase 0b
these are stubs — they echo back what they received so the delegation loop
can be proved end-to-end. In Phase 1 each stub is replaced with a real
LlmAgent with its own system prompt, tool set, and model.

The function signature (name + docstring) is what Google ADK sends to the
LLM as the tool description. Keep them precise — Narad's routing depends on
this exact text.
"""

from __future__ import annotations

from google.adk.tools import FunctionTool


def invoke_matsya(task: str) -> dict:
    """Matsya: retrieve information from external sources.
    Use for web search, finding current news, looking up live data.
    Returns sourced facts with citations."""
    return {
        "avatar": "Matsya",
        "status": "complete",
        "result": f"[Matsya stub] Retrieved external sources for: {task}",
        "citations": ["https://example.com/source-1", "https://example.com/source-2"],
        "confidence": 0.9,
    }


def invoke_varaha(task: str) -> dict:
    """Varaha: extract and synthesise from long documents the user has attached.
    Use when a report, transcript, contract, or CSV needs deep reading.
    Returns extracted findings with page/section references."""
    return {
        "avatar": "Varaha",
        "status": "complete",
        "result": f"[Varaha stub] Extracted key findings from attached document for: {task}",
        "sections_read": 12,
        "confidence": 0.85,
    }


def invoke_narasimha(task: str) -> dict:
    """Narasimha: diagnose and fix broken systems.
    Use when a bug exists in running code, an error message has appeared,
    something is crashing, or the user is stuck on a technical problem.
    Returns root cause analysis and concrete fix steps."""
    return {
        "avatar": "Narasimha",
        "status": "complete",
        "result": f"[Narasimha stub] Diagnosed issue: {task}",
        "root_cause": "stub — real diagnosis in Phase 1",
        "fix_steps": ["step 1", "step 2"],
        "confidence": 0.8,
    }


def invoke_rama(task: str) -> dict:
    """Rama: produce structured sequential output.
    Use for SOPs, checklists, runbooks, project plans, study plans.
    Returns a numbered step-by-step plan."""
    return {
        "avatar": "Rama",
        "status": "complete",
        "result": f"[Rama stub] Structured plan for: {task}",
        "steps": ["Step 1: ...", "Step 2: ...", "Step 3: ..."],
        "confidence": 0.92,
    }


def invoke_krishna(task: str) -> dict:
    """Krishna: write persuasive or stakeholder-facing prose.
    Use for emails, announcements, LinkedIn posts, client messages, team memos.
    Returns polished draft text."""
    return {
        "avatar": "Krishna",
        "status": "complete",
        "result": f"[Krishna stub] Drafted communication for: {task}",
        "draft": "Dear [recipient], ...",
        "tone": "warm and persuasive",
        "confidence": 0.93,
    }


def invoke_buddha(task: str) -> dict:
    """Buddha: evaluate arguments, check logic, analyse tradeoffs.
    Use for critiquing reasoning, pricing decisions, assumption audits,
    red-teaming, or any task requiring analytical judgement.
    Returns structured critique with specific weaknesses identified."""
    return {
        "avatar": "Buddha",
        "status": "complete",
        "result": f"[Buddha stub] Evaluated argument for: {task}",
        "weaknesses": ["assumption 1 is unsupported", "logic gap in step 3"],
        "verdict": "argument needs revision",
        "confidence": 0.88,
    }


def invoke_parashurama(task: str) -> dict:
    """Parashurama: write, refactor, review, migrate, or audit code.
    Use for any task that touches a codebase: implementing features,
    converting APIs, writing tests, reviewing diffs for security issues.
    Returns code changes as a diff or new file content."""
    return {
        "avatar": "Parashurama",
        "status": "complete",
        "result": f"[Parashurama stub] Code changes for: {task}",
        "diff": "--- a/file.py\n+++ b/file.py\n@@ stub @@",
        "files_changed": 1,
        "confidence": 0.87,
    }


# Wrap as ADK FunctionTools — these are what Narad sees in its tool registry
AVATAR_TOOLS = [
    FunctionTool(invoke_matsya),
    FunctionTool(invoke_varaha),
    FunctionTool(invoke_narasimha),
    FunctionTool(invoke_rama),
    FunctionTool(invoke_krishna),
    FunctionTool(invoke_buddha),
    FunctionTool(invoke_parashurama),
]
