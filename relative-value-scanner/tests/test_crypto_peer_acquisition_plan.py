from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import scan
from relative_value.crypto_peer_acquisition_plan import (
    B_MISSING_CDNA_PEER,
    B_MISSING_KALSHI_ORDERBOOK_QUOTE,
    B_MISSING_POLYMARKET_CLOB_QUOTE,
    B_MISSING_POLYMARKET_PEER,
    B_NO_SAFE_FETCH_COMMAND_FOUND,
    B_PEER_DATE_THRESHOLD_GAP,
    B_SETTLEMENT_SOURCE_UNVERIFIED,
    NEEDS_COMMAND_DISCOVERY,
    build_crypto_peer_acquisition_plan_report,
    write_crypto_peer_acquisition_plan_files,
)


_NOW = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)


def _kalshi_audit_row(
    *,
    ticker: str,
    event_ticker: str,
    asset: str,
    target_date: str,
    target_time: str = "21:00",
    timezone_label: str = "UTC",
    threshold: float = 86249.99,
    comparator: str = "above",
    settlement_source: str | None = "CF Benchmarks BRTI",
    settlement_source_url: str | None = None,
    quote_present: bool = False,
    raw_source_file: str | None = None,
) -> dict[str, Any]:
    return {
        "row_id": f"kalshi_crypto::{ticker}",
        "ticker": ticker,
        "event_ticker": event_ticker,
        "market_id": ticker,
        "venue": "kalshi",
        "asset": asset,
        "threshold": threshold,
        "comparator": comparator,
        "target_date": target_date,
        "target_time": target_time,
        "timezone": timezone_label,
        "settlement_source": settlement_source,
        "settlement_source_url": settlement_source_url,
        "market_shape": "point_in_time_threshold",
        "typed_complete": True,
        "raw_source_file": raw_source_file,
        "blockers": [],
        "quote": {
            "bid": 0.42 if quote_present else None,
            "ask": 0.46 if quote_present else None,
            "bid_size": 100.0 if quote_present else None,
            "ask_size": 75.0 if quote_present else None,
            "observed_at": "2026-05-26T19:00:00+00:00" if quote_present else None,
            "present": quote_present,
        },
    }


def _kalshi_audit_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_kind": "kalshi_crypto_typed_key_audit_v1",
        "schema_version": 1,
        "source": "kalshi_crypto_typed_key_audit_v1",
        "rows": rows,
        "summary": {"kalshi_crypto_rows": len(rows), "top_blockers": []},
        "safety": {"diagnostic_only": True},
    }


def _polymarket_pit_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source": "polymarket_point_in_time_typed_key_audit_v1",
        "rows": rows,
        "summary": {},
        "safety": {},
    }


def _polymarket_enriched_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_kind": "polymarket_taxonomy_shape_scout_enriched_v1",
        "schema_version": 1,
        "source": "polymarket_taxonomy_shape_scout_enriched_v1",
        "rows": rows,
        "summary": {},
        "safety": {},
    }


def _cdna_basis_payload() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source": "cdna_crypto_basis_risk_scout_v1",
        "rows": [],
        "summary": {},
        "safety": {},
    }


def _cdna_snapshot_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source": "crypto_com_predict_cdna_research_snapshot_v1",
        "rows": rows,
        "summary": {},
        "safety": {},
    }


def _core_trio_payload() -> dict[str, Any]:
    return {
        "schema_kind": "core_trio_peer_coverage_audit_v1",
        "schema_version": 1,
        "source": "core_trio_peer_coverage_audit_v1",
        "summary": {"strongest_overlap_family": None},
        "families": [],
        "safety": {},
        "input_dir": "reports",
        "report_path": "reports/core_trio_peer_coverage_audit.json",
    }


def _kalshi_snapshot(path: Path, tickers: list[str]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "source": "kalshi_markets",
                "markets": [
                    {
                        "venue": "kalshi",
                        "ticker": ticker,
                        "market_id": ticker,
                        "event_ticker": ticker.rsplit("-", 1)[0],
                        "question": f"Question for {ticker}",
                    }
                    for ticker in tickers
                ],
            }
        ),
        encoding="utf-8",
    )
    return str(path)


