#!/usr/bin/env python3
"""Encrypted, restorable backups of NARAD_HOME (~/.narad).

    .venv/bin/python scripts/narad_backup.py backup             # encrypt a snapshot now
    .venv/bin/python scripts/narad_backup.py list               # archives, newest first
    .venv/bin/python scripts/narad_backup.py restore --to DIR   # newest, or --archive NAME
    .venv/bin/python scripts/narad_backup.py drill              # restore the newest to a temp dir and verify it

launchd runs `backup` nightly (com.narad.backup) and `drill` weekly
(com.narad.drill); see scripts/install_launchd.sh.

Archive   NARAD_BACKUP_DIR (default ~/NaradBackups)/narad-YYYYMMDDTHHMMSSZ.nbk: a
          gzip'd tar of NARAD_HOME encrypted with AES-256-GCM in 1 MiB chunks, so
          memory stays small. Every chunk is authenticated and bound to its
          position and to the header; an altered, reordered, missing or truncated
          chunk fails the restore.
SQLite    copied with the sqlite3 online-backup API, so a live WAL database is
          captured consistently; -wal/-shm/-journal files are never archived.
Excluded  EXCLUDED_DIRS and EXCLUDED_FILES below: caches, logs, downloaded models,
          installed software, browser-profile caches, __pycache__, symlinks.
Key       32 random bytes (base64 text) at ~/Library/Application Support/Narad/backup.key
          on macOS or $XDG_CONFIG_HOME/narad/backup.key elsewhere; override with
          NARAD_BACKUP_KEY_FILE. It must live outside NARAD_HOME and the backup
          folder, so an archive on its own is useless. Keep a copy in a password
          manager: without the key no archive can be restored.
Retention after each successful backup: the newest archive of each of the 14 most
          recent days that have one, plus the newest of each of the 8 most recent
          ISO weeks that have one (UTC), about 8 weeks of history. Other files in
          the folder are never touched.
Records   NARAD_HOME/ops/backup.jsonl (each backup) and
          NARAD_HOME/ops/backup_drill.jsonl (each drill), read by the weekly scorecard.

Standard library plus `cryptography` only, so launchd can run it without the app.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import fnmatch
import gzip
import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
import struct
import sys
import tarfile
import tempfile
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Never archived. A directory name matches at any depth under NARAD_HOME; a file
# pattern matches the file name. Keep this list and docs/PILOT_CONSENT_AND_METRICS.md
# ("What is backed up") in step.
EXCLUDED_DIRS = frozenset({
    # Rebuildable caches.
    "__pycache__", ".cache", "cache", "caches", "raw-cache", "tmp",
    # Logs (the host keeps its own under ~/Library/Logs/Narad).
    "logs",
    # Downloaded model weights and installed software (Artemis' .venv and the like).
    "models", "ollama", "huggingface", "hf-cache", ".venv", "venv", "node_modules",
    # Browser profiles keep cookies and settings; their caches are rebuilt.
    "Cache", "Code Cache", "GPUCache", "ShaderCache", "GrShaderCache", "GraphiteDawnCache",
    "DawnCache", "DawnGraphiteCache", "DawnWebGPUCache", "CacheStorage", "ScriptCache",
    "Crashpad", "component_crx_cache", "extensions_crx_cache", "optimization_guide_model_store",
})
EXCLUDED_FILES = (
    "*.log", "*.log.[0-9]*", "*.pyc", "*.tmp", "*.partial", ".DS_Store", "*.sock",
    # SQLite side files: the online-backup copy of the database already holds their data.
    "*-wal", "*-shm", "*-journal",
    # Model weights.
    "*.gguf", "*.safetensors",
)

MAGIC = b"NARADBK1"
_HEADER = struct.Struct(">8s8s7sI5x")  # magic, key id, nonce prefix, chunk size, reserved
_LENGTH = struct.Struct(">I")
CHUNK_SIZE = 1 << 20
_TAG = 16
_SQLITE_MAGIC = b"SQLite format 3\x00"
MANIFEST_NAME = ".narad-backup-manifest.json"
_ARCHIVE_RE = re.compile(r"^narad-(\d{8}T\d{6}Z)\.nbk$")
KEEP_DAILY, KEEP_WEEKLY = 14, 8


class BackupError(RuntimeError):
    """A backup, restore or drill could not be completed."""


class IntegrityError(BackupError):
    """The archive is not authentic: tampered, truncated, or not a Narad backup."""


# ── Locations ─────────────────────────────────────────────────────────────────

def narad_home() -> Path:
    return Path(os.environ.get("NARAD_HOME") or Path.home() / ".narad").expanduser()


def backup_dir() -> Path:
    return Path(os.environ.get("NARAD_BACKUP_DIR") or Path.home() / "NaradBackups").expanduser()


def key_path() -> Path:
    override = os.environ.get("NARAD_BACKUP_KEY_FILE")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Narad" / "backup.key"
    config = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(config).expanduser() / "narad" / "backup.key"


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _record(name: str, entry: dict[str, Any]) -> None:
    """Append one line to NARAD_HOME/ops/<name> (best effort, 0600)."""
    path = narad_home() / "ops" / name
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"t": round(time.time(), 3), "ts": _now_iso(), **entry}) + "\n")
        os.chmod(path, 0o600)
    except OSError as exc:
        print(f"warning: could not write {path}: {exc}", file=sys.stderr)


# ── Key ───────────────────────────────────────────────────────────────────────

def _key_id(key: bytes) -> bytes:
    return hashlib.sha256(b"narad-backup-key-id" + key).digest()[:8]


def load_key(*, create: bool = False) -> bytes:
    path = key_path()
    for root in (narad_home(), backup_dir()):
        if _inside(path, root):
            raise BackupError(f"The backup key must live outside {root}; set NARAD_BACKUP_KEY_FILE elsewhere.")
    if path.exists():
        if path.stat().st_mode & 0o077:
            os.chmod(path, 0o600)
        try:
            key = base64.b64decode(path.read_text(encoding="ascii").strip(), validate=True)
        except (ValueError, UnicodeDecodeError) as exc:
            raise BackupError(f"{path} is not a Narad backup key") from exc
        if len(key) != 32:
            raise BackupError(f"{path} is not a Narad backup key (expected 32 bytes)")
        return key
    if not create:
        raise BackupError(
            f"No backup key at {path}. Restore it from your password manager (one line of "
            "base64, file mode 600) or point NARAD_BACKUP_KEY_FILE at it."
        )
    key = secrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(base64.b64encode(key).decode("ascii") + "\n")
    copy_hint = f'pbcopy < "{path}"' if sys.platform == "darwin" else f'cat "{path}"'
    print(
        "\nA new backup key was created at:\n"
        f"  {path}\n"
        "Without it no backup can be restored. Save a copy in your password manager now:\n"
        f"  1. {copy_hint}\n"
        '  2. Paste it into a new secure note named "Narad backup key", then copy something\n'
        "     else to clear the clipboard.\n"
        "To restore on another Mac, put that one line back in the same file (chmod 600).\n"
        "This message is shown once; the key itself is never printed or logged.\n"
    )
    return key


# ── Streaming AES-256-GCM ─────────────────────────────────────────────────────

class EncryptingWriter(io.RawIOBase):
    """Write side of the chunked format; close() seals the final chunk.

    Nonce = 7-byte random prefix | 4-byte chunk counter | 1-byte final flag, and
    the header is the associated data of every chunk (the STREAM construction).
    """

    def __init__(self, raw: BinaryIO, key: bytes, chunk_size: int = CHUNK_SIZE) -> None:
        self._raw = raw
        self._aes = AESGCM(key)
        self._prefix = secrets.token_bytes(7)
        self._chunk = chunk_size
        self._header = _HEADER.pack(MAGIC, _key_id(key), self._prefix, chunk_size)
        self._buffer = bytearray()
        self._counter = 0
        raw.write(self._header)

    def writable(self) -> bool:
        return True

    def write(self, data: Any) -> int:
        self._buffer += data
        while len(self._buffer) > self._chunk:
            self._seal(bytes(self._buffer[:self._chunk]), final=False)
            del self._buffer[:self._chunk]
        return len(data)

    def _seal(self, chunk: bytes, *, final: bool) -> None:
        if self._counter >= 2**32 - 1:
            raise BackupError("archive too large for one key stream")
        nonce = self._prefix + struct.pack(">I", self._counter) + (b"\x01" if final else b"\x00")
        sealed = self._aes.encrypt(nonce, chunk, self._header)
        self._raw.write(_LENGTH.pack(len(sealed)))
        self._raw.write(sealed)
        self._counter += 1

    def close(self) -> None:
        # A failed backup abandons the stream; only a live one gets a final chunk.
        if not self.closed and not self._raw.closed:
            self._seal(bytes(self._buffer), final=True)
            self._buffer.clear()
        super().close()


class DecryptingReader(io.RawIOBase):
    """Read side: yields plaintext only from authenticated chunks, in order."""

    def __init__(self, raw: BinaryIO, key: bytes) -> None:
        header = raw.read(_HEADER.size)
        if len(header) < _HEADER.size or not header.startswith(MAGIC):
            raise IntegrityError("not a Narad backup archive")
        _, key_id, self._prefix, self._chunk = _HEADER.unpack(header)
        if key_id != _key_id(key):
            raise BackupError("this archive was made with a different backup key")
        self._raw = raw
        self._aes = AESGCM(key)
        self._header = header
        self._counter = 0
        self._plain = b""
        self._offset = 0
        self._finished = False

    def readable(self) -> bool:
        return True

    def _next_chunk(self) -> None:
        length = self._raw.read(_LENGTH.size)
        if len(length) < _LENGTH.size:
            raise IntegrityError("archive is truncated (final chunk missing)")
        (size,) = _LENGTH.unpack(length)
        if not _TAG <= size <= self._chunk + _TAG:
            raise IntegrityError(f"chunk {self._counter} has an impossible length")
        sealed = self._raw.read(size)
        if len(sealed) < size:
            raise IntegrityError("archive is truncated")
        counter = struct.pack(">I", self._counter)
        for flag in (b"\x00", b"\x01"):
            try:
                self._plain = self._aes.decrypt(self._prefix + counter + flag, sealed, self._header)
            except InvalidTag:
                continue
            self._offset = 0
            self._counter += 1
            if flag == b"\x01":
                self._finished = True
                if self._raw.read(1):
                    raise IntegrityError("data follows the final chunk")
            return
        raise IntegrityError(f"chunk {self._counter} failed authentication (altered or corrupt)")

    def readinto(self, buffer: Any) -> int:
        while self._offset >= len(self._plain):
            if self._finished:
                return 0
            self._next_chunk()
        count = min(len(buffer), len(self._plain) - self._offset)
        buffer[:count] = self._plain[self._offset:self._offset + count]
        self._offset += count
        return count


# ── Archive contents ──────────────────────────────────────────────────────────

def is_excluded(relative: Path) -> bool:
    """Whether a path relative to NARAD_HOME is left out of backups."""
    if any(part in EXCLUDED_DIRS for part in relative.parts[:-1]):
        return True
    name = relative.name
    return name in EXCLUDED_DIRS or any(fnmatch.fnmatchcase(name, pattern) for pattern in EXCLUDED_FILES)


def _kind(path: Path, head: bytes) -> str:
    if head.startswith(_SQLITE_MAGIC):
        return "sqlite"
    suffix = path.suffix.lower()
    return {".json": "json", ".jsonl": "jsonl"}.get(suffix, "file")


def _walk(home: Path, skip: list[Path], skipped: list[dict[str, str]]) -> Iterator[Path]:
    for current, dirs, files in os.walk(home, followlinks=False):
        base = Path(current)
        kept = []
        for name in sorted(dirs):
            path = base / name
            if path.is_symlink():
                skipped.append({"path": str(path.relative_to(home)), "reason": "symlink"})
            elif name not in EXCLUDED_DIRS and not any(_inside(path, root) for root in skip):
                kept.append(name)
        dirs[:] = kept
        for name in sorted(files):
            path = base / name
            relative = path.relative_to(home)
            if is_excluded(relative):
                continue
            if path.is_symlink() or not path.is_file():
                skipped.append({"path": str(relative), "reason": "not a regular file"})
                continue
            yield path


class _FixedSize(io.RawIOBase):
    """Exactly `size` bytes from a file that may change while it is read."""

    def __init__(self, handle: BinaryIO, size: int) -> None:
        self._handle, self._left, self.changed = handle, size, False

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        if self._left <= 0:
            return 0
        want = min(len(buffer), self._left)
        data = self._handle.read(want)
        if not data:  # shrank underneath us: pad, and say so in the manifest
            self.changed = True
            data = b"\x00" * want
        buffer[:len(data)] = data
        self._left -= len(data)
        return len(data)


def _sqlite_copy(source: Path, target: Path) -> None:
    """A consistent snapshot of a live (possibly WAL) database via the backup API."""
    try:
        src = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
        src.execute("select count(*) from sqlite_master").fetchone()
    except sqlite3.Error:
        src = sqlite3.connect(str(source))
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _add(tar: tarfile.TarFile, handle: BinaryIO, arcname: str, *, mtime: float, mode: int) -> tuple[int, bool]:
    stat = os.fstat(handle.fileno())
    info = tarfile.TarInfo(arcname)
    info.size, info.mtime, info.mode = stat.st_size, int(mtime), mode & 0o777
    reader = _FixedSize(handle, stat.st_size)
    tar.addfile(info, io.BufferedReader(reader, CHUNK_SIZE))
    return stat.st_size, reader.changed


def create_backup(*, key: bytes | None = None, chunk_size: int = CHUNK_SIZE) -> dict[str, Any]:
    home, dest = narad_home(), backup_dir()
    if not home.is_dir():
        raise BackupError(f"{home} does not exist")
    key = key or load_key(create=True)
    dest.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(dest, 0o700)
    started = time.time()
    name = f"narad-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.nbk"
    final, partial = dest / name, dest / f".{name}.partial"
    skipped: list[dict[str, str]] = []
    entries: list[list[Any]] = []
    changed: list[str] = []
    try:
        with tempfile.TemporaryDirectory(prefix="narad-backup-") as scratch, open(partial, "wb") as raw:
            os.chmod(scratch, 0o700)
            writer = EncryptingWriter(raw, key, chunk_size)
            with gzip.GzipFile(fileobj=writer, mode="wb", compresslevel=6, mtime=0) as gz, \
                    tarfile.open(fileobj=gz, mode="w|", format=tarfile.PAX_FORMAT) as tar:
                for path in _walk(home, [dest], skipped):
                    arcname = path.relative_to(home).as_posix()
                    try:
                        stat = path.stat()
                        with open(path, "rb") as handle:
                            kind = _kind(path, handle.read(len(_SQLITE_MAGIC)))
                            handle.seek(0)
                            if kind != "sqlite":
                                size, was_changed = _add(tar, handle, arcname, mtime=stat.st_mtime, mode=stat.st_mode)
                        if kind == "sqlite":
                            snapshot = Path(scratch) / "snapshot.db"
                            _sqlite_copy(path, snapshot)
                            with open(snapshot, "rb") as handle:
                                size, was_changed = _add(tar, handle, arcname, mtime=stat.st_mtime, mode=stat.st_mode)
                            snapshot.unlink()
                    except (OSError, sqlite3.Error) as exc:
                        skipped.append({"path": arcname, "reason": type(exc).__name__})
                        continue
                    entries.append([arcname, size, kind])
                    if was_changed:
                        changed.append(arcname)
                manifest = {
                    "format": 1,
                    "created_at": _now_iso(),
                    "narad_home": str(home),
                    "files": len(entries),
                    "bytes": sum(entry[1] for entry in entries),
                    "entries": entries,
                    "changed_during_backup": changed,
                    "skipped": skipped,
                    "excluded_dirs": sorted(EXCLUDED_DIRS),
                    "excluded_files": list(EXCLUDED_FILES),
                }
                blob = json.dumps(manifest, indent=1).encode("utf-8")
                info = tarfile.TarInfo(MANIFEST_NAME)
                info.size, info.mtime, info.mode = len(blob), int(time.time()), 0o600
                tar.addfile(info, io.BytesIO(blob))
            writer.close()
            raw.flush()
            os.fsync(raw.fileno())
        os.chmod(partial, 0o600)
        partial.replace(final)
    except BaseException:
        with contextlib.suppress(OSError):
            partial.unlink()
        raise
    pruned = prune(dest, keep_also={name})
    result = {
        "archive": name,
        "archive_bytes": final.stat().st_size,
        "files": manifest["files"],
        "bytes": manifest["bytes"],
        "sqlite": sum(1 for entry in entries if entry[2] == "sqlite"),
        "skipped": len(skipped),
        "changed": len(changed),
        "pruned": len(pruned),
        "seconds": round(time.time() - started, 1),
    }
    _record("backup.jsonl", {"ok": True, **result})
    return result


# ── Listing and retention ─────────────────────────────────────────────────────

def archives(dest: Path | None = None) -> list[tuple[datetime, Path]]:
    """Archives in the folder, newest first."""
    folder = dest or backup_dir()
    found = []
    for path in folder.glob("narad-*.nbk") if folder.is_dir() else []:
        match = _ARCHIVE_RE.match(path.name)
        if match:
            stamp = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            found.append((stamp, path))
    return sorted(found, reverse=True)


def select_keep(stamps: list[datetime], *, daily: int = KEEP_DAILY, weekly: int = KEEP_WEEKLY) -> set[datetime]:
    """Newest archive of each of the `daily` latest days and `weekly` latest ISO weeks."""
    keep: set[datetime] = set()
    days: set[Any] = set()
    weeks: set[Any] = set()
    for stamp in sorted(stamps, reverse=True):
        day, week = stamp.date(), stamp.isocalendar()[:2]
        if day not in days and len(days) < daily:
            days.add(day)
            keep.add(stamp)
        if week not in weeks and len(weeks) < weekly:
            weeks.add(week)
            keep.add(stamp)
    return keep


def prune(dest: Path, *, keep_also: set[str] | None = None) -> list[str]:
    found = archives(dest)
    keep = select_keep([stamp for stamp, _ in found])
    removed = []
    for stamp, path in found:
        if stamp in keep or path.name in (keep_also or set()):
            continue
        path.unlink()
        removed.append(path.name)
    return removed


# ── Restore and drill ─────────────────────────────────────────────────────────

def _pick(archive: str | None) -> Path:
    found = archives()
    if archive:
        path = Path(archive).expanduser()
        path = path if path.is_file() else backup_dir() / archive
        if not path.is_file():
            raise BackupError(f"No archive {archive} in {backup_dir()}")
        return path
    if not found:
        raise BackupError(f"No backups in {backup_dir()}")
    return found[0][1]


def _safe_member(member: tarfile.TarInfo) -> None:
    name = Path(member.name)
    if member.name.startswith("/") or ".." in name.parts or not (member.isfile() or member.isdir()):
        raise IntegrityError(f"refusing unsafe archive entry {member.name!r}")


def restore(archive: Path, target: Path, *, key: bytes | None = None) -> dict[str, Any]:
    """Decrypt, authenticate and unpack `archive` into an empty or new `target`."""
    if target.exists() and any(target.iterdir()):
        raise BackupError(
            f"{target} is not empty. To replace a live NARAD_HOME: stop Narad, move the "
            "folder aside, then restore into the original path."
        )
    key = key or load_key()
    target.mkdir(parents=True, exist_ok=True)
    os.chmod(target, 0o700)
    manifest: dict[str, Any] | None = None
    files = total = 0
    with open(archive, "rb") as raw:
        reader = io.BufferedReader(DecryptingReader(raw, key), CHUNK_SIZE)
        with gzip.GzipFile(fileobj=reader, mode="rb") as gz, tarfile.open(fileobj=gz, mode="r|") as tar:
            for member in tar:
                _safe_member(member)
                if member.name == MANIFEST_NAME:
                    manifest = json.loads(tar.extractfile(member).read())  # type: ignore[union-attr]
                    continue
                if hasattr(tarfile, "data_filter"):
                    tar.extract(member, target, filter="data")
                else:  # pragma: no cover - Python before 3.11.4
                    tar.extract(member, target)
                if member.isfile():
                    files += 1
                    total += member.size
            # Read to the very end: that checks the gzip trailer and proves the
            # sealed final chunk is present, so a cut-short archive never passes.
            while gz.read(CHUNK_SIZE):
                pass
        if reader.read(1):
            raise IntegrityError("unexpected data after the archive")
    if manifest is None:
        raise IntegrityError("archive has no manifest")
    return {"archive": archive.name, "target": str(target), "files": files, "bytes": total, "manifest": manifest}


def verify_tree(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Check a restored tree: counts and sizes, SQLite integrity, JSON parsing."""
    failures: list[str] = []
    warnings: list[str] = []
    counts = {"sqlite": [0, 0], "json": [0, 0], "jsonl": [0, 0]}
    entries = manifest.get("entries") or []
    for arcname, size, kind in entries:
        path = root / arcname
        if not path.is_file():
            failures.append(f"{arcname}: missing")
            continue
        if path.stat().st_size != size:
            failures.append(f"{arcname}: size {path.stat().st_size} != {size}")
        if kind not in counts:
            continue
        counts[kind][1] += 1
        try:
            if kind == "sqlite":
                connection = sqlite3.connect(str(path))
                try:
                    verdict = connection.execute("PRAGMA integrity_check").fetchone()[0]
                finally:
                    connection.close()
                if verdict != "ok":
                    raise ValueError(verdict)
            elif kind == "json":
                text = path.read_text(encoding="utf-8")
                if not text.strip():
                    warnings.append(f"{arcname}: empty")
                    counts[kind][0] += 1
                    continue
                json.loads(text)
            else:
                text = path.read_text(encoding="utf-8")
                lines = text.split("\n")
                for number, line in enumerate(lines):
                    if not line.strip():
                        continue
                    try:
                        json.loads(line)
                    except ValueError:
                        # A line still being appended when the snapshot was taken.
                        if number == len(lines) - 1:
                            warnings.append(f"{arcname}: partial last line")
                            continue
                        raise
            counts[kind][0] += 1
        except (sqlite3.Error, ValueError, UnicodeDecodeError) as exc:
            failures.append(f"{arcname}: {type(exc).__name__}")
    restored = [p for p in root.rglob("*") if p.is_file() and not p.name.endswith(("-wal", "-shm"))]
    if len(restored) != int(manifest.get("files") or 0):
        failures.append(f"file count {len(restored)} != manifest {manifest.get('files')}")
    total = sum(entry[1] for entry in entries)
    if total != int(manifest.get("bytes") or 0):
        failures.append(f"manifest bytes {manifest.get('bytes')} != entries {total}")
    return {"failures": failures, "warnings": warnings, "counts": counts}


