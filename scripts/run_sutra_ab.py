#!/usr/bin/env python3
"""
Sutra A/B eval (M4.3) — prove or kill the learning moat.

Runs the golden-task set (M4.2) twice through the real FastAPI app in one
process: arm OFF (NARAD_SUTRAS=off) then arm ON (sutras injected as normal),
and compares structural pass rate, latency, tokens, and cost. Learning is
frozen for both arms (NARAD_LEARNING_FREEZE=1) so a mid-run promotion can't
contaminate the comparison — and no Tapas judge calls inflate the bill.

The audit's core question (§6.1): sutras cost 2-3 judge LLM calls per avatar
run with unproven benefit. This script produces the evidence:

  python scripts/run_sutra_ab.py                    # full 48-task set, both arms
  python scripts/run_sutra_ab.py --avatar Krishna   # one avatar
  python scripts/run_sutra_ab.py --limit 8          # smoke
  python scripts/run_sutra_ab.py --force            # run even with 0 active sutras

If no sutras are active the comparison is vacuous — the runner says so and
exits unless --force. Reports land in ~/.narad/benchmarks/sutra-ab/.
Run it weekly; three flat or negative reports in a row is the "kill" signal.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop


def _load_golden():
    """Import the golden-task runner as a module (shared task loading + assertions)."""
    spec = importlib.util.spec_from_file_location(
        "golden_tasks_runner", ROOT / "scripts" / "run_golden_tasks.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_arm(client, gt, tasks: list[dict], arm: str) -> dict:
    """Run every task once under the current NARAD_SUTRAS setting."""
    results = []
    print(f"\n── arm: sutras {arm.upper()} " + "─" * 40)
    for idx, task in enumerate(tasks, start=1):
        strict = task.get("strict", True)
        t0 = time.monotonic()
        try:
            with client.stream(
                "POST",
                "/chat",
                json={"query": task["prompt"], "user_id": f"sutra-ab-{arm}-{task['id']}"},
            ) as response:
                payloads = gt._parse_sse_payloads(response)
            d = gt._digest(payloads)
            reasons = gt._check(task, d)
        except Exception as exc:
            d = {"cost_usd": 0.0, "total_tokens": 0}
            reasons = [f"transport error: {exc}"]
        elapsed = round(time.monotonic() - t0, 1)
        passed = not reasons
        mark = "PASS" if passed else ("FAIL" if strict else "WARN")
        print(f"[{idx:>2}/{len(tasks)}] {mark}  {task['id']:<28} {elapsed:>6.1f}s")
        results.append({
            "id": task["id"],
            "avatar": task["avatar"],
            "strict": strict,
            "passed": passed,
            "reasons": reasons,
            "elapsed_s": elapsed,
            "cost_usd": d.get("cost_usd", 0.0),
            "total_tokens": d.get("total_tokens", 0),
        })
    return _summarize_arm(results)


def _summarize_arm(results: list[dict]) -> dict:
    strict_results = [r for r in results if r["strict"]]
    by_avatar: dict[str, dict] = {}
    for r in strict_results:
        slot = by_avatar.setdefault(r["avatar"], {"passed": 0, "total": 0})
        slot["total"] += 1
        slot["passed"] += 1 if r["passed"] else 0
    return {
        "tasks": len(results),
        "strict_tasks": len(strict_results),
        "strict_passed": sum(1 for r in strict_results if r["passed"]),
        "pass_rate": round(
            sum(1 for r in strict_results if r["passed"]) / len(strict_results), 3
        ) if strict_results else 0.0,
        "mean_latency_s": round(
            sum(r["elapsed_s"] for r in results) / len(results), 1
        ) if results else 0.0,
        "total_tokens": sum(r["total_tokens"] for r in results),
        "total_cost_usd": round(sum(r["cost_usd"] for r in results), 6),
        "by_avatar": by_avatar,
        "results": results,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Sutras-on/off A/B over the golden-task set.")
    ap.add_argument("--avatar", help="run only this avatar's tasks")
    ap.add_argument("--limit", type=int, help="run only the first N selected tasks")
    ap.add_argument("--force", action="store_true", help="run even if no sutras are active")
    ap.add_argument("--report", help="report path (default: ~/.narad/benchmarks/sutra-ab/<ts>.json)")
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import narad_paths  # noqa: F401 — registers phase dirs; must precede phase imports

    # How many sutras would arm ON actually inject? Zero → vacuous comparison.
    from sutra_engine import get_all_sutras
    active_by_avatar: dict[str, int] = {}
    for s in get_all_sutras():
        if s.get("status") == "active":
            active_by_avatar[s.get("avatar", "?")] = active_by_avatar.get(s.get("avatar", "?"), 0) + 1
    total_active = sum(active_by_avatar.values())
    print(f"Active sutras: {total_active} {active_by_avatar or ''}")
    if total_active == 0 and not args.force:
        print("Nothing to A/B — no active sutras exist, both arms would be identical.")
        print("Let the system learn first, or pass --force to run anyway.")
        return 1

    gt = _load_golden()
    tasks = gt._load_tasks()
    if args.avatar:
        tasks = [t for t in tasks if t["avatar"].lower() == args.avatar.lower()]
    if args.limit:
        tasks = tasks[: args.limit]
    if not tasks:
        print("No tasks selected.")
        return 1

    # Freeze learning for both arms: no promotions, no judge calls, no sankalpa writes.
    os.environ["NARAD_LEARNING_FREEZE"] = "1"

    from fastapi.testclient import TestClient

    # isort: split
    import server  # noqa: E402

    run_ts = datetime.now().strftime("%Y-%m-%dT%H%M%S")
    arms: dict[str, dict] = {}
    with TestClient(server.app) as client:
        os.environ["NARAD_SUTRAS"] = "off"
        arms["off"] = _run_arm(client, gt, tasks, "off")
        os.environ["NARAD_SUTRAS"] = "on"
        arms["on"] = _run_arm(client, gt, tasks, "on")
    os.environ.pop("NARAD_LEARNING_FREEZE", None)
    os.environ.pop("NARAD_SUTRAS", None)

    delta_pass = arms["on"]["pass_rate"] - arms["off"]["pass_rate"]
    delta_cost = round(arms["on"]["total_cost_usd"] - arms["off"]["total_cost_usd"], 6)
    delta_latency = round(arms["on"]["mean_latency_s"] - arms["off"]["mean_latency_s"], 1)
    # Verdict heuristic: >2pp pass-rate swing on this set size is signal, less is noise.
    if delta_pass > 0.02:
        verdict = "sutras help"
    elif delta_pass < -0.02:
        verdict = "sutras hurt"
    else:
        verdict = "no measurable effect"

    report = {
        "ts": run_ts,
        "active_sutras": active_by_avatar,
        "verdict": verdict,
        "delta": {
            "pass_rate": round(delta_pass, 3),
            "cost_usd": delta_cost,
            "mean_latency_s": delta_latency,
        },
        "arms": arms,
    }

    from narad_config import BENCHMARK_DIR
    report_path = (
        Path(args.report) if args.report
        else BENCHMARK_DIR / "sutra-ab" / f"ab_{run_ts}.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n── A/B summary " + "─" * 47)
    for arm in ("off", "on"):
        a = arms[arm]
        print(
            f"sutras {arm:<3}  pass {a['strict_passed']}/{a['strict_tasks']}"
            f" ({a['pass_rate']:.1%}) · {a['mean_latency_s']}s avg"
            f" · {a['total_tokens']} tok · ${a['total_cost_usd']}"
        )
    print(
        f"delta       pass {delta_pass:+.1%} · latency {delta_latency:+}s"
        f" · cost ${delta_cost:+} → {verdict}"
    )
    print(f"Report: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
