"""Equity calculations.

* Preflop: a 169x169 table of all-in equity between hand classes, estimated by
  Monte Carlo (card removal handled exactly by sampling only disjoint combos).
  It takes a minute or two to build, so it is cached in ``data/``.
* River: exact showdown results between specific combos on a 5-card board.
"""

from __future__ import annotations

import logging
from concurrent.futures import ProcessPoolExecutor
from itertools import repeat
from pathlib import Path

import numpy as np

from .cards import NUM_CLASSES, Combo, combo_table
from .evaluator import evaluate

log = logging.getLogger(__name__)

DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "preflop_equity.npy"
SAMPLES_PER_MATCHUP = 2000  # ~1% standard error per matchup
_SEED = 2026


def _sample_row(i: int, samples: int, seed: int) -> np.ndarray:
    """Equity of class ``i`` against classes ``i..168`` (one Monte Carlo batch)."""
    rng = np.random.default_rng([seed, i])
    table, sizes = combo_table()
    others = np.repeat(np.arange(i, NUM_CLASSES), samples)
    hero = table[i, rng.integers(0, sizes[i], len(others))]
    villain = table[others, rng.integers(0, sizes[others])]
    disjoint = (hero[:, :, None] != villain[:, None, :]).all(axis=(1, 2))
    hero, villain, others = hero[disjoint], villain[disjoint], others[disjoint]

    keys = rng.random((len(others), 52))
    rows = np.arange(len(others))[:, None]
    keys[rows, hero] = 2.0
    keys[rows, villain] = 2.0
    board = np.argpartition(keys, 5, axis=1)[:, :5]

    hero_score = evaluate(np.hstack([hero, board]))
    villain_score = evaluate(np.hstack([villain, board]))
    result = (hero_score > villain_score) + 0.5 * (hero_score == villain_score)

    totals = np.bincount(others - i, weights=result, minlength=NUM_CLASSES - i)
    trials = np.bincount(others - i, minlength=NUM_CLASSES - i)
    return totals / np.maximum(trials, 1)


def build_preflop_equity(samples: int = SAMPLES_PER_MATCHUP, seed: int = _SEED,
                         workers: int | None = None) -> np.ndarray:
    """Estimate the 169x169 class-vs-class equity table (row = hero).

    Rows are sampled in parallel processes; each row has its own seed, so the
    result is the same whatever the number of workers.
    """
    equity = np.zeros((NUM_CLASSES, NUM_CLASSES))
    rows = range(NUM_CLASSES)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = pool.map(_sample_row, rows, repeat(samples), repeat(seed))
        for i, row in zip(rows, results):
            equity[i:, i] = 1.0 - row
            equity[i, i:] = row
    np.fill_diagonal(equity, 0.5)  # a class against itself is exactly 50% by symmetry
    return equity


def load_preflop_equity(cache: Path = DEFAULT_CACHE) -> np.ndarray:
    """Load the cached preflop equity table, building and saving it if missing."""
    if cache.exists():
        return np.load(cache)
    log.info("Building preflop equity table (one-off, takes a minute or two)...")
    equity = build_preflop_equity()
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.save(cache, equity)
    return equity


def river_showdown(combos: list[Combo], board: list[int]) -> np.ndarray:
    """Hand scores for each combo on a complete 5-card board."""
    if len(board) != 5:
        raise ValueError("River showdown needs a 5-card board")
    hands = np.array([list(c) + board for c in combos])
    return evaluate(hands)


def showdown_share(hero_scores: np.ndarray, villain_scores: np.ndarray) -> np.ndarray:
    """Hero's share of the pot for every hero/villain pairing: 1 win, 0.5 tie, 0 loss."""
    diff = hero_scores[:, None] - villain_scores[None, :]
    return (diff > 0) + 0.5 * (diff == 0)
