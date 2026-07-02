"""
Academic and ML research retrieval tools for Matsya.

All functions are synchronous, return plain dicts, and rely only on stdlib
plus the existing http_request tool from phase-8/http_skill.py.

Tools:
    search_arxiv       — arXiv preprints (free, no auth)
    search_papers      — Semantic Scholar (free, optional API key for higher rate limits)
    search_hf_papers   — HuggingFace Papers (free, no auth)
    search_hf_models   — HuggingFace Hub models (free, no auth)
    query_deepwiki     — DeepWiki indexed GitHub repo docs (free, no auth)
"""

from __future__ import annotations

import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

# ── Reuse http_request from phase-8 (path registered by narad_paths) ─────────
try:
    from http_skill import http_request as _http_request
except Exception:
    def _http_request(method, url, headers=None, body=None, timeout_s=30):  # type: ignore
        return {"status": "error", "message": "http_skill unavailable", "body": "", "body_json": None}

_ABSTRACT_CAP = 600
_ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


# ── search_arxiv ─────────────────────────────────────────────────────────────

def search_arxiv(query: str, max_results: int = 10, category: str | None = None) -> dict:
    """Search arXiv for academic preprints.

    Use for: cutting-edge ML/AI papers, technical research, very recent work (last 12 months).
    Returns papers with title, authors, abstract, PDF URL, arXiv ID, and categories.

    Args:
        query:       Search terms. Supports AND/OR boolean.
                     e.g. "diffusion models AND image generation"
        max_results: Number of papers to return (default 10, max 25).
        category:    Optional arXiv category filter.
                     e.g. "cs.LG" (ML), "cs.AI" (AI), "cs.CL" (NLP), "stat.ML", "cs.CV"
    """
    max_results = min(int(max_results), 25)
    search_q = query
    if category:
        search_q = f"cat:{category}+AND+{query}"
    encoded = urllib.parse.quote(search_q)
    url = (
        f"https://export.arxiv.org/api/query"
        f"?search_query={encoded}&start=0&max_results={max_results}"
        f"&sortBy=relevance&sortOrder=descending"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Narad-Research/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            xml_bytes = resp.read()
        root = ET.fromstring(xml_bytes)

        total_el = root.find("{http://a9.com/-/spec/opensearch/1.1/}totalResults")
        total = int(total_el.text) if total_el is not None and total_el.text else 0

        papers = []
        for entry in root.findall("atom:entry", _ARXIV_NS):
            title_el = entry.find("atom:title", _ARXIV_NS)
            title = title_el.text.strip().replace("\n", " ") if title_el is not None else ""

            summary_el = entry.find("atom:summary", _ARXIV_NS)
            abstract = summary_el.text.strip().replace("\n", " ") if summary_el is not None else ""
            abstract = abstract[:_ABSTRACT_CAP]

            published_el = entry.find("atom:published", _ARXIV_NS)
            published = published_el.text[:10] if published_el is not None else ""

            authors = [
                a.find("atom:name", _ARXIV_NS).text
                for a in entry.findall("atom:author", _ARXIV_NS)
                if a.find("atom:name", _ARXIV_NS) is not None
            ]

            arxiv_id_el = entry.find("atom:id", _ARXIV_NS)
            raw_id = arxiv_id_el.text.strip() if arxiv_id_el is not None else ""
            arxiv_id = raw_id.split("/")[-1]

            pdf_url = None
            for link in entry.findall("atom:link", _ARXIV_NS):
                if link.get("title") == "pdf":
                    pdf_url = link.get("href")
                    break

            cats = [
                c.get("term", "")
                for c in entry.findall("arxiv:primary_category", _ARXIV_NS)
            ]

            papers.append({
                "arxiv_id":   arxiv_id,
                "title":      title,
                "authors":    authors[:6],
                "abstract":   abstract,
                "published":  published,
                "pdf_url":    pdf_url,
                "categories": cats,
            })

        return {"status": "ok", "query": query, "total_found": total, "papers": papers}

    except Exception as exc:
        return {"status": "error", "query": query, "message": str(exc), "papers": []}


# ── search_papers ─────────────────────────────────────────────────────────────

def search_papers(query: str, max_results: int = 10) -> dict:
    """Search Semantic Scholar for papers with citation counts and open-access PDFs.

    Use for: finding highly-cited papers, checking research impact, open-access sources.
    Returns title, abstract, citation count, year, and open-access PDF URL when available.

    Args:
        query:       Search terms.
        max_results: Number of results (default 10, max 20).
    """
    max_results = min(int(max_results), 20)
    encoded = urllib.parse.quote(query)
    fields = "title,abstract,year,citationCount,openAccessPdf,authors,externalIds"
    url = (
        f"https://api.semanticscholar.org/graph/v1/paper/search"
        f"?query={encoded}&limit={max_results}&fields={fields}"
    )
    headers: dict = {}
    api_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")
    if api_key:
        headers["x-api-key"] = api_key

    try:
        result = _http_request("GET", url, headers=headers or None, timeout_s=20)
        if result.get("status") != "ok":
            return {"status": "error", "query": query, "message": result.get("message", "request failed"), "papers": []}

        data = (result.get("body_json") or {}).get("data", [])
        papers = []
        for item in data:
            abstract = (item.get("abstract") or "")[:_ABSTRACT_CAP]
            authors = [a.get("name", "") for a in (item.get("authors") or [])[:5]]
            open_pdf = (item.get("openAccessPdf") or {}).get("url")
            arxiv_id = (item.get("externalIds") or {}).get("ArXiv")
            papers.append({
                "paper_id":       item.get("paperId", ""),
                "title":          item.get("title", ""),
                "authors":        authors,
                "year":           item.get("year"),
                "abstract":       abstract,
                "citation_count": item.get("citationCount", 0),
                "pdf_url":        open_pdf,
                "arxiv_id":       arxiv_id,
            })
        return {"status": "ok", "query": query, "papers": papers}

    except Exception as exc:
        return {"status": "error", "query": query, "message": str(exc), "papers": []}


# ── search_hf_papers ─────────────────────────────────────────────────────────

def search_hf_papers(query: str, max_results: int = 10) -> dict:
    """Search HuggingFace Papers for community-highlighted ML research.

    Use for: trending/popular papers with HuggingFace implementations or demos,
    papers that have community traction and upvotes.

    Args:
        query:       Search terms.
        max_results: Number of results (default 10, max 20).
    """
    max_results = min(int(max_results), 20)
    encoded = urllib.parse.quote(query)
    url = f"https://huggingface.co/api/papers/search?q={encoded}&limit={max_results}"

    try:
        result = _http_request("GET", url, timeout_s=15)
        if result.get("status") != "ok":
            return {"status": "error", "query": query, "message": result.get("message", "request failed"), "papers": []}

        raw = result.get("body_json") or []
        if isinstance(raw, dict):
            raw = raw.get("papers", raw.get("data", []))

        papers = []
        for item in raw:
            arxiv_id = item.get("id") or item.get("arxiv_id", "")
            title = item.get("title", "")
            authors = [
                a.get("name", a) if isinstance(a, dict) else str(a)
                for a in (item.get("authors") or [])[:6]
            ]
            abstract = (item.get("summary") or item.get("abstract") or "")[:_ABSTRACT_CAP]
            upvotes = item.get("upvotes", 0)
            published = (item.get("publishedAt") or item.get("published", ""))[:10]
            hf_url = f"https://huggingface.co/papers/{arxiv_id}" if arxiv_id else ""
            papers.append({
                "arxiv_id":  arxiv_id,
                "title":     title,
                "authors":   authors,
                "abstract":  abstract,
                "upvotes":   upvotes,
                "published": published,
                "hf_url":    hf_url,
            })
        return {"status": "ok", "query": query, "papers": papers}

    except Exception as exc:
        return {"status": "error", "query": query, "message": str(exc), "papers": []}


# ── search_hf_models ─────────────────────────────────────────────────────────

def search_hf_models(query: str, task: str | None = None, max_results: int = 10) -> dict:
    """Search HuggingFace Hub for pre-trained models sorted by download count.

    Use for: finding SOTA models for a specific capability, comparing model popularity,
    discovering open-weight implementations.

    Args:
        query:       Model name, architecture, or capability keywords.
        task:        Optional task filter. e.g. "text-generation", "image-classification",
                     "text-to-image", "automatic-speech-recognition", "question-answering",
                     "text-to-speech", "image-to-text", "translation"
        max_results: Number of results (default 10, max 20).
    """
    max_results = min(int(max_results), 20)
    encoded = urllib.parse.quote(query)
    url = (
        f"https://huggingface.co/api/models"
        f"?search={encoded}&sort=downloads&direction=-1&limit={max_results}"
    )
    if task:
        url += f"&pipeline_tag={urllib.parse.quote(task)}"

    try:
        result = _http_request("GET", url, timeout_s=15)
        if result.get("status") != "ok":
            return {"status": "error", "query": query, "message": result.get("message", "request failed"), "models": []}

        raw = result.get("body_json") or []
        if isinstance(raw, dict):
            raw = raw.get("models", [])

        models = []
        for item in raw:
            model_id = item.get("modelId") or item.get("id", "")
            models.append({
                "model_id": model_id,
                "task":     item.get("pipeline_tag"),
                "downloads": item.get("downloads", 0),
                "likes":    item.get("likes", 0),
                "tags":     item.get("tags", [])[:10],
                "hf_url":   f"https://huggingface.co/{model_id}",
                "updated":  (item.get("lastModified") or "")[:10],
            })
        return {"status": "ok", "query": query, "models": models}

    except Exception as exc:
        return {"status": "error", "query": query, "message": str(exc), "models": []}


# ── query_deepwiki ────────────────────────────────────────────────────────────

def query_deepwiki(repo_url: str, question: str) -> dict:
    """Ask a question about a GitHub repository using DeepWiki's indexed documentation.

    Use for: understanding codebases, architecture questions about open-source projects,
    "how does X work in repo Y", implementation details from source/docs.

    Args:
        repo_url: GitHub repository URL or "owner/repo" shorthand.
                  e.g. "https://github.com/huggingface/transformers"
                       or "huggingface/transformers"
        question: Natural-language question about the repository.
                  e.g. "How is the attention mechanism implemented?"
    """
    # Normalise repo_url to full GitHub URL
    rurl = repo_url.strip()
    if rurl.startswith("https://github.com/"):
        full_url = rurl
        owner_repo = rurl[len("https://github.com/"):]
    elif rurl.startswith("github.com/"):
        full_url = "https://" + rurl
        owner_repo = rurl[len("github.com/"):]
    else:
        owner_repo = rurl.strip("/")
        full_url = f"https://github.com/{owner_repo}"

    # Try MCP JSON-RPC
    try:
        mcp_body = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "ask_question",
                "arguments": {"repoUrl": full_url, "question": question},
            },
            "id": 1,
        }
        result = _http_request(
            "POST",
            "https://mcp.deepwiki.com/mcp",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            body=mcp_body,
            timeout_s=30,
        )
        if result.get("status") == "ok" and result.get("body_json"):
            rjson = result["body_json"]
            # MCP result schema: {result: {content: [{type:"text", text:"..."}]}}
            content = (
                (rjson.get("result") or {}).get("content") or []
            )
            answer = next(
                (c.get("text", "") for c in content if c.get("type") == "text"), ""
            )
            if answer:
                return {"status": "ok", "repo_url": full_url, "question": question,
                        "answer": answer, "source": "deepwiki-mcp"}
    except Exception:
        pass

    # Fallback: fetch DeepWiki page
    try:
        page_url = f"https://deepwiki.com/{owner_repo}"
        result = _http_request("GET", page_url, timeout_s=20)
        body = result.get("body", "")
        if result.get("status") == "ok" and body:
            return {
                "status": "ok",
                "repo_url": full_url,
                "question": question,
                "answer": body[:2000],
                "source": "deepwiki-page",
            }
    except Exception:
        pass

    return {
        "status": "unavailable",
        "repo_url": full_url,
        "question": question,
        "answer": "DeepWiki could not be reached. Try browse_url on https://deepwiki.com/" + owner_repo,
        "source": "none",
    }
