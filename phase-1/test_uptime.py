"""Uptime: classifying tunnel vs app failures, the ping, and waking-hours report math."""

from __future__ import annotations

import http.server
import importlib.util
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from datetime import time as dtime
from pathlib import Path
from unittest.mock import patch

import pilot_scorecard

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "uptime_ping.py"
_spec = importlib.util.spec_from_file_location("uptime_ping_under_test", _SCRIPT)
uptime_ping = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(uptime_ping)  # type: ignore[union-attr]

_NARAD = json.dumps({"status": "ok", "agent": "Narad"}).encode()
_NO_PROXY = {k: "" for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy")}


def _response(status=None, body=b"", headers=None, error=None):
    return {"http": status, "body": body, "headers": headers or {}, "error": error, "ms": 5}


class ClassificationTests(unittest.TestCase):
    def test_public_reached_health_or_access_counts_as_tunnel_up(self) -> None:
        cases = {
            "health": _response(200, _NARAD, {"cf-ray": "x"}),
            "access-redirect": _response(302, headers={
                "location": "https://example.cloudflareaccess.com/cdn-cgi/access/login/narad.example.com?kid=1",
            }),
            "access-path": _response(302, headers={"location": "https://narad.example.com/cdn-cgi/access/login"}),
            "access-403": _response(403, b"Forbidden", {"server": "cloudflare", "cf-ray": "y"}),
        }
        for name, response in cases.items():
            public = uptime_ping.classify_public(response)
            self.assertEqual(public["reached"], "health" if name == "health" else "access", name)
            self.assertIsNone(public["error"], name)
            self.assertEqual(uptime_ping.overall_status({"ok": True}, public), "up", name)

    def test_tunnel_failures_are_tunnel_down_when_the_app_is_up(self) -> None:
        cases = {
            "connection": (_response(error="refused"), "refused", None),
            "dns": (_response(error="dns"), "dns", None),
            "bad-gateway": (_response(502, b"Bad gateway", {"cf-ray": "z"}), "tunnel", None),
            "argo-1033": (_response(530, b"<title>error code: 1033</title>", {"cf-ray": "z"}), "tunnel", "1033"),
            "403-with-1033": (_response(403, b"Error 1033", {"cf-ray": "z"}), "tunnel", "1033"),
            "not-narad": (_response(200, b"<html>parked</html>"), "unexpected_http", None),
        }
        for name, (response, error, cf_error) in cases.items():
            public = uptime_ping.classify_public(response)
            self.assertIsNone(public["reached"], name)
            self.assertEqual(public["error"], error, name)
            self.assertEqual(public["cf_error"], cf_error, name)
            self.assertEqual(uptime_ping.overall_status({"ok": True}, public), "tunnel_down", name)

    def test_local_failure_is_app_down_whatever_the_tunnel_says(self) -> None:
        local = uptime_ping.classify_local(_response(error="refused"))
        self.assertFalse(local["ok"])
        self.assertEqual(uptime_ping.overall_status(local, None), "app_down")
        public = uptime_ping.classify_public(_response(502, b"", {"cf-ray": "z"}))
        self.assertEqual(uptime_ping.overall_status(local, public), "app_down")
        self.assertFalse(uptime_ping.classify_local(_response(200, b'{"agent": "Other"}'))["ok"])
        self.assertTrue(uptime_ping.classify_local(_response(200, _NARAD))["ok"])


class _Handler(http.server.BaseHTTPRequestHandler):
    pings: list[tuple[str, bytes]] = []
    public_mode = "access"

    def log_message(self, *args) -> None:  # keep test output clean
        pass

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, _NARAD)
        elif self.path == "/public/health" and _Handler.public_mode == "access":
            self.send_response(302)
            self.send_header("Location", "https://example.cloudflareaccess.com/cdn-cgi/access/login/x")
            self.end_headers()
        elif self.path == "/public/health":
            self._send(530, b"error code: 1033", {"cf-ray": "abc"})
        else:
            self._send(404, b"")

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        _Handler.pings.append((self.path, self.rfile.read(length)))
        self._send(200, b"OK")

    def _send(self, status: int, body: bytes, headers: dict | None = None) -> None:
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class LiveCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.tempdir = tempfile.TemporaryDirectory()
        _Handler.pings = []
        base = f"http://127.0.0.1:{self.port}"
        self.env = patch.dict(os.environ, {
            **_NO_PROXY,
            "NO_PROXY": "127.0.0.1,localhost",
            "NARAD_HOME": self.tempdir.name,
            "NARAD_PORT": str(self.port),
            "NARAD_PUBLIC_URL": f"{base}/public",
            "NARAD_UPTIME_PING_URL": f"{base}/ping/abc",
        })
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.server.shutdown()
        self.server.server_close()
        self.tempdir.cleanup()

    def test_check_records_up_and_pings_success(self) -> None:
        _Handler.public_mode = "access"
        self.assertEqual(uptime_ping.main([]), 0)
        rows = (Path(self.tempdir.name) / "ops" / "uptime.jsonl").read_text().splitlines()
        record = json.loads(rows[-1])
        self.assertEqual(record["status"], "up")
        self.assertEqual(record["public"]["reached"], "access")
        self.assertEqual(record["ping"], "sent")
        self.assertEqual(_Handler.pings[-1][0], "/ping/abc")
        self.assertEqual(_Handler.pings[-1][1], b"up")

    def test_tunnel_down_pings_fail_endpoint(self) -> None:
        _Handler.public_mode = "down"
        self.assertEqual(uptime_ping.main([]), 1)
        record = json.loads((Path(self.tempdir.name) / "ops" / "uptime.jsonl").read_text().splitlines()[-1])
        self.assertEqual(record["status"], "tunnel_down")
        self.assertEqual(record["public"]["cf_error"], "1033")
        self.assertEqual(_Handler.pings[-1][0], "/ping/abc/fail")
        self.assertTrue(_Handler.pings[-1][1].startswith(b"tunnel_down"))
        self.assertNotIn(b"127.0.0.1", _Handler.pings[-1][1])  # no addresses leave in the ping


