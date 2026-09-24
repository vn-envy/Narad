#!/usr/bin/env python3
"""Pariksha for Kriya: run the browser fixture tasks and print a scorecard.

Serves the local fixture sites (evals/pariksha/browser_fixtures.py) on
loopback, runs each task through the real Kriya loop in real Chromium, plays
the person (approves or rejects each proposal the fixture expects, signs in
through takeover) and scores every task by the site's server-side oracle.

    .venv/bin/python scripts/pariksha_browser.py                  # the operator model
    .venv/bin/python scripts/pariksha_browser.py --model <model id>
    .venv/bin/python scripts/pariksha_browser.py --operator scripted   # no model, no keys
    .venv/bin/python scripts/pariksha_browser.py --tasks flight,feed --json out.json

With the model operator, calls go through the privacy gateway as in the app
and are logged in the egress ledger of the "pariksha" profile; nothing is
stored in a family member's profile. The scripted operator runs in a
temporary NARAD_HOME. Columns: success (oracle), steps, operator calls, time
per step, tokens in/out, the largest observation, approvals asked/needed,
help asked, retries.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_fixtures():
    spec = importlib.util.spec_from_file_location(
        "pariksha_browser_fixtures", ROOT / "evals" / "pariksha" / "browser_fixtures.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--operator", choices=["model", "scripted"], default="model")
    parser.add_argument("--model", default="", help="operator model (default: NARAD_OPERATOR_MODEL or Matsya's)")
    parser.add_argument("--tasks", default="", help="comma-separated fixture names (default: all)")
    parser.add_argument("--profile", default="pariksha")
    parser.add_argument("--timeout", type=float, default=240.0, help="seconds per task")
    parser.add_argument("--json", default="", help="also write the rows to this file")
    args = parser.parse_args()

    if args.operator == "scripted" and "NARAD_HOME" not in os.environ:
        os.environ["NARAD_HOME"] = tempfile.mkdtemp(prefix="pariksha-home-")
    os.environ["NARAD_BROWSER_PRIVATE_HOSTS"] = ",".join(
        filter(None, [os.environ.get("NARAD_BROWSER_PRIVATE_HOSTS", ""), "127.0.0.1", "localhost"])
    )
    sys.path.insert(0, str(ROOT))
    import narad_paths  # noqa: F401

    fixtures = _load_fixtures()
    chromium = fixtures.find_chromium()
    if chromium is None:
        print("No Chromium found: install it with `python -m playwright install chromium` "
              "or set NARAD_CHROMIUM_EXECUTABLE.")
        return 2
    if chromium:
        os.environ["NARAD_CHROMIUM_EXECUTABLE"] = chromium

    if args.operator == "model":
        try:
            from dotenv import load_dotenv

            load_dotenv(ROOT / ".env")
            from kunji import apply_keys_to_env

            apply_keys_to_env()
        except Exception:
            pass
        if args.model:
            os.environ["NARAD_OPERATOR_MODEL"] = args.model

    from kriya.operator import ModelOperator, ScriptedOperator
    from kriya.runtime import TaskRuntime, set_runtime

    from profile_context import profile_scope

    wanted = {item.strip() for item in args.tasks.split(",") if item.strip()}
    selected = [task for task in fixtures.TASKS if not wanted or task.name in wanted]
    rows = []
    site = fixtures.FixtureSite().start()
    try:
        for fixture in selected:
            if args.operator == "scripted":
                factory = lambda task, script=fixture.script: ScriptedOperator(script)  # noqa: E731
            else:
                factory = lambda task: ModelOperator()  # noqa: E731
            runtime = TaskRuntime(operator_factory=factory, poll_s=0.1)
            set_runtime(runtime)
            with profile_scope(args.profile):
                try:
                    row = fixtures.run_fixture_task(
                        runtime, site, fixture, profile_id=args.profile, timeout_s=args.timeout
                    )
                except Exception as exc:
                    row = {"task": fixture.name, "success": False, "status": f"error: {exc}"[:80]}
            rows.append(row)
            print(_row_line(row), flush=True)
    finally:
        site.stop()
        from computer_use_skill import shutdown_computer_use

        shutdown_computer_use()

    _print_scorecard(rows, args.operator)
    output = Path(args.json) if args.json else (
        Path(os.environ.get("NARAD_HOME", Path.home() / ".narad")) / "benchmarks"
        / f"pariksha_browser_{time.strftime('%Y%m%d-%H%M%S')}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"operator": args.operator, "rows": rows}, indent=2, ensure_ascii=False))
    print(f"\nRows written to {output}")
    return 0 if all(row.get("success") for row in rows) else 1


def _row_line(row: dict) -> str:
    return (
        f"{row.get('task', '?'):<16} {'PASS' if row.get('success') else 'FAIL':<5} {row.get('status', ''):<10} "
        f"steps={row.get('steps', 0):<3} calls={row.get('operator_calls', 0):<3} "
        f"s/step={row.get('seconds_per_step', 0):<5} tok_in={row.get('prompt_tokens', 0):<6} "
        f"tok_out={row.get('completion_tokens', 0):<5} obs_max={row.get('max_observation_tokens', 0)}tok "
        f"approvals={row.get('approvals_asked', 0)}/{row.get('approvals_needed', 0)} "
        f"help={row.get('help_asked', 0)} retries={row.get('retries', 0)}"
    )


def _print_scorecard(rows: list[dict], operator: str) -> None:
    passed = sum(1 for row in rows if row.get("success"))
    steps = [row.get("steps", 0) for row in rows if row.get("steps")]
    per_step = sorted(row.get("seconds_per_step", 0) for row in rows if row.get("steps"))
    asked = sum(row.get("approvals_asked", 0) for row in rows)
    needed = sum(row.get("approvals_needed", 0) for row in rows)
    print(f"\nPariksha browser scorecard ({operator} operator)")
    print(f"  success        {passed}/{len(rows)}")
    if steps:
        print(f"  steps          {sum(steps)} total, {sum(steps) / len(steps):.1f} per task")
        print(f"  time per step  p50 {per_step[len(per_step) // 2]:.2f} s, max {per_step[-1]:.2f} s")
    print(f"  tokens         {sum(row.get('prompt_tokens', 0) for row in rows)} in, "
          f"{sum(row.get('completion_tokens', 0) for row in rows)} out")
    print(f"  observation    largest {max((row.get('max_observation_tokens', 0) for row in rows), default=0)} "
          "tokens (budget 2000)")
    print(f"  approvals      {asked} asked, {needed} needed")


if __name__ == "__main__":
    sys.exit(main())
