from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import scan
from relative_value.crypto_payoff_calendar_audit import (
    B_DAILY_DIRECTION_RULES_MISSING,
    B_DEADLINE_TOUCH_NOT_CLOSE_PRICE,
    B_INTRADAY_TOUCH_NOT_POINT_IN_TIME,
    B_OPEN_CLOSE_REFERENCE_MISSING,
    B_PAYOFF_SHAPE_MISMATCH,
    CLASS_BASIS_RISK_ONLY,
    CLASS_EXACT_SHAPE_POSSIBLE,
    CLASS_MANUAL_RULES_NEEDED,
    CLASS_NO_CURRENT_PEER,
    SHAPE_ALL_TIME_HIGH_BY_DATE,
    SHAPE_AMBIGUOUS,
    SHAPE_DAILY_5PM_PRICE_THRESHOLD,
    SHAPE_DAILY_DIRECTION_UP_DOWN,
    SHAPE_DEADLINE_TOUCH_THRESHOLD,
    SHAPE_HOURLY_POINT_IN_TIME_PRICE,
    SHAPE_INTRADAY_TOUCH_THRESHOLD,
    SHAPE_POINT_IN_TIME_PRICE_THRESHOLD,
    SHAPE_RANGE_BUCKET_AT_TIME,
    SHAPE_WEEKLY_FRIDAY_CLOSE_THRESHOLD,
    build_crypto_payoff_calendar_audit_report,
    write_crypto_payoff_calendar_audit_files,
)
from relative_value.crypto_manual_discovery_workbench import (
    build_crypto_manual_discovery_workbench_report,
    write_crypto_manual_discovery_workbench_files,
)


_NOW = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)


def _kalshi_row(
    *,
    ticker: str,
    title: str,
    target_date: str,
    target_time: str,
    timezone_label: str,
    threshold: float,
    comparator: str = "above",
    rules_text: str = "",
    market_shape: str = "point_in_time_threshold",
    settlement_source: str = "CF Benchmarks BRTI",
) -> dict[str, Any]:
    return {
        "row_id": f"kalshi_crypto::{ticker}",
        "ticker": ticker,
        "event_ticker": ticker.rsplit("-", 1)[0],
        "market_id": ticker,
        "venue": "kalshi",
        "asset": "BTC" if ticker.startswith("KXBTC") else ("ETH" if ticker.startswith("KXETH") else "SOL"),
        "threshold": threshold,
        "comparator": comparator,
        "target_date": target_date,
        "target_time": target_time,
        "timezone": timezone_label,
        "settlement_source": settlement_source,
        "settlement_source_url": None,
        "settlement_close_time": f"{target_date}T{target_time}:00+00:00",
        "market_shape": market_shape,
        "title": title,
        "settlement_rules_text_preview": rules_text or title,
        "quote": {"present": False, "bid": None, "ask": None, "bid_size": None, "ask_size": None, "observed_at": None},
        "raw_source_file": "reports/kalshi_markets_snapshot.json",
    }


def _polymarket_row(
    *,
    row_id: str,
    title: str,
    question: str | None,
    upstream_shape: str,
    asset: str,
    threshold: float | None,
    comparator: str | None,
    measurement_date: str | None,
    measurement_time: str | None,
    price_source_index: str | None = None,
    token_ids: list[str] | None = None,
    rules_text: str | None = None,
) -> dict[str, Any]:
    return {
        "row_id": row_id,
        "market_id": row_id.replace("poly_", "m_"),
        "condition_id": f"cond_{row_id}",
        "event_id": f"evt_{row_id}",
        "event_slug": row_id,
        "title": title,
        "question": question,
        "family": "CRYPTO",
        "market_shape": upstream_shape,
        "typed_keys": {
            "asset": asset,
            "threshold_value": threshold,
            "threshold_operator": comparator,
            "measurement_date": measurement_date,
            "measurement_time": measurement_time,
            "price_source_index": price_source_index,
        },
        "token_ids": list(token_ids or []),
        "clob_refresh": {"attached_quote": {"attached": False}},
        "settlement_rules_text_preview": rules_text or title,
        "raw_source_file": "reports/polymarket_taxonomy_shape_scout_enriched.json",
    }


