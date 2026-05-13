"""
Phase 1 FastAPI SSE server (+ Smriti memory + Yantra observability).

SSE event taxonomy (locked):
  avatar_start | avatar_done | narad_synthesis | done | error

New endpoints:
  GET /trace/{session_id}  — structured trace for a completed session
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import traceback
import uuid
from pathlib import Path
from typing import AsyncGenerator

_log = logging.getLogger("narad.json_patch")

# Load .env from the project root before any other imports that read env vars.
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "phase-2"))
sys.path.insert(0, str(_ROOT / "phase-3"))
sys.path.insert(0, str(_ROOT / "phase-5"))
sys.path.insert(0, str(_ROOT / "phase-6"))

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
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.events import Event

from narad_agent import build_narad_agent
from avatar_agents import AGENT_TOOL_NAMES, _images_ctx
from yantra import Tracer

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

app = FastAPI(title="Avatara — Narad API", version="0.1.0-phase1")

# ── Phase 11: Project Wiki + Projects + Sessions API ──────────────────────────
try:
    sys.path.insert(0, str(_ROOT / "phase-9"))
    from project_wiki_api import wiki_router, projects_router, sessions_router
    app.include_router(wiki_router)
    app.include_router(projects_router)
    app.include_router(sessions_router)
except Exception as _wiki_err:
    logging.getLogger("narad.server").warning("Project routers unavailable: %s", _wiki_err)

# ── TTS (Sarvam voice) ────────────────────────────────────────────────────────
try:
    from tts_api import tts_router
    app.include_router(tts_router)
except Exception as _tts_err:
    logging.getLogger("narad.server").warning("TTS router unavailable: %s", _tts_err)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve generated media files (video + audio from Parashurama)
sys.path.insert(0, str(_ROOT))
from narad_config import ARTIFACTS_DIR as _MEDIA_DIR
app.mount("/media", StaticFiles(directory=_MEDIA_DIR), name="media")

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
_user_runners: dict[str, Runner] = {}

def _get_runner_for_user(user_id: str) -> Runner:
    if user_id not in _user_runners:
        from narad_agent import build_narad_agent as _build
        narad = _build(user_id=user_id)
        svc = InMemorySessionService()
        _user_runners[user_id] = Runner(agent=narad, app_name="avatara", session_service=svc)
    return _user_runners[user_id]


# Background task registry: session_id → (task, event_queue)
# The ADK run lives here, decoupled from the SSE stream. If the client
# disconnects (screen lock, browser throttle) and reconnects, the task
# keeps running and the client re-attaches to the same queue.
_active_tasks: dict[str, tuple[asyncio.Task, asyncio.Queue]] = {}


class ChatRequest(BaseModel):
    query: str
    session_id: str | None = None
    user_id: str = "default"
    images: list[str] = []


@app.get("/health")
async def health():
    from model_config import AVATAR_MODELS
    return {
        "status": "ok",
        "agent": "Narad",
        "phase": "1",
        "model": AVATAR_MODELS.get("narad", "unknown"),
    }


@app.post("/chat")
async def chat(req: ChatRequest):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query cannot be empty")

    # Dharma Gate: block hard-forbidden inputs before any agent work starts
    block_reason = _dharma_gate(req.query)
    if block_reason:
        async def _blocked_stream():
            yield json.dumps({"type": "error", "data": {"message": block_reason}})
            yield json.dumps({"type": "done",  "data": {"session_id": "blocked"}})
        return EventSourceResponse(_blocked_stream())

    # Rate limiting: 10 req/min per user_id by default
    if not _check_rate_limit(req.user_id):
        from fastapi.responses import JSONResponse
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

        runner = _get_runner_for_user(req.user_id)
        tracer = Tracer(session_id=session_id, user_id=req.user_id)

        existing = await runner.session_service.get_session(
            app_name="avatara", user_id=req.user_id, session_id=session_id
        )
        if existing is None:
            await runner.session_service.create_session(
                app_name="avatara", user_id=req.user_id, session_id=session_id
            )

        from google.genai import types as genai_types

        user_message = genai_types.Content(
            role="user", parts=[genai_types.Part(text=req.query)]
        )

        # Share the SSE queue, images, and HTTP session_id with avatar tool execution
        from avatar_agents import _step_queue_ctx, _http_session_id_ctx
        _step_queue_ctx.set(queue)
        _images_ctx.set(req.images)
        _http_session_id_ctx.set(session_id)

        tracer.session_start(req.query)

        async for event in runner.run_async(
            user_id=req.user_id, session_id=session_id, new_message=user_message
        ):
            sse_payload = _event_to_sse(event)
            await queue.put(sse_payload)
            usage_payload = _usage_to_sse(event)
            if usage_payload:
                await queue.put(usage_payload)
            # Detect Parashurama CopilotKit tasks → emit learning_artifact event
            try:
                evt_data = json.loads(sse_payload)
                if (evt_data.get("type") == "avatar_start"
                        and evt_data["data"].get("avatar") == "Parashurama"
                        and "copilotkit" in evt_data["data"].get("task", "").lower()):
                    topic, atype = _extract_artifact_meta(evt_data["data"]["task"])
                    await queue.put(json.dumps({
                        "type": "learning_artifact",
                        "data": {"topic": topic, "artifact_type": atype},
                    }))
            except Exception:
                pass

        tracer.session_done()
        await queue.put(json.dumps({"type": "done", "data": {"session_id": session_id}}))

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
            runner = _get_runner_for_user(req.user_id)
            await runner.session_service.delete_session(
                app_name="avatara", user_id=req.user_id, session_id=session_id
            )
        except Exception:
            pass
        await queue.put(json.dumps({"type": "error", "data": {"message": str(exc)}}))

    finally:
        if caffeinate is not None:
            caffeinate.terminate()
        await queue.put(None)  # sentinel — signals _drain_queue to stop
        _active_tasks.pop(session_id, None)
        try:
            from avatar_agents import evict_session_state
            evict_session_state(req.user_id, session_id)
        except Exception:
            pass


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


@app.get("/plan/{session_id}")
async def get_plan(session_id: str):
    plan_path = Path.home() / ".narad" / "plans" / f"{session_id}.json"
    if not plan_path.exists():
        raise HTTPException(status_code=404, detail="No plan found for session")
    return json.loads(plan_path.read_text())


@app.get("/sutras")
async def get_sutras():
    from sutra_engine import get_all_sutras
    from tapas import sutra_summary
    return {"summary": sutra_summary(), "sutras": get_all_sutras()}


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


@app.get("/sankalpa")
async def get_sankalpa(user_id: str = "default"):
    from sankalpa import get_all_sankalpas, sankalpa_summary
    return {
        "summary":    sankalpa_summary(user_id),
        "sankalpas":  get_all_sankalpas(user_id),
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
    sys.path.insert(0, str(_ROOT / "phase-1"))
    from kanban import KanbanBoard
    return KanbanBoard().get_board(session_id)


@app.get("/kanban")
async def get_all_kanban():
    sys.path.insert(0, str(_ROOT / "phase-1"))
    from kanban import KanbanBoard
    return {"boards": KanbanBoard().get_all_active()}


# Jaagruti Andon
@app.get("/andon/log")
async def get_andon_log(limit: int = 50):
    sys.path.insert(0, str(_ROOT / "phase-1"))
    from andon import load_andon_log
    return {"events": load_andon_log(limit=limit)}


@app.get("/andon/stats")
async def get_andon_stats(days: int = 7):
    sys.path.insert(0, str(_ROOT / "phase-1"))
    from andon import andon_stats
    return andon_stats(days=days)


# Shuddhi 5S
@app.get("/5s/report")
async def get_5s_report():
    sys.path.insert(0, str(_ROOT / "phase-1"))
    from narad_5s import NaradShuddhi
    return NaradShuddhi().report()


@app.post("/5s/shine")
async def run_5s_shine(dry_run: bool = True):
    sys.path.insert(0, str(_ROOT / "phase-1"))
    from narad_5s import NaradShuddhi
    return NaradShuddhi().shine(dry_run=dry_run)


# Viveka DMAIC quality report
_last_quality_report: dict | None = None


@app.post("/quality/report")
async def generate_quality_report(user_id: str = "default"):
    global _last_quality_report
    sys.path.insert(0, str(_ROOT / "phase-1"))
    sys.path.insert(0, str(_ROOT / "phase-2"))
    sys.path.insert(0, str(_ROOT / "phase-3"))

    from andon import andon_stats, load_andon_log

    # Assemble metrics packet for Buddha
    stats = andon_stats(days=7)
    recent_andon = load_andon_log(limit=20)

    metrics_packet = {
        "period": "last_7_days",
        "andon_stats": stats,
        "recent_andon_events": recent_andon,
    }

    try:
        from phase_2_yantra import tracer
        from phase_3_tapas import TapasScorer
        sessions = list((Path.home() / ".narad" / "sessions").glob("*.jsonl"))
        metrics_packet["session_count_7d"] = len(sessions)
    except Exception:
        pass

    # Invoke Buddha with DMAIC task
    from avatar_agents import buddha, _make_avatar_tool
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
        runner = Runner(agent=buddha, app_name="quality_report", session_service=svc)
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


# ── Daily Shuddhi background loop ─────────────────────────────────────────────

async def _daily_shuddhi_loop():
    """Run a dry-run Shuddhi cycle every 24 hours and emit a Yantra event."""
    while True:
        await asyncio.sleep(86_400)
        try:
            sys.path.insert(0, str(_ROOT / "phase-1"))
            from narad_5s import NaradShuddhi
            NaradShuddhi().sustain()
            logging.getLogger("narad.server").info("Daily Shuddhi cycle complete.")
        except Exception as exc:
            logging.getLogger("narad.server").warning("Daily Shuddhi failed: %s", exc)


@app.on_event("startup")
async def _start_background_tasks():
    asyncio.create_task(_daily_shuddhi_loop())


def _event_to_sse(event: Event) -> str:
    payload: dict = {"type": "unknown", "data": {}}

    if event.is_final_response():
        text = ""
        if event.content and event.content.parts:
            text = "".join(p.text or "" for p in event.content.parts)
        payload = {"type": "narad_synthesis", "data": {"text": text}}

    elif event.content and event.content.parts:
        for part in event.content.parts:
            if part.function_call:
                fc = part.function_call
                avatar = _resolve_avatar(fc.name)
                payload = {
                    "type": "avatar_start",
                    "data": {
                        "avatar": avatar,
                        "task": (fc.args or {}).get("request", ""),
                    },
                }
            elif part.function_response:
                fr = part.function_response
                avatar = _resolve_avatar(fr.name)
                payload = {
                    "type": "avatar_done",
                    "data": {"avatar": avatar, "result": fr.response},
                }
            elif part.text:
                payload = {"type": "narad_synthesis", "data": {"text": part.text}}

    return json.dumps(payload)


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
    # phase-1: invoke_matsya → Matsya; phase-0b compat: matsya → Matsya
    return AGENT_TOOL_NAMES.get(lower, AGENT_TOOL_NAMES.get(lower.replace("invoke_", ""), tool_name.capitalize()))


import re as _re

def _extract_artifact_meta(task: str) -> tuple[str, str]:
    """Return (topic, artifact_type) from a Parashurama CopilotKit task string."""
    m = _re.search(
        r"interactive\s+(flashcard\s+set|diagram)\s+on:?\s*[\"']?(.+?)[\"']?(?:\.|$)",
        task, _re.IGNORECASE
    )
    if m:
        kind = "flashcards" if "flashcard" in m.group(1).lower() else "diagram"
        topic = m.group(2).strip().rstrip(".")
        return topic, kind
    # Fallback: anything after "on:"
    m2 = _re.search(r"on:?\s*[\"']?(.+?)[\"']?(?:\.|$)", task, _re.IGNORECASE)
    topic = m2.group(1).strip().rstrip(".") if m2 else "this topic"
    return topic, "flashcards"
