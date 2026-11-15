"""Preflop betting for a table of 2-9 players.

Seats are listed in preflop action order (first to act ... SB, BB), so the
index of a seat is also its position. Amounts are in big blinds.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

POSITIONS: dict[int, tuple[str, ...]] = {
    2: ("SB", "BB"),  # heads-up: the small blind is the button and acts first
    3: ("BTN", "SB", "BB"),
    4: ("CO", "BTN", "SB", "BB"),
    5: ("HJ", "CO", "BTN", "SB", "BB"),
    6: ("UTG", "HJ", "CO", "BTN", "SB", "BB"),
    7: ("UTG", "LJ", "HJ", "CO", "BTN", "SB", "BB"),
    8: ("UTG", "UTG+1", "LJ", "HJ", "CO", "BTN", "SB", "BB"),
    9: ("UTG", "UTG+1", "UTG+2", "LJ", "HJ", "CO", "BTN", "SB", "BB"),
}
ACTION_KINDS = ("fold", "check", "call", "raise")


@dataclass
class Action:
    kind: str
    to: float | None = None  # total bet after a raise


class BettingRound:
    """One preflop betting round; :meth:`apply` validates every action."""

    def __init__(self, stacks: list[float], small_blind: float = 0.5, big_blind: float = 1.0) -> None:
        n = len(stacks)
        if n not in POSITIONS:
            raise ValueError("a hand needs 2 to 9 players")
        if any(s <= 0 for s in stacks):
            raise ValueError("every stack must be positive")
        self.positions = POSITIONS[n]
        self.big_blind = big_blind
        self.start_stacks = [float(s) for s in stacks]
        self.bets = [0.0] * n
        self.folded = [False] * n
        self.acted = [False] * n
        self.raises = 0  # number of raises so far (an open raise is 1)
        self.history: list[tuple[int, Action]] = []
        self._post(n - 2, small_blind)
        self._post(n - 1, big_blind)
        self.highest = max(self.bets)
        self.last_increment = big_blind
        self.actor: int | None = None
        self._advance(start=0)

    # ----- state -----------------------------------------------------------

    def _post(self, seat: int, amount: float) -> None:
        self.bets[seat] = min(amount, self.start_stacks[seat])

    def stack_left(self, seat: int) -> float:
        return self.start_stacks[seat] - self.bets[seat]

    def can_act(self, seat: int) -> bool:
        return not self.folded[seat] and self.stack_left(seat) > 1e-9

    def in_hand(self) -> list[int]:
        return [i for i, f in enumerate(self.folded) if not f]

    @property
    def pot(self) -> float:
        return sum(self.bets)

    @property
    def complete(self) -> bool:
        return self.actor is None

    def to_call(self, seat: int) -> float:
        return min(self.highest - self.bets[seat], self.stack_left(seat))

    def max_raise_to(self, seat: int) -> float:
        return self.start_stacks[seat]

    def min_raise_to(self, seat: int) -> float:
        return min(self.highest + self.last_increment, self.max_raise_to(seat))

    def can_raise(self, seat: int) -> bool:
        return self.max_raise_to(seat) > self.highest + 1e-9

    def default_raise_to(self, seat: int, open_size: float) -> float:
        """Usual size: the standard open when unraised, otherwise 3x the current bet."""
        target = open_size if self.raises == 0 else 3 * self.highest
        return round(min(max(target, self.min_raise_to(seat)), self.max_raise_to(seat)), 2)

    # ----- actions ---------------------------------------------------------

    def _advance(self, start: int) -> None:
        """Move to the next seat that still has to act, or end the round."""
        n = len(self.bets)
        self.actor = None
        if len(self.in_hand()) <= 1:
            return
        for step in range(n):
            seat = (start + step) % n
            if self.can_act(seat) and (not self.acted[seat] or self.bets[seat] < self.highest - 1e-9):
                # A lone player with chips who has already matched has nobody to bet against.
                others_live = any(self.can_act(s) for s in self.in_hand() if s != seat)
                if others_live or self.bets[seat] < self.highest - 1e-9:
                    self.actor = seat
                return

    def apply(self, action: Action) -> None:
        seat = self.actor
        if seat is None:
            raise ValueError("the betting round is over")
        kind = action.kind
        if kind == "fold":
            self.folded[seat] = True
        elif kind == "check":
            if self.to_call(seat) > 1e-9:
                raise ValueError("can't check when facing a bet")
        elif kind == "call":
            if self.to_call(seat) <= 1e-9:
                raise ValueError("nothing to call; check instead")
            self.bets[seat] += self.to_call(seat)
        elif kind == "raise":
            to = action.to
            if to is None or not self.can_raise(seat):
                raise ValueError("raise needs a size and enough chips")
            to = min(float(to), self.max_raise_to(seat))
            if to < self.min_raise_to(seat) - 1e-9:
                raise ValueError(f"minimum raise is to {self.min_raise_to(seat):g} BB")
            if to - self.highest >= self.last_increment - 1e-9:
                self.last_increment = to - self.highest  # a full raise reopens the betting
            self.highest = to
            self.bets[seat] = to
            self.raises += 1
            self.acted = [False] * len(self.bets)
            action = Action("raise", to)
        else:
            raise ValueError(f"unknown action {kind!r}; use one of {', '.join(ACTION_KINDS)}")
        self.acted[seat] = True
        self.history.append((seat, action))
        self._advance(start=seat + 1)

    def after(self, action: Action) -> "BettingRound":
        """A copy of this round with ``action`` applied (for looking ahead)."""
        clone = copy.deepcopy(self)
        clone.apply(action)
        return clone
