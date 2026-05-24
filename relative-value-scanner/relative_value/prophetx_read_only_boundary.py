from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProphetXDataCategory:
    name: str
    intended_use: str
    allowed_read_only_research: bool
    requires_api_permission: bool
    forbidden_account_or_execution_surface: bool
    required_fields: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "intended_use": self.intended_use,
            "allowed_read_only_research": self.allowed_read_only_research,
            "requires_api_permission": self.requires_api_permission,
            "forbidden_account_or_execution_surface": self.forbidden_account_or_execution_surface,
            "required_fields": list(self.required_fields),
        }


@dataclass(frozen=True)
class ProphetXAdapterStage:
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


PROPHETX_EXPECTED_ENV_VARS = (
    "PROPHETX_BASE_URL",
    "PROPHETX_API_KEY",
)


PROPHETX_DATA_CATEGORIES = (
    ProphetXDataCategory(
        name="market_discovery",
        intended_use="discover event contracts, source market ids, titles, market type, status, and venue restrictions",
        allowed_read_only_research=True,
        requires_api_permission=True,
        forbidden_account_or_execution_surface=False,
        required_fields=(
            "market_id",
            "event_id",
            "title",
            "market_type",
            "status",
            "start_time",
            "close_time",
            "venue_restrictions",
        ),
    ),
    ProphetXDataCategory(
        name="orderbook_depth",
        intended_use="read bid/ask, depth, quote timestamp, and venue-specific unit metadata",
        allowed_read_only_research=True,
        requires_api_permission=True,
        forbidden_account_or_execution_surface=False,
        required_fields=(
            "best_bid",
            "best_ask",
            "bid_depth",
            "ask_depth",
            "depth_unit",
            "quote_timestamp",
        ),
    ),
    ProphetXDataCategory(
        name="settlement_metadata",
        intended_use="preserve settlement source, rule text, event window, cancellation/void rules, and outcome terms",
        allowed_read_only_research=True,
        requires_api_permission=True,
        forbidden_account_or_execution_surface=False,
        required_fields=(
            "settlement_source",
            "settlement_rule_text",
            "event_window",
            "void_or_cancellation_rules",
            "outcome_terms",
        ),
    ),
    ProphetXDataCategory(
        name="fee_and_commission_metadata",
        intended_use="record reviewed fee, commission, and withdrawal or venue restriction inputs for diagnostics",
        allowed_read_only_research=True,
        requires_api_permission=True,
        forbidden_account_or_execution_surface=False,
        required_fields=("fee_schedule_version", "maker_fee", "taker_fee", "other_venue_fees"),
    ),
    ProphetXDataCategory(
        name="account_balances_positions_or_orders",
        intended_use="explicitly forbidden account and execution surface",
        allowed_read_only_research=False,
        requires_api_permission=True,
        forbidden_account_or_execution_surface=True,
        required_fields=(),
    ),
)


PROPHETX_ADAPTER_STAGES = (
    ProphetXAdapterStage(
        stage=0,
        name="boundary_and_fixture_schema_only",
        allowed=True,
        description="Current inert metadata, documentation, and fixture-backed research schema; no ProphetX transport or API imports.",
    ),
    ProphetXAdapterStage(
        stage=1,
        name="manual_api_permission_review",
        allowed=False,
        description="Confirm account eligibility, API permissions, read-only endpoint scope, and data licensing before code.",
    ),
    ProphetXAdapterStage(
        stage=2,
        name="fixture_backed_schema_design",
        allowed=True,
        description="Static fixtures define market, quote/depth, settlement, fee, and restriction research fields before transport.",
    ),
    ProphetXAdapterStage(
        stage=3,
        name="live_read_only_transport_after_separate_review",
        allowed=False,
        description="Future transport may be considered only after separate approval; no auth/session code exists here.",
    ),
    ProphetXAdapterStage(
        stage=4,
        name="normalized_snapshot_manual_review_only",
        allowed=False,
        description="Future schema-v1-like output only after market, quote, fee, freshness, and settlement review.",
    ),
    ProphetXAdapterStage(
        stage=5,
        name="matcher_integration_after_separate_review",
        allowed=False,
        description="Future matcher use requires separate approval and all relationship/freshness/depth/fee gates.",
    ),
)


PROPHETX_REDACTION_POLICY = {
    "allow_raw_network_echo": False,
    "must_redact_fields": (
        "apiKey",
        "api_key",
        "authorization",
        "auth",
        "token",
        "session",
        "account",
        "accountId",
        "userId",
        "orderId",
        "position",
        "balance",
        "password",
    ),
    "notes": "No raw ProphetX network payloads should be persisted until an API-specific redaction/filtering pass is reviewed.",
}


PROPHETX_FAIL_CLOSED_RULES = (
    "no_live_transport_until_separate_review",
    "no_auth_session_account_balance_position_or_order_queries",
    "missing_market_mapping_blocks_candidate_use",
    "missing_settlement_rule_text_blocks_candidate_use",
    "missing_fee_or_commission_model_blocks_candidate_use",
    "missing_depth_or_unknown_depth_units_blocks_candidate_use",
    "delayed_or_stale_quotes_block_candidate_use",
    "venue_restrictions_unreviewed_blocks_candidate_use",
    "no_paper_candidate_without_executable_same_payoff_fresh_fee_adjusted_depth_backed_settlement_compatible_legs",
)


def prophetx_read_only_boundary_report() -> dict[str, object]:
    return {
        "source_id": "prophetx",
        "display_name": "ProphetX",
        "source_type": "EXECUTABLE_VENUE",
        "implementation_status": "PLANNED_NOT_IMPLEMENTED",
        "status": "fixture_backed_schema_exists_no_live_transport",
        "account_required": True,
        "permission_requirements": (
            "ProphetX account eligibility",
            "explicit API access approval",
            "read-only endpoint scope reviewed before any transport code",
        ),
        "expected_env_vars": list(PROPHETX_EXPECTED_ENV_VARS),
        "fixture_research_schema_exists": True,
        "execution_allowed_in_project_now": False,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "data_categories": [category.to_dict() for category in PROPHETX_DATA_CATEGORIES],
        "stages": [stage.to_dict() for stage in PROPHETX_ADAPTER_STAGES],
        "raw_redaction_policy": {
            "allow_raw_network_echo": PROPHETX_REDACTION_POLICY["allow_raw_network_echo"],
            "must_redact_fields": list(PROPHETX_REDACTION_POLICY["must_redact_fields"]),
            "notes": PROPHETX_REDACTION_POLICY["notes"],
        },
        "fail_closed_rules": list(PROPHETX_FAIL_CLOSED_RULES),
    }
