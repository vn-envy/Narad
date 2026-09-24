"""Privacy gateway: owner policy that DeepSeek (and unknown hosts) only ever see
pseudonymised text, xAI is never called, and replies are restored on the Mac."""
from __future__ import annotations

import asyncio
import json
import re
import sys
import types as pytypes
from pathlib import Path

import pytest

_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "narad_paths.py").exists())
sys.path[:0] = [str(_ROOT)]
import narad_litellm  # noqa: E402
from google.adk.models.lite_llm import LiteLlm  # noqa: E402
from google.adk.models.llm_request import LlmRequest  # noqa: E402
from google.adk.models.llm_response import LlmResponse  # noqa: E402
from google.genai import types  # noqa: E402
from narad_litellm import NaradLiteLlm  # noqa: E402

import narad_paths  # noqa: E402, F401
import privacy_gateway as gw  # noqa: E402

# A valid Aadhaar-format number (Verhoeff check digit) that is not a real one.
AADHAAR = "2345 6789 0124"
FAMILY = {
    term: ("PERSON", "Asha Sharma") for term in ("asha sharma", "asha", "sharma", "आशा")
}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for var in ("NARAD_ENDPOINT_URL", "NARAD_ENDPOINT_MODEL", "NARAD_PROVIDER_TIERS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NARAD_PII_DETECTOR", "rules")
    monkeypatch.setattr(gw, "_privacy_dir", lambda profile_id=None: tmp_path / "privacy")
    monkeypatch.setattr(gw, "_family_terms", lambda: dict(FAMILY))
    monkeypatch.setattr(gw, "_stores", {})
    gw._terms_cache.update(key=None, pattern=None, labels={}, checked=0.0)


def _ledger(tmp_path: Path) -> list[dict]:
    path = tmp_path / "privacy" / "egress.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _pii_free(text: str) -> bool:
    lowered = text.lower()
    return not any(term in lowered for term in ("asha", "sharma", "98765", "2345", "asha@example.com"))


# ── Tiers ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("model", "tier"),
    [
        ("deepseek/deepseek-flash", gw.REDACT),
        ("nebius/deepseek-ai/DeepSeek-V4.1-Flash", gw.REDACT),
        ("fireworks_ai/accounts/fireworks/models/deepseek-v4", gw.REDACT),
        ("anthropic/claude-sonnet-5", gw.TRUSTED),
        ("openai/gpt-6-luna", gw.TRUSTED),
        ("gemini/gemini-2.5-flash", gw.TRUSTED),
        ("ollama/gemma4:e2b-it-q4_K_M", gw.LOCAL),
        ("xai/grok-4.6", gw.BLOCKED),
        ("some-new-host/model", gw.REDACT),
    ],
)
def test_default_tiers(model: str, tier: str) -> None:
    assert gw.provider_tier(model) == tier


def test_owner_overrides_tiers_but_never_unblocks_xai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NARAD_PROVIDER_TIERS", "nebius=trusted,xai=trusted,deepseek=bogus")
    assert gw.provider_tier("nebius/nvidia/Nemotron-3-Super") == gw.TRUSTED
    assert gw.provider_tier("xai/grok-4.6") == gw.BLOCKED
    assert gw.provider_tier("deepseek/deepseek-flash") == gw.REDACT


def test_custom_endpoint_is_redact_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NARAD_ENDPOINT_URL", "http://10.0.0.5:8000/v1")
    monkeypatch.setenv("NARAD_ENDPOINT_MODEL", "house-model")
    assert gw.provider_for_model("openai/house-model") == "custom"
    assert gw.provider_tier("openai/house-model") == gw.REDACT


# ── Detectors ─────────────────────────────────────────────────────────────────

def test_indian_identifiers_are_found() -> None:
    text = (
        f"Aadhaar {AADHAAR}, PAN ABCPD1234E, IFSC SBIN0001234, UPI asha@okaxis, "
        "mail asha@example.com, call +91 98765 43210, passport no. K1234567, "
        "card 4111 1111 1111 1111"
    )
    labels = {span.label for span in gw.find_spans(text, use_ml=False)}
    assert {"AADHAAR", "PAN", "IFSC", "UPI", "EMAIL", "PHONE", "PASSPORT", "CARD"} <= labels


