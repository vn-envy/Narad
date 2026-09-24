"""Streamed model text on its way to the client.

The supervisor loop (server.py) and every avatar run (avatar_agents.py) forward
partial model text as `text_delta` SSE events. Each stream source owns one
DeltaStream, so a <think> block split across one source's chunks never
swallows another source's text, and `text_reset` tells the client to drop text
that turned out to be routing chatter before a tool call.
"""
from __future__ import annotations

import json
from typing import Any


class ThinkingFilter:
    """Per-request stateful filter — strips chain-of-thought tag blocks across
    streaming chunks: <think>, <thinking>, <reasoning>, <reflection> (any case).

    DeepSeek streams chain-of-thought across many small SSE chunks; a single-pass
    regex can only match complete tags inside one chunk and silently passes partial
    tags through. This class buffers across calls so the tag boundaries are always
    found regardless of how the model slices its output.
    """
    _PAIRS = {
        "<think>":      "</think>",
        "<thinking>":   "</thinking>",
        "<reasoning>":  "</reasoning>",
        "<reflection>": "</reflection>",
    }
    _MAX_OPEN = max(len(t) for t in _PAIRS)

    def __init__(self) -> None:
        self._buf   = ""
        self._close = None  # closing tag we're inside of, or None

    def feed(self, chunk: str) -> str:
        """Feed one streaming chunk; return text that should reach the client."""
        self._buf += chunk
        out: list[str] = []

        while self._buf:
            low = self._buf.lower()
            if self._close is not None:
                idx = low.find(self._close)
                if idx == -1:
                    # Closing tag may be split — keep last N chars safe
                    safe = max(0, len(self._buf) - len(self._close))
                    self._buf = self._buf[safe:]
                    break
                self._buf   = self._buf[idx + len(self._close):]
                self._close = None
            else:
                # Earliest opening tag of any known pair
                first_idx: int = -1
                first_tag: str | None = None
                for tag in self._PAIRS:
                    i = low.find(tag)
                    if i != -1 and (first_idx == -1 or i < first_idx):
                        first_idx, first_tag = i, tag
                if first_tag is None:
                    # No opening tag — tail might be a partial "<think…" etc.
                    for tail in range(min(self._MAX_OPEN - 1, len(self._buf)), 0, -1):
                        frag = low[-tail:]
                        if any(t.startswith(frag) for t in self._PAIRS):
                            out.append(self._buf[:-tail])
                            self._buf = self._buf[-tail:]
                            return "".join(out)
                    out.append(self._buf)
                    self._buf = ""
                    break
                out.append(self._buf[:first_idx])
                self._buf   = self._buf[first_idx + len(first_tag):]
                self._close = self._PAIRS[first_tag]

        return "".join(out)

    def flush(self) -> str:
        """Return any buffered text after the stream ends (empty if mid-block)."""
        if self._close is not None:
            return ""
        result    = self._buf
        self._buf = ""
        return result


def visible_text(event: Any) -> str:
    """An event's text as the user may see it: Part.thought text never is."""
    content = getattr(event, "content", None)
    return "".join(
        part.text
        for part in getattr(content, "parts", None) or []
        if getattr(part, "text", None) and not getattr(part, "thought", False)
    )


class DeltaStream:
    """One source's streamed text as `text_delta` / `text_reset` SSE payloads.

    *extra* rides on every delta (avatars add `handoff`: whether their text is
    the answer being written or a draft the supervisor will combine).
    """

    def __init__(self, source: str, **extra: Any) -> None:
        self.source = source
        self.extra = extra
        self._filter = ThinkingFilter()
        self.dirty = False  # text reached the client since the last reset

    def feed(self, text: str) -> str | None:
        return self._delta(self._filter.feed(text)) if text else None

    def flush(self) -> str | None:
        """One model call finished with text: release what the filter held back."""
        tail = self._filter.flush()
        self._filter = ThinkingFilter()
        return self._delta(tail)

    def reset(self) -> str | None:
        """The model moved on (a tool call, a retry): the client drops the text."""
        self._filter = ThinkingFilter()
        if not self.dirty:
            return None
        self.dirty = False
        return json.dumps({"type": "text_reset", "data": {"source": self.source}})

    def _delta(self, text: str) -> str | None:
        if not text:
            return None
        self.dirty = True
        return json.dumps({
            "type": "text_delta",
            "data": {"source": self.source, "text": text, **self.extra},
        })
