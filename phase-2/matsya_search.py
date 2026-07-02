"""
Matsya's web search tool.

Primary: Tinyfish (TINYFISH_API_KEY)
Fallback: Tavily (TAVILY_API_KEY)

If neither key is set, or both calls fail, returns a graceful unavailable response.
"""

from __future__ import annotations

import os


def web_search(query: str, max_results: int = 5) -> dict:
    """Search the live web for current information.
    Use for: current news, recent events, live data, prices, people, organisations.
    Returns a list of results with title, url, and content snippet."""
    tinyfish_key = os.environ.get("TINYFISH_API_KEY", "")
    if tinyfish_key:
        result = _search_tinyfish(query, tinyfish_key, max_results)
        if result["status"] == "ok":
            return result
        # Tinyfish failed — fall through to Tavily

    tavily_key = os.environ.get("TAVILY_API_KEY", "")
    if tavily_key:
        return _search_tavily(query, tavily_key, max_results)

    return {
        "status": "unavailable",
        "message": "Live search unavailable (TINYFISH_API_KEY and TAVILY_API_KEY not set). Using training knowledge.",
        "results": [],
    }


def _search_tinyfish(query: str, api_key: str, max_results: int) -> dict:
    try:
        import requests as _requests
        response = _requests.get(
            "https://api.search.tinyfish.ai",
            headers={"X-API-Key": api_key},
            params={"query": query, "language": "en"},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        raw = data.get("results", data) if isinstance(data, dict) else data
        if isinstance(raw, list):
            items = raw[:max_results]
        else:
            items = []
        results = [
            {
                "title":   r.get("title", ""),
                "url":     r.get("url", ""),
                "snippet": r.get("snippet", "")[:400],
            }
            for r in items
            if isinstance(r, dict)
        ]
        return {
            "status":       "ok",
            "query":        query,
            "answer":       "",
            "results":      results,
            "result_count": len(results),
            "source":       "tinyfish",
        }
    except Exception as exc:
        return {
            "status":  "error",
            "message": f"Tinyfish search failed: {exc}",
            "results": [],
            "source":  "tinyfish",
        }


def _search_tavily(query: str, api_key: str, max_results: int) -> dict:
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
            "status":       "ok",
            "query":        query,
            "answer":       response.get("answer", ""),
            "results":      results,
            "result_count": len(results),
            "source":       "tavily",
        }
    except Exception as exc:
        return {
            "status":  "error",
            "message": f"Tavily search failed: {exc}",
            "results": [],
            "source":  "tavily",
        }
