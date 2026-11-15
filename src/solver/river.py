"""Heads-up river spot, optionally node-locked to an opponent profile.

Profile locks (approximations, also returned with the result):

* **Facing a bet**, villain folds the weakest ``fold_to_cbet`` share of their
  river range and never folds the rest (who may call or raise freely). The
  profile stat is about flop c-bets, so this is a stand-in for river folds,
  and it ranks villain's *whole* river range, not just the hands that reach
  that node.
* **When betting**, villain's weaker half of the range bets (as a bluff) with
  probability ``river_bluff`` and checks otherwise; the stronger half is free.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..engine.cards import combo_name, parse_board
from ..engine.equity import river_showdown, showdown_share
from ..profiler.schema import Profile
from .cfr import CFRSolver
from .nodelock import NodeLock, lock_by_strength
from .ranges import combo_compatibility, expand_to_combos, parse_range
from .tree import HERO, VILLAIN, Node, build_river_tree

ITERATIONS = 400


@dataclass
class RiverSpot:
    board: str
    hero_range: str
    villain_range: str
    pot: float
    stack: float
    hero_first: bool = False
    bet_fractions: list[float] = field(default_factory=lambda: [0.75])


def _villain_locks(root: Node, strength: np.ndarray, weights: np.ndarray,
                   profile: Profile) -> tuple[dict[str, NodeLock], list[str]]:
    locks: dict[str, NodeLock] = {}
    notes: list[str] = []
    fold_share = profile.estimate("fold_to_cbet")
    bluff = profile.estimate("river_bluff")
    for node in root.walk():
        if node.player != VILLAIN:
            continue
        n_actions = len(node.actions)
        if node.actions[0] == "fold":
            locks[node.path] = lock_by_strength(strength, weights, [(0, fold_share)], n_actions,
                                                free_allowed=list(range(1, n_actions)))
            notes.append(f"{node.path}: weakest {fold_share:.0%} fold, rest never fold")
        else:
            bet_index = 1  # first bet size after "check"
            lock = lock_by_strength(strength, weights, [(0, 0.5)], n_actions)
            weak = lock.fixed[:, 0].copy()
            lock.fixed[:, 0] = weak * (1 - bluff)
            lock.fixed[:, bet_index] = weak * bluff
            locks[node.path] = lock
            notes.append(f"{node.path or 'first action'}: weaker half bets {bluff:.0%} of the time")
    return locks, notes


def _hero_reach(solver: CFRSolver, node: Node, reach: np.ndarray, out: dict[str, np.ndarray]) -> None:
    """Record, for every hero node, how much of each hero hand gets there."""
    if node.is_terminal:
        return
    if node.player == HERO:
        out[node.path] = reach
        strategy = solver.average_strategy(node.path)
        for a, child in enumerate(node.children):
            _hero_reach(solver, child, reach * strategy[:, a], out)
    else:
        for child in node.children:
            _hero_reach(solver, child, reach, out)


def _node_report(solver: CFRSolver, node: Node, combos: list, reach: np.ndarray) -> dict:
    """Hero's strategy at a node; ``overall`` weights hands by how often they get there."""
    strategy = solver.average_strategy(node.path)
    total = reach.sum()
    overall = reach @ strategy / total if total > 0 else np.zeros(len(node.actions))
    return {
        "path": node.path or "(start)",
        "actions": node.actions,
        "reached": round(float(total), 4),
        "overall": dict(zip(node.actions, np.round(overall, 4).tolist())),
        "hands": [{"combo": combo_name(c), "freqs": dict(zip(node.actions, np.round(s, 4).tolist()))}
                  for c, s, r in zip(combos, strategy, reach) if r > 1e-6],
    }


def solve_river(spot: RiverSpot, profile: Profile | None = None,
                iterations: int = ITERATIONS) -> dict:
    """Solve the river; with a ``profile`` the villain's play is node-locked to it."""
    board = parse_board(spot.board)
    if len(board) != 5:
        raise ValueError("the river needs exactly 5 board cards")
    if spot.pot <= 0 or spot.stack <= 0:
        raise ValueError("pot and stack must be positive")
    hero_combos, hero_w = expand_to_combos(parse_range(spot.hero_range), board)
    villain_combos, villain_w = expand_to_combos(parse_range(spot.villain_range), board)
    if not hero_combos or not villain_combos:
        raise ValueError("a range is empty once board cards are removed")

    hero_scores = river_showdown(hero_combos, board)
    villain_scores = river_showdown(villain_combos, board)
    joint = combo_compatibility(hero_combos, villain_combos) * np.outer(hero_w, villain_w)
    share = showdown_share(hero_scores, villain_scores)

    root = build_river_tree(spot.pot, spot.stack, HERO if spot.hero_first else VILLAIN,
                            spot.bet_fractions)
    locks, lock_notes = ({}, [])
    if profile is not None:
        locks, lock_notes = _villain_locks(root, villain_scores, villain_w, profile)
    solver = CFRSolver(root, joint, share, spot.pot, locks).solve(iterations)

    hero_ev, _ = solver.expected_values()
    reaches: dict[str, np.ndarray] = {}
    _hero_reach(solver, root, hero_w, reaches)
    return {
        "board": spot.board,
        "opponent": profile.name if profile else None,
        "locks": lock_notes,
        "hero_ev": round(hero_ev, 3),
        "exploitability": round(solver.exploitability(), 4),
        "hero_nodes": [_node_report(solver, solver.nodes[path], hero_combos, reach)
                       for path, reach in reaches.items()],
    }
