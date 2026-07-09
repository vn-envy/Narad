"""
O5+S3 tests — Kunji key store, provider prefix detection, env bridge,
subscription adapter honesty, and provider-glue integration.

File backend is forced (KUNJI_BACKEND=file) and paths patched to a temp dir,
so tests never touch the real keychain, ~/.narad, or the network.
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop
import narad_paths  # noqa: F401  — registers all phase dirs; must precede phase imports

# isort: split
import kunji
import subscription_providers as subs


@contextmanager
def _kunji_sandbox():
    """Temp-dir file backend + env isolation for every key env var."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        env_names = [meta["env"] for meta in kunji.PROVIDERS.values()]
        clean = {name: "" for name in env_names}
        clean["KUNJI_BACKEND"] = "file"
        clean["NARAD_CLAUDE_SUBSCRIPTION"] = ""
        with ExitStack() as stack:
            stack.enter_context(patch.object(kunji, "_KEYS_PATH", tmp / "kunji_keys.json"))
            stack.enter_context(patch.object(kunji, "_INDEX_PATH", tmp / "kunji_index.json"))
            stack.enter_context(patch.dict(os.environ, clean, clear=False))
            for name in env_names:  # patch.dict with "" still leaves the var set
                os.environ.pop(name, None)
            os.environ["KUNJI_BACKEND"] = "file"
            yield tmp


class PrefixDetectionTests(unittest.TestCase):
    def test_prefixes(self):
        cases = {
            "sk-ant-api03-abc123def456": "anthropic",
            "AIzaSyD-xxxxxxxxxxxxxxxxx": "google",
            "dsk-1234567890abcdef": "deepseek",
            "sk-proj-1234567890abcdef": "openai",
        }
        for key, expected in cases.items():
            self.assertEqual(kunji.detect_provider_from_key(key), expected, key)

    def test_sk_ant_beats_sk(self):
        self.assertEqual(kunji.detect_provider_from_key("sk-ant-xyz-something"), "anthropic")

    def test_unknown_prefix_returns_none(self):
        self.assertIsNone(kunji.detect_provider_from_key("banana-key-123"))
        self.assertIsNone(kunji.detect_provider_from_key(""))

    def test_mask_key(self):
        self.assertEqual(kunji.mask_key("sk-ant-api03-abcd1234wxyz"), "sk-a…wxyz")
        self.assertEqual(kunji.mask_key("short"), "………")


class KeyStoreTests(unittest.TestCase):
    def test_set_get_delete_roundtrip(self):
        with _kunji_sandbox():
            entry = kunji.set_key("deepseek", "dsk-secret123456")
            self.assertEqual(entry["backend"], "file")
            self.assertEqual(entry["hint"], "dsk-…3456")
            self.assertEqual(kunji.get_key("deepseek"), "dsk-secret123456")
            # exported live into env
            self.assertEqual(os.environ.get("DEEPSEEK_API_KEY"), "dsk-secret123456")
            self.assertTrue(kunji.delete_key("deepseek"))
            self.assertIsNone(kunji.get_key("deepseek"))
            self.assertFalse(os.environ.get("DEEPSEEK_API_KEY"))

    def test_index_never_contains_key_material(self):
        with _kunji_sandbox():
            kunji.set_key("openai", "sk-proj-verysecretmaterial")
            raw = kunji._INDEX_PATH.read_text()
            self.assertNotIn("verysecret", raw)
            self.assertIn("hint", raw)

    def test_file_perms_owner_only(self):
        with _kunji_sandbox():
            kunji.set_key("google", "AIzaSyD-something-long")
            mode = kunji._KEYS_PATH.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_unknown_provider_raises(self):
        with _kunji_sandbox():
            with self.assertRaises(ValueError):
                kunji.set_key("nope", "sk-x")
            with self.assertRaises(ValueError):
                kunji.set_key("openai", "   ")

    def test_delete_missing_returns_false(self):
        with _kunji_sandbox():
            self.assertFalse(kunji.delete_key("openai"))


