"""
Matsya's web search tool — powered by Tavily.

Returns structured search results that Matsya synthesises into a response.
Requires TAVILY_API_KEY env var. Gracefully degrades to training knowledge
if the key is missing or the search fails.
"""

from __future__ import annotations

import os
from typing import Any


def web_search(query: str, max_results: int = 5) -> dict:
    """Search the live web for current information.
    Use for: current news, recent events, live data, prices, people, organisations.
    Returns a list of results with title, url, and content snippet."""
    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        return {
            "status": "unavailable",
            "message": "Live search unavailable (TAVILY_API_KEY not set). Using training knowledge.",
            "results": [],
        }

    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=api_key)
        response = client.search(
            query=query,
            max_results=max_results,
            include_answer=True,
            include_raw_content=False,
        )
        results = [
            {
                "title":   r.get("title", ""),
                "url":     r.get("url", ""),
                "snippet": r.get("content", "")[:400],
            }
            for r in response.get("results", [])
        ]
        return {
            "status":        "ok",
            "query":         query,
            "answer":        response.get("answer", ""),
            "results":       results,
            "result_count":  len(results),
        }
    except Exception as exc:
        return {
            "status":  "error",
            "message": f"Search failed: {exc}",
            "results": [],
        }
