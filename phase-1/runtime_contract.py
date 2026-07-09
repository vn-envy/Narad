"""
Canonical 4-agent runtime contract and capability reporting.

This module is the single source of truth for:
  - live agent identities and cultural disciplines
  - runtime/provider capability detection
  - startup self-checks and storage validation
  - health/capabilities API payloads
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).parent.parent

from turbovec_policy import memory_tier_policy_payload

from narad_config import ARTIFACTS_DIR, CONFIG_DIR, NARAD_HOME, TRACE_DIR, WIKI_DIR

try:
    from model_config import AVATAR_MODELS
except Exception:
    AVATAR_MODELS: dict[str, str] = {}
try:
    from model_registry import context_policy_payload as _context_policy_payload
except Exception:
    _context_policy_payload = None
_CONTRACT_PATH = _ROOT / "contracts" / "agent-contracts.json"
_BUILD_PHASE = "pre-15"
_BUILD_LABEL = "narad-4-agent-cloud"
_RUNTIME_MODE = "cloud"


@dataclass(frozen=True)
class RuntimeIssue:
    level: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "level": self.level,
            "code": self.code,
            "message": self.message,
        }


def _detect_provider(model: str) -> str:
    lower = (model or "").lower()
    if "narad-local" in lower:
        return "narad-local"  # bundled llama-server tier (S1/O2)
    if "deepseek" in lower:
        return "deepseek"
    if "gemini" in lower or "google" in lower:
        return "google"
    if "gpt" in lower or "openai" in lower or "o1" in lower or "o3" in lower:
        return "openai"
    if "claude" in lower or "anthropic" in lower:
        return "anthropic"
    if "ollama" in lower or "localhost" in lower or "127.0.0.1" in lower:
        return "local"
    return "unknown"


def _env_present(*names: str) -> bool:
    return any(bool(os.environ.get(name, "").strip()) for name in names)


def _module_available(module_name: str) -> tuple[bool, str | None]:
    try:
        importlib.import_module(module_name)
        return True, None
    except Exception as exc:
        return False, f"{module_name} import failed: {type(exc).__name__}"


def _check_writable(path: Path) -> tuple[bool, str | None]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".narad-write-check"
        probe.write_text("ok")
        probe.unlink(missing_ok=True)
        return True, None
    except Exception as exc:
        return False, f"{path} not writable: {exc}"


@lru_cache(maxsize=1)
def load_contract() -> dict[str, Any]:
    return json.loads(_CONTRACT_PATH.read_text())


def agent_contracts() -> list[dict[str, Any]]:
    return list(load_contract()["agents"])


def agent_contract_map() -> dict[str, dict[str, Any]]:
    return {agent["name"]: agent for agent in agent_contracts()}


def canonical_agent_names() -> list[str]:
    return [agent["name"] for agent in agent_contracts()]


def canonical_tool_name_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for name in canonical_agent_names():
        lower = name.lower()
        mapping[lower] = name
        mapping[f"invoke_{lower}"] = name
    return mapping


def primary_discipline(agent_name: str) -> str:
    agent = agent_contract_map().get(agent_name, {})
    disciplines = agent.get("disciplines", [])
    return disciplines[0] if disciplines else "general"


def provider_status() -> dict[str, dict[str, Any]]:
    status = {
        "deepseek": {
            "available": _env_present("DEEPSEEK_API_KEY"),
            "kind": "cloud",
            "reason": None if _env_present("DEEPSEEK_API_KEY") else "DEEPSEEK_API_KEY not set",
        },
        "google": {
            "available": _env_present("GEMINI_API_KEY", "GOOGLE_API_KEY"),
            "kind": "cloud",
            "reason": None if _env_present("GEMINI_API_KEY", "GOOGLE_API_KEY") else "Gemini API key not set",
        },
        "openai": {
            "available": _env_present("OPENAI_API_KEY"),
            "kind": "cloud",
            "reason": None if _env_present("OPENAI_API_KEY") else "OPENAI_API_KEY not set",
        },
        "mimo": {
            "available": _env_present("MIMO_API_KEY"),
            "kind": "cloud",
            "reason": None if _env_present("MIMO_API_KEY") else "MIMO_API_KEY not set",
        },
        "tinyfish": {
            "available": _env_present("TINYFISH_API_KEY"),
            "kind": "cloud",
            "reason": None if _env_present("TINYFISH_API_KEY") else "TINYFISH_API_KEY not set",
        },
        "tavily": {
            "available": _env_present("TAVILY_API_KEY"),
            "kind": "cloud",
            "reason": None if _env_present("TAVILY_API_KEY") else "TAVILY_API_KEY not set",
        },
        "smtp": {
            "available": _env_present("EMAIL_ADDRESS") and _env_present("EMAIL_APP_PASSWORD"),
            "kind": "cloud",
            "reason": None if (_env_present("EMAIL_ADDRESS") and _env_present("EMAIL_APP_PASSWORD"))
            else "EMAIL_ADDRESS or EMAIL_APP_PASSWORD not set",
        },
        "caldav": {
            "available": _env_present("CALDAV_URL", "CALDAV_USERNAME", "CALDAV_PASSWORD"),
            "kind": "cloud",
            "reason": None if _env_present("CALDAV_URL", "CALDAV_USERNAME", "CALDAV_PASSWORD")
            else "CALDAV credentials not fully set",
        },
        "local-model-runtime": {
            "available": bool(shutil.which("ollama") or os.environ.get("OLLAMA_HOST")),
            "kind": "local",
            "reason": None if (shutil.which("ollama") or os.environ.get("OLLAMA_HOST"))
            else "Ollama runtime not detected",
        },
    }
    return status


def tool_family_status() -> dict[str, dict[str, Any]]:
    providers = provider_status()
    calendar_ok, calendar_reason = _module_available("calendar_skill")
    docling_ok, docling_reason = _module_available("docling_skill")
    browser_ok, browser_reason = _module_available("browser_skill")
    browser_act_ok, browser_act_reason = _module_available("browser_act_skill")
    finance_ok, finance_reason = _module_available("finance_skill")
    health_ok, health_reason = _module_available("health_skill")
    email_ok, email_reason = _module_available("email_skill")
    filesystem_ok, filesystem_reason = _module_available("local_skill")
    shell_ok, shell_reason = _module_available("shell_skill")
    sql_ok, sql_reason = _module_available("sql_skill")
    video_ok, video_reason = _module_available("video_skill")
    tts_ok, tts_reason = _module_available("tts_api")

    search_available = providers["tinyfish"]["available"] or providers["tavily"]["available"]
    media_provider_available = providers["google"]["available"] or providers["mimo"]["available"]

    return {
        "search": {
            "available": search_available,
            "reason": None if search_available else "No live search provider configured",
        },
        "browser": {
            "available": browser_ok and browser_act_ok,
            "reason": browser_reason or browser_act_reason,
        },
        "documents": {
            "available": docling_ok,
            "reason": docling_reason,
        },
        "filesystem": {
            "available": filesystem_ok,
            "reason": filesystem_reason,
        },
        "http": {
            "available": True,
            "reason": None,
        },
        "planning": {
            "available": True,
            "reason": None,
        },
        "calendar": {
            "available": calendar_ok and providers["caldav"]["available"],
            "reason": calendar_reason or providers["caldav"]["reason"],
        },
        "finance": {
            "available": finance_ok,
            "reason": finance_reason,
        },
        "health": {
            "available": health_ok,
            "reason": health_reason,
        },
        "email": {
            "available": email_ok and providers["smtp"]["available"],
            "reason": email_reason or providers["smtp"]["reason"],
        },
        "media": {
            "available": video_ok and media_provider_available,
            "reason": video_reason or ("Media provider unavailable" if not media_provider_available else None),
        },
        "presentation": {
            "available": media_provider_available,
            "reason": None if media_provider_available else "Presentation generation provider unavailable",
        },
        "tts": {
            "available": tts_ok,
            "reason": tts_reason,
        },
        "shell": {
            "available": shell_ok,
            "reason": shell_reason,
        },
        "sql": {
            "available": sql_ok,
            "reason": sql_reason,
        },
        "automation": {
            "available": shell_ok,
            "reason": shell_reason,
        },
        "projects": {
            "available": True,
            "reason": None,
        },
        "memory": {
            "available": True,
            "reason": None,
        },
    }


def startup_checks() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for name, path in (
        ("narad_home", NARAD_HOME),
        ("trace_dir", TRACE_DIR),
        ("wiki_dir", WIKI_DIR),
        ("artifacts_dir", ARTIFACTS_DIR),
        ("config_dir", CONFIG_DIR),
    ):
        ok, reason = _check_writable(path)
        checks.append({
            "name": name,
            "ok": ok,
            "reason": reason,
        })

    for name, module_name, required in (
        ("google_adk", "google.adk", True),
        ("fastapi", "fastapi", True),
        ("sse_starlette", "sse_starlette", True),
        ("lancedb", "lancedb", True),
        ("turbovec", "turbovec", False),
    ):
        ok, reason = _module_available(module_name)
        checks.append({
            "name": name,
            "ok": ok,
            "reason": reason,
            "required": required,
        })

    return checks


def agent_runtime_status() -> list[dict[str, Any]]:
    tool_status = tool_family_status()
    result: list[dict[str, Any]] = []
    for contract in agent_contracts():
        name = contract["name"]
        degraded = [
            family
            for family in contract.get("tool_families", [])
            if not tool_status.get(family, {}).get("available", False)
        ]
        model = AVATAR_MODELS.get(name.lower(), "")
        result.append({
            **contract,
            "enabled": True,
            "model": model,
            "provider": _detect_provider(model),
            "discipline": primary_discipline(name),
            "degraded_tool_families": degraded,
        })
    return result


def collect_runtime_contract() -> dict[str, Any]:
    providers = provider_status()
    tools = tool_family_status()
    checks = startup_checks()
    issues: list[RuntimeIssue] = []

    if not providers["deepseek"]["available"]:
        issues.append(RuntimeIssue("warning", "deepseek_unconfigured", providers["deepseek"]["reason"]))

    for check in checks:
        if not check["ok"] and check.get("required", True):
            issues.append(RuntimeIssue("error", check["name"], check["reason"] or "check failed"))

    for name, tool in tools.items():
        if not tool["available"] and name in {"search", "calendar", "email", "media", "sql", "shell"}:
            issues.append(RuntimeIssue("warning", f"{name}_degraded", tool["reason"] or f"{name} unavailable"))

    status = "healthy" if not any(issue.level == "error" for issue in issues) and not issues else "degraded"
    agent_status = agent_runtime_status()
    degraded_count = sum(len(agent["degraded_tool_families"]) for agent in agent_status)

    return {
        "status": status,
        "build": {
            "phase": _BUILD_PHASE,
            "label": _BUILD_LABEL,
            "runtime_mode": _RUNTIME_MODE,
        },
        "architecture": {
            **load_contract()["architecture"],
            "agent_names": canonical_agent_names(),
            "stale_agents_removed": ["Varaha", "Narasimha", "Buddha", "Vamana"],
        },
        "agents": agent_status,
        "providers": providers,
        "tool_families": tools,
        "local_ready": {
            "frontend_transport_agnostic": True,
            "local_model_runtime": providers["local-model-runtime"]["available"],
            "desktop_packaging": False,
        },
        "startup_checks": checks,
        "issues": [issue.to_dict() for issue in issues],
        "issue_count": len(issues),
        "degraded_capability_count": degraded_count,
        "context_policy": (
            _context_policy_payload(AVATAR_MODELS)
            if _context_policy_payload is not None
            else {
                "overflow_policy": "compact_then_escalate",
                "fidelity_policy": "lossless_artifacts",
                "profiles": {},
                "fallback_graph": {},
            }
        ),
        "memory_tiers": memory_tier_policy_payload(),
    }


def health_payload() -> dict[str, Any]:
    contract = collect_runtime_contract()
    return {
        "status": contract["status"],
        "agent": "Narad",
        "phase": contract["build"]["phase"],
        "model": AVATAR_MODELS.get("narad", "unknown"),
        "build": {
            "label": contract["build"]["label"],
            "phase": contract["build"]["phase"],
        },
        "architecture": {
            "model": contract["architecture"]["model"],
            "canonical_agent_count": contract["architecture"]["canonical_agent_count"],
            "agent_names": contract["architecture"]["agent_names"],
        },
        "runtime": {
            "status": contract["status"],
            "mode": contract["build"]["runtime_mode"],
            "local_ready": contract["local_ready"]["local_model_runtime"],
        },
        "issue_count": contract["issue_count"],
    }
