#!/usr/bin/env python3
"""Exercise one real model turn inside a durable Narad workflow.

The default case stops before any confirmation-gated browser action. It is
safe to run against a developer server with synthetic inputs. The market scan
passes only if a live search really ran and Matsya reported the stage done
with its roles (report_stage_result); a confident reply alone does not.
"""

from __future__ import annotations

import argparse
import json
import time
from typing import Any

import requests


def _json(response: requests.Response) -> dict[str, Any]:
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Expected an object response")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--user-id", default=f"synthetic-chat-{int(time.time())}")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    started = _json(requests.post(
        f"{base}/workflows/career/runs",
        params={"user_id": args.user_id},
        json={
            "inputs": {
                "target_role": "Senior product manager",
                "locations": "Remote India",
                "experience": "Synthetic profile: eight years in B2B SaaS product management",
                "strengths": "Discovery, analytics, zero-to-one delivery",
                "constraints": "Remote only; do not submit applications",
                "weekly_scan": False,
                "timezone": "Asia/Kolkata",
            }
        },
        timeout=30,
    ))
    run_id = str(started["run"]["run_id"])
    session_id = f"{run_id}-chat"
    events: list[str] = []
    agents: list[str] = []
    errors: list[str] = []

    with requests.post(
        f"{base}/chat",
        json={
            "query": (
                "Research two current senior product manager opportunities suitable for this "
                "synthetic profile. Use live web evidence, cite sources, and do not apply or "
                "perform any external action."
            ),
            "user_id": args.user_id,
            "session_id": session_id,
            "workflow_run_id": run_id,
        },
        stream=True,
        timeout=(30, 300),
    ) as response:
        response.raise_for_status()
        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line or not raw_line.startswith("data:"):
                continue
            try:
                event = json.loads(raw_line[5:].strip())
            except json.JSONDecodeError:
                continue
            event_type = str(event.get("type") or "")
            if event_type:
                events.append(event_type)
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            if event_type == "step_event":
                print(
                    f"{data.get('avatar', 'agent')}: {data.get('kind', 'step')} "
                    f"{data.get('tool', '')}".rstrip(),
                    flush=True,
                )
            if event_type == "avatar_start" and data.get("avatar"):
                agents.append(str(data["avatar"]))
            if event_type == "error":
                errors.append(str(data.get("message") or "unknown stream error"))
            if event_type == "done":
                break

    run = _json(requests.get(
        f"{base}/workflow-runs/{run_id}",
        params={"user_id": args.user_id},
        timeout=30,
    ))
    current_stage = (run.get("current_stage") or {}).get("id")
    report = {
        "run_id": run_id,
        "session_id": session_id,
        "agents": list(dict.fromkeys(agents)),
        "events": list(dict.fromkeys(events)),
        "errors": errors,
        "workflow_status": run.get("status"),
        "current_stage": current_stage,
        "market_scan_status": next(
            (stage.get("status") for stage in run.get("stages", []) if stage.get("id") == "market_scan"),
            None,
        ),
        "citation_count": len(run.get("state", {}).get("citations", [])),
        # The stage advances only on evidence plus Matsya's report_stage_result;
        # when it did not, this says which part of the finish line is missing.
        "still_missing": (run.get("next_action") or {}).get("missing", []),
    }
    print(json.dumps(report, indent=2))

    required_events = {"narad_synthesis", "workflow_updated", "done"}
    ok = (
        not errors
        and required_events.issubset(events)
        and current_stage == "shortlist"
        and report["citation_count"] > 0
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
