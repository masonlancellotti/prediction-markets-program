from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from relative_value.executable_venue_plan import PLANNED_EXECUTABLE_VENUE_CAPABILITIES
from relative_value.source_registry import ImplementationStatus, SOURCE_REGISTRY, SourceEntry, SourceType


SCHEMA_VERSION = 2
REPORT_SOURCE = "platform_api_expansion_audit_v1"

UNKNOWN = "unknown"

EXECUTABLE_NOW = "EXECUTABLE_NOW"
READ_ONLY_ONLY = "READ_ONLY_ONLY"
REFERENCE_ONLY = "REFERENCE_ONLY"
REQUIRES_AUTH_REVIEW = "REQUIRES_AUTH_REVIEW"
UNKNOWN_STATUS = "UNKNOWN"

# Orthogonal platform role taxonomy. execution_status describes transport
# readiness (do we have a read-only public API right now); platform_role
# describes what kind of venue/feed this is for relative-value matching
# (prediction market vs event-contract exchange vs sportsbook/exchange vs
# truth feed vs discovery signal). A row may be REQUIRES_AUTH_REVIEW while
# still being EXECUTABLE_EVENT_CONTRACT_EXCHANGE in role, etc.
PLATFORM_ROLE_EXECUTABLE_PREDICTION_MARKET = "EXECUTABLE_PREDICTION_MARKET"
PLATFORM_ROLE_EXECUTABLE_EVENT_CONTRACT_EXCHANGE = "EXECUTABLE_EVENT_CONTRACT_EXCHANGE"
PLATFORM_ROLE_EXECUTABLE_SPORTSBOOK_OR_BETTING_EXCHANGE = "EXECUTABLE_SPORTSBOOK_OR_BETTING_EXCHANGE"
PLATFORM_ROLE_EXECUTABLE_HEDGE_OR_UNDERLYING = "EXECUTABLE_HEDGE_OR_UNDERLYING"
PLATFORM_ROLE_REFERENCE_ONLY_TRUTH_FEED = "REFERENCE_ONLY_TRUTH_FEED"
PLATFORM_ROLE_DISCOVERY_ONLY = "DISCOVERY_ONLY"
PLATFORM_ROLE_UNKNOWN = "UNKNOWN"

PLATFORM_ROLES = (
    PLATFORM_ROLE_EXECUTABLE_PREDICTION_MARKET,
    PLATFORM_ROLE_EXECUTABLE_EVENT_CONTRACT_EXCHANGE,
    PLATFORM_ROLE_EXECUTABLE_SPORTSBOOK_OR_BETTING_EXCHANGE,
    PLATFORM_ROLE_EXECUTABLE_HEDGE_OR_UNDERLYING,
    PLATFORM_ROLE_REFERENCE_ONLY_TRUTH_FEED,
    PLATFORM_ROLE_DISCOVERY_ONLY,
    PLATFORM_ROLE_UNKNOWN,
)

PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


@dataclass(frozen=True)
class PlatformApiProfile:
    platform_id: str
    display_name: str
    source_registry_id: str | None
    aliases: tuple[str, ...]
    read_only_api_available: bool | str
    auth_required_for_market_data: bool | str
    orderbook_available: bool | str
    depth_available: bool | str
    trade_history_available: bool | str
    settlement_rules_available: bool | str
    explicit_resolution_source_available: bool | str
    fee_metadata_available: bool | str
    market_categories_available: tuple[str, ...]
    known_family_strengths: tuple[str, ...]
    execution_status: str
    adapter_priority: str
    why_priority: str
    fake_edge_risks: tuple[str, ...]
    missing_fields_for_normalized_market: tuple[str, ...]
    next_adapter_tasks: tuple[str, ...]
    notes: str = ""
    platform_role: str = PLATFORM_ROLE_UNKNOWN
    requires_region_review: bool | str = UNKNOWN
    requires_execution_mechanics_review: bool | str = UNKNOWN


