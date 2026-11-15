"""Hand ranges: weights over the 169 classes (0..1 each).

Range strings use the usual shorthand, separated by commas:

* ``AA``, ``AKs``, ``AKo``; ``AK`` means both AKs and AKo
* ``TT+`` (TT and better pairs), ``ATs+`` (ATs, AJs, AQs, AKs), ``KTo+``
* ``AKs:0.5``: include at 50% weight
* ``*``: every hand
"""

from __future__ import annotations

import numpy as np

from ..engine.cards import (NUM_CLASSES, RANKS, Combo, class_combos, class_compatibility,
                            class_id, class_names, combo_counts)


def _expand_token(token: str) -> list[str]:
    """Expand one shorthand token (no weight) into class names."""
    if token == "*":
        return list(class_names())
    plus = token.endswith("+")
    base = token[:-1] if plus else token
    if len(base) not in (2, 3) or base[0].upper() not in RANKS or base[1].upper() not in RANKS:
        raise ValueError(f"Invalid range token: {token!r}")
    high, low = base[0].upper(), base[1].upper()
    suffix = base[2:].lower()
    if suffix not in ("", "s", "o") or (high == low and suffix):
        raise ValueError(f"Invalid range token: {token!r}")
    if RANKS.index(high) < RANKS.index(low):
        high, low = low, high
    if high == low:
        lows = RANKS[RANKS.index(high):] if plus else high
        return [r + r for r in lows]
    kickers = RANKS[RANKS.index(low):RANKS.index(high)] if plus else low
    suffixes = [suffix] if suffix else ["s", "o"]
    return [high + k + s for k in kickers for s in suffixes]


def parse_range(text: str) -> np.ndarray:
    """Parse a range string into 169 class weights."""
    weights = np.zeros(NUM_CLASSES)
    for raw in text.split(","):
        token = raw.strip()
        if not token:
            continue
        token, _, weight_text = token.partition(":")
        weight = float(weight_text) if weight_text else 1.0
        if not 0.0 <= weight <= 1.0:
            raise ValueError(f"Range weight must be between 0 and 1: {raw!r}")
        for name in _expand_token(token.strip()):
            weights[class_id(name)] = weight
    return weights


def preflop_strength(equity: np.ndarray) -> np.ndarray:
    """Each class's all-in equity against a random hand (card removal included)."""
    compat = class_compatibility()
    return (equity * compat).sum(axis=1) / compat.sum(axis=1)


def top_fraction(strength: np.ndarray, fraction: float) -> np.ndarray:
    """Class weights for the strongest ``fraction`` of all 1326 combos.

    The class on the boundary is included partially so the range holds
    exactly that share of combos.
    """
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("fraction must be between 0 and 1")
    counts = combo_counts()
    weights = np.zeros(NUM_CLASSES)
    remaining = fraction * counts.sum()
    for cid in np.argsort(-strength, kind="stable"):
        if remaining <= 0:
            break
        take = min(counts[cid], remaining)
        weights[cid] = take / counts[cid]
        remaining -= take
    return weights


def expand_to_combos(class_weights: np.ndarray, dead_cards: list[int]) -> tuple[list[Combo], np.ndarray]:
    """Specific combos (and their weights) in a range, minus any using a dead card."""
    dead = set(dead_cards)
    combos: list[Combo] = []
    weights: list[float] = []
    for cid, w in enumerate(class_weights):
        if w <= 0:
            continue
        for combo in class_combos()[cid]:
            if dead.isdisjoint(combo):
                combos.append(combo)
                weights.append(float(w))
    return combos, np.array(weights)


def combo_compatibility(hero: list[Combo], villain: list[Combo]) -> np.ndarray:
    """1 where a hero combo and villain combo share no card, else 0."""
    h = np.array(hero)
    v = np.array(villain)
    clash = (h[:, None, :, None] == v[None, :, None, :]).any(axis=(2, 3))
    return (~clash).astype(float)
