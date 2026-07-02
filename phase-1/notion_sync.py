"""
Notion sync bridge for Narad — Phase 14.

Syncs Narad's local data (memories, kanban, andon, sutras) to a user's Notion workspace.
All operations are best-effort and fire-and-forget — never raises, never blocks.

Setup:
  1. Set NOTION_API_TOKEN env var
  2. POST /notion/setup {"parent_page_id": "<page_id>"} to create databases
  3. Optionally POST /notion/sync to bulk-push existing data
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from narad_config import NARAD_HOME
    CONFIG_DIR = NARAD_HOME / "config"
except Exception:
    NARAD_HOME = Path.home() / ".narad"
    CONFIG_DIR = NARAD_HOME / "config"

NOTION_CONFIG_PATH = CONFIG_DIR / "notion_config.json"
_NOTION_ERRORS_PATH = NARAD_HOME / "notion_errors.jsonl"
_log = logging.getLogger("narad.notion")


def _log_sync_error(method: str, exc: Exception, context: str = "") -> None:
    """Append a structured error record to notion_errors.jsonl."""
    import json as _json
    try:
        record = {
            "ts":      datetime.now(timezone.utc).isoformat(),
            "method":  method,
            "error":   str(exc)[:300],
            "context": context[:120],
        }
        with _NOTION_ERRORS_PATH.open("a") as _f:
            _f.write(_json.dumps(record) + "\n")
    except Exception:
        pass

_AVATAR_NAMES = [
    "Matsya", "Rama", "Krishna", "Parashurama",
]
_AVATAR_COLOURS = {
    "Matsya": "green",
    "Rama": "gray",
    "Krishna": "green",
    "Parashurama": "brown",
}


class NotionSync:
    """Async Notion sync bridge. All methods are fire-and-forget — never raise."""

    def __init__(self) -> None:
        self.token: str | None = os.environ.get("NOTION_API_TOKEN")
        self._config: dict | None = None

    def enabled(self) -> bool:
        return bool(self.token)

    def _get_client(self):
        if not self.token:
            return None
        try:
            from notion_client import Client  # type: ignore
            return Client(auth=self.token)
        except Exception as exc:
            _log.debug("notion_client unavailable: %s", exc)
            return None

    def _load_config(self) -> dict:
        if self._config is None:
            try:
                if NOTION_CONFIG_PATH.exists():
                    self._config = json.loads(NOTION_CONFIG_PATH.read_text())
                else:
                    self._config = {}
            except Exception:
                self._config = {}
        return self._config

    def _save_config(self, cfg: dict) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            NOTION_CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
            self._config = cfg
        except Exception as exc:
            _log.warning("Failed to save notion_config.json: %s", exc)

    def _db_ids(self) -> dict[str, str]:
        return self._load_config().get("db_ids", {})

    # ── One-time setup ──────────────────────────────────────────────────────────

    def setup_workspace(self, parent_page_id: str) -> dict:
        """Create all 5 Narad databases under the given Notion page."""
        client = self._get_client()
        if not client:
            return {"error": "NOTION_API_TOKEN not set or notion-sdk not installed"}

        avatar_select_opts = [
            {"name": n, "color": _AVATAR_COLOURS.get(n, "default")}
            for n in _AVATAR_NAMES
        ]

        db_specs: dict[str, dict] = {
            "Narad Memory": {
                "Name":       {"title": {}},
                "Avatar":     {"select": {"options": avatar_select_opts}},
                "Memory":     {"rich_text": {}},
                "Type":       {"select": {"options": [
                    {"name": "decision", "color": "blue"},
                    {"name": "feature",  "color": "green"},
                    {"name": "goal",     "color": "yellow"},
                    {"name": "insight",  "color": "gray"},
                    {"name": "context",  "color": "brown"},
                ]}},
                "Created At": {"date": {}},
                "Session ID": {"rich_text": {}},
            },
            "Narad Kanban": {
                "Title":        {"title": {}},
                "Owner":        {"select": {"options": avatar_select_opts}},
                "Status":       {"status": {}},
                "Session ID":   {"rich_text": {}},
                "Started At":   {"date": {}},
                "Completed At": {"date": {}},
                "Result":       {"rich_text": {}},
            },
            "Narad Andon": {
                "Title":        {"title": {}},
                "Avatar":       {"select": {"options": avatar_select_opts}},
                "Trigger":      {"select": {"options": [
                    {"name": "EMPTY_RESULT", "color": "red"},
                    {"name": "TIMEOUT",      "color": "orange"},
                    {"name": "CONNECTION",   "color": "yellow"},
                    {"name": "TOOL_ERROR",   "color": "brown"},
                ]}},
                "Session ID":   {"rich_text": {}},
                "Task Preview": {"rich_text": {}},
                "Timestamp":    {"date": {}},
            },
            "Narad Sutras": {
                "Title":      {"title": {}},
                "Avatar":     {"select": {"options": avatar_select_opts}},
                "Pattern":    {"rich_text": {}},
                "Score":      {"number": {"format": "number"}},
                "Status":     {"select": {"options": [
                    {"name": "active",   "color": "green"},
                    {"name": "pending",  "color": "yellow"},
                    {"name": "accepted", "color": "blue"},
                    {"name": "reverted", "color": "gray"},
                ]}},
                "Sutra ID":   {"rich_text": {}},
                "Created At": {"date": {}},
            },
            "Narad Sankalpas": {
                "Title":        {"title": {}},
                "Avatar":       {"select": {"options": avatar_select_opts + [{"name": "__global__"}]}},
                "Pattern Type": {"select": {"options": [
                    {"name": "style"}, {"name": "preference"},
                    {"name": "domain"}, {"name": "workflow"},
                ]}},
                "Content":      {"rich_text": {}},
                "Confidence":   {"number": {"format": "percent"}},
                "Status":       {"select": {"options": [
                    {"name": "active",   "color": "green"},
                    {"name": "pending",  "color": "yellow"},
                    {"name": "accepted", "color": "blue"},
                    {"name": "reverted", "color": "gray"},
                ]}},
                "Sankalpa ID":  {"rich_text": {}},
            },
        }

        created: dict[str, str] = {}
        errors: list[str] = []

        for db_name, properties in db_specs.items():
            try:
                result = client.databases.create(
                    parent={"type": "page_id", "page_id": parent_page_id},
                    title=[{"type": "text", "text": {"content": db_name}}],
                    properties=properties,
                )
                created[db_name] = result["id"]
                _log.info("Created Notion DB '%s': %s", db_name, result["id"])
            except Exception as exc:
                _log.error("Failed to create Notion DB '%s': %s", db_name, exc)
                errors.append(f"{db_name}: {exc}")

        cfg = self._load_config()
        cfg["db_ids"] = created
        cfg["parent_page_id"] = parent_page_id
        cfg["setup_at"] = datetime.now(timezone.utc).isoformat()
        self._save_config(cfg)

        return {"created": created, "errors": errors}

    # ── Push methods (all async, all fire-and-forget) ───────────────────────────

    async def push_memory(
        self,
        memory_id: str,
        user_id: str,
        avatar: str,
        memory_text: str,
        created_at: str,
        memory_type: str = "insight",
    ) -> None:
        try:
            client = self._get_client()
            db_id = self._db_ids().get("Narad Memory")
            if not client or not db_id:
                return
            title = memory_text.replace("\n", " ")[:80]
            client.pages.create(
                parent={"database_id": db_id},
                properties={
                    "Name":       {"title": [{"text": {"content": title}}]},
                    "Avatar":     {"select": {"name": avatar}},
                    "Memory":     {"rich_text": [{"text": {"content": memory_text[:2000]}}]},
                    "Type":       {"select": {"name": memory_type}},
                    "Created At": {"date": {"start": created_at[:10]}},
                    "Session ID": {"rich_text": [{"text": {"content": user_id}}]},
                },
            )
        except Exception as exc:
            _log.warning("push_memory failed: %s", exc)
            _log_sync_error("push_memory", exc, context=f"user={user_id} avatar={avatar}")

    async def push_kanban_step(
        self,
        session_id: str,
        step_id: int,
        title: str,
        owner: str,
        status: str,
        started_at: str | None,
        completed_at: str | None,
        result_digest: str | None,
    ) -> None:
        try:
            client = self._get_client()
            db_id = self._db_ids().get("Narad Kanban")
            if not client or not db_id:
                return
            status_map = {
                "backlog": "Not started", "in_progress": "In progress",
                "review": "In progress", "done": "Done", "blocked": "Done",
            }
            props: dict[str, Any] = {
                "Title":      {"title": [{"text": {"content": title[:100]}}]},
                "Owner":      {"select": {"name": owner}},
                "Status":     {"status": {"name": status_map.get(status, "Not started")}},
                "Session ID": {"rich_text": [{"text": {"content": session_id}}]},
            }
            if started_at:
                props["Started At"] = {"date": {"start": started_at[:10]}}
            if completed_at:
                props["Completed At"] = {"date": {"start": completed_at[:10]}}
            if result_digest:
                props["Result"] = {"rich_text": [{"text": {"content": result_digest[:500]}}]}
            client.pages.create(parent={"database_id": db_id}, properties=props)
        except Exception as exc:
            _log.warning("push_kanban_step failed: %s", exc)
            _log_sync_error("push_kanban_step", exc, context=f"session={session_id} owner={owner}")

    async def push_andon(
        self,
        event_id: str,
        avatar: str,
        trigger: str,
        session_id: str,
        task_preview: str,
        result_preview: str,
        ts: str,
    ) -> None:
        try:
            client = self._get_client()
            db_id = self._db_ids().get("Narad Andon")
            if not client or not db_id:
                return
            client.pages.create(
                parent={"database_id": db_id},
                properties={
                    "Title":        {"title": [{"text": {"content": f"{avatar} — {trigger}"}}]},
                    "Avatar":       {"select": {"name": avatar}},
                    "Trigger":      {"select": {"name": trigger}},
                    "Session ID":   {"rich_text": [{"text": {"content": session_id}}]},
                    "Task Preview": {"rich_text": [{"text": {"content": task_preview[:500]}}]},
                    "Timestamp":    {"date": {"start": ts[:10]}},
                },
            )
        except Exception as exc:
            _log.warning("push_andon failed: %s", exc)
            _log_sync_error("push_andon", exc, context=f"session={session_id} avatar={avatar}")

    async def push_sutra(
        self,
        sutra_id: str,
        avatar: str,
        query: str,
        result_snippet: str,
        score: float,
        status: str,
        created_at: str,
    ) -> None:
        try:
            client = self._get_client()
            db_id = self._db_ids().get("Narad Sutras")
            if not client or not db_id:
                return
            client.pages.create(
                parent={"database_id": db_id},
                properties={
                    "Title":      {"title": [{"text": {"content": query[:80]}}]},
                    "Avatar":     {"select": {"name": avatar}},
                    "Pattern":    {"rich_text": [{"text": {"content": result_snippet[:500]}}]},
                    "Score":      {"number": round(float(score), 3)},
                    "Status":     {"select": {"name": status}},
                    "Sutra ID":   {"rich_text": [{"text": {"content": sutra_id}}]},
                    "Created At": {"date": {"start": created_at[:10]}},
                },
            )
        except Exception as exc:
            _log.warning("push_sutra failed: %s", exc)
            _log_sync_error("push_sutra", exc, context=f"sutra_id={sutra_id} avatar={avatar}")

    async def push_wiki_page(
        self,
        user_id: str,
        entity_type: str,
        markdown_content: str,
    ) -> None:
        try:
            client = self._get_client()
            db_id = self._db_ids().get("Narad Memory")
            if not client or not db_id:
                return
            first_line = markdown_content.strip().split("\n")[0].lstrip("# ").strip()
            client.pages.create(
                parent={"database_id": db_id},
                properties={
                    "Name":       {"title": [{"text": {"content": first_line[:80]}}]},
                    "Avatar":     {"select": {"name": "Narad"}},
                    "Memory":     {"rich_text": [{"text": {"content": markdown_content[:2000]}}]},
                    "Type":       {"select": {"name": entity_type}},
                    "Created At": {"date": {"start": datetime.now(timezone.utc).isoformat()[:10]}},
                    "Session ID": {"rich_text": [{"text": {"content": user_id}}]},
                },
            )
        except Exception as exc:
            _log.warning("push_wiki_page failed: %s", exc)
            _log_sync_error("push_wiki_page", exc, context=f"user={user_id} type={entity_type}")

    # ── Batch sync ──────────────────────────────────────────────────────────────

    async def sync_all(self, user_id: str = "default") -> dict:
        """Full re-sync: push existing sutras and andon log to Notion."""
        if not self.enabled():
            return {"error": "NOTION_API_TOKEN not set"}

        results: dict[str, Any] = {"synced": {}, "errors": []}

        # Sync sutras
        try:
            from sutra_engine import get_all_sutras  # type: ignore
            sutras = get_all_sutras()
            count = 0
            for s in sutras:
                try:
                    await self.push_sutra(
                        s.get("id", ""), s.get("avatar", ""),
                        s.get("query", ""), s.get("result", ""),
                        float(s.get("score", 0.0)), s.get("status", "active"),
                        s.get("ts", ""),
                    )
                    count += 1
                except Exception:
                    pass
            results["synced"]["sutras"] = count
        except Exception as exc:
            results["errors"].append(f"sutras: {exc}")

        # Sync andon log
        try:
            from andon import load_andon_log  # type: ignore
            events = load_andon_log(limit=200)
            count = 0
            for e in events:
                try:
                    await self.push_andon(
                        e.get("id", ""), e.get("avatar", ""), e.get("trigger", ""),
                        e.get("session_id", ""), e.get("task_preview", ""),
                        e.get("result_preview", ""), e.get("ts", ""),
                    )
                    count += 1
                except Exception:
                    pass
            results["synced"]["andon_events"] = count
        except Exception as exc:
            results["errors"].append(f"andon: {exc}")

        cfg = self._load_config()
        cfg["last_sync"] = datetime.now(timezone.utc).isoformat()
        self._save_config(cfg)
        return results

    def get_status(self) -> dict:
        """Return sync status including recent push errors."""
        cfg = self._load_config()
        client = self._get_client()
        recent_errors: list[dict] = []
        try:
            if _NOTION_ERRORS_PATH.exists():
                lines = _NOTION_ERRORS_PATH.read_text().splitlines()
                recent_errors = [json.loads(l) for l in lines[-5:] if l]
        except Exception:
            pass
        degraded = len(recent_errors) > 0
        return {
            "enabled":        self.enabled(),
            "sdk_available":  client is not None,
            "last_sync":      cfg.get("last_sync"),
            "db_ids":         cfg.get("db_ids", {}),
            "parent_page_id": cfg.get("parent_page_id"),
            "workspace_url":  "https://notion.so" if cfg.get("parent_page_id") else None,
            "sync_degraded":  degraded,
            "recent_errors":  recent_errors,
        }
