"""
Phase 1 FastAPI SSE server (+ Smriti memory + Yantra observability).

SSE event taxonomy (locked):
  avatar_start | avatar_done | narad_synthesis | done | error

New endpoints:
  GET /trace/{session_id}  — structured trace for a completed session
  GET /capabilities       — runtime architecture and capability contract
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import subprocess
import sys
import traceback
import uuid


# ── SSL: combined CA bundle for corporate networks with SSL inspection ─────────
# Cisco Umbrella (and similar proxies) re-sign HTTPS traffic with their own CA.
# That CA is in the macOS system keychain but NOT in certifi, so Python fails.
# Fix: build a combined bundle (certifi + proxy CAs extracted from keychain),
# then patch all three SSL layers so every HTTP library picks it up.
def _build_ca_bundle() -> str:
    """Return path to combined CA bundle, building it once if needed."""
    import pathlib as _pl
    import subprocess as _sp

    import certifi as _certifi

    certifi_dir = _pl.Path(_certifi.where()).parent
    combined    = certifi_dir / "narad_cacert.pem"

    # Rebuild only when certifi's bundle is newer than our combined file
    certifi_path = _pl.Path(_certifi.where())
    if combined.exists() and combined.stat().st_mtime >= certifi_path.stat().st_mtime:
        return str(combined)

    # Start with certifi's bundle
    content = certifi_path.read_bytes()

    # Append every CA containing "Umbrella" or "Cisco" from the macOS keychain
    for keyword in ("Umbrella", "Cisco Umbrella"):
        try:
            result = _sp.run(
                ["security", "find-certificate", "-c", keyword, "-a", "-p"],
                capture_output=True, timeout=5,
            )
            if result.stdout:
                content += b"\n" + result.stdout
        except Exception:
            pass

    combined.write_bytes(content)
    return str(combined)

try:
    import ssl as _ssl
    _ca = _build_ca_bundle()

    # Env vars — for requests / urllib3 / curl
    os.environ["SSL_CERT_FILE"]      = _ca
    os.environ["REQUESTS_CA_BUNDLE"] = _ca
    os.environ["CURL_CA_BUNDLE"]     = _ca

    # Patch ssl.create_default_context — for httpx / stdlib urllib
    _orig_create_ctx = _ssl.create_default_context
    def _narad_ssl_ctx(*args, **kwargs):                            # noqa: E301
        if not (kwargs.get("cafile") or kwargs.get("capath") or kwargs.get("cadata")):
            kwargs["cafile"] = _ca
        return _orig_create_ctx(*args, **kwargs)
    _ssl.create_default_context = _narad_ssl_ctx

    # Patch SSLContext.load_default_certs — for aiohttp (litellm's async path)
    _orig_load_default = _ssl.SSLContext.load_default_certs
    def _narad_load_default(self, purpose=_ssl.Purpose.SERVER_AUTH):  # noqa: E301
        try:
            _orig_load_default(self, purpose)
        except Exception:
            pass
        self.load_verify_locations(cafile=_ca)
    _ssl.SSLContext.load_default_certs = _narad_load_default

except Exception:
    pass
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

_log = logging.getLogger("narad.json_patch")

# Load .env from the project root before any other imports that read env vars.
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop
import narad_paths  # noqa: F401  — registers all phase dirs; must precede phase imports

# isort: split

# ── JSON robustness patch ────────────────────────────────────────────────────
# DeepSeek V3 emits literal unescaped control characters (not just \n/\r/\t
# but also \x0B, \x0C, \x00, U+2028, U+2029) inside JSON string values when
# generating long function-call arguments. This makes the arguments string
# invalid JSON and crashes the ADK/LiteLLM stack before our tool code runs.
# Patch json.loads globally — always attempt repair on any JSONDecodeError.

_CTRL_ESCAPE = {
    '\n': '\\n', '\r': '\\r', '\t': '\\t',
    '\b': '\\b', '\f': '\\f',
    ' ': '\\u2028', ' ': '\\u2029',
}

def _repair_json_strings(text: str) -> str:
    result = []
    in_string = False
    escape_next = False
    for ch in text:
        if escape_next:
            result.append(ch)
            escape_next = False
        elif ch == '\\' and in_string:
            result.append(ch)
            escape_next = True
        elif ch == '"':
            in_string = not in_string
            result.append(ch)
        elif in_string:
            named = _CTRL_ESCAPE.get(ch)
            if named:
                result.append(named)
            elif ord(ch) < 0x20:
                result.append(f'\\u{ord(ch):04x}')
            else:
                result.append(ch)
        else:
            result.append(ch)
    return ''.join(result)

_orig_json_loads = json.loads

try:
    from json_repair import repair_json as _repair_json
    _HAS_JSON_REPAIR = True
except ImportError:
    _HAS_JSON_REPAIR = False

def _json_loads_tolerant(s, /, *args, **kwargs):
    try:
        return _orig_json_loads(s, *args, **kwargs)
    except json.JSONDecodeError as first_err:
        if isinstance(s, (bytes, bytearray)):
            s = s.decode('utf-8', errors='replace')
        if not isinstance(s, str):
            raise
        # Stage 1: escape stray control characters (fast, lossless)
        repaired = _repair_json_strings(s)
        try:
            return _orig_json_loads(repaired, *args, **kwargs)
        except json.JSONDecodeError:
            pass
        # Stage 2: full structural repair (handles truncation, unescaped quotes)
        if _HAS_JSON_REPAIR:
            try:
                result = _repair_json(repaired, return_objects=True)
                # ADK tool call args must be a dict
                if isinstance(result, dict):
                    _log.warning("JSON repaired via json_repair (stage 2): %s", first_err)
                    return result
                # json_repair sometimes wraps in a list when the JSON is severely truncated;
                # use the first element if it's a dict (the rest is junk)
                if (isinstance(result, list) and result
                        and isinstance(result[0], dict)):
                    _log.warning("JSON repaired (list[0] dict) via json_repair: %s", first_err)
                    return result[0]
                _log.warning("json_repair returned unusable type %s, skipping: %s",
                             type(result).__name__, first_err)
            except Exception:
                pass
        _log.error("JSON repair failed: %s  snippet=%r", first_err, s[:300])
        raise

json.loads = _json_loads_tolerant
# ─────────────────────────────────────────────────────────────────────────────

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

_ADK_IMPORT_ERROR: str | None = None
try:
    from google.adk.events import Event
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
except Exception as _adk_exc:
    Runner = Any  # type: ignore[assignment]
    InMemorySessionService = None  # type: ignore[assignment]
    Event = Any  # type: ignore[assignment]
    _ADK_IMPORT_ERROR = f"google.adk unavailable: {_adk_exc}"

_AGENT_RUNTIME_IMPORT_ERROR: str | None = None
try:
    from avatar_agents import AGENT_TOOL_NAMES, _images_ctx
    from narad_agent import build_narad_agent
except Exception as _agent_exc:
    build_narad_agent = None  # type: ignore[assignment]
    AGENT_TOOL_NAMES: dict[str, str] = {}
    _images_ctx = contextvars.ContextVar("_images_ctx", default=[])
    _AGENT_RUNTIME_IMPORT_ERROR = f"agent runtime unavailable: {_agent_exc}"
from context_governor import RuntimeEpoch, choose_model_and_plan, should_rollover_epoch
from model_config import AVATAR_MODELS
from runtime_contract import (
    agent_contract_map as _agent_contract_map,
)
from runtime_contract import (
    canonical_tool_name_map as _canonical_tool_name_map,
)
from runtime_contract import (
    collect_runtime_contract,
    health_payload,
)
from runtime_contract import (
    primary_discipline as _primary_discipline,
)
from yantra import Tracer

from conversation_memory import (
    append_turn as _append_thread_turn,
)
from conversation_memory import (
    build_recent_thread_context as _build_recent_thread_context,
)
from conversation_memory import (
    build_rehydration_query as _build_rehydration_query,
)
from conversation_memory import (
    clear_thread as _clear_thread,
)
from conversation_memory import (
    load_thread as _load_thread,
)
from conversation_memory import (
    load_working_state as _load_working_state,
)
from conversation_memory import (
    recent_threads as _recent_threads,
)
from conversation_memory import (
    save_working_state as _save_working_state,
)
from conversation_memory import (
    summarize_thread as _summarize_thread,
)
from harness_contract import (
    archive_session as _archive_harness_session,
)
from harness_contract import (
    build_context_bundle as _build_harness_context_bundle,
)
from harness_contract import (
    compact_session as _compact_harness_session,
)
from harness_contract import (
    delete_session_record as _delete_harness_session_record,
)
from harness_contract import (
    fork_session as _fork_harness_session,
)
from harness_contract import (
    get_session_record as _get_harness_session_record,
)
from harness_contract import (
    harness_overview as _harness_overview,
)
from harness_contract import (
    list_session_records as _list_harness_sessions,
)
from harness_contract import (
    record_session_state as _record_harness_session_state,
)
from harness_contract import (
    recover_session as _recover_harness_session,
)
from learning_workspace import (
    append_learning_record as _append_learning_record,
)
from learning_workspace import (
    build_workspace_packet as _build_learning_workspace_packet,
)
from learning_workspace import (
    create_learning_artifact as _create_learning_artifact,
)
from learning_workspace import (
    ensure_workspace as _ensure_learning_workspace,
)
from learning_workspace import (
    extract_learning_topic as _extract_learning_topic,
)
from learning_workspace import (
    is_learning_query as _is_learning_query,
)
from learning_workspace import (
    load_artifact as _load_learning_artifact,
)
from learning_workspace import (
    load_workspace as _load_learning_workspace,
)
from learning_workspace import (
    merge_resources as _merge_learning_resources,
)
from learning_workspace import (
    suggest_glossary_entries as _suggest_learning_glossary_entries,
)
from learning_workspace import (
    update_glossary_terms as _update_learning_glossary_terms,
)
from learning_workspace import (
    update_learning_artifact as _update_learning_artifact,
)


# ── Structured JSON logging ───────────────────────────────────────────────────
class _JsonFormatter(logging.Formatter):
    def format(self, r: logging.LogRecord) -> str:
        import json as _j
        return _j.dumps({
            "ts":    self.formatTime(r, "%Y-%m-%dT%H:%M:%S"),
            "level": r.levelname,
            "name":  r.name,
            "msg":   r.getMessage(),
        })

_log_handler = logging.StreamHandler()
_log_handler.setFormatter(_JsonFormatter())
logging.root.handlers = [_log_handler]
logging.root.setLevel(logging.INFO)
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="Narad API", version="0.15.0-pre15")

# ── Phase 11: Project Wiki + Projects + Sessions API ──────────────────────────
try:
    from learning_workspace_api import learning_router
    from project_execution_api import project_execution_router, tasks_router
    from project_wiki_api import projects_router, sessions_router, wiki_router
    app.include_router(wiki_router)
    app.include_router(projects_router)
    app.include_router(sessions_router)
    app.include_router(project_execution_router)
    app.include_router(tasks_router)
    app.include_router(learning_router)
except Exception as _wiki_err:
    logging.getLogger("narad.server").warning("Project routers unavailable: %s", _wiki_err)

# ── TTS (Sarvam voice) ────────────────────────────────────────────────────────
try:
    from tts_api import tts_router
    app.include_router(tts_router)
except Exception as _tts_err:
    logging.getLogger("narad.server").warning("TTS router unavailable: %s", _tts_err)

# ── Security floor: bearer auth + pinned CORS ─────────────────────────────────
#
# Auth modes (NARAD_AUTH env):
#   local  (default) — requests from 127.0.0.1/::1 pass; anything else needs
#                      "Authorization: Bearer <token>". Pairs with the default
#                      127.0.0.1 bind: remote access requires BOTH a rebind and
#                      the token.
#   strict           — every request needs the bearer token (except exempt paths)
#   off              — no auth (tests / trusted networks only)
#
# The token is auto-generated on first startup at ~/.narad/config/api_token
# (chmod 600). Exempt: /health (probes), /media/* (<video>/<img> tags cannot
# send Authorization headers).

from fastapi.responses import JSONResponse as _AuthJSONResponse

from narad_config import CONFIG_DIR as _CONFIG_DIR

_AUTH_MODE = os.environ.get("NARAD_AUTH", "local").strip().lower()
_API_TOKEN_PATH = _CONFIG_DIR / "api_token"
_LOCAL_CLIENTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def _load_or_create_api_token() -> str:
    try:
        if _API_TOKEN_PATH.exists():
            token = _API_TOKEN_PATH.read_text().strip()
            if token:
                return token
        import secrets as _secrets

        token = _secrets.token_urlsafe(32)
        _API_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        _API_TOKEN_PATH.write_text(token + "\n")
        _API_TOKEN_PATH.chmod(0o600)
        logging.getLogger("narad.server").info("API token created at %s", _API_TOKEN_PATH)
        return token
    except Exception as exc:
        logging.getLogger("narad.server").warning("API token unavailable: %s", exc)
        return ""


_API_TOKEN = _load_or_create_api_token() if _AUTH_MODE != "off" else ""


@app.middleware("http")
async def _bearer_auth(request, call_next):
    if _AUTH_MODE == "off" or request.method == "OPTIONS":
        return await call_next(request)
    path = request.url.path
    if path == "/health" or path.startswith("/media/"):
        return await call_next(request)
    client_host = request.client.host if request.client else ""
    if _AUTH_MODE == "local" and client_host in _LOCAL_CLIENTS:
        return await call_next(request)
    supplied = request.headers.get("authorization", "")
    if _API_TOKEN and supplied == f"Bearer {_API_TOKEN}":
        return await call_next(request)
    return _AuthJSONResponse({"detail": "Unauthorized"}, status_code=401)


# CORS pinned to the frontend dev origins; extend via NARAD_ALLOWED_ORIGINS
# (comma-separated). Added after the auth middleware so preflight OPTIONS is
# answered by CORS before auth runs.
_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "NARAD_ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve generated media files (video + audio from Parashurama)
from narad_config import ARTIFACTS_DIR as _MEDIA_DIR

app.mount("/media", StaticFiles(directory=_MEDIA_DIR), name="media")


@app.on_event("startup")
async def _startup_runtime_contract() -> None:
    app.state.runtime_contract = collect_runtime_contract()

# ── Dharma Gate — input-level topic blocking ──────────────────────────────────
import re as _re_gate

_HARD_BLOCKS: list[tuple] = [
    (r"(?i)IGNORE\s+ALL\s+PREVIOUS\s+INSTRUCTIONS?", "Prompt injection detected."),
    (r"(?i)\[INST\]", "Prompt injection detected."),
    (r"(?i)(SSN|social\s+security\s+number|passport\s+number)", "I can't collect sensitive personal identifiers."),
    (r"(?i)how\s+(to|do\s+I|can\s+I)\s+(kill|seriously\s+harm)\s+(myself|someone)", "If you're in crisis, please reach out to iCall: 9152987821 or your local emergency services."),
]

def _dharma_gate(query: str) -> str | None:
    """Return a blocking reason string if the query violates a hard rule, else None."""
    for pattern, reason in _HARD_BLOCKS:
        if _re_gate.search(pattern, query):
            return reason
    return None

# ── Rate limiting — token bucket per user_id ──────────────────────────────────
import time as _time_rl

_rate_buckets: dict[str, tuple[float, float]] = {}  # user_id → (last_check_ts, tokens)
_RATE_LIMIT  = float(os.environ.get("NARAD_RATE_LIMIT", "10"))  # requests per minute
_RATE_WINDOW = 60.0


def _agent_runtime_unavailable_reason() -> str | None:
    return _AGENT_RUNTIME_IMPORT_ERROR or _ADK_IMPORT_ERROR

def _check_rate_limit(user_id: str) -> bool:
    now = _time_rl.monotonic()
    last_ts, tokens = _rate_buckets.get(user_id, (now, _RATE_LIMIT))
    elapsed = now - last_ts
    tokens = min(_RATE_LIMIT, tokens + elapsed / _RATE_WINDOW * _RATE_LIMIT)
    if tokens < 1.0:
        _rate_buckets[user_id] = (now, tokens)
        return False
    _rate_buckets[user_id] = (now, tokens - 1.0)
    return True
# ─────────────────────────────────────────────────────────────────────────────

# One persistent runner per user_id — session service survives across requests
# so Narad sees prior turns in the same session (full conversation history).
_user_runners: dict[tuple[str, str], Any] = {}

def _get_runner_for_user(user_id: str, model: str | None = None) -> Any:
    runtime_error = _agent_runtime_unavailable_reason()
    if runtime_error:
        raise RuntimeError(runtime_error)
    resolved_model = model or AVATAR_MODELS["narad"]
    cache_key = (user_id, resolved_model)
    if cache_key not in _user_runners:
        from narad_agent import build_narad_agent as _build
        narad = _build(model=resolved_model, user_id=user_id)
        svc = InMemorySessionService()
        _user_runners[cache_key] = Runner(agent=narad, app_name="avatara", session_service=svc)
    return _user_runners[cache_key]


# Background task registry: session_id → (task, event_queue)
# The ADK run lives here, decoupled from the SSE stream. If the client
# disconnects (screen lock, browser throttle) and reconnects, the task
# keeps running and the client re-attaches to the same queue.
_active_tasks: dict[str, tuple[asyncio.Task, asyncio.Queue]] = {}
_CONTINUATION_CUES = (
    "continue",
    "carry it on",
    "carry on",
    "go ahead",
    "previous conversation",
    "previous chat",
    "pick up",
    "resume",
    "same thread",
    "step 1",
    "step one",
    "that plan",
)
_LEARNING_SUMMARY_LIMIT = 1_600


def _compact_karya_state(session_id: str) -> dict[str, Any] | None:
    try:
        from kanban import KanbanBoard
    except Exception:
        return None

    try:
        board = KanbanBoard().get_board(session_id)
    except Exception:
        return None

    total = int(board.get("total", 0) or 0)
    if total <= 0:
        return None

    columns = board.get("columns", {})
    active_titles: list[str] = []
    for column_name in ("in_progress", "review", "backlog"):
        for step in columns.get(column_name, [])[:3]:
            title = str(step.get("title", "")).strip()
            if title and title not in active_titles:
                active_titles.append(title)
            if len(active_titles) >= 5:
                break
        if len(active_titles) >= 5:
            break

    return {
        "total": total,
        "done_count": int(board.get("done_count", 0) or 0),
        "blocked_count": int(board.get("blocked_count", 0) or 0),
        "active_titles": active_titles,
    }


def _looks_like_continuation(query: str) -> bool:
    q = query.lower()
    return any(cue in q for cue in _CONTINUATION_CUES)


def _distill_learning_summary(topic: str, query: str, response: str) -> str:
    response = (response or "").strip()
    first_paragraph = response.split("\n\n", 1)[0].strip() if response else ""
    summary = "\n".join([
        f"Topic: {topic}",
        f"Learner goal: {query.strip()[:320]}",
        f"Latest teaching summary: {first_paragraph[:900]}",
    ])
    return summary[:_LEARNING_SUMMARY_LIMIT]


_STATIC_SYSTEM_OVERHEAD_TOKENS = 12_000


def _clean_optional_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "none", "null", "undefined"}:
        return ""
    return text


def _working_state_context(state: dict[str, Any] | None) -> str:
    if not state:
        return ""

    lines: list[str] = []
    if state.get("thread_summary"):
        lines.append("Earlier state summary:")
        lines.append(str(state["thread_summary"]))
    if state.get("last_user_query"):
        lines.append(f"Last user query: {state['last_user_query']}")
    if state.get("last_assistant_preview"):
        lines.append(f"Last useful result: {state['last_assistant_preview']}")
    if state.get("avatars"):
        lines.append("Active avatars: " + ", ".join(state.get("avatars", [])))
    if state.get("phase_transitions"):
        recent_phases = state.get("phase_transitions", [])[-4:]
        if recent_phases:
            lines.append("Recent phase transitions:")
            lines.extend(f"- {item}" for item in recent_phases)
    karya = state.get("karya")
    if isinstance(karya, dict) and karya.get("total"):
        parts = [f"{karya.get('total', 0)} tasks"]
        if karya.get("done_count"):
            parts.append(f"{karya['done_count']} done")
        if karya.get("blocked_count"):
            parts.append(f"{karya['blocked_count']} blocked")
        lines.append("Karya: " + " · ".join(parts))
        for title in (karya.get("active_titles") or [])[:5]:
            lines.append(f"- {title}")
    return "\n".join(lines)


def _runtime_epoch_from_state(state: dict[str, Any] | None, fallback_model: str) -> RuntimeEpoch | None:
    if not state or not state.get("runtime_epoch_id"):
        return None
    return RuntimeEpoch(
        epoch_id=str(state["runtime_epoch_id"]),
        model=str(state.get("runtime_epoch_model") or fallback_model),
        turn_count=int(state.get("runtime_epoch_turn_count", 0) or 0),
        last_prompt_tokens=int(state.get("runtime_epoch_last_prompt_tokens", 0) or 0),
        peak_prompt_tokens=int(state.get("runtime_epoch_peak_prompt_tokens", 0) or 0),
        compaction_count=int(state.get("runtime_epoch_compaction_count", 0) or 0),
    )


class ChatRequest(BaseModel):
    query: str
    session_id: Optional[str] = None
    user_id: str = "default"
    images: list[str] = []
    active_artifact_id: Optional[str] = None
    active_artifact_workspace_id: Optional[str] = None
    active_artifact_type: Optional[str] = None


@app.get("/health")
async def health():
    payload = health_payload()
    app.state.runtime_contract = collect_runtime_contract()
    return payload


@app.get("/capabilities")
async def capabilities():
    app.state.runtime_contract = collect_runtime_contract()
    return app.state.runtime_contract


@app.post("/chat")
async def chat(req: ChatRequest):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query cannot be empty")

    runtime_error = _agent_runtime_unavailable_reason()
    if runtime_error:
        async def _unavailable_stream():
            yield json.dumps({
                "type": "error",
                "data": {
                    "message": (
                        "Narad is running in degraded mode and the chat runtime is unavailable. "
                        f"{runtime_error}"
                    )
                },
            })
            yield json.dumps({"type": "done", "data": {"session_id": "unavailable"}})
        return EventSourceResponse(_unavailable_stream())

    # Dharma Gate: block hard-forbidden inputs before any agent work starts
    block_reason = _dharma_gate(req.query)
    if block_reason:
        async def _blocked_stream():
            yield json.dumps({"type": "error", "data": {"message": block_reason}})
            yield json.dumps({"type": "done",  "data": {"session_id": "blocked"}})
        return EventSourceResponse(_blocked_stream())

    # Rate limiting: 10 req/min per user_id by default
    if not _check_rate_limit(req.user_id):
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded. Try again in a minute."},
            headers={"Retry-After": "60"},
        )

    session_id = req.session_id or str(uuid.uuid4())

    # Re-attach to a still-running task (screen-lock / brief-disconnect reconnect).
    # The client resends the same session_id — we return the existing queue instead
    # of starting a new ADK run.
    if session_id in _active_tasks:
        task, queue = _active_tasks[session_id]
        if not task.done():
            return EventSourceResponse(_drain_queue(session_id, queue))

    # Start a new background task and return a stream that drains its queue.
    queue: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(_run_agent_task(req, session_id, queue))
    _active_tasks[session_id] = (task, queue)
    return EventSourceResponse(_drain_queue(session_id, queue))


async def _run_agent_task(
    req: ChatRequest,
    session_id: str,
    queue: asyncio.Queue,
) -> None:
    """Background coroutine — runs the ADK agent and pushes SSE JSON onto *queue*.

    Decoupled from the SSE consumer so the task survives client disconnects
    (screen lock, browser tab backgrounding, brief network drops).
    Uses caffeinate -i on macOS to prevent idle sleep while running.
    """
    caffeinate: subprocess.Popen | None = None
    restored_thread = False
    restored_turn_count = 0
    narad_response_text = ""
    restored_working_state: dict[str, Any] | None = None
    recent_source_sessions: list[str] = []
    runtime_session_id = session_id
    final_context_plan = None
    selected_model = AVATAR_MODELS["narad"]
    runtime_epoch: RuntimeEpoch | None = None
    rollover_reasons: list[str] = []
    rehydration_meta: dict[str, Any] = {}
    learning_workspace: dict[str, Any] | None = None
    learning_artifact_request: tuple[str, str] | None = None
    try:
        # Prevent macOS idle sleep for the duration of the task.
        try:
            caffeinate = subprocess.Popen(
                ["caffeinate", "-i"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            pass  # Not macOS — no-op

        try:
            from project_manager import detect_project as _detect_project
            await _detect_project(req.user_id, session_id, [req.query])
        except Exception:
            pass

        restored_working_state = _load_working_state(req.user_id, session_id)
        prior_turns = _load_thread(req.user_id, session_id, limit=10)
        restored_turn_count = len(prior_turns)
        same_thread_restore_available = bool(
            prior_turns or (
                restored_working_state and (
                    restored_working_state.get("thread_summary")
                    or restored_working_state.get("continued_from_sessions")
                    or restored_working_state.get("forked_from_session")
                )
            )
        )
        working_context = _working_state_context(restored_working_state)
        learning_artifact_offer_pending = bool(
            restored_working_state.get("learning_artifact_offer_pending")
            if restored_working_state else False
        )
        active_artifact_session = (
            dict(restored_working_state.get("active_artifact") or {})
            if restored_working_state else {}
        )
        learning_workspace_id = (
            _clean_optional_text(restored_working_state.get("learning_workspace_id"))
            if restored_working_state else ""
        )
        if req.active_artifact_id:
            loaded_active_artifact = _load_learning_artifact(
                user_id=req.user_id,
                artifact_id=req.active_artifact_id,
                workspace_id=req.active_artifact_workspace_id,
            )
            if loaded_active_artifact:
                active_artifact_session = _artifact_session_payload(loaded_active_artifact)
                learning_workspace_id = _clean_optional_text(req.active_artifact_workspace_id) or learning_workspace_id
        explicit_learning_artifact_request = _is_explicit_learning_artifact_request(
            req.query,
            offer_pending=learning_artifact_offer_pending,
        )
        if learning_workspace_id:
            learning_workspace = _load_learning_workspace(
                user_id=req.user_id,
                workspace_id=learning_workspace_id,
            )
        elif _is_learning_query(req.query) or explicit_learning_artifact_request:
            artifact_meta = _extract_learning_artifact_request(
                req.query,
                offer_pending=learning_artifact_offer_pending,
            )
            learning_topic = artifact_meta[0] if artifact_meta else _extract_learning_topic(req.query)
            learning_workspace = _ensure_learning_workspace(
                user_id=req.user_id,
                topic=learning_topic,
                mission=req.query,
                session_id=session_id,
            )
            learning_workspace_id = _clean_optional_text(learning_workspace.get("workspace_id"))

        if learning_workspace_id and learning_workspace is not None:
            learning_packet = _build_learning_workspace_packet(
                user_id=req.user_id,
                workspace_id=learning_workspace_id,
            )
            if learning_packet:
                working_context = "\n\n".join(
                    block for block in [learning_packet, working_context] if block.strip()
                )
        learning_artifact_request = _extract_learning_artifact_request(
            req.query,
            offer_pending=learning_artifact_offer_pending,
            fallback_topic=(learning_workspace or {}).get("topic", "this topic"),
        )
        explicit_learning_artifact_edit = bool(
            active_artifact_session
            and _is_explicit_learning_artifact_edit(
                req.query,
                artifact_type=str(active_artifact_session.get("artifact_type", "")),
            )
        )
        if learning_artifact_request:
            artifact_topic, artifact_type = learning_artifact_request
            artifact_type = _normalize_learning_artifact_type(artifact_type)
            if not learning_workspace_id:
                learning_workspace = _ensure_learning_workspace(
                    user_id=req.user_id,
                    topic=artifact_topic,
                    mission=req.query,
                    session_id=session_id,
                )
                learning_workspace_id = _clean_optional_text(learning_workspace.get("workspace_id"))
            elif learning_workspace is None:
                learning_workspace = _load_learning_workspace(
                    user_id=req.user_id,
                    workspace_id=learning_workspace_id,
                )

            predicted_record_id = f"{int((learning_workspace or {}).get('record_count', 0) or 0) + 1:04d}"
            artifact = _create_learning_artifact(
                user_id=req.user_id,
                workspace_id=learning_workspace_id,
                topic=artifact_topic,
                artifact_type=artifact_type,
                teaching_context=artifact_topic,
                record_ids=[predicted_record_id],
            )
            record = _append_learning_record(
                user_id=req.user_id,
                workspace_id=learning_workspace_id,
                title=f"{_learning_artifact_label(artifact_type).title()} — {artifact_topic}",
                summary=f"Created a native {_learning_artifact_label(artifact_type)} for {artifact_topic}.",
                body=(
                    f"Artifact ID: {artifact['artifact_id']}\n"
                    f"Artifact type: {artifact_type}\n"
                    f"Topic: {artifact_topic}\n"
                    f"Created from learner request: {req.query.strip()}"
                ),
                record_type="artifact",
                session_id=session_id,
                tags=[artifact_type, "learning-artifact"],
                source="krishna",
            )
            artifact_session = _artifact_session_payload(artifact, record_ids=[record["record_id"]])
            artifact_label = _learning_artifact_label(artifact_type)
            narad_response_text = (
                f"I opened a {artifact_label} for {artifact_topic} in the side panel. "
                "Use the main chat to make explicit edits like adding cards or nodes."
            )
            await queue.put(json.dumps({
                "type": "artifact_opened",
                "data": artifact_session,
            }))
            await queue.put(json.dumps({
                "type": "narad_synthesis",
                "data": {"text": narad_response_text},
            }))
            _append_thread_turn(
                user_id=req.user_id,
                session_id=session_id,
                role="user",
                text=req.query,
                metadata={"images": len(req.images)},
            )
            _append_thread_turn(
                user_id=req.user_id,
                session_id=session_id,
                role="assistant",
                text=narad_response_text,
                metadata={"artifact_id": artifact["artifact_id"], "artifact_type": artifact_type, "artifact_topic": artifact_topic},
            )
            thread_summary = _summarize_thread(user_id=req.user_id, session_id=session_id)
            turn_count = len(_load_thread(req.user_id, session_id))
            short_circuit_state = dict(restored_working_state or {})
            short_circuit_state.update({
                "last_user_query": req.query[:220],
                "last_assistant_preview": narad_response_text[:220],
                "turn_count": turn_count,
                "thread_summary": thread_summary,
                "learning_workspace_id": learning_workspace_id or None,
                "learning_topic": artifact_topic,
                "learning_record_ids": [record["record_id"]],
                "learning_artifact_offer_pending": False,
                "active_artifact": artifact_session,
            })
            _save_working_state(user_id=req.user_id, session_id=session_id, state=short_circuit_state)
            _record_harness_session_state(
                user_id=req.user_id,
                session_id=session_id,
                working_state=_load_working_state(req.user_id, session_id),
            )
            await queue.put(json.dumps({"type": "done", "data": {"session_id": session_id}}))
            return

        if explicit_learning_artifact_edit:
            artifact_id = _clean_optional_text(active_artifact_session.get("artifact_id"))
            workspace_id = _clean_optional_text(active_artifact_session.get("workspace_id")) or learning_workspace_id
            artifact_type = _normalize_learning_artifact_type(str(active_artifact_session.get("artifact_type", "")))
            artifact_topic = _clean_optional_text(active_artifact_session.get("topic")) or (learning_workspace or {}).get("topic", "this topic")
            if not workspace_id:
                raise RuntimeError("active artifact workspace missing")
            if learning_workspace is None:
                learning_workspace = _load_learning_workspace(user_id=req.user_id, workspace_id=workspace_id)
            predicted_record_id = f"{int((learning_workspace or {}).get('record_count', 0) or 0) + 1:04d}"
            artifact = _update_learning_artifact(
                user_id=req.user_id,
                artifact_id=artifact_id,
                workspace_id=workspace_id,
                instruction=req.query,
                record_ids=[predicted_record_id],
            )
            record = _append_learning_record(
                user_id=req.user_id,
                workspace_id=workspace_id,
                title=f"Artifact update — {artifact_topic}",
                summary=f"Updated the {_learning_artifact_label(artifact_type)} for {artifact_topic}.",
                body=(
                    f"Artifact ID: {artifact_id}\n"
                    f"Artifact type: {artifact_type}\n"
                    f"Update instruction: {req.query.strip()}"
                ),
                record_type="artifact",
                session_id=session_id,
                tags=[artifact_type, "artifact-update"],
                source="krishna",
            )
            artifact_session = _artifact_session_payload(artifact, record_ids=[record["record_id"]])
            narad_response_text = (
                f"I updated the {_learning_artifact_label(artifact_type)} for {artifact_topic}. "
                "Keep using explicit edit prompts if you want to change the open artifact further."
            )
            await queue.put(json.dumps({
                "type": "artifact_updated",
                "data": artifact_session,
            }))
            await queue.put(json.dumps({
                "type": "narad_synthesis",
                "data": {"text": narad_response_text},
            }))
            _append_thread_turn(
                user_id=req.user_id,
                session_id=session_id,
                role="user",
                text=req.query,
                metadata={"images": len(req.images), "artifact_id": artifact_id},
            )
            _append_thread_turn(
                user_id=req.user_id,
                session_id=session_id,
                role="assistant",
                text=narad_response_text,
                metadata={"artifact_id": artifact_id, "artifact_type": artifact_type, "artifact_topic": artifact_topic},
            )
            thread_summary = _summarize_thread(user_id=req.user_id, session_id=session_id)
            turn_count = len(_load_thread(req.user_id, session_id))
            short_circuit_state = dict(restored_working_state or {})
            short_circuit_state.update({
                "last_user_query": req.query[:220],
                "last_assistant_preview": narad_response_text[:220],
                "turn_count": turn_count,
                "thread_summary": thread_summary,
                "learning_workspace_id": workspace_id,
                "learning_topic": artifact_topic,
                "learning_record_ids": [record["record_id"]],
                "learning_artifact_offer_pending": False,
                "active_artifact": artifact_session,
            })
            _save_working_state(user_id=req.user_id, session_id=session_id, state=short_circuit_state)
            _record_harness_session_state(
                user_id=req.user_id,
                session_id=session_id,
                working_state=_load_working_state(req.user_id, session_id),
            )
            await queue.put(json.dumps({"type": "done", "data": {"session_id": session_id}}))
            return
        runtime_epoch = _runtime_epoch_from_state(restored_working_state, AVATAR_MODELS["narad"])
        base_requested_model = runtime_epoch.model if runtime_epoch else AVATAR_MODELS["narad"]
        selected_model = base_requested_model

        candidate_query = req.query
        if not same_thread_restore_available and _looks_like_continuation(req.query):
            recent_context, recent_source_sessions = _build_recent_thread_context(
                user_id=req.user_id,
                current_query=req.query,
                exclude_session_id=session_id,
            )
            if recent_context:
                candidate_query = recent_context

        preflight_plan, preflight_profile = choose_model_and_plan(
            model=selected_model,
            plane_specs=[
                {
                    "key": "system_plane",
                    "content": "",
                    "priority": 1,
                    "hard": True,
                    "compaction_strategy": "fixed_overhead",
                    "token_estimate": _STATIC_SYSTEM_OVERHEAD_TOKENS,
                },
                {
                    "key": "working_plane",
                    "content": working_context,
                    "priority": 2,
                    "hard": False,
                    "compaction_strategy": "state_summary",
                },
                {
                    "key": "current_turn_plane",
                    "content": candidate_query,
                    "priority": 0,
                    "hard": True,
                    "compaction_strategy": "none",
                },
            ],
            long_running=True,
        )
        selected_model = preflight_profile.model
        if runtime_epoch:
            rollover_reasons = should_rollover_epoch(runtime_epoch, preflight_plan, max_turns=12)
            if runtime_epoch.model != selected_model:
                rollover_reasons.append("model_escalated")

        runner = _get_runner_for_user(req.user_id, selected_model)
        tracer = Tracer(session_id=session_id, user_id=req.user_id)

        runtime_session_id = runtime_epoch.epoch_id if runtime_epoch else str(uuid.uuid4())
        existing = await runner.session_service.get_session(
            app_name="avatara", user_id=req.user_id, session_id=runtime_session_id
        ) if runtime_epoch and runtime_epoch.model == selected_model else None

        from google.genai import types as genai_types

        needs_restore = bool(rollover_reasons) or existing is None or candidate_query != req.query
        effective_query = candidate_query
        if needs_restore and same_thread_restore_available:
            raw_restore_query, _ = _build_rehydration_query(
                user_id=req.user_id,
                session_id=session_id,
                current_query=req.query,
                char_budget=24_000,
                return_metadata=True,
            )
            effective_query = raw_restore_query
            restored_thread = True
        elif candidate_query != req.query:
            restored_thread = False

        final_context_plan, final_profile = choose_model_and_plan(
            model=selected_model,
            plane_specs=[
                {
                    "key": "system_plane",
                    "content": "",
                    "priority": 1,
                    "hard": True,
                    "compaction_strategy": "fixed_overhead",
                    "token_estimate": _STATIC_SYSTEM_OVERHEAD_TOKENS,
                },
                {
                    "key": "working_plane",
                    "content": working_context,
                    "priority": 2,
                    "hard": False,
                    "compaction_strategy": "state_summary",
                },
                {
                    "key": "current_turn_plane",
                    "content": effective_query,
                    "priority": 0,
                    "hard": True,
                    "compaction_strategy": "thread_restore" if restored_thread else "none",
                },
            ],
            long_running=True,
        )
        selected_model = final_profile.model
        if selected_model != base_requested_model:
            final_context_plan.model_escalated_from = base_requested_model
            final_context_plan.model_escalated_to = selected_model
            if "model_escalated" not in rollover_reasons:
                rollover_reasons.append("model_escalated")
            needs_restore = True
            runner = _get_runner_for_user(req.user_id, selected_model)

        if needs_restore and same_thread_restore_available:
            restore_budget = max(
                2_048,
                final_profile.hard_input_budget_tokens - _STATIC_SYSTEM_OVERHEAD_TOKENS - 1_024,
            )
            effective_query, rehydration_meta = _build_rehydration_query(
                user_id=req.user_id,
                session_id=session_id,
                current_query=req.query,
                model=selected_model,
                token_budget=restore_budget,
                return_metadata=True,
            )
            final_context_plan, _ = choose_model_and_plan(
                model=selected_model,
                plane_specs=[
                    {
                        "key": "system_plane",
                        "content": "",
                        "priority": 1,
                        "hard": True,
                        "compaction_strategy": "fixed_overhead",
                        "token_estimate": _STATIC_SYSTEM_OVERHEAD_TOKENS,
                    },
                    {
                        "key": "working_plane",
                        "content": working_context,
                        "priority": 2,
                        "hard": False,
                        "compaction_strategy": "state_summary",
                    },
                    {
                        "key": "current_turn_plane",
                        "content": effective_query,
                        "priority": 0,
                        "hard": True,
                        "compaction_strategy": "thread_restore",
                    },
                ],
                long_running=True,
            )
            final_context_plan.compaction_applied.extend(rehydration_meta.get("compaction_applied", []))
            final_context_plan.compacted_from_tokens = int(rehydration_meta.get("compacted_from_tokens", 0) or 0)
        elif candidate_query != req.query:
            final_context_plan.compaction_applied.append("cross_thread_fallback")

        if needs_restore or runtime_epoch is None or runtime_epoch.model != selected_model:
            runtime_session_id = str(uuid.uuid4())
            await runner.session_service.create_session(
                app_name="avatara", user_id=req.user_id, session_id=runtime_session_id
            )
            runtime_epoch = RuntimeEpoch(epoch_id=runtime_session_id, model=selected_model)
        elif existing is None:
            await runner.session_service.create_session(
                app_name="avatara", user_id=req.user_id, session_id=runtime_session_id
            )

        user_message = genai_types.Content(
            role="user", parts=[genai_types.Part(text=effective_query)]
        )

        # Share the SSE queue, images, and HTTP session_id with avatar tool execution
        from avatar_agents import _http_session_id_ctx, _step_queue_ctx
        _step_queue_ctx.set(queue)
        _images_ctx.set(req.images)
        _http_session_id_ctx.set(session_id)

        tracer.session_start(req.query)

        await queue.put(json.dumps({
            "type": "context_budget",
            "data": {
                **final_context_plan.to_event_dict(),
                "runtime_epoch_id": runtime_epoch.epoch_id,
            },
        }))
        if final_context_plan.compaction_applied or rollover_reasons:
            await queue.put(json.dumps({
                "type": "context_compacted",
                "data": {
                    "runtime_epoch_id": runtime_epoch.epoch_id,
                    "reasons": rollover_reasons,
                    "compaction_applied": final_context_plan.compaction_applied,
                    "compacted_from_tokens": final_context_plan.compacted_from_tokens,
                    "predicted_input_tokens": final_context_plan.predicted_input_tokens,
                },
            }))
        if final_context_plan.model_escalated_to:
            await queue.put(json.dumps({
                "type": "context_escalated",
                "data": {
                    "runtime_epoch_id": runtime_epoch.epoch_id,
                    "from_model": final_context_plan.model_escalated_from,
                    "to_model": final_context_plan.model_escalated_to,
                },
            }))

        if restored_thread:
            await queue.put(json.dumps({
                "type": "thread_restored",
                "data": {
                    "session_id": session_id,
                    "runtime_epoch_id": runtime_epoch.epoch_id,
                    "turn_count": restored_turn_count,
                    "last_trace_session_id": (
                        restored_working_state.get("last_trace_session_id")
                        if restored_working_state else None
                    ),
                    "thread_summary": (
                        restored_working_state.get("thread_summary")
                        if restored_working_state else ""
                    ),
                },
            }))
        elif recent_source_sessions:
            await queue.put(json.dumps({
                "type": "thread_restored",
                "data": {
                    "session_id": session_id,
                    "runtime_epoch_id": runtime_epoch.epoch_id,
                    "turn_count": 0,
                    "cross_thread": True,
                    "source_sessions": recent_source_sessions,
                },
            }))

        think_filter = ThinkingFilter()
        async for event in runner.run_async(
            user_id=req.user_id, session_id=runtime_session_id, new_message=user_message
        ):
            sse_payloads = _event_to_sse(event, think_filter)
            for sse_payload in sse_payloads:
                await queue.put(sse_payload)
                try:
                    payload = json.loads(sse_payload)
                    if payload.get("type") == "narad_synthesis":
                        narad_response_text += str(payload.get("data", {}).get("text", ""))
                    if (
                        learning_workspace_id
                        and payload.get("type") == "tool_ui"
                        and payload.get("data", {}).get("avatar") == "Matsya"
                    ):
                        citations = payload.get("data", {}).get("payload", {}).get("citations", [])
                        if isinstance(citations, list) and citations:
                            _merge_learning_resources(
                                user_id=req.user_id,
                                workspace_id=learning_workspace_id,
                                resources=citations,
                            )
                except Exception:
                    pass
            usage_payload = _usage_to_sse(event)
            if usage_payload:
                await queue.put(usage_payload)

        # Flush any text buffered mid-<think> block by the stateful filter
        remaining = think_filter.flush().strip()
        if remaining:
            narad_response_text += remaining
            await queue.put(json.dumps({"type": "narad_synthesis", "data": {"text": remaining}}))

        learning_record_ids: list[str] = []
        if learning_workspace_id and narad_response_text.strip():
            try:
                topic = (
                    str((learning_workspace or {}).get("topic", "")).strip()
                    or _extract_learning_topic(req.query)
                )
                topic_tag = _extract_learning_topic(req.query)[:40] if _extract_learning_topic(req.query) else "learning"
                record = _append_learning_record(
                    user_id=req.user_id,
                    workspace_id=learning_workspace_id,
                    title=f"Teaching checkpoint — {topic}",
                    summary=_distill_learning_summary(topic, req.query, narad_response_text)[:220],
                    body=narad_response_text.strip(),
                    record_type="teaching_checkpoint",
                    session_id=session_id,
                    tags=["krishna", "teach", topic_tag],
                    source="krishna",
                )
                learning_record_ids.append(str(record.get("record_id", "")).strip())
                glossary_entries = _suggest_learning_glossary_entries(topic, narad_response_text)
                if glossary_entries:
                    _update_learning_glossary_terms(
                        user_id=req.user_id,
                        workspace_id=learning_workspace_id,
                        entries=glossary_entries,
                    )
                try:
                    from smriti import remember as _remember
                    _remember(
                        f"Learning workspace checkpoint for {topic}",
                        _distill_learning_summary(topic, req.query, narad_response_text),
                        "Krishna",
                        user_id=req.user_id,
                    )
                except Exception:
                    pass
            except Exception:
                pass

        learning_artifact_offer_pending = _learning_artifact_offer_pending(narad_response_text)

        tracer.session_done()
        _append_thread_turn(
            user_id=req.user_id,
            session_id=session_id,
            role="user",
            text=req.query,
            metadata={"images": len(req.images)},
        )
        if narad_response_text.strip():
            _append_thread_turn(
                user_id=req.user_id,
                session_id=session_id,
                role="assistant",
                text=narad_response_text.strip(),
                metadata={
                    "restored_after_reset": restored_thread,
                    "restored_turn_count": restored_turn_count,
                },
            )
        trace_summary = Tracer.summary(session_id)
        thread_summary = _summarize_thread(
            user_id=req.user_id,
            session_id=session_id,
        )
        karya_state = _compact_karya_state(session_id)
        turn_count = len(_load_thread(req.user_id, session_id))
        runtime_epoch.turn_count += 1
        runtime_epoch.last_prompt_tokens = final_context_plan.predicted_input_tokens
        runtime_epoch.peak_prompt_tokens = max(
            runtime_epoch.peak_prompt_tokens,
            final_context_plan.predicted_input_tokens,
        )
        if final_context_plan.compaction_applied:
            runtime_epoch.compaction_count += 1
        _save_working_state(
            user_id=req.user_id,
            session_id=session_id,
            state={
                "last_user_query": req.query[:220],
                "last_assistant_preview": narad_response_text.strip()[:220],
                "last_trace_session_id": session_id,
                "avatars": trace_summary.get("avatars", []),
                "latencies_ms": trace_summary.get("latencies_ms", {}),
                "phase_transitions": trace_summary.get("phase_transitions", []),
                "restored_after_reset": restored_thread,
                "restored_turn_count": restored_turn_count,
                "turn_count": turn_count,
                "thread_summary": thread_summary,
                "karya": karya_state,
                "continued_from_sessions": recent_source_sessions,
                "learning_workspace_id": learning_workspace_id or None,
                "learning_topic": (learning_workspace or {}).get("topic"),
                "learning_record_ids": learning_record_ids,
                "learning_artifact_offer_pending": learning_artifact_offer_pending,
                "active_artifact": active_artifact_session or (restored_working_state or {}).get("active_artifact"),
                "runtime_epoch_id": runtime_epoch.epoch_id,
                "runtime_epoch_model": selected_model,
                "runtime_epoch_turn_count": runtime_epoch.turn_count,
                "runtime_epoch_last_prompt_tokens": runtime_epoch.last_prompt_tokens,
                "runtime_epoch_peak_prompt_tokens": runtime_epoch.peak_prompt_tokens,
                "runtime_epoch_compaction_count": runtime_epoch.compaction_count,
                "predicted_input_tokens": final_context_plan.predicted_input_tokens,
                "hard_input_budget_tokens": final_context_plan.hard_input_budget_tokens,
                "soft_target_tokens": final_context_plan.soft_target_tokens,
                "compaction_applied": final_context_plan.compaction_applied,
                "compacted_from_tokens": final_context_plan.compacted_from_tokens,
                "model_escalated_from": final_context_plan.model_escalated_from,
                "model_escalated_to": final_context_plan.model_escalated_to,
                "cache_hit_tokens": final_context_plan.cache_hit_tokens,
            },
        )
        _record_harness_session_state(
            user_id=req.user_id,
            session_id=session_id,
            working_state=_load_working_state(req.user_id, session_id),
        )
        await queue.put(json.dumps({
            "type": "done",
            "data": {
                "session_id": session_id,
                "runtime_epoch_id": runtime_epoch.epoch_id,
            },
        }))

        # Phase 10a: Compile session into project wiki (fire-and-forget)
        try:
            from scribe import compile_session as _compile
            asyncio.create_task(_compile(session_id, req.user_id))
        except Exception:
            pass

    except Exception as exc:
        tb = traceback.format_exc()
        logging.getLogger("narad.server").error("Session %s crashed:\n%s", session_id, tb)
        try:
            runner = _get_runner_for_user(req.user_id, selected_model)
            await runner.session_service.delete_session(
                app_name="avatara", user_id=req.user_id, session_id=runtime_session_id
            )
        except Exception:
            pass
        await queue.put(json.dumps({"type": "error", "data": {"message": str(exc)}}))

    finally:
        if caffeinate is not None:
            caffeinate.terminate()
        await queue.put(None)  # sentinel — signals _drain_queue to stop
        _active_tasks.pop(session_id, None)


async def _drain_queue(
    session_id: str,
    queue: asyncio.Queue,
) -> AsyncGenerator[str, None]:
    """SSE generator — drains the task queue and yields events to the client.

    Sends a keep-alive ping every 30 s when the queue is idle, preventing
    proxies and browsers from closing the connection during long operations.
    Safe to call again on reconnect — just creates a second consumer of the
    same queue (events flow to whichever consumer is currently active).
    """
    _HEARTBEAT_S = 30
    while True:
        try:
            event = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_S)
        except asyncio.TimeoutError:
            yield json.dumps({"type": "ping"})
            continue
        if event is None:  # sentinel from _run_agent_task
            break
        yield event


@app.get("/trace/{session_id}")
async def get_trace(session_id: str):
    events = Tracer.load(session_id)
    if not events:
        raise HTTPException(status_code=404, detail="No trace found for session")
    return {"session_id": session_id, "events": events, "summary": Tracer.summary(session_id)}


@app.get("/thread/{session_id}")
async def get_thread(session_id: str, user_id: str = "default"):
    turns = _load_thread(user_id, session_id)
    working_state = _load_working_state(user_id, session_id)
    return {
        "session_id": session_id,
        "turns": turns,
        "turn_count": len(turns),
        "working_state": working_state,
        "thread_summary": (working_state or {}).get("thread_summary", ""),
        "restorable": bool(turns),
    }


@app.get("/threads/latest")
async def get_latest_thread(user_id: str = "default"):
    threads = _recent_threads(user_id, limit=1)
    latest = threads[0] if threads else None
    return {
        "user_id": user_id,
        "thread": latest,
        "has_thread": latest is not None,
    }


@app.get("/threads")
async def list_threads(user_id: str = "default", limit: int = 10):
    return {
        "user_id": user_id,
        "threads": _recent_threads(user_id, limit=max(1, min(limit, 50))),
    }


@app.delete("/thread/{session_id}")
async def clear_thread(session_id: str, user_id: str = "default"):
    working_state = _load_working_state(user_id, session_id) or {}
    runtime_epoch_id = working_state.get("runtime_epoch_id")
    runtime_epoch_model = working_state.get("runtime_epoch_model") or AVATAR_MODELS["narad"]
    result = _clear_thread(user_id, session_id)
    _delete_harness_session_record(user_id, session_id)
    if runtime_epoch_id:
        runner = _user_runners.get((user_id, runtime_epoch_model))
        if runner is not None:
            try:
                await runner.session_service.delete_session(
                    app_name="avatara",
                    user_id=user_id,
                    session_id=str(runtime_epoch_id),
                )
            except Exception:
                pass
    try:
        from avatar_agents import evict_session_state
        evict_session_state(user_id, session_id)
    except Exception:
        pass
    return result


@app.get("/harness/overview")
async def get_harness_overview(user_id: str = "default", session_id: Optional[str] = None):
    return _harness_overview(user_id=user_id, selected_session_id=session_id)


@app.get("/harness/sessions")
async def list_harness_sessions(user_id: str = "default", limit: int = 24, include_archived: bool = True):
    sessions = _list_harness_sessions(
        user_id,
        limit=max(1, min(limit, 200)),
        include_archived=include_archived,
    )
    return {"user_id": user_id, "sessions": sessions, "count": len(sessions)}


@app.get("/harness/sessions/{session_id}")
async def get_harness_session(session_id: str, user_id: str = "default"):
    session = _get_harness_session_record(user_id, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="No harness session found")
    context = _build_harness_context_bundle(user_id, session_id)
    return {"session": session, "context": context}


@app.get("/harness/context/{session_id}")
async def get_harness_context(session_id: str, user_id: str = "default"):
    context = _build_harness_context_bundle(user_id, session_id)
    if not context:
        raise HTTPException(status_code=404, detail="No harness context found")
    return context


@app.post("/harness/sessions/{session_id}/archive")
async def archive_harness_session(session_id: str, user_id: str = "default"):
    record = _archive_harness_session(user_id, session_id)
    if not record:
        raise HTTPException(status_code=404, detail="No harness session found")
    return {"status": "ok", "session": record}


@app.post("/harness/sessions/{session_id}/recover")
async def recover_harness_session(session_id: str, user_id: str = "default"):
    record = _recover_harness_session(user_id, session_id)
    if not record:
        raise HTTPException(status_code=404, detail="No harness session found")
    return {"status": "ok", "session": record}


@app.post("/harness/sessions/{session_id}/compact")
async def compact_harness_session(session_id: str, user_id: str = "default"):
    record = _compact_harness_session(user_id, session_id)
    if not record:
        raise HTTPException(status_code=404, detail="No harness session found")
    context = _build_harness_context_bundle(user_id, session_id)
    return {"status": "ok", "session": record, "context": context}


@app.post("/harness/sessions/{session_id}/fork")
async def fork_harness_session(session_id: str, user_id: str = "default", title: Optional[str] = None):
    record = _fork_harness_session(user_id, session_id, title=title)
    if not record:
        raise HTTPException(status_code=404, detail="No harness session found")
    return {"status": "ok", "session": record}


@app.get("/plan/{session_id}")
async def get_plan(session_id: str):
    plan_path = Path.home() / ".narad" / "plans" / f"{session_id}.json"
    if not plan_path.exists():
        raise HTTPException(status_code=404, detail="No plan found for session")
    return json.loads(plan_path.read_text())


@app.get("/sutras")
async def get_sutras():
    from sutra_engine import COOLDOWN_HOURS, get_all_sutras
    from tapas import PROMOTE_THRESHOLD, sutra_summary
    return {
        "summary": sutra_summary(),
        "settings": {
            "promote_threshold": PROMOTE_THRESHOLD,
            "cooldown_hours": COOLDOWN_HOURS,
            "auto_promote_after_hours": COOLDOWN_HOURS,
        },
        "sutras": get_all_sutras(),
    }


@app.post("/sutras/{sutra_id}/accept")
async def accept_sutra_endpoint(sutra_id: str):
    from sutra_engine import accept_sutra
    ok = accept_sutra(sutra_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Sutra not found")
    return {"ok": True, "sutra_id": sutra_id, "action": "accepted"}


@app.post("/sutras/{sutra_id}/revert")
async def revert_sutra_endpoint(sutra_id: str):
    from sutra_engine import revert_sutra
    ok = revert_sutra(sutra_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Sutra not found")
    return {"ok": True, "sutra_id": sutra_id, "action": "reverted"}


@app.get("/karma")
async def get_karma():
    from karma_log import karma_summary
    return karma_summary()


@app.get("/karma/mutations")
async def get_karma_mutations(limit: int = 100):
    from karma_log import load_mutations
    return {"mutations": load_mutations(limit=limit)}


@app.get("/sankalpa")
async def get_sankalpa(user_id: str = "default"):
    from sankalpa import get_all_sankalpas, sankalpa_summary

    from smriti_core import load_commitments
    return {
        "summary":    sankalpa_summary(user_id),
        "sankalpas":  get_all_sankalpas(user_id),
        "commitments": load_commitments(user_id),
    }


@app.post("/sankalpa/{sankalpa_id}/accept")
async def accept_sankalpa_endpoint(sankalpa_id: str, user_id: str = "default"):
    from sankalpa import accept_sankalpa
    ok = accept_sankalpa(sankalpa_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Sankalpa not found")
    return {"ok": True, "sankalpa_id": sankalpa_id, "action": "accepted"}


@app.post("/sankalpa/{sankalpa_id}/revert")
async def revert_sankalpa_endpoint(sankalpa_id: str, user_id: str = "default"):
    from sankalpa import revert_sankalpa
    ok = revert_sankalpa(sankalpa_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Sankalpa not found")
    return {"ok": True, "sankalpa_id": sankalpa_id, "action": "reverted"}


# ── Phase 13: Six Sigma endpoints ────────────────────────────────────────────

# Karyakrama Kanban
@app.get("/kanban/{session_id}")
async def get_kanban_board(session_id: str):
    from kanban import KanbanBoard
    return KanbanBoard().get_board(session_id)


@app.get("/kanban")
async def get_all_kanban():
    from kanban import KanbanBoard
    return {"boards": KanbanBoard().get_all_active()}


# Jaagruti Andon
@app.get("/andon/log")
async def get_andon_log(limit: int = 50):
    from andon import load_andon_log
    return {"events": load_andon_log(limit=limit)}


@app.get("/andon/stats")
async def get_andon_stats(days: int = 7):
    from andon import andon_stats
    return andon_stats(days=days)


# Shuddhi 5S
@app.get("/5s/report")
async def get_5s_report():
    from narad_5s import NaradShuddhi
    return NaradShuddhi().report()


@app.post("/5s/shine")
async def run_5s_shine(dry_run: bool = True):
    from narad_5s import NaradShuddhi
    return NaradShuddhi().shine(dry_run=dry_run)


# Viveka DMAIC quality report
_last_quality_report: dict | None = None


@app.post("/quality/report")
async def generate_quality_report(user_id: str = "default"):
    global _last_quality_report

    from andon import andon_stats, load_andon_log

    # Assemble metrics packet for the canonical quality-auditor path
    stats = andon_stats(days=7)
    recent_andon = load_andon_log(limit=20)

    metrics_packet = {
        "period": "last_7_days",
        "andon_stats": stats,
        "recent_andon_events": recent_andon,
    }

    try:
        sessions = list((Path.home() / ".narad" / "sessions").glob("*.jsonl"))
        metrics_packet["session_count_7d"] = len(sessions)
    except Exception:
        pass

    # Invoke Parashurama with a structured DMAIC task
    from avatar_agents import parashurama
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types as genai_types

    dmaic_task = (
        "VIVEKA DMAIC QUALITY REPORT\n\n"
        "Metrics for the last 7 days:\n"
        f"{json.dumps(metrics_packet, indent=2)}\n\n"
        "Produce a structured 5-section DMAIC report:\n"
        "DEFINE: Top task types; CTQs\n"
        "MEASURE: Avg score by avatar; error/andon rates; P95 latency\n"
        "ANALYZE: Which avatar fires Andon most; which task types score lowest\n"
        "IMPROVE: Patterns learned; recovery options used\n"
        "CONTROL: Trend vs prior period\n\n"
        "Be concise — 3–5 bullet points per section. No JSON in output."
    )

    try:
        svc = InMemorySessionService()
        sid = str(uuid.uuid4())
        await svc.create_session(app_name="quality_report", user_id="narad", session_id=sid)
        runner = Runner(agent=parashurama, app_name="quality_report", session_service=svc)
        msg = genai_types.Content(role="user", parts=[genai_types.Part(text=dmaic_task)])
        report_text = ""
        async for event in runner.run_async(user_id="narad", session_id=sid, new_message=msg):
            if event.is_final_response() and event.content and event.content.parts:
                report_text = "".join(p.text or "" for p in event.content.parts)

        # Save to wiki
        try:
            wiki_dir = Path.home() / ".narad" / "wiki" / user_id / "quality"
            wiki_dir.mkdir(parents=True, exist_ok=True)
            date_str = __import__("datetime").date.today().isoformat()
            report_path = wiki_dir / f"DMAIC_{date_str}.md"
            report_path.write_text(report_text)
        except Exception:
            pass

        _last_quality_report = {
            "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            "report": report_text,
            "metrics": metrics_packet,
        }
        return _last_quality_report

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Quality report generation failed: {exc}")


@app.get("/quality/report")
async def get_quality_report():
    if _last_quality_report is None:
        raise HTTPException(status_code=404, detail="No quality report generated yet. POST /quality/report first.")
    return _last_quality_report


# ── Notion sync endpoints ─────────────────────────────────────────────────────

@app.get("/notion/status")
async def notion_status():
    from notion_sync import NotionSync
    return NotionSync().get_status()


@app.post("/notion/setup")
async def notion_setup(parent_page_id: str):
    from notion_sync import NotionSync
    return NotionSync().setup_workspace(parent_page_id)


@app.post("/notion/sync")
async def notion_sync_endpoint(user_id: str = "default"):
    from notion_sync import NotionSync
    return await NotionSync().sync_all(user_id)


# ── Cultural-core endpoints ───────────────────────────────────────────────────

@app.post("/swapna/run")
async def run_swapna_endpoint(
    user_id: str = "default",
    project_id: str = "general",
    max_episodes: int = 20,
    apply: bool = False,
):
    from swapna import dream
    return dream(
        user_id=user_id,
        project_id=project_id,
        max_episodes=max_episodes,
        apply=apply,
    )


@app.get("/swapna/inbox")
async def get_swapna_inbox():
    from swapna import inbox
    return {"items": inbox()}


@app.get("/provenance/{entity_id}")
async def get_provenance_endpoint(entity_id: str, user_id: str = "default"):
    from smriti_core import get_provenance
    return get_provenance(entity_id, user_id=user_id)


@app.get("/architecture/scorecard")
async def get_architecture_scorecard():
    from smriti_core import architecture_scorecard
    return architecture_scorecard()


@app.get("/evolution/history")
async def get_evolution_history(days: int = 30):
    from smriti_core import evolution_history
    return evolution_history(days=days)


# ── Memory query endpoint ─────────────────────────────────────────────────────

@app.get("/memory")
async def query_memory(
    user_id: str = "default",
    avatar: Optional[str] = None,
    days: Optional[int] = None,
    memory_type: Optional[str] = None,
    limit: int = 50,
    q: Optional[str] = None,
):
    """Query Smriti memories with optional filters."""
    from datetime import datetime, timedelta
    try:
        from smriti import _get_table  # type: ignore
        table = _get_table()
        raw = (
            table.search()
            .where(f"user_id = '{user_id}'", prefilter=True)
            .limit(limit * 4)
            .to_list()
        )
    except Exception:
        return []

    cutoff = None
    if days:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()

    results = []
    for row in raw:
        if cutoff and row.get("created_at", "") < cutoff:
            continue
        if avatar and row.get("avatar") != avatar:
            continue
        text = row.get("memory", "")
        if q and q.lower() not in text.lower():
            continue
        tl = text.lower()
        if any(w in tl for w in ["decided", "decision", "chose", "choice"]):
            mtype = "decision"
        elif any(w in tl for w in ["implement", "built", "wrote", "created", "feature"]):
            mtype = "feature"
        elif any(w in tl for w in ["goal", "objective", "plan", "milestone"]):
            mtype = "goal"
        else:
            mtype = "insight"
        if memory_type and memory_type != mtype:
            continue
        results.append({
            "id":         row.get("id", ""),
            "avatar":     row.get("avatar", ""),
            "text":       text,
            "created_at": row.get("created_at", ""),
            "type":       mtype,
        })

    results.sort(key=lambda x: x["created_at"], reverse=True)
    return results[:limit]


@app.get("/memory/tiers")
async def get_memory_tiers(user_id: str = "default"):
    from smriti_core import memory_tier_diagnostics

    return memory_tier_diagnostics(user_id)


# ── Unified search endpoint ───────────────────────────────────────────────────

@app.get("/search")
async def unified_search(
    q: str,
    user_id: str = "default",
    limit: int = 20,
):
    """Search across memories, sutras, kanban steps, and andon log."""
    if not q or len(q.strip()) < 2:
        return []

    results: list[dict] = []
    q_lower = q.lower()

    # Memories (FTS5)
    try:
        from smriti import recall_exact  # type: ignore
        mem_text = recall_exact(q, user_id=user_id, limit=5)
        for line in mem_text.strip().split("\n"):
            line = line.strip()
            if line.startswith("- ["):
                parts = line[3:].split("]", 1)
                if len(parts) == 2:
                    results.append({
                        "id": f"mem_{len(results)}",
                        "type": "memory",
                        "avatar": parts[0],
                        "preview": parts[1].strip()[:120],
                        "ts": "",
                        "nav": "memory",
                    })
    except Exception:
        pass

    # Sutras
    try:
        from sutra_engine import get_all_sutras  # type: ignore
        sutra_count = 0
        for s in get_all_sutras():
            if q_lower in s.get("query", "").lower() or q_lower in s.get("result", "").lower():
                results.append({
                    "id": s.get("id", ""),
                    "type": "sutra",
                    "avatar": s.get("avatar", ""),
                    "preview": s.get("query", "")[:120],
                    "ts": s.get("ts", ""),
                    "nav": "sutras",
                })
                sutra_count += 1
                if sutra_count >= 5:
                    break
    except Exception:
        pass

    # Kanban steps (active sessions)
    try:
        from kanban import KanbanBoard  # type: ignore
        for board in KanbanBoard().get_all_active():
            for col_steps in board.get("columns", {}).values():
                for step in col_steps:
                    if q_lower in step.get("title", "").lower():
                        results.append({
                            "id": f"step_{step.get('session_id','')}_{step.get('step_id','')}",
                            "type": "plan",
                            "avatar": step.get("owner", ""),
                            "preview": step.get("title", "")[:120],
                            "ts": step.get("started_at") or step.get("completed_at") or "",
                            "nav": "kanban",
                        })
    except Exception:
        pass

    # Andon log
    try:
        from andon import load_andon_log  # type: ignore
        andon_count = 0
        for e in load_andon_log(limit=50):
            if q_lower in e.get("task_preview", "").lower() or q_lower in e.get("trigger", "").lower():
                results.append({
                    "id": e.get("id", ""),
                    "type": "andon",
                    "avatar": e.get("avatar", ""),
                    "preview": f"{e.get('trigger','')} — {e.get('task_preview','')[:80]}",
                    "ts": e.get("ts", ""),
                    "nav": "ops",
                })
                andon_count += 1
                if andon_count >= 3:
                    break
    except Exception:
        pass

    # Audit log
    try:
        import json as _json
        _audit_path = Path.home() / ".narad" / "audit.jsonl"
        if _audit_path.exists():
            audit_count = 0
            for raw in reversed(_audit_path.read_text().splitlines()):
                raw = raw.strip()
                if not raw:
                    continue
                entry = _json.loads(raw)
                preview = entry.get("task_preview", "")
                if q_lower in preview.lower() or q_lower in entry.get("avatar", "").lower():
                    results.append({
                        "id": f"audit_{len(results)}",
                        "type": "audit",
                        "avatar": entry.get("avatar", ""),
                        "preview": preview[:120],
                        "ts": entry.get("ts", ""),
                        "nav": "audit",
                        "event": entry.get("event", "invocation"),
                        "matched_signals": entry.get("matched_signals"),
                    })
                    audit_count += 1
                    if audit_count >= 3:
                        break
    except Exception:
        pass

    type_order = {"memory": 0, "sutra": 1, "plan": 2, "andon": 3, "audit": 4}
    results.sort(key=lambda x: type_order.get(x["type"], 9))
    return results[:limit]


# ── Audit log endpoint ────────────────────────────────────────────────────────

@app.get("/audit")
async def get_audit_log(
    user_id: str = "default",
    limit: int = 50,
    event: Optional[str] = None,
):
    """Return recent audit invocation records from ~/.narad/audit.jsonl."""
    import json as _json
    _audit_path = Path.home() / ".narad" / "audit.jsonl"
    if not _audit_path.exists():
        return []
    lines = [l.strip() for l in _audit_path.read_text().splitlines() if l.strip()]
    records: list[dict] = []
    for raw in reversed(lines):
        try:
            entry = _json.loads(raw)
        except Exception:
            continue
        if user_id and entry.get("user_id", "default") != user_id:
            continue
        if event and entry.get("event") != event:
            continue
        records.append(entry)
        if len(records) >= limit:
            break
    return records


# ── Context sandbox expand endpoint ──────────────────────────────────────────

@app.get("/sandbox/{doc_id}")
async def expand_sandbox(doc_id: str):
    """Retrieve full (uncompressed) output from context_sandbox by UUID."""
    try:
        from context_sandbox import expand_context  # type: ignore
        text = expand_context(doc_id)
        if text.startswith("[context_sandbox"):
            raise HTTPException(status_code=404, detail=text)
        return {"doc_id": doc_id, "content": text, "word_count": len(text.split())}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Daily Shuddhi background loop ─────────────────────────────────────────────

async def _daily_shuddhi_loop():
    """Run a dry-run Shuddhi cycle every 24 hours and emit a Yantra event."""
    while True:
        await asyncio.sleep(86_400)
        try:
            from narad_5s import NaradShuddhi
            NaradShuddhi().sustain()
            logging.getLogger("narad.server").info("Daily Shuddhi cycle complete.")
        except Exception as exc:
            logging.getLogger("narad.server").warning("Daily Shuddhi failed: %s", exc)


@app.on_event("startup")
async def _start_background_tasks():
    asyncio.create_task(_daily_shuddhi_loop())


class ThinkingFilter:
    """Per-request stateful filter — strips <think>…</think> across streaming chunks.

    DeepSeek streams chain-of-thought across many small SSE chunks; a single-pass
    regex can only match complete tags inside one chunk and silently passes partial
    tags through. This class buffers across calls so the tag boundaries are always
    found regardless of how the model slices its output.
    """
    _OPEN  = "<think>"
    _CLOSE = "</think>"

    def __init__(self) -> None:
        self._buf    = ""
        self._inside = False

    def feed(self, chunk: str) -> str:
        """Feed one streaming chunk; return text that should reach the client."""
        self._buf += chunk
        out: list[str] = []

        while self._buf:
            if self._inside:
                idx = self._buf.lower().find(self._CLOSE)
                if idx == -1:
                    # Closing tag may be split — keep last N chars safe
                    safe = max(0, len(self._buf) - len(self._CLOSE))
                    self._buf = self._buf[safe:]
                    break
                self._buf    = self._buf[idx + len(self._CLOSE):]
                self._inside = False
            else:
                idx = self._buf.lower().find(self._OPEN)
                if idx == -1:
                    # No opening tag — tail might be a partial "<think…"
                    for tail in range(min(len(self._OPEN) - 1, len(self._buf)), 0, -1):
                        if self._buf[-tail:].lower() == self._OPEN[:tail]:
                            out.append(self._buf[:-tail])
                            self._buf = self._buf[-tail:]
                            return "".join(out)
                    out.append(self._buf)
                    self._buf = ""
                    break
                out.append(self._buf[:idx])
                self._buf    = self._buf[idx + len(self._OPEN):]
                self._inside = True

        return "".join(out)

    def flush(self) -> str:
        """Return any buffered text after the stream ends (empty if mid-block)."""
        if self._inside:
            return ""
        result    = self._buf
        self._buf = ""
        return result


def _event_to_sse(event: Event, think: "ThinkingFilter | None" = None) -> list[str]:
    """Convert one ADK event to one or more SSE JSON strings.

    Narad can emit multiple function_call parts in a single event when routing
    to avatars in parallel — returning a list ensures every avatar_start fires.

    *think* is a per-request ThinkingFilter that strips <think>…</think> blocks
    correctly across streaming chunk boundaries.
    """
    def _filt(text: str) -> str:
        return think.feed(text) if think is not None else text

    if event.is_final_response():
        text = ""
        if event.content and event.content.parts:
            text = "".join(p.text or "" for p in event.content.parts)
        text = _filt(text).strip()
        if not text:
            return []
        return [json.dumps({"type": "narad_synthesis", "data": {"text": text}})]

    if not (event.content and event.content.parts):
        return [json.dumps({"type": "unknown", "data": {}})]

    payloads: list[str] = []
    for part in event.content.parts:
        if part.function_call:
            fc = part.function_call
            avatar = _resolve_avatar(fc.name)
            discipline = _primary_discipline(avatar)
            payloads.append(json.dumps({
                "type": "avatar_start",
                "data": {
                    "avatar": avatar,
                    "discipline": discipline,
                    "disciplines": _agent_contract_map().get(avatar, {}).get("disciplines", []),
                    "task": (fc.args or {}).get("request", ""),
                },
            }))
        elif part.function_response:
            fr = part.function_response
            avatar = _resolve_avatar(fr.name)
            discipline = _primary_discipline(avatar)
            payloads.append(json.dumps({
                "type": "avatar_done",
                "data": {
                    "avatar": avatar,
                    "discipline": discipline,
                    "disciplines": _agent_contract_map().get(avatar, {}).get("disciplines", []),
                    "result": fr.response,
                },
            }))
        elif part.text:
            # Non-final text events are always Narad's internal routing thoughts or
            # pre-emission tokens — never user-facing content. Suppress them entirely.
            # The is_final_response() path above emits the complete synthesis once ready.
            pass

    return payloads or [json.dumps({"type": "unknown", "data": {}})]


def _usage_to_sse(event: Event) -> str | None:
    """Emit a usage event for the final response event only.

    ADK attaches usage_metadata to many intermediate events (tool calls, etc.)
    with cumulative but partial counts. Only the final response event carries
    the complete turn total — gating here means exactly one usage event per turn,
    always after narad_synthesis has fired so client timing is correct.
    """
    if not event.is_final_response():
        return None
    um = event.usage_metadata
    if not um:
        return None
    prompt_toks      = um.prompt_token_count      or 0
    completion_toks  = um.candidates_token_count  or 0
    thoughts_toks    = um.thoughts_token_count    or 0
    total_toks       = um.total_token_count        or 0
    if total_toks == 0:
        return None
    return json.dumps({
        "type": "usage",
        "data": {
            "prompt_tokens":     prompt_toks,
            "completion_tokens": completion_toks,
            "thoughts_tokens":   thoughts_toks,
            "total_tokens":      total_toks,
        },
    })


def _resolve_avatar(tool_name: str) -> str:
    lower = tool_name.lower()
    tool_names = AGENT_TOOL_NAMES or _canonical_tool_name_map()
    # phase-1: invoke_matsya → Matsya; phase-0b compat: matsya → Matsya
    return tool_names.get(lower, tool_names.get(lower.replace("invoke_", ""), tool_name.capitalize()))


import re as _re


def _extract_artifact_meta(task: str) -> tuple[str, str]:
    """Return (topic, artifact_type) from a legacy artifact task string."""
    m = _re.search(
        r"interactive\s+(flashcard\s+set|diagram)\s+on:?\s*[\"']?(.+?)[\"']?(?:\.|$)",
        task, _re.IGNORECASE
    )
    if m:
        kind = "flashcards" if "flashcard" in m.group(1).lower() else "concept_map"
        topic = m.group(2).strip().rstrip(".")
        return topic, kind
    # Fallback: anything after "on:"
    m2 = _re.search(r"on:?\s*[\"']?(.+?)[\"']?(?:\.|$)", task, _re.IGNORECASE)
    topic = m2.group(1).strip().rstrip(".") if m2 else "this topic"
    return topic, "flashcards"


def _extract_learning_artifact_request(
    text: str,
    *,
    offer_pending: bool = False,
    fallback_topic: str = "this topic",
) -> tuple[str, str] | None:
    normalized = (text or "").strip()
    lower = normalized.lower()
    if not lower:
        return None

    if offer_pending:
        if lower == "d":
            return fallback_topic, "diagram"
        if _is_affirmative_reply(lower):
            return fallback_topic, "flashcards"

    flashcard_match = _re.search(
        r"\b(?:make|create|build|generate)?\s*flashcards?\s+(?:for|on|about)\s+(.+?)(?:[.?!]|$)",
        normalized,
        _re.IGNORECASE,
    )
    if flashcard_match:
        return flashcard_match.group(1).strip().rstrip(".?!"), "flashcards"

    diagram_match = _re.search(
        r"\b(?:create|make|build|generate|draw)?\s*(?:a\s+)?(?:concept\s+diagram|concept\s+map|diagram|visuali[sz]ation)\s+(?:for|of|on|about)\s+(.+?)(?:[.?!]|$)",
        normalized,
        _re.IGNORECASE,
    )
    if diagram_match:
        return diagram_match.group(1).strip().rstrip(".?!"), "concept_map"

    if any(_re.search(pattern, lower) for pattern in (r"\bflashcards?\b", r"\bstudy cards?\b")):
        return fallback_topic, "flashcards"
    if any(_re.search(pattern, lower) for pattern in (r"\bconcept map\b", r"\bdiagram\b", r"\bvisuali[sz]e\b")):
        return fallback_topic, "concept_map"
    return None


def _is_affirmative_reply(text: str) -> bool:
    normalized = (text or "").strip().lower()
    return normalized in {
        "yes", "y", "yeah", "yep", "sure", "ok", "okay", "go ahead",
        "proceed", "do it", "make it", "build it",
    }


def _is_explicit_learning_artifact_request(text: str, *, offer_pending: bool = False) -> bool:
    return _extract_learning_artifact_request(
        text,
        offer_pending=offer_pending,
    ) is not None


def _normalize_learning_artifact_type(artifact_type: str) -> str:
    return "concept_map" if artifact_type in {"diagram", "concept_map", "concept map"} else "flashcards"


def _learning_artifact_label(artifact_type: str) -> str:
    return "flashcard set" if _normalize_learning_artifact_type(artifact_type) == "flashcards" else "concept map"


def _is_explicit_learning_artifact_edit(
    text: str,
    *,
    artifact_type: str | None = None,
) -> bool:
    lowered = (text or "").strip().lower()
    if not lowered:
        return False
    normalized_type = _normalize_learning_artifact_type(artifact_type or "flashcards")
    if normalized_type == "concept_map":
        edit_phrases = (
            "add a node",
            "add node",
            "add a branch",
            "remove node",
            "delete node",
            "connect ",
            "expand the concept map",
            "update the concept map",
            "update the diagram",
        )
    else:
        edit_phrases = (
            "add one more card",
            "add another card",
            "add a card",
            "remove card",
            "delete card",
            "drop the card",
            "update the flashcards",
        )
    return any(phrase in lowered for phrase in edit_phrases)


def _artifact_session_payload(
    artifact: dict[str, Any],
    *,
    record_ids: list[str] | None = None,
) -> dict[str, Any]:
    merged_record_ids = list(dict.fromkeys([
        *(artifact.get("record_ids") or []),
        *(record_ids or []),
    ]))
    return {
        "artifact_id": artifact["artifact_id"],
        "workspace_id": artifact["workspace_id"],
        "topic": artifact["topic"],
        "artifact_type": _normalize_learning_artifact_type(str(artifact["artifact_type"])),
        "version": artifact["version"],
        "status": artifact.get("status", "active"),
        "updated_at": artifact["updated_at"],
        "record_ids": merged_record_ids,
        "doc": artifact.get("doc") or {},
    }


def _learning_artifact_offer_pending(text: str) -> bool:
    normalized = (text or "").strip().lower()
    return (
        "would you like me to create a visual learning artifact" in normalized
        or "flashcards, an interactive quiz, or a diagram" in normalized
    )
