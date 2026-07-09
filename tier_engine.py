"""
Sopan tier engine (S1) — hardware detection → deployment-tier recommendation.

Detects RAM, GPU/Apple Silicon, disk headroom, and CPU class, then walks the
Gemma 4 ladder (GURU-AND-ONBOARDING-PLAN.md Part C):

    <8 GB RAM            → T0 Kinara   · E2B QAT   (~3 GB)
    8–16 GB              → T1 Sthanik  · E4B QAT   (~5 GB; 12B-Q4 opt-in, reduced ctx)
    ≥16 GB               → T1 Sthanik  · 12B QAT Q4 (~7 GB, default)
    ≥32 GB               → T1 Sthanik  · 12B Q8    (~14 GB)
    ≥24 GB VRAM          → offer 26B-A4B (~15 GB) as an alternative
    cloud key present    → T4 Sangam (hybrid) recommended over pure local
    subscription present → T3 Sadasya available

`recommend()` is a pure function of a hardware dict (injectable for tests);
`detect_hardware()` is best-effort, stdlib-only, never raises. User override
always wins: `save_tier_choice` persists to onboarding.json and `tiers_payload`
surfaces the saved choice next to the recommendation. Wizard (O4) and doctor
(O7) both consume `tiers_payload()` via GET /tiers.

Speed estimates are deliberately coarse (order-of-magnitude honesty for the
wizard cards) — measured numbers replace them once the O2 runtime can bench.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from narad_config import NARAD_HOME, ONBOARDING_PATH

# ── Gemma 4 model catalog (S1 ladder; O2 downloads the weights) ───────────────

MODELS: dict[str, dict[str, Any]] = {
    "e2b": {
        "id": "narad-local/gemma4-e2b-it-qat",
        "label": "Gemma 4 E2B (edge)",
        "quant": "Q4 QAT",
        "download_gb": 3.0,
        "min_ram_gb": 4,
        "context_hint": "32K",
    },
    "e4b": {
        "id": "narad-local/gemma4-e4b-it-qat",
        "label": "Gemma 4 E4B (edge+)",
        "quant": "Q4 QAT",
        "download_gb": 5.0,
        "min_ram_gb": 8,
        "context_hint": "64K",
    },
    "12b-q4": {
        "id": "narad-local/gemma4-12b-it-qat",
        "label": "Gemma 4 12B (flagship)",
        "quant": "Q4 QAT",
        "download_gb": 7.0,
        "min_ram_gb": 8,   # runs at 8 GB with reduced context; 16 GB comfortable
        "context_hint": "128K (256K max)",
    },
    "12b-q8": {
        "id": "narad-local/gemma4-12b-it-q8",
        "label": "Gemma 4 12B (high precision)",
        "quant": "Q8",
        "download_gb": 14.0,
        "min_ram_gb": 32,
        "context_hint": "256K",
    },
    "26b-a4b": {
        "id": "narad-local/gemma4-26b-a4b",
        "label": "Gemma 4 26B-A4B (MoE, big GPU)",
        "quant": "A4B",
        "download_gb": 15.0,
        "min_ram_gb": 32,
        "min_vram_gb": 24,
        "context_hint": "256K",
    },
}

TIERS: dict[str, dict[str, str]] = {
    "T0": {"name": "Kinara",  "card": "Free & private, fits small devices"},
    "T1": {"name": "Sthanik", "card": "Free & private, on this device"},
    "T2": {"name": "Kunji",   "card": "Bring your own key"},
    "T3": {"name": "Sadasya", "card": "Use your Claude subscription"},
    "T4": {"name": "Sangam",  "card": "Best of both — local + cloud"},
}

_DISK_BUFFER_GB = 2.0  # headroom beyond the raw download

_CLOUD_KEY_ENVS = (
    "DEEPSEEK_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
)


# ── Hardware detection (best-effort, stdlib-only, never raises) ───────────────

def _ram_gb() -> float:
    try:
        if hasattr(os, "sysconf") and os.sysconf_names.get("SC_PHYS_PAGES"):
            return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1024**3
    except (ValueError, OSError, AttributeError):
        pass
    try:  # macOS fallback
        out = subprocess.run(["sysctl", "-n", "hw.memsize"],
                             capture_output=True, text=True, timeout=3)
        return int(out.stdout.strip()) / 1024**3
    except Exception:
        return 0.0


def _nvidia_vram_gb() -> float:
    if not shutil.which("nvidia-smi"):
        return 0.0
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        values = [float(v) for v in out.stdout.split() if v.strip().replace(".", "", 1).isdigit()]
        return max(values) / 1024 if values else 0.0
    except Exception:
        return 0.0


def _disk_free_gb() -> float:
    for candidate in (NARAD_HOME, Path.home()):
        try:
            return shutil.disk_usage(candidate).free / 1024**3
        except OSError:
            continue
    return 0.0


def detect_hardware() -> dict[str, Any]:
    """Best-effort hardware survey. Every field present even when unknown (0/False)."""
    apple_silicon = platform.system() == "Darwin" and platform.machine() in ("arm64", "aarch64")
    mlx_present = False
    try:
        import importlib.util
        mlx_present = importlib.util.find_spec("mlx") is not None
    except Exception:
        pass
    ram = round(_ram_gb(), 1)
    vram = round(_nvidia_vram_gb(), 1)
    if apple_silicon and not vram:
        vram = ram  # unified memory — the GPU sees system RAM
    return {
        "ram_gb": ram,
        "vram_gb": vram,
        "apple_silicon": apple_silicon,
        "mlx_present": mlx_present,
        "gpu": "apple-silicon" if apple_silicon else ("nvidia" if _nvidia_vram_gb() else "cpu"),
        "disk_free_gb": round(_disk_free_gb(), 1),
        "cpu_cores": os.cpu_count() or 0,
        "machine": platform.machine(),
        "system": platform.system(),
    }


def _has_cloud_key() -> bool:
    return any(os.environ.get(name, "").strip() for name in _CLOUD_KEY_ENVS)


def _has_subscription() -> bool:
    if os.environ.get("NARAD_CLAUDE_SUBSCRIPTION", "").strip():
        return True  # explicit flag — the pre-S3 escape hatch, kept
    try:
        from subscription_providers import subscription_active
        return subscription_active()
    except Exception:
        return False


# ── Speed estimate (coarse, honest) ───────────────────────────────────────────

def _est_tokens_per_sec(model_key: str, hw: dict[str, Any]) -> int:
    """Order-of-magnitude generation speed for the wizard card. Coarse on purpose."""
    base = {"e2b": 30, "e4b": 22, "12b-q4": 12, "12b-q8": 8, "26b-a4b": 20}.get(model_key, 10)
    if hw.get("apple_silicon"):
        factor = 2.0 if hw.get("ram_gb", 0) >= 32 else 1.5
    elif hw.get("gpu") == "nvidia" and hw.get("vram_gb", 0) >= 8:
        factor = 2.5
    else:  # CPU-only
        factor = 0.5
    return max(2, int(base * factor))


# ── Recommendation (pure function of the hardware dict) ──────────────────────

def _pick_model_key(hw: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    """(model_key, reasons, alternative_keys) from the Gemma 4 ladder."""
    ram = float(hw.get("ram_gb") or 0)
    vram = float(hw.get("vram_gb") or 0)
    reasons: list[str] = []
    alternatives: list[str] = []

    if ram and ram < 8:
        key = "e2b"
        reasons.append(f"{ram:.0f} GB RAM → edge model keeps everything responsive")
        alternatives = ["e4b"]
    elif ram < 16:
        key = "e4b"
        reasons.append(f"{ram:.0f} GB RAM → E4B is the comfortable fit")
        reasons.append("12B Q4 also runs here with reduced context — opt in if you prefer depth over speed")
        alternatives = ["12b-q4", "e2b"]
    elif ram < 32:
        key = "12b-q4"
        reasons.append(f"{ram:.0f} GB RAM → flagship 12B at Q4 (default)")
        alternatives = ["e4b"]
    else:
        key = "12b-q8"
        reasons.append(f"{ram:.0f} GB RAM → 12B at Q8 for maximum quality")
        alternatives = ["12b-q4"]
    if vram >= 24:
        alternatives = ["26b-a4b"] + [a for a in alternatives if a != "26b-a4b"]
        reasons.append(f"{vram:.0f} GB GPU memory → 26B-A4B available as an alternative")

    # Disk headroom: step down the ladder until the download fits.
    disk = float(hw.get("disk_free_gb") or 0)
    ladder_down = ["12b-q8", "12b-q4", "e4b", "e2b"]
    while disk and key in ladder_down:
        need = MODELS[key]["download_gb"] + _DISK_BUFFER_GB
        if disk >= need:
            break
        idx = ladder_down.index(key)
        if idx == len(ladder_down) - 1:
            reasons.append(f"only {disk:.0f} GB disk free — even E2B is tight; free up space")
            break
        key = ladder_down[idx + 1]
        reasons.append(f"stepped down to {MODELS[key]['label']} — {disk:.0f} GB disk free")
    return key, reasons, alternatives


def recommend(hw: dict[str, Any] | None = None, *,
              has_cloud_key: bool | None = None,
              has_subscription: bool | None = None) -> dict[str, Any]:
    """Tier + model recommendation. Pure given explicit args; env-aware otherwise."""
    hw = hw if hw is not None else detect_hardware()
    cloud = _has_cloud_key() if has_cloud_key is None else has_cloud_key
    subscription = _has_subscription() if has_subscription is None else has_subscription

    model_key, reasons, alt_keys = _pick_model_key(hw)
    ram = float(hw.get("ram_gb") or 0)

    if cloud and (not ram or ram >= 8):
        tier = "T4"
        reasons.append("cloud key detected → hybrid: fast local for light work, cloud for heavy lifts")
    elif cloud:
        tier = "T2"
        reasons.append("cloud key detected and RAM is tight → cloud-first is the honest default")
    elif subscription:
        tier = "T3"
        reasons.append("Claude subscription flagged → plan credits, no key handling")
    elif ram and ram < 8:
        tier = "T0"
    else:
        tier = "T1"

    model = MODELS[model_key]
    return {
        "tier": tier,
        "tier_name": TIERS[tier]["name"],
        "tier_card": TIERS[tier]["card"],
        "model_key": model_key,
        "model": model["id"],
        "model_label": model["label"],
        "quant": model["quant"],
        "est_download_gb": model["download_gb"],
        "est_tokens_per_sec": _est_tokens_per_sec(model_key, hw),
        "context_hint": model["context_hint"],
        "reasons": reasons,
        "alternatives": [
            {
                "model_key": k,
                "model": MODELS[k]["id"],
                "model_label": MODELS[k]["label"],
                "quant": MODELS[k]["quant"],
                "est_download_gb": MODELS[k]["download_gb"],
                "est_tokens_per_sec": _est_tokens_per_sec(k, hw),
                "context_hint": MODELS[k]["context_hint"],
            }
            for k in alt_keys
        ],
    }


# ── User override persistence (onboarding.json) ──────────────────────────────

def _load_onboarding() -> dict[str, Any]:
    try:
        return json.loads(ONBOARDING_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_tier_choice() -> dict[str, Any] | None:
    """The user's saved tier choice, or None. Override always wins over recommend()."""
    choice = _load_onboarding().get("tier_choice")
    return choice if isinstance(choice, dict) else None


def save_tier_choice(tier: str, model: str = "", *, source: str = "user") -> dict[str, Any]:
    """Persist the user's tier/model pick. Raises ValueError on unknown tier."""
    if tier not in TIERS:
        raise ValueError(f"unknown tier: {tier!r} (expected one of {sorted(TIERS)})")
    data = _load_onboarding()
    choice = {
        "tier": tier,
        "tier_name": TIERS[tier]["name"],
        "model": model,
        "source": source,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    data["tier_choice"] = choice
    ONBOARDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    ONBOARDING_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return choice


# ── Wizard/doctor payload ─────────────────────────────────────────────────────

def tiers_payload() -> dict[str, Any]:
    """Everything GET /tiers needs: hardware, recommendation, all tiers, saved choice."""
    hw = detect_hardware()
    return {
        "hardware": hw,
        "recommendation": recommend(hw),
        "tiers": [
            {"tier": tid, **meta} for tid, meta in TIERS.items()
        ],
        "models": [
            {"model_key": k, **v} for k, v in MODELS.items()
        ],
        "choice": load_tier_choice(),
    }
