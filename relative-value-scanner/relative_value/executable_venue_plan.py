from __future__ import annotations

from dataclasses import dataclass

from relative_value.source_registry import SourceType


@dataclass(frozen=True)
class VenueCapability:
    source_id: str
    display_name: str
    classification: SourceType
    has_public_market_data: bool
    has_orderbook_or_bid_ask: bool
    has_depth: bool
    has_trade_prints: bool
    has_settlement_rules: bool
    requires_auth_for_data: bool
    requires_wallet_or_private_key: bool
    execution_supported_by_api: bool
    execution_allowed_in_project_now: bool
    adapter_priority: str
    rationale: str

    @property
    def can_create_paper_candidate(self) -> bool:
        return (
            self.classification == SourceType.EXECUTABLE_VENUE
            and self.execution_allowed_in_project_now
            and self.has_orderbook_or_bid_ask
            and self.has_depth
            and self.has_settlement_rules
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "display_name": self.display_name,
            "classification": self.classification.value,
            "has_public_market_data": self.has_public_market_data,
            "has_orderbook_or_bid_ask": self.has_orderbook_or_bid_ask,
            "has_depth": self.has_depth,
            "has_trade_prints": self.has_trade_prints,
            "has_settlement_rules": self.has_settlement_rules,
            "requires_auth_for_data": self.requires_auth_for_data,
            "requires_wallet_or_private_key": self.requires_wallet_or_private_key,
            "execution_supported_by_api": self.execution_supported_by_api,
            "execution_allowed_in_project_now": self.execution_allowed_in_project_now,
            "can_create_paper_candidate": self.can_create_paper_candidate,
            "adapter_priority": self.adapter_priority,
            "rationale": self.rationale,
        }


PAPER_CANDIDATE_GATE_REQUIREMENTS = (
    "both_legs_are_executable_venues",
    "relationship_same_payoff_true_and_equivalent",
    "no_relationship_blocking_reasons",
    "real_bid_ask_and_depth",
    "fee_model_available",
    "slippage_or_top_of_book_size_guard",
    "fresh_quotes",
    "settlement_wording_reviewed",
    "venue_restrictions_checked",
)


PLANNED_EXECUTABLE_VENUE_CAPABILITIES: dict[str, VenueCapability] = {
    "forecastex_ibkr": VenueCapability(
        source_id="forecastex_ibkr",
        display_name="ForecastEx / IBKR",
        classification=SourceType.EXECUTABLE_VENUE,
        has_public_market_data=False,
        has_orderbook_or_bid_ask=True,
        has_depth=True,
        has_trade_prints=True,
        has_settlement_rules=True,
        requires_auth_for_data=True,
        requires_wallet_or_private_key=False,
        execution_supported_by_api=True,
        execution_allowed_in_project_now=False,
        adapter_priority="defer_high_value_high_friction",
        rationale="High-value regulated venue, but eligibility, auth, account permissions, and instrument mapping make it unsafe as the next adapter.",
    ),
    "sx_bet": VenueCapability(
        source_id="sx_bet",
        display_name="SX Bet",
        classification=SourceType.EXECUTABLE_VENUE,
        has_public_market_data=True,
        has_orderbook_or_bid_ask=True,
        has_depth=True,
        has_trade_prints=True,
        has_settlement_rules=True,
        requires_auth_for_data=False,
        requires_wallet_or_private_key=True,
        execution_supported_by_api=True,
        execution_allowed_in_project_now=False,
        adapter_priority="recommended_read_only_research_first",
        rationale="Public market/orderbook data makes read-only adapter research feasible, but wallet/signing/execution remains prohibited.",
    ),
    "azuro": VenueCapability(
        source_id="azuro",
        display_name="Azuro",
        classification=SourceType.DO_NOT_USE_YET,
        has_public_market_data=True,
        has_orderbook_or_bid_ask=False,
        has_depth=False,
        has_trade_prints=True,
        has_settlement_rules=True,
        requires_auth_for_data=False,
        requires_wallet_or_private_key=True,
        execution_supported_by_api=True,
        execution_allowed_in_project_now=False,
        adapter_priority="defer_protocol_schema_work",
        rationale="On-chain AMM/protocol liquidity does not map cleanly to schema-v1 bid/ask/depth without a separate normalization design.",
    ),
    "omen_gnosis": VenueCapability(
        source_id="omen_gnosis",
        display_name="Omen / Gnosis Conditional Tokens",
        classification=SourceType.DO_NOT_USE_YET,
        has_public_market_data=True,
        has_orderbook_or_bid_ask=False,
        has_depth=False,
        has_trade_prints=True,
        has_settlement_rules=True,
        requires_auth_for_data=False,
        requires_wallet_or_private_key=True,
        execution_supported_by_api=True,
        execution_allowed_in_project_now=False,
        adapter_priority="defer_protocol_schema_work",
        rationale="Conditional-token/indexer path needs token/collateral/oracle schema and settlement analysis before matching.",
    ),
    "predictit": VenueCapability(
        source_id="predictit",
        display_name="PredictIt",
        classification=SourceType.DO_NOT_USE_YET,
        has_public_market_data=True,
        has_orderbook_or_bid_ask=True,
        has_depth=False,
        has_trade_prints=True,
        has_settlement_rules=True,
        requires_auth_for_data=False,
        requires_wallet_or_private_key=False,
        execution_supported_by_api=False,
        execution_allowed_in_project_now=False,
        adapter_priority="defer_until_permitted_execution_api_proven",
        rationale="Read-only public data may be useful later, but it is not executable here without proven permitted execution API support.",
    ),
}


def venue_capability(source_id: str) -> VenueCapability:
    normalized = source_id.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized == "omen":
        normalized = "omen_gnosis"
    return PLANNED_EXECUTABLE_VENUE_CAPABILITIES[normalized]


def venue_capability_report() -> list[dict[str, object]]:
    return [capability.to_dict() for capability in PLANNED_EXECUTABLE_VENUE_CAPABILITIES.values()]


def recommended_next_executable_adapter() -> VenueCapability:
    return PLANNED_EXECUTABLE_VENUE_CAPABILITIES["sx_bet"]
