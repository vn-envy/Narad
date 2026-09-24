#!/usr/bin/env python3
"""Performance check for Narad's local stack on the host Mac.

Measures each installed component — privacy gateway rules, the OpenMed PII
model, a local Jev-compatible decision server (Laya), speech-to-text, OCR and
the Ollama fallback model — for load time, p50/p95 latency and memory, and
compares them with the targets in docs/PILOT_READINESS_PLAN_2026-09-23.md.
Components that are not installed are reported as skipped. Nothing leaves the
machine: only loopback services are contacted.

    .venv/bin/python scripts/bench_local_stack.py
    .venv/bin/python scripts/bench_local_stack.py --audio ~/clip_hi.wav \
        --image ~/lab_report.jpg --laya-url http://127.0.0.1:8090 --runs 30

Results are written to ~/.narad/benchmarks/local_stack_<timestamp>.json.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import narad_paths  # noqa: E402, F401

TARGETS_MS = {
    "gateway_rules_message": 5,
    "openmed_message": 150,
    "laya_decision": 20,
    "stt_final": 1000,
    "ocr_page": 10_000,
    "ollama_first_token": 2500,
}

MESSAGE = (
    "Papa ka HbA1c 8.2 aaya hai, Dr. Rao ko Thursday 10:30 dikhana hai. "
    "Mera number +91 98765 43210, UPI asha@okaxis, Aadhaar 2345 6789 0124. "
    "Report ke saath prescription bhi bhejo."
)


def _rss_mb() -> float:
    try:
        import psutil

        return psutil.Process().memory_info().rss / 1e6
    except Exception:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak / 1e6 if sys.platform == "darwin" else peak / 1e3


def _timed(fn: Callable[[], Any], runs: int) -> dict[str, float]:
    samples = []
    for _ in range(runs):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return {
        "p50_ms": round(statistics.median(samples), 2),
        "p95_ms": round(samples[min(len(samples) - 1, int(len(samples) * 0.95))], 2),
        "runs": runs,
    }


def _shell(*cmd: str) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


def system_info() -> dict[str, Any]:
    info: dict[str, Any] = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
    }
    if sys.platform == "darwin":
        info["chip"] = _shell("sysctl", "-n", "machdep.cpu.brand_string")
        mem = _shell("sysctl", "-n", "hw.memsize")
        info["ram_gb"] = round(int(mem) / 2**30, 1) if mem.isdigit() else None
        info["thermal"] = _shell("pmset", "-g", "therm")
        info["power"] = _shell("pmset", "-g", "batt").splitlines()[:1]
    return info


def bench_gateway_rules(runs: int) -> dict[str, Any]:
    os.environ["NARAD_PII_DETECTOR"] = "rules"
    import privacy_gateway as gw

    prompt = (MESSAGE + "\n") * 250  # ~40K chars, the size of a system prompt plus history
    started = time.perf_counter()
    gw.find_spans(MESSAGE, use_ml=False)  # first call loads the family name list
    return {
        "cold_ms": round((time.perf_counter() - started) * 1000, 2),
        "message": _timed(lambda: gw.find_spans(MESSAGE, use_ml=False), runs),
        "prompt_40k": _timed(lambda: gw.find_spans(prompt, use_ml=False), max(3, runs // 5)),
        "target_ms": TARGETS_MS["gateway_rules_message"],
    }


def bench_openmed(runs: int) -> dict[str, Any]:
    try:
        import openmed  # noqa: F401
    except Exception as exc:
        return {"skipped": f"openmed not installed ({type(exc).__name__}); pip install -e '.[privacy]'"}
    os.environ["NARAD_PII_DETECTOR"] = "openmed"
    import privacy_gateway as gw

    before = _rss_mb()
    started = time.perf_counter()
    gw._openmed.spans("Warm-up: Dr. Rao will call Asha tomorrow.")
    cold_ms = (time.perf_counter() - started) * 1000
    counter = iter(range(10**6))
    # Each run is a new message, so the per-line cache does not hide the model cost.
    warm = _timed(lambda: gw._openmed.spans(f"{MESSAGE} (note {next(counter)})"), runs)
    return {"cold_load_ms": round(cold_ms), "message": warm, "rss_delta_mb": round(_rss_mb() - before),
            "target_ms": TARGETS_MS["openmed_message"]}


def bench_laya(url: str, runs: int) -> dict[str, Any]:
    if not url:
        return {"skipped": "pass --laya-url http://127.0.0.1:<port> for a running Jev-compatible Laya server"}
    from decision_engine import DecisionQuestion, JevDecisionProvider

    provider = JevDecisionProvider(api_key=os.environ.get("LAYA_API_KEY", "local"), base_url=url)
    question = {"side_effect": DecisionQuestion("noul", "Does this task send money or messages?")}

    def decide() -> None:
        result = provider.evaluate(decision_id="bench_v1", state={"task": "paise bhejo Asha ko"},
                                   questions=question, record_cost=False)
        if not result.available:
            raise RuntimeError(result.error or result.status)

    try:
        decide()
    except Exception as exc:
        return {"skipped": f"decision server not answering: {exc}"}
    return {"decision": _timed(decide, runs), "target_ms": TARGETS_MS["laya_decision"]}


def bench_stt(audio: str, runs: int) -> dict[str, Any]:
    if not audio:
        return {"skipped": "pass --audio <3-8 s Hindi/Hinglish clip> to time speech-to-text"}
    results: dict[str, Any] = {}
    try:
        import mlx_whisper

        model = os.environ.get("NARAD_MLX_WHISPER", "mlx-community/whisper-large-v3-turbo")
        started = time.perf_counter()
        mlx_whisper.transcribe(audio, path_or_hf_repo=model, language="hi")
        results["mlx_whisper"] = {
            "cold_ms": round((time.perf_counter() - started) * 1000),
            "warm": _timed(lambda: mlx_whisper.transcribe(audio, path_or_hf_repo=model, language="hi"),
                           max(3, runs // 10)),
        }
    except ImportError:
        results["mlx_whisper"] = {"skipped": "pip install mlx-whisper"}
    try:
        from faster_whisper import WhisperModel

        size = os.environ.get("NARAD_WHISPER_MODEL", "small")
        started = time.perf_counter()
        model = WhisperModel(size, device="cpu", compute_type="int8")
        load_ms = (time.perf_counter() - started) * 1000

        def run() -> None:
            segments, _ = model.transcribe(audio, language="hi")
            list(segments)

        results[f"faster_whisper_{size}"] = {"load_ms": round(load_ms), "warm": _timed(run, max(3, runs // 10))}
    except ImportError:
        results["faster_whisper"] = {"skipped": "pip install -e '.[voice]'"}
    results["target_ms"] = TARGETS_MS["stt_final"]
    return results


def bench_ocr(image: str, runs: int) -> dict[str, Any]:
    """Narad's own OCR path (ocr_skill): the engine NARAD_OCR_ENGINE selects, page by page."""
    if not image:
        return {"skipped": "pass --image <photo or scanned PDF of a lab report or statement> to time OCR"}
    import ocr_skill

    engine = ocr_skill.engine_name()
    if not engine:
        return {"skipped": 'no local OCR engine: pip install -e ".[ocr]" (NARAD_OCR_ENGINE=paddle|surya)'}
    before = _rss_mb()
    started = time.perf_counter()
    first = ocr_skill.read_document(image, use_cache=False)  # includes loading the model
    cold_ms = (time.perf_counter() - started) * 1000
    if first.get("status") not in {"ok", "partial"}:
        return {"error": first.get("message", "OCR failed")}
    page_ms: list[float] = []
    for _ in range(max(1, min(runs, 5))):
        result = ocr_skill.read_document(image, use_cache=False)
        page_ms += [ms for ms, page in zip(result["timings_ms"]["pages"], result["pages"]) if page["source"] == "ocr"]
    page_ms.sort()
    pages = first["pages"]
    report: dict[str, Any] = {
        "engine": engine,
        "status": ocr_skill.status(),
        "pages": len(pages),
        "ocr_pages": sum(page["source"] == "ocr" for page in pages),
        "lines": sum(len(page["lines"]) for page in pages),
        "mean_confidence": [page["mean_confidence"] for page in pages],
        "cold_document_ms": round(cold_ms),
        "rss_delta_mb": round(_rss_mb() - before),
        "target_ms": TARGETS_MS["ocr_page"],
    }
    if page_ms:
        report["page"] = {
            "p50_ms": round(statistics.median(page_ms), 1),
            "p95_ms": round(page_ms[min(len(page_ms) - 1, int(len(page_ms) * 0.95))], 1),
            "runs": len(page_ms),
        }
    ocr_skill.unload()
    return report


