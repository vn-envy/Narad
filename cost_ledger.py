"""
Cost ledger for Narad (M4.1) — learning has a price tag.

Every LLM usage event (chat turns, Tapas judge + critique calls) is recorded
as one JSONL line with a cost computed at write time from a per-model price
table. Aggregation (`summarize`) rolls the ledger up per day / source / model
so the dashboard can answer "what did today cost, and how much of it was
learning overhead?"

Prices are defaults, USD per 1M tokens, matched by substring on the model
name. Override or extend without touching code:

  NARAD_MODEL_PRICES='{"deepseek-v4-pro": [0.60, 2.40], "my-model": [1.0, 3.0]}'

(input_per_1m, output_per_1m). Unknown models record cost 0.0 with
"priced": false — visible in the summary as unpriced tokens, never silently
wrong. Local models (ollama/) are pinned free.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta
from typing import Any

from narad_config import COST_LEDGER_PATH

# ── Price table (USD per 1M tokens: input, output) ────────────────────────────

_DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "deepseek-v4-pro":        (0.60, 2.40),
    "deepseek-v4-flash":      (0.10, 0.40),
    "text-embedding-3-small": (0.02, 0.0),
    "ollama/":                (0.0, 0.0),  # local — free
    "narad-local/":           (0.0, 0.0),  # bundled local brain (S1) — free
}

_write_lock = threading.Lock()


def _price_table() -> dict[str, tuple[float, float]]:
    table = dict(_DEFAULT_PRICES)
    raw = os.environ.get("NARAD_MODEL_PRICES", "")
    if raw:
        try:
            for model, pair in json.loads(raw).items():
                table[model] = (float(pair[0]), float(pair[1]))
        except (ValueError, TypeError, IndexError):
            pass  # malformed env → defaults; unpriced models surface in summary
    return table


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> tuple[float, bool]:
    """Return (cost_usd, priced). Longest-substring match wins; unknown → (0.0, False)."""
    model_l = (model or "").lower()
    best: tuple[float, float] | None = None
    best_len = 0
    for key, pair in _price_table().items():
        if key.lower() in model_l and len(key) > best_len:
            best, best_len = pair, len(key)
    if best is None:
        return 0.0, False
    cost = (prompt_tokens * best[0] + completion_tokens * best[1]) / 1_000_000
    return round(cost, 8), True


# ── Write path ────────────────────────────────────────────────────────────────

def record(
    *,
    source: str,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    thoughts_tokens: int = 0,
    user_id: str = "default",
    session_id: str = "",
) -> dict[str, Any]:
    """Append one usage event to the ledger. Never raises past the caller's turn.

    source: "turn" (chat), "tapas_judge", "tapas_critique", "swapna", ...
    """
    cost, priced = estimate_cost(model, prompt_tokens, completion_tokens + thoughts_tokens)
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "source": source,
        "model": model,
        "prompt_tokens": int(prompt_tokens),
        "completion_tokens": int(completion_tokens),
        "thoughts_tokens": int(thoughts_tokens),
        "cost_usd": cost,
        "priced": priced,
        "user_id": user_id,
        "session_id": session_id,
    }
    with _write_lock:
        COST_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with COST_LEDGER_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


# ── Read path ─────────────────────────────────────────────────────────────────

def summarize(days: int = 7, user_id: str | None = None) -> dict[str, Any]:
    """Roll the ledger up over the trailing `days`: totals, by_day, by_source, by_model."""
    days = max(1, min(days, 365))
    cutoff = datetime.now() - timedelta(days=days)
    total_usd = 0.0
    total_tokens = 0
    unpriced_tokens = 0
    by_day: dict[str, float] = {}
    by_source: dict[str, dict[str, float]] = {}
    by_model: dict[str, dict[str, float]] = {}
    entries = 0

    if COST_LEDGER_PATH.exists():
        with COST_LEDGER_PATH.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                    ts = datetime.fromisoformat(e["ts"])
                except (ValueError, KeyError, TypeError):
                    continue
                if ts < cutoff:
                    continue
                if user_id and e.get("user_id") != user_id:
                    continue
                entries += 1
                cost = float(e.get("cost_usd", 0.0))
                toks = (
                    int(e.get("prompt_tokens", 0))
                    + int(e.get("completion_tokens", 0))
                    + int(e.get("thoughts_tokens", 0))
                )
                total_usd += cost
                total_tokens += toks
                if not e.get("priced", True):
                    unpriced_tokens += toks
                day = ts.strftime("%Y-%m-%d")
                by_day[day] = round(by_day.get(day, 0.0) + cost, 8)
                for bucket, key in ((by_source, e.get("source", "?")), (by_model, e.get("model", "?"))):
                    slot = bucket.setdefault(key, {"cost_usd": 0.0, "tokens": 0})
                    slot["cost_usd"] = round(slot["cost_usd"] + cost, 8)
                    slot["tokens"] += toks

    return {
        "days": days,
        "entries": entries,
        "total_usd": round(total_usd, 6),
        "total_tokens": total_tokens,
        "unpriced_tokens": unpriced_tokens,
        "by_day": dict(sorted(by_day.items())),
        "by_source": by_source,
        "by_model": by_model,
    }
