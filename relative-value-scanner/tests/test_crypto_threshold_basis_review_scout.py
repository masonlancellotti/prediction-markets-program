from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import scan
from relative_value.crypto_threshold_basis_review_scout import (
    ACTION_BASIS_RISK_REVIEW,
    ACTION_IGNORE_BLOCKED,
    B_GRID_MISMATCH,
    B_SOURCE_MISMATCH,
    B_TIME_MISMATCH,
    build_crypto_threshold_basis_review_scout_report,
    write_crypto_threshold_basis_review_scout_files,
)


NOW = datetime(2026, 5, 29, 9, 25, tzinfo=timezone.utc)


def test_typed_key_extraction_and_matching_for_btc_threshold_fixture(tmp_path: Path) -> None:
    kalshi, poly = _write_evidence(tmp_path)

    report = _build(kalshi, poly)
    row = next(row for row in report["rows"] if row["direction"] == "KALSHI_YES_POLYMARKET_NO")

    assert report["kalshi_rows_loaded"] == 1
    assert report["polymarket_rows_loaded"] == 1
    assert row["asset"] == "BTC"
    assert row["threshold"] == 70000.0
    assert row["target_date"] == "2026-05-29"
    assert row["target_time_kalshi"] == "17:00 ET"
    assert row["target_time_polymarket"] == "12:00 ET"


def test_source_index_mismatch_always_blocks_exact_but_basis_review_can_surface(tmp_path: Path) -> None:
    kalshi, poly = _write_evidence(tmp_path)

    report = _build(kalshi, poly)
    row = next(row for row in report["rows"] if row["direction"] == "KALSHI_YES_POLYMARKET_NO")

    assert B_SOURCE_MISMATCH in row["blockers"]
    assert B_TIME_MISMATCH in row["blockers"]
    assert row["action"] == "WATCH"
    assert row["strict_exact_arb"] is False
    assert row["exact_ready"] is False
    assert row["paper_candidate"] is False


def test_mismatched_threshold_is_blocked_unmatched(tmp_path: Path) -> None:
    kalshi, poly = _write_evidence(tmp_path, polymarket_threshold=72000)

    report = _build(kalshi, poly)

    assert report["summary_counts"]["matched_threshold_rows"] == 0
    assert any(row["action"] == ACTION_IGNORE_BLOCKED and B_GRID_MISMATCH in row["blockers"] for row in report["rows"])


def test_no_midpoint_used_for_edge(tmp_path: Path) -> None:
    kalshi, poly = _write_evidence(tmp_path, kalshi_yes_ask="0.40", polymarket_no_ask="0.55")

    report = _build(kalshi, poly)
    row = next(row for row in report["rows"] if row["direction"] == "KALSHI_YES_POLYMARKET_NO")

    assert row["kalshi_ask"] == 0.4
    assert row["polymarket_ask"] == 0.55
    assert row["gross_edge"] == 0.05


def test_outputs_never_emit_forbidden_candidate_literal(tmp_path: Path) -> None:
    kalshi, poly = _write_evidence(tmp_path)
    json_output = tmp_path / "out.json"
    md_output = tmp_path / "out.md"

    report = write_crypto_threshold_basis_review_scout_files(
        kalshi_evidence=kalshi,
        polymarket_evidence=poly,
        asset="BTC",
        json_output=json_output,
        markdown_output=md_output,
        generated_at=NOW,
    )

    forbidden = "PAPER" + "_CANDIDATE"
    assert forbidden not in json.dumps(report)
    assert forbidden not in json_output.read_text(encoding="utf-8")
    assert forbidden not in md_output.read_text(encoding="utf-8")
    assert report["exact_ready_rows"] == 0
    assert report["paper_candidate_rows"] == 0


def test_cdna_crypto_display_price_is_fill_first_only(tmp_path: Path) -> None:
    kalshi, poly = _write_evidence(tmp_path)
    cdna = tmp_path / "cdna.json"
    cdna.write_text(json.dumps(_cdna_payload()), encoding="utf-8")

    report = build_crypto_threshold_basis_review_scout_report(
        kalshi_evidence=kalshi,
        polymarket_evidence=poly,
        cdna_evidence=cdna,
        asset="BTC",
        generated_at=NOW,
    )

    row = next(row for row in report["rows"] if row["direction"] == "CDNA_YES_POLYMARKET_NO")
    assert row["action"] == "WATCH"
    assert "cdna_display_price_only" in row["blockers"]
    assert "cdna_executable_size_unverified" in row["blockers"]
    assert row["strict_exact_arb"] is False
    assert row["exact_ready"] is False
    assert row["paper_candidate"] is False


