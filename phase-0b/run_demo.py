"""
Phase 0b CLI demo — no server needed.

Runs a single query through Narad end-to-end and prints each SSE event
as it arrives. Use this to prove the delegation loop before wiring LibreChat.

Usage:
  python run_demo.py "Research open-source vector DBs and draft a blog post"
  python run_demo.py "My SQL query runs fine on staging but takes 45s on prod"
  python run_demo.py  # uses built-in demo queries
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid

DEMO_QUERIES = [
    "What are the latest RBI interest rate announcements?",
    "My Python script throws RecursionError but only on certain inputs. Help me find the bug.",
    "Research open-source vector databases and draft a 300-word comparison blog post.",
    "Implement a Redis cache layer for user sessions, then review it for security issues.",
]


async def run_query(query: str) -> None:
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types as genai_types
    from narad_agent import build_narad_agent

    narad = build_narad_agent()
    session_service = InMemorySessionService()
    runner = Runner(agent=narad, app_name="avatara", session_service=session_service)

    session_id = str(uuid.uuid4())
    await session_service.create_session(
        app_name="avatara", user_id="user", session_id=session_id
    )

    user_message = genai_types.Content(
        role="user", parts=[genai_types.Part(text=query)]
    )

    print(f"\n{'='*60}")
    print(f"  Query: {query}")
    print(f"{'='*60}\n")

    async for event in runner.run_async(
        user_id="user",
        session_id=session_id,
        new_message=user_message,
    ):
        _print_event(event)

    print(f"\n{'='*60}")
    print("  Done.")
    print(f"{'='*60}\n")


def _print_event(event) -> None:
    """Pretty-print an ADK event to stdout."""
    if not event.content or not event.content.parts:
        return

    for part in event.content.parts:
        if part.function_call:
            fc = part.function_call
            avatar = fc.name.replace("invoke_", "").capitalize()
            task = (fc.args or {}).get("task", "")
            print(f"  🔀 ROUTING → {avatar}")
            print(f"     task: {task[:120]}")

        elif part.function_response:
            fr = part.function_response
            avatar = fr.name.replace("invoke_", "").capitalize()
            result = fr.response or {}
            print(f"  ✅ {avatar} done")
            result_text = str(result.get("result", result))
            print(f"     {result_text[:120]}")

        elif part.text and event.is_final_response():
            print(f"\n  📝 NARAD SYNTHESIS:\n")
            # Print in chunks for readability
            text = part.text
            for i in range(0, len(text), 100):
                print(f"  {text[i:i+100]}")


def main() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not set.")
        sys.exit(1)

    queries = sys.argv[1:] if len(sys.argv) > 1 else DEMO_QUERIES[:2]

    for q in queries:
        asyncio.run(run_query(q))


if __name__ == "__main__":
    main()
