"""Best play for the player to act, against everyone else's predicted play.

For each of the actor's options (fold, check/call, bet/raise to a given
size) and each of the 169 hand classes, a Monte Carlo simulation deals the
other players hands from their current ranges (per combo) and deals the rest
of the board at random, keeping the board cards already known. For every
deal, the result is averaged *exactly* over who folds and who continues (each
player continues with the probability their profile predicts for that
hand). Preflop, a heads-up showdown uses the preflop equity table rather than
the one sampled board, which removes most of the random noise; every other
showdown uses the sampled board.

Simplifications (also shown in the UI):

* One round of responses: anyone who would bet or raise after the actor is
  treated as checking or calling, and the actor doesn't get to act again.
* Hands that continue are scored by all-in equity: no betting on later
  streets, so no implied odds and no bluffs over several streets.
* Each hand class is averaged over its combos; after the flop, specific suits
  (e.g. a flush draw) can play differently from the class as a whole.
* No side pots; each player's contribution is capped at their stack.
* Opponents' cards are dealt one player at a time, avoiding cards already
  dealt, which is a close (not exact) approximation of card removal.

The best action is the one with the highest average result. When the top
two averages are within ``CLOSE_BB`` (0.1 big blinds) of each other the hand
is marked *close*: the choice barely matters. This is a fixed threshold, not
a measure of the simulation's noise.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass

import numpy as np

from ..engine.cards import NUM_CLASSES, all_combos, combo_classes, live_combos
from ..engine.evaluator import evaluate
from ..profiler.schema import Profile
from .predict import CALL, FOLD, RAISE, predict_combos
from .ranges import preflop_strength
from .table import Action, BettingRound

SAMPLES_PER_CLASS = 400
_MAX_REDRAWS = 25
CLOSE_BB = 0.1  # top two averages closer than this (in big blinds) count as a close call


@dataclass
class BestPlay:
    evs: np.ndarray  # (169, 3) average result in BB per action; NaN where not allowed or blocked
    strategy: np.ndarray  # (169, 3) 1 on the action with the best average
    close: np.ndarray  # (169,) True where the top two averages are within CLOSE_BB
    fold_equity: np.ndarray  # (169,) chance everyone folds to the raise
    called_share: np.ndarray  # (169,) average pot share when the raise is called
    call_share: np.ndarray  # (169,) average pot share when calling/checking reaches showdown


@dataclass
class _Option:
    """What happens after the actor takes one option."""

    actor_add: float
    pot_before: float
    adds: np.ndarray  # per opponent: chips added if they continue
    continue_prob: np.ndarray  # per opponent: (1326,) chance of continuing by combo


def _option(round_: BettingRound, action: Action, opponents: list[int], ranges: list[np.ndarray],
            profiles: list[Profile], board: list[int], strength: np.ndarray) -> _Option:
    actor = round_.actor
    after = round_.after(action)
    adds = np.zeros(len(opponents))
    cont = np.ones((len(opponents), len(all_combos())))
    for k, seat in enumerate(opponents):
        must_act = after.can_act(seat) and (not after.acted[seat] or after.bets[seat] < after.highest)
        if not must_act:
            continue
        to_call = after.to_call(seat)
        freqs = predict_combos(profiles[seat], ranges[seat], board, after.raises, to_call <= 1e-9, strength)
        cont[k] = 1.0 - freqs[:, FOLD]
        adds[k] = to_call
    return _Option(after.bets[actor] - round_.bets[actor], round_.pot, adds, cont)


@dataclass
class _Deal:
    hero_class: np.ndarray  # (rows,)
    hero: np.ndarray  # (rows, 2)
    opp_combo: np.ndarray  # (opponents, rows) combo ids
    opp_class: np.ndarray  # (opponents, rows)
    board: np.ndarray  # (rows, 5): the known board plus a random rest
    valid: np.ndarray  # (rows,) False where cards couldn't be dealt without a clash


def _live_table(board: list[int]) -> tuple[np.ndarray, np.ndarray]:
    """Combo ids of each class that the board doesn't block, padded to (169, 12), and their count."""
    classes = combo_classes()
    table = np.zeros((NUM_CLASSES, 12), dtype=np.int64)
    sizes = np.zeros(NUM_CLASSES, dtype=np.int64)
    for combo in np.flatnonzero(live_combos(board)):
        cls = classes[combo]
        table[cls, sizes[cls]] = combo
        sizes[cls] += 1
    return table, sizes


