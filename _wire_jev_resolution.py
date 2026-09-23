import os

# ── decision_engine.py ────────────────────────────────────────────────────────
de = os.path.expanduser("~/Downloads/Projects/Narad/Narad/decision_engine.py")
src = open(de).read()

if "_stored_typesafe_key" not in src:
    helper = (
        'def _stored_typesafe_key() -> str:\n'
        '    """Resolve the TypeSafe Jev key from the project secret store (Kunji).\n\n'
        '    Kunji keeps the key in the OS keychain or its 0600 file fallback under\n'
        '    ~/.narad/config. Guarded so the decision engine never crashes on import when\n'
        '    Kunji is unavailable; a real env var still wins (callers check env first).\n'
        '    """\n'
        '    try:\n'
        '        from kunji import get_key\n'
        '        return (get_key("typesafe") or "").strip()\n'
        '    except Exception:\n'
        '        return ""\n\n\n'
    )
    anchor = "@dataclass(frozen=True)\nclass DecisionQuestion:"
    assert anchor in src
    src = src.replace(anchor, helper + anchor, 1)

    # __init__: fall back to the store when the env var is unset
    old_init = '        self.api_key = (api_key if api_key is not None else os.environ.get("TYPESAFE_API_KEY", "")).strip()'
    new_init = (
        '        key = (api_key if api_key is not None else os.environ.get("TYPESAFE_API_KEY", "")).strip()\n'
        '        if not key:\n'
        '            key = _stored_typesafe_key()\n'
        '            if key:\n'
        '                os.environ.setdefault("TYPESAFE_API_KEY", key)\n'
        '        self.api_key = key'
    )
    assert old_init in src
    src = src.replace(old_init, new_init, 1)

    # jev_status: report presence from env OR store
    old_status = '    key_present = bool(os.environ.get("TYPESAFE_API_KEY", "").strip())'
    new_status = '    key_present = bool(os.environ.get("TYPESAFE_API_KEY", "").strip() or _stored_typesafe_key())'
    assert old_status in src
    src = src.replace(old_status, new_status, 1)

    open(de, "w").write(src)
    print("patched decision_engine.py")
else:
    print("decision_engine.py already patched")

# ── job-ops pipeline/match.py ─────────────────────────────────────────────────
m = os.path.expanduser("~/Desktop/job-ops/pipeline/match.py")
src = open(m).read()

if "_typesafe_key" not in src:
    resolver = (
        'def _typesafe_key() -> str:\n'
        '    """Resolve the TypeSafe Jev credential: env var first, else the project secret store.\n\n'
        '    The store is Narad\'s "Kunji" (OS keychain or its 0600 file fallback at\n'
        '    ~/.narad/config/kunji_keys.json). Keeps the key out of the shell environment.\n'
        '    """\n'
        '    key = os.environ.get("TYPESAFE_API_KEY", "").strip()\n'
        '    if key:\n'
        '        return key\n'
        '    try:\n'
        '        from kunji import get_key as _kunji_get_key\n'
        '        key = (_kunji_get_key("typesafe") or "").strip()\n'
        '    except Exception:\n'
        '        key = ""\n'
        '    if not key:\n'
        '        try:\n'
        '            with open(os.path.expanduser("~/.narad/config/kunji_keys.json")) as _f:\n'
        '                key = (json.load(_f).get("typesafe") or "").strip()\n'
        '        except Exception:\n'
        '            key = ""\n'
        '    if not key:\n'
        '        raise RuntimeError(\n'
        '            "TYPESAFE_API_KEY unresolved: set the env var or store it with "\n'
        '            "kunji.set_key(\'typesafe\', <key>) (~/.narad/config/kunji_keys.json)."\n'
        '        )\n'
        '    return key\n\n\n'
    )
    anchor = 'def _get_llm_client(config: dict):'
    assert anchor in src
    src = src.replace(anchor, resolver + anchor, 1)

    old_auth = '"Authorization": f"Bearer {os.environ[\'TYPESAFE_API_KEY\']}",'
    new_auth = '"Authorization": f"Bearer {_typesafe_key()}",'
    assert old_auth in src, "auth line not found"
    src = src.replace(old_auth, new_auth, 1)

    open(m, "w").write(src)
    print("patched pipeline/match.py")
else:
    print("match.py already patched")
