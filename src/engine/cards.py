"""Cards, combos and the 169 starting-hand classes.

A card is an int 0..51: ``rank * 4 + suit`` where rank 0 = deuce .. 12 = ace
and suit 0..3 = c, d, h, s.

The 169 hand classes are laid out like the 13x13 grid in the UI: row/column 0
is the ace. The diagonal holds pairs, cells above it (column > row) are suited
hands and cells below it are offsuit hands. Class id = row * 13 + column.
"""

from __future__ import annotations

from functools import lru_cache
from itertools import combinations

import numpy as np

RANKS = "23456789TJQKA"
SUITS = "cdhs"
NUM_CLASSES = 169
NUM_COMBOS = 1326

Combo = tuple[int, int]


def parse_card(text: str) -> int:
    """Parse a card like ``"As"`` or ``"td"`` into its int id."""
    if len(text) != 2 or text[0].upper() not in RANKS or text[1].lower() not in SUITS:
        raise ValueError(f"Invalid card: {text!r}")
    return RANKS.index(text[0].upper()) * 4 + SUITS.index(text[1].lower())


def card_str(card: int) -> str:
    """Format a card id as text, e.g. ``51 -> "As"``."""
    return RANKS[card // 4] + SUITS[card % 4]


def parse_board(text: str) -> list[int]:
    """Parse a board like ``"Ks7d2c9h3s"`` or ``"Ks 7d 2c"``; rejects duplicates."""
    compact = text.replace(" ", "").replace(",", "")
    if len(compact) % 2:
        raise ValueError(f"Invalid board: {text!r}")
    cards = [parse_card(compact[i:i + 2]) for i in range(0, len(compact), 2)]
    if len(set(cards)) != len(cards):
        raise ValueError(f"Duplicate card in board: {text!r}")
    return cards


def _grid_rank(index: int) -> int:
    """Grid row/column (0 = ace) to card rank (12 = ace)."""
    return 12 - index


def class_name(class_id: int) -> str:
    """Name of a hand class, e.g. ``0 -> "AA"``, ``1 -> "AKs"``, ``13 -> "AKo"``."""
    row, col = divmod(class_id, 13)
    high, low = RANKS[_grid_rank(min(row, col))], RANKS[_grid_rank(max(row, col))]
    if row == col:
        return high + low
    return high + low + ("s" if col > row else "o")


@lru_cache(maxsize=1)
def class_names() -> tuple[str, ...]:
    """All 169 class names in class-id order."""
    return tuple(class_name(i) for i in range(NUM_CLASSES))


def class_id(name: str) -> int:
    """Inverse of :func:`class_name`; accepts e.g. ``"AKs"``, ``"ako"``, ``"QQ"``."""
    try:
        return class_names().index(name[:2].upper() + name[2:].lower())
    except ValueError:
        raise ValueError(f"Unknown hand class: {name!r}") from None


@lru_cache(maxsize=1)
def class_combos() -> tuple[tuple[Combo, ...], ...]:
    """The specific two-card combos in each class (6 pairs, 4 suited, 12 offsuit)."""
    result = []
    for cid in range(NUM_CLASSES):
        row, col = divmod(cid, 13)
        r1, r2 = _grid_rank(row), _grid_rank(col)
        if row == col:
            combos = [(r1 * 4 + a, r1 * 4 + b) for a, b in combinations(range(4), 2)]
        elif col > row:
            combos = [(r1 * 4 + s, r2 * 4 + s) for s in range(4)]
        else:
            combos = [(r2 * 4 + a, r1 * 4 + b) for a in range(4) for b in range(4) if a != b]
        result.append(tuple(combos))
    return tuple(result)


def combo_counts() -> np.ndarray:
    """Number of combos per class (length 169)."""
    return np.array([len(c) for c in class_combos()], dtype=float)


@lru_cache(maxsize=1)
def all_combos() -> np.ndarray:
    """All 1326 combos as a (1326, 2) card array, grouped by class in class-id order."""
    combos = np.array([c for combos in class_combos() for c in combos], dtype=np.int64)
    combos.setflags(write=False)
    return combos


@lru_cache(maxsize=1)
def combo_classes() -> np.ndarray:
    """Class id of each combo in :func:`all_combos` order (length 1326)."""
    classes = np.repeat(np.arange(NUM_CLASSES), combo_counts().astype(int))
    classes.setflags(write=False)
    return classes


def live_combos(board: list[int]) -> np.ndarray:
    """(1326,) True for combos that share no card with ``board``."""
    dead = np.zeros(52, bool)
    dead[list(board)] = True
    return ~dead[all_combos()].any(axis=1)


@lru_cache(maxsize=1)
def class_compatibility() -> np.ndarray:
    """169x169 matrix: number of card-disjoint combo pairs between two classes.

    This is the exact joint weight of two full classes after card removal,
    e.g. AA vs AA = 6 (each AA combo is disjoint from exactly one other).
    """
    masks = [[(1 << a) | (1 << b) for a, b in combos] for combos in class_combos()]
    compat = np.zeros((NUM_CLASSES, NUM_CLASSES))
    for i in range(NUM_CLASSES):
        for j in range(i, NUM_CLASSES):
            n = sum(1 for m1 in masks[i] for m2 in masks[j] if not m1 & m2)
            compat[i, j] = compat[j, i] = n
    compat.setflags(write=False)
    return compat


def combo_name(combo: Combo) -> str:
    """Format a combo as text, high card first, e.g. ``"AsJs"``."""
    a, b = sorted(combo, reverse=True)
    return card_str(a) + card_str(b)


@lru_cache(maxsize=1)
def combo_table() -> tuple[np.ndarray, np.ndarray]:
    """Combos of every class padded to (169, 12, 2), plus the combo count per class.

    Lets numpy pick random combos for many classes at once.
    """
    table = np.zeros((NUM_CLASSES, 12, 2), dtype=np.int64)
    sizes = np.zeros(NUM_CLASSES, dtype=np.int64)
    for cid, combos in enumerate(class_combos()):
        table[cid, : len(combos)] = combos
        sizes[cid] = len(combos)
    table.setflags(write=False)
    return table, sizes
