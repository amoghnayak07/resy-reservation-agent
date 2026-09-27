"""Model pricing and cost computation. Rates come from CLAUDE.md; verify against
OpenAI's pricing page before changing (last checked 2026-09-26)."""

from decimal import Decimal

PER_MILLION = Decimal("1000000")


class ModelPricing:
    def __init__(
        self, input_rate: Decimal, cached_input_rate: Decimal, output_rate: Decimal
    ) -> None:
        self.input_rate = input_rate
        self.cached_input_rate = cached_input_rate
        self.output_rate = output_rate


MODEL_PRICING: dict[str, ModelPricing] = {
    "gpt-6-sol": ModelPricing(
        input_rate=Decimal("2.00"),
        cached_input_rate=Decimal("0.20"),
        output_rate=Decimal("10.00"),
    ),
    "gpt-6-luna": ModelPricing(
        input_rate=Decimal("0.10"),
        cached_input_rate=Decimal("0.01"),
        output_rate=Decimal("0.50"),
    ),
}


def compute_cost(model: str, input_tokens: int, cached_tokens: int, output_tokens: int) -> Decimal:
    """Cost = (input - cached) * input_rate + cached * cached_rate + output * output_rate,
    rates per 1M tokens (CLAUDE.md)."""
    pricing = MODEL_PRICING[model]
    non_cached_input = input_tokens - cached_tokens
    return (
        Decimal(non_cached_input) * pricing.input_rate
        + Decimal(cached_tokens) * pricing.cached_input_rate
        + Decimal(output_tokens) * pricing.output_rate
    ) / PER_MILLION
