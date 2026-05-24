from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from config import settings
from data.storage import Storage
from research.paper_market_making_drilldown import (
    PaperMarketMakingDrilldownConfig,
    PaperMarketMakingDrilldownReporter,
)


def _storage(tmp_path) -> Storage:
    return Storage(replace(settings, database_url=f"sqlite:///{tmp_path / 'test.db'}"))


def _quote(base: datetime, **overrides):
    row = {
        "run_id": "test-run",
        "market_ticker": "M",
        "side": "BUY_YES",
        "quote_time": base,
        "limit_price_cents": 40.0,
        "quantity": 1.0,
        "status": "OPEN",
        "quote_spread_cents": 10.0,
        "same_side_bid_cents": 39.0,
        "opposing_ask_cents": 45.0,
        "displayed_depth": 10.0,
        "strategy_version": "test",
        "reason": "test",
        "raw_json": json.dumps({"tier": "REPLAY_SUPPORTED"}),
    }
    row.update(overrides)
    return row


def test_paper_market_making_drilldown_counts_filled_cancelled_open(tmp_path):
    storage = _storage(tmp_path)
    base = datetime(2026, 5, 20, 12, tzinfo=timezone.utc)
    storage.init_db()
    storage.insert_paper_market_making_quote(_quote(base - timedelta(minutes=20), status="OPEN"))
    storage.insert_paper_market_making_quote(_quote(base - timedelta(minutes=10), status="CANCELLED", cancel_time=base - timedelta(minutes=5)))
    storage.insert_paper_market_making_quote(
        _quote(
            base - timedelta(minutes=30),
            status="FILLED",
            fill_time=base - timedelta(minutes=29),
            fill_price_cents=40.0,
            fee_cents=2.0,
            future_edge_30m_cents=10.0,
        )
    )

    result = PaperMarketMakingDrilldownReporter(storage=storage, now_fn=lambda: base).build(
        PaperMarketMakingDrilldownConfig(ticker="M", side="BUY_YES")
    )

    assert result.summary["quotes_total"] == 3
    assert result.summary["open_quotes"] == 1
    assert result.summary["filled_quotes"] == 1
    assert result.summary["cancelled_quotes"] == 1
    assert result.summary["fill_rate"] == 1 / 3


def test_paper_market_making_drilldown_missing_ticker_returns_clean_message(tmp_path):
    storage = _storage(tmp_path)
    storage.init_db()

    result = PaperMarketMakingDrilldownReporter(storage=storage).build(
        PaperMarketMakingDrilldownConfig(ticker="MISSING", side="BUY_NO")
    )

    assert result.summary["status"] == "NO_PAPER_QUOTES_FOUND"
    assert "No paper market-making quotes found" in result.summary["message"]
    assert result.rows == []


def test_paper_market_making_drilldown_net_markout_subtracts_fee(tmp_path):
    storage = _storage(tmp_path)
    base = datetime(2026, 5, 20, 12, tzinfo=timezone.utc)
    storage.init_db()
    storage.insert_paper_market_making_quote(
        _quote(
            base,
            status="FILLED",
            fill_time=base + timedelta(minutes=1),
            fill_price_cents=40.0,
            fee_cents=2.0,
            future_edge_30m_cents=10.0,
        )
    )

    result = PaperMarketMakingDrilldownReporter(storage=storage, now_fn=lambda: base).build(
        PaperMarketMakingDrilldownConfig(ticker="M", side="BUY_YES")
    )

    assert result.rows[0]["net_markout_30m_cents"] == 8.0
    assert result.summary["avg_net_markout_30m_cents"] == 8.0


def test_paper_market_making_drilldown_warning_flags(tmp_path):
    storage = _storage(tmp_path)
    base = datetime(2026, 5, 20, 12, tzinfo=timezone.utc)
    storage.init_db()
    storage.insert_paper_market_making_quote(
        _quote(
            base - timedelta(minutes=30),
            status="OPEN",
            fee_cents=None,
            displayed_depth=None,
        )
    )
    storage.insert_paper_market_making_quote(
        _quote(
            base,
            status="FILLED",
            fill_time=base + timedelta(minutes=1),
            fill_price_cents=40.0,
            fee_cents=1.0,
            unrealized_pnl_cents=-2.0,
            future_edge_30m_cents=-3.0,
        )
    )

    result = PaperMarketMakingDrilldownReporter(storage=storage, now_fn=lambda: base).build(
        PaperMarketMakingDrilldownConfig(ticker="M", side="BUY_YES", stale_open_seconds=600)
    )

    combined_flags = ";".join(row["warning_flags"] for row in result.rows)
    assert "stale_open_quote" in combined_flags
    assert "missing_fee_data" in combined_flags
    assert "missing_depth_data" in combined_flags
    assert "adverse_30m" in combined_flags
    assert "current_unrealized_negative" in combined_flags