class EnvBridgeTests(unittest.TestCase):
    def test_apply_fills_gaps_but_real_env_wins(self):
        with _kunji_sandbox():
            kunji.set_key("deepseek", "dsk-fromstore11111")
            kunji.set_key("openai", "sk-fromstore222222")
            os.environ.pop("DEEPSEEK_API_KEY", None)          # gap → filled
            os.environ["OPENAI_API_KEY"] = "sk-fromdotenv"    # .env escape hatch → wins
            applied = kunji.apply_keys_to_env()
            self.assertIn("deepseek", applied)
            self.assertNotIn("openai", applied)
            self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "dsk-fromstore11111")
            self.assertEqual(os.environ["OPENAI_API_KEY"], "sk-fromdotenv")

    def test_import_env_keys_once(self):
        with _kunji_sandbox():
            os.environ["GEMINI_API_KEY"] = "AIzaSyD-import-me-now"
            imported = kunji.import_env_keys()
            self.assertEqual(imported, ["google"])
            self.assertEqual(kunji.get_key("google"), "AIzaSyD-import-me-now")
            self.assertEqual(kunji.import_env_keys(), [])  # second run: no-op


class ConnectionsCardTests(unittest.TestCase):
    def test_list_shape_and_masking(self):
        with _kunji_sandbox():
            kunji.set_key("anthropic", "sk-ant-api03-abcd1234wxyz")
            cards = {c["provider"]: c for c in kunji.list_connections()}
            self.assertEqual(set(cards), set(kunji.PROVIDERS))
            card = cards["anthropic"]
            self.assertTrue(card["connected"])
            self.assertEqual(card["hint"], "sk-a…wxyz")
            self.assertNotIn("key", card)
            self.assertIn("mtd_spend_usd", card)
            self.assertIn("key_page", card)
            self.assertFalse(cards["openai"]["connected"])

    def test_env_only_key_shows_connected(self):
        with _kunji_sandbox():
            os.environ["DEEPSEEK_API_KEY"] = "dsk-env-only"
            cards = {c["provider"]: c for c in kunji.list_connections()}
            self.assertTrue(cards["deepseek"]["connected"])
            self.assertEqual(cards["deepseek"]["backend"], "env")


class TestKeyFailClosedTests(unittest.TestCase):
    def test_no_key_to_test(self):
        with _kunji_sandbox():
            ok, detail = kunji.test_key("openai")
            self.assertFalse(ok)
            self.assertIn("no key", detail)

    def test_transport_error_reported_not_raised(self):
        with _kunji_sandbox():
            fake = types.ModuleType("litellm")
            def _boom(**kw):
                raise ConnectionError("network down")
            fake.completion = _boom
            with patch.dict(sys.modules, {"litellm": fake}):
                ok, detail = kunji.test_key("openai", "sk-proj-abc")
            self.assertFalse(ok)
            self.assertIn("ConnectionError", detail)

    def test_success_path(self):
        with _kunji_sandbox():
            fake = types.ModuleType("litellm")
            fake.completion = lambda **kw: types.SimpleNamespace(choices=[])
            with patch.dict(sys.modules, {"litellm": fake}):
                ok, detail = kunji.test_key("anthropic", "sk-ant-abc")
            self.assertTrue(ok)


