"""
Phase 1 CLI demo.

Usage:
  python run_demo.py "My SQL query runs fine on staging but takes 45s on prod"
  python run_demo.py  # uses built-in demo queries
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

DEMO_QUERIES = [
    "My Python script throws RecursionError but only on certain inputs. Help me find the bug.",
    "Research open-source vector databases and draft a 300-word comparison blog post.",
    "Implement a Redis cache layer for user sessions, then review it for security issues.",
    "Review this pricing strategy: charge $99/month flat for all users. Is it sound?",
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
        user_id="user", session_id=session_id, new_message=user_message
    ):
        _print_event(event)

    print(f"\n{'='*60}")
    print("  Done.")
    print(f"{'='*60}\n")


def _print_event(event) -> None:
    if not event.content or not event.content.parts:
        return

    for part in event.content.parts:
        if part.function_call:
            fc = part.function_call
            avatar = fc.name.replace("invoke_", "").capitalize()
            task = (fc.args or {}).get("request", (fc.args or {}).get("task", ""))
            print(f"  🔀 ROUTING → {avatar}")
            print(f"     task: {task[:120]}")

        elif part.function_response:
            fr = part.function_response
            avatar = fr.name.replace("invoke_", "").capitalize()
            result = fr.response or {}
            print(f"  ✅ {avatar} done")
            result_str = str(result)
            print(f"     {result_str[:200]}")

        elif part.text and event.is_final_response():
            print("\n  📝 NARAD SYNTHESIS:\n")
            text = part.text
            for i in range(0, len(text), 100):
                print(f"  {text[i:i+100]}")


def main() -> None:
    missing = []
    if not os.environ.get("OPENAI_API_KEY"):
        missing.append("OPENAI_API_KEY")
    if not os.environ.get("DEEPSEEK_API_KEY"):
        missing.append("DEEPSEEK_API_KEY")
    if missing:
        print(f"ERROR: missing env vars: {', '.join(missing)}")
        sys.exit(1)

    queries = sys.argv[1:] if len(sys.argv) > 1 else DEMO_QUERIES[:1]
    for q in queries:
        asyncio.run(run_query(q))


if __name__ == "__main__":
    main()