def test_checksums_reject_lookalikes() -> None:
    assert not [s for s in gw.find_spans("order 2345 6789 0123", use_ml=False) if s.label == "AADHAAR"]
    assert not [s for s in gw.find_spans("ref 4111 1111 1111 1112", use_ml=False) if s.label == "CARD"]


def test_devanagari_digits_are_folded() -> None:
    spans = gw.find_spans("फ़ोन ९८७६५ ४३२१०", use_ml=False)
    assert [s.label for s in spans] == ["PHONE"]


def test_family_names_match_whole_words_in_both_scripts_but_not_relations() -> None:
    text = "Asha Sharma asked Papa; ashadeep is someone else; आशा को बुलाओ"
    found = [text[s.start:s.end] for s in gw.find_spans(text, use_ml=False)]
    assert found == ["Asha Sharma", "आशा"]


# ── Pseudonyms ────────────────────────────────────────────────────────────────

def test_placeholders_are_stable_and_restore_on_the_mac() -> None:
    first = gw.redact_text("Book for Asha Sharma, phone 9876543210")
    second = gw.redact_text("asha sharma again; Asha said आशा is fine")
    assert first == "Book for <PERSON_1>, phone <PHONE_1>"
    # Every name the family uses for one person maps to the same placeholder.
    assert second == "<PERSON_1> again; <PERSON_1> said <PERSON_1> is fine"
    assert gw.restore_text("Done for <PERSON_1> (<PHONE_1>), <PERSON_9> unknown") == (
        "Done for Asha Sharma (9876543210), <PERSON_9> unknown"
    )


def test_existing_placeholders_are_not_re_redacted() -> None:
    assert gw.redact_text("<PERSON_1> and <PHONE_2>") == "<PERSON_1> and <PHONE_2>"


# ── Agent calls through NaradLiteLlm ──────────────────────────────────────────

def _request() -> LlmRequest:
    return LlmRequest(
        model="deepseek/deepseek-flash",
        contents=[
            types.Content(role="user", parts=[types.Part(text=f"My Aadhaar is {AADHAAR}; I'm Asha.")]),
            types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(
                name="invoke_rama", args={"task": "Remind Asha Sharma at +91 98765 43210"},
            ))]),
            types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
                name="invoke_rama", response={"result": "Emailed asha@example.com"},
            ))]),
        ],
        config=types.GenerateContentConfig(system_instruction="Family: Asha Sharma."),
    )


def _run(model: NaradLiteLlm, request: LlmRequest) -> list[LlmResponse]:
    async def collect():
        return [item async for item in model.generate_content_async(request)]

    return asyncio.run(collect())


def test_deepseek_sees_only_placeholders_and_reply_is_restored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sent: list[str] = []

    async def fake_generate(self, llm_request, stream=False):
        sent.append(gw._serialize_request(llm_request))
        yield LlmResponse(content=types.Content(role="model", parts=[
            types.Part(text="Reminder set for <PERSON_1>."),
            types.Part(function_call=types.FunctionCall(name="set_medication_reminder", args={"who": "<PERSON_1>"})),
            types.Part(function_call=types.FunctionCall(name="web_search", args={"query": "<PERSON_1> clinic"})),
        ]))

    monkeypatch.setattr(LiteLlm, "generate_content_async", fake_generate)
    original = _request()
    responses = _run(NaradLiteLlm(model="deepseek/deepseek-flash"), original)

    assert sent and _pii_free(sent[0])
    assert "<AADHAAR_1>" in sent[0] and "<PERSON_1>" in sent[0]
    parts = responses[0].content.parts
    assert parts[0].text == "Reminder set for Asha Sharma."
    assert parts[1].function_call.args == {"who": "Asha Sharma"}
    # Search arguments leave the Mac, so they keep the placeholder.
    assert parts[2].function_call.args == {"query": "<PERSON_1> clinic"}
    # The session history (the caller's request) still holds real values.
    assert AADHAAR in original.contents[0].parts[0].text
    entry = _ledger(tmp_path)[-1]
    assert entry["tier"] == gw.REDACT and entry["entities"]["PERSON"] >= 1 and not entry["blocked"]


