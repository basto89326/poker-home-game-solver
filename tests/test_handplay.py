from dataclasses import replace

import numpy as np
import pytest

from src.engine.cards import all_combos, class_id, combo_classes, combo_counts, combo_name, parse_board
from src.engine.equity import board_strength, load_preflop_equity
from src.profiler.schema import Stat, default_profile
from src.solver.bestplay import CLOSE_BB, best_play
from src.solver.handplay import YOU, GameSettings, SeatSpec, analyze
from src.solver.predict import (CALL, FOLD, RAISE, class_average, postflop_shares, postflop_strategy,
                                predicted_strategy, update_range)
from src.solver.ranges import preflop_strength
from src.solver.table import Action, BettingRound, settle, side_pots


@pytest.fixture(scope="module")
def equity():
    return load_preflop_equity()


@pytest.fixture(scope="module")
def strength(equity):
    return preflop_strength(equity)


def seat(player: str, **stats) -> SeatSpec:
    profile = default_profile(player.title())
    for name, value in stats.items():
        profile.stats[name] = Stat(value, "medium")
    return SeatSpec(player, profile.name, profile, True, 200.0)


class TestBettingRound:
    def test_six_handed_order_and_blinds(self):
        r = BettingRound([200] * 6)
        assert r.positions == ("UTG", "HJ", "CO", "BTN", "SB", "BB")
        assert r.actor == 0 and r.pot == 1.5
        assert r.to_call(0) == 1.0

    def test_heads_up_small_blind_acts_first(self):
        r = BettingRound([200, 200])
        assert r.positions == ("SB", "BB") and r.actor == 0

    def test_raise_reopens_action_and_round_ends(self):
        r = BettingRound([200] * 3)  # BTN, SB, BB
        r.apply(Action("raise", 5))
        r.apply(Action("fold"))
        r.apply(Action("raise", 15))  # BB 3-bets
        assert r.actor == 0 and r.raises == 2
        assert r.min_raise_to(0) == 25  # 15 + the last raise of 10
        r.apply(Action("call"))
        assert r.complete and r.pot == 0.5 + 15 + 15

    def test_big_blind_gets_option_after_limps(self):
        r = BettingRound([200] * 3)
        r.apply(Action("call"))
        r.apply(Action("call"))
        assert r.actor == 2 and r.to_call(2) == 0
        r.apply(Action("check"))
        assert r.complete

    def test_everyone_folds_to_big_blind(self):
        r = BettingRound([200] * 3)
        r.apply(Action("fold"))
        r.apply(Action("fold"))
        assert r.complete and r.in_hand() == [2]

    def test_short_all_in_and_illegal_actions(self):
        r = BettingRound([200, 3, 200])  # SB has only 3 BB
        with pytest.raises(ValueError):
            r.apply(Action("check"))  # BTN faces the big blind
        with pytest.raises(ValueError):
            r.apply(Action("raise", 1.5))  # below the minimum raise
        r.apply(Action("raise", 5))
        r.apply(Action("call"))  # SB calls all-in for 3
        assert r.stack_left(1) == 0 and not r.can_act(1)
        assert r.actor == 2

    def test_default_raise_sizes(self):
        r = BettingRound([200] * 6)
        assert r.default_raise_to(0, open_size=5) == 5
        r.apply(Action("raise", 5))
        assert r.default_raise_to(1, open_size=5) == 15  # 3x the open

    def test_flop_starts_with_first_player_after_the_button(self):
        r = BettingRound([200] * 3)  # BTN, SB, BB
        r.apply(Action("raise", 5))
        r.apply(Action("fold"))
        r.apply(Action("call"))
        assert r.next_betting_street() == "flop"
        flop = r.next_street()
        assert flop.street == "flop" and flop.actor == 2  # the SB folded, so the BB is first
        assert flop.pot == 10.5 and flop.bets == [0.0] * 3 and flop.stack_left(0) == 195
        assert flop.opening_bet and flop.default_raise_to(2, open_size=5) == 5.25  # half the pot
        flop.apply(Action("raise", 3))  # a bet
        assert not flop.opening_bet and flop.default_raise_to(0, open_size=5) == 9  # raise to 3x
        flop.apply(Action("call"))
        assert flop.complete and flop.pot == 16.5 and flop.next_betting_street() == "turn"

    def test_heads_up_big_blind_acts_first_after_the_flop(self):
        r = BettingRound([200, 200])
        r.apply(Action("call"))
        r.apply(Action("check"))
        assert r.next_street().actor == 1

    def test_no_more_streets_when_all_in_or_after_the_river(self):
        r = BettingRound([200, 10])
        r.apply(Action("raise", 10))
        r.apply(Action("call"))  # the BB is all-in
        assert r.complete and r.next_betting_street() is None
        with pytest.raises(ValueError):
            r.next_street()
        r = BettingRound([200, 200])
        r.apply(Action("call"))
        r.apply(Action("check"))
        for _ in range(3):
            r = r.next_street()
            r.apply(Action("check"))
            r.apply(Action("check"))
        assert r.street == "river" and r.next_betting_street() is None


