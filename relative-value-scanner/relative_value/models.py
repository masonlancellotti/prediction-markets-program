from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional


class SourceKind(str, Enum):
    EXCHANGE = "exchange"
    SPORTSBOOK_REFERENCE = "sportsbook_reference"


class Action(str, Enum):
    IGNORE = "IGNORE"
    WATCH = "WATCH"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    PAPER = "PAPER"
    POSSIBLE_ARB = "POSSIBLE_ARB"


ACTION_SEVERITY = {
    Action.IGNORE: 0,
    Action.WATCH: 1,
    Action.MANUAL_REVIEW: 2,
    Action.PAPER: 3,
    Action.POSSIBLE_ARB: 4,
}


def _validate_probability(value: Optional[float], name: str) -> None:
    if value is None:
        return
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be in [0, 1], got {value!r}")


@dataclass(frozen=True)
class NormalizedMarket:
    venue: str
    market_id: str
    event_name: str
    outcome_name: str
    source_kind: SourceKind
    yes_bid: Optional[float] = None
    yes_ask: Optional[float] = None
    yes_reference_probability: Optional[float] = None
    #: Size at top of book in contracts. Adapters MUST normalize to this unit before
    #: constructing the market; do not pass raw USD or USDC notional.
    liquidity_top_contracts: float = 0.0
    volume_24h: float = 0.0
    settlement_time: Optional[datetime] = None
    captured_at: Optional[datetime] = None
    settlement_rule: str = ""
    is_executable: bool = False
    source_platform: Optional[str] = None
    access_platform: Optional[str] = None
    exchange_venue: Optional[str] = None
    executable_venue: Optional[str] = None
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_kind", SourceKind(self.source_kind))
        if not self.venue:
            raise ValueError("venue is required")
        if not self.market_id:
            raise ValueError("market_id is required")
        if not self.event_name:
            raise ValueError("event_name is required")
        if not self.outcome_name:
            raise ValueError("outcome_name is required")
        _validate_probability(self.yes_bid, "yes_bid")
        _validate_probability(self.yes_ask, "yes_ask")
        _validate_probability(self.yes_reference_probability, "yes_reference_probability")
        if self.yes_bid is not None and self.yes_ask is not None and self.yes_bid > self.yes_ask:
            raise ValueError("yes_bid cannot exceed yes_ask")
        if self.liquidity_top_contracts < 0:
            raise ValueError("liquidity_top_contracts cannot be negative")
        if self.volume_24h < 0:
            raise ValueError("volume_24h cannot be negative")
        if self.source_kind == SourceKind.SPORTSBOOK_REFERENCE and self.is_executable:
            raise ValueError("sportsbook reference markets cannot be executable")
        if self.settlement_time and (self.settlement_time.tzinfo is None or self.settlement_time.utcoffset() is None):
            raise ValueError("settlement_time must include timezone information")
        if self.captured_at and (self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None):
            raise ValueError("captured_at must include timezone information")

    @property
    def midpoint(self) -> Optional[float]:
        if self.yes_bid is not None and self.yes_ask is not None:
            return (self.yes_bid + self.yes_ask) / 2.0
        if self.yes_bid is not None:
            return self.yes_bid
        if self.yes_ask is not None:
            return self.yes_ask
        return self.yes_reference_probability

    def to_dict(self) -> dict[str, Any]:
        return {
            "venue": self.venue,
            "market_id": self.market_id,
            "event_name": self.event_name,
            "outcome_name": self.outcome_name,
            "source_kind": self.source_kind.value,
            "yes_bid": self.yes_bid,
            "yes_ask": self.yes_ask,
            "yes_reference_probability": self.yes_reference_probability,
            "liquidity_top_contracts": self.liquidity_top_contracts,
            "volume_24h": self.volume_24h,
            "settlement_time": self.settlement_time.isoformat() if self.settlement_time else None,
            "captured_at": self.captured_at.isoformat() if self.captured_at else None,
            "settlement_rule": self.settlement_rule,
            "is_executable": self.is_executable,
            "source_platform": self.source_platform,
            "access_platform": self.access_platform,
            "exchange_venue": self.exchange_venue,
            "executable_venue": self.executable_venue,
        }


@dataclass(frozen=True)
class MatchAssessment:
    match_confidence: float
    settlement_mismatch_risk: float
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_probability(self.match_confidence, "match_confidence")
        _validate_probability(self.settlement_mismatch_risk, "settlement_mismatch_risk")

    def to_dict(self) -> dict[str, Any]:
        return {
            "match_confidence": self.match_confidence,
            "settlement_mismatch_risk": self.settlement_mismatch_risk,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class RelativeValueCandidate:
    left: NormalizedMarket
    right: NormalizedMarket
    match: MatchAssessment
    action: Action
    gross_gap: Optional[float]
    fee_adjusted_gap: Optional[float]
    reference_gap: Optional[float]
    limiting_liquidity_top_contracts: float
    direction: str
    fees_applied: Mapping[str, float] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", Action(self.action))

    def to_dict(self) -> dict[str, Any]:
        return {
            "left": self.left.to_dict(),
            "right": self.right.to_dict(),
            "match": self.match.to_dict(),
            "action": self.action.value,
            "gross_gap": self.gross_gap,
            "fee_adjusted_gap": self.fee_adjusted_gap,
            "reference_gap": self.reference_gap,
            "limiting_liquidity_top_contracts": self.limiting_liquidity_top_contracts,
            "direction": self.direction,
            "fees_applied": dict(self.fees_applied),
            "reasons": list(self.reasons),
        }
