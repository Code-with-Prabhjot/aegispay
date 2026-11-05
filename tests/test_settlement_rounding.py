"""Partial-capture amounts must match exact math: the audit found float rounding a cent low."""

from fractions import Fraction
from math import floor

import pytest

from backend.app.services.paypal_escrow import compute_settlement_split_micros

BUDGETS = [1_000_000, 3_330_000, 7_770_000, 9_990_000, 15_000_000, 25_000_000, 49_990_000, 100_000_000]


def exact_capture(budget: int, score: Fraction) -> int:
    factor = Fraction(1, 2) + Fraction(2, 5) * ((score - Fraction(3, 5)) / Fraction(3, 10))
    return floor(budget * factor / 10_000) * 10_000


@pytest.mark.parametrize("budget", BUDGETS)
def test_partial_capture_matches_exact_math_at_every_score_to_four_places(budget):
    wrong = []
    for k in range(6000, 9000):  # S = 0.6000 .. 0.8999: the whole partial band the arbiter can emit
        got = compute_settlement_split_micros(budget, k / 10_000)["capture_micros"]
        want = exact_capture(budget, Fraction(k, 10_000))
        if got != want:
            wrong.append((k / 10_000, want, got))
    assert not wrong, wrong[:5]


@pytest.mark.parametrize("score,expected", [(0.60, 7_500_000), (0.75, 10_500_000), (0.601, 7_520_000),
                                            (0.604, 7_580_000), (0.6255, 8_010_000)])
def test_documented_and_previously_miscomputed_amounts(score, expected):
    assert compute_settlement_split_micros(15_000_000, score)["capture_micros"] == expected


def test_band_edges_and_cent_flooring_are_unchanged():
    assert compute_settlement_split_micros(15_000_000, 0.90)["decision"] == "FULL_CAPTURE"
    assert compute_settlement_split_micros(15_000_000, 0.5999)["decision"] == "VOID_AUTHORIZATION"
    split = compute_settlement_split_micros(15_000_000, 0.60)
    assert split["decision"] == "PARTIAL_CAPTURE"
    assert split["capture_micros"] % 10_000 == 0
    assert split["capture_micros"] + split["buyer_released_micros"] == 15_000_000
