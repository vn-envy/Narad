"""
Narad OpenAI baseline evaluator.

Uses client.beta.chat.completions.parse() — OpenAI's native Pydantic structured
output path. The model cannot emit invalid JSON; routing accuracy is the only
variable under test. Works with gpt-4o and gpt-4o-mini.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from pydantic import ValidationError

from narad_schema import NaradRouting, NARAD_SYSTEM_PROMPT


def route_with_openai(
    query: str,
    model: str = "gpt-4o",
    *,
    max_retries: int = 2,
) -> dict[str, Any]:
    try:
        from openai import OpenAI
    except ImportError as e:
        raise ImportError("openai not installed. Run: pip install openai") from e

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    for attempt in range(max_retries + 1):
        t0 = time.perf_counter()
        try:
            response = client.beta.chat.completions.parse(
                model=model,
                max_tokens=512,
                messages=[
                    {"role": "system", "content": NARAD_SYSTEM_PROMPT},
                    {"role": "user", "content": query},
                ],
                response_format=NaradRouting,
            )
            latency_ms = int((time.perf_counter() - t0) * 1000)
            routing = response.choices[0].message.parsed

            return {
                "routing": routing,
                "raw": routing.model_dump() if routing else None,
                "latency_ms": latency_ms,
                "parse_error": None,
                "model": model,
            }

        except (ValidationError, Exception) as exc:
            if attempt == max_retries:
                return {
                    "routing": None,
                    "raw": None,
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "parse_error": str(exc),
                    "model": model,
                }
            time.sleep(1)


def run_openai_baseline(
    prompts_path: str = "test_prompts.json",
    output_path: str = "results/gpt_results.json",
    model: str = "gpt-4o",
) -> list[dict]:
    with open(prompts_path) as f:
        data = json.load(f)

    results = []
    for i, prompt in enumerate(data["prompts"]):
        print(f"  [{i+1:02d}/50] {prompt['query'][:70]}...", end=" ", flush=True)

        result = route_with_openai(prompt["query"], model=model)

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
            "model": model,
        }

        from narad_claude import _score
        record["score"] = _score(record)
        print(record["score"])

        results.append(record)
        time.sleep(0.2)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nSaved to {output_path}")
    return results
