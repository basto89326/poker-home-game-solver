import numpy as np
import pytest

from src.engine.cards import (NUM_CLASSES, class_compatibility, class_id, class_name, combo_counts,
                              parse_board, parse_card)
from src.engine.equity import load_preflop_equity, showdown_share
from src.engine.evaluator import (FLUSH, FULL_HOUSE, HIGH_CARD, PAIR, QUADS, STRAIGHT,
                                  STRAIGHT_FLUSH, TRIPS, TWO_PAIR, category_of, evaluate)


def score(text: str) -> int:
    return int(evaluate(np.array([parse_board(text)]))[0])


class TestCards:
    def test_parse_card_round_trip(self):
        assert parse_card("As") == 51
        assert parse_card("2c") == 0

    @pytest.mark.parametrize("bad", ["A", "1s", "Ax", "Asd"])
    def test_parse_card_rejects_bad_input(self, bad):
        with pytest.raises(ValueError):
            parse_card(bad)

    def test_parse_board_rejects_duplicates(self):
        with pytest.raises(ValueError):
            parse_board("AsAs")

    def test_grid_layout(self):
        assert class_name(0) == "AA"
        assert class_name(1) == "AKs"  # above the diagonal: suited
        assert class_name(13) == "AKo"  # below the diagonal: offsuit
        assert class_name(168) == "22"
        assert class_id("ako") == 13

    def test_combo_counts(self):
        counts = combo_counts()
        assert counts.sum() == 1326
        assert counts[class_id("AA")] == 6
        assert counts[class_id("AKs")] == 4
        assert counts[class_id("AKo")] == 12

    def test_compatibility(self):
        compat = class_compatibility()
        assert compat[class_id("AA"), class_id("AA")] == 6
        assert compat[class_id("AA"), class_id("KK")] == 36
        assert compat[class_id("AKs"), class_id("AA")] == 4 * 3  # each AKs leaves 3 AA combos
        assert np.allclose(compat, compat.T)


class TestEvaluator:
    @pytest.mark.parametrize("hand, category", [
        ("AhKhQhJhTh2c3d", STRAIGHT_FLUSH),
        ("Ah2h3h4h5h9c9d", STRAIGHT_FLUSH),
        ("9c9d9h9sKd2c2d", QUADS),
        ("7c7d7hKcKdKh2c", FULL_HOUSE),
        ("2h5h9hJhKh3c3d", FLUSH),
        ("9cTdJhQsKh2c2d", STRAIGHT),
        ("Ac2d3h4s5h9cKd", STRAIGHT),
        ("7c7d7h2s9hJcKd", TRIPS),
        ("KcKd9c9d4h4s2c", TWO_PAIR),
        ("KcKd9c7d4h3s2c", PAIR),
        ("Ac9d7c5d4h3s8c", HIGH_CARD),
    ])
    def test_categories(self, hand, category):
        assert category_of(score(hand)) == category

    def test_wheel_is_lowest_straight(self):
        assert score("Ac2d3h4s5hKcQd") < score("2c3d4h5s6hKcQd")

    def test_two_pair_kicker_can_come_from_third_pair(self):
        # KK 99 with 88 on board: kicker is the 8, beating a 7 kicker.
        assert score("KcKd9c9d8h8s2c") > score("KcKd9c9d7h2s3c")

    def test_quads_kicker_uses_best_remaining_rank(self):
        assert score("9c9d9h9sKdKc2d") > score("9c9d9h9sQd2c2d")

    def test_unused_kickers_tie(self):
        # Five best cards are the same; the sixth/seventh card must not matter.
        assert score("AcKdQh9s7c3d2h") == score("AcKdQh9s7c4d3h")

    def test_flush_beats_straight_and_compares_by_rank(self):
        assert score("2h5h9hJhKh3c4d") > score("9cTdJhQsKh2c2d")
        assert score("2h5h9hJhAh3c4d") > score("2h5h9hJhKh3c4d")

    def test_rejects_bad_shape(self):
        with pytest.raises(ValueError):
            evaluate(np.zeros((2, 4), dtype=int))


class TestEquity:
    def test_showdown_share(self):
        share = showdown_share(np.array([5, 3]), np.array([3, 5]))
        assert share.tolist() == [[1.0, 0.5], [0.5, 0.0]]

    def test_preflop_table_is_consistent(self):
        equity = load_preflop_equity()
        assert equity.shape == (NUM_CLASSES, NUM_CLASSES)
        assert np.allclose(equity + equity.T, 1.0)
        # Well-known matchups, within Monte Carlo error.
        assert equity[class_id("AA"), class_id("KK")] == pytest.approx(0.82, abs=0.03)
        assert equity[class_id("AKs"), class_id("QQ")] == pytest.approx(0.46, abs=0.03)
        assert equity[class_id("AA"), class_id("72o")] == pytest.approx(0.88, abs=0.03)