def _deal(rng: np.random.Generator, samples: int, opponents: list[int],
          ranges: list[np.ndarray], board: list[int]) -> _Deal:
    """Deal hero (each class ``samples`` times), opponents and the rest of the board."""
    combos = all_combos()
    live = live_combos(board)
    table, sizes = _live_table(board)
    hero_class = np.repeat(np.arange(NUM_CLASSES), samples)
    rows = len(hero_class)
    valid = sizes[hero_class] > 0  # classes the board blocks completely can't be dealt
    hero = combos[table[hero_class, rng.integers(0, np.maximum(sizes[hero_class], 1))]]
    used = np.zeros((rows, 52), bool)
    idx = np.arange(rows)
    used[idx, hero[:, 0]] = used[idx, hero[:, 1]] = True
    used[:, board] = True

    opp_combo = np.zeros((len(opponents), rows), dtype=np.int64)
    for k, seat in enumerate(opponents):
        probs = ranges[seat] * live
        if probs.sum() <= 0:
            raise ValueError("a player has no hands left in their range")
        probs = probs / probs.sum()
        pending = idx
        for _ in range(_MAX_REDRAWS):
            drawn = rng.choice(len(combos), size=len(pending), p=probs)
            cards = combos[drawn]
            clash = used[pending, cards[:, 0]] | used[pending, cards[:, 1]]
            opp_combo[k, pending[~clash]] = drawn[~clash]
            pending = pending[clash]
            if not len(pending):
                break
        valid[pending] = False
        cards = combos[opp_combo[k]]
        used[idx, cards[:, 0]] = True
        used[idx, cards[:, 1]] = True

    need = 5 - len(board)
    keys = rng.random((rows, 52))
    keys[used] = 2.0
    rest = np.argpartition(keys, need, axis=1)[:, :need] if need else np.zeros((rows, 0), dtype=np.int64)
    full_board = np.hstack([np.tile(np.asarray(board, dtype=np.int64), (rows, 1)), rest])
    return _Deal(hero_class, hero, opp_combo, combo_classes()[opp_combo], full_board, valid)


@dataclass
class _Outcome:
    """Per dealt row: expected result, chance everyone folds, pot share when called."""

    result: np.ndarray
    all_fold: np.ndarray
    called: np.ndarray  # chance at least one opponent continues
    called_share: np.ndarray  # pot share x chance, summed over the called cases


def _outcome(opt: _Option, deal: _Deal, hero_score: np.ndarray, opp_score: np.ndarray,
             equity: np.ndarray | None) -> _Outcome:
    """Average one option exactly over every combination of folds and calls.

    Walks the fold/call choices of each responder depth-first, carrying the
    running probability, best opposing score, tie count and pot, so each
    combination costs the same however many players are at the table.
    ``equity`` (preflop only) scores heads-up showdowns from the equity table.
    """
    k = len(opt.adds)
    cont = opt.continue_prob[np.arange(k)[:, None], deal.opp_combo]  # (k, rows)
    responders = [j for j in range(k) if np.any(opt.continue_prob[j] < 1)]
    always_in = [j for j in range(k) if j not in responders]
    rows = len(hero_score)
    out = _Outcome(np.zeros(rows), np.zeros(rows), np.zeros(rows), np.zeros(rows))

    def leaf(prob, best, ties, live, single, pot):
        if live == 0:
            out.all_fold += prob
            out.result += prob * (pot - opt.actor_add)
            return
        if live == 1 and equity is not None:  # heads-up preflop: all-in equity from the table
            share = equity[deal.hero_class, deal.opp_class[single]]
        else:
            share = np.where(hero_score > best, 1.0, np.where(hero_score == best, 1.0 / (1 + ties), 0.0))
        out.result += prob * (share * pot - opt.actor_add)
        out.called += prob
        out.called_share += prob * share

    def walk(i, prob, best, ties, live, single, pot):
        if not prob.any():
            return
        if i == len(responders):
            leaf(prob, best, ties, live, single, pot)
            return
        j = responders[i]
        walk(i + 1, prob * (1 - cont[j]), best, ties, live, single, pot)  # j folds
        score = opp_score[j]
        new_ties = np.where(score > best, 1, np.where(score == best, ties + 1, ties))
        walk(i + 1, prob * cont[j], np.maximum(best, score), new_ties, live + 1, j,
             pot + opt.adds[j])  # j continues

    best, ties = np.full(rows, -1), np.zeros(rows, dtype=int)
    for j in always_in:
        score = opp_score[j]
        ties = np.where(score > best, 1, np.where(score == best, ties + 1, ties))
        best = np.maximum(best, score)
    single = always_in[0] if len(always_in) == 1 else None
    pot = opt.pot_before + opt.actor_add + opt.adds[always_in].sum()
    walk(0, np.ones(rows), best, ties, len(always_in), single, pot)
    return out