class UptimeReportTests(unittest.TestCase):
    tz = timezone(timedelta(hours=5, minutes=30))
    waking = (dtime(7, 0), dtime(23, 0))

    def _now(self) -> float:
        return datetime(2026, 9, 24, 12, 0, tzinfo=self.tz).timestamp()

    def _checks(self, now: float, days: float = 8, status_at=lambda t: "up", step: int = 300) -> list[dict]:
        start = now - days * 86400
        return [{"t": start + i * step, "status": status_at(start + i * step)}
                for i in range(int(days * 86400 // step) + 1)]

    def _waking_seconds(self, now: float) -> float:
        return pilot_scorecard.uptime_summary([{"t": now - 30 * 86400, "status": "up"}], now=now,
                                              waking=self.waking, tz=self.tz)["waking_seconds"]

    def test_all_up_passes_the_gate(self) -> None:
        now = self._now()
        summary = pilot_scorecard.uptime_summary(self._checks(now), now=now, waking=self.waking, tz=self.tz)
        self.assertEqual(summary["waking_seconds"], 7 * 16 * 3600)
        self.assertAlmostEqual(summary["uptime_pct"], 100.0, places=2)
        self.assertTrue(summary["window"]["full_window"])
        self.assertTrue(summary["gate_99"])

    def test_waking_hour_outage_counts_and_night_outage_does_not(self) -> None:
        now = self._now()
        day = datetime(2026, 9, 22, tzinfo=self.tz)
        noon, midnight = (day + timedelta(hours=12)).timestamp(), (day + timedelta(hours=2)).timestamp()

        def status(t: float) -> str:
            if noon <= t < noon + 3600:
                return "app_down"
            if midnight <= t < midnight + 3 * 3600:
                return "tunnel_down"
            return "up"

        summary = pilot_scorecard.uptime_summary(self._checks(now, status_at=status), now=now,
                                                 waking=self.waking, tz=self.tz)
        waking_s = 7 * 16 * 3600
        self.assertEqual(summary["seconds"]["app_down"], 3600)
        self.assertEqual(summary["seconds"]["tunnel_down"], 0)
        self.assertAlmostEqual(summary["uptime_pct"], 100 * (waking_s - 3600) / waking_s, places=2)
        self.assertTrue(summary["gate_99"])  # 99.1%: one hour a week is inside the budget

    def test_missing_checks_count_as_down(self) -> None:
        now = self._now()
        gap_start = datetime(2026, 9, 23, 9, 0, tzinfo=self.tz).timestamp()
        checks = [c for c in self._checks(now) if not gap_start <= c["t"] < gap_start + 3 * 3600]
        summary = pilot_scorecard.uptime_summary(checks, now=now, waking=self.waking, tz=self.tz)
        # The last check before the gap still covers its 10-minute allowance.
        self.assertEqual(summary["seconds"]["missing"], 3 * 3600 - 600 + 300)
        self.assertFalse(summary["gate_99"])

    def test_partial_window_never_passes(self) -> None:
        now = self._now()
        summary = pilot_scorecard.uptime_summary(self._checks(now, days=3), now=now, waking=self.waking, tz=self.tz)
        self.assertAlmostEqual(summary["uptime_pct"], 100.0, places=2)
        self.assertFalse(summary["window"]["full_window"])
        self.assertFalse(summary["gate_99"])

    def test_waking_hours_can_wrap_midnight_and_edge_checks_are_counted(self) -> None:
        now = self._now()
        checks = self._checks(now)
        for check in checks:
            check["public"] = {"reached": "access"}
        summary = pilot_scorecard.uptime_summary(checks, now=now, waking=(dtime(22, 0), dtime(6, 0)), tz=self.tz)
        self.assertEqual(summary["waking_hours"], "22:00-06:00")
        self.assertEqual(summary["waking_seconds"], 7 * 8 * 3600)
        self.assertGreater(summary["edge_only_checks"], 0)
        self.assertEqual(pilot_scorecard.parse_waking("7:30-22:15"), (dtime(7, 30), dtime(22, 15)))
        with self.assertRaises(ValueError):
            pilot_scorecard.parse_waking("morning")

    def test_no_checks_reports_nothing(self) -> None:
        summary = pilot_scorecard.uptime_summary([], now=self._now(), waking=self.waking, tz=self.tz)
        self.assertIsNone(summary["uptime_pct"])
        self.assertFalse(summary["gate_99"])


if __name__ == "__main__":
    unittest.main()
