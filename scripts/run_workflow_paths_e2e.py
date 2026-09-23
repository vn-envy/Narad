#!/usr/bin/env python3
"""Live HTTP smoke test for Narad's six durable Workflow Paths.

This validates orchestration state only. It never executes a browser submission,
booking, calendar write, payment, or other external side effect.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from typing import Any

import requests


@dataclass(frozen=True)
class Case:
    workflow_id: str
    persona: str
    inputs: dict[str, Any]


CASES = (
    Case("career", "asha-returning-pm", {
        "target_role": "Senior product manager",
        "locations": "Bengaluru or remote India",
        "experience": "Eight years in B2B SaaS; returning after a caregiving break",
        "strengths": "0-to-1 products, analytics, customer discovery",
        "constraints": "Remote-first, no relocation, 30-hour preferred week",
        "weekly_scan": True,
        "timezone": "Asia/Kolkata",
    }),
    Case("health", "miguel-shift-worker", {
        "goal": "Improve energy and build a sustainable walking habit",
        "baseline": "Rotating shifts, irregular sleep, currently walks twice a week",
        "dietary_context": "Vegetarian, nut allergy, cooks Latin American food",
        "activity_limits": "Previous knee injury; clinician cleared flat walking only",
        "available_days": "Tuesday, Thursday, Saturday",
        "daily_checkin": True,
        "timezone": "America/Mexico_City",
    }),
    Case("travel", "priya-accessible-japan", {
        "origin": "Delhi",
        "destination": "Japan",
        "dates": "10-18 November 2026, flexible by two days",
        "travelers": "Two adults",
        "budget": "INR 300,000 total",
        "preferences": "Food, design, slower pace, no nightlife",
        "accessibility": "Avoid long climbs; vegetarian options required",
        "price_watch": True,
        "timezone": "Asia/Kolkata",
    }),
    Case("teach", "sam-adhd-learner", {
        "topic": "SQL window functions",
        "outcome": "Solve interview questions and explain the mental model",
        "current_level": "Some familiarity",
        "minutes_per_session": 15,
        "mode": "Hybrid",
        "spaced_reviews": True,
        "timezone": "America/New_York",
    }),
    Case("finance", "fatima-freelancer", {
        "currency": "AED",
        "goal": "Build a six-month emergency fund while paying variable-rate debt",
        "monthly_context": "Irregular freelance income between AED 9k and 18k; fixed costs AED 7k",
        "debts_and_commitments": "Synthetic balance AED 22k at 11%; no account identifiers",
        "risk_comfort": "Low",
        "monthly_review": True,
        "timezone": "Asia/Dubai",
    }),
    Case("documents", "jordan-nonprofit-analyst", {
        "deliverable": "Presentation",
        "objective": "Turn synthetic donor-retention data into three board decisions",
        "audience": "Nonprofit board with mixed data literacy",
        "source_paths": "/tmp/synthetic-donor-retention.csv",
        "tone": "Clear, humane, decision-oriented",
        "constraints": "Eight slides, 16:9, cite every number",
        "recurring_report": False,
        "timezone": "Europe/London",
    }),
)


def request_json(method: str, url: str, **kwargs: Any) -> tuple[int, dict[str, Any]]:
    response = requests.request(method, url, timeout=30, **kwargs)
    try:
        payload = response.json()
    except ValueError:
        payload = {"detail": response.text[:500]}
    return response.status_code, payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--prefix", default=f"synthetic-e2e-{int(time.time())}")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    failures: list[str] = []
    report: list[dict[str, Any]] = []

    status, definitions = request_json("GET", f"{base}/workflows")
    found = {item.get("id") for item in definitions.get("workflows", [])}
    if status != 200 or found != {case.workflow_id for case in CASES}:
        print(json.dumps({"error": "workflow catalog unavailable", "status": status, "found": sorted(found)}))
        return 1

    for case in CASES:
        user_id = f"{args.prefix}-{case.persona}"
        status, started = request_json(
            "POST",
            f"{base}/workflows/{case.workflow_id}/runs",
            params={"user_id": user_id},
            json={"inputs": case.inputs},
        )
        if status != 200:
            failures.append(f"{case.workflow_id}: start failed ({status}) {started.get('detail')}")
            continue
        run = started["run"]
        run_id = run["run_id"]
        expected_task_count = len(run["stages"])

        context_status, context = request_json(
            "GET", f"{base}/workflow-runs/{run_id}/context", params={"user_id": user_id}
        )
        if context_status != 200 or run_id not in context.get("context", ""):
            failures.append(f"{case.workflow_id}: compact context contract failed")

        denied_status, _ = request_json(
            "GET", f"{base}/workflow-runs/{run_id}", params={"user_id": f"{user_id}-other"}
        )
        if denied_status != 403:
            failures.append(f"{case.workflow_id}: cross-user read was not denied")

        for schedule in run.get("schedules", []):
            for enabled in (False, True):
                toggle_status, _ = request_json(
                    "PATCH",
                    f"{base}/workflow-schedules/{schedule['schedule_id']}",
                    params={"user_id": user_id},
                    json={"enabled": enabled},
                )
                if toggle_status != 200:
                    failures.append(f"{case.workflow_id}: schedule toggle failed")

        confirmations = 0
        for _ in range(20):
            if run["status"] == "completed":
                break
            if run["status"] == "waiting_confirmation":
                confirmations += 1
                status, updated = request_json(
                    "POST",
                    f"{base}/workflow-runs/{run_id}/actions",
                    params={"user_id": user_id},
                    json={"action": "approve"},
                )
            else:
                stage = run.get("current_stage") or {}
                status, updated = request_json(
                    "POST",
                    f"{base}/workflow-runs/{run_id}/actions",
                    params={"user_id": user_id},
                    json={
                        "action": "complete",
                        "summary": f"Synthetic validation completed {stage.get('title', 'stage')}",
                        "payload": {"synthetic": True, "persona": case.persona},
                        "citations": [{"title": "Synthetic fixture", "url": "https://example.com/synthetic"}],
                    },
                )
            if status != 200:
                failures.append(f"{case.workflow_id}: transition failed ({status}) {updated.get('detail')}")
                break
            run = updated["run"]
        if run["status"] != "completed":
            failures.append(f"{case.workflow_id}: did not reach completed")
        if len(run.get("tasks", [])) != expected_task_count:
            failures.append(f"{case.workflow_id}: task mirror count changed")
        if any(stage.get("requires_confirmation") for stage in run["stages"]) and confirmations == 0:
            failures.append(f"{case.workflow_id}: confirmation stage did not pause")

        report.append({
            "workflow": case.workflow_id,
            "persona": case.persona,
            "run_id": run_id,
            "status": run["status"],
            "progress": run["progress_percent"],
            "tasks": len(run.get("tasks", [])),
            "schedules": len(run.get("schedules", [])),
            "confirmations": confirmations,
        })

    print(json.dumps({"base_url": base, "runs": report, "failures": failures}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