class TestPots:
    def test_single_pot_with_folded_chips(self):
        pots = side_pots([5, 5, 1], [False, False, True])  # the BB folded their blind
        assert pots == [(11, [0, 1])]

    def test_short_all_in_makes_a_side_pot(self):
        # Seat 0 is all-in for 10; seats 1 and 2 put in 30; seat 3 folded after 2.
        pots = side_pots([10, 30, 30, 2], [False, False, False, True])
        assert pots == [(32, [0, 1, 2]), (40, [1, 2])]
        assert settle(pots, [[0], [2]], 4, chip=1) == [32, 0, 40, 0]  # the short stack wins only the main pot
        assert settle(pots, [[1, 2], [1, 2]], 4, chip=1) == [0, 36, 36, 0]  # split pot

    def test_odd_chip_goes_left_of_the_button(self):
        # Four seats (CO, BTN, SB, BB): a 3-chip pot split by the CO and the SB.
        assert settle([(1.5, [0, 2])], [[0, 2]], 4, chip=0.5) == [0.5, 0, 1.0, 0]
        # Heads-up the BB is left of the button (the SB).
        assert settle([(1.5, [0, 1])], [[0, 1]], 2, chip=0.5) == [0.5, 1.0]

    def test_uncalled_bet_goes_back(self):
        pots = side_pots([5, 20], [False, False])  # seat 1 is covered by nobody above 5
        assert pots == [(10, [0, 1]), (15, [1])]
        assert settle(pots, [[0], []], 2, chip=1) == [10, 15]

    def test_settle_rejects_bad_winners(self):
        pots = [(10, [0, 1])]
        with pytest.raises(ValueError, match="at least one winner"):
            settle(pots, [[]], 2, chip=1)
        with pytest.raises(ValueError, match="didn't fold"):
            settle(pots, [[2]], 3, chip=1)
        with pytest.raises(ValueError, match="each of the 1 pots"):
            settle(pots, [], 2, chip=1)