def best_play(round_: BettingRound, ranges: list[np.ndarray], profiles: list[Profile],
              equity: np.ndarray, raise_to: float | None, board: list[int] = (),
              samples: int = SAMPLES_PER_CLASS, seed: int = 0) -> BestPlay:
    """Evaluate fold / check-call / raise for every hand class of the actor.

    ``ranges`` are per-combo weights (1326 each), ``equity`` is the 169x169
    preflop equity table and ``board`` the board cards so far (0, 3, 4 or 5).
    Classes the board blocks completely get NaN results and no best action.
    """
    board = list(board)
    actor = round_.actor
    if actor is None:
        raise ValueError("nobody is left to act")
    opponents = [s for s in round_.in_hand() if s != actor]
    strength = preflop_strength(equity)
    rng = np.random.default_rng(seed)

    deal = _deal(rng, samples, opponents, ranges, board)
    hero_class, valid = deal.hero_class, deal.valid
    hero_score = evaluate(np.hstack([deal.hero, deal.board]))
    opp_score = np.stack([evaluate(np.hstack([all_combos()[combo], deal.board])) for combo in deal.opp_combo])

    call_kind = "check" if round_.to_call(actor) <= 1e-9 else "call"
    options: dict[int, _Option] = {
        CALL: _option(round_, Action(call_kind), opponents, ranges, profiles, board, strength)}
    if raise_to is not None and round_.can_raise(actor):
        options[RAISE] = _option(round_, Action("raise", raise_to), opponents, ranges, profiles, board,
                                 strength)

    evs = np.full((NUM_CLASSES, 3), np.nan)
    evs[:, FOLD] = 0.0
    shares: dict[int, np.ndarray] = {}
    fold_equity = np.zeros(NUM_CLASSES)
    dealt = np.bincount(hero_class, weights=valid, minlength=NUM_CLASSES)
    trials = np.maximum(dealt, 1)

    def per_class(values: np.ndarray) -> np.ndarray:
        """Sum a per-row value over the rows of each hand class (valid rows only)."""
        return np.bincount(hero_class, weights=values * valid, minlength=NUM_CLASSES)

    for action, opt in options.items():
        outcome = _outcome(opt, deal, hero_score, opp_score, None if board else equity)
        evs[:, action] = per_class(outcome.result) / trials
        shares[action] = per_class(outcome.called_share) / np.maximum(per_class(outcome.called), 1e-12)
        if action == RAISE:
            fold_equity = per_class(outcome.all_fold) / trials

    # Fold and check/call are always allowed, so there are at least two real options.
    rows = np.arange(NUM_CLASSES)
    order = np.argsort(np.where(np.isnan(evs), -np.inf, evs), axis=1)
    first, second = order[:, -1], order[:, -2]
    close = evs[rows, first] - evs[rows, second] < CLOSE_BB
    strategy = np.zeros((NUM_CLASSES, 3))
    strategy[rows, first] = 1.0
    blocked = dealt == 0  # the board uses the cards of every combo in the class
    evs[blocked] = np.nan
    strategy[blocked] = 0.0
    close[blocked] = False
    raise_share = shares.get(RAISE, np.zeros(NUM_CLASSES))
    return BestPlay(evs, strategy, close, fold_equity, raise_share, shares[CALL])


def stable_seed(*parts: object) -> int:
    """Deterministic seed from the request, so re-asking gives the same answer."""
    return zlib.crc32(repr(parts).encode())
