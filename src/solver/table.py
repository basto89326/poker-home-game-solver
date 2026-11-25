"""Betting rounds (preflop, flop, turn, river) for a table of 2-9 players.

Seats are listed in preflop action order (first to act ... SB, BB), so the
index of a seat is also its position. Amounts are in big blinds.

After the flop the first player still in the hand after the button acts
first (the SB seat, or the BB heads-up), the minimum bet is one big blind,
and a "raise" with no bet yet is a bet. Default sizes: preflop, the usual
open or 3x the current bet; after the flop, a bet of ``POSTFLOP_BET`` x the
pot or a raise to ``RAISE_MULTIPLE`` x the current bet.

At the end of a hand, :func:`side_pots` splits the chips into a main pot and
side pots (a player all-in for less can only win what they covered) and
:func:`settle` pays each pot to the winners picked for it, split evenly in
whole chips on a tie; an odd chip goes to the winner first to the left of
the button.
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
STREETS = ("preflop", "flop", "turn", "river")
BOARD_SIZE = {"preflop": 0, "flop": 3, "turn": 4, "river": 5}
POSTFLOP_BET = 0.5  # default bet: half the pot
RAISE_MULTIPLE = 3.0  # default raise: to 3x the current bet


@dataclass
class Action:
    kind: str
    to: float | None = None  # total bet after a raise


class BettingRound:
    """One betting round; :meth:`apply` validates every action.

    A new hand starts preflop; :meth:`next_street` starts the next round.
    """

    def __init__(self, stacks: list[float], small_blind: float = 0.5, big_blind: float = 1.0) -> None:
        n = len(stacks)
        if n not in POSITIONS:
            raise ValueError("a hand needs 2 to 9 players")
        if any(s <= 0 for s in stacks):
            raise ValueError("every stack must be positive")
        self.positions = POSITIONS[n]
        self.street = "preflop"
        self.pot_before = 0.0  # chips from earlier streets
        self.big_blind = big_blind
        self.start_stacks = [float(s) for s in stacks]  # at the start of this street
        self.hand_stacks = list(self.start_stacks)  # at the start of the hand
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

    def invested(self, seat: int) -> float:
        """Chips the seat has put in during the whole hand."""
        return self.hand_stacks[seat] - self.stack_left(seat)

    def can_act(self, seat: int) -> bool:
        return not self.folded[seat] and self.stack_left(seat) > 1e-9

    def in_hand(self) -> list[int]:
        return [i for i, f in enumerate(self.folded) if not f]

    @property
    def pot(self) -> float:
        return self.pot_before + sum(self.bets)

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

    @property
    def opening_bet(self) -> bool:
        """True after the flop while nobody has bet yet (a raise here is a bet)."""
        return self.street != "preflop" and self.highest <= 1e-9

    def default_raise_to(self, seat: int, open_size: float) -> float:
        """Usual size: see the module docstring."""
        if self.opening_bet:
            target = POSTFLOP_BET * self.pot
        elif self.street == "preflop" and self.raises == 0:
            target = open_size
        else:
            target = RAISE_MULTIPLE * self.highest
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

    def next_betting_street(self) -> str | None:
        """The street that comes next if betting continues after this round, else None.

        None when this is the river, only one player is left, or fewer than
        two players have chips left to bet (the rest of the board just runs out).
        """
        if not self.complete or self.street == "river" or len(self.in_hand()) < 2:
            return None
        if sum(self.can_act(s) for s in self.in_hand()) < 2:
            return None
        return STREETS[STREETS.index(self.street) + 1]

    def next_street(self) -> "BettingRound":
        """A new round for the next street, carrying the pot, stacks and folds over."""
        street = self.next_betting_street()
        if street is None:
            raise ValueError("there is no more betting in this hand")
        n = len(self.bets)
        nxt = copy.deepcopy(self)
        nxt.street = street
        nxt.pot_before = self.pot
        nxt.start_stacks = [self.stack_left(i) for i in range(n)]
        nxt.bets = [0.0] * n
        nxt.acted = [False] * n
        nxt.raises = 0
        nxt.highest = 0.0
        nxt.last_increment = self.big_blind
        nxt.history = []
        nxt._advance(start=1 if n == 2 else n - 2)  # heads-up the BB acts first after the flop
        return nxt

    def after(self, action: Action) -> "BettingRound":
        """A copy of this round with ``action`` applied (for looking ahead)."""
        clone = copy.deepcopy(self)
        clone.apply(action)
        return clone


def side_pots(invested: list[float], folded: list[bool]) -> list[tuple[float, list[int]]]:
    """Split the chips into pots: [(amount, seats that can win it)], main pot first.

    Each pot is a layer up to the next all-in amount of a player still in the
    hand; folded players' chips go into the layers they reached. A pot with
    one eligible seat is an uncalled bet that goes back to that player.
    """
    live = [i for i, f in enumerate(folded) if not f]
    if not live:
        raise ValueError("someone must still be in the hand")
    levels = sorted({invested[i] for i in live})
    result: list[tuple[float, list[int]]] = []
    previous = 0.0
    for level in levels:
        amount = sum(min(max(c - previous, 0.0), level - previous) for c in invested)
        eligible = [i for i in live if invested[i] >= level - 1e-9]
        if amount > 1e-9:
            result.append((amount, eligible))
        previous = level
    # Chips a folded player put in above every live player's total go to the last pot.
    extra = sum(max(c - previous, 0.0) for c in invested)
    if extra > 1e-9:
        amount, eligible = result[-1] if result else (0.0, [live[-1]])
        result[-1:] = [(amount + extra, eligible)]
    return result


def settle(pots: list[tuple[float, list[int]]], winners: list[list[int]], seats: int,
           chip: float) -> list[float]:
    """Amount each seat wins: every pot is split evenly between its ``winners``.

    Pots are paid in whole chips of size ``chip``; odd chips go to the winners
    nearest the left of the button (the post-flop acting order). A pot with
    one eligible seat goes to that seat whatever ``winners`` says.
    """
    if len(winners) != len(pots):
        raise ValueError(f"pick the winners of each of the {len(pots)} pots")
    first = 1 if seats == 2 else seats - 2  # first seat to the left of the button
    order = [(first + k) % seats for k in range(seats)]
    payouts = [0.0] * seats
    for number, ((amount, eligible), picked) in enumerate(zip(pots, winners), start=1):
        picked = list(eligible) if len(eligible) == 1 else sorted(set(picked))
        if not picked:
            raise ValueError(f"pot {number} needs at least one winner")
        if any(seat not in eligible for seat in picked):
            raise ValueError(f"pot {number} can only be won by a player who put chips in it and didn't fold")
        share, odd = divmod(round(amount / chip), len(picked))
        for k, seat in enumerate(sorted(picked, key=order.index)):
            payouts[seat] += (share + (1 if k < odd else 0)) * chip
    return payouts