def _setup(
    tmp_path: Path,
    *,
    kalshi_rows: list[dict[str, Any]],
    polymarket_pit_rows: list[dict[str, Any]] | None = None,
    polymarket_enriched_rows: list[dict[str, Any]] | None = None,
    cdna_rows: list[dict[str, Any]] | None = None,
) -> Path:
    (tmp_path / "kalshi_crypto_typed_key_audit.json").write_text(
        json.dumps(_kalshi_audit_payload(kalshi_rows)), encoding="utf-8"
    )
    (tmp_path / "polymarket_point_in_time_typed_key_audit.json").write_text(
        json.dumps(_polymarket_pit_payload(polymarket_pit_rows or [])), encoding="utf-8"
    )
    (tmp_path / "polymarket_taxonomy_shape_scout_enriched.json").write_text(
        json.dumps(_polymarket_enriched_payload(polymarket_enriched_rows or [])), encoding="utf-8"
    )
    (tmp_path / "cdna_crypto_basis_risk_scout.json").write_text(
        json.dumps(_cdna_basis_payload()), encoding="utf-8"
    )
    (tmp_path / "crypto_com_predict_cdna_research_snapshot.json").write_text(
        json.dumps(_cdna_snapshot_payload(cdna_rows or [])), encoding="utf-8"
    )
    (tmp_path / "core_trio_peer_coverage_audit.json").write_text(
        json.dumps(_core_trio_payload()), encoding="utf-8"
    )
    return tmp_path


def test_builds_grid_from_typed_complete_kalshi_rows(tmp_path: Path) -> None:
    rows = [
        _kalshi_audit_row(
            ticker="KXBTC-26MAY3017-T100000",
            event_ticker="KXBTC-26MAY3017",
            asset="BTC",
            target_date="2026-05-30",
            threshold=100000.0,
        ),
        _kalshi_audit_row(
            ticker="KXBTC-26MAY3017-T101000",
            event_ticker="KXBTC-26MAY3017",
            asset="BTC",
            target_date="2026-05-30",
            threshold=101000.0,
        ),
        _kalshi_audit_row(
            ticker="KXETH-26JUL0107-T2200",
            event_ticker="KXETH-26JUL0107",
            asset="ETH",
            target_date="2026-07-01",
            threshold=2200.0,
        ),
    ]
    input_dir = _setup(tmp_path, kalshi_rows=rows)
    report = build_crypto_peer_acquisition_plan_report(input_dir=input_dir, generated_at=_NOW)
    targets = report["targets"]
    assert len(targets) == 3
    # Each target carries grid-required fields.
    for t in targets:
        assert t["asset"] in {"BTC", "ETH"}
        assert t["target_date"]
        assert t["target_time"]
        assert t["timezone"]
        assert t["threshold"] is not None
        assert t["comparator"]
        assert t["settlement_source"]
        assert t["diagnostic_only"] is True
        assert t["can_create_candidate_pair"] is False
        assert t["paper_candidate"] is False
    s = report["summary"]
    assert s["kalshi_typed_complete_grid_rows"] == 3
    assert s["unique_assets"] == 2
    assert s["unique_dates"] == 2
    assert s["unique_thresholds"] == 3
    # Asset-date density picked up.
    densities = {t["kalshi_ticker"]: t["asset_date_threshold_density"] for t in targets}
    assert densities["KXBTC-26MAY3017-T100000"] == 2
    assert densities["KXBTC-26MAY3017-T101000"] == 2
    assert densities["KXETH-26JUL0107-T2200"] == 1


def test_ranks_btc_near_term_above_distant_eth(tmp_path: Path) -> None:
    rows = [
        # Far-future ETH (low priority).
        _kalshi_audit_row(
            ticker="KXETH-27JAN0117-T5000",
            event_ticker="KXETH-27JAN0117",
            asset="ETH",
            target_date="2027-01-01",
            threshold=5000.0,
        ),
        # Near-term BTC (high priority).
        _kalshi_audit_row(
            ticker="KXBTC-26MAY3017-T100000",
            event_ticker="KXBTC-26MAY3017",
            asset="BTC",
            target_date="2026-05-30",
            threshold=100000.0,
        ),
        # Mid-term BTC (lower than near-term but still BTC).
        _kalshi_audit_row(
            ticker="KXBTC-26JUL3017-T120000",
            event_ticker="KXBTC-26JUL3017",
            asset="BTC",
            target_date="2026-07-30",
            threshold=120000.0,
        ),
    ]
    input_dir = _setup(tmp_path, kalshi_rows=rows)
    report = build_crypto_peer_acquisition_plan_report(input_dir=input_dir, generated_at=_NOW)
    ordered = [t["kalshi_ticker"] for t in report["targets"]]
    assert ordered[0] == "KXBTC-26MAY3017-T100000"
    # ETH far-future is last.
    assert ordered[-1] == "KXETH-27JAN0117-T5000"


