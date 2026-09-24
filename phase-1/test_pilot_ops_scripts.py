"""Pilot operations scripts: shared env, launchd templates, watchdog, phone enrollment, and Linux degradation."""

from __future__ import annotations

import http.server
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SHELL_SCRIPTS = [
    SCRIPTS / "pilot_env.sh", SCRIPTS / "run_backend.sh", SCRIPTS / "run_tunnel.sh",
    SCRIPTS / "run_job.sh", SCRIPTS / "narad_watchdog.sh", SCRIPTS / "install_launchd.sh",
    ROOT / "Start Family Pilot.command", SCRIPTS / "run_artemis.sh", SCRIPTS / "enroll_android.sh",
]
JOBS = ("backend", "tunnel", "watchdog", "uptime", "backup", "drill", "awake", "cua-driver", "artemis")
_BASH = shutil.which("bash")


@unittest.skipIf(_BASH is None, "bash is not installed")
class PilotOpsScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.root / "home"),
            "NARAD_HOME": str(self.root / "narad"),
            "NARAD_LOG_DIR": str(self.root / "logs"),
            "NARAD_ENV_FILE": str(self.root / "no.env"),  # never the developer's real .env
        }
        (self.root / "home").mkdir()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _run(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([_BASH, *args], env={**self.env, **(env or {})}, capture_output=True,
                              text=True, timeout=60, cwd=ROOT)

    def test_scripts_parse_and_are_executable(self) -> None:
        for script in SHELL_SCRIPTS:
            result = subprocess.run([_BASH, "-n", str(script)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, f"{script.name}: {result.stderr}")
        for script in SHELL_SCRIPTS[1:] + [SCRIPTS / "narad_backup.py", SCRIPTS / "uptime_ping.py"]:
            self.assertTrue(os.access(script, os.X_OK), f"{script.name} is not executable")

    def test_pilot_env_matches_the_launcher_defaults(self) -> None:
        env_file = self.root / "pilot.env"
        env_file.write_text("NARAD_PUBLIC_URL=https://narad.example.com\nNARAD_PORT=8123\n")
        probe = ('source scripts/pilot_env.sh; printf "%s|%s|%s|%s|%s|%s" "$NARAD_AUTH" "${MEDIA_URL_BASE:-}" '
                 '"$NARAD_ALLOWED_ORIGINS" "$NARAD_PROVIDER_TIERS" "$NARAD_PORT" "$BSK_AUTO_UPDATE"')
        with_url = self._run("-c", probe, env={"NARAD_ENV_FILE": str(env_file)})
        self.assertEqual(with_url.returncode, 0, with_url.stderr)
        self.assertEqual(with_url.stdout.split("|"), [
            "strict", "https://narad.example.com/media",
            "https://narad.example.com,http://localhost:5174,http://127.0.0.1:5174",
            "sarvam=trusted", "8123", "off",
        ])
        without = self._run("-c", probe)
        self.assertEqual(without.stdout.split("|")[1:3], ["", "http://localhost:5174,http://127.0.0.1:5174"])

    def test_rendered_launchd_jobs_are_valid_plists(self) -> None:
        odd_home = self.root / "home & more|here"
        odd_home.mkdir()
        out = self.root / "rendered"
        result = self._run(str(SCRIPTS / "install_launchd.sh"), "render", str(out), env={"HOME": str(odd_home)})
        self.assertEqual(result.returncode, 0, result.stderr)
        plists = {}
        for job in JOBS:
            text = (out / f"com.narad.{job}.plist").read_text()
            self.assertNotRegex(text, r"@[A-Z_]+@", job)  # every template token filled in
            plists[job] = plistlib.loads(text.encode())
            self.assertEqual(plists[job]["Label"], f"com.narad.{job}")
            for arg in plists[job]["ProgramArguments"]:
                if arg.startswith("/") and "scripts/" in arg:
                    self.assertTrue(Path(arg).exists(), arg)
        self.assertTrue(plists["backend"]["KeepAlive"])
        self.assertGreaterEqual(plists["backend"]["ThrottleInterval"], 30)
        self.assertTrue(plists["tunnel"]["KeepAlive"])
        self.assertEqual(plists["watchdog"]["StartInterval"], 120)
        self.assertEqual(plists["uptime"]["StartInterval"], 300)
        self.assertEqual(plists["backup"]["StartCalendarInterval"], {"Hour": 3, "Minute": 30})
        self.assertIn("Weekday", plists["drill"]["StartCalendarInterval"])
        self.assertEqual(plists["awake"]["ProgramArguments"], ["/usr/bin/caffeinate", "-s"])
        self.assertTrue(plists["backend"]["EnvironmentVariables"]["PATH"].startswith(f"{odd_home}/.local/bin:"))
        self.assertEqual(plists["backend"]["StandardOutPath"], str(self.root / "logs" / "backend.log"))
        self.assertEqual(plists["backup"]["ProgramArguments"][-2:], [str(SCRIPTS / "narad_backup.py"), "backup"])
        # The sidecars: kept alive, Cua Driver with telemetry off in its own environment.
        cua = plists["cua-driver"]
        self.assertEqual(cua["ProgramArguments"][-1], "serve")
        self.assertTrue(cua["ProgramArguments"][0].endswith("cua-driver"))
        self.assertEqual(cua["EnvironmentVariables"]["CUA_DRIVER_RS_TELEMETRY_ENABLED"], "false")
        self.assertEqual(cua["EnvironmentVariables"]["CUA_TELEMETRY_ENABLED"], "false")
        self.assertTrue(cua["KeepAlive"])
        self.assertEqual(cua["StandardOutPath"], str(self.root / "logs" / "cua-driver.log"))
        self.assertEqual(plists["artemis"]["ProgramArguments"], ["/bin/bash", str(SCRIPTS / "run_artemis.sh")])
        self.assertTrue(plists["artemis"]["KeepAlive"])
        self.assertGreaterEqual(plists["artemis"]["ThrottleInterval"], 30)

    @unittest.skipIf(sys.platform == "darwin", "checks the non-macOS path")
    def test_launchd_commands_degrade_cleanly_off_macos(self) -> None:
        status = self._run(str(SCRIPTS / "install_launchd.sh"), "status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("launchd is macOS-only", status.stdout)
        self.assertIn("NOT answering", status.stdout)
        self.assertIn("Artemis: not set up", status.stdout)
        for command in ("install", "uninstall"):
            result = self._run(str(SCRIPTS / "install_launchd.sh"), command)
            self.assertEqual(result.returncode, 1)
            self.assertIn("macOS-only", result.stderr)
        self.assertFalse((self.root / "home" / "Library").exists())
        usage = self._run(str(SCRIPTS / "install_launchd.sh"))
        self.assertEqual(usage.returncode, 2)

    def test_run_artemis_refuses_cleanly_when_artemis_is_missing(self) -> None:
        result = self._run(str(SCRIPTS / "run_artemis.sh"), env={"NARAD_ARTEMIS_DIR": str(self.root / "none")})
        self.assertEqual(result.returncode, 78)
        self.assertIn("Artemis is not installed", result.stderr)

    def test_enroll_android_dry_run_touches_nothing_and_needs_a_chosen_serial(self) -> None:
        enroll = str(SCRIPTS / "enroll_android.sh")
        plan = self._run(enroll, "--dry-run", "--serial", "emulator-5554", "--profile", "member1",
                         "--label", "Phone A")
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertIn("helper install --serial emulator-5554", plan.stdout)
        self.assertIn("deviceidle whitelist +com.artemis.helper", plan.stdout)
        self.assertIn('would POST http://127.0.0.1:8000/interaction-targets {"kind":"artemis",'
                      '"external_id":"emulator-5554","label":"Phone A","profile_id":"member1"}', plan.stdout)
        self.assertIn("Battery: Unrestricted", plan.stdout)
        self.assertFalse((self.root / "narad").exists())  # nothing written
        # No serial: list and stop, even in a dry run; nothing is ever picked for the person.
        unnamed = self._run(enroll, "--dry-run", "--profile", "member1")
        self.assertEqual(unnamed.returncode, 2)
        self.assertIn("Pick the phone to enroll", unnamed.stdout)
        for bad in (["--serial", "a;reboot", "--profile", "member1"], ["--serial", "emulator-5554"],
                    ["--serial", "emulator-5554", "--profile", "../owner"]):
            refused = self._run(enroll, "--dry-run", *bad)
            self.assertEqual(refused.returncode, 1, bad)
        if shutil.which("adb") is None:
            missing = self._run(enroll, "--serial", "emulator-5554", "--profile", "member1")
            self.assertEqual(missing.returncode, 1)
            self.assertIn("adb is not installed", missing.stderr)

    @unittest.skipIf(shutil.which("curl") is None, "curl is not installed")
    def test_watchdog_counts_failures_restarts_after_the_limit_and_rotates_logs(self) -> None:
        env = {"NARAD_PORT": "9", "NARAD_WATCHDOG_FAILURES": "2", "NARAD_LOG_MAX_BYTES": "100"}
        logs = self.root / "logs"
        logs.mkdir()
        (logs / "backend.log").write_text("x" * 500)
        state = self.root / "narad" / "ops" / "watchdog.failures"

        first = self._run(str(SCRIPTS / "narad_watchdog.sh"), env=env)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(state.read_text().strip(), "1")
        self.assertEqual((logs / "backend.log").read_text(), "")
        self.assertEqual(len((logs / "backend.log.1").read_text()), 500)

        env["NARAD_LOG_MAX_BYTES"] = "1000000"
        second = self._run(str(SCRIPTS / "narad_watchdog.sh"), env=env)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(state.read_text().strip(), "0")  # reset after acting
        log = (logs / "watchdog.log").read_text()
        self.assertIn("health check failed (1/2)", log)
        if sys.platform != "darwin":
            self.assertIn("launchctl is not available", log)

        class Healthy(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def do_GET(self) -> None:
                body = json.dumps({"agent": "Narad"}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Healthy)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            state.write_text("1")
            ok = self._run(str(SCRIPTS / "narad_watchdog.sh"), env={**env, "NARAD_PORT": str(server.server_address[1])})
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(state.read_text().strip(), "0")
        self.assertIn("healthy again after 1 failed check(s)", (logs / "watchdog.log").read_text())


if __name__ == "__main__":
    unittest.main()
