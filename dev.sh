#!/usr/bin/env bash
#
# dev.sh — kick-start Narad locally: FastAPI backend + Vite frontend.
#
#   ./dev.sh
#
# Backend  → http://127.0.0.1:8000   (uvicorn, auth mode "local")
# Frontend → http://localhost:5173   (Vite dev server; api.ts targets the backend directly)
#
# Loads .env, waits for backend /health before starting the frontend, and
# shuts both processes down cleanly on Ctrl-C.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# 8000 is the canonical port (api.ts falls back to localhost:8000 in dev).
# A custom NARAD_PORT still works: we export VITE_API_BASE_URL below so the
# frontend targets whatever port the backend actually got.
BACKEND_HOST="${NARAD_HOST:-127.0.0.1}"
BACKEND_PORT="${NARAD_PORT:-8000}"
FRONTEND_DIR="$ROOT/phase-4/frontend"

log()  { printf '\033[1;36m[dev]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[dev]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[dev]\033[0m %s\n' "$*" >&2; exit 1; }

# ── 1. Load environment ───────────────────────────────────────────────────────
if [ -f "$ROOT/.env" ]; then
  log "Loading .env"
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
else
  warn "No .env found — the backend needs GEMINI_API_KEY and DEEPSEEK_API_KEY at minimum."
fi

# ── 2. Resolve the Python interpreter ─────────────────────────────────────────
if [ -x "$ROOT/.venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
elif command -v python3.12 >/dev/null 2>&1; then
  PY="$(command -v python3.12)"
else
  PY="$(command -v python3 || true)"
fi
[ -n "${PY:-}" ] || die "No Python interpreter found. Create one with: python3.12 -m venv .venv"
log "Python: $PY"

if ! "$PY" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
  die "Backend deps missing in $PY. Install with:
       $PY -m pip install -e .
     (or the per-phase requirements listed in README.md)"
fi

# ── 3. Start the backend ──────────────────────────────────────────────────────
log "Starting backend on http://$BACKEND_HOST:$BACKEND_PORT"
NARAD_HOST="$BACKEND_HOST" NARAD_PORT="$BACKEND_PORT" \
  "$PY" narad_server_entry.py --host "$BACKEND_HOST" --port "$BACKEND_PORT" &
BACKEND_PID=$!

cleanup() {
  log "Shutting down…"
  kill "$BACKEND_PID" 2>/dev/null || true
  [ -n "${FRONTEND_PID:-}" ] && kill "$FRONTEND_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# ── 4. Wait for backend health ────────────────────────────────────────────────
log "Waiting for backend to become healthy…"
for i in $(seq 1 40); do
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    die "Backend exited during startup — see the traceback above."
  fi
  if curl -fsS "http://$BACKEND_HOST:$BACKEND_PORT/health" >/dev/null 2>&1; then
    log "Backend healthy."
    break
  fi
  sleep 0.5
  [ "$i" -eq 40 ] && warn "Backend not healthy after 20s — continuing anyway."
done

# ── 5. Start the frontend ─────────────────────────────────────────────────────
[ -d "$FRONTEND_DIR" ] || die "Frontend dir not found: $FRONTEND_DIR"
if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
  log "Installing frontend deps (first run)…"
  (cd "$FRONTEND_DIR" && npm install)
fi

log "Starting frontend on http://localhost:5173"
(cd "$FRONTEND_DIR" && VITE_API_BASE_URL="http://127.0.0.1:$BACKEND_PORT" npm run dev) &
FRONTEND_PID=$!

log "Both servers up. Open http://localhost:5173  ·  Ctrl-C to stop."
wait