def test_emits_polymarket_query_recommendations_without_live_calls(tmp_path: Path) -> None:
    rows = [
        _kalshi_audit_row(
            ticker="KXBTC-26MAY3017-T100000",
            event_ticker="KXBTC-26MAY3017",
            asset="BTC",
            target_date="2026-05-30",
            threshold=100000.0,
        ),
        _kalshi_audit_row(
            ticker="KXETH-26JUL0107-T2200",
            event_ticker="KXETH-26JUL0107",
            asset="ETH",
            target_date="2026-07-01",
            threshold=2200.0,
        ),
    ]
    input_dir = _setup(tmp_path, kalshi_rows=rows)
    report = build_crypto_peer_acquisition_plan_report(input_dir=input_dir, generated_at=_NOW)
    queries = report["polymarket_queries_recommended"]
    assert queries
    assert all(q["safe_command"] == "discover-polymarket-crypto-markets" for q in queries)
    assert all(q["targeted_query_command_available"] is True for q in queries)
    assert all("polymarket_targeted_query_command_missing" not in q["blockers"] for q in queries)
    # Now that the targeted --query / --asset / --target-date flags exist, the
    # planner emits them in the suggested invocation.
    assert all("--query" in q["safe_command_invocation"] for q in queries)
    assert all("--asset" in q["safe_command_invocation"] for q in queries)
    assert all("--target-date" in q["safe_command_invocation"] for q in queries)
    # Top-level --output (the legacy flag) is still never emitted; only --output-dir is allowed.
    assert all(
        " --output " not in q["safe_command_invocation"]
        for q in queries
    )
    # No live call performed — diagnostic_only flag intact.
    assert report["safety"]["live_fetch_attempted"] is False
    assert report["diagnostic_only"] is True
    # Each query has a deterministic, asset-and-date-derived search term.
    btc_q = next(q for q in queries if q["asset"] == "BTC")
    eth_q = next(q for q in queries if q["asset"] == "ETH")
    assert "bitcoin" in btc_q["search_term"].lower()
    assert "ethereum" in eth_q["search_term"].lower()
    assert "May 30, 2026" in btc_q["search_term"] or "2026" in btc_q["search_term"]
    # Asset/date pairs deduped.
    seen = {(q["asset"], q["target_date"]) for q in queries}
    assert len(seen) == len(queries)


def test_emits_kalshi_orderbook_targets_without_live_calls(tmp_path: Path) -> None:
    snapshot_path = _kalshi_snapshot(
        tmp_path / "reports" / "kalshi_crypto_snapshot.json",
        ["KXBTC-26MAY3017-T100000", "KXBTC-26MAY3017-T101000"],
    )
    rows = [
        _kalshi_audit_row(
            ticker="KXBTC-26MAY3017-T100000",
            event_ticker="KXBTC-26MAY3017",
            asset="BTC",
            target_date="2026-05-30",
            threshold=100000.0,
            quote_present=False,
            raw_source_file=snapshot_path,
        ),
        _kalshi_audit_row(
            ticker="KXBTC-26MAY3017-T101000",
            event_ticker="KXBTC-26MAY3017",
            asset="BTC",
            target_date="2026-05-30",
            threshold=101000.0,
            quote_present=False,
            raw_source_file=snapshot_path,
        ),
    ]
    input_dir = _setup(tmp_path, kalshi_rows=rows)
    report = build_crypto_peer_acquisition_plan_report(input_dir=input_dir, generated_at=_NOW)
    targets = report["kalshi_orderbook_targets_recommended"]
    assert targets
    one = targets[0]
    assert one["safe_command"] == "enrich-kalshi-orderbooks"
    assert one["top_event_ticker"] == "KXBTC-26MAY3017"
    assert one["kalshi_market_count"] == 2
    assert one["snapshot_input_path"] != "reports/kalshi_markets_snapshot.json"
    assert one["snapshot_input_path"].replace("\\", "/").endswith("kalshi_crypto_snapshot.json")
    assert "--snapshot" in one["safe_command_invocation"]
    assert "--output" in one["safe_command_invocation"]
    assert "--max-snapshot-age-hours 100000" in one["safe_command_invocation"]
    assert one["requires_stale_snapshot_age_override"] is True
    assert "ticker list" in one["fresh_fetch_guidance"]
    assert report["summary"]["kalshi_orderbook_targets_requiring_snapshot_age_override"] == 1
    # Per-row blocker reflected.
    assert any(B_MISSING_KALSHI_ORDERBOOK_QUOTE in t["blockers"] for t in report["targets"])


