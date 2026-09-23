"""Matsya's low-latency live web search.

Exa is the primary provider because it can return query-relevant highlights
without flooding the model context.
"""

from __future__ import annotations

import os
from math import ceil
from typing import Any

import requests

EXA_SEARCH_URL = "https://api.exa.ai/search"
EXA_SEARCH_TYPES = {"auto", "fast", "instant", "deep-lite", "deep", "deep-reasoning"}


def _error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            return str(payload.get("error") or payload.get("message") or response.reason)
    except ValueError:
        pass
    return response.reason or f"HTTP {response.status_code}"


def search_exa(
    query: str,
    api_key: str,
    max_results: int = 5,
    *,
    search_type: str = "auto",
    max_age_hours: int | None = None,
    include_text: bool = False,
    text_max_characters: int = 12000,
    output_schema: dict[str, Any] | None = None,
    system_prompt: str = "",
    category: str = "",
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    additional_queries: list[str] | None = None,
    timeout_s: int = 60,
) -> dict[str, Any]:
    """Call Exa Search using its raw JSON contract."""
    query = (query or "").strip()
    if not query:
        return {"status": "error", "message": "A non-empty search query is required.", "results": [], "source": "exa"}
    selected_type = search_type if search_type in EXA_SEARCH_TYPES else "auto"
    result_limit = max(1, min(int(max_results), 8))
    total_text_limit = max(1000, min(int(text_max_characters), 12000))
    contents: dict[str, Any] = {"highlights": True}
    if include_text:
        # Exa's content limit applies per result. Divide Narad's requested total
        # budget so one search cannot unexpectedly inject N times that amount.
        contents["text"] = {"maxCharacters": max(1000, ceil(total_text_limit / result_limit))}
    if max_age_hours is not None:
        contents["maxAgeHours"] = max(-1, min(int(max_age_hours), 720))
    payload: dict[str, Any] = {
        "query": query,
        "type": selected_type,
        "numResults": result_limit,
        "contents": contents,
        "moderation": True,
    }
    if output_schema:
        payload["outputSchema"] = output_schema
    if system_prompt.strip():
        payload["systemPrompt"] = system_prompt.strip()[:4000]
    if category.strip():
        payload["category"] = category.strip()
    if include_domains:
        payload["includeDomains"] = [str(item).strip() for item in include_domains if str(item).strip()][:100]
    if exclude_domains and category not in {"company", "people"}:
        payload["excludeDomains"] = [str(item).strip() for item in exclude_domains if str(item).strip()][:100]
    if additional_queries and selected_type in {"deep-lite", "deep", "deep-reasoning"}:
        payload["additionalQueries"] = [str(item).strip() for item in additional_queries if str(item).strip()][:5]

    try:
        response = requests.post(
            EXA_SEARCH_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=max(10, min(int(timeout_s), 120)),
        )
        if not response.ok:
            return {
                "status": "error",
                "message": f"Exa search failed ({response.status_code}): {_error_message(response)}",
                "results": [],
                "source": "exa",
                "http_status": response.status_code,
            }
        data = response.json()
        raw_results = data.get("results", []) if isinstance(data, dict) else []
        results = []
        remaining_text_chars = total_text_limit if include_text else 0
        remaining_highlight_chars = min(8000, max(2400, result_limit * 1600))
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            highlights: list[str] = []
            for value in item.get("highlights", [])[:4]:
                cleaned = str(value).strip()
                if not cleaned or remaining_highlight_chars <= 0:
                    continue
                bounded = cleaned[: min(1200, remaining_highlight_chars)]
                highlights.append(bounded)
                remaining_highlight_chars -= len(bounded)
            raw_text = str(item.get("text") or "").strip()
            text = raw_text[:remaining_text_chars] if include_text else ""
            remaining_text_chars -= len(text)
            results.append(
                {
                    "title": str(item.get("title") or "")[:400],
                    "url": str(item.get("url") or item.get("id") or "")[:2000],
                    "snippet": (" ".join(highlights) or text)[:1200],
                    "highlights": highlights,
                    "text": text,
                    "published_date": item.get("publishedDate"),
                    "author": str(item.get("author") or "")[:300] or None,
                }
            )
        output = data.get("output") if isinstance(data.get("output"), dict) else {}
        answer = output.get("content", "")
        return {
            "status": "ok",
            "query": query,
            "answer": answer,
            "results": results,
            "result_count": len(results),
            "source": "exa",
            "search_type": selected_type,
            "grounding": output.get("grounding", []),
            "request_id": data.get("requestId"),
            "cost_usd": (data.get("costDollars") or {}).get("total"),
        }
    except Exception as exc:
        return {
            "status": "error",
            "message": f"Exa search failed: {type(exc).__name__}",
            "results": [],
            "source": "exa",
        }


def web_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """Search the live web using Exa's bounded highlights."""
    exa_key = os.environ.get("EXA_API_KEY", "").strip()
    exa_failure: dict[str, Any] | None = None
    if exa_key:
        result = search_exa(query, exa_key, max_results)
        if result["status"] == "ok":
            return result
        exa_failure = result

    if exa_failure:
        return exa_failure

    return {
        "status": "unavailable",
        "message": "Live search unavailable (EXA_API_KEY not set). Using training knowledge.",
        "results": [],
        "source": "none",
    }
