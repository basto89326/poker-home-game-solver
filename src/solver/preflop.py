"""Preflop spot: hero facing a single open raise.

Model (deliberately simple; every assumption is listed here and in the UI):

* Villain opens with the strongest ``open_raise`` share of hands.
* Everyone between the opener and hero has folded; players left to act after
  hero are assumed to fold too.
* Hero may fold, call or 3-bet to a fixed size. Facing a 3-bet, villain folds
  or calls (no 4-bets).
* Hands that see a flop are scored by raw all-in equity: postflop play,
  position and stack depth beyond the 3-bet are not modelled.

**Exploit**: villain's response to a 3-bet is node-locked to the profile
(the weakest ``fold_to_3bet`` share of the opening range folds, the rest
calls) and hero's strategy is solved against that.
**Baseline**: the same spot with villain's response left to the solver, i.e.
the equilibrium of this simplified game when villain defends perfectly.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from ..engine.cards import class_compatibility, class_names, combo_counts
from ..engine.equity import load_preflop_equity
from ..profiler.schema import Profile
from .cfr import CFRSolver
from .nodelock import lock_by_strength
from .ranges import preflop_strength, top_fraction
from .tree import build_preflop_tree

POSITIONS = ("UTG", "HJ", "CO", "BTN", "SB", "BB")
BLINDS = {"SB": 0.5, "BB": 1.0}
ACTIONS = ("fold", "call", "3bet")
ITERATIONS = 600
CLOSE_EV = 0.05  # BB; EVs closer than this are described as a close decision


@dataclass
class PreflopSpot:
    opener: str = "CO"
    hero: str = "BTN"
    open_size: float = 2.5
    three_bet_to: float = 8.5
    stack: float = 100.0

    def validate(self) -> None:
        if self.opener not in POSITIONS or self.hero not in POSITIONS:
            raise ValueError(f"positions must be one of {', '.join(POSITIONS)}")
        if POSITIONS.index(self.hero) <= POSITIONS.index(self.opener):
            raise ValueError("hero must act after the opener")
        if self.opener == "BB":
            raise ValueError("the big blind cannot open")
        if not 1.0 < self.open_size < self.three_bet_to <= self.stack:
            raise ValueError("need 1 < open size < 3-bet size <= stack")

    @property
    def hero_blind(self) -> float:
        return BLINDS.get(self.hero, 0.0)

    @property
    def start_pot(self) -> float:
        """Pot when hero acts: both blinds plus the open (which includes any opener blind)."""
        return sum(BLINDS.values()) - BLINDS.get(self.opener, 0.0) + self.open_size

    @property
    def to_call(self) -> float:
        return self.open_size - self.hero_blind

    @property
    def pot_odds(self) -> float:
        """Share of the final pot hero must put in to call."""
        return self.to_call / (self.start_pot + self.to_call)

    def seats(self, villain_name: str) -> list[dict]:
        """Stack and status of every seat, for the spot bar in the UI."""
        opener_i, hero_i = POSITIONS.index(self.opener), POSITIONS.index(self.hero)
        seats = []
        for i, pos in enumerate(POSITIONS):
            put_in = self.open_size if pos == self.opener else BLINDS.get(pos, 0.0)
            if pos == self.opener:
                label = villain_name
            elif pos == self.hero:
                label = "You"
            elif i < hero_i and i != opener_i:
                label = "Fold"
            else:
                label = "-"
            seats.append({"position": pos, "stack": self.stack - put_in, "label": label})
        return seats


def _hand_kind(name: str) -> str:
    if len(name) == 2:
        return "pair"
    return "suited" if name.endswith("s") else "offsuit"


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def _explain(name: str, freqs: np.ndarray, evs: np.ndarray, eq_open: float, eq_called: float,
             villain: str, open_share: float, fold_share: float, pot_odds: float) -> str:
    """Plain-English reason for a hand's play, built from the solver's numbers."""
    best = int(np.argmax(freqs))
    order = np.argsort(-evs)
    if evs[order[0]] - evs[order[1]] < CLOSE_EV and freqs.max() < 0.95:
        a, b = ACTIONS[order[0]], ACTIONS[order[1]]
        return (f"Close decision: {a} ({evs[order[0]]:+.2f} BB) and {b} ({evs[order[1]]:+.2f} BB) "
                f"are worth almost the same against {villain}, so either is fine.")
    if ACTIONS[best] == "3bet":
        if eq_called >= 0.5:
            return (f"{villain} folds to a 3-bet {_pct(fold_share)} of the time, and {name} has "
                    f"{_pct(eq_called)} equity against the hands that call. 3-bet for value "
                    f"({evs[2]:+.2f} BB vs {evs[1]:+.2f} BB for calling).")
        return (f"This 3-bet works mainly because {villain} folds {_pct(fold_share)} of the time. "
                f"When called, {name} has {_pct(eq_called)} equity, but the folds make it "
                f"profitable ({evs[2]:+.2f} BB).")
    if ACTIONS[best] == "call":
        return (f"{name} has {_pct(eq_open)} equity against {villain}'s opening range "
                f"(top {_pct(open_share)}), better than the {_pct(pot_odds)} pot odds, so calling "
                f"wins {evs[1]:+.2f} BB. 3-betting is worse ({evs[2]:+.2f} BB): the hands that call "
                f"a 3-bet leave {name} with {_pct(eq_called)}.")
    return (f"{name} has {_pct(eq_open)} equity against {villain}'s opening range "
            f"(top {_pct(open_share)}), short of the {_pct(pot_odds)} pot odds, and a 3-bet loses "
            f"money ({evs[2]:+.2f} BB) because {villain} folds only {_pct(fold_share)}.")


def _solve_mode(spot: PreflopSpot, profile: Profile, locked: bool, iterations: int) -> dict:
    """Solve a validated spot against ``profile``; ``locked`` picks exploit vs baseline."""
    equity = load_preflop_equity()
    compat = class_compatibility()
    strength = preflop_strength(equity)
    counts = combo_counts()

    open_share = profile.estimate("open_raise")
    fold_share = profile.estimate("fold_to_3bet")
    if open_share <= 0:
        raise ValueError(f"{profile.name}'s profile says they never open-raise")
    villain_range = top_fraction(strength, open_share)
    joint = compat * villain_range[None, :]

    root = build_preflop_tree(spot.start_pot, spot.to_call, spot.three_bet_to, spot.hero_blind)
    locks = {}
    if locked:
        # Villain's hands are weighted by combos in their range for the lock.
        locks["3bet"] = lock_by_strength(strength, villain_range * counts,
                                         [(0, fold_share), (1, 1 - fold_share)], 2)
    solver = CFRSolver(root, joint, equity, spot.start_pot, locks).solve(iterations)

    hero = solver.average_strategy("")
    evs = solver.root_action_values() + 0.0  # turns -0.0 into 0.0 for display
    villain_response = solver.average_strategy("3bet")
    callers = villain_range * villain_response[:, 1]
    eq_open = (equity * joint).sum(1) / np.maximum(joint.sum(1), 1e-12)
    called_joint = compat * callers[None, :]
    eq_called = (equity * called_joint).sum(1) / np.maximum(called_joint.sum(1), 1e-12)
    villain_weight = (villain_range * counts).sum()
    actual_fold = (villain_range * counts * villain_response[:, 0]).sum() / villain_weight

    hands = []
    for cid, name in enumerate(class_names()):
        hands.append({
            "name": name,
            "kind": _hand_kind(name),
            "combos": int(counts[cid]),
            "freqs": dict(zip(ACTIONS, np.round(hero[cid], 4).tolist())),
            "evs": dict(zip(ACTIONS, np.round(evs[cid], 3).tolist())),
            "why": _explain(name, hero[cid], evs[cid], eq_open[cid], eq_called[cid],
                            profile.name, open_share, actual_fold, spot.pot_odds),
        })
    combos_by_action = (hero * counts[:, None]).sum(axis=0)
    summary = {a: {"combos": round(float(c), 1), "share": round(float(c / counts.sum()), 4)}
               for a, c in zip(ACTIONS, combos_by_action)}
    return {
        "mode": "exploit" if locked else "baseline",
        "hands": hands,
        "summary": summary,
        "villain": {"open_share": open_share, "fold_to_3bet": round(float(actual_fold), 4)},
        "exploitability": round(solver.exploitability(), 4),
    }


def solve_spot(spot: PreflopSpot, profile: Profile, iterations: int = ITERATIONS) -> dict:
    """Exploit and baseline solutions plus the spot description for the UI."""
    spot.validate()
    return {
        "spot": {**asdict(spot), "start_pot": spot.start_pot, "to_call": spot.to_call,
                 "pot_odds": round(spot.pot_odds, 4), "seats": spot.seats(profile.name)},
        "opponent": profile.name,
        "exploit": _solve_mode(spot, profile, locked=True, iterations=iterations),
        "baseline": _solve_mode(spot, profile, locked=False, iterations=iterations),
    }
