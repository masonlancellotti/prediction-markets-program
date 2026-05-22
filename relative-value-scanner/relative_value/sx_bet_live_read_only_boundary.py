from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SXBetEndpointCategory:
    name: str
    intended_use: str
    public_read_only: bool
    requires_auth: bool
    forbidden_execution_surface: bool
    required_fields: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "intended_use": self.intended_use,
            "public_read_only": self.public_read_only,
            "requires_auth": self.requires_auth,
            "forbidden_execution_surface": self.forbidden_execution_surface,
            "required_fields": list(self.required_fields),
        }


@dataclass(frozen=True)
class SXBetAdapterStage:
    stage: int
    name: str
    allowed: bool
    description: str

    def to_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "name": self.name,
            "allowed": self.allowed,
            "description": self.description,
        }


SX_BET_LIVE_READ_ONLY_ENDPOINT_CATEGORIES = (
    SXBetEndpointCategory(
        name="markets",
        intended_use="market discovery",
        public_read_only=True,
        requires_auth=False,
        forbidden_execution_surface=False,
        required_fields=(
            "marketHash",
            "eventName",
            "leagueLabel",
            "sportLabel",
            "type",
            "line",
            "mainLine",
            "status",
            "gameTime",
            "outcomeOneName",
            "outcomeTwoName",
            "outcomeVoidName",
        ),
    ),
    SXBetEndpointCategory(
        name="active_orders",
        intended_use="orderbook/depth research",
        public_read_only=True,
        requires_auth=False,
        forbidden_execution_surface=False,
        required_fields=(
            "marketHash",
            "orderHash",
            "isMakerBettingOutcomeOne",
            "percentageOdds",
            "totalBetSize",
            "fillAmount",
            "expiry",
        ),
    ),
    SXBetEndpointCategory(
        name="trade_history",
        intended_use="trade print and last-update diagnostics",
        public_read_only=True,
        requires_auth=False,
        forbidden_execution_surface=False,
        required_fields=("marketHash", "tradeHash", "makerOdds", "takerOdds", "baseTokenAmount", "timestamp"),
    ),
    SXBetEndpointCategory(
        name="realtime_orderbook",
        intended_use="future quote freshness research only",
        public_read_only=False,
        requires_auth=True,
        forbidden_execution_surface=False,
        required_fields=("channel", "marketHash", "updateType", "timestamp"),
    ),
    SXBetEndpointCategory(
        name="post_or_fill_order",
        intended_use="explicitly forbidden execution surface",
        public_read_only=False,
        requires_auth=True,
        forbidden_execution_surface=True,
        required_fields=(),
    ),
)


SX_BET_ADAPTER_STAGES = (
    SXBetAdapterStage(
        stage=0,
        name="static_fixture_parser",
        allowed=True,
        description="Current fixture-backed sx_bet_research_snapshot_v1 parser only.",
    ),
    SXBetAdapterStage(
        stage=1,
        name="live_read_only_raw_fetcher_disabled_by_default",
        allowed=False,
        description="Future public REST fetcher design only; no transport implementation in this checkpoint.",
    ),
    SXBetAdapterStage(
        stage=2,
        name="raw_snapshot_archival_with_redaction",
        allowed=False,
        description="Future archival after filtering sensitive/auth/wallet/session fields.",
    ),
    SXBetAdapterStage(
        stage=3,
        name="schema_validation_and_quote_freshness",
        allowed=False,
        description="Future validation and stale-quote policy for saved research snapshots.",
    ),
    SXBetAdapterStage(
        stage=4,
        name="normalized_snapshot_manual_review_only",
        allowed=False,
        description="Future schema-v1-like output only after separate review; still no paper-candidate eligibility by default.",
    ),
    SXBetAdapterStage(
        stage=5,
        name="matcher_integration_after_separate_review",
        allowed=False,
        description="Future matcher use requires separate approval and all fake-edge gates.",
    ),
)


SX_BET_RAW_REDACTION_POLICY = {
    "allow_raw_fixture_echo": True,
    "allow_raw_network_echo": False,
    "must_redact_fields": (
        "authorization",
        "authToken",
        "token",
        "signature",
        "privateKey",
        "wallet",
        "maker",
        "taker",
        "session",
        "executor",
        "salt",
        "nonce",
        "affiliateAddress",
        "eip712Signature",
        "relayer",
    ),
    "notes": "Static fixtures may echo raw fields for review. Any future networked adapter must filter raw payloads before persistence.",
}


SX_BET_RATE_LIMIT_AND_RETRY_POLICY = {
    "live_fetcher_implemented": False,
    "assume_rate_limits_exist": True,
    "future_timeout_required_seconds": 10.0,
    "future_retry_limit": 2,
    "future_backoff": "bounded exponential backoff with jitter",
    "fail_closed_on_http_error": True,
}


SX_BET_FAIL_CLOSED_RULES = (
    "live_read_only_snapshots_non_executable_by_default",
    "can_create_candidate_pair_false_until_registry_and_capability_review",
    "missing_fee_model_forces_watch_or_rejection",
    "stale_quotes_force_watch_or_rejection",
    "unknown_settlement_wording_forces_manual_review_or_rejection",
    "missing_depth_forces_watch_or_rejection",
    "ambiguous_event_line_period_equivalence_forces_manual_review_or_rejection",
    "no_paper_candidate_without_executable_same_payoff_fresh_fee_adjusted_depth_backed_settlement_compatible_legs",
)


def sx_bet_live_read_only_boundary_report() -> dict[str, object]:
    return {
        "source_id": "sx_bet",
        "status": "design_only_no_network",
        "execution_allowed_in_project_now": False,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "endpoint_categories": [category.to_dict() for category in SX_BET_LIVE_READ_ONLY_ENDPOINT_CATEGORIES],
        "stages": [stage.to_dict() for stage in SX_BET_ADAPTER_STAGES],
        "raw_redaction_policy": {
            "allow_raw_fixture_echo": SX_BET_RAW_REDACTION_POLICY["allow_raw_fixture_echo"],
            "allow_raw_network_echo": SX_BET_RAW_REDACTION_POLICY["allow_raw_network_echo"],
            "must_redact_fields": list(SX_BET_RAW_REDACTION_POLICY["must_redact_fields"]),
            "notes": SX_BET_RAW_REDACTION_POLICY["notes"],
        },
        "rate_limit_and_retry_policy": dict(SX_BET_RATE_LIMIT_AND_RETRY_POLICY),
        "fail_closed_rules": list(SX_BET_FAIL_CLOSED_RULES),
    }