class TestPredict:
    def test_unopened_shares(self, strength):
        profile = default_profile("Dave")
        profile.stats["open_raise"] = Stat(0.1, "high")
        profile.stats["vpip"] = Stat(0.5, "high")
        freqs = predicted_strategy(profile, np.ones(169), strength, raises=0, can_check=False)
        shares = combo_counts() @ freqs / 1326
        assert shares[RAISE] == pytest.approx(0.1, abs=1e-6)
        assert shares[CALL] == pytest.approx(0.4, abs=1e-6)
        assert freqs[class_id("AA"), RAISE] == 1 and freqs[class_id("72o"), FOLD] == 1

    def test_free_check_never_folds(self, strength):
        freqs = predicted_strategy(default_profile("X"), np.ones(169), strength, 0, can_check=True)
        assert freqs[:, FOLD].sum() == 0

    def test_range_update(self, strength):
        freqs = predicted_strategy(default_profile("X"), np.ones(169), strength, 0, False)
        narrowed, fits = update_range(np.ones(169), freqs, RAISE)
        assert fits and narrowed[class_id("72o")] == 0 and narrowed[class_id("AA")] == 1
        never = np.zeros((169, 3))
        never[:, FOLD] = 1
        same, fits = update_range(np.ones(169), never, RAISE)
        assert not fits and same.sum() == 169

    def test_postflop_shares(self):
        profile = default_profile("X")
        profile.stats["aggression"] = Stat(0.4, "high")
        profile.stats["fold_to_cbet"] = Stat(0.5, "high")
        assert postflop_shares(profile, 0) == pytest.approx((0.6, 0.0, 0.4))
        assert postflop_shares(profile, 1) == pytest.approx((0.5, 0.3, 0.2))  # raise 40% of the 50% left

    def test_river_bets_value_and_bluffs_the_weak_half(self):
        board = parse_board("Ks7d2c9h3s")
        profile = default_profile("X")
        profile.stats["aggression"] = Stat(0.3, "high")
        profile.stats["river_bluff"] = Stat(0.2, "high")
        weights = np.ones(1326)
        freqs = postflop_strategy(profile, weights, board, raises=0, can_check=True)
        live = board_strength(tuple(board)) > 0
        shares = (weights * live) @ freqs / live.sum()
        assert freqs[:, FOLD].sum() == 0  # checking is free
        assert shares[RAISE] == pytest.approx(0.3 + 0.5 * 0.2, abs=1e-6)
        names = [combo_name(tuple(c)) for c in all_combos()]
        assert freqs[names.index("KhKd"), RAISE] == 1  # top set always bets
        weakest = np.flatnonzero(live)[np.argmin(board_strength(tuple(board))[live])]
        assert freqs[weakest, RAISE] == pytest.approx(0.2)  # air bluffs river_bluff of the time

    def test_class_average(self):
        values = np.arange(1326, dtype=float)
        weights = (combo_classes() == class_id("AA")).astype(float)
        avg = class_average(values, weights)
        assert avg[class_id("AA")] == pytest.approx(2.5) and avg[class_id("KK")] == 0  # AA = combos 0..5


class TestBestPlay:
    def test_obvious_hands_vs_an_open(self, equity):
        r = BettingRound([200, 200])
        r.apply(Action("raise", 5))  # SB opens; BB (hero) to act
        sticky = default_profile("V")
        sticky.stats["fold_to_3bet"] = Stat(0.1, "high")  # so 3-bet bluffs don't work
        ranges = [np.ones(1326), np.ones(1326)]
        bp = best_play(r, ranges, [sticky, default_profile("You")], equity, raise_to=15,
                       samples=150, seed=1)
        assert bp.strategy[class_id("AA"), RAISE] == 1
        assert bp.strategy[class_id("72o"), RAISE] == 0

    def test_close_hands_are_flagged(self, equity):
        r = BettingRound([200, 200])
        r.apply(Action("raise", 5))
        bp = best_play(r, [np.ones(1326)] * 2, [default_profile("V"), default_profile("You")], equity,
                       raise_to=15, samples=100, seed=3)
        assert np.all(bp.strategy.sum(axis=1) == 1.0)
        ordered = np.sort(np.nan_to_num(bp.evs, nan=-np.inf), axis=1)
        gap = ordered[:, -1] - ordered[:, -2]
        assert np.array_equal(bp.close, gap < CLOSE_BB) and 0 < bp.close.sum() < 169
        assert np.all(bp.evs[:, FOLD] == 0)

    def test_raising_into_a_folder_wins_the_pot(self, equity):
        r = BettingRound([200, 200])  # hero is the SB; villain folds everything to a raise
        folder = default_profile("V")
        folder.stats["three_bet"] = Stat(0.0, "high")
        folder.stats["call_open"] = Stat(0.0, "high")
        ranges = [np.ones(1326), np.ones(1326)]
        bp = best_play(r, ranges, [default_profile("You"), folder], equity, raise_to=5, samples=60, seed=2)
        assert np.allclose(bp.fold_equity, 1.0)
        # Measured from the decision: the SB's own 0.5 comes back plus the big blind.
        assert np.allclose(bp.evs[:, RAISE], 1.5)
        assert bp.strategy[:, RAISE].sum() == 169


