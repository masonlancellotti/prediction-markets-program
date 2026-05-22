from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import text

from config import settings
from data.storage import Storage
from research.market_making_snapshot import MarketMakingSnapshotBuilder, MarketMakingSnapshotConfig


def _storage(tmp_path) -> Storage:
    storage = Storage(replace(settings, database_url=f"sqlite:///{tmp_path / 'test.db'}"))
    storage.init_db()
    return storage


def _insert_book(
    storage: Storage,
    ticker: str,
    *,
    depth: float | None = 10.0,
    status: str = "open",
    close_time: datetime | None = None,
    ts: datetime | None = None,
    raw_marker: str = "",
) -> None:
    snapshot_ts = ts or datetime(2026, 5, 1, 12, tzinfo=timezone.utc)
    storage.upsert_live_orderbook_snapshot(
        {
            "market_ticker": ticker,
            "ts": snapshot_ts,
            "yes_best_bid": 40,
            "yes_best_ask": 60,
            "no_best_bid": 40,
            "no_best_ask": 60,
            "spread_cents": 20,
            "mid_cents": 50,
            "depth_yes_bid_1": depth,
            "depth_yes_ask_1": depth,
            "depth_no_bid_1": depth,
            "depth_no_ask_1": depth,
            "total_yes_bid_depth": depth,
            "total_no_bid_depth": depth,
            "market_status": status,
            "market_close_time": close_time,
            "source": "test",
            "raw_json": json.dumps({"marker": raw_marker}) if raw_marker else None,
        }
    )


def _insert_trade(storage: Storage, ticker: str, *, ts: datetime | None = None) -> None:
    trade_ts = ts or datetime(2026, 5, 1, 12, 5, tzinfo=timezone.utc)
    with storage.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO historical_trades (market_ticker, ts, trade_id, price, yes_price, no_price, count, side, created_at) "
                "VALUES (:ticker, :ts, :trade_id, 40.0, 40.0, 60.0, 2, 'yes', :created_at)"
            ),
            {
                "ticker": ticker,
                "ts": trade_ts,
                "trade_id": f"{ticker}-trade-1",
                "created_at": trade_ts,
            },
        )


def _build(storage: Storage):
    return MarketMakingSnapshotBuilder(
        storage=storage,
        config=MarketMakingSnapshotConfig(venue="kalshi"),
    ).build(start=datetime(2026, 5, 1).date(), end=datetime(2026, 5, 1).date(), persist_exports=False)


def test_market_making_snapshot_schema_is_research_only(tmp_path):
    storage = _storage(tmp_path)
    _insert_book(storage, "KXHIGHAUS-26MAY01-T70")

    result = _build(storage)
    payload = result.payload

    assert payload["schema_kind"] == "market_making_snapshot_v1"
    assert payload["schema_version"] == 1
    assert payload["venue_id"] == "kalshi"
    assert payload["research_only"] is True
    assert payload["execution_enabled"] is False
    assert payload["readiness_promotion"] == "none"
    assert payload["paper_candidate_allowed_default"] is False
    assert payload["summary"]["paper_candidate_allowed_count"] == 0
    assert payload["markets"][0]["paper_candidate_allowed"] is False


def test_kalshi_snapshot_builds_from_synthetic_books_and_trades(tmp_path):
    storage = _storage(tmp_path)
    _insert_book(storage, "KXHIGHAUS-26MAY01-T70")
    _insert_trade(storage, "KXHIGHAUS-26MAY01-T70")

    result = _build(storage)
    row = result.payload["markets"][0]
    summary = result.payload["summary"]

    assert summary["total_markets"] == 1
    assert summary["markets_with_two_sided_books"] == 1
    assert summary["markets_with_depth"] == 1
    assert summary["markets_with_trade_print_evidence"] == 1
    assert row["trade_print_evidence_summary"]["trade_count"] == 1
    assert row["trade_print_evidence_summary"]["total_trade_size"] == 2.0
    assert row["bid_ask"]["yes_bid"] == 40.0
    assert row["depth"]["yes_bid_1"] == 10.0


def test_stale_post_event_flags_are_preserved(tmp_path):
    storage = _storage(tmp_path)
    _insert_book(
        storage,
        "KXNBATEAMTOTAL-26MAY01BOSNYK-BOS100",
        close_time=datetime.now(timezone.utc) - timedelta(days=1),
    )

    result = _build(storage)
    row = result.payload["markets"][0]

    assert row["category"] == "sports"
    assert "likely_expired_or_post_event" in row["stale_post_event_risk_flags"]
    assert "post_event_review_required" in row["event_drift_risk_flags"]
    assert "stale_post_event_risk" in row["missing_fields_blocking_paper_market_making"]
    assert result.payload["summary"]["stale_post_event_risk_count"] == 1


def test_missing_depth_trade_print_and_freshness_blockers_are_surfaced(tmp_path):
    storage = _storage(tmp_path)
    _insert_book(storage, "KXRAINNYC-26MAY01-T0", depth=None)

    result = _build(storage)
    row = result.payload["markets"][0]
    blockers = row["missing_fields_blocking_paper_market_making"]

    assert "missing_displayed_depth" in blockers
    assert "missing_trade_print_evidence" in blockers
    assert "paper_candidate_disabled_by_default" in blockers
    assert "missing_quote_freshness" not in blockers
    assert result.payload["summary"]["quote_freshness_available_count"] == 1


def test_snapshot_export_writes_json_and_markdown(tmp_path, monkeypatch):
    storage = _storage(tmp_path)
    monkeypatch.setattr("research.market_making_snapshot.PROJECT_ROOT", tmp_path)
    _insert_book(storage, "KXHIGHAUS-26MAY01-T70")

    result = MarketMakingSnapshotBuilder(storage=storage).build(
        start=datetime(2026, 5, 1).date(),
        end=datetime(2026, 5, 1).date(),
        persist_exports=True,
    )

    assert result.exports is not None
    assert (tmp_path / "reports" / "market_making_snapshot_kalshi.json").exists()
    assert (tmp_path / "reports" / "market_making_snapshot_kalshi.md").exists()


def test_snapshot_does_not_serialize_secretish_raw_values(tmp_path):
    storage = _storage(tmp_path)
    secret = "super-secret-market-making-token"
    _insert_book(storage, "KXHIGHAUS-26MAY01-T70", raw_marker=secret)

    result = _build(storage)
    serialized = json.dumps(result.payload) + result.markdown + result.to_text()

    assert secret not in serialized

