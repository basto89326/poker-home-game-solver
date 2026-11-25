import numpy as np
import pytest

from src.engine.cards import class_id, combo_counts
from src.engine.equity import load_preflop_equity
from src.profiler.schema import Stat, default_profile
from src.solver.bestplay import CLOSE_BB, best_play
from src.solver.handplay import YOU, GameSettings, SeatSpec, analyze
from src.solver.predict import CALL, FOLD, RAISE, predicted_strategy, update_range
from src.solver.ranges import preflop_strength
from src.solver.table import Action, BettingRound


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


class TestBestPlay:
    def test_obvious_hands_vs_an_open(self, equity):
        r = BettingRound([200, 200])
        r.apply(Action("raise", 5))  # SB opens; BB (hero) to act
        sticky = default_profile("V")
        sticky.stats["fold_to_3bet"] = Stat(0.1, "high")  # so 3-bet bluffs don't work
        ranges = [np.ones(169), np.ones(169)]
        bp = best_play(r, ranges, [sticky, default_profile("You")], equity, raise_to=15,
                       samples=150, seed=1)
        assert bp.strategy[class_id("AA"), RAISE] == 1
        assert bp.strategy[class_id("72o"), RAISE] == 0

    def test_close_hands_are_flagged(self, equity):
        r = BettingRound([200, 200])
        r.apply(Action("raise", 5))
        bp = best_play(r, [np.ones(169)] * 2, [default_profile("V"), default_profile("You")], equity,
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
        ranges = [np.ones(169), np.ones(169)]
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

    def test_money_in_explanations(self):
        result = analyze([seat("dave"), seat(YOU)], [Action("raise", 5)], self.SETTINGS)
        why = {h["name"]: h["why"] for h in result["analysis"]["hands"]}
        assert "$" in why["AA"]

    def test_action_after_round_ends(self):
        with pytest.raises(ValueError, match="ended"):
            analyze([seat("dave"), seat(YOU)], [Action("fold"), Action("fold")], self.SETTINGS)
