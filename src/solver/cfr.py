"""Vectorised CFR+ for two-player trees with a single showdown.

Hands are rows of two matrices supplied by the caller:

* ``joint[i, j]``: weight of hero hand i meeting villain hand j. This already
  includes both ranges and card removal (0 for hands that share a card).
* ``share[i, j]``: hero's share of the pot if hand i reaches showdown against
  hand j (1 / 0.5 / 0 on the river, all-in equity preflop).

Utilities are the chips each player ends with minus what they added during
the tree, so hero + villain utility always equals the starting pot (a
constant-sum game).
"""

from __future__ import annotations

import numpy as np

from .nodelock import NodeLock
from .tree import HERO, VILLAIN, Node


class CFRSolver:
    """CFR+ (regret floors at zero, linear averaging, alternating updates)."""

    def __init__(self, root: Node, joint: np.ndarray, share: np.ndarray, start_pot: float,
                 locks: dict[str, NodeLock] | None = None) -> None:
        if joint.shape != share.shape:
            raise ValueError("joint and share must have the same shape")
        self.root = root
        self.start_pot = start_pot
        self.joint = joint
        self.joint_share = joint * share
        self.num_hands = joint.shape
        self.total_weight = joint.sum()
        if self.total_weight <= 0:
            raise ValueError("the ranges never meet (total joint weight is zero)")

        self.nodes = {n.path: n for n in root.walk() if not n.is_terminal}
        locks = locks or {}
        unknown = set(locks) - set(self.nodes)
        if unknown:
            raise KeyError(f"locks refer to unknown nodes: {sorted(unknown)}")
        self.locks: dict[str, NodeLock] = {}
        self.regrets: dict[str, np.ndarray] = {}
        self.strategy_sum: dict[str, np.ndarray] = {}
        for path, node in self.nodes.items():
            shape = (self.num_hands[node.player], len(node.actions))
            lock = locks.get(path, NodeLock.unlocked(*shape))
            if lock.fixed.shape != shape:
                raise ValueError(f"lock at {path!r} has shape {lock.fixed.shape}, expected {shape}")
            self.locks[path] = lock
            self.regrets[path] = np.zeros(shape)
            self.strategy_sum[path] = np.zeros(shape)
        self.iterations = 0

    # ----- strategies -------------------------------------------------------

    def current_strategy(self, path: str) -> np.ndarray:
        """Regret-matching strategy at a node, combined with its lock."""
        lock = self.locks[path]
        positive = np.maximum(self.regrets[path], 0) * lock.allowed
        norm = positive.sum(axis=1, keepdims=True)
        uniform = lock.allowed / np.maximum(lock.allowed.sum(axis=1, keepdims=True), 1)
        matched = np.where(norm > 0, positive / np.where(norm > 0, norm, 1), uniform)
        return lock.fixed + lock.free[:, None] * matched

    def average_strategy(self, path: str) -> np.ndarray:
        """Average strategy (what CFR+ converges to); per hand, rows sum to 1."""
        total = self.strategy_sum[path]
        norm = total.sum(axis=1, keepdims=True)
        current = self.current_strategy(path)
        return np.where(norm > 0, total / np.where(norm > 0, norm, 1), current)

    # ----- terminal values --------------------------------------------------

    def _terminal_values(self, node: Node, reach: list[np.ndarray]) -> list[np.ndarray]:
        """Counterfactual values of each hand for both players at a terminal."""
        inv_h, inv_v = node.invested
        weight_h = self.joint @ reach[VILLAIN]
        weight_v = self.joint.T @ reach[HERO]
        if node.folder == HERO:
            return [-inv_h * weight_h, (node.pot - inv_v) * weight_v]
        if node.folder == VILLAIN:
            return [(node.pot - inv_h) * weight_h, -inv_v * weight_v]
        share_h = self.joint_share @ reach[VILLAIN]
        share_v = weight_v - self.joint_share.T @ reach[HERO]
        return [node.pot * share_h - inv_h * weight_h, node.pot * share_v - inv_v * weight_v]

    # ----- training ---------------------------------------------------------

    def _train(self, node: Node, reach: list[np.ndarray], updater: int, t: int) -> list[np.ndarray]:
        if node.is_terminal:
            return self._terminal_values(node, reach)
        p = node.player
        sigma = self.current_strategy(node.path)
        values = [np.zeros(self.num_hands[0]), np.zeros(self.num_hands[1])]
        action_values = np.zeros_like(sigma)
        for a, child in enumerate(node.children):
            child_reach = list(reach)
            child_reach[p] = reach[p] * sigma[:, a]
            child_values = self._train(child, child_reach, updater, t)
            action_values[:, a] = child_values[p]
            values[p] += sigma[:, a] * child_values[p]
            values[1 - p] += child_values[1 - p]
        if p == updater:
            regrets = self.regrets[node.path]
            regrets += action_values - values[p][:, None]
            np.maximum(regrets, 0, out=regrets)
            self.strategy_sum[node.path] += t * reach[p][:, None] * sigma
        return values

    def solve(self, iterations: int) -> "CFRSolver":
        """Run ``iterations`` more CFR+ iterations (both players each time)."""
        for _ in range(iterations):
            self.iterations += 1
            for updater in (HERO, VILLAIN):
                self._train(self.root, self._ones(), updater, self.iterations)
        return self

    # ----- evaluation -------------------------------------------------------

    def _values(self, node: Node, reach: list[np.ndarray], best_responder: int | None) -> list[np.ndarray]:
        """Values under the average strategies; ``best_responder`` (if set)
        instead maximises at its own nodes (respecting its locks)."""
        if node.is_terminal:
            return self._terminal_values(node, reach)
        p = node.player
        sigma = self.average_strategy(node.path)
        values = [np.zeros(self.num_hands[0]), np.zeros(self.num_hands[1])]
        action_values = np.zeros_like(sigma)
        for a, child in enumerate(node.children):
            child_reach = list(reach)
            if p != best_responder:
                child_reach[p] = reach[p] * sigma[:, a]
            child_values = self._values(child, child_reach, best_responder)
            action_values[:, a] = child_values[p]
            values[1 - p] += child_values[1 - p]
            if p != best_responder:
                values[p] += sigma[:, a] * child_values[p]
        if p == best_responder:
            lock = self.locks[node.path]
            best = np.where(lock.allowed, action_values, -np.inf).max(axis=1)
            best = np.where(np.isfinite(best), best, 0.0)
            values[p] = (lock.fixed * action_values).sum(axis=1) + lock.free * best
        return values

    def _ones(self) -> list[np.ndarray]:
        return [np.ones(self.num_hands[0]), np.ones(self.num_hands[1])]

    def expected_values(self) -> tuple[float, float]:
        """Average utility per unit of joint weight for (hero, villain), in BB."""
        values = self._values(self.root, self._ones(), None)
        return values[HERO].sum() / self.total_weight, values[VILLAIN].sum() / self.total_weight

    def exploitability(self) -> float:
        """How much (BB, averaged over both players) a best response gains.

        Approaches 0 as the solver converges. Locked strategy is part of the
        game, so locked players' best responses respect their locks.
        """
        br = [self._values(self.root, self._ones(), player)[player].sum() / self.total_weight
              for player in (HERO, VILLAIN)]
        return (br[HERO] + br[VILLAIN] - self.start_pot) / 2
