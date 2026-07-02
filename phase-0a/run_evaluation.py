"""
Phase 0a evaluation harness.

Usage:
  python run_evaluation.py --model claude       Run Claude Sonnet baseline
  python run_evaluation.py --model local        Run local Gemma 4B model
  python run_evaluation.py --compare            Compare saved results
  python run_evaluation.py --model all          Run both, then compare
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from typing import Any


# ── Scoring ────────────────────────────────────────────────────────────────

SCORE_VALUES = {
    "exact_match": 1.0,
    "partial_match": 0.5,
    "miss": 0.0,
    "parse_error": 0.0,
}

PASS_THRESHOLD = 0.80      # 80% weighted accuracy = pass for local model
BASELINE_THRESHOLD = 0.95  # 95% for Claude baseline


def weighted_accuracy(results: list[dict]) -> float:
    """Weighted accuracy: exact=1.0, partial=0.5, miss/parse_error=0.0"""
    if not results:
        return 0.0
    total = sum(SCORE_VALUES[r["score"]] for r in results)
    return total / len(results)


def exact_accuracy(results: list[dict]) -> float:
    """Strict accuracy: only exact_match counts."""
    if not results:
        return 0.0
    return sum(1 for r in results if r["score"] == "exact_match") / len(results)


def print_report(results: list[dict], label: str) -> None:
    counts = Counter(r["score"] for r in results)
    w_acc = weighted_accuracy(results)
    e_acc = exact_accuracy(results)
    avg_latency = sum(r["latency_ms"] for r in results) / len(results)

    model_name = results[0]["model"] if results else "unknown"

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  Model: {model_name}")
    print(f"{'='*60}")
    print(f"  Total prompts   : {len(results)}")
    print(f"  Exact matches   : {counts['exact_match']:3d}  ({100*counts['exact_match']/len(results):.1f}%)")
    print(f"  Partial matches : {counts['partial_match']:3d}  ({100*counts['partial_match']/len(results):.1f}%)")
    print(f"  Misses          : {counts['miss']:3d}  ({100*counts['miss']/len(results):.1f}%)")
    print(f"  Parse errors    : {counts['parse_error']:3d}  ({100*counts['parse_error']/len(results):.1f}%)")
    print(f"  ─────────────────────────────────")
    print(f"  Weighted acc    : {w_acc*100:.1f}%  (exact=1.0, partial=0.5)")
    print(f"  Exact acc       : {e_acc*100:.1f}%")
    print(f"  Avg latency     : {avg_latency:.0f} ms")

    # Per-category breakdown
    categories = sorted(set(r["category"] for r in results))
    print(f"\n  Per-category weighted accuracy:")
    for cat in categories:
        cat_results = [r for r in results if r["category"] == cat]
        cat_acc = weighted_accuracy(cat_results)
        bar = "█" * int(cat_acc * 20)
        print(f"    {cat:<30} {cat_acc*100:5.1f}%  {bar}")

    # Go/no-go decision
    threshold = BASELINE_THRESHOLD if "claude" in label.lower() else PASS_THRESHOLD
    verdict = "✅ PASS" if w_acc >= threshold else "❌ FAIL"
    print(f"\n  Threshold: {threshold*100:.0f}%   →   {verdict}")

    if w_acc < 0.70 and "local" in label.lower():
        print("\n  ⚠️  Weighted accuracy < 70%. Escalate to Gemma 27B MoE or")
        print("     fall back to cloud-default with local-optional in Phase 1.")

    # Misses and partials for investigation
    failures = [r for r in results if r["score"] in ("miss", "partial_match", "parse_error")]
    if failures:
        print(f"\n  Failures to investigate ({len(failures)}):")
        for r in failures:
            print(f"    [{r['id']:02d}] {r['score'].upper()}")
            print(f"         Query   : {r['query'][:80]}")
            print(f"         Expected: {r['expected_avatars']}")
            print(f"         Got     : {r['actual_avatars']}")
            if r["parse_error"]:
                print(f"         Error   : {r['parse_error'][:100]}")


def compare_results(
    local_path: str = "results/local_results.json",
) -> None:
    """Side-by-side comparison of baseline vs DeepSeek and/or local model."""
    def load(path):
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return json.load(f)

    # Accept whichever baseline was run
    baseline_results = load("results/gpt_results.json") or load("results/claude_results.json")
    baseline_label = "GPT-4o BASELINE" if os.path.exists("results/gpt_results.json") else "CLAUDE BASELINE"
    deepseek_results = load("results/deepseek_results.json")
    local_results = load(local_path)

    if baseline_results:
        print_report(baseline_results, baseline_label)
    else:
        print("  No baseline results found. Run: python run_evaluation.py --model gpt")

    if deepseek_results:
        print_report(deepseek_results, "DEEPSEEK V4 PRO")

    if local_results:
        print_report(local_results, "LOCAL MODEL (Gemma)")

    # DeepSeek vs baseline comparison
    if baseline_results and deepseek_results:
        baseline_acc = weighted_accuracy(baseline_results)
        ds_acc = weighted_accuracy(deepseek_results)
        gap = baseline_acc - ds_acc
        print(f"\n{'='*60}")
        print(f"  DeepSeek vs {baseline_label}")
        print(f"  Accuracy gap (Baseline - DeepSeek): {gap*100:.1f} pp")
        if gap <= 0.05:
            print("  ✅ Gap ≤ 5pp — DeepSeek matches baseline. Switch recommended.")
        elif gap <= 0.15:
            print("  ⚠️  Gap 5–15pp — DeepSeek is close. Review failures before switching.")
        else:
            print("  ❌ Gap > 15pp — Keep GPT-4o as Narad router.")

        baseline_by_id = {r["id"]: r for r in baseline_results}
        regressions = [
            r for r in deepseek_results
            if r["score"] in ("miss", "parse_error")
            and baseline_by_id.get(r["id"], {}).get("score") == "exact_match"
        ]
        if regressions:
            print(f"\n  DeepSeek regressions vs baseline ({len(regressions)} prompts):")
            for r in regressions:
                print(f"    [{r['id']:02d}] {r['query'][:70]}")

    # Local vs baseline comparison
    if baseline_results and local_results:
        baseline_acc = weighted_accuracy(baseline_results)
        local_acc = weighted_accuracy(local_results)
        gap = baseline_acc - local_acc
        print(f"\n{'='*60}")
        print(f"  Accuracy gap (Baseline - Local): {gap*100:.1f} pp")
        if gap <= 0.15:
            print("  ✅ Gap ≤ 15pp — local model is competitive.")
        else:
            print("  ⚠️  Gap > 15pp — consider upgrading local model.")

        baseline_by_id = {r["id"]: r for r in baseline_results}
        regressions = [
            r for r in local_results
            if r["score"] in ("miss", "parse_error")
            and baseline_by_id.get(r["id"], {}).get("score") == "exact_match"
        ]
        if regressions:
            print(f"\n  Local regressions vs baseline ({len(regressions)} prompts):")
            for r in regressions:
                print(f"    [{r['id']:02d}] {r['query'][:70]}")


# ── CLI ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 0a: Narad routing accuracy evaluation"
    )
    parser.add_argument(
        "--model",
        choices=["claude", "gpt", "deepseek", "local", "all"],
        help="Which model to evaluate (claude/gpt as baseline, deepseek for V4 Pro, local for Gemma)",
    )
    parser.add_argument("--compare", action="store_true", help="Compare saved results")
    parser.add_argument(
        "--prompts", default="test_prompts.json", help="Path to test prompts JSON"
    )
    parser.add_argument(
        "--local-model",
        default="mlx-community/gemma-3-4b-it-4bit",
        help="HuggingFace model ID for local evaluation",
    )
    args = parser.parse_args()

    if not args.model and not args.compare:
        parser.print_help()
        sys.exit(1)

    os.makedirs("results", exist_ok=True)

    if args.compare:
        compare_results()
        return

    if args.model in ("claude", "all"):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("ERROR: ANTHROPIC_API_KEY environment variable not set.")
            sys.exit(1)
        print("\nRunning Claude Sonnet baseline...")
        from narad_claude import run_baseline
        results = run_baseline(args.prompts)
        print_report(results, "CLAUDE BASELINE")

    if args.model in ("gpt", "all"):
        if not os.environ.get("OPENAI_API_KEY"):
            print("ERROR: OPENAI_API_KEY environment variable not set.")
            sys.exit(1)
        print("\nRunning GPT-4o baseline...")
        from narad_openai import run_openai_baseline
        results = run_openai_baseline(args.prompts)
        print_report(results, "GPT-4o BASELINE")

    if args.model in ("deepseek", "all"):
        if not os.environ.get("DEEPSEEK_API_KEY"):
            print("ERROR: DEEPSEEK_API_KEY environment variable not set.")
            sys.exit(1)
        print("\nRunning DeepSeek V4 Pro eval...")
        from narad_deepseek import run_deepseek_eval
        results = run_deepseek_eval(args.prompts)
        print_report(results, "DEEPSEEK V4 PRO")

    if args.model in ("local", "all"):
        print(f"\nRunning local model ({args.local_model})...")
        from narad_local import run_local
        results = run_local(args.prompts, model_id=args.local_model)
        print_report(results, f"LOCAL MODEL ({args.local_model})")

    if args.model == "all":
        compare_results()


if __name__ == "__main__":
    main()
