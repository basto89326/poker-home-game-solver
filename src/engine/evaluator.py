"""Vectorised 5-7 card hand evaluator.

:func:`evaluate` scores many hands at once with numpy. Higher score = better
hand; equal scores tie. A score is ``category * 13**5 + kickers`` where the
kickers are the ranks that matter for that category, most important first.
"""

from __future__ import annotations

import numpy as np

HIGH_CARD, PAIR, TWO_PAIR, TRIPS, STRAIGHT, FLUSH, FULL_HOUSE, QUADS, STRAIGHT_FLUSH = range(9)

_RANKS = np.arange(13)
_WEIGHTS = 13 ** np.arange(4, -1, -1)  # base-13 place values for 5 kickers
_CATEGORY_BASE = 13 ** 5
# Number of ranks that count as kickers for each category (straights use 1).
_KICKER_COUNT = np.array([5, 4, 3, 3, 1, 5, 2, 2, 1])
# Bit masks for the 10 straights, best first (ace-high down to the wheel).
_STRAIGHTS = [(0b11111 << (top - 4), top) for top in range(12, 3, -1)] + [((1 << 12) | 0b1111, 3)]


def _straight_high(bits: np.ndarray) -> np.ndarray:
    """Top rank of the best straight in each rank bitmask, or -1 if none."""
    high = np.full(bits.shape, -1)
    for mask, top in _STRAIGHTS:
        high = np.where((high < 0) & ((bits & mask) == mask), top, high)
    return high


def _ranks_by(key: np.ndarray) -> np.ndarray:
    """Rank indices sorted by descending ``key`` (ties broken by higher rank)."""
    return np.argsort(-(key * 16 + _RANKS), axis=1, kind="stable")


def _highest_present(present: np.ndarray) -> np.ndarray:
    """Highest rank whose ``present`` flag is set (rows must have at least one)."""
    return 12 - np.argmax(present[:, ::-1], axis=1)


def evaluate(cards: np.ndarray) -> np.ndarray:
    """Score hands given as an int array of shape ``(n, k)`` with 5 <= k <= 7."""
    cards = np.asarray(cards)
    if cards.ndim != 2 or not 5 <= cards.shape[1] <= 7:
        raise ValueError("cards must have shape (n, 5..7)")
    n = len(cards)
    ranks, suits = cards // 4, cards % 4
    rows = np.arange(n)

    counts = (ranks[:, :, None] == _RANKS).sum(axis=1)  # (n, 13)
    suit_counts = (suits[:, :, None] == np.arange(4)).sum(axis=1)  # (n, 4)
    rank_bits = ((counts > 0) << _RANKS).sum(axis=1)

    flush_suit = suit_counts.argmax(axis=1)
    has_flush = suit_counts[rows, flush_suit] >= 5
    in_flush = suits == flush_suit[:, None]
    flush_bits = np.where(in_flush, 1 << ranks, 0).sum(axis=1)
    flush_present = ((flush_bits[:, None] >> _RANKS) & 1).astype(bool)

    straight_high = _straight_high(rank_bits)
    straight_flush_high = np.where(has_flush, _straight_high(flush_bits), -1)

    max_count = counts.max(axis=1)
    num_pairs_plus = (counts >= 2).sum(axis=1)

    category = np.select(
        [
            straight_flush_high >= 0,
            max_count == 4,
            (max_count == 3) & (num_pairs_plus >= 2),
            has_flush,
            straight_high >= 0,
            max_count == 3,
            num_pairs_plus >= 2,
            max_count == 2,
        ],
        [STRAIGHT_FLUSH, QUADS, FULL_HOUSE, FLUSH, STRAIGHT, TRIPS, TWO_PAIR, PAIR],
        default=HIGH_CARD,
    )

    # Ranks ordered by (count, rank): right for high card, pair, trips and full house.
    kickers = _ranks_by(counts)[:, :5].copy()

    flush_rows = category == FLUSH
    kickers[flush_rows] = _ranks_by(flush_present[flush_rows].astype(int))[:, :5]

    kickers[:, 0] = np.where(category == STRAIGHT, straight_high, kickers[:, 0])
    kickers[:, 0] = np.where(category == STRAIGHT_FLUSH, straight_flush_high, kickers[:, 0])

    # Quads and two pair: the kicker is the best remaining rank of any count.
    present = counts > 0
    quads = category == QUADS
    if quads.any():
        others = present[quads].copy()
        others[np.arange(quads.sum()), kickers[quads, 0]] = False
        kickers[quads, 1] = _highest_present(others)
    two_pair = category == TWO_PAIR
    if two_pair.any():
        others = present[two_pair].copy()
        idx = np.arange(two_pair.sum())
        others[idx, kickers[two_pair, 0]] = False
        others[idx, kickers[two_pair, 1]] = False
        kickers[two_pair, 2] = _highest_present(others)

    used = np.arange(5) < _KICKER_COUNT[category][:, None]
    return category * _CATEGORY_BASE + (np.where(used, kickers, 0) * _WEIGHTS).sum(axis=1)


def category_of(score: np.ndarray | int) -> np.ndarray | int:
    """Hand category (e.g. :data:`FLUSH`) from a score."""
    return score // _CATEGORY_BASE