def test_trusted_provider_is_logged_but_not_rewritten(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sent: list[str] = []

    async def fake_generate(self, llm_request, stream=False):
        sent.append(gw._serialize_request(llm_request))
        yield LlmResponse()

    monkeypatch.setattr(LiteLlm, "generate_content_async", fake_generate)
    _run(NaradLiteLlm(model="anthropic/claude-sonnet-5"), _request())
    assert AADHAAR in sent[0]
    assert _ledger(tmp_path)[-1]["tier"] == gw.TRUSTED


def test_missing_openmed_fails_closed_to_the_local_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[str] = []

    async def fake_generate(self, llm_request, stream=False):
        calls.append(self.model)
        yield LlmResponse(model_version=self.model)

    monkeypatch.setenv("NARAD_PII_DETECTOR", "openmed")
    monkeypatch.setattr(gw._openmed, "_extract", None)
    monkeypatch.setattr(gw._openmed, "_error", "ModuleNotFoundError: openmed")
    monkeypatch.setattr(LiteLlm, "generate_content_async", fake_generate)
    monkeypatch.setattr(narad_litellm, "_offline_fallback_model",
                        lambda model: "" if model.startswith("ollama/") else "ollama/gemma4:e2b-it-q4_K_M")

    responses = _run(NaradLiteLlm(model="deepseek/deepseek-flash"), _request())
    assert calls == ["ollama/gemma4:e2b-it-q4_K_M"]
    assert responses[0].model_version == "ollama/gemma4:e2b-it-q4_K_M"
    assert _ledger(tmp_path)[-1]["blocked"] == "redactor_unavailable"

    monkeypatch.setattr(narad_litellm, "_offline_fallback_model", lambda model: "")
    with pytest.raises(gw.RedactorUnavailable):
        _run(NaradLiteLlm(model="deepseek/deepseek-flash"), _request())


def test_images_never_go_to_a_redact_tier_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_generate(self, llm_request, stream=False):
        calls.append(self.model)
        yield LlmResponse()

    monkeypatch.setattr(LiteLlm, "generate_content_async", fake_generate)
    monkeypatch.setattr(narad_litellm, "_offline_fallback_model", lambda model: "")
    request = _request()
    request.contents[0].parts.append(types.Part(inline_data=types.Blob(mime_type="image/png", data=b"\x89PNG")))
    with pytest.raises(gw.PrivacyGatewayError, match="Images"):
        _run(NaradLiteLlm(model="deepseek/deepseek-flash"), request)
    assert calls == []


def test_leak_after_redaction_is_blocked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gw, "redact_text", lambda text, **_: text)
    with pytest.raises(gw.LeakBlocked):
        gw.prepare_llm_request(_request(), "deepseek/deepseek-flash")
    assert _ledger(tmp_path)[-1]["blocked"].startswith("leak:")


def test_xai_is_blocked_before_any_request() -> None:
    with pytest.raises(gw.PolicyBlocked):
        gw.prepare_llm_request(_request(), "xai/grok-4.6")


# ── Streamed replies ──────────────────────────────────────────────────────────

REPLY = "Call <PERSON_1> on <PHONE_1> today."
RESTORED = "Call Asha Sharma on 9876543210 today."


def _known_placeholders() -> None:
    assert gw.redact_text("Asha Sharma, phone 9876543210") == "<PERSON_1>, phone <PHONE_1>"


def test_stream_restorer_holds_a_placeholder_split_across_chunks() -> None:
    _known_placeholders()
    restorer = gw.StreamRestorer()
    assert restorer.feed("Reminder for <PER") == "Reminder for "
    assert restorer.feed("SON_1> is set") == "Asha Sharma is set"
    assert restorer.flush() == ""


def test_stream_restorer_is_exact_at_every_split_point() -> None:
    _known_placeholders()
    for cut in range(len(REPLY) + 1):
        restorer = gw.StreamRestorer()
        pieces = [restorer.feed(REPLY[:cut]), restorer.feed(REPLY[cut:]), restorer.flush()]
        assert "".join(pieces) == RESTORED, cut
        assert not any("<" in piece for piece in pieces), cut
    restorer = gw.StreamRestorer()
    one_char_chunks = [restorer.feed(char) for char in REPLY] + [restorer.flush()]
    assert "".join(one_char_chunks) == RESTORED
    assert not any("<" in piece or "_" in piece for piece in one_char_chunks)


