from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from statistics import median
from typing import Any

from relative_value._numeric import float_or_none
from relative_value.live_snapshot_matcher import match_snapshot_files
from relative_value.markout_replay import MarkoutReplayConfig, replay_paper_candidate_markout_files
from relative_value.orderbook_enrichment import enrich_orderbook_snapshot_file
from relative_value.paper_candidate_evaluator import (
    PaperCandidateEvaluatorConfig,
    evaluate_paper_candidate_files,
)
from relative_value.report import write_json_report, write_markdown_report
from relative_value.scanner import RelativeValueScanner
from venues.kalshi import (
    FixtureKalshiAdapter,
    KalshiMarketFilterOptions,
    KalshiReadOnlyClient,
    write_kalshi_market_snapshot,
)
from venues.polymarket import (
    FixturePolymarketAdapter,
    PolymarketGammaClient,
    PolymarketMarketFilterOptions,
    write_polymarket_market_snapshot,
)
from venues.the_odds_api import FixtureTheOddsApiAdapter, TheOddsApiReadOnlyClient, write_the_odds_api_reference_snapshot


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

    match_parser = subparsers.add_parser(
        "match-live-snapshots",
        help="Match saved Kalshi/Polymarket schema-v1 snapshots for manual review only.",
    )
    match_parser.add_argument("--polymarket", type=Path, default=PROJECT_ROOT / "reports" / "polymarket_markets_snapshot.json")
    match_parser.add_argument("--kalshi", type=Path, default=PROJECT_ROOT / "reports" / "kalshi_markets_snapshot.json")
    match_parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "reports" / "live_snapshot_pairs.json")
    match_parser.add_argument("--min-similarity", type=float, default=0.68)
    match_parser.add_argument("--max-snapshot-age-hours", type=float, default=24.0)

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
    if args.command == "match-live-snapshots":
        return match_live_snapshots(
            args.polymarket,
            args.kalshi,
            args.output,
            min_similarity=args.min_similarity,
            max_snapshot_age_hours=args.max_snapshot_age_hours,
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

    scanner = RelativeValueScanner()
    candidates = scanner.scan_from_adapters(build_fixture_adapters(args.fixture_dir), include_ignore=args.include_ignore)
    json_path = args.output_dir / "relative_value_candidates.json"
    md_path = args.output_dir / "relative_value_candidates.md"
    write_json_report(candidates, json_path)
    write_markdown_report(candidates, md_path)

    possible_arbs = sum(1 for candidate in candidates if candidate.action.value == "POSSIBLE_ARB")
    print(
        f"relative_value_scan_status=OFFLINE_COMPLETE candidates={len(candidates)} "
        f"possible_arbs={possible_arbs} json={json_path} markdown={md_path}"
    )
    return 0


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


def match_live_snapshots(
    polymarket: Path,
    kalshi: Path,
    output: Path,
    min_similarity: float = 0.68,
    max_snapshot_age_hours: float = 24.0,
) -> int:
    try:
        payload = match_snapshot_files(
            polymarket,
            kalshi,
            output_path=output,
            min_similarity=min_similarity,
            max_snapshot_age_hours=max_snapshot_age_hours,
        )
    except ValueError as exc:
        print(f"live_snapshot_match_status=FAILED message={exc}")
        return 1
    actions = {pair["action"] for pair in payload["pairs"]}
    print(
        "live_snapshot_match_status=OK "
        f"pairs={payload['pair_count']} actions={','.join(sorted(actions)) or 'none'} "
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
) -> int:
    config = PaperCandidateEvaluatorConfig(
        max_quote_age_seconds=max_quote_age_seconds,
        max_settlement_delta_seconds=max_settlement_delta_seconds,
        min_top_of_book_size=min_top_of_book_size,
        min_net_gap=min_net_gap,
        accept_unit_mismatch=accept_unit_mismatch,
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


def _safe_pipeline_label(label: str) -> str:
    normalized = label.strip()
    if not normalized:
        raise ValueError("label must not be empty")
    if any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in normalized):
        raise ValueError("label may contain only letters, numbers, underscores, and hyphens")
    return normalized


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