class TestAnalyze:
    SETTINGS = GameSettings()

    def test_requires_exactly_one_you(self):
        with pytest.raises(ValueError, match="you"):
            analyze([seat("dave"), seat("sarah")], [], self.SETTINGS)

    def test_your_seat_has_its_own_mode(self):
        seats = [seat(YOU), seat("dave")]
        result = analyze(seats, [], self.SETTINGS, mode="predicted")
        assert result["analysis"]["is_you"] and result["analysis"]["mode"] == "best"
        result = analyze(seats, [], self.SETTINGS, mode="best", your_mode="predicted")
        assert result["analysis"]["mode"] == "predicted"

    def test_predicted_mode_has_evs_but_no_close_calls(self):
        result = analyze([seat(YOU), seat("dave")], [], self.SETTINGS, your_mode="predicted")
        hands = result["analysis"]["hands"]
        assert all(h["evs"][FOLD] == 0 and h["evs"][CALL] is not None for h in hands)
        assert not any(h["close"] for h in hands)

    def test_ranges_narrow_and_log_records_actions(self):
        seats = [seat("dave", open_raise=0.1), seat("sarah"), seat(YOU)]
        result = analyze(seats, [Action("raise", 5), Action("fold")], self.SETTINGS, mode="predicted")
        assert [e["kind"] for e in result["log"]] == ["raise", "fold"]
        assert result["state"]["actor"] == 2
        assert result["analysis"]["name"] == "You"

    def test_explanations_for_your_seat_use_second_person(self):
        result = analyze([seat(YOU), seat("dave")], [], self.SETTINGS, your_mode="predicted")
        why = {h["name"]: h["why"] for h in result["analysis"]["hands"]}["AA"]
        assert why.startswith("With no raise yet, your profile raises your strongest")
        assert "you're most likely to raise" in why and "You's" not in why
        friend = analyze([seat("dave"), seat(YOU)], [], self.SETTINGS, mode="predicted")
        why = {h["name"]: h["why"] for h in friend["analysis"]["hands"]}["AA"]
        assert "Dave's profile raises their strongest" in why and "they're most likely" in why

    def test_money_in_explanations(self):
        result = analyze([seat("dave"), seat(YOU)], [Action("raise", 5)], self.SETTINGS)
        why = {h["name"]: h["why"] for h in result["analysis"]["hands"]}
        assert "$" in why["AA"]

    def test_action_after_round_ends(self):
        with pytest.raises(ValueError, match="ended"):
            analyze([seat("dave"), seat(YOU)], [Action("fold"), Action("fold")], self.SETTINGS)

    LIMPED = [Action("call"), Action("check")]  # heads-up: dave (SB) limps, you (BB) check
    FLOP = parse_board("Ks7d2c")

    def test_flop_needs_its_cards(self):
        seats = [seat("dave"), seat(YOU)]
        result = analyze(seats, self.LIMPED, self.SETTINGS)
        assert result["analysis"] is None
        assert result["state"]["needs_street"] == "flop" and result["state"]["cards_needed"] == 3
        with pytest.raises(ValueError, match="choose the flop cards first"):
            analyze(seats, self.LIMPED + [Action("check")], self.SETTINGS)
        with pytest.raises(ValueError, match="before the preflop betting round ended"):
            analyze(seats, self.LIMPED[:1], self.SETTINGS, board=self.FLOP)

    def test_flop_analysis(self):
        seats = [seat("dave"), seat(YOU)]
        result = analyze(seats, self.LIMPED, self.SETTINGS, mode="predicted", board=self.FLOP)
        analysis = result["analysis"]
        assert analysis["street"] == "flop" and analysis["is_you"] and analysis["opening_bet"]
        assert result["state"]["board"] == ["Ks", "7d", "2c"] and result["state"]["pot"] == 2
        hands = {h["name"]: h for h in analysis["hands"]}
        assert hands["KK"]["combos"] == 3 and hands["AA"]["combos"] == 6
        assert all(h["evs"][FOLD] == 0 for h in hands.values())
        assert [e["kind"] for e in result["log"]] == ["call", "check", "board"]
        bet = analyze(seats, self.LIMPED + [Action("raise", 1)], self.SETTINGS, board=self.FLOP)
        assert bet["log"][-1]["kind"] == "bet" and bet["analysis"]["name"] == "Dave"
        assert not bet["analysis"]["opening_bet"]

    def test_default_bet_is_whole_chips(self):
        seats = [seat("dave"), seat(YOU)]
        actions = [Action("raise", 3.5), Action("call")]  # pot of 7 BB = $0.70
        result = analyze(seats, actions, self.SETTINGS, mode="predicted", board=self.FLOP)
        assert result["analysis"]["raise_to"] == pytest.approx(3.5)  # half pot: exactly $0.35
        three = [seat("dave"), seat("sarah"), seat(YOU)]  # BTN, SB, BB
        actions = [Action("raise", 2.5), Action("fold"), Action("call")]  # pot of 5.5 BB = $0.55
        odd = analyze(three, actions, self.SETTINGS, mode="predicted", board=self.FLOP)
        size = odd["analysis"]["raise_to"]  # half pot would be $0.275
        assert size == pytest.approx(odd["state"]["legal"]["default_raise_to"])
        assert size / 0.5 == pytest.approx(round(size / 0.5))  # a whole number of $0.05 chips

    def test_stacks_and_bets_are_rounded_to_chips(self):
        seats = [replace(seat("dave"), stack=200.2), seat(YOU)]  # $20.02
        result = analyze(seats, [Action("raise", 5.3)], self.SETTINGS, mode="predicted")
        assert result["state"]["seats"][0]["stack"] == pytest.approx(200 - 5.5)  # $20.00 - $0.55
        assert result["log"][0]["to"] == pytest.approx(5.5)

    def test_board_blocked_hands(self):
        seats = [seat("dave"), seat(YOU)]
        result = analyze(seats, self.LIMPED, self.SETTINGS, board=parse_board("AsAdAh"))
        aa = next(h for h in result["analysis"]["hands"] if h["name"] == "AA")
        assert aa["combos"] == 0 and not aa["in_range"] and aa["evs"] == [None, None, None]

    def test_settlement_and_profiles_untouched(self):
        seats = [seat("dave"), seat(YOU)]
        before = seats[0].profile.to_dict()
        over = analyze(seats, [Action("raise", 5), Action("fold")], self.SETTINGS)
        assert over["state"]["pots"] == [{"amount": 6, "eligible": [0]}]
        result = analyze(seats, [Action("raise", 5), Action("fold")], self.SETTINGS, winners=[[]])
        assert result["settlement"] == {"payouts": [6, 0], "stacks": [201, 199]}
        assert seats[0].profile.to_dict() == before
        with pytest.raises(ValueError, match="isn't over"):
            analyze(seats, [], self.SETTINGS, winners=[[0]])

    def test_bad_boards(self):
        seats = [seat("dave"), seat(YOU)]
        with pytest.raises(ValueError, match="0, 3, 4 or 5"):
            analyze(seats, self.LIMPED, self.SETTINGS, board=self.FLOP[:2])
        with pytest.raises(ValueError, match="once"):
            analyze(seats, self.LIMPED, self.SETTINGS, board=[self.FLOP[0]] * 3)
