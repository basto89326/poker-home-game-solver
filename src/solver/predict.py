"""Predicted play from a profile.

Each decision is turned into shares of the player's *current* range (what is
left after their earlier actions this hand), assigned by hand strength: the
strongest hands raise, the next call, the weakest fold.

Preflop (strength = all-in equity against a random hand):

* No raise yet (unopened or limped): raise ``open_raise``, call (limp) up to
  ``vpip`` in total, fold the rest. If checking is free, "fold" becomes check.
* Facing one raise: raise ``three_bet``, call ``call_open``, fold the rest.
* Facing a re-raise (3-bet or more): fold ``fold_to_3bet``, raise up to
  ``three_bet``, call the rest.

After the flop (strength = :func:`board_strength` on the board so far):

* Nobody has bet: bet the strongest ``aggression`` share, check the rest. On
  the river, the weaker half of the range also bets (bluffs) ``river_bluff``
  of the time.
* Facing a bet or raise: fold the weakest ``fold_to_cbet`` share, raise
  ``aggression`` of the hands that continue, call the rest.

These are simplifications: real players don't sort hands by a single
strength number, and the shares don't change with bet size or position.
After the flop, ``fold_to_cbet`` stands in for folding to any bet, using
``aggression`` as a bet frequency is an assumption (the stat is the share of
bets and raises among non-check actions), and flop/turn strength is equity
against a random hand, so draws rank by their equity, not by how they play.
"""

from __future__ import annotations

import numpy as np

from ..engine.cards import NUM_CLASSES, combo_classes, combo_counts, live_combos
from ..engine.equity import board_strength
from ..profiler.schema import Profile
from .nodelock import lock_by_strength

FOLD, CALL, RAISE = 0, 1, 2
_WEAKER_HALF = 0.5  # share of the river range that may bluff


def action_shares(profile: Profile, raises: int) -> tuple[float, float, float]:
    """(fold, call, raise) shares of the current range for a preflop decision."""
    stat = profile.estimate
    if raises == 0:
        raise_share = stat("open_raise")
        call_share = max(stat("vpip") - raise_share, 0.0)
    elif raises == 1:
        raise_share, call_share = stat("three_bet"), stat("call_open")
    else:
        fold_share = stat("fold_to_3bet")
        raise_share = min(stat("three_bet"), 1 - fold_share)
        call_share = 1 - fold_share - raise_share
    raise_share = min(raise_share, 1.0)
    call_share = min(call_share, 1.0 - raise_share)
    return 1.0 - raise_share - call_share, call_share, raise_share


def postflop_shares(profile: Profile, raises: int) -> tuple[float, float, float]:
    """(fold-or-check, call, raise-or-bet) shares of the current range after the flop."""
    aggression = min(max(profile.estimate("aggression"), 0.0), 1.0)
    if raises == 0:
        return 1.0 - aggression, 0.0, aggression
    fold = min(max(profile.estimate("fold_to_cbet"), 0.0), 1.0)
    raise_share = aggression * (1 - fold)
    return fold, 1.0 - fold - raise_share, raise_share


def _by_strength(shares: tuple[float, float, float], weights: np.ndarray, strength: np.ndarray,
                 can_check: bool, name: str) -> np.ndarray:
    """Assign (fold, call, raise) shares to hands, weakest first."""
    fold, call, raise_ = shares
    if weights.sum() <= 0:
        raise ValueError(f"{name} has no hands left in their range")
    lock = lock_by_strength(strength, weights, [(FOLD, fold), (CALL, call), (RAISE, raise_)], 3)
    freqs = lock.fixed
    # Rounding (or a hand stronger than the whole range) can leave a little
    # mass unassigned; it belongs to the strongest action in use.
    top = RAISE if raise_ > 0 else CALL if call > 0 else FOLD
    freqs[:, top] += lock.free
    if can_check:
        freqs[:, CALL] += freqs[:, FOLD]
        freqs[:, FOLD] = 0.0
    return freqs


def predicted_strategy(profile: Profile, range_weights: np.ndarray, strength: np.ndarray,
                       raises: int, can_check: bool) -> np.ndarray:
    """(169, 3) preflop fold/call/raise frequencies per hand class.

    When ``can_check`` is true the "call" column means check, and nobody folds.
    Classes outside the current range still get a row (placed by strength),
    so the grid can show where they would fall.
    """
    weights = range_weights * combo_counts()
    return _by_strength(action_shares(profile, raises), weights, strength, can_check, profile.name)


def postflop_strategy(profile: Profile, combo_range: np.ndarray, board: list[int], raises: int,
                      can_check: bool) -> np.ndarray:
    """(1326, 3) fold/call/raise frequencies per combo after the flop (see the module docstring)."""
    strength = board_strength(tuple(board))
    weights = combo_range * live_combos(board)
    freqs = _by_strength(postflop_shares(profile, raises), weights, strength, can_check, profile.name)
    bluff = profile.estimate("river_bluff")
    if len(board) == 5 and raises == 0 and bluff > 0:
        weak = lock_by_strength(strength, weights, [(FOLD, _WEAKER_HALF)], 1).fixed[:, FOLD]
        moved = np.minimum(freqs[:, CALL], weak * bluff)
        freqs[:, CALL] -= moved
        freqs[:, RAISE] += moved
    return freqs


def class_average(combo_values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Weighted average of per-combo values (1326 or 1326 x k) for each of the 169 classes.

    Classes whose combos all have zero weight get 0.
    """
    classes = combo_classes()
    total = np.bincount(classes, weights=weights, minlength=NUM_CLASSES)
    safe = np.where(total > 0, total, 1)
    if combo_values.ndim == 1:
        return np.bincount(classes, weights=combo_values * weights, minlength=NUM_CLASSES) / safe
    return np.stack([np.bincount(classes, weights=combo_values[:, a] * weights, minlength=NUM_CLASSES) / safe
                     for a in range(combo_values.shape[1])], axis=1)


def predict_combos(profile: Profile, combo_range: np.ndarray, board: list[int], raises: int,
                   can_check: bool, preflop_strength: np.ndarray) -> np.ndarray:
    """(1326, 3) predicted frequencies per combo, preflop or after the flop."""
    if board:
        return postflop_strategy(profile, combo_range, board, raises, can_check)
    class_range = class_average(combo_range, np.ones(len(combo_range)))
    return predicted_strategy(profile, class_range, preflop_strength, raises, can_check)[combo_classes()]


def update_range(range_weights: np.ndarray, freqs: np.ndarray, action: int) -> tuple[np.ndarray, bool]:
    """Narrow a range after an action. Returns (new range, fits_profile).

    If the profile says the player never takes that action, the range is
    kept unchanged and ``fits_profile`` is False.
    """
    narrowed = range_weights * freqs[:, action]
    if narrowed.sum() <= 1e-9:
        return range_weights, False
    return narrowed, True