def test_kalshi_orderbook_input_snapshot_missing_for_crypto_grid_when_no_viable_snapshot(tmp_path: Path) -> None:
    rows = [
        _kalshi_audit_row(
            ticker="KXBTC-26MAY3017-T100000",
            event_ticker="KXBTC-26MAY3017",
            asset="BTC",
            target_date="2026-05-30",
            threshold=100000.0,
            quote_present=False,
        )
    ]
    input_dir = _setup(tmp_path, kalshi_rows=rows)
    report = build_crypto_peer_acquisition_plan_report(input_dir=input_dir, generated_at=_NOW)

    assert report["summary"]["kalshi_orderbook_input_snapshot_missing_for_crypto_grid"] is True
    assert any(
        "kalshi_orderbook_input_snapshot_missing_for_crypto_grid" in target["blockers"]
        for target in report["kalshi_orderbook_targets_recommended"]
    )
    assert not any(
        target.get("snapshot_input_path") == "reports/kalshi_markets_snapshot.json"
        for target in report["kalshi_orderbook_targets_recommended"]
    )


def test_recommends_fresh_kalshi_crypto_snapshot_when_saved_books_are_settled(tmp_path: Path) -> None:
    row = _kalshi_audit_row(
        ticker="KXBTC-26MAY2517-T100000",
        event_ticker="KXBTC-26MAY2517",
        asset="BTC",
        target_date="2026-05-25",
        threshold=100000.0,
        quote_present=False,
        raw_source_file="reports/live_readonly/sweep/overlap_crypto_bitcoin/kalshi_live_readonly_snapshot.json",
    )
    row["quote"].update(
        {
            "stale_top_of_book": True,
            "full_orderbook_missing": True,
            "orderbook_failure_reason": "closed_or_settled_empty_book",
            "market_settled": True,
        }
    )
    input_dir = _setup(tmp_path, kalshi_rows=[row])

    report = build_crypto_peer_acquisition_plan_report(input_dir=input_dir, generated_at=_NOW)
    targets = report["kalshi_orderbook_targets_recommended"]
    assert targets
    assert targets[0]["safe_command"] == "fetch-kalshi-crypto-readonly"
    assert "--asset BTC,ETH" in targets[0]["safe_command_invocation"]
    assert "--include-orderbooks" in targets[0]["safe_command_invocation"]
    assert "kalshi_fresh_crypto_snapshot_required" in targets[0]["blockers"]
    assert report["summary"]["kalshi_fresh_crypto_snapshot_recommended"] is True
    assert report["summary"]["command_validation_error_count"] == 0