def default_platform_api_profiles() -> list[PlatformApiProfile]:
    return [
        PlatformApiProfile(
            platform_id="sx_bet",
            display_name="SX Bet",
            source_registry_id="sx_bet",
            aliases=("sx", "sx.bet"),
            read_only_api_available=True,
            auth_required_for_market_data=False,
            orderbook_available=True,
            depth_available=True,
            trade_history_available=True,
            settlement_rules_available=True,
            explicit_resolution_source_available=UNKNOWN,
            fee_metadata_available=UNKNOWN,
            market_categories_available=("sports",),
            known_family_strengths=("SPORTS",),
            execution_status=READ_ONLY_ONLY,
            adapter_priority="P0",
            why_priority="Best new surface-area target: saved research snapshots and public read-only sports/order data already exist, with overlap against current sports universes.",
            fake_edge_risks=(
                "sports_rule_and_void_mismatch",
                "line_and_market_type_mismatch",
                "fee_model_unreviewed",
                "wallet_or_signing_surface_must_remain_out_of_scope",
                "game_level_vs_futures_scope_mismatch_with_kalshi_polymarket",
            ),
            missing_fields_for_normalized_market=(
                "reviewed_fee_model",
                "explicit_resolution_source_url",
                "settlement_wording_normalization",
                "venue_restrictions_review",
                "sports_selection_and_market_type_typed_keys",
            ),
            next_adapter_tasks=(
                "Normalize saved SX Bet research snapshot rows into a diagnostic-only normalized market draft.",
                "Map sport/league/market_type/start_time fields to existing sports family keys.",
                "Create settlement and fee blockers without evaluator integration.",
                "Pause overlap-driven discovery until typed-key overlap returns >0 matches against Kalshi/Polymarket saved rows; keep typed-key audit and saved-file diagnostics running.",
            ),
            notes="Public read-only market/order research exists in this repo; execution remains prohibited and out of scope. Saved overlap with current Kalshi/Polymarket sports universes is currently 0 because saved Kalshi/Polymarket sports rows are futures/championship-level while SX Bet saved rows are game-level.",
            platform_role=PLATFORM_ROLE_EXECUTABLE_SPORTSBOOK_OR_BETTING_EXCHANGE,
            requires_region_review=True,
            requires_execution_mechanics_review=True,
        ),
        PlatformApiProfile(
            platform_id="kalshi",
            display_name="Kalshi",
            source_registry_id="kalshi",
            aliases=("kalshi",),
            read_only_api_available=True,
            auth_required_for_market_data=False,
            orderbook_available=True,
            depth_available=True,
            trade_history_available=UNKNOWN,
            settlement_rules_available=True,
            explicit_resolution_source_available=False,
            fee_metadata_available=True,
            market_categories_available=("series", "events", "markets"),
            known_family_strengths=("FED_FOMC", "CRYPTO", "SPORTS"),
            execution_status=READ_ONLY_ONLY,
            adapter_priority="P1",
            why_priority="Existing read-only adapter is productive; next work is metadata completeness and family-specific normalization rather than a new platform.",
            fake_edge_risks=(
                "yes_no_orderbook_side_conversion_error",
                "rules_text_without_external_source_url",
                "expected_expiration_not_actual_resolution",
            ),
            missing_fields_for_normalized_market=(
                "explicit_resolution_source_url",
                "actual_resolution_time",
                "source_registry_or_manual_convention_evidence",
            ),
            next_adapter_tasks=(
                "Preserve YES/NO bid conversion evidence in normalized quote depth.",
                "Continue family typed-key extraction for Fed and crypto threshold contracts.",
                "Add manual source registry evidence where explicit source URLs are absent.",
            ),
            platform_role=PLATFORM_ROLE_EXECUTABLE_EVENT_CONTRACT_EXCHANGE,
            requires_region_review=False,
            requires_execution_mechanics_review=False,
        ),
        PlatformApiProfile(
            platform_id="polymarket",
            display_name="Polymarket",
            source_registry_id="polymarket",
            aliases=("polymarket", "gamma"),
            read_only_api_available=True,
            auth_required_for_market_data=False,
            orderbook_available=True,
            depth_available=True,
            trade_history_available=UNKNOWN,
            settlement_rules_available=True,
            explicit_resolution_source_available=True,
            fee_metadata_available=True,
            market_categories_available=("gamma_events", "markets", "tags"),
            known_family_strengths=("SPORTS", "CRYPTO", "ELECTION_RESULT", "WEATHER"),
            execution_status=READ_ONLY_ONLY,
            adapter_priority="P1",
            why_priority="Existing public Gamma/CLOB read-only paths are already normalized enough for source-ready sports diagnostics.",
            fake_edge_risks=(
                "end_date_deadline_not_actual_resolution",
                "source_url_coverage_varies_by_market",
                "conditional_token_outcome_mapping_error",
            ),
            missing_fields_for_normalized_market=(
                "actual_resolution_time",
                "per_market_fee_review_status",
                "complete_quote_freshness_for_all_rows",
            ),
            next_adapter_tasks=(
                "Keep public CLOB read endpoints read-only.",
                "Expand explicit source URL coverage diagnostics by family.",
                "Improve outcome/token evidence pointers in normalized records.",
            ),
            platform_role=PLATFORM_ROLE_EXECUTABLE_PREDICTION_MARKET,
            requires_region_review=True,
            requires_execution_mechanics_review=False,
        ),
        PlatformApiProfile(
            platform_id="ibkr_forecastex",
            display_name="ForecastEx / IBKR",
            source_registry_id="forecastex_ibkr",
            aliases=("forecastex_ibkr", "ibkr", "forecast_ex"),
            read_only_api_available=UNKNOWN,
            auth_required_for_market_data=True,
            orderbook_available=True,
            depth_available=True,
            trade_history_available=True,
            settlement_rules_available=True,
            explicit_resolution_source_available=UNKNOWN,
            fee_metadata_available=UNKNOWN,
            market_categories_available=("macro", "rates", "economic_events"),
            known_family_strengths=("FED_FOMC", "ECONOMIC_INDICATOR"),
            execution_status=REQUIRES_AUTH_REVIEW,
            adapter_priority="P3",
            why_priority="High-value venue, but current repo only has fixture research and account/API permission review is required before any live transport planning.",
            fake_edge_risks=(
                "fixture_only_quote_staleness",
                "instrument_mapping_mismatch",
                "contract_multiplier_or_fee_mismatch",
                "account_permission_boundary",
            ),
            missing_fields_for_normalized_market=(
                "public_read_only_transport",
                "reviewed_instrument_mapping",
                "reviewed_fee_commission_model",
                "quote_freshness_policy",
                "settlement_wording_normalization",
            ),
            next_adapter_tasks=(
                "Keep existing fixtures as research-only.",
                "Document market-data permission boundary before any adapter work.",
                "Map ForecastEx contract identifiers to normalized identity fields from saved fixtures only.",
            ),
            platform_role=PLATFORM_ROLE_EXECUTABLE_EVENT_CONTRACT_EXCHANGE,
            requires_region_review=True,
            requires_execution_mechanics_review=True,
        ),
        PlatformApiProfile(
            platform_id="manifold",
            display_name="Manifold",
            source_registry_id="manifold",
            aliases=("manifold_markets",),
            read_only_api_available=True,
            auth_required_for_market_data=False,
            orderbook_available=False,
            depth_available=False,
            trade_history_available=UNKNOWN,
            settlement_rules_available=UNKNOWN,
            explicit_resolution_source_available=UNKNOWN,
            fee_metadata_available=UNKNOWN,
            market_categories_available=("forecasting", "reference_signal"),
            known_family_strengths=("ELECTION_RESULT", "CRYPTO", "WEATHER"),
            execution_status=REFERENCE_ONLY,
            adapter_priority="P2",
            why_priority="Useful for discovery signals, but market mechanics and currency/liquidity do not fit execution-grade relative-value gates.",
            fake_edge_risks=(
                "signal_price_not_executable_orderbook",
                "currency_and_liquidity_mismatch",
                "community_resolution_mismatch",
            ),
            missing_fields_for_normalized_market=(
                "execution_grade_orderbook",
                "depth_with_units",
                "reviewed_fee_model",
                "explicit_resolution_source_url",
            ),
            next_adapter_tasks=(
                "Treat as a discovery/reference signal only.",
                "If added later, emit reference rows that cannot affect evaluator gates.",
                "Do not compare Manifold prices as executable legs.",
            ),
            platform_role=PLATFORM_ROLE_DISCOVERY_ONLY,
            requires_region_review=False,
            requires_execution_mechanics_review=True,
        ),
        PlatformApiProfile(
            platform_id="prophetx",
            display_name="ProphetX",
            source_registry_id="prophetx",
            aliases=("prophet_x",),
            read_only_api_available=UNKNOWN,
            auth_required_for_market_data=True,
            orderbook_available=True,
            depth_available=True,
            trade_history_available=UNKNOWN,
            settlement_rules_available=True,
            explicit_resolution_source_available=UNKNOWN,
            fee_metadata_available=UNKNOWN,
            market_categories_available=("sports", "macro_fixture_research"),
            known_family_strengths=("SPORTS", "ECONOMIC_INDICATOR"),
            execution_status=REQUIRES_AUTH_REVIEW,
            adapter_priority="P3",
            why_priority="Current repo has fixture inspection only; API access, venue restrictions, settlement, and fee evidence are unreviewed.",
            fake_edge_risks=(
                "fixture_only_quote_staleness",
                "venue_restriction_mismatch",
                "fee_commission_model_unknown",
                "settlement_wording_unreviewed",
            ),
            missing_fields_for_normalized_market=(
                "public_read_only_transport",
                "api_permission_review",
                "reviewed_fee_commission_model",
                "quote_freshness_policy",
                "venue_restrictions_review",
            ),
            next_adapter_tasks=(
                "Keep fixture inspection research-only.",
                "Document API permission and public market-data availability before adapter work.",
                "Do not integrate fixture rows with matcher or evaluator.",
            ),
            platform_role=PLATFORM_ROLE_EXECUTABLE_SPORTSBOOK_OR_BETTING_EXCHANGE,
            requires_region_review=True,
            requires_execution_mechanics_review=True,
        ),
        PlatformApiProfile(
            platform_id="the_odds_api",
            display_name="The Odds API / Sportsbook Reference",
            source_registry_id="the_odds_api",
            aliases=("sportsbooks", "sportsbook_reference"),
            read_only_api_available=True,
            auth_required_for_market_data=True,
            orderbook_available=False,
            depth_available=False,
            trade_history_available=False,
            settlement_rules_available=False,
            explicit_resolution_source_available=False,
            fee_metadata_available=False,
            market_categories_available=("sports_reference_odds",),
            known_family_strengths=("SPORTS",),
            execution_status=REFERENCE_ONLY,
            adapter_priority="P2",
            why_priority="Already useful as sportsbook context, but odds are reference prices and cannot be normalized as tradable prediction-market legs.",
            fake_edge_risks=(
                "sportsbook_odds_not_prediction_market_contract",
                "vig_and_payout_model_mismatch",
                "reference_price_treated_as_executable",
            ),
            missing_fields_for_normalized_market=(
                "prediction_market_orderbook",
                "depth_with_units",
                "settlement_contract_terms",
                "fee_model_for_prediction_market_leg",
            ),
            next_adapter_tasks=(
                "Keep as reference diagnostics only.",
                "Use for sports context and schedule metadata, not candidate creation.",
                "Preserve blockers when joined to executable venue rows.",
            ),
            platform_role=PLATFORM_ROLE_REFERENCE_ONLY_TRUTH_FEED,
            requires_region_review=False,
            requires_execution_mechanics_review=False,
        ),
        PlatformApiProfile(
            platform_id="azuro",
            display_name="Azuro",
            source_registry_id="azuro",
            aliases=("azuro",),
            read_only_api_available=UNKNOWN,
            auth_required_for_market_data=UNKNOWN,
            orderbook_available=False,
            depth_available=False,
            trade_history_available=UNKNOWN,
            settlement_rules_available=UNKNOWN,
            explicit_resolution_source_available=UNKNOWN,
            fee_metadata_available=UNKNOWN,
            market_categories_available=("sports", "protocol_research"),
            known_family_strengths=("SPORTS",),
            execution_status=UNKNOWN_STATUS,
            adapter_priority="P3",
            why_priority="Current source registry marks this as do-not-use-yet; protocol liquidity needs a separate schema before any adapter work.",
            fake_edge_risks=("amm_or_protocol_price_treated_as_clob_depth", "wallet_surface_scope_risk"),
            missing_fields_for_normalized_market=(
                "schema_for_protocol_liquidity",
                "outcome_token_mapping",
                "fee_and_settlement_review",
            ),
            next_adapter_tasks=("Defer until a protocol-specific normalization design exists.",),
            platform_role=PLATFORM_ROLE_DISCOVERY_ONLY,
            requires_region_review=True,
            requires_execution_mechanics_review=True,
        ),
        PlatformApiProfile(
            platform_id="omen_gnosis",
            display_name="Omen / Gnosis Conditional Tokens",
            source_registry_id="omen_gnosis",
            aliases=("omen", "gnosis"),
            read_only_api_available=UNKNOWN,
            auth_required_for_market_data=UNKNOWN,
            orderbook_available=False,
            depth_available=False,
            trade_history_available=UNKNOWN,
            settlement_rules_available=UNKNOWN,
            explicit_resolution_source_available=UNKNOWN,
            fee_metadata_available=UNKNOWN,
            market_categories_available=("conditional_tokens", "protocol_research"),
            known_family_strengths=("ELECTION_RESULT", "CRYPTO"),
            execution_status=UNKNOWN_STATUS,
            adapter_priority="P3",
            why_priority="Conditional-token/indexer path needs token, collateral, oracle, and settlement schema work before use.",
            fake_edge_risks=("oracle_resolution_mismatch", "collateral_token_or_fee_mismatch", "wallet_surface_scope_risk"),
            missing_fields_for_normalized_market=(
                "conditional_token_schema",
                "collateral_and_fee_model",
                "oracle_settlement_evidence",
            ),
            next_adapter_tasks=("Defer until a conditional-token normalization design exists.",),
            platform_role=PLATFORM_ROLE_DISCOVERY_ONLY,
            requires_region_review=True,
            requires_execution_mechanics_review=True,
        ),
        PlatformApiProfile(
            platform_id="predictit",
            display_name="PredictIt",
            source_registry_id="predictit",
            aliases=("predict_it",),
            read_only_api_available=True,
            auth_required_for_market_data=UNKNOWN,
            orderbook_available=True,
            depth_available=UNKNOWN,
            trade_history_available=UNKNOWN,
            settlement_rules_available=True,
            explicit_resolution_source_available=UNKNOWN,
            fee_metadata_available=UNKNOWN,
            market_categories_available=("politics", "election_result"),
            known_family_strengths=("ELECTION_RESULT",),
            execution_status=UNKNOWN_STATUS,
            adapter_priority="P3",
            why_priority="Do not use until permitted execution API support and venue constraints are proven and reviewed.",
            fake_edge_risks=("regulatory_or_eligibility_mismatch", "fee_and_limit_model_mismatch"),
            missing_fields_for_normalized_market=(
                "permitted_execution_api_review",
                "fee_and_limit_model",
                "quote_depth_freshness_policy",
            ),
            next_adapter_tasks=("Defer until source and permission review is complete.",),
            platform_role=PLATFORM_ROLE_REFERENCE_ONLY_TRUTH_FEED,
            requires_region_review=True,
            requires_execution_mechanics_review=True,
        ),
        PlatformApiProfile(
            platform_id="crypto_com_predict_cdna",
            display_name="Crypto.com Predict / CDNA",
            source_registry_id="crypto_com_predict_cdna",
            aliases=(
                "crypto_com_predict",
                "crypto.com_predict",
                "cdna",
                "crypto_com_derivatives_north_america",
                "crypto_com_event_contracts",
            ),
            read_only_api_available=UNKNOWN,
            auth_required_for_market_data=UNKNOWN,
            orderbook_available=UNKNOWN,
            depth_available=UNKNOWN,
            trade_history_available=UNKNOWN,
            settlement_rules_available=UNKNOWN,
            explicit_resolution_source_available=UNKNOWN,
            fee_metadata_available=UNKNOWN,
            market_categories_available=("event_contracts", "sports", "macro_or_crypto_events"),
            known_family_strengths=("SPORTS", "CRYPTO_PRICE_THRESHOLD", "MACRO_ECONOMIC_EVENT"),
            execution_status=REQUIRES_AUTH_REVIEW,
            adapter_priority="P2",
            why_priority="Crypto.com Predict/CDNA is a regulated event-contract venue, NOT a crypto exchange or truth feed. It is a potentially valuable additional executable event-contract exchange next to Kalshi and ForecastEx, but read-only public market-data availability, region/account eligibility, instrument mapping, settlement, and fee mechanics are unreviewed. Promote to P1 only after public read-only availability, region/auth review, and fixture-backed schema review are complete.",
            fake_edge_risks=(
                "treating_crypto_com_predict_as_truth_feed_or_crypto_exchange",
                "missing_public_read_only_market_data_review",
                "instrument_mapping_or_outcome_token_mismatch",
                "fee_commission_model_unreviewed",
                "settlement_wording_and_resolution_source_unreviewed",
                "region_eligibility_or_account_permission_mismatch",
                "regulated_dcm_or_event_contract_status_unverified",
            ),
            missing_fields_for_normalized_market=(
                "public_read_only_transport",
                "venue_role_classification_executable_event_contract_exchange",
                "reviewed_instrument_mapping",
                "outcome_token_or_payout_terms",
                "settlement_source_url_or_registry_evidence",
                "reviewed_fee_commission_model",
                "quote_freshness_policy",
                "region_eligibility_review",
            ),
            next_adapter_tasks=(
                "Keep the saved-file-only boundary document and fixture scaffold as research-only until separate review.",
                "Review whether public read-only market discovery, orderbook depth, settlement metadata, fee metadata, and region eligibility data are available without account/order/wallet surfaces.",
                "Only use saved fixtures under venues/fixtures/crypto_com_predict_cdna/ until a separate transport review is approved.",
                "Do not add live transport, auth, account, balance, position, order, wallet, or signing logic.",
            ),
            notes="Crypto.com Predict/CDNA is the consumer-facing event-contract product of Crypto.com Derivatives North America. It must NOT be treated as a generic crypto exchange or as a reference/truth feed; classify it as an EXECUTABLE_EVENT_CONTRACT_EXCHANGE that is currently auth/region-review gated.",
            platform_role=PLATFORM_ROLE_EXECUTABLE_EVENT_CONTRACT_EXCHANGE,
            requires_region_review=True,
            requires_execution_mechanics_review=True,
        ),
    ]


