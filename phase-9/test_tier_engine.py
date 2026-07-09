"""
S1 tier engine tests — Gemma 4 ladder, tier selection, persistence, provider glue.

All recommend() calls pass an explicit hardware dict + explicit cloud/subscription
flags, so tests are pure and never touch the real machine or env.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import narad_paths  # noqa: F401  (bootstraps cross-phase imports)

import tier_engine
from tier_engine import MODELS, TIERS, detect_hardware, recommend


def _hw(ram=16.0, vram=0.0, disk=200.0, apple=False, gpu="cpu") -> dict:
    return {
        "ram_gb": ram,
        "vram_gb": vram,
        "apple_silicon": apple,
        "mlx_present": False,
        "gpu": gpu,
        "disk_free_gb": disk,
        "cpu_cores": 8,
        "machine": "x86_64",
        "system": "Linux",
    }


def _rec(hw, cloud=False, sub=False) -> dict:
    return recommend(hw, has_cloud_key=cloud, has_subscription=sub)


class ModelLadderTests(unittest.TestCase):
    def test_under_8gb_gets_e2b_t0(self):
        r = _rec(_hw(ram=4))
        self.assertEqual(r["model_key"], "e2b")
        self.assertEqual(r["tier"], "T0")
        self.assertEqual(r["tier_name"], "Kinara")

    def test_12gb_gets_e4b_with_12b_optin(self):
        r = _rec(_hw(ram=12))
        self.assertEqual(r["model_key"], "e4b")
        self.assertEqual(r["tier"], "T1")
        alt_keys = [a["model_key"] for a in r["alternatives"]]
        self.assertIn("12b-q4", alt_keys)

    def test_16gb_gets_flagship_12b_q4(self):
        r = _rec(_hw(ram=16))
        self.assertEqual(r["model_key"], "12b-q4")
        self.assertEqual(r["model"], "narad-local/gemma4-12b-it-qat")
        self.assertEqual(r["tier"], "T1")

    def test_32gb_gets_12b_q8(self):
        r = _rec(_hw(ram=32))
        self.assertEqual(r["model_key"], "12b-q8")

    def test_24gb_vram_offers_26b_alternative(self):
        r = _rec(_hw(ram=32, vram=24, gpu="nvidia"))
        alt_keys = [a["model_key"] for a in r["alternatives"]]
        self.assertEqual(alt_keys[0], "26b-a4b")

    def test_payload_shape(self):
        r = _rec(_hw(ram=16))
        for field in ("tier", "tier_name", "tier_card", "model", "model_label",
                      "quant", "est_download_gb", "est_tokens_per_sec",
                      "context_hint", "reasons", "alternatives"):
            self.assertIn(field, r)
        self.assertGreaterEqual(r["est_tokens_per_sec"], 2)


class DiskStepDownTests(unittest.TestCase):
    def test_tight_disk_steps_down_ladder(self):
        # 32 GB RAM wants 12b-q8 (needs 16 GB); only 8 GB free → fits 12b-q4? needs 9 → no → e4b needs 7 → yes
        r = _rec(_hw(ram=32, disk=8))
        self.assertEqual(r["model_key"], "e4b")
        self.assertTrue(any("stepped down" in reason for reason in r["reasons"]))

    def test_hopeless_disk_lands_on_e2b_with_warning(self):
        r = _rec(_hw(ram=32, disk=1))
        self.assertEqual(r["model_key"], "e2b")
        self.assertTrue(any("free up space" in reason for reason in r["reasons"]))

    def test_unknown_disk_skips_stepdown(self):
        r = _rec(_hw(ram=32, disk=0))
        self.assertEqual(r["model_key"], "12b-q8")


class TierSelectionTests(unittest.TestCase):
    def test_cloud_key_with_ram_recommends_hybrid_t4(self):
        r = _rec(_hw(ram=16), cloud=True)
        self.assertEqual(r["tier"], "T4")
        self.assertEqual(r["tier_name"], "Sangam")

    def test_cloud_key_tight_ram_recommends_t2(self):
        r = _rec(_hw(ram=4), cloud=True)
        self.assertEqual(r["tier"], "T2")
        self.assertEqual(r["tier_name"], "Kunji")

    def test_subscription_recommends_t3(self):
        r = _rec(_hw(ram=16), sub=True)
        self.assertEqual(r["tier"], "T3")
        self.assertEqual(r["tier_name"], "Sadasya")

    def test_cloud_key_beats_subscription(self):
        r = _rec(_hw(ram=16), cloud=True, sub=True)
        self.assertEqual(r["tier"], "T4")

    def test_unknown_ram_with_cloud_key_still_t4(self):
        r = _rec(_hw(ram=0), cloud=True)
        self.assertEqual(r["tier"], "T4")


class ChoicePersistenceTests(unittest.TestCase):
    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "onboarding.json"
            with patch.object(tier_engine, "ONBOARDING_PATH", path):
                saved = tier_engine.save_tier_choice("T1", "narad-local/gemma4-12b-it-qat")
                self.assertEqual(saved["tier"], "T1")
                self.assertEqual(saved["tier_name"], "Sthanik")
                loaded = tier_engine.load_tier_choice()
                self.assertEqual(loaded["model"], "narad-local/gemma4-12b-it-qat")
                self.assertEqual(loaded["source"], "user")
                # sibling keys survive
                data = json.loads(path.read_text())
                data["other"] = 1
                path.write_text(json.dumps(data))
                tier_engine.save_tier_choice("T4")
                data = json.loads(path.read_text())
                self.assertEqual(data["other"], 1)
                self.assertEqual(data["tier_choice"]["tier"], "T4")

    def test_unknown_tier_raises(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(tier_engine, "ONBOARDING_PATH", Path(td) / "onboarding.json"):
                with self.assertRaises(ValueError):
                    tier_engine.save_tier_choice("T9")

    def test_load_missing_file_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(tier_engine, "ONBOARDING_PATH", Path(td) / "nope.json"):
                self.assertIsNone(tier_engine.load_tier_choice())


class ProviderGlueTests(unittest.TestCase):
    def test_detect_provider_narad_local(self):
        from model_registry import detect_provider
        self.assertEqual(detect_provider("narad-local/gemma4-12b-it-qat"), "narad-local")

    def test_narad_local_routable_only_with_url(self):
        from model_registry import provider_available_for_model
        with patch.dict("os.environ", {"NARAD_LOCAL_URL": ""}, clear=False):
            self.assertFalse(provider_available_for_model("narad-local/gemma4-e2b-it-qat"))
        with patch.dict("os.environ", {"NARAD_LOCAL_URL": "http://127.0.0.1:880"}, clear=False):
            self.assertTrue(provider_available_for_model("narad-local/gemma4-e2b-it-qat"))

    def test_narad_local_model_profile_no_keyerror(self):
        from model_registry import get_model_profile
        profile = get_model_profile("narad-local/gemma4-e2b-it-qat")
        self.assertEqual(profile.provider, "narad-local")
        self.assertGreater(profile.max_context_tokens, 0)

    def test_cost_ledger_pins_narad_local_free(self):
        from cost_ledger import estimate_cost
        cost, priced = estimate_cost("narad-local/gemma4-12b-it-qat", 100_000, 50_000)
        self.assertEqual(cost, 0.0)
        self.assertTrue(priced)


class DetectHardwareTests(unittest.TestCase):
    def test_detect_hardware_shape(self):
        hw = detect_hardware()
        for field in ("ram_gb", "vram_gb", "apple_silicon", "mlx_present",
                      "gpu", "disk_free_gb", "cpu_cores", "machine", "system"):
            self.assertIn(field, hw)


class CatalogSanityTests(unittest.TestCase):
    def test_all_models_have_required_fields(self):
        for key, m in MODELS.items():
            for field in ("id", "label", "quant", "download_gb", "min_ram_gb", "context_hint"):
                self.assertIn(field, m, f"{key} missing {field}")
            self.assertTrue(m["id"].startswith("narad-local/"), key)

    def test_five_tiers(self):
        self.assertEqual(sorted(TIERS), ["T0", "T1", "T2", "T3", "T4"])


if __name__ == "__main__":
    unittest.main()
