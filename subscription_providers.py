"""
Subscription providers (S3) — plan credits instead of API keys.

Since 2026-06-15, Claude Pro/Max/Team/Enterprise plans include a monthly
Agent SDK credit that explicitly covers third-party apps built on the Claude
Agent SDK. Narad's "Sign in with Claude" (T3 Sadasya) is therefore an Agent
SDK provider adapter drawing on plan credits — ToS-compliant, never raw
consumer OAuth token reuse (banned).

Registry pattern: one adapter file per vendor program. If/when OpenAI or
Google open an equivalent, they slot in as another `SubscriptionAdapter`.
No sanctioned ChatGPT path exists today → OpenAI stays BYO-key (Kunji).

Adapters are honest about their state:
  * `installed`  — SDK importable in this environment
  * `signed_in`  — auth material present (Claude Code CLI login) or the
                   NARAD_CLAUDE_SUBSCRIPTION env flag set (pre-adapter escape
                   hatch, kept for compatibility with S1)
  * `available`  — both, i.e. completions would actually draw plan credits

Cost ledger: models routed here use the `narad-claude-sdk/` prefix, pinned
$0 marginal (plan fee already paid); record with source="subscription".
"""
from __future__ import annotations

import importlib.util
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

MODEL_PREFIX = "narad-claude-sdk/"


@dataclass
class SubscriptionStatus:
    provider: str
    label: str
    installed: bool
    signed_in: bool
    available: bool
    detail: str
    plan: str | None = None
    remaining_credit: float | None = None  # SDK does not expose this yet — stays None, never guessed
    models: list[str] = field(default_factory=list)
    disabled_by_policy: bool = False  # owner policy: never routable, whatever is stored

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ClaudeAgentSDKAdapter:
    """Claude plan credits via the Claude Agent SDK."""

    name = "claude-agent-sdk"
    label = "Claude subscription (Agent SDK credits)"
    model_prefix = MODEL_PREFIX
    default_models = [
        f"{MODEL_PREFIX}claude-sonnet-4-6",
        f"{MODEL_PREFIX}claude-haiku-4-5",
    ]

    @staticmethod
    def _sdk_installed() -> bool:
        try:
            return importlib.util.find_spec("claude_agent_sdk") is not None
        except Exception:
            return False

    @staticmethod
    def _cli_auth_present() -> bool:
        """Best-effort: Claude Code CLI login leaves credentials under ~/.claude."""
        try:
            claude_home = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
            return any(
                (claude_home / name).exists()
                for name in (".credentials.json", "credentials.json")
            )
        except Exception:
            return False

    def signed_in(self) -> bool:
        if os.environ.get("NARAD_CLAUDE_SUBSCRIPTION", "").strip():
            return True  # explicit user intent — the S1-era escape hatch
        return self._cli_auth_present()

    def available(self) -> bool:
        return self._sdk_installed() and self.signed_in()

    def status(self) -> SubscriptionStatus:
        installed = self._sdk_installed()
        signed_in = self.signed_in()
        if installed and signed_in:
            detail = "ready — completions draw on your Claude plan's Agent SDK credit"
        elif installed:
            detail = "SDK installed but not signed in — run `claude login` or use Sign in with Claude"
        elif signed_in:
            detail = "signed in, but the claude-agent-sdk package is not installed in this runtime"
        else:
            detail = "not set up — install the Claude Agent SDK and sign in with your Claude plan"
        return SubscriptionStatus(
            provider=self.name,
            label=self.label,
            installed=installed,
            signed_in=signed_in,
            available=installed and signed_in,
            detail=detail,
            models=self.default_models if installed and signed_in else [],
        )

    def completion(self, model: str, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        """Synchronous completion drawing plan credits. Raises RuntimeError when unavailable.

        Routing integration (per-avatar tables) lands with S2; until then this is
        the callable surface the runtime will consume.
        """
        if not self.available():
            raise RuntimeError(
                "Claude subscription adapter unavailable — "
                + self.status().detail
            )
        import anyio
        from claude_agent_sdk import ClaudeAgentOptions, query  # type: ignore

        bare_model = model.removeprefix(MODEL_PREFIX)
        prompt = "\n\n".join(
            f"[{m.get('role', 'user')}] {m.get('content', '')}" for m in messages
        )

        async def _run() -> str:
            chunks: list[str] = []
            async for message in query(
                prompt=prompt,
                options=ClaudeAgentOptions(model=bare_model, max_turns=1),
            ):
                for block in getattr(message, "content", []) or []:
                    text = getattr(block, "text", None)
                    if text:
                        chunks.append(text)
            return "".join(chunks)

        return anyio.from_thread.run(_run) if kwargs.get("_in_thread") else anyio.run(_run)


class XaiOAuthAdapter:
    """Grok via xAI OAuth — disabled by owner policy (2026-09-23).

    xAI/Grok is out of every routing chain, so this adapter is never
    `available` and never offers a sign-in. It remains only to report a
    stored Grok credential (OAuth token or XAI_API_KEY) so Settings can show
    it as disabled and disconnect it. Removing it together with xai_oauth.py
    and the /connections/xai routes is a Phase 3 cleanup.
    """

    name = "xai-oauth"
    label = "Grok (xAI)"
    model_prefix = "xai/"
    default_models: list[str] = []

    @staticmethod
    def _oauth_signed_in() -> bool:
        try:
            import xai_oauth
            return xai_oauth.signed_in()
        except Exception:
            return False

    def signed_in(self) -> bool:
        if os.environ.get("XAI_API_KEY", "").strip():
            return True  # .env escape hatch — a real key always counts
        return self._oauth_signed_in()

    def available(self) -> bool:
        return False  # owner policy: a stored credential never makes xAI routable

    def status(self) -> SubscriptionStatus:
        signed_in = self.signed_in()
        if self._oauth_signed_in():
            detail = (
                "disabled by owner policy — Narad no longer uses Grok; "
                "disconnect to remove the stored sign-in"
            )
        elif signed_in:
            detail = "disabled by owner policy — XAI_API_KEY is set but ignored; remove it from .env"
        else:
            detail = "disabled by owner policy — Narad does not use Grok"
        return SubscriptionStatus(
            provider=self.name,
            label=self.label,
            installed=True,  # no local runtime needed — pure HTTPS
            signed_in=signed_in,
            available=False,
            detail=detail,
            disabled_by_policy=True,
        )


# ── Registry ──────────────────────────────────────────────────────────────────

ADAPTERS: dict[str, Any] = {
    ClaudeAgentSDKAdapter.name: ClaudeAgentSDKAdapter(),
    XaiOAuthAdapter.name: XaiOAuthAdapter(),
}


def get_adapter(name: str) -> Any | None:
    return ADAPTERS.get(name)


def subscription_active(name: str | None = None) -> bool:
    """True when a subscription adapter can actually serve completions.

    Pass a name to check one provider — routing checks MUST do this, so a
    Grok sign-in never falsely enables narad-claude-sdk models (or vice versa).
    """
    if name is not None:
        adapter = ADAPTERS.get(name)
        return bool(adapter and adapter.available())
    return any(adapter.available() for adapter in ADAPTERS.values())


def subscriptions_payload() -> list[dict[str, Any]]:
    """Status card per adapter for Settings → Connections."""
    return [adapter.status().to_dict() for adapter in ADAPTERS.values()]