def bench_ollama(model: str) -> dict[str, Any]:
    import httpx

    base = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    try:
        tags = httpx.get(f"{base}/api/tags", timeout=3).json()
    except Exception:
        return {"skipped": f"Ollama not reachable at {base}"}
    names = [m.get("name") for m in tags.get("models", [])]
    model = model or next((n for n in names if n and "gemma" in n), "")
    if not model:
        return {"skipped": f"no Gemma model installed (found: {names})"}
    started = time.perf_counter()
    first_token_ms = None
    tok_s = None
    with httpx.stream("POST", f"{base}/api/generate", timeout=120, json={
        "model": model, "prompt": "Reply in one Hinglish sentence: aaj mausam kaisa hai?",
        "stream": True, "options": {"num_ctx": 16384},
    }) as response:
        for line in response.iter_lines():
            if not line:
                continue
            chunk = json.loads(line)
            if chunk.get("response") and first_token_ms is None:
                first_token_ms = (time.perf_counter() - started) * 1000
            if chunk.get("done"):
                eval_ns = chunk.get("eval_duration") or 0
                tok_s = (chunk.get("eval_count") or 0) / (eval_ns / 1e9) if eval_ns else None
                break
    return {
        "model": model,
        "first_token_ms_incl_load": round(first_token_ms or 0),
        "tokens_per_s": round(tok_s, 1) if tok_s else None,
        "resident": _shell("ollama", "ps"),
        "target_ms": TARGETS_MS["ollama_first_token"],
    }