def build_platform_api_expansion_report(
    *,
    project_root: Path,
    input_dir: Path,
    generated_at: datetime | None = None,
    extra_platforms: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    profiles = _profiles_with_source_registry(default_platform_api_profiles(), extra_platforms)
    evidence = _saved_file_evidence(project_root=project_root, input_dir=input_dir)
    platforms = [_platform_row(profile, evidence.get(profile.platform_id, {})) for profile in profiles]
    platforms.sort(key=lambda row: (PRIORITY_ORDER.get(row["adapter_priority"], 99), row["platform_id"]))
    recommendations = _recommendations(platforms)
    top_blockers = _top_blockers(platforms)
    return {
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "input_dir": str(input_dir),
        "summary": {
            "platform_count": len(platforms),
            "read_only_only_count": sum(1 for row in platforms if row["execution_status"] == READ_ONLY_ONLY),
            "reference_only_count": sum(1 for row in platforms if row["execution_status"] == REFERENCE_ONLY),
            "requires_auth_review_count": sum(1 for row in platforms if row["execution_status"] == REQUIRES_AUTH_REVIEW),
            "unknown_status_count": sum(1 for row in platforms if row["execution_status"] == UNKNOWN_STATUS),
            "executable_now_count": sum(1 for row in platforms if row["execution_status"] == EXECUTABLE_NOW),
            "p0_adapter_count": sum(1 for row in platforms if row["adapter_priority"] == "P0"),
            "new_paper_actions_created": 0,
            "platform_role_counts": _platform_role_counts(platforms),
            "requires_region_review_count": sum(
                1 for row in platforms if _is_true((row.get("review_flags") or {}).get("requires_region_review"))
            ),
            "requires_execution_mechanics_review_count": sum(
                1 for row in platforms if _is_true((row.get("review_flags") or {}).get("requires_execution_mechanics_review"))
            ),
        },
        "platforms": platforms,
        "normalized_adapter_contract_checklist": _adapter_contract_checklist(),
        "recommendations": recommendations,
        "top_blockers": top_blockers,
        "safety": {
            "saved_files_only": True,
            "live_api_calls_attempted_in_this_command": False,
            "auth_or_account_flow_added": False,
            "order_or_execution_logic_added": False,
            "wallet_or_signing_logic_added": False,
            "private_key_logic_added": False,
            "browser_automation_or_bypass_added": False,
            "candidate_actions_created": False,
            "affects_evaluator_gates": False,
            "reference_only_platforms_claimed_executable": False,
            "auth_required_platforms_auto_enabled": False,
        },
    }


def write_platform_api_expansion_files(
    *,
    project_root: Path,
    input_dir: Path,
    json_output: Path,
    markdown_output: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_platform_api_expansion_report(
        project_root=project_root,
        input_dir=input_dir,
        generated_at=generated_at,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_platform_api_expansion_markdown(report), encoding="utf-8")
    return report


def render_platform_api_expansion_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    recommendations = report.get("recommendations") or {}
    lines = [
        "# Platform API Expansion Audit",
        "",
        "Saved-file-only adapter readiness matrix. This report does not fetch APIs, authenticate, execute orders, or change evaluator gates.",
        "",
        "## Summary",
        "",
        f"- platforms: `{summary.get('platform_count', 0)}`",
        f"- read_only_only: `{summary.get('read_only_only_count', 0)}`",
        f"- reference_only: `{summary.get('reference_only_count', 0)}`",
        f"- requires_auth_review: `{summary.get('requires_auth_review_count', 0)}`",
        f"- requires_region_review: `{summary.get('requires_region_review_count', 0)}`",
        f"- requires_execution_mechanics_review: `{summary.get('requires_execution_mechanics_review_count', 0)}`",
        f"- executable_now: `{summary.get('executable_now_count', 0)}`",
        f"- best_next_platform_adapter: `{recommendations.get('best_next_platform_adapter')}`",
        f"- best_next_family_universe: `{recommendations.get('best_next_family_universe')}`",
        "",
        "## Platform Roles",
        "",
        "| Role | Count |",
        "|---|---:|",
    ]
    for role, count in (summary.get("platform_role_counts") or {}).items():
        lines.append(f"| {_md(role)} | {_md(count)} |")
    lines.extend([
        "",
        "## Platform Matrix",
        "",
        "| Platform | Role | Read-only API | Auth | Region review | Mechanics review | Orderbook | Depth | Settlement rules | Source URL | Fee metadata | Status | Priority | Missing normalized fields |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ])
    for row in report.get("platforms") or []:
        flags = row.get("review_flags") or {}
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("platform_id")),
                    _md(row.get("platform_role")),
                    _md(_tri(row.get("read_only_api_available"))),
                    _md(_tri(row.get("auth_required_for_market_data"))),
                    _md(_tri(flags.get("requires_region_review"))),
                    _md(_tri(flags.get("requires_execution_mechanics_review"))),
                    _md(_tri(row.get("orderbook_available"))),
                    _md(_tri(row.get("depth_available"))),
                    _md(_tri(row.get("settlement_rules_available"))),
                    _md(_tri(row.get("explicit_resolution_source_available"))),
                    _md(_tri(row.get("fee_metadata_available"))),
                    _md(row.get("execution_status")),
                    _md(row.get("adapter_priority")),
                    _md(", ".join(row.get("missing_fields_for_normalized_market") or [])),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Recommendations",
            "",
            f"- best_next_platform_adapter: `{recommendations.get('best_next_platform_adapter')}`",
            f"- best_next_family_universe: `{recommendations.get('best_next_family_universe')}`",
            f"- fastest_path_to_more_cross_platform_candidates: {_md(recommendations.get('fastest_path_to_more_cross_platform_candidates'))}",
            f"- platforms_to_avoid_for_now: `{', '.join(recommendations.get('platforms_to_avoid_for_now') or []) or 'none'}`",
            "",
            "## Top Blockers",
            "",
        ]
    )
    blockers = report.get("top_blockers") or []
    if blockers:
        lines.extend(["| Blocker | Count |", "|---|---:|"])
        for blocker in blockers:
            lines.append(f"| {_md(blocker.get('blocker'))} | {_md(blocker.get('count'))} |")
    else:
        lines.append("(none)")
    lines.extend(
        [
            "",
            "## Adapter Contract Checklist",
            "",
            "| Section | Required fields |",
            "|---|---|",
        ]
    )
    for item in report.get("normalized_adapter_contract_checklist") or []:
        lines.append(f"| {_md(item.get('section'))} | {_md(', '.join(item.get('required_fields') or []))} |")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- saved_files_only: `true`",
            "- live_api_calls_attempted_in_this_command: `false`",
            "- auth_or_account_flow_added: `false`",
            "- order_or_execution_logic_added: `false`",
            "- affects_evaluator_gates: `false`",
        ]
    )
    return "\n".join(lines) + "\n"