def drill(archive: str | None = None) -> dict[str, Any]:
    """Restore the newest (or named) archive into a temp dir, verify it, record the result."""
    started = time.time()
    result: dict[str, Any] = {"archive": None, "passed": False, "failures": [], "warnings": 0}
    try:
        path = _pick(archive)
        result["archive"] = path.name
        with tempfile.TemporaryDirectory(prefix="narad-drill-") as scratch:
            os.chmod(scratch, 0o700)
            restored = restore(path, Path(scratch) / "home")
            check = verify_tree(Path(scratch) / "home", restored["manifest"])
        counts = check["counts"]
        result.update(
            files=restored["files"],
            bytes=restored["bytes"],
            manifest_files=restored["manifest"].get("files"),
            sqlite_ok=counts["sqlite"][0], sqlite_total=counts["sqlite"][1],
            json_ok=counts["json"][0], json_total=counts["json"][1],
            jsonl_ok=counts["jsonl"][0], jsonl_total=counts["jsonl"][1],
            failures=check["failures"][:20],
            warnings=len(check["warnings"]),
            passed=not check["failures"],
        )
    except BackupError as exc:
        result["failures"] = [f"{type(exc).__name__}: {exc}"]
    except (OSError, tarfile.TarError, EOFError, ValueError, zlib.error) as exc:
        result["failures"] = [f"{type(exc).__name__}: {exc}"]
    result["seconds"] = round(time.time() - started, 1)
    _record("backup_drill.jsonl", result)
    return result


