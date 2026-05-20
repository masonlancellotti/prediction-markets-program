from __future__ import annotations

import inspect
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from config import settings
from data.storage import Storage
from research.paper_market_making_evidence import (
    PaperMarketMakingEvidenceConfig,
    PaperMarketMakingEvidenceReporter,
    _flag_count,
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


def _latest_book(storage: Storage, market_ticker: str, ts: datetime) -> None:
    storage.upsert_live_orderbook_snapshot(
        {
            "market_ticker": market_ticker,
            "ts": ts,
            "yes_best_bid": 41,
            "yes_best_ask": 44,
            "no_best_bid": 56,
            "no_best_ask": 59,
            "spread_cents": 3,
            "mid_cents": 42.5,
            "depth_yes_bid_1": 10,
            "depth_yes_ask_1": 10,
            "depth_no_bid_1": 10,
            "depth_no_ask_1": 10,
            "market_status": "active",
            "market_close_time": ts + timedelta(hours=2),
            "source": "test",
        }
    )


def test_paper_market_making_evidence_counts_and_stale_open_flag(tmp_path):
    storage = _storage(tmp_path)
    base = datetime(2026, 5, 20, 12, tzinfo=timezone.utc)
    storage.init_db()
    storage.insert_paper_market_making_quote(_quote(base - timedelta(minutes=20), status="OPEN"))
    storage.insert_paper_market_making_quote(_quote(base - timedelta(minutes=10), status="CANCELLED", cancel_time=base - timedelta(minutes=5), cancel_reason="quote_ttl_expired"))
    storage.insert_paper_market_making_quote(
        _quote(
            base - timedelta(minutes=30),
            status="FILLED",
            fill_time=base - timedelta(minutes=29),
            fill_price_cents=40.0,
            fill_trade_price_cents=39.0,
            fee_cents=1.0,
            current_mark_cents=45.0,
            unrealized_pnl_cents=4.0,
            future_edge_30m_cents=6.0,
        )
    )
    _latest_book(storage, "M", base)

    result = PaperMarketMakingEvidenceReporter(storage=storage, now_fn=lambda: base).build(
        PaperMarketMakingEvidenceConfig(stale_open_seconds=600),
        persist_exports=False,
    )

    row = result.rows[0]
    assert row["quotes_total"] == 3
    assert row["quotes_opened"] == 3
    assert row["quotes_cancelled"] == 1
    assert row["quotes_filled"] == 1
    assert row["open_quotes"] == 1
    assert row["stale_open_quotes"] == 1
    assert "stale_open_quote" in row["warning_flags"]
    assert result.summary["quotes_total"] == 3
    assert result.summary["open_quotes"] == 1
    assert result.summary["filled_quotes"] == 1
    assert result.summary["cancelled_quotes"] == 1


def test_paper_market_making_evidence_missing_30m_markout_flag(tmp_path):
    storage = _storage(tmp_path)
    base = datetime(2026, 5, 20, 12, tzinfo=timezone.utc)
    storage.init_db()
    storage.insert_paper_market_making_quote(
        _quote(
            base,
            status="FILLED",
            fill_time=base + timedelta(minutes=1),
            fill_price_cents=40.0,
            fee_cents=1.0,
            future_edge_30m_cents=None,
        )
    )
    _latest_book(storage, "M", base)

    result = PaperMarketMakingEvidenceReporter(storage=storage, now_fn=lambda: base).build(persist_exports=False)

    assert "missing_30m_markout" in result.rows[0]["warning_flags"]


def test_paper_market_making_evidence_net_markout_subtracts_fees(tmp_path):
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
    _latest_book(storage, "M", base)

    result = PaperMarketMakingEvidenceReporter(storage=storage, now_fn=lambda: base).build(persist_exports=False)

    assert result.rows[0]["avg_gross_markout_30m_cents"] == 10.0
    assert result.rows[0]["avg_net_markout_30m_cents"] == 8.0


def test_paper_market_making_evidence_flags_missing_fee_and_depth(tmp_path):
    storage = _storage(tmp_path)
    base = datetime(2026, 5, 20, 12, tzinfo=timezone.utc)
    storage.init_db()
    storage.insert_paper_market_making_quote(
        _quote(
            base,
            status="FILLED",
            fill_time=base + timedelta(minutes=1),
            fill_price_cents=40.0,
            fee_cents=None,
            displayed_depth=None,
            future_edge_30m_cents=10.0,
            raw_json=json.dumps({"tier": "EXPLORATORY_CURRENT"}),
        )
    )
    _latest_book(storage, "M", base)

    result = PaperMarketMakingEvidenceReporter(storage=storage, now_fn=lambda: base).build(persist_exports=False)
    flags = result.rows[0]["warning_flags"]

    assert "missing_fee_data" in flags
    assert "missing_depth_data" in flags
    assert "exploratory_target" in flags
    assert result.rows[0]["avg_net_markout_30m_cents"] == 9.0


def test_paper_market_making_evidence_fee_source_counts(tmp_path):
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
    storage.insert_paper_market_making_quote(
        _quote(
            base + timedelta(minutes=1),
            status="FILLED",
            fill_time=base + timedelta(minutes=2),
            fill_price_cents=40.0,
            fee_cents=None,
            future_edge_30m_cents=10.0,
        )
    )
    _latest_book(storage, "M", base)

    result = PaperMarketMakingEvidenceReporter(storage=storage, now_fn=lambda: base).build(persist_exports=False)

    assert result.rows[0]["stored_fee_count"] == 1
    assert result.rows[0]["estimated_fee_count"] == 1
    assert result.rows[0]["missing_fee_count"] == 0
    assert result.summary["stored_fee_count"] == 1
    assert result.summary["estimated_fee_count"] == 1
    assert result.summary["missing_fee_count"] == 0
    assert result.summary["estimated_fee_share"] == 0.5
    assert "stored fees and conservative estimated fees" in result.summary["fee_source_note"]


def test_paper_market_making_evidence_zero_sort_key_beats_missing_markout(tmp_path):
    storage = _storage(tmp_path)
    base = datetime(2026, 5, 20, 12, tzinfo=timezone.utc)
    storage.init_db()
    storage.insert_paper_market_making_quote(
        _quote(
            base,
            market_ticker="ZERO",
            status="FILLED",
            fill_time=base + timedelta(minutes=1),
            fill_price_cents=40.0,
            fee_cents=0.0,
            future_edge_30m_cents=0.0,
        )
    )
    storage.insert_paper_market_making_quote(
        _quote(
            base,
            market_ticker="MISSING",
            status="FILLED",
            fill_time=base + timedelta(minutes=1),
            fill_price_cents=40.0,
            fee_cents=0.0,
            future_edge_30m_cents=None,
        )
    )
    _latest_book(storage, "ZERO", base)
    _latest_book(storage, "MISSING", base)

    result = PaperMarketMakingEvidenceReporter(storage=storage, now_fn=lambda: base).build(persist_exports=False)

    assert result.rows[0]["market_ticker"] == "ZERO"
    assert result.rows[0]["avg_net_markout_30m_cents"] == 0.0


def test_empty_warning_flags_count_as_zero() -> None:
    assert _flag_count("") == 0
    assert _flag_count(None) == 0
    assert _flag_count("too_few_fills;missing_30m_markout") == 2


def test_paper_market_making_evidence_last_days_filter(tmp_path):
    storage = _storage(tmp_path)
    base = datetime(2026, 5, 20, 12, tzinfo=timezone.utc)
    storage.init_db()
    storage.insert_paper_market_making_quote(_quote(base - timedelta(days=2), status="CANCELLED"))
    storage.insert_paper_market_making_quote(_quote(base - timedelta(hours=2), status="OPEN"))
    _latest_book(storage, "M", base)

    result = PaperMarketMakingEvidenceReporter(storage=storage, now_fn=lambda: base).build(
        PaperMarketMakingEvidenceConfig(last_days=1),
        persist_exports=False,
    )

    assert result.summary["quotes_total"] == 1
    assert result.summary["window_description"] == "filtered_by_quote_time"
    assert result.summary["window_start"] == (base - timedelta(days=1)).isoformat()


def test_paper_market_making_evidence_module_has_no_live_trading_calls():
    import research.paper_market_making_evidence as evidence

    source = inspect.getsource(evidence)
    forbidden = ("KalshiClient", "create_order", "cancel_order", "private_key", "enable_live_trading", "TradingReadiness")
    for token in forbidden:
        assert token not in source