def _profiles_with_source_registry(
    base_profiles: list[PlatformApiProfile],
    extra_platforms: Iterable[Mapping[str, Any]] | None,
) -> list[PlatformApiProfile]:
    by_id = {profile.platform_id: profile for profile in base_profiles}
    by_registry = {profile.source_registry_id: profile.platform_id for profile in base_profiles if profile.source_registry_id}

    for source_id, entry in SOURCE_REGISTRY.items():
        if source_id in by_registry:
            continue
        platform_id = source_id
        if platform_id in by_id:
            continue
        by_id[platform_id] = _profile_from_source_entry(entry)

    for raw in extra_platforms or []:
        platform_id = _string_or_none(raw.get("platform_id") or raw.get("source_id"))
        if not platform_id:
            continue
        normalized = platform_id.strip().lower().replace("-", "_").replace(" ", "_")
        by_id[normalized] = _unknown_profile(normalized, raw)

    return list(by_id.values())


def _profile_from_source_entry(entry: SourceEntry) -> PlatformApiProfile:
    if entry.source_type == SourceType.REFERENCE_ONLY:
        execution_status = REFERENCE_ONLY
        adapter_priority = "P2"
        platform_role = PLATFORM_ROLE_REFERENCE_ONLY_TRUTH_FEED
    elif entry.source_type == SourceType.SIGNAL_ONLY:
        execution_status = REFERENCE_ONLY
        adapter_priority = "P2"
        platform_role = PLATFORM_ROLE_DISCOVERY_ONLY
    elif entry.source_type == SourceType.DO_NOT_USE_YET:
        execution_status = UNKNOWN_STATUS
        adapter_priority = "P3"
        platform_role = PLATFORM_ROLE_DISCOVERY_ONLY
    elif entry.implementation_status == ImplementationStatus.PLANNED_NOT_IMPLEMENTED:
        execution_status = REQUIRES_AUTH_REVIEW
        adapter_priority = "P3"
        platform_role = PLATFORM_ROLE_UNKNOWN
    else:
        execution_status = READ_ONLY_ONLY
        adapter_priority = "P1"
        platform_role = PLATFORM_ROLE_UNKNOWN
    return PlatformApiProfile(
        platform_id=entry.source_id,
        display_name=entry.display_name,
        source_registry_id=entry.source_id,
        aliases=(),
        read_only_api_available=entry.implementation_status == ImplementationStatus.IMPLEMENTED_READ_ONLY or UNKNOWN,
        auth_required_for_market_data=UNKNOWN,
        orderbook_available=UNKNOWN,
        depth_available=UNKNOWN,
        trade_history_available=UNKNOWN,
        settlement_rules_available=UNKNOWN,
        explicit_resolution_source_available=UNKNOWN,
        fee_metadata_available=UNKNOWN,
        market_categories_available=(),
        known_family_strengths=(),
        execution_status=execution_status,
        adapter_priority=adapter_priority,
        why_priority=f"Auto-included from source registry: {entry.notes}",
        fake_edge_risks=("source_registry_only_no_adapter_contract",),
        missing_fields_for_normalized_market=(
            "identity_mapping",
            "orderbook_depth_freshness",
            "settlement_source_evidence",
            "fee_model_review",
        ),
        next_adapter_tasks=("Write a saved-file-only fixture or boundary audit before any adapter integration.",),
        notes=entry.notes,
        platform_role=platform_role,
        requires_region_review=UNKNOWN,
        requires_execution_mechanics_review=UNKNOWN,
    )


