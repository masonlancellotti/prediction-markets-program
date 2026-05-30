from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

from relative_value._numeric import float_or_none
from relative_value.fees import FeeModel, KalshiTieredFeeModel, PolymarketConservativeFeeModel
from relative_value.live_snapshot_matcher import (
    DEFAULT_SETTLEMENT_BONUS_WINDOW_SECONDS,
    _event_keyword_bonus,
    _event_keyword_tokens,
    _market_question,
    _meaningful_tokens,
    _numeric_tokens,
    _parse_datetime_or_none,
    _settlement_time_bonus,
    _settlement_time_delta_seconds,
    _settlement_time_warning,
    _text_similarity,
    match_snapshot_files,
)
from relative_value.llm_relationship_review_report import review_relationship_report_file
from relative_value.markout_replay import MarkoutReplayConfig, replay_paper_candidate_markout_files
from relative_value.market_graph_diagnostics import build_market_graph_diagnostics_files
from relative_value.market_graph_hints import explain_market_graph_diagnostics_files
from relative_value.mlb_same_scope_audit import audit_same_scope_mlb_candidate_files
from relative_value.mlb_same_scope_audit import build_mlb_world_series_pairs_files
from relative_value.mlb_same_scope_audit import diagnose_mlb_same_scope_targeting_files
from relative_value.mlb_world_series_execution_diagnostics import diagnose_mlb_world_series_execution_blockers_files
from relative_value.mlb_world_series_execution_diagnostics import diagnose_mlb_world_series_evaluator_blockers_files
from relative_value.nhl_same_scope import build_nhl_stanley_cup_pairs_files
from relative_value.orderbook_enrichment import enrich_orderbook_snapshot_file
from relative_value.orderbook_enrichment import enrich_orderbook_snapshot
from relative_value.paper_candidate_evaluator import (
    PaperCandidateEvaluatorConfig,
    evaluate_paper_candidate_files,
)
from relative_value.provenance import build_fixture_scan_provenance, source_readiness_report
from relative_value.reference_diagnostics import explain_reference_context_files, write_reference_odds_fv_files
from relative_value.report import write_json_report, write_markdown_report
from relative_value.scanner import RelativeValueScanner
from relative_value.same_payoff_board import build_same_payoff_board_files
from relative_value.same_payoff_board import diagnose_mlb_world_series_board_blockers_files
from relative_value.same_payoff_evidence import attach_same_payoff_evidence_files
from relative_value.source_registry import ImplementationStatus, SOURCE_REGISTRY, SourceType
from relative_value.executable_venue_plan import PLANNED_EXECUTABLE_VENUE_CAPABILITIES
from relative_value.exact_paper_candidate_universes import build_exact_paper_candidate_universe_report_files
from relative_value.exact_market_expansion_plan import write_exact_market_expansion_plan_files
from relative_value.platform_expansion_matrix import write_platform_expansion_matrix_files
from relative_value.structural_basket_detector import build_structural_basket_review_report_files
from relative_value.structural_manifest_scout import scout_structural_manifest_candidates_file
from relative_value.structural_basket_parlay_scout import write_structural_basket_parlay_scout_files
from relative_value.kalshi_event_metadata import (
    audit_kalshi_event_metadata_files,
    join_kalshi_event_metadata_files,
)
from relative_value.kalshi_event_evidence_summary import write_kalshi_event_evidence_summary_files
from relative_value.paper_fill_simulator import simulate_paper_fill_journal_files
from relative_value.kalshi_native_groups import audit_kalshi_native_groups_file, kalshi_native_group_audit_paths
from relative_value.structural_basket_dry_run import (
    import_kalshi_event_metadata_files,
    render_metadata_importer_markdown,
    run_structural_basket_dry_run_files,
)
from relative_value.structural_basket_hunter import (
    hunt_structural_basket_candidates_files,
)
from relative_value.cross_platform_opportunity_triage import (
    write_cross_platform_opportunity_triage_files,
)
from relative_value.crypto_com_predict_cdna_saved_page_parser import (
    write_crypto_com_predict_cdna_research_snapshot_file,
)
from relative_value.cdna_vs_kalshi_btc_basis_risk import write_cdna_vs_kalshi_btc_basis_risk_file
from relative_value.normalized_markets_v0 import write_normalized_markets_v0_files
from relative_value.quote_freshness_policy import DEFAULT_STALENESS_SECONDS
from relative_value.settlement_evidence_burden import write_settlement_evidence_burden_files
from relative_value.standardized_family_candidates import write_standardized_family_candidates_files
from relative_value.canonical_convention_registry import build_canonical_convention_registry_audit
from relative_value.canonical_registry_coverage import write_canonical_registry_coverage_files
from relative_value.canonical_registry_expiry_audit import write_canonical_registry_expiry_audit_files
from relative_value.cdna_crypto_basis_risk_scout import (
    write_cdna_crypto_basis_risk_scout_files,
)
from relative_value.cdna_fill_first_scout import (
    DEFAULT_MAX_QUOTE_AGE_SECONDS as CDNA_FILL_FIRST_DEFAULT_MAX_QUOTE_AGE_SECONDS,
    write_cdna_fill_first_scout_files,
)
from relative_value.cdna_fill_log import record_cdna_fill_file
from relative_value.polymarket_taxonomy_shape_scout import (
    write_polymarket_taxonomy_shape_scout_files,
)
from relative_value.polymarket_clob_taxonomy_refresh import (
    DEFAULT_SHAPE_PRIORITY as POLYMARKET_CLOB_TAXONOMY_REFRESH_DEFAULT_SHAPE_PRIORITY,
    write_polymarket_clob_taxonomy_refresh_files,
)
from relative_value.kalshi_crypto_typed_key_audit import (
    _classify_shape as _kalshi_crypto_classify_shape,
    _extract_asset as _kalshi_crypto_extract_asset,
    _extract_comparator as _kalshi_crypto_extract_comparator,
    _extract_settlement_source as _kalshi_crypto_extract_settlement_source,
    _extract_target_datetime as _kalshi_crypto_extract_target_datetime,
    _extract_threshold as _kalshi_crypto_extract_threshold,
    write_kalshi_crypto_typed_key_audit_files,
)
from relative_value.crypto_peer_acquisition_plan import (
    write_crypto_peer_acquisition_plan_files,
)
from relative_value.crypto_payoff_calendar_audit import (
    write_crypto_payoff_calendar_audit_files,
)
from relative_value.crypto_manual_discovery_workbench import (
    write_crypto_manual_discovery_workbench_files,
)
from relative_value.crypto_threshold_basis_review_scout import write_crypto_threshold_basis_review_scout_files
from relative_value.daily_crypto_three_venue_check import (
    DEFAULT_EVIDENCE_ROOTS as DAILY_CRYPTO_DEFAULT_EVIDENCE_ROOTS,
    write_daily_crypto_three_venue_check_files,
)
from relative_value.crypto_interval_three_venue_check import (
    write_crypto_interval_three_venue_check_files,
)
from relative_value.crypto_structural_payoff_arb_scout import (
    write_crypto_structural_payoff_arb_scout_files,
)
from relative_value.watch_crypto_structural_arb import run_watch as run_crypto_structural_watch
from relative_value.batch_evidence_import_readiness import write_batch_evidence_import_readiness_files
from relative_value.extract_crypto_paper_candidate_audit_pack import (
    write_crypto_paper_candidate_audit_pack_files,
)
from relative_value.cdna_crypto_snapshot_ingest import write_cdna_crypto_snapshot_files
from relative_value.execution_microstructure_plan import write_execution_plan_files
from relative_value.crypto_micro_test_journal import (
    start_crypto_micro_test,
    record_crypto_micro_fill,
    finalize_crypto_micro_test,
    crypto_micro_test_report,
    append_crypto_micro_quote_snapshot,
)
from relative_value.live_crypto_micro_executor import run_crypto_structural_trigger
from relative_value.crypto_arb_surface_coverage_audit import write_crypto_arb_surface_coverage_audit_files
from relative_value.crypto_fast_path_executor import (
    build_active_candidate_universe,
    run_crypto_fast_path_trigger,
)
from relative_value.daily_summary_notifier import write_and_send_daily_summary
from relative_value.notification_providers import PROVIDER_NAMES
from relative_value.championship_operator_scout_generic import write_championship_operator_scout_generic_files
from relative_value.three_venue_operator_scout import write_three_venue_operator_scout_files
from relative_value.manual_evidence_requirements import (
    write_manual_evidence_requirements_files,
)
from relative_value.polymarket_point_in_time_typed_key_audit import (
    write_polymarket_point_in_time_typed_key_audit_files,
)
from relative_value.cross_venue_opportunity_scout import (
    write_cross_venue_opportunity_scout_files,
)
from relative_value.core_trio_peer_coverage_audit import (
    write_core_trio_peer_coverage_audit_files,
)
from relative_value.relative_value_ops_status import write_relative_value_ops_status_files
from relative_value.existing_paper_candidate_audit import write_existing_paper_candidate_audit_files
from relative_value.family_graduation import write_family_graduation_files
from relative_value.ibkr_forecastex_readonly_access import (
    DEFAULT_IBKR_FORECASTEX_BASE_URL,
    DEFAULT_MAX_CONTRACT_INFO_REQUESTS,
    DEFAULT_MAX_FOLLOWUP_ERRORS,
    build_ibkr_forecastex_access_doctor,
    write_ibkr_forecastex_access_doctor_file,
    write_ibkr_forecastex_readonly_snapshot_file,
)
from relative_value.ibkr_forecastex_manual_memo import validate_ibkr_forecastex_manual_memo_file
from relative_value.mlb_world_series_revival_status import write_mlb_world_series_revival_status_files
from relative_value.platform_api_expansion import write_platform_api_expansion_files
from relative_value.paper_readiness_probe import write_paper_readiness_probe_files
from relative_value.polymarket_crypto_discovery_normalizer import write_polymarket_crypto_discovery_normalization_files
from relative_value.polymarket_market_taxonomy import write_polymarket_market_universe_files
from relative_value.polymarket_public_discovery import write_polymarket_crypto_discovery_files
from relative_value.pending_registry_entries_plan import (
    audit_pending_registry_entries_for_promotion,
    write_pending_registry_entries_plan,
)
from relative_value.sx_bet_saved_adapter import write_sx_bet_saved_normalization_files
from relative_value.sx_bet_sports_overlap import write_sx_bet_sports_overlap_files
from relative_value.sx_bet_sports_typed_keys import write_sx_bet_sports_typed_keys_files
from relative_value.sports_mlb_daily_residual_risk_scout import (
    DEFAULT_MAX_QUOTE_AGE_SECONDS as MLB_DAILY_RESIDUAL_RISK_DEFAULT_MAX_QUOTE_AGE_SECONDS,
    DEFAULT_MIN_AVAILABLE_NOTIONAL as MLB_DAILY_RESIDUAL_RISK_DEFAULT_MIN_AVAILABLE_NOTIONAL,
    write_sports_mlb_daily_residual_risk_files,
)
from relative_value.sports_mlb_daily_game_evidence_collector import write_mlb_daily_game_evidence_files
from relative_value.sports_mlb_world_series_evidence_compare import (
    write_sports_mlb_world_series_evidence_compare_files,
)
from relative_value.sports_mlb_world_series_evidence_collector import write_mlb_world_series_evidence_files
from relative_value.sports_mlb_world_series_residual_risk_scout import (
    DEFAULT_MAX_QUOTE_AGE_SECONDS as MLB_WORLD_SERIES_RESIDUAL_RISK_DEFAULT_MAX_QUOTE_AGE_SECONDS,
    DEFAULT_MIN_AVAILABLE_NOTIONAL as MLB_WORLD_SERIES_RESIDUAL_RISK_DEFAULT_MIN_AVAILABLE_NOTIONAL,
    write_sports_mlb_world_series_residual_risk_files,
)
from relative_value.operator_arb_convergence_plan import write_operator_arb_convergence_plan_files
from relative_value.stale_report_archive_plan import (
    apply_stale_report_archive_plan,
    write_stale_report_archive_plan_files,
)
from relative_value.venue_metadata_coverage import write_venue_metadata_coverage_files
from venues.kalshi import (
    FixtureKalshiAdapter,
    KalshiMarketFilterOptions,
    KalshiReadOnlyClient,
    write_kalshi_market_snapshot,
)
from venues.orderbooks import KalshiOrderbookClient, PolymarketOrderbookClient
from venues.polymarket import (
    FixturePolymarketAdapter,
    PolymarketGammaClient,
    PolymarketMarketFilterOptions,
    write_polymarket_market_snapshot,
)
from venues.the_odds_api import FixtureTheOddsApiAdapter, TheOddsApiReadOnlyClient, write_the_odds_api_reference_snapshot
from venues.sx_bet import SXBetReadOnlyClient, SXBetReadOnlyFetchError, build_sx_bet_failure_snapshot
from venues.ibkr_forecastex import (
    IBKR_FORECASTEX_REQUIRED_BLOCKERS,
    IBKR_FORECASTEX_RESEARCH_SCHEMA_KIND,
    load_ibkr_forecastex_research_fixtures,
)
from venues.prophetx import (
    PROPHETX_REQUIRED_BLOCKERS,
    PROPHETX_RESEARCH_SCHEMA_KIND,
    load_prophetx_research_fixtures,
)


PROJECT_ROOT = Path(__file__).resolve().parent


def _default_canonical_registry_path() -> Path | None:
    path = PROJECT_ROOT / "docs" / "example_canonical_convention_registry_v0.json"
    return path if path.exists() else None


def build_fixture_adapters(fixture_dir: Path) -> list[object]:
    return [
        FixtureKalshiAdapter(fixture_dir / "kalshi_markets.json"),
        FixturePolymarketAdapter(fixture_dir / "polymarket_markets.json"),
        FixtureTheOddsApiAdapter(fixture_dir / "the_odds_api_events.json"),
    ]


def _str2bool(value: str) -> bool:
    return str(value).strip().lower() in ("true", "1", "yes", "y", "on")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only relative-value scanner")
    subparsers = parser.add_subparsers(dest="command")

    fetch_parser = subparsers.add_parser(
        "fetch-polymarket",
        help="Fetch a small read-only Polymarket Gamma market snapshot.",
    )
    fetch_parser.add_argument("--limit", type=int, default=25)
    fetch_parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "reports" / "polymarket_markets_snapshot.json")
    fetch_parser.add_argument("--timeout-seconds", type=float, default=10.0)
    fetch_parser.add_argument("--tag-slug", help="Filter Polymarket Gamma events by tag slug.")
    fetch_parser.add_argument("--tag-id", type=int, help="Filter Polymarket Gamma events by tag id.")
    fetch_parser.add_argument("--include-closed", action="store_true", help="Include closed Polymarket markets.")
    fetch_parser.add_argument(
        "--include-not-accepting-orders",
        action="store_true",
        help="Include markets where acceptingOrders is false.",
    )
    fetch_parser.add_argument(
        "--include-past-end-date",
        action="store_true",
        help="Include markets with parseable end dates before the fetch timestamp.",
    )

    polymarket_crypto_discovery_parser = subparsers.add_parser(
        "discover-polymarket-crypto-markets",
        help=(
            "Explicit public no-auth Polymarket Gamma/CLOB read-only crypto threshold discovery. "
            "Not part of the default scan and never creates candidate pairs."
        ),
    )
    polymarket_crypto_discovery_parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "reports" / "manual_snapshots" / "polymarket_crypto",
        help="Base directory for timestamped raw public discovery snapshots.",
    )
    polymarket_crypto_discovery_parser.add_argument("--limit", type=int, default=200)
    polymarket_crypto_discovery_parser.add_argument("--timeout-seconds", type=float, default=10.0)
    polymarket_crypto_discovery_parser.add_argument(
        "--max-pages",
        type=int,
        default=3,
        help="Maximum offset pages to try per public endpoint pattern.",
    )
    polymarket_crypto_discovery_parser.add_argument(
        "--include-books",
        action="store_true",
        help="Also call public CLOB book endpoints for discovered token IDs. Defaults off.",
    )
    polymarket_crypto_discovery_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "polymarket_crypto_discovery.json",
    )
    polymarket_crypto_discovery_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "polymarket_crypto_discovery.md",
    )
    polymarket_crypto_discovery_parser.add_argument(
        "--query",
        type=str,
        default=None,
        help=(
            "Targeted public-no-auth search term passed to Gamma's `search=` query parameter, "
            "e.g. \"bitcoin May 29, 2026\". When set, the broad TARGETED_SEARCH_TERMS list is skipped "
            "in favor of this single query plus optional --queries-file entries."
        ),
    )
    polymarket_crypto_discovery_parser.add_argument(
        "--queries-file",
        type=Path,
        default=None,
        help=(
            "Optional path to a text file (one search term per line). Each non-blank, non-comment line "
            "becomes an additional targeted Gamma search."
        ),
    )
    polymarket_crypto_discovery_parser.add_argument(
        "--asset",
        type=str,
        default=None,
        help=(
            "Optional client-side asset filter (BTC / ETH / SOL). Drops candidates whose title/question "
            "does not mention the asset. No live private endpoints are called."
        ),
    )
    polymarket_crypto_discovery_parser.add_argument(
        "--target-date",
        type=str,
        default=None,
        help=(
            "Optional client-side target-date filter (e.g. 2026-05-29). Drops candidates whose title "
            "does not contain a matching date rendering. Pure post-filter; never inferred."
        ),
    )
    polymarket_crypto_normalize_parser = subparsers.add_parser(
        "normalize-polymarket-crypto-discovery",
        help="Saved-file-only conversion of Polymarket public discovery candidates into manual crypto fixtures.",
    )
    polymarket_crypto_normalize_parser.add_argument(
        "--discovery",
        type=Path,
        default=PROJECT_ROOT / "reports" / "polymarket_crypto_discovery.json",
        help="Saved discover-polymarket-crypto-markets JSON output.",
    )
    polymarket_crypto_normalize_parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "reports" / "manual_snapshots" / "polymarket_crypto" / "normalized",
        help="Directory for generated manual_polymarket_crypto_event_page_snapshot fixture files.",
    )
    polymarket_crypto_normalize_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "polymarket_crypto_discovery_normalized.json",
    )
    polymarket_universe_parser = subparsers.add_parser(
        "discover-polymarket-market-universe",
        help=(
            "Explicit public no-auth Polymarket Gamma/CLOB read-only market universe discovery "
            "and taxonomy report. Not part of the default scan and never creates candidate pairs."
        ),
    )
    polymarket_universe_parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "reports" / "manual_snapshots" / "polymarket_universe",
        help="Base directory for timestamped raw public universe snapshots.",
    )
    polymarket_universe_parser.add_argument("--limit", type=int, default=1000)
    polymarket_universe_parser.add_argument("--timeout-seconds", type=float, default=10.0)
    polymarket_universe_parser.add_argument(
        "--max-pages",
        type=int,
        default=2,
        help="Maximum offset pages to try per public endpoint pattern.",
    )
    polymarket_universe_parser.add_argument(
        "--include-books",
        action="store_true",
        help="Also call public CLOB book endpoints for discovered token IDs. Defaults off.",
    )
    polymarket_universe_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "polymarket_market_taxonomy.json",
    )
    polymarket_universe_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "polymarket_market_taxonomy.md",
    )

    kalshi_parser = subparsers.add_parser(
        "fetch-kalshi",
        help="Fetch a small read-only Kalshi market snapshot.",
    )
    kalshi_parser.add_argument("--limit", type=int, default=25)
    kalshi_parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "reports" / "kalshi_markets_snapshot.json")
    kalshi_parser.add_argument("--timeout-seconds", type=float, default=10.0)
    kalshi_parser.add_argument("--series-ticker", help="Filter Kalshi markets by series ticker.")
    kalshi_parser.add_argument("--event-ticker", help="Filter Kalshi markets by event ticker.")
    kalshi_parser.add_argument("--cursor", help="Start Kalshi market discovery from a pagination cursor.")
    kalshi_parser.add_argument(
        "--max-pages",
        type=int,
        default=1,
        help="Maximum Kalshi market pages to fetch when the response exposes a cursor.",
    )
    kalshi_parser.add_argument("--include-closed", action="store_true", help="Include closed/settled Kalshi markets.")
    kalshi_parser.add_argument(
        "--include-past-close-time",
        action="store_true",
        help="Include markets with parseable close times before the fetch timestamp.",
    )

    kalshi_crypto_parser = subparsers.add_parser(
        "fetch-kalshi-crypto-readonly",
        help="Fetch current/future public Kalshi BTC/ETH crypto threshold markets, optionally with read-only orderbooks.",
    )
    kalshi_crypto_parser.add_argument(
        "--asset",
        default="BTC,ETH",
        help="Comma-separated crypto assets to fetch. Supported: BTC, ETH.",
    )
    kalshi_crypto_parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "live_readonly" / "crypto" / "kalshi_live_readonly_snapshot.json",
    )
    kalshi_crypto_parser.add_argument("--limit", type=int, default=1000)
    kalshi_crypto_parser.add_argument("--max-pages", type=int, default=20)
    kalshi_crypto_parser.add_argument("--timeout-seconds", type=float, default=10.0)
    kalshi_crypto_parser.add_argument(
        "--max-orderbooks",
        type=int,
        default=200,
        help="Maximum public orderbook endpoints to call when --include-orderbooks is set. Use 0 to skip orderbook calls.",
    )
    kalshi_crypto_parser.add_argument(
        "--include-orderbooks",
        action="store_true",
        help="Also call public no-auth Kalshi orderbook endpoints for the retained current/future crypto rows.",
    )

    odds_parser = subparsers.add_parser(
        "fetch-the-odds-api",
        help="Fetch a read-only sportsbook reference odds snapshot from The Odds API.",
    )
    odds_parser.add_argument("--sport-key", required=True, help="The Odds API sport key, for example basketball_nba.")
    odds_parser.add_argument("--regions", default="us")
    odds_parser.add_argument("--markets", default="h2h,spreads,totals")
    odds_parser.add_argument("--odds-format", default="american")
    odds_parser.add_argument("--api-key", help="The Odds API key. Prefer --api-key-env for local use.")
    odds_parser.add_argument("--api-key-env", default="THE_ODDS_API_KEY")
    odds_parser.add_argument("--timeout-seconds", type=float, default=10.0)
    odds_parser.add_argument("--stale-after-seconds", type=int, default=900)
    odds_parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "reports" / "the_odds_api_reference_snapshot.json")

    reference_odds_fv_parser = subparsers.add_parser(
        "audit-reference-odds-fv",
        help="Saved-file-only fair-value residual diagnostics for The Odds API reference snapshots.",
    )
    reference_odds_fv_parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help="Saved reports directory containing manual_snapshots/the_odds_api snapshots and optional sports reports.",
    )
    reference_odds_fv_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "the_odds_api_fv_residuals.json",
    )
    reference_odds_fv_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "the_odds_api_fv_residuals.md",
    )

    sx_bet_parser = subparsers.add_parser(
        "fetch-sx-bet-readonly",
        help=(
            "Fetch a public read-only REST-only SX Bet research snapshot; no auth, no orders, "
            "no wallet/signing, diagnostic only."
        ),
    )
    sx_bet_parser.add_argument("--max-markets", type=int, default=25)
    sx_bet_parser.add_argument("--timeout-seconds", type=float, default=10.0)
    sx_bet_parser.add_argument("--sport", help="Optional local sport filter for SX Bet research snapshots, for example baseball or basketball.")
    sx_bet_parser.add_argument("--league", help="Optional local league filter for SX Bet research snapshots, for example MLB, NBA, or NFL.")
    sx_bet_parser.add_argument("--query", help="Optional local free-text filter across SX Bet event/team/outcome fields.")
    sx_bet_parser.add_argument("--label", help="Optional safe label for reports/sx_bet/<label>/sx_bet_research_snapshot.json.")
    sx_bet_parser.add_argument("--output", type=Path, help="Raw sx_bet_research_snapshot_v1 JSON output path.")
    sx_bet_parser.add_argument(
        "--output-dir",
        type=Path,
        help="Optional base directory for a timestamped raw SX Bet public snapshot, for example reports/manual_snapshots/sx_bet.",
    )
    sx_bet_parser.add_argument(
        "--json-output",
        type=Path,
        help="Optional sx_bet_normalized_draft_v1 JSON output path derived from the fetched raw snapshot.",
    )
    sx_bet_parser.add_argument(
        "--coverage-output",
        type=Path,
        help="Optional sx_bet_normalized_draft_coverage_v1 JSON output path.",
    )

    sx_bet_public_snapshot_parser = subparsers.add_parser(
        "fetch-sx-bet-public-snapshot",
        help=(
            "Alias for fetch-sx-bet-readonly. Public read-only REST only; no auth, no orders, "
            "no wallet/signing; output is diagnostic only."
        ),
    )
    sx_bet_public_snapshot_parser.add_argument("--max-markets", type=int, default=25)
    sx_bet_public_snapshot_parser.add_argument("--timeout-seconds", type=float, default=10.0)
    sx_bet_public_snapshot_parser.add_argument("--sport", help="Optional local sport filter for SX Bet research snapshots, for example baseball or basketball.")
    sx_bet_public_snapshot_parser.add_argument("--league", help="Optional local league filter for SX Bet research snapshots, for example MLB, NBA, or NFL.")
    sx_bet_public_snapshot_parser.add_argument("--query", help="Optional local free-text filter across SX Bet event/team/outcome fields.")
    sx_bet_public_snapshot_parser.add_argument("--label", help="Optional safe label for reports/sx_bet/<label>/sx_bet_research_snapshot.json.")
    sx_bet_public_snapshot_parser.add_argument("--output", type=Path, help="Raw sx_bet_research_snapshot_v1 JSON output path.")
    sx_bet_public_snapshot_parser.add_argument(
        "--output-dir",
        type=Path,
        help="Optional base directory for a timestamped raw SX Bet public snapshot, for example reports/manual_snapshots/sx_bet.",
    )
    sx_bet_public_snapshot_parser.add_argument(
        "--json-output",
        type=Path,
        help="Optional sx_bet_normalized_draft_v1 JSON output path derived from the fetched raw snapshot.",
    )
    sx_bet_public_snapshot_parser.add_argument(
        "--coverage-output",
        type=Path,
        help="Optional sx_bet_normalized_draft_coverage_v1 JSON output path.",
    )

    sx_bet_compare_parser = subparsers.add_parser(
        "compare-sx-bet-reference",
        help="Compare saved SX Bet research snapshots against saved Kalshi/Polymarket snapshots as reference context only.",
    )
    sx_bet_compare_parser.add_argument(
        "--sx-bet-snapshot",
        type=Path,
        default=None,
    )
    sx_bet_compare_parser.add_argument(
        "--kalshi-snapshot",
        type=Path,
        default=PROJECT_ROOT / "reports" / "live_readonly" / "kalshi_live_readonly_snapshot.json",
    )
    sx_bet_compare_parser.add_argument(
        "--polymarket-snapshot",
        type=Path,
        default=PROJECT_ROOT / "reports" / "live_readonly" / "polymarket_live_readonly_snapshot.json",
    )
    sx_bet_compare_parser.add_argument("--top-limit", type=int, default=20)
    sx_bet_compare_parser.add_argument("--label", help="Optional safe label for reports/sx_bet_reference/<label>/ outputs.")
    sx_bet_compare_parser.add_argument(
        "--json-output",
        type=Path,
    )
    sx_bet_compare_parser.add_argument(
        "--markdown-output",
        type=Path,
    )

    match_parser = subparsers.add_parser(
        "match-live-snapshots",
        help="Match saved Kalshi/Polymarket schema-v1 snapshots for manual review only.",
    )
    match_parser.add_argument("--polymarket", type=Path, default=PROJECT_ROOT / "reports" / "polymarket_markets_snapshot.json")
    match_parser.add_argument("--kalshi", type=Path, default=PROJECT_ROOT / "reports" / "kalshi_markets_snapshot.json")
    match_parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "reports" / "live_snapshot_pairs.json")
    match_parser.add_argument("--min-similarity", type=float, default=0.68)
    match_parser.add_argument("--max-snapshot-age-hours", type=float, default=24.0)
    match_parser.add_argument(
        "--reference-snapshot",
        type=Path,
        action="append",
        default=[],
        help="Optional reference_snapshot_v1 file for observability diagnostics only.",
    )

    enrich_parser = subparsers.add_parser(
        "enrich-orderbooks",
        help="Read-only orderbook/depth enrichment for a saved schema-v1 snapshot.",
    )
    enrich_parser.add_argument("--snapshot", type=Path, required=True)
    enrich_parser.add_argument("--venue", choices=["kalshi", "polymarket"], required=True)
    enrich_parser.add_argument("--output", type=Path, required=True)
    enrich_parser.add_argument("--timeout-seconds", type=float, default=10.0)
    enrich_parser.add_argument(
        "--max-snapshot-age-hours",
        type=float,
        default=24.0,
        help=(
            "Maximum accepted age for the saved source snapshot before read-only book fetch is skipped. "
            "Increase explicitly when using an old saved snapshot only as a ticker/token list."
        ),
    )
    enrich_parser.add_argument("--preserve-raw-orderbook", action="store_true", help="Preserve raw orderbook payloads in the saved enriched output.")

    enrich_kalshi_parser = subparsers.add_parser(
        "enrich-kalshi-orderbooks",
        help="Explicit read-only Kalshi orderbook/depth enrichment for a saved schema-v1 snapshot.",
    )
    enrich_kalshi_parser.add_argument("--snapshot", type=Path, required=True)
    enrich_kalshi_parser.add_argument("--output", type=Path, required=True)
    enrich_kalshi_parser.add_argument("--timeout-seconds", type=float, default=10.0)
    enrich_kalshi_parser.add_argument(
        "--max-snapshot-age-hours",
        type=float,
        default=24.0,
        help=(
            "Maximum accepted age for the saved Kalshi snapshot before public read-only orderbook fetch is skipped. "
            "Increase explicitly when using an old saved snapshot only as a ticker list."
        ),
    )
    enrich_kalshi_parser.add_argument("--preserve-raw-orderbook", action="store_true", help="Preserve raw Kalshi orderbook payloads in the saved enriched output.")
    enrich_kalshi_parser.add_argument(
        "--max-markets",
        type=int,
        default=None,
        help="If set, stop fetching after this many markets (the rest are tagged max_markets_reached). Bounded by snapshot size.",
    )
    enrich_kalshi_parser.add_argument(
        "--progress-every",
        type=int,
        default=0,
        help="If > 0, log enrichment progress every N markets to stderr.",
    )
    enrich_kalshi_parser.add_argument(
        "--retry-failed-once",
        action="store_true",
        help="Retry transient failures (timeout / network / HTTP 5xx / HTTP 429) once.",
    )
    enrich_kalshi_parser.add_argument(
        "--failure-sample-limit",
        type=int,
        default=10,
        help="Maximum sample failed markets to record in the enriched output.",
    )

    evaluate_parser = subparsers.add_parser(
        "evaluate-paper-candidates",
        help="Evaluate saved matched/enriched snapshots into a read-only paper candidate ledger.",
    )
    evaluate_parser.add_argument("--pairs", type=Path, required=True)
    evaluate_parser.add_argument("--polymarket-enriched", type=Path, required=True)
    evaluate_parser.add_argument("--kalshi-enriched", type=Path, required=True)
    evaluate_parser.add_argument("--output", type=Path, required=True)
    evaluate_parser.add_argument("--max-quote-age-seconds", type=float, default=1800.0)
    evaluate_parser.add_argument("--max-settlement-delta-seconds", type=float, default=3600.0)
    evaluate_parser.add_argument("--min-top-of-book-size", type=float, default=1.0)
    evaluate_parser.add_argument("--min-net-gap", type=float, default=0.01)
    evaluate_parser.add_argument(
        "--accept-unit-mismatch",
        action="store_true",
        help=(
            "Required for otherwise-clean rows to reach PAPER_CANDIDATE because "
            "Polymarket share units and Kalshi contract units are not normalized."
        ),
    )
    evaluate_parser.add_argument(
        "--trust-settlement-normalization",
        action="append",
        default=[],
        help="Default-off trusted same-payoff-board settlement normalization to honor, for example mlb_world_series_timezone_convention_drift.",
    )

    same_payoff_parser = subparsers.add_parser(
        "same-payoff-board",
        help="Build a saved-file deterministic same-payoff diagnostic board for Kalshi/Polymarket pairs.",
    )
    same_payoff_parser.add_argument("--pairs", type=Path, required=True)
    same_payoff_parser.add_argument("--polymarket-enriched", type=Path, required=True)
    same_payoff_parser.add_argument("--kalshi-enriched", type=Path, required=True)
    same_payoff_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "same_payoff_candidate_board.json",
    )
    same_payoff_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "same_payoff_candidate_board.md",
    )

    mlb_ws_board_blockers_parser = subparsers.add_parser(
        "diagnose-mlb-world-series-board-blockers",
        help="Summarize saved MLB World Series same-payoff board blockers.",
    )
    mlb_ws_board_blockers_parser.add_argument("--board", type=Path, required=True)
    mlb_ws_board_blockers_parser.add_argument("--pairs", type=Path, required=True)
    mlb_ws_board_blockers_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "mlb_world_series_board_blockers.json",
    )
    mlb_ws_board_blockers_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "mlb_world_series_board_blockers.md",
    )

    mlb_ws_execution_blockers_parser = subparsers.add_parser(
        "diagnose-mlb-world-series-execution-blockers",
        help="Summarize saved MLB World Series quote/depth/fee execution blockers.",
    )
    mlb_ws_execution_blockers_parser.add_argument("--pairs", type=Path, required=True)
    mlb_ws_execution_blockers_parser.add_argument("--polymarket-enriched", type=Path, required=True)
    mlb_ws_execution_blockers_parser.add_argument("--kalshi-enriched", type=Path, required=True)
    mlb_ws_execution_blockers_parser.add_argument("--evaluator", type=Path)
    mlb_ws_execution_blockers_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "mlb_world_series_execution_blockers.json",
    )
    mlb_ws_execution_blockers_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "mlb_world_series_execution_blockers.md",
    )

    mlb_ws_evaluator_blockers_parser = subparsers.add_parser(
        "diagnose-mlb-world-series-evaluator-blockers",
        help="Summarize saved MLB World Series evaluator WATCH blockers and quote/gap details.",
    )
    mlb_ws_evaluator_blockers_parser.add_argument("--evaluator", type=Path, required=True)
    mlb_ws_evaluator_blockers_parser.add_argument("--pairs", type=Path, required=True)
    mlb_ws_evaluator_blockers_parser.add_argument("--polymarket-enriched", type=Path, required=True)
    mlb_ws_evaluator_blockers_parser.add_argument("--kalshi-enriched", type=Path, required=True)
    mlb_ws_evaluator_blockers_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "mlb_world_series_evaluator_blockers.json",
    )
    mlb_ws_evaluator_blockers_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "mlb_world_series_evaluator_blockers.md",
    )

    attach_same_payoff_parser = subparsers.add_parser(
        "attach-same-payoff-evidence",
        help="Write a derived matcher pairs JSON with typed same-payoff board evidence.",
    )
    attach_same_payoff_parser.add_argument("--pairs", type=Path, required=True)
    attach_same_payoff_parser.add_argument("--board", type=Path, required=True)
    attach_same_payoff_parser.add_argument("--output", type=Path, required=True)

    mlb_audit_parser = subparsers.add_parser(
        "audit-same-scope-mlb-candidates",
        help="Run a saved-file MLB/KXMLB same-scope board/evidence/evaluator audit.",
    )
    mlb_audit_parser.add_argument(
        "--pairs",
        type=Path,
        default=PROJECT_ROOT / "reports" / "mlb_kxmlb_48h_unitok_after_guardrails_pairs.json",
    )
    mlb_audit_parser.add_argument(
        "--polymarket-enriched",
        type=Path,
        default=PROJECT_ROOT / "reports" / "mlb_kxmlb_48h_unitok_after_guardrails_polymarket_enriched.json",
    )
    mlb_audit_parser.add_argument(
        "--kalshi-enriched",
        type=Path,
        default=PROJECT_ROOT / "reports" / "mlb_kxmlb_48h_unitok_after_guardrails_kalshi_enriched.json",
    )
    mlb_audit_parser.add_argument("--json-output", type=Path, default=PROJECT_ROOT / "reports" / "mlb_same_scope_audit.json")
    mlb_audit_parser.add_argument("--markdown-output", type=Path, default=PROJECT_ROOT / "reports" / "mlb_same_scope_audit.md")
    mlb_audit_parser.add_argument(
        "--board-json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "mlb_same_scope_audit_board.json",
    )
    mlb_audit_parser.add_argument(
        "--board-markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "mlb_same_scope_audit_board.md",
    )
    mlb_audit_parser.add_argument(
        "--derived-pairs-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "mlb_same_scope_audit_derived_pairs.json",
    )
    mlb_audit_parser.add_argument(
        "--evaluator-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "mlb_same_scope_audit_evaluator.json",
    )
    mlb_audit_parser.add_argument("--max-quote-age-seconds", type=float, default=1800.0)
    mlb_audit_parser.add_argument("--max-settlement-delta-seconds", type=float, default=3600.0)
    mlb_audit_parser.add_argument("--min-top-of-book-size", type=float, default=1.0)
    mlb_audit_parser.add_argument("--min-net-gap", type=float, default=0.01)
    mlb_audit_parser.add_argument(
        "--accept-unit-mismatch",
        action="store_true",
        help="Forward explicit unit-mismatch acceptance to the saved-file evaluator pass.",
    )

    mlb_targeting_parser = subparsers.add_parser(
        "diagnose-mlb-same-scope-targeting",
        help="Classify saved MLB Kalshi/Polymarket inventory by same-scope competition bucket.",
    )
    mlb_targeting_parser.add_argument(
        "--polymarket-snapshot",
        type=Path,
        default=PROJECT_ROOT / "reports" / "mlb_kxmlb_48h_unitok_after_guardrails_polymarket_snapshot.json",
    )
    mlb_targeting_parser.add_argument(
        "--kalshi-snapshot",
        type=Path,
        default=PROJECT_ROOT / "reports" / "mlb_kxmlb_48h_unitok_after_guardrails_kalshi_snapshot.json",
    )
    mlb_targeting_parser.add_argument("--pairs", type=Path, default=PROJECT_ROOT / "reports" / "mlb_kxmlb_48h_unitok_after_guardrails_pairs.json")
    mlb_targeting_parser.add_argument("--audit", type=Path, default=PROJECT_ROOT / "reports" / "mlb_same_scope_audit.json")
    mlb_targeting_parser.add_argument("--scope", choices=["all", "world_series", "alcs", "nlcs"], default="all")
    mlb_targeting_parser.add_argument("--json-output", type=Path, default=PROJECT_ROOT / "reports" / "mlb_same_scope_targeting.json")
    mlb_targeting_parser.add_argument("--markdown-output", type=Path, default=PROJECT_ROOT / "reports" / "mlb_same_scope_targeting.md")

    mlb_ws_pairs_parser = subparsers.add_parser(
        "build-mlb-world-series-pairs",
        help="Build saved-file-only MLB World Series Kalshi/Polymarket candidate pairs by team entity.",
    )
    mlb_ws_pairs_parser.add_argument(
        "--polymarket-snapshot",
        type=Path,
        required=True,
        help="Saved MLB-targeted Polymarket snapshot. Do not use generic live_readonly snapshots unless they currently contain MLB inventory.",
    )
    mlb_ws_pairs_parser.add_argument(
        "--kalshi-snapshot",
        type=Path,
        required=True,
        help="Saved MLB-targeted Kalshi snapshot. Do not use generic live_readonly snapshots unless they currently contain MLB inventory.",
    )
    mlb_ws_pairs_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "mlb_world_series_pairs.json",
    )
    mlb_ws_pairs_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "mlb_world_series_pairs.md",
    )
    mlb_ws_pairs_parser.add_argument(
        "--match-report",
        type=Path,
        default=PROJECT_ROOT / "reports" / "live_readonly" / "mlb" / "live_readonly_match_report.json",
        help="Optional saved prior MLB-targeted matcher report used only to explain old ranking behavior.",
    )

    nhl_sc_pairs_parser = subparsers.add_parser(
        "build-nhl-stanley-cup-pairs",
        help="Build saved-file-only NHL Stanley Cup Kalshi/Polymarket candidate pairs by team entity.",
    )
    nhl_sc_pairs_parser.add_argument(
        "--polymarket-snapshot",
        type=Path,
        default=PROJECT_ROOT / "reports" / "nhl_kxnhl_polymarket_snapshot.json",
        help="Saved NHL-targeted Polymarket snapshot.",
    )
    nhl_sc_pairs_parser.add_argument(
        "--kalshi-snapshot",
        type=Path,
        default=PROJECT_ROOT / "reports" / "nhl_kxnhl_kalshi_snapshot.json",
        help="Saved Kalshi KXNHL snapshot.",
    )
    nhl_sc_pairs_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "nhl_stanley_cup_pairs.json",
    )
    nhl_sc_pairs_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "nhl_stanley_cup_pairs.md",
    )

    mlb_ws_paper_check_parser = subparsers.add_parser(
        "run-mlb-world-series-paper-check",
        help="Explicit read-only MLB World Series same-payoff paper check using saved snapshots and fresh orderbook enrichment.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mlb_ws_paper_check_parser.add_argument("--polymarket-snapshot", type=Path, required=True, help="Saved Polymarket MLB World Series schema-v1 snapshot.")
    mlb_ws_paper_check_parser.add_argument("--kalshi-snapshot", type=Path, required=True, help="Saved Kalshi MLB World Series schema-v1 snapshot.")
    mlb_ws_paper_check_parser.add_argument("--pairs", type=Path, help="Saved WS/WS Kalshi-Polymarket pairs file. Required unless --rebuild-pairs-from-snapshots is set.")
    mlb_ws_paper_check_parser.add_argument(
        "--rebuild-pairs-from-snapshots",
        action="store_true",
        help="Rebuild MLB World Series pairs from the same snapshots enriched in this run.",
    )
    mlb_ws_paper_check_parser.add_argument("--timeout-seconds", type=float, default=10.0, help="Read-only orderbook request timeout.")
    mlb_ws_paper_check_parser.add_argument("--max-snapshot-age-hours", type=float, default=24.0, help="Maximum age for input snapshots before enrichment fails closed.")
    mlb_ws_paper_check_parser.add_argument("--max-quote-age-seconds", type=float, default=1800.0, help="Evaluator and board quote freshness limit.")
    mlb_ws_paper_check_parser.add_argument("--max-settlement-delta-seconds", type=float, default=3600.0, help="Evaluator settlement-time delta limit before trusted normalizations.")
    mlb_ws_paper_check_parser.add_argument("--min-top-of-book-size", type=float, default=1.0, help="Evaluator hit-side depth requirement.")
    mlb_ws_paper_check_parser.add_argument("--min-net-gap", type=float, default=0.01, help="Evaluator fee-adjusted net gap requirement.")
    mlb_ws_paper_check_parser.add_argument("--accept-unit-mismatch", action="store_true", help="Forward explicit unit-mismatch acknowledgement to the evaluator.")
    mlb_ws_paper_check_parser.add_argument("--trust-settlement-normalization", action="append", default=[], help="Trusted board settlement normalization to honor, e.g. mlb_world_series_timezone_convention_drift.")
    mlb_ws_paper_check_parser.add_argument("--polymarket-enriched-output", type=Path, default=PROJECT_ROOT / "reports" / "mlb_fresh_polymarket_enriched.json", help="Output path for freshly enriched Polymarket snapshot.")
    mlb_ws_paper_check_parser.add_argument("--kalshi-enriched-output", type=Path, default=PROJECT_ROOT / "reports" / "mlb_fresh_kalshi_enriched.json", help="Output path for freshly enriched Kalshi snapshot.")
    mlb_ws_paper_check_parser.add_argument("--rebuilt-pairs-json-output", type=Path, default=PROJECT_ROOT / "reports" / "mlb_world_series_pairs_run.json", help="Output path for rebuilt pairs when --rebuild-pairs-from-snapshots is set.")
    mlb_ws_paper_check_parser.add_argument("--rebuilt-pairs-markdown-output", type=Path, default=PROJECT_ROOT / "reports" / "mlb_world_series_pairs_run.md", help="Output path for rebuilt pairs Markdown when --rebuild-pairs-from-snapshots is set.")
    mlb_ws_paper_check_parser.add_argument("--board-json-output", type=Path, default=PROJECT_ROOT / "reports" / "mlb_world_series_same_payoff_board_fresh.json", help="Output path for same-payoff board JSON.")
    mlb_ws_paper_check_parser.add_argument("--board-markdown-output", type=Path, default=PROJECT_ROOT / "reports" / "mlb_world_series_same_payoff_board_fresh.md", help="Output path for same-payoff board Markdown.")
    mlb_ws_paper_check_parser.add_argument("--derived-pairs-output", type=Path, default=PROJECT_ROOT / "reports" / "mlb_world_series_pairs_with_evidence_fresh.json", help="Output path for derived pairs with same-payoff evidence.")
    mlb_ws_paper_check_parser.add_argument("--evaluator-output", type=Path, default=PROJECT_ROOT / "reports" / "mlb_world_series_evaluator_fresh_trust_settlement.json", help="Output path for evaluator ledger.")
    mlb_ws_paper_check_parser.add_argument("--summary-json-output", type=Path, default=PROJECT_ROOT / "reports" / "mlb_world_series_paper_check_summary.json", help="Output path for compact paper-check summary JSON.")
    mlb_ws_paper_check_parser.add_argument("--summary-markdown-output", type=Path, default=PROJECT_ROOT / "reports" / "mlb_world_series_paper_check_summary.md", help="Output path for compact paper-check summary Markdown.")
    mlb_ws_paper_check_parser.add_argument("--settlement-audit-json-output", type=Path, default=PROJECT_ROOT / "reports" / "mlb_world_series_settlement_audit.json", help="Output path for reporting-only MLB settlement/source blocker audit JSON.")
    mlb_ws_paper_check_parser.add_argument("--settlement-audit-markdown-output", type=Path, default=PROJECT_ROOT / "reports" / "mlb_world_series_settlement_audit.md", help="Output path for reporting-only MLB settlement/source blocker audit Markdown.")

    nba_championship_paper_check_parser = subparsers.add_parser(
        "run-nba-championship-paper-check",
        help="Explicit read-only NBA championship same-payoff paper check using saved snapshots and fresh orderbook enrichment.",
    )
    nba_championship_paper_check_parser.add_argument("--polymarket-snapshot", type=Path, required=True, help="Saved Polymarket NBA championship schema-v1 snapshot.")
    nba_championship_paper_check_parser.add_argument("--kalshi-snapshot", type=Path, required=True, help="Saved Kalshi KXNBA schema-v1 snapshot.")
    nba_championship_paper_check_parser.add_argument("--pairs", type=Path, required=True, help="Saved NBA championship Kalshi-Polymarket pairs file.")
    nba_championship_paper_check_parser.add_argument("--timeout-seconds", type=float, default=10.0, help="Read-only orderbook request timeout.")
    nba_championship_paper_check_parser.add_argument("--max-snapshot-age-hours", type=float, default=24.0, help="Maximum age for input snapshots before enrichment fails closed.")
    nba_championship_paper_check_parser.add_argument("--max-quote-age-seconds", type=float, default=1800.0, help="Evaluator and board quote freshness limit.")
    nba_championship_paper_check_parser.add_argument("--max-settlement-delta-seconds", type=float, default=3600.0, help="Evaluator settlement-time delta limit before trusted normalizations.")
    nba_championship_paper_check_parser.add_argument("--min-top-of-book-size", type=float, default=1.0, help="Evaluator hit-side depth requirement.")
    nba_championship_paper_check_parser.add_argument("--min-net-gap", type=float, default=0.01, help="Evaluator fee-adjusted net gap requirement.")
    nba_championship_paper_check_parser.add_argument("--accept-unit-mismatch", action="store_true", help="Forward explicit unit-mismatch acknowledgement to the evaluator.")
    nba_championship_paper_check_parser.add_argument("--trust-settlement-normalization", action="append", default=[], help="Trusted board settlement normalization to honor, e.g. nba_finals_timezone_convention_drift.")
    nba_championship_paper_check_parser.add_argument("--polymarket-enriched-output", type=Path, default=PROJECT_ROOT / "reports" / "nba_kxnba_polymarket_enriched_fresh.json", help="Output path for freshly enriched Polymarket snapshot.")
    nba_championship_paper_check_parser.add_argument("--kalshi-enriched-output", type=Path, default=PROJECT_ROOT / "reports" / "nba_kxnba_kalshi_enriched_fresh.json", help="Output path for freshly enriched Kalshi snapshot.")
    nba_championship_paper_check_parser.add_argument("--board-json-output", type=Path, default=PROJECT_ROOT / "reports" / "nba_kxnba_same_payoff_board_fresh.json", help="Output path for same-payoff board JSON.")
    nba_championship_paper_check_parser.add_argument("--board-markdown-output", type=Path, default=PROJECT_ROOT / "reports" / "nba_kxnba_same_payoff_board_fresh.md", help="Output path for same-payoff board Markdown.")
    nba_championship_paper_check_parser.add_argument("--derived-pairs-output", type=Path, default=PROJECT_ROOT / "reports" / "nba_kxnba_pairs_with_evidence_fresh.json", help="Output path for derived pairs with same-payoff evidence.")
    nba_championship_paper_check_parser.add_argument("--evaluator-output", type=Path, default=PROJECT_ROOT / "reports" / "nba_kxnba_evaluator_fresh.json", help="Output path for evaluator ledger.")
    nba_championship_paper_check_parser.add_argument("--summary-json-output", type=Path, default=PROJECT_ROOT / "reports" / "nba_championship_paper_check_summary.json", help="Output path for compact paper-check summary JSON.")
    nba_championship_paper_check_parser.add_argument("--summary-markdown-output", type=Path, default=PROJECT_ROOT / "reports" / "nba_championship_paper_check_summary.md", help="Output path for compact paper-check summary Markdown.")

    exact_universes_parser = subparsers.add_parser(
        "discover-exact-paper-candidate-universes",
        help="Saved-file diagnostic readiness report for exact same-payoff paper-candidate universes.",
    )
    exact_universes_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "exact_paper_candidate_universes.json",
    )
    exact_universes_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "exact_paper_candidate_universes.md",
    )

    basket_parser = subparsers.add_parser(
        "detect-structural-baskets",
        help="Saved-file-only single-venue exhaustive basket review using explicit venue-native group evidence.",
    )
    basket_parser.add_argument(
        "--snapshot",
        dest="snapshots",
        action="append",
        type=Path,
        required=True,
        help="Saved market/orderbook snapshot path. Repeat for multiple saved snapshots.",
    )
    basket_parser.add_argument(
        "--manifest",
        type=Path,
        help="Optional trusted local exhaustive-group manifest with explicit source/evidence fields.",
    )
    basket_parser.add_argument("--max-quote-age-seconds", type=float, default=1800.0)
    basket_parser.add_argument("--min-depth", type=float, default=1.0)
    basket_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "structural_basket_review.json",
    )
    basket_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "structural_basket_review.md",
    )

    scout_manifest_parser = subparsers.add_parser(
        "scout-structural-manifest-candidates",
        help="Saved-file-only diagnostic scout for groups worth human local_manifest_v1 review.",
    )
    scout_manifest_parser.add_argument("--snapshot", required=True, type=Path, help="Saved Kalshi snapshot/event/market JSON.")
    scout_manifest_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "structural_manifest_candidates.json",
    )
    scout_manifest_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "structural_manifest_candidates.md",
    )
    scout_manifest_parser.add_argument("--max-quote-age-seconds", type=float, default=1800.0)
    scout_manifest_parser.add_argument("--min-depth", type=float, default=1.0)

    structural_parlay_parser = subparsers.add_parser(
        "structural-basket-parlay-scout",
        help=(
            "Saved-file-only structural basket/parlay diagnostic scout across Kalshi, Polymarket, and CDNA. "
            "Prices review rows only; never creates standard candidate pairs or paper actions."
        ),
    )
    structural_parlay_parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "reports" / "manual_evidence",
        help="Directory of saved normalized/raw evidence JSON files.",
    )
    structural_parlay_parser.add_argument(
        "--graph-hints-json",
        type=Path,
        default=None,
        help="Optional saved graph/parlay relationship hints JSON. Advisory only.",
    )
    structural_parlay_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "structural_basket_parlay_scout.json",
    )
    structural_parlay_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "structural_basket_parlay_scout.md",
    )

    audit_event_metadata_parser = subparsers.add_parser(
        "audit-kalshi-event-metadata",
        help="Saved-file-only normalize/audit of Kalshi event-metadata JSON files (no live calls, no orders).",
    )
    audit_event_metadata_parser.add_argument(
        "--metadata",
        dest="metadata_paths",
        action="append",
        type=Path,
        required=True,
        help="Saved Kalshi event metadata JSON path. Repeat to pass multiple files.",
    )
    audit_event_metadata_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "kalshi_event_metadata_audit.json",
    )
    audit_event_metadata_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "kalshi_event_metadata_audit.md",
    )

    join_event_metadata_parser = subparsers.add_parser(
        "join-kalshi-event-metadata",
        help="Saved-file-only join of normalized Kalshi event metadata into a saved market/orderbook snapshot.",
    )
    join_event_metadata_parser.add_argument(
        "--snapshot",
        required=True,
        type=Path,
        help="Saved Kalshi market/orderbook snapshot JSON.",
    )
    join_event_metadata_parser.add_argument(
        "--metadata",
        dest="metadata_paths",
        action="append",
        type=Path,
        required=True,
        help="Saved Kalshi event metadata JSON path. Repeat to pass multiple files.",
    )
    join_event_metadata_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "kalshi_event_metadata_join_report.json",
    )
    join_event_metadata_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "kalshi_event_metadata_join_report.md",
    )
    join_event_metadata_parser.add_argument(
        "--enriched-snapshot-output",
        type=Path,
        default=None,
        help="Optional path to write the enriched snapshot JSON for downstream saved-file diagnostics.",
    )

    paper_fill_parser = subparsers.add_parser(
        "simulate-paper-fills",
        help="Saved-file-only paper fill journal for rows already gated by upstream review logic.",
    )
    paper_fill_parser.add_argument("--input", required=True, type=Path, help="Saved upstream review report JSON.")
    paper_fill_parser.add_argument(
        "--json-output",
        "--output",
        dest="json_output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "paper_fill_journal.json",
    )
    paper_fill_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "paper_fill_journal.md",
    )
    paper_fill_parser.add_argument("--desired-quantity", type=float, default=1.0)
    paper_fill_parser.add_argument("--max-quote-age-seconds", type=float, default=1800.0)
    paper_fill_parser.add_argument("--slippage-budget-cents-per-leg", type=float, default=0.0)

    dry_run_parser = subparsers.add_parser(
        "run-structural-basket-dry-run",
        help=(
            "Saved-file-only structural basket dry run: audit metadata, join into snapshot, "
            "build structural basket review, and simulate paper fills only when STOP_FOR_REVIEW "
            "is surfaced. No live API, no orders."
        ),
    )
    dry_run_parser.add_argument(
        "--snapshot",
        required=True,
        type=Path,
        help="Saved Kalshi market/orderbook snapshot JSON.",
    )
    dry_run_parser.add_argument(
        "--metadata",
        dest="metadata_paths",
        action="append",
        type=Path,
        required=True,
        help="Saved Kalshi event metadata JSON path. Repeat to pass multiple files.",
    )
    dry_run_parser.add_argument(
        "--summary-json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "structural_basket_dry_run_summary.json",
    )
    dry_run_parser.add_argument(
        "--summary-markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "structural_basket_dry_run_summary.md",
    )
    dry_run_parser.add_argument(
        "--audit-json-output",
        type=Path,
        default=None,
        help="Optional path to write the kalshi_event_metadata_audit sub-report JSON.",
    )
    dry_run_parser.add_argument("--audit-markdown-output", type=Path, default=None)
    dry_run_parser.add_argument(
        "--join-json-output",
        type=Path,
        default=None,
        help="Optional path to write the kalshi_event_metadata_join sub-report JSON.",
    )
    dry_run_parser.add_argument("--join-markdown-output", type=Path, default=None)
    dry_run_parser.add_argument(
        "--enriched-snapshot-output",
        type=Path,
        default=None,
        help="Optional path to write the enriched snapshot consumed by detect-structural-baskets.",
    )
    dry_run_parser.add_argument(
        "--structural-json-output",
        type=Path,
        default=None,
        help="Optional path to write the structural basket detector sub-report JSON.",
    )
    dry_run_parser.add_argument("--structural-markdown-output", type=Path, default=None)
    dry_run_parser.add_argument(
        "--paper-fill-json-output",
        type=Path,
        default=None,
        help=(
            "Optional path to write the paper-fill journal JSON. Only written when "
            "STOP_FOR_REVIEW rows triggered paper simulation."
        ),
    )
    dry_run_parser.add_argument("--paper-fill-markdown-output", type=Path, default=None)
    dry_run_parser.add_argument("--max-quote-age-seconds", type=float, default=1800.0)
    dry_run_parser.add_argument("--min-depth", type=float, default=1.0)
    dry_run_parser.add_argument("--desired-quantity", type=float, default=1.0)
    dry_run_parser.add_argument("--slippage-budget-cents-per-leg", type=float, default=0.0)
    dry_run_parser.add_argument(
        "--skip-paper-fill-simulation",
        action="store_true",
        help=(
            "Skip paper-fill simulation even if STOP_FOR_REVIEW rows are surfaced. "
            "The detector still runs; the simulator is just not invoked."
        ),
    )

    import_metadata_parser = subparsers.add_parser(
        "import-kalshi-event-metadata",
        help=(
            "Saved-file-only acquisition: validate Kalshi event metadata JSON files "
            "and optionally copy them into a destination directory. No live API calls."
        ),
    )
    import_metadata_parser.add_argument(
        "--source",
        dest="sources",
        action="append",
        type=Path,
        required=True,
        help="Saved Kalshi event metadata JSON file to validate. Repeat for multiple files.",
    )
    import_metadata_parser.add_argument(
        "--destination-dir",
        type=Path,
        default=None,
        help="Optional directory to copy validated files into.",
    )
    import_metadata_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing files in the destination directory.",
    )
    import_metadata_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "kalshi_event_metadata_import.json",
    )
    import_metadata_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "kalshi_event_metadata_import.md",
    )

    hunt_basket_parser = subparsers.add_parser(
        "hunt-structural-basket-candidates",
        help=(
            "Saved-file-only structural basket hunter: sweep saved snapshots, Kalshi event "
            "metadata, and local_manifest_v1 files; report exactly which groups are closest "
            "to credible paper review. Never makes live API calls, never places orders, "
            "never emits PAPER_CANDIDATE. Templates written are INVALID by default."
        ),
    )
    hunt_basket_parser.add_argument(
        "--snapshots-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help="Directory to recursively scan for Kalshi market/orderbook snapshots.",
    )
    hunt_basket_parser.add_argument(
        "--metadata-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help="Directory to recursively scan for Kalshi event metadata JSON files.",
    )
    hunt_basket_parser.add_argument(
        "--manifest-dir",
        type=Path,
        default=None,
        help="Optional directory to recursively scan for local_manifest_v1 manifests.",
    )
    hunt_basket_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "structural_basket_hunt.json",
    )
    hunt_basket_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "structural_basket_hunt.md",
    )
    hunt_basket_parser.add_argument(
        "--manifest-template-dir",
        type=Path,
        default=PROJECT_ROOT / "reports" / "manifest_templates",
        help=(
            "Directory where the hunter writes manifest templates for candidate groups "
            "missing metadata. Templates are intentionally invalid by default."
        ),
    )
    hunt_basket_parser.add_argument(
        "--skip-template-writes",
        action="store_true",
        help="Compute template suggestions but do not write any template files.",
    )
    hunt_basket_parser.add_argument(
        "--max-quote-age-seconds",
        type=float,
        default=1800.0,
    )
    hunt_basket_parser.add_argument("--min-depth", type=float, default=1.0)
    hunt_basket_parser.add_argument("--desired-quantity", type=float, default=1.0)
    hunt_basket_parser.add_argument("--slippage-budget-cents-per-leg", type=float, default=0.0)
    hunt_basket_parser.add_argument(
        "--skip-paper-fill-simulation",
        action="store_true",
        help=(
            "Skip paper-fill simulation even if STOP_FOR_REVIEW rows are surfaced. "
            "The detector still runs; the simulator is just not invoked."
        ),
    )
    hunt_basket_parser.add_argument(
        "--top-closest-n",
        type=int,
        default=10,
        help="Maximum number of closest_groups_to_review rows to include in the report.",
    )

    kalshi_native_parser = subparsers.add_parser(
        "audit-kalshi-native-groups",
        help="Saved-file-only audit for explicit Kalshi venue-native event/group completeness metadata.",
    )
    kalshi_native_parser.add_argument("--snapshot", required=True, type=Path, help="Saved Kalshi snapshot/event/market JSON.")
    kalshi_native_parser.add_argument(
        "--output",
        "--json-output",
        dest="json_output",
        type=Path,
        default=None,
    )
    kalshi_native_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=None,
    )

    kxmlb_evidence_parser = subparsers.add_parser(
        "audit-kalshi-kxmlb26-event-evidence",
        help=(
            "Saved-file-only evidence summary for KXMLB-26 manifest readiness. "
            "No live API calls, no manifest writes, no approvals, no orders."
        ),
    )
    kxmlb_evidence_parser.add_argument("--input-dir", type=Path, default=PROJECT_ROOT / "reports")
    kxmlb_evidence_parser.add_argument("--event-ticker", default="KXMLB-26")
    kxmlb_evidence_parser.add_argument("--max-quote-age-seconds", type=float, default=1800.0)
    kxmlb_evidence_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "kalshi_kxmlb26_event_evidence_summary.json",
    )
    kxmlb_evidence_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "kalshi_kxmlb26_event_evidence_summary.md",
    )

    graph_parser = subparsers.add_parser(
        "market-graph-diagnostics",
        help="Build fixture-backed market graph relationship diagnostics for review only.",
    )
    graph_parser.add_argument(
        "--fixture",
        type=Path,
        help="Optional local JSON fixture list or object with a markets list.",
    )
    graph_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "market_graph_consistency_diagnostics.json",
    )
    graph_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "market_graph_consistency_diagnostics.md",
    )

    graph_hints_parser = subparsers.add_parser(
        "explain-market-graph-diagnostics",
        help="Read saved market graph diagnostics as info-only relative-value hints.",
    )
    graph_hints_parser.add_argument(
        "--graph-report",
        type=Path,
        default=PROJECT_ROOT / "reports" / "market_graph_consistency_diagnostics.json",
    )
    graph_hints_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "market_graph_relative_value_hints.json",
    )
    graph_hints_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "market_graph_relative_value_hints.md",
    )

    cross_platform_triage_parser = subparsers.add_parser(
        "triage-cross-platform-opportunities",
        help="Saved-file-only cross-platform opportunity triage for review; never emits PAPER_CANDIDATE.",
    )
    cross_platform_triage_parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help="Saved reports/snapshots directory to scan for diagnostic opportunity rows.",
    )
    cross_platform_triage_parser.add_argument(
        "--graph-hints-path",
        type=Path,
        default=None,
        help="Optional saved market-graph relative-value hints JSON; advisory only.",
    )
    cross_platform_triage_parser.add_argument(
        "--json-output",
        "--out",
        dest="json_output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "cross_platform_opportunity_triage.json",
    )
    cross_platform_triage_parser.add_argument(
        "--csv-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "cross_platform_opportunity_triage.csv",
    )
    cross_platform_triage_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "cross_platform_opportunity_triage.md",
    )

    metadata_coverage_parser = subparsers.add_parser(
        "audit-venue-metadata-coverage",
        help="Saved-file-only venue metadata/inventory coverage audit for cross-platform matching readiness.",
    )
    metadata_coverage_parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help="Saved snapshots/reports directory to scan for market metadata rows.",
    )
    metadata_coverage_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "venue_metadata_coverage.json",
    )
    metadata_coverage_parser.add_argument(
        "--csv-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "venue_metadata_coverage.csv",
    )
    metadata_coverage_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "venue_metadata_coverage.md",
    )

    normalize_markets_parser = subparsers.add_parser(
        "normalize-market-snapshots",
        help="Saved-file-only NormalizedMarket contract v0 report for adapter shape/coverage review.",
    )
    normalize_markets_parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help="Saved snapshots/reports directory to scan for market rows.",
    )
    normalize_markets_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "normalized_markets_v0.json",
    )
    normalize_markets_parser.add_argument(
        "--coverage-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "normalized_markets_v0_coverage.json",
    )
    normalize_markets_parser.add_argument("--csv-output", type=Path, default=None)
    normalize_markets_parser.add_argument("--markdown-output", type=Path, default=None)

    canonical_registry_parser = subparsers.add_parser(
        "audit-canonical-convention-registry",
        help="Saved-file-only audit for manually reviewed canonical convention registry entries.",
    )
    canonical_registry_parser.add_argument(
        "--registry",
        type=Path,
        required=True,
        help="Manual canonical convention registry JSON to validate.",
    )
    canonical_registry_parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Optional path for the registry audit JSON report.",
    )

    canonical_registry_coverage_parser = subparsers.add_parser(
        "audit-canonical-registry-coverage",
        help="Saved-file-only canonical registry coverage and reviewer-flow report.",
    )
    canonical_registry_coverage_parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help="Saved reports directory containing settlement burden and family graduation reports.",
    )
    canonical_registry_coverage_parser.add_argument(
        "--registry",
        type=Path,
        default=_default_canonical_registry_path(),
        help=(
            "Manual canonical convention registry JSON to compare against proposal scopes. "
            "Defaults to docs/example_canonical_convention_registry_v0.json when that file exists."
        ),
    )
    canonical_registry_coverage_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "canonical_registry_coverage.json",
    )
    canonical_registry_coverage_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "canonical_registry_coverage.md",
    )

    canonical_registry_expiry_parser = subparsers.add_parser(
        "audit-canonical-registry-expiry",
        help="Saved-file-only audit of canonical registry review_until expiry status.",
    )
    canonical_registry_expiry_parser.add_argument(
        "--registry",
        type=Path,
        default=PROJECT_ROOT / "docs" / "example_canonical_convention_registry_v0.json",
        help="Manual canonical convention registry JSON to audit for review_until expiry.",
    )
    canonical_registry_expiry_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "canonical_registry_expiry_audit.json",
    )
    canonical_registry_expiry_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "canonical_registry_expiry_audit.md",
    )
    canonical_registry_expiry_parser.add_argument(
        "--expiring-soon-days",
        type=int,
        default=7,
        help="Number of days before review_until to flag review_expiring_soon.",
    )

    pending_registry_entries_parser = subparsers.add_parser(
        "plan-pending-registry-entries",
        help="Write pending registry-entry skeleton files from canonical registry coverage; proposals are not trust.",
    )
    pending_registry_entries_parser.add_argument(
        "--coverage",
        type=Path,
        default=PROJECT_ROOT / "reports" / "canonical_registry_coverage.json",
        help="Saved canonical_registry_coverage JSON report.",
    )
    pending_registry_entries_parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "docs" / "pending_registry_entries",
        help="Directory for pending registry skeleton JSON files.",
    )
    pending_registry_entries_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "pending_registry_entries_plan.json",
    )

    audit_pending_promotion_parser = subparsers.add_parser(
        "audit-pending-registry-entries-promotion",
        help=(
            "Saved-file-only audit of docs/pending_registry_entries/*.json: reports which "
            "skeletons have been filled in and are structurally ready for manual merge into "
            "the canonical registry. NEVER mutates the canonical registry."
        ),
    )
    audit_pending_promotion_parser.add_argument(
        "--pending-dir",
        type=Path,
        default=PROJECT_ROOT / "docs" / "pending_registry_entries",
        help="Directory of pending registry skeleton JSON files.",
    )
    audit_pending_promotion_parser.add_argument(
        "--registry",
        type=Path,
        default=_default_canonical_registry_path(),
        help=(
            "Optional canonical registry JSON for entry_id collision checks. Defaults to "
            "docs/example_canonical_convention_registry_v0.json when that file exists."
        ),
    )
    audit_pending_promotion_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "pending_registry_entries_promotion_audit.json",
    )

    settlement_burden_parser = subparsers.add_parser(
        "audit-settlement-evidence-burden",
        help="Saved-file-only family-aware settlement-evidence burden audit; diagnostic only, never emits PAPER_CANDIDATE.",
    )
    settlement_burden_parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help="Saved snapshots/reports directory to scan for market rows (normalized markets preferred).",
    )
    settlement_burden_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "settlement_evidence_burden.json",
    )
    settlement_burden_parser.add_argument(
        "--csv-output",
        type=Path,
        default=None,
        help="Optional CSV output for the per-market evidence-burden classification.",
    )
    settlement_burden_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=None,
        help="Optional markdown summary output.",
    )
    settlement_burden_parser.add_argument(
        "--registry-path",
        type=Path,
        default=_default_canonical_registry_path(),
        help=(
            "Optional path to a manually reviewed canonical convention registry JSON. "
            "Defaults to docs/example_canonical_convention_registry_v0.json when that file "
            "exists so reviewed-scope rows are promoted to SETTLEMENT_SOURCE_REVIEW_READY. "
            "Pass an explicit path to override."
        ),
    )
    settlement_burden_parser.add_argument(
        "--staleness-seconds",
        type=int,
        default=DEFAULT_STALENESS_SECONDS,
        help="Maximum quote capture age in seconds before the burden audit adds stale_quote.",
    )

    standardized_candidates_parser = subparsers.add_parser(
        "generate-standardized-family-candidates",
        help="Saved-file-only exact typed-key candidate groups for standardized families; diagnostic only.",
    )
    standardized_candidates_parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help="Saved reports directory used for source-row evidence lookup.",
    )
    standardized_candidates_parser.add_argument(
        "--burden-report",
        type=Path,
        default=PROJECT_ROOT / "reports" / "settlement_evidence_burden.json",
        help="Saved audit-settlement-evidence-burden JSON output.",
    )
    standardized_candidates_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "standardized_family_candidates.json",
    )
    standardized_candidates_parser.add_argument(
        "--csv-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "standardized_family_candidates.csv",
    )
    standardized_candidates_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "standardized_family_candidates.md",
    )

    family_graduation_parser = subparsers.add_parser(
        "plan-family-graduation",
        help="Saved-file-only family typed-key graduation plan; proposes registry review without creating candidates.",
    )
    family_graduation_parser.add_argument(
        "--family",
        choices=("CRYPTO_PRICE_THRESHOLD", "FED_FOMC"),
        default=None,
        help="Family to plan. If omitted, selects the supported family with the most FAMILY_TYPED_REVIEW_READY rows.",
    )
    family_graduation_parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help="Saved reports directory containing settlement_evidence_burden and normalized_markets_v0.",
    )
    family_graduation_parser.add_argument(
        "--registry-path",
        type=Path,
        default=_default_canonical_registry_path(),
        help=(
            "Optional manually reviewed canonical convention registry JSON for projection only. "
            "Defaults to docs/example_canonical_convention_registry_v0.json when that file exists."
        ),
    )
    family_graduation_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "family_graduation.json",
    )
    family_graduation_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "family_graduation.md",
    )

    ops_status_parser = subparsers.add_parser(
        "relative-value-ops-status",
        help="Saved-file-only operator status summary for the relative-value diagnostics lane.",
    )
    ops_status_parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help="Saved reports directory to summarize.",
    )
    ops_status_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "relative_value_ops_status.json",
    )
    ops_status_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "relative_value_ops_status.md",
    )

    poly_shape_parser = subparsers.add_parser(
        "polymarket-taxonomy-shape-scout",
        help="Saved-file-only Polymarket taxonomy + market-shape scout that ranks exact-matchability likelihood. Diagnostic only; never creates candidate pairs or paper actions.",
    )
    poly_shape_parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help="Directory containing polymarket_market_taxonomy.json and polymarket_orderbook_enriched_snapshot.json.",
    )
    poly_shape_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "polymarket_taxonomy_shape_scout.json",
    )
    poly_shape_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "polymarket_taxonomy_shape_scout.md",
    )

    poly_clob_refresh_parser = subparsers.add_parser(
        "refresh-polymarket-clob-for-taxonomy-candidates",
        help=(
            "Public no-auth Polymarket CLOB refresh-and-attach for top taxonomy-shape "
            "candidates. Diagnostic only; never creates paper candidates or exact-payoff equivalences."
        ),
    )
    poly_clob_refresh_parser.add_argument(
        "--taxonomy-json",
        type=Path,
        default=PROJECT_ROOT / "reports" / "polymarket_taxonomy_shape_scout.json",
        help="Saved polymarket-taxonomy-shape-scout JSON to read candidate rows from.",
    )
    poly_clob_refresh_parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "reports" / "manual_snapshots" / "polymarket_clob_taxonomy",
        help="Directory under which a timestamped folder of raw CLOB book snapshots is written.",
    )
    poly_clob_refresh_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "polymarket_clob_taxonomy_refresh.json",
    )
    poly_clob_refresh_parser.add_argument(
        "--enriched-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "polymarket_taxonomy_shape_scout_enriched.json",
        help=(
            "Copy of the scout JSON with refreshed CLOB quote fields and recomputed blockers "
            "merged in. Stand-alone enriched view; never emits paper candidates."
        ),
    )
    poly_clob_refresh_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "polymarket_clob_taxonomy_refresh.md",
    )
    poly_clob_refresh_parser.add_argument(
        "--max-candidates",
        type=int,
        default=200,
        help="Maximum number of candidate rows to refresh (after shape + min-score filtering).",
    )
    poly_clob_refresh_parser.add_argument(
        "--shape",
        dest="shape_filter",
        type=str,
        default="point_in_time_threshold",
        choices=[*POLYMARKET_CLOB_TAXONOMY_REFRESH_DEFAULT_SHAPE_PRIORITY, "all"],
        help="Restrict to a single market_shape value; pass 'all' to drop the shape filter.",
    )
    poly_clob_refresh_parser.add_argument(
        "--min-score",
        type=float,
        default=30.0,
        help="Minimum exact_matchability_score (review_priority_score) for a row to be eligible.",
    )
    poly_clob_refresh_parser.add_argument(
        "--include-deadline-range",
        action="store_true",
        help=(
            "Include deadline_threshold_touch / range_hit / range_bucket / crypto_deadline_range_hit "
            "shapes (excluded by default; these can never be exact point-in-time)."
        ),
    )
    poly_clob_refresh_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=10.0,
        help="HTTP timeout for each public CLOB book request.",
    )

    poly_point_audit_parser = subparsers.add_parser(
        "polymarket-point-in-time-typed-key-audit",
        help=(
            "Saved-file-only audit of Polymarket point-in-time taxonomy rows. "
            "Ranks typed-key completeness and targeted CLOB refresh candidates; diagnostic only."
        ),
    )
    poly_point_audit_parser.add_argument(
        "--taxonomy-json",
        type=Path,
        default=PROJECT_ROOT / "reports" / "polymarket_taxonomy_shape_scout.json",
        help="Saved polymarket-taxonomy-shape-scout JSON.",
    )
    poly_point_audit_parser.add_argument(
        "--enriched-json",
        type=Path,
        default=PROJECT_ROOT / "reports" / "polymarket_taxonomy_shape_scout_enriched.json",
        help="Saved enriched taxonomy JSON with any attached CLOB evidence.",
    )
    poly_point_audit_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "polymarket_point_in_time_typed_key_audit.json",
    )
    poly_point_audit_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "polymarket_point_in_time_typed_key_audit.md",
    )

    kalshi_crypto_audit_parser = subparsers.add_parser(
        "kalshi-crypto-typed-key-audit",
        help=(
            "Saved-file-only audit of Kalshi crypto price-threshold rows. Extracts explicit "
            "typed keys, classifies shape, tags blockers, and emits diagnostic CDNA / "
            "Polymarket peer hints. Never creates candidate pairs or paper actions."
        ),
    )
    kalshi_crypto_audit_parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help=(
            "Directory containing normalized_markets_v0.json plus optional CDNA snapshot and "
            "Polymarket point-in-time typed-key audit reports."
        ),
    )
    kalshi_crypto_audit_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "kalshi_crypto_typed_key_audit.json",
    )
    kalshi_crypto_audit_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "kalshi_crypto_typed_key_audit.md",
    )

    crypto_peer_plan_parser = subparsers.add_parser(
        "crypto-peer-acquisition-plan",
        help=(
            "Saved-file-only planner that turns the typed-complete Kalshi crypto grid into "
            "precise Polymarket / CDNA / Kalshi orderbook acquisition targets. Diagnostic only; "
            "never creates candidate pairs, performs live fetches, or emits paper actions."
        ),
    )
    crypto_peer_plan_parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help=(
            "Directory containing kalshi_crypto_typed_key_audit.json, "
            "polymarket_point_in_time_typed_key_audit.json, polymarket_taxonomy_shape_scout_enriched.json, "
            "cdna_crypto_basis_risk_scout.json, and core_trio_peer_coverage_audit.json."
        ),
    )
    crypto_peer_plan_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "crypto_peer_acquisition_plan.json",
    )
    crypto_peer_plan_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "crypto_peer_acquisition_plan.md",
    )

    crypto_payoff_calendar_parser = subparsers.add_parser(
        "crypto-payoff-calendar-audit",
        help=(
            "Saved-file-only crypto payoff-calendar ontology audit. Classifies every saved Kalshi / "
            "Polymarket / CDNA crypto row into a payoff-calendar shape (daily 5pm / hourly / weekly "
            "Friday / intraday touch / deadline touch / up-down / all-time-high / range bucket / "
            "point-in-time) and applies a conservative cross-venue compatibility matrix. Diagnostic only."
        ),
    )
    crypto_payoff_calendar_parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help=(
            "Directory containing kalshi_crypto_typed_key_audit.json, "
            "polymarket_taxonomy_shape_scout_enriched.json, polymarket_point_in_time_typed_key_audit.json, "
            "crypto_com_predict_cdna_research_snapshot.json, and cdna_crypto_basis_risk_scout.json."
        ),
    )
    crypto_payoff_calendar_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "crypto_payoff_calendar_audit.json",
    )
    crypto_payoff_calendar_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "crypto_payoff_calendar_audit.md",
    )

    crypto_manual_workbench_parser = subparsers.add_parser(
        "crypto-manual-discovery-workbench",
        help=(
            "Saved-file-only manual discovery workbench. Reads the crypto-payoff-calendar-audit JSON "
            "and emits a per-venue checklist of the highest-priority manual evidence Mason must "
            "collect (rules text, settlement source, observation time, comparator, identifiers). "
            "Emits diagnostic manual_manifest_candidate templates with approved=false; never reaches "
            "the evaluator or trusted-manifest tier."
        ),
    )
    crypto_manual_workbench_parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help="Directory containing crypto_payoff_calendar_audit.json.",
    )
    crypto_manual_workbench_parser.add_argument(
        "--audit-json",
        type=Path,
        default=None,
        help="Override path to a saved crypto_payoff_calendar_audit.json. Default: <input-dir>/crypto_payoff_calendar_audit.json.",
    )
    crypto_manual_workbench_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "crypto_manual_discovery_workbench.json",
    )
    crypto_manual_workbench_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "crypto_manual_discovery_workbench.md",
    )
    crypto_manual_workbench_parser.add_argument(
        "--max-targets-per-group",
        type=int,
        default=20,
        help="Maximum number of manual discovery targets to emit per payoff-calendar group.",
    )

    crypto_threshold_basis_parser = subparsers.add_parser(
        "crypto-threshold-basis-review-scout",
        help=(
            "Saved-file-only Kalshi/Polymarket crypto point-in-time threshold basis-risk scout. "
            "Never creates exact relationships, candidate pairs, or standard paper actions."
        ),
    )
    crypto_threshold_basis_parser.add_argument("--kalshi-evidence", type=Path, required=True)
    crypto_threshold_basis_parser.add_argument("--polymarket-evidence", type=Path, required=True)
    crypto_threshold_basis_parser.add_argument(
        "--cdna-evidence",
        type=Path,
        default=None,
        help="Optional saved CDNA display-price evidence. Included as fill-first/reference rows only.",
    )
    crypto_threshold_basis_parser.add_argument("--asset", required=True, help="Crypto asset symbol, e.g. BTC or ETH.")
    crypto_threshold_basis_parser.add_argument(
        "--operator-risk-mode",
        choices=("conservative", "standard", "aggressive"),
        default="conservative",
        help="Paper-candidate acceptance mode. Aggressive accepts crypto basis-risk assumptions when hard quote/depth/fee gates pass.",
    )
    crypto_threshold_basis_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "crypto_threshold_basis_review_scout.json",
    )
    crypto_threshold_basis_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "crypto_threshold_basis_review_scout.md",
    )

    daily_crypto_three_venue_parser = subparsers.add_parser(
        "run-daily-crypto-three-venue-check",
        help=(
            "Saved-evidence-only daily Kalshi/Polymarket/CDNA crypto point-in-time "
            "threshold check across multiple assets. Auto-discovers per-asset "
            "evidence under reports/manual_evidence/automation_batch_002 (preferred) "
            "and automation_batch_001_polished. Settlement-time discipline is "
            "preserved: target_time/timezone/threshold/date mismatches remain hard "
            "blockers in every operator-risk-mode."
        ),
    )
    daily_crypto_three_venue_parser.add_argument(
        "--assets",
        default="BTC,ETH,SOL,XRP,DOGE",
        help="Comma-separated list of asset symbols, e.g. BTC,ETH,SOL,XRP,DOGE.",
    )
    daily_crypto_three_venue_parser.add_argument(
        "--date",
        default=None,
        help="Optional YYYY-MM-DD filter; rows for other target dates are dropped.",
    )
    daily_crypto_three_venue_parser.add_argument(
        "--operator-risk-mode",
        choices=("conservative", "standard", "aggressive"),
        default="conservative",
    )
    daily_crypto_three_venue_parser.add_argument("--include-cdna", action="store_true")
    daily_crypto_three_venue_parser.add_argument(
        "--operator-accept-cdna-display-price-risk",
        action="store_true",
    )
    daily_crypto_three_venue_parser.add_argument("--cdna-operator-size-cap", type=float, default=1.0)
    daily_crypto_three_venue_parser.add_argument("--max-quote-age-seconds", type=float, default=300.0)
    daily_crypto_three_venue_parser.add_argument("--min-available-notional", type=float, default=1.0)
    daily_crypto_three_venue_parser.add_argument(
        "--allow-top-of-book-depth",
        action="store_true",
        help=(
            "When --operator-size-cap is also supplied, accept top-of-book / "
            "limited depth as an explicit operator assumption. Adds "
            "limited_depth_operator_size_cap_applied to assumptions_accepted. "
            "Still requires fresh quote, exact target time, and positive net edge."
        ),
    )
    daily_crypto_three_venue_parser.add_argument(
        "--operator-size-cap",
        type=float,
        default=0.0,
        help="Dollar cap applied when --allow-top-of-book-depth is set. Default 0 (off).",
    )
    daily_crypto_three_venue_parser.add_argument(
        "--refresh-kalshi-polymarket",
        action="store_true",
        help=(
            "Fetch fresh Kalshi + Polymarket crypto evidence via public read-only APIs "
            "before scouting. No auth, no order, no browser automation."
        ),
    )
    daily_crypto_three_venue_parser.add_argument(
        "--cdna-evidence-dir",
        type=Path,
        default=None,
        help=(
            "Optional folder of saved CDNA evidence files. Files are copied into "
            "per-asset refresh folders. CDNA missing never blocks Kalshi/Polymarket."
        ),
    )
    daily_crypto_three_venue_parser.add_argument(
        "--write-refreshed-evidence",
        type=Path,
        default=None,
        help=(
            "Override the output root for refreshed evidence. Defaults to "
            "reports/manual_evidence/daily_crypto_live/<date>/<timestamp>."
        ),
    )
    daily_crypto_three_venue_parser.add_argument(
        "--evidence-roots",
        type=Path,
        nargs="+",
        default=None,
        help=(
            "Override evidence root folders. Defaults to "
            "reports/manual_evidence/automation_batch_002/crypto then "
            "reports/manual_evidence/automation_batch_001_polished/crypto."
        ),
    )
    daily_crypto_three_venue_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "daily_crypto_three_venue_check.json",
    )
    daily_crypto_three_venue_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "daily_crypto_three_venue_check.md",
    )

    crypto_interval_parser = subparsers.add_parser(
        "run-crypto-interval-three-venue-check",
        help=(
            "Public-read-only intraday crypto interval / point-in-time threshold scan "
            "across Kalshi, Polymarket, and optional saved CDNA evidence. Matches by "
            "EXACT UTC settlement instant (Kalshi close_time / Polymarket endDate) so "
            "hourly contracts that share a clock-hour boundary can form typed-key "
            "candidates. Asks only; no midpoint; settlement-instant discipline preserved."
        ),
    )
    crypto_interval_parser.add_argument("--assets", default="BTC,ETH,SOL,XRP,DOGE")
    crypto_interval_parser.add_argument("--lookahead-hours", type=float, default=8.0)
    crypto_interval_parser.add_argument("--target-time-tolerance-seconds", type=float, default=0.0)
    crypto_interval_parser.add_argument(
        "--operator-risk-mode", choices=("conservative", "standard", "aggressive"), default="conservative"
    )
    crypto_interval_parser.add_argument("--allow-top-of-book-depth", action="store_true")
    crypto_interval_parser.add_argument("--operator-size-cap", type=float, default=0.0)
    crypto_interval_parser.add_argument("--include-cdna", action="store_true")
    crypto_interval_parser.add_argument("--operator-accept-cdna-display-price-risk", action="store_true")
    crypto_interval_parser.add_argument("--cdna-operator-size-cap", type=float, default=1.0)
    crypto_interval_parser.add_argument("--cdna-evidence-dir", type=Path, default=None)
    crypto_interval_parser.add_argument("--max-quote-age-seconds", type=float, default=300.0)
    crypto_interval_parser.add_argument("--min-available-notional", type=float, default=1.0)
    crypto_interval_parser.add_argument(
        "--refresh-kalshi-polymarket",
        action="store_true",
        help="Fetch fresh Kalshi + Polymarket interval evidence via public read-only APIs.",
    )
    crypto_interval_parser.add_argument("--write-refreshed-evidence", type=Path, default=None)
    crypto_interval_parser.add_argument("--evidence-roots", type=Path, nargs="+", default=None)
    crypto_interval_parser.add_argument(
        "--json-output", type=Path, default=PROJECT_ROOT / "reports" / "crypto_interval_three_venue_check.json"
    )
    crypto_interval_parser.add_argument(
        "--markdown-output", type=Path, default=PROJECT_ROOT / "reports" / "crypto_interval_three_venue_check.md"
    )

    structural_arb_parser = subparsers.add_parser(
        "crypto-structural-payoff-arb-scout",
        help=(
            "Structural payoff-state arb engine for crypto interval markets. Converts every "
            "compatible contract for the same asset + target instant into a payoff vector over "
            "discrete terminal price states, then searches for long-only guaranteed-payoff "
            "baskets, bucket->cumulative synthesis (YES buckets only), cross-venue threshold "
            "basis, same-payoff-cheaper baskets, and diagnostic inequality violations. "
            "Saved-evidence / public-read-only; asks only; no midpoint."
        ),
    )
    structural_arb_parser.add_argument("--assets", default="BTC,ETH,SOL,XRP,DOGE")
    structural_arb_parser.add_argument("--evidence-roots", type=Path, nargs="+", default=None)
    structural_arb_parser.add_argument(
        "--operator-risk-mode", choices=("conservative", "standard", "aggressive"), default="conservative"
    )
    structural_arb_parser.add_argument("--include-cdna", action="store_true")
    structural_arb_parser.add_argument("--operator-accept-cdna-display-price-risk", action="store_true")
    structural_arb_parser.add_argument("--allow-top-of-book-depth", action="store_true")
    structural_arb_parser.add_argument("--operator-size-cap", type=float, default=0.0)
    structural_arb_parser.add_argument("--cdna-operator-size-cap", type=float, default=1.0)
    structural_arb_parser.add_argument("--cdna-evidence-dir", type=Path, default=None)
    structural_arb_parser.add_argument("--cdna-timeseries-dir", type=Path, default=None,
                                       help="Dir holding cdna_crypto_latest.json (file only; no network).")
    structural_arb_parser.add_argument("--max-cdna-snapshot-age-seconds", type=float, default=60.0)
    structural_arb_parser.add_argument("--require-cdna-fresh-for-cdna-candidates", type=_str2bool, default=True)
    structural_arb_parser.add_argument("--max-quote-age-seconds", type=float, default=300.0)
    structural_arb_parser.add_argument("--min-available-notional", type=float, default=1.0)
    structural_arb_parser.add_argument("--max-basket-legs", type=int, default=12)
    structural_arb_parser.add_argument(
        "--source-basis-buffer-bps", type=float, default=0.0,
        help="Haircut (basis points of $1 edge) applied to cross-source candidates; PAPER requires adjusted net > 0.",
    )
    structural_arb_parser.add_argument(
        "--source-basis-buffer-absolute", type=str, default=None,
        help="Informational per-asset feed-price buffer, e.g. BTC=25,ETH=2,SOL=0.25,XRP=0.005,DOGE=0.0002.",
    )
    structural_arb_parser.add_argument(
        "--refresh-kalshi-polymarket", action="store_true",
        help="Fetch fresh interval evidence via public read-only APIs instead of reading saved roots.",
    )
    structural_arb_parser.add_argument("--lookahead-hours", type=float, default=8.0)
    structural_arb_parser.add_argument(
        "--json-output", type=Path, default=PROJECT_ROOT / "reports" / "crypto_structural_payoff_arb_scout.json"
    )
    structural_arb_parser.add_argument(
        "--markdown-output", type=Path, default=PROJECT_ROOT / "reports" / "crypto_structural_payoff_arb_scout.md"
    )

    watch_structural_parser = subparsers.add_parser(
        "watch-crypto-structural-arb",
        help=(
            "Repeatedly run crypto-structural-payoff-arb-scout over live crypto windows and "
            "maintain a rolling summary of the best post-fee opportunities. Public-read-only; "
            "CDNA saved-evidence-only; no alerts/notifications; no trading."
        ),
    )
    watch_structural_parser.add_argument("--assets", default="BTC,ETH,SOL,XRP,DOGE")
    watch_structural_parser.add_argument("--interval-seconds", type=float, default=60.0)
    watch_structural_parser.add_argument("--iterations", type=int, default=30)
    watch_structural_parser.add_argument(
        "--burst-mode", action="store_true",
        help="Scan faster near 5m/15m/20m/hourly/2h/4h boundaries to catch fleeting quotes.",
    )
    watch_structural_parser.add_argument("--burst-interval-seconds", type=float, default=5.0)
    watch_structural_parser.add_argument(
        "--normal-interval-seconds", type=float, default=None,
        help="Off-boundary interval in burst mode (defaults to --interval-seconds).",
    )
    watch_structural_parser.add_argument("--boundary-window-seconds", type=float, default=90.0)
    watch_structural_parser.add_argument(
        "--operator-risk-mode", choices=("conservative", "standard", "aggressive"), default="aggressive"
    )
    watch_structural_parser.add_argument("--include-cdna", action="store_true")
    watch_structural_parser.add_argument("--operator-accept-cdna-display-price-risk", action="store_true")
    watch_structural_parser.add_argument("--allow-top-of-book-depth", action="store_true")
    watch_structural_parser.add_argument("--operator-size-cap", type=float, default=0.0)
    watch_structural_parser.add_argument("--cdna-operator-size-cap", type=float, default=1.0)
    watch_structural_parser.add_argument("--cdna-evidence-dir", type=Path, default=None)
    watch_structural_parser.add_argument("--cdna-timeseries-dir", type=Path, default=None,
                                         help="Dir holding cdna_crypto_latest.json (file only; no network).")
    watch_structural_parser.add_argument("--max-cdna-snapshot-age-seconds", type=float, default=60.0)
    watch_structural_parser.add_argument("--require-cdna-fresh-for-cdna-candidates", type=_str2bool, default=True)
    watch_structural_parser.add_argument("--max-quote-age-seconds", type=float, default=180.0)
    watch_structural_parser.add_argument("--min-available-notional", type=float, default=1.0)
    watch_structural_parser.add_argument("--max-basket-legs", type=int, default=12)
    watch_structural_parser.add_argument("--source-basis-buffer-bps", type=float, default=0.0)
    watch_structural_parser.add_argument("--source-basis-buffer-absolute", type=str, default=None)
    watch_structural_parser.add_argument("--near-miss-net-edge-threshold", type=float, default=0.02)
    watch_structural_parser.add_argument("--wide-near-miss-net-edge-threshold", type=float, default=0.10)
    watch_structural_parser.add_argument("--lookahead-hours", type=float, default=8.0)
    watch_structural_parser.add_argument(
        "--output-dir", type=Path, default=PROJECT_ROOT / "reports" / "crypto_structural_watch"
    )

    audit_pack_parser = subparsers.add_parser(
        "extract-crypto-paper-candidate-audit-pack",
        help=(
            "Extract every PAPER_CANDIDATE row from watcher iteration reports with full leg "
            "detail and independently re-validate (payoff/cost/net edge, no missing/stale quote, "
            "buy-only, no short, no midpoint). Read-only over local reports; no trading."
        ),
    )
    audit_pack_parser.add_argument(
        "--watch-dir", type=Path, required=True,
        help="Watcher output dir containing <timestamp>/iteration.json reports.",
    )
    audit_pack_parser.add_argument(
        "--json-output", type=Path, default=PROJECT_ROOT / "reports" / "crypto_paper_candidate_audit_pack.json",
    )
    audit_pack_parser.add_argument(
        "--markdown-output", type=Path, default=PROJECT_ROOT / "reports" / "crypto_paper_candidate_audit_pack.md",
    )

    cdna_ingest_parser = subparsers.add_parser(
        "ingest-cdna-crypto-snapshots",
        help=(
            "Ingest saved CDNA intraday crypto evidence snapshots into a normalized time "
            "series (jsonl + latest.json + summary). Saved-evidence only; no CDNA network "
            "fetch; no browser; no trading; sensitive fields redacted."
        ),
    )
    cdna_ingest_parser.add_argument(
        "--input-root", type=Path, required=True,
        help="Root holding <snapshot_id>/cdna_crypto_intraday_evidence.json folders.",
    )
    cdna_ingest_parser.add_argument(
        "--output-dir", type=Path, default=PROJECT_ROOT / "reports" / "cdna_crypto_timeseries",
    )

    exec_plan_parser = subparsers.add_parser(
        "crypto-execution-plan",
        help=(
            "Convert audited buy-only PAPER_CANDIDATE rows into protected execution INTENTS "
            "(slippage caps, freshness/latency budget, partial-fill + residual-exposure model, "
            "CDNA fill-first). Produces order intents only — never places/cancels orders."
        ),
    )
    exec_plan_parser.add_argument(
        "--candidate-report", type=Path,
        default=PROJECT_ROOT / "reports" / "crypto_paper_candidate_audit_pack.json",
    )
    exec_plan_parser.add_argument("--candidate-id", type=str, default=None)
    exec_plan_parser.add_argument("--max-total-notional", type=float, default=10.0)
    exec_plan_parser.add_argument("--max-leg-notional", type=float, default=5.0)
    exec_plan_parser.add_argument("--max-slippage-cents", type=float, default=1.0)
    exec_plan_parser.add_argument("--max-quote-age-ms", type=float, default=750.0)
    exec_plan_parser.add_argument(
        "--execution-style",
        choices=("parallel_protected_limit", "fill_worst_leg_first", "manual"),
        default="manual",
    )
    exec_plan_parser.add_argument(
        "--json-output", type=Path, default=PROJECT_ROOT / "reports" / "crypto_execution_plan.json",
    )
    exec_plan_parser.add_argument(
        "--markdown-output", type=Path, default=PROJECT_ROOT / "reports" / "crypto_execution_plan.md",
    )

    # --- Crypto micro-test forensic journal (manual; never trades) -------------- #
    _MICRO_ROOT = PROJECT_ROOT / "reports" / "crypto_micro_tests"

    mt_start = subparsers.add_parser(
        "start-crypto-micro-test",
        help="Freeze a candidate + execution plan into a manual micro-test journal (no trading).",
    )
    mt_start.add_argument("--candidate-audit-pack", type=Path, required=True)
    mt_start.add_argument("--candidate-id", type=str, required=True)
    mt_start.add_argument("--execution-plan", type=Path, default=None)
    mt_start.add_argument("--max-total-notional", type=float, default=10.0)
    mt_start.add_argument("--test-label", type=str, default=None)
    mt_start.add_argument("--output-root", type=Path, default=_MICRO_ROOT)

    mt_fill = subparsers.add_parser(
        "record-crypto-micro-fill",
        help="Record a manually-observed fill for a micro-test leg (no trading).",
    )
    mt_fill.add_argument("--test-id", type=str, required=True)
    mt_fill.add_argument("--platform", choices=("kalshi", "polymarket", "cdna"), required=True)
    mt_fill.add_argument("--market-id-or-ticker", type=str, required=True)
    mt_fill.add_argument("--side", choices=("YES", "NO"), required=True)
    mt_fill.add_argument("--intended-limit-price", type=float, default=None)
    mt_fill.add_argument("--filled-price", type=float, default=None)
    mt_fill.add_argument("--filled-quantity", type=float, default=None)
    mt_fill.add_argument("--fees", type=float, default=None)
    mt_fill.add_argument("--order-start-time-utc", type=str, default=None)
    mt_fill.add_argument("--order-submit-time-utc", type=str, default=None)
    mt_fill.add_argument("--first-fill-time-utc", type=str, default=None)
    mt_fill.add_argument("--final-fill-time-utc", type=str, default=None)
    mt_fill.add_argument("--order-status", choices=("filled", "partial", "not_filled", "canceled", "rejected"), default="filled")
    mt_fill.add_argument("--notes", type=str, default=None)
    mt_fill.add_argument("--output-root", type=Path, default=_MICRO_ROOT)

    mt_final = subparsers.add_parser(
        "finalize-crypto-micro-test",
        help="Finalize a micro-test: compute economics, residual exposure, and verdict (no trading).",
    )
    mt_final.add_argument("--test-id", type=str, required=True)
    mt_final.add_argument("--settlement-status", type=str, default=None)
    mt_final.add_argument("--manual-notes", type=str, default=None)
    mt_final.add_argument("--output-root", type=Path, default=_MICRO_ROOT)

    mt_report = subparsers.add_parser(
        "crypto-micro-test-report",
        help="Render the 12-section forensic markdown report for a micro-test (no trading).",
    )
    mt_report.add_argument("--test-id", type=str, required=True)
    mt_report.add_argument("--markdown-output", type=Path, default=None)
    mt_report.add_argument("--output-root", type=Path, default=_MICRO_ROOT)

    mt_quote = subparsers.add_parser(
        "append-crypto-micro-quote-snapshot",
        help="Append a quote snapshot (pre/post-order, after-fill) to a micro-test (no trading).",
    )
    mt_quote.add_argument("--test-id", type=str, required=True)
    mt_quote.add_argument("--source", choices=("scanner", "manual", "api"), default="manual")
    mt_quote.add_argument("--json-file", type=Path, default=None)
    mt_quote.add_argument("--output-root", type=Path, default=_MICRO_ROOT)

    # --- Guarded live micro-test trigger (dry-run default; never trades unattended) - #
    trig = subparsers.add_parser(
        "trigger-crypto-structural-arb",
        help=(
            "Guarded micro-test trigger: live-scan -> freeze candidate -> refresh quotes -> recompute "
            "edge -> protected execution plan -> micro-test journal. Dry-run by default; live placement "
            "requires ALL explicit gates. Protected LIMIT BUY only; no shorting; CDNA manual fill-first."
        ),
    )
    trig.add_argument("--assets", default="BTC,ETH,SOL,XRP,DOGE")
    trig.add_argument("--watch-once-or-loop", choices=("once", "loop"), default="once")
    trig.add_argument("--iterations", type=int, default=300)
    trig.add_argument("--min-net-edge", type=float, default=0.10)
    trig.add_argument("--operator-risk-mode", choices=("conservative", "standard", "aggressive"), default="aggressive")
    trig.add_argument("--burst-mode", action="store_true")
    trig.add_argument("--burst-interval-seconds", type=float, default=3.0)
    trig.add_argument("--normal-interval-seconds", type=float, default=20.0)
    trig.add_argument("--boundary-window-seconds", type=float, default=120.0)
    trig.add_argument("--max-quote-age-ms", type=float, default=750.0)
    trig.add_argument("--max-slippage-cents", type=float, default=1.0)
    trig.add_argument("--order-timeout-ms", type=float, default=1500.0)
    trig.add_argument("--max-total-notional", type=float, default=30.0)
    trig.add_argument("--max-platform-notional", type=float, default=10.0)
    trig.add_argument("--max-leg-notional", type=float, default=5.0)
    trig.add_argument("--operator-size-cap", type=float, default=10.0)
    trig.add_argument("--max-daily-notional", type=float, default=30.0)
    trig.add_argument("--max-orders", type=int, default=4)
    trig.add_argument("--max-residual-exposure", type=float, default=5.0)
    trig.add_argument("--include-cdna", action="store_true")
    trig.add_argument("--cdna-evidence-dir", type=Path, default=None)
    trig.add_argument("--operator-accept-cdna-display-price-risk", action="store_true")
    trig.add_argument("--cdna-operator-size-cap", type=float, default=1.0)
    trig.add_argument("--source-basis-buffer-bps", type=float, default=0.0)
    trig.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "reports" / "crypto_structural_trigger")
    trig.add_argument("--execution-style", choices=("parallel_protected_limit", "least_liquid_first", "manual"), default="manual")
    trig.add_argument("--dry-run", action="store_true")
    trig.add_argument("--live", action="store_true")
    trig.add_argument("--i-understand-this-places-real-orders", action="store_true")
    trig.add_argument("--fail-fast", action="store_true")

    cov_audit = subparsers.add_parser(
        "crypto-arb-surface-coverage-audit",
        help=(
            "Verify the scanner is evaluating every plausible buy-only arb combination "
            "(contract family × platform pair/triple × candidate type) and flag GAP / "
            "EXPECTED_ZERO / NEEDS_DATA per candidate type. Read-only; no trading."
        ),
    )
    cov_audit.add_argument("--input-report", type=Path, default=None)
    cov_audit.add_argument("--latest-iteration-dir", type=Path, default=None)
    cov_audit.add_argument("--assets", default="BTC,ETH,SOL,XRP,DOGE")
    cov_audit.add_argument("--include-cdna", action="store_true")
    cov_audit.add_argument("--json-output", type=Path, default=PROJECT_ROOT / "reports" / "crypto_arb_surface_coverage_audit.json")
    cov_audit.add_argument("--markdown-output", type=Path, default=PROJECT_ROOT / "reports" / "crypto_arb_surface_coverage_audit.md")

    # --- Fast path: discovery pass + hot quote-loop trigger -------------------- #
    universe_parser = subparsers.add_parser(
        "build-crypto-candidate-universe",
        help="Discovery pass: run the full structural scout once and freeze the buy-only candidate universe (legs/strikes/instants/payoff vectors).",
    )
    universe_parser.add_argument("--assets", default="BTC,ETH,SOL,XRP,DOGE")
    universe_parser.add_argument("--operator-risk-mode", choices=("conservative", "standard", "aggressive"), default="aggressive")
    universe_parser.add_argument("--include-cdna", action="store_true")
    universe_parser.add_argument("--operator-accept-cdna-display-price-risk", action="store_true")
    universe_parser.add_argument("--cdna-evidence-dir", type=Path, default=None)
    universe_parser.add_argument("--cdna-timeseries-dir", type=Path, default=None,
                                 help="Dir holding cdna_crypto_latest.json (file only; no network).")
    universe_parser.add_argument("--max-cdna-snapshot-age-seconds", type=float, default=60.0)
    universe_parser.add_argument("--require-cdna-fresh-for-cdna-candidates", type=_str2bool, default=True)
    universe_parser.add_argument("--executable-venues", default="kalshi,polymarket",
                                 help="Venues allowed for automated live orders (CDNA never executable).")
    universe_parser.add_argument("--scan-venues", default="kalshi,polymarket,cdna")
    universe_parser.add_argument("--exclude-non-executable-from-live-universe", type=_str2bool, default=True)
    universe_parser.add_argument("--include-near-miss-templates", action="store_true")
    universe_parser.add_argument("--near-miss-net-edge-threshold", type=float, default=0.10)
    universe_parser.add_argument("--include-missing-quote-templates", action="store_true")
    universe_parser.add_argument("--min-template-quality", choices=("compatible_payoff", "priced_only", "paper_only"),
                                 default="compatible_payoff")
    universe_parser.add_argument("--allow-top-of-book-depth", action="store_true")
    universe_parser.add_argument("--operator-size-cap", type=float, default=10.0)
    universe_parser.add_argument("--cdna-operator-size-cap", type=float, default=1.0)
    universe_parser.add_argument("--max-basket-legs", type=int, default=12)
    universe_parser.add_argument("--min-net-edge", type=float, default=0.0)
    universe_parser.add_argument("--max-candidates", type=int, default=50)
    universe_parser.add_argument("--source-basis-buffer-bps", type=float, default=0.0)
    universe_parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "reports" / "active_crypto_candidate_universe.json")

    fast = subparsers.add_parser(
        "trigger-crypto-fast-path",
        help=(
            "Fast quote-loop trigger over a frozen candidate universe (no full scan / no markdown in the hot path). "
            "Measures recognition->order-intent and quote-refresh->order-submit latency; gates on decision/quote age. "
            "Dry-run by default; protected LIMIT BUY only."
        ),
    )
    fast.add_argument("--candidate-universe", type=Path, default=PROJECT_ROOT / "reports" / "active_crypto_candidate_universe.json")
    fast.add_argument("--quote-source", choices=("reference", "public_live"), default="reference")
    fast.add_argument("--cdna-timeseries-dir", type=Path, default=None,
                      help="Dir holding cdna_crypto_latest.json; CDNA legs are served from this file (no network).")
    fast.add_argument("--cdna-evidence-dir", type=Path, default=None)
    fast.add_argument("--max-cdna-snapshot-age-seconds", type=float, default=60.0)
    fast.add_argument("--require-cdna-fresh-for-cdna-candidates", type=_str2bool, default=True)
    fast.add_argument("--cdna-operator-size-cap", type=float, default=1.0)
    fast.add_argument("--executable-venues", default="kalshi,polymarket",
                      help="Venues allowed for automated live orders (CDNA never executable).")
    fast.add_argument("--quote-loop-interval-ms", type=float, default=500.0)
    fast.add_argument("--iterations", type=int, default=1)
    fast.add_argument("--min-net-edge", type=float, default=0.10)
    fast.add_argument("--max-decision-age-ms", type=float, default=500.0)
    fast.add_argument("--max-quote-age-ms", type=float, default=750.0)
    fast.add_argument("--refresh-universe-every-seconds", type=float, default=60.0)
    fast.add_argument("--source-basis-buffer-bps", type=float, default=0.0)
    fast.add_argument("--max-slippage-cents", type=float, default=1.0)
    fast.add_argument("--order-timeout-ms", type=float, default=1500.0)
    fast.add_argument("--max-total-notional", type=float, default=30.0)
    fast.add_argument("--max-platform-notional", type=float, default=10.0)
    fast.add_argument("--max-leg-notional", type=float, default=5.0)
    fast.add_argument("--operator-size-cap", type=float, default=10.0)
    fast.add_argument("--max-orders", type=int, default=4)
    fast.add_argument("--max-residual-exposure", type=float, default=5.0)
    fast.add_argument("--execution-style", choices=("parallel_protected_limit", "least_liquid_first", "manual"), default="manual")
    fast.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "reports" / "crypto_fast_path_trigger")
    fast.add_argument("--dry-run", action="store_true")
    fast.add_argument("--live", action="store_true")
    fast.add_argument("--i-understand-this-places-real-orders", action="store_true")

    # --- Daily phone summary notifier (reporting only) ------------------------ #
    summary_parser = subparsers.add_parser(
        "send-daily-summary",
        help=(
            "Build a daily crypto-arb summary from local reports and (only with --send) deliver a concise "
            "phone message via a notification provider. Reporting only; never trades; default provider dry_run."
        ),
    )
    summary_parser.add_argument("--date", default=None, help="YYYY-MM-DD (default: today, local).")
    summary_parser.add_argument("--reports-root", type=Path, default=PROJECT_ROOT / "reports")
    summary_parser.add_argument("--provider", choices=PROVIDER_NAMES, default="dry_run")
    summary_parser.add_argument("--send", action="store_true",
                                help="Actually deliver via the provider. Without it, nothing is sent.")
    summary_parser.add_argument("--json-output", type=Path, default=None)
    summary_parser.add_argument("--markdown-output", type=Path, default=None)
    summary_parser.add_argument("--message-output", type=Path, default=None)
    summary_parser.add_argument("--max-message-chars", type=int, default=1500)

    batch_readiness_parser = subparsers.add_parser(
        "batch-evidence-import-readiness",
        help=(
            "Saved-file-only readiness matrix over manual evidence batches. "
            "Ranks crypto, sports, CDNA fill-first, and graph-review worklists."
        ),
    )
    batch_readiness_parser.add_argument(
        "--input-roots",
        type=Path,
        nargs="+",
        required=True,
        help="One or more manual evidence batch roots.",
    )
    batch_readiness_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "batch_evidence_import_readiness.json",
    )
    batch_readiness_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "batch_evidence_import_readiness.md",
    )

    championship_generic_parser = subparsers.add_parser(
        "championship-operator-scout-generic",
        help=(
            "Saved-file-only generic championship/categorical winner operator scout. "
            "Kalshi/Polymarket rows stay operator-review only; CDNA stays fill-first only."
        ),
    )
    championship_generic_parser.add_argument("--family-folder", type=Path, required=True)
    championship_generic_parser.add_argument("--accept-operator-risk", action="store_true")
    championship_generic_parser.add_argument("--include-cdna-fill-first", action="store_true")
    championship_generic_parser.add_argument("--operator-accept-cdna-display-price-risk", action="store_true")
    championship_generic_parser.add_argument(
        "--operator-risk-mode",
        choices=("conservative", "standard", "aggressive"),
        default="conservative",
    )
    championship_generic_parser.add_argument("--max-quote-age-seconds", type=float, default=3600.0)
    championship_generic_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "championship_operator_scout_generic.json",
    )
    championship_generic_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "championship_operator_scout_generic.md",
    )

    three_venue_parser = subparsers.add_parser(
        "three-venue-operator-scout",
        help=(
            "Saved-file-only unified Kalshi/Polymarket/CDNA operator scout. "
            "CDNA fill-first rows are generated in the same candidate pass as executable venue rows."
        ),
    )
    three_venue_parser.add_argument(
        "--family-folder",
        type=Path,
        action="append",
        required=True,
        help="Market family folder containing saved Kalshi/Polymarket/CDNA evidence. Repeat for multiple families.",
    )
    three_venue_parser.add_argument("--include-cdna", action="store_true")
    three_venue_parser.add_argument("--operator-accept-cdna-display-price-risk", action="store_true")
    three_venue_parser.add_argument("--cdna-operator-size-cap", type=float, default=1.0)
    three_venue_parser.add_argument("--max-quote-age-seconds", type=float, default=900.0)
    three_venue_parser.add_argument("--min-available-notional", type=float, default=10.0)
    three_venue_parser.add_argument(
        "--operator-risk-mode",
        choices=("conservative", "standard", "aggressive"),
        default="conservative",
        help="Paper-candidate acceptance mode. Aggressive includes CDNA fill-first display-price candidates when hard gates pass.",
    )
    three_venue_parser.add_argument(
        "--allow-stale-for-diagnostic",
        action="store_true",
        help="Keep stale_quote as a blocker but still compute diagnostic gross/net rows. Stale rows cannot become operator-review actions.",
    )
    three_venue_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "three_venue_operator_scout.json",
    )
    three_venue_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "three_venue_operator_scout.md",
    )

    manual_evidence_parser = subparsers.add_parser(
        "manual-evidence-requirements",
        help=(
            "Saved-file-only manual evidence requirements catalogue + playbook. Emits the full "
            "per-vertical per-platform list of manual evidence Mason needs to capture (rules text, "
            "settlement source, comparator, observation time, fee schedule, fresh quote, etc.) to "
            "move rows from missing-evidence into source-review. Diagnostic only; never clears "
            "evaluator gates and never creates paper candidates."
        ),
    )
    manual_evidence_parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help=(
            "Directory containing the saved reports the catalogue consults to enrich item status "
            "(kalshi_crypto_typed_key_audit.json, crypto_payoff_calendar_audit.json, "
            "family_graduation_fed.json, default_sports_sweep_summary.json, "
            "ibkr_forecastex_quote_diagnostics.json, the_odds_api_fv_residuals.json, "
            "relative_value_ops_status.json)."
        ),
    )
    manual_evidence_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "manual_evidence_requirements.json",
    )
    manual_evidence_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "manual_evidence_requirements.md",
    )

    cdna_basis_parser = subparsers.add_parser(
        "cdna-crypto-basis-risk-scout",
        help="Saved-file-only CDNA / Crypto.com Predict crypto basis-risk scout. Emits BASIS_RISK_REVIEW / WATCH / MANUAL_REVIEW only; never exact-equality or paper actions.",
    )
    cdna_basis_parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Path to a saved CDNA fixture JSON file (top-level array or single object).",
    )
    cdna_basis_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "cdna_crypto_basis_risk_scout.json",
    )
    cdna_basis_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "cdna_crypto_basis_risk_scout.md",
    )
    cdna_basis_parser.add_argument(
        "--peer-input-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help="Directory containing normalized_markets_v0.json with saved Kalshi/Polymarket crypto rows for peer comparison.",
    )

    cdna_fill_first_parser = subparsers.add_parser(
        "cdna-fill-first-scout",
        help=(
            "Saved-file-only CDNA fill-first operator scout. CDNA display prices are indicative "
            "until a manual fill is recorded; this command never creates standard candidate pairs or paper actions."
        ),
    )
    cdna_fill_first_parser.add_argument("--cdna-evidence", type=Path, required=True)
    cdna_fill_first_parser.add_argument("--partner-evidence", type=Path, required=True)
    cdna_fill_first_parser.add_argument(
        "--partner-platform",
        choices=("kalshi", "polymarket"),
        required=True,
        help="Partner venue used for the hedge leg.",
    )
    cdna_fill_first_parser.add_argument("--market-family", required=True)
    cdna_fill_first_parser.add_argument("--league", required=True)
    cdna_fill_first_parser.add_argument("--season", required=True)
    cdna_fill_first_parser.add_argument(
        "--operator-accept-display-price-risk",
        action="store_true",
        help="Allow CDNA_FILL_FIRST_REVIEW rows after display-price risk is explicitly accepted.",
    )
    cdna_fill_first_parser.add_argument("--cdna-operator-size-cap", type=float, default=1.0)
    cdna_fill_first_parser.add_argument("--max-partner-hedge-slippage", type=float, default=0.01)
    cdna_fill_first_parser.add_argument(
        "--max-quote-age-seconds",
        type=float,
        default=CDNA_FILL_FIRST_DEFAULT_MAX_QUOTE_AGE_SECONDS,
    )
    cdna_fill_first_parser.add_argument(
        "--fill-log",
        type=Path,
        default=None,
        help="Optional saved manual CDNA fill log. No API calls are made.",
    )
    cdna_fill_first_parser.add_argument(
        "--operator-risk-mode",
        choices=("conservative", "standard", "aggressive"),
        default="conservative",
    )
    cdna_fill_first_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "cdna_fill_first_scout.json",
    )
    cdna_fill_first_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "cdna_fill_first_scout.md",
    )

    cdna_record_fill_parser = subparsers.add_parser(
        "cdna-record-fill",
        help=(
            "Append a manually entered CDNA fill record to a saved JSON log. "
            "This command never calls any venue API and rejects sensitive fields."
        ),
    )
    cdna_record_fill_parser.add_argument("--fill-log", type=Path, required=True)
    cdna_record_fill_parser.add_argument("--event-key", required=True)
    cdna_record_fill_parser.add_argument("--market-family", required=True)
    cdna_record_fill_parser.add_argument("--team", required=True)
    cdna_record_fill_parser.add_argument("--side", choices=("YES", "NO"), required=True)
    cdna_record_fill_parser.add_argument("--contract-id", required=True)
    cdna_record_fill_parser.add_argument("--symbol", required=True)
    cdna_record_fill_parser.add_argument("--requested-quantity", type=float, required=True)
    cdna_record_fill_parser.add_argument("--filled-quantity", type=float, required=True)
    cdna_record_fill_parser.add_argument("--filled-price", type=float, required=True)
    cdna_record_fill_parser.add_argument("--fee-per-contract", type=float, default=0.02)
    cdna_record_fill_parser.add_argument("--filled-at", required=True)
    cdna_record_fill_parser.add_argument("--source-note", default="")
    cdna_record_fill_parser.add_argument("--time-to-fill-seconds", type=float, default=None)

    scout_parser = subparsers.add_parser(
        "cross-venue-opportunity-scout",
        help="Saved-file-only cross-venue diagnostic scout that ranks closest review targets and their blockers. Does not create candidate pairs or paper actions.",
    )
    scout_parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help="Saved reports directory to scan.",
    )
    scout_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "cross_venue_opportunity_scout.json",
    )
    scout_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "cross_venue_opportunity_scout.md",
    )
    scout_parser.add_argument(
        "--polymarket-enriched-json",
        type=Path,
        default=None,
        help="Optional enriched Polymarket taxonomy JSON with attached CLOB quote evidence. Defaults to input-dir/polymarket_taxonomy_shape_scout_enriched.json when present.",
    )
    scout_parser.add_argument(
        "--active-platforms",
        default="kalshi,polymarket,cdna",
        help="Comma-separated active platform filter for ranking. Rows outside this set stay in the report as queued/inactive.",
    )

    core_trio_peer_parser = subparsers.add_parser(
        "core-trio-peer-coverage-audit",
        help=(
            "Saved-file-only diagnostic audit of Kalshi, Polymarket, and CDNA peer coverage. "
            "Identifies missing Kalshi family coverage and typed-key gaps; creates no pairs or paper actions."
        ),
    )
    core_trio_peer_parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help="Saved reports directory to audit.",
    )
    core_trio_peer_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "core_trio_peer_coverage_audit.json",
    )
    core_trio_peer_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "core_trio_peer_coverage_audit.md",
    )

    existing_paper_audit_parser = subparsers.add_parser(
        "audit-existing-paper-candidates",
        help="Saved-file-only forensic audit of existing evaluator positive rows; creates no new candidates.",
    )
    existing_paper_audit_parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help="Saved reports directory to scan.",
    )
    existing_paper_audit_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "existing_paper_candidate_audit.json",
    )
    existing_paper_audit_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "existing_paper_candidate_audit.md",
    )

    stale_archive_plan_parser = subparsers.add_parser(
        "plan-stale-report-archive",
        help="Saved-file-only stale evaluator/report archive plan; prints suggested move commands but moves nothing.",
    )
    stale_archive_plan_parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help="Saved reports directory to scan.",
    )
    stale_archive_plan_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "stale_report_archive_plan.json",
    )
    stale_archive_plan_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "stale_report_archive_plan.md",
    )

    stale_archive_apply_parser = subparsers.add_parser(
        "apply-stale-report-archive-plan",
        help="Dry-run or apply the saved stale report archive plan with shutil.move; dry-run by default.",
    )
    stale_archive_apply_parser.add_argument(
        "--plan",
        type=Path,
        default=PROJECT_ROOT / "reports" / "stale_report_archive_plan.json",
        help="Saved stale_report_archive_plan.json to read.",
    )
    stale_archive_apply_parser.add_argument(
        "--applied-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "stale_report_archive_applied.json",
        help="Applied manifest written only when --apply is used.",
    )
    stale_archive_apply_mode = stale_archive_apply_parser.add_mutually_exclusive_group()
    stale_archive_apply_mode.add_argument("--dry-run", action="store_true", help="Print moves and modify nothing.")
    stale_archive_apply_mode.add_argument("--apply", action="store_true", help="Move only files listed in the saved plan.")

    paper_readiness_probe_parser = subparsers.add_parser(
        "audit-paper-readiness-probe",
        help="Saved-file-only probe for reviewed-scope rows still blocked before execution readiness.",
    )
    paper_readiness_probe_parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help="Saved reports directory containing burden, registry coverage, graduation, and normalized reports.",
    )
    paper_readiness_probe_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "paper_readiness_probe.json",
    )
    paper_readiness_probe_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "paper_readiness_probe.md",
    )

    mlb_revival_status_parser = subparsers.add_parser(
        "run-mlb-world-series-revival-status",
        help="Saved-file-only MLB World Series revival checklist using strict same-payoff-board evidence and saved enrichment.",
    )
    mlb_revival_status_parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help="Saved reports directory to inspect.",
    )
    mlb_revival_status_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "mlb_world_series_revival_status.json",
    )
    mlb_revival_status_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "mlb_world_series_revival_status.md",
    )

    platform_api_expansion_parser = subparsers.add_parser(
        "audit-platform-api-expansion",
        help="Saved-file-only platform API adapter readiness matrix; no live fetching or evaluator promotion.",
    )
    platform_api_expansion_parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help="Saved reports directory used for fixture/report evidence summaries.",
    )
    platform_api_expansion_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "platform_api_expansion.json",
    )
    platform_api_expansion_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "platform_api_expansion.md",
    )

    crypto_com_predict_parser = subparsers.add_parser(
        "parse-crypto-com-predict-cdna-fixtures",
        help="Parse saved Crypto.com Predict/CDNA fixtures into a research-only snapshot; no live fetching.",
    )
    crypto_com_predict_parser.add_argument(
        "--fixture-dir",
        type=Path,
        action="append",
        default=None,
        help="Directory containing saved Crypto.com Predict/CDNA HTML or JSON fixtures.",
    )
    crypto_com_predict_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "crypto_com_predict_cdna_research_snapshot.json",
    )

    cdna_kalshi_basis_parser = subparsers.add_parser(
        "compare-cdna-vs-kalshi-btc-basis-risk",
        help="Saved-file-only CDNA x Kalshi BTC basis-risk join; diagnostic only and never evaluator input.",
    )
    cdna_kalshi_basis_parser.add_argument(
        "--cdna",
        type=Path,
        default=PROJECT_ROOT / "reports" / "crypto_com_predict_cdna_research_snapshot.json",
        help="Saved Crypto.com Predict/CDNA research snapshot JSON.",
    )
    cdna_kalshi_basis_parser.add_argument(
        "--standardized",
        type=Path,
        default=PROJECT_ROOT / "reports" / "standardized_family_candidates.json",
        help="Saved standardized family candidates JSON.",
    )
    cdna_kalshi_basis_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "cdna_vs_kalshi_btc_basis_risk.json",
    )

    sx_bet_saved_parser = subparsers.add_parser(
        "normalize-sx-bet-saved",
        help="Saved-file-only SX Bet draft normalizer; research-only and never evaluator input.",
    )
    sx_bet_saved_parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help="Saved reports directory to scan for SX Bet research snapshots.",
    )
    sx_bet_saved_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "sx_bet_normalized_draft.json",
    )
    sx_bet_saved_parser.add_argument(
        "--coverage-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "sx_bet_normalized_draft_coverage.json",
    )

    sx_bet_typed_keys_parser = subparsers.add_parser(
        "audit-sx-bet-sports-typed-keys",
        help="Saved-file-only SX Bet sports typed-key coverage audit; no candidates or evaluator integration.",
    )
    sx_bet_typed_keys_parser.add_argument(
        "--input",
        type=Path,
        default=PROJECT_ROOT / "reports" / "sx_bet_normalized_draft.json",
        help="Saved normalize-sx-bet-saved JSON report.",
    )
    sx_bet_typed_keys_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "sx_bet_sports_typed_keys.json",
    )
    sx_bet_typed_keys_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "sx_bet_sports_typed_keys.md",
    )

    sx_bet_overlap_parser = subparsers.add_parser(
        "audit-sx-bet-sports-overlap",
        help="Saved-file-only SX Bet sports typed-key overlap diagnostics; no candidates or evaluator integration.",
    )
    sx_bet_overlap_parser.add_argument(
        "--sx-bet-typed-keys",
        type=Path,
        default=PROJECT_ROOT / "reports" / "sx_bet_sports_typed_keys.json",
        help="Saved audit-sx-bet-sports-typed-keys JSON report.",
    )
    sx_bet_overlap_parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help="Saved reports directory containing normalized_markets_v0 and settlement_evidence_burden reports.",
    )
    sx_bet_overlap_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "sx_bet_sports_overlap.json",
    )
    sx_bet_overlap_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "sx_bet_sports_overlap.md",
    )
    sx_bet_overlap_parser.add_argument(
        "--require-game-level-target",
        action="store_true",
        help="Only consider Kalshi/Polymarket target rows with game-level sports market types.",
    )

    mlb_daily_residual_parser = subparsers.add_parser(
        "sports-mlb-daily-residual-risk-scout",
        help=(
            "Saved-evidence-only MLB daily game-winner residual-risk scout. "
            "The contingency-risk override is default-off and never affects strict evaluator gates."
        ),
    )
    mlb_daily_residual_parser.add_argument(
        "--kalshi-evidence",
        type=Path,
        required=True,
        help="Saved normalized Kalshi MLB daily-game evidence JSON.",
    )
    mlb_daily_residual_parser.add_argument(
        "--polymarket-evidence",
        type=Path,
        required=True,
        help="Saved normalized Polymarket MLB daily-game evidence JSON.",
    )
    mlb_daily_residual_parser.add_argument("--date", required=True, help="Slate date label, e.g. 2026-05-28.")
    mlb_daily_residual_parser.add_argument(
        "--accept-mlb-daily-contingency-risk",
        action="store_true",
        help="Explicitly accept MLB daily-game residual postponement/suspension/cancellation tail risk for this diagnostic run only.",
    )
    mlb_daily_residual_parser.add_argument(
        "--operator-accepted-as-arb",
        action="store_true",
        help="Allow scoped operator-approved arb review rows after the MLB daily contingency-risk flag and all diagnostic gates pass.",
    )
    mlb_daily_residual_parser.add_argument(
        "--include-live-games",
        action="store_true",
        help="Deprecated compatibility no-op: live games are included by default in the scoped operator-risk lane.",
    )
    mlb_daily_residual_parser.add_argument(
        "--exclude-live-games",
        action="store_true",
        help="Exclude live/in-progress games from operator review; live rows become WATCH with live_game_excluded_by_operator_flag.",
    )
    mlb_daily_residual_parser.add_argument(
        "--max-quote-age-seconds",
        type=float,
        default=MLB_DAILY_RESIDUAL_RISK_DEFAULT_MAX_QUOTE_AGE_SECONDS,
        help="Maximum quote age before stale_or_missing_quote blocks the row.",
    )
    mlb_daily_residual_parser.add_argument(
        "--operator-risk-mode",
        choices=("conservative", "standard", "aggressive"),
        default="conservative",
    )
    mlb_daily_residual_parser.add_argument(
        "--min-available-notional",
        type=float,
        default=MLB_DAILY_RESIDUAL_RISK_DEFAULT_MIN_AVAILABLE_NOTIONAL,
        help="Minimum explicit same-unit available notional before a row can reach residual-risk shadow review.",
    )
    mlb_daily_residual_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "sports_mlb_daily_residual_risk_scout.json",
    )
    mlb_daily_residual_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "sports_mlb_daily_residual_risk_scout.md",
    )

    mlb_daily_fetch_parser = subparsers.add_parser(
        "fetch-mlb-daily-game-evidence",
        help=(
            "Public no-auth collector for MLB daily game-winner Kalshi/Polymarket evidence. "
            "Writes raw snapshots and normalized evidence only."
        ),
    )
    mlb_daily_fetch_parser.add_argument(
        "--date",
        default=None,
        help="Slate date in YYYY-MM-DD. Defaults to current local date if omitted.",
    )
    mlb_daily_fetch_parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Raw snapshot base directory. Defaults to reports/live_readonly/mlb_daily/<date>.",
    )
    mlb_daily_fetch_parser.add_argument(
        "--normalized-output-dir",
        type=Path,
        default=None,
        help="Normalized output directory. Defaults to reports/manual_evidence/sports/mlb_daily_games/<date>/normalized.",
    )
    mlb_daily_fetch_parser.add_argument("--max-games", type=int, default=20)
    mlb_daily_fetch_parser.add_argument("--timeout-seconds", type=float, default=10.0)

    mlb_daily_operator_check_parser = subparsers.add_parser(
        "run-mlb-daily-operator-check",
        help=(
            "Run the public no-auth MLB daily evidence collector and then the saved-evidence-only "
            "operator/residual scout. No execution or candidate-pair logic is invoked."
        ),
    )
    mlb_daily_operator_check_parser.add_argument(
        "--date",
        default=None,
        help="Slate date in YYYY-MM-DD. Defaults to current local date if omitted.",
    )
    mlb_daily_operator_check_parser.add_argument("--max-games", type=int, default=20)
    mlb_daily_operator_check_parser.add_argument("--timeout-seconds", type=float, default=10.0)
    mlb_daily_operator_check_parser.add_argument(
        "--accept-mlb-daily-contingency-risk",
        action="store_true",
        help="Explicitly accept MLB daily-game residual contingency risk for this diagnostic run only.",
    )
    mlb_daily_operator_check_parser.add_argument(
        "--operator-accepted-as-arb",
        action="store_true",
        help="Allow scoped OPERATOR_ARB_PAPER_REVIEW rows if all diagnostic gates pass.",
    )
    mlb_daily_operator_check_parser.add_argument(
        "--include-live-games",
        action="store_true",
        help="Deprecated compatibility no-op: live games are included by default in the scoped operator-risk lane.",
    )
    mlb_daily_operator_check_parser.add_argument(
        "--exclude-live-games",
        action="store_true",
        help="Exclude live/in-progress games from operator review.",
    )
    mlb_daily_operator_check_parser.add_argument(
        "--max-quote-age-seconds",
        type=float,
        default=MLB_DAILY_RESIDUAL_RISK_DEFAULT_MAX_QUOTE_AGE_SECONDS,
    )
    mlb_daily_operator_check_parser.add_argument(
        "--operator-risk-mode",
        choices=("conservative", "standard", "aggressive"),
        default="conservative",
    )
    mlb_daily_operator_check_parser.add_argument(
        "--min-available-notional",
        type=float,
        default=MLB_DAILY_RESIDUAL_RISK_DEFAULT_MIN_AVAILABLE_NOTIONAL,
    )
    mlb_daily_operator_check_parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / "reports" / "live_readonly" / "mlb_daily",
        help="Raw collector output root. The date subdirectory is appended.",
    )
    mlb_daily_operator_check_parser.add_argument(
        "--normalized-root",
        type=Path,
        default=PROJECT_ROOT / "reports" / "manual_evidence" / "sports" / "mlb_daily_games",
        help="Normalized evidence root. The date/normalized subdirectory is appended.",
    )
    mlb_daily_operator_check_parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Scout JSON output path. Defaults to reports/sports_mlb_daily_games_<date>_operator_arb_scout.json.",
    )
    mlb_daily_operator_check_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=None,
        help="Scout Markdown output path. Defaults to reports/sports_mlb_daily_games_<date>_operator_arb_scout.md.",
    )

    mlb_world_series_fetch_parser = subparsers.add_parser(
        "fetch-mlb-world-series-evidence",
        help=(
            "Public no-auth collector for MLB World Series/Pro Baseball Champion futures evidence. "
            "Writes raw snapshots and normalized evidence only."
        ),
    )
    mlb_world_series_fetch_parser.add_argument("--season", required=True, help="Season year, e.g. 2026.")
    mlb_world_series_fetch_parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Raw snapshot base directory. Defaults to reports/live_readonly/mlb_world_series/<season>.",
    )
    mlb_world_series_fetch_parser.add_argument(
        "--normalized-output-dir",
        type=Path,
        default=None,
        help="Normalized output directory. Defaults to reports/manual_evidence/sports/mlb_world_series_<season>.",
    )
    mlb_world_series_fetch_parser.add_argument("--timeout-seconds", type=float, default=10.0)

    mlb_world_series_compare_parser = subparsers.add_parser(
        "sports-mlb-world-series-evidence-compare",
        help=(
            "Saved-evidence-only MLB World Series Kalshi/Polymarket source comparison. "
            "Diagnostic only; never creates candidate pairs or evaluator actions."
        ),
    )
    mlb_world_series_compare_parser.add_argument(
        "--kalshi-evidence",
        type=Path,
        required=True,
        help="Saved normalized Kalshi MLB World Series evidence JSON.",
    )
    mlb_world_series_compare_parser.add_argument(
        "--polymarket-evidence",
        type=Path,
        required=True,
        help="Saved normalized Polymarket MLB World Series evidence JSON.",
    )
    mlb_world_series_compare_parser.add_argument(
        "--accept-world-series-remote-tail-risk",
        action="store_true",
        help="Record human acceptance of no-champion/Other-vs-proportional remote tail risk for diagnostics only.",
    )
    mlb_world_series_compare_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "sports_mlb_world_series_cross_venue_comparison.json",
    )
    mlb_world_series_compare_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "sports_mlb_world_series_cross_venue_comparison.md",
    )

    mlb_world_series_residual_parser = subparsers.add_parser(
        "sports-mlb-world-series-residual-risk-scout",
        help=(
            "Saved-evidence-only MLB World Series residual-risk shadow scout. "
            "Diagnostic only; remote-tail-risk override is explicit and default-off."
        ),
    )
    mlb_world_series_residual_parser.add_argument(
        "--kalshi-evidence",
        type=Path,
        required=True,
        help="Saved normalized Kalshi MLB World Series evidence JSON.",
    )
    mlb_world_series_residual_parser.add_argument(
        "--polymarket-evidence",
        type=Path,
        required=True,
        help="Saved normalized Polymarket MLB World Series evidence JSON.",
    )
    mlb_world_series_residual_parser.add_argument("--season", required=True, help="Season year, e.g. 2026.")
    mlb_world_series_residual_parser.add_argument(
        "--accept-world-series-remote-tail-risk",
        action="store_true",
        help="Explicitly accept no-champion/Other-vs-proportional remote tail risk for this diagnostic run only.",
    )
    mlb_world_series_residual_parser.add_argument(
        "--operator-accepted-as-arb",
        action="store_true",
        help=(
            "Scoped operator-approved arb mode for MLB World Series winner markets only. "
            "Requires --accept-world-series-remote-tail-risk and never emits standard paper candidates."
        ),
    )
    mlb_world_series_residual_parser.add_argument(
        "--max-quote-age-seconds",
        type=float,
        default=MLB_WORLD_SERIES_RESIDUAL_RISK_DEFAULT_MAX_QUOTE_AGE_SECONDS,
        help="Maximum quote age before stale_or_missing_quote blocks a row.",
    )
    mlb_world_series_residual_parser.add_argument(
        "--operator-risk-mode",
        choices=("conservative", "standard", "aggressive"),
        default="conservative",
    )
    mlb_world_series_residual_parser.add_argument(
        "--min-available-notional",
        type=float,
        default=MLB_WORLD_SERIES_RESIDUAL_RISK_DEFAULT_MIN_AVAILABLE_NOTIONAL,
        help="Minimum explicit same-unit available notional before a row can reach residual-risk shadow review.",
    )
    mlb_world_series_residual_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "sports_mlb_world_series_residual_risk_scout.json",
    )
    mlb_world_series_residual_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "sports_mlb_world_series_residual_risk_scout.md",
    )

    operator_convergence_parser = subparsers.add_parser(
        "operator-arb-convergence-plan",
        help=(
            "Saved-report-only convergence and early-exit plan for operator-approved arb rows. "
            "Diagnostic only; never places trades or emits standard paper candidates."
        ),
    )
    operator_convergence_parser.add_argument("--input-report", type=Path, required=True)
    operator_convergence_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "operator_arb_convergence_plan.json",
    )
    operator_convergence_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "operator_arb_convergence_plan.md",
    )
    operator_convergence_parser.add_argument("--target-exit-edge", type=float, default=0.015)
    operator_convergence_parser.add_argument("--min-hold-net-edge", type=float, default=0.02)
    operator_convergence_parser.add_argument("--min-annualized-return", type=float, default=0.10)
    operator_convergence_parser.add_argument(
        "--settlement-date",
        help="Optional YYYY-MM-DD settlement date for hold-to-settlement annualized return diagnostics.",
    )
    operator_convergence_parser.add_argument("--max-capital-tieup-days", type=int, default=45)

    markout_parser = subparsers.add_parser(
        "replay-paper-candidate-markouts",
        help="Fill saved paper-candidate markout windows from later saved enriched snapshots.",
    )
    markout_parser.add_argument("--ledger", type=Path, required=True)
    markout_parser.add_argument("--polymarket-enriched-later", type=Path, required=True)
    markout_parser.add_argument("--kalshi-enriched-later", type=Path, required=True)
    markout_parser.add_argument("--output", type=Path, required=True)
    markout_parser.add_argument(
        "--window-tolerance-seconds",
        type=float,
        default=60.0,
        help="Allowed timestamp distance around each markout window.",
    )

    pipeline_parser = subparsers.add_parser(
        "run-targeted-pipeline",
        help="Run the read-only saved-file workflow for one targeted universe.",
    )
    pipeline_parser.add_argument("--polymarket-tag-slug", help="Polymarket Gamma tag slug, for example nba.")
    pipeline_parser.add_argument("--polymarket-tag-id", type=int, help="Polymarket Gamma tag id.")
    pipeline_parser.add_argument("--kalshi-series-ticker", help="Kalshi series ticker, for example KXNBA.")
    pipeline_parser.add_argument("--kalshi-event-ticker", help="Kalshi event ticker.")
    pipeline_parser.add_argument("--label", required=True, help="Safe label used to prefix reports output files.")
    pipeline_parser.add_argument("--limit", type=int, default=50)
    pipeline_parser.add_argument("--kalshi-max-pages", type=int, default=2)
    pipeline_parser.add_argument("--timeout-seconds", type=float, default=10.0)
    pipeline_parser.add_argument("--max-snapshot-age-hours", type=float, default=24.0)
    pipeline_parser.add_argument("--max-quote-age-seconds", type=float, default=1800.0)
    pipeline_parser.add_argument("--max-settlement-delta-seconds", type=float, default=3600.0)
    pipeline_parser.add_argument("--min-top-of-book-size", type=float, default=1.0)
    pipeline_parser.add_argument("--min-net-gap", type=float, default=0.01)
    pipeline_parser.add_argument(
        "--accept-unit-mismatch",
        action="store_true",
        help="Forward unit-mismatch acceptance to evaluate-paper-candidates.",
    )
    pipeline_parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "reports")

    sweep_parser = subparsers.add_parser(
        "run-multi-universe-sweep",
        help="Run the read-only targeted pipeline once per universe in a JSON manifest.",
    )
    sweep_parser.add_argument("--manifest", type=Path, required=True)
    sweep_parser.add_argument("--sweep-label", required=True, help="Safe label used to prefix aggregate sweep outputs.")
    sweep_parser.add_argument("--limit", type=int, default=50)
    sweep_parser.add_argument("--kalshi-max-pages", type=int, default=2)
    sweep_parser.add_argument("--timeout-seconds", type=float, default=10.0)
    sweep_parser.add_argument("--max-snapshot-age-hours", type=float, default=24.0)
    sweep_parser.add_argument("--max-quote-age-seconds", type=float, default=1800.0)
    sweep_parser.add_argument("--max-settlement-delta-seconds", type=float, default=3600.0)
    sweep_parser.add_argument("--min-top-of-book-size", type=float, default=1.0)
    sweep_parser.add_argument("--min-net-gap", type=float, default=0.01)
    sweep_parser.add_argument(
        "--accept-unit-mismatch",
        action="store_true",
        help="Forward unit-mismatch acceptance to each evaluate-paper-candidates run.",
    )
    sweep_parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "reports")

    explain_sweep_parser = subparsers.add_parser(
        "explain-sweep-summary",
        help="Print a human-readable explanation of a saved multi-universe sweep summary.",
    )
    explain_sweep_parser.add_argument("--summary", type=Path, required=True)

    explain_pipeline_parser = subparsers.add_parser(
        "explain-pipeline-summary",
        help="Print a human-readable explanation of a saved targeted pipeline summary.",
    )
    explain_pipeline_parser.add_argument("--summary", type=Path, required=True)

    explain_candidates_parser = subparsers.add_parser(
        "explain-paper-candidates",
        help="Print a human-readable explanation of saved paper-candidate ledger rows.",
    )
    explain_candidates_parser.add_argument("--ledger", type=Path, required=True)
    explain_candidates_parser.add_argument("--action", choices=["PAPER_CANDIDATE", "MANUAL_REVIEW", "WATCH"])
    explain_candidates_parser.add_argument("--limit", type=int)

    explain_reference_parser = subparsers.add_parser(
        "explain-reference-context",
        help="Print diagnostic-only sportsbook reference context for one executable snapshot.",
    )
    explain_reference_parser.add_argument("--snapshot", type=Path, required=True)
    explain_reference_parser.add_argument("--reference-snapshot", type=Path, required=True)
    explain_reference_parser.add_argument("--min-similarity", type=float, default=0.35)

    llm_review_parser = subparsers.add_parser(
        "llm-review-relationships",
        help="Attach stubbed LLM relationship review audit metadata to a saved matcher/evaluator report.",
    )
    llm_review_parser.add_argument("--input", type=Path, required=True)
    llm_review_parser.add_argument("--output", type=Path, required=True)
    llm_review_parser.add_argument("--markdown-output", type=Path)
    llm_review_parser.add_argument("--stub", action="store_true", help="Use the deterministic no-network stub client.")

    source_readiness_parser = subparsers.add_parser(
        "source-readiness",
        help="Print a read-only source/API readiness checklist and provenance summary.",
    )
    source_readiness_parser.add_argument("--output", type=Path, help="Optional JSON output path for the readiness report.")

    executable_readiness_parser = subparsers.add_parser(
        "executable-venue-readiness",
        help="Audit next executable-venue read-only adapter readiness without making live calls.",
    )
    executable_readiness_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "executable_venue_readiness.json",
    )
    executable_readiness_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "executable_venue_readiness.md",
    )

    ibkr_fixture_parser = subparsers.add_parser(
        "inspect-ibkr-forecastex-fixtures",
        help="Inspect local fixture-only IBKR / ForecastEx research schema; no live transport.",
    )
    ibkr_fixture_parser.add_argument(
        "--instruments",
        type=Path,
        default=PROJECT_ROOT / "venues" / "fixtures" / "ibkr_forecastex_instruments_sample.json",
    )
    ibkr_fixture_parser.add_argument(
        "--quotes",
        type=Path,
        default=PROJECT_ROOT / "venues" / "fixtures" / "ibkr_forecastex_quotes_sample.json",
    )
    ibkr_fixture_parser.add_argument(
        "--settlement",
        type=Path,
        default=PROJECT_ROOT / "venues" / "fixtures" / "ibkr_forecastex_settlement_sample.json",
    )
    ibkr_fixture_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "ibkr_forecastex_fixture_inspection.json",
    )
    ibkr_fixture_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "ibkr_forecastex_fixture_inspection.md",
    )

    ibkr_doctor_parser = subparsers.add_parser(
        "ibkr-forecastex-access-doctor",
        help=(
            "Check local IBKR Client Portal Gateway reachability/auth status only. "
            "Public/local read-only diagnostics; no login, auth material, account, order, portfolio, wallet, or signing calls."
        ),
    )
    ibkr_doctor_parser.add_argument("--base-url", default=DEFAULT_IBKR_FORECASTEX_BASE_URL)
    ibkr_doctor_parser.add_argument("--timeout-seconds", type=float, default=5.0)
    ibkr_doctor_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "ibkr_forecastex_access_doctor.json",
    )

    ibkr_fetch_parser = subparsers.add_parser(
        "fetch-ibkr-forecastex-readonly",
        help=(
            "Fetch a local IBKR / ForecastEx read-only diagnostic snapshot from Client Portal Gateway. "
            "No auth material, no account/order/portfolio calls, no wallet/signing, diagnostic output only."
        ),
    )
    ibkr_fetch_parser.add_argument("--base-url", default=DEFAULT_IBKR_FORECASTEX_BASE_URL)
    ibkr_fetch_parser.add_argument("--timeout-seconds", type=float, default=8.0)
    ibkr_fetch_parser.add_argument("--max-contracts", type=int, default=100)
    ibkr_fetch_parser.add_argument(
        "--max-contract-info-requests",
        type=int,
        default=DEFAULT_MAX_CONTRACT_INFO_REQUESTS,
        help="Bound read-only secdef/info contract-detail follow-up requests after strikes discovery.",
    )
    ibkr_fetch_parser.add_argument(
        "--max-followup-errors",
        type=int,
        default=DEFAULT_MAX_FOLLOWUP_ERRORS,
        help="Stop read-only ForecastEx follow-up discovery after this many gateway request failures.",
    )
    ibkr_fetch_parser.add_argument(
        "--search-terms",
        default=None,
        help="Comma-separated read-only secdef search terms, for example ForecastEx,FORECASTX,event contract,BTC.",
    )
    ibkr_fetch_parser.add_argument(
        "--forecastx-doc-seed",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Try documented ForecastEx seed symbols first, with FF first. Use --no-forecastx-doc-seed to disable.",
    )
    ibkr_fetch_parser.add_argument(
        "--forecastx-months",
        default=None,
        help=(
            "Explicit bounded ForecastEx options-style months to inspect, for example JUN26. "
            "When omitted, the fetcher records forecastx_month_required and does not guess month ranges."
        ),
    )
    ibkr_fetch_parser.add_argument(
        "--seed-conids",
        type=Path,
        help="Optional text file of operator-provided conids, one per line. Uses only read-only secdef info and market-data snapshot endpoints.",
    )
    ibkr_fetch_parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "reports" / "manual_snapshots" / "ibkr_forecastex",
    )
    ibkr_fetch_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "ibkr_forecastex_normalized_draft.json",
    )
    ibkr_fetch_parser.add_argument(
        "--discovery-json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "ibkr_forecastex_discovery_candidates.json",
    )
    ibkr_fetch_parser.add_argument(
        "--discovery-markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "ibkr_forecastex_discovery_candidates.md",
    )

    ibkr_pipeline_parser = subparsers.add_parser(
        "ibkr-forecastex-readonly-pipeline",
        help=(
            "Run the safe IBKR / ForecastEx read-only diagnostic pipeline after manual Gateway login. "
            "No browser, login automation, account, portfolio, position, balance, or order calls."
        ),
    )
    ibkr_pipeline_parser.add_argument("--base-url", default=DEFAULT_IBKR_FORECASTEX_BASE_URL)
    ibkr_pipeline_parser.add_argument("--wait-for-auth-seconds", type=int, default=0)
    ibkr_pipeline_parser.add_argument("--poll-seconds", type=float, default=10.0)
    ibkr_pipeline_parser.add_argument("--search-terms", default="FF")
    ibkr_pipeline_parser.add_argument(
        "--forecastx-months",
        required=True,
        help="Explicit bounded ForecastEx options-style months to inspect, for example JUN26.",
    )
    ibkr_pipeline_parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "reports" / "manual_snapshots" / "ibkr_forecastex",
    )
    ibkr_pipeline_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "ibkr_forecastex_normalized_draft.json",
    )
    ibkr_pipeline_parser.add_argument(
        "--max-contract-info-requests",
        type=int,
        default=DEFAULT_MAX_CONTRACT_INFO_REQUESTS,
    )
    ibkr_pipeline_parser.add_argument("--timeout-seconds", type=float, default=8.0)
    ibkr_pipeline_parser.add_argument(
        "--max-followup-errors",
        type=int,
        default=DEFAULT_MAX_FOLLOWUP_ERRORS,
    )
    ibkr_pipeline_parser.add_argument(
        "--ops-json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "relative_value_ops_status.json",
    )
    ibkr_pipeline_parser.add_argument(
        "--ops-markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "relative_value_ops_status.md",
    )

    ibkr_memo_parser = subparsers.add_parser(
        "validate-ibkr-forecastex-manual-memo",
        help="Validate a manually filled IBKR / ForecastEx FF UI memo. Diagnostic only; no source-registry or evaluator effects.",
    )
    ibkr_memo_parser.add_argument("--memo-json", type=Path, required=True)
    ibkr_memo_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "ibkr_forecastex_manual_ui_memo_validation.json",
    )

    prophetx_fixture_parser = subparsers.add_parser(
        "inspect-prophetx-fixtures",
        help="Inspect local fixture-only ProphetX research schema; no live transport.",
    )
    prophetx_fixture_parser.add_argument(
        "--markets",
        type=Path,
        default=PROJECT_ROOT / "venues" / "fixtures" / "prophetx_markets_sample.json",
    )
    prophetx_fixture_parser.add_argument(
        "--orderbook",
        type=Path,
        default=PROJECT_ROOT / "venues" / "fixtures" / "prophetx_orderbook_sample.json",
    )
    prophetx_fixture_parser.add_argument(
        "--settlement",
        type=Path,
        default=PROJECT_ROOT / "venues" / "fixtures" / "prophetx_settlement_sample.json",
    )
    prophetx_fixture_parser.add_argument(
        "--fees",
        type=Path,
        default=PROJECT_ROOT / "venues" / "fixtures" / "prophetx_fee_sample.json",
    )
    prophetx_fixture_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "prophetx_fixture_inspection.json",
    )
    prophetx_fixture_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "prophetx_fixture_inspection.md",
    )

    source_smoke_parser = subparsers.add_parser(
        "source-smoke",
        help="Run explicit live-read-only source connection smoke tests with key-safe output.",
    )
    source_smoke_parser.add_argument("--max-markets", type=int, default=3)
    source_smoke_parser.add_argument("--timeout-seconds", type=float, default=10.0)
    source_smoke_parser.add_argument("--the-odds-api-sport-key", default="basketball_nba")
    source_smoke_parser.add_argument("--output", type=Path, help="Optional JSON output path for the smoke report.")

    inventory_parser = subparsers.add_parser(
        "discover-live-source-inventory",
        help="Explicitly fetch public Kalshi series and Polymarket tag inventories for human review.",
    )
    inventory_parser.add_argument("--limit", type=int, default=500)
    inventory_parser.add_argument("--timeout-seconds", type=float, default=10.0)
    inventory_parser.add_argument("--json-output", type=Path, default=PROJECT_ROOT / "reports" / "live_source_inventory.json")
    inventory_parser.add_argument("--markdown-output", type=Path, default=PROJECT_ROOT / "reports" / "live_source_inventory.md")

    fetch_live_parser = subparsers.add_parser(
        "fetch-live-readonly",
        help="Explicitly fetch reviewed live read-only sources and save sanitized snapshots plus a manifest.",
    )
    fetch_live_parser.add_argument("--sources", default="kalshi,polymarket")
    fetch_live_parser.add_argument("--max-markets", type=int, default=25)
    fetch_live_parser.add_argument("--timeout-seconds", type=float, default=10.0)
    fetch_live_parser.add_argument("--the-odds-api-sport-key", default="basketball_nba")
    fetch_live_parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "reports" / "live_readonly")

    overlap_parser = subparsers.add_parser(
        "fetch-live-overlap-universe",
        help="Explicitly fetch Kalshi/Polymarket read-only snapshots for one overlapping research universe.",
    )
    overlap_parser.add_argument(
        "--category",
        choices=["sports", "politics", "macro", "crypto", "companies", "ai", "weather", "entertainment", "all"],
        default="all",
    )
    overlap_parser.add_argument("--query", help="Optional free-text local retention query, for example NBA.")
    overlap_parser.add_argument("--max-markets", type=int, default=500)
    overlap_parser.add_argument("--timeout-seconds", type=float, default=10.0)
    overlap_parser.add_argument("--kalshi-max-pages", type=int, default=2)
    overlap_parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "reports" / "live_readonly")
    overlap_parser.add_argument("--report-dir", type=Path, default=PROJECT_ROOT / "reports")
    overlap_parser.add_argument(
        "--label",
        help="Optional safe label for non-overwriting overlap report copies. Defaults to category/query.",
    )

    overlap_sweep_parser = subparsers.add_parser(
        "sweep-live-overlap-universe",
        help="Explicitly sweep non-sports Kalshi/Polymarket live overlap universes for research-only diagnostics.",
    )
    overlap_sweep_parser.add_argument(
        "--categories",
        default="macro,politics,crypto,companies,ai,weather",
        help="Comma-separated categories to sweep, for example macro,politics,crypto,companies,ai,weather.",
    )
    overlap_sweep_parser.add_argument("--max-markets", type=int, default=500)
    overlap_sweep_parser.add_argument("--timeout-seconds", type=float, default=10.0)
    overlap_sweep_parser.add_argument("--kalshi-max-pages", type=int, default=2)
    overlap_sweep_parser.add_argument("--sleep-seconds", type=float, default=0.0)
    overlap_sweep_parser.add_argument("--snapshot-dir", type=Path, default=PROJECT_ROOT / "reports" / "live_readonly")
    overlap_sweep_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "live_overlap_sweep.json",
    )
    overlap_sweep_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "live_overlap_sweep.md",
    )

    inspect_live_parser = subparsers.add_parser(
        "inspect-live-snapshots",
        help="Inspect saved live-read-only snapshots for shape, safety, and future matching blockers.",
    )
    inspect_live_parser.add_argument("--snapshot-dir", type=Path, default=PROJECT_ROOT / "reports" / "live_readonly")
    inspect_live_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "live_snapshot_inspection.json",
    )
    inspect_live_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "live_snapshot_inspection.md",
    )

    match_live_readonly_parser = subparsers.add_parser(
        "match-live-readonly-snapshots",
        help="Match saved Kalshi vs Polymarket live-read-only snapshots as research-only WATCH/MANUAL_REVIEW pairs.",
    )
    match_live_readonly_parser.add_argument("--snapshot-dir", type=Path, default=PROJECT_ROOT / "reports" / "live_readonly")
    match_live_readonly_parser.add_argument("--min-similarity", type=float, default=0.68)
    match_live_readonly_parser.add_argument("--max-snapshot-age-hours", type=float, default=24.0)
    match_live_readonly_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "live_readonly_match_report.json",
    )
    match_live_readonly_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "live_readonly_match_report.md",
    )
    match_live_readonly_parser.add_argument(
        "--include-reference-context",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Attach The Odds API reference snapshot as reference_context only when present and valid.",
    )

    enrich_live_candidates_parser = subparsers.add_parser(
        "enrich-live-match-candidates",
        help="Read-only depth/timestamp/fee-status enrichment for current WATCH/MANUAL_REVIEW match pairs only.",
    )
    enrich_live_candidates_parser.add_argument(
        "--match-report",
        type=Path,
        default=PROJECT_ROOT / "reports" / "live_readonly_match_report.json",
    )
    enrich_live_candidates_parser.add_argument("--snapshot-dir", type=Path, default=PROJECT_ROOT / "reports" / "live_readonly")
    enrich_live_candidates_parser.add_argument("--timeout-seconds", type=float, default=10.0)
    enrich_live_candidates_parser.add_argument("--max-snapshot-age-hours", type=float, default=24.0)
    enrich_live_candidates_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "live_match_candidate_enrichment.json",
    )
    enrich_live_candidates_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "live_match_candidate_enrichment.md",
    )

    diagnose_live_parser = subparsers.add_parser(
        "diagnose-live-matching",
        help="Explain why saved live-read-only snapshots did or did not clear conservative matching.",
    )
    diagnose_live_parser.add_argument("--snapshot-dir", type=Path, default=PROJECT_ROOT / "reports" / "live_readonly")
    diagnose_live_parser.add_argument("--top-limit", type=int, default=20)
    diagnose_live_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "live_matching_diagnostics.json",
    )
    diagnose_live_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "live_matching_diagnostics.md",
    )

    non_sports_near_miss_parser = subparsers.add_parser(
        "diagnose-non-sports-near-misses",
        help="Explain below-threshold non-sports Kalshi/Polymarket near misses without changing matcher gates.",
    )
    non_sports_near_miss_parser.add_argument(
        "--sweep-report",
        type=Path,
        default=PROJECT_ROOT / "reports" / "live_overlap_sweep.json",
    )
    non_sports_near_miss_parser.add_argument("--min-similarity", type=float, default=0.68)
    non_sports_near_miss_parser.add_argument("--top-limit", type=int, default=8)
    non_sports_near_miss_parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "non_sports_near_miss_diagnostics.json",
    )
    non_sports_near_miss_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "non_sports_near_miss_diagnostics.md",
    )

    parser.add_argument("--fixture-dir", type=Path, default=PROJECT_ROOT / "venues" / "fixtures")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "reports")
    parser.add_argument("--include-ignore", action="store_true", help="Include ignored pairs in reports.")
    args = parser.parse_args(argv)

    if args.command == "fetch-polymarket":
        return fetch_polymarket(
            args.limit,
            args.output,
            args.timeout_seconds,
            tag_slug=args.tag_slug,
            tag_id=args.tag_id,
            include_closed=args.include_closed,
            include_not_accepting_orders=args.include_not_accepting_orders,
            include_past_end_date=args.include_past_end_date,
        )
    if args.command == "discover-polymarket-crypto-markets":
        queries_from_file: list[str] = []
        if getattr(args, "queries_file", None) is not None:
            queries_from_file = [
                line.strip()
                for line in args.queries_file.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
        report = write_polymarket_crypto_discovery_files(
            output_dir=args.output_dir,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
            limit=args.limit,
            include_books=args.include_books,
            timeout_seconds=args.timeout_seconds,
            max_pages=args.max_pages,
            targeted_query=getattr(args, "query", None),
            targeted_queries=queries_from_file or None,
            targeted_asset=getattr(args, "asset", None),
            targeted_target_date=getattr(args, "target_date", None),
        )
        summary = report["summary"]
        targeted = report.get("targeted_filter") or {}
        print(
            "polymarket_crypto_discovery_status=OK "
            f"endpoints_attempted={summary['endpoints_attempted']} "
            f"raw_files_written={summary['raw_files_written']} "
            f"candidate_events={summary['candidate_events']} "
            f"candidate_markets={summary['candidate_markets']} "
            f"threshold_like_candidates={summary['threshold_like_candidates']} "
            f"token_ids_available={summary['token_ids_available']} "
            f"books_saved={summary['books_saved']} "
            f"book_tokens_saved={summary.get('book_token_ids_saved_count', 0)} "
            f"book_tokens_failed={summary.get('book_token_ids_failed_count', 0)} "
            f"candidates_with_any_book={summary.get('candidates_with_any_book_attached_count', 0)} "
            f"candidates_with_all_books={summary.get('candidates_with_all_books_attached_count', 0)} "
            f"targeted_filter_active={str(bool(targeted.get('active'))).lower()} "
            f"targeted_filter_mode={targeted.get('targeted_filter_mode') or 'off'} "
            f"targeted_rows_found={summary.get('targeted_rows_found', 0)} "
            f"targeted_point_in_time_rows={summary.get('targeted_point_in_time_rows', 0)} "
            f"targeted_deadline_or_range_hit_rows={summary.get('targeted_deadline_or_range_hit_rows', 0)} "
            f"targeted_typed_rows={summary.get('targeted_typed_rows', 0)} "
            f"targeted_rows_with_token_ids={summary.get('targeted_rows_with_token_ids', 0)} "
            f"warnings={summary['warning_count']} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "normalize-polymarket-crypto-discovery":
        report = write_polymarket_crypto_discovery_normalization_files(
            discovery_path=args.discovery,
            output_dir=args.output_dir,
            json_output=args.json_output,
        )
        summary = report["summary"]
        print(
            "polymarket_crypto_discovery_normalized_status=OK "
            f"discovery_candidates_read={summary['discovery_candidates_read']} "
            f"normalized_fixtures_written={summary['normalized_fixtures_written']} "
            f"markets_expanded={summary['markets_expanded']} "
            f"point_in_time={summary['point_in_time_count']} "
            f"monthly_extreme={summary['monthly_extreme_count']} "
            f"range_hit={summary['range_hit_count']} "
            f"token_ids_carried={summary['token_ids_carried']} "
            f"book_files_attached={summary.get('book_files_attached_total', 0)} "
            f"fixtures_with_any_book={summary.get('fixtures_with_any_book_attached', 0)} "
            f"fixtures_with_all_books={summary.get('fixtures_with_all_tokens_with_books', 0)} "
            f"json={args.json_output}"
        )
        return 0
    if args.command == "discover-polymarket-market-universe":
        report = write_polymarket_market_universe_files(
            output_dir=args.output_dir,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
            limit=args.limit,
            include_books=args.include_books,
            timeout_seconds=args.timeout_seconds,
            max_pages=args.max_pages,
        )
        summary = report["summary"]
        print(
            "polymarket_market_taxonomy_status=OK "
            f"endpoints_attempted={summary['endpoints_attempted']} "
            f"raw_files_written={summary['raw_files_written']} "
            f"total_events={summary['total_events']} "
            f"total_markets={summary['total_markets']} "
            f"typed_key_complete={summary['typed_key_complete_count']} "
            f"partial={summary['partial_count']} "
            f"unknown={summary['unknown_count']} "
            f"books_saved={summary.get('books_saved', 0)} "
            f"warnings={summary['warning_count']} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "fetch-kalshi":
        return fetch_kalshi(
            args.limit,
            args.output,
            args.timeout_seconds,
            series_ticker=args.series_ticker,
            event_ticker=args.event_ticker,
            cursor=args.cursor,
            max_pages=args.max_pages,
            include_closed=args.include_closed,
            include_past_close_time=args.include_past_close_time,
        )
    if args.command == "fetch-kalshi-crypto-readonly":
        return fetch_kalshi_crypto_readonly(
            assets=args.asset,
            output=args.output,
            limit=args.limit,
            max_pages=args.max_pages,
            timeout_seconds=args.timeout_seconds,
            include_orderbooks=args.include_orderbooks,
            max_orderbooks=args.max_orderbooks,
        )
    if args.command == "fetch-the-odds-api":
        return fetch_the_odds_api(
            sport_key=args.sport_key,
            regions=args.regions,
            markets=args.markets,
            odds_format=args.odds_format,
            api_key=args.api_key,
            api_key_env=args.api_key_env,
            timeout_seconds=args.timeout_seconds,
            stale_after_seconds=args.stale_after_seconds,
            output=args.output,
        )
    if args.command in {"fetch-sx-bet-readonly", "fetch-sx-bet-public-snapshot"}:
        return fetch_sx_bet_readonly(
            max_markets=args.max_markets,
            timeout_seconds=args.timeout_seconds,
            sport=args.sport,
            league=args.league,
            query=args.query,
            label=args.label,
            output=args.output,
            output_dir=args.output_dir,
            json_output=args.json_output,
            coverage_output=args.coverage_output,
        )
    if args.command == "compare-sx-bet-reference":
        return compare_sx_bet_reference(
            sx_bet_snapshot=args.sx_bet_snapshot,
            kalshi_snapshot=args.kalshi_snapshot,
            polymarket_snapshot=args.polymarket_snapshot,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
            label=args.label,
            top_limit=args.top_limit,
        )
    if args.command == "match-live-snapshots":
        return match_live_snapshots(
            args.polymarket,
            args.kalshi,
            args.output,
            min_similarity=0.68,
            max_snapshot_age_hours=args.max_snapshot_age_hours,
            reference_snapshots=args.reference_snapshot,
        )
    if args.command == "enrich-orderbooks":
        return enrich_orderbooks(
            args.snapshot,
            args.venue,
            args.output,
            timeout_seconds=args.timeout_seconds,
            max_snapshot_age_hours=args.max_snapshot_age_hours,
            preserve_raw_orderbook=args.preserve_raw_orderbook,
        )
    if args.command == "enrich-kalshi-orderbooks":
        return enrich_orderbooks(
            args.snapshot,
            "kalshi",
            args.output,
            timeout_seconds=args.timeout_seconds,
            max_snapshot_age_hours=args.max_snapshot_age_hours,
            preserve_raw_orderbook=args.preserve_raw_orderbook,
            max_markets=args.max_markets,
            progress_every=args.progress_every,
            retry_failed_once=args.retry_failed_once,
            failure_sample_limit=args.failure_sample_limit,
        )
    if args.command == "evaluate-paper-candidates":
        return evaluate_paper_candidates(
            args.pairs,
            args.polymarket_enriched,
            args.kalshi_enriched,
            args.output,
            max_quote_age_seconds=args.max_quote_age_seconds,
            max_settlement_delta_seconds=args.max_settlement_delta_seconds,
            min_top_of_book_size=args.min_top_of_book_size,
            min_net_gap=args.min_net_gap,
            accept_unit_mismatch=args.accept_unit_mismatch,
            trusted_settlement_normalizations=frozenset(args.trust_settlement_normalization or []),
        )
    if args.command == "same-payoff-board":
        return same_payoff_board(
            pairs=args.pairs,
            polymarket_enriched=args.polymarket_enriched,
            kalshi_enriched=args.kalshi_enriched,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
    if args.command == "diagnose-mlb-world-series-board-blockers":
        return diagnose_mlb_world_series_board_blockers(
            board=args.board,
            pairs=args.pairs,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
    if args.command == "diagnose-mlb-world-series-execution-blockers":
        return diagnose_mlb_world_series_execution_blockers(
            pairs=args.pairs,
            polymarket_enriched=args.polymarket_enriched,
            kalshi_enriched=args.kalshi_enriched,
            evaluator=args.evaluator,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
    if args.command == "diagnose-mlb-world-series-evaluator-blockers":
        return diagnose_mlb_world_series_evaluator_blockers(
            evaluator=args.evaluator,
            pairs=args.pairs,
            polymarket_enriched=args.polymarket_enriched,
            kalshi_enriched=args.kalshi_enriched,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
    if args.command == "attach-same-payoff-evidence":
        return attach_same_payoff_evidence(pairs=args.pairs, board=args.board, output=args.output)
    if args.command == "audit-same-scope-mlb-candidates":
        return audit_same_scope_mlb_candidates(
            pairs=args.pairs,
            polymarket_enriched=args.polymarket_enriched,
            kalshi_enriched=args.kalshi_enriched,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
            board_json_output=args.board_json_output,
            board_markdown_output=args.board_markdown_output,
            derived_pairs_output=args.derived_pairs_output,
            evaluator_output=args.evaluator_output,
            max_quote_age_seconds=args.max_quote_age_seconds,
            max_settlement_delta_seconds=args.max_settlement_delta_seconds,
            min_top_of_book_size=args.min_top_of_book_size,
            min_net_gap=args.min_net_gap,
            accept_unit_mismatch=args.accept_unit_mismatch,
        )
    if args.command == "diagnose-mlb-same-scope-targeting":
        return diagnose_mlb_same_scope_targeting(
            polymarket_snapshot=args.polymarket_snapshot,
            kalshi_snapshot=args.kalshi_snapshot,
            pairs=args.pairs,
            audit=args.audit,
            scope=args.scope,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
    if args.command == "build-mlb-world-series-pairs":
        return build_mlb_world_series_pairs(
            polymarket_snapshot=args.polymarket_snapshot,
            kalshi_snapshot=args.kalshi_snapshot,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
            match_report=args.match_report,
        )
    if args.command == "build-nhl-stanley-cup-pairs":
        return build_nhl_stanley_cup_pairs(
            polymarket_snapshot=args.polymarket_snapshot,
            kalshi_snapshot=args.kalshi_snapshot,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
    if args.command == "run-mlb-world-series-paper-check":
        return run_mlb_world_series_paper_check(
            polymarket_snapshot=args.polymarket_snapshot,
            kalshi_snapshot=args.kalshi_snapshot,
            pairs=args.pairs,
            timeout_seconds=args.timeout_seconds,
            max_snapshot_age_hours=args.max_snapshot_age_hours,
            max_quote_age_seconds=args.max_quote_age_seconds,
            max_settlement_delta_seconds=args.max_settlement_delta_seconds,
            min_top_of_book_size=args.min_top_of_book_size,
            min_net_gap=args.min_net_gap,
            accept_unit_mismatch=args.accept_unit_mismatch,
            trusted_settlement_normalizations=frozenset(args.trust_settlement_normalization or []),
            polymarket_enriched_output=args.polymarket_enriched_output,
            kalshi_enriched_output=args.kalshi_enriched_output,
            rebuild_pairs_from_snapshots=args.rebuild_pairs_from_snapshots,
            rebuilt_pairs_json_output=args.rebuilt_pairs_json_output,
            rebuilt_pairs_markdown_output=args.rebuilt_pairs_markdown_output,
            board_json_output=args.board_json_output,
            board_markdown_output=args.board_markdown_output,
            derived_pairs_output=args.derived_pairs_output,
            evaluator_output=args.evaluator_output,
            summary_json_output=args.summary_json_output,
            summary_markdown_output=args.summary_markdown_output,
            settlement_audit_json_output=args.settlement_audit_json_output,
            settlement_audit_markdown_output=args.settlement_audit_markdown_output,
        )
    if args.command == "run-nba-championship-paper-check":
        return run_nba_championship_paper_check(
            polymarket_snapshot=args.polymarket_snapshot,
            kalshi_snapshot=args.kalshi_snapshot,
            pairs=args.pairs,
            timeout_seconds=args.timeout_seconds,
            max_snapshot_age_hours=args.max_snapshot_age_hours,
            max_quote_age_seconds=args.max_quote_age_seconds,
            max_settlement_delta_seconds=args.max_settlement_delta_seconds,
            min_top_of_book_size=args.min_top_of_book_size,
            min_net_gap=args.min_net_gap,
            accept_unit_mismatch=args.accept_unit_mismatch,
            trusted_settlement_normalizations=frozenset(args.trust_settlement_normalization),
            polymarket_enriched_output=args.polymarket_enriched_output,
            kalshi_enriched_output=args.kalshi_enriched_output,
            board_json_output=args.board_json_output,
            board_markdown_output=args.board_markdown_output,
            derived_pairs_output=args.derived_pairs_output,
            evaluator_output=args.evaluator_output,
            summary_json_output=args.summary_json_output,
            summary_markdown_output=args.summary_markdown_output,
        )
    if args.command == "discover-exact-paper-candidate-universes":
        return discover_exact_paper_candidate_universes(
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
    if args.command == "detect-structural-baskets":
        report = build_structural_basket_review_report_files(
            snapshot_paths=args.snapshots,
            manifest_path=args.manifest,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
            max_quote_age_seconds=args.max_quote_age_seconds,
            min_depth=args.min_depth,
        )
        summary = report["summary"]
        print(
            "structural_basket_review_status=OK "
            f"groups={summary['evaluated_group_count']} "
            f"review={summary['review_count']} "
            f"stop_for_review={summary['stop_for_review_count']} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        if summary["stop_for_review_count"]:
            print("STOP_FOR_REVIEW structural basket review candidate detected; report only, no orders placed.")
        return 0
    if args.command == "scout-structural-manifest-candidates":
        report = scout_structural_manifest_candidates_file(
            snapshot_path=args.snapshot,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
            max_quote_age_seconds=args.max_quote_age_seconds,
            min_depth=args.min_depth,
        )
        summary = report["summary"]
        print(
            "structural_manifest_candidate_scout_status=OK "
            f"groups={summary['groups_discovered']} "
            f"manifest_review_candidates={summary['manifest_review_candidate_count']} "
            f"blocked={summary['blocked_count']} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "structural-basket-parlay-scout":
        report = write_structural_basket_parlay_scout_files(
            input_dir=args.input_dir,
            graph_hints_json=args.graph_hints_json,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        counts = report.get("summary_counts") or {}
        top_blocker = (report.get("top_blockers") or [{}])[0].get("blocker")
        print(
            "structural_basket_parlay_scout_status=OK "
            "diagnostic_only=true saved_files_only=true execution_enabled=false "
            f"rows={counts.get('rows', 0)} "
            f"structural_review_rows={counts.get('structural_basket_review_rows', 0)} "
            f"cdna_fill_first_review_rows={counts.get('cdna_fill_first_review_rows', 0)} "
            f"manual_review_rows={counts.get('manual_review_rows', 0)} "
            f"watch_rows={counts.get('watch_rows', 0)} "
            f"ignore_blocked_rows={counts.get('ignore_blocked_rows', 0)} "
            f"strict_paper_candidate_rows={counts.get('strict_paper_candidate_rows', 0)} "
            f"operator_paper_candidate_rows={counts.get('operator_paper_candidate_rows', 0)} "
            f"cdna_fill_first_paper_candidate_rows={counts.get('cdna_fill_first_paper_candidate_rows', 0)} "
            f"total_paper_candidate_rows={counts.get('total_paper_candidate_rows', 0)} "
            f"exact_ready_rows=0 "
            f"top_blocker={top_blocker} json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "audit-kalshi-event-metadata":
        report = audit_kalshi_event_metadata_files(
            metadata_paths=args.metadata_paths,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        summary = report["summary"]
        print(
            "kalshi_event_metadata_audit_status=OK "
            f"files={summary['metadata_files']} "
            f"events={summary['events_discovered']} "
            f"trusted={summary['events_trusted_for_completeness']} "
            f"blocked={summary['events_blocked']} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "join-kalshi-event-metadata":
        result = join_kalshi_event_metadata_files(
            snapshot_path=args.snapshot,
            metadata_paths=args.metadata_paths,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
            enriched_snapshot_output=args.enriched_snapshot_output,
        )
        summary = result["report"]["summary"]
        print(
            "kalshi_event_metadata_join_status=OK "
            f"events={summary['events_discovered']} "
            f"matched={summary['events_matched_to_snapshot']} "
            f"trusted={summary['events_trusted_after_join']} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "simulate-paper-fills":
        journal = simulate_paper_fill_journal_files(
            input_path=args.input,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
            desired_quantity=args.desired_quantity,
            max_quote_age_seconds=args.max_quote_age_seconds,
            slippage_budget_cents_per_leg=args.slippage_budget_cents_per_leg,
        )
        summary = journal["summary"]
        print(
            "paper_fill_journal_status=OK "
            f"rows={summary['input_row_count']} "
            f"simulated={summary['simulated_fill_count']} "
            f"blocked={summary['blocked_count']} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "run-structural-basket-dry-run":
        report = run_structural_basket_dry_run_files(
            snapshot_path=args.snapshot,
            metadata_paths=args.metadata_paths,
            summary_json_output=args.summary_json_output,
            summary_markdown_output=args.summary_markdown_output,
            audit_json_output=args.audit_json_output,
            audit_markdown_output=args.audit_markdown_output,
            join_json_output=args.join_json_output,
            join_markdown_output=args.join_markdown_output,
            enriched_snapshot_output=args.enriched_snapshot_output,
            structural_json_output=args.structural_json_output,
            structural_markdown_output=args.structural_markdown_output,
            paper_fill_json_output=args.paper_fill_json_output,
            paper_fill_markdown_output=args.paper_fill_markdown_output,
            max_quote_age_seconds=args.max_quote_age_seconds,
            min_depth=args.min_depth,
            desired_quantity=args.desired_quantity,
            slippage_budget_cents_per_leg=args.slippage_budget_cents_per_leg,
            simulate_paper_fills_on_stop_for_review=not args.skip_paper_fill_simulation,
        )
        summary = report["summary"]
        print(
            "structural_basket_dry_run_status=OK "
            f"metadata_events={summary['metadata_events']} "
            f"trusted={summary['trusted_metadata_events']} "
            f"matched={summary['matched_events']} "
            f"enriched_rows={summary['enriched_normalized_market_rows']} "
            f"structural_groups={summary['structural_groups_evaluated']} "
            f"stop_for_review={summary['stop_for_review_count']} "
            f"paper_fill_rows={summary['paper_fill_rows']} "
            f"paper_simulation_skipped={summary['paper_simulation_skipped']} "
            f"reason={summary['paper_simulation_skip_reason']} "
            f"summary_json={args.summary_json_output} summary_markdown={args.summary_markdown_output}"
        )
        if summary["stop_for_review_count"]:
            print(
                "STOP_FOR_REVIEW structural basket review candidate detected; review/report only, "
                "no orders placed and no PAPER_CANDIDATE emitted."
            )
        return 0
    if args.command == "import-kalshi-event-metadata":
        report = import_kalshi_event_metadata_files(
            sources=args.sources,
            destination_dir=args.destination_dir,
            overwrite=args.overwrite,
        )
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        args.markdown_output.write_text(render_metadata_importer_markdown(report), encoding="utf-8")
        summary = report["summary"]
        print(
            "kalshi_event_metadata_import_status=OK "
            f"files_seen={summary['files_seen']} "
            f"files_written={summary['files_written']} "
            f"files_skipped_existing={summary['files_skipped_existing']} "
            f"trusted_events={summary['trusted_event_count']} "
            f"blocked_events={summary['blocked_event_count']} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "hunt-structural-basket-candidates":
        report = hunt_structural_basket_candidates_files(
            snapshots_dir=args.snapshots_dir,
            metadata_dir=args.metadata_dir,
            manifest_dir=args.manifest_dir,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
            manifest_template_output_dir=args.manifest_template_dir,
            write_templates=not args.skip_template_writes,
            max_quote_age_seconds=args.max_quote_age_seconds,
            min_depth=args.min_depth,
            desired_quantity=args.desired_quantity,
            slippage_budget_cents_per_leg=args.slippage_budget_cents_per_leg,
            simulate_paper_fills_on_stop_for_review=not args.skip_paper_fill_simulation,
            top_closest_n=args.top_closest_n,
        )
        summary = report["summary"]
        print(
            "structural_basket_hunt_status=OK "
            f"files_considered={summary['files_considered']} "
            f"snapshots={summary['snapshots_considered']} "
            f"metadata={summary['metadata_files_considered']} "
            f"manifests={summary['manifests_considered']} "
            f"structural_groups={summary['structural_groups_evaluated']} "
            f"stop_for_review={summary['stop_for_review_count']} "
            f"paper_fill_rows={summary['paper_fill_rows']} "
            f"templates_written={summary['manifest_templates_written']} "
            f"paper_candidates={summary['paper_candidate_count']} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        if summary["stop_for_review_count"]:
            print(
                "STOP_FOR_REVIEW row(s) surfaced; review/report only, no orders placed and "
                "no PAPER_CANDIDATE emitted."
            )
        return 0
    if args.command == "audit-kalshi-native-groups":
        outputs = kalshi_native_group_audit_paths(
            args.snapshot,
            output_dir=PROJECT_ROOT / "reports" / "native_group_audits",
        )
        json_output = args.json_output or outputs["json_output"]
        markdown_output = args.markdown_output or outputs["markdown_output"]
        report = audit_kalshi_native_groups_file(
            snapshot_path=args.snapshot,
            json_output=json_output,
            markdown_output=markdown_output,
        )
        summary = report["summary"]
        print(
            "kalshi_native_groups_audit_status=OK "
            f"groups={summary['groups_discovered']} "
            f"complete={summary['complete_groups']} "
            f"blocked={summary['blocked_groups']} "
            f"candidate_input_rows={summary['candidate_input_row_count']} "
            f"json={json_output} markdown={markdown_output}"
        )
        return 0
    if args.command == "audit-kalshi-kxmlb26-event-evidence":
        report = write_kalshi_event_evidence_summary_files(
            input_dir=args.input_dir,
            event_ticker=args.event_ticker,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
            max_quote_age_seconds=args.max_quote_age_seconds,
        )
        summary = report["summary"]
        print(
            "kalshi_kxmlb26_event_evidence_summary_status=OK "
            f"event_ticker={summary['event_ticker']} "
            f"markets={summary['market_count']} "
            f"explicit_outcome_list={str(summary['explicit_outcome_list_exists']).lower()} "
            f"explicit_completeness={str(summary['explicit_completeness_evidence_exists']).lower()} "
            f"settlement_source={str(summary['settlement_rules_source_evidence_exists']).lower()} "
            f"fresh_orderbook_depth={str(summary['fresh_orderbook_depth_exists']).lower()} "
            f"ready_for_human_manifest_review={str(summary['ready_for_human_manifest_review']).lower()} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "market-graph-diagnostics":
        return market_graph_diagnostics(
            fixture=args.fixture,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
    if args.command == "explain-market-graph-diagnostics":
        return explain_market_graph_diagnostics(
            graph_report=args.graph_report,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
    if args.command == "triage-cross-platform-opportunities":
        report = write_cross_platform_opportunity_triage_files(
            input_dir=args.input_dir,
            graph_hints_path=args.graph_hints_path,
            json_output=args.json_output,
            csv_output=args.csv_output,
            markdown_output=args.markdown_output,
        )
        summary = report["summary"]
        print(
            "cross_platform_opportunity_triage_status=OK "
            f"rows={summary['row_count']} "
            f"paper_candidates={summary['paper_candidate_count']} "
            f"warnings={summary['warning_count']} "
            f"json={args.json_output} csv={args.csv_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "audit-venue-metadata-coverage":
        report = write_venue_metadata_coverage_files(
            input_dir=args.input_dir,
            json_output=args.json_output,
            csv_output=args.csv_output,
            markdown_output=args.markdown_output,
        )
        summary = report["summary"]
        print(
            "venue_metadata_coverage_status=OK "
            f"venues={summary['venue_count']} "
            f"markets={summary['market_count']} "
            f"match_ready={summary['match_ready_count']} "
            f"evaluator_ready={summary['evaluator_ready_count']} "
            f"warnings={summary['warning_count']} "
            f"json={args.json_output} csv={args.csv_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "normalize-market-snapshots":
        outputs = write_normalized_markets_v0_files(
            input_dir=args.input_dir,
            json_output=args.json_output,
            coverage_output=args.coverage_output,
            csv_output=args.csv_output,
            markdown_output=args.markdown_output,
        )
        summary = outputs["coverage"]["summary"]
        print(
            "normalized_markets_v0_status=OK "
            f"venues={summary['venue_count']} "
            f"normalized={summary['normalized_count']} "
            f"identity_ready={summary['fully_identity_ready']} "
            f"settlement_ready={summary['settlement_metadata_ready']} "
            f"quote_depth_ready={summary['quote_depth_ready']} "
            f"fee_ready={summary['fee_metadata_ready']} "
            f"evaluator_metadata_ready={summary['evaluator_metadata_ready']} "
            f"warnings={summary['warning_count']} "
            f"json={args.json_output} coverage={args.coverage_output}"
        )
        return 0
    if args.command == "audit-canonical-convention-registry":
        report = build_canonical_convention_registry_audit(registry_path=args.registry)
        if args.json_output is not None:
            args.json_output.parent.mkdir(parents=True, exist_ok=True)
            args.json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        summary = report["summary"]
        print(
            "canonical_convention_registry_status=OK "
            f"entries={summary['registry_entry_count']} "
            f"valid={summary['valid_entry_count']} "
            f"invalid={summary['invalid_entry_count']} "
            f"warnings={summary['warning_count']} "
            f"registry={args.registry} "
            f"json={args.json_output}"
        )
        return 0
    if args.command == "audit-canonical-registry-coverage":
        report = write_canonical_registry_coverage_files(
            input_dir=args.input_dir,
            registry_path=args.registry,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        summary = report["summary"]
        print(
            "canonical_registry_coverage_status=OK "
            f"scopes={summary['scopes_total']} "
            f"reviewed={summary['scopes_reviewed']} "
            f"unreviewed={summary['scopes_unreviewed']} "
            f"rows_covered={summary['rows_covered_by_reviewed_scopes']} "
            f"rows_uncovered={summary['rows_uncovered']} "
            f"top_scope={summary.get('top_leverage_scope')} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "audit-canonical-registry-expiry":
        report = write_canonical_registry_expiry_audit_files(
            registry_path=args.registry,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
            expiring_soon_days=args.expiring_soon_days,
        )
        summary = report["summary"]
        print(
            "canonical_registry_expiry_audit_status=OK "
            f"entries={summary['registry_entries_total']} "
            f"valid_current={summary['registry_entries_valid_current_review']} "
            f"expiring_soon={summary['registry_entries_expiring_soon']} "
            f"expired={summary['registry_entries_expired']} "
            f"missing_review_until={summary['registry_entries_missing_review_until']} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "plan-pending-registry-entries":
        report = write_pending_registry_entries_plan(
            coverage_path=args.coverage,
            output_dir=args.output_dir,
            json_output=args.json_output,
        )
        summary = report["summary"]
        print(
            "pending_registry_entries_plan_status=OK "
            f"written={summary['pending_files_written']} "
            f"skipped_reviewed={summary['skipped_reviewed_scopes']} "
            f"output_dir={summary['output_dir']} "
            f"json={args.json_output}"
        )
        return 0
    if args.command == "audit-pending-registry-entries-promotion":
        report = audit_pending_registry_entries_for_promotion(
            pending_dir=args.pending_dir,
            registry_path=args.registry,
        )
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        summary = report["summary"]
        print(
            "pending_registry_entries_promotion_audit_status=OK "
            f"files={summary['pending_file_count']} "
            f"ready={summary['ready_to_promote_count']} "
            f"blocked={summary['blocked_count']} "
            f"warnings={summary['warning_count']} "
            f"registry={args.registry} "
            f"json={args.json_output}"
        )
        return 0
    if args.command == "audit-settlement-evidence-burden":
        report = write_settlement_evidence_burden_files(
            input_dir=args.input_dir,
            json_output=args.json_output,
            csv_output=args.csv_output,
            markdown_output=args.markdown_output,
            registry_path=args.registry_path,
            staleness_seconds=args.staleness_seconds,
        )
        summary = report["summary"]
        tier_counts = summary.get("by_review_readiness_tier") or {}
        freshness_counts = summary.get("by_quote_freshness_blocker") or {}
        print(
            "settlement_evidence_burden_status=OK "
            f"markets={summary['market_row_count']} "
            f"unique_markets={summary['unique_market_count']} "
            f"family_typed_review={tier_counts.get('FAMILY_TYPED_REVIEW_READY', 0)} "
            f"settlement_source_review={tier_counts.get('SETTLEMENT_SOURCE_REVIEW_READY', 0)} "
            f"exact_payoff_review={tier_counts.get('EXACT_PAYOFF_REVIEW_READY', 0)} "
            f"execution_evaluation={tier_counts.get('EXECUTION_EVALUATION_READY', 0)} "
            f"stale_quote={freshness_counts.get('stale_quote', 0)} "
            f"missing_quote_captured_at={freshness_counts.get('missing_quote_captured_at', 0)} "
            f"future_quote_captured_at={freshness_counts.get('future_quote_captured_at', 0)} "
            f"warnings={summary['warning_count']} "
            f"json={args.json_output}"
        )
        return 0
    if args.command == "generate-standardized-family-candidates":
        report = write_standardized_family_candidates_files(
            input_dir=args.input_dir,
            burden_report=args.burden_report,
            json_output=args.json_output,
            csv_output=args.csv_output,
            markdown_output=args.markdown_output,
        )
        summary = report["summary"]
        print(
            "standardized_family_candidates_status=OK "
            f"groups={summary['candidate_group_count']} "
            f"pairs={summary['candidate_pair_count']} "
            f"basis_risk_rows={summary.get('basis_risk_row_count', 0)} "
            f"btc_basis_risk_review={summary.get('btc_basis_risk_review_count', 0)} "
            f"cross_venue_groups={summary['cross_venue_candidate_group_count']} "
            f"manual_registry_review_ready={summary['manual_registry_review_ready_count']} "
            f"typed_key_review_ready={summary['review_typed_key_match_ready_count']} "
            f"paper_candidates={summary['paper_candidate_count']} "
            f"warnings={summary['warning_count']} "
            f"json={args.json_output} csv={args.csv_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "plan-family-graduation":
        report = write_family_graduation_files(
            input_dir=args.input_dir,
            family=args.family,
            registry_path=args.registry_path,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        summary = report["summary"]
        print(
            "family_graduation_status=OK "
            f"family={report['family']} "
            f"rows={summary['candidate_row_count']} "
            f"typed_ready={summary['family_typed_ready_count']} "
            f"registry_proposals={summary['registry_proposal_count']} "
            f"existing_registry_matches={summary['existing_reviewed_registry_match_count']} "
            f"projected_exact_if_reviewed={summary['projected_exact_review_if_registry_reviewed_count']} "
            f"projected_execution_ready={summary['projected_execution_ready_count']} "
            f"paper_candidates={summary['paper_candidate_count']} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "relative-value-ops-status":
        report = write_relative_value_ops_status_files(
            input_dir=args.input_dir,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        summary = report["summary"]
        tiers = summary.get("review_readiness_tier_counts") or {}
        next_action = report.get("highest_priority_next_action") or {}
        print(
            "relative_value_ops_status=OK "
            f"unique_markets={summary['unique_market_count']} "
            f"venues={summary['venue_count']} "
            f"families={summary['family_count']} "
            f"discovery={tiers.get('DISCOVERY_READY', 0)} "
            f"family_typed={tiers.get('FAMILY_TYPED_REVIEW_READY', 0)} "
            f"source_review={tiers.get('SETTLEMENT_SOURCE_REVIEW_READY', 0)} "
            f"exact_review={tiers.get('EXACT_PAYOFF_REVIEW_READY', 0)} "
            f"execution_ready={tiers.get('EXECUTION_EVALUATION_READY', 0)} "
            f"next_action={next_action.get('action')} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "polymarket-taxonomy-shape-scout":
        report = write_polymarket_taxonomy_shape_scout_files(
            input_dir=args.input_dir,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        summary = report.get("summary") or {}
        top_blockers = summary.get("top_blockers") or []
        top_blocker_str = ",".join(
            f"{item.get('blocker')}:{item.get('count')}" for item in top_blockers[:3]
        ) or "none"
        print(
            "polymarket_taxonomy_shape_scout=OK diagnostic_only=true "
            f"total_rows={summary.get('total_rows', 0)} "
            f"point_in_time_candidates={summary.get('point_in_time_candidates', 0)} "
            f"deadline_or_range_hit_blocked={summary.get('deadline_or_range_hit_blocked', 0)} "
            f"deadline_touch_phrase_rows={summary.get('deadline_touch_phrase_rows', 0)} "
            f"deadline_touch_phrase_reclassified_rows={summary.get('deadline_touch_phrase_reclassified_rows', 0)} "
            f"clob_book_attached={summary.get('clob_book_attached', 0)} "
            f"typed_key_complete={summary.get('typed_key_complete', 0)} "
            f"exact_ready_rows=0 paper_candidate_rows=0 "
            f"top_blockers={top_blocker_str} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "refresh-polymarket-clob-for-taxonomy-candidates":
        shape_filter = None if args.shape_filter == "all" else args.shape_filter
        bundle = write_polymarket_clob_taxonomy_refresh_files(
            taxonomy_json=args.taxonomy_json,
            output_dir=args.output_dir,
            json_output=args.json_output,
            enriched_output=args.enriched_output,
            markdown_output=args.markdown_output,
            max_candidates=args.max_candidates,
            shape_filter=shape_filter,
            min_score=args.min_score,
            include_deadline_range=args.include_deadline_range,
            timeout_seconds=args.timeout_seconds,
        )
        summary = bundle["report"].get("summary") or {}
        top_remaining = summary.get("top_remaining_blockers") or []
        top_blocker_str = ",".join(
            f"{item.get('blocker')}:{item.get('count')}" for item in top_remaining[:3]
        ) or "none"
        print(
            "polymarket_clob_taxonomy_refresh=OK diagnostic_only=true "
            f"shape_filter={shape_filter} min_score={args.min_score} "
            f"candidates_selected={summary.get('candidates_selected', 0)} "
            f"books_requested={summary.get('books_requested', 0)} "
            f"books_saved={summary.get('books_saved', 0)} "
            f"rows_enriched={summary.get('rows_enriched', 0)} "
            f"rows_with_bid={summary.get('rows_with_bid', 0)} "
            f"rows_with_ask={summary.get('rows_with_ask', 0)} "
            f"rows_with_bid_ask={summary.get('rows_with_bid_ask', 0)} "
            f"rows_with_bid_ask_size={summary.get('rows_with_bid_ask_size', 0)} "
            f"rows_with_timestamp={summary.get('rows_with_timestamp', 0)} "
            f"still_missing_clob={summary.get('still_missing_clob', 0)} "
            f"still_stale_or_missing_quote={summary.get('still_stale_or_missing_quote', 0)} "
            f"exact_ready_rows=0 paper_candidate_rows=0 "
            f"top_remaining_blockers={top_blocker_str} "
            f"json={args.json_output} enriched={args.enriched_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "cdna-crypto-basis-risk-scout":
        report = write_cdna_crypto_basis_risk_scout_files(
            input_fixture=args.input,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
            peer_input_dir=args.peer_input_dir,
        )
        summary = report.get("summary") or {}
        top_blockers = summary.get("top_blockers") or []
        top_blocker_str = ",".join(
            f"{item.get('blocker')}:{item.get('count')}" for item in top_blockers[:3]
        ) or "none"
        print(
            "cdna_crypto_basis_risk_scout=OK diagnostic_only=true "
            f"cdna_rows={summary.get('cdna_rows', 0)} "
            f"btc_rows={summary.get('cdna_btc_rows', 0)} "
            f"eth_rows={summary.get('cdna_eth_rows', 0)} "
            f"point_in_time_rows={summary.get('point_in_time_rows', 0)} "
            f"deadline_or_range_hit_rows={summary.get('deadline_or_range_hit_rows', 0)} "
            f"ambiguous_rows={summary.get('ambiguous_rows', 0)} "
            f"scout_rows={summary.get('scout_row_count', 0)} "
            f"exact_ready_rows=0 paper_candidate_rows=0 "
            f"top_blockers={top_blocker_str} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "cdna-fill-first-scout":
        report = write_cdna_fill_first_scout_files(
            cdna_evidence=args.cdna_evidence,
            partner_evidence=args.partner_evidence,
            partner_platform=args.partner_platform,
            market_family=args.market_family,
            league=args.league,
            season=args.season,
            operator_accept_display_price_risk=args.operator_accept_display_price_risk,
            cdna_operator_size_cap=args.cdna_operator_size_cap,
            max_partner_hedge_slippage=args.max_partner_hedge_slippage,
            max_quote_age_seconds=args.max_quote_age_seconds,
            fill_log=args.fill_log,
            operator_risk_mode=args.operator_risk_mode,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        counts = report.get("summary_counts") or {}
        top_blocker = (report.get("top_blockers") or [{}])[0].get("blocker")
        print(
            "cdna_fill_first_scout_status=OK "
            "diagnostic_only=true saved_files_only=true execution_enabled=false "
            f"partner_platform={report.get('partner_platform')} "
            f"rows={counts.get('rows', 0)} "
            f"fill_first_review_rows={counts.get('cdna_fill_first_review_rows', 0)} "
            f"display_review_rows={counts.get('cdna_display_price_operator_review_rows', 0)} "
            f"fill_confirmed_hedge_required_rows={counts.get('cdna_fill_confirmed_hedge_required_rows', 0)} "
            f"hedged_complete_rows={counts.get('cdna_hedged_complete_rows', 0)} "
            f"watch_rows={counts.get('watch_rows', 0)} "
            f"ignore_blocked_rows={counts.get('ignore_blocked_rows', 0)} "
            f"exact_ready_rows=0 standard_paper_candidate_rows=0 "
            f"top_blocker={top_blocker} json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "cdna-record-fill":
        report = record_cdna_fill_file(
            fill_log=args.fill_log,
            event_key=args.event_key,
            market_family=args.market_family,
            team=args.team,
            side=args.side,
            contract_id=args.contract_id,
            symbol=args.symbol,
            requested_quantity=args.requested_quantity,
            filled_quantity=args.filled_quantity,
            filled_price=args.filled_price,
            fee_per_contract=args.fee_per_contract,
            filled_at=args.filled_at,
            source_note=args.source_note,
            time_to_fill_seconds=args.time_to_fill_seconds,
        )
        status = "OK" if report.get("record_written") else "FAILED"
        errors = ",".join(report.get("validation_errors") or [])
        print(
            f"cdna_record_fill_status={status} "
            "diagnostic_only=true saved_files_only=true execution_enabled=false "
            f"record_written={str(bool(report.get('record_written'))).lower()} "
            f"records_count={report.get('records_count', 0)} "
            f"validation_errors={errors or 'none'} fill_log={args.fill_log}"
        )
        return 0 if report.get("record_written") else 1
    if args.command == "polymarket-point-in-time-typed-key-audit":
        report = write_polymarket_point_in_time_typed_key_audit_files(
            taxonomy_json=args.taxonomy_json,
            enriched_json=args.enriched_json,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        summary = report.get("summary") or {}
        top_blockers = summary.get("top_blockers") or []
        top_blocker_str = ",".join(
            f"{item.get('blocker')}:{item.get('count')}" for item in top_blockers[:3]
        ) or "none"
        print(
            "polymarket_point_in_time_typed_key_audit=OK diagnostic_only=true "
            f"point_in_time_rows_audited={summary.get('point_in_time_rows_audited', 0)} "
            f"excluded_fake_point_in_time_rows={summary.get('excluded_fake_point_in_time_rows', 0)} "
            f"typed_complete_rows={summary.get('typed_complete_rows', 0)} "
            f"targeted_clob_refresh_candidate_rows={summary.get('targeted_clob_refresh_candidate_rows', 0)} "
            f"rows_with_clob_attached={summary.get('rows_with_clob_attached', 0)} "
            f"exact_ready_rows=0 paper_candidate_rows=0 "
            f"top_blockers={top_blocker_str} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "kalshi-crypto-typed-key-audit":
        report = write_kalshi_crypto_typed_key_audit_files(
            input_dir=args.input_dir,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        summary = report.get("summary") or {}
        top_blockers = summary.get("top_blockers") or []
        top_blocker_str = ",".join(
            f"{item.get('blocker')}:{item.get('count')}" for item in top_blockers[:3]
        ) or "none"
        print(
            "kalshi_crypto_typed_key_audit=OK diagnostic_only=true "
            f"kalshi_crypto_rows={summary.get('kalshi_crypto_rows', 0)} "
            f"typed_complete_rows={summary.get('typed_complete_rows', 0)} "
            f"point_in_time_rows={summary.get('point_in_time_rows', 0)} "
            f"deadline_or_range_hit_rows={summary.get('deadline_or_range_hit_rows', 0)} "
            f"rows_with_threshold={summary.get('rows_with_threshold', 0)} "
            f"rows_with_target_date={summary.get('rows_with_target_date', 0)} "
            f"rows_with_target_time={summary.get('rows_with_target_time', 0)} "
            f"rows_with_settlement_source={summary.get('rows_with_settlement_source', 0)} "
            f"rows_with_quote={summary.get('rows_with_quote', 0)} "
            f"possible_cdna_peer_rows={summary.get('possible_cdna_peer_rows', 0)} "
            f"possible_polymarket_peer_rows={summary.get('possible_polymarket_peer_rows', 0)} "
            f"date_threshold_comparator_overlap_rows={summary.get('date_threshold_comparator_overlap_rows', 0)} "
            f"exact_ready_rows=0 paper_candidate_rows=0 "
            f"top_blockers={top_blocker_str} "
            f"next_action={summary.get('next_action')} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "crypto-peer-acquisition-plan":
        report = write_crypto_peer_acquisition_plan_files(
            input_dir=args.input_dir,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        summary = report.get("summary") or {}
        top_blockers = summary.get("top_blockers") or []
        top_blocker_str = ",".join(
            f"{item.get('blocker')}:{item.get('count')}" for item in top_blockers[:3]
        ) or "none"
        top_assets = ",".join(
            item.get("asset") for item in (summary.get("top_target_assets") or [])[:3]
        ) or "none"
        top_dates = ",".join(
            item.get("target_date") for item in (summary.get("top_target_dates") or [])[:3]
        ) or "none"
        print(
            "crypto_peer_acquisition_plan=OK diagnostic_only=true saved_files_only=true "
            f"kalshi_typed_complete_grid_rows={summary.get('kalshi_typed_complete_grid_rows', 0)} "
            f"unique_assets={summary.get('unique_assets', 0)} "
            f"unique_dates={summary.get('unique_dates', 0)} "
            f"unique_thresholds={summary.get('unique_thresholds', 0)} "
            f"top_target_assets={top_assets} "
            f"top_target_dates={top_dates} "
            f"polymarket_queries_recommended={summary.get('polymarket_queries_recommended', 0)} "
            f"polymarket_clob_refresh_recommended={summary.get('polymarket_clob_refresh_recommended', 0)} "
            f"cdna_targets_recommended={summary.get('cdna_targets_recommended', 0)} "
            f"kalshi_orderbook_targets_recommended={summary.get('kalshi_orderbook_targets_recommended', 0)} "
            f"safe_commands_referenced={','.join(summary.get('safe_commands_referenced') or []) or 'none'} "
            f"exact_ready_rows=0 paper_candidate_rows=0 "
            f"top_blockers={top_blocker_str} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "crypto-payoff-calendar-audit":
        report = write_crypto_payoff_calendar_audit_files(
            input_dir=args.input_dir,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        summary = report.get("summary") or {}
        top_blockers = summary.get("top_blockers") or []
        top_blocker_str = ",".join(
            f"{item.get('blocker')}:{item.get('count')}" for item in top_blockers[:3]
        ) or "none"
        # Top-3 shapes by combined cross-venue count.
        by_shape = summary.get("counts_by_shape_and_venue") or {}
        shape_totals = sorted(
            (
                (shape, sum(per_venue.values()))
                for shape, per_venue in by_shape.items()
            ),
            key=lambda kv: -kv[1],
        )
        top_shapes = ",".join(f"{shape}:{total}" for shape, total in shape_totals[:3]) or "none"
        print(
            "crypto_payoff_calendar_audit=OK diagnostic_only=true saved_files_only=true "
            f"total_crypto_rows={summary.get('total_crypto_rows', 0)} "
            f"venues={','.join(summary.get('venues') or []) or 'none'} "
            f"exact_shape_possible_rows={summary.get('exact_shape_possible_rows', 0)} "
            f"basis_risk_only_rows={summary.get('basis_risk_only_rows', 0)} "
            f"manual_rules_needed_rows={summary.get('manual_rules_needed_rows', 0)} "
            f"reference_only_rows={summary.get('reference_only_rows', 0)} "
            f"no_current_peer_rows={summary.get('no_current_peer_rows', 0)} "
            f"top_shapes={top_shapes} "
            f"exact_ready_rows=0 paper_candidate_rows=0 "
            f"top_blockers={top_blocker_str} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "crypto-manual-discovery-workbench":
        report = write_crypto_manual_discovery_workbench_files(
            input_dir=args.input_dir,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
            audit_path=args.audit_json,
            max_targets_per_group=args.max_targets_per_group,
        )
        summary = report.get("summary") or {}
        groups = report.get("groups") or []
        group_str = ",".join(
            f"{g['group_name']}:{g['targets_emitted']}/{g['total_eligible_rows']}" for g in groups
        ) or "none"
        print(
            "crypto_manual_discovery_workbench=OK diagnostic_only=true saved_files_only=true "
            f"groups={summary.get('group_count', 0)} "
            f"targets_emitted={summary.get('targets_emitted', 0)} "
            f"total_eligible_audit_rows={summary.get('total_eligible_audit_rows', 0)} "
            f"top_target_group={summary.get('top_target_group')} "
            f"top_target_venue={summary.get('top_target_venue')} "
            f"top_target_asset={summary.get('top_target_asset')} "
            f"top_target_date={summary.get('top_target_date')} "
            f"top_target_payoff_shape={summary.get('top_target_payoff_shape')} "
            f"group_breakdown={group_str} "
            f"exact_ready_rows=0 paper_candidate_rows=0 "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "crypto-threshold-basis-review-scout":
        report = write_crypto_threshold_basis_review_scout_files(
            kalshi_evidence=args.kalshi_evidence,
            polymarket_evidence=args.polymarket_evidence,
            cdna_evidence=args.cdna_evidence,
            asset=args.asset,
            operator_risk_mode=args.operator_risk_mode,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        counts = report.get("summary_counts") or {}
        top_blocker = (report.get("top_blockers") or [{}])[0].get("blocker")
        print(
            "crypto_threshold_basis_review_scout_status=OK "
            "diagnostic_only=true basis_risk_only=true execution_enabled=false "
            f"asset={report.get('asset')} "
            f"kalshi_rows={counts.get('kalshi_rows_loaded', 0)} "
            f"polymarket_rows={counts.get('polymarket_rows_loaded', 0)} "
            f"cdna_rows={counts.get('cdna_rows_loaded', 0)} "
            f"matched_threshold_rows={counts.get('matched_threshold_rows', 0)} "
            f"basis_risk_review_rows={counts.get('basis_risk_review_rows', 0)} "
            f"cdna_fill_first_review_rows={counts.get('cdna_fill_first_review_rows', 0)} "
            f"strict_paper_candidate_rows={counts.get('strict_paper_candidate_rows', 0)} "
            f"operator_paper_candidate_rows={counts.get('operator_paper_candidate_rows', 0)} "
            f"cdna_fill_first_paper_candidate_rows={counts.get('cdna_fill_first_paper_candidate_rows', 0)} "
            f"total_paper_candidate_rows={counts.get('total_paper_candidate_rows', 0)} "
            f"manual_review_rows={counts.get('manual_review_rows', 0)} "
            f"watch_rows={counts.get('watch_rows', 0)} "
            f"ignore_blocked_rows={counts.get('ignore_blocked_rows', 0)} "
            f"exact_ready_rows=0 total_paper_candidate_rows={counts.get('total_paper_candidate_rows', 0)} "
            f"top_blocker={top_blocker} json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "run-daily-crypto-three-venue-check":
        asset_list = [a.strip().upper() for a in (args.assets or "").split(",") if a.strip()]
        report = write_daily_crypto_three_venue_check_files(
            assets=asset_list,
            date=args.date,
            operator_risk_mode=args.operator_risk_mode,
            include_cdna=args.include_cdna,
            operator_accept_cdna_display_price_risk=args.operator_accept_cdna_display_price_risk,
            cdna_operator_size_cap=args.cdna_operator_size_cap,
            max_quote_age_seconds=args.max_quote_age_seconds,
            min_available_notional=args.min_available_notional,
            evidence_roots=args.evidence_roots,
            allow_top_of_book_depth=args.allow_top_of_book_depth,
            operator_size_cap=args.operator_size_cap,
            refresh_kalshi_polymarket=args.refresh_kalshi_polymarket,
            cdna_evidence_dir=args.cdna_evidence_dir,
            write_refreshed_evidence_root=args.write_refreshed_evidence,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        counts = report.get("summary_counts") or {}
        top_hard = (report.get("top_hard_blockers") or [{}])[0].get("blocker")
        refresh = report.get("refresh_summary") or {}
        refresh_root = refresh.get("output_root") or "none"
        venue_counts = report.get("venue_market_counts") or {}
        # Compact, space-free token so a zero is never silent in the status line.
        no_rows_reason = report.get("no_cross_venue_rows_reason")
        reason_token = (no_rows_reason or "rows_present").split(":")[0].split(" ")[0]
        print(
            "run_daily_crypto_three_venue_check_status=OK "
            "diagnostic_only=true saved_files_only=true execution_enabled=false "
            f"operator_risk_mode={report.get('operator_risk_mode')} "
            f"assets={','.join(report.get('assets_requested') or [])} "
            f"date_filter={report.get('date_filter') or 'any'} "
            f"refresh_kalshi_polymarket={str(bool(report.get('refresh_kalshi_polymarket'))).lower()} "
            f"refresh_root={refresh_root} "
            f"allow_top_of_book_depth={str(bool(report.get('allow_top_of_book_depth'))).lower()} "
            f"operator_size_cap={report.get('operator_size_cap', 0)} "
            f"kalshi_markets={venue_counts.get('kalshi_markets', 0)} "
            f"polymarket_markets={venue_counts.get('polymarket_markets', 0)} "
            f"cdna_markets={venue_counts.get('cdna_markets', 0)} "
            f"typed_key_candidates={venue_counts.get('typed_key_candidates', 0)} "
            f"rows={counts.get('rows', 0)} "
            f"total_paper_candidate_rows={counts.get('total_paper_candidate_rows', 0)} "
            f"strict_paper_candidate_rows={counts.get('strict_paper_candidate_rows', 0)} "
            f"operator_paper_candidate_rows={counts.get('operator_paper_candidate_rows', 0)} "
            f"cdna_fill_first_paper_candidate_rows={counts.get('cdna_fill_first_paper_candidate_rows', 0)} "
            f"watch_rows={counts.get('watch_rows', 0)} "
            f"ignore_blocked_rows={counts.get('ignore_blocked_rows', 0)} "
            f"unmatched_target_time_rows={counts.get('unmatched_target_time_rows', 0)} "
            f"unmatched_single_venue_rows={counts.get('unmatched_single_venue_rows', 0)} "
            f"no_cross_venue_rows_reason={reason_token} "
            f"top_hard_blocker={top_hard} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "run-crypto-interval-three-venue-check":
        asset_list = [a.strip().upper() for a in (args.assets or "").split(",") if a.strip()]
        report = write_crypto_interval_three_venue_check_files(
            assets=asset_list,
            lookahead_hours=args.lookahead_hours,
            target_time_tolerance_seconds=args.target_time_tolerance_seconds,
            operator_risk_mode=args.operator_risk_mode,
            allow_top_of_book_depth=args.allow_top_of_book_depth,
            operator_size_cap=args.operator_size_cap,
            include_cdna=args.include_cdna,
            operator_accept_cdna_display_price_risk=args.operator_accept_cdna_display_price_risk,
            cdna_operator_size_cap=args.cdna_operator_size_cap,
            cdna_evidence_dir=args.cdna_evidence_dir,
            max_quote_age_seconds=args.max_quote_age_seconds,
            min_available_notional=args.min_available_notional,
            refresh_kalshi_polymarket=args.refresh_kalshi_polymarket,
            write_refreshed_evidence_root=args.write_refreshed_evidence,
            evidence_roots=args.evidence_roots,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        counts = report.get("summary_counts") or {}
        venue_counts = report.get("venue_market_counts") or {}
        reason_token = (report.get("no_cross_venue_rows_reason") or "rows_present").split(":")[0].split(" ")[0]
        top_blocker = (report.get("top_blockers") or [{}])[0].get("blocker")
        print(
            "run_crypto_interval_three_venue_check_status=OK "
            "diagnostic_only=true public_read_only=true execution_enabled=false "
            f"operator_risk_mode={report.get('operator_risk_mode')} "
            f"assets={','.join(report.get('assets_requested') or [])} "
            f"lookahead_hours={report.get('lookahead_hours')} "
            f"target_time_tolerance_seconds={report.get('target_time_tolerance_seconds')} "
            f"refresh_kalshi_polymarket={str(bool(report.get('refresh_kalshi_polymarket'))).lower()} "
            f"allow_top_of_book_depth={str(bool(report.get('allow_top_of_book_depth'))).lower()} "
            f"operator_size_cap={report.get('operator_size_cap', 0)} "
            f"kalshi_markets={venue_counts.get('kalshi_markets', 0)} "
            f"polymarket_markets={venue_counts.get('polymarket_markets', 0)} "
            f"cdna_markets={venue_counts.get('cdna_markets', 0)} "
            f"exact_matched_windows={venue_counts.get('typed_key_candidates', 0)} "
            f"harmonic_endpoints={(report.get('harmonic_summary') or {}).get('endpoints', 0)} "
            f"harmonic_compatible_instants={(report.get('harmonic_summary') or {}).get('compatible_shared_target_instants', 0)} "
            f"harmonic_point_in_time_matches={(report.get('harmonic_summary') or {}).get('harmonic_point_in_time_matches', 0)} "
            f"direct_updown_matches={(report.get('harmonic_summary') or {}).get('direct_updown_matches', 0)} "
            f"rows={counts.get('rows', 0)} "
            f"paper_candidate_rows={counts.get('paper_candidate_rows', 0)} "
            f"operator_paper_candidate_rows={counts.get('operator_paper_candidate_rows', 0)} "
            f"cdna_fill_first_paper_candidate_rows={counts.get('cdna_fill_first_paper_candidate_rows', 0)} "
            f"synthetic_rows={counts.get('synthetic_rows', 0)} "
            f"synthetic_paper_candidate_rows={counts.get('synthetic_paper_candidate_rows', 0)} "
            f"watch_rows={counts.get('watch_rows', 0)} "
            f"ignore_blocked_rows={counts.get('ignore_blocked_rows', 0)} "
            f"no_cross_venue_rows_reason={reason_token} "
            f"top_blocker={top_blocker} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "crypto-structural-payoff-arb-scout":
        asset_list = [a.strip().upper() for a in (args.assets or "").split(",") if a.strip()]
        report = write_crypto_structural_payoff_arb_scout_files(
            assets=asset_list,
            evidence_roots=args.evidence_roots,
            operator_risk_mode=args.operator_risk_mode,
            include_cdna=args.include_cdna,
            operator_accept_cdna_display_price_risk=args.operator_accept_cdna_display_price_risk,
            allow_top_of_book_depth=args.allow_top_of_book_depth,
            operator_size_cap=args.operator_size_cap,
            cdna_operator_size_cap=args.cdna_operator_size_cap,
            cdna_evidence_dir=args.cdna_evidence_dir,
            cdna_timeseries_dir=args.cdna_timeseries_dir,
            max_cdna_snapshot_age_seconds=args.max_cdna_snapshot_age_seconds,
            require_cdna_fresh_for_cdna_candidates=args.require_cdna_fresh_for_cdna_candidates,
            max_quote_age_seconds=args.max_quote_age_seconds,
            min_available_notional=args.min_available_notional,
            max_basket_legs=args.max_basket_legs,
            source_basis_buffer_bps=args.source_basis_buffer_bps,
            source_basis_buffer_absolute=args.source_basis_buffer_absolute,
            refresh_kalshi_polymarket=args.refresh_kalshi_polymarket,
            lookahead_hours=args.lookahead_hours,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        counts = report.get("summary_counts") or {}
        ctc = report.get("candidate_type_counts") or {}
        bbs = report.get("basis_buffer_sensitivity") or {}
        top_blocker = (report.get("top_blockers") or [{}])[0].get("blocker")
        print(
            "run_crypto_structural_payoff_arb_scout_status=OK "
            "diagnostic_only=true public_read_only=true execution_enabled=false "
            f"operator_risk_mode={report.get('operator_risk_mode')} "
            f"assets={','.join(report.get('assets_requested') or [])} "
            f"load_source={(report.get('load_diagnostics') or {}).get('source')} "
            f"grammar_families={len(report.get('contract_grammar_counts') or {})} "
            f"state_grids_built={counts.get('state_grids_built', 0)} "
            f"rows={counts.get('rows', 0)} "
            f"paper_candidate_rows={counts.get('paper_candidate_rows', 0)} "
            f"strict_paper_candidate_rows={counts.get('strict_paper_candidate_rows', 0)} "
            f"operator_paper_candidate_rows={counts.get('operator_paper_candidate_rows', 0)} "
            f"cdna_fill_first_paper_candidate_rows={counts.get('cdna_fill_first_paper_candidate_rows', 0)} "
            f"long_only={ctc.get('LONG_ONLY_GUARANTEED_PAYOFF', 0)} "
            f"bucket_to_threshold={ctc.get('BUCKET_TO_CUMULATIVE_THRESHOLD', 0)} "
            f"cross_venue={ctc.get('CROSS_VENUE_THRESHOLD_BASIS', 0)} "
            f"up_down={ctc.get('UP_DOWN_SAME_WINDOW', 0)} "
            f"source_basis_buffer_bps={report.get('source_basis_buffer_bps', 0)} "
            f"rows_removed_by_buffer={bbs.get('rows_removed_by_buffer', 0)} "
            f"watch_rows={counts.get('watch_rows', 0)} "
            f"ignore_blocked_rows={counts.get('ignore_blocked_rows', 0)} "
            f"top_blocker={top_blocker} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "watch-crypto-structural-arb":
        asset_list = [a.strip().upper() for a in (args.assets or "").split(",") if a.strip()]
        summary = run_crypto_structural_watch(
            assets=asset_list,
            interval_seconds=args.interval_seconds,
            iterations=args.iterations,
            burst_mode=args.burst_mode,
            burst_interval_seconds=args.burst_interval_seconds,
            normal_interval_seconds=args.normal_interval_seconds,
            boundary_window_seconds=args.boundary_window_seconds,
            operator_risk_mode=args.operator_risk_mode,
            include_cdna=args.include_cdna,
            operator_accept_cdna_display_price_risk=args.operator_accept_cdna_display_price_risk,
            allow_top_of_book_depth=args.allow_top_of_book_depth,
            operator_size_cap=args.operator_size_cap,
            cdna_operator_size_cap=args.cdna_operator_size_cap,
            cdna_evidence_dir=args.cdna_evidence_dir,
            cdna_timeseries_dir=args.cdna_timeseries_dir,
            max_cdna_snapshot_age_seconds=args.max_cdna_snapshot_age_seconds,
            require_cdna_fresh_for_cdna_candidates=args.require_cdna_fresh_for_cdna_candidates,
            max_quote_age_seconds=args.max_quote_age_seconds,
            min_available_notional=args.min_available_notional,
            max_basket_legs=args.max_basket_legs,
            source_basis_buffer_bps=args.source_basis_buffer_bps,
            source_basis_buffer_absolute=args.source_basis_buffer_absolute,
            near_miss_net_edge_threshold=args.near_miss_net_edge_threshold,
            wide_near_miss_net_edge_threshold=args.wide_near_miss_net_edge_threshold,
            lookahead_hours=args.lookahead_hours,
            output_dir=args.output_dir,
        )
        totals = summary.get("totals") or {}
        cadence = summary.get("cadence") or {}
        watch_top_blocker = (totals.get("top_hard_blockers") or [{}])[0].get("blocker")
        print(
            "run_watch_crypto_structural_arb_status=OK "
            "diagnostic_only=true public_read_only=true execution_enabled=false alerts_added=false "
            f"operator_risk_mode={summary.get('operator_risk_mode')} "
            f"assets={','.join(summary.get('assets') or [])} "
            f"iterations_completed={summary.get('iterations_completed')} "
            f"interval_seconds={summary.get('interval_seconds')} "
            f"burst_mode={bool(cadence.get('burst_mode'))} "
            f"iterations_by_cadence={cadence.get('iterations_by_mode') or {}} "
            f"paper_candidates_found={totals.get('paper_candidates_found', 0)} "
            f"manual_micro_test_candidates={totals.get('manual_micro_test_candidates', 0)} "
            f"complement_quote_rows={totals.get('complement_quote_rows', 0)} "
            f"run_quality={summary.get('run_quality_label')} "
            f"best_priced_buy_only_net_edge_after_fees={totals.get('best_priced_buy_only_net_edge_after_fees')} "
            f"best_priced_buy_only_reason={totals.get('best_priced_buy_only_net_edge_after_fees_reason')} "
            f"best_near_miss_net_edge_after_fees={totals.get('best_near_miss_net_edge_after_fees')} "
            f"worst_net_edge_after_fees={totals.get('worst_net_edge_after_fees')} "
            f"top_blocker={watch_top_blocker} "
            f"summary_dir={args.output_dir}"
        )
        return 0
    if args.command == "extract-crypto-paper-candidate-audit-pack":
        report = write_crypto_paper_candidate_audit_pack_files(
            watch_dir=args.watch_dir,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        print(
            "extract_crypto_paper_candidate_audit_pack=OK diagnostic_only=true public_read_only=true "
            "execution_enabled=false network_access=false "
            f"watch_dir={args.watch_dir} watch_dir_exists={report.get('watch_dir_exists')} "
            f"iteration_reports_scanned={report.get('iteration_reports_scanned', 0)} "
            f"canonical_rows_seen={report.get('canonical_rows_seen', 0)} "
            f"summary_copies_seen={report.get('summary_copies_seen', 0)} "
            f"naive_all_paths_total={report.get('naive_all_paths_total', 0)} "
            f"unique_candidates={report.get('unique_candidates', 0)} "
            f"duplicates_ignored={report.get('duplicates_ignored_count', 0)} "
            f"verdict_counts={report.get('verdict_counts') or {}} "
            f"best_adjusted_net_edge_after_fees={report.get('best_candidate_adjusted_net_edge_after_fees')} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "build-crypto-candidate-universe":
        asset_list = [a.strip().upper() for a in (args.assets or "").split(",") if a.strip()]
        universe = build_active_candidate_universe(
            assets=asset_list, operator_risk_mode=args.operator_risk_mode, include_cdna=args.include_cdna,
            operator_accept_cdna_display_price_risk=args.operator_accept_cdna_display_price_risk,
            cdna_evidence_dir=args.cdna_evidence_dir, cdna_timeseries_dir=args.cdna_timeseries_dir,
            max_cdna_snapshot_age_seconds=args.max_cdna_snapshot_age_seconds,
            require_cdna_fresh_for_cdna_candidates=args.require_cdna_fresh_for_cdna_candidates,
            executable_venues=args.executable_venues, scan_venues=args.scan_venues,
            exclude_non_executable_from_live_universe=args.exclude_non_executable_from_live_universe,
            include_near_miss_templates=args.include_near_miss_templates,
            near_miss_net_edge_threshold=args.near_miss_net_edge_threshold,
            include_missing_quote_templates=args.include_missing_quote_templates,
            min_template_quality=args.min_template_quality,
            allow_top_of_book_depth=args.allow_top_of_book_depth,
            operator_size_cap=args.operator_size_cap, cdna_operator_size_cap=args.cdna_operator_size_cap,
            max_basket_legs=args.max_basket_legs, min_net_edge=args.min_net_edge, max_candidates=args.max_candidates,
            source_basis_buffer_bps=args.source_basis_buffer_bps, output_path=args.output,
        )
        print(
            "build_crypto_candidate_universe=OK diagnostic_only=true discovery_pass=true "
            f"assets={','.join(asset_list)} executable_universe_candidate_count={universe.get('executable_universe_candidate_count')} "
            f"non_executable_scan_candidate_count={universe.get('non_executable_scan_candidate_count')} "
            f"excluded_cdna_candidate_count={universe.get('excluded_cdna_candidate_count')} "
            f"excluded_cdna_reason={universe.get('excluded_cdna_reason')} "
            f"watched_leg_count={universe.get('watched_leg_count')} "
            f"watched_leg_count_by_platform={universe.get('watched_leg_count_by_platform')} "
            f"watched_leg_count_by_side={universe.get('watched_leg_count_by_side')} "
            f"zero_universe_reason={universe.get('zero_universe_reason')} "
            f"cdna_scan_only=true cdna_executable=false output={args.output}"
        )
        return 0
    if args.command == "trigger-crypto-fast-path":
        try:
            _uni = json.loads(Path(args.candidate_universe).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _uni = None
        _dp = (_uni or {}).get("discovery_params") if isinstance(_uni, dict) else None
        _discovery_fn = None
        if _dp:
            def _discovery_fn():  # slow-cadence re-discovery; runs the full scout, never per tick
                return build_active_candidate_universe(
                    assets=_dp.get("assets") or [], operator_risk_mode=_dp.get("operator_risk_mode", "aggressive"),
                    include_cdna=bool(_dp.get("include_cdna")), cdna_evidence_dir=(Path(_dp["cdna_evidence_dir"]) if _dp.get("cdna_evidence_dir") else None),
                    cdna_timeseries_dir=(args.cdna_timeseries_dir or (Path(_dp["cdna_timeseries_dir"]) if _dp.get("cdna_timeseries_dir") else None)),
                    max_cdna_snapshot_age_seconds=args.max_cdna_snapshot_age_seconds,
                    require_cdna_fresh_for_cdna_candidates=args.require_cdna_fresh_for_cdna_candidates,
                    operator_size_cap=_dp.get("operator_size_cap", 10.0), cdna_operator_size_cap=args.cdna_operator_size_cap,
                    executable_venues=(args.executable_venues or _dp.get("executable_venues")),
                    scan_venues=_dp.get("scan_venues"),
                    exclude_non_executable_from_live_universe=bool(_dp.get("exclude_non_executable_from_live_universe", True)),
                    include_near_miss_templates=bool(_dp.get("include_near_miss_templates")),
                    near_miss_net_edge_threshold=float(_dp.get("near_miss_net_edge_threshold", 0.10)),
                    include_missing_quote_templates=bool(_dp.get("include_missing_quote_templates")),
                    min_template_quality=str(_dp.get("min_template_quality", "compatible_payoff")),
                    min_net_edge=_dp.get("min_net_edge", 0.0),
                    max_candidates=_dp.get("max_candidates", 50), source_basis_buffer_bps=_dp.get("source_basis_buffer_bps", 0.0),
                    output_path=args.candidate_universe,
                )
        summary = run_crypto_fast_path_trigger(
            candidate_universe=args.candidate_universe, quote_loop_interval_ms=args.quote_loop_interval_ms,
            iterations=args.iterations, min_net_edge=args.min_net_edge, max_decision_age_ms=args.max_decision_age_ms,
            max_quote_age_ms=args.max_quote_age_ms, refresh_universe_every_seconds=args.refresh_universe_every_seconds,
            source_basis_buffer_bps=args.source_basis_buffer_bps, discovery_fn=_discovery_fn,
            quote_source=args.quote_source, cdna_timeseries_dir=args.cdna_timeseries_dir,
            cdna_evidence_dir=args.cdna_evidence_dir, max_cdna_snapshot_age_seconds=args.max_cdna_snapshot_age_seconds,
            require_cdna_fresh_for_cdna_candidates=args.require_cdna_fresh_for_cdna_candidates,
            cdna_operator_size_cap=args.cdna_operator_size_cap, executable_venues=args.executable_venues,
            max_slippage_cents=args.max_slippage_cents,
            order_timeout_ms=args.order_timeout_ms, max_total_notional=args.max_total_notional,
            max_platform_notional=args.max_platform_notional, max_leg_notional=args.max_leg_notional,
            operator_size_cap=args.operator_size_cap, max_orders=args.max_orders,
            max_residual_exposure=args.max_residual_exposure, execution_style=args.execution_style,
            output_dir=args.output_dir, dry_run=args.dry_run, live=args.live,
            i_understand_this_places_real_orders=args.i_understand_this_places_real_orders,
        )
        print(
            "trigger_crypto_fast_path=OK dry_run_default=true protected_limit_buy_only=true market_orders=false "
            "shorting=false hot_path_no_full_scan=true hot_path_no_markdown=true cdna_in_live_hot_path=false "
            f"mode={summary.get('mode')} quote_source={summary.get('quote_source')} "
            f"executable_universe_candidate_count={summary.get('executable_universe_candidate_count')} "
            f"non_executable_scan_candidate_count={summary.get('non_executable_scan_candidate_count')} "
            f"watched_leg_count={summary.get('watched_leg_count')} "
            f"zero_universe_reason={summary.get('zero_universe_reason')} "
            f"best_watched_edge={summary.get('best_watched_edge')} "
            f"adapter_status={summary.get('adapter_status')} "
            f"ticks={summary.get('ticks')} decisions={summary.get('decisions')} "
            f"decisions_that_would_trade={summary.get('decisions_that_would_trade')} "
            f"cdna={summary.get('cdna')} "
            f"kill_switch_present={summary.get('kill_switch_present')} output_dir={args.output_dir}"
        )
        return 0
    if args.command == "send-daily-summary":
        date = args.date or datetime.now().strftime("%Y-%m-%d")
        base_dir = Path(args.reports_root) / "daily_summaries" / date
        json_output = args.json_output or (base_dir / "daily_summary.json")
        markdown_output = args.markdown_output or (base_dir / "daily_summary.md")
        message_output = args.message_output or (base_dir / "daily_summary_message.txt")
        result = write_and_send_daily_summary(
            reports_root=args.reports_root, date=date, provider_name=args.provider, send=args.send,
            json_output=json_output, markdown_output=markdown_output, message_output=message_output,
            max_message_chars=args.max_message_chars,
        )
        note = result["notification"]
        print(
            "send_daily_summary=OK reporting_only=true no_trading=true "
            f"date={date} provider={note.get('provider')} send_flag={bool(args.send)} "
            f"delivery_status={note.get('status')} redacted_config={note.get('redacted_config')} "
            f"json={json_output} markdown={markdown_output} message={message_output}"
        )
        return 0
    if args.command == "crypto-arb-surface-coverage-audit":
        asset_list = [a.strip().upper() for a in (args.assets or "").split(",") if a.strip()]
        report = write_crypto_arb_surface_coverage_audit_files(
            input_report=args.input_report, latest_iteration_dir=args.latest_iteration_dir,
            assets=asset_list, include_cdna=args.include_cdna,
            json_output=args.json_output, markdown_output=args.markdown_output,
        )
        print(
            "crypto_arb_surface_coverage_audit=OK diagnostic_only=true public_read_only=true network_access=false "
            f"source_kind={report.get('source_kind')} verdict={report.get('verdict')} "
            f"gap_count={report.get('gap_count')} gaps={report.get('gaps')} "
            f"expected_zero_count={report.get('expected_zero_count')} needs_data_count={report.get('needs_data_count')} "
            f"load_error={report.get('load_error')} json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "trigger-crypto-structural-arb":
        asset_list = [a.strip().upper() for a in (args.assets or "").split(",") if a.strip()]
        summary = run_crypto_structural_trigger(
            assets=asset_list, watch_once_or_loop=args.watch_once_or_loop, iterations=args.iterations,
            min_net_edge=args.min_net_edge, operator_risk_mode=args.operator_risk_mode, burst_mode=args.burst_mode,
            burst_interval_seconds=args.burst_interval_seconds, normal_interval_seconds=args.normal_interval_seconds,
            boundary_window_seconds=args.boundary_window_seconds, max_quote_age_ms=args.max_quote_age_ms,
            max_slippage_cents=args.max_slippage_cents, order_timeout_ms=args.order_timeout_ms,
            max_total_notional=args.max_total_notional, max_platform_notional=args.max_platform_notional,
            max_leg_notional=args.max_leg_notional, operator_size_cap=args.operator_size_cap,
            max_daily_notional=args.max_daily_notional, max_orders=args.max_orders,
            max_residual_exposure=args.max_residual_exposure, include_cdna=args.include_cdna,
            cdna_evidence_dir=args.cdna_evidence_dir,
            operator_accept_cdna_display_price_risk=args.operator_accept_cdna_display_price_risk,
            cdna_operator_size_cap=args.cdna_operator_size_cap, source_basis_buffer_bps=args.source_basis_buffer_bps,
            output_dir=args.output_dir, execution_style=args.execution_style, dry_run=args.dry_run, live=args.live,
            i_understand_this_places_real_orders=args.i_understand_this_places_real_orders, fail_fast=args.fail_fast,
        )
        print(
            "trigger_crypto_structural_arb=OK dry_run_default=true protected_limit_buy_only=true "
            "market_orders=false shorting=false browser_automation_added=false reads_credentials=false "
            f"mode={summary.get('mode')} assets={','.join(summary.get('assets') or [])} "
            f"iterations_completed={summary.get('iterations_completed')} "
            f"triggers_created={summary.get('triggers_created')} "
            f"triggers_that_would_trade={summary.get('triggers_that_would_trade')} "
            f"kill_switch_present={summary.get('kill_switch_present')} output_dir={args.output_dir}"
        )
        return 0
    if args.command == "start-crypto-micro-test":
        result = start_crypto_micro_test(
            candidate_audit_pack=args.candidate_audit_pack, candidate_id=args.candidate_id,
            execution_plan=args.execution_plan, max_total_notional=args.max_total_notional,
            test_label=args.test_label, output_root=args.output_root,
        )
        print(
            "start_crypto_micro_test=OK forensic_journal_only=true live_order_placement=false "
            "order_submit_or_cancel=false account_connection=false network_access=false "
            f"test_id={result.get('test_id')} test_dir={result.get('test_dir')} "
            f"intended_legs={result.get('intended_legs')} warnings={result.get('warnings')}"
        )
        return 0
    if args.command == "record-crypto-micro-fill":
        result = record_crypto_micro_fill(
            test_id=args.test_id, platform=args.platform, market_id_or_ticker=args.market_id_or_ticker,
            side=args.side, intended_limit_price=args.intended_limit_price, filled_price=args.filled_price,
            filled_quantity=args.filled_quantity, fees=args.fees, order_start_time_utc=args.order_start_time_utc,
            order_submit_time_utc=args.order_submit_time_utc, first_fill_time_utc=args.first_fill_time_utc,
            final_fill_time_utc=args.final_fill_time_utc, order_status=args.order_status, notes=args.notes,
            output_root=args.output_root,
        )
        print(
            "record_crypto_micro_fill=OK forensic_journal_only=true live_order_placement=false "
            f"test_id={result.get('test_id')} leg_key={result.get('leg_key')} warnings={result.get('warnings')}"
        )
        return 0
    if args.command == "finalize-crypto-micro-test":
        final = finalize_crypto_micro_test(
            test_id=args.test_id, settlement_status=args.settlement_status,
            manual_notes=args.manual_notes, output_root=args.output_root,
        )
        print(
            "finalize_crypto_micro_test=OK forensic_journal_only=true live_order_placement=false "
            f"test_id={final.get('test_id')} verdict={final.get('verdict')} "
            f"guarantee_holds={final.get('guarantee_holds')} "
            f"actual_net_edge_after_fees_if_all_filled={final.get('actual_net_edge_after_fees_if_all_filled')} "
            f"filled_legs={final.get('filled_legs')}/{final.get('legs_total')}"
        )
        return 0
    if args.command == "crypto-micro-test-report":
        result = crypto_micro_test_report(
            test_id=args.test_id, markdown_output=args.markdown_output, output_root=args.output_root,
        )
        print(
            "crypto_micro_test_report=OK forensic_journal_only=true "
            f"test_id={result.get('test_id')} verdict={result.get('verdict')} markdown={result.get('markdown_path')}"
        )
        return 0
    if args.command == "append-crypto-micro-quote-snapshot":
        result = append_crypto_micro_quote_snapshot(
            test_id=args.test_id, source=args.source, json_file=args.json_file, output_root=args.output_root,
        )
        print(
            "append_crypto_micro_quote_snapshot=OK forensic_journal_only=true network_access=false "
            f"test_id={result.get('test_id')} appended={result.get('appended')} warnings={result.get('warnings')}"
        )
        return 0
    if args.command == "crypto-execution-plan":
        report = write_execution_plan_files(
            candidate_report=args.candidate_report,
            candidate_id=args.candidate_id,
            max_total_notional=args.max_total_notional,
            max_leg_notional=args.max_leg_notional,
            max_slippage_cents=args.max_slippage_cents,
            max_quote_age_ms=args.max_quote_age_ms,
            execution_style=args.execution_style,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        print(
            "crypto_execution_plan=OK diagnostic_only=true produces_order_intents_only=true "
            "live_order_placement=false order_submit_or_cancel=false network_access=false "
            f"candidate_report={args.candidate_report} candidate_report_exists={report.get('candidate_report_exists')} "
            f"execution_style={args.execution_style} "
            f"candidate_plans_total={report.get('candidate_plans_total', 0)} "
            f"executable_intent_plans={report.get('executable_intent_plans', 0)} "
            f"do_not_trade_plans={report.get('do_not_trade_plans', 0)} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "ingest-cdna-crypto-snapshots":
        report = write_cdna_crypto_snapshot_files(
            input_root=args.input_root,
            output_dir=args.output_dir,
        )
        files = report.get("output_files") or {}
        by_asset = report.get("contracts_by_asset") or {}
        print(
            "ingest_cdna_crypto_snapshots=OK diagnostic_only=true saved_evidence_only=true "
            "cdna_network_fetch_attempted=false browser_automation_added=false execution_enabled=false "
            f"input_root={args.input_root} input_root_exists={report.get('input_root_exists')} "
            f"snapshots_ingested={report.get('snapshots_ingested', 0)} "
            f"raw_observations={report.get('raw_observations', 0)} "
            f"duplicates_removed={report.get('duplicates_removed', 0)} "
            f"distinct_contracts={report.get('distinct_contract_ids', 0)} "
            f"contracts_by_asset={by_asset} "
            f"target_instants_observed={len(report.get('target_instants_observed') or [])} "
            f"sensitive_fields_redacted={report.get('redacted_field_count', 0)} "
            f"jsonl={files.get('jsonl')} latest={files.get('latest')} summary={files.get('summary')}"
        )
        return 0
    if args.command == "batch-evidence-import-readiness":
        report = write_batch_evidence_import_readiness_files(
            input_roots=args.input_roots,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        counts = report.get("summary_counts") or {}
        top_crypto = ((report.get("crypto_worklist") or [{}])[0]).get("family") or "none"
        top_sports = ((report.get("sports_worklist") or [{}])[0]).get("family") or "none"
        print(
            "batch_evidence_import_readiness=OK diagnostic_only=true saved_files_only=true "
            f"families={counts.get('families', 0)} "
            f"ready_crypto={counts.get('READY_FOR_CRYPTO_BASIS_SCOUT', 0)} "
            f"ready_sports={counts.get('READY_FOR_SPORTS_OPERATOR_SCOUT', 0)} "
            f"ready_cdna={counts.get('READY_FOR_CDNA_FILL_FIRST_SCOUT', 0)} "
            f"ready_graph={counts.get('READY_FOR_GRAPH_REVIEW', 0)} "
            f"top_crypto={top_crypto} top_sports={top_sports} "
            f"exact_ready_rows=0 paper_candidate_rows=0 "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "championship-operator-scout-generic":
        report = write_championship_operator_scout_generic_files(
            family_folder=args.family_folder,
            accept_operator_risk=args.accept_operator_risk,
            include_cdna_fill_first=args.include_cdna_fill_first,
            operator_accept_cdna_display_price_risk=args.operator_accept_cdna_display_price_risk,
            operator_risk_mode=args.operator_risk_mode,
            max_quote_age_seconds=args.max_quote_age_seconds,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        counts = report.get("summary_counts") or {}
        top_blocker = ((report.get("top_blockers") or [{}])[0]).get("blocker") or "none"
        print(
            "championship_operator_scout_generic=OK diagnostic_only=true saved_files_only=true "
            f"family={report.get('market_family')} rows={counts.get('rows', 0)} "
            f"strict_paper_candidate_rows={counts.get('strict_paper_candidate_rows', 0)} "
            f"operator_paper_candidate_rows={counts.get('operator_paper_candidate_rows', 0)} "
            f"cdna_fill_first_paper_candidate_rows={counts.get('cdna_fill_first_paper_candidate_rows', 0)} "
            f"total_paper_candidate_rows={counts.get('total_paper_candidate_rows', 0)} "
            f"watch_rows={counts.get('watch_rows', 0)} ignore_blocked_rows={counts.get('ignore_blocked_rows', 0)} "
            f"exact_ready_rows=0 top_blocker={top_blocker} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "three-venue-operator-scout":
        report = write_three_venue_operator_scout_files(
            family_folders=args.family_folder,
            include_cdna=args.include_cdna,
            operator_accept_cdna_display_price_risk=args.operator_accept_cdna_display_price_risk,
            cdna_operator_size_cap=args.cdna_operator_size_cap,
            max_quote_age_seconds=args.max_quote_age_seconds,
            min_available_notional=args.min_available_notional,
            allow_stale_for_diagnostic=args.allow_stale_for_diagnostic,
            operator_risk_mode=args.operator_risk_mode,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        counts = report.get("summary_counts") or {}
        top_blocker = ((report.get("top_blockers") or [{}])[0]).get("blocker") or "none"
        print(
            "three_venue_operator_scout=OK diagnostic_only=true saved_files_only=true "
            f"rows={counts.get('rows', 0)} "
            f"kalshi_poly_rows={counts.get('kalshi_poly_rows', 0)} "
            f"cdna_kalshi_rows={counts.get('cdna_kalshi_rows', 0)} "
            f"cdna_poly_rows={counts.get('cdna_poly_rows', 0)} "
            f"strict_paper_candidate_rows={counts.get('strict_paper_candidate_rows', 0)} "
            f"operator_paper_candidate_rows={counts.get('operator_paper_candidate_rows', 0)} "
            f"cdna_fill_first_paper_candidate_rows={counts.get('cdna_fill_first_paper_candidate_rows', 0)} "
            f"total_paper_candidate_rows={counts.get('total_paper_candidate_rows', 0)} "
            f"watch_rows={counts.get('watch_rows', 0)} ignore_blocked_rows={counts.get('ignore_blocked_rows', 0)} "
            f"exact_ready_rows=0 top_blocker={top_blocker} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "manual-evidence-requirements":
        report = write_manual_evidence_requirements_files(
            input_dir=args.input_dir,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        summary = report.get("summary") or {}
        priorities = summary.get("priority_counts") or {}
        print(
            "manual_evidence_requirements=OK diagnostic_only=true saved_files_only=true "
            f"total_items={summary.get('total_items', 0)} "
            f"verticals={','.join(summary.get('verticals') or [])} "
            f"P0={priorities.get('P0', 0)} "
            f"P1={priorities.get('P1', 0)} "
            f"P2={priorities.get('P2', 0)} "
            f"P3={priorities.get('P3', 0)} "
            f"P4={priorities.get('P4', 0)} "
            f"closest_to_source_review_vertical={summary.get('closest_to_source_review_vertical')} "
            f"closest_to_exact_review_vertical={summary.get('closest_to_exact_review_vertical')} "
            f"distraction_vertical={summary.get('distraction_vertical')} "
            f"queued_platforms_remain_queued={str(bool(summary.get('queued_platforms_remain_queued'))).lower()} "
            f"reference_only_platforms_never_become_pair_side={str(bool(summary.get('reference_only_platforms_never_become_pair_side'))).lower()} "
            f"exact_ready_rows=0 paper_candidate_rows=0 "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "cross-venue-opportunity-scout":
        report = write_cross_venue_opportunity_scout_files(
            input_dir=args.input_dir,
            polymarket_enriched_json=args.polymarket_enriched_json,
            active_platforms=args.active_platforms,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        summary = report.get("summary") or {}
        top_blockers = summary.get("top_blockers") or []
        top_blocker_str = ",".join(
            f"{item.get('blocker')}:{item.get('count')}" for item in top_blockers[:3]
        ) or "none"
        print(
            "cross_venue_opportunity_scout=OK diagnostic_only=true "
            f"scout_rows={summary.get('scout_row_count', 0)} "
            f"exact_ready_rows={summary.get('exact_ready_rows', 0)} "
            f"paper_candidate_rows={summary.get('paper_candidate_rows', 0)} "
            f"execution_ready_rows={summary.get('execution_ready_rows', 0)} "
            f"polymarket_enriched_rows_loaded={summary.get('polymarket_enriched_rows_loaded', 0)} "
            f"polymarket_rows_with_bid_ask_size={summary.get('polymarket_rows_with_bid_ask_size', 0)} "
            f"polymarket_overlap_rows={summary.get('polymarket_overlap_rows', 0)} "
            f"top_lane={summary.get('top_lane') or 'none'} "
            f"active_platforms={','.join(summary.get('active_platforms') or []) or 'all'} "
            f"core_trio_top_lane={summary.get('core_trio_top_lane') or 'none'} "
            f"top_blockers={top_blocker_str} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "core-trio-peer-coverage-audit":
        report = write_core_trio_peer_coverage_audit_files(
            input_dir=args.input_dir,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        summary = report.get("summary") or {}
        top_blockers = summary.get("top_blockers") or []
        top_blocker_str = ",".join(
            f"{item.get('blocker')}:{item.get('count')}" for item in top_blockers[:3]
        ) or "none"
        print(
            "core_trio_peer_coverage_audit=OK diagnostic_only=true saved_files_only=true "
            f"families={summary.get('peer_coverage_families', 0)} "
            f"strongest_overlap_family={summary.get('strongest_overlap_family') or 'none'} "
            f"families_with_kalshi_peer_rows={summary.get('families_with_kalshi_peer_rows', 0)} "
            f"families_without_kalshi_peer_rows={summary.get('families_without_kalshi_peer_rows', 0)} "
            f"exact_ready_rows=0 paper_candidate_rows=0 "
            f"top_blockers={top_blocker_str} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "audit-reference-odds-fv":
        report = write_reference_odds_fv_files(
            input_dir=args.input_dir,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        summary = report["summary"]
        print(
            "reference_odds_fv_status=OK "
            f"odds_events={summary['odds_events_read']} "
            f"reference_markets={summary['reference_markets_read']} "
            f"matched={summary['matched_rows']} "
            f"residual_rows={summary['residual_rows']} "
            f"unmatched={summary['unmatched_reference_rows']} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "audit-existing-paper-candidates":
        report = write_existing_paper_candidate_audit_files(
            input_dir=args.input_dir,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        summary = report["summary"]
        print(
            "existing_paper_candidate_audit_status=OK "
            f"rows={summary['total_paper_candidate_rows_found']} "
            f"unique={summary['unique_candidate_count']} "
            f"current_needs_review={summary['current_needs_review_count']} "
            f"stale={summary['stale_count']} "
            f"likely_fake_or_blocked={summary['likely_fake_or_blocked_count']} "
            f"recommended_next_action={summary['recommended_next_action']} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "plan-stale-report-archive":
        report = write_stale_report_archive_plan_files(
            input_dir=args.input_dir,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        summary = report["summary"]
        print(
            "stale_report_archive_plan_status=OK "
            f"scanned={summary['scanned_file_count']} "
            f"archive_candidates={summary['archive_candidate_count']} "
            f"stale_evaluator={summary['stale_evaluator_output_count']} "
            f"stale_pipeline={summary['stale_pipeline_summary_count']} "
            f"legacy_artifacts={summary['legacy_candidate_artifact_count']} "
            f"commands={summary['suggested_command_count']} "
            f"moved_deleted={summary['files_moved_or_deleted']} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "apply-stale-report-archive-plan":
        should_apply = bool(args.apply)
        report = apply_stale_report_archive_plan(
            plan_path=args.plan,
            applied_output=args.applied_output,
            apply=should_apply,
        )
        summary = report["summary"]
        mode = report["mode"]
        status = report["status"]
        print(
            "stale_report_archive_apply_status="
            f"{status} mode={mode} "
            f"planned={summary['planned_move_count']} "
            f"applied={summary['applied_move_count']} "
            f"noop={summary['noop_move_count']} "
            f"refused={summary['refused_move_count']} "
            f"deleted={summary['files_deleted']} "
            f"plan={args.plan}"
        )
        if not should_apply:
            for move in report.get("planned_moves") or []:
                print(move.get("suggested_move_command") or f"Move-Item -LiteralPath \"{move.get('source_file')}\" -Destination \"{move.get('destination_file')}\"")
        if should_apply:
            print(f"applied_output={args.applied_output}")
        return 1 if summary["refused_move_count"] else 0
    if args.command == "audit-paper-readiness-probe":
        report = write_paper_readiness_probe_files(
            input_dir=args.input_dir,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        summary = report["summary"]
        print(
            "paper_readiness_probe_status=OK "
            f"rows={summary['total_rows_considered']} "
            f"stale_quote={summary['rows_blocked_by_stale_quote']} "
            f"missing_quote={summary['rows_blocked_by_missing_quote']} "
            f"fee={summary['rows_blocked_by_fee']} "
            f"pair_review={summary['rows_blocked_by_pair_review']} "
            f"paper_ready={summary['paper_ready_count']} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "run-mlb-world-series-revival-status":
        report = write_mlb_world_series_revival_status_files(
            input_dir=args.input_dir,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        summary = report["summary"]
        print(
            "mlb_world_series_revival_status=OK "
            f"pairs={summary['pairs_found']} "
            f"strict_same_payoff={summary['strict_same_payoff_pass_count']} "
            f"trusted_relationships={summary['trusted_relationships_attached']} "
            f"evaluator_rows={summary['evaluator_rows']} "
            f"paper_count={summary['paper_candidate_count']} "
            f"blockers={len(report['blockers'])} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "audit-platform-api-expansion":
        report = write_platform_api_expansion_files(
            project_root=PROJECT_ROOT,
            input_dir=args.input_dir,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        summary = report["summary"]
        recommendations = report.get("recommendations") or {}
        print(
            "platform_api_expansion_status=OK "
            f"platforms={summary['platform_count']} "
            f"read_only_only={summary['read_only_only_count']} "
            f"reference_only={summary['reference_only_count']} "
            f"requires_auth_review={summary['requires_auth_review_count']} "
            f"best_next={recommendations.get('best_next_platform_adapter')} "
            f"family={recommendations.get('best_next_family_universe')} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "parse-crypto-com-predict-cdna-fixtures":
        fixture_dirs = args.fixture_dir or [PROJECT_ROOT / "venues" / "fixtures" / "crypto_com_predict_cdna"]
        report = write_crypto_com_predict_cdna_research_snapshot_file(
            fixture_dirs=fixture_dirs,
            json_output=args.json_output,
        )
        summary = report["summary"]
        top_blocker = (summary.get("top_blockers") or [{}])[0].get("blocker")
        print(
            "crypto_com_predict_cdna_research_snapshot_status=OK "
            f"rows={summary['parsed_rows']} "
            f"btc_rows={summary['btc_rows']} "
            f"eth_rows={summary['eth_rows']} "
            f"basis_risk_compatible={summary['basis_risk_compatible_with_kalshi']} "
            f"exact_compatible={summary['exact_payoff_compatible_with_kalshi']} "
            f"top_blocker={top_blocker} "
            f"json={args.json_output}"
        )
        return 0
    if args.command == "compare-cdna-vs-kalshi-btc-basis-risk":
        report = write_cdna_vs_kalshi_btc_basis_risk_file(
            cdna_path=args.cdna,
            standardized_path=args.standardized,
            json_output=args.json_output,
        )
        summary = report["summary"]
        print(
            "cdna_vs_kalshi_btc_basis_risk_status=OK "
            f"cdna_btc={summary['cdna_btc_rows_considered']} "
            f"kalshi_btc={summary['kalshi_btc_rows_considered']} "
            f"basis_risk_rows={summary['basis_risk_row_count']} "
            f"btc_basis_risk_review={summary['btc_basis_risk_review_count']} "
            f"paper_candidates={summary['paper_candidate_count']} "
            f"warnings={summary['warning_count']} "
            f"json={args.json_output}"
        )
        return 0
    if args.command == "normalize-sx-bet-saved":
        outputs = write_sx_bet_saved_normalization_files(
            project_root=PROJECT_ROOT,
            input_dir=args.input_dir,
            json_output=args.json_output,
            coverage_output=args.coverage_output,
        )
        summary = outputs["coverage"]["summary"]
        top_blocker = (summary.get("top_blockers") or [{}])[0].get("blocker")
        print(
            "sx_bet_normalized_draft_status=OK "
            f"rows_read={summary['rows_read']} "
            f"normalized={summary['normalized_records']} "
            f"unique_events={summary['unique_events']} "
            f"unique_markets={summary['unique_markets']} "
            f"quote_fields={summary['quote_fields_present']} "
            f"depth_fields={summary['depth_fields_present']} "
            f"top_blocker={top_blocker} "
            f"json={args.json_output} coverage={args.coverage_output}"
        )
        return 0
    if args.command == "audit-sx-bet-sports-typed-keys":
        report = write_sx_bet_sports_typed_keys_files(
            input_path=args.input,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        summary = report["summary"]
        status = "NO_INPUT" if report.get("warnings") else "OK"
        top_blocker = (summary.get("top_blockers") or [{}])[0].get("blocker")
        print(
            f"sx_bet_sports_typed_keys_status={status} "
            f"rows={summary['total_rows']} "
            f"complete={summary['complete']} "
            f"partial={summary['partial']} "
            f"blocked={summary['blocked']} "
            f"future_overlap_usable={summary['future_overlap_review_usable_count']} "
            f"top_blocker={top_blocker} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "audit-sx-bet-sports-overlap":
        report = write_sx_bet_sports_overlap_files(
            sx_bet_typed_keys_path=args.sx_bet_typed_keys,
            input_dir=args.input_dir,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
            require_game_level_target=args.require_game_level_target,
        )
        summary = report["summary"]
        top_blocker = (summary.get("top_blockers") or [{}])[0].get("blocker")
        scope = summary.get("scope_mismatch_breakdown") or {}
        print(
            "sx_bet_sports_overlap_status=OK "
            f"sx_rows={summary['sx_bet_rows_considered']} "
            f"overlap_rows={summary['overlap_rows']} "
            f"exact={summary['exact_typed_key_matches']} "
            f"partial={summary['partial_matches']} "
            f"blocked_reference_only={summary['blocked_reference_only']} "
            f"require_game_level_target={str(report.get('require_game_level_target', False)).lower()} "
            f"game_level_targets={scope.get('kalshi_polymarket_targets_game_level', 0)} "
            f"top_blocker={top_blocker} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "sports-mlb-daily-residual-risk-scout":
        report = write_sports_mlb_daily_residual_risk_files(
            kalshi_evidence=args.kalshi_evidence,
            polymarket_evidence=args.polymarket_evidence,
            date=args.date,
            accept_mlb_daily_contingency_risk=args.accept_mlb_daily_contingency_risk,
            operator_accepted_as_arb=args.operator_accepted_as_arb,
            include_live_games=args.include_live_games,
            exclude_live_games=args.exclude_live_games,
            max_quote_age_seconds=args.max_quote_age_seconds,
            min_available_notional=args.min_available_notional,
            operator_risk_mode=args.operator_risk_mode,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        summary = report.get("summary_counts") or {}
        top_blocker = (summary.get("top_blockers") or [{}])[0].get("blocker")
        print(
            "sports_mlb_daily_residual_risk_scout_status=OK "
            f"diagnostic_only=true shadow_paper_only=true "
            f"human_accepted_residual_risk={str(bool(report.get('human_accepted_residual_risk'))).lower()} "
            f"operator_accepted_as_arb={str(bool(report.get('operator_accepted_as_arb'))).lower()} "
            f"matched_games={report.get('matched_games', 0)} "
            f"rows={summary.get('rows', 0)} "
            f"strict_paper_candidate_rows={summary.get('strict_paper_candidate_rows', 0)} "
            f"operator_paper_candidate_rows={summary.get('operator_paper_candidate_rows', 0)} "
            f"cdna_fill_first_paper_candidate_rows={summary.get('cdna_fill_first_paper_candidate_rows', 0)} "
            f"total_paper_candidate_rows={summary.get('total_paper_candidate_rows', 0)} "
            f"watch_rows={summary.get('watch_rows', 0)} "
            f"exact_ready_rows=0 "
            f"top_blocker={top_blocker} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "run-mlb-daily-operator-check":
        date_label = args.date or datetime.now().date().isoformat()
        output_dir = args.output_root / date_label
        normalized_output_dir = args.normalized_root / date_label / "normalized"
        scout_json = args.json_output or PROJECT_ROOT / "reports" / f"sports_mlb_daily_games_{date_label}_operator_arb_scout.json"
        scout_markdown = (
            args.markdown_output
            or PROJECT_ROOT / "reports" / f"sports_mlb_daily_games_{date_label}_operator_arb_scout.md"
        )
        summary_json = PROJECT_ROOT / "reports" / f"sports_mlb_daily_games_{date_label}_operator_check_summary.json"
        summary_markdown = PROJECT_ROOT / "reports" / f"sports_mlb_daily_games_{date_label}_operator_check_summary.md"
        collector_report = write_mlb_daily_game_evidence_files(
            target_date=date_label,
            output_dir=output_dir,
            normalized_output_dir=normalized_output_dir,
            max_games=args.max_games,
            timeout_seconds=args.timeout_seconds,
        )
        kalshi_evidence = normalized_output_dir / f"sports_kalshi_mlb_daily_games_{date_label}_normalized_evidence.json"
        polymarket_evidence = normalized_output_dir / f"sports_polymarket_mlb_daily_games_{date_label}_normalized_evidence.json"
        missing_outputs = [str(path) for path in (kalshi_evidence, polymarket_evidence) if not path.exists()]
        if missing_outputs:
            print(
                "run_mlb_daily_operator_check_status=FAILED "
                "diagnostic_only=true execution_enabled=false "
                f"date={date_label} missing_outputs={','.join(missing_outputs)}"
            )
            return 1
        scout_report = write_sports_mlb_daily_residual_risk_files(
            kalshi_evidence=kalshi_evidence,
            polymarket_evidence=polymarket_evidence,
            date=date_label,
            accept_mlb_daily_contingency_risk=args.accept_mlb_daily_contingency_risk,
            operator_accepted_as_arb=args.operator_accepted_as_arb,
            include_live_games=args.include_live_games,
            exclude_live_games=args.exclude_live_games,
            max_quote_age_seconds=args.max_quote_age_seconds,
            min_available_notional=args.min_available_notional,
            operator_risk_mode=args.operator_risk_mode,
            json_output=scout_json,
            markdown_output=scout_markdown,
        )
        runner_summary = _mlb_daily_operator_check_summary(
            date_label=date_label,
            collector_report=collector_report,
            scout_report=scout_report,
            kalshi_evidence=kalshi_evidence,
            polymarket_evidence=polymarket_evidence,
            scout_json=scout_json,
            scout_markdown=scout_markdown,
            summary_json=summary_json,
            summary_markdown=summary_markdown,
        )
        summary_json.parent.mkdir(parents=True, exist_ok=True)
        summary_markdown.parent.mkdir(parents=True, exist_ok=True)
        summary_json.write_text(json.dumps(runner_summary, indent=2, sort_keys=True), encoding="utf-8")
        summary_markdown.write_text(_render_mlb_daily_operator_check_summary(runner_summary), encoding="utf-8")
        counts = scout_report.get("summary_counts") or {}
        top_blocker = (counts.get("top_blockers") or [{}])[0].get("blocker")
        print(
            "run_mlb_daily_operator_check_status=OK "
            "diagnostic_only=true public_no_auth_only=true execution_enabled=false "
            f"date={date_label} "
            f"matched_games={scout_report.get('matched_games', 0)} "
            f"scout_rows={counts.get('rows', 0)} "
            f"operator_arb_review_rows={counts.get('operator_arb_review_rows', 0)} "
            f"manual_review_rows={counts.get('manual_review_rows', 0)} "
            f"watch_rows={counts.get('watch_rows', 0)} "
            f"ignore_blocked_rows={counts.get('ignore_blocked_rows', 0)} "
            f"strict_paper_candidate_rows={counts.get('strict_paper_candidate_rows', 0)} "
            f"operator_paper_candidate_rows={counts.get('operator_paper_candidate_rows', 0)} "
            f"cdna_fill_first_paper_candidate_rows={counts.get('cdna_fill_first_paper_candidate_rows', 0)} "
            f"total_paper_candidate_rows={counts.get('total_paper_candidate_rows', 0)} "
            f"exact_ready_rows=0 "
            f"top_blocker={top_blocker} "
            f"scout_json={scout_json} scout_markdown={scout_markdown} "
            f"summary_json={summary_json} summary_markdown={summary_markdown}"
        )
        return 0
    if args.command == "fetch-mlb-daily-game-evidence":
        date_label = args.date or datetime.now().date().isoformat()
        output_dir = args.output_dir or PROJECT_ROOT / "reports" / "live_readonly" / "mlb_daily" / date_label
        normalized_output_dir = (
            args.normalized_output_dir
            or PROJECT_ROOT / "reports" / "manual_evidence" / "sports" / "mlb_daily_games" / date_label / "normalized"
        )
        report = write_mlb_daily_game_evidence_files(
            target_date=date_label,
            output_dir=output_dir,
            normalized_output_dir=normalized_output_dir,
            max_games=args.max_games,
            timeout_seconds=args.timeout_seconds,
        )
        counts = report.get("summary_counts") or {}
        top_blocker = (report.get("top_blockers") or [{}])[0].get("blocker")
        outputs = report.get("outputs") or {}
        print(
            "fetch_mlb_daily_game_evidence_status=OK "
            "diagnostic_only=true public_no_auth_only=true "
            f"date={date_label} "
            f"polymarket_games={counts.get('polymarket_games', 0)} "
            f"kalshi_games={counts.get('kalshi_games', 0)} "
            f"matched_games={counts.get('matched_games', 0)} "
            f"missing_kalshi_peer={counts.get('missing_kalshi_peer', 0)} "
            f"missing_polymarket_peer={counts.get('missing_polymarket_peer', 0)} "
            f"raw_files_written={counts.get('raw_files_written', 0)} "
            f"exact_ready_rows=0 paper_candidate_rows=0 "
            f"top_blocker={top_blocker} "
            f"summary={outputs.get('summary_json')} markdown={outputs.get('summary_markdown')}"
        )
        return 0
    if args.command == "fetch-mlb-world-series-evidence":
        season = str(args.season).strip()
        output_dir = args.output_dir or PROJECT_ROOT / "reports" / "live_readonly" / "mlb_world_series" / season
        normalized_output_dir = (
            args.normalized_output_dir
            or PROJECT_ROOT / "reports" / "manual_evidence" / "sports" / f"mlb_world_series_{season}"
        )
        report = write_mlb_world_series_evidence_files(
            season=season,
            output_dir=output_dir,
            normalized_output_dir=normalized_output_dir,
            timeout_seconds=args.timeout_seconds,
        )
        counts = report.get("summary_counts") or {}
        missing = report.get("missing_fields_or_blockers") or {}
        top_blocker = (report.get("top_blockers") or [{}])[0].get("blocker")
        outputs = report.get("outputs") or {}
        print(
            "fetch_mlb_world_series_evidence_status=OK "
            "diagnostic_only=true public_no_auth_only=true "
            f"season={season} "
            f"kalshi_team_outcomes={counts.get('kalshi_team_outcomes', 0)} "
            f"kalshi_tickers={counts.get('kalshi_tickers', 0)} "
            f"kalshi_orderbooks_requested={counts.get('kalshi_orderbooks_requested', 0)} "
            f"polymarket_team_outcomes={counts.get('polymarket_team_outcomes', 0)} "
            f"polymarket_token_ids={counts.get('polymarket_token_ids', 0)} "
            f"polymarket_books_requested={counts.get('polymarket_books_requested', 0)} "
            f"kalshi_missing_orderbooks={missing.get('kalshi_missing_orderbooks', 0)} "
            f"polymarket_missing_books={missing.get('polymarket_missing_books', 0)} "
            f"raw_files_written={counts.get('raw_files_written', 0)} "
            f"exact_ready_rows=0 paper_candidate_rows=0 "
            f"top_blocker={top_blocker} "
            f"summary={outputs.get('summary_json')} markdown={outputs.get('summary_markdown')}"
        )
        return 0
    if args.command == "sports-mlb-world-series-evidence-compare":
        report = write_sports_mlb_world_series_evidence_compare_files(
            kalshi_evidence=args.kalshi_evidence,
            polymarket_evidence=args.polymarket_evidence,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
            accept_world_series_remote_tail_risk=args.accept_world_series_remote_tail_risk,
        )
        counts = report.get("summary_counts") or {}
        top_blocker = (report.get("top_blockers") or [{}])[0].get("blocker")
        print(
            "sports_mlb_world_series_evidence_compare_status=OK "
            "diagnostic_only=true strict_exact_arb=false "
            f"human_accepted_remote_tail_risk={str(bool(report.get('human_accepted_remote_tail_risk'))).lower()} "
            f"kalshi_rows_loaded={report.get('kalshi_rows_loaded', 0)} "
            f"polymarket_rows_loaded={report.get('polymarket_rows_loaded', 0)} "
            f"matched_team_rows={report.get('matched_team_rows', 0)} "
            f"unmatched_kalshi_rows={report.get('unmatched_kalshi_rows', 0)} "
            f"unmatched_polymarket_rows={report.get('unmatched_polymarket_rows', 0)} "
            f"source_review_rows={counts.get('source_review_rows', 0)} "
            f"manual_review_rows={counts.get('manual_review_rows', 0)} "
            f"watch_rows={counts.get('watch_rows', 0)} "
            f"ignore_blocked_rows={counts.get('ignore_blocked_rows', 0)} "
            f"exact_ready_rows=0 paper_candidate_rows=0 "
            f"top_blocker={top_blocker} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "sports-mlb-world-series-residual-risk-scout":
        report = write_sports_mlb_world_series_residual_risk_files(
            kalshi_evidence=args.kalshi_evidence,
            polymarket_evidence=args.polymarket_evidence,
            season=args.season,
            accept_world_series_remote_tail_risk=args.accept_world_series_remote_tail_risk,
            operator_accepted_as_arb=args.operator_accepted_as_arb,
            max_quote_age_seconds=args.max_quote_age_seconds,
            min_available_notional=args.min_available_notional,
            operator_risk_mode=args.operator_risk_mode,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        counts = report.get("summary_counts") or {}
        top_blocker = (report.get("top_blockers") or [{}])[0].get("blocker")
        print(
            "sports_mlb_world_series_residual_risk_scout_status=OK "
            "diagnostic_only=true shadow_paper_only=true strict_exact_arb=false mathematical_strict_exact_arb=false "
            f"human_accepted_remote_tail_risk={str(bool(report.get('human_accepted_remote_tail_risk'))).lower()} "
            f"operator_accepted_as_arb={str(bool(report.get('operator_accepted_as_arb'))).lower()} "
            f"operator_arb_mode={str(bool(report.get('operator_arb_mode'))).lower()} "
            f"kalshi_rows_loaded={report.get('kalshi_rows_loaded', 0)} "
            f"polymarket_rows_loaded={report.get('polymarket_rows_loaded', 0)} "
            f"matched_team_rows={report.get('matched_team_rows', 0)} "
            f"rows={counts.get('rows', 0)} "
            f"operator_arb_review_rows={counts.get('operator_arb_review_rows', 0)} "
            f"manual_review_rows={counts.get('manual_review_rows', 0)} "
            f"watch_rows={counts.get('watch_rows', 0)} "
            f"ignore_blocked_rows={counts.get('ignore_blocked_rows', 0)} "
            f"positive_gross_rows={counts.get('positive_gross_rows', 0)} "
            f"positive_net_rows={counts.get('positive_net_rows', 0)} "
            f"strict_paper_candidate_rows={counts.get('strict_paper_candidate_rows', 0)} "
            f"operator_paper_candidate_rows={counts.get('operator_paper_candidate_rows', 0)} "
            f"cdna_fill_first_paper_candidate_rows={counts.get('cdna_fill_first_paper_candidate_rows', 0)} "
            f"total_paper_candidate_rows={counts.get('total_paper_candidate_rows', 0)} "
            f"exact_ready_rows=0 "
            f"top_blocker={top_blocker} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "operator-arb-convergence-plan":
        report = write_operator_arb_convergence_plan_files(
            input_report=args.input_report,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
            target_exit_edge=args.target_exit_edge,
            min_hold_net_edge=args.min_hold_net_edge,
            min_annualized_return=args.min_annualized_return,
            settlement_date=args.settlement_date,
            max_capital_tieup_days=args.max_capital_tieup_days,
        )
        counts = report.get("summary_counts") or {}
        top_blocker = (report.get("top_blockers") or [{}])[0].get("blocker")
        print(
            "operator_arb_convergence_plan_status=OK "
            "diagnostic_only=true execution_recommendation_only=true standard_paper_candidate_emitted=false "
            f"rows={counts.get('rows', 0)} "
            f"exit_now_review_rows={counts.get('exit_now_review_rows', 0)} "
            f"exit_target_already_met_rows={counts.get('exit_target_already_met_rows', 0)} "
            f"enter_and_monitor_rows={counts.get('enter_and_monitor_rows', 0)} "
            f"hold_to_settlement_rows={counts.get('hold_to_settlement_rows', 0)} "
            f"manual_review_rows={counts.get('manual_review_rows', 0)} "
            f"watch_rows={counts.get('watch_rows', 0)} "
            f"ignore_low_return_rows={counts.get('ignore_low_return_rows', 0)} "
            f"ignore_insufficient_size_rows={counts.get('ignore_insufficient_size_rows', 0)} "
            f"exact_ready_rows=0 standard_paper_candidate_rows=0 "
            f"top_blocker={top_blocker} "
            f"json={args.json_output} markdown={args.markdown_output}"
        )
        return 0
    if args.command == "replay-paper-candidate-markouts":
        return replay_paper_candidate_markouts(
            args.ledger,
            args.polymarket_enriched_later,
            args.kalshi_enriched_later,
            args.output,
            window_tolerance_seconds=args.window_tolerance_seconds,
        )
    if args.command == "run-targeted-pipeline":
        return run_targeted_pipeline(
            label=args.label,
            output_dir=args.output_dir,
            limit=args.limit,
            timeout_seconds=args.timeout_seconds,
            polymarket_tag_slug=args.polymarket_tag_slug,
            polymarket_tag_id=args.polymarket_tag_id,
            kalshi_series_ticker=args.kalshi_series_ticker,
            kalshi_event_ticker=args.kalshi_event_ticker,
            kalshi_max_pages=args.kalshi_max_pages,
            max_snapshot_age_hours=args.max_snapshot_age_hours,
            max_quote_age_seconds=args.max_quote_age_seconds,
            max_settlement_delta_seconds=args.max_settlement_delta_seconds,
            min_top_of_book_size=args.min_top_of_book_size,
            min_net_gap=args.min_net_gap,
            accept_unit_mismatch=args.accept_unit_mismatch,
        )
    if args.command == "run-multi-universe-sweep":
        return run_multi_universe_sweep(
            manifest=args.manifest,
            sweep_label=args.sweep_label,
            output_dir=args.output_dir,
            limit=args.limit,
            timeout_seconds=args.timeout_seconds,
            kalshi_max_pages=args.kalshi_max_pages,
            max_snapshot_age_hours=args.max_snapshot_age_hours,
            max_quote_age_seconds=args.max_quote_age_seconds,
            max_settlement_delta_seconds=args.max_settlement_delta_seconds,
            min_top_of_book_size=args.min_top_of_book_size,
            min_net_gap=args.min_net_gap,
            accept_unit_mismatch=args.accept_unit_mismatch,
        )
    if args.command == "explain-sweep-summary":
        return explain_sweep_summary(args.summary)
    if args.command == "explain-pipeline-summary":
        return explain_pipeline_summary(args.summary)
    if args.command == "explain-paper-candidates":
        return explain_paper_candidates(args.ledger, action=args.action, limit=args.limit)
    if args.command == "explain-reference-context":
        return explain_reference_context(
            args.snapshot,
            args.reference_snapshot,
            min_similarity=args.min_similarity,
        )
    if args.command == "llm-review-relationships":
        return llm_review_relationships(
            args.input,
            args.output,
            markdown_output=args.markdown_output,
            stub=args.stub,
        )
    if args.command == "source-readiness":
        return source_readiness(args.output)
    if args.command == "executable-venue-readiness":
        return executable_venue_readiness(
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
    if args.command == "inspect-ibkr-forecastex-fixtures":
        return inspect_ibkr_forecastex_fixtures(
            instruments_path=args.instruments,
            quotes_path=args.quotes,
            settlement_path=args.settlement,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
    if args.command == "ibkr-forecastex-access-doctor":
        return ibkr_forecastex_access_doctor(
            base_url=args.base_url,
            timeout_seconds=args.timeout_seconds,
            json_output=args.json_output,
        )
    if args.command == "fetch-ibkr-forecastex-readonly":
        return fetch_ibkr_forecastex_readonly(
            base_url=args.base_url,
            timeout_seconds=args.timeout_seconds,
            max_contracts=args.max_contracts,
            max_contract_info_requests=args.max_contract_info_requests,
            max_followup_errors=args.max_followup_errors,
            search_terms=args.search_terms,
            forecastx_doc_seed=args.forecastx_doc_seed,
            forecastx_months=args.forecastx_months,
            seed_conids=args.seed_conids,
            output_dir=args.output_dir,
            json_output=args.json_output,
            discovery_json_output=args.discovery_json_output,
            discovery_markdown_output=args.discovery_markdown_output,
        )
    if args.command == "ibkr-forecastex-readonly-pipeline":
        return ibkr_forecastex_readonly_pipeline(
            base_url=args.base_url,
            wait_for_auth_seconds=args.wait_for_auth_seconds,
            poll_seconds=args.poll_seconds,
            search_terms=args.search_terms,
            forecastx_months=args.forecastx_months,
            output_dir=args.output_dir,
            json_output=args.json_output,
            max_contract_info_requests=args.max_contract_info_requests,
            timeout_seconds=args.timeout_seconds,
            max_followup_errors=args.max_followup_errors,
            ops_json_output=args.ops_json_output,
            ops_markdown_output=args.ops_markdown_output,
        )
    if args.command == "validate-ibkr-forecastex-manual-memo":
        return validate_ibkr_forecastex_manual_memo_cli(
            memo_json=args.memo_json,
            json_output=args.json_output,
        )
    if args.command == "inspect-prophetx-fixtures":
        return inspect_prophetx_fixtures(
            markets_path=args.markets,
            orderbook_path=args.orderbook,
            settlement_path=args.settlement,
            fee_path=args.fees,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
    if args.command == "source-smoke":
        return source_smoke(
            max_markets=args.max_markets,
            timeout_seconds=args.timeout_seconds,
            the_odds_api_sport_key=args.the_odds_api_sport_key,
            output=args.output,
        )
    if args.command == "discover-live-source-inventory":
        return discover_live_source_inventory(
            limit=args.limit,
            timeout_seconds=args.timeout_seconds,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
    if args.command == "fetch-live-readonly":
        return fetch_live_readonly(
            sources=args.sources,
            max_markets=args.max_markets,
            timeout_seconds=args.timeout_seconds,
            the_odds_api_sport_key=args.the_odds_api_sport_key,
            output_dir=args.output_dir,
        )
    if args.command == "fetch-live-overlap-universe":
        return fetch_live_overlap_universe(
            category=args.category,
            query=args.query,
            max_markets=args.max_markets,
            timeout_seconds=args.timeout_seconds,
            kalshi_max_pages=args.kalshi_max_pages,
            output_dir=args.output_dir,
            report_dir=args.report_dir,
            label=args.label,
        )
    if args.command == "sweep-live-overlap-universe":
        return sweep_live_overlap_universe(
            categories=args.categories,
            max_markets=args.max_markets,
            timeout_seconds=args.timeout_seconds,
            kalshi_max_pages=args.kalshi_max_pages,
            sleep_seconds=args.sleep_seconds,
            snapshot_dir=args.snapshot_dir,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
    if args.command == "inspect-live-snapshots":
        return inspect_live_snapshots(
            snapshot_dir=args.snapshot_dir,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
    if args.command == "match-live-readonly-snapshots":
        return match_live_readonly_snapshots(
            snapshot_dir=args.snapshot_dir,
            min_similarity=args.min_similarity,
            max_snapshot_age_hours=args.max_snapshot_age_hours,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
            include_reference_context=args.include_reference_context,
        )
    if args.command == "enrich-live-match-candidates":
        return enrich_live_match_candidates(
            match_report=args.match_report,
            snapshot_dir=args.snapshot_dir,
            timeout_seconds=args.timeout_seconds,
            max_snapshot_age_hours=args.max_snapshot_age_hours,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
    if args.command == "diagnose-live-matching":
        return diagnose_live_matching(
            snapshot_dir=args.snapshot_dir,
            min_similarity=0.68,
            top_limit=args.top_limit,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
    if args.command == "diagnose-non-sports-near-misses":
        return diagnose_non_sports_near_misses(
            sweep_report=args.sweep_report,
            min_similarity=args.min_similarity,
            top_limit=args.top_limit,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )

    scanner = RelativeValueScanner()
    candidates = scanner.scan_from_adapters(build_fixture_adapters(args.fixture_dir), include_ignore=args.include_ignore)
    json_path = args.output_dir / "relative_value_candidates.json"
    md_path = args.output_dir / "relative_value_candidates.md"
    provenance = build_fixture_scan_provenance(args.fixture_dir)
    write_json_report(candidates, json_path, provenance=provenance)
    write_markdown_report(candidates, md_path, provenance=provenance)

    possible_arbs = sum(1 for candidate in candidates if candidate.action.value == "POSSIBLE_ARB")
    print(
        f"relative_value_scan_status=OFFLINE_COMPLETE candidates={len(candidates)} "
        f"possible_arbs={possible_arbs} data_source_mode={provenance['data_source_mode']} "
        f"live_fetch_attempted={str(provenance['live_fetch_attempted']).lower()} "
        f"json={json_path} markdown={md_path}"
    )
    return 0


def source_readiness(output: Path | None = None) -> int:
    report = source_readiness_report()
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print("source_readiness_status=OK default_scan_data_source_mode=STATIC_FIXTURE default_scan_live_fetch_attempted=false")
    for row in report["rows"]:
        print(
            "source="
            f"{row['source_id']} type={row['source_type']} account_needed={row['account_needed']} "
            f"api_key_needed={row['api_key_needed']} api_key_env_var={row['api_key_env_var'] or 'none'} "
            f"api_key_configured={str(row['api_key_configured']).lower()} "
            f"live_fetch_implemented={str(row['live_fetch_implemented']).lower()} "
            f"used_by_scan_py={str(row['live_fetch_currently_used_by_scan_py']).lower()} "
            f"mode={row['source_mode_currently_used']} "
            f"can_participate_in_candidate_pair={str(row['can_participate_in_candidate_pair']).lower()} "
            f"can_create_paper_candidate={str(row['can_create_paper_candidate']).lower()}"
        )
    if output is not None:
        print(f"source_readiness_output={output}")
    return 0


def executable_venue_readiness(*, json_output: Path, markdown_output: Path, load_env_file: bool = True) -> int:
    if load_env_file:
        _load_local_env_safely()
    report = build_executable_venue_readiness_report()
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(_executable_venue_readiness_markdown(report), encoding="utf-8")
    recommendation = report["recommended_next_adapter_candidate"]
    print(
        "executable_venue_readiness_status=OK "
        "default_scan_data_source_mode=STATIC_FIXTURE "
        "default_scan_live_fetch_attempted=false "
        f"recommended_next={recommendation['source_id']} "
        f"json={json_output} markdown={markdown_output}"
    )
    for row in report["rows"]:
        print(
            "executable_venue_readiness_row "
            f"source={row['source_id']} type={row['source_type']} "
            f"status={row['implementation_status']} "
            f"env_configured={_env_configured_display(row['env_configured'])} "
            f"live_readonly_research_fetch_exists={str(row['live_readonly_research_fetch_exists']).lower()} "
            f"live_readonly_candidate_adapter_exists={str(row['live_readonly_candidate_adapter_exists']).lower()} "
            f"live_readonly_adapter_exists={str(row['live_readonly_adapter_exists']).lower()} "
            f"fixture_research_schema_exists={str(row['fixture_research_schema_exists']).lower()} "
            f"execution_allowed_now={str(row['execution_allowed_in_project_now']).lower()} "
            f"can_create_candidate_pair_now={str(row['can_create_candidate_pair_now']).lower()} "
            f"can_create_paper_candidate_now={str(row['can_create_paper_candidate_now']).lower()}"
        )
    return 0


def inspect_ibkr_forecastex_fixtures(
    *,
    instruments_path: Path,
    quotes_path: Path,
    settlement_path: Path,
    json_output: Path,
    markdown_output: Path,
) -> int:
    try:
        snapshot = load_ibkr_forecastex_research_fixtures(
            instruments_path=instruments_path,
            quotes_path=quotes_path,
            settlement_path=settlement_path,
        )
        status = "OK"
        failure_reason = None
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        snapshot = _ibkr_forecastex_fixture_failure_snapshot(
            instruments_path=instruments_path,
            quotes_path=quotes_path,
            settlement_path=settlement_path,
            exc=exc,
        )
        status = "FAILED"
        failure_reason = snapshot["failure_reason"]
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(_ibkr_forecastex_fixture_markdown(snapshot), encoding="utf-8")
    print(
        f"ibkr_forecastex_fixture_inspection_status={status} "
        "live_fetch_attempted=false "
        f"schema_kind={snapshot.get('schema_kind')} "
        f"research_markets={snapshot.get('research_market_count', 0)} "
        "is_executable=false "
        "can_create_candidate_pair=false "
        "can_create_paper_candidate=false "
        f"failure_reason={failure_reason or 'none'} "
        f"json={json_output} markdown={markdown_output}"
    )
    return 0 if status == "OK" else 1


def ibkr_forecastex_access_doctor(
    *,
    base_url: str,
    timeout_seconds: float,
    json_output: Path,
) -> int:
    report = write_ibkr_forecastex_access_doctor_file(
        json_output=json_output,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
    )
    blockers = ",".join(report.get("blockers", [])[:5])
    print(
        "ibkr_forecastex_access_doctor_status={status} reachable={reachable} authenticated={authenticated} "
        "blockers={blockers} json={json}".format(
            status=report.get("status"),
            reachable=str(report.get("reachable")).lower(),
            authenticated=str(report.get("authenticated")).lower(),
            blockers=blockers or "none",
            json=json_output,
        )
    )
    return 0


def fetch_ibkr_forecastex_readonly(
    *,
    base_url: str,
    timeout_seconds: float,
    max_contracts: int,
    max_contract_info_requests: int,
    max_followup_errors: int,
    search_terms: str | None,
    forecastx_doc_seed: bool,
    forecastx_months: str | None,
    seed_conids: Path | None,
    output_dir: Path,
    json_output: Path,
    discovery_json_output: Path,
    discovery_markdown_output: Path,
) -> int:
    report = write_ibkr_forecastex_readonly_snapshot_file(
        output_dir=output_dir,
        json_output=json_output,
        discovery_json_output=discovery_json_output,
        discovery_markdown_output=discovery_markdown_output,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        max_contracts=max_contracts,
        max_contract_info_requests=max_contract_info_requests,
        max_followup_errors=max_followup_errors,
        search_terms=search_terms,
        forecastx_doc_seed=forecastx_doc_seed,
        forecastx_months=forecastx_months,
        seed_conids_path=seed_conids,
    )
    summary = report.get("summary", {})
    blockers = ",".join(report.get("blockers", [])[:5])
    print(
        "ibkr_forecastex_readonly_fetch_status={status} reachable={reachable} authenticated={authenticated} "
        "discovery_status={discovery_status} candidates={candidates} rows={rows} raw_files_written={raw_files} "
        "final_tradable_rows={final_tradable_rows} yes_rows={yes_rows} no_rows={no_rows} "
        "quote_rows_mapped={quote_rows_mapped} quote_bid_rows={quote_bid_rows} quote_ask_rows={quote_ask_rows} "
        "quote_bid_ask_rows={quote_bid_ask_rows} quote_bid_ask_size_rows={quote_bid_ask_size_rows} "
        "quote_timestamp_rows={quote_timestamp_rows} quote_complete_rows={quote_complete_rows} quote_execution_ready_rows={quote_execution_ready_rows} "
        "months_attempted={months_attempted} strikes_found={strikes_found} info_requests={info_requests} "
        "search_requests={search_requests} followup_requests={followup_requests} followup_errors={followup_errors} "
        "blockers={blockers} json={json} discovery_json={discovery_json}".format(
            status=report.get("status"),
            reachable=str(report.get("reachable")).lower(),
            authenticated=str(report.get("authenticated")).lower(),
            discovery_status=summary.get("discovery_status"),
            candidates=summary.get("forecastx_candidate_count", 0),
            rows=summary.get("normalized_rows", 0),
            raw_files=summary.get("raw_files_written", 0),
            final_tradable_rows=summary.get("final_tradable_rows", 0),
            yes_rows=summary.get("forecastx_yes_rows", 0),
            no_rows=summary.get("forecastx_no_rows", 0),
            quote_rows_mapped=summary.get("ibkr_quote_rows_mapped_to_contracts", 0),
            quote_bid_rows=summary.get("ibkr_quote_rows_with_bid", 0),
            quote_ask_rows=summary.get("ibkr_quote_rows_with_ask", 0),
            quote_bid_ask_rows=summary.get("ibkr_quote_rows_with_bid_ask", 0),
            quote_bid_ask_size_rows=summary.get("ibkr_quote_rows_with_bid_ask_size", 0),
            quote_timestamp_rows=summary.get("ibkr_quote_rows_with_timestamp", 0),
            quote_complete_rows=summary.get("ibkr_quote_rows_quote_diagnostic_complete", 0),
            quote_execution_ready_rows=summary.get("ibkr_quote_rows_execution_ready", 0),
            months_attempted=summary.get("forecastx_option_months_attempted", 0),
            strikes_found=summary.get("forecastx_strikes_found", 0),
            info_requests=summary.get("forecastx_info_requests", 0),
            search_requests=summary.get("search_requests_attempted", 0),
            followup_requests=summary.get("followup_requests_attempted", 0),
            followup_errors=summary.get("followup_errors", 0),
            blockers=blockers or "none",
            json=json_output,
            discovery_json=discovery_json_output,
        )
    )
    return 0


def ibkr_forecastex_readonly_pipeline(
    *,
    base_url: str,
    wait_for_auth_seconds: int,
    poll_seconds: float,
    search_terms: str | None,
    forecastx_months: str,
    output_dir: Path,
    json_output: Path,
    max_contract_info_requests: int,
    timeout_seconds: float,
    max_followup_errors: int,
    ops_json_output: Path,
    ops_markdown_output: Path,
) -> int:
    wait_seconds = min(max(0, int(wait_for_auth_seconds)), 600)
    poll_interval = max(0.0, float(poll_seconds))
    access_json_output = ops_json_output.parent / "ibkr_forecastex_access_doctor.json"
    report = _write_pipeline_access_doctor(
        access_json_output=access_json_output,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
    )
    if not report.get("reachable"):
        print(
            "ibkr_forecastex_readonly_pipeline_status=GATEWAY_UNREACHABLE "
            "manual_action=start_IBKR_Client_Portal_Gateway access_doctor_json={json}".format(
                json=access_json_output
            )
        )
        return 1
    if not report.get("authenticated"):
        if wait_seconds > 0:
            deadline = time.monotonic() + wait_seconds
            while not report.get("authenticated"):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if poll_interval > 0:
                    time.sleep(min(poll_interval, remaining))
                report = _write_pipeline_access_doctor(
                    access_json_output=access_json_output,
                    base_url=base_url,
                    timeout_seconds=timeout_seconds,
                )
                if poll_interval <= 0:
                    break
        if not report.get("authenticated"):
            print(
                "ibkr_forecastex_readonly_pipeline_status=AUTH_REQUIRED "
                "manual_action=open_https_localhost_5000_and_log_in wait_seconds={wait} access_doctor_json={json}".format(
                    wait=wait_seconds,
                    json=access_json_output,
                )
            )
            return 1

    discovery_json_output = json_output.parent / "ibkr_forecastex_discovery_candidates.json"
    discovery_markdown_output = json_output.parent / "ibkr_forecastex_discovery_candidates.md"
    fetch_report = write_ibkr_forecastex_readonly_snapshot_file(
        output_dir=output_dir,
        json_output=json_output,
        discovery_json_output=discovery_json_output,
        discovery_markdown_output=discovery_markdown_output,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        max_contract_info_requests=max_contract_info_requests,
        max_followup_errors=max_followup_errors,
        search_terms=search_terms,
        forecastx_doc_seed=True,
        forecastx_months=forecastx_months,
        seed_conids_path=None,
    )
    ops_report = write_relative_value_ops_status_files(
        input_dir=ops_json_output.parent,
        json_output=ops_json_output,
        markdown_output=ops_markdown_output,
    )
    summary = fetch_report.get("summary", {})
    next_action = ops_report.get("highest_priority_next_action") or {}
    print(
        "ibkr_forecastex_readonly_pipeline_status=OK fetch_status={fetch_status} "
        "final_tradable_rows={final_rows} quote_complete_rows={quote_complete_rows} "
        "execution_ready_rows={execution_ready_rows} ops_next_action={ops_next_action} "
        "json={json} ops_json={ops_json}".format(
            fetch_status=fetch_report.get("status"),
            final_rows=summary.get("final_tradable_rows", 0),
            quote_complete_rows=summary.get("ibkr_quote_rows_quote_diagnostic_complete", 0),
            execution_ready_rows=summary.get("ibkr_quote_rows_execution_ready", 0),
            ops_next_action=next_action.get("action"),
            json=json_output,
            ops_json=ops_json_output,
        )
    )
    return 0


def _write_pipeline_access_doctor(
    *,
    access_json_output: Path,
    base_url: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    report = build_ibkr_forecastex_access_doctor(
        base_url=base_url,
        timeout_seconds=timeout_seconds,
    )
    access_json_output.parent.mkdir(parents=True, exist_ok=True)
    access_json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def validate_ibkr_forecastex_manual_memo_cli(
    *,
    memo_json: Path,
    json_output: Path,
) -> int:
    report = validate_ibkr_forecastex_manual_memo_file(
        memo_json=memo_json,
        json_output=json_output,
    )
    summary = report.get("summary", {})
    blockers = ",".join(report.get("blockers", [])[:5])
    print(
        "ibkr_forecastex_manual_memo_validation_status={status} diagnostic_only=true "
        "missing_required_fields={missing} validation_blocker_count={blocker_count} "
        "memo_credibility_for_downstream_merge={memo_credibility} "
        "can_create_candidate_pair=false paper_candidate_emitted=false blockers={blockers} json={json}".format(
            status="OK" if report.get("validation_passed") else "BLOCKED",
            missing=summary.get("missing_required_fields", 0),
            blocker_count=summary.get("validation_blocker_count", 0),
            memo_credibility=str(summary.get("memo_credibility_for_downstream_merge")).lower(),
            blockers=blockers or "none",
            json=json_output,
        )
    )
    return 0 if report.get("validation_passed") else 1


def _ibkr_forecastex_fixture_failure_snapshot(
    *,
    instruments_path: Path,
    quotes_path: Path,
    settlement_path: Path,
    exc: Exception,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "schema_kind": IBKR_FORECASTEX_RESEARCH_SCHEMA_KIND,
        "source": "ibkr_forecastex_research",
        "source_id": "forecastex_ibkr",
        "source_type": "EXECUTABLE_VENUE",
        "implementation_status": "PLANNED_NOT_IMPLEMENTED",
        "permission": "FIXTURE_RESEARCH_ONLY",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "live_fetch_attempted": False,
        "live_fetch_succeeded": False,
        "is_executable": False,
        "execution_allowed_in_project_now": False,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "instrument_count": 0,
        "quote_count": 0,
        "settlement_count": 0,
        "research_market_count": 0,
        "research_markets": [],
        "unresolved_blockers": list(IBKR_FORECASTEX_REQUIRED_BLOCKERS),
        "fixture_paths": {
            "instruments": str(instruments_path),
            "quotes": str(quotes_path),
            "settlement": str(settlement_path),
        },
        "failure_reason": f"{type(exc).__name__}: {exc}",
    }


def _ibkr_forecastex_fixture_markdown(snapshot: dict[str, Any]) -> str:
    lines = [
        "# IBKR / ForecastEx Fixture Inspection",
        "",
        "Fixture-only inspection for the planned IBKR / ForecastEx read-only research schema.",
        "",
        "- Live fetch attempted: `false`",
        "- Source role: `planned_executable_venue_research_only`",
        "- Is executable: `false`",
        "- Candidate-pair eligible: `false`",
        "- Paper-candidate eligible: `false`",
        f"- Schema kind: `{snapshot.get('schema_kind')}`",
        f"- Research markets: `{snapshot.get('research_market_count', 0)}`",
    ]
    if snapshot.get("failure_reason"):
        lines.append(f"- Failure reason: `{snapshot['failure_reason']}`")
    lines.extend(
        [
            "",
            "## Unresolved Blockers",
            "",
        ]
    )
    for blocker in snapshot.get("unresolved_blockers", []):
        lines.append(f"- `{blocker}`")
    lines.extend(
        [
            "",
            "## Research Markets",
            "",
            "| Instrument | Title | Bid | Ask | Market data timestamp | Fee status | Blockers |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in snapshot.get("research_markets", []):
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(row.get("instrument_id")),
                    _markdown_cell(row.get("contract_title") or row.get("question")),
                    _markdown_cell(row.get("best_bid")),
                    _markdown_cell(row.get("best_ask")),
                    _markdown_cell(row.get("market_data_timestamp")),
                    _markdown_cell(row.get("fee_commission_status")),
                    _markdown_cell(",".join(row.get("unresolved_blockers") or [])),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def inspect_prophetx_fixtures(
    *,
    markets_path: Path,
    orderbook_path: Path,
    settlement_path: Path,
    fee_path: Path,
    json_output: Path,
    markdown_output: Path,
) -> int:
    try:
        snapshot = load_prophetx_research_fixtures(
            markets_path=markets_path,
            orderbook_path=orderbook_path,
            settlement_path=settlement_path,
            fee_path=fee_path,
        )
        status = "OK"
        failure_reason = None
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        snapshot = _prophetx_fixture_failure_snapshot(
            markets_path=markets_path,
            orderbook_path=orderbook_path,
            settlement_path=settlement_path,
            fee_path=fee_path,
            exc=exc,
        )
        status = "FAILED"
        failure_reason = snapshot["failure_reason"]
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(_prophetx_fixture_markdown(snapshot), encoding="utf-8")
    print(
        f"prophetx_fixture_inspection_status={status} "
        "live_fetch_attempted=false "
        f"schema_kind={snapshot.get('schema_kind')} "
        f"research_markets={snapshot.get('research_market_count', 0)} "
        "is_executable=false "
        "can_create_candidate_pair=false "
        "can_create_paper_candidate=false "
        f"failure_reason={failure_reason or 'none'} "
        f"json={json_output} markdown={markdown_output}"
    )
    return 0 if status == "OK" else 1


def _prophetx_fixture_failure_snapshot(
    *,
    markets_path: Path,
    orderbook_path: Path,
    settlement_path: Path,
    fee_path: Path,
    exc: Exception,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "schema_kind": PROPHETX_RESEARCH_SCHEMA_KIND,
        "source": "prophetx_research",
        "source_id": "prophetx",
        "source_type": "EXECUTABLE_VENUE",
        "implementation_status": "PLANNED_NOT_IMPLEMENTED",
        "permission": "FIXTURE_RESEARCH_ONLY",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "live_fetch_attempted": False,
        "live_fetch_succeeded": False,
        "is_executable": False,
        "execution_allowed_in_project_now": False,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "market_count": 0,
        "orderbook_count": 0,
        "settlement_count": 0,
        "fee_count": 0,
        "research_market_count": 0,
        "research_markets": [],
        "unresolved_blockers": list(PROPHETX_REQUIRED_BLOCKERS),
        "fixture_paths": {
            "markets": str(markets_path),
            "orderbook": str(orderbook_path),
            "settlement": str(settlement_path),
            "fees": str(fee_path),
        },
        "failure_reason": f"{type(exc).__name__}: {exc}",
    }


def _prophetx_fixture_markdown(snapshot: dict[str, Any]) -> str:
    lines = [
        "# ProphetX Fixture Inspection",
        "",
        "Fixture-only inspection for the planned ProphetX read-only research schema.",
        "",
        "- Live fetch attempted: `false`",
        "- Source role: `planned_executable_venue_research_only`",
        "- Is executable: `false`",
        "- Candidate-pair eligible: `false`",
        "- Paper-candidate eligible: `false`",
        f"- Schema kind: `{snapshot.get('schema_kind')}`",
        f"- Research markets: `{snapshot.get('research_market_count', 0)}`",
    ]
    if snapshot.get("failure_reason"):
        lines.append(f"- Failure reason: `{snapshot['failure_reason']}`")
    lines.extend(
        [
            "",
            "## Unresolved Blockers",
            "",
        ]
    )
    for blocker in snapshot.get("unresolved_blockers", []):
        lines.append(f"- `{blocker}`")
    lines.extend(
        [
            "",
            "## Research Markets",
            "",
            "| Market | Title | Bid | Ask | Market data timestamp | Fee status | Blockers |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in snapshot.get("research_markets", []):
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(row.get("market_id")),
                    _markdown_cell(row.get("title") or row.get("question")),
                    _markdown_cell(row.get("best_bid")),
                    _markdown_cell(row.get("best_ask")),
                    _markdown_cell(row.get("market_data_timestamp")),
                    _markdown_cell(row.get("fee_commission_status")),
                    _markdown_cell(",".join(row.get("unresolved_blockers") or [])),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def build_executable_venue_readiness_report(*, env: dict[str, str] | None = None) -> dict[str, Any]:
    environment = env if env is not None else os.environ
    rows = [_executable_venue_readiness_row(source_id, environment) for source_id in _EXECUTABLE_READINESS_SOURCE_ORDER]
    recommendation = _recommended_executable_venue_row(rows)
    return {
        "schema_version": 1,
        "source": "executable_venue_readiness",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "default_scan_data_source_mode": "STATIC_FIXTURE",
        "default_scan_live_fetch_attempted": False,
        "live_api_fetch_attempted": False,
        "research_only": True,
        "readiness_promotion": "none",
        "recommended_next_adapter_candidate": recommendation,
        "rows": rows,
        "safety": {
            "execution_enabled": False,
            "thresholds_changed": False,
            "uses_reference_as_executable_leg": False,
            "uses_the_odds_api_as_executable_leg": False,
            "planned_sources_create_candidate_pairs": False,
            "readiness_promotion": "none",
        },
        "disclaimer": (
            "Readiness audit only. This command does not fetch live markets, does not inspect accounts, "
            "does not grant paper/live readiness, and does not enable execution."
        ),
    }


_EXECUTABLE_READINESS_SOURCE_ORDER = (
    "kalshi",
    "polymarket",
    "forecastex_ibkr",
    "sx_bet",
    "prophetx",
    "crypto_com",
    "robinhood",
    "the_odds_api",
)


def _executable_venue_readiness_row(source_id: str, env: dict[str, str]) -> dict[str, Any]:
    definitions = _EXECUTABLE_READINESS_DEFINITIONS[source_id]
    entry = SOURCE_REGISTRY.get(source_id)
    capability = PLANNED_EXECUTABLE_VENUE_CAPABILITIES.get(source_id)
    source_type = definitions.get("source_type") or (entry.source_type.value if entry else SourceType.DO_NOT_USE_YET.value)
    implementation_status = definitions.get("implementation_status") or (
        entry.implementation_status.value if entry else "NOT_IMPLEMENTED"
    )
    expected_env_vars = list(definitions.get("expected_env_vars") or ())
    api_or_credentials_expected = bool(definitions["api_key_or_credentials_expected"])
    live_readonly_candidate_adapter_exists = bool(
        definitions.get("live_readonly_candidate_adapter_exists", definitions.get("live_readonly_adapter_exists", False))
    )
    live_readonly_research_fetch_exists = bool(
        definitions.get("live_readonly_research_fetch_exists", live_readonly_candidate_adapter_exists)
    )
    live_readonly_adapter_exists = live_readonly_candidate_adapter_exists
    execution_allowed_now = False
    can_create_candidate_pair_now = bool(
        entry
        and entry.can_create_candidate_pair
        and live_readonly_candidate_adapter_exists
        and source_type == SourceType.EXECUTABLE_VENUE.value
        and source_id in {"kalshi", "polymarket"}
    )
    return {
        "source_id": source_id,
        "display_name": definitions["display_name"],
        "source_type": source_type,
        "implementation_status": implementation_status,
        "account_required": bool(definitions["account_required"]),
        "api_key_or_credentials_expected": api_or_credentials_expected,
        "expected_env_vars": expected_env_vars,
        "env_configured": _readiness_env_configured(expected_env_vars, api_or_credentials_expected, env),
        "live_readonly_research_fetch_exists": live_readonly_research_fetch_exists,
        "live_readonly_candidate_adapter_exists": live_readonly_candidate_adapter_exists,
        "live_readonly_adapter_exists": live_readonly_adapter_exists,
        "fixture_research_schema_exists": bool(definitions.get("fixture_research_schema_exists", False)),
        "live_readonly_smoke_exists": bool(definitions["live_readonly_smoke_exists"]),
        "public_market_data_possible": bool(
            definitions.get(
                "public_market_data_possible",
                capability.has_public_market_data if capability else live_readonly_research_fetch_exists,
            )
        ),
        "orderbook_or_bidask_possible": bool(
            definitions.get(
                "orderbook_or_bidask_possible",
                capability.has_orderbook_or_bid_ask if capability else live_readonly_research_fetch_exists,
            )
        ),
        "depth_possible": bool(
            definitions.get("depth_possible", capability.has_depth if capability else live_readonly_research_fetch_exists)
        ),
        "settlement_metadata_possible": bool(
            definitions.get(
                "settlement_metadata_possible",
                capability.has_settlement_rules if capability else live_readonly_research_fetch_exists,
            )
        ),
        "execution_allowed_in_project_now": execution_allowed_now,
        "can_create_candidate_pair_now": can_create_candidate_pair_now,
        "can_create_paper_candidate_now": False,
        "next_required_step": definitions["next_required_step"],
        "blocked_reason": definitions.get("blocked_reason"),
    }


def _readiness_env_configured(expected_env_vars: list[str], credentials_expected: bool, env: dict[str, str]) -> bool | str:
    if expected_env_vars:
        return all(bool(env.get(name)) for name in expected_env_vars)
    if credentials_expected:
        return False
    return "not_applicable"


def _env_configured_display(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _recommended_executable_venue_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    for row in rows:
        if row["source_id"] == "sx_bet":
            return {
                "source_id": row["source_id"],
                "display_name": row["display_name"],
                "recommendation": "best_next_read_only_research_adapter",
                "rationale": (
                    "SX Bet is the best next adapter candidate because a public read-only research fetch exists "
                    "without project execution support, while auth-heavy venues remain higher-friction. It still has "
                    "no candidate-eligible normalized adapter and cannot create candidate pairs."
                ),
            }
    return {"source_id": "none", "display_name": "none", "recommendation": "none", "rationale": "No candidate found."}


_EXECUTABLE_READINESS_DEFINITIONS: dict[str, dict[str, Any]] = {
    "kalshi": {
        "display_name": "Kalshi",
        "account_required": False,
        "api_key_or_credentials_expected": False,
        "expected_env_vars": [],
        "live_readonly_research_fetch_exists": True,
        "live_readonly_candidate_adapter_exists": True,
        "live_readonly_adapter_exists": True,
        "live_readonly_smoke_exists": True,
        "public_market_data_possible": True,
        "orderbook_or_bidask_possible": True,
        "depth_possible": True,
        "settlement_metadata_possible": True,
        "next_required_step": "Already implemented as read-only; continue using as one executable research leg with relationship/depth/fee/freshness gates.",
        "blocked_reason": None,
    },
    "polymarket": {
        "display_name": "Polymarket",
        "account_required": False,
        "api_key_or_credentials_expected": False,
        "expected_env_vars": [],
        "live_readonly_research_fetch_exists": True,
        "live_readonly_candidate_adapter_exists": True,
        "live_readonly_adapter_exists": True,
        "live_readonly_smoke_exists": True,
        "public_market_data_possible": True,
        "orderbook_or_bidask_possible": True,
        "depth_possible": True,
        "settlement_metadata_possible": True,
        "next_required_step": "Already implemented as read-only; continue using as one executable research leg with relationship/depth/fee/freshness gates.",
        "blocked_reason": None,
    },
    "forecastex_ibkr": {
        "display_name": "IBKR / ForecastEx",
        "account_required": True,
        "api_key_or_credentials_expected": True,
        "expected_env_vars": ["IBKR_HOST", "IBKR_PORT", "IBKR_CLIENT_ID", "IBKR_ACCOUNT_ID"],
        "live_readonly_research_fetch_exists": False,
        "live_readonly_candidate_adapter_exists": False,
        "live_readonly_adapter_exists": False,
        "live_readonly_smoke_exists": False,
        "fixture_research_schema_exists": True,
        "next_required_step": "Complete manual eligibility/API-permission review and settlement wording catalog, then review fixture-backed schemas before any live IBKR transport.",
        "blocked_reason": "NOT_IMPLEMENTED; fixture-backed schema exists, but account permission, instrument mapping, settlement terms, fees, and read-only API boundary are not yet reviewed.",
    },
    "sx_bet": {
        "display_name": "SX Bet",
        "account_required": False,
        "api_key_or_credentials_expected": False,
        "expected_env_vars": [],
        "live_readonly_research_fetch_exists": True,
        "live_readonly_candidate_adapter_exists": False,
        "live_readonly_adapter_exists": False,
        "live_readonly_smoke_exists": False,
        "next_required_step": "Use the existing research-only fetch for overlap review; build a separate candidate-eligible normalized adapter only after fee/depth/settlement/restriction review.",
        "blocked_reason": "Research fetch exists, but no candidate-eligible adapter; fee/depth units/settlement wording/venue restrictions remain unreviewed.",
    },
    "prophetx": {
        "display_name": "ProphetX",
        "source_type": SourceType.EXECUTABLE_VENUE.value,
        "implementation_status": "PLANNED_NOT_IMPLEMENTED",
        "account_required": True,
        "api_key_or_credentials_expected": True,
        "expected_env_vars": ["PROPHETX_BASE_URL", "PROPHETX_API_KEY"],
        "live_readonly_research_fetch_exists": False,
        "live_readonly_candidate_adapter_exists": False,
        "live_readonly_adapter_exists": False,
        "live_readonly_smoke_exists": False,
        "public_market_data_possible": True,
        "orderbook_or_bidask_possible": True,
        "depth_possible": True,
        "settlement_metadata_possible": True,
        "fixture_research_schema_exists": True,
        "next_required_step": "Fixture-backed schemas exist; next complete manual eligibility/API-permission review and settlement-wording catalog before any live ProphetX transport.",
        "blocked_reason": "PLANNED_NOT_IMPLEMENTED; fixture-backed schema exists, but API access, endpoint scope, venue restrictions, fees, and settlement metadata are not reviewed.",
    },
    "crypto_com": {
        "display_name": "Crypto.com",
        "source_type": SourceType.DO_NOT_USE_YET.value,
        "implementation_status": "NOT_IMPLEMENTED",
        "account_required": True,
        "api_key_or_credentials_expected": True,
        "expected_env_vars": [],
        "live_readonly_research_fetch_exists": False,
        "live_readonly_candidate_adapter_exists": False,
        "live_readonly_adapter_exists": False,
        "live_readonly_smoke_exists": False,
        "public_market_data_possible": False,
        "orderbook_or_bidask_possible": False,
        "depth_possible": False,
        "settlement_metadata_possible": False,
        "next_required_step": "Confirm prediction-market product fit, API permissions, and settlement schema before any adapter work.",
        "blocked_reason": "NOT_IMPLEMENTED; product/schema fit not reviewed.",
    },
    "robinhood": {
        "display_name": "Robinhood",
        "source_type": SourceType.DO_NOT_USE_YET.value,
        "implementation_status": "NOT_IMPLEMENTED",
        "account_required": True,
        "api_key_or_credentials_expected": True,
        "expected_env_vars": [],
        "live_readonly_research_fetch_exists": False,
        "live_readonly_candidate_adapter_exists": False,
        "live_readonly_adapter_exists": False,
        "live_readonly_smoke_exists": False,
        "public_market_data_possible": False,
        "orderbook_or_bidask_possible": False,
        "depth_possible": False,
        "settlement_metadata_possible": False,
        "next_required_step": "Confirm permitted read-only API access and prediction-market instrument coverage before any adapter work.",
        "blocked_reason": "NOT_IMPLEMENTED; API permission/instrument fit not reviewed.",
    },
    "the_odds_api": {
        "display_name": "The Odds API",
        "account_required": True,
        "api_key_or_credentials_expected": True,
        "expected_env_vars": ["THE_ODDS_API_KEY"],
        "live_readonly_research_fetch_exists": True,
        "live_readonly_candidate_adapter_exists": False,
        "live_readonly_adapter_exists": True,
        "live_readonly_smoke_exists": True,
        "public_market_data_possible": True,
        "orderbook_or_bidask_possible": False,
        "depth_possible": False,
        "settlement_metadata_possible": False,
        "next_required_step": "Use only as REFERENCE_ONLY context; never as an executable candidate leg.",
        "blocked_reason": "REFERENCE_ONLY; sportsbook odds are not executable prices in this scanner.",
    },
}


def _executable_venue_readiness_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Executable Venue Readiness",
        "",
        "Explicit audit for read-only adapter readiness. This report does not fetch live data, inspect accounts, grant readiness, or enable execution.",
        "",
        f"- Recommended next adapter candidate: `{report['recommended_next_adapter_candidate']['source_id']}`",
        f"- Rationale: {report['recommended_next_adapter_candidate']['rationale']}",
        f"- Default scan mode: `{report['default_scan_data_source_mode']}`",
        "",
        "| Source | Type | Status | Env configured | Research fetch | Fixture schema | Candidate adapter | Adapter alias | Smoke | Public data | Bid/ask | Depth | Settlement | Candidate pair now | Paper candidate now | Next step |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in report["rows"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(row["display_name"]),
                    _markdown_cell(row["source_type"]),
                    _markdown_cell(row["implementation_status"]),
                    _markdown_cell(_env_configured_display(row["env_configured"])),
                    _markdown_cell(_yes_no(row["live_readonly_research_fetch_exists"])),
                    _markdown_cell(_yes_no(row["fixture_research_schema_exists"])),
                    _markdown_cell(_yes_no(row["live_readonly_candidate_adapter_exists"])),
                    _markdown_cell(_yes_no(row["live_readonly_adapter_exists"])),
                    _markdown_cell(_yes_no(row["live_readonly_smoke_exists"])),
                    _markdown_cell(_yes_no(row["public_market_data_possible"])),
                    _markdown_cell(_yes_no(row["orderbook_or_bidask_possible"])),
                    _markdown_cell(_yes_no(row["depth_possible"])),
                    _markdown_cell(_yes_no(row["settlement_metadata_possible"])),
                    _markdown_cell(_yes_no(row["can_create_candidate_pair_now"])),
                    _markdown_cell(_yes_no(row["can_create_paper_candidate_now"])),
                    _markdown_cell(row["next_required_step"]),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def source_smoke(
    *,
    max_markets: int = 3,
    timeout_seconds: float = 10.0,
    the_odds_api_sport_key: str = "basketball_nba",
    output: Path | None = None,
    load_env_file: bool = True,
) -> int:
    if load_env_file:
        _load_local_env_safely()
    report = build_source_smoke_report(
        max_markets=max_markets,
        timeout_seconds=timeout_seconds,
        the_odds_api_sport_key=the_odds_api_sport_key,
    )
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(
        "source_smoke_status=OK "
        "default_scan_data_source_mode=STATIC_FIXTURE "
        "default_scan_live_fetch_attempted=false"
    )
    for row in report["rows"]:
        print(_format_source_smoke_row(row))
    if output is not None:
        print(f"source_smoke_output={output}")
    return 0


def build_source_smoke_report(
    *,
    max_markets: int = 3,
    timeout_seconds: float = 10.0,
    the_odds_api_sport_key: str = "basketball_nba",
) -> dict[str, Any]:
    if max_markets <= 0:
        raise ValueError("max_markets must be positive")
    captured_at = datetime.now(timezone.utc)
    rows = [
        _smoke_kalshi(max_markets=max_markets, timeout_seconds=timeout_seconds),
        _smoke_polymarket(max_markets=max_markets, timeout_seconds=timeout_seconds),
        _smoke_the_odds_api(
            sport_key=the_odds_api_sport_key,
            timeout_seconds=timeout_seconds,
        ),
    ]
    rows.extend(_not_implemented_smoke_rows())
    return {
        "schema_version": 1,
        "source": "source_smoke",
        "captured_at": captured_at.isoformat(),
        "data_source_mode": "LIVE_API",
        "default_scan_data_source_mode": "STATIC_FIXTURE",
        "default_scan_live_fetch_attempted": False,
        "rows": rows,
    }


def discover_live_source_inventory(
    *,
    limit: int,
    timeout_seconds: float,
    json_output: Path,
    markdown_output: Path,
) -> int:
    report = build_live_source_inventory_report(limit=limit, timeout_seconds=timeout_seconds)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(_live_source_inventory_markdown(report), encoding="utf-8")
    print(
        "live_source_inventory_status="
        f"{report['status']} kalshi_status={report['sources']['kalshi']['status']} "
        f"kalshi_records={report['sources']['kalshi']['record_count']} "
        f"polymarket_status={report['sources']['polymarket']['status']} "
        f"polymarket_records={report['sources']['polymarket']['record_count']} "
        f"json={json_output} markdown={markdown_output}"
    )
    return 0 if report["status"] in {"OK", "PARTIAL"} else 1


def build_live_source_inventory_report(
    *,
    limit: int = 500,
    timeout_seconds: float = 10.0,
    kalshi_client: Any | None = None,
    polymarket_client: Any | None = None,
) -> dict[str, Any]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    generated_at = datetime.now(timezone.utc)
    kalshi_client = kalshi_client or KalshiReadOnlyClient(
        base_url=os.environ.get("KALSHI_BASE_URL", "https://external-api.kalshi.com/trade-api/v2"),
        timeout_seconds=timeout_seconds,
    )
    polymarket_client = polymarket_client or PolymarketGammaClient(
        base_url=os.environ.get("POLYMARKET_GAMMA_BASE_URL", "https://gamma-api.polymarket.com"),
        timeout_seconds=timeout_seconds,
    )
    sources = {
        "kalshi": _discover_kalshi_inventory(kalshi_client, limit=limit),
        "polymarket": _discover_polymarket_inventory(polymarket_client, limit=limit),
    }
    completed = [source for source in sources.values() if source["status"] == "OK"]
    status = "OK" if len(completed) == len(sources) else "PARTIAL" if completed else "FAILED"
    analysis = _live_source_inventory_analysis(sources)
    return {
        "schema_version": 1,
        "source": "live_source_inventory",
        "status": status,
        "generated_at": generated_at.isoformat(),
        "data_source_mode": "LIVE_API",
        "default_scan_data_source_mode": "STATIC_FIXTURE",
        "default_scan_live_fetch_attempted": False,
        "research_only": True,
        "readiness_promotion": "none",
        "profile_table_modified": False,
        "sources": sources,
        "analysis": analysis,
        "safety": {
            "execution_enabled": False,
            "uses_reference_as_executable_leg": False,
            "uses_the_odds_api_as_executable_leg": False,
            "thresholds_changed": False,
            "same_payoff_asserted": False,
            "profit_claim": False,
            "profile_table_auto_modified": False,
        },
        "disclaimer": (
            "Public source inventory discovery is human-review-only. Discovery does not imply overlap, edge, "
            "same-payoff, paper readiness, live readiness, or tradability."
        ),
    }


def _discover_kalshi_inventory(client: Any, *, limit: int) -> dict[str, Any]:
    try:
        raw = client.fetch_series_inventory(limit=limit)
        records = _normalize_kalshi_series_inventory(raw)
    except Exception as exc:
        return _failed_inventory_source("kalshi", "series_inventory", exc)
    return {
        "source_id": "kalshi",
        "inventory_kind": "series_inventory",
        "status": "OK",
        "live_fetch_attempted": True,
        "live_fetch_succeeded": True,
        "record_count": len(records),
        "requested_limit": limit,
        "pagination_or_server_cap_possible": False,
        "records": records,
        "error_category": None,
        "failure_reason": None,
    }


def _discover_polymarket_inventory(client: Any, *, limit: int) -> dict[str, Any]:
    try:
        raw = client.fetch_tag_inventory(limit=limit)
        records = _normalize_polymarket_tag_inventory(raw)
    except Exception as exc:
        return _failed_inventory_source("polymarket", "tag_inventory", exc)
    return {
        "source_id": "polymarket",
        "inventory_kind": "tag_inventory",
        "status": "OK",
        "live_fetch_attempted": True,
        "live_fetch_succeeded": True,
        "record_count": len(records),
        "requested_limit": limit,
        "pagination_or_server_cap_possible": len(records) == 100 and limit > 100,
        "records": records,
        "error_category": None,
        "failure_reason": None,
    }


def _failed_inventory_source(source_id: str, inventory_kind: str, exc: Exception) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "inventory_kind": inventory_kind,
        "status": "FAILED",
        "live_fetch_attempted": True,
        "live_fetch_succeeded": False,
        "record_count": 0,
        "requested_limit": None,
        "pagination_or_server_cap_possible": False,
        "records": [],
        "error_category": _error_category(exc),
        "failure_reason": _safe_cli_text(exc),
    }


def _normalize_kalshi_series_inventory(raw: Any) -> list[dict[str, Any]]:
    records = []
    for row in _inventory_items(raw, preferred_keys=("series", "data", "results")):
        ticker = _inventory_string(row, "series_ticker", "ticker", "id")
        if not ticker:
            continue
        records.append(
            {
                "source_id": "kalshi",
                "series_ticker": ticker,
                "title": _inventory_string(row, "title", "name", "series_title"),
                "category_hint": _inventory_string(row, "category", "category_hint", "product", "frequency"),
                "active_market_count": _inventory_int(row, "active_market_count", "open_market_count", "market_count"),
                "status": _inventory_string(row, "status", "state"),
                "sample_markets": _inventory_sample_markets(row),
            }
        )
    return sorted(records, key=lambda item: item["series_ticker"])


def _normalize_polymarket_tag_inventory(raw: Any) -> list[dict[str, Any]]:
    records = []
    for row in _inventory_items(raw, preferred_keys=("tags", "data", "results")):
        tag_slug = _inventory_string(row, "slug", "tag_slug")
        label = _inventory_string(row, "label", "name", "title")
        if not tag_slug and label:
            tag_slug = _polymarket_tag_slug_from_label(label)
        if not tag_slug:
            continue
        records.append(
            {
                "source_id": "polymarket",
                "tag_id": _inventory_string(row, "id", "tag_id"),
                "tag_slug": tag_slug,
                "label": label,
                "active_market_count": _inventory_int(row, "active_market_count", "market_count", "count"),
            }
        )
    return sorted(records, key=lambda item: item["tag_slug"])


def _polymarket_tag_slug_from_label(label: str) -> str | None:
    normalized = "-".join(label.strip().lower().split())
    if not normalized:
        return None
    if any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in normalized):
        return None
    try:
        return _safe_pipeline_label(normalized)
    except ValueError:
        return None


def _inventory_items(raw: Any, *, preferred_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [row for row in raw if isinstance(row, dict)]
    if isinstance(raw, dict):
        for key in preferred_keys:
            value = raw.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        return [raw] if any(key in raw for key in ("series_ticker", "ticker", "slug", "name", "label")) else []
    return []


def _inventory_string(row: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _inventory_int(row: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = row.get(key)
        if value is None or value == "":
            continue
        try:
            return int(float(value))
        except (TypeError, ValueError):
            continue
    return None


def _inventory_sample_markets(row: dict[str, Any]) -> list[dict[str, Any]]:
    samples = []
    value = row.get("markets") or row.get("sample_markets")
    if not isinstance(value, list):
        return samples
    for market in value[:5]:
        if not isinstance(market, dict):
            continue
        samples.append(
            {
                "ticker": _inventory_string(market, "ticker", "market_ticker"),
                "title": _inventory_string(market, "title", "question", "subtitle"),
            }
        )
    return samples


_INVENTORY_ANALYSIS_TERMS: dict[str, tuple[str, ...]] = {
    "ai": ("ai", "openai", "chatgpt", "gpt", "artificial intelligence"),
    "macro": ("fed", "fomc", "cpi", "inflation", "rate", "gdp", "unemployment", "recession"),
    "politics": ("election", "president", "presidential", "politics", "senate", "congress"),
    "crypto": ("bitcoin", "btc", "ethereum", "eth", "crypto"),
    "companies": ("tesla", "tsla", "nvidia", "nvda", "stock", "earnings", "company"),
    "weather": ("weather", "temperature", "rain", "snow", "hurricane"),
}


def _live_source_inventory_analysis(sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    kalshi_records = sources["kalshi"].get("records") or []
    polymarket_records = sources["polymarket"].get("records") or []
    kalshi_inventory_available = sources["kalshi"].get("status") == "OK"
    kalshi_by_category = {
        category: _likely_kalshi_series_for_terms(kalshi_records, terms)
        for category, terms in _INVENTORY_ANALYSIS_TERMS.items()
    }
    polymarket_by_category = {
        category: _likely_polymarket_tags_for_terms(polymarket_records, terms)
        for category, terms in _INVENTORY_ANALYSIS_TERMS.items()
    }
    return {
        "human_review_only": True,
        "likely_kalshi_series_by_category": kalshi_by_category,
        "likely_polymarket_tags_by_category": polymarket_by_category,
        "candidate_profile_suggestions": _candidate_profile_suggestions(kalshi_by_category, polymarket_by_category),
        "overlap_profile_kalshi_series_recheck": _overlap_profile_kalshi_series_recheck(
            kalshi_records,
            inventory_available=kalshi_inventory_available,
        ),
        "dead_or_guessed_profiles_to_recheck": _dead_or_guessed_profile_recheck(
            kalshi_records,
            inventory_available=kalshi_inventory_available,
        ),
        "warnings": [
            "inventory discovery does not imply overlap",
            "inventory discovery does not imply same payoff",
            "inventory discovery does not imply edge or tradability",
            "do not auto-modify overlap profiles without human review",
        ],
    }


def _likely_kalshi_series_for_terms(records: list[dict[str, Any]], terms: tuple[str, ...]) -> list[dict[str, Any]]:
    matches = []
    for row in records:
        text = " ".join(str(row.get(key) or "") for key in ("series_ticker", "title", "category_hint")).lower()
        hit_terms = _inventory_hit_terms(text, terms)
        if not hit_terms:
            continue
        matches.append(
            {
                "series_ticker": row.get("series_ticker"),
                "title": row.get("title"),
                "category_hint": row.get("category_hint"),
                "active_market_count": row.get("active_market_count"),
                "hit_terms": hit_terms,
            }
        )
    return sorted(matches, key=_kalshi_inventory_suggestion_sort_key)[:10]


def _likely_polymarket_tags_for_terms(records: list[dict[str, Any]], terms: tuple[str, ...]) -> list[dict[str, Any]]:
    matches = []
    for row in records:
        text = " ".join(str(row.get(key) or "") for key in ("tag_slug", "label")).lower()
        hit_terms = _inventory_hit_terms(text, terms)
        if not hit_terms:
            continue
        matches.append(
            {
                "tag_id": row.get("tag_id"),
                "tag_slug": row.get("tag_slug"),
                "label": row.get("label"),
                "active_market_count": row.get("active_market_count"),
                "hit_terms": hit_terms,
            }
        )
    return sorted(matches, key=_polymarket_inventory_suggestion_sort_key)[:10]


def _kalshi_inventory_suggestion_sort_key(row: dict[str, Any]) -> tuple[int, str]:
    return (-_inventory_count_for_sort(row.get("active_market_count")), str(row.get("series_ticker") or ""))


def _polymarket_inventory_suggestion_sort_key(row: dict[str, Any]) -> tuple[int, str]:
    label = str(row.get("tag_slug") or row.get("label") or "")
    return (-_inventory_count_for_sort(row.get("active_market_count")), label)


def _inventory_count_for_sort(value: Any) -> int:
    if isinstance(value, bool):
        return -1
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _inventory_hit_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    tokens = _meaningful_tokens(text)
    hits = []
    for term in terms:
        normalized = term.lower()
        if " " in normalized:
            if normalized in text:
                hits.append(term)
            continue
        if len(normalized) <= 3:
            if normalized in tokens:
                hits.append(term)
            continue
        if normalized in text:
            hits.append(term)
    return hits


def _candidate_profile_suggestions(
    kalshi_by_category: dict[str, list[dict[str, Any]]],
    polymarket_by_category: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    suggestions = []
    for category in sorted(_INVENTORY_ANALYSIS_TERMS):
        kalshi = kalshi_by_category.get(category) or []
        polymarket = polymarket_by_category.get(category) or []
        if not kalshi and not polymarket:
            continue
        suggestions.append(
            {
                "category": category,
                "human_review_required": True,
                "kalshi_series_candidates": [row.get("series_ticker") for row in kalshi[:5] if row.get("series_ticker")],
                "polymarket_tag_candidates": [row.get("tag_slug") for row in polymarket[:5] if row.get("tag_slug")],
                "kalshi_series_candidate_details": [
                    {
                        "series_ticker": row.get("series_ticker"),
                        "title": row.get("title"),
                        "active_market_count": row.get("active_market_count"),
                    }
                    for row in kalshi[:5]
                    if row.get("series_ticker")
                ],
                "polymarket_tag_candidate_details": [
                    {
                        "tag_slug": row.get("tag_slug"),
                        "label": row.get("label"),
                        "active_market_count": row.get("active_market_count"),
                    }
                    for row in polymarket[:5]
                    if row.get("tag_slug")
                ],
                "note": "suggestion only; does not update profile table or assert overlap",
            }
        )
    return suggestions


def _overlap_profile_kalshi_series_recheck(
    records: list[dict[str, Any]],
    *,
    inventory_available: bool,
) -> dict[str, dict[str, Any]]:
    return {
        ticker: _series_inventory_status(records, ticker) if inventory_available else _unresolved_series_inventory_status(ticker)
        for ticker in _overlap_profile_kalshi_series_tickers()
    }


def _overlap_profile_kalshi_series_tickers() -> list[str]:
    tickers = set()
    for profile in _OVERLAP_QUERY_PROFILES.values():
        for ticker in profile.get("kalshi_series_tickers") or ():
            if ticker:
                tickers.add(str(ticker).upper())
    return sorted(tickers)


def _dead_or_guessed_profile_recheck(
    records: list[dict[str, Any]],
    *,
    inventory_available: bool,
) -> dict[str, dict[str, Any]]:
    profile_recheck = _overlap_profile_kalshi_series_recheck(records, inventory_available=inventory_available)
    return {
        ticker: profile_recheck.get(
            ticker,
            _series_inventory_status(records, ticker) if inventory_available else _unresolved_series_inventory_status(ticker),
        )
        for ticker in ("KXAI", "KXPRES", "KXNVDA")
    }


def _series_inventory_status(records: list[dict[str, Any]], series_ticker: str) -> dict[str, Any]:
    for row in records:
        if str(row.get("series_ticker") or "").upper() == series_ticker.upper():
            return {
                "status": "confirmed_present",
                "series_ticker": series_ticker,
                "title": row.get("title"),
                "active_market_count": row.get("active_market_count"),
            }
    return {"status": "confirmed_absent", "series_ticker": series_ticker}


def _unresolved_series_inventory_status(series_ticker: str) -> dict[str, Any]:
    return {
        "status": "unresolved",
        "series_ticker": series_ticker,
        "reason": "kalshi_series_inventory_unavailable",
    }


def _live_source_inventory_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Live Source Inventory",
        "",
        "Explicit public read-only inventory discovery for human review. Discovery does not imply overlap, same payoff, edge, paper readiness, live readiness, or tradability.",
        "",
        f"- Status: `{report['status']}`",
        f"- Kalshi: `{report['sources']['kalshi']['status']}` records=`{report['sources']['kalshi']['record_count']}`",
        f"- Polymarket: `{report['sources']['polymarket']['status']}` records=`{report['sources']['polymarket']['record_count']}`",
        f"- Polymarket pagination/server cap possible: `{str(report['sources']['polymarket'].get('pagination_or_server_cap_possible', False)).lower()}`",
        f"- Profile table modified: `{str(report['profile_table_modified']).lower()}`",
        "",
        "## Source Rows",
        "",
        "| Source | Inventory | Status | Records | Error |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for source in report["sources"].values():
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(source["source_id"]),
                    _markdown_cell(source["inventory_kind"]),
                    _markdown_cell(source["status"]),
                    _markdown_cell(source["record_count"]),
                    _markdown_cell(source.get("failure_reason") or "none"),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Human Review Suggestions", ""])
    suggestions = report["analysis"]["candidate_profile_suggestions"]
    if not suggestions:
        lines.append("- none")
    for suggestion in suggestions:
        lines.append(
            "- "
            + f"{suggestion['category']}: Kalshi={','.join(suggestion['kalshi_series_candidates']) or 'none'}; "
            + f"Polymarket={','.join(suggestion['polymarket_tag_candidates']) or 'none'}"
        )
    lines.extend(["", "## Guessed Profiles To Recheck", ""])
    for ticker, status in report["analysis"]["dead_or_guessed_profiles_to_recheck"].items():
        lines.append(f"- `{ticker}`: {status['status']}")
    lines.extend(["", "## Current Profile Kalshi Series Recheck", ""])
    for ticker, status in report["analysis"]["overlap_profile_kalshi_series_recheck"].items():
        lines.append(f"- `{ticker}`: {status['status']}")
    lines.append("")
    return "\n".join(lines)


def fetch_live_readonly(
    *,
    sources: str,
    max_markets: int,
    timeout_seconds: float,
    the_odds_api_sport_key: str,
    output_dir: Path,
    load_env_file: bool = True,
) -> int:
    if load_env_file:
        _load_local_env_safely()
    requested_sources = _parse_sources_arg(sources)
    output_dir.mkdir(parents=True, exist_ok=True)
    captured_at = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for source_id in requested_sources:
        rows.append(
            _fetch_live_readonly_source(
                source_id=source_id,
                max_markets=max_markets,
                timeout_seconds=timeout_seconds,
                the_odds_api_sport_key=the_odds_api_sport_key,
                output_dir=output_dir,
            )
        )
    manifest = {
        "schema_version": 1,
        "source": "fetch_live_readonly",
        "captured_at": captured_at.isoformat(),
        "data_source_mode": "LIVE_API",
        "default_scan_data_source_mode": "STATIC_FIXTURE",
        "default_scan_live_fetch_attempted": False,
        "rows": rows,
    }
    manifest_path = output_dir / "live_readonly_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    succeeded = sum(1 for row in rows if row["live_fetch_succeeded"])
    failed = len(rows) - succeeded
    print(
        "fetch_live_readonly_status=OK "
        f"sources={len(rows)} succeeded={succeeded} failed={failed} manifest={manifest_path}"
    )
    for row in rows:
        print(
            "live_readonly_source="
            f"{row['source_id']} attempted={str(row['live_fetch_attempted']).lower()} "
            f"succeeded={str(row['live_fetch_succeeded']).lower()} "
            f"result_count={_display_smoke_value(row.get('result_count'))} "
            f"error_category={row.get('error_category') or 'none'} "
            f"snapshot_path={row.get('snapshot_path') or 'none'}"
        )
    return 0


_OVERLAP_CATEGORY_TERMS: dict[str, tuple[str, ...]] = {
    "sports": (
        "sports",
        "nba",
        "nfl",
        "mlb",
        "nhl",
        "basketball",
        "football",
        "baseball",
        "hockey",
        "soccer",
        "tennis",
        "golf",
        "championship",
        "finals",
        "super bowl",
        "world series",
        "stanley cup",
    ),
    "politics": (
        "politics",
        "election",
        "president",
        "senate",
        "house",
        "congress",
        "governor",
        "mayor",
        "primary",
        "democrat",
        "republican",
    ),
    "macro": (
        "macro",
        "cpi",
        "inflation",
        "fed",
        "federal reserve",
        "interest rate",
        "rates",
        "gdp",
        "unemployment",
        "recession",
        "treasury",
    ),
    "crypto": (
        "crypto",
        "bitcoin",
        "btc",
        "ethereum",
        "eth",
        "solana",
        "sol",
        "xrp",
        "doge",
    ),
    "companies": (
        "company",
        "companies",
        "earnings",
        "stock",
        "ipo",
        "nvidia",
        "tesla",
        "apple",
        "meta",
        "amazon",
        "openai",
        "spacex",
    ),
    "ai": (
        "ai",
        "artificial intelligence",
        "openai",
        "chatgpt",
        "gpt",
        "anthropic",
        "claude",
        "agi",
    ),
    "weather": (
        "weather",
        "temperature",
        "rain",
        "snow",
        "hurricane",
        "storm",
        "tornado",
        "heat",
        "degrees",
    ),
    "entertainment": (
        "entertainment",
        "movie",
        "oscars",
        "grammy",
        "album",
        "box office",
        "tv",
        "game",
        "gta",
    ),
}

_POLYMARKET_TAG_BY_CATEGORY = {
    "sports": "sports",
    "politics": "politics",
    "crypto": "crypto",
    "companies": "business",
    "ai": "ai",
    "entertainment": "entertainment",
}

_LIVE_OVERLAP_SWEEP_QUERIES: dict[str, tuple[str, ...]] = {
    "macro": ("Fed", "CPI", "inflation", "unemployment", "recession", "GDP"),
    "politics": ("election", "politics"),
    "crypto": ("Bitcoin", "Ethereum", "crypto"),
    "companies": ("OpenAI", "Tesla", "Nvidia", "companies"),
    "ai": ("OpenAI", "AI"),
    "weather": ("weather",),
}

_OVERLAP_QUERY_PROFILES: dict[str, dict[str, Any]] = {
    "nba": {
        "category": "sports",
        "kalshi_series_tickers": ("KXNBA",),
        "polymarket_tag_slug": "nba",
        "terms": ("nba", "kxnba", "basketball", "finals"),
    },
    "mlb": {
        "category": "sports",
        "kalshi_series_tickers": ("KXMLB",),
        "polymarket_tag_slug": "mlb",
        "terms": ("mlb", "kxmlb", "baseball", "world series"),
    },
    "nfl": {
        "category": "sports",
        "kalshi_series_tickers": ("KXNFL",),
        "polymarket_tag_slug": "nfl",
        "terms": ("nfl", "kxnfl", "football", "super bowl"),
    },
    "nhl": {
        "category": "sports",
        "kalshi_series_tickers": ("KXNHL",),
        "polymarket_tag_slug": "nhl",
        "terms": ("nhl", "kxnhl", "hockey", "stanley cup"),
    },
    "ai": {
        "category": "ai",
        "kalshi_series_tickers": ("AIDEBATES", "AILEGISLATION", "AITURING", "APPLEAI", "GPT45", "KXGPT5"),
        "polymarket_tag_slug": "openai",
        "terms": ("ai", "artificial intelligence", "openai", "chatgpt", "gpt", "ai regulation", "turing"),
        "source_inventory_confirmed": True,
        "discovery_source": "live_source_inventory",
        "human_review_required": True,
        "replaced_dead_series_tickers": ("KXAI",),
    },
    "openai": {
        "category": "ai",
        "kalshi_series_tickers": ("KXOPENAICEO", "KXOAIAGI", "KXOAIPROFIT", "KXIPOOPENAI", "KXOAIBROWSER", "KXOPENAIBOARD"),
        "polymarket_tag_slug": "openai",
        "terms": ("openai", "chatgpt", "gpt", "ai", "agi"),
        "source_inventory_confirmed": True,
        "discovery_source": "live_source_inventory",
        "human_review_required": True,
        "replaced_dead_series_tickers": ("KXAI",),
    },
    "fed": {
        "category": "macro",
        "kalshi_series_tickers": ("KXFED", "FEDDECISION", "KXFOMCDISSENTCOUNT", "KXFOMCVOTE"),
        "terms": ("fed", "fomc", "federal reserve", "interest rate", "rate cut", "kxfed", "kxfomc"),
        "source_inventory_confirmed": True,
        "discovery_source": "live_source_inventory",
        "human_review_required": True,
        "replaced_dead_series_tickers": ("KXFOMC",),
    },
    "fomc": {
        "category": "macro",
        "kalshi_series_tickers": ("KXFOMCDISSENTCOUNT", "KXFOMCVOTE", "FEDDECISION", "KXFED"),
        "terms": ("fomc", "fed", "federal reserve", "interest rate", "rate cut", "kxfomc", "kxfed"),
        "source_inventory_confirmed": True,
        "discovery_source": "live_source_inventory",
        "human_review_required": True,
        "replaced_dead_series_tickers": ("KXFOMC",),
    },
    "cpi": {
        "category": "macro",
        "kalshi_series_tickers": ("KXCPI", "CPI", "CPIYOY", "CPICORE", "KXCOREUND"),
        "terms": ("cpi", "inflation", "consumer price", "kxcpi"),
        "source_inventory_confirmed": True,
        "discovery_source": "live_source_inventory",
        "human_review_required": True,
    },
    "inflation": {
        "category": "macro",
        "kalshi_series_tickers": ("KXCPI", "CPI", "CPIYOY", "CPICORE", "KXCOREUND"),
        "terms": ("inflation", "cpi", "consumer price", "kxcpi"),
        "source_inventory_confirmed": True,
        "discovery_source": "live_source_inventory",
        "human_review_required": True,
    },
    "gdp": {
        "category": "macro",
        "polymarket_tag_slug": "gdp",
        "terms": ("gdp", "gross domestic product"),
        "source_inventory_confirmed": True,
        "discovery_source": "live_source_inventory",
        "human_review_required": True,
    },
    "bitcoin": {
        "category": "crypto",
        "kalshi_series_tickers": ("KXBTC", "BTC", "BTCD", "BTCATH", "KXBTCD", "KXBTCATH"),
        "terms": ("bitcoin", "btc", "kxbtc"),
        "source_inventory_confirmed": True,
        "discovery_source": "live_source_inventory",
        "human_review_required": True,
        "polymarket_tag_inventory_confirmed": False,
    },
    "btc": {
        "category": "crypto",
        "kalshi_series_tickers": ("KXBTC", "BTC", "BTCD", "BTCATH", "KXBTCD", "KXBTCATH"),
        "terms": ("bitcoin", "btc", "kxbtc"),
        "source_inventory_confirmed": True,
        "discovery_source": "live_source_inventory",
        "human_review_required": True,
        "polymarket_tag_inventory_confirmed": False,
    },
    "ethereum": {
        "category": "crypto",
        "kalshi_series_tickers": ("KXETH", "ETH", "ETHD", "ETHATH", "KXBTCETHATH", "KXBTCETHRETURN"),
        "terms": ("ethereum", "eth", "kxeth"),
        "source_inventory_confirmed": True,
        "discovery_source": "live_source_inventory",
        "human_review_required": True,
        "polymarket_tag_inventory_confirmed": False,
    },
    "eth": {
        "category": "crypto",
        "kalshi_series_tickers": ("KXETH", "ETH", "ETHD", "ETHATH", "KXBTCETHATH", "KXBTCETHRETURN"),
        "terms": ("ethereum", "eth", "kxeth"),
        "source_inventory_confirmed": True,
        "discovery_source": "live_source_inventory",
        "human_review_required": True,
        "polymarket_tag_inventory_confirmed": False,
    },
    "politics": {
        "category": "politics",
        "kalshi_series_tickers": ("KX538APPROVE", "KXAPRPOTUS", "KXTRUMPAPPROVALBELOW", "KXTRUMPFAV", "KXBLUEWALL"),
        "polymarket_tag_slug": "federal-government",
        "terms": ("politics", "president", "approval", "trump", "government", "federal"),
        "source_inventory_confirmed": True,
        "discovery_source": "live_source_inventory",
        "human_review_required": True,
    },
    "election": {
        "category": "politics",
        "kalshi_series_tickers": ("KXARGENTINAPRES", "KXBRAZILPRES1R", "KXBULGARIAPRES", "KXCAMEROONPRES", "KXATTYGENVA"),
        "polymarket_tag_slug": "thailand-election",
        "terms": ("election", "president", "presidential", "politics"),
        "source_inventory_confirmed": True,
        "discovery_source": "live_source_inventory",
        "human_review_required": True,
        "replaced_dead_series_tickers": ("KXPRES",),
    },
    "presidential election": {
        "category": "politics",
        "kalshi_series_tickers": ("KXARGENTINAPRES", "KXBRAZILPRES1R", "KXBULGARIAPRES", "KXCAMEROONPRES", "KXATTYGENVA"),
        "terms": ("election", "president", "presidential"),
        "source_inventory_confirmed": True,
        "discovery_source": "live_source_inventory",
        "human_review_required": True,
        "replaced_dead_series_tickers": ("KXPRES",),
    },
    "companies": {
        "category": "companies",
        "kalshi_series_tickers": ("ALTMAN", "KXACQANNOUNCESPACEX", "KXACQUIREAXIOM", "KXACQUIREDFIRST", "KXAGICO"),
        "terms": ("company", "companies", "earnings", "stock", "openai", "tesla", "spacex", "agi"),
        "source_inventory_confirmed": True,
        "discovery_source": "live_source_inventory",
        "human_review_required": True,
        "polymarket_tag_inventory_confirmed": False,
    },
    "nvidia": {
        "category": "companies",
        "kalshi_series_tickers": ("KXNVIDIARASGONQ", "KXEARNINGSMENTIONNVDA", "KXH200CHINA"),
        "terms": ("nvidia", "nvda", "kxnvda"),
        "source_inventory_confirmed": True,
        "discovery_source": "live_source_inventory",
        "human_review_required": True,
        "replaced_dead_series_tickers": ("KXNVDA",),
        "polymarket_tag_inventory_confirmed": False,
    },
    "nvda": {
        "category": "companies",
        "kalshi_series_tickers": ("KXNVIDIARASGONQ", "KXEARNINGSMENTIONNVDA", "KXH200CHINA"),
        "terms": ("nvidia", "nvda", "kxnvda"),
        "source_inventory_confirmed": True,
        "discovery_source": "live_source_inventory",
        "human_review_required": True,
        "replaced_dead_series_tickers": ("KXNVDA",),
        "polymarket_tag_inventory_confirmed": False,
    },
    "tesla": {
        "category": "companies",
        "kalshi_series_tickers": ("KXTSLA", "KXTESLA", "KXTESLAPROD", "KXACQANNOUNCESPACEX", "KXEARNINGSMENTIONTSLA"),
        "terms": ("tesla", "tsla", "kxtsla"),
        "source_inventory_confirmed": True,
        "discovery_source": "live_source_inventory",
        "human_review_required": True,
        "polymarket_tag_inventory_confirmed": False,
    },
    "tsla": {
        "category": "companies",
        "kalshi_series_tickers": ("KXTSLA", "KXTESLA", "KXTESLAPROD", "KXACQANNOUNCESPACEX", "KXEARNINGSMENTIONTSLA"),
        "terms": ("tesla", "tsla", "kxtsla"),
        "source_inventory_confirmed": True,
        "discovery_source": "live_source_inventory",
        "human_review_required": True,
        "polymarket_tag_inventory_confirmed": False,
    },
}


def fetch_live_overlap_universe(
    *,
    category: str,
    query: str | None,
    max_markets: int,
    timeout_seconds: float,
    kalshi_max_pages: int,
    output_dir: Path,
    report_dir: Path,
    label: str | None = None,
) -> int:
    report_label = _safe_overlap_label(label, category=category, query=query)
    default_live_readonly_dir = PROJECT_ROOT / "reports" / "live_readonly"
    if _is_default_live_readonly_dir(output_dir):
        output_dir = default_live_readonly_dir / report_label
    if _is_default_reports_dir(report_dir):
        report_dir = output_dir
    try:
        report = build_live_overlap_universe_report(
            category=category,
            query=query,
            max_markets=max_markets,
            timeout_seconds=timeout_seconds,
            kalshi_max_pages=kalshi_max_pages,
            output_dir=output_dir,
        )
    except Exception as exc:
        print(f"live_overlap_universe_status=FAILED error_category={_error_category(exc)} message={_safe_cli_text(exc)}")
        return 1
    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = report_dir / "live_overlap_universe_manifest.json"
    json_path = report_dir / "live_overlap_universe_report.json"
    markdown_path = report_dir / "live_overlap_universe_report.md"
    labeled_manifest_path = report_dir / f"{report_label}_live_overlap_universe_manifest.json"
    labeled_json_path = report_dir / f"{report_label}_live_overlap_universe_report.json"
    labeled_markdown_path = report_dir / f"{report_label}_live_overlap_universe_report.md"
    manifest_path.write_text(json.dumps(report["manifest"], indent=2, sort_keys=True), encoding="utf-8")
    json_path.write_text(json.dumps(report["report"], indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(_live_overlap_universe_markdown(report["report"]), encoding="utf-8")
    labeled_manifest_path.write_text(json.dumps(report["manifest"], indent=2, sort_keys=True), encoding="utf-8")
    labeled_json_path.write_text(json.dumps(report["report"], indent=2, sort_keys=True), encoding="utf-8")
    labeled_markdown_path.write_text(_live_overlap_universe_markdown(report["report"]), encoding="utf-8")

    summary = report["report"]["summary"]
    print(
        "live_overlap_universe_status="
        f"{report['report']['status']} category={category} query={_safe_cli_text(query or 'none')} "
        f"kalshi_retained={summary['retained_by_source']['kalshi']} "
        f"polymarket_retained={summary['retained_by_source']['polymarket']} "
        f"top_text_similarity={summary['top_text_similarity']} "
        f"overlap_improved={str(summary['overlap_improved']).lower()} "
        f"manifest={manifest_path} json={json_path} markdown={markdown_path} "
        f"labeled_json={labeled_json_path}"
    )
    return 0 if report["report"]["status"] == "OK" else 1


def _is_default_live_readonly_dir(path: Path) -> bool:
    return path in {PROJECT_ROOT / "reports" / "live_readonly", Path("reports") / "live_readonly"}


def _is_default_reports_dir(path: Path) -> bool:
    return path in {PROJECT_ROOT / "reports", Path("reports")}


def build_live_overlap_universe_report(
    *,
    category: str,
    query: str | None,
    max_markets: int,
    timeout_seconds: float,
    kalshi_max_pages: int,
    output_dir: Path,
) -> dict[str, Any]:
    if max_markets <= 0:
        raise ValueError("max_markets must be positive")
    if kalshi_max_pages <= 0:
        raise ValueError("kalshi_max_pages must be positive")
    if _is_default_live_readonly_dir(output_dir):
        raise ValueError("live_readonly overlap snapshots require a universe-specific output_dir")

    generated_at = datetime.now(timezone.utc)
    previous_summary = _current_saved_overlap_summary(output_dir)
    kalshi_path = output_dir / "kalshi_live_readonly_snapshot.json"
    polymarket_path = output_dir / "polymarket_live_readonly_snapshot.json"

    kalshi_snapshot, kalshi_fetch = _fetch_overlap_kalshi_snapshot(
        category=category,
        query=query,
        max_markets=max_markets,
        timeout_seconds=timeout_seconds,
        max_pages=kalshi_max_pages,
    )
    polymarket_snapshot, polymarket_fetch = _fetch_overlap_polymarket_snapshot(
        category=category,
        query=query,
        max_markets=max_markets,
        timeout_seconds=timeout_seconds,
    )

    kalshi_filtered = _filter_overlap_snapshot(
        kalshi_snapshot,
        source_id="kalshi",
        category=category,
        query=query,
        source_specific_target_used=kalshi_fetch.get("direct_targeting", "").startswith("series_ticker:"),
    )
    polymarket_filtered = _filter_overlap_snapshot(
        polymarket_snapshot,
        source_id="polymarket",
        category=category,
        query=query,
        source_specific_target_used=polymarket_fetch.get("direct_targeting", "").startswith("tag_slug:"),
    )
    _attach_overlap_snapshot_metadata(kalshi_filtered, category=category, query=query)
    _attach_overlap_snapshot_metadata(polymarket_filtered, category=category, query=query)

    output_dir.mkdir(parents=True, exist_ok=True)
    write_kalshi_market_snapshot(kalshi_filtered, kalshi_path)
    write_polymarket_market_snapshot(polymarket_filtered, polymarket_path)

    current_summary = _overlap_text_summary(
        _normalized_market_rows(kalshi_filtered),
        _normalized_market_rows(polymarket_filtered),
        limit=10,
    )
    category_counts = {
        "kalshi": _category_counts(_normalized_market_rows(kalshi_filtered)),
        "polymarket": _category_counts(_normalized_market_rows(polymarket_filtered)),
    }
    retained_by_source = {
        "kalshi": len(_normalized_market_rows(kalshi_filtered)),
        "polymarket": len(_normalized_market_rows(polymarket_filtered)),
    }
    fetched_by_source = {
        "kalshi": int(kalshi_snapshot.get("normalized_count") or 0),
        "polymarket": int(polymarket_snapshot.get("normalized_count") or 0),
    }
    report = {
        "schema_version": 1,
        "source": "live_overlap_universe",
        "status": "OK",
        "generated_at": generated_at.isoformat(),
        "research_only": True,
        "readiness_promotion": "none",
        "category": category,
        "query": query,
        "max_markets": max_markets,
        "default_scan_data_source_mode": "STATIC_FIXTURE",
        "default_scan_live_fetch_attempted": False,
        "source_snapshot_paths": {
            "kalshi": str(kalshi_path),
            "polymarket": str(polymarket_path),
        },
        "fetch": {
            "kalshi": kalshi_fetch,
            "polymarket": polymarket_fetch,
            "the_odds_api": {
                "role": "not_used_as_executable_leg",
                "live_fetch_attempted": False,
                "source_type": SourceType.REFERENCE_ONLY.value,
            },
        },
        "summary": {
            "fetched_by_source": fetched_by_source,
            "retained_by_source": retained_by_source,
            "category_counts_by_source": category_counts,
            "source_targeting": {
                "kalshi": {
                    "method": kalshi_fetch.get("targeting_method"),
                    "direct_targeting": kalshi_fetch.get("direct_targeting"),
                    "source_specific_profile_attempted": kalshi_fetch.get("source_specific_profile_attempted"),
                    "attempted_series_tickers": kalshi_fetch.get("attempted_series_tickers") or [],
                    "series_results": kalshi_fetch.get("series_results") or [],
                },
                "polymarket": {
                    "method": polymarket_fetch.get("targeting_method"),
                    "direct_targeting": polymarket_fetch.get("direct_targeting"),
                    "source_specific_profile_attempted": polymarket_fetch.get("source_specific_profile_attempted"),
                    "attempted_tag_slug": polymarket_fetch.get("attempted_tag_slug"),
                },
            },
            "likely_overlap_categories": _likely_overlap_categories(category_counts),
            "top_text_similarity": current_summary["top_text_similarity"],
            "previous_top_text_similarity": previous_summary["top_text_similarity"],
            "overlap_improved": current_summary["top_text_similarity"] > previous_summary["top_text_similarity"],
            "raw_cross_source_candidate_comparisons": current_summary["comparison_count"],
            "top_textual_overlap_candidates": current_summary["top_candidates"],
            "filter_diagnostics_by_source": {
                "kalshi": kalshi_filtered.get("overlap_universe_filter", {}),
                "polymarket": polymarket_filtered.get("overlap_universe_filter", {}),
            },
            "warnings": [
                "same broad sport category does not imply contract overlap",
                "league/query overlap is required before matching should be expected",
                "do not lower thresholds to force matches",
            ],
            "recommended_next_query": _recommended_overlap_query(category, query, category_counts, current_summary),
        },
        "safety": {
            "execution_enabled": False,
            "uses_reference_as_executable_leg": False,
            "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
            "emits_actions": False,
            "thresholds_changed": False,
            "same_payoff_asserted": False,
            "profit_claim": False,
        },
    }
    manifest = {
        "schema_version": 1,
        "source": "live_overlap_universe_manifest",
        "generated_at": generated_at.isoformat(),
        "category": category,
        "query": query,
        "data_source_mode": "LIVE_API",
        "default_scan_data_source_mode": "STATIC_FIXTURE",
        "default_scan_live_fetch_attempted": False,
        "rows": [
            _overlap_manifest_row("kalshi", kalshi_path, fetched_by_source["kalshi"], retained_by_source["kalshi"]),
            _overlap_manifest_row(
                "polymarket",
                polymarket_path,
                fetched_by_source["polymarket"],
                retained_by_source["polymarket"],
            ),
        ],
        "reference_sources_used_as_executable_legs": False,
        "readiness_promotion": "none",
    }
    return {"manifest": manifest, "report": report}


def _fetch_overlap_kalshi_snapshot(
    *,
    category: str,
    query: str | None,
    max_markets: int,
    timeout_seconds: float,
    max_pages: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    profile = _query_profile(query)
    series_tickers = tuple(str(value) for value in (profile.get("kalshi_series_tickers") or ()) if value) if profile else ()
    client = KalshiReadOnlyClient(
        base_url=os.environ.get("KALSHI_BASE_URL", "https://external-api.kalshi.com/trade-api/v2"),
        timeout_seconds=timeout_seconds,
    )
    series_results: list[dict[str, Any]] = []
    if series_tickers:
        snapshots: list[dict[str, Any]] = []
        for series_ticker in series_tickers:
            try:
                series_snapshot = client.fetch_market_snapshot(
                    limit=max_markets,
                    max_pages=max_pages,
                    series_ticker=series_ticker,
                )
            except Exception as exc:
                series_results.append(
                    {
                        "series_ticker": series_ticker,
                        "status": "FAILED",
                        "error_category": _error_category(exc),
                        "message": _safe_cli_text(exc),
                        "result_count": 0,
                    }
                )
                continue
            series_results.append(
                {
                    "series_ticker": series_ticker,
                    "status": "OK",
                    "result_count": int(series_snapshot.get("normalized_count") or 0),
                }
            )
            snapshots.append(series_snapshot)
        snapshot = _combine_overlap_snapshots(snapshots, source_id="kalshi")
    else:
        snapshot = client.fetch_market_snapshot(limit=max_markets, max_pages=max_pages)
    _attach_live_provenance(snapshot, source_id="kalshi")
    snapshot = _redact_secretish_fields(snapshot)
    targeting_method = "series_based" if series_tickers else "broad_then_local"
    return snapshot, {
        "source_id": "kalshi",
        "live_fetch_attempted": True,
        "live_fetch_succeeded": True,
        "direct_targeting": f"series_ticker:{','.join(series_tickers)}" if series_tickers else "broad_open_inventory_then_local_filter",
        "targeting_method": targeting_method,
        "source_specific_profile_attempted": bool(series_tickers),
        "profile_provenance": _overlap_profile_provenance(profile),
        "attempted_series_tickers": list(series_tickers),
        "series_results": series_results,
        "local_filter_category": _effective_overlap_category(category, query),
        "local_filter_query": query,
        "result_count": snapshot.get("normalized_count"),
    }


def _fetch_overlap_polymarket_snapshot(
    *,
    category: str,
    query: str | None,
    max_markets: int,
    timeout_seconds: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    profile = _query_profile(query)
    tag_slug = profile.get("polymarket_tag_slug") if profile else _POLYMARKET_TAG_BY_CATEGORY.get(category)
    attempted_direct = bool(tag_slug)
    snapshot = PolymarketGammaClient(
        base_url=os.environ.get("POLYMARKET_GAMMA_BASE_URL", "https://gamma-api.polymarket.com"),
        timeout_seconds=timeout_seconds,
    ).fetch_market_snapshot(limit=max_markets, tag_slug=tag_slug)
    _attach_live_provenance(snapshot, source_id="polymarket")
    snapshot = _redact_secretish_fields(snapshot)
    return snapshot, {
        "source_id": "polymarket",
        "live_fetch_attempted": True,
        "live_fetch_succeeded": True,
        "direct_targeting": f"tag_slug:{tag_slug}" if attempted_direct else "broad_active_inventory_then_local_filter",
        "targeting_method": "tag_slug" if attempted_direct else "broad_then_local",
        "source_specific_profile_attempted": bool(profile and profile.get("polymarket_tag_slug")),
        "profile_provenance": _overlap_profile_provenance(profile),
        "attempted_tag_slug": tag_slug,
        "local_filter_category": _effective_overlap_category(category, query),
        "local_filter_query": query,
        "result_count": snapshot.get("normalized_count"),
    }


def _combine_overlap_snapshots(snapshots: list[dict[str, Any]], *, source_id: str) -> dict[str, Any]:
    if not snapshots:
        return {
            "schema_version": 1,
            "source": f"{source_id}_markets",
            "source_id": source_id,
            "event_count": 0,
            "market_count": 0,
            "normalized_count": 0,
            "normalized_markets": [],
            "raw_response": {"profile_attempts": []},
        }
    combined = json.loads(json.dumps(snapshots[0]))
    seen: set[str] = set()
    markets: list[dict[str, Any]] = []
    raw_responses: list[Any] = []
    for snapshot in snapshots:
        raw_responses.append(snapshot.get("raw_response"))
        for market in _normalized_market_rows(snapshot):
            key = str(market.get("ticker") or market.get("market_id") or market.get("condition_id") or "")
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            markets.append(json.loads(json.dumps(market)))
    combined["normalized_markets"] = markets
    combined["normalized_count"] = len(markets)
    combined["market_count"] = len(markets)
    combined["event_count"] = len(markets)
    combined["raw_response"] = {"profile_attempts": raw_responses}
    return combined


def _filter_overlap_snapshot(
    snapshot: dict[str, Any],
    *,
    source_id: str,
    category: str,
    query: str | None,
    source_specific_target_used: bool = False,
) -> dict[str, Any]:
    cloned = json.loads(json.dumps(snapshot))
    rows = _normalized_market_rows(cloned)
    retained: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    rejected_counts: Counter[str] = Counter()
    for row in rows:
        retention = _overlap_retention(
            row,
            category=category,
            query=query,
            source_specific_target_used=source_specific_target_used,
        )
        row_copy = json.loads(json.dumps(row))
        row_copy["overlap_filter_diagnostics"] = {
            "detected_category": retention["detected_category"],
            "query_hit_terms": retention["query_hit_terms"],
            "category_hit": retention["category_hit"],
            "query_hit": retention["query_hit"],
        }
        if retention["retained"]:
            retained.append(row_copy)
        else:
            rejected_counts[retention["rejected_reason"]] += 1
            rejected.append(row_copy)
    cloned["normalized_markets"] = retained
    cloned["normalized_count"] = len(retained)
    cloned["overlap_universe_filter"] = {
        "category": category,
        "effective_category": _effective_overlap_category(category, query),
        "query": query,
        "source_id": source_id,
        "input_normalized_count": len(rows),
        "retained_normalized_count": len(retained),
        "rejected_normalized_count": len(rejected),
        "rejected_count_by_reason": dict(sorted(rejected_counts.items())),
        "sample_retained_markets": _sample_overlap_rows(retained),
        "sample_rejected_markets": _sample_overlap_rows(rejected),
        "mode": "advisory_local_filter_after_source_targeted_fetch"
        if source_specific_target_used and _query_profile(query)
        else "local_filter_after_read_only_fetch",
        "source_specific_target_used": source_specific_target_used,
        "source_specific_profile_attempted": bool(source_specific_target_used and _query_profile(query)),
    }
    return cloned


def _attach_overlap_snapshot_metadata(snapshot: dict[str, Any], *, category: str, query: str | None) -> None:
    snapshot["overlap_universe"] = {
        "category": category,
        "query": query,
        "research_only": True,
        "readiness_promotion": "none",
        "can_create_paper_candidate": False,
        "same_payoff_asserted": False,
    }


def _overlap_market_retained(row: dict[str, Any], *, category: str, query: str | None) -> bool:
    return bool(_overlap_retention(row, category=category, query=query)["retained"])


def _overlap_retention(
    row: dict[str, Any],
    *,
    category: str,
    query: str | None,
    source_specific_target_used: bool = False,
) -> dict[str, Any]:
    text = _overlap_market_text(row)
    effective_category = _effective_overlap_category(category, query)
    detected_category = _market_category(row)
    category_hit = effective_category == "all" or detected_category == effective_category
    query_terms = _query_match_terms(query)
    query_hit_terms = [term for term in query_terms if term in text]
    query_hit = True
    if query_terms:
        if _query_profile(query):
            query_hit = bool(query_hit_terms)
        else:
            query_hit = all(term in text for term in query_terms)
    advisory_filter = bool(source_specific_target_used and _query_profile(query))
    if advisory_filter:
        category_hit = True
        query_hit = True
    retained = category_hit and query_hit
    rejected_reason = "retained"
    if not category_hit:
        rejected_reason = "category_mismatch"
    elif not query_hit:
        rejected_reason = "query_mismatch"
    return {
        "retained": retained,
        "detected_category": detected_category,
        "category_hit": category_hit,
        "query_hit": query_hit,
        "advisory_filter": advisory_filter,
        "query_hit_terms": query_hit_terms,
        "rejected_reason": rejected_reason,
    }


def _query_profile(query: str | None) -> dict[str, Any] | None:
    if not query:
        return None
    return _OVERLAP_QUERY_PROFILES.get(query.strip().lower())


def _overlap_profile_provenance(profile: dict[str, Any] | None) -> dict[str, Any]:
    if not profile:
        return {
            "source_inventory_confirmed": False,
            "discovery_source": None,
            "human_review_required": True,
        }
    return {
        "source_inventory_confirmed": bool(profile.get("source_inventory_confirmed")),
        "discovery_source": profile.get("discovery_source"),
        "human_review_required": bool(profile.get("human_review_required", True)),
        "replaced_dead_series_tickers": list(profile.get("replaced_dead_series_tickers") or ()),
        "polymarket_tag_inventory_confirmed": bool(
            profile.get("polymarket_tag_slug") and profile.get("polymarket_tag_inventory_confirmed", True)
        ),
    }


def _effective_overlap_category(category: str, query: str | None) -> str:
    profile = _query_profile(query)
    if profile and category == "all":
        return str(profile["category"])
    return category


def _query_match_terms(query: str | None) -> tuple[str, ...]:
    profile = _query_profile(query)
    if profile:
        return tuple(str(term).lower() for term in profile["terms"])
    if not query:
        return ()
    return tuple(sorted(_meaningful_tokens(query)))


def _market_category(row: dict[str, Any]) -> str:
    text = _overlap_market_text(row)
    for category, terms in _OVERLAP_CATEGORY_TERMS.items():
        if any(term in text for term in terms):
            return category
    return "other/unknown"


def _sample_overlap_rows(rows: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    samples = []
    for row in rows[:limit]:
        diagnostics = row.get("overlap_filter_diagnostics") if isinstance(row.get("overlap_filter_diagnostics"), dict) else {}
        samples.append(
            {
                "identifier": row.get("ticker") or row.get("market_id") or row.get("condition_id"),
                "title_or_question": _display_market_title(row),
                "detected_category": diagnostics.get("detected_category") or _market_category(row),
                "query_hit_terms": diagnostics.get("query_hit_terms") or [],
            }
        )
    return samples


def _overlap_market_text(row: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("event_title", "title", "question", "market_id", "ticker", "condition_id", "category", "market_type"):
        value = row.get(key)
        if value is not None:
            values.append(str(value))
    outcomes = row.get("outcomes")
    if isinstance(outcomes, list):
        values.extend(str(value) for value in outcomes if value is not None)
    raw = row.get("raw")
    if isinstance(raw, dict):
        for key in ("series_ticker", "event_ticker", "category", "subcategory", "sport", "league", "title"):
            value = raw.get(key)
            if value is not None:
                values.append(str(value))
    return " ".join(values).lower()


def _category_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter(_market_category(row) for row in rows)
    for category in (*_OVERLAP_CATEGORY_TERMS.keys(), "other/unknown"):
        counts.setdefault(category, 0)
    return dict(sorted(counts.items()))


def _likely_overlap_categories(category_counts: dict[str, dict[str, int]]) -> list[str]:
    kalshi = category_counts.get("kalshi", {})
    polymarket = category_counts.get("polymarket", {})
    return [
        category
        for category in sorted(set(kalshi) | set(polymarket))
        if kalshi.get(category, 0) > 0 and polymarket.get(category, 0) > 0
    ]


def _overlap_text_summary(
    kalshi_rows: list[dict[str, Any]],
    polymarket_rows: list[dict[str, Any]],
    *,
    limit: int,
) -> dict[str, Any]:
    top: list[dict[str, Any]] = []
    for poly in polymarket_rows:
        poly_text = _overlap_market_text(poly)
        for kalshi in kalshi_rows:
            kalshi_text = _overlap_market_text(kalshi)
            score = round(_text_similarity(poly_text, kalshi_text), 6)
            top.append(
                {
                    "similarity_score": score,
                    "polymarket": {
                        "market_id": poly.get("market_id") or poly.get("condition_id"),
                        "title_or_question": _display_market_title(poly),
                        "category": _market_category(poly),
                    },
                    "kalshi": {
                        "ticker": kalshi.get("ticker") or kalshi.get("market_id"),
                        "title_or_question": _display_market_title(kalshi),
                        "category": _market_category(kalshi),
                    },
                    "diagnostic_only": True,
                    "same_payoff_asserted": False,
                }
            )
    top.sort(key=lambda row: row["similarity_score"], reverse=True)
    return {
        "comparison_count": len(polymarket_rows) * len(kalshi_rows),
        "top_text_similarity": top[0]["similarity_score"] if top else 0.0,
        "top_candidates": top[:limit],
    }


def _display_market_title(row: dict[str, Any]) -> str:
    return str(row.get("question") or row.get("title") or row.get("event_title") or row.get("market_id") or "")


def _current_saved_overlap_summary(snapshot_dir: Path) -> dict[str, Any]:
    kalshi = _load_json_object(snapshot_dir / "kalshi_live_readonly_snapshot.json")
    polymarket = _load_json_object(snapshot_dir / "polymarket_live_readonly_snapshot.json")
    if not kalshi or not polymarket:
        return {"top_text_similarity": 0.0, "comparison_count": 0, "top_candidates": []}
    return _overlap_text_summary(_normalized_market_rows(kalshi), _normalized_market_rows(polymarket), limit=5)


def _recommended_overlap_query(
    category: str,
    query: str | None,
    category_counts: dict[str, dict[str, int]],
    current_summary: dict[str, Any],
) -> str:
    if current_summary["top_text_similarity"] >= 0.68:
        return "Run inspect-live-snapshots, match-live-readonly-snapshots, and diagnose-live-matching on the saved overlap snapshots."
    overlap_categories = _likely_overlap_categories(category_counts)
    if query:
        return f"Try a narrower related query or a known venue series/tag for {query}; current text overlap is still below matcher threshold."
    if category == "sports":
        return "Try --query NBA, NFL, MLB, or NHL, or use the existing targeted pipeline for a known Kalshi series such as KXNBA."
    if overlap_categories:
        return f"Try a narrower query inside {', '.join(overlap_categories[:3])}."
    return "Try a different category or a narrower query with known coverage on both venues."


def _overlap_manifest_row(source_id: str, path: Path, fetched_count: int, retained_count: int) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "source_type": _source_type(source_id),
        "snapshot_path": str(path),
        "data_source_mode": "LIVE_API",
        "live_fetch_attempted": True,
        "live_fetch_succeeded": True,
        "fetched_normalized_count": fetched_count,
        "retained_normalized_count": retained_count,
        "can_participate_in_candidate_pair": source_id in {"kalshi", "polymarket"},
        "can_create_paper_candidate": False,
        "used_for_default_scan": False,
    }


def _redact_secretish_fields(value: Any) -> Any:
    secret_tokens = (
        "api_key",
        "apikey",
        "api_secret",
        "private_key",
        "signing_key",
        "auth_token",
        "session_token",
        "password",
        "wallet_private",
    )
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, child in value.items():
            key_text = str(key).lower()
            if any(token in key_text for token in secret_tokens):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = _redact_secretish_fields(child)
        return redacted
    if isinstance(value, list):
        return [_redact_secretish_fields(item) for item in value]
    return value


def _live_overlap_universe_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Live Overlap Universe Report",
        "",
        "Explicit read-only Kalshi/Polymarket universe fetch. This report is diagnostics only and does not assert same-payoff equivalence.",
        "Same broad sport category does not imply contract overlap. League/query overlap is required before matching should be expected, and thresholds must not be lowered to force matches.",
        "",
        f"- Category: `{report['category']}`",
        f"- Query: `{report['query'] or 'none'}`",
        f"- Kalshi retained: `{summary['retained_by_source']['kalshi']}`",
        f"- Polymarket retained: `{summary['retained_by_source']['polymarket']}`",
        f"- Top text similarity: `{summary['top_text_similarity']}`",
        f"- Overlap improved: `{str(summary['overlap_improved']).lower()}`",
        f"- Recommended next query: {summary['recommended_next_query']}",
        "",
        "## Category Counts",
        "",
        "| Category | Kalshi | Polymarket |",
        "| --- | ---: | ---: |",
    ]
    categories = sorted(set(summary["category_counts_by_source"]["kalshi"]) | set(summary["category_counts_by_source"]["polymarket"]))
    for category in categories:
        lines.append(
            f"| {category} | {summary['category_counts_by_source']['kalshi'].get(category, 0)} | "
            f"{summary['category_counts_by_source']['polymarket'].get(category, 0)} |"
        )
    lines.extend(
        [
            "",
            "## Top Textual Overlap Candidates",
            "",
            "| Similarity | Polymarket | Kalshi |",
            "| ---: | --- | --- |",
        ]
    )
    for row in summary["top_textual_overlap_candidates"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(row["similarity_score"]),
                    _markdown_cell(row["polymarket"]["title_or_question"]),
                    _markdown_cell(row["kalshi"]["title_or_question"]),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Filter Samples", ""])
    diagnostics = summary.get("filter_diagnostics_by_source", {})
    for source_id in ("kalshi", "polymarket"):
        source_diagnostics = diagnostics.get(source_id, {})
        lines.append(f"### {source_id}")
        lines.append("")
        lines.append(f"- Rejected counts: `{source_diagnostics.get('rejected_count_by_reason') or {}}`")
        lines.append("- Retained examples:")
        for sample in source_diagnostics.get("sample_retained_markets") or []:
            lines.append(f"  - `{sample.get('identifier')}` {sample.get('title_or_question')}")
        lines.append("- Rejected examples:")
        for sample in source_diagnostics.get("sample_rejected_markets") or []:
            lines.append(f"  - `{sample.get('identifier')}` {sample.get('title_or_question')}")
        lines.append("")
    lines.append("")
    return "\n".join(lines)


def sweep_live_overlap_universe(
    *,
    categories: str,
    max_markets: int,
    timeout_seconds: float,
    kalshi_max_pages: int,
    sleep_seconds: float = 0.0,
    snapshot_dir: Path,
    json_output: Path,
    markdown_output: Path,
) -> int:
    try:
        report = build_live_overlap_sweep_report(
            categories=categories,
            max_markets=max_markets,
            timeout_seconds=timeout_seconds,
            kalshi_max_pages=kalshi_max_pages,
            sleep_seconds=sleep_seconds,
            snapshot_dir=snapshot_dir,
        )
    except Exception as exc:
        print(f"live_overlap_sweep_status=FAILED error_category={_error_category(exc)} message={_safe_cli_text(exc)}")
        return 1
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(_live_overlap_sweep_markdown(report), encoding="utf-8")
    summary = report["summary"]
    print(
        "live_overlap_sweep_status="
        f"{report['status']} rows={summary['row_count']} completed={summary['completed_count']} "
        f"pairs={summary['total_pairs']} best_next={_safe_cli_text(summary['best_next_investigation'] or 'none')} "
        f"json={json_output} markdown={markdown_output}"
    )
    return 0 if report["status"] in {"OK", "NO_CATEGORIES"} else 1


def build_live_overlap_sweep_report(
    *,
    categories: str,
    max_markets: int,
    timeout_seconds: float,
    kalshi_max_pages: int,
    sleep_seconds: float = 0.0,
    snapshot_dir: Path,
) -> dict[str, Any]:
    requested_categories = _parse_sweep_categories(categories)
    generated_at = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    first_row = True
    for category in requested_categories:
        for query in _queries_for_sweep_category(category):
            if not first_row and sleep_seconds > 0:
                time.sleep(sleep_seconds)
            first_row = False
            rows.append(
                _live_overlap_sweep_row(
                    category=category,
                    query=query,
                    max_markets=max_markets,
                    timeout_seconds=timeout_seconds,
                    kalshi_max_pages=kalshi_max_pages,
                    snapshot_dir=snapshot_dir,
                )
            )
    summary = _live_overlap_sweep_summary(rows)
    status = "NO_CATEGORIES" if not requested_categories else "OK"
    return {
        "schema_version": 1,
        "source": "live_overlap_sweep",
        "status": status,
        "generated_at": generated_at.isoformat(),
        "research_only": True,
        "readiness_promotion": "none",
        "default_scan_data_source_mode": "STATIC_FIXTURE",
        "default_scan_live_fetch_attempted": False,
        "requested_categories": requested_categories,
        "max_markets": max_markets,
        "sleep_seconds": sleep_seconds,
        "rows": rows,
        "summary": summary,
        "safety": {
            "execution_enabled": False,
            "uses_reference_as_executable_leg": False,
            "uses_the_odds_api_as_executable_leg": False,
            "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
            "readiness_promotion": "none",
            "same_payoff_asserted": False,
            "thresholds_changed": False,
            "profit_claim": False,
        },
        "disclaimer": (
            "Explicit live-read-only overlap sweep. Rows are diagnostics only, do not assert same payoff, "
            "do not grant paper/live readiness, and do not use reference-only sources as executable legs."
        ),
    }


def _live_overlap_sweep_row(
    *,
    category: str,
    query: str,
    max_markets: int,
    timeout_seconds: float,
    kalshi_max_pages: int,
    snapshot_dir: Path,
) -> dict[str, Any]:
    row_label = _safe_overlap_label(None, category=category, query=query)
    row_snapshot_dir = snapshot_dir / "sweep" / row_label
    try:
        overlap = build_live_overlap_universe_report(
            category=category,
            query=query,
            max_markets=max_markets,
            timeout_seconds=timeout_seconds,
            kalshi_max_pages=kalshi_max_pages,
            output_dir=row_snapshot_dir,
        )["report"]
        inspection = build_live_snapshot_inspection_report(snapshot_dir=row_snapshot_dir)
        match = build_live_readonly_match_report(snapshot_dir=row_snapshot_dir, include_reference_context=True)
        diagnostics = build_live_matching_diagnostics_report(snapshot_dir=row_snapshot_dir)
    except Exception as exc:
        return {
            "category": category,
            "query": query,
            "status": "FAILED",
            "failure_reason": _safe_cli_text(exc),
            "research_only": True,
            "readiness_promotion": "none",
            "recommended_next_step": "fix_fetch_or_validation_before_matching",
        }

    match_summary = match.get("match_summary") or {}
    blockers = match_summary.get("top_blockers") or []
    relationship_blocker_count = _relationship_blocker_count(blockers)
    pair_count = int(match_summary.get("pair_count") or 0)
    watch_count = sum(1 for pair in match.get("pairs") or [] if isinstance(pair, dict) and pair.get("action") == "WATCH")
    manual_review_count = sum(1 for pair in match.get("pairs") or [] if isinstance(pair, dict) and pair.get("action") == "MANUAL_REVIEW")
    row = {
        "category": category,
        "query": query,
        "status": "OK",
        "snapshot_dir": str(row_snapshot_dir),
        "source_snapshot_paths": overlap.get("source_snapshot_paths", {}),
        "research_only": True,
        "readiness_promotion": "none",
        "kalshi_retained_count": overlap["summary"]["retained_by_source"]["kalshi"],
        "polymarket_retained_count": overlap["summary"]["retained_by_source"]["polymarket"],
        "raw_comparisons": overlap["summary"]["raw_cross_source_candidate_comparisons"],
        "pair_count": pair_count,
        "watch_count": watch_count,
        "manual_review_count": manual_review_count,
        "top_similarity": overlap["summary"]["top_text_similarity"],
        "top_blockers": blockers,
        "relationship_blocker_count": relationship_blocker_count,
        "relationship_blocker_weight": _relationship_blocker_weight(pair_count, relationship_blocker_count),
        "cleaner_than_sports_debug_baseline": _cleaner_than_sports_debug_baseline(blockers, pair_count),
        "worth_enrichment_next": _worth_enrichment_next(pair_count, blockers),
        "recommended_next_step": _sweep_row_recommendation(pair_count, blockers, overlap["summary"]["top_text_similarity"]),
        "targeting": {
            "kalshi": overlap["fetch"]["kalshi"]["direct_targeting"],
            "polymarket": overlap["fetch"]["polymarket"]["direct_targeting"],
            "kalshi_method": overlap["fetch"]["kalshi"].get("targeting_method"),
            "polymarket_method": overlap["fetch"]["polymarket"].get("targeting_method"),
            "kalshi_profile_attempted": overlap["fetch"]["kalshi"].get("source_specific_profile_attempted"),
            "polymarket_profile_attempted": overlap["fetch"]["polymarket"].get("source_specific_profile_attempted"),
            "kalshi_attempted_series_tickers": overlap["fetch"]["kalshi"].get("attempted_series_tickers") or [],
            "kalshi_series_results": overlap["fetch"]["kalshi"].get("series_results") or [],
            "kalshi_filter_mode": overlap["summary"]["filter_diagnostics_by_source"]["kalshi"].get("mode"),
            "polymarket_filter_mode": overlap["summary"]["filter_diagnostics_by_source"]["polymarket"].get("mode"),
        },
        "kalshi_sample_retained_markets": overlap["summary"]["filter_diagnostics_by_source"]["kalshi"].get("sample_retained_markets") or [],
        "kalshi_sample_rejected_markets": overlap["summary"]["filter_diagnostics_by_source"]["kalshi"].get("sample_rejected_markets") or [],
        "inspection_statuses": {
            item["source_id"]: item["safety_status"]
            for item in inspection.get("rows", [])
            if isinstance(item, dict) and item.get("source_id")
        },
        "diagnostics": diagnostics.get("comparison_summary", {}),
        "top_textual_overlap_candidates": overlap["summary"].get("top_textual_overlap_candidates", [])[:3],
    }
    return row


def _parse_sweep_categories(categories: str) -> list[str]:
    allowed = set(_LIVE_OVERLAP_SWEEP_QUERIES)
    parsed = []
    for raw in categories.split(","):
        category = raw.strip().lower()
        if not category:
            continue
        if category not in allowed:
            continue
        if category not in parsed:
            parsed.append(category)
    return parsed


def _queries_for_sweep_category(category: str) -> tuple[str, ...]:
    return _LIVE_OVERLAP_SWEEP_QUERIES.get(category, ())


def _relationship_blocker_count(blockers: list[dict[str, Any]]) -> int:
    names = {
        "relationship_manual_review_required",
        "sports_competition_scope_mismatch",
        "sports_team_alias_mismatch",
        "weak_title_semantic_only_match",
    }
    return sum(int(row.get("count") or 0) for row in blockers if row.get("blocker") in names)


def _relationship_blocker_weight(pair_count: int, relationship_blocker_count: int) -> str:
    if pair_count <= 0:
        return "none_no_pairs"
    if relationship_blocker_count == 0:
        return "lighter_than_sports_debug_baseline"
    if relationship_blocker_count >= pair_count:
        return "heavy_like_sports_or_worse"
    return "mixed"


def _cleaner_than_sports_debug_baseline(blockers: list[dict[str, Any]], pair_count: int) -> bool:
    blocker_names = {str(row.get("blocker")) for row in blockers}
    return pair_count > 0 and not ({"sports_competition_scope_mismatch", "sports_team_alias_mismatch"} & blocker_names)


def _worth_enrichment_next(pair_count: int, blockers: list[dict[str, Any]]) -> bool:
    if pair_count <= 0:
        return False
    blocker_names = {str(row.get("blocker")) for row in blockers}
    hard_relationship = {"sports_competition_scope_mismatch", "sports_team_alias_mismatch"} & blocker_names
    return not hard_relationship


def _sweep_row_recommendation(pair_count: int, blockers: list[dict[str, Any]], top_similarity: float) -> str:
    if pair_count > 0 and _worth_enrichment_next(pair_count, blockers):
        return "consider_pair_only_enrichment_next"
    if top_similarity >= 0.6:
        return "improve_targeting_or_relationship_review_before_enrichment"
    return "skip_or_try_narrower_query"


def _live_overlap_sweep_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [row for row in rows if row.get("status") == "OK"]
    candidates = [row for row in completed if row.get("worth_enrichment_next")]
    candidates.sort(key=lambda row: (int(row.get("pair_count") or 0), float(row.get("top_similarity") or 0.0)), reverse=True)
    total_pairs = sum(int(row.get("pair_count") or 0) for row in completed)
    kalshi_zero = [
        {"category": row.get("category"), "query": row.get("query")}
        for row in completed
        if int(row.get("kalshi_retained_count") or 0) == 0
    ]
    polymarket_zero = [
        {"category": row.get("category"), "query": row.get("query")}
        for row in completed
        if int(row.get("polymarket_retained_count") or 0) == 0
    ]
    return {
        "row_count": len(rows),
        "completed_count": len(completed),
        "failed_count": len(rows) - len(completed),
        "total_pairs": total_pairs,
        "total_watch": sum(int(row.get("watch_count") or 0) for row in completed),
        "total_manual_review": sum(int(row.get("manual_review_count") or 0) for row in completed),
        "kalshi_zero_retention_count": len(kalshi_zero),
        "polymarket_zero_retention_count": len(polymarket_zero),
        "kalshi_zero_retention_rows": kalshi_zero,
        "polymarket_zero_retention_rows": polymarket_zero,
        "best_next_investigation": candidates[0]["query"] if candidates else None,
        "best_next_category": candidates[0]["category"] if candidates else None,
        "categories_worth_enrichment": [
            {"category": row["category"], "query": row["query"], "pair_count": row["pair_count"], "top_similarity": row["top_similarity"]}
            for row in candidates[:5]
        ],
        "recommendation": _live_overlap_sweep_recommendation(
            candidates,
            total_pairs,
            kalshi_zero_count=len(kalshi_zero),
            polymarket_zero_count=len(polymarket_zero),
        ),
    }


def _live_overlap_sweep_recommendation(
    candidates: list[dict[str, Any]],
    total_pairs: int,
    *,
    kalshi_zero_count: int = 0,
    polymarket_zero_count: int = 0,
) -> str:
    if candidates:
        first = candidates[0]
        return f"Next investigate {first['category']} / {first['query']} with pair-only enrichment and relationship review."
    if total_pairs > 0:
        return "Some pairs were found, but blockers remain heavy; improve targeting or semantic relationship review before enrichment."
    if kalshi_zero_count > polymarket_zero_count and kalshi_zero_count > 0:
        return (
            "No non-sports category produced pairs; many rows still retained zero Kalshi markets, so the blocker is likely "
            "incomplete Kalshi-side series/event targeting rather than proof no opportunities exist. Verify additional "
            "Kalshi series profiles and pair them with narrower Polymarket tags/queries next."
        )
    return "No non-sports category produced pairs; try narrower source-specific queries or expand executable venue coverage."


def _live_overlap_sweep_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Live Overlap Sweep",
        "",
        "Explicit live-read-only non-sports sweep. Diagnostics only: no same-payoff assertion, no paper/live readiness, no trade or profit claim.",
        "",
        f"- Status: `{report['status']}`",
        f"- Rows: `{report['summary']['row_count']}`",
        f"- Total pairs: `{report['summary']['total_pairs']}`",
        f"- Kalshi zero-retention rows: `{report['summary']['kalshi_zero_retention_count']}`",
        f"- Polymarket zero-retention rows: `{report['summary']['polymarket_zero_retention_count']}`",
        f"- Recommendation: {report['summary']['recommendation']}",
        "- Sweep rows write labelled snapshots under the sweep snapshot directory so default live snapshots are not silently left as the final sweep row.",
        "",
        "| Category | Query | Kalshi target | Polymarket target | Kalshi | Polymarket | Comparisons | Pairs | WATCH | MANUAL_REVIEW | Top similarity | Relationship blockers | Worth enrichment | Top blockers |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for row in report["rows"]:
        if row.get("status") != "OK":
            lines.append(
                "| "
                + " | ".join(
                    [
                        _markdown_cell(row.get("category")),
                        _markdown_cell(row.get("query")),
                        _markdown_cell("failed"),
                        _markdown_cell("failed"),
                        "0",
                        "0",
                        "0",
                        "0",
                        "0",
                        "0",
                        "0",
                        _markdown_cell("failed"),
                        "no",
                        _markdown_cell(row.get("failure_reason") or ""),
                    ]
                )
                + " |"
            )
            continue
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(row.get("category")),
                    _markdown_cell(row.get("query")),
                    _markdown_cell((row.get("targeting") or {}).get("kalshi")),
                    _markdown_cell((row.get("targeting") or {}).get("polymarket")),
                    _markdown_cell(row.get("kalshi_retained_count")),
                    _markdown_cell(row.get("polymarket_retained_count")),
                    _markdown_cell(row.get("raw_comparisons")),
                    _markdown_cell(row.get("pair_count")),
                    _markdown_cell(row.get("watch_count")),
                    _markdown_cell(row.get("manual_review_count")),
                    _markdown_cell(row.get("top_similarity")),
                    _markdown_cell(row.get("relationship_blocker_weight")),
                    _markdown_cell(_yes_no(row.get("worth_enrichment_next"))),
                    _markdown_cell(_format_sweep_blockers(row.get("top_blockers") or [])),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def _format_sweep_blockers(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "none"
    return ",".join(f"{row.get('blocker')}:{row.get('count')}" for row in rows[:3])


def inspect_live_snapshots(
    *,
    snapshot_dir: Path,
    json_output: Path,
    markdown_output: Path,
) -> int:
    report = build_live_snapshot_inspection_report(snapshot_dir=snapshot_dir)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(_live_snapshot_inspection_markdown(report), encoding="utf-8")
    print(
        "live_snapshot_inspection_status=OK "
        f"sources={len(report['rows'])} json={json_output} markdown={markdown_output}"
    )
    for row in report["rows"]:
        print(
            "live_snapshot_inspection_row "
            f"source_id={row['source_id']} "
            f"safety_status={row['safety_status']} "
            f"found={str(row['file_found']).lower()} "
            f"live_fetch_succeeded={str(row['live_fetch_succeeded']).lower()} "
            f"records={row['record_count']} "
            f"missing={','.join(row['missing_required_fields_for_future_matching']) or 'none'}"
        )
    return 0


def match_live_readonly_snapshots(
    *,
    snapshot_dir: Path,
    min_similarity: float,
    max_snapshot_age_hours: float,
    json_output: Path,
    markdown_output: Path,
    include_reference_context: bool = True,
) -> int:
    report = build_live_readonly_match_report(
        snapshot_dir=snapshot_dir,
        min_similarity=min_similarity,
        max_snapshot_age_hours=max_snapshot_age_hours,
        include_reference_context=include_reference_context,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(_live_readonly_match_markdown(report), encoding="utf-8")
    pair_count = report["match_summary"]["pair_count"]
    actions = ",".join(report["match_summary"]["actions"]) or "none"
    print(
        "live_readonly_match_status="
        f"{report['status']} pairs={pair_count} actions={actions} "
        f"json={json_output} markdown={markdown_output}"
    )
    if report["validation_errors"]:
        print(f"live_readonly_match_validation_errors={','.join(report['validation_errors'])}")
        return 1
    return 0


def build_live_readonly_match_report(
    *,
    snapshot_dir: Path,
    min_similarity: float = 0.68,
    max_snapshot_age_hours: float = 24.0,
    include_reference_context: bool = True,
) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc)
    paths = {
        "kalshi": snapshot_dir / "kalshi_live_readonly_snapshot.json",
        "polymarket": snapshot_dir / "polymarket_live_readonly_snapshot.json",
        "the_odds_api_reference": snapshot_dir / "the_odds_api_reference_snapshot.json",
    }
    validation = _validate_live_readonly_match_inputs(paths["kalshi"], paths["polymarket"])
    reference_paths = []
    if include_reference_context and _reference_snapshot_valid_for_context(paths["the_odds_api_reference"]):
        reference_paths.append(paths["the_odds_api_reference"])

    matcher_payload: dict[str, Any] | None = None
    pairs: list[dict[str, Any]] = []
    if not validation["errors"]:
        matcher_payload = match_snapshot_files(
            paths["polymarket"],
            paths["kalshi"],
            now=generated_at,
            max_snapshot_age_hours=max_snapshot_age_hours,
            min_similarity=min_similarity,
            reference_snapshot_paths=reference_paths,
        )
        pairs = [_research_only_pair(pair) for pair in matcher_payload.get("pairs", []) if pair.get("action") in {"WATCH", "MANUAL_REVIEW"}]

    actions = sorted({str(pair.get("action")) for pair in pairs})
    blockers = _top_match_blockers(pairs)
    provenance = _live_readonly_match_provenance(paths, validation, matcher_payload, reference_paths, generated_at)
    return {
        "schema_version": 1,
        "source": "live_readonly_saved_snapshot_match",
        "status": "VALIDATION_FAILED" if validation["errors"] else "OK",
        "generated_at": generated_at.isoformat(),
        "research_only": True,
        "readiness_promotion": "none",
        "default_scan_data_source_mode": "STATIC_FIXTURE",
        "default_scan_live_fetch_attempted": False,
        "validation_errors": validation["errors"],
        "input_validation": validation["rows"],
        "provenance": provenance,
        "reference_context_used": bool(reference_paths),
        "reference_context_role": "reference_context_only" if reference_paths else "none",
        "match_summary": {
            "pair_count": len(pairs),
            "actions": actions,
            "top_blockers": blockers,
        },
        "pairs": pairs,
        "safety": {
            "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
            "uses_live_api": False,
            "uses_reference_as_executable_leg": False,
            "execution_enabled": False,
            "profit_claim": False,
            "readiness_promotion": "none",
        },
    }


def _validate_live_readonly_match_inputs(kalshi_path: Path, polymarket_path: Path) -> dict[str, Any]:
    rows = [
        _validate_live_readonly_snapshot(kalshi_path, expected_source_id="kalshi"),
        _validate_live_readonly_snapshot(polymarket_path, expected_source_id="polymarket"),
    ]
    _validate_live_readonly_universe_alignment(rows)
    return {
        "rows": rows,
        "errors": [issue for row in rows for issue in row["issues"]],
    }


def _validate_live_readonly_snapshot(path: Path, *, expected_source_id: str) -> dict[str, Any]:
    row = {
        "path": str(path),
        "expected_source_id": expected_source_id,
        "source_id": None,
        "source_type": None,
        "data_source_mode": None,
        "captured_at": None,
        "overlap_universe": None,
        "overlap_universe_key": None,
        "live_fetch_succeeded": False,
        "normalized_count": 0,
        "issues": [],
    }
    if not path.exists():
        row["issues"].append(f"{expected_source_id}:snapshot_not_found")
        return row
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        row["issues"].append(f"{expected_source_id}:invalid_json")
        return row
    if not isinstance(payload, dict):
        row["issues"].append(f"{expected_source_id}:invalid_shape")
        return row
    source_id = payload.get("source_id")
    row.update(
        {
            "source_id": source_id,
            "source_type": _source_type(str(source_id or "")),
            "data_source_mode": payload.get("data_source_mode"),
            "captured_at": payload.get("captured_at"),
            "overlap_universe": payload.get("overlap_universe")
            if isinstance(payload.get("overlap_universe"), dict)
            else None,
            "live_fetch_succeeded": payload.get("live_fetch_succeeded") is True,
            "normalized_count": len(payload.get("normalized_markets")) if isinstance(payload.get("normalized_markets"), list) else 0,
        }
    )
    row["overlap_universe_key"] = _live_readonly_universe_key(row["overlap_universe"])
    if source_id != expected_source_id:
        row["issues"].append(f"{expected_source_id}:source_id_invalid")
    if row["source_type"] != SourceType.EXECUTABLE_VENUE.value:
        row["issues"].append(f"{expected_source_id}:source_type_not_executable")
    if payload.get("data_source_mode") not in {"LIVE_API", "SAVED_SNAPSHOT"}:
        row["issues"].append(f"{expected_source_id}:data_source_mode_invalid")
    if payload.get("live_fetch_succeeded") is not True:
        row["issues"].append(f"{expected_source_id}:live_fetch_not_succeeded")
    if not isinstance(payload.get("normalized_markets"), list):
        row["issues"].append(f"{expected_source_id}:missing_normalized_markets")
    if not payload.get("captured_at"):
        row["issues"].append(f"{expected_source_id}:missing_captured_at")
    if _payload_has_secretish_key(payload):
        row["issues"].append(f"{expected_source_id}:secretish_field_present")
    return row


def _live_readonly_universe_key(overlap_universe: Any) -> str | None:
    if not isinstance(overlap_universe, dict):
        return None
    category = overlap_universe.get("category")
    query = overlap_universe.get("query")
    if category is None and query is None:
        return None
    return f"{str(category or '').strip().lower()}::{str(query or '').strip().lower()}"


def _validate_live_readonly_universe_alignment(rows: list[dict[str, Any]]) -> None:
    universe_keys = {
        row["expected_source_id"]: row.get("overlap_universe_key")
        for row in rows
        if row.get("overlap_universe_key")
    }
    if not universe_keys:
        return
    if len(universe_keys) != len(rows):
        for row in rows:
            if not row.get("overlap_universe_key"):
                row["issues"].append(f"{row['expected_source_id']}:overlap_universe_missing")
        return
    if len(set(universe_keys.values())) > 1:
        for row in rows:
            row["issues"].append(f"{row['expected_source_id']}:overlap_universe_mismatch")


def _reference_snapshot_valid_for_context(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return (
        isinstance(payload, dict)
        and payload.get("schema_kind") == "reference_snapshot_v1"
        and payload.get("source_type") == SourceType.REFERENCE_ONLY.value
        and isinstance(payload.get("normalized_records"), list)
        and not _payload_has_secretish_key(payload)
    )


def _research_only_pair(pair: dict[str, Any]) -> dict[str, Any]:
    sanitized = json.loads(json.dumps(pair))
    sanitized["action"] = "WATCH" if sanitized.get("action") not in {"WATCH", "MANUAL_REVIEW"} else sanitized["action"]
    sanitized["research_blockers"] = _research_pair_blockers(sanitized)
    sanitized["research_only"] = True
    sanitized["readiness_promotion"] = "none"
    sanitized.pop("notes", None)
    return sanitized


def _research_pair_blockers(pair: dict[str, Any]) -> list[str]:
    blockers = set(pair.get("ineligibility_reasons") or [])
    fields = pair.get("matched_fields") if isinstance(pair.get("matched_fields"), dict) else {}
    relationship = pair.get("contract_relationship") if isinstance(pair.get("contract_relationship"), dict) else {}
    if relationship.get("manual_review_required") is True:
        blockers.add("relationship_manual_review_required")
    for reason in relationship.get("blocking_reasons") or []:
        blockers.add(str(reason))
    if fields.get("settlement_time_warning"):
        blockers.add(str(fields["settlement_time_warning"]))
    if fields.get("settlement_time_delta_seconds") is None:
        blockers.add("deadline_timezone_review_required")
    if float(fields.get("question_similarity") or 0.0) < 0.8:
        blockers.add("weak_title_semantic_only_match")
    blockers.add("missing_orderbook_depth")
    blockers.add("missing_fees")
    blockers.add("missing_quote_timestamp")
    return sorted(blockers)


def _top_match_blockers(pairs: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for pair in pairs:
        for blocker in pair.get("research_blockers") or []:
            counts[blocker] = counts.get(blocker, 0) + 1
    return [
        {"blocker": blocker, "count": count}
        for blocker, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


def _live_readonly_match_provenance(
    paths: dict[str, Path],
    validation: dict[str, Any],
    matcher_payload: dict[str, Any] | None,
    reference_paths: list[Path],
    generated_at: datetime,
) -> dict[str, Any]:
    rows = {row["expected_source_id"]: row for row in validation["rows"]}
    return {
        "generated_at": generated_at.isoformat(),
        "research_only": True,
        "readiness_promotion": "none",
        "source_snapshot_paths": {
            "kalshi": str(paths["kalshi"]),
            "polymarket": str(paths["polymarket"]),
            "the_odds_api_reference": str(paths["the_odds_api_reference"]),
        },
        "captured_at": {
            "kalshi": rows.get("kalshi", {}).get("captured_at"),
            "polymarket": rows.get("polymarket", {}).get("captured_at"),
        },
        "data_source_mode": {
            "kalshi": rows.get("kalshi", {}).get("data_source_mode"),
            "polymarket": rows.get("polymarket", {}).get("data_source_mode"),
        },
        "live_fetch_attempted": {"kalshi": True, "polymarket": True},
        "live_fetch_succeeded": {
            "kalshi": rows.get("kalshi", {}).get("live_fetch_succeeded") is True,
            "polymarket": rows.get("polymarket", {}).get("live_fetch_succeeded") is True,
        },
        "reference_context_paths": [str(path) for path in reference_paths],
        "matcher_snapshot_issues": {} if matcher_payload is None else matcher_payload.get("snapshot_issues", {}),
        "reference_context": {} if matcher_payload is None else matcher_payload.get("reference_context", {}),
    }


def _payload_has_secretish_key(value: Any) -> bool:
    secret_tokens = (
        "api_key",
        "apikey",
        "api_secret",
        "private_key",
        "signing_key",
        "auth_token",
        "session_token",
        "password",
        "wallet_private",
    )
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key).lower()
            if any(token in key_text for token in secret_tokens):
                return True
            if _payload_has_secretish_key(child):
                return True
    if isinstance(value, list):
        return any(_payload_has_secretish_key(item) for item in value)
    return False


def _live_readonly_match_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Live Read-Only Snapshot Match Report",
        "",
        "Saved-file research-only Kalshi vs Polymarket matching. No live API fetches, scoring integration, execution, or readiness promotion.",
        "",
        f"- Status: `{report['status']}`",
        f"- Matched pairs: `{report['match_summary']['pair_count']}`",
        f"- Actions: `{','.join(report['match_summary']['actions']) or 'none'}`",
        f"- Reference context used: `{str(report['reference_context_used']).lower()}`",
        "",
        "## Top Blockers",
        "",
    ]
    if report["match_summary"]["top_blockers"]:
        for row in report["match_summary"]["top_blockers"]:
            lines.append(f"- `{row['blocker']}`: {row['count']}")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Pairs",
            "",
            "| Action | Similarity | Polymarket | Kalshi | Blockers |",
            "| --- | ---: | --- | --- | --- |",
        ]
    )
    for pair in report["pairs"]:
        poly = pair.get("polymarket") if isinstance(pair.get("polymarket"), dict) else {}
        kalshi = pair.get("kalshi") if isinstance(pair.get("kalshi"), dict) else {}
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(pair.get("action")),
                    _markdown_cell(pair.get("similarity_score")),
                    _markdown_cell(poly.get("question") or poly.get("market_id") or ""),
                    _markdown_cell(kalshi.get("question") or kalshi.get("ticker") or ""),
                    _markdown_cell(",".join(pair.get("research_blockers") or []) or "none"),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def enrich_live_match_candidates(
    *,
    match_report: Path,
    snapshot_dir: Path,
    timeout_seconds: float,
    max_snapshot_age_hours: float,
    json_output: Path,
    markdown_output: Path,
) -> int:
    try:
        report = build_live_match_candidate_enrichment_report(
            match_report_path=match_report,
            snapshot_dir=snapshot_dir,
            timeout_seconds=timeout_seconds,
            max_snapshot_age_hours=max_snapshot_age_hours,
        )
    except Exception as exc:
        print(f"live_match_candidate_enrichment_status=FAILED error_category={_error_category(exc)} message={_safe_cli_text(exc)}")
        return 1
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(_live_match_candidate_enrichment_markdown(report), encoding="utf-8")
    summary = report["summary"]
    print(
        "live_match_candidate_enrichment_status="
        f"{report['status']} pairs={summary['pair_count']} "
        f"depth_available={summary['depth_available_count']} "
        f"fees_available={summary['fees_available_count']} "
        f"quote_timestamp_available={summary['quote_timestamp_available_count']} "
        f"json={json_output} markdown={markdown_output}"
    )
    return 0 if report["status"] == "OK" else 1


def build_live_match_candidate_enrichment_report(
    *,
    match_report_path: Path,
    snapshot_dir: Path,
    timeout_seconds: float = 10.0,
    max_snapshot_age_hours: float = 24.0,
    now: datetime | None = None,
    kalshi_client: KalshiOrderbookClient | None = None,
    polymarket_client: PolymarketOrderbookClient | None = None,
    kalshi_fee_model: FeeModel | None = None,
    polymarket_fee_model: FeeModel | None = None,
    kalshi_fee_model_status: str = "reviewed_conservative",
    polymarket_fee_model_status: str = "reviewed_official_category_schedule_2026_05_22",
) -> dict[str, Any]:
    generated_at = now or datetime.now(timezone.utc)
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("now must include timezone information")
    match_payload = _load_json_report(match_report_path, "live_readonly_match_report")
    if match_payload.get("schema_version") != 1:
        raise ValueError("live_readonly_match_report schema_version must be 1")
    if match_payload.get("source") != "live_readonly_saved_snapshot_match":
        raise ValueError("live_readonly_match_report source must be live_readonly_saved_snapshot_match")

    pairs = [
        pair
        for pair in match_payload.get("pairs") or []
        if isinstance(pair, dict) and pair.get("action") in {"WATCH", "MANUAL_REVIEW"}
    ]
    kalshi_snapshot_path = snapshot_dir / "kalshi_live_readonly_snapshot.json"
    polymarket_snapshot_path = snapshot_dir / "polymarket_live_readonly_snapshot.json"
    kalshi_snapshot = _load_json_report(kalshi_snapshot_path, "kalshi_live_readonly_snapshot")
    polymarket_snapshot = _load_json_report(polymarket_snapshot_path, "polymarket_live_readonly_snapshot")

    selected_kalshi = _selected_snapshot_for_pairs(
        kalshi_snapshot,
        pairs,
        venue="kalshi",
        pair_key=("kalshi", "ticker"),
        fallback_key=("kalshi", "market_id"),
    )
    selected_polymarket = _selected_snapshot_for_pairs(
        polymarket_snapshot,
        pairs,
        venue="polymarket",
        pair_key=("polymarket", "market_id"),
        fallback_key=("polymarket", "condition_id"),
    )
    kalshi_client = kalshi_client or KalshiOrderbookClient(timeout_seconds=timeout_seconds)
    polymarket_client = polymarket_client or PolymarketOrderbookClient(timeout_seconds=timeout_seconds)
    enriched_kalshi = enrich_orderbook_snapshot(
        selected_kalshi,
        venue="kalshi",
        captured_at=generated_at,
        max_snapshot_age_hours=max_snapshot_age_hours,
        kalshi_client=kalshi_client,
        polymarket_client=polymarket_client,
    )
    enriched_polymarket = enrich_orderbook_snapshot(
        selected_polymarket,
        venue="polymarket",
        captured_at=generated_at,
        max_snapshot_age_hours=max_snapshot_age_hours,
        kalshi_client=kalshi_client,
        polymarket_client=polymarket_client,
    )
    kalshi_enrichment_by_id = _enrichment_by_identifier(enriched_kalshi, keys=("ticker", "market_id"))
    polymarket_enrichment_by_id = _enrichment_by_identifier(enriched_polymarket, keys=("market_id", "condition_id"))

    rows = []
    fee_config = {
        "kalshi_fee_model": kalshi_fee_model or KalshiTieredFeeModel(),
        "polymarket_fee_model": polymarket_fee_model or PolymarketConservativeFeeModel(),
        "kalshi_fee_model_status": kalshi_fee_model_status,
        "polymarket_fee_model_status": polymarket_fee_model_status,
    }
    for pair in pairs:
        kalshi_id = _pair_identifier(pair, "kalshi", "ticker") or _pair_identifier(pair, "kalshi", "market_id")
        polymarket_id = _pair_identifier(pair, "polymarket", "market_id") or _pair_identifier(pair, "polymarket", "condition_id")
        rows.append(
            _enriched_match_candidate_row(
                pair,
                kalshi_id=kalshi_id,
                polymarket_id=polymarket_id,
                kalshi_enrichment=kalshi_enrichment_by_id.get(kalshi_id or ""),
                polymarket_enrichment=polymarket_enrichment_by_id.get(polymarket_id or ""),
                generated_at=generated_at,
                fee_config=fee_config,
            )
        )

    return {
        "schema_version": 1,
        "source": "live_match_candidate_enrichment",
        "status": "OK",
        "generated_at": generated_at.isoformat(),
        "research_only": True,
        "readiness_promotion": "none",
        "default_scan_data_source_mode": "STATIC_FIXTURE",
        "default_scan_live_fetch_attempted": False,
        "input_match_report": str(match_report_path),
        "input_snapshot_paths": {
            "kalshi": str(kalshi_snapshot_path),
            "polymarket": str(polymarket_snapshot_path),
            "the_odds_api": "not_used_as_executable_leg",
        },
        "summary": _live_match_candidate_enrichment_summary(rows, selected_kalshi, selected_polymarket),
        "pairs": rows,
        "safety": {
            "execution_enabled": False,
            "uses_reference_as_executable_leg": False,
            "uses_the_odds_api_as_executable_leg": False,
            "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
            "readiness_promotion": "none",
            "same_payoff_asserted": False,
            "thresholds_changed": False,
            "profit_claim": False,
        },
        "disclaimer": (
            "Research-only enrichment for current saved WATCH/MANUAL_REVIEW pairs. "
            "Fee diagnostics, including fee-adjusted gaps if present, are not trade signals. "
            "No same-payoff assertion, paper readiness, live readiness, profit claim, or executable-liquidity claim is made. "
            "Relationship blockers remain authoritative."
        ),
        "next_step_note": (
            "After this pair-only enrichment pass, broaden explicit overlap diagnostics to macro/economics, politics, "
            "crypto/company events, weather, and additional executable venue read-only adapters."
        ),
    }


def _selected_snapshot_for_pairs(
    snapshot: dict[str, Any],
    pairs: list[dict[str, Any]],
    *,
    venue: str,
    pair_key: tuple[str, str],
    fallback_key: tuple[str, str],
) -> dict[str, Any]:
    wanted = {
        identifier
        for pair in pairs
        for identifier in (_pair_identifier(pair, *pair_key), _pair_identifier(pair, *fallback_key))
        if identifier
    }
    selected = []
    seen = set()
    for row in _normalized_market_rows(snapshot):
        identifiers = {str(row.get("market_id") or ""), str(row.get("ticker") or ""), str(row.get("condition_id") or "")}
        if not (identifiers & wanted):
            continue
        key = next(identifier for identifier in identifiers if identifier and identifier in wanted)
        if key in seen:
            continue
        seen.add(key)
        selected.append(json.loads(json.dumps(row)))
    payload = {
        "schema_version": 1,
        "source": snapshot.get("source"),
        "source_id": snapshot.get("source_id") or venue,
        "data_source_mode": snapshot.get("data_source_mode"),
        "captured_at": snapshot.get("captured_at"),
        "live_fetch_succeeded": snapshot.get("live_fetch_succeeded"),
        "event_count": None,
        "market_count": len(selected),
        "normalized_count": len(selected),
        "normalized_markets": selected,
        "pair_only_enrichment_input": True,
        "research_only": True,
    }
    return _redact_secretish_fields(payload)


def _enrichment_by_identifier(snapshot: dict[str, Any], *, keys: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for row in _normalized_market_rows(snapshot):
        enrichment = row.get("orderbook_enrichment") if isinstance(row.get("orderbook_enrichment"), dict) else {}
        for key in keys:
            identifier = row.get(key)
            if identifier:
                by_id[str(identifier)] = enrichment
    return by_id


def _pair_identifier(pair: dict[str, Any], venue: str, key: str) -> str | None:
    venue_row = pair.get(venue)
    if not isinstance(venue_row, dict):
        return None
    value = venue_row.get(key)
    return None if value is None else str(value)


def _enriched_match_candidate_row(
    pair: dict[str, Any],
    *,
    kalshi_id: str | None,
    polymarket_id: str | None,
    kalshi_enrichment: dict[str, Any] | None,
    polymarket_enrichment: dict[str, Any] | None,
    generated_at: datetime,
    fee_config: dict[str, Any],
) -> dict[str, Any]:
    kalshi_enrichment = kalshi_enrichment or {}
    polymarket_enrichment = polymarket_enrichment or {}
    depth_available = _depth_metadata_available(kalshi_enrichment) and _depth_metadata_available(polymarket_enrichment)
    quote_timestamp_available = _quote_timestamp_available(kalshi_enrichment) and _quote_timestamp_available(polymarket_enrichment)
    fee_diagnostics = _fee_diagnostics(
        kalshi_enrichment=kalshi_enrichment,
        polymarket_enrichment=polymarket_enrichment,
        fee_config=fee_config,
        polymarket_category=_market_category(pair.get("polymarket") or {}),
    )
    fees_available = bool(fee_diagnostics["fees_available"])
    original_blockers = set(str(reason) for reason in pair.get("research_blockers") or [])
    remaining = set(original_blockers)
    resolved = set()
    if depth_available:
        remaining.discard("missing_orderbook_depth")
        resolved.add("missing_orderbook_depth")
    else:
        remaining.add("missing_orderbook_depth")
    if quote_timestamp_available:
        remaining.discard("missing_quote_timestamp")
        resolved.add("missing_quote_timestamp")
    else:
        remaining.add("missing_quote_timestamp")
    if fees_available:
        remaining.discard("missing_fees")
        resolved.add("missing_fees")
    else:
        remaining.add("missing_fees")
    kalshi_quote_age = _quote_age_seconds(kalshi_enrichment.get("orderbook_captured_at"), generated_at)
    polymarket_quote_age = _quote_age_seconds(polymarket_enrichment.get("orderbook_captured_at"), generated_at)
    quote_age_seconds = max(
        [age for age in (kalshi_quote_age, polymarket_quote_age) if age is not None],
        default=None,
    )
    kalshi_bid_source = _bid_ask_source(kalshi_enrichment, "best_bid")
    kalshi_ask_source = _bid_ask_source(kalshi_enrichment, "best_ask")
    polymarket_bid_source = _bid_ask_source(polymarket_enrichment, "best_bid")
    polymarket_ask_source = _bid_ask_source(polymarket_enrichment, "best_ask")
    contract_relationship = _redact_secretish_fields(pair.get("contract_relationship") or {})
    same_payoff_false = isinstance(contract_relationship, dict) and contract_relationship.get("same_payoff") is False
    gross_gap_caveat = "same_payoff=false; gross_gap_cents is not arb edge" if same_payoff_false else None
    return {
        "action": pair.get("action") if pair.get("action") in {"WATCH", "MANUAL_REVIEW"} else "WATCH",
        "research_only": True,
        "readiness_promotion": "none",
        "gross_gap_caveat": gross_gap_caveat,
        "similarity_score": pair.get("similarity_score"),
        "polymarket_market_id": polymarket_id,
        "kalshi_ticker": kalshi_id,
        "polymarket_question": (pair.get("polymarket") or {}).get("question") if isinstance(pair.get("polymarket"), dict) else None,
        "kalshi_question": (pair.get("kalshi") or {}).get("question") if isinstance(pair.get("kalshi"), dict) else None,
        "contract_relationship": contract_relationship,
        "original_research_blockers": sorted(original_blockers),
        "resolved_research_blockers": sorted(resolved),
        "remaining_research_blockers": sorted(remaining),
        "enrichment": {
            "kalshi_bid": _first_number(kalshi_enrichment.get("best_bid"), _matched_field_value(pair, "kalshi_best_bid")),
            "kalshi_ask": _first_number(kalshi_enrichment.get("best_ask"), _matched_field_value(pair, "kalshi_best_ask")),
            "kalshi_bid_source": kalshi_bid_source,
            "kalshi_ask_source": kalshi_ask_source,
            "kalshi_depth_bid": float_or_none(kalshi_enrichment.get("depth_at_best_bid")),
            "kalshi_depth_ask": float_or_none(kalshi_enrichment.get("depth_at_best_ask")),
            "kalshi_quote_timestamp": kalshi_enrichment.get("orderbook_captured_at") if _quote_timestamp_available(kalshi_enrichment) else None,
            "kalshi_orderbook_fetched_at": kalshi_enrichment.get("orderbook_captured_at") if _quote_timestamp_available(kalshi_enrichment) else None,
            "kalshi_quote_age_seconds": kalshi_quote_age,
            "kalshi_fee_model_status": fee_diagnostics["kalshi_fee_model_status"],
            "kalshi_estimated_fee_cents": fee_diagnostics["kalshi_estimated_fee_cents"],
            "polymarket_bid": _first_number(polymarket_enrichment.get("best_bid"), _matched_field_value(pair, "polymarket_best_bid")),
            "polymarket_ask": _first_number(polymarket_enrichment.get("best_ask"), _matched_field_value(pair, "polymarket_best_ask")),
            "polymarket_bid_source": polymarket_bid_source,
            "polymarket_ask_source": polymarket_ask_source,
            "polymarket_depth_bid": float_or_none(polymarket_enrichment.get("depth_at_best_bid")),
            "polymarket_depth_ask": float_or_none(polymarket_enrichment.get("depth_at_best_ask")),
            "polymarket_quote_timestamp": polymarket_enrichment.get("orderbook_captured_at") if _quote_timestamp_available(polymarket_enrichment) else None,
            "polymarket_orderbook_fetched_at": polymarket_enrichment.get("orderbook_captured_at") if _quote_timestamp_available(polymarket_enrichment) else None,
            "polymarket_quote_age_seconds": polymarket_quote_age,
            "polymarket_fee_model_status": fee_diagnostics["polymarket_fee_model_status"],
            "polymarket_fee_source_used": fee_diagnostics["polymarket_fee_source_used"],
            "polymarket_fee_source": fee_diagnostics["polymarket_fee_source"],
            "polymarket_fee_source_version": fee_diagnostics["polymarket_fee_source_version"],
            "polymarket_fee_category": fee_diagnostics["polymarket_fee_category"],
            "polymarket_fee_rate_used": fee_diagnostics["polymarket_fee_rate_used"],
            "polymarket_maker_fee_rate": fee_diagnostics["polymarket_maker_fee_rate"],
            "polymarket_maker_fee_used_for_diagnostic": False,
            "polymarket_taker_fee_used_for_diagnostic": fee_diagnostics["fees_available"],
            "polymarket_fee_assumption_type": fee_diagnostics["polymarket_fee_assumption_type"],
            "polymarket_estimated_fee_cents": fee_diagnostics["polymarket_estimated_fee_cents"],
            "gross_gap_cents": fee_diagnostics["gross_gap_cents"],
            "gross_gap_caveat": gross_gap_caveat,
            "estimated_total_fees_cents": fee_diagnostics["estimated_total_fees_cents"],
            "fee_adjusted_gap_cents": fee_diagnostics["fee_adjusted_gap_cents"],
            "quote_age_seconds": quote_age_seconds if quote_timestamp_available else None,
            "depth_available": depth_available,
            "fees_available": fees_available,
            "fee_blocker_reason": fee_diagnostics["fee_blocker_reason"],
            "quote_timestamp_available": quote_timestamp_available,
            "unresolved_enrichment_blockers": sorted(remaining),
            "kalshi_enrichment_status": kalshi_enrichment.get("enrichment_status") or "missing_market",
            "polymarket_enrichment_status": polymarket_enrichment.get("enrichment_status") or "missing_market",
            "kalshi_enrichment_warnings": kalshi_enrichment.get("enrichment_warnings") or [],
            "polymarket_enrichment_warnings": polymarket_enrichment.get("enrichment_warnings") or [],
        },
    }


def _depth_metadata_available(enrichment: dict[str, Any]) -> bool:
    return (
        enrichment.get("enrichment_status") == "enriched"
        and float_or_none(enrichment.get("depth_at_best_bid")) is not None
        and float_or_none(enrichment.get("depth_at_best_ask")) is not None
    )


def _quote_timestamp_available(enrichment: dict[str, Any]) -> bool:
    return enrichment.get("enrichment_status") == "enriched" and _parse_datetime_or_none(str(enrichment.get("orderbook_captured_at") or "")) is not None


def _fee_diagnostics(
    *,
    kalshi_enrichment: dict[str, Any],
    polymarket_enrichment: dict[str, Any],
    fee_config: dict[str, Any],
    polymarket_category: str | None = None,
) -> dict[str, Any]:
    kalshi_status = str(fee_config.get("kalshi_fee_model_status") or "missing_or_unreviewed")
    polymarket_status = str(fee_config.get("polymarket_fee_model_status") or "missing_or_unreviewed")
    kalshi_model = fee_config.get("kalshi_fee_model")
    polymarket_model = fee_config.get("polymarket_fee_model")
    prices = {
        "kalshi_bid": float_or_none(kalshi_enrichment.get("best_bid")),
        "kalshi_ask": float_or_none(kalshi_enrichment.get("best_ask")),
        "polymarket_bid": float_or_none(polymarket_enrichment.get("best_bid")),
        "polymarket_ask": float_or_none(polymarket_enrichment.get("best_ask")),
    }
    direction = _diagnostic_best_direction(prices)
    base = {
        "kalshi_fee_model_status": kalshi_status,
        "polymarket_fee_model_status": polymarket_status,
        "polymarket_fee_source_used": _polymarket_fee_source_used(polymarket_model, polymarket_category, polymarket_status),
        "polymarket_fee_source": _model_attr(polymarket_model, "source_url"),
        "polymarket_fee_source_version": _model_attr(polymarket_model, "source_version"),
        "polymarket_fee_category": _polymarket_fee_category_key(polymarket_model, polymarket_category),
        "polymarket_fee_rate_used": _polymarket_fee_rate(polymarket_model, polymarket_category),
        "polymarket_maker_fee_rate": _model_attr(polymarket_model, "maker_fee_rate"),
        "polymarket_fee_assumption_type": _model_attr(polymarket_model, "assumption_type"),
        "kalshi_estimated_fee_cents": None,
        "polymarket_estimated_fee_cents": None,
        "gross_gap_cents": None if direction is None else _cents(direction["gross_gap"]),
        "estimated_total_fees_cents": None,
        "fee_adjusted_gap_cents": None,
        "fees_available": False,
        "fee_blocker_reason": None,
    }
    missing = []
    if direction is None:
        missing.append("missing_bid_ask_for_fee_diagnostics")
    if not _fee_model_reviewed(kalshi_status) or kalshi_model is None:
        missing.append("kalshi_fee_model_missing_or_unreviewed")
    if not _fee_model_reviewed(polymarket_status) or polymarket_model is None:
        missing.append("polymarket_fee_model_missing_or_unreviewed")
    if missing:
        base["fee_blocker_reason"] = ",".join(missing)
        return base
    try:
        kalshi_fee = kalshi_model.fee_for_leg(float(direction["kalshi_would_enter_price"]))
        polymarket_price = float(direction["polymarket_would_enter_price"])
        if hasattr(polymarket_model, "fee_for_leg_for_category"):
            polymarket_fee = polymarket_model.fee_for_leg_for_category(polymarket_price, category=polymarket_category)
        else:
            polymarket_fee = polymarket_model.fee_for_leg(polymarket_price)
    except (TypeError, ValueError, AttributeError) as exc:
        base["fee_blocker_reason"] = f"fee_model_error:{_safe_cli_text(exc)}"
        return base
    total_fees = kalshi_fee + polymarket_fee
    base.update(
        {
            "kalshi_estimated_fee_cents": _cents(kalshi_fee),
            "polymarket_estimated_fee_cents": _cents(polymarket_fee),
            "estimated_total_fees_cents": _cents(total_fees),
            "fee_adjusted_gap_cents": _cents(float(direction["gross_gap"]) - total_fees),
            "fees_available": True,
            "fee_blocker_reason": None,
        }
    )
    return base


def _model_attr(model: Any, name: str) -> Any:
    return getattr(model, name, None) if model is not None else None


def _polymarket_fee_rate(model: Any, category: str | None) -> float | None:
    if model is None or not hasattr(model, "rate_for_category"):
        return None
    try:
        return float(model.rate_for_category(category))
    except (TypeError, ValueError, AttributeError):
        return None


def _polymarket_fee_category_key(model: Any, category: str | None) -> str | None:
    if model is None or not hasattr(model, "category_key"):
        return None
    try:
        value = model.category_key(category)
    except (TypeError, ValueError, AttributeError):
        return None
    return str(value) if value is not None else None


def _diagnostic_best_direction(prices: dict[str, float | None]) -> dict[str, Any] | None:
    if any(value is None for value in prices.values()):
        return None
    poly_bid = float(prices["polymarket_bid"])
    poly_ask = float(prices["polymarket_ask"])
    kalshi_bid = float(prices["kalshi_bid"])
    kalshi_ask = float(prices["kalshi_ask"])
    sell_poly_gap = poly_bid - kalshi_ask
    sell_kalshi_gap = kalshi_bid - poly_ask
    if sell_poly_gap >= sell_kalshi_gap:
        return {
            "gross_gap": round(sell_poly_gap, 6),
            "polymarket_would_enter_price": poly_bid,
            "kalshi_would_enter_price": kalshi_ask,
        }
    return {
        "gross_gap": round(sell_kalshi_gap, 6),
        "polymarket_would_enter_price": poly_ask,
        "kalshi_would_enter_price": kalshi_bid,
    }


def _fee_model_reviewed(status: str) -> bool:
    return status in {
        "reviewed_conservative",
        "reviewed_official_category_schedule_2026_05_22",
        "reviewed_official_fee_rate_endpoint_2026_05_22",
    }


def _polymarket_fee_source_used(model: Any, category: str | None, status: str) -> str:
    if model is None or not _fee_model_reviewed(status):
        return "missing_or_unreviewed"
    if status == "reviewed_official_fee_rate_endpoint_2026_05_22":
        return "official_fee_rate_endpoint"
    category_key = _polymarket_fee_category_key(model, category)
    if category_key == "other_general":
        return "conservative_unknown"
    if status == "reviewed_official_category_schedule_2026_05_22":
        return "official_category_schedule"
    return "missing_or_unreviewed"


def _cents(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value) * 100.0, 6)


def _bid_ask_source(enrichment: dict[str, Any], key: str) -> str:
    if float_or_none(enrichment.get(key)) is not None:
        return "orderbook_fetch"
    return "none"


def _quote_age_seconds(value: Any, generated_at: datetime) -> float | None:
    parsed = _parse_datetime_or_none(str(value or ""))
    if parsed is None:
        return None
    return max(0.0, round((generated_at - parsed).total_seconds(), 6))


def _max_quote_age_seconds(values: list[Any], generated_at: datetime) -> float | None:
    ages = []
    for value in values:
        age = _quote_age_seconds(value, generated_at)
        if age is not None:
            ages.append(age)
    return max(ages) if ages else None


def _first_number(*values: Any) -> float | None:
    for value in values:
        parsed = float_or_none(value)
        if parsed is not None:
            return parsed
    return None


def _matched_field_value(pair: dict[str, Any], key: str) -> Any:
    fields = pair.get("matched_fields")
    return fields.get(key) if isinstance(fields, dict) else None


def _live_match_candidate_enrichment_summary(
    rows: list[dict[str, Any]],
    selected_kalshi: dict[str, Any],
    selected_polymarket: dict[str, Any],
) -> dict[str, Any]:
    blockers = Counter()
    resolved = Counter()
    for row in rows:
        blockers.update(row.get("remaining_research_blockers") or [])
        resolved.update(row.get("resolved_research_blockers") or [])
    fee_adjusted = [
        row["enrichment"].get("fee_adjusted_gap_cents")
        for row in rows
        if row["enrichment"].get("fee_adjusted_gap_cents") is not None
    ]
    return {
        "pair_count": len(rows),
        "actions": sorted({str(row.get("action")) for row in rows}),
        "selected_markets_by_source": {
            "kalshi": len(_normalized_market_rows(selected_kalshi)),
            "polymarket": len(_normalized_market_rows(selected_polymarket)),
        },
        "depth_available_count": sum(1 for row in rows if row["enrichment"]["depth_available"]),
        "fees_available_count": sum(1 for row in rows if row["enrichment"]["fees_available"]),
        "quote_timestamp_available_count": sum(1 for row in rows if row["enrichment"]["quote_timestamp_available"]),
        "fee_adjusted_gap_cents": {
            "count": len(fee_adjusted),
            "min": round(min(fee_adjusted), 6) if fee_adjusted else None,
            "max": round(max(fee_adjusted), 6) if fee_adjusted else None,
            "median": round(median(fee_adjusted), 6) if fee_adjusted else None,
        },
        "resolved_blockers": [{"blocker": key, "count": value} for key, value in resolved.most_common()],
        "remaining_blockers": [{"blocker": key, "count": value} for key, value in blockers.most_common()],
        "relationship_blocker_count": sum(
            1
            for row in rows
            if any(
                blocker
                in {
                    "relationship_manual_review_required",
                    "sports_competition_scope_mismatch",
                    "sports_team_alias_mismatch",
                    "weak_title_semantic_only_match",
                }
                for blocker in row.get("remaining_research_blockers") or []
            )
        ),
    }


def _live_match_candidate_enrichment_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Live Match Candidate Enrichment",
        "",
        "Research-only enrichment for current saved WATCH/MANUAL_REVIEW Kalshi/Polymarket pairs. No same-payoff assertion, paper/live readiness, profit claim, or executable-liquidity claim is made.",
        "Relationship blockers remain authoritative. Depth, fee, and timestamp metadata are diagnostics only; fee-adjusted gaps are not trade signals.",
        "",
        f"- Status: `{report['status']}`",
        f"- Pairs enriched: `{summary['pair_count']}`",
        f"- Kalshi markets touched: `{summary['selected_markets_by_source']['kalshi']}`",
        f"- Polymarket markets touched: `{summary['selected_markets_by_source']['polymarket']}`",
        f"- Depth available: `{summary['depth_available_count']}`",
        f"- Fees available: `{summary['fees_available_count']}`",
        f"- Quote timestamps available: `{summary['quote_timestamp_available_count']}`",
        f"- Fee-adjusted gap diagnostics: `{summary['fee_adjusted_gap_cents']['count']}`",
        "",
        "## Remaining Blockers",
        "",
    ]
    for row in summary["remaining_blockers"]:
        lines.append(f"- `{row['blocker']}`: {row['count']}")
    if not summary["remaining_blockers"]:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Pairs",
            "",
            "| Action | Polymarket | Kalshi | Depth | Fees | Quote Timestamp | Fee-adjusted gap cents | Gross gap caveat | Remaining Blockers |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in report["pairs"]:
        enrichment = row["enrichment"]
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(row.get("action")),
                    _markdown_cell(row.get("polymarket_question") or row.get("polymarket_market_id")),
                    _markdown_cell(row.get("kalshi_question") or row.get("kalshi_ticker")),
                    _markdown_cell(_yes_no(enrichment["depth_available"])),
                    _markdown_cell(_yes_no(enrichment["fees_available"])),
                    _markdown_cell(_yes_no(enrichment["quote_timestamp_available"])),
                    _markdown_cell(enrichment.get("fee_adjusted_gap_cents")),
                    _markdown_cell(row.get("gross_gap_caveat") or "none"),
                    _markdown_cell(",".join(row.get("remaining_research_blockers") or []) or "none"),
                ]
            )
            + " |"
        )
    lines.extend(["", f"Next step: {report['next_step_note']}", ""])
    return "\n".join(lines)


def diagnose_live_matching(
    *,
    snapshot_dir: Path,
    min_similarity: float,
    top_limit: int,
    json_output: Path,
    markdown_output: Path,
) -> int:
    report = build_live_matching_diagnostics_report(
        snapshot_dir=snapshot_dir,
        min_similarity=min_similarity,
        top_limit=top_limit,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(_live_matching_diagnostics_markdown(report), encoding="utf-8")
    summary = report["comparison_summary"]
    print(
        "live_matching_diagnostics_status="
        f"{report['status']} comparisons={summary['raw_cross_source_candidate_comparisons']} "
        f"low_text={summary['rejected_by_low_title_text_similarity']} "
        f"missing_deadline={summary['rejected_by_missing_deadline_or_settlement_fields']} "
        f"schema_validation={summary['rejected_by_source_schema_validation']} "
        f"near_future_review={summary['near_future_manual_review_count']} "
        f"json={json_output} markdown={markdown_output}"
    )
    return 0 if report["status"] == "OK" else 1


_NON_SPORTS_NEAR_MISS_QUERIES = {
    "openai",
    "ai",
    "bitcoin",
    "btc",
    "ethereum",
    "eth",
    "fed",
    "fomc",
    "cpi",
    "inflation",
}


def diagnose_non_sports_near_misses(
    *,
    sweep_report: Path,
    min_similarity: float,
    top_limit: int,
    json_output: Path,
    markdown_output: Path,
) -> int:
    report = build_non_sports_near_miss_diagnostics_report(
        sweep_report=sweep_report,
        min_similarity=min_similarity,
        top_limit=top_limit,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(_non_sports_near_miss_markdown(report), encoding="utf-8")
    summary = report["summary"]
    print(
        "non_sports_near_miss_diagnostics_status="
        f"{report['status']} categories={summary['category_count']} "
        f"near_miss_rows={summary['near_miss_count']} "
        f"strongest_category={summary['strongest_category'] or 'none'} "
        f"json={json_output} markdown={markdown_output}"
    )
    return 0 if report["status"] in {"OK", "NO_DATA"} else 1


def build_non_sports_near_miss_diagnostics_report(
    *,
    sweep_report: Path,
    min_similarity: float = 0.68,
    top_limit: int = 8,
) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc)
    sweep_payload = _load_json_object(sweep_report)
    selected_rows = _selected_non_sports_sweep_rows(sweep_payload)
    categories: list[dict[str, Any]] = []
    near_misses: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for row in selected_rows:
        snapshot_dir_value = row.get("snapshot_dir")
        category = str(row.get("category") or "unknown")
        query = str(row.get("query") or "unknown")
        if not snapshot_dir_value:
            failures.append({"category": category, "query": query, "reason": "missing_snapshot_dir"})
            continue
        diagnostics = build_live_matching_diagnostics_report(
            snapshot_dir=Path(snapshot_dir_value),
            min_similarity=min_similarity,
            top_limit=top_limit,
        )
        transformed = [
            _non_sports_near_miss_row(pair, category=category, query=query, min_similarity=min_similarity)
            for pair in diagnostics.get("top_rejected_pairs") or []
        ]
        transformed = [row for row in transformed if row["similarity_score"] < min_similarity]
        categories.append(
            {
                "category": category,
                "query": query,
                "snapshot_dir": snapshot_dir_value,
                "raw_comparisons": diagnostics["comparison_summary"]["raw_cross_source_candidate_comparisons"],
                "top_similarity": max((row["similarity_score"] for row in transformed), default=0.0),
                "near_miss_count": len(transformed),
                "recommendation": _non_sports_category_recommendation(category, query, transformed, row),
                "diagnostic_only": True,
                "same_payoff_asserted": False,
            }
        )
        near_misses.extend(transformed)
    near_misses.sort(key=lambda row: row["similarity_score"], reverse=True)
    near_misses = _dedupe_non_sports_near_misses(near_misses)
    category_summaries = _non_sports_category_summaries(categories)
    status = "OK" if categories else "NO_DATA" if not failures else "FAILED"
    return {
        "schema_version": 1,
        "source": "non_sports_near_miss_diagnostics",
        "status": status,
        "generated_at": generated_at.isoformat(),
        "research_only": True,
        "diagnostic_only": True,
        "live_api_fetch_attempted": False,
        "default_scan_data_source_mode": "STATIC_FIXTURE",
        "sweep_report_path": str(sweep_report),
        "matching_thresholds": {
            "min_similarity": min_similarity,
            "thresholds_changed_by_diagnostics": False,
        },
        "summary": {
            "category_count": len(categories),
            "near_miss_count": len(near_misses),
            "strongest_category": category_summaries[0]["category_query"] if category_summaries else None,
            "category_summaries": category_summaries,
            "failures": failures,
        },
        "near_misses": near_misses[: max(top_limit * max(len(categories), 1), top_limit)],
        "safety": {
            "execution_enabled": False,
            "uses_live_api": False,
            "thresholds_changed": False,
            "same_payoff_asserted": False,
            "readiness_promotion": "none",
            "paper_candidate_emitted": False,
        },
        "disclaimer": (
            "Diagnostics only. Near misses are not same-payoff claims, not paper/live readiness, "
            "not edge evidence, and not trading signals."
        ),
    }


def _selected_non_sports_sweep_rows(sweep_payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = sweep_payload.get("rows")
    if not isinstance(rows, list):
        return []
    selected = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        category = str(row.get("category") or "").lower()
        query = str(row.get("query") or "").lower()
        if category == "sports":
            continue
        if category in {"ai", "crypto", "macro"} or query in _NON_SPORTS_NEAR_MISS_QUERIES:
            selected.append(row)
    return selected


def _non_sports_near_miss_row(
    pair: dict[str, Any],
    *,
    category: str,
    query: str,
    min_similarity: float,
) -> dict[str, Any]:
    poly = pair.get("polymarket") or {}
    kalshi = pair.get("kalshi") or {}
    poly_text = " ".join(str(poly.get(key) or "") for key in ("title_or_question", "event_title"))
    kalshi_text = " ".join(str(kalshi.get(key) or "") for key in ("title_or_question", "event_title"))
    poly_entities = _diagnostic_entities(poly_text)
    kalshi_entities = _diagnostic_entities(kalshi_text)
    poly_numbers = _numeric_tokens(poly_text)
    kalshi_numbers = _numeric_tokens(kalshi_text)
    poly_dates = _diagnostic_dates(poly_text)
    kalshi_dates = _diagnostic_dates(kalshi_text)
    blockers = _non_sports_near_miss_blockers(
        pair=pair,
        poly_entities=poly_entities,
        kalshi_entities=kalshi_entities,
        poly_numbers=poly_numbers,
        kalshi_numbers=kalshi_numbers,
        poly_dates=poly_dates,
        kalshi_dates=kalshi_dates,
        min_similarity=min_similarity,
    )
    return {
        "diagnostic_only": True,
        "same_payoff_asserted": False,
        "source_ids": {"polymarket": "polymarket", "kalshi": "kalshi"},
        "category": category,
        "query": query,
        "similarity_score": pair.get("similarity_score"),
        "failed_current_matcher_reason": "text_similarity_below_threshold"
        if "text_similarity_below_threshold" in blockers
        else ",".join(blockers),
        "blocker_labels": blockers,
        "possible_normalization_needed": _possible_normalization_needed(blockers),
        "recommended_next_step": _near_miss_recommendation(blockers),
        "polymarket": {
            "market_id": poly.get("market_id"),
            "title_or_question": poly.get("title_or_question"),
            "event_title": poly.get("event_title"),
            "entities_detected": sorted(poly_entities),
            "numeric_thresholds_detected": sorted(poly_numbers),
            "dates_deadlines_detected": poly_dates,
            "event_outcome_phrase": _event_outcome_phrase(poly_text),
        },
        "kalshi": {
            "ticker": kalshi.get("ticker"),
            "title_or_question": kalshi.get("title_or_question"),
            "event_title": kalshi.get("event_title"),
            "entities_detected": sorted(kalshi_entities),
            "numeric_thresholds_detected": sorted(kalshi_numbers),
            "dates_deadlines_detected": kalshi_dates,
            "event_outcome_phrase": _event_outcome_phrase(kalshi_text),
        },
        "matched_fields": pair.get("matched_fields") or {},
    }


def _diagnostic_entities(text: str) -> set[str]:
    entity_terms = {
        "openai": ("openai", "chatgpt", "gpt", "gpt5"),
        "bitcoin": ("bitcoin", "btc"),
        "ethereum": ("ethereum", "eth"),
        "fed": ("fed", "fomc", "federal reserve", "federal funds"),
        "cpi": ("cpi", "inflation", "consumer price"),
        "tesla": ("tesla", "tsla"),
        "nvidia": ("nvidia", "nvda"),
        "election": ("election", "president", "presidential"),
    }
    return {
        entity
        for entity, terms in entity_terms.items()
        if any(_diagnostic_term_matches(text, term) for term in terms)
    }


def _diagnostic_term_matches(text: str, term: str) -> bool:
    escaped = re.escape(term)
    if " " in term:
        pattern = rf"(?<![a-z0-9]){escaped}(?![a-z0-9])"
    else:
        pattern = rf"(?<![a-z0-9]){escaped}(?:-\d+(?:\.\d+)?|(?=\b))(?![a-z0-9])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _diagnostic_dates(text: str) -> list[str]:
    patterns = (
        r"\b20\d{2}\b",
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:,\s*20\d{2})?\b",
        r"\bq[1-4]\s*20\d{2}\b",
        r"\bend of\s+20\d{2}\b",
    )
    hits: list[str] = []
    for pattern in patterns:
        hits.extend(match.group(0) for match in re.finditer(pattern, text, flags=re.IGNORECASE))
    return sorted(set(hits))


def _event_outcome_phrase(text: str) -> str:
    cleaned = " ".join(text.split())
    if not cleaned:
        return ""
    match = re.search(r"\bwill\b\s+(.+?)(?:\?|$)", cleaned, flags=re.IGNORECASE)
    phrase = match.group(1) if match else cleaned
    return _truncate(phrase, limit=140)


def _non_sports_near_miss_blockers(
    *,
    pair: dict[str, Any],
    poly_entities: set[str],
    kalshi_entities: set[str],
    poly_numbers: set[str],
    kalshi_numbers: set[str],
    poly_dates: list[str],
    kalshi_dates: list[str],
    min_similarity: float,
) -> list[str]:
    blockers = set()
    if float(pair.get("similarity_score") or 0.0) < min_similarity:
        blockers.add("text_similarity_below_threshold")
    if poly_entities and kalshi_entities and poly_entities.isdisjoint(kalshi_entities):
        blockers.add("entity_mismatch")
    if poly_numbers != kalshi_numbers:
        blockers.add("threshold_mismatch")
    if poly_dates and kalshi_dates and set(poly_dates).isdisjoint(kalshi_dates):
        blockers.add("date_or_deadline_mismatch")
    matched_fields = pair.get("matched_fields") or {}
    if matched_fields.get("settlement_time_warning") not in (None, "", "none"):
        blockers.add("date_or_deadline_mismatch")
    if _outcome_scope_mismatch(pair):
        blockers.add("outcome_scope_mismatch")
    if not poly_entities or not kalshi_entities:
        blockers.add("vague_event_wording")
    return sorted(blockers)


def _outcome_scope_mismatch(pair: dict[str, Any]) -> bool:
    poly_text = str(((pair.get("polymarket") or {}).get("title_or_question")) or "").lower()
    kalshi_text = str(((pair.get("kalshi") or {}).get("title_or_question")) or "").lower()
    scope_terms = (
        ("above", "below", "range"),
        ("release", "launch", "announce"),
        ("win", "winner"),
        ("approval", "rating"),
        ("meeting", "decision", "vote"),
    )
    poly_scopes = {index for index, terms in enumerate(scope_terms) if any(term in poly_text for term in terms)}
    kalshi_scopes = {index for index, terms in enumerate(scope_terms) if any(term in kalshi_text for term in terms)}
    return bool(poly_scopes and kalshi_scopes and poly_scopes.isdisjoint(kalshi_scopes))


def _possible_normalization_needed(blockers: list[str]) -> list[str]:
    needed = []
    if "entity_mismatch" in blockers or "vague_event_wording" in blockers:
        needed.append("entity_extraction")
    if "threshold_mismatch" in blockers:
        needed.append("numeric_threshold_normalization")
    if "date_or_deadline_mismatch" in blockers:
        needed.append("deadline_normalization")
    if "outcome_scope_mismatch" in blockers:
        needed.append("outcome_scope_normalization")
    if "text_similarity_below_threshold" in blockers:
        needed.append("text_normalization_review")
    return needed


def _near_miss_recommendation(blockers: list[str]) -> str:
    if "entity_mismatch" in blockers:
        return "skip_for_now_or_improve_source_targeting"
    if "vague_event_wording" in blockers:
        return "better_source_targeting"
    if "threshold_mismatch" in blockers or "date_or_deadline_mismatch" in blockers:
        return "better_contract_normalization_then_relationship_classifier_review"
    return "relationship_classifier_review"


def _non_sports_category_recommendation(
    category: str,
    query: str,
    transformed: list[dict[str, Any]],
    sweep_row: dict[str, Any],
) -> str:
    if not transformed:
        if int(sweep_row.get("kalshi_retained_count") or 0) == 0 or int(sweep_row.get("polymarket_retained_count") or 0) == 0:
            return "better_source_targeting"
        return "skip_for_now"
    blocker_counts = Counter(blocker for row in transformed for blocker in row["blocker_labels"])
    if blocker_counts.get("entity_mismatch") or blocker_counts.get("vague_event_wording"):
        return "better_source_targeting"
    if blocker_counts.get("threshold_mismatch") or blocker_counts.get("date_or_deadline_mismatch"):
        return "better_contract_normalization"
    if max(row["similarity_score"] for row in transformed) >= 0.58:
        return "relationship_classifier_review"
    return "skip_for_now"


def _non_sports_category_summaries(categories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries = []
    for row in categories:
        summaries.append(
            {
                "category_query": f"{row['category']}:{row['query']}",
                "top_similarity": row["top_similarity"],
                "near_miss_count": row["near_miss_count"],
                "recommendation": row["recommendation"],
            }
        )
    return sorted(summaries, key=lambda row: row["top_similarity"], reverse=True)


def _dedupe_non_sports_near_misses(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in rows:
        key = (
            str(row.get("category") or ""),
            str(row.get("query") or ""),
            str((row.get("polymarket") or {}).get("title_or_question") or ""),
            str((row.get("kalshi") or {}).get("title_or_question") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _non_sports_near_miss_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Non-Sports Near-Miss Diagnostics",
        "",
        "Diagnostic-only review of below-threshold Kalshi/Polymarket comparisons. This report does not assert same payoff, edge, paper readiness, live readiness, or tradability.",
        "",
        f"- Status: `{report['status']}`",
        f"- Categories inspected: `{report['summary']['category_count']}`",
        f"- Near-miss rows: `{report['summary']['near_miss_count']}`",
        f"- Strongest category: `{report['summary']['strongest_category'] or 'none'}`",
        f"- Threshold changed: `{str(report['matching_thresholds']['thresholds_changed_by_diagnostics']).lower()}`",
        "",
        "## Category Summary",
        "",
        "| Category/query | Top similarity | Near misses | Recommendation |",
        "| --- | ---: | ---: | --- |",
    ]
    for row in report["summary"]["category_summaries"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(row["category_query"]),
                    _markdown_cell(row["top_similarity"]),
                    _markdown_cell(row["near_miss_count"]),
                    _markdown_cell(row["recommendation"]),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Top Near Misses", ""])
    if not report["near_misses"]:
        lines.append("- none")
    else:
        lines.extend(
            [
                "| Similarity | Category | Polymarket | Kalshi | Blockers | Recommendation |",
                "| ---: | --- | --- | --- | --- | --- |",
            ]
        )
        for row in report["near_misses"][:20]:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _markdown_cell(row["similarity_score"]),
                        _markdown_cell(f"{row['category']}:{row['query']}"),
                        _markdown_cell(row["polymarket"].get("title_or_question")),
                        _markdown_cell(row["kalshi"].get("title_or_question")),
                        _markdown_cell(",".join(row["blocker_labels"])),
                        _markdown_cell(row["recommended_next_step"]),
                    ]
                )
                + " |"
            )
    lines.append("")
    return "\n".join(lines)


def build_live_matching_diagnostics_report(
    *,
    snapshot_dir: Path,
    min_similarity: float = 0.68,
    top_limit: int = 20,
) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc)
    paths = {
        "kalshi": snapshot_dir / "kalshi_live_readonly_snapshot.json",
        "polymarket": snapshot_dir / "polymarket_live_readonly_snapshot.json",
        "the_odds_api_reference": snapshot_dir / "the_odds_api_reference_snapshot.json",
    }
    kalshi_payload = _load_json_object(paths["kalshi"])
    polymarket_payload = _load_json_object(paths["polymarket"])
    reference_payload = _load_json_object(paths["the_odds_api_reference"])
    validation = _validate_live_readonly_match_inputs(paths["kalshi"], paths["polymarket"])
    validation_errors = validation["errors"]
    kalshi_rows = _normalized_market_rows(kalshi_payload)
    polymarket_rows = _normalized_market_rows(polymarket_payload)
    comparisons = _diagnose_executable_comparisons(
        polymarket_rows=polymarket_rows,
        kalshi_rows=kalshi_rows,
        min_similarity=min_similarity,
        source_validation_errors=validation_errors,
        top_limit=top_limit,
    )
    reference_context = _diagnose_reference_context(
        reference_payload=reference_payload,
        executable_rows={
            "kalshi": kalshi_rows,
            "polymarket": polymarket_rows,
        },
        top_limit=10,
    )
    snapshot_summary = {
        "kalshi": _diagnostic_snapshot_summary("kalshi", paths["kalshi"], kalshi_payload),
        "polymarket": _diagnostic_snapshot_summary("polymarket", paths["polymarket"], polymarket_payload),
        "the_odds_api_reference": _diagnostic_reference_summary(paths["the_odds_api_reference"], reference_payload),
    }
    return {
        "schema_version": 1,
        "source": "live_matching_diagnostics",
        "status": "VALIDATION_FAILED" if validation_errors else "OK",
        "generated_at": generated_at.isoformat(),
        "research_only": True,
        "live_api_fetch_attempted": False,
        "default_scan_data_source_mode": "STATIC_FIXTURE",
        "matching_thresholds": {
            "min_similarity": min_similarity,
            "settlement_bonus_window_seconds": DEFAULT_SETTLEMENT_BONUS_WINDOW_SECONDS,
            "thresholds_changed_by_diagnostics": False,
        },
        "source_snapshot_paths": {key: str(path) for key, path in paths.items()},
        "validation_errors": validation_errors,
        "input_validation": validation["rows"],
        "snapshot_summary": snapshot_summary,
        "comparison_summary": comparisons["summary"],
        "top_rejected_pairs": comparisons["top_rejected_pairs"],
        "reference_context": reference_context,
        "safety": {
            "execution_enabled": False,
            "uses_live_api": False,
            "uses_reference_as_executable_leg": False,
            "readiness_promotion": "none",
            "same_payoff_asserted": False,
        },
        "disclaimer": (
            "Diagnostics only. Rejected rows are not relationship proof, not same-payoff claims, "
            "and not trading or readiness signals."
        ),
    }


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalized_market_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("normalized_markets")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _normalized_reference_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("normalized_records")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _diagnostic_snapshot_summary(source_id: str, path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    rows = _normalized_market_rows(payload)
    return {
        "path": str(path),
        "source_id": payload.get("source_id") or source_id,
        "source_type": _source_type(str(payload.get("source_id") or source_id)),
        "data_source_mode": payload.get("data_source_mode"),
        "captured_at": payload.get("captured_at"),
        "record_count": len(rows),
        "source_categories": _top_value_counts(rows, ["category", "market_type", "event_title"]),
        "title_question_availability": _availability_counts(rows, ["question", "title", "event_title"]),
        "deadline_availability": _availability_counts(rows, ["end_date", "close_time"]),
        "bid_ask_availability": _bid_ask_availability(rows),
        "missing_field_counts": {
            "market_identifier": sum(1 for row in rows if not _has_any(row, ["market_id", "ticker", "condition_id"])),
            "title_or_question": sum(1 for row in rows if not _has_any(row, ["question", "title", "event_title"])),
            "deadline_or_settlement_time": sum(1 for row in rows if not _has_any(row, ["end_date", "close_time"])),
            "bid_or_ask": sum(1 for row in rows if row.get("best_bid") is None or row.get("best_ask") is None),
        },
        "secretish_fields_detected": _payload_has_secretish_key(payload),
    }


def _diagnostic_reference_summary(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    rows = _normalized_reference_rows(payload)
    return {
        "path": str(path),
        "source_id": payload.get("source_id") or "the_odds_api",
        "source_type": payload.get("source_type"),
        "role": "reference_context_only",
        "data_source_mode": payload.get("data_source_mode"),
        "captured_at": payload.get("retrieved_at") or payload.get("captured_at"),
        "record_count": len(rows),
        "market_types": _top_value_counts(rows, ["market_type"]),
        "title_question_availability": _availability_counts(rows, ["event_title", "outcome_name"]),
        "deadline_availability": _availability_counts(rows, ["commence_time"]),
        "price_availability": _availability_counts(rows, ["american_odds", "implied_probability", "no_vig_probability"]),
        "secretish_fields_detected": _payload_has_secretish_key(payload),
    }


def _availability_counts(rows: list[dict[str, Any]], fields: list[str]) -> dict[str, int]:
    return {field: sum(1 for row in rows if row.get(field) not in (None, "", [])) for field in fields}


def _bid_ask_availability(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "both_best_bid_and_best_ask": sum(1 for row in rows if row.get("best_bid") is not None and row.get("best_ask") is not None),
        "best_bid": sum(1 for row in rows if row.get("best_bid") is not None),
        "best_ask": sum(1 for row in rows if row.get("best_ask") is not None),
    }


def _top_value_counts(rows: list[dict[str, Any]], fields: list[str], limit: int = 10) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for row in rows:
        for field in fields:
            value = row.get(field)
            if value in (None, "", []):
                continue
            counts[f"{field}:{str(value)[:80]}"] += 1
    return [{"value": value, "count": count} for value, count in counts.most_common(limit)]


def _diagnose_executable_comparisons(
    *,
    polymarket_rows: list[dict[str, Any]],
    kalshi_rows: list[dict[str, Any]],
    min_similarity: float,
    source_validation_errors: list[str],
    top_limit: int,
) -> dict[str, Any]:
    total = len(polymarket_rows) * len(kalshi_rows)
    rejected_low_text = 0
    rejected_missing_deadline = 0
    rejected_schema = len(source_validation_errors)
    rejected_bid_ask_missing = 0
    near_future_review = 0
    rows: list[dict[str, Any]] = []
    for polymarket in polymarket_rows:
        for kalshi in kalshi_rows:
            diagnostic = _diagnose_pair(polymarket, kalshi, min_similarity, bool(source_validation_errors))
            blockers = diagnostic["blocker_reasons"]
            if "low_title_text_similarity" in blockers:
                rejected_low_text += 1
            if "missing_deadline_or_settlement_fields" in blockers or "unparseable_or_naive_deadline" in blockers:
                rejected_missing_deadline += 1
            if "missing_bid_or_ask" in blockers:
                rejected_bid_ask_missing += 1
            if diagnostic["future_manual_review_hint"]:
                near_future_review += 1
            rows.append(diagnostic)
    rows.sort(key=lambda row: row["similarity_score"], reverse=True)
    return {
        "summary": {
            "raw_cross_source_candidate_comparisons": total,
            "rejected_by_low_title_text_similarity": rejected_low_text,
            "rejected_by_missing_deadline_or_settlement_fields": rejected_missing_deadline,
            "rejected_by_source_schema_validation": rejected_schema,
            "rejected_by_missing_bid_ask_fields": rejected_bid_ask_missing,
            "top_rejected_pair_count": min(top_limit, len(rows)),
            "near_future_manual_review_count": near_future_review,
            "any_pair_close_enough_for_future_manual_review": near_future_review > 0,
        },
        "top_rejected_pairs": rows[:top_limit],
    }


def _diagnose_pair(
    polymarket: dict[str, Any],
    kalshi: dict[str, Any],
    min_similarity: float,
    has_source_validation_errors: bool,
) -> dict[str, Any]:
    poly_question = _market_question(polymarket)
    kalshi_question = _market_question(kalshi)
    poly_event = str(polymarket.get("event_title") or "")
    kalshi_event = str(kalshi.get("event_title") or "")
    question_score = _text_similarity(poly_question, kalshi_question)
    event_score = _text_similarity(poly_event, kalshi_event) if poly_event and kalshi_event else None
    base_similarity = min(question_score, event_score) if event_score is not None else question_score
    settlement_time_delta = _settlement_time_delta_seconds(polymarket, kalshi)
    settlement_time_bonus = _settlement_time_bonus(question_score, settlement_time_delta)
    shared_event_tokens = sorted(_event_keyword_tokens(polymarket) & _event_keyword_tokens(kalshi))
    event_keyword_bonus = _event_keyword_bonus(question_score, shared_event_tokens)
    similarity = min(1.0, base_similarity + settlement_time_bonus + event_keyword_bonus)
    blockers = _diagnostic_pair_blockers(
        polymarket=polymarket,
        kalshi=kalshi,
        similarity=similarity,
        min_similarity=min_similarity,
        has_source_validation_errors=has_source_validation_errors,
    )
    future_review_hint = similarity >= max(0.5, min_similarity - 0.1) and "source_schema_validation" not in blockers
    return {
        "relationship_status": "REJECTED_DIAGNOSTIC",
        "future_manual_review_hint": future_review_hint,
        "similarity_score": round(similarity, 4),
        "blocker_reasons": blockers,
        "polymarket": {
            "market_id": polymarket.get("market_id") or polymarket.get("condition_id"),
            "title_or_question": _truncate(poly_question),
            "event_title": _truncate(poly_event),
            "deadline": polymarket.get("end_date") or polymarket.get("close_time"),
            "best_bid": polymarket.get("best_bid"),
            "best_ask": polymarket.get("best_ask"),
        },
        "kalshi": {
            "ticker": kalshi.get("ticker") or kalshi.get("market_id"),
            "title_or_question": _truncate(kalshi_question),
            "event_title": _truncate(kalshi_event),
            "deadline": kalshi.get("close_time") or kalshi.get("end_date"),
            "best_bid": kalshi.get("best_bid"),
            "best_ask": kalshi.get("best_ask"),
        },
        "matched_fields": {
            "question_similarity": round(question_score, 4),
            "event_title_similarity": None if event_score is None else round(event_score, 4),
            "settlement_time_delta_seconds": settlement_time_delta,
            "settlement_time_bonus": round(settlement_time_bonus, 4),
            "settlement_time_warning": _settlement_time_warning(polymarket, kalshi, settlement_time_delta),
            "shared_event_tokens": shared_event_tokens,
            "event_keyword_bonus": round(event_keyword_bonus, 4),
            "numeric_tokens_match": _numeric_tokens(poly_question) == _numeric_tokens(kalshi_question),
            "final_similarity_score": round(similarity, 4),
        },
    }


def _diagnostic_pair_blockers(
    *,
    polymarket: dict[str, Any],
    kalshi: dict[str, Any],
    similarity: float,
    min_similarity: float,
    has_source_validation_errors: bool,
) -> list[str]:
    blockers: list[str] = []
    if has_source_validation_errors:
        blockers.append("source_schema_validation")
    if similarity < min_similarity:
        blockers.append("low_title_text_similarity")
    if not _has_any(polymarket, ["end_date", "close_time"]) or not _has_any(kalshi, ["close_time", "end_date"]):
        blockers.append("missing_deadline_or_settlement_fields")
    else:
        poly_time = _parse_datetime_or_none(str(polymarket.get("end_date") or polymarket.get("close_time") or ""))
        kalshi_time = _parse_datetime_or_none(str(kalshi.get("close_time") or kalshi.get("end_date") or ""))
        if poly_time is None or kalshi_time is None:
            blockers.append("unparseable_or_naive_deadline")
    if polymarket.get("best_bid") is None or polymarket.get("best_ask") is None or kalshi.get("best_bid") is None or kalshi.get("best_ask") is None:
        blockers.append("missing_bid_or_ask")
    if _numeric_tokens(_market_question(polymarket)) != _numeric_tokens(_market_question(kalshi)):
        blockers.append("numeric_wording_mismatch")
    return sorted(set(blockers))


def _diagnose_reference_context(
    *,
    reference_payload: dict[str, Any],
    executable_rows: dict[str, list[dict[str, Any]]],
    top_limit: int,
) -> dict[str, Any]:
    rows = _normalized_reference_rows(reference_payload)
    related: list[dict[str, Any]] = []
    for reference in rows:
        reference_text = _reference_text(reference)
        for source_id, markets in executable_rows.items():
            for market in markets:
                executable_text = " ".join(
                    str(value or "")
                    for value in (
                        market.get("event_title"),
                        market.get("question"),
                        market.get("title"),
                    )
                )
                score = _text_similarity(reference_text, executable_text)
                if score <= 0:
                    continue
                related.append(
                    {
                        "relationship_status": "REFERENCE_CONTEXT_ONLY",
                        "similarity_score": round(score, 4),
                        "reference_event_id": reference.get("event_id"),
                        "reference_event_title": _truncate(str(reference.get("event_title") or "")),
                        "reference_market_type": reference.get("market_type"),
                        "executable_source": source_id,
                        "executable_id": market.get("ticker") or market.get("market_id") or market.get("condition_id"),
                        "executable_title_or_question": _truncate(_market_question(market)),
                        "notes": "Reference context only; not a candidate leg.",
                    }
                )
    related.sort(key=lambda row: row["similarity_score"], reverse=True)
    return {
        "role": "reference_context_only",
        "source_id": reference_payload.get("source_id") or "the_odds_api",
        "record_count": len(rows),
        "textually_related_count": len(related),
        "top_textually_related": related[:top_limit],
    }


def _reference_text(row: dict[str, Any]) -> str:
    return " ".join(
        str(value or "")
        for value in (
            row.get("event_title"),
            row.get("outcome_name"),
            row.get("market_type"),
        )
    )


def _has_any(row: dict[str, Any], fields: list[str]) -> bool:
    return any(row.get(field) not in (None, "", []) for field in fields)


def _truncate(value: str, limit: int = 160) -> str:
    value = " ".join(str(value or "").split())
    if len(value) <= limit:
        return value
    return f"{value[: limit - 3]}..."


def _live_matching_diagnostics_markdown(report: dict[str, Any]) -> str:
    summary = report["comparison_summary"]
    lines = [
        "# Live Matching Diagnostics",
        "",
        "Saved-file diagnostics only. This report explains rejected Kalshi/Polymarket comparisons without fetching live APIs, asserting equivalence, or promoting readiness.",
        "",
        f"- Status: `{report['status']}`",
        f"- Raw comparisons: `{summary['raw_cross_source_candidate_comparisons']}`",
        f"- Rejected by low text similarity: `{summary['rejected_by_low_title_text_similarity']}`",
        f"- Rejected by missing deadline/settlement fields: `{summary['rejected_by_missing_deadline_or_settlement_fields']}`",
        f"- Rejected by source/schema validation: `{summary['rejected_by_source_schema_validation']}`",
        f"- Near future manual-review hints: `{summary['near_future_manual_review_count']}`",
        "",
        "## Snapshot Summary",
        "",
        "| Source | Records | Captured At | Text | Deadline | Bid/Ask | Missing IDs | Missing Text | Missing Deadline | Missing Bid/Ask |",
        "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for source_id in ("kalshi", "polymarket"):
        row = report["snapshot_summary"][source_id]
        missing = row["missing_field_counts"]
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(source_id),
                    _markdown_cell(row["record_count"]),
                    _markdown_cell(row.get("captured_at") or "n/a"),
                    _markdown_cell(sum(row["title_question_availability"].values())),
                    _markdown_cell(sum(row["deadline_availability"].values())),
                    _markdown_cell(row["bid_ask_availability"]["both_best_bid_and_best_ask"]),
                    _markdown_cell(missing["market_identifier"]),
                    _markdown_cell(missing["title_or_question"]),
                    _markdown_cell(missing["deadline_or_settlement_time"]),
                    _markdown_cell(missing["bid_or_ask"]),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Top Rejected Kalshi/Polymarket Pairs",
            "",
            "| Similarity | Future Review Hint | Polymarket | Kalshi | Blockers |",
            "| ---: | --- | --- | --- | --- |",
        ]
    )
    for row in report["top_rejected_pairs"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(row["similarity_score"]),
                    _markdown_cell(_yes_no(row["future_manual_review_hint"])),
                    _markdown_cell(row["polymarket"]["title_or_question"]),
                    _markdown_cell(row["kalshi"]["title_or_question"]),
                    _markdown_cell(",".join(row["blocker_reasons"]) or "none"),
                ]
            )
            + " |"
        )
    reference = report["reference_context"]
    lines.extend(
        [
            "",
            "## Reference Context",
            "",
            f"- Role: `{reference['role']}`",
            f"- Reference records: `{reference['record_count']}`",
            f"- Textually related observations: `{reference['textually_related_count']}`",
            "",
        ]
    )
    return "\n".join(lines)


def build_live_snapshot_inspection_report(*, snapshot_dir: Path) -> dict[str, Any]:
    rows = [
        _inspect_live_snapshot("kalshi", snapshot_dir / "kalshi_live_readonly_snapshot.json"),
        _inspect_live_snapshot("polymarket", snapshot_dir / "polymarket_live_readonly_snapshot.json"),
        _inspect_live_snapshot("the_odds_api", snapshot_dir / "the_odds_api_reference_snapshot.json"),
    ]
    return {
        "schema_version": 1,
        "source": "live_snapshot_inspection",
        "inspected_at": datetime.now(timezone.utc).isoformat(),
        "default_scan_data_source_mode": "STATIC_FIXTURE",
        "default_scan_live_fetch_attempted": False,
        "rows": rows,
        "summary": {
            "sources_inspected": len(rows),
            "files_found": sum(1 for row in rows if row["file_found"]),
            "safe_rows": sum(1 for row in rows if row["safety_status"] in {"SAFE_FOR_REVIEW", "SAFE_REFERENCE_ONLY"}),
            "match_shape_ready_sources": [row["source_id"] for row in rows if row["match_shape_ready"]],
            "match_ready_sources": [row["source_id"] for row in rows if row["match_ready"]],
        },
    }


def _inspect_live_snapshot(source_id: str, path: Path) -> dict[str, Any]:
    row = _base_inspection_row(source_id, path)
    if not path.exists():
        row["safety_status"] = "NOT_FOUND"
        row["missing_required_fields_for_future_matching"] = ["snapshot_file"]
        row["blockers_before_live_matching"] = ["snapshot_file_not_found"]
        return row
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        row["file_found"] = True
        row["safety_status"] = "INVALID_JSON"
        row["missing_required_fields_for_future_matching"] = ["valid_json"]
        row["blockers_before_live_matching"] = ["snapshot_json_invalid"]
        return row
    if not isinstance(payload, dict):
        row["file_found"] = True
        row["safety_status"] = "INVALID_SHAPE"
        row["missing_required_fields_for_future_matching"] = ["object_payload"]
        row["blockers_before_live_matching"] = ["snapshot_payload_not_object"]
        return row
    if source_id == "the_odds_api":
        return _inspect_reference_snapshot(row, payload)
    return _inspect_executable_snapshot(row, payload)


def _base_inspection_row(source_id: str, path: Path) -> dict[str, Any]:
    entry = SOURCE_REGISTRY.get(source_id)
    source_type = entry.source_type.value if entry else SourceType.DO_NOT_USE_YET.value
    return {
        "file_path": str(path),
        "file_found": False,
        "source_id": source_id,
        "source_type": source_type,
        "data_source_mode": None,
        "captured_at": None,
        "live_fetch_succeeded": False,
        "event_count": None,
        "market_count": None,
        "record_count": 0,
        "normalized_market_identifiers_exist": False,
        "title_or_question_text_exists": False,
        "settlement_or_deadline_fields_exist": False,
        "bid_ask_fields_exist": False,
        "orderbook_depth_fields_exist": False,
        "quote_timestamp_or_freshness_fields_exist": False,
        "fee_fields_exist": False,
        "can_participate_in_candidate_pair": bool(entry and entry.can_create_candidate_pair),
        "can_create_paper_candidate": False,
        "missing_required_fields_for_future_matching": [],
        "blockers_before_live_matching": [],
        "safety_status": "UNKNOWN",
        "match_shape_ready": False,
        "match_ready": False,
        "paper_simulation_ready": False,
        "snapshot_contract": None,
        "reference_fields_present": {},
        "notes": [],
    }


def _inspect_executable_snapshot(row: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    markets = payload.get("normalized_markets")
    market_rows = markets if isinstance(markets, list) else []
    source_id = str(payload.get("source_id") or row["source_id"])
    row.update(
        {
            "file_found": True,
            "source_id": source_id,
            "source_type": _source_type(source_id),
            "data_source_mode": payload.get("data_source_mode"),
            "captured_at": payload.get("captured_at"),
            "live_fetch_succeeded": payload.get("live_fetch_succeeded") is True,
            "event_count": payload.get("event_count"),
            "market_count": payload.get("market_count"),
            "record_count": len(market_rows),
            "snapshot_contract": "normalized_markets_v1_like" if isinstance(markets, list) else "missing_normalized_markets",
        }
    )
    row["normalized_market_identifiers_exist"] = _any_row_has_any(market_rows, ["market_id", "ticker", "condition_id"])
    row["title_or_question_text_exists"] = _any_row_has_any(market_rows, ["event_title", "title", "question"])
    row["settlement_or_deadline_fields_exist"] = _any_row_has_any(market_rows, ["end_date", "close_time"])
    row["bid_ask_fields_exist"] = _any_row_has_any(market_rows, ["best_bid", "best_ask"])
    row["orderbook_depth_fields_exist"] = _any_nested_key(market_rows, "orderbook_enrichment") or _any_row_has_any(
        market_rows,
        ["liquidity_top_contracts", "depth_at_best", "depth_within_1c"],
    )
    row["quote_timestamp_or_freshness_fields_exist"] = bool(payload.get("captured_at")) or _any_row_has_any(
        market_rows,
        ["captured_at", "quote_captured_at", "orderbook_captured_at"],
    )
    row["fee_fields_exist"] = _any_row_has_any(market_rows, ["fee_model", "fees", "fee_cents"])
    row["missing_required_fields_for_future_matching"] = _missing_matching_fields(row)
    row["blockers_before_live_matching"] = _live_matching_blockers(row)
    row["match_shape_ready"] = (
        not row["missing_required_fields_for_future_matching"] and row["source_type"] == SourceType.EXECUTABLE_VENUE.value
    )
    row["match_ready"] = row["match_shape_ready"]
    row["paper_simulation_ready"] = row["match_shape_ready"] and row["orderbook_depth_fields_exist"] and row["fee_fields_exist"]
    row["safety_status"] = "SAFE_FOR_REVIEW" if row["live_fetch_succeeded"] else "FETCH_NOT_CONFIRMED"
    if not row["orderbook_depth_fields_exist"]:
        row["notes"].append("needs saved orderbook enrichment before paper simulation")
    if not row["fee_fields_exist"]:
        row["notes"].append("needs venue fee metadata before paper simulation")
    return row


def _inspect_reference_snapshot(row: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    records = payload.get("normalized_records")
    reference_rows = records if isinstance(records, list) else []
    source_type = str(payload.get("source_type") or row["source_type"])
    row.update(
        {
            "file_found": True,
            "source_id": str(payload.get("source_id") or "the_odds_api"),
            "source_type": source_type,
            "data_source_mode": payload.get("data_source_mode"),
            "captured_at": payload.get("retrieved_at") or payload.get("captured_at"),
            "live_fetch_succeeded": payload.get("live_fetch_succeeded") is True,
            "event_count": payload.get("event_count"),
            "market_count": None,
            "record_count": len(reference_rows),
            "snapshot_contract": payload.get("schema_kind"),
            "can_participate_in_candidate_pair": False,
            "can_create_paper_candidate": False,
        }
    )
    row["normalized_market_identifiers_exist"] = _any_row_has_any(reference_rows, ["event_id"])
    row["title_or_question_text_exists"] = _any_row_has_any(reference_rows, ["event_title", "outcome_name"])
    row["settlement_or_deadline_fields_exist"] = _any_row_has_any(reference_rows, ["commence_time"])
    row["bid_ask_fields_exist"] = _any_row_has_any(reference_rows, ["american_odds", "implied_probability", "no_vig_probability"])
    row["quote_timestamp_or_freshness_fields_exist"] = bool(payload.get("retrieved_at") and payload.get("stale_after"))
    row["reference_fields_present"] = {
        "sportsbook": _any_row_has_any(reference_rows, ["bookmaker", "bookmaker_key"]),
        "market_type": _any_row_has_any(reference_rows, ["market_type"]),
        "odds": _any_row_has_any(reference_rows, ["american_odds", "implied_probability", "no_vig_probability"]),
        "event_time": _any_row_has_any(reference_rows, ["commence_time"]),
        "teams_or_outcomes": _any_row_has_any(reference_rows, ["event_title", "outcome_name"]),
    }
    if payload.get("schema_kind") != "reference_snapshot_v1":
        row["missing_required_fields_for_future_matching"].append("reference_snapshot_v1_schema_kind")
    if source_type != SourceType.REFERENCE_ONLY.value:
        row["missing_required_fields_for_future_matching"].append("reference_only_source_type")
    if not isinstance(records, list):
        row["missing_required_fields_for_future_matching"].append("normalized_records")
    row["blockers_before_live_matching"] = ["reference_only_not_executable", "not_candidate_pair_eligible"]
    row["match_shape_ready"] = False
    row["match_ready"] = False
    row["paper_simulation_ready"] = False
    row["safety_status"] = (
        "SAFE_REFERENCE_ONLY" if not row["missing_required_fields_for_future_matching"] else "REFERENCE_SHAPE_BLOCKED"
    )
    row["notes"].append("reference diagnostics only; sportsbook odds are not executable")
    return row


def _missing_matching_fields(row: dict[str, Any]) -> list[str]:
    checks = [
        ("normalized_market_identifiers", row["normalized_market_identifiers_exist"]),
        ("title_or_question_text", row["title_or_question_text_exists"]),
        ("settlement_or_deadline_fields", row["settlement_or_deadline_fields_exist"]),
    ]
    return [name for name, present in checks if not present]


def _live_matching_blockers(row: dict[str, Any]) -> list[str]:
    blockers = list(row["missing_required_fields_for_future_matching"])
    if row["source_type"] != SourceType.EXECUTABLE_VENUE.value:
        blockers.append("not_executable_venue")
    if not row["live_fetch_succeeded"]:
        blockers.append("live_fetch_not_succeeded")
    if not row["quote_timestamp_or_freshness_fields_exist"]:
        blockers.append("missing_quote_freshness_metadata")
    return blockers


def _any_row_has_any(rows: list[Any], keys: list[str]) -> bool:
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in keys:
            value = row.get(key)
            if value not in (None, "", []):
                return True
    return False


def _any_nested_key(rows: list[Any], key: str) -> bool:
    return any(isinstance(row, dict) and isinstance(row.get(key), dict) for row in rows)


def _live_snapshot_inspection_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Live Snapshot Inspection",
        "",
        "Explicit read-only snapshot shape inspection. This report does not score, match, rank, or promote sources.",
        "Match-shape ready means required saved-snapshot fields are present; it is not paper-simulation readiness.",
        "",
        "| Source | Type | Mode | Found | Records | IDs | Text | Deadline | Bid/Ask | Depth | Freshness | Fees | Pair source | Match-shape ready | Paper-sim ready | Safety | Missing for matching |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for row in report["rows"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(row["source_id"]),
                    _markdown_cell(row["source_type"]),
                    _markdown_cell(row["data_source_mode"] or "n/a"),
                    _markdown_cell(_yes_no(row["file_found"])),
                    _markdown_cell(row["record_count"]),
                    _markdown_cell(_yes_no(row["normalized_market_identifiers_exist"])),
                    _markdown_cell(_yes_no(row["title_or_question_text_exists"])),
                    _markdown_cell(_yes_no(row["settlement_or_deadline_fields_exist"])),
                    _markdown_cell(_yes_no(row["bid_ask_fields_exist"])),
                    _markdown_cell(_yes_no(row["orderbook_depth_fields_exist"])),
                    _markdown_cell(_yes_no(row["quote_timestamp_or_freshness_fields_exist"])),
                    _markdown_cell(_yes_no(row["fee_fields_exist"])),
                    _markdown_cell(_yes_no(row["can_participate_in_candidate_pair"])),
                    _markdown_cell(_yes_no(row["match_shape_ready"])),
                    _markdown_cell(_yes_no(row["paper_simulation_ready"])),
                    _markdown_cell(row["safety_status"]),
                    _markdown_cell(",".join(row["missing_required_fields_for_future_matching"]) or "none"),
                ]
            )
            + " |"
        )
    lines.append("")
    lines.append("## Notes")
    for row in report["rows"]:
        blockers = ",".join(row["blockers_before_live_matching"]) or "none"
        notes = "; ".join(row["notes"]) or "none"
        lines.append(f"- {row['source_id']}: blockers={blockers}; notes={notes}")
    lines.append("")
    return "\n".join(lines)


def _yes_no(value: Any) -> str:
    return "yes" if bool(value) else "no"


def fetch_polymarket(
    limit: int,
    output: Path,
    timeout_seconds: float = 10.0,
    tag_slug: str | None = None,
    tag_id: int | None = None,
    include_closed: bool = False,
    include_not_accepting_orders: bool = False,
    include_past_end_date: bool = False,
) -> int:
    filter_options = PolymarketMarketFilterOptions(
        include_closed=include_closed,
        include_not_accepting_orders=include_not_accepting_orders,
        include_past_end_date=include_past_end_date,
    )
    try:
        snapshot = PolymarketGammaClient(timeout_seconds=timeout_seconds).fetch_market_snapshot(
            limit=limit,
            filter_options=filter_options,
            tag_slug=tag_slug,
            tag_id=tag_id,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"polymarket_fetch_status=FAILED message={exc}")
        return 1

    write_polymarket_market_snapshot(snapshot, output)
    print(
        "polymarket_fetch_status=OK "
        f"events={snapshot['event_count']} markets={snapshot['market_count']} "
        f"normalized={snapshot['normalized_count']} "
        f"skipped_closed={snapshot['skipped_closed_count']} "
        f"skipped_not_accepting_orders={snapshot['skipped_not_accepting_orders_count']} "
        f"skipped_inactive={snapshot['skipped_inactive_count']} "
        f"skipped_archived={snapshot['skipped_archived_count']} "
        f"skipped_past_end_date={snapshot['skipped_past_end_date_count']} "
        f"orderbook_enabled={snapshot['orderbook_enabled_count']} "
        f"(skip counters can overlap) output={output}"
    )
    return 0


def fetch_kalshi(
    limit: int,
    output: Path,
    timeout_seconds: float = 10.0,
    series_ticker: str | None = None,
    event_ticker: str | None = None,
    cursor: str | None = None,
    max_pages: int = 1,
    include_closed: bool = False,
    include_past_close_time: bool = False,
) -> int:
    filter_options = KalshiMarketFilterOptions(
        include_closed=include_closed,
        include_past_close_time=include_past_close_time,
    )
    try:
        snapshot = KalshiReadOnlyClient(timeout_seconds=timeout_seconds).fetch_market_snapshot(
            limit=limit,
            filter_options=filter_options,
            series_ticker=series_ticker,
            event_ticker=event_ticker,
            cursor=cursor,
            max_pages=max_pages,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"kalshi_fetch_status=FAILED message={exc}")
        return 1

    write_kalshi_market_snapshot(snapshot, output)
    print(
        "kalshi_fetch_status=OK "
        f"markets={snapshot['market_count']} "
        f"normalized={snapshot['normalized_count']} "
        f"skipped_closed={snapshot['skipped_closed_count']} "
        f"skipped_inactive={snapshot['skipped_inactive_count']} "
        f"skipped_past_close_time={snapshot['skipped_past_close_time_count']} "
        f"(skip counters can overlap) output={output}"
    )
    return 0


_KALSHI_CRYPTO_SERIES_BY_ASSET: dict[str, tuple[str, ...]] = {
    "BTC": ("KXBTC", "KXBTCD"),
    "ETH": ("KXETH", "ETHD"),
}


def fetch_kalshi_crypto_readonly(
    *,
    assets: str,
    output: Path,
    limit: int,
    max_pages: int,
    timeout_seconds: float,
    include_orderbooks: bool,
    max_orderbooks: int,
) -> int:
    try:
        report = build_kalshi_crypto_readonly_snapshot(
            assets=assets,
            limit=limit,
            max_pages=max_pages,
            timeout_seconds=timeout_seconds,
            include_orderbooks=include_orderbooks,
            max_orderbooks=max_orderbooks,
            output_path=output,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"kalshi_crypto_readonly_fetch_status=FAILED message={_safe_cli_text(exc)}")
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    summary = report["kalshi_crypto_readonly_summary"]
    print(
        "kalshi_crypto_readonly_fetch_status=OK "
        f"markets_fetched={summary['markets_fetched']} "
        f"active_markets={summary['active_markets']} "
        f"future_markets={summary['future_markets']} "
        f"btc_rows={summary['btc_rows']} "
        f"eth_rows={summary['eth_rows']} "
        f"typed_complete_rows={summary['typed_complete_rows']} "
        f"orderbooks_fetched={summary['orderbooks_fetched']} "
        f"orderbooks_enriched={summary['orderbooks_enriched']} "
        f"settled_rows_excluded={summary['settled_rows_excluded']} "
        f"output={output}"
    )
    return 0


def build_kalshi_crypto_readonly_snapshot(
    *,
    assets: str,
    limit: int = 1000,
    max_pages: int = 20,
    timeout_seconds: float = 10.0,
    include_orderbooks: bool = False,
    max_orderbooks: int | None = 200,
    output_path: Path | None = None,
    generated_at: datetime | None = None,
    kalshi_client: Any | None = None,
    kalshi_orderbook_client: Any | None = None,
) -> dict[str, Any]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    if max_pages <= 0:
        raise ValueError("max_pages must be positive")
    if max_orderbooks is not None and max_orderbooks < 0:
        raise ValueError("max_orderbooks must be non-negative")
    captured_at = generated_at or datetime.now(timezone.utc)
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise ValueError("generated_at must include timezone information")
    parsed_assets = _parse_kalshi_crypto_assets(assets)
    series_tickers = _kalshi_crypto_series_for_assets(parsed_assets)
    client = kalshi_client or KalshiReadOnlyClient(
        base_url=os.environ.get("KALSHI_BASE_URL", "https://external-api.kalshi.com/trade-api/v2"),
        timeout_seconds=timeout_seconds,
    )

    snapshots: list[dict[str, Any]] = []
    series_results: list[dict[str, Any]] = []
    skip_counts: Counter[str] = Counter()
    for series in series_tickers:
        try:
            snapshot = client.fetch_market_snapshot(
                limit=limit,
                max_pages=max_pages,
                series_ticker=series,
                filter_options=KalshiMarketFilterOptions(),
            )
        except TypeError:
            # Test doubles and older client-like objects may not accept filter_options.
            snapshot = client.fetch_market_snapshot(limit=limit, max_pages=max_pages, series_ticker=series)
        except Exception as exc:
            series_results.append(
                {
                    "series_ticker": series,
                    "status": "FAILED",
                    "error_category": _error_category(exc),
                    "message": _safe_cli_text(exc),
                    "result_count": 0,
                }
            )
            continue
        series_results.append(
            {
                "series_ticker": series,
                "status": "OK",
                "market_count": int(snapshot.get("market_count") or 0),
                "result_count": int(snapshot.get("normalized_count") or 0),
                "skipped_closed_count": int(snapshot.get("skipped_closed_count") or 0),
                "skipped_inactive_count": int(snapshot.get("skipped_inactive_count") or 0),
                "skipped_past_close_time_count": int(snapshot.get("skipped_past_close_time_count") or 0),
            }
        )
        for key in ("skipped_closed_count", "skipped_inactive_count", "skipped_past_close_time_count"):
            skip_counts[key] += int(snapshot.get(key) or 0)
        snapshots.append(snapshot)

    combined = _combine_overlap_snapshots(snapshots, source_id="kalshi")
    combined["captured_at"] = captured_at.isoformat()
    combined["source"] = "kalshi_markets"
    combined["source_id"] = "kalshi"
    combined["live_fetch_attempted"] = True
    combined["live_fetch_succeeded"] = True
    rows = _normalized_market_rows(combined)
    retained: list[dict[str, Any]] = []
    settled_rows_excluded = skip_counts["skipped_closed_count"] + skip_counts["skipped_past_close_time_count"]
    inactive_rows_excluded = skip_counts["skipped_inactive_count"]
    for row in rows:
        row_copy = json.loads(json.dumps(row))
        if not _kalshi_crypto_row_is_current_future(row_copy, captured_at):
            settled_rows_excluded += 1
            continue
        _attach_kalshi_crypto_typed_fields(row_copy)
        retained.append(row_copy)
    combined["normalized_markets"] = retained
    combined["normalized_count"] = len(retained)
    combined["market_count"] = len(retained)
    combined["event_count"] = len({row.get("event_id") for row in retained if row.get("event_id")})
    combined["raw_response"] = {
        "series_results": series_results,
        "requested_assets": parsed_assets,
        "attempted_series_tickers": series_tickers,
    }
    _attach_live_provenance(combined, source_id="kalshi")
    combined = _redact_secretish_fields(combined)

    orderbook_limit = max_orderbooks if max_orderbooks is not None else None
    if include_orderbooks and retained and orderbook_limit != 0:
        combined = enrich_orderbook_snapshot(
            combined,
            venue="kalshi",
            captured_at=captured_at,
            max_snapshot_age_hours=1.0,
            kalshi_client=kalshi_orderbook_client
            or KalshiOrderbookClient(
                base_url=os.environ.get("KALSHI_BASE_URL", "https://external-api.kalshi.com/trade-api/v2"),
                timeout_seconds=timeout_seconds,
            ),
            polymarket_client=PolymarketOrderbookClient(timeout_seconds=timeout_seconds),
            source_snapshot_path=str(output_path) if output_path is not None else None,
            preserve_raw_orderbook=False,
            max_markets=orderbook_limit,
        )

    summary = _kalshi_crypto_readonly_summary(
        combined,
        requested_assets=parsed_assets,
        attempted_series_tickers=series_tickers,
        series_results=series_results,
        include_orderbooks=include_orderbooks,
        max_orderbooks=orderbook_limit,
        settled_rows_excluded=settled_rows_excluded,
        inactive_rows_excluded=inactive_rows_excluded,
        generated_at=captured_at,
    )
    combined["kalshi_crypto_readonly_summary"] = summary
    combined["kalshi_crypto_readonly"] = {
        "schema_version": 1,
        "source": "kalshi_crypto_readonly_fetch",
        "diagnostic_only": True,
        "research_only": True,
        "requested_assets": parsed_assets,
        "attempted_series_tickers": series_tickers,
        "uses_public_readonly_markets_endpoint": True,
        "uses_public_readonly_orderbook_endpoint": bool(include_orderbooks),
        "orderbook_fetch_cap": orderbook_limit,
        "forbidden_private_endpoints_used": False,
        "default_scan_live_fetch_attempted": False,
    }
    combined["safety"] = {
        "diagnostic_only": True,
        "research_only": True,
        "execution_enabled": False,
        "orders_enabled": False,
        "private_or_auth_endpoints_used": False,
        "account_or_position_endpoints_used": False,
        "same_payoff_asserted": False,
        "paper_candidate_emitted": False,
        "evaluator_gates_changed": False,
    }
    return combined


def _parse_kalshi_crypto_assets(value: str) -> list[str]:
    assets = [item.strip().upper() for item in str(value or "").split(",") if item.strip()]
    if not assets:
        raise ValueError("at least one asset is required")
    unsupported = [asset for asset in assets if asset not in _KALSHI_CRYPTO_SERIES_BY_ASSET]
    if unsupported:
        raise ValueError(f"unsupported crypto asset(s): {','.join(unsupported)}")
    return list(dict.fromkeys(assets))


def _kalshi_crypto_series_for_assets(assets: list[str]) -> list[str]:
    series: list[str] = []
    for asset in assets:
        series.extend(_KALSHI_CRYPTO_SERIES_BY_ASSET[asset])
    return list(dict.fromkeys(series))


def _kalshi_crypto_row_is_current_future(row: dict[str, Any], now: datetime) -> bool:
    if row.get("closed") is True:
        return False
    status = str(row.get("status") or "").strip().lower()
    if status and status not in {"open", "active"}:
        return False
    close_time = _parse_datetime_or_none(row.get("close_time"))
    if close_time is not None and close_time < now:
        return False
    return True


def _attach_kalshi_crypto_typed_fields(row: dict[str, Any]) -> None:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    ticker = row.get("ticker") or row.get("market_id")
    event_ticker = row.get("event_id") or raw.get("event_ticker")
    title = row.get("title") or row.get("question") or raw.get("title")
    rules_text = "\n\n".join(
        str(value)
        for value in (raw.get("rules_primary"), raw.get("rules_secondary"))
        if value is not None and str(value).strip()
    )
    threshold, secondary_threshold = _kalshi_crypto_extract_threshold(ticker=str(ticker) if ticker else None)
    shape, comparator_from_shape = _kalshi_crypto_classify_shape(
        rules_text=rules_text,
        ticker=str(ticker) if ticker else None,
        has_secondary=bool(secondary_threshold),
    )
    comparator = _kalshi_crypto_extract_comparator(
        rules_text=rules_text,
        fallback=comparator_from_shape,
        shape=shape,
    )
    target_date, target_time, timezone_label = _kalshi_crypto_extract_target_datetime(
        rules_text=rules_text,
        close_time_iso=row.get("close_time") or raw.get("close_time"),
        resolution_time_iso=raw.get("expected_expiration_time") or raw.get("expiration_time"),
    )
    settlement_source = _kalshi_crypto_extract_settlement_source(rules_text=rules_text)
    asset = _kalshi_crypto_extract_asset(
        ticker=str(ticker) if ticker else None,
        event_ticker=str(event_ticker) if event_ticker else None,
        title=str(title) if title else None,
    )
    typed_complete = all(
        value not in (None, "")
        for value in (asset, threshold, comparator, target_date, target_time, timezone_label, settlement_source)
    )
    typed_keys = {
        "asset": asset,
        "threshold": threshold,
        "threshold_lower": secondary_threshold,
        "comparator": comparator,
        "target_date": target_date,
        "target_time": target_time,
        "timezone": timezone_label,
        "settlement_source": settlement_source,
        "settlement_source_url": None,
        "market_shape": shape,
        "typed_complete": typed_complete,
        "source": "public_kalshi_markets_rules_text",
    }
    row["kalshi_crypto_typed_keys"] = typed_keys
    for key, value in typed_keys.items():
        if key != "source":
            row[key] = value


def _kalshi_crypto_readonly_summary(
    snapshot: dict[str, Any],
    *,
    requested_assets: list[str],
    attempted_series_tickers: list[str],
    series_results: list[dict[str, Any]],
    include_orderbooks: bool,
    max_orderbooks: int | None,
    settled_rows_excluded: int,
    inactive_rows_excluded: int,
    generated_at: datetime,
) -> dict[str, Any]:
    rows = _normalized_market_rows(snapshot)
    asset_counts: Counter[str] = Counter(str(row.get("asset") or "UNKNOWN").upper() for row in rows)
    active_markets = sum(1 for row in rows if row.get("active") is True or str(row.get("status") or "").lower() in {"open", "active"})
    future_markets = sum(1 for row in rows if _kalshi_crypto_row_is_current_future(row, generated_at))
    typed_complete = sum(1 for row in rows if row.get("typed_complete") is True)
    orderbook_summary = snapshot.get("orderbook_enrichment") if isinstance(snapshot.get("orderbook_enrichment"), dict) else {}
    orderbook_market_rows = int(orderbook_summary.get("market_count") or 0) if include_orderbooks else 0
    orderbook_cap_skips = int(orderbook_summary.get("skipped_due_to_max_markets_count") or 0) if include_orderbooks else 0
    orderbooks_fetched = max(0, orderbook_market_rows - orderbook_cap_skips)
    orderbooks_enriched = int(orderbook_summary.get("enriched_count") or 0) if include_orderbooks else 0
    return {
        "markets_fetched": sum(int(item.get("market_count") or 0) for item in series_results),
        "markets_retained": len(rows),
        "active_markets": active_markets,
        "future_markets": future_markets,
        "btc_rows": asset_counts.get("BTC", 0),
        "eth_rows": asset_counts.get("ETH", 0),
        "typed_complete_rows": typed_complete,
        "orderbooks_requested": bool(include_orderbooks),
        "orderbook_fetch_cap": max_orderbooks,
        "orderbook_market_rows": orderbook_market_rows,
        "orderbooks_fetched": orderbooks_fetched,
        "orderbooks_enriched": orderbooks_enriched,
        "orderbooks_skipped_due_to_cap": orderbook_cap_skips,
        "settled_rows_excluded": settled_rows_excluded,
        "inactive_rows_excluded": inactive_rows_excluded,
        "requested_assets": requested_assets,
        "attempted_series_tickers": attempted_series_tickers,
        "series_results": series_results,
        "exact_ready_rows": 0,
        "paper_candidate_rows": 0,
    }


def fetch_the_odds_api(
    *,
    sport_key: str,
    regions: str,
    markets: str,
    odds_format: str,
    api_key: str | None,
    api_key_env: str,
    timeout_seconds: float,
    stale_after_seconds: int,
    output: Path,
) -> int:
    resolved_api_key = api_key or os.environ.get(api_key_env)
    if not resolved_api_key:
        print(f"the_odds_api_fetch_status=FAILED message=missing API key; pass --api-key or set {api_key_env}")
        return 1
    try:
        snapshot = TheOddsApiReadOnlyClient(
            api_key=resolved_api_key,
            timeout_seconds=timeout_seconds,
        ).fetch_reference_snapshot(
            sport_key=sport_key,
            regions=regions,
            markets=markets,
            odds_format=odds_format,
            stale_after_seconds=stale_after_seconds,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"the_odds_api_fetch_status=FAILED message={exc}")
        return 1

    write_the_odds_api_reference_snapshot(snapshot, output)
    print(
        "the_odds_api_fetch_status=OK "
        f"record_count={snapshot['record_count']} "
        f"normalized={snapshot['normalized_count']} "
        f"skipped={snapshot['skipped_count']} "
        f"output={output}"
    )
    return 0


def fetch_sx_bet_readonly(
    *,
    max_markets: int,
    timeout_seconds: float,
    sport: str | None = None,
    league: str | None = None,
    query: str | None = None,
    label: str | None = None,
    output: Path | None = None,
    output_dir: Path | None = None,
    json_output: Path | None = None,
    coverage_output: Path | None = None,
    client_factory: Any | None = None,
) -> int:
    captured_at = datetime.now(timezone.utc)
    output = output or _sx_bet_public_snapshot_output_path(output_dir=output_dir, label=label, captured_at=captured_at)
    if coverage_output is None and json_output is not None:
        coverage_output = PROJECT_ROOT / "reports" / "sx_bet_normalized_draft_coverage.json"
    client = (client_factory or SXBetReadOnlyClient)(timeout_seconds=timeout_seconds)
    try:
        snapshot = client.fetch_research_snapshot(
            max_markets=max_markets,
            sport=sport,
            league=league,
            query=query,
        )
        if label:
            snapshot["targeting"]["label"] = _safe_pipeline_label(label)
    except SXBetReadOnlyFetchError as exc:
        snapshot = build_sx_bet_failure_snapshot(
            error_category=exc.error_category,
            error_message=str(exc),
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
        print(
            "sx_bet_readonly_fetch_status=FAILED "
            f"error_category={exc.error_category} "
            "is_executable=false "
            "can_create_candidate_pair=false "
            "can_create_paper_candidate=false "
            f"output={output}"
        )
        return 1
    except (RuntimeError, ValueError) as exc:
        snapshot = build_sx_bet_failure_snapshot(
            error_category="READ_ONLY_FETCH_FAILED",
            error_message=str(exc),
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
        print(
            "sx_bet_readonly_fetch_status=FAILED "
            "error_category=READ_ONLY_FETCH_FAILED "
            "is_executable=false "
            "can_create_candidate_pair=false "
            "can_create_paper_candidate=false "
            f"output={output}"
        )
        return 1

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
    normalized_summary_text = ""
    if json_output is not None and coverage_output is not None:
        outputs = write_sx_bet_saved_normalization_files(
            project_root=PROJECT_ROOT,
            input_dir=output.parent,
            json_output=json_output,
            coverage_output=coverage_output,
            include_fixture_dir=False,
        )
        coverage_summary = outputs["coverage"]["summary"]
        normalized_summary_text = (
            f" normalized_json={json_output}"
            f" coverage={coverage_output}"
            f" normalized_rows={coverage_summary['normalized_records']}"
        )
    print(
        "sx_bet_readonly_fetch_status=OK "
        f"schema_kind={snapshot.get('schema_kind')} "
        f"label={snapshot.get('targeting', {}).get('label') or 'none'} "
        f"markets={snapshot.get('market_count')} "
        f"research_markets={snapshot.get('research_market_count')} "
        f"orders={snapshot.get('order_count')} "
        f"targeting_method={snapshot.get('targeting', {}).get('targeting_method', 'none')} "
        f"requested_sport={snapshot.get('targeting', {}).get('requested_sport') or 'none'} "
        f"requested_league={snapshot.get('targeting', {}).get('requested_league') or 'none'} "
        f"requested_query={snapshot.get('targeting', {}).get('requested_query') or 'none'} "
        f"sx_bet_fetched_count={snapshot.get('sx_bet_fetched_count', snapshot.get('market_count'))} "
        f"sx_bet_retained_count={snapshot.get('sx_bet_retained_count', snapshot.get('research_market_count'))} "
        "is_executable=false "
        "can_create_candidate_pair=false "
        "can_create_paper_candidate=false "
        f"output={output}"
        f"{normalized_summary_text}"
    )
    if sport or league or query:
        print(
            "sx_bet_compatible_universe_note="
            "run fetch-live-overlap-universe for Kalshi/Polymarket with the same sport/league/query before compare-sx-bet-reference"
        )
    return 0


def compare_sx_bet_reference(
    *,
    sx_bet_snapshot: Path | None,
    kalshi_snapshot: Path,
    polymarket_snapshot: Path,
    json_output: Path | None,
    markdown_output: Path | None,
    label: str | None = None,
    top_limit: int = 20,
) -> int:
    safe_label = _safe_pipeline_label(label) if label else None
    sx_bet_snapshot = sx_bet_snapshot or _sx_bet_research_snapshot_path(safe_label)
    json_output = json_output or _sx_bet_reference_json_path(safe_label)
    markdown_output = markdown_output or _sx_bet_reference_markdown_path(safe_label)
    report = build_sx_bet_reference_context_report(
        sx_bet_snapshot=sx_bet_snapshot,
        executable_snapshots={
            "kalshi": kalshi_snapshot,
            "polymarket": polymarket_snapshot,
        },
        top_limit=top_limit,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(_sx_bet_reference_context_markdown(report), encoding="utf-8")
    summary = report["summary"]
    print(
        "sx_bet_reference_context_status=OK "
        "live_fetch_attempted=false "
        f"sx_bet_markets={summary['sx_bet_markets_inspected']} "
        f"kalshi_records={summary['kalshi_records_inspected']} "
        f"polymarket_records={summary['polymarket_records_inspected']} "
        f"top_candidates={summary['top_overlap_candidate_count']} "
        f"asymmetric_universe_warning={summary.get('asymmetric_universe_warning') or 'none'} "
        f"label={safe_label or 'none'} "
        f"json={json_output} markdown={markdown_output}"
    )
    return 0


def _sx_bet_research_snapshot_path(label: str | None = None) -> Path:
    if label:
        return PROJECT_ROOT / "reports" / "sx_bet" / _safe_pipeline_label(label) / "sx_bet_research_snapshot.json"
    return PROJECT_ROOT / "reports" / "sx_bet_research_snapshot.json"


def _sx_bet_public_snapshot_output_path(
    *,
    output_dir: Path | None,
    label: str | None,
    captured_at: datetime,
) -> Path:
    if output_dir is None:
        return _sx_bet_research_snapshot_path(label)
    timestamp = captured_at.strftime("%Y%m%d_%H%M%SZ")
    parts = [output_dir, Path(timestamp)]
    if label:
        parts.append(Path(_safe_pipeline_label(label)))
    return Path(*parts) / "sx_bet_research_snapshot.json"


def _sx_bet_reference_json_path(label: str | None = None) -> Path:
    if label:
        return PROJECT_ROOT / "reports" / "sx_bet_reference" / _safe_pipeline_label(label) / "sx_bet_reference_context.json"
    return PROJECT_ROOT / "reports" / "sx_bet_reference_context.json"


def _sx_bet_reference_markdown_path(label: str | None = None) -> Path:
    if label:
        return PROJECT_ROOT / "reports" / "sx_bet_reference" / _safe_pipeline_label(label) / "sx_bet_reference_context.md"
    return PROJECT_ROOT / "reports" / "sx_bet_reference_context.md"


def build_sx_bet_reference_context_report(
    *,
    sx_bet_snapshot: Path,
    executable_snapshots: dict[str, Path],
    top_limit: int = 20,
) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc)
    sx_payload, sx_source_row = _load_sx_bet_reference_snapshot(sx_bet_snapshot)
    executable_rows: dict[str, dict[str, Any]] = {}
    executable_markets: list[dict[str, Any]] = []
    for source_id, path in executable_snapshots.items():
        payload, source_row = _load_executable_snapshot_for_sx_bet_reference(source_id, path)
        executable_rows[source_id] = source_row
        if source_row["status"] == "OK":
            for market in _normalized_market_rows(payload):
                executable_markets.append({"source_id": source_id, "market": market})
    sx_markets = _sx_bet_reference_markets(sx_payload) if sx_source_row["status"] == "OK" else []
    comparison_payload = _sx_bet_reference_comparison_payload(sx_markets, executable_markets, top_limit=top_limit)
    comparisons = comparison_payload["top_overlap_candidates"]
    source_rows = {"sx_bet": sx_source_row, **executable_rows}
    blocker_counts = _counter_rows(reason for row in comparisons for reason in row["blockers"])
    recommendation = _sx_bet_reference_review_recommendation(comparisons)
    asymmetric_warning = _sx_bet_asymmetric_universe_warning(executable_rows)
    warnings = _sx_bet_reference_warnings(asymmetric_warning, executable_rows)
    return {
        "schema_version": 1,
        "source": "sx_bet_reference_context",
        "generated_at": generated_at.isoformat(),
        "diagnostic_only": True,
        "is_reference_only": True,
        "same_payoff_asserted": False,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "readiness_promotion": "none",
        "live_fetch_attempted": False,
        "source_rows": source_rows,
        "summary": {
            "sx_bet_markets_inspected": len(sx_markets),
            "sx_bet_event_title_coverage_ratio": _sx_bet_event_title_coverage_ratio(sx_markets),
            "kalshi_records_inspected": executable_rows.get("kalshi", {}).get("record_count", 0),
            "polymarket_records_inspected": executable_rows.get("polymarket", {}).get("record_count", 0),
            "structured_pairs_considered": comparison_payload["structured_pairs_considered"],
            "structured_pairs_rejected": comparison_payload["structured_pairs_rejected"],
            "sport_or_league_mismatch_rejections": comparison_payload["sport_or_league_mismatch_rejections"],
            "top_overlap_candidate_count": len(comparisons),
            "top_similarity": comparisons[0]["similarity_score"] if comparisons else 0.0,
            "top_similarity_after_structured_filter": comparisons[0]["similarity_score"] if comparisons else 0.0,
            "blockers": blocker_counts,
            "recommendation": recommendation,
            "future_review_recommendation": recommendation,
            "asymmetric_universe_warning": asymmetric_warning,
            "warnings": warnings,
        },
        "top_overlap_candidates": comparisons,
        "structured_rejections_sample": comparison_payload["structured_rejections_sample"],
        "disclaimer": (
            "SX Bet reference comparison is diagnostic only. It does not normalize SX Bet into schema-v1, "
            "does not use SX Bet as an executable leg, and does not assert same payoff."
        ),
    }


def _sx_bet_asymmetric_universe_warning(executable_rows: dict[str, dict[str, Any]]) -> str | None:
    for source_id in ("kalshi", "polymarket"):
        row = executable_rows.get(source_id, {})
        if row.get("status") != "OK" or int(row.get("record_count") or 0) == 0:
            return "ASYMMETRIC_EXECUTABLE_UNIVERSE"
    return None


def _sx_bet_reference_warnings(
    asymmetric_warning: str | None,
    executable_rows: dict[str, dict[str, Any]],
) -> list[str]:
    warnings: list[str] = []
    if asymmetric_warning:
        impacted: list[str] = []
        for source_id in ("kalshi", "polymarket"):
            row = executable_rows.get(source_id, {})
            status = row.get("status") or "MISSING"
            record_count = int(row.get("record_count") or 0)
            if status != "OK" or record_count == 0:
                impacted.append(f"{source_id} status={status} records={record_count}")
        detail = "; ".join(impacted) if impacted else "one or more executable snapshots is unavailable"
        warnings.append(
            f"{asymmetric_warning}: {detail}. Top SX Bet reference candidates may reflect an incomplete executable universe."
        )
    return warnings


def _load_sx_bet_reference_snapshot(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    if not path.exists():
        return {}, _snapshot_status_row("sx_bet", path, "NOT_FOUND", 0, "SX Bet research snapshot not found")
    payload = _load_json_object(path)
    if not payload:
        return {}, _snapshot_status_row("sx_bet", path, "INVALID_JSON", 0, "SX Bet research snapshot is missing or invalid")
    if payload.get("schema_kind") != "sx_bet_research_snapshot_v1":
        return {}, _snapshot_status_row("sx_bet", path, "UNSUPPORTED_SCHEMA_KIND", 0, "Expected sx_bet_research_snapshot_v1")
    rows = _sx_bet_reference_markets(payload)
    return payload, _snapshot_status_row("sx_bet", path, "OK", len(rows), None)


def _load_executable_snapshot_for_sx_bet_reference(source_id: str, path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    if not path.exists():
        return {}, _snapshot_status_row(source_id, path, "NOT_FOUND", 0, f"{source_id} snapshot not found")
    payload = _load_json_object(path)
    if not payload:
        return {}, _snapshot_status_row(source_id, path, "INVALID_JSON", 0, f"{source_id} snapshot is missing or invalid")
    if payload.get("schema_version") != 1 or payload.get("schema_kind") not in (None, "market_snapshot_v1"):
        return {}, _snapshot_status_row(source_id, path, "UNSUPPORTED_SCHEMA", 0, "Expected executable schema-v1 market snapshot")
    rows = _normalized_market_rows(payload)
    if not rows:
        return payload, _snapshot_status_row(source_id, path, "MISSING_NORMALIZED_MARKETS", 0, "No normalized_markets rows")
    return payload, _snapshot_status_row(source_id, path, "OK", len(rows), None)


def _snapshot_status_row(
    source_id: str,
    path: Path,
    status: str,
    record_count: int,
    message: str | None,
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "path": str(path),
        "status": status,
        "record_count": record_count,
        "message": message,
    }


def _sx_bet_reference_markets(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("research_markets")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _sx_bet_reference_comparisons(
    sx_markets: list[dict[str, Any]],
    executable_markets: list[dict[str, Any]],
    *,
    top_limit: int,
) -> list[dict[str, Any]]:
    return _sx_bet_reference_comparison_payload(
        sx_markets,
        executable_markets,
        top_limit=top_limit,
    )["top_overlap_candidates"]


def _sx_bet_reference_comparison_payload(
    sx_markets: list[dict[str, Any]],
    executable_markets: list[dict[str, Any]],
    *,
    top_limit: int,
) -> dict[str, Any]:
    comparisons: list[dict[str, Any]] = []
    structured_rejections: list[dict[str, Any]] = []
    structured_pairs_considered = 0
    sport_or_league_mismatch_rejections = 0
    for sx_market in sx_markets:
        sx_text = _sx_bet_reference_text(sx_market)
        for executable in executable_markets:
            structured_pairs_considered += 1
            source_id = executable["source_id"]
            market = executable["market"]
            structured = _sx_bet_structured_compatibility(sx_market, market)
            if structured["hard_rejected"]:
                if "sport_mismatch" in structured["blockers"] or "league_mismatch" in structured["blockers"]:
                    sport_or_league_mismatch_rejections += 1
                structured_rejections.append(
                    {
                        "diagnostic_only": True,
                        "is_reference_only": True,
                        "same_payoff_asserted": False,
                        "can_create_candidate_pair": False,
                        "can_create_paper_candidate": False,
                        "readiness_promotion": "none",
                        "sx_bet_market": _sx_bet_reference_market_summary(sx_market),
                        "executable_market": _executable_reference_market_summary(source_id, market),
                        "structured_compatibility": structured,
                        "blockers": [
                            "sx_bet_reference_only",
                            "same_payoff_not_asserted",
                            *structured["blockers"],
                        ],
                    }
                )
                continue
            executable_text = _executable_reference_text(market)
            raw_score = _text_similarity(sx_text, executable_text)
            score = round(raw_score, 6)
            blockers = [
                "sx_bet_reference_only",
                "same_payoff_not_asserted",
                "depth_units_not_normalized",
                "fee_model_not_reviewed",
                "settlement_wording_not_normalized",
                "venue_restrictions_not_reviewed",
                "not_candidate_pair_eligible",
            ]
            blockers.extend(structured["blockers"])
            comparisons.append(
                {
                    "diagnostic_only": True,
                    "is_reference_only": True,
                    "same_payoff_asserted": False,
                    "can_create_candidate_pair": False,
                    "can_create_paper_candidate": False,
                    "readiness_promotion": "none",
                    "similarity_score": score,
                    "raw_similarity_score": round(raw_score, 6),
                    "structured_compatibility": structured,
                    "sport_league_compatibility": structured["sport_league_compatibility"],
                    "sx_bet_market": _sx_bet_reference_market_summary(sx_market),
                    "executable_market": _executable_reference_market_summary(source_id, market),
                    "sx_bet_research_orderbook": _sx_bet_reference_orderbook(sx_market),
                    "comparison_fields_used": [
                        "event_title",
                        "market_title",
                        "start_or_close_time",
                        "outcome_names",
                        "market_type",
                        "sport",
                        "league",
                    ],
                    "blockers": blockers,
                }
            )
    comparisons.sort(key=lambda row: row["similarity_score"], reverse=True)
    top = _dedupe_sx_bet_reference_comparisons(comparisons, top_limit=top_limit)
    return {
        "top_overlap_candidates": top,
        "structured_rejections_sample": structured_rejections[:20],
        "structured_pairs_considered": structured_pairs_considered,
        "structured_pairs_rejected": len(structured_rejections),
        "sport_or_league_mismatch_rejections": sport_or_league_mismatch_rejections,
    }


def _dedupe_sx_bet_reference_comparisons(comparisons: list[dict[str, Any]], *, top_limit: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_market_hashes: set[str] = set()
    for row in comparisons:
        market_hash = str(row.get("sx_bet_market", {}).get("market_hash") or "")
        if market_hash and market_hash in seen_market_hashes:
            continue
        if market_hash:
            seen_market_hashes.add(market_hash)
        selected.append(row)
        if len(selected) >= max(top_limit, 0):
            break
    return selected


def _sx_bet_event_title_coverage_ratio(sx_markets: list[dict[str, Any]]) -> float:
    if not sx_markets:
        return 0.0
    covered = sum(1 for market in sx_markets if str(market.get("event_title") or "").strip())
    return round(covered / len(sx_markets), 6)


def _sx_bet_structured_compatibility(sx_market: dict[str, Any], executable_market: dict[str, Any]) -> dict[str, Any]:
    sx_sport_key = _sx_bet_sport_league_key(sx_market)
    executable_sport_key = _executable_sport_league_key(executable_market)
    sx_market_type = _sx_bet_market_type_key(sx_market)
    executable_market_type = _executable_market_type_key(executable_market)
    sx_start = _parse_datetime_or_none(str(sx_market.get("starts_at") or ""))
    executable_time = _parse_datetime_or_none(
        str(executable_market.get("close_time") or executable_market.get("end_date") or "")
    )
    blockers: list[str] = []
    missing_fields: list[str] = []
    if not sx_sport_key or not executable_sport_key:
        missing_fields.append("sport_or_league")
    elif sx_sport_key != executable_sport_key:
        blockers.append("sport_mismatch")
        blockers.append("league_mismatch")
    if not sx_market_type or not executable_market_type:
        missing_fields.append("market_type")
    elif sx_market_type != executable_market_type:
        blockers.append("market_type_mismatch")
    if sx_start and executable_time:
        delta_seconds = abs((sx_start - executable_time).total_seconds())
        if delta_seconds > 6 * 60 * 60:
            blockers.append("start_time_mismatch")
    elif sx_market.get("starts_at") or executable_market.get("close_time") or executable_market.get("end_date"):
        missing_fields.append("start_time")
    scope_blocker = _sx_bet_outcome_scope_blocker(sx_market, executable_market)
    if scope_blocker:
        blockers.append(scope_blocker)
    if missing_fields:
        blockers.append("structured_fields_missing")
    hard_rejected = any(
        blocker in blockers
        for blocker in (
            "sport_mismatch",
            "league_mismatch",
            "team_mismatch",
            "start_time_mismatch",
            "market_type_mismatch",
            "outcome_scope_mismatch",
        )
    )
    return {
        "hard_rejected": hard_rejected,
        "blockers": blockers,
        "missing_fields": missing_fields,
        "sport_league_compatibility": {
            "sx_bet_sport_league_key": sx_sport_key,
            "executable_sport_league_key": executable_sport_key,
            "compatible": bool(sx_sport_key and executable_sport_key and sx_sport_key == executable_sport_key),
        },
        "market_type_compatibility": {
            "sx_bet_market_type_key": sx_market_type,
            "executable_market_type_key": executable_market_type,
            "compatible": bool(sx_market_type and executable_market_type and sx_market_type == executable_market_type),
        },
    }


_SX_BET_SPORT_LEAGUE_TERMS: dict[str, tuple[str, ...]] = {
    "nfl": ("nfl", "football", "jaguars", "browns", "chiefs", "eagles", "cowboys", "ravens", "bills", "packers"),
    "mlb": ("mlb", "baseball", "guardians", "phillies", "dodgers", "yankees", "mets", "cubs", "brewers", "astros", "tigers", "orioles"),
    "nba": ("nba", "basketball", "celtics", "knicks", "lakers", "warriors", "bucks", "nuggets"),
    "nhl": ("nhl", "hockey", "stanley cup", "rangers", "bruins", "maple leafs", "oilers"),
    "soccer": ("soccer", "football club", "premier league", "champions league", "world cup"),
}


_SX_BET_MARKET_TYPE_TERMS: dict[str, tuple[str, ...]] = {
    "moneyline": ("moneyline", "winner", " win ", " beat ", "championship"),
    "spread": ("spread", " -", " +"),
    "total": ("o/u", "over/under", " over ", " under ", " total "),
}


def _sx_bet_sport_league_key(market: dict[str, Any]) -> str | None:
    text = " ".join(
        str(value)
        for value in (
            market.get("sport"),
            market.get("league"),
            market.get("event_title"),
            market.get("outcome_one_name"),
            market.get("outcome_two_name"),
        )
        if value is not None
    )
    return _sport_league_key_from_text(text)


def _sx_bet_market_type_key(market: dict[str, Any]) -> str | None:
    text = " ".join(
        str(value)
        for value in (
            market.get("market_type"),
            market.get("event_title"),
            market.get("outcome_one_name"),
            market.get("outcome_two_name"),
        )
        if value is not None
    )
    return _market_type_key_from_text(text)


def _executable_market_type_key(market: dict[str, Any]) -> str | None:
    text = _executable_reference_text(market)
    raw = market.get("raw")
    if isinstance(raw, dict):
        text += " " + " ".join(
            str(raw.get(key) or "")
            for key in ("marketType", "type", "slug", "description", "title", "question", "rules_primary")
        )
    return _market_type_key_from_text(text)


def _market_type_key_from_text(text: str) -> str | None:
    normalized = f" {text.lower()} "
    for key, terms in _SX_BET_MARKET_TYPE_TERMS.items():
        if any(term in normalized for term in terms):
            return key
    return None


def _sx_bet_outcome_scope_blocker(sx_market: dict[str, Any], executable_market: dict[str, Any]) -> str | None:
    text = f"{_sx_bet_reference_text(sx_market)} {_executable_reference_text(executable_market)}".lower()
    has_world_series = "world series" in text or "pro baseball championship" in text
    has_lcs = any(term in text for term in ("alcs", "nlcs", "league championship series"))
    if has_world_series and has_lcs:
        return "outcome_scope_mismatch"
    has_btc_price = "bitcoin" in text or "btc" in text
    has_company_btc = any(term in text for term in ("sell bitcoin", "buys bitcoin", "sells btc", "treasury bitcoin"))
    if has_btc_price and has_company_btc:
        return "outcome_scope_mismatch"
    return None


def _executable_sport_league_key(market: dict[str, Any]) -> str | None:
    text = _executable_reference_text(market)
    raw = market.get("raw")
    if isinstance(raw, dict):
        text += " " + " ".join(
            str(raw.get(key) or "")
            for key in ("event_slug", "slug", "description", "sports_event_ticker", "ticker", "title", "rules_primary")
        )
    diagnostics = market.get("overlap_filter_diagnostics")
    if isinstance(diagnostics, dict):
        terms = diagnostics.get("query_hit_terms")
        if isinstance(terms, list):
            text += " " + " ".join(str(term) for term in terms)
    return _sport_league_key_from_text(text)


def _sport_league_key_from_text(text: str) -> str | None:
    normalized = text.lower()
    for key, terms in _SX_BET_SPORT_LEAGUE_TERMS.items():
        if any(term in normalized for term in terms):
            return key
    return None


def _sx_bet_reference_text(market: dict[str, Any]) -> str:
    pieces = [
        market.get("event_title"),
        market.get("league"),
        market.get("sport"),
        market.get("market_type"),
        market.get("outcome_one_name"),
        market.get("outcome_two_name"),
        market.get("starts_at"),
    ]
    return " ".join(str(piece) for piece in pieces if piece is not None)


def _executable_reference_text(market: dict[str, Any]) -> str:
    pieces = [
        market.get("event_title"),
        market.get("title"),
        market.get("question"),
        market.get("ticker"),
        market.get("end_date"),
        market.get("close_time"),
    ]
    outcomes = market.get("outcomes")
    if isinstance(outcomes, list):
        for outcome in outcomes:
            if isinstance(outcome, dict):
                pieces.append(outcome.get("name"))
            else:
                pieces.append(outcome)
    return " ".join(str(piece) for piece in pieces if piece is not None)


def _sx_bet_reference_market_summary(market: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": "sx_bet",
        "market_hash": market.get("market_hash"),
        "event_title": market.get("event_title"),
        "league": market.get("league"),
        "sport": market.get("sport"),
        "market_type": market.get("market_type"),
        "starts_at": market.get("starts_at"),
        "outcome_one_name": market.get("outcome_one_name"),
        "outcome_two_name": market.get("outcome_two_name"),
        "is_reference_only": True,
        "same_payoff_asserted": False,
    }


def _executable_reference_market_summary(source_id: str, market: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "market_id": market.get("market_id"),
        "ticker": market.get("ticker"),
        "question": market.get("question"),
        "event_title": market.get("event_title"),
        "title": market.get("title"),
        "close_time": market.get("close_time"),
        "end_date": market.get("end_date"),
    }


def _sx_bet_reference_orderbook(market: dict[str, Any]) -> dict[str, Any]:
    orderbook = market.get("research_orderbook")
    if not isinstance(orderbook, dict):
        return {"depth_units_not_normalized": True, "available": False}
    return {
        "available": True,
        "depth_units_not_normalized": True,
        "unit_warning": orderbook.get("unit_warning"),
        "best_taker_price_outcome_one": orderbook.get("best_taker_price_outcome_one"),
        "best_taker_price_outcome_two": orderbook.get("best_taker_price_outcome_two"),
        "maker_stake_usdc_at_best_outcome_one": orderbook.get("depth_usdc_at_best_outcome_one"),
        "maker_stake_usdc_at_best_outcome_two": orderbook.get("depth_usdc_at_best_outcome_two"),
    }


def _counter_rows(values: Any) -> list[dict[str, Any]]:
    counts = Counter(value for value in values if value)
    return [{"reason": reason, "count": count} for reason, count in counts.most_common()]


def _sx_bet_reference_review_recommendation(comparisons: list[dict[str, Any]]) -> str:
    if not comparisons:
        return "no_overlap_visible_from_saved_files"
    top_score = comparisons[0]["similarity_score"]
    if top_score >= 0.6:
        return "overlap_worth_future_fee_depth_settlement_review"
    if top_score >= 0.35:
        return "weak_overlap_review_targeting_before_normalization"
    return "low_overlap_skip_normalization_for_now"


def _sx_bet_reference_context_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# SX Bet Reference Context",
        "",
        "Diagnostic-only comparison of saved SX Bet research rows against saved Kalshi/Polymarket snapshots.",
        "",
        "- Live fetch attempted by this command: `false`",
        "- SX Bet role: `reference_context_only`",
        "- Same payoff asserted: `false`",
        "- Readiness promotion: `none`",
        f"- SX Bet markets inspected: `{summary['sx_bet_markets_inspected']}`",
        f"- SX Bet event title coverage: `{summary['sx_bet_event_title_coverage_ratio']}`",
        f"- Kalshi records inspected: `{summary['kalshi_records_inspected']}`",
        f"- Polymarket records inspected: `{summary['polymarket_records_inspected']}`",
        f"- Structured pairs considered: `{summary['structured_pairs_considered']}`",
        f"- Structured pairs rejected: `{summary['structured_pairs_rejected']}`",
        f"- Sport/league mismatch rejections: `{summary['sport_or_league_mismatch_rejections']}`",
        f"- Top similarity: `{summary['top_similarity']}`",
        f"- Top similarity after structured filter: `{summary['top_similarity_after_structured_filter']}`",
        f"- Future review recommendation: `{summary['future_review_recommendation']}`",
        "",
        "`weak_overlap_review_targeting_before_normalization` means the saved files show some weak textual overlap, but the next step is tighter sport/league/start-time targeting before any fee, depth, settlement, or normalization review.",
        "",
    ]
    if summary.get("asymmetric_universe_warning"):
        lines.extend(
            [
                "## Warnings",
                "",
                f"- `{summary['asymmetric_universe_warning']}`",
            ]
        )
        for warning in summary.get("warnings", []):
            lines.append(f"- {warning}")
        lines.append("")
    lines.extend(
        [
            "## Sources",
            "",
            "| Source | Status | Records | Path | Message |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in report["source_rows"].values():
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(row["source_id"]),
                    _markdown_cell(row["status"]),
                    _markdown_cell(row["record_count"]),
                    _markdown_cell(row["path"]),
                    _markdown_cell(row.get("message")),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Top Overlap Candidates",
            "",
            "| Similarity | SX Bet event | Executable source | Executable title | Blockers |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in report["top_overlap_candidates"]:
        executable = row["executable_market"]
        title = executable.get("question") or executable.get("title") or executable.get("event_title")
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(row["similarity_score"]),
                    _markdown_cell(row["sx_bet_market"].get("event_title")),
                    _markdown_cell(executable.get("source_id")),
                    _markdown_cell(title),
                    _markdown_cell(", ".join(row["blockers"])),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def _smoke_kalshi(*, max_markets: int, timeout_seconds: float) -> dict[str, Any]:
    row = _base_smoke_row("kalshi", expected_env_vars=[])
    row["live_fetch_implemented"] = True
    row["live_fetch_attempted"] = True
    try:
        snapshot = KalshiReadOnlyClient(
            base_url=os.environ.get("KALSHI_BASE_URL", "https://external-api.kalshi.com/trade-api/v2"),
            timeout_seconds=timeout_seconds,
        ).fetch_market_snapshot(limit=max_markets)
    except Exception as exc:
        return _failed_smoke_row(row, exc)
    row["live_fetch_succeeded"] = True
    row["result_count"] = snapshot.get("normalized_count")
    row["error_category"] = None
    row["next_required_step"] = (
        "Save reviewed snapshots, enrich depth, and keep settlement/freshness/fee gates before any paper review."
    )
    return row


def _smoke_polymarket(*, max_markets: int, timeout_seconds: float) -> dict[str, Any]:
    row = _base_smoke_row("polymarket", expected_env_vars=[])
    row["live_fetch_implemented"] = True
    row["live_fetch_attempted"] = True
    try:
        snapshot = PolymarketGammaClient(
            base_url=os.environ.get("POLYMARKET_GAMMA_BASE_URL", "https://gamma-api.polymarket.com"),
            timeout_seconds=timeout_seconds,
        ).fetch_market_snapshot(limit=max_markets)
    except Exception as exc:
        return _failed_smoke_row(row, exc)
    row["live_fetch_succeeded"] = True
    row["result_count"] = snapshot.get("normalized_count")
    row["error_category"] = None
    row["next_required_step"] = (
        "Save reviewed snapshots, enrich public orderbooks, and keep wallet/signing/execution out of scope."
    )
    return row


def _smoke_the_odds_api(*, sport_key: str, timeout_seconds: float) -> dict[str, Any]:
    row = _base_smoke_row("the_odds_api", expected_env_vars=["THE_ODDS_API_KEY"])
    row["live_fetch_implemented"] = True
    api_key = os.environ.get("THE_ODDS_API_KEY")
    if not api_key:
        row["error_category"] = "MISSING_ENV"
        row["next_required_step"] = (
            "Set THE_ODDS_API_KEY locally for explicit reference-only odds fetches; never commit or print it."
        )
        return row
    row["live_fetch_attempted"] = True
    try:
        snapshot = TheOddsApiReadOnlyClient(
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        ).fetch_reference_snapshot(
            sport_key=sport_key,
            regions=os.environ.get("THE_ODDS_API_REGION", "us"),
            markets=os.environ.get("THE_ODDS_API_MARKETS", "h2h,spreads,totals"),
            odds_format=os.environ.get("THE_ODDS_API_ODDS_FORMAT", "american"),
        )
    except Exception as exc:
        return _failed_smoke_row(row, exc)
    row["live_fetch_succeeded"] = True
    row["result_count"] = snapshot.get("normalized_count")
    row["error_category"] = None
    row["next_required_step"] = "Keep The Odds API outputs REFERENCE_ONLY diagnostics; never candidate legs."
    return row


def _not_implemented_smoke_rows() -> list[dict[str, Any]]:
    expected_env_by_source = {
        "sx_bet": ["SX_BET_BASE_URL"],
        "forecastex_ibkr": ["IBKR_HOST", "IBKR_PORT", "IBKR_CLIENT_ID", "IBKR_ACCOUNT_ID"],
        "prophetx": ["PROPHETX_BASE_URL", "PROPHETX_API_KEY"],
        "crypto_com": ["CRYPTO_COM_BASE_URL", "CRYPTO_COM_API_KEY"],
        "robinhood": ["ROBINHOOD_ENABLED"],
    }
    source_types = {
        "crypto_com": SourceType.DO_NOT_USE_YET.value,
        "robinhood": SourceType.DO_NOT_USE_YET.value,
    }
    next_steps = {
        "sx_bet": "Build a separately reviewed public read-only fetcher; no wallet, signing, or execution.",
        "forecastex_ibkr": "Resolve account/API permission and read-only boundaries before any adapter work.",
        "prophetx": "Complete manual API-permission review, then design fixture-backed market/depth/settlement/fee schema before adapter work.",
        "crypto_com": "Do product, settlement, and read-only API review before adding any adapter.",
        "robinhood": "Do API-permission review; do not use browser automation, sessions, or credentials.",
    }
    rows: list[dict[str, Any]] = []
    for source_id, expected_env_vars in expected_env_by_source.items():
        row = _base_smoke_row(source_id, expected_env_vars=expected_env_vars)
        if source_id in source_types:
            row["source_type"] = source_types[source_id]
            row["implementation_status"] = ImplementationStatus.PLANNED_NOT_IMPLEMENTED.value
        row["error_category"] = "LIVE_FETCH_NOT_IMPLEMENTED"
        row["next_required_step"] = next_steps[source_id]
        rows.append(row)
    return rows


def _base_smoke_row(source_id: str, *, expected_env_vars: list[str]) -> dict[str, Any]:
    entry = SOURCE_REGISTRY.get(source_id)
    source_type = entry.source_type.value if entry else SourceType.DO_NOT_USE_YET.value
    implementation_status = (
        entry.implementation_status.value if entry else ImplementationStatus.PLANNED_NOT_IMPLEMENTED.value
    )
    env_configured = all(bool(os.environ.get(name)) for name in expected_env_vars) if expected_env_vars else True
    return {
        "source_id": source_id,
        "source_type": source_type,
        "implementation_status": implementation_status,
        "expected_env_vars": expected_env_vars,
        "env_configured": env_configured,
        "live_fetch_implemented": implementation_status == ImplementationStatus.IMPLEMENTED_READ_ONLY.value,
        "live_fetch_attempted": False,
        "live_fetch_succeeded": False,
        "result_count": None,
        "error_category": None,
        "used_for_default_scan": False,
        "can_participate_in_candidate_pair": bool(entry and entry.can_create_candidate_pair),
        "can_create_paper_candidate": False,
        "next_required_step": "",
    }


def _failed_smoke_row(row: dict[str, Any], exc: Exception) -> dict[str, Any]:
    row["live_fetch_succeeded"] = False
    row["result_count"] = None
    row["error_category"] = _error_category(exc)
    row["next_required_step"] = "Inspect network/API availability and retry explicit read-only smoke; no default scan change."
    return row


def _format_source_smoke_row(row: dict[str, Any]) -> str:
    expected_env_vars = ",".join(row["expected_env_vars"]) if row["expected_env_vars"] else "none"
    return (
        "source_smoke_row "
        f"source_id={row['source_id']} "
        f"source_type={row['source_type']} "
        f"implementation_status={row['implementation_status']} "
        f"expected_env_vars={expected_env_vars} "
        f"env_configured={str(row['env_configured']).lower()} "
        f"live_fetch_implemented={str(row['live_fetch_implemented']).lower()} "
        f"live_fetch_attempted={str(row['live_fetch_attempted']).lower()} "
        f"live_fetch_succeeded={str(row['live_fetch_succeeded']).lower()} "
        f"result_count={_display_smoke_value(row['result_count'])} "
        f"error_category={row['error_category'] or 'none'} "
        f"used_for_default_scan={str(row['used_for_default_scan']).lower()} "
        f"can_participate_in_candidate_pair={str(row['can_participate_in_candidate_pair']).lower()} "
        f"can_create_paper_candidate={str(row['can_create_paper_candidate']).lower()} "
        f"next_required_step={_safe_cli_text(row['next_required_step'])}"
    )


def _fetch_live_readonly_source(
    *,
    source_id: str,
    max_markets: int,
    timeout_seconds: float,
    the_odds_api_sport_key: str,
    output_dir: Path,
) -> dict[str, Any]:
    if source_id == "kalshi":
        return _fetch_live_kalshi_snapshot(max_markets=max_markets, timeout_seconds=timeout_seconds, output_dir=output_dir)
    if source_id == "polymarket":
        return _fetch_live_polymarket_snapshot(
            max_markets=max_markets,
            timeout_seconds=timeout_seconds,
            output_dir=output_dir,
        )
    if source_id == "the_odds_api":
        return _fetch_live_the_odds_api_snapshot(
            sport_key=the_odds_api_sport_key,
            timeout_seconds=timeout_seconds,
            output_dir=output_dir,
        )
    row = _base_live_fetch_row(source_id)
    row["error_category"] = "LIVE_FETCH_NOT_IMPLEMENTED"
    row["next_required_step"] = "No reviewed live read-only fetcher exists for this source."
    _write_failure_snapshot(row, output_dir)
    return row


def _fetch_live_kalshi_snapshot(*, max_markets: int, timeout_seconds: float, output_dir: Path) -> dict[str, Any]:
    row = _base_live_fetch_row("kalshi")
    row["live_fetch_implemented"] = True
    row["live_fetch_attempted"] = True
    path = output_dir / "kalshi_live_readonly_snapshot.json"
    try:
        snapshot = KalshiReadOnlyClient(
            base_url=os.environ.get("KALSHI_BASE_URL", "https://external-api.kalshi.com/trade-api/v2"),
            timeout_seconds=timeout_seconds,
        ).fetch_market_snapshot(limit=max_markets)
        _attach_live_provenance(snapshot, source_id="kalshi")
        write_kalshi_market_snapshot(snapshot, path)
    except Exception as exc:
        return _failed_live_fetch_row(row, exc, output_dir)
    row["live_fetch_succeeded"] = True
    row["result_count"] = snapshot.get("normalized_count")
    row["snapshot_path"] = str(path)
    row["error_category"] = None
    return row


def _fetch_live_polymarket_snapshot(*, max_markets: int, timeout_seconds: float, output_dir: Path) -> dict[str, Any]:
    row = _base_live_fetch_row("polymarket")
    row["live_fetch_implemented"] = True
    row["live_fetch_attempted"] = True
    path = output_dir / "polymarket_live_readonly_snapshot.json"
    try:
        snapshot = PolymarketGammaClient(
            base_url=os.environ.get("POLYMARKET_GAMMA_BASE_URL", "https://gamma-api.polymarket.com"),
            timeout_seconds=timeout_seconds,
        ).fetch_market_snapshot(limit=max_markets)
        _attach_live_provenance(snapshot, source_id="polymarket")
        write_polymarket_market_snapshot(snapshot, path)
    except Exception as exc:
        return _failed_live_fetch_row(row, exc, output_dir)
    row["live_fetch_succeeded"] = True
    row["result_count"] = snapshot.get("normalized_count")
    row["snapshot_path"] = str(path)
    row["error_category"] = None
    return row


def _fetch_live_the_odds_api_snapshot(*, sport_key: str, timeout_seconds: float, output_dir: Path) -> dict[str, Any]:
    row = _base_live_fetch_row("the_odds_api")
    row["live_fetch_implemented"] = True
    api_key = os.environ.get("THE_ODDS_API_KEY")
    if not api_key:
        row["error_category"] = "MISSING_ENV"
        row["next_required_step"] = "Set THE_ODDS_API_KEY locally for explicit reference-only snapshots."
        _write_failure_snapshot(row, output_dir)
        return row
    row["live_fetch_attempted"] = True
    path = output_dir / "the_odds_api_reference_snapshot.json"
    try:
        snapshot = TheOddsApiReadOnlyClient(
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        ).fetch_reference_snapshot(
            sport_key=sport_key,
            regions=os.environ.get("THE_ODDS_API_REGION", "us"),
            markets=os.environ.get("THE_ODDS_API_MARKETS", "h2h,spreads,totals"),
            odds_format=os.environ.get("THE_ODDS_API_ODDS_FORMAT", "american"),
        )
        _attach_live_provenance(snapshot, source_id="the_odds_api")
        write_the_odds_api_reference_snapshot(snapshot, path)
    except Exception as exc:
        return _failed_live_fetch_row(row, exc, output_dir)
    row["live_fetch_succeeded"] = True
    row["result_count"] = snapshot.get("normalized_count")
    row["snapshot_path"] = str(path)
    row["error_category"] = None
    return row


def _base_live_fetch_row(source_id: str) -> dict[str, Any]:
    row = _base_smoke_row(source_id, expected_env_vars=["THE_ODDS_API_KEY"] if source_id == "the_odds_api" else [])
    row["snapshot_path"] = None
    return row


def _failed_live_fetch_row(row: dict[str, Any], exc: Exception, output_dir: Path) -> dict[str, Any]:
    row["live_fetch_succeeded"] = False
    row["result_count"] = None
    row["error_category"] = _error_category(exc)
    row["next_required_step"] = "Inspect source availability and retry the explicit read-only fetch."
    _write_failure_snapshot(row, output_dir)
    return row


def _write_failure_snapshot(row: dict[str, Any], output_dir: Path) -> None:
    path = output_dir / f"{row['source_id']}_live_readonly_failure.json"
    payload = {
        "schema_version": 1,
        "source": "live_readonly_failure",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "data_source_mode": "LIVE_API",
        "source_id": row["source_id"],
        "source_type": row["source_type"],
        "implementation_status": row["implementation_status"],
        "live_fetch_attempted": row["live_fetch_attempted"],
        "live_fetch_succeeded": False,
        "error_category": row["error_category"],
        "provenance": {
            "used_for_default_scan": False,
            "secrets_serialized": False,
        },
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    row["snapshot_path"] = str(path)


def _attach_live_provenance(snapshot: dict[str, Any], *, source_id: str) -> None:
    captured_at = snapshot.get("captured_at") or snapshot.get("retrieved_at") or datetime.now(timezone.utc).isoformat()
    snapshot["data_source_mode"] = "LIVE_API"
    snapshot["source_id"] = source_id
    snapshot["live_fetch_attempted"] = True
    snapshot["live_fetch_succeeded"] = True
    snapshot["provenance"] = {
        "data_source_mode": "LIVE_API",
        "captured_at": captured_at,
        "source_id": source_id,
        "source_type": _source_type(source_id),
        "live_fetch_attempted": True,
        "live_fetch_succeeded": True,
        "used_for_default_scan": False,
        "secrets_serialized": False,
        "quote_freshness": _snapshot_quote_freshness(snapshot),
    }


def _snapshot_quote_freshness(snapshot: dict[str, Any]) -> dict[str, Any]:
    captured_at = snapshot.get("captured_at") or snapshot.get("retrieved_at")
    stale_after = snapshot.get("stale_after")
    return {
        "captured_at": captured_at,
        "stale_after": stale_after,
        "freshness_checked": bool(captured_at),
    }


def _parse_sources_arg(sources: str) -> list[str]:
    parsed = [source.strip().lower().replace("-", "_") for source in sources.split(",") if source.strip()]
    if not parsed:
        raise ValueError("at least one source is required")
    return parsed


def _load_local_env_safely(path: Path | None = None) -> None:
    env_path = path or PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.lower().startswith("export "):
            stripped = stripped[7:].strip()
        name, value = stripped.split("=", 1)
        name = name.strip()
        if not name or not _valid_env_name(name):
            continue
        os.environ.setdefault(name, _strip_env_value(value))


def _strip_env_value(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        return stripped[1:-1]
    return stripped


def _valid_env_name(name: str) -> bool:
    return all(char.isalnum() or char == "_" for char in name) and not name[0].isdigit()


def _source_type(source_id: str) -> str:
    entry = SOURCE_REGISTRY.get(source_id)
    if entry is None:
        return SourceType.DO_NOT_USE_YET.value
    return entry.source_type.value


def _error_category(exc: Exception) -> str:
    message = str(exc).lower()
    if "timed out" in message or "timeout" in message:
        return "TIMEOUT"
    if "http " in message:
        return "HTTP_ERROR"
    if "api key" in message or "required" in message:
        return "CONFIG_ERROR"
    if "failed" in message or "url" in message:
        return "NETWORK_ERROR"
    return "FETCH_ERROR"


def _display_smoke_value(value: Any) -> str:
    if value is None:
        return "none"
    return str(value)


def _safe_cli_text(value: Any) -> str:
    return str(value).replace("\n", " ").replace("\r", " ").replace("|", "/")


def match_live_snapshots(
    polymarket: Path,
    kalshi: Path,
    output: Path,
    min_similarity: float = 0.68,
    max_snapshot_age_hours: float = 24.0,
    reference_snapshots: list[Path] | None = None,
) -> int:
    try:
        payload = match_snapshot_files(
            polymarket,
            kalshi,
            output_path=output,
            min_similarity=min_similarity,
            max_snapshot_age_hours=max_snapshot_age_hours,
            reference_snapshot_paths=reference_snapshots,
        )
    except ValueError as exc:
        print(f"live_snapshot_match_status=FAILED message={exc}")
        return 1
    actions = {pair["action"] for pair in payload["pairs"]}
    print(
        "live_snapshot_match_status=OK "
        f"pairs={payload['pair_count']} actions={','.join(sorted(actions)) or 'none'} "
        f"reference_snapshots={payload['reference_context']['snapshot_count']} "
        f"output={output}"
    )
    return 0


def enrich_orderbooks(
    snapshot: Path,
    venue: str,
    output: Path,
    timeout_seconds: float = 10.0,
    max_snapshot_age_hours: float = 24.0,
    preserve_raw_orderbook: bool = False,
    max_markets: int | None = None,
    progress_every: int = 0,
    retry_failed_once: bool = False,
    failure_sample_limit: int = 10,
) -> int:
    import sys

    def _stderr_progress(event: dict[str, object]) -> None:
        sys.stderr.write(
            f"orderbook_enrichment_progress venue={venue} "
            f"processed={event.get('processed')} total={event.get('total')} "
            f"enriched={event.get('enriched_count')} fetch_failed={event.get('fetch_failed_count')}\n"
        )

    try:
        payload = enrich_orderbook_snapshot_file(
            snapshot_path=snapshot,
            venue=venue,
            output_path=output,
            timeout_seconds=timeout_seconds,
            max_snapshot_age_hours=max_snapshot_age_hours,
            preserve_raw_orderbook=preserve_raw_orderbook,
            max_markets=max_markets,
            progress_every=progress_every,
            retry_failed_once=retry_failed_once,
            progress_callback=_stderr_progress if progress_every > 0 else None,
            failure_sample_limit=failure_sample_limit,
        )
    except ValueError as exc:
        print(f"orderbook_enrichment_status=FAILED venue={venue} message={exc}")
        return 1

    summary = payload["orderbook_enrichment"]
    by_reason = summary.get("fetch_failed_by_reason") or {}
    top_reasons = ",".join(
        f"{reason}:{count}"
        for reason, count in sorted(by_reason.items(), key=lambda kv: -kv[1])[:5]
    ) or "none"
    print(
        "orderbook_enrichment_status=OK "
        f"venue={venue} markets={summary['market_count']} "
        f"enriched={summary['enriched_count']} unenriched={summary['unenriched_count']} "
        f"fresh_orderbook_fetch_enriched={summary.get('fresh_orderbook_fetch_enriched_count', summary['enriched_count'])} "
        f"existing_top_of_book_present={summary.get('existing_top_of_book_present_count', 0)} "
        f"full_orderbook_missing={summary.get('full_orderbook_missing_count', summary['unenriched_count'])} "
        f"fetch_failed={summary.get('fetch_failed_count', 0)} "
        f"stale_existing_top_of_book={summary.get('stale_existing_top_of_book_count', 0)} "
        f"closed_or_settled={summary.get('closed_or_settled_count', 0)} "
        f"empty_book_no_levels={summary.get('empty_book_no_levels_count', 0)} "
        f"endpoint_errors={summary.get('endpoint_error_count', 0)} "
        f"timeouts={summary.get('timeout_count', 0)} "
        f"missing_ticker={summary.get('missing_ticker_count', 0)} "
        f"retry_attempts={summary.get('retry_attempts', 0)} "
        f"retry_successes={summary.get('retry_successes', 0)} "
        f"top_failure_reasons={top_reasons} "
        f"output={output}"
    )
    return 0


def evaluate_paper_candidates(
    pairs: Path,
    polymarket_enriched: Path,
    kalshi_enriched: Path,
    output: Path,
    *,
    max_quote_age_seconds: float = 1800.0,
    max_settlement_delta_seconds: float = 3600.0,
    min_top_of_book_size: float = 1.0,
    min_net_gap: float = 0.01,
    accept_unit_mismatch: bool = False,
    trusted_settlement_normalizations: frozenset[str] = frozenset(),
) -> int:
    config = PaperCandidateEvaluatorConfig(
        max_quote_age_seconds=max_quote_age_seconds,
        max_settlement_delta_seconds=max_settlement_delta_seconds,
        min_top_of_book_size=min_top_of_book_size,
        min_net_gap=min_net_gap,
        accept_unit_mismatch=accept_unit_mismatch,
        trusted_settlement_normalizations=trusted_settlement_normalizations,
    )
    try:
        payload = evaluate_paper_candidate_files(
            pairs_path=pairs,
            polymarket_enriched_path=polymarket_enriched,
            kalshi_enriched_path=kalshi_enriched,
            output_path=output,
            config=config,
        )
    except ValueError as exc:
        print(f"paper_candidate_evaluator_status=FAILED message={exc}")
        return 1

    counts = payload["counts_by_action"]
    print(
        "paper_candidate_evaluator_status=OK "
        f"candidates={payload['ledger_count']} "
        f"paper={counts['PAPER_CANDIDATE']} "
        f"manual_review={counts['MANUAL_REVIEW']} "
        f"watch={counts['WATCH']} "
        f"output={output}"
    )
    return 0


def same_payoff_board(
    *,
    pairs: Path,
    polymarket_enriched: Path,
    kalshi_enriched: Path,
    json_output: Path,
    markdown_output: Path,
) -> int:
    try:
        payload = build_same_payoff_board_files(
            pairs_path=pairs,
            polymarket_enriched_path=polymarket_enriched,
            kalshi_enriched_path=kalshi_enriched,
            json_output_path=json_output,
            markdown_output_path=markdown_output,
        )
    except ValueError as exc:
        print(f"same_payoff_board_status=FAILED message={exc}")
        return 1

    review_count = payload["counts_by_recommended_next_action"].get("RELATIONSHIP_REVIEW", 0)
    print(
        "same_payoff_board_status=OK "
        f"rows={payload['row_count']} "
        f"strict_same_payoff_passes={payload['strict_same_payoff_pass_count']} "
        f"relationship_review={review_count} "
        f"json={json_output} markdown={markdown_output}"
    )
    return 0


def diagnose_mlb_world_series_board_blockers(
    *,
    board: Path,
    pairs: Path,
    json_output: Path,
    markdown_output: Path,
) -> int:
    try:
        payload = diagnose_mlb_world_series_board_blockers_files(
            board_path=board,
            pairs_path=pairs,
            json_output_path=json_output,
            markdown_output_path=markdown_output,
        )
    except ValueError as exc:
        print(f"mlb_world_series_board_blockers_status=FAILED message={exc}")
        return 1

    print(
        "mlb_world_series_board_blockers_status=OK "
        f"rows={payload['row_count']} "
        f"json={json_output} markdown={markdown_output}"
    )
    return 0


def diagnose_mlb_world_series_execution_blockers(
    *,
    pairs: Path,
    polymarket_enriched: Path,
    kalshi_enriched: Path,
    evaluator: Path | None,
    json_output: Path,
    markdown_output: Path,
) -> int:
    try:
        payload = diagnose_mlb_world_series_execution_blockers_files(
            pairs_path=pairs,
            polymarket_enriched_path=polymarket_enriched,
            kalshi_enriched_path=kalshi_enriched,
            evaluator_path=evaluator,
            json_output_path=json_output,
            markdown_output_path=markdown_output,
        )
    except ValueError as exc:
        print(f"mlb_world_series_execution_blockers_status=FAILED message={exc}")
        return 1

    summary = payload["summary"]
    print(
        "mlb_world_series_execution_blockers_status=OK "
        f"pairs={payload['pair_count']} "
        f"dominant_blocker={summary['dominant_blocker']} "
        f"missed_fill_reasons={summary['missed_fill_reasons']} "
        f"orderbook_status_blockers={summary['orderbook_status_blockers']} "
        f"stale_quote_blockers={summary['stale_quote_blockers']} "
        f"missing_fields={summary['missing_fields']} "
        f"no_liquidity_fields={summary['no_liquidity_fields']} "
        f"json={json_output} markdown={markdown_output}"
    )
    return 0


def diagnose_mlb_world_series_evaluator_blockers(
    *,
    evaluator: Path,
    pairs: Path,
    polymarket_enriched: Path,
    kalshi_enriched: Path,
    json_output: Path,
    markdown_output: Path,
) -> int:
    try:
        payload = diagnose_mlb_world_series_evaluator_blockers_files(
            evaluator_path=evaluator,
            pairs_path=pairs,
            polymarket_enriched_path=polymarket_enriched,
            kalshi_enriched_path=kalshi_enriched,
            json_output_path=json_output,
            markdown_output_path=markdown_output,
        )
    except ValueError as exc:
        print(f"mlb_world_series_evaluator_blockers_status=FAILED message={exc}")
        return 1

    summary = payload["summary"]
    print(
        "mlb_world_series_evaluator_blockers_status=OK "
        f"rows={payload['row_count']} "
        f"actions={summary['action_counts']} "
        f"missed_fill_reasons={summary['missed_fill_reason_counts']} "
        f"blocker_categories={summary['blocker_category_counts']} "
        f"dominant_blocker={summary['dominant_blocker']} "
        f"json={json_output} markdown={markdown_output}"
    )
    return 0


def attach_same_payoff_evidence(*, pairs: Path, board: Path, output: Path) -> int:
    try:
        payload = attach_same_payoff_evidence_files(pairs, board, output)
    except ValueError as exc:
        print(f"same_payoff_evidence_attach_status=FAILED message={exc}")
        return 1

    summary = payload["same_payoff_evidence_attachment"]
    print(
        "same_payoff_evidence_attach_status=OK "
        f"pairs={summary['pair_count']} "
        f"trusted_relationships={summary['trusted_relationship_attached_count']} "
        f"diagnostic_relationships={summary['diagnostic_evidence_attached_count']} "
        f"ambiguous_identities={summary['ambiguous_identity_count']} "
        f"unmatched_pairs={summary['unmatched_pair_count']} "
        f"output={output}"
    )
    return 0


def audit_same_scope_mlb_candidates(
    *,
    pairs: Path,
    polymarket_enriched: Path,
    kalshi_enriched: Path,
    json_output: Path,
    markdown_output: Path,
    board_json_output: Path,
    board_markdown_output: Path,
    derived_pairs_output: Path,
    evaluator_output: Path,
    max_quote_age_seconds: float = 1800.0,
    max_settlement_delta_seconds: float = 3600.0,
    min_top_of_book_size: float = 1.0,
    min_net_gap: float = 0.01,
    accept_unit_mismatch: bool = False,
) -> int:
    try:
        payload = audit_same_scope_mlb_candidate_files(
            pairs_path=pairs,
            polymarket_enriched_path=polymarket_enriched,
            kalshi_enriched_path=kalshi_enriched,
            json_output_path=json_output,
            markdown_output_path=markdown_output,
            board_json_output_path=board_json_output,
            board_markdown_output_path=board_markdown_output,
            derived_pairs_output_path=derived_pairs_output,
            evaluator_output_path=evaluator_output,
            max_quote_age_seconds=max_quote_age_seconds,
            max_settlement_delta_seconds=max_settlement_delta_seconds,
            min_top_of_book_size=min_top_of_book_size,
            min_net_gap=min_net_gap,
            accept_unit_mismatch=accept_unit_mismatch,
        )
    except ValueError as exc:
        print(f"mlb_same_scope_audit_status=FAILED message={exc}")
        return 1

    summary = payload["summary"]
    print(
        "mlb_same_scope_audit_status=OK "
        f"rows={summary['row_count']} "
        f"same_scope={summary['same_scope_candidate_count']} "
        f"trusted_evidence={summary['trusted_same_payoff_evidence_count']} "
        f"candidate_actions={summary['candidate_action_count']} "
        f"json={json_output} markdown={markdown_output}"
    )
    return 0


def diagnose_mlb_same_scope_targeting(
    *,
    polymarket_snapshot: Path,
    kalshi_snapshot: Path,
    pairs: Path,
    audit: Path,
    scope: str,
    json_output: Path,
    markdown_output: Path,
) -> int:
    try:
        payload = diagnose_mlb_same_scope_targeting_files(
            polymarket_snapshot_path=polymarket_snapshot,
            kalshi_snapshot_path=kalshi_snapshot,
            pairs_path=pairs if pairs.exists() else None,
            audit_path=audit if audit.exists() else None,
            scope=scope,
            json_output_path=json_output,
            markdown_output_path=markdown_output,
        )
    except ValueError as exc:
        print(f"mlb_same_scope_targeting_status=FAILED message={exc}")
        return 1

    summary = payload["summary"]
    print(
        "mlb_same_scope_targeting_status=OK "
        f"scope={payload['scope_filter']} "
        f"polymarket_rows={summary['polymarket_rows']} "
        f"kalshi_rows={summary['kalshi_rows']} "
        f"overlap_scopes={','.join(summary['overlap_scopes']) or 'none'} "
        f"json={json_output} markdown={markdown_output}"
    )
    return 0


def build_mlb_world_series_pairs(
    *,
    polymarket_snapshot: Path,
    kalshi_snapshot: Path,
    json_output: Path,
    markdown_output: Path,
    match_report: Path | None = None,
) -> int:
    try:
        payload = build_mlb_world_series_pairs_files(
            polymarket_snapshot_path=polymarket_snapshot,
            kalshi_snapshot_path=kalshi_snapshot,
            json_output_path=json_output,
            markdown_output_path=markdown_output,
            match_report_path=match_report,
        )
    except ValueError as exc:
        print(f"mlb_world_series_pairs_status=FAILED message={exc}")
        return 1

    summary = payload["summary"]
    counts = summary["source_counts_by_scope"]
    warnings = summary.get("warnings") or []
    provenance = payload.get("input_provenance") if isinstance(payload.get("input_provenance"), dict) else {}
    print(
        "mlb_world_series_pairs_status=OK "
        f"ws_ws_pairs={summary['generated_ws_ws_pair_count']} "
        f"polymarket_ws={counts['polymarket'].get('WORLD_SERIES', 0)} "
        f"kalshi_ws={counts['kalshi'].get('WORLD_SERIES', 0)} "
        f"warnings={','.join(warnings) if warnings else 'none'} "
        f"json={json_output} markdown={markdown_output}"
    )
    for source in ("polymarket", "kalshi"):
        info = provenance.get(source) if isinstance(provenance.get(source), dict) else {}
        print(
            "mlb_world_series_pairs_input_provenance "
            f"source={source} "
            f"captured_at={info.get('captured_at') or 'unknown'} "
            f"normalized_count={info.get('normalized_count', 'unknown')} "
            f"overlap_universe_query={info.get('overlap_universe_query') or 'none'}"
        )
    return 0


def build_nhl_stanley_cup_pairs(
    *,
    polymarket_snapshot: Path,
    kalshi_snapshot: Path,
    json_output: Path,
    markdown_output: Path,
) -> int:
    try:
        payload = build_nhl_stanley_cup_pairs_files(
            polymarket_snapshot_path=polymarket_snapshot,
            kalshi_snapshot_path=kalshi_snapshot,
            json_output_path=json_output,
            markdown_output_path=markdown_output,
        )
    except ValueError as exc:
        print(f"nhl_stanley_cup_pairs_status=FAILED message={exc}")
        return 1

    summary = payload["summary"]
    counts = summary["source_counts_by_scope"]
    print(
        "nhl_stanley_cup_pairs_status=OK "
        f"stanley_cup_pairs={summary['generated_stanley_cup_pair_count']} "
        f"polymarket_stanley_cup={counts['polymarket'].get('STANLEY_CUP', 0)} "
        f"kalshi_stanley_cup={counts['kalshi'].get('STANLEY_CUP', 0)} "
        f"json={json_output} markdown={markdown_output}"
    )
    return 0


def run_mlb_world_series_paper_check(
    *,
    polymarket_snapshot: Path,
    kalshi_snapshot: Path,
    pairs: Path | None = None,
    timeout_seconds: float = 10.0,
    max_snapshot_age_hours: float = 24.0,
    max_quote_age_seconds: float = 1800.0,
    max_settlement_delta_seconds: float = 3600.0,
    min_top_of_book_size: float = 1.0,
    min_net_gap: float = 0.01,
    accept_unit_mismatch: bool = False,
    trusted_settlement_normalizations: frozenset[str] = frozenset(),
    polymarket_enriched_output: Path = PROJECT_ROOT / "reports" / "mlb_fresh_polymarket_enriched.json",
    kalshi_enriched_output: Path = PROJECT_ROOT / "reports" / "mlb_fresh_kalshi_enriched.json",
    rebuild_pairs_from_snapshots: bool = False,
    rebuilt_pairs_json_output: Path = PROJECT_ROOT / "reports" / "mlb_world_series_pairs_run.json",
    rebuilt_pairs_markdown_output: Path = PROJECT_ROOT / "reports" / "mlb_world_series_pairs_run.md",
    board_json_output: Path = PROJECT_ROOT / "reports" / "mlb_world_series_same_payoff_board_fresh.json",
    board_markdown_output: Path = PROJECT_ROOT / "reports" / "mlb_world_series_same_payoff_board_fresh.md",
    derived_pairs_output: Path = PROJECT_ROOT / "reports" / "mlb_world_series_pairs_with_evidence_fresh.json",
    evaluator_output: Path = PROJECT_ROOT / "reports" / "mlb_world_series_evaluator_fresh_trust_settlement.json",
    summary_json_output: Path = PROJECT_ROOT / "reports" / "mlb_world_series_paper_check_summary.json",
    summary_markdown_output: Path = PROJECT_ROOT / "reports" / "mlb_world_series_paper_check_summary.md",
    settlement_audit_json_output: Path = PROJECT_ROOT / "reports" / "mlb_world_series_settlement_audit.json",
    settlement_audit_markdown_output: Path = PROJECT_ROOT / "reports" / "mlb_world_series_settlement_audit.md",
) -> int:
    generated_at = datetime.now(timezone.utc)
    if rebuild_pairs_from_snapshots:
        if board_json_output == PROJECT_ROOT / "reports" / "mlb_world_series_same_payoff_board_fresh.json":
            board_json_output = PROJECT_ROOT / "reports" / "mlb_world_series_same_payoff_board_run.json"
        if board_markdown_output == PROJECT_ROOT / "reports" / "mlb_world_series_same_payoff_board_fresh.md":
            board_markdown_output = PROJECT_ROOT / "reports" / "mlb_world_series_same_payoff_board_run.md"
        if derived_pairs_output == PROJECT_ROOT / "reports" / "mlb_world_series_pairs_with_evidence_fresh.json":
            derived_pairs_output = PROJECT_ROOT / "reports" / "mlb_world_series_pairs_with_evidence_run.json"
        if evaluator_output == PROJECT_ROOT / "reports" / "mlb_world_series_evaluator_fresh_trust_settlement.json":
            evaluator_output = PROJECT_ROOT / "reports" / "mlb_world_series_evaluator_run.json"
    try:
        if pairs is None and not rebuild_pairs_from_snapshots:
            raise ValueError("provide --pairs or set --rebuild-pairs-from-snapshots")
        snapshot_universe_validation = _validate_mlb_world_series_snapshot_universe(
            polymarket_snapshot_path=polymarket_snapshot,
            kalshi_snapshot_path=kalshi_snapshot,
        )
        polymarket_enriched = enrich_orderbook_snapshot_file(
            snapshot_path=polymarket_snapshot,
            venue="polymarket",
            output_path=polymarket_enriched_output,
            now=generated_at,
            timeout_seconds=timeout_seconds,
            max_snapshot_age_hours=max_snapshot_age_hours,
        )
        kalshi_enriched = enrich_orderbook_snapshot_file(
            snapshot_path=kalshi_snapshot,
            venue="kalshi",
            output_path=kalshi_enriched_output,
            now=generated_at,
            timeout_seconds=timeout_seconds,
            max_snapshot_age_hours=max_snapshot_age_hours,
        )
        effective_pairs = pairs
        rebuilt_pairs = None
        if rebuild_pairs_from_snapshots:
            rebuilt_pairs = build_mlb_world_series_pairs_files(
                polymarket_snapshot_path=polymarket_enriched_output,
                kalshi_snapshot_path=kalshi_enriched_output,
                json_output_path=rebuilt_pairs_json_output,
                markdown_output_path=rebuilt_pairs_markdown_output,
                match_report_path=None,
                now=generated_at,
            )
            effective_pairs = rebuilt_pairs_json_output
        if effective_pairs is None:
            raise ValueError("effective pairs path was not resolved")
        _validate_mlb_paper_check_report_inputs(
            label="pairs",
            payload=_load_json_report(effective_pairs, "pairs"),
            expected={
                "polymarket_snapshot": polymarket_enriched_output if rebuild_pairs_from_snapshots else polymarket_snapshot,
                "kalshi_snapshot": kalshi_enriched_output if rebuild_pairs_from_snapshots else kalshi_snapshot,
            },
        )
        join_validation = _validate_pairs_join_enriched_markets(
            pairs_path=effective_pairs,
            polymarket_enriched=polymarket_enriched,
            kalshi_enriched=kalshi_enriched,
        )
        board = build_same_payoff_board_files(
            pairs_path=effective_pairs,
            polymarket_enriched_path=polymarket_enriched_output,
            kalshi_enriched_path=kalshi_enriched_output,
            json_output_path=board_json_output,
            markdown_output_path=board_markdown_output,
            now=generated_at,
            max_quote_age_seconds=max_quote_age_seconds,
        )
        _validate_mlb_paper_check_report_inputs(
            label="same_payoff_board",
            payload=board,
            expected={
                "pairs": effective_pairs,
                "polymarket_enriched": polymarket_enriched_output,
                "kalshi_enriched": kalshi_enriched_output,
            },
        )
        derived_pairs = attach_same_payoff_evidence_files(effective_pairs, board_json_output, derived_pairs_output)
        _validate_mlb_paper_check_report_inputs(
            label="same_payoff_evidence_attachment",
            payload=derived_pairs.get("same_payoff_evidence_attachment"),
            expected={
                "pairs": effective_pairs,
                "board": board_json_output,
            },
        )
        evaluator = evaluate_paper_candidate_files(
            pairs_path=derived_pairs_output,
            polymarket_enriched_path=polymarket_enriched_output,
            kalshi_enriched_path=kalshi_enriched_output,
            output_path=evaluator_output,
            config=PaperCandidateEvaluatorConfig(
                max_quote_age_seconds=max_quote_age_seconds,
                max_settlement_delta_seconds=max_settlement_delta_seconds,
                min_top_of_book_size=min_top_of_book_size,
                min_net_gap=min_net_gap,
                accept_unit_mismatch=accept_unit_mismatch,
                trusted_settlement_normalizations=trusted_settlement_normalizations,
            ),
            now=generated_at,
        )
        _validate_mlb_paper_check_report_inputs(
            label="paper_candidate_evaluator",
            payload=evaluator,
            expected={
                "pairs": derived_pairs_output,
                "polymarket_enriched": polymarket_enriched_output,
                "kalshi_enriched": kalshi_enriched_output,
            },
        )
    except ValueError as exc:
        print(f"mlb_world_series_paper_check_status=FAILED message={exc}")
        return 1

    summary = _mlb_world_series_paper_check_summary(
        generated_at=generated_at,
        polymarket_snapshot=polymarket_snapshot,
        kalshi_snapshot=kalshi_snapshot,
        pairs=effective_pairs,
        polymarket_enriched_output=polymarket_enriched_output,
        kalshi_enriched_output=kalshi_enriched_output,
        rebuilt_pairs_output=rebuilt_pairs_json_output if rebuild_pairs_from_snapshots else None,
        rebuilt_pairs_markdown_output=rebuilt_pairs_markdown_output if rebuild_pairs_from_snapshots else None,
        board_json_output=board_json_output,
        board_markdown_output=board_markdown_output,
        derived_pairs_output=derived_pairs_output,
        evaluator_output=evaluator_output,
        summary_json_output=summary_json_output,
        summary_markdown_output=summary_markdown_output,
        settlement_audit_json_output=settlement_audit_json_output,
        settlement_audit_markdown_output=settlement_audit_markdown_output,
        polymarket_enriched=polymarket_enriched,
        kalshi_enriched=kalshi_enriched,
        rebuilt_pairs=rebuilt_pairs,
        pair_join_validation=join_validation,
        snapshot_universe_validation=snapshot_universe_validation,
        board=board,
        derived_pairs=derived_pairs,
        evaluator=evaluator,
        max_quote_age_seconds=max_quote_age_seconds,
        max_snapshot_age_hours=max_snapshot_age_hours,
        max_settlement_delta_seconds=max_settlement_delta_seconds,
        min_top_of_book_size=min_top_of_book_size,
        min_net_gap=min_net_gap,
        accept_unit_mismatch=accept_unit_mismatch,
        trusted_settlement_normalizations=trusted_settlement_normalizations,
    )
    summary_json_output.parent.mkdir(parents=True, exist_ok=True)
    summary_markdown_output.parent.mkdir(parents=True, exist_ok=True)
    summary_json_output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    summary_markdown_output.write_text(_mlb_world_series_paper_check_markdown(summary), encoding="utf-8")
    settlement_audit = summary["settlement_source_audit"]
    settlement_audit_json_output.parent.mkdir(parents=True, exist_ok=True)
    settlement_audit_markdown_output.parent.mkdir(parents=True, exist_ok=True)
    settlement_audit_json_output.write_text(json.dumps(settlement_audit, indent=2, sort_keys=True), encoding="utf-8")
    settlement_audit_markdown_output.write_text(_mlb_world_series_settlement_audit_markdown(settlement_audit), encoding="utf-8")

    counts = summary["evaluator_counts"]
    paper_candidate_ids = summary["paper_candidate_ids"]
    print(
        "mlb_world_series_paper_check_status=OK "
        f"universe={summary['preflight']['universe']} "
        f"universe_specific_paths={str(summary['preflight']['paths_are_universe_specific']).lower()} "
        f"polymarket_enriched={summary['polymarket_enrichment']['enriched_count']}/{summary['polymarket_enrichment']['market_count']} "
        f"kalshi_enriched={summary['kalshi_enrichment']['enriched_count']}/{summary['kalshi_enrichment']['market_count']} "
        f"matched_pairs={summary['pair_join_validation']['matched_pairs']} "
        f"missing_polymarket_enriched_market={summary['pair_join_validation']['missing_polymarket_enriched_market']} "
        f"missing_kalshi_enriched_market={summary['pair_join_validation']['missing_kalshi_enriched_market']} "
        f"strict_same_payoff_passes={summary['strict_same_payoff_passes']} "
        f"trusted_relationships={summary['trusted_relationships']} "
        f"paper={counts.get('PAPER_CANDIDATE', 0)} "
        f"manual_review={counts.get('MANUAL_REVIEW', 0)} "
        f"watch={counts.get('WATCH', 0)} "
        f"dominant_blocker={summary['dominant_blocker'] or 'none'} "
        f"strict_blockers={_format_count_map(summary['strict_same_payoff_blocker_counts'])} "
        f"trusted_blockers={_format_count_map(summary['trusted_relationship_blocker_counts'])} "
        f"evaluator_blockers={_format_count_map(summary['evaluator_blocker_counts'])} "
        f"settlement_delta_min={summary['settlement_delta_seconds']['min'] if summary['settlement_delta_seconds']['min'] is not None else 'none'} "
        f"settlement_delta_max={summary['settlement_delta_seconds']['max'] if summary['settlement_delta_seconds']['max'] is not None else 'none'} "
        f"paper_candidate_ids={','.join(paper_candidate_ids) if paper_candidate_ids else 'none'} "
        f"max_quote_age_seconds={summary['quote_freshness']['max_quote_age_seconds'] if summary['quote_freshness']['max_quote_age_seconds'] is not None else 'unknown'} "
        f"stale_quote_warning={str(summary['quote_freshness']['stale_quote_warning']).lower()} "
        f"summary={summary_json_output}"
    )
    print(
        "mlb_world_series_paper_check_preflight "
        f"source_polymarket_snapshot={summary['evaluated_paths']['source_polymarket_snapshot']} "
        f"source_kalshi_snapshot={summary['evaluated_paths']['source_kalshi_snapshot']} "
        f"pairs_evaluated={summary['evaluated_paths']['pairs_evaluated']} "
        f"same_payoff_board={summary['evaluated_paths']['same_payoff_board_used']} "
        f"polymarket_enriched_orderbook={summary['evaluated_paths']['polymarket_enriched_orderbook_used']} "
        f"kalshi_enriched_orderbook={summary['evaluated_paths']['kalshi_enriched_orderbook_used']} "
        f"quote_freshness={summary['preflight']['quote_freshness_status']['status']} "
        f"fee_models=polymarket:{summary['preflight']['fee_model_names']['polymarket']},kalshi:{summary['preflight']['fee_model_names']['kalshi']} "
        f"settlement_normalization_trust={summary['preflight']['settlement_normalization_trust']['status']} "
        f"depth_top_of_book=polymarket:{summary['preflight']['top_of_book_depth_status']['polymarket']['status']},kalshi:{summary['preflight']['top_of_book_depth_status']['kalshi']['status']} "
        f"generic_live_readonly_warning={summary['preflight']['generic_live_readonly_warning'] or 'none'}"
    )
    phase = summary["blocker_drilldown"]["phase_diagnosis"]
    print(
        "mlb_world_series_paper_check_blocker_drilldown "
        f"board_failed_before_evidence={str(phase['same_payoff_board_failed_before_evidence_attachment']).lower()} "
        f"evidence_attachment_failed={str(phase['evidence_attachment_failed']).lower()} "
        f"evaluator_rejected_trusted_relationships={str(phase['evaluator_rejected_trusted_relationships']).lower()} "
        f"orderbook_execution_failed={str(phase['orderbook_execution_failed']).lower()} "
        f"settlement_normalization_requested={','.join(summary['settlement_normalization_counts']['requested']) or 'none'} "
        f"settlement_normalization_evidence={summary['settlement_normalization_counts']['evidence_normalization_count']} "
        f"settlement_normalization_accepted={summary['settlement_normalization_counts']['accepted_by_evaluator_count']} "
        f"settlement_normalization_rejected={summary['settlement_normalization_counts']['rejected_by_evaluator_count']}"
    )
    audit_summary = settlement_audit["summary"]
    print(
        "mlb_world_series_settlement_audit_status=OK "
        f"audited_pairs={audit_summary['audited_pairs']} "
        f"source_mismatch_count={audit_summary['source_mismatch_count']} "
        f"time_mismatch_count={audit_summary['time_mismatch_count']} "
        f"parser_missing_count={audit_summary['parser_missing_count']} "
        f"unknown_count={audit_summary['unknown_count']} "
        f"top_blocked_examples={settlement_audit_json_output}"
    )
    if summary["preflight"]["generic_live_readonly_warning"]:
        print(
            "GENERIC_LIVE_READONLY_WARNING "
            f"{summary['preflight']['generic_live_readonly_warning']} "
            "prefer=reports/live_readonly/mlb"
        )
    if counts.get("PAPER_CANDIDATE", 0):
        print(
            "STOP_FOR_REVIEW paper_candidates_detected="
            f"{counts.get('PAPER_CANDIDATE', 0)} ids={','.join(paper_candidate_ids)} "
            "diagnostics_only=true no_trading_or_execution_performed=true"
        )
        print(
            "STOP_AND_REVIEW paper_candidates_detected="
            f"{counts.get('PAPER_CANDIDATE', 0)} ids={','.join(paper_candidate_ids)} "
            "diagnostics_only=true no_trading_or_execution_performed=true"
        )
    elif summary["quote_freshness"]["stale_quote_warning"]:
        print(
            "STALE_QUOTE_WARNING "
            f"max_quote_age_seconds={summary['quote_freshness']['max_quote_age_seconds']} "
            f"limit={max_quote_age_seconds} "
            "paper_check_remains_blocked=true"
        )
    return 0


def run_nba_championship_paper_check(
    *,
    polymarket_snapshot: Path,
    kalshi_snapshot: Path,
    pairs: Path,
    timeout_seconds: float = 10.0,
    max_snapshot_age_hours: float = 24.0,
    max_quote_age_seconds: float = 1800.0,
    max_settlement_delta_seconds: float = 3600.0,
    min_top_of_book_size: float = 1.0,
    min_net_gap: float = 0.01,
    accept_unit_mismatch: bool = False,
    trusted_settlement_normalizations: frozenset[str] = frozenset(),
    polymarket_enriched_output: Path = PROJECT_ROOT / "reports" / "nba_kxnba_polymarket_enriched_fresh.json",
    kalshi_enriched_output: Path = PROJECT_ROOT / "reports" / "nba_kxnba_kalshi_enriched_fresh.json",
    board_json_output: Path = PROJECT_ROOT / "reports" / "nba_kxnba_same_payoff_board_fresh.json",
    board_markdown_output: Path = PROJECT_ROOT / "reports" / "nba_kxnba_same_payoff_board_fresh.md",
    derived_pairs_output: Path = PROJECT_ROOT / "reports" / "nba_kxnba_pairs_with_evidence_fresh.json",
    evaluator_output: Path = PROJECT_ROOT / "reports" / "nba_kxnba_evaluator_fresh.json",
    summary_json_output: Path = PROJECT_ROOT / "reports" / "nba_championship_paper_check_summary.json",
    summary_markdown_output: Path = PROJECT_ROOT / "reports" / "nba_championship_paper_check_summary.md",
) -> int:
    generated_at = datetime.now(timezone.utc)
    try:
        polymarket_enriched = enrich_orderbook_snapshot_file(
            snapshot_path=polymarket_snapshot,
            venue="polymarket",
            output_path=polymarket_enriched_output,
            now=generated_at,
            timeout_seconds=timeout_seconds,
            max_snapshot_age_hours=max_snapshot_age_hours,
        )
        kalshi_enriched = enrich_orderbook_snapshot_file(
            snapshot_path=kalshi_snapshot,
            venue="kalshi",
            output_path=kalshi_enriched_output,
            now=generated_at,
            timeout_seconds=timeout_seconds,
            max_snapshot_age_hours=max_snapshot_age_hours,
        )
        board = build_same_payoff_board_files(
            pairs_path=pairs,
            polymarket_enriched_path=polymarket_enriched_output,
            kalshi_enriched_path=kalshi_enriched_output,
            json_output_path=board_json_output,
            markdown_output_path=board_markdown_output,
            now=generated_at,
            max_quote_age_seconds=max_quote_age_seconds,
        )
        derived_pairs = attach_same_payoff_evidence_files(pairs, board_json_output, derived_pairs_output)
        evaluator = evaluate_paper_candidate_files(
            pairs_path=derived_pairs_output,
            polymarket_enriched_path=polymarket_enriched_output,
            kalshi_enriched_path=kalshi_enriched_output,
            output_path=evaluator_output,
            config=PaperCandidateEvaluatorConfig(
                max_quote_age_seconds=max_quote_age_seconds,
                max_settlement_delta_seconds=max_settlement_delta_seconds,
                min_top_of_book_size=min_top_of_book_size,
                min_net_gap=min_net_gap,
                accept_unit_mismatch=accept_unit_mismatch,
                trusted_settlement_normalizations=trusted_settlement_normalizations,
            ),
            now=generated_at,
        )
    except ValueError as exc:
        print(f"nba_championship_paper_check_status=FAILED message={exc}")
        return 1

    summary = _mlb_world_series_paper_check_summary(
        generated_at=generated_at,
        polymarket_snapshot=polymarket_snapshot,
        kalshi_snapshot=kalshi_snapshot,
        pairs=pairs,
        polymarket_enriched_output=polymarket_enriched_output,
        kalshi_enriched_output=kalshi_enriched_output,
        board_json_output=board_json_output,
        board_markdown_output=board_markdown_output,
        derived_pairs_output=derived_pairs_output,
        evaluator_output=evaluator_output,
        summary_json_output=summary_json_output,
        summary_markdown_output=summary_markdown_output,
        polymarket_enriched=polymarket_enriched,
        kalshi_enriched=kalshi_enriched,
        board=board,
        derived_pairs=derived_pairs,
        evaluator=evaluator,
        max_quote_age_seconds=max_quote_age_seconds,
        max_snapshot_age_hours=max_snapshot_age_hours,
        max_settlement_delta_seconds=max_settlement_delta_seconds,
        min_top_of_book_size=min_top_of_book_size,
        min_net_gap=min_net_gap,
        accept_unit_mismatch=accept_unit_mismatch,
        trusted_settlement_normalizations=trusted_settlement_normalizations,
        source="nba_championship_paper_check_runner",
        title="NBA Championship Paper Check",
        disclaimer=(
            "Read-only NBA championship paper-check diagnostics. This runner enriches saved snapshots, "
            "attaches deterministic same-payoff evidence, evaluates existing paper gates, and stops at "
            "STOP_AND_REVIEW when PAPER_CANDIDATE appears. It does not trade or execute."
        ),
    )
    summary_json_output.parent.mkdir(parents=True, exist_ok=True)
    summary_markdown_output.parent.mkdir(parents=True, exist_ok=True)
    summary_json_output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    summary_markdown_output.write_text(_mlb_world_series_paper_check_markdown(summary), encoding="utf-8")

    counts = summary["evaluator_counts"]
    paper_candidate_ids = summary["paper_candidate_ids"]
    print(
        "nba_championship_paper_check_status=OK "
        f"polymarket_enriched={summary['polymarket_enrichment']['enriched_count']}/{summary['polymarket_enrichment']['market_count']} "
        f"kalshi_enriched={summary['kalshi_enrichment']['enriched_count']}/{summary['kalshi_enrichment']['market_count']} "
        f"strict_same_payoff_passes={summary['strict_same_payoff_passes']} "
        f"trusted_relationships={summary['trusted_relationships']} "
        f"paper={counts.get('PAPER_CANDIDATE', 0)} "
        f"manual_review={counts.get('MANUAL_REVIEW', 0)} "
        f"watch={counts.get('WATCH', 0)} "
        f"dominant_blocker={summary['dominant_blocker'] or 'none'} "
        f"paper_candidate_ids={','.join(paper_candidate_ids) if paper_candidate_ids else 'none'} "
        f"max_quote_age_seconds={summary['quote_freshness']['max_quote_age_seconds'] if summary['quote_freshness']['max_quote_age_seconds'] is not None else 'unknown'} "
        f"stale_quote_warning={str(summary['quote_freshness']['stale_quote_warning']).lower()} "
        f"summary={summary_json_output}"
    )
    if counts.get("PAPER_CANDIDATE", 0):
        print(
            "STOP_FOR_REVIEW paper_candidates_detected="
            f"{counts.get('PAPER_CANDIDATE', 0)} ids={','.join(paper_candidate_ids)} "
            "diagnostics_only=true no_trading_or_execution_performed=true"
        )
        print(
            "STOP_AND_REVIEW paper_candidates_detected="
            f"{counts.get('PAPER_CANDIDATE', 0)} ids={','.join(paper_candidate_ids)} "
            "diagnostics_only=true no_trading_or_execution_performed=true"
        )
    elif summary["quote_freshness"]["stale_quote_warning"]:
        print(
            "STALE_QUOTE_WARNING "
            f"max_quote_age_seconds={summary['quote_freshness']['max_quote_age_seconds']} "
            f"limit={max_quote_age_seconds} "
            "paper_check_remains_blocked=true"
        )
    return 0


def _validate_pairs_join_enriched_markets(
    *,
    pairs_path: Path,
    polymarket_enriched: dict[str, Any],
    kalshi_enriched: dict[str, Any],
) -> dict[str, Any]:
    pairs_payload = _load_json_report(pairs_path, "pairs")
    pairs = pairs_payload.get("pairs")
    if not isinstance(pairs, list):
        raise ValueError("pairs input must contain pairs list")
    polymarket_ids = {
        str(row.get("market_id"))
        for row in _normalized_market_rows_for_join(polymarket_enriched)
        if row.get("market_id") is not None
    }
    kalshi_ids = {
        str(row.get("ticker") or row.get("market_id"))
        for row in _normalized_market_rows_for_join(kalshi_enriched)
        if row.get("ticker") is not None or row.get("market_id") is not None
    }
    missing_polymarket = 0
    missing_kalshi = 0
    matched = 0
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        poly_id = _paper_check_pair_polymarket_id(pair)
        kalshi_id = _paper_check_pair_kalshi_id(pair)
        poly_missing = not poly_id or poly_id not in polymarket_ids
        kalshi_missing = not kalshi_id or kalshi_id not in kalshi_ids
        if poly_missing:
            missing_polymarket += 1
        if kalshi_missing:
            missing_kalshi += 1
        if not poly_missing and not kalshi_missing:
            matched += 1
    total = len([pair for pair in pairs if isinstance(pair, dict)])
    result = {
        "pair_count": total,
        "matched_pairs": matched,
        "missing_polymarket_enriched_market": missing_polymarket,
        "missing_kalshi_enriched_market": missing_kalshi,
    }
    if total > 0 and (missing_polymarket or missing_kalshi):
        raise ValueError(
            "snapshot_pair_provenance_mismatch "
            f"matched_pairs={matched} "
            f"missing_polymarket_enriched_market={missing_polymarket} "
            f"missing_kalshi_enriched_market={missing_kalshi}"
        )
    return result


def _empty_pair_join_validation() -> dict[str, int]:
    return {
        "pair_count": 0,
        "matched_pairs": 0,
        "missing_polymarket_enriched_market": 0,
        "missing_kalshi_enriched_market": 0,
    }


def _validate_mlb_paper_check_report_inputs(
    *,
    label: str,
    payload: Any,
    expected: dict[str, Path],
) -> None:
    if not isinstance(payload, dict):
        raise ValueError(f"mlb_paper_check_report_inputs_missing sub_step={label} missing_payload=true")
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError(f"mlb_paper_check_report_inputs_missing sub_step={label} missing_inputs=true")
    missing_keys = []
    mismatches = []
    for key, expected_path in expected.items():
        actual = inputs.get(key)
        if actual is None:
            missing_keys.append(key)
            continue
        if not _same_report_path(actual, expected_path):
            mismatches.append(f"{key}:expected={expected_path}:actual={actual}")
    if missing_keys:
        raise ValueError(f"mlb_paper_check_report_inputs_missing sub_step={label} missing_inputs={','.join(missing_keys)}")
    if mismatches:
        raise ValueError(f"snapshot_set_mismatch {label} " + " ".join(mismatches))


def _same_report_path(actual: Any, expected: Path) -> bool:
    try:
        actual_path = Path(str(actual)).expanduser()
        expected_path = Path(expected).expanduser()
        return actual_path.resolve(strict=False) == expected_path.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return str(actual) == str(expected)


def _validate_mlb_world_series_snapshot_universe(
    *,
    polymarket_snapshot_path: Path,
    kalshi_snapshot_path: Path,
) -> dict[str, Any]:
    polymarket_snapshot = _load_json_object(polymarket_snapshot_path)
    kalshi_snapshot = _load_json_object(kalshi_snapshot_path)
    polymarket_query = _snapshot_overlap_query(polymarket_snapshot)
    kalshi_query = _snapshot_overlap_query(kalshi_snapshot)
    bad_queries = {
        source: query
        for source, query in {"polymarket": polymarket_query, "kalshi": kalshi_query}.items()
        if query and not _query_targets_mlb_world_series(query)
    }
    polymarket_inventory = _mlb_world_series_inventory_count(polymarket_snapshot, venue="polymarket")
    kalshi_inventory = _mlb_world_series_inventory_count(kalshi_snapshot, venue="kalshi")
    result = {
        "expected_universe": "mlb_world_series_kxmlb",
        "polymarket_overlap_query": polymarket_query,
        "kalshi_overlap_query": kalshi_query,
        "polymarket_mlb_world_series_inventory": polymarket_inventory,
        "kalshi_mlb_world_series_inventory": kalshi_inventory,
        "wrong_universe_queries": bad_queries,
    }
    if bad_queries or polymarket_inventory <= 0 or kalshi_inventory <= 0:
        raise ValueError(
            "wrong_universe_snapshot "
            f"polymarket_query={_safe_cli_text(polymarket_query or 'none')} "
            f"kalshi_query={_safe_cli_text(kalshi_query or 'none')} "
            f"polymarket_mlb_world_series_inventory={polymarket_inventory} "
            f"kalshi_mlb_world_series_inventory={kalshi_inventory}"
        )
    return result


def _snapshot_overlap_query(snapshot: dict[str, Any]) -> str | None:
    overlap = snapshot.get("overlap_universe")
    if isinstance(overlap, dict) and overlap.get("query") is not None:
        return str(overlap.get("query") or "")
    metadata = snapshot.get("overlap_universe_filter")
    if isinstance(metadata, dict) and metadata.get("query") is not None:
        return str(metadata.get("query") or "")
    return None


def _query_targets_mlb_world_series(query: str) -> bool:
    normalized = query.lower()
    return any(term in normalized for term in ("mlb", "baseball", "world series", "kxmlb"))


def _mlb_world_series_inventory_count(snapshot: dict[str, Any], *, venue: str) -> int:
    return sum(1 for row in _normalized_market_rows_for_join(snapshot) if _is_mlb_world_series_market(row, venue=venue))


def _is_mlb_world_series_market(row: dict[str, Any], *, venue: str) -> bool:
    fields = [
        row.get("market_id"),
        row.get("ticker"),
        row.get("event_ticker"),
        row.get("series_ticker"),
        row.get("question"),
        row.get("title"),
        row.get("event_title"),
        row.get("subtitle"),
        row.get("slug"),
    ]
    raw = row.get("raw")
    if isinstance(raw, dict):
        fields.extend(raw.get(key) for key in ("ticker", "event_ticker", "series_ticker", "title", "question", "slug"))
    text = " ".join(str(value or "") for value in fields).lower()
    if venue == "kalshi" and "kxmlb" in text:
        return True
    world_series = "world series" in text
    pro_baseball_championship = "pro baseball championship" in text
    baseball_context = any(term in text for term in ("mlb", "baseball", "kxmlb"))
    return world_series or pro_baseball_championship or ("championship" in text and baseball_context)


def _normalized_market_rows_for_join(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("normalized_markets")
    if not isinstance(rows, list):
        raise ValueError("enriched snapshot must contain normalized_markets list")
    return [row for row in rows if isinstance(row, dict)]


def _paper_check_pair_polymarket_id(pair: dict[str, Any]) -> str:
    polymarket = pair.get("polymarket") if isinstance(pair.get("polymarket"), dict) else {}
    return str(polymarket.get("market_id") or "")


def _paper_check_pair_kalshi_id(pair: dict[str, Any]) -> str:
    kalshi = pair.get("kalshi") if isinstance(pair.get("kalshi"), dict) else {}
    return str(kalshi.get("ticker") or kalshi.get("market_id") or "")


def _mlb_world_series_paper_check_summary(
    *,
    generated_at: datetime,
    polymarket_snapshot: Path,
    kalshi_snapshot: Path,
    pairs: Path,
    polymarket_enriched_output: Path,
    kalshi_enriched_output: Path,
    rebuilt_pairs_output: Path | None = None,
    rebuilt_pairs_markdown_output: Path | None = None,
    board_json_output: Path,
    board_markdown_output: Path,
    derived_pairs_output: Path,
    evaluator_output: Path,
    summary_json_output: Path,
    summary_markdown_output: Path,
    settlement_audit_json_output: Path | None = None,
    settlement_audit_markdown_output: Path | None = None,
    polymarket_enriched: dict[str, Any],
    kalshi_enriched: dict[str, Any],
    rebuilt_pairs: dict[str, Any] | None = None,
    pair_join_validation: dict[str, Any] | None = None,
    board: dict[str, Any],
    derived_pairs: dict[str, Any],
    evaluator: dict[str, Any],
    snapshot_universe_validation: dict[str, Any] | None = None,
    max_quote_age_seconds: float,
    max_snapshot_age_hours: float,
    max_settlement_delta_seconds: float,
    min_top_of_book_size: float,
    min_net_gap: float,
    accept_unit_mismatch: bool,
    trusted_settlement_normalizations: frozenset[str],
    source: str = "mlb_world_series_paper_check_runner",
    title: str = "MLB World Series Paper Check",
    disclaimer: str | None = None,
) -> dict[str, Any]:
    evaluator_counts = evaluator.get("counts_by_action") if isinstance(evaluator.get("counts_by_action"), dict) else {}
    top_reasons = _top_rejection_reasons(evaluator)
    quote_freshness = _paper_check_quote_freshness(evaluator, generated_at, max_quote_age_seconds)
    depth_status = _paper_check_depth_status(evaluator, polymarket_enriched, kalshi_enriched)
    blocker_drilldown = _paper_check_blocker_drilldown(
        board=board,
        derived_pairs=derived_pairs,
        evaluator=evaluator,
        trusted_settlement_normalizations=trusted_settlement_normalizations,
    )
    settlement_source_audit = _mlb_world_series_settlement_audit(
        generated_at=generated_at,
        board=board,
        evaluator=evaluator,
        trusted_settlement_normalizations=trusted_settlement_normalizations,
        json_output=settlement_audit_json_output,
        markdown_output=settlement_audit_markdown_output,
    )
    paper_candidate_ids = [
        str(row.get("candidate_id"))
        for row in evaluator.get("ledger", [])
        if isinstance(row, dict) and row.get("action") == "PAPER_CANDIDATE" and row.get("candidate_id")
    ]
    counts = {
        "PAPER_CANDIDATE": int(evaluator_counts.get("PAPER_CANDIDATE") or 0),
        "MANUAL_REVIEW": int(evaluator_counts.get("MANUAL_REVIEW") or 0),
        "WATCH": int(evaluator_counts.get("WATCH") or 0),
    }
    return {
        "schema_version": 1,
        "source": source,
        "title": title,
        "generated_at": generated_at.isoformat(),
        "inputs": {
            "polymarket_snapshot": str(polymarket_snapshot),
            "kalshi_snapshot": str(kalshi_snapshot),
            "pairs": str(pairs),
        },
        "evaluated_paths": {
            "source_polymarket_snapshot": str(polymarket_snapshot),
            "source_kalshi_snapshot": str(kalshi_snapshot),
            "pairs_evaluated": str(pairs),
            "same_payoff_board_used": str(board_json_output),
            "same_payoff_evidence_pairs_used": str(derived_pairs_output),
            "polymarket_enriched_orderbook_used": str(polymarket_enriched_output),
            "kalshi_enriched_orderbook_used": str(kalshi_enriched_output),
            "evaluator_used": str(evaluator_output),
        },
        "outputs": {
            "polymarket_enriched": str(polymarket_enriched_output),
            "kalshi_enriched": str(kalshi_enriched_output),
            "rebuilt_pairs_json": str(rebuilt_pairs_output) if rebuilt_pairs_output is not None else None,
            "rebuilt_pairs_markdown": str(rebuilt_pairs_markdown_output) if rebuilt_pairs_markdown_output is not None else None,
            "same_payoff_board_json": str(board_json_output),
            "same_payoff_board_markdown": str(board_markdown_output),
            "derived_pairs": str(derived_pairs_output),
            "evaluator": str(evaluator_output),
            "summary_json": str(summary_json_output),
            "summary_markdown": str(summary_markdown_output),
            "settlement_audit_json": str(settlement_audit_json_output) if settlement_audit_json_output else None,
            "settlement_audit_markdown": str(settlement_audit_markdown_output) if settlement_audit_markdown_output else None,
        },
        "parameters": {
            "max_snapshot_age_hours": max_snapshot_age_hours,
            "max_quote_age_seconds": max_quote_age_seconds,
            "max_settlement_delta_seconds": max_settlement_delta_seconds,
            "min_top_of_book_size": min_top_of_book_size,
            "min_net_gap": min_net_gap,
            "accept_unit_mismatch": accept_unit_mismatch,
            "trusted_settlement_normalizations": sorted(trusted_settlement_normalizations),
        },
        "polymarket_enrichment": _paper_check_enrichment_summary(polymarket_enriched),
        "kalshi_enrichment": _paper_check_enrichment_summary(kalshi_enriched),
        "rebuilt_pairs": {
            "enabled": rebuilt_pairs is not None,
            "pair_count": int((rebuilt_pairs or {}).get("pair_count") or 0),
        },
        "snapshot_universe_validation": snapshot_universe_validation or {},
        "pair_join_validation": pair_join_validation or _empty_pair_join_validation(),
        "strict_same_payoff_passes": int(board.get("strict_same_payoff_pass_count") or 0),
        "trusted_relationships": int(
            (derived_pairs.get("same_payoff_evidence_attachment") or {}).get("trusted_relationship_attached_count") or 0
        ),
        "evaluator_counts": counts,
        "paper_count": counts["PAPER_CANDIDATE"],
        "watch_manual_review_count": counts["WATCH"] + counts["MANUAL_REVIEW"],
        "killed_rejected_count": counts["WATCH"],
        "dominant_blocker": top_reasons[0]["reason"] if top_reasons else None,
        "top_rejection_reasons": top_reasons,
        "blocker_drilldown": blocker_drilldown,
        "strict_same_payoff_blocker_counts": blocker_drilldown["strict_same_payoff_blocker_counts"],
        "trusted_relationship_blocker_counts": blocker_drilldown["trusted_relationship_blocker_counts"],
        "evaluator_blocker_counts": blocker_drilldown["evaluator_blocker_counts"],
        "settlement_delta_seconds": blocker_drilldown["settlement_delta_seconds"],
        "settlement_normalization_counts": blocker_drilldown["settlement_normalization_counts"],
        "blocked_pair_examples": blocker_drilldown["blocked_pair_examples"],
        "settlement_source_audit": settlement_source_audit,
        "paper_candidate_ids": paper_candidate_ids,
        "quote_freshness": quote_freshness,
        "preflight": {
            "universe": "mlb_world_series_kxmlb" if source == "mlb_world_series_paper_check_runner" else source,
            "source_snapshot_paths": {
                "polymarket_snapshot": str(polymarket_snapshot),
                "kalshi_snapshot": str(kalshi_snapshot),
            },
            "paths_are_universe_specific": _paper_check_paths_are_universe_specific(
                universe="mlb" if source == "mlb_world_series_paper_check_runner" else None,
                paths=[
                    polymarket_snapshot,
                    kalshi_snapshot,
                    pairs,
                    board_json_output,
                    derived_pairs_output,
                    polymarket_enriched_output,
                    kalshi_enriched_output,
                    evaluator_output,
                ],
            ),
            "generic_live_readonly_warning": _paper_check_generic_live_readonly_warning(
                paths=[polymarket_snapshot, kalshi_snapshot, pairs, polymarket_enriched_output, kalshi_enriched_output],
            ),
            "pair_file_evaluated": str(pairs),
            "same_payoff_board_evidence_file_evaluated": str(board_json_output),
            "same_payoff_evidence_pairs_file_evaluated": str(derived_pairs_output),
            "enriched_orderbook_files_evaluated": {
                "polymarket": str(polymarket_enriched_output),
                "kalshi": str(kalshi_enriched_output),
            },
            "quote_freshness_status": {
                "status": "stale_or_missing" if quote_freshness["stale_quote_warning"] else "fresh_or_not_flagged",
                **quote_freshness,
            },
            "fee_model_names": {
                "polymarket": type(PolymarketConservativeFeeModel()).__name__,
                "kalshi": type(KalshiTieredFeeModel()).__name__,
            },
            "settlement_normalization_trust": {
                "requested": sorted(trusted_settlement_normalizations),
                "status": "requested" if trusted_settlement_normalizations else "absent",
                "mlb_only_allowed_value": "mlb_world_series_timezone_convention_drift",
            },
            "top_of_book_depth_status": depth_status,
            "paper_count": counts["PAPER_CANDIDATE"],
            "watch_manual_review_count": counts["WATCH"] + counts["MANUAL_REVIEW"],
            "rejected_count": counts["WATCH"],
            "top_blockers": [row["reason"] for row in top_reasons[:5]],
            "phase_diagnosis": blocker_drilldown["phase_diagnosis"],
        },
        "safety": {
            "explicit_saved_snapshot_inputs_required": True,
            "original_inputs_mutated": False,
            "trading_or_execution_performed": False,
            "balances_or_positions_accessed": False,
            "secrets_used": False,
            "thresholds_or_relationship_gates_lowered": False,
            "default_scan_mode_changed": False,
        },
        "disclaimer": disclaimer or (
            "Read-only MLB World Series paper-check diagnostics. This runner enriches saved snapshots, "
            "attaches deterministic same-payoff evidence, evaluates existing paper gates, and stops at "
            "STOP_AND_REVIEW when PAPER_CANDIDATE appears. It does not trade or execute."
        ),
    }


def _paper_check_blocker_drilldown(
    *,
    board: dict[str, Any],
    derived_pairs: dict[str, Any],
    evaluator: dict[str, Any],
    trusted_settlement_normalizations: frozenset[str],
) -> dict[str, Any]:
    board_rows = [row for row in board.get("rows", []) if isinstance(row, dict)]
    evaluator_rows = [row for row in evaluator.get("ledger", []) if isinstance(row, dict)]
    strict_counts = Counter(
        str(blocker)
        for row in board_rows
        for blocker in row.get("strict_blockers", [])
        if blocker
    )
    trusted_counts = _trusted_relationship_blocker_counts(derived_pairs, board_rows)
    evaluator_counts = Counter()
    settlement_deltas: list[float] = []
    for row in evaluator_rows:
        missed = row.get("missed_fill_reason")
        if missed:
            evaluator_counts[f"missed_fill:{missed}"] += 1
        reasons = row.get("ineligibility_reasons") if isinstance(row.get("ineligibility_reasons"), list) else []
        for reason in reasons:
            if reason:
                evaluator_counts[str(reason)] += 1
        gap = row.get("gap") if isinstance(row.get("gap"), dict) else {}
        delta = float_or_none(gap.get("settlement_delta_seconds"))
        if delta is not None:
            settlement_deltas.append(float(delta))
    normalization_counts = _settlement_normalization_counts(
        derived_pairs=derived_pairs,
        evaluator_rows=evaluator_rows,
        trusted_settlement_normalizations=trusted_settlement_normalizations,
    )
    return {
        "strict_same_payoff_blocker_counts": _counter_dict(strict_counts),
        "trusted_relationship_blocker_counts": _counter_dict(trusted_counts),
        "evaluator_blocker_counts": _counter_dict(evaluator_counts),
        "settlement_delta_seconds": {
            "count": len(settlement_deltas),
            "min": round(min(settlement_deltas), 6) if settlement_deltas else None,
            "max": round(max(settlement_deltas), 6) if settlement_deltas else None,
        },
        "settlement_normalization_counts": normalization_counts,
        "phase_diagnosis": _paper_check_phase_diagnosis(
            board=board,
            derived_pairs=derived_pairs,
            evaluator=evaluator,
            strict_counts=strict_counts,
            trusted_counts=trusted_counts,
            evaluator_counts=evaluator_counts,
        ),
        "blocked_pair_examples": _blocked_pair_examples(board_rows, evaluator_rows),
    }


def _mlb_world_series_settlement_audit(
    *,
    generated_at: datetime,
    board: dict[str, Any],
    evaluator: dict[str, Any],
    trusted_settlement_normalizations: frozenset[str],
    json_output: Path | None,
    markdown_output: Path | None,
    limit: int = 5,
) -> dict[str, Any]:
    board_rows = [row for row in board.get("rows", []) if isinstance(row, dict)]
    evaluator_by_id = {
        _paper_check_ledger_identity(row): row
        for row in evaluator.get("ledger", [])
        if isinstance(row, dict) and _paper_check_ledger_identity(row)
    }
    audited_rows = []
    for row in board_rows:
        blockers = [str(blocker) for blocker in row.get("strict_blockers") or row.get("blockers") or []]
        missing = [str(field) for field in row.get("strict_missing_fields") or row.get("missing_fields") or []]
        if not any("settlement" in value for value in [*blockers, *missing]):
            continue
        evaluator_row = evaluator_by_id.get(_paper_check_board_identity(row), {})
        audited_rows.append(_mlb_settlement_audit_row(row, evaluator_row, trusted_settlement_normalizations))
    audited_rows = audited_rows[:limit]
    counts = Counter(row["classification"] for row in audited_rows)
    return {
        "schema_version": 1,
        "source": "mlb_world_series_settlement_source_audit_v1",
        "generated_at": generated_at.isoformat(),
        "diagnostic_only": True,
        "outputs": {
            "json": str(json_output) if json_output else None,
            "markdown": str(markdown_output) if markdown_output else None,
        },
        "summary": {
            "audited_pairs": len(audited_rows),
            "source_mismatch_count": counts.get("real_source_mismatch", 0),
            "time_mismatch_count": counts.get("real_time_mismatch", 0) + counts.get("unsupported_normalization_case", 0),
            "parser_missing_count": counts.get("parser_missing_data", 0),
            "unknown_count": counts.get("unknown", 0),
            "classification_counts": dict(sorted(counts.items())),
        },
        "rows": audited_rows,
        "safety": {
            "reporting_only": True,
            "does_not_emit_trusted_evidence": True,
            "does_not_change_same_payoff_board": True,
            "does_not_emit_paper_candidate": True,
            "settlement_trust_expanded": False,
            "trading_or_execution_performed": False,
        },
        "disclaimer": (
            "Reporting-only MLB settlement/source audit. This does not alter same-payoff board decisions, "
            "does not attach trusted evidence, and does not emit PAPER_CANDIDATE."
        ),
    }


def _mlb_settlement_audit_row(
    board_row: dict[str, Any],
    evaluator_row: dict[str, Any],
    trusted_settlement_normalizations: frozenset[str],
) -> dict[str, Any]:
    poly = board_row.get("polymarket") if isinstance(board_row.get("polymarket"), dict) else {}
    kalshi = board_row.get("kalshi") if isinstance(board_row.get("kalshi"), dict) else {}
    evidence = board_row.get("same_payoff_evidence") if isinstance(board_row.get("same_payoff_evidence"), dict) else {}
    source_cmp = evidence.get("settlement_source") if isinstance(evidence.get("settlement_source"), dict) else {}
    time_cmp = evidence.get("settlement_time") if isinstance(evidence.get("settlement_time"), dict) else {}
    source_values = source_cmp.get("values") if isinstance(source_cmp.get("values"), dict) else {}
    time_values = time_cmp.get("values") if isinstance(time_cmp.get("values"), dict) else {}
    gap = evaluator_row.get("gap") if isinstance(evaluator_row.get("gap"), dict) else {}
    settlement_delta = gap.get("settlement_delta_seconds")
    if settlement_delta is None:
        settlement_delta = time_values.get("delta_seconds")
    normalization = time_values.get("normalization")
    normalized_requested = normalization in trusted_settlement_normalizations if normalization else False
    strict_blockers = [str(blocker) for blocker in board_row.get("strict_blockers") or []]
    classification = _mlb_settlement_audit_classification(
        strict_blockers=strict_blockers,
        source_cmp=source_cmp,
        time_cmp=time_cmp,
        evaluator_row=evaluator_row,
        normalization=normalization,
        normalized_requested=normalized_requested,
    )
    return {
        "candidate_id": f"{poly.get('market_id') or ''}__{kalshi.get('ticker') or kalshi.get('market_id') or ''}",
        "polymarket": {
            "market_id": poly.get("market_id"),
            "question": poly.get("question"),
            "title": poly.get("title") or poly.get("event_title"),
        },
        "kalshi": {
            "ticker": kalshi.get("ticker") or kalshi.get("market_id"),
            "title": kalshi.get("title") or kalshi.get("question") or kalshi.get("event_title"),
        },
        "parsed_team_entity": {
            "polymarket": _audit_team_id(poly, evidence, "polymarket"),
            "kalshi": _audit_team_id(kalshi, evidence, "kalshi"),
        },
        "parsed_settlement_source_fields": {
            "polymarket": source_values.get("polymarket"),
            "kalshi": source_values.get("kalshi"),
            "source_status": source_cmp.get("status"),
        },
        "parsed_settlement_timestamps": {
            "polymarket": _audit_time_value(time_values, "polymarket"),
            "kalshi": _audit_time_value(time_values, "kalshi"),
            "time_status": time_cmp.get("status"),
        },
        "settlement_delta_seconds": settlement_delta,
        "normalization": {
            "requested": sorted(trusted_settlement_normalizations),
            "board_normalization": normalization,
            "requested_for_pair": normalized_requested,
            "accepted": _audit_normalization_accepted(evaluator_row, normalization, normalized_requested),
            "rejected_reason": _audit_normalization_rejected_reason(evaluator_row, normalization, normalized_requested),
        },
        "strict_blockers": strict_blockers,
        "classification": classification,
    }


def _mlb_settlement_audit_classification(
    *,
    strict_blockers: list[str],
    source_cmp: dict[str, Any],
    time_cmp: dict[str, Any],
    evaluator_row: dict[str, Any],
    normalization: Any,
    normalized_requested: bool,
) -> str:
    if source_cmp.get("status") == "MISSING" or time_cmp.get("status") == "MISSING":
        return "parser_missing_data"
    if "settlement_source_mismatch" in strict_blockers or source_cmp.get("status") == "FAIL":
        return "real_source_mismatch"
    if evaluator_row.get("missed_fill_reason") == "settlement_delta_exceeds_limit":
        if normalization and not normalized_requested:
            return "unsupported_normalization_case"
        return "real_time_mismatch"
    if (
        "settlement_date_drift" in strict_blockers
        or any("settlement_time" in blocker or "settlement_delta" in blocker for blocker in strict_blockers)
        or time_cmp.get("status") == "FAIL"
    ):
        return "real_time_mismatch"
    return "unknown"


def _audit_team_id(market: dict[str, Any], evidence: dict[str, Any], venue: str) -> Any:
    for key in ("market_event_entity", "sports_league_team"):
        comparator = evidence.get(key) if isinstance(evidence.get(key), dict) else {}
        values = comparator.get("values") if isinstance(comparator.get("values"), dict) else {}
        profile = values.get(venue) if isinstance(values.get(venue), dict) else {}
        if profile.get("team_id"):
            return profile.get("team_id")
    text = " ".join(str(market.get(key) or "") for key in ("question", "title", "event_title", "market_id", "ticker"))
    match = re.search(r"\b([A-Z]{2,3})\b", text)
    return match.group(1) if match else None


def _audit_time_value(values: dict[str, Any], venue: str) -> Any:
    side = values.get(venue) if isinstance(values.get(venue), dict) else {}
    return side.get("end_date") or side.get("close_time") or side.get("settlement_time") or values.get(venue)


def _audit_normalization_accepted(evaluator_row: dict[str, Any], normalization: Any, requested: bool) -> bool:
    return bool(normalization and requested and evaluator_row.get("missed_fill_reason") != "settlement_delta_exceeds_limit")


def _audit_normalization_rejected_reason(evaluator_row: dict[str, Any], normalization: Any, requested: bool) -> str | None:
    if not normalization:
        return "no_board_settlement_time_normalization"
    if not requested:
        return "normalization_not_requested"
    if evaluator_row.get("missed_fill_reason") == "settlement_delta_exceeds_limit":
        return "evaluator_settlement_delta_exceeds_limit"
    return None


def _paper_check_board_identity(row: dict[str, Any]) -> str:
    poly = row.get("polymarket") if isinstance(row.get("polymarket"), dict) else {}
    kalshi = row.get("kalshi") if isinstance(row.get("kalshi"), dict) else {}
    return f"{poly.get('market_id') or ''}__{kalshi.get('ticker') or kalshi.get('market_id') or ''}"


def _paper_check_ledger_identity(row: dict[str, Any]) -> str:
    poly = row.get("polymarket") if isinstance(row.get("polymarket"), dict) else {}
    kalshi = row.get("kalshi") if isinstance(row.get("kalshi"), dict) else {}
    return f"{poly.get('market_id') or ''}__{kalshi.get('ticker') or ''}"


def _trusted_relationship_blocker_counts(derived_pairs: dict[str, Any], board_rows: list[dict[str, Any]]) -> Counter:
    counts: Counter = Counter()
    attachment = derived_pairs.get("same_payoff_evidence_attachment")
    if isinstance(attachment, dict):
        if int(attachment.get("unmatched_pair_count") or 0):
            counts["evidence_unmatched_pair"] += int(attachment.get("unmatched_pair_count") or 0)
        if int(attachment.get("ambiguous_identity_count") or 0):
            counts["evidence_ambiguous_identity"] += int(attachment.get("ambiguous_identity_count") or 0)
    if board_rows:
        for row in board_rows:
            if row.get("same_payoff") is True:
                continue
            for blocker in row.get("strict_blockers", []) or ["strict_same_payoff_not_passed"]:
                counts[str(blocker)] += 1
    pairs = derived_pairs.get("pairs") if isinstance(derived_pairs.get("pairs"), list) else []
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        relationship = pair.get("contract_relationship")
        if not isinstance(relationship, dict):
            counts["missing_trusted_contract_relationship"] += 1
            continue
        if relationship.get("source") != "same_payoff_board_v1":
            counts["relationship_source_not_same_payoff_board_v1"] += 1
        if relationship.get("relationship") != "EQUIVALENT" or relationship.get("same_payoff") is not True:
            counts["relationship_not_trusted_equivalent"] += 1
        for blocker in relationship.get("blocking_reasons") or []:
            counts[str(blocker)] += 1
    return counts


def _settlement_normalization_counts(
    *,
    derived_pairs: dict[str, Any],
    evaluator_rows: list[dict[str, Any]],
    trusted_settlement_normalizations: frozenset[str],
) -> dict[str, Any]:
    requested = sorted(trusted_settlement_normalizations)
    evidence_normalization_count = 0
    requested_evidence_count = 0
    rejected_by_evaluator = 0
    pairs = derived_pairs.get("pairs") if isinstance(derived_pairs.get("pairs"), list) else []
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        relationship = pair.get("contract_relationship") if isinstance(pair.get("contract_relationship"), dict) else {}
        evidence = relationship.get("same_payoff_board_evidence") if isinstance(relationship.get("same_payoff_board_evidence"), dict) else {}
        normalization = evidence.get("settlement_time_normalization")
        if normalization:
            evidence_normalization_count += 1
            if normalization in trusted_settlement_normalizations:
                requested_evidence_count += 1
    for row in evaluator_rows:
        if row.get("missed_fill_reason") == "settlement_delta_exceeds_limit":
            rejected_by_evaluator += 1
    return {
        "requested": requested,
        "evidence_normalization_count": evidence_normalization_count,
        "requested_evidence_count": requested_evidence_count,
        "accepted_by_evaluator_count": max(requested_evidence_count - rejected_by_evaluator, 0),
        "rejected_by_evaluator_count": rejected_by_evaluator,
    }


def _paper_check_phase_diagnosis(
    *,
    board: dict[str, Any],
    derived_pairs: dict[str, Any],
    evaluator: dict[str, Any],
    strict_counts: Counter,
    trusted_counts: Counter,
    evaluator_counts: Counter,
) -> dict[str, Any]:
    strict_passes = int(board.get("strict_same_payoff_pass_count") or 0)
    attachment = derived_pairs.get("same_payoff_evidence_attachment") if isinstance(derived_pairs.get("same_payoff_evidence_attachment"), dict) else {}
    trusted = int(attachment.get("trusted_relationship_attached_count") or 0)
    evaluator_rows = len([row for row in evaluator.get("ledger", []) if isinstance(row, dict)])
    return {
        "same_payoff_board_failed_before_evidence_attachment": strict_passes == 0 and bool(strict_counts),
        "evidence_attachment_failed": strict_passes > 0 and trusted == 0 and bool(trusted_counts),
        "evaluator_rejected_trusted_relationships": trusted > 0 and bool(evaluator_counts),
        "orderbook_execution_failed": any(
            key
            for key in evaluator_counts
            if "depth" in key or "quote" in key or "gap" in key or "settlement_delta" in key
        ),
        "evaluator_rows": evaluator_rows,
    }


def _blocked_pair_examples(board_rows: list[dict[str, Any]], evaluator_rows: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    board_by_id = {}
    for row in board_rows:
        poly = row.get("polymarket") if isinstance(row.get("polymarket"), dict) else {}
        kalshi = row.get("kalshi") if isinstance(row.get("kalshi"), dict) else {}
        key = (str(poly.get("market_id") or ""), str(kalshi.get("ticker") or ""))
        board_by_id[key] = row
    examples = []
    for index, row in enumerate(evaluator_rows):
        if not isinstance(row, dict):
            continue
        poly = row.get("polymarket") if isinstance(row.get("polymarket"), dict) else {}
        kalshi = row.get("kalshi") if isinstance(row.get("kalshi"), dict) else {}
        key = (str(poly.get("market_id") or ""), str(kalshi.get("ticker") or ""))
        board_row = board_by_id.get(key, {})
        if not board_row and index < len(board_rows):
            board_row = board_rows[index]
            board_poly = board_row.get("polymarket") if isinstance(board_row.get("polymarket"), dict) else {}
            board_kalshi = board_row.get("kalshi") if isinstance(board_row.get("kalshi"), dict) else {}
            poly = {**board_poly, **poly}
            kalshi = {**board_kalshi, **kalshi}
        blockers = []
        blockers.extend(str(reason) for reason in row.get("ineligibility_reasons") or [] if reason)
        if row.get("missed_fill_reason"):
            blockers.append(f"missed_fill:{row.get('missed_fill_reason')}")
        blockers.extend(str(blocker) for blocker in board_row.get("strict_blockers", []) if blocker)
        examples.append(
            {
                "candidate_id": row.get("candidate_id"),
                "polymarket_market_id": poly.get("market_id"),
                "polymarket_question": poly.get("question"),
                "kalshi_ticker": kalshi.get("ticker"),
                "kalshi_question": kalshi.get("question"),
                "settlement_delta_seconds": (row.get("gap") or {}).get("settlement_delta_seconds") if isinstance(row.get("gap"), dict) else None,
                "blockers": sorted(set(blockers)),
            }
        )
        if len(examples) >= limit:
            break
    if examples:
        return examples
    for row in board_rows[:limit]:
        poly = row.get("polymarket") if isinstance(row.get("polymarket"), dict) else {}
        kalshi = row.get("kalshi") if isinstance(row.get("kalshi"), dict) else {}
        examples.append(
            {
                "candidate_id": f"{poly.get('market_id')}__{kalshi.get('ticker')}",
                "polymarket_market_id": poly.get("market_id"),
                "polymarket_question": poly.get("question"),
                "kalshi_ticker": kalshi.get("ticker"),
                "kalshi_question": kalshi.get("question"),
                "settlement_delta_seconds": _board_settlement_delta(row),
                "blockers": row.get("strict_blockers") or row.get("blockers") or [],
            }
        )
    return examples


def _board_settlement_delta(row: dict[str, Any]) -> Any:
    evidence = row.get("same_payoff_evidence") if isinstance(row.get("same_payoff_evidence"), dict) else {}
    settlement = evidence.get("settlement_time") if isinstance(evidence.get("settlement_time"), dict) else {}
    values = settlement.get("values") if isinstance(settlement.get("values"), dict) else {}
    return values.get("delta_seconds")


def _counter_dict(counter: Counter) -> dict[str, int]:
    return {str(key): int(value) for key, value in sorted(counter.items(), key=lambda item: (-item[1], str(item[0])))}


def _paper_check_paths_are_universe_specific(*, universe: str | None, paths: list[Path]) -> bool:
    warning = _paper_check_generic_live_readonly_warning(paths=paths)
    if warning:
        return False
    if universe is None:
        return True
    expected = f"reports/live_readonly/{universe}"
    for path in paths:
        normalized = path.as_posix().lower()
        if "reports/live_readonly/" in normalized and expected not in normalized:
            return False
    return True


def _paper_check_generic_live_readonly_warning(*, paths: list[Path]) -> str | None:
    generic = []
    for path in paths:
        normalized = path.as_posix().lower()
        if "reports/live_readonly/" not in normalized:
            continue
        parts = [part.lower() for part in path.parts]
        try:
            index = parts.index("live_readonly")
        except ValueError:
            continue
        child = parts[index + 1] if index + 1 < len(parts) else ""
        if child.endswith(".json") or child.endswith(".md") or not child:
            generic.append(str(path))
    if not generic:
        return None
    return "GENERIC_LIVE_READONLY_PATH_USED:" + ",".join(generic)


def _paper_check_depth_status(
    evaluator: dict[str, Any],
    polymarket_enriched: dict[str, Any],
    kalshi_enriched: dict[str, Any],
) -> dict[str, Any]:
    ledger = evaluator.get("ledger") if isinstance(evaluator.get("ledger"), list) else []
    ledger_status = {
        "polymarket": _paper_check_ledger_depth_status(ledger, "polymarket"),
        "kalshi": _paper_check_ledger_depth_status(ledger, "kalshi"),
    }
    return {
        "polymarket": ledger_status["polymarket"] if ledger_status["polymarket"]["status"] != "missing" else _paper_check_snapshot_depth_status(polymarket_enriched),
        "kalshi": ledger_status["kalshi"] if ledger_status["kalshi"]["status"] != "missing" else _paper_check_snapshot_depth_status(kalshi_enriched),
    }


def _paper_check_ledger_depth_status(ledger: list[Any], venue: str) -> dict[str, Any]:
    rows_with_depth = 0
    for row in ledger:
        if not isinstance(row, dict):
            continue
        venue_row = row.get(venue) if isinstance(row.get(venue), dict) else {}
        if venue_row.get("depth_at_best_bid") is not None and venue_row.get("depth_at_best_ask") is not None:
            rows_with_depth += 1
    if rows_with_depth:
        return {"status": "available", "rows_with_top_of_book_depth": rows_with_depth}
    return {"status": "missing", "rows_with_top_of_book_depth": 0}


def _paper_check_snapshot_depth_status(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("normalized_markets") if isinstance(payload.get("normalized_markets"), list) else []
    rows_with_depth = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        enrichment = row.get("orderbook_enrichment") if isinstance(row.get("orderbook_enrichment"), dict) else row
        if enrichment.get("depth_at_best_bid") is not None and enrichment.get("depth_at_best_ask") is not None:
            rows_with_depth += 1
    if rows_with_depth:
        return {"status": "available", "rows_with_top_of_book_depth": rows_with_depth}
    summary = payload.get("orderbook_enrichment") if isinstance(payload.get("orderbook_enrichment"), dict) else {}
    if int(summary.get("enriched_count") or 0) > 0:
        return {"status": "enriched_summary_available", "rows_with_top_of_book_depth": 0}
    return {"status": "missing", "rows_with_top_of_book_depth": 0}


def _paper_check_enrichment_summary(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("orderbook_enrichment") if isinstance(payload.get("orderbook_enrichment"), dict) else {}
    return {
        "market_count": int(summary.get("market_count") or 0),
        "enriched_count": int(summary.get("enriched_count") or 0),
        "unenriched_count": int(summary.get("unenriched_count") or 0),
        "fresh_orderbook_fetch_enriched_count": int(summary.get("fresh_orderbook_fetch_enriched_count") or summary.get("enriched_count") or 0),
        "existing_top_of_book_present_count": int(summary.get("existing_top_of_book_present_count") or 0),
        "full_orderbook_missing_count": int(summary.get("full_orderbook_missing_count") or summary.get("unenriched_count") or 0),
        "fetch_failed_count": int(summary.get("fetch_failed_count") or 0),
        "stale_existing_top_of_book_count": int(summary.get("stale_existing_top_of_book_count") or 0),
        "snapshot_warnings": summary.get("snapshot_warnings") if isinstance(summary.get("snapshot_warnings"), list) else [],
    }


def _paper_check_quote_freshness(
    evaluator: dict[str, Any],
    generated_at: datetime,
    max_quote_age_seconds: float,
) -> dict[str, Any]:
    ages: list[float] = []
    stale_rows = 0
    for row in evaluator.get("ledger", []):
        if not isinstance(row, dict):
            continue
        reasons = row.get("ineligibility_reasons") if isinstance(row.get("ineligibility_reasons"), list) else []
        missed = str(row.get("missed_fill_reason") or "")
        if "stale" in missed or any("stale_quote" in str(reason) for reason in reasons):
            stale_rows += 1
        for venue in ("polymarket", "kalshi"):
            quote = row.get(venue) if isinstance(row.get(venue), dict) else {}
            raw_captured = quote.get("quote_captured_at")
            captured = _parse_datetime_or_none(raw_captured) if isinstance(raw_captured, str) else None
            if captured is not None:
                ages.append((generated_at - captured).total_seconds())
    max_age = max(ages) if ages else None
    return {
        "quote_count": len(ages),
        "max_quote_age_seconds": round(max_age, 6) if max_age is not None else None,
        "limit_seconds": max_quote_age_seconds,
        "stale_quote_row_count": stale_rows,
        "stale_quote_warning": bool(stale_rows or (max_age is not None and max_age >= max_quote_age_seconds)),
    }


def _mlb_world_series_paper_check_markdown(payload: dict[str, Any]) -> str:
    counts = payload["evaluator_counts"]
    freshness = payload["quote_freshness"]
    preflight = payload.get("preflight") if isinstance(payload.get("preflight"), dict) else {}
    lines = [
        f"# {payload.get('title') or 'Paper Check'}",
        "",
        payload["disclaimer"],
        "",
        "## Summary",
        "",
        f"- Polymarket enriched: `{payload['polymarket_enrichment']['enriched_count']}/{payload['polymarket_enrichment']['market_count']}`",
        f"- Kalshi enriched: `{payload['kalshi_enrichment']['enriched_count']}/{payload['kalshi_enrichment']['market_count']}`",
        f"- Strict same-payoff passes: `{payload['strict_same_payoff_passes']}`",
        f"- Trusted relationships: `{payload['trusted_relationships']}`",
        f"- Evaluator counts: `PAPER_CANDIDATE={counts['PAPER_CANDIDATE']} MANUAL_REVIEW={counts['MANUAL_REVIEW']} WATCH={counts['WATCH']}`",
        f"- Paper count: `{payload.get('paper_count', counts['PAPER_CANDIDATE'])}`",
        f"- Watch/manual-review count: `{payload.get('watch_manual_review_count', counts['WATCH'] + counts['MANUAL_REVIEW'])}`",
        f"- Killed/rejected count: `{payload.get('killed_rejected_count', counts['WATCH'])}`",
        f"- Dominant blocker: `{payload['dominant_blocker'] or 'none'}`",
        f"- Max quote age seconds: `{freshness['max_quote_age_seconds']}`",
        f"- Stale quote warning: `{freshness['stale_quote_warning']}`",
        "",
        "## Paper Candidate IDs",
        "",
    ]
    ids = payload.get("paper_candidate_ids") or []
    lines.append(", ".join(ids) if ids else "none")
    if counts["PAPER_CANDIDATE"] > 0:
        lines.extend(["", "## STOP_FOR_REVIEW", "", "Paper candidates are present in the evaluated report. Review only; no trading or execution is performed."])
    lines.extend(
        [
            "",
            "## Operator Preflight",
            "",
            f"- Universe: `{preflight.get('universe')}`",
            f"- Universe-specific paths: `{preflight.get('paths_are_universe_specific')}`",
            f"- Generic live_readonly warning: `{preflight.get('generic_live_readonly_warning') or 'none'}`",
            f"- Pair file evaluated: `{preflight.get('pair_file_evaluated')}`",
            f"- Same-payoff board/evidence file evaluated: `{preflight.get('same_payoff_board_evidence_file_evaluated')}`",
            f"- Polymarket enriched orderbook: `{(preflight.get('enriched_orderbook_files_evaluated') or {}).get('polymarket')}`",
            f"- Kalshi enriched orderbook: `{(preflight.get('enriched_orderbook_files_evaluated') or {}).get('kalshi')}`",
            f"- Quote freshness status: `{(preflight.get('quote_freshness_status') or {}).get('status')}`",
            f"- Fee models: Polymarket `{(preflight.get('fee_model_names') or {}).get('polymarket')}`, Kalshi `{(preflight.get('fee_model_names') or {}).get('kalshi')}`",
            f"- Settlement normalization trust: `{(preflight.get('settlement_normalization_trust') or {}).get('status')}`",
            f"- Depth/top-of-book: Polymarket `{((preflight.get('top_of_book_depth_status') or {}).get('polymarket') or {}).get('status')}`, Kalshi `{((preflight.get('top_of_book_depth_status') or {}).get('kalshi') or {}).get('status')}`",
            f"- Counts: paper `{preflight.get('paper_count')}`, watch/manual_review `{preflight.get('watch_manual_review_count')}`, rejected `{preflight.get('rejected_count')}`",
            f"- Top blockers: `{','.join(preflight.get('top_blockers') or []) or 'none'}`",
        ]
    )
    drilldown = payload.get("blocker_drilldown") if isinstance(payload.get("blocker_drilldown"), dict) else {}
    phase = drilldown.get("phase_diagnosis") if isinstance(drilldown.get("phase_diagnosis"), dict) else {}
    settlement = drilldown.get("settlement_delta_seconds") if isinstance(drilldown.get("settlement_delta_seconds"), dict) else {}
    normalization = drilldown.get("settlement_normalization_counts") if isinstance(drilldown.get("settlement_normalization_counts"), dict) else {}
    lines.extend(
        [
            "",
            "## Blocker Drilldown",
            "",
            f"- Same-payoff board failed before evidence attachment: `{phase.get('same_payoff_board_failed_before_evidence_attachment')}`",
            f"- Evidence attachment failed: `{phase.get('evidence_attachment_failed')}`",
            f"- Evaluator rejected trusted relationships: `{phase.get('evaluator_rejected_trusted_relationships')}`",
            f"- Orderbook execution failed: `{phase.get('orderbook_execution_failed')}`",
            f"- Strict same-payoff blockers: `{payload.get('strict_same_payoff_blocker_counts')}`",
            f"- Trusted relationship blockers: `{payload.get('trusted_relationship_blocker_counts')}`",
            f"- Evaluator blockers: `{payload.get('evaluator_blocker_counts')}`",
            f"- Settlement delta seconds: min `{settlement.get('min')}`, max `{settlement.get('max')}`",
            f"- Settlement normalization: requested `{normalization.get('requested')}`, accepted `{normalization.get('accepted_by_evaluator_count')}`, rejected `{normalization.get('rejected_by_evaluator_count')}`",
            "",
            "### Blocked Pair Examples",
            "",
        ]
    )
    for example in payload.get("blocked_pair_examples") or []:
        if not isinstance(example, dict):
            continue
        lines.append(
            f"- `{example.get('candidate_id')}` poly=`{example.get('polymarket_market_id')}` "
            f"kalshi=`{example.get('kalshi_ticker')}` settlement_delta=`{example.get('settlement_delta_seconds')}` "
            f"blockers=`{','.join(example.get('blockers') or []) or 'none'}`"
        )
    audit = payload.get("settlement_source_audit") if isinstance(payload.get("settlement_source_audit"), dict) else {}
    audit_summary = audit.get("summary") if isinstance(audit.get("summary"), dict) else {}
    lines.extend(
        [
            "",
            "## Settlement Source Audit",
            "",
            f"- Audited pairs: `{audit_summary.get('audited_pairs')}`",
            f"- Source mismatch count: `{audit_summary.get('source_mismatch_count')}`",
            f"- Time mismatch count: `{audit_summary.get('time_mismatch_count')}`",
            f"- Parser missing count: `{audit_summary.get('parser_missing_count')}`",
            f"- Unknown count: `{audit_summary.get('unknown_count')}`",
        ]
    )
    lines.extend(["", "## Evaluated Paths", ""])
    for key, value in payload.get("evaluated_paths", {}).items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Outputs", ""])
    for key, value in payload["outputs"].items():
        lines.append(f"- {key}: `{value}`")
    lines.append("")
    return "\n".join(lines)


def _mlb_world_series_settlement_audit_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    lines = [
        "# MLB World Series Settlement Audit",
        "",
        str(payload.get("disclaimer") or ""),
        "",
        "## Summary",
        "",
        f"- Audited pairs: `{summary.get('audited_pairs')}`",
        f"- Source mismatch count: `{summary.get('source_mismatch_count')}`",
        f"- Time mismatch count: `{summary.get('time_mismatch_count')}`",
        f"- Parser missing count: `{summary.get('parser_missing_count')}`",
        f"- Unknown count: `{summary.get('unknown_count')}`",
        "",
        "| Classification | Polymarket | Kalshi | Delta seconds | Normalization | Strict blockers |",
        "|---|---|---|---:|---|---|",
    ]
    for row in payload.get("rows") or []:
        if not isinstance(row, dict):
            continue
        normalization = row.get("normalization") if isinstance(row.get("normalization"), dict) else {}
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(row.get("classification")),
                    _markdown_cell((row.get("polymarket") or {}).get("market_id")),
                    _markdown_cell((row.get("kalshi") or {}).get("ticker")),
                    _markdown_cell(row.get("settlement_delta_seconds")),
                    _markdown_cell(normalization.get("board_normalization") or normalization.get("rejected_reason")),
                    _markdown_cell(",".join(row.get("strict_blockers") or []) or "none"),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def discover_exact_paper_candidate_universes(*, json_output: Path, markdown_output: Path) -> int:
    try:
        payload = build_exact_paper_candidate_universe_report_files(
            project_root=PROJECT_ROOT,
            json_output_path=json_output,
            markdown_output_path=markdown_output,
        )
        expansion_payload = write_exact_market_expansion_plan_files(
            project_root=PROJECT_ROOT,
            readiness_payload=payload,
        )
        platform_payload = write_platform_expansion_matrix_files(
            project_root=PROJECT_ROOT,
        )
    except ValueError as exc:
        print(f"exact_paper_candidate_universes_status=FAILED message={exc}")
        return 1

    summary = payload["summary"]
    counts = summary["readiness_counts"]
    closest = summary.get("closest_universe_id") or "none"
    print(
        "exact_paper_candidate_universes_status=OK "
        f"universes={summary['universe_count']} "
        f"closest={closest} "
        f"next_strict={summary.get('next_universe_by_strict_criteria') or 'none'} "
        f"paper_candidates={summary['paper_candidate_count']} "
        f"execution_data={counts.get('EXECUTION_DATA_AVAILABLE', 0)} "
        f"trusted_relationships={counts.get('TRUSTED_RELATIONSHIPS_AVAILABLE', 0)} "
        f"same_scope_pairs={counts.get('SAME_SCOPE_PAIRS_AVAILABLE', 0)} "
        f"inventory_only={counts.get('INVENTORY_ONLY', 0)} "
        f"no_inventory={counts.get('NO_INVENTORY', 0)} "
        f"json={json_output} markdown={markdown_output} "
        "expansion_json=reports\\exact_market_expansion_plan.json "
        "expansion_markdown=reports\\exact_market_expansion_plan.md "
        "platform_json=reports\\platform_expansion_matrix.json "
        "platform_markdown=reports\\platform_expansion_matrix.md"
    )
    print(_exact_universe_readiness_table(payload))
    print(_exact_market_expansion_plan_table(expansion_payload))
    print(_platform_expansion_matrix_table(platform_payload))
    return 0


def _exact_universe_readiness_table(payload: dict[str, Any]) -> str:
    rows = payload.get("universes") if isinstance(payload.get("universes"), list) else []
    lines = [
        "universe | inventory | universe_paths | pairs | strict | trusted | fresh_ob | evaluator | paper | review | top_fail_closed",
        "--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---",
    ]
    for row in rows:
        if not isinstance(row, dict):
            continue
        reasons = row.get("top_fail_closed_reasons") if isinstance(row.get("top_fail_closed_reasons"), list) else []
        lines.append(
            " | ".join(
                [
                    _safe_cli_text(str(row.get("universe_id") or "")),
                    str(row.get("inventory_available") is True).lower(),
                    str(((row.get("preflight") or {}).get("paths_are_universe_specific")) is True).lower(),
                    str(row.get("same_scope_pair_count") or 0),
                    str(row.get("strict_same_payoff_passes") or row.get("strict_same_payoff_pass_count") or 0),
                    str(row.get("trusted_relationships_attached") or row.get("trusted_relationship_count") or 0),
                    str(row.get("fresh_orderbook_enrichment_available") is True).lower(),
                    str(row.get("evaluator_ready") is True).lower(),
                    str(row.get("paper_candidates_count") or 0),
                    _safe_cli_text(str(row.get("paper_review_notice") or "none")),
                    _safe_cli_text(",".join(str(reason) for reason in reasons) or "none"),
                ]
            )
        )
    return "\n".join(lines)


def _exact_market_expansion_plan_table(payload: dict[str, Any]) -> str:
    rows = payload.get("families") if isinstance(payload.get("families"), list) else []
    lines = [
        "expansion_family | inventory | typed | exact_groups | cross_venue | paperability | top_blockers",
        "--- | ---: | ---: | ---: | ---: | --- | ---",
    ]
    for row in rows:
        if not isinstance(row, dict):
            continue
        blockers = row.get("top_blockers") if isinstance(row.get("top_blockers"), list) else []
        lines.append(
            " | ".join(
                [
                    _safe_cli_text(str(row.get("family") or "")),
                    str(row.get("current_saved_inventory_count") or 0),
                    str(row.get("typed_formula_count") or 0),
                    str(row.get("exact_group_count") or 0),
                    str(row.get("cross_venue_exact_group_count") or 0),
                    _safe_cli_text(str(row.get("paperability_status") or "NOT_EXACT_PIPELINE")),
                    _safe_cli_text(",".join(str(item.get("blocker")) for item in blockers if isinstance(item, dict)) or "none"),
                ]
            )
        )
    return "\n".join(lines)


def _platform_expansion_matrix_table(payload: dict[str, Any]) -> str:
    rows = payload.get("venues") if isinstance(payload.get("venues"), list) else []
    lines = [
        "platform | orderbook | fee_model | settlement_metadata | reference_only | paperability | blockers",
        "--- | ---: | --- | --- | ---: | --- | ---",
    ]
    for row in rows:
        if not isinstance(row, dict):
            continue
        blockers = row.get("blockers") if isinstance(row.get("blockers"), list) else []
        lines.append(
            " | ".join(
                [
                    _safe_cli_text(str(row.get("venue_id") or "")),
                    str(row.get("executable_orderbook_available") is True).lower(),
                    _safe_cli_text(str(row.get("fee_model_status") or "")),
                    _safe_cli_text(str(row.get("settlement_metadata_quality") or "")),
                    str(row.get("reference_only") is True).lower(),
                    _safe_cli_text(str(row.get("paperability_status") or "")),
                    _safe_cli_text(",".join(str(blocker) for blocker in blockers) or "none"),
                ]
            )
        )
    return "\n".join(lines)


def market_graph_diagnostics(
    *,
    fixture: Path | None,
    json_output: Path,
    markdown_output: Path,
) -> int:
    try:
        payload = build_market_graph_diagnostics_files(
            fixture_path=fixture,
            json_output_path=json_output,
            markdown_output_path=markdown_output,
        )
    except ValueError as exc:
        print(f"market_graph_diagnostics_status=FAILED message={exc}")
        return 1

    print(
        "market_graph_diagnostics_status=OK "
        f"edges={payload['edge_count']} "
        f"mode={payload['data_source_mode']} "
        f"json={json_output} markdown={markdown_output}"
    )
    return 0


def explain_market_graph_diagnostics(*, graph_report: Path, json_output: Path, markdown_output: Path) -> int:
    try:
        payload = explain_market_graph_diagnostics_files(
            graph_report_path=graph_report,
            json_output_path=json_output,
            markdown_output_path=markdown_output,
        )
    except ValueError as exc:
        print(f"market_graph_hints_status=FAILED message={exc}")
        return 1

    print(
        "market_graph_hints_status=OK "
        f"hints={payload['hint_count']} "
        f"info_only={str(payload['safety']['info_only']).lower()} "
        f"json={json_output} markdown={markdown_output}"
    )
    return 0


def replay_paper_candidate_markouts(
    ledger: Path,
    polymarket_enriched_later: Path,
    kalshi_enriched_later: Path,
    output: Path,
    *,
    window_tolerance_seconds: float = 60.0,
) -> int:
    try:
        payload = replay_paper_candidate_markout_files(
            ledger_path=ledger,
            polymarket_enriched_later_path=polymarket_enriched_later,
            kalshi_enriched_later_path=kalshi_enriched_later,
            output_path=output,
            config=MarkoutReplayConfig(window_tolerance_seconds=window_tolerance_seconds),
        )
    except ValueError as exc:
        print(f"paper_candidate_markout_replay_status=FAILED message={exc}")
        return 1

    summary = payload["markout_replay"]
    counts = summary["counts_by_status"]
    print(
        "paper_candidate_markout_replay_status=OK "
        f"candidates={payload['ledger_count']} "
        f"windows={sum(counts.values())} "
        f"filled={counts['filled']} "
        f"no_data={counts['no_data']} "
        f"stale={counts['stale']} "
        f"missing_market={counts['missing_market']} "
        f"missing_orderbook={counts['missing_orderbook']} "
        f"output={output}"
    )
    return 0


def run_targeted_pipeline(
    *,
    label: str,
    output_dir: Path,
    limit: int = 50,
    timeout_seconds: float = 10.0,
    polymarket_tag_slug: str | None = None,
    polymarket_tag_id: int | None = None,
    kalshi_series_ticker: str | None = None,
    kalshi_event_ticker: str | None = None,
    kalshi_max_pages: int = 2,
    max_snapshot_age_hours: float = 24.0,
    max_quote_age_seconds: float = 1800.0,
    max_settlement_delta_seconds: float = 3600.0,
    min_top_of_book_size: float = 1.0,
    min_net_gap: float = 0.01,
    accept_unit_mismatch: bool = False,
) -> int:
    try:
        safe_label = _safe_pipeline_label(label)
        _validate_pipeline_target(polymarket_tag_slug, polymarket_tag_id, kalshi_series_ticker, kalshi_event_ticker)
    except ValueError as exc:
        print(f"targeted_pipeline_status=FAILED message={exc}")
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    paths = _targeted_pipeline_paths(output_dir, safe_label)

    steps = [
        (
            "fetch_polymarket",
            lambda: fetch_polymarket(
                limit,
                paths["polymarket_snapshot"],
                timeout_seconds,
                tag_slug=polymarket_tag_slug,
                tag_id=polymarket_tag_id,
            ),
        ),
        (
            "fetch_kalshi",
            lambda: fetch_kalshi(
                limit,
                paths["kalshi_snapshot"],
                timeout_seconds,
                series_ticker=kalshi_series_ticker,
                event_ticker=kalshi_event_ticker,
                max_pages=kalshi_max_pages,
            ),
        ),
        (
            "enrich_polymarket",
            lambda: enrich_orderbooks(
                paths["polymarket_snapshot"],
                "polymarket",
                paths["polymarket_enriched"],
                timeout_seconds=timeout_seconds,
                max_snapshot_age_hours=max_snapshot_age_hours,
            ),
        ),
        (
            "enrich_kalshi",
            lambda: enrich_orderbooks(
                paths["kalshi_snapshot"],
                "kalshi",
                paths["kalshi_enriched"],
                timeout_seconds=timeout_seconds,
                max_snapshot_age_hours=max_snapshot_age_hours,
            ),
        ),
        (
            "match_live_snapshots",
            lambda: match_live_snapshots(
                paths["polymarket_snapshot"],
                paths["kalshi_snapshot"],
                paths["pairs"],
                max_snapshot_age_hours=max_snapshot_age_hours,
            ),
        ),
        (
            "evaluate_paper_candidates",
            lambda: evaluate_paper_candidates(
                paths["pairs"],
                paths["polymarket_enriched"],
                paths["kalshi_enriched"],
                paths["paper_candidates"],
                max_quote_age_seconds=max_quote_age_seconds,
                max_settlement_delta_seconds=max_settlement_delta_seconds,
                min_top_of_book_size=min_top_of_book_size,
                min_net_gap=min_net_gap,
                accept_unit_mismatch=accept_unit_mismatch,
            ),
        ),
    ]
    for step_name, step in steps:
        result = step()
        if result != 0:
            print(f"targeted_pipeline_status=FAILED step={step_name}")
            return result

    try:
        summary = _targeted_pipeline_summary(
            paths,
            min_net_gap=min_net_gap,
            max_settlement_delta_seconds=max_settlement_delta_seconds,
        )
    except ValueError as exc:
        print(f"targeted_pipeline_status=FAILED message={exc}")
        return 1

    later_markout_command = (
        "python scan.py replay-paper-candidate-markouts "
        f"--ledger {paths['paper_candidates']} "
        f"--polymarket-enriched-later {output_dir / f'{safe_label}_polymarket_enriched_later.json'} "
        f"--kalshi-enriched-later {output_dir / f'{safe_label}_kalshi_enriched_later.json'} "
        f"--output {output_dir / f'{safe_label}_paper_candidates_marked.json'}"
    )
    summary_path = output_dir / f"{safe_label}_pipeline_summary.json"
    summary_payload = {
        "schema_version": 1,
        "source": "targeted_pipeline_runner",
        "label": safe_label,
        "paths": {name: str(path) for name, path in paths.items()},
        "summary": summary,
        "later_markout_command": later_markout_command,
        "disclaimer": (
            "Read-only saved-file pipeline. No trading, auth, orders, midpoint fills, "
            "profit claim, executable-liquidity claim, PAPER output, or POSSIBLE_ARB output."
        ),
    }
    summary_path.write_text(json.dumps(summary_payload, indent=2, sort_keys=True), encoding="utf-8")

    actions = summary["evaluator_counts"]
    top_reasons = _format_top_reasons(summary["top_rejection_reasons"])
    print(
        "targeted_pipeline_status=OK "
        f"label={safe_label} "
        f"polymarket_normalized={summary['polymarket_normalized_count']} "
        f"kalshi_normalized={summary['kalshi_normalized_count']} "
        f"polymarket_enriched={summary['polymarket_enriched_count']}/{summary['polymarket_enrichment_market_count']} "
        f"kalshi_enriched={summary['kalshi_enriched_count']}/{summary['kalshi_enrichment_market_count']} "
        f"pairs={summary['pair_count']} "
        f"watch={actions.get('WATCH', 0)} "
        f"manual_review={actions.get('MANUAL_REVIEW', 0)} "
        f"paper_candidate={actions.get('PAPER_CANDIDATE', 0)} "
        f"top_rejection_reasons={top_reasons} "
        f"summary={summary_path}"
    )
    print(f"later_markout_command={later_markout_command}")
    return 0


def run_multi_universe_sweep(
    *,
    manifest: Path,
    sweep_label: str,
    output_dir: Path,
    limit: int = 50,
    timeout_seconds: float = 10.0,
    kalshi_max_pages: int = 2,
    max_snapshot_age_hours: float = 24.0,
    max_quote_age_seconds: float = 1800.0,
    max_settlement_delta_seconds: float = 3600.0,
    min_top_of_book_size: float = 1.0,
    min_net_gap: float = 0.01,
    accept_unit_mismatch: bool = False,
) -> int:
    try:
        safe_sweep_label = _safe_pipeline_label(sweep_label)
        universes = _load_sweep_manifest(manifest)
    except ValueError as exc:
        print(f"multi_universe_sweep_status=FAILED message={exc}")
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []

    for universe in universes:
        raw_label = str(universe.get("label") or "")
        try:
            safe_label = _safe_pipeline_label(raw_label)
        except ValueError as exc:
            safe_label = raw_label or "<missing_label>"
            rows.append(_failed_sweep_row(safe_label, str(exc)))
            print(f"multi_universe_sweep_universe_status=FAILED label={safe_label} reason={exc}")
            continue

        try:
            result = run_targeted_pipeline(
                label=safe_label,
                output_dir=output_dir,
                limit=limit,
                timeout_seconds=timeout_seconds,
                polymarket_tag_slug=_optional_string(universe.get("polymarket_tag_slug")),
                polymarket_tag_id=_optional_int(universe.get("polymarket_tag_id")),
                kalshi_series_ticker=_optional_string(universe.get("kalshi_series_ticker")),
                kalshi_event_ticker=_optional_string(universe.get("kalshi_event_ticker")),
                kalshi_max_pages=kalshi_max_pages,
                max_snapshot_age_hours=max_snapshot_age_hours,
                max_quote_age_seconds=max_quote_age_seconds,
                max_settlement_delta_seconds=max_settlement_delta_seconds,
                min_top_of_book_size=min_top_of_book_size,
                min_net_gap=min_net_gap,
                accept_unit_mismatch=accept_unit_mismatch,
            )
        except Exception as exc:
            rows.append(_failed_sweep_row(safe_label, str(exc)))
            print(f"multi_universe_sweep_universe_status=FAILED label={safe_label} reason={exc}")
            continue
        if result != 0:
            reason = f"run_targeted_pipeline_returned_{result}"
            rows.append(_failed_sweep_row(safe_label, reason))
            print(f"multi_universe_sweep_universe_status=FAILED label={safe_label} reason={reason}")
            continue

        summary_path = output_dir / f"{safe_label}_pipeline_summary.json"
        try:
            summary_payload = _load_json_report(summary_path, f"{safe_label}_pipeline_summary")
            row = _completed_sweep_row(safe_label, summary_payload)
        except ValueError as exc:
            row = _failed_sweep_row(safe_label, str(exc))
            print(f"multi_universe_sweep_universe_status=FAILED label={safe_label} reason={exc}")
            rows.append(row)
            continue

        rows.append(row)
        actions = row["evaluator_counts"]
        print(
            "multi_universe_sweep_universe_status=OK "
            f"label={safe_label} "
            f"pairs={row['pair_count']} "
            f"watch={actions.get('WATCH', 0)} "
            f"manual_review={actions.get('MANUAL_REVIEW', 0)} "
            f"paper_candidate={actions.get('PAPER_CANDIDATE', 0)}"
        )

    completed_count = sum(1 for row in rows if row["status"] == "completed")
    failed_count = len(rows) - completed_count
    summary_payload = {
        "schema_version": 1,
        "source": "multi_universe_sweep",
        "sweep_label": safe_sweep_label,
        "manifest": str(manifest),
        "output_dir": str(output_dir),
        "completed_count": completed_count,
        "failed_count": failed_count,
        "universes": rows,
        "disclaimer": (
            "Read-only saved-file sweep. Per-universe work is delegated to the targeted "
            "pipeline runner; no trading, auth, orders, midpoint fills, profit claims, "
            "or executable-liquidity claims are introduced here."
        ),
    }
    json_path = output_dir / f"{safe_sweep_label}_sweep_summary.json"
    md_path = output_dir / f"{safe_sweep_label}_sweep_summary.md"
    json_path.write_text(json.dumps(summary_payload, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(_sweep_markdown(summary_payload), encoding="utf-8")

    status = "OK" if completed_count else "FAILED"
    print(
        f"multi_universe_sweep_status={status} "
        f"sweep_label={safe_sweep_label} "
        f"universes={len(rows)} "
        f"completed={completed_count} "
        f"failed={failed_count} "
        f"json={json_path} "
        f"markdown={md_path}"
    )
    return 0 if completed_count else 1


def explain_sweep_summary(path: Path) -> int:
    try:
        payload = _load_json_report(path, "sweep_summary")
        if payload.get("schema_version") != 1:
            raise ValueError("sweep_summary schema_version must be 1")
        if payload.get("source") != "multi_universe_sweep":
            raise ValueError("sweep_summary source must be multi_universe_sweep")
    except ValueError as exc:
        print(f"explain_sweep_summary_status=FAILED message={exc}")
        return 1

    universes = payload.get("universes")
    universes = universes if isinstance(universes, list) else []
    for row in universes:
        if not isinstance(row, dict):
            continue
        counts = row.get("evaluator_counts")
        counts = counts if isinstance(counts, dict) else {}
        gap_distribution = row.get("gap_distribution")
        gap_distribution = gap_distribution if isinstance(gap_distribution, dict) else _empty_gap_distribution()
        near_miss_summary = row.get("near_miss_summary")
        near_miss_summary = near_miss_summary if isinstance(near_miss_summary, dict) else _empty_near_miss_summary()
        print(f"Universe: {_display_value(row.get('label'))}")
        print(f"  status: {_display_value(row.get('status'))}")
        print(f"  polymarket_normalized_count: {_display_value(row.get('polymarket_normalized_count'))}")
        print(f"  kalshi_normalized_count: {_display_value(row.get('kalshi_normalized_count'))}")
        print(f"  pair_count: {_display_value(row.get('pair_count'))}")
        print(
            "  evaluator_counts: "
            f"WATCH={counts.get('WATCH', 0)} "
            f"MANUAL_REVIEW={counts.get('MANUAL_REVIEW', 0)} "
            f"PAPER_CANDIDATE={counts.get('PAPER_CANDIDATE', 0)}"
        )
        print(f"  Gap > 0 total: {_gross_gap_positive_count(gap_distribution)}")
        print(f"  Net > 0: {gap_distribution.get('estimated_net_gap_gt_0_count', 0)}")
        print(f"  near_miss.net_gap.median_distance: {_near_miss_median(near_miss_summary, 'net_gap')}")
        print(f"  near_miss.settlement_delta.median_distance: {_near_miss_median(near_miss_summary, 'settlement_delta')}")
        print(
            "  near_miss.settlement_delta_near_pass.median_distance: "
            f"{_near_miss_median(near_miss_summary, 'settlement_delta_near_pass')}"
        )
        print(f"  top_rejection_reasons: {_format_top_reasons(row.get('top_rejection_reasons') or [])}")
        print("")

    print(
        "Aggregate: "
        f"total_universes={len(universes)} "
        f"completed={int(payload.get('completed_count') or 0)} "
        f"failed={int(payload.get('failed_count') or 0)}"
    )
    print(f"explain_sweep_summary_status=OK summary={path}")
    return 0


def explain_pipeline_summary(path: Path) -> int:
    try:
        payload = _load_json_report(path, "pipeline_summary")
        if payload.get("schema_version") != 1:
            raise ValueError("pipeline_summary schema_version must be 1")
        if payload.get("source") != "targeted_pipeline_runner":
            raise ValueError("pipeline_summary source must be targeted_pipeline_runner")
        summary = payload.get("summary")
        if not isinstance(summary, dict):
            raise ValueError("pipeline_summary summary must be an object")
    except ValueError as exc:
        print(f"explain_pipeline_summary_status=FAILED message={exc}")
        return 1

    counts = summary.get("evaluator_counts")
    counts = counts if isinstance(counts, dict) else {}
    gap_distribution = summary.get("gap_distribution")
    gap_distribution = gap_distribution if isinstance(gap_distribution, dict) else _empty_gap_distribution()
    near_miss_summary = summary.get("near_miss_summary")
    near_miss_summary = near_miss_summary if isinstance(near_miss_summary, dict) else _empty_near_miss_summary()
    print(f"Pipeline: {_display_value(payload.get('label'))}")
    print(f"  polymarket_normalized_count: {_display_value(summary.get('polymarket_normalized_count'))}")
    print(f"  kalshi_normalized_count: {_display_value(summary.get('kalshi_normalized_count'))}")
    print(
        "  polymarket_enriched: "
        f"{_display_value(summary.get('polymarket_enriched_count'))}/"
        f"{_display_value(summary.get('polymarket_enrichment_market_count'))}"
    )
    print(
        "  kalshi_enriched: "
        f"{_display_value(summary.get('kalshi_enriched_count'))}/"
        f"{_display_value(summary.get('kalshi_enrichment_market_count'))}"
    )
    print(f"  pair_count: {_display_value(summary.get('pair_count'))}")
    print(
        "  evaluator_counts: "
        f"WATCH={counts.get('WATCH', 0)} "
        f"MANUAL_REVIEW={counts.get('MANUAL_REVIEW', 0)} "
        f"PAPER_CANDIDATE={counts.get('PAPER_CANDIDATE', 0)}"
    )
    print(f"  Gap > 0 total: {_gross_gap_positive_count(gap_distribution)}")
    print(f"  Net > 0: {gap_distribution.get('estimated_net_gap_gt_0_count', 0)}")
    print(f"  near_miss.net_gap.median_distance: {_near_miss_median(near_miss_summary, 'net_gap')}")
    print(f"  near_miss.settlement_delta.median_distance: {_near_miss_median(near_miss_summary, 'settlement_delta')}")
    print(
        "  near_miss.settlement_delta_near_pass.median_distance: "
        f"{_near_miss_median(near_miss_summary, 'settlement_delta_near_pass')}"
    )
    print(f"  top_rejection_reasons: {_format_top_reasons(summary.get('top_rejection_reasons') or [])}")
    print(f"  later_markout_command: {_display_value(payload.get('later_markout_command'))}")
    print(f"explain_pipeline_summary_status=OK summary={path}")
    return 0


def explain_paper_candidates(path: Path, action: str | None = None, limit: int | None = None) -> int:
    try:
        payload = _load_json_report(path, "paper_candidates")
        if payload.get("schema_version") != 1:
            raise ValueError("paper_candidates schema_version must be 1")
        if payload.get("source") != "paper_candidate_evaluator":
            raise ValueError("paper_candidates source must be paper_candidate_evaluator")
        ledger = payload.get("ledger")
        if not isinstance(ledger, list):
            raise ValueError("paper_candidates ledger must be a list")
    except ValueError as exc:
        print(f"explain_paper_candidates_status=FAILED message={exc}")
        return 1

    rows = [row for row in ledger if isinstance(row, dict)]
    if action:
        rows = [row for row in rows if row.get("action") == action]
    rows = sorted(rows, key=_paper_candidate_sort_key)
    if limit is not None:
        rows = rows[: max(limit, 0)]

    print("Paper candidate ledger explanation: research review only; PAPER_CANDIDATE is not a trade signal.")
    for row in rows:
        polymarket = row.get("polymarket")
        kalshi = row.get("kalshi")
        gap = row.get("gap")
        markouts = row.get("markouts")
        polymarket = polymarket if isinstance(polymarket, dict) else {}
        kalshi = kalshi if isinstance(kalshi, dict) else {}
        gap = gap if isinstance(gap, dict) else {}
        print(f"Candidate: {_display_value(row.get('candidate_id'))}")
        print(f"  action: {_display_value(row.get('action'))}")
        print(f"  opportunity_class: {_display_value(row.get('opportunity_class'))}")
        print(
            "  Polymarket: "
            f"market_id={_display_value(polymarket.get('market_id'))} "
            f"question={_display_value(polymarket.get('question'))} "
            f"venue={_display_value(polymarket.get('venue'))}"
        )
        print(
            "    would_enter: "
            f"side={_display_value(polymarket.get('would_enter_side'))} "
            f"price={_display_value(polymarket.get('would_enter_price'))}"
        )
        print(
            "    quote: "
            f"best_bid={_display_value(polymarket.get('best_bid'))} "
            f"best_ask={_display_value(polymarket.get('best_ask'))}"
        )
        print(
            "    depth: "
            f"best_bid={_display_value(polymarket.get('depth_at_best_bid'))} "
            f"best_ask={_display_value(polymarket.get('depth_at_best_ask'))}"
        )
        print(
            "  Kalshi: "
            f"ticker={_display_value(kalshi.get('ticker'))} "
            f"question={_display_value(kalshi.get('question'))} "
            f"venue={_display_value(kalshi.get('venue'))}"
        )
        print(
            "    would_enter: "
            f"side={_display_value(kalshi.get('would_enter_side'))} "
            f"price={_display_value(kalshi.get('would_enter_price'))}"
        )
        print(
            "    quote: "
            f"best_bid={_display_value(kalshi.get('best_bid'))} "
            f"best_ask={_display_value(kalshi.get('best_ask'))}"
        )
        print(
            "    depth: "
            f"best_bid={_display_value(kalshi.get('depth_at_best_bid'))} "
            f"best_ask={_display_value(kalshi.get('depth_at_best_ask'))}"
        )
        print(f"  gross_gap: {_display_value(gap.get('gross_gap'))}")
        print(f"  polymarket_fee: {_display_value(gap.get('polymarket_fee'))}")
        print(f"  kalshi_fee: {_display_value(gap.get('kalshi_fee'))}")
        print(f"  estimated_net_gap: {_display_value(gap.get('estimated_net_gap'))}")
        print(f"  settlement_delta_seconds: {_display_value(gap.get('settlement_delta_seconds'))}")
        print(f"  size_unit_warning: {_display_value(gap.get('size_unit_warning'))}")
        print(f"  missed_fill_reason: {_display_value(row.get('missed_fill_reason'))}")
        print(f"  ineligibility_reasons: {_format_reason_list(row.get('ineligibility_reasons'))}")
        print(f"  markouts: {_format_markout_summary(markouts)}")
        print("")

    print(f"explain_paper_candidates_status=OK candidates_shown={len(rows)} ledger={path}")
    return 0


def explain_reference_context(snapshot: Path, reference_snapshot: Path, min_similarity: float = 0.35) -> int:
    try:
        payload = explain_reference_context_files(
            snapshot_path=snapshot,
            reference_snapshot_path=reference_snapshot,
            min_similarity=min_similarity,
        )
    except ValueError as exc:
        print(f"explain_reference_context_status=FAILED message={exc}")
        return 1

    print("Reference context diagnostics: review only; sportsbook odds are not executable prices.")
    print(f"  executable_market_count: {payload['executable_market_count']}")
    print(f"  reference_record_count: {payload['reference_record_count']}")
    print(f"  diagnostic_match_count: {payload['diagnostic_match_count']}")
    print(f"  stale_reference_record_count: {payload['stale_reference_record_count']}")
    print(f"  malformed_reference_record_count: {payload['malformed_reference_record_count']}")
    for row in payload["diagnostic_rows"]:
        print(f"Diagnostic: {_display_value(row.get('executable_market_id'))}")
        print(f"  action: {_display_value(row.get('action'))}")
        print(f"  executable_market_title: {_display_value(row.get('executable_market_title'))}")
        print(f"  reference_event_title: {_display_value(row.get('reference_event_title'))}")
        print(
            "  reference: "
            f"bookmaker={_display_value(row.get('bookmaker'))} "
            f"market_type={_display_value(row.get('market_type'))} "
            f"outcome={_display_value(row.get('reference_outcome_name'))}"
        )
        print(f"  no_vig_probability: {_display_value(row.get('no_vig_probability'))}")
        print(f"  retrieved_at: {_display_value(row.get('retrieved_at'))}")
        print(f"  stale_after: {_display_value(row.get('stale_after'))}")
        print(f"  reference_status: {_display_value(row.get('reference_status'))}")
        print(f"  match_score: {_display_value(row.get('match_score'))}")
        print(f"  match_reason: {_display_value(row.get('match_reason'))}")
        print(f"  diagnostics: {_format_reason_list(row.get('reference_diagnostics'))}")
        print("")
    print(f"explain_reference_context_status=OK matches={payload['diagnostic_match_count']}")
    return 0


def llm_review_relationships(input_path: Path, output_path: Path, markdown_output: Path | None = None, stub: bool = False) -> int:
    if not stub:
        print("llm_review_relationships_status=FAILED message=only --stub mode is supported; no real LLM calls are implemented")
        return 1
    try:
        payload = review_relationship_report_file(
            input_path=input_path,
            output_path=output_path,
            markdown_output_path=markdown_output,
        )
    except ValueError as exc:
        print(f"llm_review_relationships_status=FAILED message={exc}")
        return 1
    summary = payload["llm_relationship_review"]
    print(
        "llm_review_relationships_status=OK "
        f"rows_reviewed={summary['rows_reviewed']} "
        f"validation_errors={summary['validation_error_count']} "
        f"manual_review_escalations={summary['manual_review_escalation_count']} "
        f"output={output_path}"
    )
    return 0


def _safe_pipeline_label(label: str) -> str:
    normalized = label.strip()
    if not normalized:
        raise ValueError("label must not be empty")
    if any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in normalized):
        raise ValueError("label may contain only letters, numbers, underscores, and hyphens")
    return normalized


def _safe_overlap_label(label: str | None, *, category: str, query: str | None) -> str:
    raw_label = label or "_".join(part for part in ("overlap", category, query or "none") if part)
    return _safe_pipeline_label(raw_label.lower())


def _validate_pipeline_target(
    polymarket_tag_slug: str | None,
    polymarket_tag_id: int | None,
    kalshi_series_ticker: str | None,
    kalshi_event_ticker: str | None,
) -> None:
    if not polymarket_tag_slug and polymarket_tag_id is None:
        raise ValueError("provide --polymarket-tag-slug and/or --polymarket-tag-id")
    if not kalshi_series_ticker and not kalshi_event_ticker:
        raise ValueError("provide --kalshi-series-ticker and/or --kalshi-event-ticker")


def _targeted_pipeline_paths(output_dir: Path, label: str) -> dict[str, Path]:
    return {
        "polymarket_snapshot": output_dir / f"{label}_polymarket_snapshot.json",
        "kalshi_snapshot": output_dir / f"{label}_kalshi_snapshot.json",
        "polymarket_enriched": output_dir / f"{label}_polymarket_enriched.json",
        "kalshi_enriched": output_dir / f"{label}_kalshi_enriched.json",
        "pairs": output_dir / f"{label}_pairs.json",
        "paper_candidates": output_dir / f"{label}_paper_candidates.json",
    }


def _targeted_pipeline_summary(
    paths: dict[str, Path],
    min_net_gap: float = 0.01,
    max_settlement_delta_seconds: float = 3600.0,
) -> dict[str, Any]:
    polymarket_snapshot = _load_json_report(paths["polymarket_snapshot"], "polymarket_snapshot")
    kalshi_snapshot = _load_json_report(paths["kalshi_snapshot"], "kalshi_snapshot")
    polymarket_enriched = _load_json_report(paths["polymarket_enriched"], "polymarket_enriched")
    kalshi_enriched = _load_json_report(paths["kalshi_enriched"], "kalshi_enriched")
    pairs = _load_json_report(paths["pairs"], "pairs")
    ledger = _load_json_report(paths["paper_candidates"], "paper_candidates")
    polymarket_enrichment = polymarket_enriched.get("orderbook_enrichment") or {}
    kalshi_enrichment = kalshi_enriched.get("orderbook_enrichment") or {}
    return {
        "polymarket_normalized_count": int(polymarket_snapshot.get("normalized_count") or 0),
        "kalshi_normalized_count": int(kalshi_snapshot.get("normalized_count") or 0),
        "polymarket_enrichment_market_count": int(polymarket_enrichment.get("market_count") or 0),
        "polymarket_enriched_count": int(polymarket_enrichment.get("enriched_count") or 0),
        "polymarket_unenriched_count": int(polymarket_enrichment.get("unenriched_count") or 0),
        "kalshi_enrichment_market_count": int(kalshi_enrichment.get("market_count") or 0),
        "kalshi_enriched_count": int(kalshi_enrichment.get("enriched_count") or 0),
        "kalshi_unenriched_count": int(kalshi_enrichment.get("unenriched_count") or 0),
        "pair_count": int(pairs.get("pair_count") or 0),
        "evaluator_counts": ledger.get("counts_by_action") or {},
        "top_rejection_reasons": _top_rejection_reasons(ledger),
        "gap_distribution": _gap_distribution(ledger),
        "near_miss_summary": _near_miss_summary(
            ledger,
            min_net_gap=min_net_gap,
            max_settlement_delta_seconds=max_settlement_delta_seconds,
        ),
    }


def _load_sweep_manifest(path: Path) -> list[dict[str, Any]]:
    payload = _load_json_report(path, "sweep_manifest")
    _validate_sweep_manifest_structure(payload)
    return payload["universes"]


def _validate_sweep_manifest_structure(payload: dict[str, Any]) -> None:
    if payload.get("version") != 1:
        raise ValueError("sweep manifest version must be 1")
    universes = payload.get("universes")
    if not isinstance(universes, list):
        raise ValueError("sweep manifest must contain a universes list")
    if not universes:
        raise ValueError("sweep manifest universes list must not be empty")
    labels: set[str] = set()
    for index, universe in enumerate(universes):
        if not isinstance(universe, dict):
            raise ValueError(f"sweep manifest universe at index {index} must be an object")
        label = universe.get("label")
        if not isinstance(label, str) or not label:
            raise ValueError(f"sweep manifest universe at index {index} must contain a non-empty label")
        try:
            safe_label = _safe_pipeline_label(label)
        except ValueError as exc:
            raise ValueError(f"sweep manifest universe label at index {index} is invalid: {exc}") from exc
        if safe_label != label:
            raise ValueError(
                f"sweep manifest universe label at index {index} may contain only letters, numbers, underscores, and hyphens"
            )
        if label in labels:
            raise ValueError(f"sweep manifest contains duplicate label: {label}")
        labels.add(label)
        try:
            _validate_pipeline_target(
                _optional_string(universe.get("polymarket_tag_slug")),
                _optional_int(universe.get("polymarket_tag_id")),
                _optional_string(universe.get("kalshi_series_ticker")),
                _optional_string(universe.get("kalshi_event_ticker")),
            )
        except ValueError as exc:
            raise ValueError(f"sweep manifest universe {label}: {exc}") from exc


def _completed_sweep_row(label: str, summary_payload: dict[str, Any]) -> dict[str, Any]:
    summary = summary_payload.get("summary")
    if not isinstance(summary, dict):
        raise ValueError(f"{label}_pipeline_summary missing summary object")
    evaluator_counts = summary.get("evaluator_counts")
    if not isinstance(evaluator_counts, dict):
        evaluator_counts = {}
    top_reasons = summary.get("top_rejection_reasons")
    if not isinstance(top_reasons, list):
        top_reasons = []
    gap_distribution = summary.get("gap_distribution")
    if not isinstance(gap_distribution, dict):
        gap_distribution = _empty_gap_distribution()
    near_miss_summary = summary.get("near_miss_summary")
    if not isinstance(near_miss_summary, dict):
        near_miss_summary = _empty_near_miss_summary()
    return {
        "label": label,
        "status": "completed",
        "failure_reason": None,
        "polymarket_normalized_count": int(summary.get("polymarket_normalized_count") or 0),
        "kalshi_normalized_count": int(summary.get("kalshi_normalized_count") or 0),
        "pair_count": int(summary.get("pair_count") or 0),
        "evaluator_counts": evaluator_counts,
        "top_rejection_reasons": top_reasons[:3],
        "gap_distribution": gap_distribution,
        "near_miss_summary": near_miss_summary,
    }


def _failed_sweep_row(label: str, failure_reason: str) -> dict[str, Any]:
    return {
        "label": label,
        "status": "failed",
        "failure_reason": failure_reason,
        "polymarket_normalized_count": None,
        "kalshi_normalized_count": None,
        "pair_count": None,
        "evaluator_counts": {},
        "top_rejection_reasons": [],
        "gap_distribution": _empty_gap_distribution(),
        "near_miss_summary": _empty_near_miss_summary(),
    }


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _sweep_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Multi-Universe Sweep: {payload['sweep_label']}",
        "",
        "Read-only targeted pipeline sweep. Rows summarize saved-file outputs only.",
        "",
        "| Label | Status | Polymarket | Kalshi | Pairs | Gap > 0 | Net > 0 | Near-miss net | Near-miss settlement | WATCH | MANUAL_REVIEW | PAPER_CANDIDATE | Top rejection reasons | Failure reason |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    universes = payload.get("universes") or []
    if isinstance(universes, list):
        for row in universes:
            if not isinstance(row, dict):
                continue
            counts = row.get("evaluator_counts")
            if not isinstance(counts, dict):
                counts = {}
            gap_distribution = row.get("gap_distribution")
            if not isinstance(gap_distribution, dict):
                gap_distribution = _empty_gap_distribution()
            near_miss_summary = row.get("near_miss_summary")
            if not isinstance(near_miss_summary, dict):
                near_miss_summary = _empty_near_miss_summary()
            lines.append(
                "| "
                + " | ".join(
                    [
                        _markdown_cell(row.get("label")),
                        _markdown_cell(row.get("status")),
                        _markdown_cell(row.get("polymarket_normalized_count")),
                        _markdown_cell(row.get("kalshi_normalized_count")),
                        _markdown_cell(row.get("pair_count")),
                        _markdown_cell(_gross_gap_positive_count(gap_distribution)),
                        _markdown_cell(gap_distribution.get("estimated_net_gap_gt_0_count", 0)),
                        _markdown_cell(_near_miss_count(near_miss_summary, "net_gap")),
                        _markdown_cell(_near_miss_count(near_miss_summary, "settlement_delta")),
                        _markdown_cell(counts.get("WATCH", 0)),
                        _markdown_cell(counts.get("MANUAL_REVIEW", 0)),
                        _markdown_cell(counts.get("PAPER_CANDIDATE", 0)),
                        _markdown_cell(_format_top_reasons(row.get("top_rejection_reasons") or [])),
                        _markdown_cell(row.get("failure_reason") or ""),
                    ]
                )
                + " |"
            )
    lines.append("")
    return "\n".join(lines)


def _markdown_cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def _empty_gap_distribution() -> dict[str, int]:
    return {
        "gross_gap_lte_0_count": 0,
        "gross_gap_gt_0_lte_0_005_count": 0,
        "gross_gap_gt_0_005_lte_0_01_count": 0,
        "gross_gap_gt_0_01_lte_0_02_count": 0,
        "gross_gap_gt_0_02_count": 0,
        "estimated_net_gap_gt_0_count": 0,
        "estimated_net_gap_lte_0_count": 0,
    }


def _gap_distribution(ledger_payload: dict[str, Any]) -> dict[str, int]:
    distribution = _empty_gap_distribution()
    rows = ledger_payload.get("ledger")
    if not isinstance(rows, list):
        return distribution
    for row in rows:
        if not isinstance(row, dict):
            continue
        gap = row.get("gap")
        if not isinstance(gap, dict):
            continue
        gross_gap = float_or_none(gap.get("gross_gap"))
        if gross_gap is not None:
            if gross_gap <= 0:
                distribution["gross_gap_lte_0_count"] += 1
            elif gross_gap <= 0.005:
                distribution["gross_gap_gt_0_lte_0_005_count"] += 1
            elif gross_gap <= 0.01:
                distribution["gross_gap_gt_0_005_lte_0_01_count"] += 1
            elif gross_gap <= 0.02:
                distribution["gross_gap_gt_0_01_lte_0_02_count"] += 1
            else:
                distribution["gross_gap_gt_0_02_count"] += 1
        estimated_net_gap = float_or_none(gap.get("estimated_net_gap"))
        if estimated_net_gap is not None:
            if estimated_net_gap > 0:
                distribution["estimated_net_gap_gt_0_count"] += 1
            else:
                distribution["estimated_net_gap_lte_0_count"] += 1
    return distribution


def _gross_gap_positive_count(gap_distribution: dict[str, Any]) -> int:
    keys = [
        "gross_gap_gt_0_lte_0_005_count",
        "gross_gap_gt_0_005_lte_0_01_count",
        "gross_gap_gt_0_01_lte_0_02_count",
        "gross_gap_gt_0_02_count",
    ]
    return sum(int(gap_distribution.get(key) or 0) for key in keys)


def _near_miss_count(near_miss_summary: dict[str, Any], key: str) -> int:
    summary = near_miss_summary.get(key)
    if not isinstance(summary, dict):
        return 0
    return int(summary.get("count") or 0)


def _near_miss_median(near_miss_summary: dict[str, Any], key: str) -> str:
    summary = near_miss_summary.get(key)
    if not isinstance(summary, dict):
        return "n/a"
    return _display_value(summary.get("median_distance"))


def _display_value(value: Any) -> str:
    if value is None:
        return "n/a"
    return str(value)


def _paper_candidate_sort_key(row: dict[str, Any]) -> tuple[int, str]:
    action_order = {"PAPER_CANDIDATE": 0, "MANUAL_REVIEW": 1, "WATCH": 2}
    return (action_order.get(str(row.get("action")), 99), str(row.get("candidate_id") or ""))


def _format_reason_list(value: Any) -> str:
    if not isinstance(value, list) or not value:
        return "none"
    return ",".join(str(reason) for reason in value if reason is not None) or "none"


def _format_markout_summary(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return "none"
    parts: list[str] = []
    for window, markout in value.items():
        if not isinstance(markout, dict):
            parts.append(f"{window}:present")
            continue
        status = markout.get("markout_status")
        if status is None:
            has_observation = any(item is not None for item in markout.values())
            status = "present" if has_observation else "placeholder"
        parts.append(f"{window}:{status}")
    return ", ".join(parts)


def _empty_distance_summary() -> dict[str, Any]:
    return {
        "count": 0,
        "min_distance": None,
        "max_distance": None,
        "median_distance": None,
    }


def _empty_near_miss_summary() -> dict[str, Any]:
    return {
        "net_gap": _empty_distance_summary(),
        "settlement_delta": _empty_distance_summary(),
        "settlement_delta_near_pass": _empty_distance_summary(),
    }


def _near_miss_summary(
    ledger_payload: dict[str, Any],
    min_net_gap: float = 0.01,
    max_settlement_delta_seconds: float = 3600.0,
) -> dict[str, Any]:
    return {
        "net_gap": _net_gap_near_miss_summary(ledger_payload, min_net_gap=min_net_gap),
        "settlement_delta": _settlement_delta_near_miss_summary(
            ledger_payload,
            max_settlement_delta_seconds=max_settlement_delta_seconds,
        ),
        "settlement_delta_near_pass": _settlement_delta_near_pass_summary(
            ledger_payload,
            max_settlement_delta_seconds=max_settlement_delta_seconds,
        ),
    }


def _net_gap_near_miss_summary(ledger_payload: dict[str, Any], min_net_gap: float = 0.01) -> dict[str, Any]:
    distances: list[float] = []
    rows = ledger_payload.get("ledger")
    if not isinstance(rows, list):
        return _empty_distance_summary()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("action") != "WATCH":
            continue
        if row.get("missed_fill_reason") != "estimated_net_gap_below_minimum":
            continue
        gap = row.get("gap")
        if not isinstance(gap, dict):
            continue
        estimated_net_gap = float_or_none(gap.get("estimated_net_gap"))
        if estimated_net_gap is None:
            continue
        distances.append(round(min_net_gap - estimated_net_gap, 6))
    if not distances:
        return _empty_distance_summary()
    return _distance_summary(distances)


def _settlement_delta_near_miss_summary(
    ledger_payload: dict[str, Any],
    max_settlement_delta_seconds: float = 3600.0,
) -> dict[str, Any]:
    distances: list[float] = []
    rows = ledger_payload.get("ledger")
    if not isinstance(rows, list):
        return _empty_distance_summary()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("action") != "WATCH":
            continue
        if row.get("missed_fill_reason") != "settlement_delta_exceeds_limit":
            continue
        gap = row.get("gap")
        if not isinstance(gap, dict):
            continue
        settlement_delta_seconds = float_or_none(gap.get("settlement_delta_seconds"))
        if settlement_delta_seconds is None:
            continue
        distances.append(round(settlement_delta_seconds - max_settlement_delta_seconds, 6))
    if not distances:
        return _empty_distance_summary()
    return _distance_summary(distances)


def _settlement_delta_near_pass_summary(
    ledger_payload: dict[str, Any],
    max_settlement_delta_seconds: float = 3600.0,
) -> dict[str, Any]:
    distances: list[float] = []
    rows = ledger_payload.get("ledger")
    if not isinstance(rows, list):
        return _empty_distance_summary()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("missed_fill_reason") == "settlement_delta_exceeds_limit":
            continue
        gap = row.get("gap")
        if not isinstance(gap, dict):
            continue
        settlement_delta_seconds = float_or_none(gap.get("settlement_delta_seconds"))
        if settlement_delta_seconds is None:
            continue
        distances.append(round(max_settlement_delta_seconds - settlement_delta_seconds, 6))
    if not distances:
        return _empty_distance_summary()
    return _distance_summary(distances)


def _distance_summary(distances: list[float]) -> dict[str, Any]:
    return {
        "count": len(distances),
        "min_distance": round(min(distances), 6),
        "max_distance": round(max(distances), 6),
        "median_distance": round(median(distances), 6),
    }


def _load_json_report(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"{label} file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} JSON is invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} JSON must be an object")
    return payload


def _top_rejection_reasons(ledger_payload: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    rows = ledger_payload.get("ledger")
    if not isinstance(rows, list):
        return []
    for row in rows:
        if not isinstance(row, dict):
            continue
        reasons = row.get("ineligibility_reasons")
        if isinstance(reasons, list):
            for reason in reasons:
                if reason is not None:
                    reason_key = str(reason)
                    counts[reason_key] = counts.get(reason_key, 0) + 1
        missed_fill_reason = row.get("missed_fill_reason")
        if missed_fill_reason:
            reason_key = f"missed_fill:{missed_fill_reason}"
            counts[reason_key] = counts.get(reason_key, 0) + 1
    return [
        {"reason": reason, "count": count}
        for reason, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


def _format_top_reasons(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "none"
    return ",".join(f"{row['reason']}:{row['count']}" for row in rows)


def _format_count_map(counts: dict[str, int], limit: int = 3) -> str:
    if not counts:
        return "none"
    items = list(counts.items())[:limit]
    return ",".join(f"{_safe_cli_text(str(key))}:{value}" for key, value in items)


def _mlb_daily_operator_check_summary(
    *,
    date_label: str,
    collector_report: dict[str, Any],
    scout_report: dict[str, Any],
    kalshi_evidence: Path,
    polymarket_evidence: Path,
    scout_json: Path,
    scout_markdown: Path,
    summary_json: Path,
    summary_markdown: Path,
) -> dict[str, Any]:
    collector_counts = collector_report.get("summary_counts") or {}
    scout_counts = scout_report.get("summary_counts") or {}
    return {
        "schema_kind": "mlb_daily_operator_check_summary_v1",
        "diagnostic_only": True,
        "public_no_auth_only": True,
        "execution_enabled": False,
        "date": date_label,
        "collector_status": "OK",
        "games_found": {
            "kalshi": collector_counts.get("kalshi_games", 0),
            "polymarket": collector_counts.get("polymarket_games", 0),
        },
        "matched_games": scout_report.get("matched_games", collector_counts.get("matched_games", 0)),
        "scout_rows": scout_counts.get("rows", 0),
        "strict_paper_candidate_rows": scout_counts.get("strict_paper_candidate_rows", 0),
        "operator_paper_candidate_rows": scout_counts.get("operator_paper_candidate_rows", 0),
        "cdna_fill_first_paper_candidate_rows": scout_counts.get("cdna_fill_first_paper_candidate_rows", 0),
        "total_paper_candidate_rows": scout_counts.get("total_paper_candidate_rows", 0),
        "standard_paper_candidate_rows": scout_counts.get("total_paper_candidate_rows", 0),
        "operator_arb_review_rows": scout_counts.get("operator_arb_review_rows", 0),
        "manual_review_rows": scout_counts.get("manual_review_rows", 0),
        "watch_rows": scout_counts.get("watch_rows", 0),
        "ignore_blocked_rows": scout_counts.get("ignore_blocked_rows", 0),
        "exact_ready_rows": 0,
        "global_paper_candidate_emitted": scout_counts.get("total_paper_candidate_rows", 0) > 0,
        "top_blockers": scout_counts.get("top_blockers") or [],
        "report_paths": {
            "kalshi_evidence": str(kalshi_evidence),
            "polymarket_evidence": str(polymarket_evidence),
            "scout_json": str(scout_json),
            "scout_markdown": str(scout_markdown),
            "collector_summary_json": (collector_report.get("outputs") or {}).get("summary_json"),
            "collector_summary_markdown": (collector_report.get("outputs") or {}).get("summary_markdown"),
            "runner_summary_json": str(summary_json),
            "runner_summary_markdown": str(summary_markdown),
        },
        "safety": {
            "collector_public_no_auth_only": True,
            "saved_evidence_scout_only": True,
            "execution_enabled": False,
            "candidate_pair_creation": False,
            "evaluator_invoked": False,
            "exact_ready": False,
            "total_paper_candidate_rows": scout_counts.get("total_paper_candidate_rows", 0),
            "global_paper_candidate_emitted": scout_counts.get("total_paper_candidate_rows", 0) > 0,
        },
    }


def _render_mlb_daily_operator_check_summary(summary: dict[str, Any]) -> str:
    paths = summary.get("report_paths") or {}
    lines = [
        "# MLB Daily Operator Check Summary",
        "",
        "Public-read-only collection followed by saved-evidence-only operator/residual scouting. No execution, evaluator, or candidate-pair path is invoked.",
        "",
        "## Summary",
        "",
        f"- date: `{_safe_markdown_text(summary.get('date'))}`",
        f"- collector_status: `{_safe_markdown_text(summary.get('collector_status'))}`",
        f"- kalshi_games_found: `{(summary.get('games_found') or {}).get('kalshi', 0)}`",
        f"- polymarket_games_found: `{(summary.get('games_found') or {}).get('polymarket', 0)}`",
        f"- matched_games: `{summary.get('matched_games', 0)}`",
        f"- scout_rows: `{summary.get('scout_rows', 0)}`",
        f"- strict_paper_candidate_rows: `{summary.get('strict_paper_candidate_rows', 0)}`",
        f"- operator_paper_candidate_rows: `{summary.get('operator_paper_candidate_rows', 0)}`",
        f"- cdna_fill_first_paper_candidate_rows: `{summary.get('cdna_fill_first_paper_candidate_rows', 0)}`",
        f"- total_paper_candidate_rows: `{summary.get('total_paper_candidate_rows', 0)}`",
        f"- manual_review_rows: `{summary.get('manual_review_rows', 0)}`",
        f"- watch_rows: `{summary.get('watch_rows', 0)}`",
        f"- ignore_blocked_rows: `{summary.get('ignore_blocked_rows', 0)}`",
        f"- exact_ready_rows: `0`",
        f"- total_paper_candidate_rows: `{summary.get('total_paper_candidate_rows', 0)}`",
        "",
        "## Report Paths",
        "",
        f"- kalshi_evidence: `{_safe_markdown_text(paths.get('kalshi_evidence'))}`",
        f"- polymarket_evidence: `{_safe_markdown_text(paths.get('polymarket_evidence'))}`",
        f"- scout_json: `{_safe_markdown_text(paths.get('scout_json'))}`",
        f"- scout_markdown: `{_safe_markdown_text(paths.get('scout_markdown'))}`",
        f"- collector_summary_json: `{_safe_markdown_text(paths.get('collector_summary_json'))}`",
        f"- collector_summary_markdown: `{_safe_markdown_text(paths.get('collector_summary_markdown'))}`",
        "",
        "## Top Blockers",
        "",
        "| Blocker | Count |",
        "|---|---:|",
    ]
    blockers = summary.get("top_blockers") or []
    if blockers:
        for item in blockers:
            lines.append(f"| {_safe_markdown_text(item.get('blocker'))} | {item.get('count', 0)} |")
    else:
        lines.append("| none | 0 |")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- diagnostic_only: `true`",
            "- public_no_auth_only: `true`",
            "- execution_enabled: `false`",
            "- candidate_pair_creation: `false`",
            "- evaluator_invoked: `false`",
            "- exact_ready: `false`",
            f"- total_paper_candidate_rows: `{summary.get('total_paper_candidate_rows', 0)}`",
            f"- global_paper_candidate_emitted: `{str(bool(summary.get('global_paper_candidate_emitted'))).lower()}`",
        ]
    )
    return "\n".join(lines) + "\n"


def _safe_markdown_text(value: Any) -> str:
    return "" if value is None else str(value).replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
