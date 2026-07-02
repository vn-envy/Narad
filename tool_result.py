"""
Shared tool result envelope helpers for Narad's upgraded tool contracts.

The goal is not to replace every legacy skill return shape at once. Instead,
new and fortified tools emit a standard envelope so the frontend can render
stateful artifacts, citations, and confirmation gates without scraping chat text.
"""

from __future__ import annotations

import html
import json
import os
import uuid
from pathlib import Path
from typing import Any

from narad_config import ARTIFACTS_DIR

SERVER_MEDIA_BASE = os.environ.get("MEDIA_URL_BASE", "http://localhost:8000/media").rstrip("/")


def ensure_artifact_dir(prefix: str) -> Path:
    run_id = f"{prefix}_{uuid.uuid4().hex[:8]}"
    out_dir = ARTIFACTS_DIR / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def media_url_for(path: str | Path | None) -> str | None:
    if not path:
        return None
    p = Path(path).resolve()
    try:
        rel = p.relative_to(ARTIFACTS_DIR.resolve())
    except Exception:
        return None
    return f"{SERVER_MEDIA_BASE}/{rel.as_posix()}"


def artifact(
    *,
    type: str,
    label: str,
    path: str | Path | None = None,
    url: str | None = None,
    description: str = "",
    mime_type: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_url = url or media_url_for(path)
    payload: dict[str, Any] = {
        "type": type,
        "label": label,
        "description": description,
    }
    if path is not None:
        payload["path"] = str(path)
    if resolved_url:
        payload["url"] = resolved_url
    if mime_type:
        payload["mime_type"] = mime_type
    if metadata:
        payload["metadata"] = metadata
    return payload


def citation(
    *,
    title: str,
    url: str,
    source: str = "",
    snippet: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": title,
        "url": url,
        "source": source,
        "snippet": snippet,
    }
    if metadata:
        payload["metadata"] = metadata
    return payload


def ui_panel(
    *,
    title: str,
    summary: str,
    sections: list[dict[str, str]] | None = None,
    primary_artifact_label: str | None = None,
    tone: str = "tool-result",
) -> dict[str, Any]:
    return {
        "kind": "panel",
        "title": title,
        "summary": summary,
        "sections": sections or [],
        "primary_artifact_label": primary_artifact_label,
        "tone": tone,
    }


def envelope(
    *,
    status: str,
    summary: str,
    artifacts: list[dict[str, Any]] | None = None,
    citations: list[dict[str, Any]] | None = None,
    ui: dict[str, Any] | None = None,
    provenance: dict[str, Any] | list[dict[str, Any]] | None = None,
    requires_confirmation: bool = False,
    error: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "summary": summary,
        "artifacts": artifacts or [],
        "citations": citations or [],
        "ui": ui,
        "provenance": provenance or {},
        "requires_confirmation": requires_confirmation,
    }
    if error:
        payload["error"] = error
    payload.update(extra)
    return payload


def is_tool_envelope(value: Any) -> bool:
    return isinstance(value, dict) and "status" in value and "summary" in value


def write_html_surface(
    *,
    out_dir: Path,
    title: str,
    summary: str,
    sections: list[dict[str, str]] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    citations: list[dict[str, Any]] | None = None,
    filename: str = "index.html",
) -> dict[str, Any]:
    sections = sections or []
    artifacts = artifacts or []
    citations = citations or []

    def _li(text: str) -> str:
        return f"<li>{html.escape(text)}</li>"

    section_html = "".join(
        f"<section><h2>{html.escape(sec.get('title', 'Section'))}</h2>"
        f"<p>{html.escape(sec.get('body', ''))}</p></section>"
        for sec in sections
    )
    artifact_html = "".join(
        "<li>"
        f"<strong>{html.escape(item.get('label', item.get('type', 'artifact')))}</strong>"
        + (
            f' — <a href="{html.escape(item["url"])}" target="_blank" rel="noopener noreferrer">open</a>'
            if item.get("url") else ""
        )
        + (
            f"<div>{html.escape(item.get('description', ''))}</div>"
            if item.get("description") else ""
        )
        + "</li>"
        for item in artifacts
    )
    citation_html = "".join(
        "<li>"
        f'<a href="{html.escape(item.get("url", ""))}" target="_blank" rel="noopener noreferrer">'
        f'{html.escape(item.get("title", item.get("url", "source")))}'
        "</a>"
        + (
            f" <span>— {html.escape(item.get('source', ''))}</span>"
            if item.get("source") else ""
        )
        + (
            f"<div>{html.escape(item.get('snippet', ''))}</div>"
            if item.get("snippet") else ""
        )
        + "</li>"
        for item in citations
    )
    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --paper: #fcfaf2;
      --ink: #2d2a26;
      --muted: rgba(45,42,38,0.68);
      --line: rgba(45,42,38,0.14);
      --accent: #c2410c;
      --accent-2: #065f46;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 32px;
      background: linear-gradient(180deg, #fcfaf2 0%, #f5efe2 100%);
      color: var(--ink);
      font-family: ui-serif, Georgia, serif;
      line-height: 1.55;
    }}
    main {{
      max-width: 980px;
      margin: 0 auto;
      background: rgba(255,255,255,0.55);
      border: 1px solid var(--line);
      border-radius: 20px;
      overflow: hidden;
      box-shadow: 0 24px 80px rgba(45,42,38,0.08);
    }}
    header {{
      padding: 28px 28px 18px;
      border-bottom: 1px solid var(--line);
      background: linear-gradient(135deg, rgba(194,65,12,0.07), rgba(6,95,70,0.05));
    }}
    h1 {{ margin: 0 0 10px; font-size: 30px; }}
    p.lead {{ margin: 0; color: var(--muted); }}
    .grid {{
      display: grid;
      grid-template-columns: 1.3fr 0.9fr;
      gap: 24px;
      padding: 24px 28px 32px;
    }}
    section {{
      padding: 16px 0;
      border-bottom: 1px solid var(--line);
    }}
    section:last-child {{ border-bottom: none; }}
    h2 {{
      margin: 0 0 8px;
      font-size: 13px;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--accent);
      font-family: ui-monospace, SFMono-Regular, monospace;
    }}
    ul {{ margin: 0; padding-left: 18px; }}
    li {{ margin: 0 0 10px; color: var(--muted); }}
    a {{ color: var(--accent-2); }}
    @media (max-width: 840px) {{
      body {{ padding: 16px; }}
      .grid {{ grid-template-columns: 1fr; padding: 20px; }}
      header {{ padding: 22px 20px 14px; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>{html.escape(title)}</h1>
      <p class="lead">{html.escape(summary)}</p>
    </header>
    <div class="grid">
      <div>
        {section_html or f"<section><h2>Overview</h2><p>{html.escape(summary)}</p></section>"}
      </div>
      <div>
        <section><h2>Artifacts</h2><ul>{artifact_html or _li("No attached artifacts.")}</ul></section>
        <section><h2>Sources</h2><ul>{citation_html or _li("No citations attached.")}</ul></section>
      </div>
    </div>
  </main>
</body>
</html>
"""
    path = out_dir / filename
    path.write_text(body, encoding="utf-8")
    return artifact(
        type="html",
        label=title,
        path=path,
        mime_type="text/html",
        description="Rendered HTML workspace for this tool result.",
    )


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
