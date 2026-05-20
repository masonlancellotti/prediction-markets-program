from __future__ import annotations

import argparse
from pathlib import Path

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
from venues.the_odds_api import FixtureTheOddsApiAdapter


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


if __name__ == "__main__":
    raise SystemExit(main())