def _unknown_profile(platform_id: str, raw: Mapping[str, Any]) -> PlatformApiProfile:
    display_name = _string_or_none(raw.get("display_name")) or platform_id
    source_registry_id = _string_or_none(raw.get("source_registry_id")) or _string_or_none(raw.get("source_id"))
    return PlatformApiProfile(
        platform_id=platform_id,
        display_name=display_name,
        source_registry_id=source_registry_id,
        aliases=tuple(str(alias) for alias in raw.get("aliases", ()) if alias),
        read_only_api_available=raw.get("read_only_api_available", UNKNOWN),
        auth_required_for_market_data=raw.get("auth_required_for_market_data", UNKNOWN),
        orderbook_available=raw.get("orderbook_available", UNKNOWN),
        depth_available=raw.get("depth_available", UNKNOWN),
        trade_history_available=raw.get("trade_history_available", UNKNOWN),
        settlement_rules_available=raw.get("settlement_rules_available", UNKNOWN),
        explicit_resolution_source_available=raw.get("explicit_resolution_source_available", UNKNOWN),
        fee_metadata_available=raw.get("fee_metadata_available", UNKNOWN),
        market_categories_available=tuple(str(value) for value in raw.get("market_categories_available", ()) if value),
        known_family_strengths=tuple(str(value) for value in raw.get("known_family_strengths", ()) if value),
        execution_status=UNKNOWN_STATUS,
        adapter_priority="P3",
        why_priority="Unknown platform row: requires saved-file research and source-registry review before use.",
        fake_edge_risks=("unknown_platform_contract", "unreviewed_market_mechanics"),
        missing_fields_for_normalized_market=(
            "identity_mapping",
            "settlement_source_evidence",
            "quote_depth_freshness",
            "fee_model_review",
        ),
        next_adapter_tasks=("Create a saved fixture inspection and source-registry entry before adapter work.",),
        notes="Unknown future platform row; fail-closed.",
        platform_role=PLATFORM_ROLE_UNKNOWN,
        requires_region_review=UNKNOWN,
        requires_execution_mechanics_review=UNKNOWN,
    )