def test_stream_restorer_handles_several_placeholders_in_one_chunk() -> None:
    _known_placeholders()
    restorer = gw.StreamRestorer()
    assert restorer.feed("<PERSON_1> and <PHONE_1>, again <PER") == "Asha Sharma and 9876543210, again "
    assert restorer.feed("SON_1>.") == "Asha Sharma."


def test_stream_restorer_releases_a_less_than_that_is_not_a_placeholder() -> None:
    restorer = gw.StreamRestorer()
    assert restorer.feed("if a < b then") == "if a < b then"
    assert restorer.feed("so a <") == "so a "  # could still be a placeholder...
    assert restorer.feed(" b") == "< b"  # ...until the next chunk says otherwise
    assert restorer.feed("<3 and <b>bold</b>") == "<3 and <b>bold</b>"
    long_caps = "<" + "A" * 40
    assert restorer.feed(long_caps) == long_caps


def test_stream_restorer_flushes_what_it_holds_when_the_stream_ends() -> None:
    _known_placeholders()
    restorer = gw.StreamRestorer()
    assert restorer.feed("Ends with <PHONE_") == "Ends with "
    assert restorer.flush() == "<PHONE_"  # a cut-off placeholder is shown as is
    assert restorer.flush() == ""
    restorer.feed("then <PERSON_1")
    assert restorer.feed(">") == "Asha Sharma"


def _streamed(chunks: list[str], final: str) -> list[LlmResponse]:
    """What LiteLlm yields when streaming: text chunks, then the aggregated reply."""
    partials = [
        LlmResponse(content=types.Content(role="model", parts=[types.Part(text=chunk)]), partial=True)
        for chunk in chunks
    ]
    return partials + [LlmResponse(content=types.Content(role="model", parts=[types.Part(text=final)]))]


def test_deepseek_stream_is_restored_chunk_by_chunk_and_the_final_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunks = ["Call <PER", "SON_1> on <PHO", "NE_1> today", " and <"]
    final = "".join(chunks)

    async def fake_generate(self, llm_request, stream=False):
        for response in _streamed(chunks, final):
            yield response

    restored: list[str] = []
    original_restore = gw.restore_llm_response

    def counting_restore(response):
        restored.append(response.content.parts[0].text)
        return original_restore(response)

    monkeypatch.setattr(gw, "restore_llm_response", counting_restore)
    monkeypatch.setattr(LiteLlm, "generate_content_async", fake_generate)
    responses = _run(NaradLiteLlm(model="deepseek/deepseek-flash"), _request())

    expected = gw.restore_text(final)  # the request seeded <PERSON_1> and <PHONE_1>
    assert expected.startswith("Call Asha Sharma on ") and "_1>" not in expected
    partial_text = [r.content.parts[0].text for r in responses if r.partial]
    finals = [r for r in responses if not r.partial]
    assert "".join(partial_text) == expected
    assert not any("PER" in text or "PHO" in text for text in partial_text)
    assert partial_text[-1] == "<"  # held until the stream settled, then flushed
    assert len(finals) == 1 and finals[0].content.parts[0].text == expected
    assert restored == [final]  # the aggregated reply is restored in full, exactly once


def test_no_failover_after_the_first_chunk_even_while_it_is_held_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_generate(self, llm_request, stream=False):
        calls.append(self.model)
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="<PER")]), partial=True)
        raise TimeoutError("stream stalled")

    monkeypatch.setattr(narad_litellm, "_offline_fallback_model", lambda _model: "ollama/gemma4:e2b-it-q4_K_M")
    monkeypatch.setattr(LiteLlm, "generate_content_async", fake_generate)
    with pytest.raises(TimeoutError):
        _run(NaradLiteLlm(model="deepseek/deepseek-flash"), _request())
    assert calls == ["deepseek/deepseek-flash"]


def test_failover_before_the_first_byte_restores_on_the_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_generate(self, llm_request, stream=False):
        calls.append(self.model)
        if self.model.startswith("deepseek/"):
            raise TimeoutError("Connection timed out")
        for response in _streamed(["Hello ", "there"], "Hello there"):
            yield response

    monkeypatch.setattr(
        narad_litellm, "_offline_fallback_model",
        lambda model: "" if model.startswith("ollama/") else "ollama/gemma4:e2b-it-q4_K_M",
    )
    monkeypatch.setattr(LiteLlm, "generate_content_async", fake_generate)
    responses = _run(NaradLiteLlm(model="deepseek/deepseek-flash"), _request())
    assert calls == ["deepseek/deepseek-flash", "ollama/gemma4:e2b-it-q4_K_M"]
    assert [r.content.parts[0].text for r in responses] == ["Hello ", "there", "Hello there"]


