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
import contextlib
import json
import re
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

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


# ── Structural guru checks (G7) — offline, in-process, no LLM ─────────────────

@contextlib.contextmanager
def _guru_sandbox():
    """Temp LEARNING_DIR patched into guru_engine + learning_workspace; llm_json
    forced to raise so every path under test is the deterministic offline one."""
    import guru_engine
    import learning_workspace

    def _no_llm(*_a, **_k):
        raise RuntimeError("structural checks run offline")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with patch.object(learning_workspace, "LEARNING_DIR", root), \
             patch.object(guru_engine, "LEARNING_DIR", root), \
             patch.object(guru_engine, "llm_json", _no_llm):
            yield root


def _fixture_syllabus(workspace_id: str) -> dict:
    """Two-atom syllabus (dot-product → softmax) for state/packet checks."""
    def atom(atom_id: str, name: str, prereqs: list[str], q: str, good: str) -> dict:
        return {
            "id": atom_id, "name": name, "prerequisites": prereqs,
            "eli5": f"A playground picture of {name}.",
            "plain": f"{name} in plain English.",
            "precise": f"A precise paragraph about {name}.",
            "formal": f"The formal definition of {name}.",
            "misconception": f"A common mix-up about {name}.",
            "check": {"q": q, "good_answer": good},
        }
    return {
        "workspace_id": workspace_id,
        "topic": "dot product",
        "generator": "fixture",
        "atoms": [
            atom("dot-product", "Dot product", [],
                 "What does a large dot product mean?", "high similarity between vectors"),
            atom("softmax", "Softmax", ["dot-product"],
                 "Why do softmax outputs sum to one?", "normalization into weights"),
        ],
    }


def _guru_workspace(root: Path, topic: str) -> str:
    import learning_workspace
    workspace = learning_workspace.ensure_workspace(
        user_id="golden", topic=topic, mission=f"Golden structural check: {topic}.", session_id="golden",
    )
    return str(workspace["workspace_id"])


def _check_syllabus_schema() -> list[str]:
    import guru_engine
    with _guru_sandbox() as root:
        workspace_id = _guru_workspace(root, "binary search")
        syllabus = guru_engine.generate_syllabus(
            user_id="golden", workspace_id=workspace_id, topic="binary search",
        )
        reasons = list(guru_engine._validate_syllabus(syllabus))
        reasons += [f"missing field: {f}" for f in ("workspace_id", "topic", "generator", "atoms") if f not in syllabus]
        if syllabus.get("generator") not in ("template", "taxonomy"):
            reasons.append(f"offline path expected template/taxonomy generator, got {syllabus.get('generator')!r}")
        return reasons


def _check_syllabus_acyclic() -> list[str]:
    import guru_engine
    reasons: list[str] = []
    # the guard itself: a synthetic 2-cycle must be flagged
    cyclic = _fixture_syllabus("cycle-probe")
    cyclic["atoms"][0]["prerequisites"] = ["softmax"]
    if not any("cycle" in p for p in guru_engine._validate_syllabus(cyclic)):
        reasons.append("validator failed to flag a 2-cycle")
    with _guru_sandbox() as root:
        workspace_id = _guru_workspace(root, "sorting algorithms")
        syllabus = guru_engine.generate_syllabus(
            user_id="golden", workspace_id=workspace_id, topic="sorting algorithms",
        )
        reasons += [
            p for p in guru_engine._validate_syllabus(syllabus)
            if "cycle" in p or "unknown prerequisite" in p
        ]
    return reasons


def _check_atom_rungs() -> list[str]:
    import guru_engine
    with _guru_sandbox() as root:
        workspace_id = _guru_workspace(root, "recursion")
        syllabus = guru_engine.generate_syllabus(
            user_id="golden", workspace_id=workspace_id, topic="recursion",
        )
        reasons: list[str] = []
        atoms = syllabus.get("atoms") or []
        if not atoms:
            return ["no atoms generated"]
        for atom in atoms:
            for rung in guru_engine._RUNGS:
                if not str(atom.get(rung, "")).strip():
                    reasons.append(f"{atom.get('id')}: empty rung {rung}")
        return reasons


def _check_artifact_add_remove() -> list[str]:
    import learning_workspace as lw
    with _guru_sandbox() as root:
        workspace_id = _guru_workspace(root, "quicksort")
        artifact = lw.create_learning_artifact(
            user_id="golden", workspace_id=workspace_id, topic="quicksort", artifact_type="flashcards",
        )
        cards = list((artifact.get("doc") or {}).get("cards") or [])
        if not cards:
            return ["seed flashcards doc has no cards"]
        reasons: list[str] = []
        added = lw.update_learning_artifact(
            user_id="golden", artifact_id=artifact["artifact_id"], workspace_id=workspace_id,
            instruction="add a card about pivot selection",
        )
        cards_after_add = list((added.get("doc") or {}).get("cards") or [])
        if len(cards_after_add) != len(cards) + 1:
            reasons.append(f"add: expected {len(cards) + 1} cards, got {len(cards_after_add)}")
        if not any("pivot selection" in json.dumps(c).lower() for c in cards_after_add):
            reasons.append("add: no card mentions 'pivot selection'")
        removed = lw.update_learning_artifact(
            user_id="golden", artifact_id=artifact["artifact_id"], workspace_id=workspace_id,
            instruction="remove the card about pivot selection",
        )
        cards_after_remove = list((removed.get("doc") or {}).get("cards") or [])
        if any("pivot selection" in json.dumps(c).lower() for c in cards_after_remove):
            reasons.append("remove: 'pivot selection' card still present")
        if len(cards_after_remove) >= len(cards_after_add):
            reasons.append(f"remove: card count did not drop ({len(cards_after_add)} → {len(cards_after_remove)})")
        return reasons


