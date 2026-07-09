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


# ── Registry ──────────────────────────────────────────────────────────────────

ADAPTERS: dict[str, ClaudeAgentSDKAdapter] = {
    ClaudeAgentSDKAdapter.name: ClaudeAgentSDKAdapter(),
}


def get_adapter(name: str) -> ClaudeAgentSDKAdapter | None:
    return ADAPTERS.get(name)


def subscription_active() -> bool:
    """True when any subscription adapter can actually serve completions."""
    return any(adapter.available() for adapter in ADAPTERS.values())


def subscriptions_payload() -> list[dict[str, Any]]:
    """Status card per adapter for Settings → Connections."""
    return [adapter.status().to_dict() for adapter in ADAPTERS.values()]
