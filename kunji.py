"""
Kunji (कुंजी) — key & connection management for non-tech users (O5).

The .env file is the single biggest non-tech blocker. Kunji replaces it with:
paste a key → provider auto-detected from its prefix → 1-token live test →
stored in the OS keychain (via the `keyring` lib) with an owner-only file
fallback → exported into the process env so every existing provider check
(model_registry.provider_available_for_model, litellm) keeps working untouched.

Rules of the house:
  * .env stays as the power-user escape hatch — a real env var always wins;
    Kunji only fills gaps (`apply_keys_to_env`).
  * The key is never rendered after save — `list_connections` returns a masked
    hint (first-4…last-4) only.
  * Keyring failures (headless Linux, locked keychain) degrade to a 0600
    JSON file under ~/.narad/config — never to a crash.
  * An index file records *which* providers are connected (keychains can't
    enumerate) plus backend + timestamp; it never contains key material.

`import_env_keys()` is the one-time .env → keychain migrator (explicit call,
POST /connections/import-env — never silent). Month-to-date spend per provider
comes from the cost ledger, so the Connections card can show real numbers.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from narad_config import CONFIG_DIR

_KEYS_PATH = CONFIG_DIR / "kunji_keys.json"    # file-backend key material (0600)
_INDEX_PATH = CONFIG_DIR / "kunji_index.json"  # provider → {backend, ts, hint}; NO keys
_KEYRING_SERVICE = "narad"

# ── Provider catalog ──────────────────────────────────────────────────────────
# Prefix order matters: sk-ant- must match before sk-.

PROVIDERS: dict[str, dict[str, Any]] = {
    "anthropic": {
        "label": "Claude (Anthropic)",
        "env": "ANTHROPIC_API_KEY",
        "prefixes": ("sk-ant-",),
        "key_page": "https://console.anthropic.com/settings/keys",
        "test_model": "claude-haiku-4-5",
    },
    "google": {
        "label": "Gemini (Google)",
        "env": "GEMINI_API_KEY",
        "prefixes": ("AIza",),
        "key_page": "https://aistudio.google.com/apikey",
        "test_model": "gemini/gemini-2.5-flash",
    },
    "deepseek": {
        "label": "DeepSeek",
        "env": "DEEPSEEK_API_KEY",
        "prefixes": ("dsk-",),
        "key_page": "https://platform.deepseek.com/api_keys",
        "test_model": "deepseek/deepseek-v4-flash",
    },
    "openai": {
        "label": "OpenAI",
        "env": "OPENAI_API_KEY",
        "prefixes": ("sk-",),
        "key_page": "https://platform.openai.com/api-keys",
        "test_model": "gpt-4o-mini",
    },
    "search": {
        "label": "Web search (Brave/Tavily/Serper)",
        "env": "BRAVE_API_KEY",
        "prefixes": (),  # no reliable prefix — explicit provider only
        "key_page": "https://brave.com/search/api/",
        "test_model": "",
    },
}

_PREFIX_ORDER = ("anthropic", "google", "deepseek", "openai")  # longest/most-specific first


def detect_provider_from_key(key: str) -> str | None:
    """Auto-detect provider from the pasted key's prefix. None when ambiguous/unknown."""
    key = (key or "").strip()
    for provider in _PREFIX_ORDER:
        if any(key.startswith(p) for p in PROVIDERS[provider]["prefixes"]):
            return provider
    return None


def mask_key(key: str) -> str:
    """first-4…last-4 hint; never enough to reconstruct."""
    key = (key or "").strip()
    if len(key) <= 8:
        return "…" * 3
    return f"{key[:4]}…{key[-4:]}"


# ── Storage backends (keyring → 0600 file fallback) ──────────────────────────

def _backend_preference() -> str:
    return os.environ.get("KUNJI_BACKEND", "keyring").strip().lower()


def _keyring_set(provider: str, key: str) -> bool:
    if _backend_preference() == "file":
        return False
    try:
        import keyring
        keyring.set_password(_KEYRING_SERVICE, provider, key)
        return keyring.get_password(_KEYRING_SERVICE, provider) == key  # verify round-trip
    except Exception:
        return False


def _keyring_get(provider: str) -> str | None:
    if _backend_preference() == "file":
        return None
    try:
        import keyring
        return keyring.get_password(_KEYRING_SERVICE, provider)
    except Exception:
        return None


def _keyring_delete(provider: str) -> None:
    try:
        import keyring
        keyring.delete_password(_KEYRING_SERVICE, provider)
    except Exception:
        pass