def _platform_row(profile: PlatformApiProfile, saved_evidence: Mapping[str, Any]) -> dict[str, Any]:
    source_entry = SOURCE_REGISTRY.get(profile.source_registry_id or profile.platform_id)
    planned = PLANNED_EXECUTABLE_VENUE_CAPABILITIES.get(profile.source_registry_id or profile.platform_id)
    if profile.platform_id == "ibkr_forecastex":
        planned = PLANNED_EXECUTABLE_VENUE_CAPABILITIES.get("forecastex_ibkr")
    status = _conservative_execution_status(profile, source_entry, planned)
    profile = replace(profile, execution_status=status)
    blockers = _blockers(profile, source_entry, planned)
    return {
        "platform_id": profile.platform_id,
        "display_name": profile.display_name,
        "source_registry_id": profile.source_registry_id,
        "aliases": list(profile.aliases),
        "read_only_api_available": profile.read_only_api_available,
        "auth_required_for_market_data": profile.auth_required_for_market_data,
        "orderbook_available": profile.orderbook_available,
        "depth_available": profile.depth_available,
        "trade_history_available": profile.trade_history_available,
        "settlement_rules_available": profile.settlement_rules_available,
        "explicit_resolution_source_available": profile.explicit_resolution_source_available,
        "fee_metadata_available": profile.fee_metadata_available,
        "market_categories_available": list(profile.market_categories_available),
        "known_family_strengths": list(profile.known_family_strengths),
        "execution_status": profile.execution_status,
        "platform_role": profile.platform_role,
        "review_flags": {
            "requires_auth_review": profile.execution_status == REQUIRES_AUTH_REVIEW,
            "requires_region_review": profile.requires_region_review,
            "requires_execution_mechanics_review": profile.requires_execution_mechanics_review,
        },
        "adapter_priority": profile.adapter_priority,
        "why_priority": profile.why_priority,
        "fake_edge_risks": list(profile.fake_edge_risks),
        "missing_fields_for_normalized_market": list(profile.missing_fields_for_normalized_market),
        "next_adapter_tasks": list(profile.next_adapter_tasks),
        "blockers": blockers,
        "automatic_adapter_use_allowed": _automatic_adapter_use_allowed(profile, source_entry),
        "candidate_actions_allowed": False,
        "affects_evaluator_gates": False,
        "current_repo_evidence": {
            "source_registry": source_entry.to_dict() if source_entry else None,
            "planned_capability": planned.to_dict() if planned else None,
            "saved_files": dict(saved_evidence),
            "notes": profile.notes,
        },
    }


