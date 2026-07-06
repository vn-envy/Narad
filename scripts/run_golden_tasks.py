#!/usr/bin/env python3
"""
Golden-task CI runner (M4.2) — nightly structural evals for the four avatars.

Runs the canonical task set (evals/golden_tasks.json) through the real FastAPI
app via TestClient, parses the SSE stream, and asserts on STRUCTURE only:

  * the expected avatar started and finished (routing accuracy)
  * at least one expected tool was invoked (tools_any)
  * no forbidden tool fired (forbid_tools — e.g. send_email on a preview task)
  * synthesis is non-trivial (min_synthesis_chars) and, where specified,
    matches a shape regex (synthesis_regex) — never exact text
  * the stream ended cleanly (done event, no error events)

Each run writes a JSON report to ~/.narad/benchmarks/golden/ and prints a
summary. Exit code 1 on any strict failure — cron/CI friendly:

  python scripts/run_golden_tasks.py                 # full set (live LLM calls)
  python scripts/run_golden_tasks.py --avatar Rama   # one avatar
  python scripts/run_golden_tasks.py --task rama-02-budget
  python scripts/run_golden_tasks.py --list          # show tasks, no calls
  python scripts/run_golden_tasks.py --limit 4       # smoke subset

Tasks marked "strict": false are routing-ambiguous probes: their failures are
reported as warnings and don't affect the exit code. Cost per run lands in the
cost ledger automatically (M4.1) and is echoed in the summary.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop

TASKS_PATH = ROOT / "evals" / "golden_tasks.json"


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _fixture_paths() -> tuple[str, str]:
    """Create the golden fixture file; return (file_path, dir_path)."""
    fixture_dir = Path.home() / ".narad" / "golden-fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    fixture = fixture_dir / "fixture.txt"
    fixture.write_text(
        "Narad golden-task fixture\n"
        "This file exists so structural evals can exercise document and file tools.\n"
        "The canonical agents are Matsya, Rama, Krishna, and Parashurama.\n"
    )
    return str(fixture), str(fixture_dir)


def _load_tasks() -> list[dict]:
    spec = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    fixture, fixture_dir = _fixture_paths()
    tasks = []
    for task in spec["tasks"]:
        task = dict(task)
        task["prompt"] = (
            task["prompt"]
            .replace("{fixture_dir}", fixture_dir)
            .replace("{fixture}", fixture)
            .replace("{root}", str(ROOT))
        )
        tasks.append(task)
    return tasks


# ── SSE parsing ───────────────────────────────────────────────────────────────

def _parse_sse_payloads(response) -> list[dict]:
    payloads: list[dict] = []
    for line in response.iter_lines():
        if not line:
            continue
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        if not line.startswith("data: "):
            continue
        try:
            payloads.append(json.loads(line[6:]))
        except json.JSONDecodeError:
            continue
    return payloads


def _digest(payloads: list[dict]) -> dict:
    """Reduce an SSE stream to the structural facts the assertions need."""
    d = {
        "avatars_started": set(),
        "avatars_done": set(),
        "tools_called": set(),
        "synthesis": "",
        "errors": [],
        "done": False,
        "cost_usd": 0.0,
        "total_tokens": 0,
    }
    for p in payloads:
        t = p.get("type")
        data = p.get("data", {}) or {}
        if t == "avatar_start":
            d["avatars_started"].add(data.get("avatar", ""))
        elif t == "avatar_done":
            d["avatars_done"].add(data.get("avatar", ""))
        elif t == "step_event" and data.get("kind") == "tool_call":
            d["tools_called"].add(data.get("tool", ""))
        elif t == "narad_synthesis":
            d["synthesis"] += str(data.get("text", ""))
        elif t == "error":
            d["errors"].append(str(data.get("message", "unknown")))
        elif t == "done":
            d["done"] = True
        elif t == "usage":
            d["cost_usd"] += float(data.get("cost_usd", 0.0) or 0.0)
            d["total_tokens"] += int(data.get("total_tokens", 0) or 0)
    return d


# ── Assertions ────────────────────────────────────────────────────────────────

def _check(task: dict, d: dict) -> list[str]:
    """Return list of structural failure reasons (empty = pass)."""
    reasons: list[str] = []
    expect = task.get("expect", {}) or {}
    avatar = task["avatar"]

    if d["errors"]:
        reasons.append(f"SSE error: {d['errors'][0][:120]}")
    if not d["done"]:
        reasons.append("stream never emitted done")
    if avatar not in d["avatars_started"]:
        got = ", ".join(sorted(a for a in d["avatars_started"] if a)) or "none"
        reasons.append(f"routing: expected {avatar}, started: {got}")
    elif avatar not in d["avatars_done"]:
        reasons.append(f"{avatar} started but never finished")

    tools_any = expect.get("tools_any") or []
    if tools_any and not (set(tools_any) & d["tools_called"]):
        called = ", ".join(sorted(d["tools_called"])) or "none"
        reasons.append(f"tools: expected one of {tools_any}, called: {called}")

    for tool in expect.get("forbid_tools") or []:
        if tool in d["tools_called"]:
            reasons.append(f"forbidden tool fired: {tool}")

    min_chars = int(expect.get("min_synthesis_chars", 20))
    text = d["synthesis"].strip()
    if len(text) < min_chars:
        reasons.append(f"synthesis too short: {len(text)} < {min_chars} chars")

    pattern = expect.get("synthesis_regex")
    if pattern and text and not re.search(pattern, text, re.IGNORECASE):
        reasons.append(f"synthesis shape: no match for /{pattern}/")

    return reasons


# ── Runner ────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Run the golden-task structural evals.")
    ap.add_argument("--avatar", help="run only this avatar's tasks (Matsya/Rama/Krishna/Parashurama)")
    ap.add_argument("--task", help="run a single task by id")
    ap.add_argument("--limit", type=int, help="run only the first N selected tasks")
    ap.add_argument("--list", action="store_true", help="list selected tasks and exit (no LLM calls)")
    ap.add_argument("--report", help="report path (default: ~/.narad/benchmarks/golden/<ts>.json)")
    args = ap.parse_args()

    tasks = _load_tasks()
    if args.avatar:
        tasks = [t for t in tasks if t["avatar"].lower() == args.avatar.lower()]
    if args.task:
        tasks = [t for t in tasks if t["id"] == args.task]
    if args.limit:
        tasks = tasks[: args.limit]
    if not tasks:
        print("No tasks selected.")
        return 1

    if args.list:
        for t in tasks:
            strict = "" if t.get("strict", True) else "  [non-strict]"
            print(f"{t['id']:<28} {t['avatar']:<12}{strict}")
        print(f"\n{len(tasks)} task(s).")
        return 0

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import narad_paths  # noqa: F401 — registers phase dirs; must precede phase imports
    from fastapi.testclient import TestClient

    # isort: split
    import server  # noqa: E402

    run_ts = datetime.now().strftime("%Y-%m-%dT%H%M%S")
    results: list[dict] = []
    strict_failures = 0
    warnings = 0
    total_cost = 0.0

    print(f"Golden-task run — {len(tasks)} task(s)\n")
    with TestClient(server.app) as client:
        for idx, task in enumerate(tasks, start=1):
            strict = task.get("strict", True)
            t0 = time.monotonic()
            try:
                with client.stream(
                    "POST",
                    "/chat",
                    json={"query": task["prompt"], "user_id": f"golden-{task['id']}"},
                ) as response:
                    payloads = _parse_sse_payloads(response)
                d = _digest(payloads)
                reasons = _check(task, d)
            except Exception as exc:
                d = {"cost_usd": 0.0, "total_tokens": 0, "tools_called": set(), "synthesis": ""}
                reasons = [f"transport error: {exc}"]
            elapsed = round(time.monotonic() - t0, 1)
            total_cost += d.get("cost_usd", 0.0)

            passed = not reasons
            if not passed:
                if strict:
                    strict_failures += 1
                else:
                    warnings += 1
            mark = "PASS" if passed else ("FAIL" if strict else "WARN")
            print(f"[{idx:>2}/{len(tasks)}] {mark}  {task['id']:<28} {elapsed:>6.1f}s")
            for reason in reasons:
                print(f"          - {reason}")

            results.append({
                "id": task["id"],
                "avatar": task["avatar"],
                "strict": strict,
                "passed": passed,
                "reasons": reasons,
                "elapsed_s": elapsed,
                "tools_called": sorted(d.get("tools_called", set())),
                "synthesis_chars": len(d.get("synthesis", "").strip()),
                "cost_usd": d.get("cost_usd", 0.0),
                "total_tokens": d.get("total_tokens", 0),
            })

    passed_n = sum(1 for r in results if r["passed"])
    summary = {
        "ts": run_ts,
        "tasks": len(results),
        "passed": passed_n,
        "strict_failures": strict_failures,
        "warnings": warnings,
        "pass_rate": round(passed_n / len(results), 3),
        "total_cost_usd": round(total_cost, 6),
    }

    from narad_config import BENCHMARK_DIR
    report_path = (
        Path(args.report) if args.report
        else BENCHMARK_DIR / "golden" / f"golden_{run_ts}.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps({"summary": summary, "results": results}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(
        f"\n{passed_n}/{len(results)} passed"
        f" · {strict_failures} strict failure(s) · {warnings} warning(s)"
        f" · ${summary['total_cost_usd']} spent"
    )
    print(f"Report: {report_path}")
    return 1 if strict_failures else 0


if __name__ == "__main__":
    sys.exit(main())
