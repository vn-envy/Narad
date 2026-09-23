"""Provider-neutral web enrichment for Narad Workflow Paths.

Exa owns discovery, token-efficient extraction, and structured deep research.
Firecrawl remains an optional difficult-page fallback. Authenticated navigation
and side effects stay in Narad's local ``computer_use`` tool.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlparse

import requests

from tool_result import artifact, citation, ensure_artifact_dir, envelope, ui_panel


def _valid_public_url(url: str) -> bool:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    host = (parsed.hostname or "").lower()
    return host not in {"localhost", "127.0.0.1", "::1"} and not host.endswith(".local")


def _citations_from_exa(result: dict[str, Any]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in result.get("results", []):
        if not isinstance(item, dict) or not item.get("url"):
            continue
        url = str(item["url"])
        if url in seen:
            continue
        seen.add(url)
        citations.append(
            citation(
                title=str(item.get("title") or url),
                url=url,
                source="Exa",
                snippet=str(item.get("snippet") or "")[:500],
                metadata={
                    "published_date": item.get("published_date"),
                    "author": item.get("author"),
                },
            )
        )
    for field in result.get("grounding", []):
        if not isinstance(field, dict):
            continue
        for item in field.get("citations", []):
            if not isinstance(item, dict) or not item.get("url"):
                continue
            url = str(item["url"])
            if url in seen:
                continue
            seen.add(url)
            citations.append(
                citation(
                    title=str(item.get("title") or url),
                    url=url,
                    source="Exa grounding",
                    metadata={"field": field.get("field"), "confidence": field.get("confidence")},
                )
            )
    return citations


def exa_search(
    query: str,
    search_type: str = "auto",
    max_results: int = 5,
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
    """Search with Exa; use deep modes and ``output_schema`` for synthesis."""
    api_key = os.environ.get("EXA_API_KEY", "").strip()
    if not api_key:
        return envelope(
            status="unavailable",
            summary="Exa is not configured. Add EXA_API_KEY or use the Tavily-backed web_search fallback.",
            provenance={"tool": "exa_search", "provider": "exa"},
            error="exa_unconfigured",
        )

    from matsya_search import search_exa

    result = search_exa(
        query,
        api_key,
        max_results=max_results,
        search_type=search_type,
        max_age_hours=max_age_hours,
        include_text=include_text,
        text_max_characters=text_max_characters,
        output_schema=output_schema,
        system_prompt=system_prompt,
        category=category,
        include_domains=include_domains,
        exclude_domains=exclude_domains,
        additional_queries=additional_queries,
        timeout_s=timeout_s,
    )
    if result.get("status") != "ok":
        return envelope(
            status="error",
            summary=str(result.get("message") or "Exa search failed."),
            provenance={"tool": "exa_search", "provider": "exa", "search_type": search_type},
            error="exa_search_failed",
        )

    citations = _citations_from_exa(result)
    answer = result.get("answer")
    artifacts: list[dict[str, Any]] = []
    if answer not in (None, ""):
        out_dir = ensure_artifact_dir("exa_search")
        out_path = out_dir / "structured-result.json"
        out_path.write_text(
            json.dumps(
                {
                    "query": query,
                    "output": answer,
                    "grounding": result.get("grounding", []),
                    "request_id": result.get("request_id"),
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        artifacts.append(artifact(type="json", label="Structured research", path=out_path, mime_type="application/json"))
    answer_summary = answer if isinstance(answer, str) else ""
    return envelope(
        status="ok",
        summary=answer_summary[:1500] or f"Exa found {len(result.get('results', []))} grounded sources.",
        artifacts=artifacts,
        citations=citations,
        ui=ui_panel(title="Exa research", summary=f"{len(citations)} cited sources for {query}"),
        provenance={
            "tool": "exa_search",
            "provider": "exa",
            "search_type": result.get("search_type"),
            "request_id": result.get("request_id"),
            "cost_usd": result.get("cost_usd"),
        },
        result=result,
    )


def exa_contents(
    urls: list[str],
    question: str = "",
    max_characters: int = 8000,
    max_age_hours: int | None = None,
    timeout_s: int = 90,
) -> dict[str, Any]:
    """Extract full Markdown content for known public URLs through Exa."""
    if isinstance(urls, str):
        urls = [urls]
    clean_urls = [str(url).strip() for url in urls if _valid_public_url(str(url))][:20]
    if not clean_urls:
        return envelope(status="error", summary="At least one valid public HTTP(S) URL is required.", error="invalid_urls")
    api_key = os.environ.get("EXA_API_KEY", "").strip()
    if not api_key:
        return envelope(
            status="unavailable",
            summary="Exa is not configured; use Firecrawl or browse_url as an extraction fallback.",
            provenance={"tool": "exa_contents", "provider": "exa", "urls": clean_urls},
            error="exa_unconfigured",
        )
    text_limit = max(1000, min(int(max_characters), 16000))
    per_page_limit = max(1000, text_limit // len(clean_urls))
    payload: dict[str, Any] = {
        "urls": clean_urls,
        "text": {"maxCharacters": per_page_limit},
    }
    if question.strip():
        payload["highlights"] = {"query": question.strip()[:1200], "maxCharacters": min(5000, per_page_limit)}
    if max_age_hours is not None:
        payload["maxAgeHours"] = max(-1, min(int(max_age_hours), 720))
    try:
        response = requests.post(
            "https://api.exa.ai/contents",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=max(10, min(int(timeout_s), 150)),
        )
        if not response.ok:
            try:
                detail = response.json().get("error", response.reason)
            except ValueError:
                detail = response.reason
            return envelope(
                status="error",
                summary=f"Exa content extraction failed ({response.status_code}): {detail}",
                provenance={"tool": "exa_contents", "provider": "exa", "urls": clean_urls},
                error="exa_contents_failed",
            )
        raw = response.json()
        results = raw.get("results", []) if isinstance(raw, dict) else []
        out_dir = ensure_artifact_dir("exa_contents")
        out_path = out_dir / "extracted-pages.md"
        sections: list[str] = []
        citations: list[dict[str, Any]] = []
        compact_results: list[dict[str, Any]] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or item.get("id") or "")
            title = str(item.get("title") or url or "Source")
            page_text = str(item.get("text") or "").strip()
            highlights = [str(value).strip() for value in item.get("highlights", []) if str(value).strip()]
            sections.append(f"# {title}\n\nSource: {url}\n\n{page_text}\n")
            if url:
                citations.append(citation(title=title, url=url, source="Exa", snippet=" ".join(highlights)[:1200]))
            compact_results.append(
                {
                    "title": title,
                    "url": url,
                    "published_date": item.get("publishedDate"),
                    "author": item.get("author"),
                    "highlights": highlights,
                    "text": page_text[:per_page_limit],
                }
            )
        out_path.write_text("\n---\n\n".join(sections) or "No content returned.\n", encoding="utf-8")
        return envelope(
            status="ok",
            summary=f"Exa extracted {len(compact_results)} of {len(clean_urls)} requested pages.",
            artifacts=[artifact(type="markdown", label="Extracted pages", path=out_path, mime_type="text/markdown")],
            citations=citations,
            ui=ui_panel(title="Web extraction", summary=f"{len(compact_results)} pages extracted", primary_artifact_label="Extracted pages"),
            provenance={
                "tool": "exa_contents",
                "provider": "exa",
                "request_id": raw.get("requestId"),
                "statuses": raw.get("statuses", []),
                "cost_usd": (raw.get("costDollars") or {}).get("total"),
            },
            results=compact_results,
        )
    except Exception as exc:
        return envelope(
            status="error",
            summary=f"Exa content extraction failed: {type(exc).__name__}",
            provenance={"tool": "exa_contents", "provider": "exa", "urls": clean_urls},
            error="exa_contents_failed",
        )


def firecrawl_extract(
    url: str,
    question: str = "",
    *,
    use_cache: bool = True,
    timeout_s: int = 90,
) -> dict[str, Any]:
    """Extract one difficult public page when Exa cannot parse it."""
    if not _valid_public_url(url):
        return envelope(status="error", summary="A valid public HTTP(S) URL is required.", error="invalid_url")
    api_key = os.environ.get("FIRECRAWL_API_KEY", "").strip()
    if not api_key:
        return envelope(
            status="unavailable",
            summary="Firecrawl is not configured; use exa_contents or browse_url.",
            provenance={"tool": "firecrawl_extract", "url": url},
            error="firecrawl_unconfigured",
        )
    base = os.environ.get("FIRECRAWL_BASE_URL", "https://api.firecrawl.dev").rstrip("/")
    formats: list[Any] = [{"type": "markdown"}]
    if question.strip():
        formats.append({"type": "summary", "prompt": question.strip()[:1200]})
    try:
        response = requests.post(
            f"{base}/v2/scrape",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"url": url, "formats": formats, "storeInCache": bool(use_cache)},
            timeout=max(10, min(int(timeout_s), 150)),
        )
        response.raise_for_status()
        raw = response.json()
        data = raw.get("data", raw) if isinstance(raw, dict) else {}
        markdown = str(data.get("markdown") or "").strip()
        summary = str(data.get("summary") or "").strip()
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        out_dir = ensure_artifact_dir("firecrawl")
        out_path = out_dir / "extracted.md"
        out_path.write_text(markdown or summary or f"Source: {url}\n", encoding="utf-8")
        source_title = str(metadata.get("title") or url)
        return envelope(
            status="ok",
            summary=summary or f"Extracted {len(markdown):,} characters from {source_title}.",
            artifacts=[artifact(type="markdown", label="Extracted page", path=out_path, mime_type="text/markdown")],
            citations=[citation(title=source_title, url=str(metadata.get("sourceURL") or url), source="Firecrawl")],
            ui=ui_panel(title="Web extraction", summary=summary or source_title, primary_artifact_label="Extracted page"),
            provenance={"tool": "firecrawl_extract", "provider": "firecrawl", "url": url},
            content=markdown[:30000],
        )
    except Exception as exc:
        return envelope(
            status="error",
            summary=f"Firecrawl extraction failed: {type(exc).__name__}",
            provenance={"tool": "firecrawl_extract", "provider": "firecrawl", "url": url},
            error="firecrawl_extract_failed",
        )


def enrich_web_research(
    query: str,
    *,
    include_recent: bool = False,
    max_results: int = 5,
) -> dict[str, Any]:
    """Run the cheapest discovery layer and optionally add community signals."""
    from matsya_search import web_search

    search = web_search(query, max_results=max(1, min(max_results, 10)))
    recent: dict[str, Any] = {}
    if include_recent:
        try:
            from http_skill import search_last30days

            recent = search_last30days(query)
        except Exception as exc:
            recent = {"status": "error", "summary": str(exc)}
    citations = [
        citation(
            title=str(item.get("title") or item.get("url") or "Source"),
            url=str(item.get("url") or ""),
            source=str(search.get("source") or "web"),
            snippet=str(item.get("snippet") or ""),
        )
        for item in search.get("results", [])
        if isinstance(item, dict) and item.get("url")
    ]
    if isinstance(recent.get("citations"), list):
        citations.extend(item for item in recent["citations"] if isinstance(item, dict))
    status = "ok" if search.get("status") == "ok" or recent.get("status") == "ok" else "unavailable"
    answer = search.get("answer")
    summary = answer if isinstance(answer, str) and answer.strip() else search.get("message")
    return envelope(
        status=status,
        summary=str(summary or f"Found {len(citations)} source references."),
        citations=citations[:30],
        ui=ui_panel(title="Research evidence", summary=f"{len(citations)} source references for {query}"),
        provenance={"tool": "enrich_web_research", "search_provider": search.get("source"), "recent_included": include_recent},
        search=search,
        recent=recent,
    )