def _automatic_adapter_use_allowed(profile: PlatformApiProfile, source_entry: SourceEntry | None) -> bool:
    return (
        profile.execution_status == READ_ONLY_ONLY
        and source_entry is not None
        and source_entry.implementation_status == ImplementationStatus.IMPLEMENTED_READ_ONLY
    )


def _conservative_execution_status(
    profile: PlatformApiProfile,
    source_entry: SourceEntry | None,
    planned: Any,
) -> str:
    if source_entry and source_entry.source_type in {SourceType.REFERENCE_ONLY, SourceType.SIGNAL_ONLY}:
        return REFERENCE_ONLY
    if source_entry and source_entry.source_type == SourceType.DO_NOT_USE_YET:
        return UNKNOWN_STATUS
    if _is_true(profile.auth_required_for_market_data):
        return REQUIRES_AUTH_REVIEW
    if planned is not None and getattr(planned, "requires_auth_for_data", False):
        return REQUIRES_AUTH_REVIEW
    if planned is not None and not getattr(planned, "execution_allowed_in_project_now", False):
        return READ_ONLY_ONLY if profile.execution_status == READ_ONLY_ONLY else profile.execution_status
    if profile.execution_status == EXECUTABLE_NOW:
        return READ_ONLY_ONLY
    return profile.execution_status


def _blockers(profile: PlatformApiProfile, source_entry: SourceEntry | None, planned: Any) -> list[str]:
    blockers: set[str] = set()
    if profile.execution_status == REFERENCE_ONLY:
        blockers.add("reference_only_not_executable")
    if profile.execution_status == REQUIRES_AUTH_REVIEW:
        blockers.add("auth_or_account_permission_review_required")
        blockers.add("automatic_adapter_use_blocked")
    if profile.execution_status == UNKNOWN_STATUS:
        blockers.add("unknown_or_unreviewed_platform_contract")
    if source_entry and source_entry.implementation_status == ImplementationStatus.PLANNED_NOT_IMPLEMENTED:
        blockers.add("source_registry_planned_not_implemented")
    if source_entry and source_entry.source_type == SourceType.DO_NOT_USE_YET:
        blockers.add("do_not_use_yet_source_type")
    if planned is not None and not getattr(planned, "execution_allowed_in_project_now", False):
        blockers.add("execution_not_allowed_in_project_now")
    if profile.orderbook_available is not True:
        blockers.add("orderbook_missing_or_unreviewed")
    if profile.depth_available is not True:
        blockers.add("depth_missing_or_unreviewed")
    if profile.settlement_rules_available is not True:
        blockers.add("settlement_rules_missing_or_unknown")
    if profile.explicit_resolution_source_available is not True:
        blockers.add("explicit_resolution_source_missing_or_unknown")
    if profile.fee_metadata_available is not True:
        blockers.add("fee_metadata_missing_or_unreviewed")
    if profile.read_only_api_available is not True:
        blockers.add("read_only_market_data_api_missing_or_unknown")
    if _is_true(profile.auth_required_for_market_data):
        blockers.add("auth_required_for_market_data")
    if profile.platform_id in {"kalshi", "polymarket"}:
        blockers.add("read_only_adapter_only_no_execution_flow")
    if _is_true(profile.requires_region_review):
        blockers.add("region_eligibility_review_required")
    if _is_true(profile.requires_execution_mechanics_review):
        blockers.add("execution_mechanics_review_required")
    if profile.platform_role == PLATFORM_ROLE_EXECUTABLE_SPORTSBOOK_OR_BETTING_EXCHANGE:
        blockers.add("sportsbook_or_exchange_mechanics_review_required")
    if profile.platform_role == PLATFORM_ROLE_UNKNOWN:
        blockers.add("platform_role_unclassified")
    return sorted(blockers)


