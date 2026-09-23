"""Managed, zero-key Ollama runtime for Narad's offline Gemma lane.

Narad uses one simple hardware split: Gemma 4 E2B below 16 GB RAM and E4B at
16 GB or above. Ollama stays available while model weights load lazily on the
first local request and unload quickly on constrained machines.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from narad_config import CONFIG_DIR

log = logging.getLogger("narad.local_model")

DEFAULT_BASE_URL = "http://127.0.0.1:11434"
E2B_MODEL_TAG = "gemma4:e2b-it-q4_K_M"
E4B_MODEL_TAG = "gemma4:e4b-it-q4_K_M"
MODEL_DOWNLOAD_GB = {E2B_MODEL_TAG: 7.2, E4B_MODEL_TAG: 9.6}
MODEL_MAX_CONTEXT_TOKENS = 131_072
E4B_RAM_THRESHOLD_GB = 16
_STATUS_CACHE_SECONDS = 2.0


def _apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() in {"arm64", "aarch64"}


def _ram_gb() -> float:
    try:
        if platform.system() == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if result.returncode == 0:
                return round(int(result.stdout.strip()) / 1024**3, 1)
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return round((pages * page_size) / 1024**3, 1)
    except (AttributeError, OSError, TypeError, ValueError):
        return 0.0


def _normalized_base_url(value: str | None = None) -> str:
    raw = (value or os.environ.get("OLLAMA_API_BASE") or os.environ.get("OLLAMA_HOST") or DEFAULT_BASE_URL).strip()
    if not raw.startswith(("http://", "https://")):
        raw = f"http://{raw}"
    raw = raw.rstrip("/")
    for suffix in ("/v1", "/api"):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)]
    return raw


def default_model_tag() -> str:
    override = os.environ.get("NARAD_LOCAL_MODEL", "").strip()
    if override:
        return override.removeprefix("ollama/").removeprefix("ollama_chat/")
    return E4B_MODEL_TAG if _ram_gb() >= E4B_RAM_THRESHOLD_GB else E2B_MODEL_TAG


def selected_model_size() -> str:
    return "E4B" if "e4b" in default_model_tag().lower() else "E2B"


def model_download_gb() -> float:
    return MODEL_DOWNLOAD_GB.get(default_model_tag(), 0.0)


def local_model_id() -> str:
    return f"ollama/{default_model_tag()}"


def configured_context_tokens() -> int:
    """Return a usable context, not Gemma's marketing maximum.

    The 8 GB profile prioritizes reliable tool execution and UI responsiveness.
    More memory enables a larger working context without changing model tiers.
    """
    configured = os.environ.get("NARAD_LOCAL_CONTEXT_TOKENS", "").strip()
    if configured:
        try:
            return max(8_192, min(int(configured), MODEL_MAX_CONTEXT_TOKENS))
        except ValueError:
            pass
    ram = _ram_gb()
    if ram and ram < E4B_RAM_THRESHOLD_GB:
        return 16_384
    return 32_768


def configured_keep_alive() -> str:
    override = os.environ.get("NARAD_LOCAL_KEEP_ALIVE", "").strip()
    if override:
        return override
    ram = _ram_gb()
    if ram and ram < E4B_RAM_THRESHOLD_GB:
        return "2m"
    if ram and ram < 32:
        return "10m"
    return "-1"


def local_completion_options(model: str) -> dict[str, Any]:
    """LiteLLM options for Gemma 4's native Ollama chat/tool interface."""
    lower = (model or "").lower()
    if "gemma4" not in lower or not (
        lower.startswith("ollama/") or lower.startswith("ollama_chat/")
    ):
        return {}
    return {
        "api_base": _normalized_base_url(),
        "num_ctx": configured_context_tokens(),
        # Google's recommended Gemma 4 sampling defaults.
        "temperature": 1.0,
        "top_p": 0.95,
        "top_k": 64,
    }


def _model_matches(candidate: str, wanted: str) -> bool:
    candidate = candidate.removeprefix("ollama/").removesuffix(":latest").lower()
    wanted = wanted.removeprefix("ollama/").removesuffix(":latest").lower()
    return candidate == wanted


