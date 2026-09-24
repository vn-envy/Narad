"""
Anumati: hash-bound approvals that a person gives on their phone.

A commit-class side effect (send, pay, book, apply, delete, post, …) runs only
against an ActionProposal its person approved. The proposal stores the exact
surface, action, target and canonical arguments, and ``args_hash`` binds the
approval to them: change one argument and it is a different proposal that
needs its own approval. A model can no longer confirm its own side effects;
``confirmed=True`` or ``dry_run=False`` from a tool call approves nothing.

Flow:
  1. A tool calls ``require(...)``. If an approved, unconsumed, unexpired
     proposal has the same hash, the approval is consumed (single use,
     atomically) and the tool proceeds. Otherwise the tool gets the pending
     proposal (created, or the identical one already waiting) and returns
     ``needs_approval_result(...)``.
  2. A new proposal notifies its person (Vahana, kind ``approval_request``),
     and the chat stream shows an approval card (``approval_requested``).
  3. The person approves, rejects or edits it in the app (``/approvals``).
     Approving runs the stored arguments server-side through the executor its
     surface registered, never through another model call, so what runs is
     exactly what was previewed. The result lands on the proposal, in Karma,
     in the originating chat thread, and as an ``approval_result`` notification.

Storage: one SQLite file (WAL) per profile under ``profile_root()``. A profile
only ever opens its own file, so another profile's proposal id is simply not
found. ``scope`` is reserved for standing envelopes (approving a whole plan
such as "book up to ₹6k"), which are not built yet.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import re
import sqlite3
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from profile_context import current_profile_id, profile_root, profile_scope, validate_profile_id

log = logging.getLogger("narad.anumati")

SURFACES = frozenset({
    "email", "browser", "signed_in_browser", "desktop", "phone", "executor", "workflow",
})
STATUSES = (
    "pending", "approved", "executing", "rejected", "expired", "edited", "executed", "failed",
)
_TERMINAL = frozenset({"rejected", "expired", "edited", "executed", "failed"})
# Surfaces whose proposals the person can rewrite before approving.
EDITABLE_SURFACES = frozenset({"email"})

_DEFAULT_TTL_S = 15 * 60
# A path step waits for its person longer than a live browser page can.
_SURFACE_TTL_S = {"workflow": 24 * 60 * 60}
_PRUNE_AFTER_S = 90 * 24 * 60 * 60
_DB_NAME = "anumati.db"
_ID_RE = re.compile(r"^apr_[0-9a-f]{16}$")
_SESSION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
# Results that mean the approved action went through (a phone task still
# running on the device was dispatched; the driver reports it separately).
_SUCCESS = frozenset({"ok", "sent", "unverified", "running"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    proposal_id     TEXT PRIMARY KEY,
    profile_id      TEXT NOT NULL,
    surface         TEXT NOT NULL,
    action          TEXT NOT NULL,
    target          TEXT NOT NULL,
    args_json       TEXT NOT NULL,
    args_hash       TEXT NOT NULL,
    summary         TEXT NOT NULL,
    risk_class      TEXT NOT NULL,
    preview_json    TEXT NOT NULL DEFAULT '{}',
    scope_json      TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL,
    created_ts      REAL NOT NULL,
    expires_ts      REAL NOT NULL,
    decided_by      TEXT,
    decided_ts      REAL,
    decided_device  TEXT,
    decision_reason TEXT,
    consumed_ts     REAL,
    finished_ts     REAL,
    session_id      TEXT,
    supersedes      TEXT,
    superseded_by   TEXT,
    result_json     TEXT
);
CREATE INDEX IF NOT EXISTS proposals_hash ON proposals(args_hash, status);
CREATE INDEX IF NOT EXISTS proposals_status ON proposals(status, created_ts);
"""

_READY: set[str] = set()
_EXECUTORS: dict[str, Callable[["ActionProposal"], dict[str, Any]]] = {}
_EDITORS: dict[str, Callable[["ActionProposal", dict[str, Any]], dict[str, Any]]] = {}
# Where each surface registers its executor, imported on first use so an
# approval can run in a process that has not loaded the tool yet.
_EXECUTOR_MODULES = {
    "email": "email_skill",
    "browser": "computer_use_skill",
    "signed_in_browser": "computer_use_skill",
    "desktop": "computer_use_skill",
    "phone": "artemis_adapter",
    "workflow": "workflow_engine",
}


