"""
shadcn/ui component registry skill — Parashurama UI design tool.

Fetches live component source and dependency data from the shadcn/ui registry
so Parashurama generates code against the current API, not training-data memory.

Registry endpoints used:
  Component list:  https://ui.shadcn.com/registry/index.json
  Component data:  https://ui.shadcn.com/registry/styles/default/{name}.json
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

_REGISTRY_BASE = "https://ui.shadcn.com/r"
_STYLE = "default"
_TIMEOUT = 10


def list_shadcn_components() -> dict:
    """List all available shadcn/ui components from the official registry.

    Returns:
        status:     "ok" | "error"
        components: list of component name strings (e.g. ["button", "card", "dialog", ...])
        message:    summary or error description
    """
    try:
        with urllib.request.urlopen(f"{_REGISTRY_BASE}/index.json", timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
        names = sorted(
            item["name"]
            for item in data
            if item.get("type") in ("registry:ui", "registry:component")
        )
        return {
            "status":     "ok",
            "components": names,
            "message":    f"{len(names)} shadcn/ui components available.",
        }
    except Exception as exc:
        return {"status": "error", "components": [], "message": str(exc)}


def fetch_shadcn_component(name: str) -> dict:
    """Fetch the full source, dependencies, and registry deps for a shadcn/ui component.

    Use this before generating UI code — it returns the current TypeScript
    implementation so you produce accurate, up-to-date code.

    Args:
        name: Component name in kebab-case (e.g. "button", "card", "data-table",
              "dialog", "dropdown-menu", "form", "input", "sheet", "table").
              Call list_shadcn_components() first if unsure of the exact name.

    Returns:
        status:               "ok" | "error"
        name:                 normalised component name
        files:                list of {name, content, type, target} — the TS source files
        dependencies:         npm packages this component requires at runtime
        devDependencies:      dev-only npm packages
        registryDependencies: other shadcn components this one depends on
        tailwindConfig:       any tailwind config extensions this component needs
        cssVars:              CSS custom properties (light/dark) this component introduces
        message:              summary or error description
    """
    try:
        url = f"{_REGISTRY_BASE}/styles/{_STYLE}/{name}.json"  # e.g. /r/styles/default/button.json
        with urllib.request.urlopen(url, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())

        files = data.get("files", [])
        # Older registry format stores content inline; newer may omit it.
        return {
            "status":               "ok",
            "name":                 data.get("name", name),
            "files":                files,
            "dependencies":         data.get("dependencies", []),
            "devDependencies":      data.get("devDependencies", []),
            "registryDependencies": data.get("registryDependencies", []),
            "tailwindConfig":       data.get("tailwind", {}).get("config", {}),
            "cssVars":              data.get("cssVars", {}),
            "message":              (
                f"Fetched '{name}': {len(files)} file(s), "
                f"{len(data.get('dependencies', []))} dep(s)."
            ),
        }

    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {
                "status":  "error",
                "name":    name,
                "files":   [],
                "message": (
                    f"Component '{name}' not found in the registry. "
                    "Call list_shadcn_components() to see what's available."
                ),
            }
        return {"status": "error", "name": name, "files": [], "message": str(exc)}

    except Exception as exc:
        return {"status": "error", "name": name, "files": [], "message": str(exc)}
