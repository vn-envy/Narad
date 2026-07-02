from __future__ import annotations

import importlib
import os
import sys
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop
import narad_paths  # noqa: F401  — registers all phase dirs; must precede phase imports

# isort: split


def _reload_smriti():
    sys.modules.pop("smriti", None)
    fake_lancedb = types.SimpleNamespace(DBConnection=object)
    fake_pa = types.SimpleNamespace(
        schema=lambda fields: fields,
        field=lambda name, type_: (name, type_),
        utf8=lambda: "utf8",
        float32=lambda: "float32",
        list_=lambda inner, size=None: ("list", inner, size),
    )
    with patch.dict(sys.modules, {"lancedb": fake_lancedb, "pyarrow": fake_pa}):
        return importlib.import_module("smriti")


class SmritiConfigTests(unittest.TestCase):
    def test_default_prefers_gemini_when_available(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SMRITI_EMBEDDING_MODEL": "",
                "GEMINI_API_KEY": "set",
                "GOOGLE_API_KEY": "",
                "MIMO_API_KEY": "",
                "OPENAI_API_KEY": "",
            },
            clear=False,
        ):
            smriti = _reload_smriti()
            self.assertEqual(smriti._SMRITI_EMBED_PROVIDER, "gemini")
            self.assertEqual(smriti._EMBED_DIM, 768)

    def test_explicit_mimo_override_is_respected(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SMRITI_EMBEDDING_MODEL": "mimo",
                "GEMINI_API_KEY": "set",
                "MIMO_API_KEY": "set",
                "OPENAI_API_KEY": "",
            },
            clear=False,
        ):
            smriti = _reload_smriti()
            self.assertEqual(smriti._SMRITI_EMBED_PROVIDER, "mimo")
            self.assertEqual(smriti._EMBED_DIM, 1536)

    def test_gemini_quota_failure_enters_cooldown(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SMRITI_EMBEDDING_MODEL": "gemini",
                "GEMINI_API_KEY": "set",
                "GOOGLE_API_KEY": "",
                "MIMO_API_KEY": "",
                "OPENAI_API_KEY": "",
                "SMRITI_EMBED_FAILURE_COOLDOWN_S": "60",
            },
            clear=False,
        ):
            smriti = _reload_smriti()

        fake_client = types.SimpleNamespace(
            models=types.SimpleNamespace(
                embed_content=lambda **kwargs: (_ for _ in ()).throw(
                    RuntimeError("429 RESOURCE_EXHAUSTED quota exhausted")
                )
            )
        )
        fake_google = types.ModuleType("google")
        fake_genai = types.ModuleType("google.genai")
        fake_gtypes = types.ModuleType("google.genai.types")
        fake_genai.Client = lambda api_key: fake_client
        fake_gtypes.EmbedContentConfig = lambda output_dimensionality: {
            "output_dimensionality": output_dimensionality
        }
        fake_genai.types = fake_gtypes
        fake_google.genai = fake_genai

        with patch.dict(
            sys.modules,
            {
                "google": fake_google,
                "google.genai": fake_genai,
                "google.genai.types": fake_gtypes,
            },
        ):
            with self.assertRaises(RuntimeError):
                smriti._embed("first request")

            self.assertGreater(smriti._embed_unavailable_until, time.time())

            with self.assertRaises(RuntimeError) as ctx:
                smriti._embed("second request")

        self.assertIn("cooling down", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
