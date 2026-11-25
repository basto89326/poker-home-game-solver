"""Hand play-through: replay the preflop actions so far and analyse the next decision.

Every seat is either "you" or a friend. Ranges start as every hand and are
narrowed after each action using that seat's predicted play. Your seat uses
your own profile (from the Profile tab), or placeholder defaults without one.

Average results (EVs) come from the best-play simulation in both modes, so in
predicted mode they show what each action would earn, not what the profile
picks. Close calls are only flagged in best-play mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from ..engine.cards import class_names, combo_counts
from ..engine.equity import load_preflop_equity
from ..profiler.schema import Profile
from ..profiler.store import SELF_SLUG
from .bestplay import CLOSE_BB, BestPlay, best_play, stable_seed
from .predict import CALL, FOLD, RAISE, action_shares, predicted_strategy, update_range
from .ranges import preflop_strength
from .table import Action, BettingRound

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


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def replay(seats: list[SeatSpec], actions: list[Action],
           settings: GameSettings) -> tuple[BettingRound, list[np.ndarray], list[dict]]:
    """Apply ``actions`` in order; returns the round, each seat's range and a log."""
    round_ = BettingRound([s.stack for s in seats], settings.small_blind, 1.0)
    ranges = [np.ones(len(class_names())) for _ in seats]
    log = []
    for number, action in enumerate(actions, start=1):
        seat = round_.actor
        if seat is None:
            raise ValueError(f"action {number} comes after the betting round ended")
        freqs = predicted_strategy(seats[seat].profile, ranges[seat], _strength(),
                                   round_.raises, round_.to_call(seat) <= 1e-9)
        try:
            round_.apply(action)
        except ValueError as exc:
            raise ValueError(f"action {number} ({seats[seat].name}): {exc}") from None
        ranges[seat], fits = update_range(ranges[seat], freqs, _column(action.kind))
        recorded = round_.history[-1][1]
        log.append({"seat": seat, "position": round_.positions[seat], "name": seats[seat].name,
                    "kind": recorded.kind, "to": recorded.to, "pot": round_.pot,
                    "fits_profile": fits or action.kind == "fold"})
    return round_, ranges, log


def _state(round_: BettingRound, seats: list[SeatSpec], settings: GameSettings) -> dict:
    actor = round_.actor
    state = {
        "seats": [{"position": round_.positions[i], "player": s.player, "name": s.name,
                   "stack": round_.stack_left(i), "bet": round_.bets[i],
                   "folded": round_.folded[i], "all_in": round_.stack_left(i) <= 1e-9}
                  for i, s in enumerate(seats)],
        "pot": round_.pot,
        "actor": actor,
        "complete": round_.complete,
        "players_left": [seats[i].name for i in round_.in_hand()],
    }
    if actor is not None:
        state["legal"] = {
            "to_call": round_.to_call(actor),
            "can_raise": round_.can_raise(actor),
            "min_raise_to": round_.min_raise_to(actor),
            "max_raise_to": round_.max_raise_to(actor),
            "default_raise_to": round_.default_raise_to(actor, settings.open_size),
        }
    return state


def _describe_rule(profile: Profile, raises: int) -> str:
    fold, call, raise_ = action_shares(profile, raises)
    name = profile.name
    if raises == 0:
        return (f"With no raise yet, {name}'s profile raises their strongest {_pct(raise_)}, "
                f"limps or checks with the next {_pct(call)} and folds the rest.")
    if raises == 1:
        return (f"Facing a raise, {name}'s profile re-raises their strongest {_pct(raise_)}, "
                f"calls with the next {_pct(call)} and folds the weakest {_pct(fold)}.")
    return (f"Facing a re-raise, {name}'s profile folds the weakest {_pct(fold)} of what they "
            f"have left, re-raises the strongest {_pct(raise_)} and calls with the rest.")


def _predicted_why(hand: str, freqs: np.ndarray, in_range: bool, rule: str, name: str,
                   can_check: bool) -> str:
    if not in_range:
        return f"{hand} isn't in {name}'s range any more after their earlier actions. {rule}"
    labels = ("fold", "check" if can_check else "call", "raise")
    likely = int(np.argmax(freqs))
    others = [labels[a] for a in (RAISE, CALL, FOLD) if a != likely and freqs[a] >= SOMETIMES]
    tail = f" (sometimes {' or '.join(others)}: {hand} sits on the edge of that group)" if others else ""
    return f"{rule} So with {hand} they're most likely to {labels[likely]}{tail}."