def _cdna_row(
    *,
    event_id: str,
    asset: str,
    market_type: str,
    threshold: float | None,
    comparator: str | None,
    deadline: str,
    title: str,
    source: str = "Nadex BTC Index",
) -> dict[str, Any]:
    return {
        "asset": asset,
        "market_id": event_id,
        "event_id": event_id,
        "market_type": market_type,
        "threshold_value": threshold,
        "comparator": comparator,
        "deadline_or_expiry": deadline,
        "title": title,
        "price_source_index": source,
        "settlement_source_url": None,
        "raw_source_file": "reports/cdna_research.json",
    }


def _kalshi_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_kind": "kalshi_crypto_typed_key_audit_v1",
        "source": "kalshi_crypto_typed_key_audit_v1",
        "summary": {"kalshi_crypto_rows": len(rows)},
        "rows": rows,
        "safety": {"diagnostic_only": True},
    }


def _polymarket_enriched_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_kind": "polymarket_taxonomy_shape_scout_enriched_v1",
        "source": "polymarket_taxonomy_shape_scout_enriched_v1",
        "rows": rows,
        "summary": {},
        "safety": {},
    }


def _polymarket_pit_audit_payload(rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source": "polymarket_point_in_time_typed_key_audit_v1",
        "rows": rows or [],
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


def _cdna_basis_payload() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source": "cdna_crypto_basis_risk_scout_v1",
        "rows": [],
        "summary": {},
        "safety": {},
    }


