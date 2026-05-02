"""
Narad Claude baseline evaluator.

Calls Claude Sonnet with the Narad system prompt and the structured output
schema enforced via the Anthropic API's native JSON mode / tool-use path.
Target: ≥ 95% routing accuracy on the 50 test prompts.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import anthropic
from pydantic import ValidationError

from narad_schema import NaradRouting, NARAD_SYSTEM_PROMPT


def route_with_claude(
    query: str,
    model: str = "claude-sonnet-4-6",
    *,
    max_retries: int = 2,
) -> dict[str, Any]:
    """
    Route a single query via Claude with structured JSON output.
    Returns a dict with 'routing' (NaradRouting), 'raw', 'latency_ms',
    'parse_error' (if any).
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # Use tool-use path for guaranteed structured output from Claude
    routing_tool = {
        "name": "route_task",
        "description": "Emit Narad's routing decision as structured JSON.",
        "input_schema": NaradRouting.model_json_schema(),
    }

    for attempt in range(max_retries + 1):
        t0 = time.perf_counter()
        try:
            response = client.messages.create(
                model=model,
                max_tokens=512,
                system=NARAD_SYSTEM_PROMPT,
                tools=[routing_tool],
                tool_choice={"type": "tool", "name": "route_task"},
                messages=[{"role": "user", "content": query}],
            )
            latency_ms = int((time.perf_counter() - t0) * 1000)

            # Extract the tool input block
            tool_block = next(
                b for b in response.content if b.type == "tool_use"
            )
            raw = tool_block.input

            routing = NaradRouting.model_validate(raw)
            return {
                "routing": routing,
                "raw": raw,
                "latency_ms": latency_ms,
                "parse_error": None,
                "model": model,
            }

        except (ValidationError, StopIteration, KeyError) as exc:
            if attempt == max_retries:
                return {
                    "routing": None,
                    "raw": None,
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "parse_error": str(exc),
                    "model": model,
                }
            time.sleep(1)

        except anthropic.RateLimitError:
            time.sleep(5 * (attempt + 1))


def run_baseline(
    prompts_path: str = "test_prompts.json",
    output_path: str = "results/claude_results.json",
    model: str = "claude-sonnet-4-6",
) -> list[dict]:
    """Run all 50 prompts through Claude and save raw results."""
    with open(prompts_path) as f:
        data = json.load(f)

    results = []
    for i, prompt in enumerate(data["prompts"]):
        print(f"  [{i+1:02d}/50] {prompt['query'][:70]}...", end=" ", flush=True)

        result = route_with_claude(prompt["query"], model=model)

        record = {
            "id": prompt["id"],
            "query": prompt["query"],
            "category": prompt["category"],
            "expected_avatars": prompt["expected_avatars"],
            "expected_mode": prompt["expected_mode"],
            "actual_avatars": (
                [a.value for a in result["routing"].avatars]
                if result["routing"] else None
            ),
            "actual_mode": (
                result["routing"].mode.value
                if result["routing"] else None
            ),
            "rationale": (
                result["routing"].rationale if result["routing"] else None
            ),
            "eval_criteria": (
                result["routing"].eval_criteria if result["routing"] else None
            ),
            "latency_ms": result["latency_ms"],
            "parse_error": result["parse_error"],
            "model": result["model"],
        }

        # Score
        record["score"] = _score(record)
        print(record["score"])

        results.append(record)
        time.sleep(0.3)  # gentle rate limiting

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nSaved to {output_path}")
    return results


def _score(record: dict) -> str:
    """
    exact_match   — all expected avatars present, no extras, correct mode
    partial_match — all expected avatars present but extras OR wrong mode
    miss          — at least one expected avatar absent
    parse_error   — model returned invalid JSON
    """
    if record["parse_error"] or record["actual_avatars"] is None:
        return "parse_error"

    expected = set(record["expected_avatars"])
    actual = set(record["actual_avatars"])

    if not expected.issubset(actual):
        return "miss"

    if actual == expected and record["actual_mode"] == record["expected_mode"]:
        return "exact_match"

    return "partial_match"
