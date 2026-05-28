from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SCHEMA_VERSION = 0


@dataclass(frozen=True)
class NormalizedSportsSelection:
    venue: str | None = None
    event_id: str | None = None
    selection_id: str | None = None
    market_type: str | None = None
    participants: tuple[str, ...] = ()
    home_team: str | None = None
    away_team: str | None = None
    odds_format: str | None = None
    stake_payout_mechanics: str | None = None
    void_rules: str | None = None
    cancellation_rules: str | None = None
    limits_max_stake: float | None = None
    market_suspension_state: str | None = None
    bet_acceptance_risk: str | None = None
    region_restrictions: str | None = None
    settlement_source: str | None = None
    fee_or_commission: str | None = None
    odds_timestamp: str | None = None
    depth_or_max_stake: float | None = None
    currency: str | None = None
    line: float | None = None
    threshold: float | None = None
    operator: str | None = None
    payout_mechanics_class: str | None = None
    raw_evidence_paths: tuple[str, ...] = ()
    diagnostic_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "venue": self.venue,
            "event_id": self.event_id,
            "selection_id": self.selection_id,
            "market_type": self.market_type,
            "participants": list(self.participants),
            "home_team": self.home_team,
            "away_team": self.away_team,
            "odds_format": self.odds_format,
            "stake_payout_mechanics": self.stake_payout_mechanics,
            "void_rules": self.void_rules,
            "cancellation_rules": self.cancellation_rules,
            "limits_max_stake": self.limits_max_stake,
            "market_suspension_state": self.market_suspension_state,
            "bet_acceptance_risk": self.bet_acceptance_risk,
            "region_restrictions": self.region_restrictions,
            "settlement_source": self.settlement_source,
            "fee_or_commission": self.fee_or_commission,
            "odds_timestamp": self.odds_timestamp,
            "depth_or_max_stake": self.depth_or_max_stake,
            "currency": self.currency,
            "line": self.line,
            "threshold": self.threshold,
            "operator": self.operator,
            "payout_mechanics_class": self.payout_mechanics_class,
            "raw_evidence_paths": list(self.raw_evidence_paths),
            "diagnostic_only": self.diagnostic_only,
        }