def _file_load() -> dict[str, str]:
    try:
        return json.loads(_KEYS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _file_save(data: dict[str, str]) -> None:
    _KEYS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _KEYS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(_KEYS_PATH, 0o600)
    except OSError:
        pass


def _load_index() -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_index(index: dict[str, dict[str, Any]]) -> None:
    _INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    _INDEX_PATH.write_text(json.dumps(index, indent=2), encoding="utf-8")


# ── Public key API ────────────────────────────────────────────────────────────

def set_key(provider: str, key: str, *, source: str = "user") -> dict[str, Any]:
    """Store a key (keyring → file fallback), record it in the index, export to env.

    Raises ValueError on unknown provider or empty key.
    """
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider: {provider!r} (expected one of {sorted(PROVIDERS)})")
    key = (key or "").strip()
    if not key:
        raise ValueError("empty key")

    backend = "keyring" if _keyring_set(provider, key) else "file"
    if backend == "file":
        data = _file_load()
        data[provider] = key
        _file_save(data)

    index = _load_index()
    entry = {
        "backend": backend,
        "hint": mask_key(key),
        "source": source,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    index[provider] = entry
    _save_index(index)

    os.environ[PROVIDERS[provider]["env"]] = key  # live immediately, this process
    return {"provider": provider, **entry}


def get_key(provider: str) -> str | None:
    if provider not in PROVIDERS:
        return None
    backend = _load_index().get(provider, {}).get("backend")
    if backend == "keyring":
        stored = _keyring_get(provider)
        if stored:
            return stored
    return _file_load().get(provider)


def delete_key(provider: str) -> bool:
    """Remove a key everywhere (keyring, file, index, process env). True if it existed."""
    index = _load_index()
    existed = provider in index or provider in _file_load()
    _keyring_delete(provider)
    data = _file_load()
    if provider in data:
        del data[provider]
        _file_save(data)
    if provider in index:
        del index[provider]
        _save_index(index)
    env_name = PROVIDERS.get(provider, {}).get("env", "")
    if env_name:
        os.environ.pop(env_name, None)
    return existed


def apply_keys_to_env() -> list[str]:
    """Export stored keys into os.environ for providers whose env var is unset.

    A real env var (the .env escape hatch) always wins. Returns providers applied.
    """
    applied: list[str] = []
    for provider in _load_index():
        env_name = PROVIDERS.get(provider, {}).get("env", "")
        if not env_name or os.environ.get(env_name, "").strip():
            continue
        key = get_key(provider)
        if key:
            os.environ[env_name] = key
            applied.append(provider)
    return applied


def import_env_keys() -> list[str]:
    """One-time .env → keychain migrator. Imports env keys not yet stored."""
    imported: list[str] = []
    index = _load_index()
    for provider, meta in PROVIDERS.items():
        if provider in index:
            continue
        value = os.environ.get(meta["env"], "").strip()
        if value:
            set_key(provider, value, source="env-import")
            imported.append(provider)
    return imported


# ── Live validation (1-token test call) ───────────────────────────────────────

def test_key(provider: str, key: str | None = None) -> tuple[bool, str]:
    """Fire a 1-token completion against the provider. Never raises.

    Uses the stored key when none is passed. (ok, detail).
    """
    if provider not in PROVIDERS:
        return False, f"unknown provider: {provider}"
    meta = PROVIDERS[provider]
    if not meta["test_model"]:
        return False, "no test call defined for this provider — key stored unverified"
    key = (key or "").strip() or get_key(provider) or ""
    if not key:
        return False, "no key to test"
    try:
        import litellm
        litellm.completion(
            model=meta["test_model"],
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            api_key=key,
            timeout=15,
        )
        return True, "key verified with a live 1-token call"
    except Exception as exc:  # auth error, network, quota — all land here honestly
        return False, f"test call failed: {type(exc).__name__}: {exc}"[:300]


# ── Connections card payload ──────────────────────────────────────────────────

def _mtd_spend_by_provider() -> dict[str, float]:
    """Month-to-date USD per provider, folded from the cost ledger's by_model rollup."""
    spend: dict[str, float] = {}
    try:
        from model_registry import detect_provider

        from cost_ledger import summarize
        rollup = summarize(days=max(1, datetime.now().day))
        for model, slot in rollup.get("by_model", {}).items():
            spend_provider = detect_provider(model)
            spend[spend_provider] = round(
                spend.get(spend_provider, 0.0) + float(slot.get("cost_usd", 0.0)), 6
            )
    except Exception:
        pass
    return spend


def list_connections() -> list[dict[str, Any]]:
    """One entry per provider for the Settings → Connections cards. No key material."""
    index = _load_index()
    spend = _mtd_spend_by_provider()
    cards: list[dict[str, Any]] = []
    for provider, meta in PROVIDERS.items():
        entry = index.get(provider, {})
        env_set = bool(os.environ.get(meta["env"], "").strip())
        cards.append({
            "provider": provider,
            "label": meta["label"],
            "key_page": meta["key_page"],
            "connected": bool(entry) or env_set,
            "backend": entry.get("backend", "env" if env_set else ""),
            "hint": entry.get("hint", ""),
            "source": entry.get("source", "env" if env_set else ""),
            "ts": entry.get("ts", ""),
            "mtd_spend_usd": spend.get(provider, 0.0),
        })
    return cards