def test_polymarket_clob_refresh_recommended_when_token_ids_present_but_no_book(tmp_path: Path) -> None:
    kalshi_rows = [
        _kalshi_audit_row(
            ticker="KXBTC-26DEC3122-T100000",
            event_ticker="KXBTC-26DEC3122",
            asset="BTC",
            target_date="2026-12-31",
            threshold=100000.0,
        )
    ]
    pm_pit_rows = [
        {
            "row_id": "poly_pit_btc_dec31",
            "market_shape": "point_in_time_threshold",
            "asset_or_family": "BTC",
            "threshold": 100000.0,
            "comparator": ">",
            "target_date": "December 31, 2026",
            "target_time": "4:00 PM ET",
            "settlement_source_present": True,
        }
    ]
    pm_enriched_rows = [
        {
            "row_id": "poly_pit_btc_dec31",
            "market_id": "m_btc_dec31",
            "condition_id": "0xcondbtc",
            "token_ids": ["tok_yes_btc_dec31", "tok_no_btc_dec31"],
            "clob_book_attached": False,
        }
    ]
    input_dir = _setup(
        tmp_path,
        kalshi_rows=kalshi_rows,
        polymarket_pit_rows=pm_pit_rows,
        polymarket_enriched_rows=pm_enriched_rows,
    )
    report = build_crypto_peer_acquisition_plan_report(input_dir=input_dir, generated_at=_NOW)
    clob_targets = report["polymarket_clob_refresh_recommended"]
    assert clob_targets
    one = clob_targets[0]
    assert one["safe_command"] == "refresh-polymarket-clob-for-taxonomy-candidates"
    assert one["polymarket_row_id"] == "poly_pit_btc_dec31"
    assert "tok_yes_btc_dec31" in one["token_ids"]
    target = report["targets"][0]
    assert target["has_polymarket_peer"] is True
    assert target["recommended_next_action"]["safe_command"] == "refresh-polymarket-clob-for-taxonomy-candidates"


def test_cdna_target_emitted_when_no_saved_cdna_peer(tmp_path: Path) -> None:
    rows = [
        _kalshi_audit_row(
            ticker="KXETH-26JUN1517-T3000",
            event_ticker="KXETH-26JUN1517",
            asset="ETH",
            target_date="2026-06-15",
            threshold=3000.0,
        )
    ]
    input_dir = _setup(tmp_path, kalshi_rows=rows)
    report = build_crypto_peer_acquisition_plan_report(input_dir=input_dir, generated_at=_NOW)
    cdna_targets = report["cdna_targets_recommended"]
    assert cdna_targets
    one = cdna_targets[0]
    assert one["safe_command"] == "parse-crypto-com-predict-cdna-fixtures"
    assert one["asset"] == "ETH"
    assert one["target_date"] == "2026-06-15"
    assert one["saved_cdna_row_present"] is False
    target = report["targets"][0]
    assert B_MISSING_CDNA_PEER in target["blockers"]
    assert B_PEER_DATE_THRESHOLD_GAP in target["blockers"]


def test_no_paper_candidate_emitted_anywhere(tmp_path: Path) -> None:
    rows = [
        _kalshi_audit_row(
            ticker=f"KXBTC-26MAY{i:02d}17-T{100000 + i * 1000}",
            event_ticker=f"KXBTC-26MAY{i:02d}17",
            asset="BTC",
            target_date=f"2026-05-{i:02d}",
            threshold=100000.0 + i * 1000,
        )
        for i in range(28, 31)
    ]
    input_dir = _setup(tmp_path, kalshi_rows=rows)
    json_output = tmp_path / "plan.json"
    md_output = tmp_path / "plan.md"
    write_crypto_peer_acquisition_plan_files(
        input_dir=input_dir,
        json_output=json_output,
        markdown_output=md_output,
        generated_at=_NOW,
    )
    forbidden = "PAPER" + "_CANDIDATE"
    for path in (json_output, md_output):
        text = path.read_text(encoding="utf-8")
        assert forbidden not in text
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["summary"]["exact_ready_rows"] == 0
    assert payload["summary"]["paper_candidate_rows"] == 0
    for target in payload["targets"]:
        assert target["can_create_candidate_pair"] is False
        assert target["can_create_paper_candidate"] is False
        assert target["exact_ready"] is False
        assert target["paper_candidate"] is False
        assert target["execution_ready"] is False