class ApprovalError(Exception):
    """Base class for approval refusals."""


class ProposalNotFound(ApprovalError, KeyError):
    """No proposal with this id belongs to this profile."""


class ProposalClosed(ApprovalError):
    """The proposal expired or was already decided."""


def _now() -> float:
    return time.time()


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    """Sorted keys, no whitespace, UTF-8 text: one spelling per value."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _canonical(value: Any) -> Any:
    return json.loads(canonical_json(value))


def compute_hash(surface: str, action: str, target: str, args: Any) -> str:
    """sha256 over surface, action, target and the canonical-JSON arguments."""
    payload = canonical_json({"surface": surface, "action": action, "target": target, "args": args})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class ActionProposal:
    proposal_id: str
    profile_id: str
    surface: str
    action: str
    target: str
    args: Any
    args_hash: str
    summary: str
    risk_class: str
    status: str
    created_ts: float
    expires_ts: float
    preview: dict[str, Any] = field(default_factory=dict)
    scope: dict[str, Any] = field(default_factory=dict)
    decided_by: str | None = None
    decided_ts: float | None = None
    decided_device: str | None = None
    decision_reason: str | None = None
    consumed_ts: float | None = None
    finished_ts: float | None = None
    session_id: str | None = None
    supersedes: str | None = None
    superseded_by: str | None = None
    result: dict[str, Any] | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ActionProposal":
        return cls(
            proposal_id=row["proposal_id"],
            profile_id=row["profile_id"],
            surface=row["surface"],
            action=row["action"],
            target=row["target"],
            args=json.loads(row["args_json"]),
            args_hash=row["args_hash"],
            summary=row["summary"],
            risk_class=row["risk_class"],
            status=row["status"],
            created_ts=row["created_ts"],
            expires_ts=row["expires_ts"],
            preview=json.loads(row["preview_json"] or "{}"),
            scope=json.loads(row["scope_json"] or "{}"),
            decided_by=row["decided_by"],
            decided_ts=row["decided_ts"],
            decided_device=row["decided_device"],
            decision_reason=row["decision_reason"],
            consumed_ts=row["consumed_ts"],
            finished_ts=row["finished_ts"],
            session_id=row["session_id"],
            supersedes=row["supersedes"],
            superseded_by=row["superseded_by"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
        )

    def to_payload(self) -> dict[str, Any]:
        """What the app, the chat stream and notifications see."""
        from risk_policy import commit_label

        now = _now()
        return {
            "id": self.proposal_id,
            "profile_id": self.profile_id,
            "surface": self.surface,
            "action": self.action,
            "target": self.target,
            "args": self.args,
            "args_hash": self.args_hash,
            "summary": self.summary,
            "risk_class": self.risk_class,
            "risk_label": commit_label(self.risk_class),
            "preview": self.preview,
            "status": self.status,
            "created_at": _iso(self.created_ts),
            "expires_at": _iso(self.expires_ts),
            "expires_in_s": max(0, int(self.expires_ts - now)) if self.status in {"pending", "approved"} else 0,
            "decided_by": self.decided_by,
            "decided_at": _iso(self.decided_ts),
            "decided_device": self.decided_device,
            "decision_reason": self.decision_reason,
            "finished_at": _iso(self.finished_ts),
            "session_id": self.session_id,
            "supersedes": self.supersedes,
            "superseded_by": self.superseded_by,
            "result": self.result,
            "editable": self.surface in EDITABLE_SURFACES and self.status == "pending",
        }


@dataclass(frozen=True)
class Gate:
    """``require``'s answer: proceed, wait for the person, or already done."""

    status: str  # approved | needs_approval | already_executed
    proposal: ActionProposal

    @property
    def approved(self) -> bool:
        return self.status == "approved"


# ── Storage ──────────────────────────────────────────────────────────────────


def _db_path(profile_id: str) -> Path:
    return profile_root(profile_id) / _DB_NAME


