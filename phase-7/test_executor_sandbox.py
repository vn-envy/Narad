"""Security-floor regression tests for the Phase-7 executor.

Covers the C-phase guarantees:
  - AST safety analysis blocks imports, calls, and classic string-blocklist evasions
  - Subprocess env is scrubbed — secrets in the parent never cross
  - Wall-clock timeout kills the whole process group
  - Dharma action gate is mandatory and fail-closed via policy
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Isolate NARAD_HOME before any narad import so ledger/policy writes stay here.
_TEST_HOME = tempfile.mkdtemp(prefix="narad-executor-test-")
os.environ["NARAD_HOME"] = _TEST_HOME

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop
import narad_paths  # noqa: F401  — registers all phase dirs; must precede phase imports

# isort: split
# Pytest imports every test module at collection before running any test, so an
# earlier-collected suite may already have imported narad_config under a
# different NARAD_HOME. Pop the chain so everything below re-binds to
# _TEST_HOME — otherwise this module's DHARMA_POLICY_PATH and the dharma module
# the executor lazily imports would point at different homes.
for _name in ("executor", "dharma", "narad_config"):
    sys.modules.pop(_name, None)

from executor import _check_safety, execute_code  # noqa: E402

from narad_config import DHARMA_POLICY_PATH  # noqa: E402


class AstSafetyTests(unittest.TestCase):
    def test_blocked_imports(self) -> None:
        for code in (
            "import subprocess",
            "import subprocess as sp",
            "from subprocess import run",
            "import requests",
            "from urllib.request import urlopen",
            "import importlib",
        ):
            with self.subTest(code=code):
                self.assertIsNotNone(_check_safety(code))

    def test_blocked_calls_and_evasions(self) -> None:
        for code in (
            "import os\nos.system('ls')",
            "import os\nos.execvp('ls', ['ls'])",
            "import shutil\nshutil.rmtree('/tmp/x')",
            "eval('1+1')",
            "__import__('subprocess')",
            "import os\ngetattr(os, 'system')('ls')",
            "import os\nn = 'sys' + 'tem'\ngetattr(os, n)('ls')",
            "open('/etc/passwd')",
        ):
            with self.subTest(code=code):
                self.assertIsNotNone(_check_safety(code))

    def test_benign_code_passes(self) -> None:
        for code in (
            "print('hello')",
            "import os\nprint(os.environ.get('OUTPUT_DIR'))",
            "import json, math\nprint(json.dumps({'x': math.pi}))",
            "with open('out.txt', 'w') as f:\n    f.write('ok')",
        ):
            with self.subTest(code=code):
                self.assertIsNone(_check_safety(code))


class ExecutionTests(unittest.TestCase):
    def test_env_is_scrubbed(self) -> None:
        os.environ["EXECUTOR_TEST_API_KEY"] = "sk-should-never-cross"
        try:
            result = execute_code(
                "import os, json\n"
                "print(json.dumps(sorted(k for k in os.environ if 'KEY' in k or 'TOKEN' in k or 'SECRET' in k)))\n"
                "print(os.environ.get('OUTPUT_DIR') is not None)"
            )
            self.assertEqual(result["status"], "ok")
            first, second = result["stdout"].strip().splitlines()
            self.assertEqual(json.loads(first), [])
            self.assertEqual(second, "True")
        finally:
            os.environ.pop("EXECUTOR_TEST_API_KEY", None)

    def test_timeout_kills_process(self) -> None:
        result = execute_code("import time\nwhile True: time.sleep(0.2)", timeout_s=2)
        self.assertEqual(result["status"], "timeout")

    def test_stdout_capped(self) -> None:
        result = execute_code("print('x' * 100_000)")
        self.assertEqual(result["status"], "ok")
        self.assertLessEqual(len(result["stdout"]), 4000)


class DharmaGateTests(unittest.TestCase):
    def test_gate_blocks_when_disabled(self) -> None:
        original = DHARMA_POLICY_PATH.read_text() if DHARMA_POLICY_PATH.exists() else None
        try:
            policy = json.loads(original) if original else {}
            policy.setdefault("actions", {})["executor"] = {"enabled": False}
            DHARMA_POLICY_PATH.parent.mkdir(parents=True, exist_ok=True)
            DHARMA_POLICY_PATH.write_text(json.dumps(policy))

            result = execute_code("print('should not run')")
            self.assertEqual(result["status"], "blocked")
            self.assertIn("disabled", result["stderr"])
        finally:
            if original is not None:
                DHARMA_POLICY_PATH.write_text(original)
            elif DHARMA_POLICY_PATH.exists():
                DHARMA_POLICY_PATH.write_text(json.dumps({}))

    def test_gate_allows_by_default(self) -> None:
        result = execute_code("print('gated ok')")
        self.assertEqual(result["status"], "ok")
        self.assertIn("gated ok", result["stdout"])


if __name__ == "__main__":
    unittest.main()
