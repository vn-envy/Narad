"""Phase 0 review hardening: host shell custody, browser reach, per-target
serialization, per-profile notifications, and the pilot launcher's opt-outs.

All offline: no network, no LLM, no real crontab, Chromium, or phone.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]
import narad_paths  # noqa: F401

# isort: split
import artemis_adapter
import browser_skill
import computer_use_skill
import host_access
import interaction_targets
import mail_triage_skill
import shell_skill

import family_profiles
import profile_context
import vahana
from profile_context import profile_scope


class _FamilyHome(unittest.TestCase):
    """A temporary NARAD_HOME-like tree with an owner (default) and Bob."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name).resolve()
        self.home = self.root / "home"
        self.narad = self.home / ".narad"
        self.config = self.narad / "config"
        self.profiles = self.narad / "profiles"
        self.artifacts = self.narad / "artifacts"
        self.attachments = self.narad / "attachments"
        for directory in (self.config, self.profiles, self.artifacts, self.attachments):
            directory.mkdir(parents=True)
        self.patches = [
            patch.object(family_profiles, "FAMILY_PROFILES_PATH", self.config / "family_profiles.json"),
            patch.object(family_profiles, "PROFILE_SESSION_SECRET_PATH", self.config / "profile_session_secret"),
            patch.object(profile_context, "PROFILES_DIR", self.profiles),
            patch.object(host_access, "CONFIG_DIR", self.config),
            patch.object(host_access, "PROFILES_DIR", self.profiles),
            patch.object(host_access, "ARTIFACTS_DIR", self.artifacts),
            patch.object(host_access, "ATTACHMENTS_DIR", self.attachments),
            patch.dict(os.environ, {"HOME": str(self.home)}),
        ]
        for active in self.patches:
            active.start()
        family_profiles.create_profile("Bob", "1357")
        (self.config / "api_token").write_text("host-token\n")
        (self.profiles / "bob").mkdir(exist_ok=True)
        (self.profiles / "bob" / "notes.txt").write_text("bob private")
        (self.profiles / "default").mkdir(exist_ok=True)
        (self.profiles / "default" / "notes.txt").write_text("owner private")
        (self.home / "project.txt").write_text("owner project")

    def tearDown(self) -> None:
        for active in reversed(self.patches):
            active.stop()
        self.tempdir.cleanup()