class LocalModelRuntime:
    """Owns only the Ollama daemon and pull job that Narad starts itself."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._log_handle: Any = None
        self._last_probe_at = 0.0
        self._last_probe: dict[str, Any] | None = None
        self._install: dict[str, Any] = {
            "state": "idle",
            "progress": 0.0,
            "status": "Not downloaded",
            "error": None,
        }
        self._install_thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return _normalized_base_url()

    @property
    def model_tag(self) -> str:
        return default_model_tag()

    def _request_json(
        self,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout: float = 2.0,
    ) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers={"Content-Type": "application/json"} if body else {},
            method="POST" if body else "GET",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data if isinstance(data, dict) else {}

    def probe(self, *, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            if (
                not force
                and self._last_probe is not None
                and now - self._last_probe_at < _STATUS_CACHE_SECONDS
            ):
                return dict(self._last_probe)

        binary = shutil.which("ollama")
        reachable = False
        version: str | None = None
        models: list[str] = []
        try:
            version_payload = self._request_json("/api/version")
            version = str(version_payload.get("version") or "") or None
            tags_payload = self._request_json("/api/tags", timeout=4.0)
            models = [
                str(item.get("name") or item.get("model") or "")
                for item in tags_payload.get("models", [])
                if isinstance(item, dict)
            ]
            reachable = True
        except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
            pass

        model_installed = any(_model_matches(item, self.model_tag) for item in models)
        ready = reachable and model_installed
        ram = _ram_gb()
        constrained = bool(ram and ram < 12)
        parsed = urllib.parse.urlparse(self.base_url)
        local_host = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        with self._lock:
            install = dict(self._install)
            managed = bool(self._process and self._process.poll() is None)

        if ready:
            reason = None
        elif install.get("state") == "running":
            reason = f"Gemma 4 {selected_model_size()} is downloading"
        elif not reachable and not binary:
            reason = "Ollama is not installed"
        elif not reachable:
            reason = "Ollama is installed but its local service is not reachable"
        else:
            reason = f"{self.model_tag} is not downloaded"

        result = {
            "available": reachable,
            "ready": ready,
            "runtime_installed": bool(binary) or reachable,
            "reachable": reachable,
            "managed": managed,
            "local_host": local_host,
            "url": self.base_url,
            "version": version,
            "model": local_model_id(),
            "model_tag": self.model_tag,
            "model_installed": model_installed,
            "model_size": selected_model_size(),
            "optimized_variant": "q4-k-m",
            "download_gb": model_download_gb(),
            "upgrade_threshold_gb": E4B_RAM_THRESHOLD_GB,
            "ram_gb": ram,
            "memory_constrained": constrained,
            "residency": "on-demand" if constrained else "warm",
            "keep_alive": configured_keep_alive(),
            "configured_context_tokens": configured_context_tokens(),
            "max_context_tokens": MODEL_MAX_CONTEXT_TOKENS,
            "no_api_key": True,
            "supports": {
                "text": True,
                "images": True,
                "native_tools": True,
                "narad_tools": True,
                "browser_use": True,
                "computer_use": True,
            },
            "install": install,
            "reason": reason,
        }
        with self._lock:
            self._last_probe = dict(result)
            self._last_probe_at = now
        return result

    def ensure_server(self, *, timeout: float = 10.0) -> dict[str, Any]:
        """Start loopback Ollama when available; never replace a remote host."""
        current = self.probe(force=True)
        if current["reachable"]:
            return current
        if os.environ.get("NARAD_LOCAL_RUNTIME", "on").strip().lower() in {"0", "off", "false"}:
            return current
        if not current["local_host"]:
            return current
        binary = shutil.which("ollama")
        if not binary:
            return current

        with self._lock:
            if self._process and self._process.poll() is None:
                process = self._process
            else:
                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                log_path = CONFIG_DIR / "ollama-runtime.log"
                self._log_handle = log_path.open("a", encoding="utf-8")
                env = os.environ.copy()
                parsed = urllib.parse.urlparse(self.base_url)
                env["OLLAMA_HOST"] = f"{parsed.hostname or '127.0.0.1'}:{parsed.port or 11434}"
                env.setdefault("OLLAMA_CONTEXT_LENGTH", str(configured_context_tokens()))
                env.setdefault("OLLAMA_KEEP_ALIVE", configured_keep_alive())
                env.setdefault("OLLAMA_FLASH_ATTENTION", "1")
                env.setdefault("OLLAMA_KV_CACHE_TYPE", "q8_0")
                process = subprocess.Popen(
                    [binary, "serve"],
                    stdout=self._log_handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=env,
                    start_new_session=True,
                )
                self._process = process
                log.info("Started managed Ollama runtime pid=%s at %s", process.pid, self.base_url)

        deadline = time.monotonic() + max(0.5, timeout)
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            status = self.probe(force=True)
            if status["reachable"]:
                return status
            time.sleep(0.2)
        return self.probe(force=True)

    def start_install(self) -> dict[str, Any]:
        status = self.ensure_server()
        if status["ready"]:
            with self._lock:
                self._install = {
                    "state": "complete",
                    "progress": 1.0,
                    "status": "Offline model ready",
                    "error": None,
                }
            return self.probe(force=True)
        if not status["reachable"]:
            raise RuntimeError(status["reason"] or "Ollama is unavailable")

        try:
            free_gb = shutil.disk_usage(Path.home()).free / 1024**3
        except OSError:
            free_gb = 0.0
        download_gb = model_download_gb()
        if free_gb and free_gb < download_gb + 2.0:
            raise RuntimeError(
                f"Gemma needs about {download_gb:.1f} GB plus working space; "
                f"only {free_gb:.1f} GB is free"
            )

        with self._lock:
            if self._install_thread and self._install_thread.is_alive():
                return self.probe(force=True)
            self._install = {
                "state": "running",
                "progress": 0.0,
                "status": f"Starting {self.model_tag} download",
                "error": None,
            }
            self._last_probe_at = 0.0
            self._install_thread = threading.Thread(
                target=self._pull_model,
                name="narad-gemma-pull",
                daemon=True,
            )
            self._install_thread.start()
        return self.probe(force=True)

    def _pull_model(self) -> None:
        payload = json.dumps({"model": self.model_tag, "stream": True}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/pull",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120.0) as response:
                for raw_line in response:
                    if not raw_line.strip():
                        continue
                    event = json.loads(raw_line.decode("utf-8"))
                    if event.get("error"):
                        raise RuntimeError(str(event["error"]))
                    total = int(event.get("total") or 0)
                    completed = int(event.get("completed") or 0)
                    progress = completed / total if total > 0 else 0.0
                    with self._lock:
                        self._install = {
                            "state": "running",
                            "progress": round(max(0.0, min(progress, 1.0)), 4),
                            "status": str(event.get("status") or "Downloading"),
                            "error": None,
                        }
                        self._last_probe_at = 0.0
            with self._lock:
                self._install = {
                    "state": "complete",
                    "progress": 1.0,
                    "status": "Offline model ready",
                    "error": None,
                }
                self._last_probe_at = 0.0
            self.probe(force=True)
            log.info("Offline model installed: %s", self.model_tag)
        except Exception as exc:
            with self._lock:
                self._install = {
                    "state": "error",
                    "progress": 0.0,
                    "status": "Download failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
                self._last_probe_at = 0.0
            log.warning("Offline model pull failed: %s", exc)

    def shutdown(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
            log_handle = self._log_handle
            self._log_handle = None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        if log_handle:
            log_handle.close()


_RUNTIME = LocalModelRuntime()


def get_local_model_runtime() -> LocalModelRuntime:
    return _RUNTIME


def local_runtime_status(*, force: bool = False) -> dict[str, Any]:
    return _RUNTIME.probe(force=force)


def local_model_ready() -> bool:
    return bool(_RUNTIME.probe().get("ready"))


def local_model_available(model: str = "") -> bool:
    status = _RUNTIME.probe()
    if not status.get("reachable"):
        return False
    wanted = (model or status.get("model_tag") or "").removeprefix("ollama/").removeprefix("ollama_chat/")
    if _model_matches(str(status.get("model_tag") or ""), wanted) and status.get("model_installed"):
        return True
    try:
        payload = _RUNTIME._request_json("/api/tags", timeout=4.0)
    except Exception:
        return False
    return any(
        _model_matches(str(item.get("name") or item.get("model") or ""), wanted)
        for item in payload.get("models", [])
        if isinstance(item, dict)
    )