def test_scan_command_writes_crypto_threshold_basis_report(tmp_path: Path) -> None:
    kalshi, poly = _write_evidence(tmp_path)
    json_output = tmp_path / "out.json"
    md_output = tmp_path / "out.md"

    rc = scan.main(
        [
            "crypto-threshold-basis-review-scout",
            "--kalshi-evidence",
            str(kalshi),
            "--polymarket-evidence",
            str(poly),
            "--asset",
            "BTC",
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(md_output),
        ]
    )

    assert rc == 0
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["schema_kind"] == "crypto_threshold_basis_review_scout_v1"


def _build(kalshi: Path, poly: Path) -> dict:
    return build_crypto_threshold_basis_review_scout_report(
        kalshi_evidence=kalshi,
        polymarket_evidence=poly,
        asset="BTC",
        generated_at=NOW,
    )


def _write_evidence(
    tmp_path: Path,
    *,
    polymarket_threshold: int = 70000,
    kalshi_yes_ask: str = "0.40",
    polymarket_no_ask: str = "0.55",
) -> tuple[Path, Path]:
    kalshi = tmp_path / "kalshi.json"
    poly = tmp_path / "poly.json"
    kalshi.write_text(json.dumps(_kalshi_payload(yes_ask=kalshi_yes_ask)), encoding="utf-8")
    poly.write_text(json.dumps(_poly_payload(threshold=polymarket_threshold, no_ask=polymarket_no_ask)), encoding="utf-8")
    return kalshi, poly


def _base(platform: str) -> dict:
    return {
        "schema_kind": "polished_crypto_market_family_evidence_v1",
        "diagnostic_only": True,
        "platform": platform,
        "category": "crypto",
        "market_family": "btc_price_threshold",
        "asset": "BTC",
        "market_shape": "point_in_time_threshold",
        "comparator": "above",
        "target_date": "2026-05-29",
        "target_time": "per-event",
        "timezone": "ET",
    }


def _kalshi_payload(*, yes_ask: str) -> dict:
    payload = _base("Kalshi")
    payload.update(
        {
            "price_source": "CF Benchmarks Bitcoin Real-Time Index (BRTI)",
            "settlement_source": "CF Benchmarks BRTI",
            "outcomes": [
                {
                    "market_title": "Bitcoin price on May 29, 2026?",
                    "platform_market_id": "KXBTCD-26MAY2917-T69999.99",
                    "market_ticker": "KXBTCD-26MAY2917-T69999.99",
                    "outcome_name": "$70,000 or above",
                    "yes_bid": "0.39",
                    "yes_ask": yes_ask,
                    "yes_bid_size": "100",
                    "yes_ask_size": "100",
                    "no_bid": "0.59",
                    "no_ask": "0.60",
                    "no_bid_size": "100",
                    "no_ask_size": "100",
                    "strike_floor": 69999.99,
                    "depth_status": "full_clob",
                    "quote_timestamp": "2026-05-29T09:20:00Z",
                }
            ],
        }
    )
    return payload


def _poly_payload(*, threshold: int, no_ask: str) -> dict:
    payload = _base("Polymarket")
    payload.update(
        {
            "price_source": "Binance",
            "settlement_source": "Binance BTC/USDT Close",
            "rules_text": "This resolves using Binance BTC/USDT 12:00 in the ET timezone (noon).",
            "outcomes": [
                {
                    "market_title": f"Will the price of Bitcoin be above ${threshold:,} on May 29?",
                    "platform_market_id": "2361673",
                    "condition_id": "0xabc",
                    "token_id_yes": "yes",
                    "token_id_no": "no",
                    "market_ticker": f"bitcoin-above-{threshold}-on-may-29-2026",
                    "yes_bid": "0.44",
                    "yes_ask": "0.45",
                    "yes_bid_size": "100",
                    "yes_ask_size": "100",
                    "no_bid": "0.54",
                    "no_ask": no_ask,
                    "no_bid_size": "100",
                    "no_ask_size": "100",
                    "depth_status": "full_clob",
                    "quote_timestamp": "2026-05-29T09:20:00Z",
                }
            ],
        }
    )
    return payload


def _cdna_payload() -> dict:
    payload = _base("Crypto.com Predict / CDNA")
    payload.update(
        {
            "price_source": "Crypto.com Predict display price",
            "settlement_source": "CDNA rulebook",
            "outcomes": [
                {
                    "market_title": "Bitcoin above $70,000 on May 29, 2026",
                    "contract_id": "cdna-btc-70k",
                    "symbol": "BTC-70K",
                    "display_price": "0.35",
                    "display_no_price": "0.67",
                    "threshold": 70000,
                    "depth_status": "display_price_only",
                    "quote_timestamp": "2026-05-29T09:20:00Z",
                }
            ],
        }
    )
    return payload
