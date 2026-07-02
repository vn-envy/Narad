#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop
import narad_paths  # noqa: F401

import server  # noqa: E402


@dataclass(frozen=True)
class LiveCase:
    name: str
    expected_avatar: str
    prompt: str


def _parse_sse_payloads(response) -> list[dict]:
    payloads: list[dict] = []
    for line in response.iter_lines():
        if not line:
            continue
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        if not line.startswith("data: "):
            continue
        payloads.append(json.loads(line[6:]))
    return payloads


def _fixture_path() -> str:
    fixture_dir = Path.home() / ".narad" / "refinement-fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    fixture = fixture_dir / "fixture.txt"
    fixture.write_text(
        "Narad refinement fixture\n"
        "This file is for Matsya and Parashurama end-to-end checks.\n"
        "The canonical agents are Matsya, Rama, Krishna, and Parashurama.\n"
    )
    return str(fixture)


def _live_cases() -> list[LiveCase]:
    fixture = _fixture_path()
    return [
        LiveCase(
            name="matsya-document",
            expected_avatar="Matsya",
            prompt=f"Use Matsya. Call extract_document on '{fixture}' and summarize the file in 3 bullets.",
        ),
        LiveCase(
            name="rama-finance",
            expected_avatar="Rama",
            prompt=(
                "Use Rama. Call get_financial_context() and get_budget_status() only. "
                "Do not modify any data. Summarize the current finance snapshot in 3 bullets."
            ),
        ),
        LiveCase(
            name="krishna-email",
            expected_avatar="Krishna",
            prompt=(
                "Use Krishna. Call compose_email with to='team@example.com', "
                "subject='Narad readiness update', "
                "body='The four-agent consolidation is complete and the system is ready for final validation.', "
                "and cc=''. Show me the preview and do not send anything."
            ),
        ),
        LiveCase(
            name="parashurama-shell",
            expected_avatar="Parashurama",
            prompt=(
                f"Use Parashurama. First read_file('{fixture}'), then run_shell('pwd', "
                f"working_dir='{ROOT}'). Briefly report what you found."
            ),
        ),
    ]


def main() -> int:
    print("Running live 4-agent E2E harness against FastAPI app...")
    failures: list[str] = []

    with TestClient(server.app) as client:
        health = client.get("/health")
        capabilities = client.get("/capabilities")
        if health.status_code != 200 or capabilities.status_code != 200:
            print("Health or capabilities endpoint failed.")
            return 1

        health_json = health.json()
        capabilities_json = capabilities.json()
        print(
            json.dumps(
                {
                    "health_status": health_json.get("status"),
                    "agent_names": health_json.get("architecture", {}).get("agent_names"),
                    "providers": {
                        name: payload.get("available")
                        for name, payload in capabilities_json.get("providers", {}).items()
                    },
                },
                indent=2,
            )
        )

        for idx, case in enumerate(_live_cases(), start=1):
            print(f"\n[{idx}/4] {case.name} -> {case.expected_avatar}")
            with client.stream(
                "POST",
                "/chat",
                json={"query": case.prompt, "user_id": f"live-e2e-{case.name}"},
            ) as response:
                payloads = _parse_sse_payloads(response)

            events = [payload.get("type") for payload in payloads]
            starts = [p for p in payloads if p.get("type") == "avatar_start"]
            dones = [p for p in payloads if p.get("type") == "avatar_done"]
            errors = [p for p in payloads if p.get("type") == "error"]
            session_id = next(
                (p.get("data", {}).get("session_id") for p in payloads if p.get("type") == "done"),
                None,
            )

            start_ok = any(p.get("data", {}).get("avatar") == case.expected_avatar for p in starts)
            done_ok = any(p.get("data", {}).get("avatar") == case.expected_avatar for p in dones)

            if errors:
                failures.append(f"{case.name}: SSE error -> {errors[0].get('data', {}).get('message')}")
            if not start_ok:
                failures.append(f"{case.name}: missing avatar_start for {case.expected_avatar}")
            if not done_ok:
                failures.append(f"{case.name}: missing avatar_done for {case.expected_avatar}")
            if session_id is None:
                failures.append(f"{case.name}: missing done/session_id event")

            trace_summary = None
            if session_id:
                trace_response = client.get(f"/trace/{session_id}")
                if trace_response.status_code == 200:
                    trace_summary = trace_response.json().get("summary")
                else:
                    failures.append(f"{case.name}: trace unavailable for session {session_id}")

            synthesis = next(
                (p.get("data", {}).get("text", "") for p in payloads if p.get("type") == "narad_synthesis"),
                "",
            )
            preview = synthesis.replace("\n", " ")[:220]
            print(
                json.dumps(
                    {
                        "events": events,
                        "session_id": session_id,
                        "summary_preview": preview,
                        "trace_summary": trace_summary,
                    },
                    ensure_ascii=False,
                )
            )

    if failures:
        print("\nLive E2E failures:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("\nLive E2E passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
