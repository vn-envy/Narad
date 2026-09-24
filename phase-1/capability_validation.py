"""Live skill/tool/workflow validation for Narad's four avatars.

The contract deliberately separates callable registration from runtime
readiness. Optional integrations stay visible without being represented as
installed, and workflow ownership mistakes fail validation deterministically.
"""

from __future__ import annotations

import importlib.util
from collections import defaultdict
from pathlib import Path
from typing import Any

from workflow_packs.definitions import list_packs

_SKILL_CATALOG_PATH = Path(__file__).parent.parent / "phase-9" / "skills.py"
_SKILL_SPEC = importlib.util.spec_from_file_location("narad_phase9_skill_catalog", _SKILL_CATALOG_PATH)
if _SKILL_SPEC is None or _SKILL_SPEC.loader is None:
    raise ImportError(f"Could not load Narad skill catalog from {_SKILL_CATALOG_PATH}")
_SKILL_MODULE = importlib.util.module_from_spec(_SKILL_SPEC)
_SKILL_SPEC.loader.exec_module(_SKILL_MODULE)
SKILLS = _SKILL_MODULE.SKILLS
SKILL_OWNERS = _SKILL_MODULE.SKILL_OWNERS
SKILL_TOOL_REQUIREMENTS = _SKILL_MODULE.SKILL_TOOL_REQUIREMENTS


_TOOL_FAMILIES = {
    "web_search": "search",
    "exa_search": "search",
    "exa_contents": "search",
    "firecrawl_extract": "search",
    "enrich_web_research": "search",
    "browse_url": "browser",
    "computer_use": "browser",
    "phone_use": "phone",
    "browser_screenshot": "browser",
    "browser_fill": "browser",
    "browser_upload_and_submit": "browser",
    "extract_document": "documents",
    "extract_fields": "documents",
    "scan_directory": "filesystem",
    "find_large_files": "filesystem",
    "organize_by_type": "filesystem",
    "move_to_trash": "filesystem",
    "get_upcoming_events": "calendar",
    "create_event": "calendar",
    "get_spending": "finance",
    "get_financial_context": "finance",
    "get_recurring_expenses": "finance",
    "import_csv": "finance",
    "log_symptom": "health",
    "get_health_log": "health",
    "get_lab_results": "health",
    "compose_email": "email",
    "send_email": "email",
    "create_document": "documents",
    "create_video": "media",
    "generate_video_clip": "media",
    "generate_image": "media",
    "read_file": "filesystem",
    "write_script": "filesystem",
    "run_shell": "shell",
    "query_database": "sql",
    "schedule_cron": "automation",
}

_WORKFLOW_GAPS: dict[str, list[str]] = {
    "career": [
        "Application tracking uses durable project tasks but has no ATS-specific application record yet.",
    ],
    "health": [
        "Daily adherence, sleep, energy, and activity still lack one structured tracker schema.",
    ],
    "travel": [
        "Browser booking is available, but price normalization and reservation records are not dedicated tools.",
    ],
    "teach": [
        "Grounded resource refresh is available through Matsya but is not yet a first-class stage in every lesson run.",
    ],
    "finance": [
        "The local ledger is CSV/email-driven; direct bank or brokerage synchronization is not configured.",
    ],
    "documents": [
        "Presentation creation currently produces HTML/PDF-ready decks, not native PPTX output.",
    ],
}

_SKILL_IMPLEMENTATION: dict[str, dict[str, Any]] = {
    "source_reach": {
        "mode": "native_pattern",
        "source": "Panniantong/Agent-Reach",
        "note": "Ordered direct/fallback source channels and explicit coverage diagnostics; no automatic cookie import.",
    },
    "motion_design": {
        "mode": "vendored_skill_rules",
        "source": "diffusionstudio/lottie + emilkowalski/skills",
        "note": "Purpose-gated motion, deterministic recipes, frame checks, and reduced-motion fallback without a default bundle dependency.",
    },
    "concept_visualization": {
        "mode": "native_first_optional_renderer",
        "source": "mingrammer/diagrams",
        "note": "Native concept-map artifacts for teaching; Diagrams is reserved for technical architecture when Graphviz is ready.",
    },
    "browser_task": {
        "mode": "native_with_optional_driver",
        "source": "Tencent BrowserSkill + trycua/cua",
        "note": "Playwright remains the isolated default; BrowserSkill provides profile-granted signed-in Chromium and CUA remains opt-in desktop control.",
    },
    "mobile_task": {
        "mode": "optional_direct_adapter",
        "source": "google/artemis",
        "note": "Android-only, lazy sidecar with profile-bound device grants, preview-first execution, and verified mode for high-risk work.",
    },
}