def _check_grading_mutates_state() -> list[str]:
    import guru_engine
    with _guru_sandbox() as root:
        workspace_id = _guru_workspace(root, "dot product")
        syllabus_path = root / "golden" / workspace_id / "syllabus.json"
        syllabus_path.write_text(json.dumps(_fixture_syllabus(workspace_id)), encoding="utf-8")
        reasons: list[str] = []
        if guru_engine.load_learner_state(user_id="golden", workspace_id=workspace_id):
            reasons.append("learner state not empty before grading")
        grade = guru_engine.grade_check_answer(
            user_id="golden", workspace_id=workspace_id, atom_id="dot-product",
            answer="the vectors have high similarity with each other",
        )
        if grade.get("grader") != "heuristic":
            reasons.append(f"offline path expected heuristic grader, got {grade.get('grader')!r}")
        if not grade.get("correct"):
            reasons.append("heuristic grader rejected a keyword-matching answer")
        entry = guru_engine.load_learner_state(user_id="golden", workspace_id=workspace_id).get("dot-product") or {}
        if int(entry.get("attempts", 0)) != 1:
            reasons.append(f"attempts != 1 after one grade: {entry.get('attempts')}")
        if entry.get("status") in (None, "", "untaught"):
            reasons.append(f"status not advanced: {entry.get('status')!r}")
        if not entry.get("next_review"):
            reasons.append("next_review not scheduled")
        guru_engine.grade_check_answer(
            user_id="golden", workspace_id=workspace_id, atom_id="dot-product", answer="no idea",
        )
        entry2 = guru_engine.load_learner_state(user_id="golden", workspace_id=workspace_id).get("dot-product") or {}
        if int(entry2.get("attempts", 0)) != 2:
            reasons.append(f"attempts did not increment on second grade: {entry2.get('attempts')}")
        if entry2.get("status") != "shaky":
            reasons.append(f"wrong answer should set status shaky, got {entry2.get('status')!r}")
        return reasons


def _check_packet_single_question() -> list[str]:
    import learning_workspace as lw
    with _guru_sandbox() as root:
        workspace_id = _guru_workspace(root, "dot product")
        syllabus_path = root / "golden" / workspace_id / "syllabus.json"
        syllabus_path.write_text(json.dumps(_fixture_syllabus(workspace_id)), encoding="utf-8")
        packet = lw.build_workspace_packet(user_id="golden", workspace_id=workspace_id)
        reasons: list[str] = []
        question_lines = packet.count("Check question to ask:")
        if question_lines != 1:
            reasons.append(f"expected exactly one check question in packet, found {question_lines}")
        if "CURRENT TEACHING ATOM: Dot product [dot-product]" not in packet:
            reasons.append("packet missing frontier atom (expected Dot product first)")
        if "SYLLABUS PROGRESS:" not in packet:
            reasons.append("packet missing syllabus progress line")
        return reasons


_STRUCTURAL_CHECKS = {
    "syllabus_schema_valid": _check_syllabus_schema,
    "syllabus_acyclic": _check_syllabus_acyclic,
    "atom_rungs_nonempty": _check_atom_rungs,
    "artifact_add_remove": _check_artifact_add_remove,
    "grading_mutates_state": _check_grading_mutates_state,
    "packet_single_check_question": _check_packet_single_question,
}


def _run_structural(task: dict) -> list[str]:
    name = str(task.get("check", ""))
    fn = _STRUCTURAL_CHECKS.get(name)
    if fn is None:
        return [f"unknown structural check: {name!r}"]
    return fn()


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
            mode = "  [structural]" if t.get("mode") == "structural" else ""
            print(f"{t['id']:<28} {t['avatar']:<12}{strict}{mode}")
        print(f"\n{len(tasks)} task(s).")
        return 0

    live_tasks = [t for t in tasks if t.get("mode") != "structural"]
    client = None
    if live_tasks:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        import narad_paths  # noqa: F401 — registers phase dirs; must precede phase imports
        from fastapi.testclient import TestClient

        # isort: split
        import server  # noqa: E402
        client_cm = TestClient(server.app)
    else:
        client_cm = contextlib.nullcontext()

    run_ts = datetime.now().strftime("%Y-%m-%dT%H%M%S")
    results: list[dict] = []
    strict_failures = 0
    warnings = 0
    total_cost = 0.0

    print(f"Golden-task run — {len(tasks)} task(s)\n")
    with client_cm as client:
        for idx, task in enumerate(tasks, start=1):
            strict = task.get("strict", True)
            structural = task.get("mode") == "structural"
            t0 = time.monotonic()
            try:
                if structural:
                    d = {"cost_usd": 0.0, "total_tokens": 0, "tools_called": set(), "synthesis": ""}
                    reasons = _run_structural(task)
                else:
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
                reasons = [f"{'check crashed' if structural else 'transport error'}: {exc}"]
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