def test_only_recognized_safe_commands_are_recommended(tmp_path: Path) -> None:
    """The planner never invents command names; recommendations only reference
    commands that exist in the repo as safe saved-file or public-no-auth tools."""
    rows = [
        _kalshi_audit_row(
            ticker="KXBTC-26MAY3017-T100000",
            event_ticker="KXBTC-26MAY3017",
            asset="BTC",
            target_date="2026-05-30",
            threshold=100000.0,
        )
    ]
    input_dir = _setup(tmp_path, kalshi_rows=rows)
    report = build_crypto_peer_acquisition_plan_report(input_dir=input_dir, generated_at=_NOW)
    # Build the set of known safe repo commands by scanning scan.py for subparser names.
    scan_text = Path("scan.py").read_text(encoding="utf-8")
    # All commands the planner can recommend must be defined as subparsers in scan.py.
    recommended_commands: set[str] = set()
    for plan_list_key in (
        "polymarket_queries_recommended",
        "polymarket_clob_refresh_recommended",
        "cdna_targets_recommended",
        "kalshi_orderbook_targets_recommended",
    ):
        for item in report[plan_list_key]:
            cmd = item.get("safe_command")
            if cmd:
                recommended_commands.add(cmd)
    for target in report["targets"]:
        cmd = (target.get("recommended_next_action") or {}).get("safe_command")
        if cmd:
            recommended_commands.add(cmd)
    assert recommended_commands  # at least one recommendation expected
    for cmd in recommended_commands:
        # Each recommended command must be referenced as a subparser name in scan.py.
        assert f'"{cmd}"' in scan_text, f"recommended command {cmd} is not a registered scan.py subparser"


def test_planner_targeted_polymarket_query_command_is_available(tmp_path: Path) -> None:
    """Once --query / --asset / --target-date land in scan.py, the planner must
    flip ``polymarket_targeted_query_command_missing`` to ``False`` and stop
    listing the missing-command blocker, and the missing-command label must no
    longer be in ``safe_commands_missing``. The standalone blocker label
    constant remains importable so older reports stay readable."""
    rows = [
        _kalshi_audit_row(
            ticker="KXBTC-26MAY3017-T100000",
            event_ticker="KXBTC-26MAY3017",
            asset="BTC",
            target_date="2026-05-30",
            threshold=100000.0,
        )
    ]
    input_dir = _setup(tmp_path, kalshi_rows=rows)
    report = build_crypto_peer_acquisition_plan_report(input_dir=input_dir, generated_at=_NOW)
    assert report["summary"]["polymarket_targeted_query_command_missing"] is False
    assert "polymarket_targeted_query" not in report["summary"]["safe_commands_missing"]
    assert not any(
        item["blocker"] == "polymarket_targeted_query_command_missing"
        for item in report["summary"]["top_blockers"]
    )
    # The blocker label constant remains defined so older reports stay parseable.
    assert B_NO_SAFE_FETCH_COMMAND_FOUND


def test_markdown_uses_current_targeted_polymarket_command_text(tmp_path: Path) -> None:
    rows = [
        _kalshi_audit_row(
            ticker="KXBTC-26MAY2721-T67000",
            event_ticker="KXBTC-26MAY2721",
            asset="BTC",
            target_date="2026-05-27",
            threshold=67000.0,
        ),
        _kalshi_audit_row(
            ticker="KXBTC-26MAY2921-T67000",
            event_ticker="KXBTC-26MAY2921",
            asset="BTC",
            target_date="2026-05-29",
            threshold=67000.0,
        ),
        _kalshi_audit_row(
            ticker="KXETH-26MAY2721-T2500",
            event_ticker="KXETH-26MAY2721",
            asset="ETH",
            target_date="2026-05-27",
            threshold=2500.0,
        ),
    ]
    input_dir = _setup(tmp_path, kalshi_rows=rows)
    json_output = tmp_path / "plan.json"
    md_output = tmp_path / "plan.md"
    write_crypto_peer_acquisition_plan_files(
        input_dir=input_dir,
        json_output=json_output,
        markdown_output=md_output,
        generated_at=_NOW,
    )

    md = md_output.read_text(encoding="utf-8")
    assert "targeted query command is missing" not in md
    assert "supports targeted `--query`, `--asset`, and `--target-date` filters" in md
    assert "## Exact Next Commands" in md
    assert "### targeted Polymarket BTC 2026-05-27" in md
    assert "### targeted Polymarket BTC 2026-05-29" in md
    assert "### targeted Polymarket ETH 2026-05-27" in md
    assert "discover-polymarket-crypto-markets" in md
    assert "--query" in md
    assert "--asset BTC" in md
    assert "--asset ETH" in md
    assert "--target-date 2026-05-27" in md
    assert "--target-date 2026-05-29" in md
    polymarket_lines = [line for line in md.splitlines() if "discover-polymarket-crypto-markets" in line]
    assert polymarket_lines
    assert all(" --output " not in line for line in polymarket_lines)


