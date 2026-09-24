"""Cloudflare Access JWT verification in front of Narad's own profile auth."""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import cf_access
import jwt
import server
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm
from starlette.requests import Request

import family_profiles
import profile_context

_TEAM = "myteam.cloudflareaccess.com"
_ISSUER = f"https://{_TEAM}"
_AUD = "4714c1358e65fe4b408ad6d432a5f878f08194bdb4752441fd56faefa9b2b6f2"
_TUNNEL = {"CF-Connecting-IP": "203.0.113.7", "X-Forwarded-For": "203.0.113.7"}
_DENIED = {"detail": "Cloudflare Access required"}


def _rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


_SIGNING_KEY = _rsa_key()
_ROTATED_KEY = _rsa_key()
_FORGED_KEY = _rsa_key()


def _jwk(private_key: rsa.RSAPrivateKey, kid: str) -> dict:
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    return {**jwk, "kid": kid, "alg": "RS256", "use": "sig"}


def _token(private_key=_SIGNING_KEY, kid: str = "current", **overrides) -> str:
    now = int(time.time())
    claims = {
        "aud": [_AUD],
        "email": "asha@example.com",
        "exp": now + 3600,
        "iat": now,
        "nbf": now,
        "iss": _ISSUER,
        "type": "app",
        "sub": "7335d417-61da-459d-899c-0a01c76a2f94",
        **overrides,
    }
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


def _access(token: str) -> dict[str, str]:
    return {**_TUNNEL, "Cf-Access-Jwt-Assertion": token}


class CloudflareAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.jwks = {"keys": [_jwk(_SIGNING_KEY, "current")], "public_certs": []}
        self.fetches: list[str] = []
        self.patches = [
            patch.object(server, "_AUTH_MODE", "strict"),
            patch.object(server, "_login_failures", {}),
            patch.object(server, "_CF_ACCESS", cf_access.AccessConfig(_TEAM, (_AUD,))),
            patch.object(cf_access, "_cache", cf_access._KeyCache()),
            patch.object(cf_access, "fetch_jwks", self._fetch_jwks),
            patch.object(family_profiles, "FAMILY_PROFILES_PATH", self.root / "family_profiles.json"),
            patch.object(family_profiles, "PROFILE_SESSION_SECRET_PATH", self.root / "profile_secret"),
            patch.object(profile_context, "PROFILES_DIR", self.root / "profiles"),
        ]
        for active_patch in self.patches:
            active_patch.start()
        self.client = TestClient(server.app)
        family_profiles.update_profile("default", pin="8642")

    def tearDown(self) -> None:
        for active_patch in reversed(self.patches):
            active_patch.stop()
        self.tempdir.cleanup()

    def _fetch_jwks(self, url: str) -> dict:
        self.fetches.append(url)
        return json.loads(json.dumps(self.jwks))

    def assertDenied(self, response) -> None:
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json(), _DENIED)

    # ── Tunnel requests need a valid Access JWT ─────────────────────────────

    def test_valid_token_reaches_narads_own_profile_auth(self) -> None:
        token = _token()
        profiles = self.client.get("/profiles", headers=_access(token))
        self.assertEqual(profiles.status_code, 200, profiles.text)
        self.assertEqual(self.fetches, [f"{_ISSUER}/cdn-cgi/access/certs"])
        # Access alone is not a Narad session: profile auth still applies.
        self.assertEqual(self.client.post("/profiles/invites", headers=_access(token)).status_code, 401)

        login = self.client.post(
            "/profiles/login", json={"user_id": "default", "pin": "8642"}, headers=_access(token)
        )
        self.assertEqual(login.status_code, 200, login.text)
        owner = {**_access(token), "Authorization": f"Bearer {login.json()['token']}"}
        self.assertEqual(self.client.post("/profiles/invites", headers=owner).status_code, 201)
        self.assertEqual(len(self.fetches), 1)  # keys are cached in-process

    def test_cookie_is_accepted_when_the_header_is_absent(self) -> None:
        response = self.client.get(
            "/profiles", headers={**_TUNNEL, "Cookie": f"CF_Authorization={_token()}"}
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_verified_email_is_put_on_request_state(self) -> None:
        seen: dict[str, str] = {}

        async def call_next(request: Request):
            seen["email"] = request.state.access_email
            return PlainTextResponse("ok")

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/profiles",
            "query_string": b"",
            "headers": [
                (b"cf-access-jwt-assertion", _token().encode()),
                (b"cf-connecting-ip", b"203.0.113.7"),
            ],
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 8000),
            "scheme": "http",
        }
        response = asyncio.run(server._cloudflare_access(Request(scope), call_next))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen["email"], "asha@example.com")

    def test_missing_token_is_forbidden_even_for_the_app_shell(self) -> None:
        self.assertDenied(self.client.get("/profiles", headers=_TUNNEL))
        self.assertDenied(self.client.get("/", headers=_TUNNEL))
        self.assertDenied(self.client.get("/manifest.webmanifest", headers=_TUNNEL))
        self.assertDenied(self.client.get("/profiles", headers={**_TUNNEL, "Cf-Access-Jwt-Assertion": "junk"}))

    def test_wrong_audience_is_forbidden(self) -> None:
        self.assertDenied(self.client.get("/profiles", headers=_access(_token(aud=["another-app"]))))

    def test_any_configured_audience_is_accepted(self) -> None:
        with patch.object(server, "_CF_ACCESS", cf_access.AccessConfig(_TEAM, ("old-app", _AUD))):
            response = self.client.get("/profiles", headers=_access(_token(aud=_AUD)))
        self.assertEqual(response.status_code, 200, response.text)

    def test_wrong_issuer_is_forbidden(self) -> None:
        for issuer in ("https://evil.cloudflareaccess.com", "https://myteam", _TEAM):
            with self.subTest(issuer=issuer):
                self.assertDenied(self.client.get("/profiles", headers=_access(_token(iss=issuer))))

    def test_expired_or_not_yet_valid_token_is_forbidden(self) -> None:
        now = int(time.time())
        self.assertDenied(self.client.get("/profiles", headers=_access(_token(exp=now - 3600))))
        self.assertDenied(self.client.get("/profiles", headers=_access(_token(nbf=now + 3600))))
        claims = jwt.decode(_token(), options={"verify_signature": False})
        del claims["exp"]
        no_exp = jwt.encode(claims, _SIGNING_KEY, algorithm="RS256", headers={"kid": "current"})
        self.assertDenied(self.client.get("/profiles", headers=_access(no_exp)))

    def test_signature_from_another_key_is_forbidden(self) -> None:
        self.assertDenied(self.client.get("/profiles", headers=_access(_token(_FORGED_KEY))))

    # ── Signing keys: cache, rotation, outages ──────────────────────────────

    def test_unknown_kid_triggers_one_refetch(self) -> None:
        with patch.object(cf_access, "_REFETCH_MIN_S", 0.0):
            self.assertEqual(self.client.get("/profiles", headers=_access(_token())).status_code, 200)
            self.assertEqual(len(self.fetches), 1)

            self.jwks["keys"].append(_jwk(_ROTATED_KEY, "rotated"))
            rotated = _access(_token(_ROTATED_KEY, kid="rotated"))
            self.assertEqual(self.client.get("/profiles", headers=rotated).status_code, 200)
            self.assertEqual(len(self.fetches), 2)
            self.assertEqual(self.client.get("/profiles", headers=rotated).status_code, 200)
            self.assertEqual(len(self.fetches), 2)

            self.assertDenied(self.client.get("/profiles", headers=_access(_token(kid="missing"))))
            self.assertEqual(len(self.fetches), 3)  # one refetch, then give up

    def test_unknown_kid_refetches_are_throttled(self) -> None:
        self.assertEqual(self.client.get("/profiles", headers=_access(_token())).status_code, 200)
        for _ in range(3):
            self.assertDenied(self.client.get("/profiles", headers=_access(_token(kid="made-up"))))
        self.assertEqual(len(self.fetches), 1)

    def test_last_good_keys_survive_a_certs_outage(self) -> None:
        self.assertEqual(self.client.get("/profiles", headers=_access(_token())).status_code, 200)

        attempts: list[str] = []

        def _unreachable(url: str) -> dict:
            attempts.append(url)
            raise OSError("network unreachable")

        with (
            patch.object(cf_access, "_JWKS_TTL_S", 0.0),
            patch.object(cf_access, "_REFETCH_MIN_S", 0.0),
            patch.object(cf_access, "fetch_jwks", _unreachable),
        ):
            self.assertEqual(self.client.get("/profiles", headers=_access(_token())).status_code, 200)
        self.assertEqual(len(attempts), 1)

    def test_no_keys_at_all_fails_closed(self) -> None:
        def _unreachable(url: str) -> dict:
            raise OSError("network unreachable")

        with patch.object(cf_access, "fetch_jwks", _unreachable):
            self.assertDenied(self.client.get("/profiles", headers=_access(_token())))

    # ── Exemptions and the unconfigured default ─────────────────────────────

    def test_direct_local_request_needs_no_token(self) -> None:
        self.assertEqual(self.client.get("/profiles").status_code, 200)
        self.assertEqual(self.client.post("/profiles/invites").status_code, 401)
        self.assertEqual(self.fetches, [])

    def test_get_health_is_the_only_exempt_route(self) -> None:
        async def _contract() -> dict:
            return {}

        with (
            patch.object(server, "_runtime_contract_snapshot", _contract),
            patch.object(server, "health_payload", lambda contract: {"status": "healthy"}),
        ):
            self.assertEqual(self.client.get("/health", headers=_TUNNEL).status_code, 200)
            self.assertDenied(self.client.post("/health", headers=_TUNNEL))
            self.assertDenied(self.client.get("/health/", headers=_TUNNEL))

    def test_cors_preflight_is_not_blocked(self) -> None:
        response = self.client.options(
            "/profiles",
            headers={
                **_TUNNEL,
                "Origin": "http://localhost:5174",
                "Access-Control-Request-Method": "GET",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_unset_env_changes_nothing(self) -> None:
        self.assertIsNone(cf_access.load_config({}))
        self.assertIsNone(cf_access.load_config({"NARAD_CF_ACCESS_TEAM_DOMAIN": _TEAM}))
        self.assertIsNone(cf_access.load_config({"NARAD_CF_ACCESS_AUD": _AUD}))
        with patch.object(server, "_CF_ACCESS", cf_access.load_config({})):
            self.assertEqual(self.client.get("/profiles", headers=_TUNNEL).status_code, 200)
            self.assertEqual(self.client.post("/profiles/invites", headers=_TUNNEL).status_code, 401)
        self.assertEqual(self.fetches, [])

    def test_config_accepts_a_url_and_several_audiences(self) -> None:
        config = cf_access.load_config({
            "NARAD_CF_ACCESS_TEAM_DOMAIN": " https://MyTeam.cloudflareaccess.com/ ",
            "NARAD_CF_ACCESS_AUD": f"{_AUD}, second-aud ,",
        })
        self.assertEqual(config, cf_access.AccessConfig(_TEAM, (_AUD, "second-aud")))
        self.assertEqual(config.issuer, _ISSUER)
        self.assertEqual(config.certs_url, f"{_ISSUER}/cdn-cgi/access/certs")


if __name__ == "__main__":
    unittest.main()
