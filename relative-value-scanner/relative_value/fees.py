from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class FeeModel(ABC):
    @abstractmethod
    def fee_for_leg(self, price: float) -> float:
        raise NotImplementedError


@dataclass(frozen=True)
class FlatFeeModel(FeeModel):
    per_leg: float

    def fee_for_leg(self, price: float) -> float:
        return self.per_leg


@dataclass(frozen=True)
class KalshiTieredFeeModel(FeeModel):
    """Conservative Kalshi fee approximation.

    Kalshi's real event-contract fee schedule is proportional to
    ``0.07 * p * (1 - p)``. This model deliberately uses
    ``rate * min(p, 1 - p)`` with a cap, which is an upper bound for prices in
    ``[0, 1]`` because ``min(p, 1 - p) >= p * (1 - p)``. The goal is
    safety-over-precision: marginal apparent edges should be rejected until a
    venue-specific fee schedule is wired in and tested.
    """

    minimum_fee: float = 0.0075
    rate: float = 0.07
    worst_tier_cap: float = 0.02

    def fee_for_leg(self, price: float) -> float:
        if not 0.0 <= price <= 1.0:
            raise ValueError(f"price must be in [0, 1], got {price!r}")
        price_risk = min(price, 1.0 - price)
        tiered_fee = self.rate * price_risk
        return round(min(max(self.minimum_fee, tiered_fee), self.worst_tier_cap), 6)


class NoFeeModel(FeeModel):
    def fee_for_leg(self, price: float) -> float:
        if not 0.0 <= price <= 1.0:
            raise ValueError(f"price must be in [0, 1], got {price!r}")
        return 0.0


@dataclass(frozen=True)
class PolymarketConservativeFeeModel(FeeModel):
    """Conservative taker-fee diagnostics for Polymarket CLOB markets.

    Official Polymarket documentation describes fees as taker-only and
    category-specific: ``shares * taker_fee_rate * p * (1 - p)``. This model
    deliberately applies the taker rate to diagnostics and never assumes maker
    execution or zero fees. Unknown categories use the conservative
    ``other/general`` rate.
    """

    source_url: str = "https://docs.polymarket.com/trading/fees"
    source_version: str = "official_category_schedule_2026_05_22"
    assumption_type: str = "taker_fee_official_category_schedule_conservative"
    maker_fee_rate: float = 0.0
    conservative_unknown_category: str = "other_general"

    def fee_for_leg(self, price: float) -> float:
        return self.fee_for_leg_for_category(price, category=None)

    def fee_for_leg_for_category(self, price: float, category: str | None = None) -> float:
        if not 0.0 <= price <= 1.0:
            raise ValueError(f"price must be in [0, 1], got {price!r}")
        rate = self.rate_for_category(category)
        return round(rate * price * (1.0 - price), 6)

    def rate_for_category(self, category: str | None = None) -> float:
        key = _normalize_polymarket_fee_category(category)
        return _POLYMARKET_TAKER_RATES.get(key, _POLYMARKET_TAKER_RATES[self.conservative_unknown_category])

    def category_key(self, category: str | None = None) -> str:
        key = _normalize_polymarket_fee_category(category)
        if key in _POLYMARKET_TAKER_RATES:
            return key
        return self.conservative_unknown_category


_POLYMARKET_TAKER_RATES = {
    "crypto": 0.07,
    "sports": 0.03,
    "finance": 0.04,
    "politics": 0.04,
    "economics": 0.05,
    "culture": 0.05,
    "weather": 0.05,
    "other_general": 0.05,
    "mentions": 0.04,
    "tech": 0.04,
    # Keep geopolitical fee-free markets conservative for diagnostics unless
    # market-specific CLOB fee parameters are added in a future reviewed step.
    "geopolitics": 0.05,
}


def _normalize_polymarket_fee_category(category: str | None) -> str:
    if not category:
        return "other_general"
    normalized = category.strip().lower().replace("-", "_").replace("/", "_").replace(" ", "_")
    aliases = {
        "macro": "economics",
        "business": "finance",
        "companies": "finance",
        "company": "finance",
        "ai": "tech",
        "technology": "tech",
        "other": "other_general",
        "general": "other_general",
        "other__general": "other_general",
    }
    return aliases.get(normalized, normalized)