class SubscriptionAdapterTests(unittest.TestCase):
    def test_registry_has_claude_adapter(self):
        self.assertIn("claude-agent-sdk", subs.ADAPTERS)
        self.assertIsNotNone(subs.get_adapter("claude-agent-sdk"))

    def test_status_honest_when_sdk_missing(self):
        adapter = subs.get_adapter("claude-agent-sdk")
        with patch.object(adapter, "_sdk_installed", return_value=False), \
             patch.object(adapter, "_cli_auth_present", return_value=False), \
             patch.dict(os.environ, {"NARAD_CLAUDE_SUBSCRIPTION": ""}, clear=False):
            os.environ.pop("NARAD_CLAUDE_SUBSCRIPTION", None)
            st = adapter.status()
        self.assertFalse(st.installed)
        self.assertFalse(st.available)
        self.assertIsNone(st.remaining_credit)  # never guessed
        self.assertEqual(st.models, [])

    def test_available_needs_both_install_and_signin(self):
        adapter = subs.get_adapter("claude-agent-sdk")
        with patch.object(adapter, "_sdk_installed", return_value=True), \
             patch.object(adapter, "_cli_auth_present", return_value=False), \
             patch.dict(os.environ, {"NARAD_CLAUDE_SUBSCRIPTION": "1"}, clear=False):
            self.assertTrue(adapter.available())
        with patch.object(adapter, "_sdk_installed", return_value=False), \
             patch.dict(os.environ, {"NARAD_CLAUDE_SUBSCRIPTION": "1"}, clear=False):
            self.assertFalse(adapter.available())

    def test_completion_raises_clear_error_when_unavailable(self):
        adapter = subs.get_adapter("claude-agent-sdk")
        with patch.object(adapter, "_sdk_installed", return_value=False), \
             patch.object(adapter, "_cli_auth_present", return_value=False):
            os.environ.pop("NARAD_CLAUDE_SUBSCRIPTION", None)
            with self.assertRaises(RuntimeError) as ctx:
                adapter.completion("narad-claude-sdk/claude-sonnet-4-6", [{"role": "user", "content": "hi"}])
            self.assertIn("unavailable", str(ctx.exception))

    def test_subscriptions_payload_shape(self):
        payload = subs.subscriptions_payload()
        self.assertEqual(len(payload), 1)
        for field in ("provider", "label", "installed", "signed_in", "available", "detail"):
            self.assertIn(field, payload[0])


class ProviderGlueTests(unittest.TestCase):
    def test_detect_provider(self):
        from model_registry import detect_provider
        self.assertEqual(detect_provider("narad-claude-sdk/claude-sonnet-4-6"), "narad-claude-sdk")

    def test_model_profile_gets_anthropic_class_window(self):
        from model_registry import get_model_profile
        profile = get_model_profile("narad-claude-sdk/claude-sonnet-4-6")
        self.assertEqual(profile.provider, "narad-claude-sdk")
        self.assertGreaterEqual(profile.max_context_tokens, 200_000)

    def test_availability_follows_subscription(self):
        import model_registry
        adapter = subs.get_adapter("claude-agent-sdk")
        with patch.object(adapter, "_sdk_installed", return_value=True), \
             patch.dict(os.environ, {"NARAD_CLAUDE_SUBSCRIPTION": "1"}, clear=False):
            self.assertTrue(model_registry.provider_available_for_model("narad-claude-sdk/claude-haiku-4-5"))
        with patch.object(adapter, "_sdk_installed", return_value=False), \
             patch.object(adapter, "_cli_auth_present", return_value=False):
            os.environ.pop("NARAD_CLAUDE_SUBSCRIPTION", None)
            self.assertFalse(model_registry.provider_available_for_model("narad-claude-sdk/claude-haiku-4-5"))

    def test_cost_pinned_free(self):
        from cost_ledger import estimate_cost
        cost, priced = estimate_cost("narad-claude-sdk/claude-sonnet-4-6", 500_000, 100_000)
        self.assertEqual(cost, 0.0)
        self.assertTrue(priced)

    def test_tier_engine_sees_subscription(self):
        import tier_engine
        adapter = subs.get_adapter("claude-agent-sdk")
        hw = {"ram_gb": 16, "vram_gb": 0, "disk_free_gb": 100, "apple_silicon": False, "gpu": "cpu"}
        with patch.object(adapter, "_sdk_installed", return_value=True), \
             patch.dict(os.environ, {"NARAD_CLAUDE_SUBSCRIPTION": "1"}, clear=False):
            r = tier_engine.recommend(hw, has_cloud_key=False)  # subscription auto-detected
        self.assertEqual(r["tier"], "T3")


if __name__ == "__main__":
    unittest.main()
