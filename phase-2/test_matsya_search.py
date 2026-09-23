from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock, patch

_root = next(path for path in Path(__file__).resolve().parents if (path / "narad_paths.py").exists())
sys.path[:0] = [str(_root)]
import matsya_search
import web_enrichment_skill

import narad_paths  # noqa: F401


def _response(payload: dict, status: int = 200) -> Mock:
    response = Mock()
    response.ok = status < 400
    response.status_code = status
    response.reason = "OK" if status < 400 else "Bad Request"
    response.json.return_value = payload
    return response


def test_exa_search_uses_highlights_freshness_deep_schema_and_grounding() -> None:
    schema = {
        "type": "object",
        "properties": {"roles": {"type": "array", "items": {"type": "string"}}},
        "required": ["roles"],
    }
    payload = {
        "requestId": "req_1",
        "results": [{
            "title": "Synthetic role",
            "url": "https://example.com/role",
            "highlights": ["Remote senior role"],
            "publishedDate": "2026-09-10",
        }],
        "output": {
            "content": {"roles": ["Synthetic role"]},
            "grounding": [{
                "field": "roles[0]",
                "confidence": "high",
                "citations": [{"url": "https://example.com/role", "title": "Synthetic role"}],
            }],
        },
        "costDollars": {"total": 0.012},
    }
    with patch.object(matsya_search.requests, "post", return_value=_response(payload)) as post:
        result = matsya_search.search_exa(
            "remote product roles",
            "exa-test-key",
            search_type="deep",
            max_age_hours=0,
            output_schema=schema,
            additional_queries=["product leadership remote India"],
        )

    sent = post.call_args.kwargs
    assert sent["headers"]["Authorization"] == "Bearer exa-test-key"
    assert sent["json"]["contents"]["highlights"] is True
    assert sent["json"]["contents"]["maxAgeHours"] == 0
    assert sent["json"]["type"] == "deep"
    assert sent["json"]["outputSchema"] == schema
    assert result["status"] == "ok"
    assert result["answer"] == {"roles": ["Synthetic role"]}
    assert result["grounding"][0]["confidence"] == "high"


def test_web_search_prefers_exa_and_returns_token_bounded_snippet(monkeypatch) -> None:
    monkeypatch.setenv("EXA_API_KEY", "exa-test-key")
    exa_result = {
        "status": "ok",
        "source": "exa",
        "answer": "",
        "results": [{"title": "A", "url": "https://example.com", "snippet": "short"}],
    }
    with patch.object(matsya_search, "search_exa", return_value=exa_result) as exa:
        result = matsya_search.web_search("current signal", max_results=3)

    assert result["source"] == "exa"
    exa.assert_called_once()


def test_exa_search_bounds_highlights_and_full_text_across_results() -> None:
    payload = {
        "requestId": "req_bounded",
        "results": [
            {
                "title": "A" * 800,
                "url": "https://example.com/a",
                "highlights": ["h" * 5000] * 8,
                "text": "a" * 9000,
                "author": "x" * 800,
            },
            {
                "title": "B",
                "url": "https://example.com/b",
                "highlights": ["j" * 5000] * 8,
                "text": "b" * 9000,
            },
        ],
    }
    with patch.object(matsya_search.requests, "post", return_value=_response(payload)) as post:
        result = matsya_search.search_exa(
            "bounded evidence",
            "exa-test-key",
            max_results=2,
            include_text=True,
            text_max_characters=5000,
        )

    assert post.call_args.kwargs["json"]["contents"]["text"]["maxCharacters"] == 2500
    assert sum(len(item["text"]) for item in result["results"]) <= 5000
    assert sum(len(value) for item in result["results"] for value in item["highlights"]) <= 3200
    assert all(len(item["highlights"]) <= 4 for item in result["results"])
    assert len(result["results"][0]["title"]) == 400
    assert len(result["results"][0]["author"]) == 300


def test_exa_contents_writes_bounded_lossless_artifact(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EXA_API_KEY", "exa-test-key")
    raw = {
        "requestId": "contents_1",
        "results": [{
            "title": "Official guide",
            "url": "https://example.com/guide",
            "text": "Exact source text",
            "highlights": ["Exact source"],
        }],
        "statuses": [{"id": "https://example.com/guide", "status": "success", "source": "cached"}],
        "costDollars": {"total": 0.001},
    }
    out_dir = tmp_path / "artifact"
    out_dir.mkdir()
    with patch.object(web_enrichment_skill.requests, "post", return_value=_response(raw)) as post, patch.object(
        web_enrichment_skill, "ensure_artifact_dir", return_value=out_dir
    ):
        result = web_enrichment_skill.exa_contents(
            ["https://example.com/guide"],
            question="What is exact?",
            max_characters=5000,
            max_age_hours=24,
        )

    assert result["status"] == "ok"
    assert result["results"][0]["text"] == "Exact source text"
    assert (out_dir / "extracted-pages.md").read_text(encoding="utf-8").endswith("Exact source text\n")
    assert post.call_args.kwargs["json"]["text"]["maxCharacters"] == 5000
    assert post.call_args.kwargs["json"]["maxAgeHours"] == 24
