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
from relative_value.reference_diagnostics import explain_reference_context_files
from relative_value.report import write_json_report, write_markdown_report
from relative_value.scanner import RelativeValueScanner
from relative_value.same_payoff_board import build_same_payoff_board_files
from relative_value.same_payoff_board import diagnose_mlb_world_series_board_blockers_files
from relative_value.same_payoff_evidence import attach_same_payoff_evidence_files
from relative_value.source_registry import ImplementationStatus, SOURCE_REGISTRY, SourceType
from relative_value.executable_venue_plan import PLANNED_EXECUTABLE_VENUE_CAPABILITIES
from relative_value.exact_paper_candidate_universes import build_exact_paper_candidate_universe_report_files
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


def build_fixture_adapters(fixture_dir: Path) -> list[object]:
    return [
        FixtureKalshiAdapter(fixture_dir / "kalshi_markets.json"),
        FixturePolymarketAdapter(fixture_dir / "polymarket_markets.json"),
        FixtureTheOddsApiAdapter(fixture_dir / "the_odds_api_events.json"),
    ]


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

    sx_bet_parser = subparsers.add_parser(
        "fetch-sx-bet-readonly",
        help="Fetch a public read-only SX Bet research snapshot that remains non-executable.",
    )
    sx_bet_parser.add_argument("--max-markets", type=int, default=25)
    sx_bet_parser.add_argument("--timeout-seconds", type=float, default=10.0)
    sx_bet_parser.add_argument("--sport", help="Optional local sport filter for SX Bet research snapshots, for example baseball or basketball.")
    sx_bet_parser.add_argument("--league", help="Optional local league filter for SX Bet research snapshots, for example MLB, NBA, or NFL.")
    sx_bet_parser.add_argument("--query", help="Optional local free-text filter across SX Bet event/team/outcome fields.")
    sx_bet_parser.add_argument("--label", help="Optional safe label for reports/sx_bet/<label>/sx_bet_research_snapshot.json.")
    sx_bet_parser.add_argument("--output", type=Path)

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
    enrich_parser.add_argument("--max-snapshot-age-hours", type=float, default=24.0)

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
        default=PROJECT_ROOT / "reports" / "live_readonly_match_report.json",
        help="Optional saved prior matcher report used only to explain old ranking behavior.",
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
    mlb_ws_paper_check_parser.add_argument("--pairs", type=Path, required=True, help="Saved WS/WS Kalshi-Polymarket pairs file.")
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
    mlb_ws_paper_check_parser.add_argument("--board-json-output", type=Path, default=PROJECT_ROOT / "reports" / "mlb_world_series_same_payoff_board_fresh.json", help="Output path for same-payoff board JSON.")
    mlb_ws_paper_check_parser.add_argument("--board-markdown-output", type=Path, default=PROJECT_ROOT / "reports" / "mlb_world_series_same_payoff_board_fresh.md", help="Output path for same-payoff board Markdown.")
    mlb_ws_paper_check_parser.add_argument("--derived-pairs-output", type=Path, default=PROJECT_ROOT / "reports" / "mlb_world_series_pairs_with_evidence_fresh.json", help="Output path for derived pairs with same-payoff evidence.")
    mlb_ws_paper_check_parser.add_argument("--evaluator-output", type=Path, default=PROJECT_ROOT / "reports" / "mlb_world_series_evaluator_fresh_trust_settlement.json", help="Output path for evaluator ledger.")
    mlb_ws_paper_check_parser.add_argument("--summary-json-output", type=Path, default=PROJECT_ROOT / "reports" / "mlb_world_series_paper_check_summary.json", help="Output path for compact paper-check summary JSON.")
    mlb_ws_paper_check_parser.add_argument("--summary-markdown-output", type=Path, default=PROJECT_ROOT / "reports" / "mlb_world_series_paper_check_summary.md", help="Output path for compact paper-check summary Markdown.")

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
    if args.command == "fetch-sx-bet-readonly":
        return fetch_sx_bet_readonly(
            max_markets=args.max_markets,
            timeout_seconds=args.timeout_seconds,
            sport=args.sport,
            league=args.league,
            query=args.query,
            label=args.label,
            output=args.output,
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
            board_json_output=args.board_json_output,
            board_markdown_output=args.board_markdown_output,
            derived_pairs_output=args.derived_pairs_output,
            evaluator_output=args.evaluator_output,
            summary_json_output=args.summary_json_output,
            summary_markdown_output=args.summary_markdown_output,
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

    report_label = _safe_overlap_label(label, category=category, query=query)
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
            "live_fetch_succeeded": payload.get("live_fetch_succeeded") is True,
            "normalized_count": len(payload.get("normalized_markets")) if isinstance(payload.get("normalized_markets"), list) else 0,
        }
    )
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
) -> int:
    output = output or _sx_bet_research_snapshot_path(label)
    try:
        snapshot = SXBetReadOnlyClient(timeout_seconds=timeout_seconds).fetch_research_snapshot(
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
) -> int:
    try:
        payload = enrich_orderbook_snapshot_file(
            snapshot_path=snapshot,
            venue=venue,
            output_path=output,
            timeout_seconds=timeout_seconds,
            max_snapshot_age_hours=max_snapshot_age_hours,
        )
    except ValueError as exc:
        print(f"orderbook_enrichment_status=FAILED venue={venue} message={exc}")
        return 1

    summary = payload["orderbook_enrichment"]
    print(
        "orderbook_enrichment_status=OK "
        f"venue={venue} markets={summary['market_count']} "
        f"enriched={summary['enriched_count']} unenriched={summary['unenriched_count']} "
        f"fresh_orderbook_fetch_enriched={summary.get('fresh_orderbook_fetch_enriched_count', summary['enriched_count'])} "
        f"existing_top_of_book_present={summary.get('existing_top_of_book_present_count', 0)} "
        f"full_orderbook_missing={summary.get('full_orderbook_missing_count', summary['unenriched_count'])} "
        f"fetch_failed={summary.get('fetch_failed_count', 0)} "
        f"stale_existing_top_of_book={summary.get('stale_existing_top_of_book_count', 0)} "
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
    pairs: Path,
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
    board_json_output: Path = PROJECT_ROOT / "reports" / "mlb_world_series_same_payoff_board_fresh.json",
    board_markdown_output: Path = PROJECT_ROOT / "reports" / "mlb_world_series_same_payoff_board_fresh.md",
    derived_pairs_output: Path = PROJECT_ROOT / "reports" / "mlb_world_series_pairs_with_evidence_fresh.json",
    evaluator_output: Path = PROJECT_ROOT / "reports" / "mlb_world_series_evaluator_fresh_trust_settlement.json",
    summary_json_output: Path = PROJECT_ROOT / "reports" / "mlb_world_series_paper_check_summary.json",
    summary_markdown_output: Path = PROJECT_ROOT / "reports" / "mlb_world_series_paper_check_summary.md",
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
        print(f"mlb_world_series_paper_check_status=FAILED message={exc}")
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
    )
    summary_json_output.parent.mkdir(parents=True, exist_ok=True)
    summary_markdown_output.parent.mkdir(parents=True, exist_ok=True)
    summary_json_output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    summary_markdown_output.write_text(_mlb_world_series_paper_check_markdown(summary), encoding="utf-8")

    counts = summary["evaluator_counts"]
    paper_candidate_ids = summary["paper_candidate_ids"]
    print(
        "mlb_world_series_paper_check_status=OK "
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


def _mlb_world_series_paper_check_summary(
    *,
    generated_at: datetime,
    polymarket_snapshot: Path,
    kalshi_snapshot: Path,
    pairs: Path,
    polymarket_enriched_output: Path,
    kalshi_enriched_output: Path,
    board_json_output: Path,
    board_markdown_output: Path,
    derived_pairs_output: Path,
    evaluator_output: Path,
    summary_json_output: Path,
    summary_markdown_output: Path,
    polymarket_enriched: dict[str, Any],
    kalshi_enriched: dict[str, Any],
    board: dict[str, Any],
    derived_pairs: dict[str, Any],
    evaluator: dict[str, Any],
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
    paper_candidate_ids = [
        str(row.get("candidate_id"))
        for row in evaluator.get("ledger", [])
        if isinstance(row, dict) and row.get("action") == "PAPER_CANDIDATE" and row.get("candidate_id")
    ]
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
        "outputs": {
            "polymarket_enriched": str(polymarket_enriched_output),
            "kalshi_enriched": str(kalshi_enriched_output),
            "same_payoff_board_json": str(board_json_output),
            "same_payoff_board_markdown": str(board_markdown_output),
            "derived_pairs": str(derived_pairs_output),
            "evaluator": str(evaluator_output),
            "summary_json": str(summary_json_output),
            "summary_markdown": str(summary_markdown_output),
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
        "strict_same_payoff_passes": int(board.get("strict_same_payoff_pass_count") or 0),
        "trusted_relationships": int(
            (derived_pairs.get("same_payoff_evidence_attachment") or {}).get("trusted_relationship_attached_count") or 0
        ),
        "evaluator_counts": {
            "PAPER_CANDIDATE": int(evaluator_counts.get("PAPER_CANDIDATE") or 0),
            "MANUAL_REVIEW": int(evaluator_counts.get("MANUAL_REVIEW") or 0),
            "WATCH": int(evaluator_counts.get("WATCH") or 0),
        },
        "dominant_blocker": top_reasons[0]["reason"] if top_reasons else None,
        "top_rejection_reasons": top_reasons,
        "paper_candidate_ids": paper_candidate_ids,
        "quote_freshness": quote_freshness,
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
            captured = _parse_datetime_or_none(quote.get("quote_captured_at"))
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
        f"- Dominant blocker: `{payload['dominant_blocker'] or 'none'}`",
        f"- Max quote age seconds: `{freshness['max_quote_age_seconds']}`",
        f"- Stale quote warning: `{freshness['stale_quote_warning']}`",
        "",
        "## Paper Candidate IDs",
        "",
    ]
    ids = payload.get("paper_candidate_ids") or []
    lines.append(", ".join(ids) if ids else "none")
    lines.extend(["", "## Outputs", ""])
    for key, value in payload["outputs"].items():
        lines.append(f"- {key}: `{value}`")
    lines.append("")
    return "\n".join(lines)


def discover_exact_paper_candidate_universes(*, json_output: Path, markdown_output: Path) -> int:
    try:
        payload = build_exact_paper_candidate_universe_report_files(
            project_root=PROJECT_ROOT,
            json_output_path=json_output,
            markdown_output_path=markdown_output,
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
        f"paper_candidates={summary['paper_candidate_count']} "
        f"execution_data={counts.get('EXECUTION_DATA_AVAILABLE', 0)} "
        f"trusted_relationships={counts.get('TRUSTED_RELATIONSHIPS_AVAILABLE', 0)} "
        f"same_scope_pairs={counts.get('SAME_SCOPE_PAIRS_AVAILABLE', 0)} "
        f"inventory_only={counts.get('INVENTORY_ONLY', 0)} "
        f"no_inventory={counts.get('NO_INVENTORY', 0)} "
        f"json={json_output} markdown={markdown_output}"
    )
    return 0


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


if __name__ == "__main__":
    raise SystemExit(main())
