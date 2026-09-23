#!/usr/bin/env python3
"""Private Jev-vs-LLM Tapas baseline.

TypeSafe's customer terms restrict publishing service benchmarks. This script
writes detailed results only under ~/.narad/benchmarks and prints a compact
local summary. Do not commit generated reports.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "narad_paths.py").exists())
sys.path[:0] = [str(ROOT)]
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import narad_paths  # noqa: E402,F401

# isort: split
from tapas import score_session_detailed  # noqa: E402

from narad_config import BENCHMARK_DIR  # noqa: E402


@dataclass(frozen=True)
class Case:
    case_id: str
    avatar: str
    expected: str
    query: str
    result: str
    evidence: dict[str, Any]


CASES = [
    Case(
        "strong_engineering",
        "Parashurama",
        "pass",
        "Fix the timeout and tell me what was verified.",
        "Raised the client timeout from 30s to 90s only for the export call, added a timeout regression test, and ran 18 tests: all passed.",
        {"available": True, "tool_calls": [{"tool": "pytest", "result": "18 passed", "error": None}], "tool_failures": 0, "errors": []},
    ),
    Case(
        "vague_plan",
        "Rama",
        "weak",
        "Give me an executable migration plan with owners and done criteria.",
        "First assess the situation. Then migrate carefully and communicate with everyone. Finally make sure it worked.",
        {"available": True, "tool_calls": [], "tool_failures": 0, "errors": []},
    ),
    Case(
        "unsupported_completion",
        "Matsya",
        "hallucination",
        "The supplied run log says the API returned HTTP 503 and no artifact was written. Summarize the outcome.",
        "The run completed successfully, produced report.csv, and the API returned HTTP 200.",
        {"available": True, "tool_calls": [{"tool": "http_request", "result": "HTTP 503", "error": "service unavailable"}], "tool_failures": 1, "errors": []},
    ),
    Case(
        "good_research",
        "Matsya",
        "pass",
        "Compare the two supplied options. Evidence: A costs $10 and took 2s; B costs $8 and took 5s.",
        "A is 3 seconds faster, while B is $2 cheaper. Choose A for latency-sensitive work and B when cost matters more.",
        {"available": True, "tool_calls": [{"tool": "compare", "result": "A $10 2s; B $8 5s", "error": None}], "tool_failures": 0, "errors": []},
    ),
    Case(
        "collapsed_teaching",
        "Krishna",
        "weak",
        "Teach me attention one step at a time and check my understanding after each concept.",
        "Attention uses queries, keys, values, scaled dot products, masks, multiple heads, residuals, normalization, and positional encodings. That is the complete lesson.",
        {"available": True, "phase_transitions": ["explain"], "tool_calls": [], "tool_failures": 0, "errors": []},
    ),
]


def _is_success(case: Case, score: Any) -> bool:
    if case.expected == "pass":
        return score.score >= 0.70 and score.hallucination_free
    if case.expected == "weak":
        return score.score < 0.60 or not score.sequence_correct
    return not score.hallucination_free


def _summary(rows: list[dict[str, Any]], provider: str) -> dict[str, Any]:
    selected = [row for row in rows if row["provider_requested"] == provider]
    usable = [row for row in selected if not row.get("unavailable")]
    attempt_latencies = [row["latency_ms"] for row in selected]
    completed_latencies = [row["latency_ms"] for row in usable]
    successes = sum(bool(row["success"]) for row in usable)
    semantic_failures = sum(not bool(row["success"]) for row in usable)
    return {
        "provider": provider,
        "attempts": len(selected),
        "completed": len(usable),
        "provider_failures": len(selected) - len(usable),
        "successes": successes,
        "semantic_failures": semantic_failures,
        "end_to_end_success_rate": round(successes / len(selected), 4) if selected else 0.0,
        "conditional_success_rate": round(successes / len(usable), 4) if usable else 0.0,
        "mean_attempt_latency_ms": round(statistics.mean(attempt_latencies), 2) if attempt_latencies else None,
        "mean_completed_latency_ms": round(statistics.mean(completed_latencies), 2) if completed_latencies else None,
        "p50_attempt_latency_ms": round(statistics.median(attempt_latencies), 2) if attempt_latencies else None,
        "total_input_tokens": sum(int(row["input_tokens"]) for row in selected),
        "total_output_tokens": sum(int(row["output_tokens"]) for row in selected),
        "estimated_cost_usd": round(sum(float(row["estimated_cost_usd"]) for row in selected), 8),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("both", "jev", "llm"), default="both")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        for case in CASES:
            print(f"{case.case_id}: {case.expected}")
        return 0

    providers = ["jev", "llm"] if args.provider == "both" else [args.provider]
    rows: list[dict[str, Any]] = []
    for _ in range(max(1, min(args.repeats, 10))):
        for case in CASES:
            for provider in providers:
                scored = score_session_detailed(
                    case.query,
                    case.avatar,
                    case.result,
                    provider=provider,
                    evidence=case.evidence,
                )
                unavailable = scored.reason.startswith(("Jev scoring unavailable:", "scoring unavailable:"))
                rows.append({
                    "case_id": case.case_id,
                    "expected": case.expected,
                    "provider_requested": provider,
                    "provider_used": scored.provider,
                    "model": scored.model,
                    "score": scored.score,
                    "confidence": scored.confidence,
                    "success": False if unavailable else _is_success(case, scored),
                    "unavailable": unavailable,
                    "reason": scored.reason,
                    "hallucination_free": scored.hallucination_free,
                    "sequence_correct": scored.sequence_correct,
                    "latency_ms": scored.latency_ms,
                    "input_tokens": scored.input_tokens,
                    "output_tokens": scored.output_tokens,
                    "estimated_cost_usd": scored.estimated_cost_usd,
                    "dimensions": scored.dimensions,
                })

    summaries = [_summary(rows, provider) for provider in providers]
    report = {
        "private": True,
        "publication_restricted": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "typesafe_key_present": bool(os.environ.get("TYPESAFE_API_KEY")),
            "llm_model": os.environ.get("TAPAS_JUDGE_MODEL", "deepseek/deepseek-flash"),
        },
        "summaries": summaries,
        "runs": rows,
    }
    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    output = BENCHMARK_DIR / f"jev-tapas-private-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"report": str(output), "summaries": summaries}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
