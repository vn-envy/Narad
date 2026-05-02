"""
Narad local model evaluator — mlx-lm + outlines on Apple Silicon.

Uses outlines' grammar-constrained generation to enforce the NaradRouting
JSON schema at the token level. Invalid JSON is mathematically impossible
to emit regardless of model capability — so routing *accuracy* is the only
variable under test (no parse failures should appear in results).

Requires:
  pip install mlx-lm outlines

Model: google/gemma-3-4b-it (MLX quantised via mlx-community)
  mlx_lm.convert --hf-path google/gemma-3-4b-it -q --q-bits 4
  or pull directly: mlx_lm.generate --model mlx-community/gemma-3-4b-it-4bit
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from pydantic import ValidationError

from narad_schema import NaradRouting, NARAD_SYSTEM_PROMPT

# Model identifier — mlx-community hosts 4-bit quantised variants
DEFAULT_MODEL = "mlx-community/gemma-3-4b-it-4bit"


def _load_model(model_id: str) -> tuple[Any, Any]:
    """Lazily load the MLX model + tokenizer (cached after first call)."""
    try:
        from mlx_lm import load
    except ImportError as e:
        raise ImportError(
            "mlx-lm not installed. Run: pip install mlx-lm"
        ) from e

    print(f"  Loading model {model_id} (first run downloads ~2.5GB)...")
    model, tokenizer = load(model_id)
    print("  Model loaded.")
    return model, tokenizer


_model_cache: dict[str, tuple] = {}


def route_with_local(
    query: str,
    model_id: str = DEFAULT_MODEL,
    *,
    max_tokens: int = 512,
) -> dict[str, Any]:
    """
    Route a single query via the local MLX model with constrained generation.
    outlines enforces the NaradRouting JSON schema at token level.
    """
    try:
        import outlines
    except ImportError as e:
        raise ImportError(
            "outlines not installed. Run: pip install outlines"
        ) from e

    if model_id not in _model_cache:
        mlx_model, tokenizer = _load_model(model_id)
        # outlines 1.x API: from_mlxlm(model, tokenizer) — not om.mlxlm(model_id)
        from outlines.models.mlxlm import from_mlxlm
        model = from_mlxlm(mlx_model, tokenizer)
        _model_cache[model_id] = model

    model = _model_cache[model_id]

    # outlines JSON schema-constrained generator
    generator = outlines.generate.json(model, NaradRouting)

    prompt = f"{NARAD_SYSTEM_PROMPT}\n\nUser task: {query}\n\nJSON routing:"

    t0 = time.perf_counter()
    try:
        result: NaradRouting = generator(prompt, max_tokens=max_tokens)
        latency_ms = int((time.perf_counter() - t0) * 1000)

        return {
            "routing": result,
            "raw": result.model_dump(),
            "latency_ms": latency_ms,
            "parse_error": None,
            "model": model_id,
        }

    except (ValidationError, Exception) as exc:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "routing": None,
            "raw": None,
            "latency_ms": latency_ms,
            "parse_error": str(exc),
            "model": model_id,
        }


def run_local(
    prompts_path: str = "test_prompts.json",
    output_path: str = "results/local_results.json",
    model_id: str = DEFAULT_MODEL,
) -> list[dict]:
    """Run all 50 prompts through the local model and save raw results."""
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