# ── OpenMed detector ──────────────────────────────────────────────────────────

def test_openmed_names_are_redacted_and_cached_per_line(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_extract(text, **kwargs):
        seen.append(text)
        entities = []
        for match in re.finditer(r"Dr\. Rao|12/03/2026", text):
            label = "DATE" if match.group(0)[0].isdigit() else "NAME"
            entities.append(pytypes.SimpleNamespace(start=match.start(), end=match.end(), label=label))
        return pytypes.SimpleNamespace(entities=entities)

    monkeypatch.setenv("NARAD_PII_DETECTOR", "openmed")
    monkeypatch.setattr(gw._openmed, "_extract", fake_extract)
    monkeypatch.setattr(gw._openmed, "_cache", gw.OrderedDict())
    text = "Appointment with Dr. Rao on 12/03/2026\nstatic prompt line\n"
    redacted = gw.redact_text(text)
    assert redacted == "Appointment with <PERSON_1> on 12/03/2026\nstatic prompt line\n"
    gw.redact_text(text)
    assert len(seen) == 2  # two lines, scanned once each


# ── Plain LiteLLM calls ───────────────────────────────────────────────────────

def _fake_litellm(reply: str, captured: list) -> pytypes.ModuleType:
    module = pytypes.ModuleType("litellm")

    def completion(**kwargs):
        captured.append(kwargs)
        message = pytypes.SimpleNamespace(content=reply)
        return pytypes.SimpleNamespace(choices=[pytypes.SimpleNamespace(message=message)])

    def embedding(**kwargs):
        captured.append(kwargs)
        return {"data": [{"embedding": [0.0]} for _ in kwargs["input"]]}

    module.completion = completion
    module.embedding = embedding
    return module


def test_background_completion_is_pseudonymised_and_restored(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict] = []
    monkeypatch.setitem(sys.modules, "litellm", _fake_litellm("Style: short replies for <PERSON_1>", captured))
    messages = [{"role": "user", "content": "Asha Sharma prefers short replies"}]
    response = gw.completion(model="deepseek/deepseek-flash", messages=messages, narad_source="sankalpa")
    assert _pii_free(json.dumps(captured[0]["messages"]))
    assert "narad_source" not in captured[0]
    assert messages[0]["content"] == "Asha Sharma prefers short replies"
    assert response.choices[0].message.content == "Style: short replies for Asha Sharma"
    with pytest.raises(gw.PolicyBlocked):
        gw.completion(model="xai/grok-4.6", messages=messages)


def test_embeddings_are_guarded_by_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict] = []
    monkeypatch.setitem(sys.modules, "litellm", _fake_litellm("", captured))
    gw.embedding(model="deepseek/deepseek-embed", input=["Asha Sharma lab report"])
    assert captured[-1]["input"] == ["<PERSON_1> lab report"]
    assert gw.guard_texts("gemini", ["Asha Sharma"]) == ["Asha Sharma"]
    assert gw.guard_texts("mimo", ["Asha Sharma"]) == ["<PERSON_1>"]


# ── Chokepoint ────────────────────────────────────────────────────────────────

_ALLOWED_DIRECT_CALLERS = {
    "privacy_gateway.py",  # the chokepoint itself
    "kunji.py",  # provider key test: a fixed "ping", no family text
}


def test_no_direct_model_calls_outside_the_gateway() -> None:
    pattern = re.compile(r"litellm\.(a?completion|a?embedding)\(")
    offenders = []
    for path in _ROOT.rglob("*.py"):
        rel = path.relative_to(_ROOT).as_posix()
        if rel.startswith((".venv/", ".claude/", "node_modules/")) or "/test_" in rel or rel.startswith("test_"):
            continue
        if path.name in _ALLOWED_DIRECT_CALLERS:
            continue
        if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
            offenders.append(rel)
    assert offenders == []