def test_generated_command_snippets_use_only_supported_flags(tmp_path: Path) -> None:
    snapshot_path = _kalshi_snapshot(
        tmp_path / "reports" / "kalshi_crypto_snapshot.json",
        ["KXBTC-26MAY3017-T100000"],
    )
    rows = [
        _kalshi_audit_row(
            ticker="KXBTC-26MAY3017-T100000",
            event_ticker="KXBTC-26MAY3017",
            asset="BTC",
            target_date="2026-05-30",
            threshold=100000.0,
            raw_source_file=snapshot_path,
        )
    ]
    input_dir = _setup(tmp_path, kalshi_rows=rows)
    report = build_crypto_peer_acquisition_plan_report(input_dir=input_dir, generated_at=_NOW)

    invocations = []
    for key in (
        "polymarket_queries_recommended",
        "polymarket_clob_refresh_recommended",
        "cdna_targets_recommended",
        "kalshi_orderbook_targets_recommended",
    ):
        invocations.extend(
            item.get("safe_command_invocation")
            for item in report[key]
            if item.get("safe_command_invocation")
        )
    assert invocations
    # Polymarket discovery invocations must now use the new targeted --query / --asset
    # / --target-date flags; other plan lanes must NOT use them.
    polymarket_invocations = [i for i in invocations if "discover-polymarket-crypto-markets" in i]
    assert polymarket_invocations
    assert all("--query " in i and "--asset " in i and "--target-date " in i for i in polymarket_invocations)
    # The legacy top-level --output flag is never emitted (only --output-dir).
    assert all(" --output " not in i for i in polymarket_invocations)
    other_invocations = [i for i in invocations if "discover-polymarket-crypto-markets" not in i]
    for invocation in other_invocations:
        assert "--query " not in invocation
        assert "--target-date " not in invocation
    assert report["summary"]["command_validation_error_count"] == 0


def test_no_private_or_auth_strings_in_source_or_outputs(tmp_path: Path) -> None:
    rows = [
        _kalshi_audit_row(
            ticker="KXBTC-26MAY3017-T100000",
            event_ticker="KXBTC-26MAY3017",
            asset="BTC",
            target_date="2026-05-30",
            threshold=100000.0,
        )
    ]
    input_dir = _setup(tmp_path, kalshi_rows=rows)
    json_output = tmp_path / "plan.json"
    md_output = tmp_path / "plan.md"
    write_crypto_peer_acquisition_plan_files(
        input_dir=input_dir,
        json_output=json_output,
        markdown_output=md_output,
        generated_at=_NOW,
    )
    source_text = (Path("relative_value") / "crypto_peer_acquisition_plan.py").read_text(encoding="utf-8")
    output_text = json_output.read_text(encoding="utf-8") + md_output.read_text(encoding="utf-8")
    forbidden_patterns = (
        '"Authorization"',
        "'Authorization'",
        "Bearer ",
        "X-API-Key",
        "x-api-key",
        "PRIVATE_KEY",
        "private_key=",
        "signTypedData",
        "eth_signTypedData",
        "mnemonic_phrase",
        "seed_phrase=",
        "Cloudflare-Bypass-Token",
        'method="POST"',
        "method='POST'",
        'method="DELETE"',
        "method='DELETE'",
        "urlopen(",
        "requests.post(",
        "requests.put(",
        "requests.delete(",
        "/auth/api-key",
        "/clob/auth",
        "kalshi.com/trade-api/v2/orders",
        "kalshi.com/trade-api/v2/positions",
        "kalshi.com/trade-api/v2/balance",
        "kalshi.com/trade-api/v2/fills",
    )
    for forbidden in forbidden_patterns:
        assert forbidden not in source_text, f"forbidden token in module source: {forbidden}"
        assert forbidden not in output_text, f"forbidden token in outputs: {forbidden}"