def _setup(
    tmp_path: Path,
    *,
    kalshi_rows: list[dict[str, Any]] | None = None,
    polymarket_rows: list[dict[str, Any]] | None = None,
    cdna_rows: list[dict[str, Any]] | None = None,
) -> Path:
    (tmp_path / "kalshi_crypto_typed_key_audit.json").write_text(
        json.dumps(_kalshi_payload(kalshi_rows or [])), encoding="utf-8"
    )
    (tmp_path / "polymarket_taxonomy_shape_scout_enriched.json").write_text(
        json.dumps(_polymarket_enriched_payload(polymarket_rows or [])), encoding="utf-8"
    )
    (tmp_path / "polymarket_point_in_time_typed_key_audit.json").write_text(
        json.dumps(_polymarket_pit_audit_payload()), encoding="utf-8"
    )
    (tmp_path / "crypto_com_predict_cdna_research_snapshot.json").write_text(
        json.dumps(_cdna_snapshot_payload(cdna_rows or [])), encoding="utf-8"
    )
    (tmp_path / "cdna_crypto_basis_risk_scout.json").write_text(
        json.dumps(_cdna_basis_payload()), encoding="utf-8"
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Shape classification tests
# ---------------------------------------------------------------------------


def test_polymarket_hit_by_date_is_deadline_touch_not_point_in_time(tmp_path: Path) -> None:
    pm = _polymarket_row(
        row_id="poly_hit_btc_150k",
        title="Will Bitcoin hit $150k by June 30, 2026?",
        question="Will Bitcoin hit $150k by June 30, 2026?",
        upstream_shape="crypto_deadline_range_hit",
        asset="BTC",
        threshold=150000.0,
        comparator=">=",
        measurement_date="June 30, 2026",
        measurement_time="11:59PM ET",
        price_source_index="Binance",
    )
    input_dir = _setup(tmp_path, polymarket_rows=[pm])
    report = build_crypto_payoff_calendar_audit_report(input_dir=input_dir, generated_at=_NOW)
    row = report["rows"][0]
    assert row["payoff_shape"] == SHAPE_DEADLINE_TOUCH_THRESHOLD
    assert B_DEADLINE_TOUCH_NOT_CLOSE_PRICE in row["blockers"]
    assert row["treats_intraday_touch_as_point_in_time"] is False
    assert row["exact_ready"] is False


def test_polymarket_up_or_down_is_daily_direction(tmp_path: Path) -> None:
    pm = _polymarket_row(
        row_id="poly_btc_ud_dec19",
        title="Bitcoin Up or Down - December 19, 11:30AM-11:35AM ET",
        question="Bitcoin Up or Down - December 19, 11:30AM-11:35AM ET",
        upstream_shape="crypto_deadline_range_hit",
        asset="BTC",
        threshold=None,
        comparator=None,
        measurement_date="December 19",
        measurement_time="11:35AM ET",
        price_source_index="Chainlink",
    )
    input_dir = _setup(tmp_path, polymarket_rows=[pm])
    report = build_crypto_payoff_calendar_audit_report(input_dir=input_dir, generated_at=_NOW)
    row = report["rows"][0]
    assert row["payoff_shape"] == SHAPE_DAILY_DIRECTION_UP_DOWN
    assert B_DAILY_DIRECTION_RULES_MISSING in row["blockers"]
    assert B_OPEN_CLOSE_REFERENCE_MISSING in row["blockers"]
    assert row["treats_daily_direction_as_threshold"] is False


def test_polymarket_all_time_high_classified_explicitly(tmp_path: Path) -> None:
    pm = _polymarket_row(
        row_id="poly_btc_ath_march",
        title="Bitcoin all time high by March 31, 2026?",
        question=None,
        upstream_shape="all_time_high_by_date",
        asset="BTC",
        threshold=1.0,
        comparator=">=",
        measurement_date="March 31, 2026",
        measurement_time="11:59PM ET",
        price_source_index="Binance",
    )
    input_dir = _setup(tmp_path, polymarket_rows=[pm])
    report = build_crypto_payoff_calendar_audit_report(input_dir=input_dir, generated_at=_NOW)
    assert report["rows"][0]["payoff_shape"] == SHAPE_ALL_TIME_HIGH_BY_DATE


def test_kalshi_5pm_close_classified_as_daily_5pm(tmp_path: Path) -> None:
    # 5pm EDT = 21:00 UTC. May 28, 2026 is a Thursday → daily_5pm, not Friday.
    kalshi = _kalshi_row(
        ticker="KXBTC-26MAY2817-T100000",
        title="Bitcoin price on May 28, 2026?",
        target_date="2026-05-28",
        target_time="21:00",
        timezone_label="UTC",
        threshold=100000.0,
        rules_text=(
            "If the simple average of CF Benchmarks' Bitcoin Real-Time Index (BRTI) is above "
            "100000 at 5 PM EDT on May 28, 2026, then the market resolves to Yes."
        ),
    )
    input_dir = _setup(tmp_path, kalshi_rows=[kalshi])
    report = build_crypto_payoff_calendar_audit_report(input_dir=input_dir, generated_at=_NOW)
    row = report["rows"][0]
    assert row["payoff_shape"] == SHAPE_DAILY_5PM_PRICE_THRESHOLD


def test_kalshi_friday_5pm_classified_as_weekly_friday(tmp_path: Path) -> None:
    # 2026-05-29 is a Friday — weekly_friday_close_threshold instead of daily_5pm.
    kalshi = _kalshi_row(
        ticker="KXBTC-26MAY2917-T100000",
        title="Bitcoin price on May 29, 2026?",
        target_date="2026-05-29",
        target_time="21:00",
        timezone_label="UTC",
        threshold=100000.0,
        rules_text=(
            "If the simple average of CF Benchmarks' Bitcoin Real-Time Index (BRTI) is above "
            "100000 at 5 PM EDT on May 29, 2026, then the market resolves to Yes."
        ),
    )
    input_dir = _setup(tmp_path, kalshi_rows=[kalshi])
    report = build_crypto_payoff_calendar_audit_report(input_dir=input_dir, generated_at=_NOW)
    assert report["rows"][0]["payoff_shape"] == SHAPE_WEEKLY_FRIDAY_CLOSE_THRESHOLD


def test_kalshi_hourly_eth_classified_as_hourly_point_in_time(tmp_path: Path) -> None:
    kalshi = _kalshi_row(
        ticker="KXETH-26MAY2207-T2839.99",
        title="Ethereum price at May 22, 2026 at 7am EDT?",
        target_date="2026-05-22",
        target_time="11:00",
        timezone_label="UTC",
        threshold=2839.99,
        rules_text=(
            "If CF Benchmarks' Ethereum Real-Time Index (ERTI) is above 2839.99 at 7 AM EDT "
            "on May 22, 2026, then the market resolves to Yes."
        ),
        settlement_source="CF Benchmarks ERTI",
    )
    input_dir = _setup(tmp_path, kalshi_rows=[kalshi])
    report = build_crypto_payoff_calendar_audit_report(input_dir=input_dir, generated_at=_NOW)
    row = report["rows"][0]
    assert row["payoff_shape"] == SHAPE_HOURLY_POINT_IN_TIME_PRICE
    assert row["reference_price_type"] == "CF_ERTI"


def test_kalshi_range_bucket_classified_as_range_bucket_at_time(tmp_path: Path) -> None:
    kalshi = _kalshi_row(
        ticker="KXBTC-26MAY2917-B88750",
        title="Bitcoin price range on May 29, 2026?",
        target_date="2026-05-29",
        target_time="21:00",
        timezone_label="UTC",
        threshold=88750.0,
        comparator="between",
        market_shape="range_bucket",
        rules_text=(
            "If CF Benchmarks' Bitcoin Real-Time Index (BRTI) is between 88500-88999.99 at "
            "5 PM EDT on May 29, 2026, then the market resolves to Yes."
        ),
    )
    input_dir = _setup(tmp_path, kalshi_rows=[kalshi])
    report = build_crypto_payoff_calendar_audit_report(input_dir=input_dir, generated_at=_NOW)
    assert report["rows"][0]["payoff_shape"] == SHAPE_RANGE_BUCKET_AT_TIME


def test_cdna_eth_point_in_time_classified_as_point_in_time(tmp_path: Path) -> None:
    cdna = _cdna_row(
        event_id="cdna_eth_pit_may23",
        asset="ETH",
        market_type="point_in_time_threshold",
        threshold=2050.0,
        comparator=">",
        deadline="May 23, 2026 at 9:00 am Eastern Time",
        title="Ethereum price on 23 May at 9:00 am ET",
        source="CDNA Rule 14.72 / Nadex ETH Index",
    )
    input_dir = _setup(tmp_path, cdna_rows=[cdna])
    report = build_crypto_payoff_calendar_audit_report(input_dir=input_dir, generated_at=_NOW)
    row = report["rows"][0]
    assert row["payoff_shape"] in {SHAPE_HOURLY_POINT_IN_TIME_PRICE, SHAPE_POINT_IN_TIME_PRICE_THRESHOLD}
    assert row["asset"] == "ETH"


# ---------------------------------------------------------------------------
# Compatibility matrix tests
# ---------------------------------------------------------------------------


def test_compatibility_matrix_blocks_touch_versus_close(tmp_path: Path) -> None:
    kalshi = _kalshi_row(
        ticker="KXBTC-26MAY2917-T100000",
        title="Bitcoin price on May 29, 2026?",
        target_date="2026-05-29",
        target_time="21:00",
        timezone_label="UTC",
        threshold=100000.0,
        rules_text="CF Benchmarks BRTI is above 100000 at 5 PM EDT on May 29, 2026.",
    )
    pm = _polymarket_row(
        row_id="poly_btc_touch_may",
        title="Will Bitcoin touch $100,000 by May 29, 2026?",
        question="Will Bitcoin touch $100,000 any time before May 29, 2026?",
        upstream_shape="crypto_deadline_range_hit",
        asset="BTC",
        threshold=100000.0,
        comparator=">=",
        measurement_date="May 29, 2026",
        measurement_time="11:59PM ET",
        price_source_index="Binance",
    )
    input_dir = _setup(tmp_path, kalshi_rows=[kalshi], polymarket_rows=[pm])
    report = build_crypto_payoff_calendar_audit_report(input_dir=input_dir, generated_at=_NOW)
    kalshi_row = next(r for r in report["rows"] if r["venue"] == "kalshi")
    pm_row = next(r for r in report["rows"] if r["venue"] == "polymarket")
    assert kalshi_row["best_peer"]
    assert kalshi_row["comparability_class"] == CLASS_BASIS_RISK_ONLY
    assert pm_row["comparability_class"] == CLASS_BASIS_RISK_ONLY
    assert B_PAYOFF_SHAPE_MISMATCH in kalshi_row["blockers"]
    assert B_DEADLINE_TOUCH_NOT_CLOSE_PRICE in pm_row["blockers"]
    # Kalshi exact_ready stays false.
    assert kalshi_row["exact_ready"] is False
    assert pm_row["exact_ready"] is False


def test_compatibility_matrix_up_down_versus_threshold_is_basis_risk(tmp_path: Path) -> None:
    kalshi = _kalshi_row(
        ticker="KXBTC-26MAY2817-T100000",
        title="Bitcoin price on May 28, 2026?",
        target_date="2026-05-28",
        target_time="21:00",
        timezone_label="UTC",
        threshold=100000.0,
        rules_text="CF Benchmarks BRTI is above 100000 at 5 PM EDT on May 28, 2026.",
    )
    pm = _polymarket_row(
        row_id="poly_btc_ud_may28",
        title="Bitcoin Up or Down - May 28, 2026",
        question="Will Bitcoin be up or down on May 28, 2026?",
        upstream_shape="crypto_deadline_range_hit",
        asset="BTC",
        threshold=None,
        comparator=None,
        measurement_date="May 28, 2026",
        measurement_time="4:00 PM ET",
        price_source_index="Chainlink",
    )
    input_dir = _setup(tmp_path, kalshi_rows=[kalshi], polymarket_rows=[pm])
    report = build_crypto_payoff_calendar_audit_report(input_dir=input_dir, generated_at=_NOW)
    kalshi_row = next(r for r in report["rows"] if r["venue"] == "kalshi")
    pm_row = next(r for r in report["rows"] if r["venue"] == "polymarket")
    assert kalshi_row["comparability_class"] == CLASS_BASIS_RISK_ONLY
    assert pm_row["comparability_class"] == CLASS_BASIS_RISK_ONLY
    assert B_OPEN_CLOSE_REFERENCE_MISSING in pm_row["blockers"]


def test_compatibility_kalshi_5pm_versus_kalshi_5pm_is_exact_shape_possible(tmp_path: Path) -> None:
    # Same-venue pairing should NOT generate a peer (we exclude same venue);
    # but cross-venue same shape with same date should pair as exact_shape_possible.
    kalshi = _kalshi_row(
        ticker="KXBTC-26MAY2817-T100000",
        title="Bitcoin price on May 28, 2026?",
        target_date="2026-05-28",
        target_time="21:00",
        timezone_label="UTC",
        threshold=100000.0,
        rules_text="CF Benchmarks BRTI is above 100000 at 5 PM EDT on May 28, 2026.",
    )
    # Polymarket point-in-time at 5 PM ET on same date with explicit time.
    pm = _polymarket_row(
        row_id="poly_btc_pit_may28",
        title="Will Bitcoin be above $100,000 at 5pm ET on May 28, 2026?",
        question=None,
        upstream_shape="point_in_time_threshold",
        asset="BTC",
        threshold=100000.0,
        comparator=">=",
        measurement_date="May 28, 2026",
        measurement_time="5:00 PM ET",
        price_source_index="CF Benchmarks BRTI",
    )
    input_dir = _setup(tmp_path, kalshi_rows=[kalshi], polymarket_rows=[pm])
    report = build_crypto_payoff_calendar_audit_report(input_dir=input_dir, generated_at=_NOW)
    kalshi_row = next(r for r in report["rows"] if r["venue"] == "kalshi")
    pm_row = next(r for r in report["rows"] if r["venue"] == "polymarket")
    assert kalshi_row["payoff_shape"] == SHAPE_DAILY_5PM_PRICE_THRESHOLD
    assert pm_row["payoff_shape"] == SHAPE_DAILY_5PM_PRICE_THRESHOLD
    assert kalshi_row["comparability_class"] == CLASS_EXACT_SHAPE_POSSIBLE
    assert pm_row["comparability_class"] == CLASS_EXACT_SHAPE_POSSIBLE
    # Even at exact_shape_possible, the row is still diagnostic-only.
    assert kalshi_row["exact_ready"] is False
    assert pm_row["exact_ready"] is False
    assert kalshi_row["can_create_candidate_pair"] is False
    assert pm_row["can_create_candidate_pair"] is False


# ---------------------------------------------------------------------------
# Safety tests
# ---------------------------------------------------------------------------


def test_no_paper_candidate_emitted_or_exact_ready_after_full_run(tmp_path: Path) -> None:
    kalshi = _kalshi_row(
        ticker="KXBTC-26MAY2917-T100000",
        title="Bitcoin price on May 29, 2026?",
        target_date="2026-05-29",
        target_time="21:00",
        timezone_label="UTC",
        threshold=100000.0,
        rules_text="CF Benchmarks BRTI is above 100000 at 5 PM EDT on May 29, 2026.",
    )
    input_dir = _setup(tmp_path, kalshi_rows=[kalshi])
    json_output = tmp_path / "calendar.json"
    md_output = tmp_path / "calendar.md"
    write_crypto_payoff_calendar_audit_files(
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
    for row in payload["rows"]:
        assert row["can_create_candidate_pair"] is False
        assert row["can_create_paper_candidate"] is False
        assert row["exact_ready"] is False
        assert row["paper_candidate"] is False
        assert row["execution_ready"] is False


def test_cli_writes_outputs(tmp_path: Path, capsys) -> None:
    kalshi = _kalshi_row(
        ticker="KXBTC-26MAY2817-T100000",
        title="Bitcoin price on May 28, 2026?",
        target_date="2026-05-28",
        target_time="21:00",
        timezone_label="UTC",
        threshold=100000.0,
        rules_text="CF Benchmarks BRTI is above 100000 at 5 PM EDT on May 28, 2026.",
    )
    input_dir = _setup(tmp_path, kalshi_rows=[kalshi])
    result = scan.main(
        [
            "crypto-payoff-calendar-audit",
            "--input-dir",
            str(input_dir),
            "--json-output",
            str(tmp_path / "calendar.json"),
            "--markdown-output",
            str(tmp_path / "calendar.md"),
        ]
    )
    assert result == 0
    stdout = capsys.readouterr().out
    assert "crypto_payoff_calendar_audit=OK" in stdout
    assert "diagnostic_only=true" in stdout
    assert "exact_ready_rows=0" in stdout
    assert "paper_candidate_rows=0" in stdout


# ---------------------------------------------------------------------------
# Workbench tests
# ---------------------------------------------------------------------------


def test_workbench_emits_manual_discovery_target_for_missing_rules(tmp_path: Path) -> None:
    # Build a single Polymarket up/down row → workbench should emit a target in
    # the polymarket_up_down group with rules-missing blocker context.
    pm = _polymarket_row(
        row_id="poly_btc_ud_jun15",
        title="Bitcoin Up or Down - June 15, 2026 11:30AM ET",
        question="Bitcoin Up or Down - June 15, 2026 11:30AM ET",
        upstream_shape="crypto_deadline_range_hit",
        asset="BTC",
        threshold=None,
        comparator=None,
        measurement_date="June 15, 2026",
        measurement_time="11:30AM ET",
        price_source_index="Chainlink",
    )
    input_dir = _setup(tmp_path, polymarket_rows=[pm])
    # First produce the audit file the workbench reads.
    write_crypto_payoff_calendar_audit_files(
        input_dir=input_dir,
        json_output=input_dir / "crypto_payoff_calendar_audit.json",
        markdown_output=input_dir / "crypto_payoff_calendar_audit.md",
        generated_at=_NOW,
    )
    report = build_crypto_manual_discovery_workbench_report(
        input_dir=input_dir, generated_at=_NOW
    )
    pm_groups = [g for g in report["groups"] if g["group_name"] == "polymarket_up_down"]
    assert pm_groups
    targets = pm_groups[0]["targets"]
    assert targets
    target = targets[0]
    assert target["payoff_shape"] == SHAPE_DAILY_DIRECTION_UP_DOWN
    # Manual manifest candidate is template-only, never approved.
    mmc = target["manual_manifest_candidate"]
    assert mmc["approved"] is False
    assert mmc["can_create_candidate_pair"] is False
    assert mmc["can_create_paper_candidate"] is False
    # Up/down requires open/close reference.
    assert B_OPEN_CLOSE_REFERENCE_MISSING in target["blockers"]
    assert report["summary"]["exact_ready_rows"] == 0
    assert report["summary"]["paper_candidate_rows"] == 0


def test_workbench_no_paper_candidate_emitted(tmp_path: Path) -> None:
    kalshi = _kalshi_row(
        ticker="KXBTC-26MAY2917-T100000",
        title="Bitcoin price on May 29, 2026?",
        target_date="2026-05-29",
        target_time="21:00",
        timezone_label="UTC",
        threshold=100000.0,
    )
    input_dir = _setup(tmp_path, kalshi_rows=[kalshi])
    write_crypto_payoff_calendar_audit_files(
        input_dir=input_dir,
        json_output=input_dir / "crypto_payoff_calendar_audit.json",
        markdown_output=input_dir / "crypto_payoff_calendar_audit.md",
        generated_at=_NOW,
    )
    json_output = tmp_path / "workbench.json"
    md_output = tmp_path / "workbench.md"
    write_crypto_manual_discovery_workbench_files(
        input_dir=input_dir,
        json_output=json_output,
        markdown_output=md_output,
        generated_at=_NOW,
    )
    forbidden = "PAPER" + "_CANDIDATE"
    for path in (json_output, md_output):
        text = path.read_text(encoding="utf-8")
        assert forbidden not in text


def test_workbench_cli_writes_outputs(tmp_path: Path, capsys) -> None:
    kalshi = _kalshi_row(
        ticker="KXBTC-26MAY2917-T100000",
        title="Bitcoin price on May 29, 2026?",
        target_date="2026-05-29",
        target_time="21:00",
        timezone_label="UTC",
        threshold=100000.0,
    )
    input_dir = _setup(tmp_path, kalshi_rows=[kalshi])
    # Audit step first so workbench has its input.
    assert scan.main(
        [
            "crypto-payoff-calendar-audit",
            "--input-dir",
            str(input_dir),
            "--json-output",
            str(input_dir / "crypto_payoff_calendar_audit.json"),
            "--markdown-output",
            str(input_dir / "crypto_payoff_calendar_audit.md"),
        ]
    ) == 0
    capsys.readouterr()  # drain
    assert scan.main(
        [
            "crypto-manual-discovery-workbench",
            "--input-dir",
            str(input_dir),
            "--json-output",
            str(tmp_path / "workbench.json"),
            "--markdown-output",
            str(tmp_path / "workbench.md"),
        ]
    ) == 0
    stdout = capsys.readouterr().out
    assert "crypto_manual_discovery_workbench=OK" in stdout
    assert "exact_ready_rows=0" in stdout
    assert "paper_candidate_rows=0" in stdout


def test_no_private_or_auth_strings_in_module_or_outputs(tmp_path: Path) -> None:
    kalshi = _kalshi_row(
        ticker="KXBTC-26MAY2917-T100000",
        title="Bitcoin price on May 29, 2026?",
        target_date="2026-05-29",
        target_time="21:00",
        timezone_label="UTC",
        threshold=100000.0,
    )
    input_dir = _setup(tmp_path, kalshi_rows=[kalshi])
    json_output = tmp_path / "calendar.json"
    md_output = tmp_path / "calendar.md"
    write_crypto_payoff_calendar_audit_files(
        input_dir=input_dir,
        json_output=json_output,
        markdown_output=md_output,
        generated_at=_NOW,
    )
    source_paths = [
        Path("relative_value") / "crypto_payoff_calendar_audit.py",
        Path("relative_value") / "crypto_manual_discovery_workbench.py",
    ]
    text = "".join(p.read_text(encoding="utf-8") for p in source_paths)
    text += json_output.read_text(encoding="utf-8") + md_output.read_text(encoding="utf-8")
    forbidden_patterns = (
        '"Authorization"',
        "'Authorization'",
        "Bearer ",
        "X-API-Key",
        "PRIVATE_KEY",
        "private_key=",
        "signTypedData",
        "mnemonic_phrase",
        "seed_phrase=",
        'method="POST"',
        "method='POST'",
        'method="DELETE"',
        "method='DELETE'",
        "urlopen(",
        "requests.post(",
        "requests.put(",
        "requests.delete(",
        "/auth/api-key",
        "kalshi.com/trade-api/v2/orders",
    )
    for forbidden in forbidden_patterns:
        assert forbidden not in text, f"forbidden token found: {forbidden}"


def test_ops_status_surfaces_crypto_payoff_calendar_blocks(tmp_path: Path) -> None:
    audit_payload = {
        "schema_kind": "crypto_payoff_calendar_audit_v1",
        "schema_version": 1,
        "source": "crypto_payoff_calendar_audit_v1",
        "generated_at": _NOW.isoformat(),
        "input_dir": "reports",
        "summary": {
            "total_crypto_rows": 100,
            "venues": ["kalshi", "polymarket", "cdna"],
            "exact_shape_possible_rows": 3,
            "basis_risk_only_rows": 42,
            "manual_rules_needed_rows": 17,
            "reference_only_rows": 0,
            "no_current_peer_rows": 38,
            "exact_ready_rows": 0,
            "paper_candidate_rows": 0,
            "counts_by_shape_and_venue": {
                "daily_5pm_price_threshold": {"kalshi": 20, "polymarket": 3},
                "deadline_touch_threshold": {"polymarket": 24},
            },
            "counts_by_class_and_venue": {
                "exact_shape_possible": {"kalshi": 1, "polymarket": 1, "cdna": 1},
                "basis_risk_only": {"kalshi": 20, "polymarket": 22},
            },
            "top_blockers": [
                {"blocker": "polymarket_rules_missing", "count": 40},
                {"blocker": "deadline_touch_not_close_price", "count": 24},
            ],
        },
        "rows": [],
    }
    workbench_payload = {
        "schema_kind": "crypto_manual_discovery_workbench_v1",
        "schema_version": 1,
        "source": "crypto_manual_discovery_workbench_v1",
        "generated_at": _NOW.isoformat(),
        "input_dir": "reports",
        "summary": {
            "total_eligible_audit_rows": 100,
            "group_count": 7,
            "targets_emitted": 25,
            "top_target_group": "kalshi_daily_5pm",
            "top_target_venue": "kalshi",
            "top_target_asset": "BTC",
            "top_target_date": "2026-05-28",
            "top_target_payoff_shape": "daily_5pm_price_threshold",
            "top_target_comparability_class": "exact_shape_possible",
            "exact_ready_rows": 0,
            "paper_candidate_rows": 0,
        },
        "groups": [{"group_name": "kalshi_daily_5pm", "venue": "kalshi", "targets_emitted": 10, "total_eligible_rows": 20}],
    }
    (tmp_path / "crypto_payoff_calendar_audit.json").write_text(json.dumps(audit_payload), encoding="utf-8")
    (tmp_path / "crypto_manual_discovery_workbench.json").write_text(json.dumps(workbench_payload), encoding="utf-8")
    from relative_value.relative_value_ops_status import (
        build_relative_value_ops_status_report,
        render_relative_value_ops_status_markdown,
    )

    report = build_relative_value_ops_status_report(input_dir=tmp_path, generated_at=_NOW)
    audit_block = report["summary"]["crypto_payoff_calendar_audit"]
    workbench_block = report["summary"]["crypto_manual_discovery_workbench"]
    assert audit_block["present"] is True
    assert audit_block["total_crypto_rows"] == 100
    assert audit_block["exact_shape_possible_rows"] == 3
    assert audit_block["basis_risk_only_rows"] == 42
    assert audit_block["manual_rules_needed_rows"] == 17
    assert audit_block["exact_ready_rows"] == 0
    assert audit_block["paper_candidate_rows"] == 0
    assert workbench_block["present"] is True
    assert workbench_block["top_target_group"] == "kalshi_daily_5pm"
    assert workbench_block["top_target_payoff_shape"] == "daily_5pm_price_threshold"
    md = render_relative_value_ops_status_markdown(report)
    assert "crypto_payoff_calendar_audit" in md
    assert "crypto_manual_discovery_workbench" in md
    assert "exact_shape_possible_rows: `3`" in md
    forbidden = "PAPER" + "_CANDIDATE"
    assert forbidden not in md
