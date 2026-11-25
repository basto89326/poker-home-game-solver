"""Hand play-through: replay the actions so far and analyse the next decision.

Every seat is either "you" or a friend. Ranges are weights over the 1326
specific combos; they start as every hand, are narrowed after each action
using that seat's predicted play, and lose the combos that use a board card
when the flop, turn and river are dealt. Your seat uses your own profile
(from the Profile tab), or placeholder defaults without one.

Betting goes street by street. When a round ends and betting can continue,
the board cards for the next street must be given before the next action.
The grid shows hand classes: per-combo results are averaged over the combos
of each class that are still in the player's range (or, for classes outside
it, over every combo the board doesn't block).

Average results (EVs) come from the best-play simulation in both modes, so in
predicted mode they show what each action would earn, not what the profile
picks. Close calls are only flagged in best-play mode.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache

import numpy as np

from ..engine.cards import NUM_CLASSES, NUM_COMBOS, card_str, class_names, combo_classes, live_combos
from ..engine.equity import load_preflop_equity
from ..profiler.schema import Profile
from ..profiler.store import SELF_SLUG
from .bestplay import CLOSE_BB, BestPlay, best_play, stable_seed
from .predict import (CALL, FOLD, RAISE, action_shares, class_average, postflop_shares, predict_combos,
                      update_range)
from .ranges import preflop_strength
from .table import BOARD_SIZE, Action, BettingRound, settle, side_pots

YOU = SELF_SLUG
MODES = ("predicted", "best")
SOMETIMES = 0.05  # a predicted action this likely is mentioned as "sometimes"


@dataclass
class SeatSpec:
    player: str  # YOU or a player slug
    name: str
    profile: Profile  # placeholder defaults if they have no AI profile yet
    has_profile: bool
    stack: float  # BB


@dataclass
class GameSettings:
    small_blind: float = 0.5  # BB
    open_size: float = 5.0  # BB
    big_blind_dollars: float = 0.10


@lru_cache(maxsize=1)
def _strength() -> np.ndarray:
    return preflop_strength(load_preflop_equity())


def _column(kind: str) -> int:
    return {"fold": FOLD, "raise": RAISE}.get(kind, CALL)


class _Money:
    def __init__(self, big_blind_dollars: float) -> None:
        self.bb = big_blind_dollars

    def __call__(self, amount_bb: float, signed: bool = False) -> str:
        dollars = amount_bb * self.bb
        text = f"${abs(dollars):.2f}"
        if signed:
            return ("+" if dollars >= 0 else "-") + text
        return text


def _chips(amount: float, settings: GameSettings) -> float:
    """Round an amount (BB) to a whole number of the smallest chip, the small blind ($0.05)."""
    return round(amount / settings.small_blind) * settings.small_blind


def _default_raise_to(round_: BettingRound, seat: int, settings: GameSettings) -> float:
    """The usual bet/raise size (see ``table.py``) in whole chips, kept legal."""
    size = _chips(round_.default_raise_to(seat, settings.open_size), settings)
    return min(max(size, round_.min_raise_to(seat)), round_.max_raise_to(seat))


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def _predict(seat: SeatSpec, combo_range: np.ndarray, round_: BettingRound, board: list[int]) -> np.ndarray:
    """(1326, 3) predicted play of the seat to act."""
    return predict_combos(seat.profile, combo_range, board[:BOARD_SIZE[round_.street]], round_.raises,
                          round_.to_call(round_.actor) <= 1e-9, _strength())


def replay(seats: list[SeatSpec], actions: list[Action], board: list[int],
           settings: GameSettings) -> tuple[BettingRound, list[np.ndarray], list[dict]]:
    """Apply ``actions`` in order, dealing ``board`` street by street.

    Returns the current round, each seat's per-combo range and a log.
    """
    ranges = [np.ones(NUM_COMBOS) for _ in seats]
    log: list[dict] = []

    def deal_streets(current: BettingRound) -> BettingRound:
        """Move on to every later street whose cards are already on the board."""
        while (street := current.next_betting_street()) and len(board) >= BOARD_SIZE[street]:
            cards = board[BOARD_SIZE[current.street]:BOARD_SIZE[street]]
            current = current.next_street()
            live = live_combos(board[:BOARD_SIZE[street]])
            for i in range(len(ranges)):
                ranges[i] = ranges[i] * live
            log.append({"kind": "board", "street": street, "cards": [card_str(c) for c in cards],
                        "pot": current.pot})
        return current

    round_ = deal_streets(BettingRound([s.stack for s in seats], settings.small_blind, 1.0))
    for number, action in enumerate(actions, start=1):
        seat = round_.actor
        if seat is None:
            street = round_.next_betting_street()
            if street:
                raise ValueError(f"action {number} comes after the {round_.street} betting round ended; "
                                 f"choose the {street} cards first")
            raise ValueError(f"action {number} comes after the betting round ended")
        freqs = _predict(seats[seat], ranges[seat], round_, board)
        opening_bet = round_.opening_bet
        try:
            round_.apply(action)
        except ValueError as exc:
            raise ValueError(f"action {number} ({seats[seat].name}): {exc}") from None
        ranges[seat], fits = update_range(ranges[seat], freqs, _column(action.kind))
        recorded = round_.history[-1][1]
        kind = "bet" if recorded.kind == "raise" and opening_bet else recorded.kind
        log.append({"seat": seat, "position": round_.positions[seat], "name": seats[seat].name,
                    "street": round_.street, "kind": kind, "to": recorded.to, "pot": round_.pot,
                    "fits_profile": fits or action.kind == "fold"})
        round_ = deal_streets(round_)
    if len(board) > BOARD_SIZE[round_.street]:
        raise ValueError(f"board cards were given before the {round_.street} betting round ended")
    return round_, ranges, log


def _state(round_: BettingRound, seats: list[SeatSpec], board: list[int], settings: GameSettings) -> dict:
    actor = round_.actor
    next_street = round_.next_betting_street()
    state = {
        "street": round_.street,
        "board": [card_str(c) for c in board],
        # The street whose cards must be chosen before betting can go on, if any.
        "needs_street": next_street,
        "cards_needed": BOARD_SIZE[next_street] - len(board) if next_street else 0,
        "seats": [{"position": round_.positions[i], "player": s.player, "name": s.name,
                   "stack": round_.stack_left(i), "bet": round_.bets[i],
                   "folded": round_.folded[i], "all_in": round_.stack_left(i) <= 1e-9}
                  for i, s in enumerate(seats)],
        "pot": round_.pot,
        "actor": actor,
        "complete": round_.complete,
        "players_left": [seats[i].name for i in round_.in_hand()],
    }
    if round_.complete and next_street is None:  # the hand is over: who can win what
        invested = [round_.invested(i) for i in range(len(seats))]
        state["pots"] = [{"amount": amount, "eligible": eligible}
                         for amount, eligible in side_pots(invested, round_.folded)]
    if actor is not None:
        state["legal"] = {
            "to_call": round_.to_call(actor),
            "can_raise": round_.can_raise(actor),
            "min_raise_to": round_.min_raise_to(actor),
            "max_raise_to": round_.max_raise_to(actor),
            "default_raise_to": _default_raise_to(round_, actor, settings),
        }
    return state


@dataclass
class _Voice:
    """How generated text refers to the player: second person for you, "they" for friends."""

    owner: str  # "your" / "Dave's"
    poss: str  # "your" / "their"
    subject: str  # "you" / "they"
    subject_are: str  # "you're" / "they're"

    @classmethod
    def for_seat(cls, seat: SeatSpec) -> "_Voice":
        if seat.player == YOU:
            return cls("your", "your", "you", "you're")
        return cls(f"{seat.name}'s", "their", "they", "they're")


def _describe_rule(profile: Profile, round_: BettingRound, voice: _Voice) -> str:
    owner, poss, raises = voice.owner, voice.poss, round_.raises
    if round_.street != "preflop":
        fold, _, raise_ = postflop_shares(profile, raises)
        if raises == 0:
            bluff = profile.estimate("river_bluff")
            tail = (f"; the weaker half of {poss} range also bluffs {_pct(bluff)} of the time"
                    if round_.street == "river" and bluff > 0 else "")
            return (f"With no bet yet, {owner} profile bets {poss} strongest {_pct(raise_)} "
                    f"(from {poss} aggression) and checks the rest{tail}.")
        return (f"Facing a bet, {owner} profile folds the weakest {_pct(fold)} of {poss} range (fold to "
                f"c-bet), raises the strongest {_pct(raise_)} and calls with the rest.")
    fold, call, raise_ = action_shares(profile, raises)
    if raises == 0:
        return (f"With no raise yet, {owner} profile raises {poss} strongest {_pct(raise_)}, "
                f"limps or checks with the next {_pct(call)} and folds the rest.")
    if raises == 1:
        return (f"Facing a raise, {owner} profile re-raises {poss} strongest {_pct(raise_)}, "
                f"calls with the next {_pct(call)} and folds the weakest {_pct(fold)}.")
    return (f"Facing a re-raise, {owner} profile folds the weakest {_pct(fold)} of what {voice.subject} "
            f"have left, re-raises the strongest {_pct(raise_)} and calls with the rest.")


def _predicted_why(hand: str, freqs: np.ndarray, in_range: bool, rule: str, voice: _Voice,
                   can_check: bool, opening_bet: bool) -> str:
    if not in_range:
        return f"{hand} isn't in {voice.owner} range any more after {voice.poss} earlier actions. {rule}"
    labels = ("fold", "check" if can_check else "call", "bet" if opening_bet else "raise")
    likely = int(np.argmax(freqs))
    others = [labels[a] for a in (RAISE, CALL, FOLD) if a != likely and freqs[a] >= SOMETIMES]
    tail = f" (sometimes {' or '.join(others)}: {hand} sits on the edge of that group)" if others else ""
    return f"{rule} So with {hand} {voice.subject_are} most likely to {labels[likely]}{tail}."


def _best_why(hand: str, cid: int, bp: BestPlay, can_check: bool, raise_phrase: str, money: _Money) -> str:
    """``raise_phrase`` is e.g. "raising to $1.50" or "betting $0.50"."""
    evs = bp.evs[cid]
    call_word = "checking" if can_check else "calling"
    best = int(np.nanargmax(evs))
    prefix = (f"Close call: the top two choices are within {CLOSE_BB:g} BB of each other, so either "
              "is reasonable. " if bp.close[cid] else "")
    has_raise = not np.isnan(evs[RAISE])
    raise_text = f"{raise_phrase} averages {money(evs[RAISE], True)}" if has_raise else ""
    if best == RAISE:
        return (f"{prefix}{raise_phrase.capitalize()} averages {money(evs[RAISE], True)}, vs "
                f"{money(evs[CALL], True)} for {call_word}. Everyone folds "
                f"{_pct(bp.fold_equity[cid])} of the time; when called, {hand} wins "
                f"{_pct(bp.called_share[cid])} of the pot on average.")
    if best == CALL:
        tail = f" and {raise_text}" if has_raise else ""
        return (f"{prefix}{call_word.capitalize()} averages {money(evs[CALL], True)}{tail}. "
                f"When it reaches showdown, {hand} wins {_pct(bp.call_share[cid])} of the pot "
                f"on average.")
    tail = f", and {raise_text}" if has_raise else ""
    return (f"{prefix}Folding is best: {call_word} averages {money(evs[CALL], True)}{tail}.")


def _summary(combo_freqs: np.ndarray, combo_range: np.ndarray) -> dict:
    by_action = combo_range @ combo_freqs
    total = max(combo_range.sum(), 1e-12)
    return {name: {"combos": round(float(c), 1), "share": round(float(c / total), 4)}
            for name, c in zip(("fold", "call", "raise"), by_action)}


def analyze(seats: list[SeatSpec], actions: list[Action], settings: GameSettings,
            mode: str = "best", raise_to: float | None = None, your_mode: str = "best",
            board: list[int] = (), winners: list[list[int]] | None = None) -> dict:
    """State of the hand plus the analysis for whoever acts next.

    ``mode`` applies on friends' turns and ``your_mode`` on yours; each is
    "predicted" or "best". ``board`` holds the board cards chosen so far
    (0, 3, 4 or 5 card ids). Once the hand is over, ``winners`` (seat numbers
    for each pot in ``state["pots"]``) adds a ``settlement`` with each seat's
    winnings and stack for the next hand. Profiles are never changed.

    Stacks and bet sizes are rounded to whole chips (the small blind, $0.05),
    so every stack, bet and pot stays a multiple of $0.05.
    """
    seats = [replace(s, stack=_chips(s.stack, settings)) for s in seats]
    actions = [a if a.to is None else Action(a.kind, _chips(a.to, settings)) for a in actions]
    raise_to = None if raise_to is None else _chips(raise_to, settings)
    board = list(board)
    if len(board) not in BOARD_SIZE.values():
        raise ValueError("the board needs 0, 3, 4 or 5 cards")
    if len(set(board)) != len(board):
        raise ValueError("a card can only be on the board once")
    if mode not in MODES or your_mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if sum(s.player == YOU for s in seats) != 1:
        raise ValueError("exactly one seat must be you")
    players = [s.player for s in seats if s.player != YOU]
    if len(players) != len(set(players)):
        raise ValueError("a player can only sit in one seat")

    round_, ranges, log = replay(seats, actions, board, settings)
    result = {"state": _state(round_, seats, board, settings), "log": log, "analysis": None}
    if winners is not None:
        if "pots" not in result["state"]:
            raise ValueError("the hand isn't over yet")
        pots = [(p["amount"], p["eligible"]) for p in result["state"]["pots"]]
        payouts = settle(pots, winners, len(seats), chip=settings.small_blind)
        result["settlement"] = {"payouts": payouts,
                                "stacks": [round_.stack_left(i) + payouts[i] for i in range(len(seats))]}
    actor = round_.actor
    if actor is None:
        return result

    seat = seats[actor]
    if seat.player == YOU:
        mode = your_mode
    can_check = round_.to_call(actor) <= 1e-9
    size = raise_to if raise_to is not None else _default_raise_to(round_, actor, settings)
    size = min(max(size, round_.min_raise_to(actor)), round_.max_raise_to(actor))
    money = _Money(settings.big_blind_dollars)
    names = class_names()
    classes = combo_classes()
    street_board = board[:BOARD_SIZE[round_.street]]
    live = live_combos(street_board)
    live_count = np.bincount(classes, weights=live, minlength=NUM_CLASSES)
    in_range = np.bincount(classes, weights=ranges[actor], minlength=NUM_CLASSES) > 0
    seed = stable_seed([(s.player, s.stack) for s in seats], [(a.kind, a.to) for a in actions], size,
                       street_board)
    bp = best_play(round_, ranges, [s.profile for s in seats], load_preflop_equity(),
                   size if round_.can_raise(actor) else None, board=street_board, seed=seed)
    raise_phrase = f"betting {money(size)}" if round_.opening_bet else f"raising to {money(size)}"

    if mode == "predicted":
        combo_freqs = _predict(seat, ranges[actor], round_, board)
        # In-range classes: average over the combos still in range; others: over every live combo.
        freqs = class_average(combo_freqs, np.where(in_range[classes], ranges[actor], live))
        close = np.zeros(NUM_CLASSES, bool)
        voice = _Voice.for_seat(seat)
        rule = _describe_rule(seat.profile, round_, voice)
        whys = [_predicted_why(n, freqs[c], in_range[c], rule, voice, can_check, round_.opening_bet)
                for c, n in enumerate(names)]
    else:
        freqs, close = bp.strategy, bp.close
        combo_freqs = freqs[classes]
        whys = [_best_why(n, c, bp, can_check, raise_phrase, money) if live_count[c] else ""
                for c, n in enumerate(names)]
    for c, n in enumerate(names):
        if not live_count[c]:
            freqs[c] = 0.0
            whys[c] = f"The board uses the cards {n} needs, so nobody can hold it."
    hands = [{"name": n, "freqs": freqs[c].round(4).tolist(),
              "evs": [None if np.isnan(v) else round(float(v), 3) for v in bp.evs[c]],
              "in_range": bool(in_range[c]), "close": bool(close[c]), "combos": int(live_count[c]),
              "why": whys[c]}
             for c, n in enumerate(names)]

    result["analysis"] = {
        "seat": actor,
        "mode": mode,
        "name": seat.name,
        "position": round_.positions[actor],
        "is_you": seat.player == YOU,
        "street": round_.street,
        "can_check": can_check,
        "opening_bet": round_.opening_bet,
        "raise_to": size,
        "hands": hands,
        "summary": _summary(combo_freqs, ranges[actor]),
        "profile": seat.profile.to_dict(),
        "has_profile": seat.has_profile,
    }
    return result