def test_cli_writes_outputs_with_safe_summary_line(tmp_path: Path, capsys) -> None:
    rows = [
        _kalshi_audit_row(
            ticker="KXBTC-26MAY3017-T100000",
            event_ticker="KXBTC-26MAY3017",
            asset="BTC",
            target_date="2026-05-30",
            threshold=100000.0,
        )
    ]
    input_dir = _setup(tmp_path, kalshi_rows=rows)
    result = scan.main(
        [
            "crypto-peer-acquisition-plan",
            "--input-dir",
            str(input_dir),
            "--json-output",
            str(tmp_path / "plan.json"),
            "--markdown-output",
            str(tmp_path / "plan.md"),
        ]
    )
    assert result == 0
    stdout = capsys.readouterr().out
    assert "crypto_peer_acquisition_plan=OK" in stdout
    assert "diagnostic_only=true" in stdout
    assert "saved_files_only=true" in stdout
    assert "exact_ready_rows=0" in stdout
    assert "paper_candidate_rows=0" in stdout
    payload = json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))
    assert payload["schema_kind"] == "crypto_peer_acquisition_plan_v1"
    assert payload["summary"]["kalshi_typed_complete_grid_rows"] == 1


def test_ops_status_surfaces_crypto_peer_acquisition_plan(tmp_path: Path) -> None:
    plan_payload = {
        "schema_kind": "crypto_peer_acquisition_plan_v1",
        "schema_version": 1,
        "source": "crypto_peer_acquisition_plan_v1",
        "generated_at": "2026-05-27T00:00:00+00:00",
        "input_dir": "reports",
        "diagnostic_only": True,
        "saved_files_only": True,
        "summary": {
            "kalshi_typed_complete_grid_rows": 570,
            "unique_assets": 2,
            "unique_dates": 8,
            "unique_thresholds": 540,
            "top_target_assets": [{"asset": "BTC", "count": 445}, {"asset": "ETH", "count": 125}],
            "top_target_dates": [{"target_date": "2026-05-30", "count": 80}],
            "polymarket_queries_recommended": 8,
            "polymarket_clob_refresh_recommended": 0,
            "cdna_targets_recommended": 8,
            "kalshi_orderbook_targets_recommended": 12,
            "safe_commands_referenced": [
                "discover-polymarket-crypto-markets",
                "enrich-kalshi-orderbooks",
                "parse-crypto-com-predict-cdna-fixtures",
            ],
            "safe_commands_missing": [],
            "exact_ready_rows": 0,
            "paper_candidate_rows": 0,
            "top_blockers": [
                {"blocker": "missing_polymarket_peer_for_kalshi_grid", "count": 570},
                {"blocker": "missing_cdna_peer_for_kalshi_grid", "count": 570},
            ],
            "top_20_targets": [
                {
                    "kalshi_ticker": "KXBTC-26MAY3017-T100000",
                    "asset": "BTC",
                    "target_date": "2026-05-30",
                    "priority_score": 168.5,
                    "recommended_next_action": "DISCOVER_POLYMARKET_FOR_ASSET_DATE",
                    "safe_command": "discover-polymarket-crypto-markets",
                }
            ],
        },
        "targets": [],
        "polymarket_queries_recommended": [],
        "cdna_targets_recommended": [],
        "kalshi_orderbook_targets_recommended": [],
        "warnings": [],
        "safety": {"diagnostic_only": True},
    }
    (tmp_path / "crypto_peer_acquisition_plan.json").write_text(json.dumps(plan_payload), encoding="utf-8")
    from relative_value.relative_value_ops_status import (
        build_relative_value_ops_status_report,
        render_relative_value_ops_status_markdown,
    )

    report = build_relative_value_ops_status_report(input_dir=tmp_path, generated_at=_NOW)
    block = report["summary"]["crypto_peer_acquisition_plan"]
    assert block["present"] is True
    assert block["kalshi_typed_complete_grid_rows"] == 570
    assert block["top_target_asset"] == "BTC"
    assert block["top_target_date"] == "2026-05-30"
    assert block["polymarket_queries_recommended"] == 8
    assert block["recommended_next_command"] == "discover-polymarket-crypto-markets"
    assert block["exact_ready_rows"] == 0
    assert block["paper_candidate_rows"] == 0
    md = render_relative_value_ops_status_markdown(report)
    assert "crypto_peer_acquisition_plan" in md
    assert "kalshi_typed_complete_grid_rows: `570`" in md
    forbidden = "PAPER" + "_CANDIDATE"
    assert forbidden not in md
