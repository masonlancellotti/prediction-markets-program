from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CryptoComPredictCDNADataCategory:
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
class CryptoComPredictCDNAAdapterStage:
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


CRYPTO_COM_PREDICT_CDNA_DATA_CATEGORIES = (
    CryptoComPredictCDNADataCategory(
        name="market_discovery",
        intended_use="preserve saved research rows for event-contract market identity and lifecycle fields",
        allowed_read_only_research=True,
        requires_account_permission=True,
        forbidden_account_or_execution_surface=False,
        required_fields=(
            "market_id",
            "event_id",
            "title",
            "market_type",
            "status",
            "start_time",
            "close_time",
        ),
    ),
    CryptoComPredictCDNADataCategory(
        name="orderbook_depth",
        intended_use="preserve fixture-backed bid/ask, displayed depth, and quote timestamp fields for review only",
        allowed_read_only_research=True,
        requires_account_permission=True,
        forbidden_account_or_execution_surface=False,
        required_fields=("best_bid", "best_ask", "bid_size", "ask_size", "depth_units", "quote_timestamp"),
    ),
    CryptoComPredictCDNADataCategory(
        name="settlement_metadata",
        intended_use="preserve settlement source, rule text, close time, and void/cancellation wording",
        allowed_read_only_research=True,
        requires_account_permission=True,
        forbidden_account_or_execution_surface=False,
        required_fields=("settlement_source", "settlement_rule_text", "void_or_cancellation_rules", "close_time"),
    ),
    CryptoComPredictCDNADataCategory(
        name="fee_metadata",
        intended_use="preserve reviewed fee schedule fields before any downstream quote or edge diagnostics",
        allowed_read_only_research=True,
        requires_account_permission=True,
        forbidden_account_or_execution_surface=False,
        required_fields=("fee_schedule_version", "maker_fee", "taker_fee"),
    ),
    CryptoComPredictCDNADataCategory(
        name="region_eligibility",
        intended_use="record manual region and eligibility review status without storing account or identity data",
        allowed_read_only_research=True,
        requires_account_permission=True,
        forbidden_account_or_execution_surface=False,
        required_fields=("eligibility_review_status", "jurisdiction_notes", "reviewed_at"),
    ),
    CryptoComPredictCDNADataCategory(
        name="account_balances_positions_or_orders",
        intended_use="explicitly forbidden account and execution surface",
        allowed_read_only_research=False,
        requires_account_permission=True,
        forbidden_account_or_execution_surface=True,
        required_fields=(),
    ),
)


CRYPTO_COM_PREDICT_CDNA_ADAPTER_STAGES = (
    CryptoComPredictCDNAAdapterStage(
        stage=0,
        name="boundary_and_fixture_schema_only",
        allowed=True,
        description="Current inert boundary metadata, documentation, and fake fixture schema; no live transport.",
    ),
    CryptoComPredictCDNAAdapterStage(
        stage=1,
        name="manual_region_auth_execution_review",
        allowed=False,
        description="Manual review of region eligibility, account/API permission, and execution mechanics before code.",
    ),
    CryptoComPredictCDNAAdapterStage(
        stage=2,
        name="fixture_backed_research_schema",
        allowed=True,
        description="Static saved JSON fixtures may define market, depth, settlement, fee, and region fields.",
    ),
    CryptoComPredictCDNAAdapterStage(
        stage=3,
        name="live_read_only_transport_after_separate_review",
        allowed=False,
        description="No live read-only transport exists; any future transport requires separate approval.",
    ),
    CryptoComPredictCDNAAdapterStage(
        stage=4,
        name="normalized_snapshot_manual_review_only",
        allowed=False,
        description="Future normalized saved snapshots require separate review and may not feed evaluator gates.",
    ),
    CryptoComPredictCDNAAdapterStage(
        stage=5,
        name="matcher_or_evaluator_integration_after_separate_review",
        allowed=False,
        description="Matcher or evaluator integration is forbidden until a separate gate review approves it.",
    ),
)


CRYPTO_COM_PREDICT_CDNA_REDACTION_POLICY = {
    "allow_raw_network_echo": False,
    "must_redact_fields": (
        "account",
        "accountId",
        "account_id",
        "auth",
        "authorization",
        "token",
        "session",
        "cookie",
        "password",
        "username",
        "apiKey",
        "api_key",
        "secret",
        "orderId",
        "order_id",
        "position",
        "balance",
        "wallet",
        "privateKey",
        "private_key",
        "signing_key",
    ),
    "notes": "Only synthetic or reviewed saved fixtures may be stored; no raw live payloads or credentials are allowed.",
}


CRYPTO_COM_PREDICT_CDNA_FAIL_CLOSED_RULES = (
    "no_live_transport_until_separate_review",
    "no_auth_session_account_balance_position_or_order_queries",
    "missing_region_eligibility_review_blocks_candidate_use",
    "missing_settlement_source_or_rules_blocks_candidate_use",
    "missing_fee_model_blocks_candidate_use",
    "missing_depth_or_quote_freshness_blocks_candidate_use",
    "fixture_rows_cannot_create_candidate_pairs",
    "no_paper_candidate_without_separate_evaluator_gate_review",
)


def crypto_com_predict_cdna_read_only_boundary_report() -> dict[str, object]:
    return {
        "source_id": "crypto_com_predict_cdna",
        "display_name": "Crypto.com Predict / CDNA",
        "source_type": "EXECUTABLE_VENUE",
        "implementation_status": "PLANNED_NOT_IMPLEMENTED",
        "status": "boundary_and_fixture_schema_only_no_live_transport",
        "account_required": True,
        "permission_requirements": (
            "Crypto.com Predict/CDNA eligibility and region review",
            "explicit read-only market-data endpoint review before any transport code",
            "execution mechanics review before any matcher or evaluator use",
        ),
        "expected_env_vars": [],
        "fixture_research_schema_exists": True,
        "execution_allowed_in_project_now": False,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "data_categories": [category.to_dict() for category in CRYPTO_COM_PREDICT_CDNA_DATA_CATEGORIES],
        "stages": [stage.to_dict() for stage in CRYPTO_COM_PREDICT_CDNA_ADAPTER_STAGES],
        "raw_redaction_policy": {
            "allow_raw_network_echo": CRYPTO_COM_PREDICT_CDNA_REDACTION_POLICY["allow_raw_network_echo"],
            "must_redact_fields": list(CRYPTO_COM_PREDICT_CDNA_REDACTION_POLICY["must_redact_fields"]),
            "notes": CRYPTO_COM_PREDICT_CDNA_REDACTION_POLICY["notes"],
        },
        "fail_closed_rules": list(CRYPTO_COM_PREDICT_CDNA_FAIL_CLOSED_RULES),
    }
