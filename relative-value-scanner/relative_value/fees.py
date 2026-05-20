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
