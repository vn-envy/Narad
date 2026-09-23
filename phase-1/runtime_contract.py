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
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).parent.parent

from turbovec_policy import memory_tier_policy_payload

from narad_config import (
    ARTIFACTS_DIR,
    ATTACHMENTS_DIR,
    CONFIG_DIR,
    NARAD_HOME,
    TRACE_DIR,
    WIKI_DIR,
)

try:
    from model_config import AVATAR_MODELS, refresh_avatar_models
except Exception:
    AVATAR_MODELS: dict[str, str] = {}
    refresh_avatar_models = None
try:
    from model_registry import context_policy_payload as _context_policy_payload
except Exception:
    _context_policy_payload = None
_CONTRACT_PATH = _ROOT / "contracts" / "agent-contracts.json"
_BUILD_PHASE = "pre-15"
_BUILD_LABEL = "narad-4-agent-local-hybrid"
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
    if "narad-claude-sdk" in lower:
        return "narad-claude-sdk"  # subscription plan credits (S3)
    if "deepseek" in lower:
        return "deepseek"
    if "grok" in lower or "xai" in lower:
        return "xai"
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
    try:
        import xai_oauth

        xai_available = xai_oauth.ensure_runtime_token()
        xai_auth_mode = "oauth" if xai_oauth.signed_in() else (
            "api_key" if _env_present("XAI_API_KEY") else None
        )
    except Exception:
        xai_available = _env_present("XAI_API_KEY")
        xai_auth_mode = "api_key" if xai_available else None
    try:
        from narad_litellm import xai_resilience_status

        xai_resilience = xai_resilience_status()
    except Exception:
        xai_resilience = {
            "request_timeout_s": 90.0,
            "fallback_model": os.environ.get(
                "NARAD_XAI_FALLBACK_MODEL", "deepseek/deepseek-flash"
            ),
            "circuit_open": False,
            "circuit_remaining_s": 0.0,
            "last_transient_error": None,
        }
    try:
        from local_model_runtime import local_runtime_status

        local_runtime = local_runtime_status()
    except Exception as exc:
        local_runtime = {
            "available": False,
            "ready": False,
            "runtime_installed": False,
            "reachable": False,
            "reason": f"Local runtime probe failed: {type(exc).__name__}",
        }
    try:
        from google_workspace import status as google_workspace_status

        google_workspace = google_workspace_status()
    except Exception as exc:
        google_workspace = {
            "configured": False,
            "connected": False,
            "services": {},
            "reason": f"Google Workspace probe failed: {type(exc).__name__}",
        }
    try:
        from decision_engine import jev_status

        typesafe = jev_status()
    except Exception as exc:
        typesafe = {
            "available": False,
            "configured": False,
            "enabled": False,
            "reason": f"Jev status failed: {type(exc).__name__}",
        }

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
        "xai": {
            "available": xai_available,
            "kind": "cloud_oauth_or_key",
            "reason": None if xai_available else "Sign in with Grok or set XAI_API_KEY",
            "auth_mode": xai_auth_mode,
            "model": "xai/grok-4.6",
            "service_tier": os.environ.get("GROK_SERVICE_TIER", "priority"),
            "resilience": xai_resilience,
        },
        "exa": {
            "available": _env_present("EXA_API_KEY"),
            "kind": "cloud",
            "reason": None if _env_present("EXA_API_KEY") else "EXA_API_KEY not set",
        },
        "firecrawl": {
            "available": _env_present("FIRECRAWL_API_KEY"),
            "kind": "cloud_or_self_hosted",
            "reason": None if _env_present("FIRECRAWL_API_KEY") else "FIRECRAWL_API_KEY not set",
        },
        "typesafe": {
            "kind": "cloud_decision_accelerator",
            **typesafe,
        },
        "google-workspace": {
            "available": bool(google_workspace.get("connected")),
            "kind": "oauth_connector",
            **google_workspace,
        },
        "local-model-runtime": {
            "kind": "local",
            **local_runtime,
        },
    }
    return status


