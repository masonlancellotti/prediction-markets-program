from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from graph_engine.reporting.json_report import PROHIBITED_VIOLATION_FIELDS
from graph_engine.reporting.schema_validation import SchemaValidationError
from graph_engine.reporting.venue_lag import (
    build_venue_lag_watchlist_report,
    validate_venue_lag_watchlist_report,
    write_venue_lag_watchlist_report,
)


PROHIBITED_TOKENS = sorted(PROHIBITED_VIOLATION_FIELDS)


def _snapshot(
    path: Path,
    *,
    as_of: str,
    kalshi_price: float,
    poly_price: float,
    kalshi_updated: str,
    poly_updated: str,
    poly_date: str = "2026-06-30",
) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "snapshot_id": path.stem,
                "as_of": as_of,
                "normalized_markets": [
                    _btc_row(
                        "kalshi:kxbtc-26jun30-t100000",
                        "kalshi",
                        "Will BTC be above $100000 on June 30, 2026?",
                        kalshi_price,
                        kalshi_updated,
                        "2026-06-30",
                    ),
                    _btc_row(
                        "polymarket:btc-above-100000-june-30",
                        "polymarket",
                        f"Will Bitcoin be above $100000 on {poly_date}?",
                        poly_price,
                        poly_updated,
                        poly_date,
                    ),
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _btc_row(market_id: str, venue: str, title: str, price: float, updated_at: str, resolution_date: str) -> dict:
    return {
        "market_id": market_id,
        "venue": venue,
        "title": title,
        "canonical_text": title,
        "resolution_criteria": "Saved snapshot fixture.",
        "resolution_date": resolution_date,
        "entities": ["BTC"],
        "themes": ["crypto", "threshold"],
        "yes_price": price,
        "bid": max(0.0, price - 0.01),
        "ask": min(1.0, price + 0.01),
        "updated_at": updated_at,
        "settlement_source": "fixture_btc_index",
        "window": resolution_date,
    }


def _unknown_snapshot(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "snapshot_id": path.stem,
                "as_of": "2026-05-20T13:00:00+00:00",
                "normalized_markets": [
                    {
                        "market_id": "kalshi:similar-alpha",
                        "venue": "kalshi",
                        "title": "Will Alpha happen?",
                        "canonical_text": "Will Alpha happen?",
                        "yes_price": 0.40,
                        "updated_at": "2026-05-20T12:00:00+00:00",
                    },
                    {
                        "market_id": "polymarket:similar-alpha",
                        "venue": "polymarket",
                        "title": "Will Alpha happen?",
                        "canonical_text": "Will Alpha happen?",
                        "yes_price": 0.60,
                        "updated_at": "2026-05-20T13:00:00+00:00",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_stale_related_market_creates_watch_row(tmp_path) -> None:
    old_path = _snapshot(
        tmp_path / "old.json",
        as_of="2026-05-20T12:00:00+00:00",
        kalshi_price=0.40,
        poly_price=0.40,
        kalshi_updated="2026-05-20T12:00:00+00:00",
        poly_updated="2026-05-20T12:00:00+00:00",
    )
    new_path = _snapshot(
        tmp_path / "new.json",
        as_of="2026-05-20T13:00:00+00:00",
        kalshi_price=0.40,
        poly_price=0.58,
        kalshi_updated="2026-05-20T12:00:00+00:00",
        poly_updated="2026-05-20T13:00:00+00:00",
    )

    report = build_venue_lag_watchlist_report([old_path, new_path], stale_seconds=1800, price_delta_threshold=0.10)

    assert report["diagnostic_only"] is True
    assert report["affects_evaluator_gates"] is False
    assert report["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    assert report["watchlist_count"] == 1
    row = report["venue_lag_watchlist"][0]
    assert row["max_action_cap"] == "WATCH"
    assert row["formula_relation"] == "typed_formula_match_review_only"
    assert row["quote_age_seconds"] == 3600
    assert row["relative_age_seconds"] == 3600
    assert row["observed_price_delta"] == 0.18
    assert row["blockers"] == []
    assert row["required_review_questions"]


def test_fresh_markets_create_no_lag_row(tmp_path) -> None:
    old_path = _snapshot(
        tmp_path / "old.json",
        as_of="2026-05-20T12:00:00+00:00",
        kalshi_price=0.40,
        poly_price=0.40,
        kalshi_updated="2026-05-20T12:00:00+00:00",
        poly_updated="2026-05-20T12:00:00+00:00",
    )
    new_path = _snapshot(
        tmp_path / "new.json",
        as_of="2026-05-20T13:00:00+00:00",
        kalshi_price=0.50,
        poly_price=0.58,
        kalshi_updated="2026-05-20T13:00:00+00:00",
        poly_updated="2026-05-20T13:00:00+00:00",
    )

    report = build_venue_lag_watchlist_report([old_path, new_path], stale_seconds=1800, price_delta_threshold=0.10)

    assert report["venue_lag_watchlist"] == []


def test_ambiguous_formula_relation_adds_blocker_and_watch_priority(tmp_path) -> None:
    old_path = _snapshot(
        tmp_path / "old.json",
        as_of="2026-05-20T12:00:00+00:00",
        kalshi_price=0.40,
        poly_price=0.40,
        kalshi_updated="2026-05-20T12:00:00+00:00",
        poly_updated="2026-05-20T12:00:00+00:00",
        poly_date="2026-07-31",
    )
    new_path = _snapshot(
        tmp_path / "new.json",
        as_of="2026-05-20T13:00:00+00:00",
        kalshi_price=0.40,
        poly_price=0.58,
        kalshi_updated="2026-05-20T12:00:00+00:00",
        poly_updated="2026-05-20T13:00:00+00:00",
        poly_date="2026-07-31",
    )

    report = build_venue_lag_watchlist_report([old_path, new_path], stale_seconds=1800, price_delta_threshold=0.10)
    row = report["venue_lag_watchlist"][0]

    assert row["formula_relation"] == "ambiguous_not_exact"
    assert row["max_action_cap"] == "WATCH"
    assert "source_or_date_mismatch" in row["blockers"]


def test_same_title_without_typed_formula_does_not_qualify(tmp_path) -> None:
    old_path = _unknown_snapshot(tmp_path / "old.json")
    new_path = _unknown_snapshot(tmp_path / "new.json")

    report = build_venue_lag_watchlist_report([old_path, new_path], stale_seconds=1800, price_delta_threshold=0.10)

    assert report["venue_lag_watchlist"] == []


def test_venue_lag_report_validates_before_writing(tmp_path) -> None:
    old_path = _snapshot(
        tmp_path / "old.json",
        as_of="2026-05-20T12:00:00+00:00",
        kalshi_price=0.40,
        poly_price=0.40,
        kalshi_updated="2026-05-20T12:00:00+00:00",
        poly_updated="2026-05-20T12:00:00+00:00",
    )
    new_path = _snapshot(
        tmp_path / "new.json",
        as_of="2026-05-20T13:00:00+00:00",
        kalshi_price=0.40,
        poly_price=0.58,
        kalshi_updated="2026-05-20T12:00:00+00:00",
        poly_updated="2026-05-20T13:00:00+00:00",
    )
    json_output = tmp_path / "market_graph_venue_lag_watchlist.json"
    md_output = tmp_path / "market_graph_venue_lag_watchlist.md"

    report = write_venue_lag_watchlist_report([old_path, new_path], json_output, md_output, stale_seconds=1800, price_delta_threshold=0.10)

    assert json.loads(json_output.read_text(encoding="utf-8")) == report
    assert md_output.exists()
    validate_venue_lag_watchlist_report(report)


@pytest.mark.parametrize("token", ["trade", "fill", "size", "paper"])
def test_venue_lag_report_rejects_prohibited_tokens(tmp_path, token: str) -> None:
    old_path = _snapshot(
        tmp_path / "old.json",
        as_of="2026-05-20T12:00:00+00:00",
        kalshi_price=0.40,
        poly_price=0.40,
        kalshi_updated="2026-05-20T12:00:00+00:00",
        poly_updated="2026-05-20T12:00:00+00:00",
    )
    new_path = _snapshot(
        tmp_path / "new.json",
        as_of="2026-05-20T13:00:00+00:00",
        kalshi_price=0.40,
        poly_price=0.58,
        kalshi_updated="2026-05-20T12:00:00+00:00",
        poly_updated="2026-05-20T13:00:00+00:00",
    )
    report = build_venue_lag_watchlist_report([old_path, new_path], stale_seconds=1800, price_delta_threshold=0.10)
    report["venue_lag_watchlist"][0]["reason_for_review"] = token

    with pytest.raises(SchemaValidationError):
        validate_venue_lag_watchlist_report(report)


def test_venue_lag_output_contains_no_prohibited_tokens(tmp_path) -> None:
    old_path = _snapshot(
        tmp_path / "old.json",
        as_of="2026-05-20T12:00:00+00:00",
        kalshi_price=0.40,
        poly_price=0.40,
        kalshi_updated="2026-05-20T12:00:00+00:00",
        poly_updated="2026-05-20T12:00:00+00:00",
    )
    new_path = _snapshot(
        tmp_path / "new.json",
        as_of="2026-05-20T13:00:00+00:00",
        kalshi_price=0.40,
        poly_price=0.58,
        kalshi_updated="2026-05-20T12:00:00+00:00",
        poly_updated="2026-05-20T13:00:00+00:00",
    )
    report = build_venue_lag_watchlist_report([old_path, new_path], stale_seconds=1800, price_delta_threshold=0.10)
    serialized = json.dumps(report).lower()

    for token in PROHIBITED_TOKENS:
        assert re.search(rf"\b{re.escape(token)}\b", serialized, flags=re.IGNORECASE) is None