def _best_why(hand: str, cid: int, bp: BestPlay, can_check: bool, raise_to: float, money: _Money) -> str:
    evs = bp.evs[cid]
    call_word = "checking" if can_check else "calling"
    best = int(np.nanargmax(evs))
    prefix = (f"Close call: the top two choices are within {CLOSE_BB:g} BB of each other, so either "
              "is reasonable. " if bp.close[cid] else "")
    has_raise = not np.isnan(evs[RAISE])
    raise_text = f"raising to {money(raise_to)} averages {money(evs[RAISE], True)}" if has_raise else ""
    if best == RAISE:
        return (f"{prefix}Raising to {money(raise_to)} averages {money(evs[RAISE], True)}, vs "
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


def _summary(freqs: np.ndarray, range_weights: np.ndarray) -> dict:
    combos = range_weights * combo_counts()
    by_action = combos @ freqs
    total = max(combos.sum(), 1e-12)
    return {name: {"combos": round(float(c), 1), "share": round(float(c / total), 4)}
            for name, c in zip(("fold", "call", "raise"), by_action)}


def analyze(seats: list[SeatSpec], actions: list[Action], settings: GameSettings,
            mode: str = "best", raise_to: float | None = None, your_mode: str = "best") -> dict:
    """State of the hand plus the analysis for whoever acts next.

    ``mode`` applies on friends' turns and ``your_mode`` on yours; each is
    "predicted" or "best".
    """
    if mode not in MODES or your_mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if sum(s.player == YOU for s in seats) != 1:
        raise ValueError("exactly one seat must be you")
    players = [s.player for s in seats if s.player != YOU]
    if len(players) != len(set(players)):
        raise ValueError("a player can only sit in one seat")

    round_, ranges, log = replay(seats, actions, settings)
    result = {"state": _state(round_, seats, settings), "log": log, "analysis": None}
    actor = round_.actor
    if actor is None:
        return result

    seat = seats[actor]
    if seat.player == YOU:
        mode = your_mode
    can_check = round_.to_call(actor) <= 1e-9
    size = raise_to if raise_to is not None else round_.default_raise_to(actor, settings.open_size)
    size = min(max(size, round_.min_raise_to(actor)), round_.max_raise_to(actor))
    money = _Money(settings.big_blind_dollars)
    names = class_names()
    in_range = ranges[actor] > 0
    seed = stable_seed([(s.player, s.stack) for s in seats], [(a.kind, a.to) for a in actions], size)
    bp = best_play(round_, ranges, [s.profile for s in seats], load_preflop_equity(),
                   size if round_.can_raise(actor) else None, seed=seed)

    if mode == "predicted":
        freqs = predicted_strategy(seat.profile, ranges[actor], _strength(), round_.raises, can_check)
        rule = _describe_rule(seat.profile, round_.raises)
        close = np.zeros(len(names), bool)
        whys = [_predicted_why(n, freqs[c], in_range[c], rule, seat.name, can_check) for c, n in enumerate(names)]
    else:
        freqs, close = bp.strategy, bp.close
        whys = [_best_why(n, c, bp, can_check, size, money) for c, n in enumerate(names)]
    hands = [{"name": n, "freqs": freqs[c].round(4).tolist(),
              "evs": [None if np.isnan(v) else round(float(v), 3) for v in bp.evs[c]],
              "in_range": bool(in_range[c]), "close": bool(close[c]), "why": whys[c]}
             for c, n in enumerate(names)]

    result["analysis"] = {
        "seat": actor,
        "mode": mode,
        "name": seat.name,
        "position": round_.positions[actor],
        "is_you": seat.player == YOU,
        "can_check": can_check,
        "raise_to": size,
        "hands": hands,
        "summary": _summary(freqs, ranges[actor]),
        "profile": seat.profile.to_dict(),
        "has_profile": seat.has_profile,
    }
    return result
