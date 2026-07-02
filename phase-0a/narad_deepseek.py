"""
Narad DeepSeek V4 Pro evaluator.

Uses DeepSeek's OpenAI-compatible API with client.beta.chat.completions.parse()
for structured output. Same schema contract as narad_openai.py.

Reuses _score() from narad_claude.py — do not duplicate.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from pydantic import ValidationError

from narad_schema import NaradRouting, NARAD_SYSTEM_PROMPT

# DeepSeek uses json_object mode (no schema enforcement), so we append an
# explicit field-name example to prevent it inventing its own keys.
_DEEPSEEK_SCHEMA_SUFFIX = """
You MUST output valid JSON with EXACTLY these field names (no others):
{
  "avatars": ["<Avatar name>"],
  "mode": "sequential",
  "rationale": "<why these avatars>",
  "expected_outputs": ["<what each avatar should return>"],
  "eval_criteria": "<one-line success criterion>"
}

Rules:
- "avatars" must be a JSON array of 1–3 strings from: Matsya, Varaha, Narasimha, Rama, Krishna, Buddha, Parashurama
- "mode" must be exactly "sequential" or "parallel"
- All five fields are required. Do not rename them. Do not add extra fields.
"""

_DEEPSEEK_SYSTEM_PROMPT = NARAD_SYSTEM_PROMPT.rstrip() + _DEEPSEEK_SCHEMA_SUFFIX


def route_with_deepseek(
    query: str,
    model: str = "deepseek-chat",
    *,
    max_retries: int = 2,
) -> dict[str, Any]:
    """DeepSeek doesn't support OpenAI's beta structured output endpoint.
    Uses json_object mode + manual Pydantic parsing instead."""
    try:
        from openai import OpenAI
    except ImportError as e:
        raise ImportError("openai not installed. Run: pip install openai") from e

    client = OpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url="https://api.deepseek.com/v1",
    )

    for attempt in range(max_retries + 1):
        t0 = time.perf_counter()
        try:
            response = client.chat.completions.create(
                model=model,
                max_tokens=512,
                messages=[
                    {"role": "system", "content": _DEEPSEEK_SYSTEM_PROMPT},
                    {"role": "user", "content": query},
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
            )
            latency_ms = int((time.perf_counter() - t0) * 1000)
            raw_text = response.choices[0].message.content or ""
            parsed = json.loads(raw_text)
            routing = NaradRouting.model_validate(parsed)

            return {
                "routing": routing,
                "raw": routing.model_dump(),
                "latency_ms": latency_ms,
                "parse_error": None,
                "model": model,
            }

        except (ValidationError, json.JSONDecodeError, Exception) as exc:
            if attempt == max_retries:
                return {
                    "routing": None,
                    "raw": None,
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "parse_error": str(exc),
                    "model": model,
                }
            time.sleep(1)


def run_deepseek_eval(
    prompts_path: str = "test_prompts.json",
    output_path: str = "results/deepseek_results.json",
    model: str = "deepseek-chat",
) -> list[dict]:
    with open(prompts_path) as f:
        data = json.load(f)

    results = []
    for i, prompt in enumerate(data["prompts"]):
        print(f"  [{i+1:02d}/{len(data['prompts'])}] {prompt['query'][:70]}...", end=" ", flush=True)

        result = route_with_deepseek(prompt["query"], model=model)

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
        time.sleep(0.1)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nSaved to {output_path}")
    return results