# ── CLI ───────────────────────────────────────────────────────────────────────

def _lock(dest: Path) -> Any:
    dest.mkdir(parents=True, exist_ok=True)
    handle = open(dest / ".narad-backup.lock", "w")
    if fcntl is not None:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise BackupError("another backup or drill is running") from None
    return handle


def _mb(size: int) -> str:
    return f"{size / 1_000_000:.1f} MB"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("backup", help="encrypt a snapshot of NARAD_HOME now, then apply retention")
    sub.add_parser("list", help="list archives, newest first")
    restore_cmd = sub.add_parser("restore", help="decrypt an archive into an empty directory")
    restore_cmd.add_argument("--to", required=True, help="target directory (new or empty)")
    restore_cmd.add_argument("--archive", help="archive name or path (default: newest)")
    drill_cmd = sub.add_parser("drill", help="restore the newest archive to a temp dir and verify it")
    drill_cmd.add_argument("--archive", help="archive name or path (default: newest)")
    args = parser.parse_args(argv)

    try:
        if args.command == "list":
            found = archives()
            keep = select_keep([stamp for stamp, _ in found])
            print(f"{len(found)} archive(s) in {backup_dir()}")
            for stamp, path in found:
                local = stamp.astimezone().strftime("%Y-%m-%d %H:%M")
                print(f"  {path.name}  {_mb(path.stat().st_size):>10}  {local}{'' if stamp in keep else '  (expires next backup)'}")
            return 0
        with _lock(backup_dir()):
            if args.command == "backup":
                result = create_backup()
                print(
                    f"Backed up {result['files']} files ({_mb(result['bytes'])}, {result['sqlite']} SQLite) "
                    f"to {backup_dir() / result['archive']} ({_mb(result['archive_bytes'])}) in "
                    f"{result['seconds']} s; {result['pruned']} old archive(s) removed."
                )
                if result["skipped"] or result["changed"]:
                    print(f"  {result['skipped']} skipped, {result['changed']} changed while reading (see manifest)")
                return 0
            if args.command == "restore":
                restored = restore(_pick(args.archive), Path(args.to).expanduser())
                check = verify_tree(Path(restored["target"]), restored["manifest"])
                print(f"Restored {restored['files']} files ({_mb(restored['bytes'])}) from "
                      f"{restored['archive']} into {restored['target']}")
                for failure in check["failures"]:
                    print(f"  problem: {failure}")
                return 0 if not check["failures"] else 1
            result = drill(args.archive)
            status = "PASS" if result["passed"] else "FAIL"
            print(f"Restore drill {status}: {result.get('archive') or 'no archive'}")
            if result.get("files") is not None:
                print(
                    f"  files {result['files']}/{result['manifest_files']}, SQLite ok "
                    f"{result['sqlite_ok']}/{result['sqlite_total']}, JSON ok {result['json_ok']}/"
                    f"{result['json_total']}, JSONL ok {result['jsonl_ok']}/{result['jsonl_total']}, "
                    f"{result['warnings']} warning(s), {result['seconds']} s"
                )
            for failure in result["failures"]:
                print(f"  problem: {failure}")
            return 0 if result["passed"] else 1
    except BackupError as exc:
        print(f"narad_backup: {exc}", file=sys.stderr)
        if args.command == "backup":
            _record("backup.jsonl", {"ok": False, "error": type(exc).__name__})
        return 1


if __name__ == "__main__":
    sys.exit(main())
