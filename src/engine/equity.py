"""Equity calculations.

* Preflop: a 169x169 table of all-in equity between hand classes, estimated by
  Monte Carlo (card removal handled exactly by sampling only disjoint combos).
  It takes a minute or two to build, so it is cached in ``data/``.
* River: exact showdown results between specific combos on a 5-card board.
* Board strength: how strong every combo is on a flop, turn or river, used to
  rank a player's range after the flop.
"""

from __future__ import annotations

import logging
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
from itertools import repeat
from pathlib import Path

import numpy as np

from .cards import NUM_CLASSES, Combo, all_combos, combo_table, live_combos
from .evaluator import evaluate

log = logging.getLogger(__name__)

DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "preflop_equity.npy"
SAMPLES_PER_MATCHUP = 2000  # ~1% standard error per matchup
_SEED = 2026
STRENGTH_SAMPLES = 200  # deals per combo: ~3.5% standard error on its equity
_STRENGTH_CHUNK_ROWS = 40_000  # rows simulated at once, to keep memory small


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


@lru_cache(maxsize=32)
def board_strength(board: tuple[int, ...], samples: int = STRENGTH_SAMPLES) -> np.ndarray:
    """(1326,) strength of every combo on a 3-5 card board (0 for combos the board blocks).

    River: the combo's showdown score (only the order matters). Flop/turn: its
    all-in equity against one random hand with the rest of the board dealt at
    random, estimated by Monte Carlo (``samples`` deals per combo, seeded by
    the board so the same board always gives the same answer).
    """
    if len(board) not in (3, 4, 5):
        raise ValueError("board strength needs 3 to 5 board cards")
    combos = all_combos()
    live = live_combos(list(board))
    strength = np.zeros(len(combos))
    if len(board) == 5:
        strength[live] = evaluate(np.hstack([combos[live], np.tile(board, (live.sum(), 1))]))
        return strength
    rng = np.random.default_rng([_SEED, *board])
    need = 2 + 5 - len(board)  # opponent's two cards plus the rest of the board
    ids = np.flatnonzero(live)
    for chunk in np.array_split(ids, max(1, len(ids) * samples // _STRENGTH_CHUNK_ROWS)):
        hero_ids = np.repeat(chunk, samples)
        rows = np.arange(len(hero_ids))[:, None]
        hero = combos[hero_ids]
        keys = rng.random((len(hero_ids), 52))
        keys[rows, hero] = 2.0
        keys[:, list(board)] = 2.0
        drawn = np.argpartition(keys, need, axis=1)[:, :need]
        full_board = np.hstack([np.tile(board, (len(hero_ids), 1)), drawn[:, 2:]])
        hero_score = evaluate(np.hstack([hero, full_board]))
        opp_score = evaluate(np.hstack([drawn[:, :2], full_board]))
        result = (hero_score > opp_score) + 0.5 * (hero_score == opp_score)
        strength[chunk] = result.reshape(len(chunk), samples).mean(axis=1)
    return strength


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
