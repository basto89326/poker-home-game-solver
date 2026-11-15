"""Player profile data model and validation.

Every stat is a frequency between 0 and 1 with a confidence level. The exact
definitions below are given to the LLM and used by the solver, so the numbers
mean the same thing in both places.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

CONFIDENCE_LEVELS = ("low", "medium", "high")

STAT_DEFINITIONS: dict[str, str] = {
    "vpip": "Share of all hands where they voluntarily put money in preflop.",
    "pfr": "Share of all hands where they raise preflop.",
    "open_raise": "When everyone folds to them before the flop, how often they open with a raise.",
    "fold_to_3bet": "After opening, how often they fold when re-raised (3-bet).",
    "fold_to_cbet": "How often they fold when someone bets into them after the flop.",
    "aggression": ("Of their postflop actions other than checking, the share that are bets or "
                   "raises (not calls)."),
    "river_bluff": "When holding a weak hand on the river, how often they bet anyway.",
}

# Rough placeholder guesses for a typical loose home game. These are NOT
# measured population stats; they are only used when the notes say nothing.
POPULATION_DEFAULTS: dict[str, float] = {
    "vpip": 0.40,
    "pfr": 0.12,
    "open_raise": 0.25,
    "fold_to_3bet": 0.45,
    "fold_to_cbet": 0.45,
    "aggression": 0.30,
    "river_bluff": 0.15,
}


class ProfileError(ValueError):
    """Raised when profile data is missing fields or has invalid values."""


@dataclass
class Stat:
    estimate: float
    confidence: str

    @classmethod
    def from_dict(cls, name: str, data: Any) -> "Stat":
        if not isinstance(data, dict):
            raise ProfileError(f"stat {name!r} must be an object")
        try:
            estimate = float(data["estimate"])
        except (KeyError, TypeError, ValueError):
            raise ProfileError(f"stat {name!r} needs a numeric 'estimate'") from None
        if not 0.0 <= estimate <= 1.0:
            raise ProfileError(f"stat {name!r} estimate must be between 0 and 1, got {estimate}")
        confidence = str(data.get("confidence", "")).lower()
        if confidence not in CONFIDENCE_LEVELS:
            raise ProfileError(f"stat {name!r} confidence must be one of {CONFIDENCE_LEVELS}")
        return cls(estimate, confidence)


@dataclass
class Modifier:
    """A situational change, e.g. "after a few drinks: plays even more hands"."""

    condition: str
    description: str
    adjustments: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any) -> "Modifier":
        if not isinstance(data, dict) or not data.get("condition") or not data.get("description"):
            raise ProfileError("each modifier needs a 'condition' and a 'description'")
        adjustments = data.get("adjustments") or {}
        if not isinstance(adjustments, dict):
            raise ProfileError("modifier 'adjustments' must be an object")
        for stat, delta in adjustments.items():
            if stat not in STAT_DEFINITIONS:
                raise ProfileError(f"modifier adjusts unknown stat {stat!r}")
            if not isinstance(delta, (int, float)) or not -1.0 <= delta <= 1.0:
                raise ProfileError(f"modifier adjustment for {stat!r} must be a number in [-1, 1]")
        return cls(str(data["condition"]), str(data["description"]),
                   {k: float(v) for k, v in adjustments.items()})


@dataclass
class Profile:
    name: str
    archetype: str
    summary: str
    stats: dict[str, Stat]
    tendencies: list[str]
    modifiers: list[Modifier]
    last_updated: str

    def estimate(self, stat: str) -> float:
        return self.stats[stat].estimate

    @classmethod
    def from_dict(cls, data: Any) -> "Profile":
        """Validate raw (e.g. LLM or JSON file) data and build a profile."""
        if not isinstance(data, dict):
            raise ProfileError("profile must be a JSON object")
        missing = [k for k in ("name", "archetype", "stats") if not data.get(k)]
        if missing:
            raise ProfileError(f"profile is missing: {', '.join(missing)}")
        raw_stats = data["stats"]
        if not isinstance(raw_stats, dict):
            raise ProfileError("'stats' must be an object")
        absent = [s for s in STAT_DEFINITIONS if s not in raw_stats]
        if absent:
            raise ProfileError(f"profile is missing stats: {', '.join(absent)}")
        stats = {s: Stat.from_dict(s, raw_stats[s]) for s in STAT_DEFINITIONS}
        if stats["pfr"].estimate > stats["vpip"].estimate:
            raise ProfileError("pfr cannot be higher than vpip")
        tendencies = data.get("tendencies") or []
        if not isinstance(tendencies, list) or not all(isinstance(t, str) for t in tendencies):
            raise ProfileError("'tendencies' must be a list of strings")
        modifiers = data.get("modifiers") or []
        if not isinstance(modifiers, list):
            raise ProfileError("'modifiers' must be a list")
        last_updated = str(data.get("last_updated", ""))
        if last_updated and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", last_updated):
            raise ProfileError("'last_updated' must be a YYYY-MM-DD date")
        return cls(
            name=str(data["name"]),
            archetype=str(data["archetype"]),
            summary=str(data.get("summary", "")),
            stats=stats,
            tendencies=list(tendencies),
            modifiers=[Modifier.from_dict(m) for m in modifiers],
            last_updated=last_updated,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_profile(name: str) -> Profile:
    """A low-confidence profile built from :data:`POPULATION_DEFAULTS` only."""
    return Profile(
        name=name,
        archetype="Unknown (population defaults)",
        summary="",
        stats={s: Stat(v, "low") for s, v in POPULATION_DEFAULTS.items()},
        tendencies=[],
        modifiers=[],
        last_updated="",
    )