def _verdict(entry: dict[str, Any]) -> str:
    target = entry.get("target_ms")
    measured = None
    for key in ("message", "decision", "page", "warm"):
        if isinstance(entry.get(key), dict) and "p95_ms" in entry[key]:
            measured = entry[key]["p95_ms"]
    if measured is None and "first_token_ms_incl_load" in entry:
        measured = entry["first_token_ms_incl_load"]
    if target is None or measured is None:
        return ""
    return f"p95 {measured:.0f} ms vs target {target} ms: {'OK' if measured <= target else 'OVER'}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--audio", default="")
    parser.add_argument("--image", default="")
    parser.add_argument("--laya-url", default=os.environ.get("NARAD_LAYA_URL", ""))
    parser.add_argument("--ollama-model", default="")
    parser.add_argument("--out", default=str(Path(os.environ.get("NARAD_HOME", Path.home() / ".narad")) / "benchmarks"))
    args = parser.parse_args()

    report: dict[str, Any] = {"system": system_info(), "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    for name, run in (
        ("gateway_rules", lambda: bench_gateway_rules(args.runs)),
        ("openmed", lambda: bench_openmed(args.runs)),
        ("laya", lambda: bench_laya(args.laya_url, args.runs)),
        ("stt", lambda: bench_stt(args.audio, args.runs)),
        ("ocr", lambda: bench_ocr(args.image, args.runs)),
        ("ollama", lambda: bench_ollama(args.ollama_model)),
    ):
        try:
            report[name] = run()
        except Exception as exc:
            report[name] = {"error": f"{type(exc).__name__}: {exc}"}
        entry = report[name]
        status = entry.get("skipped") or entry.get("error") or _verdict(entry) or "measured"
        print(f"{name:14} {status}")
    report["rss_mb"] = round(_rss_mb())
    if sys.platform == "darwin":
        report["thermal_after"] = _shell("pmset", "-g", "therm")

    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"local_stack_{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nFull report: {path}")


if __name__ == "__main__":
    main()
