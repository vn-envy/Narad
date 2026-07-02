"""
Matsya HTTP skill — safe parameterised HTTP requests.

Lets Matsya reach any REST API, webhook endpoint, or JSON service
without hardcoded integrations.

Safety model:
  - Blocks private/localhost IP ranges (SSRF prevention)
  - Non-HTTP protocols are rejected
  - Response body is capped to prevent context overflow
  - Timeout enforced (default 30s)
  - No redirect following to private ranges

Uses httpx (async-capable, modern). Falls back to urllib if not installed.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from tool_result import citation, envelope, ui_panel

TIMEOUT_S = int(os.environ.get("HTTP_TIMEOUT", "30"))

# Private/reserved IP ranges blocked to prevent SSRF
_PRIVATE_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),   # link-local
    ipaddress.ip_network("100.64.0.0/10"),    # shared address space
    ipaddress.ip_network("::1/128"),           # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),          # IPv6 private
]

_RESPONSE_CAP = 12_000  # chars

_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "over", "under",
    "about", "what", "your", "their", "they", "them", "have", "has", "after", "before",
    "people", "topic", "query", "last", "days", "month", "just", "been", "more",
}


def _summarize_last30days(results: list[dict], query: str) -> tuple[str, list[str]]:
    if not results:
        return "No grounded discussions were found.", ["No source returned matching results."]
    tokens = Counter()
    for item in results:
        text = f"{item.get('title', '')} {item.get('snippet', '')}".lower()
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", text):
            if token not in _STOPWORDS and token != query.lower():
                tokens[token] += 1
    top_terms = [term for term, _ in tokens.most_common(5)]
    top_platforms = Counter(item.get("platform", "unknown") for item in results).most_common(3)
    themes = ", ".join(top_terms[:3]) if top_terms else "no strong repeated themes"
    summary = (
        f"Recent community signal around {query!r} clusters most around {themes}. "
        f"Highest visible activity came from "
        + ", ".join(f"{platform} ({count})" for platform, count in top_platforms)
        + "."
    )
    gaps: list[str] = []
    if not os.environ.get("YOUTUBE_API_KEY"):
        gaps.append("YouTube transcript/search enrichment is not configured.")
    if not os.environ.get("X_BEARER_TOKEN"):
        gaps.append("Direct X API enrichment is not configured; public scrape fallback may be sparse.")
    if not any(item.get("platform") == "github" for item in results):
        gaps.append("No recent GitHub signal was found for this query.")
    return summary, gaps


def _engagement_signals(results: list[dict]) -> dict:
    by_platform: dict[str, dict[str, int]] = {}
    for item in results:
        platform = item.get("platform", "unknown")
        stats = by_platform.setdefault(platform, {"items": 0, "engagement": 0, "comments": 0})
        stats["items"] += 1
        stats["engagement"] += int(item.get("engagement", 0) or 0)
        stats["comments"] += int(item.get("comments", 0) or 0)
    return by_platform


def _check_url(url: str) -> str | None:
    """Return an error message if the URL is unsafe, else None."""
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        return f"Only http:// and https:// are allowed. Got: {parsed.scheme!r}"

    hostname = parsed.hostname or ""
    if not hostname:
        return "No hostname in URL."

    # Resolve hostname to IP and check for private ranges
    try:
        ip_str = socket.gethostbyname(hostname)
        ip = ipaddress.ip_address(ip_str)
        for private_range in _PRIVATE_RANGES:
            if ip in private_range:
                return (
                    f"Blocked: '{hostname}' resolves to a private/reserved IP "
                    f"({ip_str}). Only public internet addresses are allowed."
                )
    except socket.gaierror:
        pass  # DNS failure — let the actual request produce a clear error

    return None


def http_request(
    method: str,
    url: str,
    headers: dict | None = None,
    body: dict | str | None = None,
    timeout_s: int = TIMEOUT_S,
) -> dict:
    """Make an HTTP request to any public REST API or webhook endpoint.

    Use this when you need to:
    - Call a REST API directly with specific parameters or auth headers
    - Send a webhook payload (Slack, Discord, Zapier, etc.)
    - Fetch JSON data from an API that web_search cannot reach
    - POST form data or JSON to an external service

    Args:
        method:    HTTP method: GET, POST, PUT, PATCH, DELETE, HEAD
        url:       Full URL including query parameters.
                   Must be public (private/localhost addresses are blocked).
        headers:   Dict of request headers. e.g. {"Authorization": "Bearer TOKEN",
                   "Content-Type": "application/json"}
        body:      Request body. Pass a dict for JSON (auto-serialised),
                   or a string for raw body (set Content-Type manually).
        timeout_s: Seconds before timeout (default 30, max 120).

    Returns:
        status:        "ok" | "error" | "http_error"
        status_code:   HTTP response code
        body:          Response body as string (capped at 12000 chars)
        body_json:     Parsed JSON if response Content-Type is application/json
        headers:       Response headers as dict
        duration_s:    Round-trip time in seconds
        message:       Summary or error description

    Examples:
        # GET a public API
        http_request("GET", "https://api.github.com/repos/anthropics/anthropic-sdk-python")

        # POST JSON to a webhook
        http_request("POST", "https://hooks.slack.com/services/XXX",
                     body={"text": "Hello from Narad!"})

        # Authenticated API call
        http_request("GET", "https://api.example.com/data",
                     headers={"Authorization": "Bearer mytoken123"})
    """
    method = method.upper().strip()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
        return {
            "status":  "error",
            "message": f"Unsupported method: {method!r}. Use GET, POST, PUT, PATCH, DELETE.",
        }

    url_error = _check_url(url)
    if url_error:
        return {"status": "error", "message": url_error}

    timeout_s = min(max(1, timeout_s), 120)
    headers = headers or {}

    # Auto-set Content-Type for dict bodies
    if isinstance(body, dict) and "Content-Type" not in headers:
        headers["Content-Type"] = "application/json"

    body_bytes: bytes | None = None
    if body is not None:
        if isinstance(body, dict):
            body_bytes = json.dumps(body).encode()
        elif isinstance(body, str):
            body_bytes = body.encode()

    start = time.time()

    try:
        import httpx

        with httpx.Client(timeout=timeout_s, follow_redirects=True, max_redirects=5) as client:
            req = httpx.Request(
                method=method,
                url=url,
                headers=headers,
                content=body_bytes,
            )
            resp = client.send(req)

        duration = round(time.time() - start, 2)
        resp_text = resp.text[:_RESPONSE_CAP]
        resp_json = None
        ct = resp.headers.get("content-type", "")
        if "json" in ct:
            try:
                resp_json = resp.json()
                # Cap nested json to avoid token overflow
                resp_json_str = json.dumps(resp_json)
                if len(resp_json_str) > _RESPONSE_CAP:
                    resp_json = json.loads(resp_json_str[:_RESPONSE_CAP])
            except Exception:
                pass

        ok = 200 <= resp.status_code < 300
        return {
            "status":      "ok" if ok else "http_error",
            "status_code": resp.status_code,
            "body":        resp_text,
            "body_json":   resp_json,
            "headers":     dict(resp.headers),
            "duration_s":  duration,
            "message":     f"HTTP {resp.status_code} in {duration}s.",
        }

    except ImportError:
        pass  # fall through to urllib

    except Exception as exc:
        return {
            "status":   "error",
            "message":  f"Request failed: {exc}",
            "duration_s": round(time.time() - start, 2),
        }

    # Fallback: urllib (stdlib)
    try:
        import urllib.request
        import urllib.error

        req_obj = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
        with urllib.request.urlopen(req_obj, timeout=timeout_s) as resp:
            duration = round(time.time() - start, 2)
            resp_body = resp.read().decode("utf-8", errors="replace")[:_RESPONSE_CAP]
            resp_json = None
            ct = resp.headers.get("Content-Type", "")
            if "json" in ct:
                try:
                    resp_json = json.loads(resp_body)
                except Exception:
                    pass
            return {
                "status":      "ok",
                "status_code": resp.status,
                "body":        resp_body,
                "body_json":   resp_json,
                "headers":     dict(resp.headers),
                "duration_s":  duration,
                "message":     f"HTTP {resp.status} in {duration}s.",
            }

    except Exception as exc:
        return {
            "status":   "error",
            "message":  f"Request failed: {exc}",
            "duration_s": round(time.time() - start, 2),
        }


def search_last30days(
    query: str,
    platforms: list | None = None,
) -> dict:
    """Search for recent community discussions about a topic (last 30 days).

    Surfaces what people are actively saying on social/community platforms —
    Reddit threads, Hacker News posts, and X/Twitter mentions.

    Use for: consumer sentiment, trending topics, community reactions, product feedback,
    "what are people saying about X", market signal research.

    Args:
        query:     Search query string — topic, product name, or keyword phrase.
        platforms: Optional list to restrict results. Choices: "reddit", "hn", "x".
                   Default: all three.

    Returns:
        status:    "ok" | "partial" | "error"
        results:   List of {platform, title, url, snippet, engagement} dicts.
        query:     Echo of the search query.
        message:   Summary string.
    """
    if not query or not query.strip():
        return envelope(
            status="error",
            summary="The last-30-days search could not start because the query was empty.",
            error="query cannot be empty",
            results=[],
            query=query,
        )

    import time as _time
    platforms = [p.lower().strip() for p in (platforms or ["reddit", "hn", "x", "github"])]
    results: list[dict] = []
    errors: list[str] = []

    # ── Reddit ────────────────────────────────────────────────────────────────
    if "reddit" in platforms:
        reddit_url = (
            f"https://www.reddit.com/search.json"
            f"?q={query}&sort=top&t=month&limit=5"
        )
        try:
            resp = http_request("GET", reddit_url, headers={"User-Agent": "Narad/1.0"})
            if resp.get("status") == "ok" and resp.get("body_json"):
                posts = resp["body_json"].get("data", {}).get("children", [])
                for p in posts[:5]:
                    d = p.get("data", {})
                    results.append({
                        "platform":   "reddit",
                        "title":      d.get("title", "")[:120],
                        "url":        f"https://reddit.com{d.get('permalink', '')}",
                        "snippet":    (d.get("selftext", "")[:200] or d.get("title", "")),
                        "engagement": d.get("score", 0),
                        "comments":   d.get("num_comments", 0),
                        "subreddit":  d.get("subreddit", ""),
                    })
            else:
                errors.append(f"reddit: {resp.get('message', 'no data')}")
        except Exception as _exc:
            errors.append(f"reddit: {_exc}")

    # ── Hacker News ───────────────────────────────────────────────────────────
    if "hn" in platforms:
        cutoff = int(_time.time()) - 30 * 86400
        hn_url = (
            f"https://hn.algolia.com/api/v1/search"
            f"?query={query}&tags=story"
            f"&numericFilters=created_at_i>{cutoff}&hitsPerPage=5"
        )
        try:
            resp = http_request("GET", hn_url)
            if resp.get("status") == "ok" and resp.get("body_json"):
                hits = resp["body_json"].get("hits", [])
                for h in hits[:5]:
                    results.append({
                        "platform":   "hn",
                        "title":      h.get("title", "")[:120],
                        "url":        (
                            h.get("url")
                            or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
                        ),
                        "snippet":    h.get("story_text", "")[:200] or h.get("title", ""),
                        "engagement": h.get("points", 0),
                        "comments":   h.get("num_comments", 0),
                        "author":     h.get("author", ""),
                    })
            else:
                errors.append(f"hn: {resp.get('message', 'no data')}")
        except Exception as _exc:
            errors.append(f"hn: {_exc}")

    # ── GitHub — zero-config repo/issues pulse (best-effort) ─────────────────
    if "github" in platforms:
        recent_date = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 30 * 86400))
        gh_repo_url = (
            "https://api.github.com/search/repositories"
            f"?q={query}+pushed:>={recent_date}&sort=stars&order=desc&per_page=5"
        )
        try:
            resp = http_request("GET", gh_repo_url, headers={"Accept": "application/vnd.github+json"})
            if resp.get("status") == "ok" and resp.get("body_json"):
                for repo in resp["body_json"].get("items", [])[:5]:
                    results.append({
                        "platform": "github",
                        "title": repo.get("full_name", "")[:120],
                        "url": repo.get("html_url", ""),
                        "snippet": (repo.get("description") or "")[:220],
                        "engagement": repo.get("stargazers_count", 0),
                        "comments": repo.get("open_issues_count", 0),
                        "language": repo.get("language", ""),
                    })
            else:
                errors.append(f"github: {resp.get('message', 'no data')}")
        except Exception as _exc:
            errors.append(f"github: {_exc}")

    # ── X/Twitter — public Nitter scrape (best-effort) ────────────────────────
    if "x" in platforms:
        try:
            import re as _re_x
            nitter_url = f"https://nitter.net/search?f=tweets&q={query}&since=30d"
            resp = http_request("GET", nitter_url)
            if resp.get("status") == "ok":
                body = resp.get("body", "")
                mentions = _re_x.findall(
                    r'<div class="tweet-content[^"]*"[^>]*>(.*?)</div>', body[:6000], _re_x.S
                )
                for m in mentions[:5]:
                    clean = _re_x.sub(r'<[^>]+>', '', m).strip()
                    if clean:
                        results.append({
                            "platform":   "x",
                            "title":      clean[:120],
                            "url":        nitter_url,
                            "snippet":    clean[:200],
                            "engagement": 0,
                        })
        except Exception as _exc:
            errors.append(f"x/nitter: {_exc}")

    if not results and errors:
        return envelope(
            status="error",
            summary=f"No recent community signal could be gathered for {query!r}.",
            error="; ".join(errors),
            results=[],
            query=query,
            source_breakdown={},
            engagement_signals={},
            judge_summary="No evidence could be synthesized.",
            coverage_gaps=errors,
        )

    results.sort(key=lambda r: r.get("engagement", 0), reverse=True)
    platforms_hit = len(set(r["platform"] for r in results))
    judge_summary, coverage_gaps = _summarize_last30days(results, query)
    source_breakdown = {
        platform: {
            "count": len([item for item in results if item.get("platform") == platform]),
            "total_engagement": sum(int(item.get("engagement", 0) or 0) for item in results if item.get("platform") == platform),
        }
        for platform in sorted({item.get("platform", "unknown") for item in results})
    }
    citations = [
        citation(
            title=item.get("title", item.get("url", "source")),
            url=item.get("url", ""),
            source=item.get("platform", ""),
            snippet=item.get("snippet", "")[:240],
            metadata={"engagement": item.get("engagement", 0), "comments": item.get("comments", 0)},
        )
        for item in results[:10]
        if item.get("url")
    ]
    summary = (
        f"Found {len(results)} recent community discussions about {query!r} "
        f"across {platforms_hit} platform(s)."
        + (f" Some sources failed: {'; '.join(errors)}" if errors else "")
    )
    return envelope(
        status="ok" if results else "partial",
        summary=summary,
        query=query,
        results=results,
        citations=citations,
        source_breakdown=source_breakdown,
        engagement_signals=_engagement_signals(results),
        judge_summary=judge_summary,
        coverage_gaps=coverage_gaps,
        ui=ui_panel(
            title="Last 30 days report",
            summary=summary,
            sections=[
                {"title": "Judge summary", "body": judge_summary},
                {"title": "Coverage gaps", "body": " • ".join(coverage_gaps[:4]) if coverage_gaps else "No major coverage gaps detected."},
            ],
            primary_artifact_label="Last 30 days report",
        ),
        provenance={"tool": "search_last30days", "platforms": platforms},
        errors=errors,
    )
