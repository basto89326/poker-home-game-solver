"""Prompt sent to the LLM to turn notes into a profile."""

from __future__ import annotations

import json

from .schema import CONFIDENCE_LEVELS, POPULATION_DEFAULTS, STAT_DEFINITIONS

SYSTEM_PROMPT = (
    "You convert a poker player's free-text notes into a structured profile. "
    "Reply with a single JSON object and nothing else: no prose, no code fences."
)

_EXAMPLE_SHAPE = {
    "name": "<player name>",
    "archetype": "<2-4 words, e.g. 'Calling station'>",
    "summary": "<one short sentence condensing the notes, in plain words>",
    "stats": {stat: {"estimate": 0.0, "confidence": "low|medium|high"} for stat in STAT_DEFINITIONS},
    "tendencies": ["<short sentence>"],
    "modifiers": [{"condition": "<e.g. When drinking>", "description": "<what changes>",
                   "adjustments": {"<stat>": 0.0}}],
}


def build_prompt(name: str, notes: str, session_notes: list[str]) -> str:
    """The full user prompt for one player's notes."""
    stat_lines = "\n".join(f"- {k}: {v}" for k, v in STAT_DEFINITIONS.items())
    defaults = ", ".join(f"{k}={v}" for k, v in POPULATION_DEFAULTS.items())
    sessions = "\n".join(f"- {s}" for s in session_notes) or "(none)"
    return f"""Build a poker profile for the player "{name}" from the notes below.

Rules:
1. Every stat is a frequency between 0 and 1, defined as:
{stat_lines}
2. Map descriptive language to numbers (e.g. "plays almost every hand" -> vpip about 0.5 or more).
3. Confidence ({", ".join(CONFIDENCE_LEVELS)}) reflects how directly the notes support the number:
   high = stated clearly or repeatedly, medium = reasonably implied, low = little or no evidence.
4. Where the notes say nothing about a stat, use these placeholder defaults with confidence "low": {defaults}
5. pfr must not exceed vpip, and three_bet + call_open must not exceed 1.
6. Tendencies and modifiers must come from the notes. Never invent tendencies the notes don't support.
   Use an empty list if there are none. Modifier adjustments are changes to a stat (e.g. +0.1).
7. Refer to the player by name or "they", never "he" or "she".

Reply with exactly this JSON shape:
{json.dumps(_EXAMPLE_SHAPE, indent=2)}

Main notes:
\"\"\"
{notes.strip() or "(empty)"}
\"\"\"

Session notes:
{sessions}
"""