class HostShellCustodyTests(_FamilyHome):
    def test_family_members_cannot_run_host_commands(self) -> None:
        run = Mock(side_effect=AssertionError("a non-owner reached the host shell"))
        with profile_scope("bob"), patch.object(shell_skill.subprocess, "run", run):
            results = [
                shell_skill.run_shell("printenv DEEPSEEK_API_KEY"),
                shell_skill.run_shell("git status"),
                shell_skill.write_script("print('hi')", "~/x.py"),
                shell_skill.schedule_cron("0 2 * * *", "echo hi", "nightly"),
                shell_skill.list_cron_jobs(),
                shell_skill.remove_cron_job("nightly"),
            ]
        self.assertEqual([result["status"] for result in results], ["blocked"] * len(results))
        self.assertIn("owner", results[0]["message"])
        self.assertFalse((self.home / "x.py").exists())

    def test_owner_shell_never_sees_secrets_or_unlisted_chains(self) -> None:
        captured: dict = {}

        def _fake_run(command, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        env = {"DEEPSEEK_API_KEY": "sk-secret", "NARAD_API_TOKEN": "t", "PATH": "/usr/bin", "SSH_AUTH_SOCK": "/tmp/agent"}
        with profile_scope("default"), patch.dict(os.environ, env), \
             patch.object(shell_skill.subprocess, "run", side_effect=_fake_run):
            self.assertEqual(shell_skill.run_shell("git status", working_dir=str(self.home))["status"], "ok")
            for command in ("printenv DEEPSEEK_API_KEY", "env", "echo hi; id -un", "ls && whoami",
                            "echo $(id)", "echo `id`", "echo hi\nid"):
                self.assertEqual(shell_skill.run_shell(command)["status"], "blocked", command)
            self.assertEqual(shell_skill._check_command("python3 x.py >> log 2>&1"), None)
            self.assertEqual(shell_skill._check_command('grep "a;b" notes.txt | sort'), None)
        self.assertNotIn("DEEPSEEK_API_KEY", captured["env"])
        self.assertNotIn("NARAD_API_TOKEN", captured["env"])
        self.assertEqual(captured["env"]["PATH"], "/usr/bin")
        self.assertEqual(captured["env"]["SSH_AUTH_SOCK"], "/tmp/agent")

    def test_read_file_never_opens_secrets_or_another_profile(self) -> None:
        with profile_scope("default"):
            self.assertEqual(shell_skill.read_file("~/project.txt")["content"], "owner project")
            self.assertEqual(shell_skill.read_file("~/.narad/profiles/default/notes.txt")["content"], "owner private")
            for secret in ("~/.narad/config/api_token", "~/.narad/config/profile_session_secret",
                           "~/.narad/profiles/bob/notes.txt", "~/.narad/profiles/../config/api_token"):
                result = shell_skill.read_file(secret)
                self.assertEqual(result["status"], "blocked", secret)
                self.assertEqual(result["content"], "")
        with profile_scope("bob"):
            self.assertEqual(shell_skill.read_file("~/.narad/profiles/bob/notes.txt")["content"], "bob private")
            for foreign in ("~/project.txt", "~/.narad/profiles/default/notes.txt", "~/.narad/config/api_token"):
                self.assertEqual(shell_skill.read_file(foreign)["status"], "blocked", foreign)

    def test_browser_uploads_are_confined_the_same_way(self) -> None:
        with profile_scope("bob"):
            self.assertEqual(
                computer_use_skill._upload_path("~/.narad/profiles/bob/notes.txt"),
                self.profiles / "bob" / "notes.txt",
            )
            for blocked in ("~/.narad/config/api_token", "~/project.txt", "~/.narad/profiles/default/notes.txt"):
                with self.assertRaisesRegex(ValueError, "Upload blocked"):
                    computer_use_skill._upload_path(blocked)
        with profile_scope("default"):
            self.assertEqual(computer_use_skill._upload_path("~/project.txt"), self.home / "project.txt")
            with self.assertRaisesRegex(ValueError, "Upload blocked"):
                computer_use_skill._upload_path("~/.narad/config/api_token")

    def test_parallel_cron_edits_never_drop_a_job(self) -> None:
        crontab = self.root / "crontab"
        crontab.write_text("")

        def _fake_crontab(args, input=None, **kwargs):
            if args[-1] == "-l":
                text = crontab.read_text()
                time.sleep(0.05)  # widen the read → write window
                return SimpleNamespace(returncode=0, stdout=text, stderr="")
            crontab.write_text(input)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        barrier = threading.Barrier(4)

        def _schedule(index: int) -> None:
            barrier.wait()
            with profile_scope("default"):
                shell_skill.schedule_cron("0 2 * * *", f"echo job{index}", f"job{index}")

        with patch.object(shell_skill.subprocess, "run", side_effect=_fake_crontab):
            threads = [threading.Thread(target=_schedule, args=(index,)) for index in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        tags = sorted(line.split()[-1] for line in crontab.read_text().splitlines())
        self.assertEqual(tags, [f"narad:job{index}" for index in range(4)])


class BrowserReachTests(unittest.TestCase):
    def test_loopback_lan_and_numeric_hosts_are_blocked(self) -> None:
        for url in (
            "http://127.0.0.1:8000/threads/latest?user_id=default",
            "http://localhost:8124/api/status",
            "http://[::1]:8000/",
            "http://2130706433:8124/",
            "http://0x7f000001/",
            "http://10.0.0.1/",
            "http://192.168.1.20/",
            "http://100.101.102.103/",
            "http://[::ffff:169.254.169.254]/",
            "http://0.0.0.0:8000/",
        ):
            with self.assertRaises(ValueError, msg=url):
                computer_use_skill._validate_url(url)
        public = [(2, 1, 6, "", ("93.184.216.34", 0))]
        with patch.object(computer_use_skill.socket, "getaddrinfo", return_value=public):
            self.assertEqual(computer_use_skill._validate_url("https://example.com/a"), "https://example.com/a")

    def test_owner_can_name_a_private_host(self) -> None:
        with patch.dict(os.environ, {"NARAD_BROWSER_PRIVATE_HOSTS": "127.0.0.1"}):
            self.assertEqual(computer_use_skill._validate_url("http://127.0.0.1:5173/"), "http://127.0.0.1:5173/")
            with self.assertRaises(ValueError):
                computer_use_skill._validate_url("http://localhost:8000/")

    def test_browse_url_refuses_private_hosts_before_launching(self) -> None:
        with patch.dict(sys.modules, {"playwright": None, "playwright.async_api": None}):
            result = asyncio.run(browser_skill.browse_url("http://127.0.0.1:8000/threads/latest"))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["content"], "")

    def test_isolated_browser_drops_a_redirect_to_a_private_host(self) -> None:
        class _Page:
            url = ""

            async def goto(self, url, **kwargs):
                self.url = "http://127.0.0.1:8000/threads/latest" if url.startswith("https://") else url

            async def wait_for_load_state(self, *args, **kwargs):
                return None

        page = _Page()
        public = [(2, 1, 6, "", ("93.184.216.34", 0))]
        real = computer_use_skill.socket.getaddrinfo

        def _resolve(host, *args, **kwargs):
            return public if host == "short.example" else real(host, *args, **kwargs)

        manager = computer_use_skill.BrowserSessionManager()
        with patch.object(computer_use_skill.socket, "getaddrinfo", side_effect=_resolve):
            with self.assertRaises(ValueError):
                asyncio.run(manager._navigate(page, "https://short.example/r", 30))
        self.assertEqual(page.url, "about:blank")


class _Tab:
    """A Playwright page stand-in: its history move lands where the context says."""

    def __init__(self, context: SimpleNamespace, url: str) -> None:
        self.context, self.url = context, url
        context.pages.append(self)

    async def go_back(self, **kwargs) -> None:
        if self.context.in_new_tab:
            _Tab(self.context, self.context.lands_on)
        else:
            self.url = self.context.lands_on

    async def goto(self, url, **kwargs) -> None:
        self.url = url

    async def close(self) -> None:
        self.context.pages.remove(self)

    async def evaluate(self, *args, **kwargs):
        return {"text": "", "interactive": [], "fields": []} if args and "maxElements" in args[0] else ""

    async def title(self) -> str:
        return ""

    async def wait_for_timeout(self, *args) -> None:
        return None


class IsolatedBrowserLandingTests(unittest.TestCase):
    """Where a page lands after any action gets the same URL policy as navigate."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.manager = computer_use_skill.BrowserSessionManager()
        self.run = patch.object(
            self.manager, "_call", side_effect=lambda coroutine, timeout_s=0: asyncio.run(coroutine)
        )
        self.run.start()

    def tearDown(self) -> None:
        self.run.stop()
        self.tempdir.cleanup()

    def _session(self, lands_on: str, *, in_new_tab: bool = False):
        context = SimpleNamespace(pages=[], lands_on=lands_on, in_new_tab=in_new_tab)
        session = computer_use_skill._BrowserSession(
            session_id="browser_t",
            owner_profile_id="alice",
            context=context,
            page=_Tab(context, "https://example.com/start"),
            run_dir=Path(self.tempdir.name),
            task="Check the page",
            start_url="",
        )
        self.manager._sessions[session.session_id] = session
        return session

    def _execute(self, actions: list[dict]) -> dict:
        return self.manager.execute(
            "browser_t", actions, owner_profile_id="alice", confirmed=False, timeout_s=30
        )

    def test_a_history_move_to_a_refused_address_is_blanked_and_stops_the_batch(self) -> None:
        session = self._session("http://127.0.0.1:8000/threads/latest?user_id=default")
        result = self._execute([{"action": "back"}, {"action": "back"}])

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["error"], "navigation_refused")
        self.assertEqual([row["status"] for row in result["action_results"]], ["refused"])
        self.assertIn("127.0.0.1", result["reason"])
        self.assertNotIn("/threads/latest", json.dumps(result))
        self.assertEqual(session.page.url, "about:blank")
        trace = (Path(self.tempdir.name) / "trace.jsonl").read_text(encoding="utf-8")
        self.assertIn("navigation_refused", trace)

    def test_a_new_tab_on_a_refused_address_is_closed(self) -> None:
        session = self._session("http://192.168.1.1/admin", in_new_tab=True)
        result = self._execute([{"action": "back"}])

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(len(session.context.pages), 1)
        self.assertEqual(session.page.url, "https://example.com/start")

    def test_a_late_redirect_is_caught_before_acting_or_observing(self) -> None:
        session = self._session("https://example.com/next")
        session.page.url = "http://169.254.169.254/latest/meta-data"
        result = self._execute([{"action": "back"}])
        self.assertEqual((result["status"], result["action_results"]), ("blocked", []))
        self.assertEqual(session.page.url, "about:blank")

        session.page.url = "http://10.0.0.1/router"
        observation = self.manager.observe("browser_t", owner_profile_id="alice", include_screenshot=False)
        self.assertEqual(observation["url"], "about:blank")
        self.assertIn("10.0.0.1", observation["navigation_refused"])

    def test_computer_use_reports_a_refused_landing_clearly(self) -> None:
        manager = Mock()
        manager.open.return_value = (SimpleNamespace(session_id="browser_t"), False)
        manager.execute.return_value = {"status": "ok", "action_results": [], "requires_confirmation": False}
        manager.observe.return_value = {"url": "about:blank", "navigation_refused": "Stopped: refused"}
        with patch.object(computer_use_skill, "_BROWSER_MANAGER", manager):
            late = computer_use_skill.computer_use(
                "Next page", session_id="browser_t", actions=[{"action": "back"}], dry_run=False
            )
            manager.open.side_effect = computer_use_skill.NavigationRefused("Stopped: redirected")
            start = computer_use_skill.computer_use("Open", start_url="https://93.184.216.34/r", dry_run=False)

        self.assertEqual((late["status"], late["error"]), ("blocked", "navigation_refused"))
        self.assertEqual((start["status"], start["error"], start["summary"]), (
            "blocked", "navigation_refused", "Stopped: redirected",
        ))


class PerTargetSerializationTests(unittest.TestCase):
    def _overlap(self, target, calls) -> int:
        active = {"now": 0, "peak": 0}
        guard = threading.Lock()

        def _slow(**kwargs):
            with guard:
                active["now"] += 1
                active["peak"] = max(active["peak"], active["now"])
            time.sleep(0.1)
            with guard:
                active["now"] -= 1
            return {"status": "ok"}

        barrier = threading.Barrier(len(calls))

        def _call(kwargs):
            barrier.wait()
            with profile_scope("default"):
                target(**kwargs)

        with patch.object(*_slow_patch_target(target), side_effect=_slow):
            threads = [threading.Thread(target=_call, args=(kwargs,)) for kwargs in calls]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        return active["peak"]

    def test_parallel_desktop_and_signed_in_calls_run_one_at_a_time(self) -> None:
        desktop = [dict(task=f"t{index}", environment="desktop", dry_run=False, confirmed=True) for index in range(3)]
        self.assertEqual(self._overlap(computer_use_skill.computer_use, desktop), 1)

    def test_parallel_phone_tasks_on_one_device_run_one_at_a_time(self) -> None:
        grant = {"external_id": "emulator-5554", "label": "Phone"}
        with patch.object(artemis_adapter, "resolve_interaction_target", return_value=grant):
            calls = [dict(task=f"open app {index}", dry_run=False) for index in range(3)]
            self.assertEqual(self._overlap(artemis_adapter.phone_use, calls), 1)
        self.assertIsNot(interaction_targets.operation_lock("android:a"), interaction_targets.operation_lock("android:b"))


def _slow_patch_target(target):
    if target is artemis_adapter.phone_use:
        return artemis_adapter, "_phone_use"
    return computer_use_skill, "_desktop_use"


class ProfileNotificationTests(_FamilyHome):
    def test_mail_triage_notifies_the_profile_whose_mail_was_read(self) -> None:
        inbox = self.root / "inbox"
        read_as: list[str] = []

        def _search(query, max_results=25):
            read_as.append(profile_context.current_profile_id())
            return {"status": "ok", "messages": [{"id": "m1", "from": "Bank <a@hdfcbank.com>", "subject": "Final notice"}]}

        fake_gmail = SimpleNamespace(search_google_mail=_search)
        pushed: list[str | None] = []
        stubs = {"google_workspace_skill": fake_gmail, "karma_log": SimpleNamespace(log_karma=lambda *a, **k: None)}
        with patch.object(vahana, "INBOX_DIR", inbox), \
             patch.object(vahana, "_notify", side_effect=lambda event: pushed.append(event["profile_id"]) or {"queued": False}), \
             patch.dict(sys.modules, stubs):
            with profile_scope("bob"):
                result = asyncio.run(asyncio.to_thread(mail_triage_skill.triage_inbox, 25, True))
            self.assertEqual(result["total"], 1)
            self.assertEqual(read_as, ["bob"])
            self.assertEqual(pushed, ["bob"])  # Bob's private topic, not the owner's
            self.assertEqual(vahana.unread_count("bob"), 1)
            self.assertEqual(vahana.unread_count("default"), 0)
        # No argument lets the model address another profile's inbox.
        self.assertNotIn("user_id", inspect.signature(mail_triage_skill.triage_inbox).parameters)


class ServerStartupTests(unittest.TestCase):
    def test_startup_never_refreshes_or_exports_a_grok_sign_in(self) -> None:
        import server

        import xai_oauth

        forbidden = Mock(side_effect=AssertionError("startup touched xai_oauth"))
        runtime = SimpleNamespace(ensure_server=lambda timeout=None: {"ready": False})
        with patch.object(xai_oauth, "apply_to_env", forbidden), \
             patch.object(xai_oauth, "get_access_token", forbidden), \
             patch("kunji.apply_keys_to_env", return_value=None), \
             patch("local_model_runtime.get_local_model_runtime", return_value=runtime), \
             patch.object(server, "refresh_avatar_models", return_value={}), \
             patch.object(server, "collect_runtime_contract", return_value={}), \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop("XAI_API_KEY", None)
            asyncio.run(server._startup_runtime_contract())
            for thread in threading.enumerate():
                if thread.name == "runtime-contract-warmup":
                    thread.join(timeout=5)
        server.app.state.runtime_contract = None
        forbidden.assert_not_called()
        self.assertNotIn("XAI_API_KEY", os.environ)


class PilotLauncherTests(unittest.TestCase):
    SCRIPT = _r / "Start Family Pilot.command"

    def test_cua_driver_opt_out_reaches_the_daemon(self) -> None:
        text = self.SCRIPT.read_text(encoding="utf-8")
        disable = text.index("cua-driver telemetry disable")
        launch = text.index("open -n -g -a CuaDriver")
        self.assertLess(disable, launch)  # persisted before any daemon starts
        self.assertIn("--env CUA_DRIVER_RS_TELEMETRY_ENABLED=false", text[launch:launch + 200])
        subprocess.run(["bash", "-n", str(self.SCRIPT)], check=True)

    def test_first_run_opens_the_host_address_when_the_owner_has_no_pin(self) -> None:
        text = self.SCRIPT.read_text(encoding="utf-8")
        start = text.index("-c '") + 4
        check = text[start:text.index("'", start)]
        for profiles, expected in (
            ('{"profiles": [{"is_owner": true, "has_pin": false}]}', 0),
            ('{"profiles": [{"is_owner": true, "has_pin": true}]}', 1),
        ):
            result = subprocess.run([sys.executable, "-c", check], input=profiles, text=True)
            self.assertEqual(result.returncode, expected, profiles)
        self.assertIn('OPEN_URL="http://$BACKEND_HOST:$BACKEND_PORT"', text)


if __name__ == "__main__":
    unittest.main()