def _registered_tools() -> tuple[dict[str, set[str]], str | None]:
    try:
        import avatar_agents

        agents = (
            avatar_agents.matsya,
            avatar_agents.rama,
            avatar_agents.krishna,
            avatar_agents.parashurama,
        )
        return {
            agent.name: {
                str(getattr(tool, "name", ""))
                for tool in (agent.tools or [])
                if getattr(tool, "name", None)
            }
            for agent in agents
        }, None
    except Exception as exc:
        return {name: set() for name in SKILL_OWNERS}, f"avatar tool inventory failed: {type(exc).__name__}: {exc}"


def _integration_status(tool_families: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    computer = tool_families.get("computer", {})
    desktop = computer.get("desktop", {}) if isinstance(computer, dict) else {}
    adapters = desktop.get("adapters", {}) if isinstance(desktop, dict) else {}
    cua = adapters.get("cua", {}) if isinstance(adapters, dict) else {}
    contexts = computer.get("browser", {}).get("contexts", {}) if isinstance(computer, dict) else {}
    browser_skill = contexts.get("signed_in", {}) if isinstance(contexts, dict) else {}
    phone = tool_families.get("phone", {})
    return [
        {
            "id": "browser_skill",
            "owner": "Matsya",
            "adoption": "optional_direct_adapter",
            "status": "ready" if browser_skill.get("ready") else "optional_unavailable",
            "available": bool(browser_skill.get("ready")),
            "reason": browser_skill.get("reason"),
            "source": "https://github.com/tencent/BrowserSkill",
        },
        {
            "id": "artemis",
            "owner": "Matsya",
            "adoption": "optional_direct_adapter",
            "status": "ready" if phone.get("available") else "optional_unavailable",
            "available": bool(phone.get("available")),
            "reason": phone.get("reason"),
            "source": "https://github.com/google/artemis",
        },
        {
            "id": "cua",
            "owner": "Matsya",
            "adoption": "optional_direct_adapter",
            "status": "ready" if cua.get("ready") else "optional_unavailable",
            "available": bool(cua.get("ready")),
            "reason": cua.get("reason"),
            "source": "https://github.com/trycua/cua",
        },
        {
            "id": "agent_reach_pattern",
            "owner": "Matsya",
            "adoption": "native_pattern",
            "status": "ready",
            "available": True,
            "reason": "Narad keeps its own permission boundary and does not auto-read or install cookie-backed CLIs",
            "source": "https://github.com/Panniantong/Agent-Reach",
        },
    ]


def validate_capabilities(
    tool_families: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Validate skill ownership, actual tool registration, and workflow wiring."""
    registered, inventory_error = _registered_tools()
    workflow_skill_usage: dict[str, set[str]] = defaultdict(set)
    packs = list_packs()
    for pack in packs:
        for stage in pack.get("stages", []):
            if stage.get("skill"):
                workflow_skill_usage[str(stage["skill"])].add(str(pack["id"]))

    avatars: dict[str, Any] = {}
    invalid_skill_count = 0
    limited_skill_count = 0
    for owner, skill_names in SKILL_OWNERS.items():
        owner_tools = registered.get(owner, set())
        skill_payloads: list[dict[str, Any]] = []
        for skill_name in skill_names:
            required = list(SKILL_TOOL_REQUIREMENTS.get(skill_name, ()))
            missing = sorted(set(required) - owner_tools)
            degraded = sorted({
                family
                for tool in required
                if (family := _TOOL_FAMILIES.get(tool))
                and not tool_families.get(family, {}).get("available", False)
            })
            implementation = _SKILL_IMPLEMENTATION.get(skill_name, {"mode": "native"})
            if missing:
                status = "invalid"
                invalid_skill_count += 1
            elif degraded:
                status = "limited"
                limited_skill_count += 1
            else:
                status = "ready"
            skill_payloads.append({
                "name": skill_name,
                "status": status,
                "phases": list(SKILLS.get(skill_name, [])),
                "required_tools": required,
                "missing_tools": missing,
                "degraded_tool_families": degraded,
                "workflows": sorted(workflow_skill_usage.get(skill_name, set())),
                "implementation": implementation,
            })
        invalid = sum(item["status"] == "invalid" for item in skill_payloads)
        limited = sum(item["status"] in {"limited", "optional_unavailable"} for item in skill_payloads)
        avatars[owner] = {
            "status": "invalid" if invalid else ("limited" if limited else "ready"),
            "registered_tools": sorted(owner_tools),
            "tool_count": len(owner_tools),
            "skills": skill_payloads,
            "skill_summary": {
                "total": len(skill_payloads),
                "ready": sum(item["status"] == "ready" for item in skill_payloads),
                "limited": limited,
                "profile_gated": sum(item["status"] == "profile_gated" for item in skill_payloads),
                "invalid": invalid,
            },
        }

    workflows: dict[str, Any] = {}
    invalid_stage_count = 0
    limited_stage_count = 0
    for pack in packs:
        stage_payloads: list[dict[str, Any]] = []
        for stage in pack.get("stages", []):
            owner = str(stage.get("owner") or "")
            skill = str(stage.get("skill") or "")
            declared_tools = [str(tool) for tool in stage.get("tools", [])]
            missing = sorted(set(declared_tools) - registered.get(owner, set()))
            correct_skill_owner = not skill or skill in SKILL_OWNERS.get(owner, ())
            degraded = sorted({
                family
                for tool in declared_tools
                if (family := _TOOL_FAMILIES.get(tool))
                and not tool_families.get(family, {}).get("available", False)
            })
            if missing or not correct_skill_owner:
                status = "invalid"
                invalid_stage_count += 1
            elif degraded:
                status = "limited"
                limited_stage_count += 1
            else:
                status = "ready"
            stage_payloads.append({
                "id": stage.get("id"),
                "title": stage.get("title"),
                "owner": owner,
                "skill": skill or None,
                "tools": declared_tools,
                "missing_tools": missing,
                "skill_owner_valid": correct_skill_owner,
                "degraded_tool_families": degraded,
                "status": status,
            })
        required_missing = [
            name for name in pack.get("required_capabilities", [])
            if not tool_families.get(name, {"available": True}).get("available", False)
        ]
        optional_missing = [
            name for name in pack.get("optional_capabilities", [])
            if not tool_families.get(name, {"available": True}).get("available", False)
        ]
        if required_missing or any(item["status"] == "invalid" for item in stage_payloads):
            workflow_status = "invalid"
        elif optional_missing or any(item["status"] == "limited" for item in stage_payloads):
            workflow_status = "limited"
        else:
            workflow_status = "ready"
        workflows[str(pack["id"])] = {
            "status": workflow_status,
            "owner": pack.get("owner"),
            "required_capabilities_missing": required_missing,
            "optional_capabilities_missing": optional_missing,
            "known_gaps": _WORKFLOW_GAPS.get(str(pack["id"]), []),
            "stages": stage_payloads,
        }

    integrations = _integration_status(tool_families)
    return {
        "status": "invalid" if invalid_skill_count or invalid_stage_count else (
            "limited" if limited_skill_count or limited_stage_count else "ready"
        ),
        "inventory_error": inventory_error,
        "summary": {
            "avatars": len(avatars),
            "registered_tools": sum(item["tool_count"] for item in avatars.values()),
            "skills": sum(item["skill_summary"]["total"] for item in avatars.values()),
            "invalid_skills": invalid_skill_count,
            "limited_skills": limited_skill_count,
            "workflows": len(workflows),
            "invalid_workflow_stages": invalid_stage_count,
            "limited_workflow_stages": limited_stage_count,
        },
        "avatars": avatars,
        "workflows": workflows,
        "integrations": integrations,
        "policy": {
            "registered_is_not_ready": True,
            "optional_dependencies_are_not_auto_installed": True,
            "profile_before_native_json_parser": True,
            "no_automatic_cookie_import": True,
        },
    }
