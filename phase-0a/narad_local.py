"""
Narad local model evaluator — mlx-lm + outlines on Apple Silicon.

outlines 1.x API:
  model  = outlines.from_mlxlm(mlx_model, tokenizer)
  gen    = outlines.Generator(model, NaradRouting)
  result = gen(prompt, max_tokens=512)

Token-level grammar masking: invalid JSON is impossible to emit.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from narad_schema import NaradRouting, NARAD_SYSTEM_PROMPT

DEFAULT_MODEL = "mlx-community/gemma-3-4b-it-4bit"

_model_cache: dict[str, Any] = {}


def _get_generator(model_id: str) -> Any:
    """Load model + build outlines generator (cached after first call)."""
    if model_id in _model_cache:
        return _model_cache[model_id]

    try:
        import outlines
        from mlx_lm import load
    except ImportError as e:
        raise ImportError("Run: pip install mlx-lm outlines") from e

    print(f"  Loading model {model_id} (first run downloads ~2.5GB)...")
    mlx_model, tokenizer = load(model_id)
    print("  Model loaded.")

    model = outlines.from_mlxlm(mlx_model, tokenizer)
    generator = outlines.Generator(model, NaradRouting)

    _model_cache[model_id] = generator
    return generator


def route_with_local(
    query: str,
    model_id: str = DEFAULT_MODEL,
    *,
    max_tokens: int = 512,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        generator = _get_generator(model_id)
        prompt = f"{NARAD_SYSTEM_PROMPT}\n\nUser task: {query}\n\nJSON routing:"
        raw = generator(prompt, max_tokens=max_tokens)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        # outlines 1.x SteerableGenerator returns a JSON string, not a Pydantic instance
        if isinstance(raw, str):
            result = NaradRouting.model_validate_json(raw)
        elif isinstance(raw, dict):
            result = NaradRouting.model_validate(raw)
        else:
            result = raw  # already a NaradRouting
        return {
            "routing": result,
            "raw": result.model_dump(),
            "latency_ms": latency_ms,
            "parse_error": None,
            "model": model_id,
        }
    except Exception as exc:
        return {
            "routing": None,
            "raw": None,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "parse_error": str(exc),
            "model": model_id,
        }


def run_local(
    prompts_path: str = "test_prompts.json",
    output_path: str = "results/local_results.json",
    model_id: str = DEFAULT_MODEL,
) -> list[dict]:
    with open(prompts_path) as f:
        data = json.load(f)

    results = []
    for i, prompt in enumerate(data["prompts"]):
        print(f"  [{i+1:02d}/50] {prompt['query'][:70]}...", end=" ", flush=True)

        result = route_with_local(prompt["query"], model_id=model_id)

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
                result["routing"].mode.value if result["routing"] else None
            ),
            "rationale": (
                result["routing"].rationale if result["routing"] else None
            ),
            "eval_criteria": (
                result["routing"].eval_criteria if result["routing"] else None
            ),
            "latency_ms": result["latency_ms"],
            "parse_error": result["parse_error"],
            "model": model_id,
        }

        from narad_claude import _score
        record["score"] = _score(record)
        print(f"{record['score']}  ({record['latency_ms']}ms)")

        results.append(record)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nSaved to {output_path}")
    return results
