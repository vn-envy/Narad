"""
Template selector for beautiful-html-templates.

Reads the index.json from the templates repo and scores each template
against user-specified mood/tone/formality/scheme criteria.

Usage:
    from template_selector import rank
    candidates = rank(mood="editorial", tone="serious", formality="high")
    # Returns top-3 matches with scores and reasoning
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Default location — override via BEAUTIFUL_TEMPLATES_PATH env var
_DEFAULT_TEMPLATES_DIR = (
    Path(__file__).parent / "templates" / "beautiful-html-templates"
)


@dataclass
class TemplateMatch:
    name: str
    score: float
    reasoning: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            f"**{self.name}** (score: {self.score:.1f})",
            f"  Mood: {self.metadata.get('mood', '—')} | "
            f"Tone: {self.metadata.get('tone', '—')} | "
            f"Best for: {self.metadata.get('best_for', '—')}",
        ]
        if self.reasoning:
            lines.append("  Match: " + "; ".join(self.reasoning))
        return "\n".join(lines)


def _load_index(templates_dir: str | None = None) -> list[dict[str, Any]]:
    """Load templates/index.json. Returns empty list if not found."""
    base = Path(templates_dir or os.environ.get("BEAUTIFUL_TEMPLATES_PATH", "") or _DEFAULT_TEMPLATES_DIR)
    index_path = base / "index.json"
    if not index_path.exists():
        return []
    with open(index_path, encoding="utf-8") as f:
        data = json.load(f)
    # Support both {templates: [...]} and flat list formats
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "templates" in data:
        return data["templates"]
    return []


def _normalise(value: "str | list | None") -> str:
    """Normalise string or list field to a single lowercase string for matching."""
    if isinstance(value, list):
        return " ".join(v.lower().strip() for v in value)
    return (value or "").lower().strip()


def _in_field(needle: str, field_value: "str | list | None") -> bool:
    """Return True if needle matches any element in a list field or substring of a string field."""
    needle = needle.lower().strip()
    if isinstance(field_value, list):
        return any(needle == v.lower().strip() or needle in v.lower() for v in field_value)
    return needle in _normalise(field_value)


def _score_template(
    template: dict[str, Any],
    mood: str,
    tone: str,
    formality: str,
    scheme: str,
    avoid: str,
) -> tuple[float, list[str]]:
    """Score a single template against the request criteria."""
    score = 0.0
    reasoning: list[str] = []

    t_mood = template.get("mood")
    t_tone = template.get("tone")
    t_formality = _normalise(template.get("formality"))
    t_scheme = _normalise(template.get("scheme"))
    t_best_for = _normalise(template.get("best_for", ""))
    t_avoid_for = _normalise(template.get("avoid_for", ""))

    if mood:
        mood_norm = mood.lower().strip()
        if isinstance(t_mood, list) and mood_norm in [v.lower().strip() for v in t_mood]:
            score += 2.0
            reasoning.append(f"mood matches '{mood}'")
        elif _in_field(mood_norm, t_mood):
            score += 1.0
            reasoning.append(f"mood partially matches '{mood}'")

    if tone:
        tone_norm = tone.lower().strip()
        if isinstance(t_tone, list) and tone_norm in [v.lower().strip() for v in t_tone]:
            score += 1.5
            reasoning.append(f"tone matches '{tone}'")
        elif _in_field(tone_norm, t_tone):
            score += 0.75
            reasoning.append(f"tone partially matches '{tone}'")

    if formality and t_formality == formality.lower().strip():
        score += 1.0
        reasoning.append(f"formality matches '{formality}'")

    if scheme and t_scheme == scheme.lower().strip():
        score += 0.5
        reasoning.append(f"color scheme matches '{scheme}'")

    if avoid:
        avoid_norm = avoid.lower().strip()
        if _in_field(avoid_norm, t_mood) or _in_field(avoid_norm, t_tone) or avoid_norm in t_best_for:
            score -= 3.0
            reasoning.append(f"PENALISED: '{avoid}' overlaps with template characteristics")
        if avoid_norm in t_avoid_for:
            score += 0.5

    return score, reasoning


def rank(
    mood: str = "",
    tone: str = "",
    formality: str = "",
    scheme: str = "",
    avoid: str = "",
    top_n: int = 3,
    templates_dir: str | None = None,
) -> list[TemplateMatch]:
    """
    Rank templates by match quality.

    Args:
        mood:       Emotional quality — 'editorial', 'playful', 'bold', 'minimal', etc.
        tone:       Tone register — 'serious', 'warm', 'energetic', 'calm', etc.
        formality:  'high', 'medium', 'low'
        scheme:     Color scheme — 'light', 'dark', 'monochrome', 'colorful'
        avoid:      Characteristics to penalise heavily
        top_n:      How many results to return (default 3)
        templates_dir: Override path to beautiful-html-templates directory

    Returns:
        List of TemplateMatch objects, highest score first.
    """
    templates = _load_index(templates_dir)

    if not templates:
        # Return synthetic fallback entries when repo isn't installed
        return _fallback_matches(mood, tone, top_n)

    scored: list[TemplateMatch] = []
    for t in templates:
        name = t.get("name") or t.get("id") or "unknown"
        score, reasoning = _score_template(t, mood, tone, formality, scheme, avoid)
        scored.append(TemplateMatch(
            name=name,
            score=score,
            reasoning=reasoning,
            metadata=t,
        ))

    scored.sort(key=lambda m: m.score, reverse=True)
    return scored[:top_n]


def format_candidates(matches: list[TemplateMatch]) -> str:
    """Format ranked candidates as a numbered presentation for the user."""
    if not matches:
        return "No templates found. Consider a custom design."
    lines = ["Here are 3 template candidates:\n"]
    for i, m in enumerate(matches, 1):
        lines.append(f"{i}. {m.summary()}\n")
    lines.append('Pick 1, 2, or 3 — or say "none of these" for a fully custom design.')
    return "\n".join(lines)


def _fallback_matches(mood: str, tone: str, top_n: int) -> list[TemplateMatch]:
    """Synthetic fallbacks when the templates repo is not installed locally."""
    fallbacks = [
        TemplateMatch(
            name="editorial-minimal",
            score=2.0 if "editorial" in mood.lower() or "minimal" in mood.lower() else 1.0,
            reasoning=["fallback: beautiful-html-templates not installed locally"],
            metadata={"mood": "editorial", "tone": "serious", "formality": "high",
                      "best_for": "product pitches, reports, portfolios"},
        ),
        TemplateMatch(
            name="bold-hero",
            score=2.0 if "bold" in mood.lower() else 0.8,
            reasoning=["fallback: beautiful-html-templates not installed locally"],
            metadata={"mood": "bold", "tone": "energetic", "formality": "low",
                      "best_for": "startup pitches, product launches"},
        ),
        TemplateMatch(
            name="minimal-clean",
            score=1.5,
            reasoning=["fallback: beautiful-html-templates not installed locally"],
            metadata={"mood": "minimal", "tone": "calm", "formality": "medium",
                      "best_for": "general purpose presentations"},
        ),
    ]
    fallbacks.sort(key=lambda m: m.score, reverse=True)
    return fallbacks[:top_n]


if __name__ == "__main__":
    import sys
    args = dict(arg.split("=") for arg in sys.argv[1:] if "=" in arg)
    results = rank(**args)
    print(format_candidates(results))
