from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IBKRForecastExDataCategory:
    name: str
    intended_use: str
    allowed_read_only_research: bool
    requires_account_permission: bool
    forbidden_account_or_execution_surface: bool
    required_fields: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "intended_use": self.intended_use,
            "allowed_read_only_research": self.allowed_read_only_research,
            "requires_account_permission": self.requires_account_permission,
            "forbidden_account_or_execution_surface": self.forbidden_account_or_execution_surface,
            "required_fields": list(self.required_fields),
        }


@dataclass(frozen=True)
class IBKRForecastExAdapterStage:
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


IBKR_FORECASTEX_EXPECTED_ENV_VARS = (
    "IBKR_HOST",
    "IBKR_PORT",
    "IBKR_CLIENT_ID",
    "IBKR_ACCOUNT_ID",
)


IBKR_FORECASTEX_DATA_CATEGORIES = (
    IBKRForecastExDataCategory(
        name="instrument_discovery",
        intended_use="map ForecastEx event contracts to stable instrument identifiers",
        allowed_read_only_research=True,
        requires_account_permission=True,
        forbidden_account_or_execution_surface=False,
        required_fields=(
            "conid_or_contract_id",
            "symbol",
            "exchange",
            "event_contract_title",
            "contract_terms_url_or_text",
            "expiration",
            "trading_class",
            "currency",
        ),
    ),
    IBKRForecastExDataCategory(
        name="market_data_snapshot",
        intended_use="read top-of-book bid/ask, depth, and quote timestamps for approved instruments",
        allowed_read_only_research=True,
        requires_account_permission=True,
        forbidden_account_or_execution_surface=False,
        required_fields=(
            "bid",
            "ask",
            "bid_size",
            "ask_size",
            "market_data_timestamp",
            "delayed_or_realtime_status",
        ),
    ),
    IBKRForecastExDataCategory(
        name="settlement_metadata",
        intended_use="preserve settlement source, event terms, contract multiplier, and final outcome wording",
        allowed_read_only_research=True,
        requires_account_permission=True,
        forbidden_account_or_execution_surface=False,
        required_fields=(
            "settlement_source",
            "settlement_rule_text",
            "event_window",
            "contract_multiplier",
            "yes_no_payout_terms",
        ),
    ),
    IBKRForecastExDataCategory(
        name="fee_and_commission_metadata",
        intended_use="record reviewed commission/fee schedule inputs for diagnostics only",
        allowed_read_only_research=True,
        requires_account_permission=True,
        forbidden_account_or_execution_surface=False,
        required_fields=("commission_schedule_version", "per_contract_fee", "regulatory_or_exchange_fees"),
    ),
    IBKRForecastExDataCategory(
        name="account_balances_positions_or_orders",
        intended_use="explicitly forbidden account and execution surface",
        allowed_read_only_research=False,
        requires_account_permission=True,
        forbidden_account_or_execution_surface=True,
        required_fields=(),
    ),
)


IBKR_FORECASTEX_ADAPTER_STAGES = (
    IBKRForecastExAdapterStage(
        stage=0,
        name="boundary_and_fixture_schema_only",
        allowed=True,
        description="Current inert metadata, documentation, and fixture-backed research schema; no live transport or IBKR imports.",
    ),
    IBKRForecastExAdapterStage(
        stage=1,
        name="manual_account_permission_review",
        allowed=False,
        description="Confirm ForecastEx eligibility, market-data permissions, and read-only API mode before code.",
    ),
    IBKRForecastExAdapterStage(
        stage=2,
        name="fixture_backed_instrument_schema",
        allowed=True,
        description="Static fixtures define instrument, quote, fee, and settlement research snapshot fields before transport.",
    ),
    IBKRForecastExAdapterStage(
        stage=3,
        name="live_read_only_transport_after_separate_review",
        allowed=False,
        description="Future transport may be considered only after separate approval; no TWS/Gateway code exists here.",
    ),
    IBKRForecastExAdapterStage(
        stage=4,
        name="normalized_snapshot_manual_review_only",
        allowed=False,
        description="Future schema-v1-like output only after instrument, quote, fee, freshness, and settlement review.",
    ),
    IBKRForecastExAdapterStage(
        stage=5,
        name="matcher_integration_after_separate_review",
        allowed=False,
        description="Future matcher use requires separate approval and all relationship/freshness/depth/fee gates.",
    ),
)


IBKR_FORECASTEX_REDACTION_POLICY = {
    "allow_raw_network_echo": False,
    "must_redact_fields": (
        "account",
        "accountId",
        "account_id",
        "session",
        "sessionToken",
        "auth",
        "authorization",
        "token",
        "password",
        "username",
        "clientPortalCookie",
        "orderId",
        "permId",
        "position",
        "balance",
    ),
    "notes": "No raw IBKR/ForecastEx network payloads should be persisted until a redaction/filtering pass is reviewed.",
}


IBKR_FORECASTEX_FAIL_CLOSED_RULES = (
    "no_live_transport_until_separate_review",
    "no_account_balance_position_or_order_queries",
    "missing_instrument_mapping_blocks_candidate_use",
    "missing_settlement_rule_text_blocks_candidate_use",
    "missing_fee_or_commission_model_blocks_candidate_use",
    "delayed_or_stale_quotes_block_candidate_use",
    "no_paper_candidate_without_executable_same_payoff_fresh_fee_adjusted_depth_backed_settlement_compatible_legs",
)


def ibkr_forecastex_read_only_boundary_report() -> dict[str, object]:
    return {
        "source_id": "forecastex_ibkr",
        "display_name": "IBKR / ForecastEx",
        "source_type": "EXECUTABLE_VENUE",
        "implementation_status": "PLANNED_NOT_IMPLEMENTED",
        "status": "fixture_backed_schema_exists_no_live_transport",
        "account_required": True,
        "permission_requirements": (
            "ForecastEx event-contract eligibility",
            "IBKR market-data permission for relevant instruments",
            "read-only API mode reviewed before any transport code",
        ),
        "expected_env_vars": list(IBKR_FORECASTEX_EXPECTED_ENV_VARS),
        "fixture_research_schema_exists": True,
        "execution_allowed_in_project_now": False,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "data_categories": [category.to_dict() for category in IBKR_FORECASTEX_DATA_CATEGORIES],
        "stages": [stage.to_dict() for stage in IBKR_FORECASTEX_ADAPTER_STAGES],
        "raw_redaction_policy": {
            "allow_raw_network_echo": IBKR_FORECASTEX_REDACTION_POLICY["allow_raw_network_echo"],
            "must_redact_fields": list(IBKR_FORECASTEX_REDACTION_POLICY["must_redact_fields"]),
            "notes": IBKR_FORECASTEX_REDACTION_POLICY["notes"],
        },
        "fail_closed_rules": list(IBKR_FORECASTEX_FAIL_CLOSED_RULES),
    }