def tool_family_status() -> dict[str, dict[str, Any]]:
    providers = provider_status()
    calendar_ok, calendar_reason = _module_available("calendar_skill")
    docling_ok, docling_reason = _module_available("docling_skill")
    browser_ok, browser_reason = _module_available("browser_skill")
    browser_act_ok, browser_act_reason = _module_available("browser_act_skill")
    computer_ok, computer_reason = _module_available("computer_use_skill")
    computer_detail: dict[str, Any] = {}
    if computer_ok:
        try:
            from computer_use_skill import browser_runtime_status

            computer_detail = browser_runtime_status()
        except Exception as exc:
            computer_ok = False
            computer_reason = f"computer-use status failed: {type(exc).__name__}"
    computer_runtime_ok = computer_ok and bool(computer_detail.get("available", False))
    try:
        from artemis_adapter import artemis_status

        phone_detail = artemis_status(include_devices=False)
    except Exception as exc:
        phone_detail = {
            "available": False,
            "ready": False,
            "reason": f"Artemis status failed: {type(exc).__name__}: {exc}",
        }
    finance_ok, finance_reason = _module_available("finance_skill")
    health_ok, health_reason = _module_available("health_skill")
    email_ok, email_reason = _module_available("email_skill")
    filesystem_ok, filesystem_reason = _module_available("local_skill")
    shell_ok, shell_reason = _module_available("shell_skill")
    sql_ok, sql_reason = _module_available("sql_skill")
    video_ok, video_reason = _module_available("video_skill")
    try:
        from voice_engine import voice_engine
        tts_ok = bool(voice_engine.tts_tiers())
        tts_reason = None if tts_ok else (
            "No TTS engine — connect a Smallest.ai key or install a local engine"
        )
    except Exception as _tts_exc:  # noqa: BLE001 — availability probe only
        tts_ok, tts_reason = False, str(_tts_exc)

    search_available = (
        providers["exa"]["available"]
        or providers["firecrawl"]["available"]
    )
    source_reach: dict[str, Any] = {}
    try:
        from http_skill import source_reach_status

        source_reach = source_reach_status()
        search_available = search_available or bool(source_reach.get("available", False))
    except Exception:
        pass
    media_provider_available = (
        providers["google"]["available"]
        or providers["mimo"]["available"]
        or providers["local-model-runtime"].get("ready", False)
    )

    return {
        "search": {
            "available": search_available,
            "reason": None if search_available else "No live search provider configured",
            "source_reach": source_reach,
        },
        "browser": {
            "available": browser_ok and browser_act_ok and computer_runtime_ok,
            "reason": (
                browser_reason
                or browser_act_reason
                or computer_reason
                or computer_detail.get("reason")
            ),
            "runtime": computer_detail,
        },
        "computer": {
            "available": computer_runtime_ok,
            "reason": computer_reason or computer_detail.get("reason"),
            "browser": {
                "available": computer_runtime_ok,
                "persistent_sessions": bool(computer_detail.get("persistent_sessions", False)),
                "batched_actions": bool(computer_detail.get("batched_actions", False)),
                "contexts": computer_detail.get("contexts", {}),
            },
            "desktop": computer_detail.get("desktop", {}),
        },
        "phone": {
            "available": bool(phone_detail.get("ready", False)),
            "reason": phone_detail.get("reason"),
            "android_only": True,
            "managed": False,
            "runtime": phone_detail,
        },
        "documents": {
            "available": docling_ok,
            "reason": docling_reason,
        },
        "attachments": {
            "available": True,
            "reason": None,
            "local_first": True,
            "folder_uploads": True,
            "exact_reread": True,
            "url_handoff": "Matsya",
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
            "available": calendar_ok and bool(
                providers["google-workspace"].get("services", {}).get("calendar", {}).get("read")
            ),
            "optional": True,
            "reason": calendar_reason or (
                None if bool(providers["google-workspace"].get("services", {}).get("calendar", {}).get("read"))
                else "Connect Google Calendar to check and manage events"
            ),
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
            "available": email_ok and bool(
                providers["google-workspace"].get("services", {}).get("gmail", {}).get("read")
            ),
            "optional": True,
            "reason": email_reason or (
                None if bool(providers["google-workspace"].get("services", {}).get("gmail", {}).get("read"))
                else "Connect Gmail to read or send email"
            ),
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
        ("attachments_dir", ATTACHMENTS_DIR),
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
    if refresh_avatar_models is not None:
        refresh_avatar_models()
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

    model_endpoint_ready = any(
        bool(providers[name].get("available"))
        for name in ("deepseek", "google", "openai", "mimo", "xai")
    ) or bool(providers["local-model-runtime"].get("ready"))
    if not model_endpoint_ready:
        issues.append(RuntimeIssue(
            "warning",
            "model_endpoint_unavailable",
            "Connect a model endpoint or install the offline Gemma 4 model",
        ))

    for check in checks:
        if not check["ok"] and check.get("required", True):
            issues.append(RuntimeIssue("error", check["name"], check["reason"] or "check failed"))

    for name, tool in tools.items():
        if not tool["available"] and name in {
            "search", "browser", "computer", "media", "sql", "shell",
        }:
            issues.append(RuntimeIssue("warning", f"{name}_degraded", tool["reason"] or f"{name} unavailable"))

    status = "healthy" if not any(issue.level == "error" for issue in issues) and not issues else "degraded"
    agent_status = agent_runtime_status()
    degraded_count = sum(len(agent["degraded_tool_families"]) for agent in agent_status)
    try:
        from capability_validation import validate_capabilities

        skill_tool_validation = validate_capabilities(tools)
    except Exception as exc:
        skill_tool_validation = {
            "status": "invalid",
            "inventory_error": f"capability validation failed: {type(exc).__name__}: {exc}",
            "summary": {},
            "avatars": {},
            "workflows": {},
            "integrations": [],
        }
    try:
        from model_config import get_vision_endpoint

        vision_endpoint = get_vision_endpoint("matsya")
        multimodal = {
            "available": vision_endpoint is not None,
            "model": vision_endpoint.model if vision_endpoint else None,
            "provider": vision_endpoint.provider if vision_endpoint else None,
            "source": vision_endpoint.source if vision_endpoint else None,
        }
    except Exception as exc:
        multimodal = {
            "available": False,
            "model": None,
            "provider": None,
            "source": None,
            "reason": f"multimodal endpoint probe failed: {type(exc).__name__}",
        }

    return {
        "status": status,
        "build": {
            "phase": _BUILD_PHASE,
            "label": _BUILD_LABEL,
            "runtime_mode": (
                "local"
                if all(_detect_provider(model) == "local" for model in AVATAR_MODELS.values())
                else "hybrid"
                if providers["local-model-runtime"].get("ready")
                else _RUNTIME_MODE
            ),
        },
        "architecture": {
            **load_contract()["architecture"],
            "agent_names": canonical_agent_names(),
            "stale_agents_removed": ["Varaha", "Narasimha", "Buddha", "Vamana"],
        },
        "agents": agent_status,
        "model_roles": {
            "orchestrator": AVATAR_MODELS.get("narad", "unknown"),
            "workers": {
                name: AVATAR_MODELS.get(name, "unknown")
                for name in ("matsya", "rama", "krishna", "parashurama")
            },
            "worker_service_tier": os.environ.get("GROK_SERVICE_TIER", "priority"),
            "multimodal": multimodal,
        },
        "providers": providers,
        "tool_families": tools,
        "skill_tool_validation": skill_tool_validation,
        "local_ready": {
            "frontend_transport_agnostic": True,
            "local_model_runtime": bool(providers["local-model-runtime"].get("ready")),
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
