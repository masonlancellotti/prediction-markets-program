from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import scan
from relative_value.polymarket_point_in_time_typed_key_audit import (
    B_MISSING_CLOB_BOOK,
    B_MISSING_SETTLEMENT_SOURCE,
    B_MISSING_TARGET_TIME,
    B_STALE_OR_MISSING_QUOTE,
    build_polymarket_point_in_time_typed_key_audit_report,
    write_polymarket_point_in_time_typed_key_audit_files,
)


def _write(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _row(
    *,
    row_id: str,
    question: str = "Will Bitcoin be above $100,000 on December 31, 2026 at 5:00 PM ET?",
    family: str = "CRYPTO",
    typed_keys: dict[str, Any] | None = None,
    settlement_source_present: bool = True,
    token_ids: list[str] | None = None,
    condition_id: str | None = "0xcond",
) -> dict[str, Any]:
    return {
        "row_id": row_id,
        "market_id": row_id.replace("poly_", ""),
        "condition_id": condition_id,
        "event_id": f"evt_{row_id}",
        "event_slug": f"event-{row_id}",
        "market_slug": f"market-{row_id}",
        "venue": "polymarket",
        "source_url": "https://polymarket.com/market/test",
        "raw_source_file": "fake.json",
        "question": question,
        "title": None,
        "family": family,
        "market_shape": "point_in_time_threshold",
        "typed_keys": typed_keys
        or {
            "asset": "BTC",
            "threshold_value": 100000.0,
            "threshold_operator": ">",
            "measurement_date": "December 31, 2026",
            "measurement_time": "5:00 PM ET",
        },
        "typed_key_complete": True,
        "settlement_source_present": settlement_source_present,
        "settlement_rules_text_present": True,
        "token_ids": token_ids if token_ids is not None else ["tok_yes", "tok_no"],
        "blockers": ["title_only_match_not_equivalence"],
        "diagnostic_only": True,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "exact_ready": False,
        "execution_ready": False,
        "paper_candidate": False,
    }


def _payload(rows: list[dict[str, Any]], *, source: str = "polymarket_taxonomy_shape_scout_v1") -> dict[str, Any]:
    return {
        "schema_kind": source,
        "schema_version": 1,
        "source": source,
        "rows": rows,
        "summary": {"exact_ready_rows": 0, "paper_candidate_rows": 0},
        "safety": {"diagnostic_only": True},
    }


def _write_inputs(tmp_path: Path, taxonomy_rows: list[dict[str, Any]], enriched_rows: list[dict[str, Any]] | None = None) -> tuple[Path, Path]:
    taxonomy_json = _write(tmp_path / "polymarket_taxonomy_shape_scout.json", _payload(taxonomy_rows))
    enriched_json = _write(
        tmp_path / "polymarket_taxonomy_shape_scout_enriched.json",
        _payload(enriched_rows if enriched_rows is not None else taxonomy_rows, source="polymarket_taxonomy_shape_scout_enriched_v1"),
    )
    _write(
        tmp_path / "normalized_markets_v0.json",
        {
            "normalized_markets": [
                {
                    "venue": "kalshi",
                    "event_ticker": "KXBTC-26DEC3117",
                    "ticker": "KXBTC-26DEC3117-T100000",
                    "title": "Bitcoin price above 100000 at close?",
                }
            ]
        },
    )
    _write(
        tmp_path / "crypto_com_predict_cdna_research_snapshot.json",
        {"rows": [{"venue": "crypto_com_predict_cdna", "asset": "BTC"}]},
    )
    return taxonomy_json, enriched_json


def _with_clob(row: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(row)
    enriched["clob_book_attached"] = True
    enriched["clob_refresh"] = {
        "attached_quote": {
            "attached": True,
            "bid": 0.42,
            "ask": 0.44,
            "bid_size": 100.0,
            "ask_size": 120.0,
            "quote_timestamp": "2026-05-27T04:00:00+00:00",
            "observed_at": "2026-05-27T04:00:00+00:00",
            "raw_book_file": "reports/manual_snapshots/polymarket_clob_taxonomy/book.json",
            "inferred_from_midpoint_or_complement": False,
        }
    }
    return enriched


def test_hit_by_wording_remains_excluded(tmp_path: Path) -> None:
    taxonomy_json, enriched_json = _write_inputs(
        tmp_path,
        [
            _row(
                row_id="poly_hit_by",
                question="Will Bitcoin hit $150k by June 30, 2026?",
            )
        ],
    )

    report = build_polymarket_point_in_time_typed_key_audit_report(
        taxonomy_json=taxonomy_json,
        enriched_json=enriched_json,
        generated_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
    )

    assert report["summary"]["point_in_time_rows_seen"] == 1
    assert report["summary"]["point_in_time_rows_audited"] == 0
    assert report["summary"]["excluded_fake_point_in_time_rows"] == 1


def test_full_typed_keys_score_high_and_can_target_clob_refresh(tmp_path: Path) -> None:
    taxonomy_json, enriched_json = _write_inputs(tmp_path, [_row(row_id="poly_full")])

    report = build_polymarket_point_in_time_typed_key_audit_report(
        taxonomy_json=taxonomy_json,
        enriched_json=enriched_json,
        generated_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
    )
    row = report["rows"][0]

    assert row["typed_key_completeness_score"] >= 90
    assert row["typed_key_complete_for_review"] is True
    assert row["targeted_clob_refresh_candidate"] is True
    assert B_MISSING_CLOB_BOOK in row["blockers"]
    assert report["summary"]["targeted_clob_refresh_candidate_rows"] == 1
    assert report["summary"]["exact_ready_rows"] == 0
    assert report["summary"]["paper_candidate_rows"] == 0


def test_missing_target_time_blocks_exact_readiness(tmp_path: Path) -> None:
    typed = {
        "asset": "BTC",
        "threshold_value": 100000.0,
        "threshold_operator": ">",
        "measurement_date": "December 31, 2026",
    }
    taxonomy_json, enriched_json = _write_inputs(tmp_path, [_row(row_id="poly_no_time", typed_keys=typed)])

    report = build_polymarket_point_in_time_typed_key_audit_report(
        taxonomy_json=taxonomy_json,
        enriched_json=enriched_json,
    )
    row = report["rows"][0]

    assert B_MISSING_TARGET_TIME in row["blockers"]
    assert row["typed_key_complete_for_review"] is False
    assert row["exact_ready"] is False
    assert report["summary"]["exact_ready_rows"] == 0


def test_missing_settlement_source_blocks_exact_readiness(tmp_path: Path) -> None:
    taxonomy_json, enriched_json = _write_inputs(
        tmp_path,
        [_row(row_id="poly_no_source", settlement_source_present=False)],
    )

    report = build_polymarket_point_in_time_typed_key_audit_report(
        taxonomy_json=taxonomy_json,
        enriched_json=enriched_json,
    )
    row = report["rows"][0]

    assert B_MISSING_SETTLEMENT_SOURCE in row["blockers"]
    assert row["typed_key_complete_for_review"] is False
    assert row["exact_ready"] is False


def test_clob_attached_counted_correctly(tmp_path: Path) -> None:
    base = _row(row_id="poly_clob")
    taxonomy_json, enriched_json = _write_inputs(tmp_path, [base], [_with_clob(base)])

    report = build_polymarket_point_in_time_typed_key_audit_report(
        taxonomy_json=taxonomy_json,
        enriched_json=enriched_json,
    )
    row = report["rows"][0]

    assert row["clob_book_attached"] is True
    assert row["quote"]["bid"] == 0.42
    assert row["quote"]["ask"] == 0.44
    assert B_MISSING_CLOB_BOOK not in row["blockers"]
    assert B_STALE_OR_MISSING_QUOTE not in row["blockers"]
    assert report["summary"]["rows_with_clob_attached"] == 1
    assert report["summary"]["rows_with_bid_ask_size"] == 1


def test_no_paper_candidate_emitted(tmp_path: Path) -> None:
    taxonomy_json, enriched_json = _write_inputs(tmp_path, [_row(row_id="poly_safe")])
    json_output = tmp_path / "audit.json"
    markdown_output = tmp_path / "audit.md"

    report = write_polymarket_point_in_time_typed_key_audit_files(
        taxonomy_json=taxonomy_json,
        enriched_json=enriched_json,
        json_output=json_output,
        markdown_output=markdown_output,
    )

    forbidden = "PAPER" + "_CANDIDATE"
    assert forbidden not in json_output.read_text(encoding="utf-8")
    assert forbidden not in markdown_output.read_text(encoding="utf-8")
    assert report["summary"]["paper_candidate_rows"] == 0
    for row in report["rows"]:
        assert row["can_create_candidate_pair"] is False
        assert row["can_create_paper_candidate"] is False
        assert row["exact_ready"] is False
        assert row["paper_candidate"] is False


def test_cli_writes_audit_outputs(tmp_path: Path, capsys) -> None:
    taxonomy_json, enriched_json = _write_inputs(tmp_path, [_row(row_id="poly_cli")])
    json_output = tmp_path / "audit.json"
    markdown_output = tmp_path / "audit.md"

    result = scan.main(
        [
            "polymarket-point-in-time-typed-key-audit",
            "--taxonomy-json",
            str(taxonomy_json),
            "--enriched-json",
            str(enriched_json),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )

    assert result == 0
    stdout = capsys.readouterr().out
    assert "polymarket_point_in_time_typed_key_audit=OK" in stdout
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["schema_kind"] == "polymarket_point_in_time_typed_key_audit_v1"
    assert payload["summary"]["exact_ready_rows"] == 0