def _saved_file_evidence(*, project_root: Path, input_dir: Path) -> dict[str, dict[str, Any]]:
    platform_patterns = {
        "kalshi": ("kalshi",),
        "polymarket": ("polymarket",),
        "ibkr_forecastex": ("ibkr", "forecastex"),
        "prophetx": ("prophetx",),
        "crypto_com_predict_cdna": ("crypto_com_predict_cdna", "crypto_com_predict", "crypto.com_predict", "cdna"),
        "sx_bet": ("sx_bet", "sx-bet", "sxbet"),
        "manifold": ("manifold",),
        "the_odds_api": ("odds_api", "sportsbook"),
        "azuro": ("azuro",),
        "omen_gnosis": ("omen", "gnosis"),
        "predictit": ("predictit",),
    }
    roots = [
        ("fixture", project_root / "venues" / "fixtures"),
        ("report", input_dir),
    ]
    evidence: dict[str, dict[str, Any]] = {
        platform_id: {"fixture_files": [], "report_files": [], "saved_report_summaries": []}
        for platform_id in platform_patterns
    }
    for kind, root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.json")):
            lowered = str(path.relative_to(root)).lower()
            for platform_id, patterns in platform_patterns.items():
                if any(pattern in lowered for pattern in patterns):
                    key = "fixture_files" if kind == "fixture" else "report_files"
                    evidence[platform_id][key].append(str(path))
                    if kind == "report":
                        summary = _saved_report_summary(path)
                        if summary:
                            evidence[platform_id]["saved_report_summaries"].append(summary)
                    break
    for value in evidence.values():
        value["fixture_file_count"] = len(value["fixture_files"])
        value["report_file_count"] = len(value["report_files"])
    return evidence


def _saved_report_summary(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"path": str(path), "status": "unreadable_or_invalid_json"}
    if not isinstance(payload, dict):
        return {"path": str(path), "status": "json_not_object"}
    fields = (
        "source",
        "schema_version",
        "permission",
        "implementation_status",
        "is_executable",
        "execution_allowed_in_project_now",
        "can_create_candidate_pair",
        "can_create_paper_candidate",
        "live_fetch_attempted",
        "live_fetch_succeeded",
        "market_count",
        "order_count",
        "research_market_count",
        "instrument_count",
        "quote_count",
        "fee_count",
    )
    summary = {"path": str(path)}
    for field in fields:
        if field in payload:
            summary[field] = payload.get(field)
    return summary


def _adapter_contract_checklist() -> list[dict[str, Any]]:
    return [
        {
            "section": "identity",
            "required_fields": ["venue", "event_id", "event_ticker", "event_slug", "market_id", "ticker", "token_id"],
            "notes": "Preserve nullable identity slots and raw evidence paths; do not require venue-specific IDs to exist.",
        },
        {
            "section": "event_grouping",
            "required_fields": ["event_id", "series_id_or_slug", "category", "family", "typed_keys"],
            "notes": "Grouping fields are diagnostic until exact payoff review is complete.",
        },
        {
            "section": "outcomes_token_ids",
            "required_fields": ["outcome_name", "outcome_token_id", "side", "payout_terms"],
            "notes": "Outcome/token mapping must be explicit for CLOB-style venues and marked missing otherwise.",
        },
        {
            "section": "settlement_rules_source",
            "required_fields": ["settlement_rules_text", "settlement_source_url", "settlement_source_kind", "raw_evidence_paths"],
            "notes": "Rules text is not an external source URL; source evidence must stay distinct.",
        },
        {
            "section": "close_resolution_expiry_times",
            "required_fields": ["close_time", "resolution_time", "resolution_time_kind", "expiry_time"],
            "notes": "Expected/deadline timestamps must not be treated as actual resolution times.",
        },
        {
            "section": "orderbook_depth_freshness",
            "required_fields": ["best_bid", "best_ask", "bid_size", "ask_size", "depth_by_band", "captured_at"],
            "notes": "Record update timestamps do not count as quote freshness.",
        },
        {
            "section": "fee_model",
            "required_fields": ["fee_model_status", "fee_source_kind", "fee_source_url_or_default_marker", "reviewed_at"],
            "notes": "Fee metadata needs explicit reviewed evidence or a conservative venue default marker.",
        },
        {
            "section": "raw_payload_source_evidence",
            "required_fields": ["source_file", "source_schema", "raw_field_paths", "collector_version"],
            "notes": "Every normalized field should point back to saved raw payload evidence where possible.",
        },
    ]


def _recommendations(platforms: list[dict[str, Any]]) -> dict[str, Any]:
    best = next((row for row in platforms if row.get("adapter_priority") == "P0"), platforms[0] if platforms else None)
    avoid = [
        row["platform_id"]
        for row in platforms
        if row.get("execution_status") in {REQUIRES_AUTH_REVIEW, UNKNOWN_STATUS}
        or "do_not_use_yet_source_type" in row.get("blockers", [])
    ]
    return {
        "best_next_platform_adapter": best.get("platform_id") if best else None,
        "best_next_family_universe": "SPORTS",
        "fastest_path_to_more_cross_platform_candidates": (
            "Normalize saved SX Bet research rows into diagnostic-only records, then compare sports family keys against current Kalshi/Polymarket sports coverage without evaluator promotion."
        ),
        "platforms_to_avoid_for_now": sorted(avoid),
        "why_avoid": "Avoid auth-gated, do-not-use-yet, and unknown platform contracts until saved-file fixture and permission reviews are complete.",
    }


def _top_blockers(platforms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for row in platforms:
        counts.update(row.get("blockers") or [])
    return [{"blocker": blocker, "count": count} for blocker, count in counts.most_common()]


def _platform_role_counts(platforms: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in platforms:
        counts[row.get("platform_role") or PLATFORM_ROLE_UNKNOWN] += 1
    return {role: counts.get(role, 0) for role in PLATFORM_ROLES if counts.get(role, 0) > 0 or role == PLATFORM_ROLE_UNKNOWN}


def _tri(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return UNKNOWN


def _is_true(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.lower() == "true")


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _md(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
