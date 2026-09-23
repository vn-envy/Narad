from __future__ import annotations

import sys
from pathlib import Path

_root = next(path for path in Path(__file__).resolve().parents if (path / "narad_paths.py").exists())
sys.path[:0] = [str(_root)]
import narad_paths  # noqa: F401

# isort: split
import avatar_agents
from capability_validation import validate_capabilities
from runtime_contract import tool_family_status


def test_public_tool_names_match_prompt_contracts() -> None:
    matsya_tools = {tool.name for tool in avatar_agents.matsya.tools}
    krishna_tools = {tool.name for tool in avatar_agents.krishna.tools}
    parashurama_tools = {tool.name for tool in avatar_agents.parashurama.tools}

    assert "browse_url" in matsya_tools
    assert "rank_ui_templates" in krishna_tools
    assert not {"browse_url_sync", "_rank_ui_templates"} & (
        matsya_tools | krishna_tools | parashurama_tools
    )


def test_all_workflow_stage_tools_and_skill_owners_validate() -> None:
    payload = validate_capabilities(tool_family_status())

    assert payload["inventory_error"] is None
    assert payload["summary"]["invalid_skills"] == 0
    assert payload["summary"]["invalid_workflow_stages"] == 0
    assert set(payload["avatars"]) == {"Matsya", "Rama", "Krishna", "Parashurama"}
    assert set(payload["workflows"]) == {"career", "health", "travel", "teach", "finance", "documents"}

    document_stages = {stage["id"]: stage for stage in payload["workflows"]["documents"]["stages"]}
    assert document_stages["ingest"]["owner"] == "Matsya"
    assert document_stages["ingest"]["tools"] == ["extract_document"]
    assert document_stages["analyze"]["owner"] == "Parashurama"
    assert document_stages["analyze"]["tools"] == ["read_file", "query_database"]


def test_core_browser_and_research_adoptions_are_reported() -> None:
    payload = validate_capabilities(tool_family_status())
    integrations = {item["id"]: item for item in payload["integrations"]}

    assert integrations["cua"]["adoption"] == "optional_direct_adapter"
    assert integrations["agent_reach_pattern"]["status"] == "ready"
    assert integrations["browser_skill"]["adoption"] == "optional_direct_adapter"
    assert integrations["artemis"]["adoption"] == "optional_direct_adapter"
    assert set(integrations) == {
        "cua",
        "agent_reach_pattern",
        "browser_skill",
        "artemis",
    }