@contextmanager
def _connect(profile_id: str) -> Iterator[sqlite3.Connection]:
    path = _db_path(validate_profile_id(profile_id))
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists()
    con = sqlite3.connect(path, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA busy_timeout=10000")
        if str(path) not in _READY or fresh:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(_SCHEMA)
            _READY.add(str(path))
            try:
                os.chmod(path, 0o600)  # email bodies and form values live here
            except OSError:
                pass
        yield con
    finally:
        con.close()


@contextmanager
def _transaction(profile_id: str) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE: one writer at a time, so a check-then-update is atomic."""
    with _connect(profile_id) as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            yield con
        except BaseException:
            con.execute("ROLLBACK")
            raise
        con.execute("COMMIT")


def _expire_stale(con: sqlite3.Connection, now: float) -> list[ActionProposal]:
    rows = con.execute(
        "SELECT * FROM proposals WHERE status IN ('pending', 'approved') AND expires_ts <= ?", (now,)
    ).fetchall()
    if rows:
        con.execute(
            "UPDATE proposals SET status = 'expired', finished_ts = ? "
            "WHERE status IN ('pending', 'approved') AND expires_ts <= ?",
            (now, now),
        )
    con.execute(
        "DELETE FROM proposals WHERE status IN ('rejected', 'expired', 'edited', 'executed', 'failed') "
        "AND created_ts < ?",
        (now - _PRUNE_AFTER_S,),
    )
    expired = [ActionProposal.from_row(row) for row in rows]
    for proposal in expired:
        proposal.status = "expired"
    return expired


def _fetch(con: sqlite3.Connection, proposal_id: str) -> ActionProposal | None:
    row = con.execute("SELECT * FROM proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
    return ActionProposal.from_row(row) if row else None


def _owner(profile_id: str | None) -> str:
    return validate_profile_id(profile_id or current_profile_id())


def _clean_surface(surface: str) -> str:
    value = str(surface or "").strip().lower()
    if value not in SURFACES:
        raise ValueError(f"Unknown approval surface {surface!r}")
    return value


def _origin_session_id() -> str | None:
    """The chat thread this tool call belongs to, when it runs inside a turn."""
    agents = sys.modules.get("avatar_agents")
    context = getattr(agents, "_http_session_id_ctx", None)
    value = context.get("") if context is not None else ""
    return value if value and _SESSION_RE.fullmatch(value) else None


def _ttl(surface: str, ttl_s: float | None) -> float:
    if ttl_s is not None:
        return max(30.0, float(ttl_s))
    configured = os.environ.get("NARAD_APPROVAL_TTL_S", "").strip()
    default = float(configured) if configured.replace(".", "", 1).isdigit() else _DEFAULT_TTL_S
    return float(_SURFACE_TTL_S.get(surface, default))


# ── Proposals ────────────────────────────────────────────────────────────────


def propose(
    *,
    surface: str,
    action: str,
    target: str,
    args: Any,
    summary: str,
    risk_class: str,
    preview: dict[str, Any] | None = None,
    session_id: str | None = None,
    profile_id: str | None = None,
    ttl_s: float | None = None,
    scope: dict[str, Any] | None = None,
    supersedes: str | None = None,
    notify: bool = True,
) -> tuple[ActionProposal, bool]:
    """Create a pending proposal, or return the identical one already waiting.

    Returns ``(proposal, created)``. ``summary`` must be built by the tool
    from the actual arguments, never taken from the model.
    """
    owner = _owner(profile_id)
    surface = _clean_surface(surface)
    args = _canonical(args)
    target = str(target or "")[:500]
    digest = compute_hash(surface, str(action), target, args)
    now = _now()
    session = session_id if session_id is not None else _origin_session_id()
    if not session or not _SESSION_RE.fullmatch(session):
        session = None
    with _transaction(owner) as con:
        expired = _expire_stale(con, now)
        row = con.execute(
            "SELECT * FROM proposals WHERE args_hash = ? AND status = 'pending' AND expires_ts > ? "
            "ORDER BY created_ts DESC LIMIT 1",
            (digest, now),
        ).fetchone()
        if row is not None:
            proposal, created = ActionProposal.from_row(row), False
        else:
            proposal = ActionProposal(
                proposal_id=f"apr_{uuid.uuid4().hex[:16]}",
                profile_id=owner,
                surface=surface,
                action=str(action)[:80],
                target=target,
                args=args,
                args_hash=digest,
                summary=" ".join(str(summary or "").split())[:600] or f"{surface} {action}",
                risk_class=str(risk_class or "unclassified")[:40],
                status="pending",
                created_ts=now,
                expires_ts=now + _ttl(surface, ttl_s),
                preview=_canonical(preview or {}),
                scope=_canonical(scope or {}),
                session_id=session,
                supersedes=supersedes,
            )
            con.execute(
                "INSERT INTO proposals (proposal_id, profile_id, surface, action, target, args_json, "
                "args_hash, summary, risk_class, preview_json, scope_json, status, created_ts, expires_ts, "
                "session_id, supersedes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    proposal.proposal_id, owner, surface, proposal.action, target, canonical_json(args),
                    digest, proposal.summary, proposal.risk_class, canonical_json(proposal.preview),
                    canonical_json(proposal.scope), "pending", now, proposal.expires_ts, session, supersedes,
                ),
            )
            created = True
    _log_expired(expired)
    if created:
        _karma(proposal, "approval_requested")
        if notify:
            _notify_request(proposal)
    return proposal, created


def consume(
    *,
    surface: str,
    action: str,
    target: str,
    args: Any,
    profile_id: str | None = None,
) -> ActionProposal | None:
    """Atomically take an approved, unconsumed, unexpired proposal with this hash.

    The caller must then run the action and call ``record_result``.
    """
    owner = _owner(profile_id)
    surface = _clean_surface(surface)
    digest = compute_hash(surface, str(action), str(target or "")[:500], _canonical(args))
    now = _now()
    with _transaction(owner) as con:
        expired = _expire_stale(con, now)
        row = con.execute(
            "SELECT * FROM proposals WHERE args_hash = ? AND status = 'approved' AND consumed_ts IS NULL "
            "AND expires_ts > ? ORDER BY decided_ts LIMIT 1",
            (digest, now),
        ).fetchone()
        proposal = _take(con, row["proposal_id"], now) if row is not None else None
    _log_expired(expired)
    if proposal is not None:
        _karma(proposal, "approval_consumed")
    return proposal


def _take(con: sqlite3.Connection, proposal_id: str, now: float) -> ActionProposal | None:
    cursor = con.execute(
        "UPDATE proposals SET status = 'executing', consumed_ts = ? "
        "WHERE proposal_id = ? AND status = 'approved' AND consumed_ts IS NULL AND expires_ts > ?",
        (now, proposal_id, now),
    )
    return _fetch(con, proposal_id) if cursor.rowcount == 1 else None


def require(
    *,
    surface: str,
    action: str,
    target: str,
    args: Any,
    summary: str,
    risk_class: str,
    preview: dict[str, Any] | None = None,
    profile_id: str | None = None,
    session_id: str | None = None,
    ttl_s: float | None = None,
) -> Gate:
    """The one check a commit-class executor makes before acting.

    Approved and matching: consumed, proceed (then ``record_result``). The same
    action already ran after an approval inside the expiry window: do not run
    it twice. Otherwise: a pending proposal to hand back as ``needs_approval``.
    """
    owner = _owner(profile_id)
    gate = check(surface=surface, action=action, target=target, args=args, profile_id=owner, ttl_s=ttl_s)
    if gate is not None:
        return gate
    proposal, _created = propose(
        surface=surface, action=action, target=target, args=args, summary=summary,
        risk_class=risk_class, preview=preview, profile_id=owner, session_id=session_id, ttl_s=ttl_s,
    )
    return Gate("needs_approval", proposal)


def check(
    *,
    surface: str,
    action: str,
    target: str,
    args: Any,
    profile_id: str | None = None,
    ttl_s: float | None = None,
) -> Gate | None:
    """``require`` without creating a proposal: approved (consumed), already
    executed, or None when the person has not approved this exact action."""
    owner = _owner(profile_id)
    taken = consume(surface=surface, action=action, target=target, args=args, profile_id=owner)
    if taken is not None:
        return Gate("approved", taken)
    surface = _clean_surface(surface)
    digest = compute_hash(surface, str(action), str(target or "")[:500], _canonical(args))
    done = _recently_executed(digest, owner, _ttl(surface, ttl_s))
    return Gate("already_executed", done) if done is not None else None


def _recently_executed(digest: str, owner: str, window_s: float) -> ActionProposal | None:
    with _connect(owner) as con:
        row = con.execute(
            "SELECT * FROM proposals WHERE args_hash = ? AND status IN ('executing', 'executed') "
            "AND consumed_ts > ? ORDER BY consumed_ts DESC LIMIT 1",
            (digest, _now() - window_s),
        ).fetchone()
    return ActionProposal.from_row(row) if row else None


def get(proposal_id: str, *, profile_id: str | None = None) -> ActionProposal:
    owner = _owner(profile_id)
    if not _ID_RE.fullmatch(str(proposal_id or "")):
        raise ProposalNotFound(proposal_id)
    with _transaction(owner) as con:
        expired = _expire_stale(con, _now())
        proposal = _fetch(con, proposal_id)
    _log_expired(expired)
    if proposal is None:
        raise ProposalNotFound(proposal_id)
    return proposal


def list_proposals(
    *, profile_id: str | None = None, status: str | None = None, limit: int = 50
) -> list[ActionProposal]:
    owner = _owner(profile_id)
    wanted = [item.strip() for item in str(status or "").split(",") if item.strip()]
    if any(item not in STATUSES for item in wanted):
        raise ValueError(f"status must be one of: {', '.join(STATUSES)}")
    with _transaction(owner) as con:
        expired = _expire_stale(con, _now())
        query = "SELECT * FROM proposals"
        params: list[Any] = []
        if wanted:
            query += f" WHERE status IN ({', '.join('?' for _ in wanted)})"
            params.extend(wanted)
        query += " ORDER BY created_ts DESC LIMIT ?"
        params.append(max(1, min(int(limit), 200)))
        rows = con.execute(query, params).fetchall()
    _log_expired(expired)
    return [ActionProposal.from_row(row) for row in rows]


# ── Decisions ────────────────────────────────────────────────────────────────


def _decide(
    proposal_id: str,
    status: str,
    *,
    profile_id: str | None,
    decided_by: str,
    device: str = "",
    reason: str = "",
) -> ActionProposal:
    owner = _owner(profile_id)
    if not _ID_RE.fullmatch(str(proposal_id or "")):
        raise ProposalNotFound(proposal_id)
    now = _now()
    with _transaction(owner) as con:
        expired = _expire_stale(con, now)
        proposal = _fetch(con, proposal_id)
        if proposal is not None and proposal.status == "pending":
            con.execute(
                "UPDATE proposals SET status = ?, decided_by = ?, decided_ts = ?, decided_device = ?, "
                "decision_reason = ? WHERE proposal_id = ? AND status = 'pending'",
                (status, decided_by[:80], now, device[:160] or None, reason[:500] or None, proposal_id),
            )
            proposal = _fetch(con, proposal_id)
            changed = True
        else:
            changed = False
    _log_expired(expired)
    if proposal is None:
        raise ProposalNotFound(proposal_id)
    if not changed:
        raise ProposalClosed(
            "This approval has expired." if proposal.status == "expired"
            else f"This approval was already {proposal.status}."
        )
    _karma(proposal, f"approval_{status}", verdict=status, decided_by=decided_by, device=device[:160])
    return proposal


def approve(
    proposal_id: str, *, profile_id: str | None = None, decided_by: str, device: str = ""
) -> ActionProposal:
    """Record the person's approval. ``execute_approved`` then runs it."""
    return _decide(proposal_id, "approved", profile_id=profile_id, decided_by=decided_by, device=device)


def reject(
    proposal_id: str,
    *,
    profile_id: str | None = None,
    decided_by: str,
    device: str = "",
    reason: str = "",
) -> ActionProposal:
    proposal = _decide(
        proposal_id, "rejected", profile_id=profile_id, decided_by=decided_by, device=device, reason=reason
    )
    _announce(proposal)
    return proposal


def edit(
    proposal_id: str,
    changes: dict[str, Any],
    *,
    profile_id: str | None = None,
    decided_by: str,
    device: str = "",
) -> ActionProposal:
    """Replace a pending proposal with the person's edited version.

    The surface's editor rebuilds action, target, arguments, summary and
    preview from the changes; the new proposal has its own hash and needs its
    own approval, and the old one is marked ``edited``.
    """
    owner = _owner(profile_id)
    old = get(proposal_id, profile_id=owner)
    editor = _editor_for(old.surface)
    if editor is None:
        raise ValueError("This kind of approval cannot be edited; reject it and ask for a new one.")
    if old.status != "pending":
        raise ProposalClosed(
            "This approval has expired." if old.status == "expired" else f"This approval was already {old.status}."
        )
    spec = editor(old, dict(changes or {}))
    new_hash = compute_hash(old.surface, str(spec["action"]), str(spec["target"])[:500], _canonical(spec["args"]))
    if new_hash == old.args_hash:
        return old
    new, _created = propose(
        surface=old.surface,
        action=spec["action"],
        target=spec["target"],
        args=spec["args"],
        summary=spec["summary"],
        risk_class=spec.get("risk_class") or old.risk_class,
        preview=spec.get("preview") or {},
        session_id=old.session_id,
        profile_id=owner,
        supersedes=old.proposal_id,
        notify=False,  # the person is looking at it
    )
    now = _now()
    with _transaction(owner) as con:
        cursor = con.execute(
            "UPDATE proposals SET status = 'edited', superseded_by = ?, decided_by = ?, decided_ts = ?, "
            "decided_device = ?, finished_ts = ? WHERE proposal_id = ? AND status = 'pending'",
            (new.proposal_id, decided_by[:80], now, device[:160] or None, now, old.proposal_id),
        )
        if cursor.rowcount != 1:
            con.execute(
                "UPDATE proposals SET status = 'expired', finished_ts = ? WHERE proposal_id = ?",
                (now, new.proposal_id),
            )
            raise ProposalClosed("This approval changed while it was being edited.")
    old.status, old.superseded_by = "edited", new.proposal_id
    _karma(old, "approval_edited", verdict="edited", new_hash=new.args_hash, superseded_by=new.proposal_id)
    return new


def supersede(proposal_id: str, *, profile_id: str | None = None, reason: str = "") -> None:
    """Retire a pending proposal that a newer preview replaced (never an approved one)."""
    owner = _owner(profile_id)
    if not _ID_RE.fullmatch(str(proposal_id or "")):
        return
    with _transaction(owner) as con:
        con.execute(
            "UPDATE proposals SET status = 'expired', finished_ts = ?, decision_reason = ? "
            "WHERE proposal_id = ? AND status = 'pending'",
            (_now(), reason[:500] or "Replaced by a newer preview", proposal_id),
        )


# ── Execution ────────────────────────────────────────────────────────────────


def register_executor(surface: str, executor: Callable[[ActionProposal], dict[str, Any]]) -> None:
    """Run an approved proposal of ``surface`` from its stored arguments.

    The executor gets the consumed proposal and returns a result dict with a
    ``status`` ("ok" on success) and a plain ``summary``.
    """
    _EXECUTORS[_clean_surface(surface)] = executor


def register_editor(
    surface: str, editor: Callable[[ActionProposal, dict[str, Any]], dict[str, Any]]
) -> None:
    """Rebuild a proposal from the person's edits: {action, target, args, summary, preview}."""
    _EDITORS[_clean_surface(surface)] = editor


def _registered(table: dict[str, Any], surface: str) -> Any:
    if surface not in table and surface in _EXECUTOR_MODULES:
        try:
            importlib.import_module(_EXECUTOR_MODULES[surface])
        except Exception as exc:
            log.warning("Anumati: %s executor could not load: %s", surface, exc)
    return table.get(surface)


def _executor_for(surface: str) -> Callable[[ActionProposal], dict[str, Any]] | None:
    return _registered(_EXECUTORS, surface)


def _editor_for(surface: str) -> Callable[[ActionProposal, dict[str, Any]], dict[str, Any]] | None:
    return _registered(_EDITORS, surface)


def execute_approved(proposal_id: str, *, profile_id: str | None = None) -> ActionProposal:
    """Run an approved proposal through its surface's executor, exactly once.

    Without a registered executor the approval stays waiting for the tool to
    call ``require`` again with the same arguments.
    """
    owner = _owner(profile_id)
    proposal = get(proposal_id, profile_id=owner)
    if proposal.status != "approved" or _executor_for(proposal.surface) is None:
        return proposal
    with _transaction(owner) as con:
        taken = _take(con, proposal_id, _now())
    if taken is None:
        return get(proposal_id, profile_id=owner)
    _karma(taken, "approval_consumed")
    executor = _executor_for(taken.surface)
    try:
        with profile_scope(owner):
            result = executor(taken) if executor else {"status": "error", "summary": "No executor"}
    except Exception as exc:
        log.exception("Anumati: executor for %s failed", taken.surface)
        result = {"status": "error", "summary": f"{type(exc).__name__}: {exc}"[:500]}
    return record_result(taken.proposal_id, result, profile_id=owner)


def record_result(
    proposal_id: str, result: dict[str, Any], *, profile_id: str | None = None
) -> ActionProposal:
    """Store what a consumed proposal did; executed on success, failed otherwise."""
    owner = _owner(profile_id)
    clean = _result_record(result)
    status = "executed" if clean.get("status") in _SUCCESS else "failed"
    now = _now()
    with _transaction(owner) as con:
        cursor = con.execute(
            "UPDATE proposals SET status = ?, result_json = ?, finished_ts = ? "
            "WHERE proposal_id = ? AND status = 'executing'",
            (status, canonical_json(clean), now, proposal_id),
        )
        proposal = _fetch(con, proposal_id)
    if proposal is None:
        raise ProposalNotFound(proposal_id)
    if cursor.rowcount != 1:
        return proposal  # already recorded; a result is written once
    _karma(proposal, f"approval_{proposal.status}", result_status=clean.get("status"))
    _announce(proposal)
    return proposal


def _result_record(result: Any) -> dict[str, Any]:
    """The part of a tool result worth keeping: status, summary, a few fields."""
    if not isinstance(result, dict):
        return {"status": "ok" if result else "error", "summary": str(result)[:500]}
    kept: dict[str, Any] = {
        "status": str(result.get("status") or "error"),
        "summary": str(result.get("summary") or result.get("message") or "")[:1000],
    }
    for key in ("error", "message_id", "provider", "url", "task_id", "run_status", "screenshot_url"):
        if result.get(key) not in (None, ""):
            kept[key] = str(result[key])[:500]
    actions = result.get("action_results")
    if isinstance(actions, list):
        kept["action_results"] = [
            {key: row.get(key) for key in ("action", "status", "error", "effect_state") if key in row}
            for row in actions[:24]
            if isinstance(row, dict)
        ]
    return kept


def waiting_message(proposal: ActionProposal) -> str:
    """What the model reads when a tool is waiting for the person's approval."""
    return (
        f"Waiting for approval: {proposal.summary}. Nothing has happened yet. An approval card is on "
        "the person's screen and phone; tell them in one sentence that it is waiting for their OK in "
        "Narad. Do not ask them to type yes, and do not call this tool again for the same action."
    )


def needs_approval_result(proposal: ActionProposal, **extra: Any) -> dict[str, Any]:
    """The structured ``needs_approval`` answer a tool returns to the model."""
    from tool_result import envelope

    return envelope(
        status="needs_approval",
        summary=waiting_message(proposal),
        requires_confirmation=True,
        approval=proposal.to_payload(),
        proposal_id=proposal.proposal_id,
        **extra,
    )


def already_executed_result(proposal: ActionProposal, **extra: Any) -> dict[str, Any]:
    from tool_result import envelope

    if proposal.status == "executing":
        message = f"This exact action is already running after the person approved it: {proposal.summary}."
    else:
        outcome = (proposal.result or {}).get("summary") or "it went through"
        message = f"This exact action already ran after the person approved it: {proposal.summary}. Result: {outcome}."
    return envelope(
        status="already_done",
        summary=f"{message} Do not repeat it.",
        requires_confirmation=False,
        approval=proposal.to_payload(),
        proposal_id=proposal.proposal_id,
        **extra,
    )


# ── Side channels: Karma, notifications, the chat thread ─────────────────────


def _karma(proposal: ActionProposal, action: str, **metadata: Any) -> None:
    try:
        from karma_log import log_karma

        with profile_scope(proposal.profile_id):
            log_karma(
                action,
                proposal.proposal_id,
                "Anumati",
                f"{proposal.surface}.{proposal.action}: {proposal.summary}",
                triggered_by=proposal.session_id,
                entity_type="approval",
                policy="anumati",
                metadata={
                    "args_hash": proposal.args_hash,
                    "surface": proposal.surface,
                    "action": proposal.action,
                    "risk_class": proposal.risk_class,
                    "status": proposal.status,
                    **{key: value for key, value in metadata.items() if value not in (None, "")},
                },
            )
    except Exception as exc:
        log.warning("Anumati: Karma entry failed: %s", exc)


def _log_expired(expired: list[ActionProposal]) -> None:
    for proposal in expired:
        _karma(proposal, "approval_expired", verdict="expired")


def _vahana_deliver(**kwargs: Any) -> dict[str, Any]:
    from vahana import deliver

    return deliver(**kwargs)


def _notify_request(proposal: ActionProposal) -> None:
    from risk_policy import commit_label

    try:
        _vahana_deliver(
            user_id=proposal.profile_id,
            kind="approval_request",
            title=f"Needs your OK: {commit_label(proposal.risk_class)}",
            body=proposal.summary,
            data={
                "proposal_id": proposal.proposal_id,
                "url": f"/?approval={proposal.proposal_id}",
                "surface": proposal.surface,
                "risk_class": proposal.risk_class,
                "expires_at": _iso(proposal.expires_ts),
            },
            priority="high",
            source="anumati",
        )
    except Exception as exc:
        log.warning("Anumati: approval request notification failed: %s", exc)


_OUTCOME_WORDS = {
    "executed": ("Done", "Approved and done"),
    "failed": ("Did not go through", "Approved, but it did not go through"),
    "rejected": ("Declined", "You declined"),
}


def _announce(proposal: ActionProposal) -> None:
    """Tell the person and the chat thread how a decided proposal ended."""
    from risk_policy import commit_label

    title_word, note_lead = _OUTCOME_WORDS.get(proposal.status, (proposal.status, proposal.status))
    outcome = str((proposal.result or {}).get("summary") or "").strip()
    if proposal.status == "rejected" and proposal.decision_reason:
        outcome = f"Reason: {proposal.decision_reason}"
    body = f"{proposal.summary}. {outcome}".strip()
    try:
        _vahana_deliver(
            user_id=proposal.profile_id,
            kind="approval_result",
            title=f"{commit_label(proposal.risk_class)}: {title_word}",
            body=body,
            data={
                "proposal_id": proposal.proposal_id,
                "url": f"/?approval={proposal.proposal_id}",
                "status": proposal.status,
            },
            priority="default",
            source="anumati",
        )
    except Exception as exc:
        log.warning("Anumati: approval result notification failed: %s", exc)
    _thread_note(proposal, f"{note_lead}: {body}")


def _thread_note(proposal: ActionProposal, text: str) -> None:
    session_id = proposal.session_id
    if not session_id or not _SESSION_RE.fullmatch(session_id):
        return
    try:
        from conversation_memory import append_turn, load_thread

        if not load_thread(proposal.profile_id, session_id, limit=1):
            return  # the thread is gone or never persisted; nothing to note in
        append_turn(
            user_id=proposal.profile_id,
            session_id=session_id,
            role="assistant",
            text=f"[Approval] {text}",
            metadata={"kind": "approval_result", "proposal_id": proposal.proposal_id, "status": proposal.status},
        )
    except Exception as exc:
        log.warning("Anumati: chat thread note failed: %s", exc)
