"""Node-locking: fixing part of a player's strategy at a node.

A :class:`NodeLock` fixes, per hand, some probability mass on specific actions.
The rest of each hand's mass ("free" mass) is still chosen by the solver, but
only among the ``allowed`` actions. That covers both a full lock (all mass
fixed) and partial locks such as "these hands never fold, but may call or
raise as they like".

Profiles describe frequencies over a whole range ("folds to a 3-bet 20% of
the time"), so :func:`lock_by_strength` turns range-level frequencies into
per-hand locks by assigning actions to hands in order of strength: weakest
hands first.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class NodeLock:
    """``fixed[i, a]``: mass hand i must put on action a (rows sum to <= 1).

    ``allowed[i, a]``: whether hand i's free mass may use action a.
    """

    fixed: np.ndarray
    allowed: np.ndarray

    def __post_init__(self) -> None:
        if self.fixed.shape != self.allowed.shape:
            raise ValueError("fixed and allowed must have the same shape")
        if (self.fixed < -1e-9).any() or (self.fixed.sum(axis=1) > 1 + 1e-9).any():
            raise ValueError("fixed rows must be non-negative and sum to at most 1")
        free = 1 - self.fixed.sum(axis=1)
        if ((free > 1e-9) & ~self.allowed.any(axis=1)).any():
            raise ValueError("a hand with free mass needs at least one allowed action")

    @property
    def free(self) -> np.ndarray:
        """Unlocked probability mass per hand."""
        return np.clip(1 - self.fixed.sum(axis=1), 0, 1)

    @classmethod
    def unlocked(cls, num_hands: int, num_actions: int) -> "NodeLock":
        """A lock that fixes nothing and allows everything."""
        return cls(np.zeros((num_hands, num_actions)), np.ones((num_hands, num_actions), bool))


def strength_intervals(strength: np.ndarray, weights: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Each hand's slice ``[lo, hi)`` of the range, weakest hands at 0.

    Slices are fractions of total weight, so a hand with 4% of the range's
    weight gets a slice 0.04 wide.
    """
    total = weights.sum()
    if total <= 0:
        raise ValueError("range has no weight")
    order = np.argsort(strength, kind="stable")
    cum = np.cumsum(weights[order]) / total
    hi = np.empty_like(cum)
    hi[order] = cum
    lo = hi - weights / total
    return lo, hi


def lock_by_strength(strength: np.ndarray, weights: np.ndarray,
                     segments: list[tuple[int | None, float]], num_actions: int,
                     free_allowed: list[int] | None = None) -> NodeLock:
    """Lock actions onto slices of the range, from the weakest hands upward.

    ``segments`` is a list of ``(action, share)``: e.g. ``[(0, 0.2)]`` means
    the weakest 20% of the range (by weight) takes action 0. ``action=None``
    leaves that slice free. Anything above the last segment is free. A hand
    straddling a boundary is split proportionally. ``free_allowed`` limits
    which actions free mass may use (default: all).
    """
    shares = [share for _, share in segments]
    if any(s < 0 for s in shares) or sum(shares) > 1 + 1e-9:
        raise ValueError("segment shares must be non-negative and sum to at most 1")
    lo, hi = strength_intervals(strength, weights)
    width = hi - lo
    fixed = np.zeros((len(strength), num_actions))
    start = 0.0
    for action, share in segments:
        end = start + share
        if action is not None:
            overlap = np.clip(np.minimum(hi, end) - np.maximum(lo, start), 0, None)
            # Zero-weight hands have no width: lock them by where they sit.
            point = (lo >= start) & (lo < end)
            fixed[:, action] += np.where(width > 0, overlap / np.where(width > 0, width, 1), point)
        start = end
    allowed = np.zeros((len(strength), num_actions), bool)
    allowed[:, free_allowed if free_allowed is not None else slice(None)] = True
    return NodeLock(np.clip(fixed, 0, 1), allowed)
