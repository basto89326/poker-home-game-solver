"""Predicted preflop play from a profile.

Each decision is turned into three shares of the player's *current* range
(what is left after their earlier actions this hand), assigned by hand
strength: the strongest hands raise, the next call, the weakest fold.

* No raise yet (unopened or limped): raise ``open_raise``, call (limp) up to
  ``vpip`` in total, fold the rest. If checking is free, "fold" becomes check.
* Facing one raise: raise ``three_bet``, call ``call_open``, fold the rest.
* Facing a re-raise (3-bet or more): fold ``fold_to_3bet``, raise up to
  ``three_bet``, call the rest.

These are simplifications: real players don't sort hands by a single
strength number, and the shares don't change with bet size or position.
"""

from __future__ import annotations

import numpy as np

from ..engine.cards import combo_counts
from ..profiler.schema import Profile
from .nodelock import lock_by_strength

FOLD, CALL, RAISE = 0, 1, 2


def action_shares(profile: Profile, raises: int) -> tuple[float, float, float]:
    """(fold, call, raise) shares of the current range for a decision."""
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


def predicted_strategy(profile: Profile, range_weights: np.ndarray, strength: np.ndarray,
                       raises: int, can_check: bool) -> np.ndarray:
    """(169, 3) fold/call/raise frequencies per hand class for this decision.

    When ``can_check`` is true the "call" column means check, and nobody folds.
    Classes outside the current range still get a row (placed by strength),
    so the grid can show where they would fall.
    """
    fold, call, raise_ = action_shares(profile, raises)
    weights = range_weights * combo_counts()
    if weights.sum() <= 0:
        raise ValueError(f"{profile.name} has no hands left in their range")
    lock = lock_by_strength(strength, weights, [(FOLD, fold), (CALL, call), (RAISE, raise_)], 3)
    freqs = lock.fixed
    # Rounding (or a class stronger than the whole range) can leave a little
    # mass unassigned; it belongs to the strongest action in use.
    top = RAISE if raise_ > 0 else CALL if call > 0 else FOLD
    freqs[:, top] += lock.free
    if can_check:
        freqs[:, CALL] += freqs[:, FOLD]
        freqs[:, FOLD] = 0.0
    return freqs


def update_range(range_weights: np.ndarray, freqs: np.ndarray, action: int) -> tuple[np.ndarray, bool]:
    """Narrow a range after an action. Returns (new range, fits_profile).

    If the profile says the player never takes that action, the range is
    kept unchanged and ``fits_profile`` is False.
    """
    narrowed = range_weights * freqs[:, action]
    if (narrowed * combo_counts()).sum() <= 1e-9:
        return range_weights, False
    return narrowed, True
