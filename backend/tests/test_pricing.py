from decimal import Decimal

from app.observability.pricing import compute_cost


def test_compute_cost_matches_hand_calculation_gpt_6_sol() -> None:
    # (1000 - 200) * 2.00 + 200 * 0.20 + 500 * 10.00, all per 1M tokens
    # = 1600 + 40 + 5000 = 6640 -> 6640 / 1_000_000
    cost = compute_cost("gpt-6-sol", input_tokens=1000, cached_tokens=200, output_tokens=500)
    assert cost == Decimal("6640") / Decimal("1000000")


def test_compute_cost_matches_hand_calculation_gpt_6_luna() -> None:
    # (2000 - 500) * 0.10 + 500 * 0.01 + 1000 * 0.50
    # = 150 + 5 + 500 = 655 -> 655 / 1_000_000
    cost = compute_cost("gpt-6-luna", input_tokens=2000, cached_tokens=500, output_tokens=1000)
    assert cost == Decimal("655") / Decimal("1000000")


def test_compute_cost_zero_usage_is_zero() -> None:
    cost = compute_cost("gpt-6-sol", input_tokens=0, cached_tokens=0, output_tokens=0)
    assert cost == Decimal("0")
