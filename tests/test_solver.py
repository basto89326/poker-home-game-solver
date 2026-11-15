import numpy as np
import pytest

from src.engine.cards import class_id, combo_counts
from src.profiler.schema import Stat, default_profile
from src.solver.cfr import CFRSolver
from src.solver.nodelock import NodeLock, lock_by_strength
from src.solver.preflop import PreflopSpot, solve_spot
from src.solver.ranges import parse_range, top_fraction
from src.solver.river import RiverSpot, solve_river
from src.solver.tree import HERO, VILLAIN, build_preflop_tree, build_river_tree


def polar_toy_game() -> tuple:
    """Hero has nuts or air (bets first), villain has a bluff-catcher; pot 1, all-in for 1."""
    root = build_river_tree(start_pot=1.0, stack=1.0, first_to_act=HERO, bet_fractions=[1.0])
    joint = np.array([[1.0], [1.0]])  # hero hands: nuts, air
    share = np.array([[1.0], [0.0]])
    return root, joint, share


class TestRanges:
    def test_parse_shorthand(self):
        weights = parse_range("TT+, ATs+, KQ, 22:0.5")
        for name in ["AA", "TT", "AKs", "ATs", "KQs", "KQo"]:
            assert weights[class_id(name)] == 1.0
        assert weights[class_id("99")] == 0
        assert weights[class_id("ATo")] == 0
        assert weights[class_id("22")] == 0.5

    @pytest.mark.parametrize("bad", ["AAs", "XYZ", "AK:2", "A"])
    def test_parse_rejects_bad_tokens(self, bad):
        with pytest.raises(ValueError):
            parse_range(bad)

    def test_top_fraction_has_exact_combo_share(self):
        strength = np.arange(169, 0, -1, dtype=float)
        weights = top_fraction(strength, 0.25)
        assert (weights * combo_counts()).sum() == pytest.approx(0.25 * 1326)


class TestNodeLock:
    def test_lock_by_strength_assigns_weakest_first(self):
        strength = np.array([3.0, 1.0, 2.0, 4.0])
        weights = np.ones(4)
        lock = lock_by_strength(strength, weights, [(0, 0.375)], 2)
        # Weakest hand (index 1) fully locked, next weakest (index 2) half locked.
        assert lock.fixed[:, 0].tolist() == [0.0, 1.0, 0.5, 0.0]
        assert lock.free.tolist() == [1.0, 0.0, 0.5, 1.0]

    def test_rejects_overfull_rows(self):
        with pytest.raises(ValueError):
            NodeLock(np.array([[0.8, 0.8]]), np.ones((1, 2), bool))


class TestCFR:
    def test_polar_game_converges_to_known_equilibrium(self):
        # Pot 1, pot-sized bet: hero bluffs 1/2 as often as value-bets,
        # villain calls 1/2 the time. The nuts win 1.5 on average (fold: +1, call: +2)
        # and air breaks even, so hero's EV is 0.75 per hand.
        root, joint, share = polar_toy_game()
        solver = CFRSolver(root, joint, share, start_pot=1.0).solve(2000)
        hero = solver.average_strategy("")
        assert hero[0, 1] == pytest.approx(1.0, abs=0.01)  # always bet the nuts
        assert hero[1, 1] == pytest.approx(0.5, abs=0.02)  # bluff half the time
        assert solver.average_strategy("all-in")[0, 1] == pytest.approx(0.5, abs=0.02)
        assert solver.expected_values()[0] == pytest.approx(0.75, abs=0.01)
        assert solver.exploitability() < 0.01

    def test_locked_villain_who_never_calls_gets_bluffed(self):
        root, joint, share = polar_toy_game()
        never_call = NodeLock(np.array([[1.0, 0.0]]), np.zeros((1, 2), bool))
        solver = CFRSolver(root, joint, share, 1.0, {"all-in": never_call}).solve(500)
        assert solver.average_strategy("")[1, 1] == pytest.approx(1.0, abs=0.01)
        assert solver.average_strategy("all-in")[0].tolist() == [1.0, 0.0]

    def test_rejects_unknown_lock(self):
        root, joint, share = polar_toy_game()
        with pytest.raises(KeyError):
            CFRSolver(root, joint, share, 1.0, {"nope": NodeLock.unlocked(1, 2)})

    def test_preflop_tree_amounts(self):
        root = build_preflop_tree(start_pot=4.0, to_call=2.5, three_bet_to=8.5)
        assert root.find("call").pot == 6.5
        assert root.find("3bet/call").invested == (8.5, 6.0)
        assert root.find("3bet/fold").folder == VILLAIN


@pytest.fixture(scope="module")
def preflop_result():
    profile = default_profile("Dave")
    profile.stats["open_raise"] = Stat(0.4, "medium")
    profile.stats["fold_to_3bet"] = Stat(0.3, "medium")
    return solve_spot(PreflopSpot(), profile)


class TestPreflop:
    @pytest.fixture
    def result(self, preflop_result):
        return preflop_result

    def test_spot_numbers(self, result):
        spot = result["spot"]
        assert spot["start_pot"] == 4.0
        assert spot["to_call"] == 2.5
        assert spot["pot_odds"] == pytest.approx(2.5 / 6.5, abs=1e-4)

    def test_summary_covers_every_combo(self, result):
        for mode in ("exploit", "baseline"):
            total = sum(v["combos"] for v in result[mode]["summary"].values())
            assert total == pytest.approx(1326, abs=0.5)

    def test_obvious_hands(self, result):
        hands = {h["name"]: h for h in result["exploit"]["hands"]}
        assert hands["AA"]["freqs"]["fold"] == 0
        assert hands["72o"]["freqs"]["fold"] == pytest.approx(1.0)

    def test_lock_is_respected(self, result):
        assert result["exploit"]["villain"]["fold_to_3bet"] == pytest.approx(0.3, abs=1e-3)
        assert result["exploit"]["exploitability"] < 0.01

    def test_invalid_spot(self):
        with pytest.raises(ValueError):
            PreflopSpot(opener="BTN", hero="CO").validate()


class TestRiver:
    SPOT = RiverSpot("Ks7d2c9h3s", "AA,KQ,QJs,65s", "AK,KJ,99,JTs,T8s", pot=10, stack=40)

    def test_converges(self):
        result = solve_river(self.SPOT)
        assert result["exploitability"] < 0.05
        assert result["hero_nodes"][0]["path"] == "check"  # villain acts first by default

    def test_profile_locks_are_reported_and_help_hero(self):
        profile = default_profile("Dave")
        profile.stats["fold_to_cbet"] = Stat(0.6, "high")
        baseline = solve_river(self.SPOT)
        exploit = solve_river(self.SPOT, profile)
        assert exploit["locks"]
        # Hero can only gain from an opponent who is restricted.
        assert exploit["hero_ev"] >= baseline["hero_ev"] - 0.05

    def test_board_must_have_five_cards(self):
        with pytest.raises(ValueError):
            solve_river(RiverSpot("Ks7d2c", "AA", "KK", pot=10, stack=40))
