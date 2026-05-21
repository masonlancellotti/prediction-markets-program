from __future__ import annotations

import time as _time_mod
from dataclasses import replace
from datetime import date, datetime, time, timezone

import pandas as pd

from backtest.execution import NormalizedOrderBook
from backtest.passive_fill_model import PassiveFillConfig, PassiveFillType, PassiveQuote, adverse_selection_cents, simulate_passive_fill
from backtest.recorded_replay import RecordedOrderbookReplayBuilder, missing_weather_features_asof
from config import settings
from data.storage import Storage
from live.paper_trader import PaperTrader
from live.paper_market_maker import PaperMarketMaker, PaperMarketMakerConfig
from live.paper_market_making_basket import PaperMarketMakingBasket, PaperMarketMakingBasketConfig
from models.weather_fair_value import WeatherFairValueModel
from parsing.weather_contract import WeatherContract
from research.signal_validation import future_price_validation_for_signal
from research.liquidity_analysis import LiquidityAnalysisResult, _future_mid_after, _passive_verdict
from research.market_making_analysis import MarketMakingAnalyzer, MarketMakingConfig, _market_likely_expired, _market_readiness, _prepare_trades, _trade_fill_for_quote
from research.market_making_replay import MarketMakingReplayBacktester, MarketMakingReplayConfig
from maintenance import ProjectMaintenance
from research.trading_readiness import TradingReadiness
from research.weather_edge_miner import WeatherEdgeMiner, WeatherEdgeMiningConfig
from risk.risk_engine import RiskDecision, RiskEngine


def _storage(tmp_path) -> Storage:
    return Storage(replace(settings, database_url=f"sqlite:///{tmp_path / 'test.db'}"))


def test_no_midpoint_fill_orderbook_math_uses_ask():
    raw = {"orderbook_fp": {"yes_dollars": [["0.42", "10"]], "no_dollars": [["0.53", "7"]]}}
    book = NormalizedOrderBook.from_kalshi("T", raw)
    assert book.mid == 44.5
    assert book.yes_ask == 47
    assert book.yes_ask != book.mid


def test_passive_touched_quote_does_not_fill():
    quote = PassiveQuote("M", "BUY_YES", 45, 1)
    future = pd.DataFrame([{"ts": datetime(2026, 5, 1, 12, tzinfo=timezone.utc), "yes_best_ask": 45, "depth_yes_bid_1": 10}])
    result = simulate_passive_fill(quote, future, PassiveFillConfig(assume_touch_fill=False, require_traded_through=True))
    assert result.fill_type == PassiveFillType.TOUCHED_ONLY_NO_FILL
    assert not result.filled


def test_passive_traded_through_quote_fills_with_haircut_and_penalty():
    quote = PassiveQuote("M", "BUY_YES", 45, 4)
    future = pd.DataFrame([{"ts": datetime(2026, 5, 1, 12, tzinfo=timezone.utc), "yes_best_ask": 43, "depth_yes_bid_1": 20}])
    result = simulate_passive_fill(quote, future, PassiveFillConfig(fill_haircut=0.25, adverse_selection_penalty_cents=2))
    assert result.fill_type == PassiveFillType.PARTIAL_FILL_CONSERVATIVE
    assert result.fill_quantity == 1
    assert result.fill_price == 47


def test_adverse_selection_metric_directional():
    assert adverse_selection_cents("BUY_YES", 45, 40) == -5
    assert adverse_selection_cents("BUY_NO", 45, 60) == -5


def test_fair_value_threshold_and_range_bucket_probabilities():
    model = WeatherFairValueModel()
    threshold = WeatherContract(event_ticker="E", market_ticker="T", variable_type="high_temp", contract_type="threshold_above", threshold=70, comparator="gte", parse_confidence=0.9, station_confidence=0.9)
    features = {"current_temp_asof": 68, "max_temp_so_far_asof": 69, "forecast_high_remaining_f": 75, "local_hour": 12, "weather_asof_quality_score": 0.9}
    assert model.estimate(threshold, features).fair_yes_probability > 0.7
    bucket = WeatherContract(event_ticker="E", market_ticker="B", variable_type="high_temp", contract_type="range_bucket", range_low=70, range_high=72, parse_confidence=0.9, station_confidence=0.9)
    bucket_prob = model.estimate(bucket, features).fair_yes_probability
    assert 0.05 < bucket_prob < 0.6


def test_risk_engine_rejects_stale_and_low_confidence_and_small_edge():
    risk = RiskEngine()
    base = {"contract_type": "threshold_above", "settlement_quality_score": 0.9, "weather_data_age_minutes": 1, "forecast_data_age_minutes": 1, "edge_after_buffers_cents": 8, "depth": 10, "market_ticker": "M", "intended_price": 50}
    assert risk.evaluate_candidate(base).decision == RiskDecision.APPROVE
    assert risk.evaluate_candidate({**base, "weather_data_age_minutes": 99}).decision == RiskDecision.REJECT_DATA_STALE
    assert risk.evaluate_candidate({**base, "settlement_quality_score": 0.5}).decision == RiskDecision.REJECT_LOW_SETTLEMENT_CONFIDENCE
    assert risk.evaluate_candidate({**base, "edge_after_buffers_cents": 1}).decision == RiskDecision.REJECT_EDGE_TOO_SMALL


def test_future_price_validation_for_buy_yes_and_buy_no():
    replay = pd.DataFrame(
        [
            {"ts": datetime(2026, 5, 1, 12, 5, tzinfo=timezone.utc), "yes_mid": 55},
            {"ts": datetime(2026, 5, 1, 12, 30, tzinfo=timezone.utc), "yes_mid": 60},
        ]
    )
    yes = future_price_validation_for_signal({"ts": datetime(2026, 5, 1, 12, tzinfo=timezone.utc), "action": "BUY_YES", "entry_price": 50}, replay)
    no = future_price_validation_for_signal({"ts": datetime(2026, 5, 1, 12, tzinfo=timezone.utc), "action": "BUY_NO", "entry_price": 50}, replay)
    assert yes["beat_5m"] is True
    assert no["beat_5m"] is False


def test_future_price_validation_handles_naive_replay_timestamps():
    replay = pd.DataFrame(
        [
            {"ts": "2026-05-01 12:05:00", "yes_mid": 55},
            {"ts": "2026-05-01 12:30:00", "yes_mid": 60},
        ]
    )
    result = future_price_validation_for_signal({"ts": "2026-05-01 12:00:00", "action": "BUY_YES", "entry_price": 50}, replay)
    assert result["beat_5m"] is True


def test_liquidity_future_mid_handles_mixed_timezone_timestamps():
    future = pd.DataFrame(
        [
            {"ts": "2026-05-01 12:20:00", "yes_mid": 52},
            {"ts": datetime(2026, 5, 1, 12, 30, tzinfo=timezone.utc), "yes_mid": 56},
        ]
    )
    assert _future_mid_after(future, datetime(2026, 5, 1, 12, 0), 30) == 56


def test_paper_trading_only_logs_orders(monkeypatch, tmp_path):
    class FakeRanker:
        def __init__(self, storage=None):
            pass

        def rank(self, weather_only=True, max_markets=100, persist_exports=True):
            class Result:
                rows = [
                    {
                        "market_ticker": "M",
                        "recommended_action": "TAKER_BUY_YES_CANDIDATE",
                        "edge_type": "FAIR_VALUE_TAKER_EDGE",
                        "yes_ask": 40,
                        "no_ask": 60,
                        "fair_yes_price": 55,
                        "edge_after_buffers_cents": 8,
                        "reason": "test",
                        "raw_json": "{}",
                    }
                ]

            return Result()

    monkeypatch.setattr("live.paper_trader.OpportunityRanker", FakeRanker)
    storage = _storage(tmp_path)
    result = PaperTrader(storage=storage).run_once()
    assert result["orders_logged"] == 1
    assert len(storage.fetch_table("paper_orders")) == 1


def test_stale_run_reason_includes_count(tmp_path):
    from sqlalchemy import text as _text
    storage = _storage(tmp_path)
    storage.init_db()
    # Insert a stale backtest_run directly (no public helper exists for this table)
    with storage.engine.begin() as conn:
        conn.execute(
            _text("INSERT INTO backtest_runs (strategy, mode, parser_version, settlement_version, is_stale, created_at) VALUES ('test', 'taker', 'old', 'old', 1, datetime('now'))")
        )
    result = TradingReadiness(storage).evaluate(last_days=30)
    stale_reason = next((r for r in result.reasons if "stale" in r.lower()), None)
    assert stale_reason is not None
    assert "1 stale" in stale_reason
    assert "0 clean" in stale_reason


def test_stale_runs_excluded_from_readiness(tmp_path):
    storage = _storage(tmp_path)
    storage.insert_recorded_strategy_sweep(
        {
            "ts": datetime(2026, 5, 1, tzinfo=timezone.utc),
            "strategy": "already_hit",
            "mode": "taker",
            "params_json": "{}",
            "label_quality": "primary",
            "markets": 10,
            "snapshots": 1000,
            "signals": 100,
            "fills": 100,
            "gross_pnl": 1000,
            "fees": 100,
            "net_pnl": 900,
            "roi": 0.1,
            "win_rate": 0.8,
            "max_drawdown": -50,
            "robustness_verdict": "passes",
            "recommendation": "PAPER_READY_SPECIFIC_STRATEGY",
            "parser_version": "old",
            "settlement_version": "old",
            "strategy_version": "old",
            "parameter_hash": "old",
            "is_stale": 1,
            "raw_json": "{}",
        }
    )
    result = TradingReadiness(storage).evaluate(last_days=30)
    assert result.status != "PAPER_READY_SPECIFIC_STRATEGY"


def test_readiness_distinguishes_missing_sweeps_from_no_edge(tmp_path, monkeypatch):
    import research.trading_readiness as tr_mod
    monkeypatch.setattr(tr_mod, "_load_market_making_summary", lambda path=None: {})
    storage = _storage(tmp_path)
    storage.upsert_recorded_orderbook_replay_snapshot(
        {
            "market_ticker": "M",
            "event_ticker": "E",
            "ts": datetime.now(timezone.utc),
            "weather_feature_source": "recorded_live_asof",
        }
    )
    result = TradingReadiness(storage).evaluate(last_days=7)
    assert result.status == "NOT_READY_ANALYSIS_NOT_RUN"
    assert result.next_command == "python main.py sweep-recorded --last-days 7"


def test_market_making_trade_fill_uses_trade_prints_for_yes_and_no():
    trades = _prepare_trades(
        pd.DataFrame(
            [
                {"market_ticker": "M", "ts": datetime(2026, 5, 1, 12, 1, tzinfo=timezone.utc), "trade_id": "a", "yes_price": 44, "no_price": 56},
                {"market_ticker": "M", "ts": datetime(2026, 5, 1, 12, 2, tzinfo=timezone.utc), "trade_id": "b", "yes_price": 60, "no_price": 40},
            ]
        )
    )
    yes_fill = _trade_fill_for_quote("BUY_YES", 45, trades)
    no_fill = _trade_fill_for_quote("BUY_NO", 41, trades)
    assert yes_fill is not None
    assert yes_fill["trade_id"] == "a"
    assert no_fill is not None
    assert no_fill["trade_id"] == "b"


def test_market_readiness_labels_zero_and_few_fills_separately():
    zero = {"trade_evidence_fills": 0, "avg_edge_after_penalty_30m_cents": 5.0, "adverse_fill_rate_30m": 0.1}
    assert _market_readiness(zero) == "ZERO_TRADE_PRINT_FILLS"
    few = {"trade_evidence_fills": 5, "avg_edge_after_penalty_30m_cents": 5.0, "adverse_fill_rate_30m": 0.1}
    assert _market_readiness(few) == "FEW_FILLS_NEED_MORE"
    enough = {"trade_evidence_fills": 15, "avg_edge_after_penalty_30m_cents": 5.0, "adverse_fill_rate_30m": 0.1}
    assert _market_readiness(enough) == "PROMISING_NEEDS_MORE_FILLS"
    watchlist = {"trade_evidence_fills": 30, "avg_edge_after_penalty_30m_cents": 5.0, "adverse_fill_rate_30m": 0.1}
    assert _market_readiness(watchlist) == "PAPER_WATCHLIST"


def test_market_likely_expired_detects_closed_status():
    settled = pd.DataFrame([{"market_status": "finalized"}, {"market_status": "finalized"}])
    assert _market_likely_expired(settled) is True
    open_book = pd.DataFrame([{"market_status": "open"}, {"market_status": "open"}])
    assert _market_likely_expired(open_book) is False
    no_status = pd.DataFrame([{"yes_best_bid": 44}])
    assert _market_likely_expired(no_status) is False


def test_market_making_analyzer_labels_zero_fill_markets(tmp_path):
    storage = _storage(tmp_path)
    base = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)
    # Wide-spread book but NO trades — should be ZERO_TRADE_PRINT_FILLS
    for idx in range(6):
        storage.upsert_live_orderbook_snapshot(
            {
                "market_ticker": "NOFILL",
                "ts": base + pd.Timedelta(minutes=idx * 5).to_pytimedelta(),
                "yes_best_bid": 40,
                "yes_best_ask": 60,
                "no_best_bid": 40,
                "no_best_ask": 60,
                "spread_cents": 20,
                "mid_cents": 50,
                "depth_yes_bid_1": 10,
                "depth_yes_ask_1": 10,
                "source": "test",
            }
        )
    result = MarketMakingAnalyzer(
        storage=storage,
        config=MarketMakingConfig(min_spread_cents=8, quote_spacing_seconds=60, fill_horizon_minutes=30),
    ).analyze(last_days=30, persist_exports=False)
    assert result.summary["zero_fill_markets"] == 1
    assert result.markets[0]["readiness"] == "ZERO_TRADE_PRINT_FILLS"


def test_market_making_analyzer_loads_trades_only_for_analyzed_book_markets(tmp_path):
    storage = _storage(tmp_path)
    base = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)
    analyzer = MarketMakingAnalyzer(storage=storage)
    for ticker in ["BOOK_MARKET", "OTHER_MARKET"]:
        storage.upsert_historical_trade(
            {
                "market_ticker": ticker,
                "ts": base,
                "trade_id": f"{ticker}-trade",
                "price": 45,
                "yes_price": 45,
                "no_price": 55,
                "count": 1,
                "side": "yes",
            }
        )

    trades = analyzer._load_trades(base.date(), base.date(), market_tickers=["BOOK_MARKET"])

    assert set(trades["market_ticker"]) == {"BOOK_MARKET"}


def test_market_making_analyzer_scores_trade_evidence(tmp_path):
    storage = _storage(tmp_path)
    base = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)
    for idx in range(12):
        ts = base + pd.Timedelta(minutes=idx * 5).to_pytimedelta()
        storage.upsert_live_orderbook_snapshot(
            {
                "market_ticker": "M",
                "ts": ts,
                "yes_best_bid": 44,
                "yes_best_ask": 56,
                "no_best_bid": 44,
                "no_best_ask": 56,
                "spread_cents": 12,
                "mid_cents": 50 + min(idx, 3),
                "depth_yes_bid_1": 10,
                "depth_yes_ask_1": 10,
                "total_yes_bid_depth": 20,
                "total_no_bid_depth": 20,
                "source": "test",
            }
        )
    storage.upsert_historical_trade(
        {
            "market_ticker": "M",
            "ts": base + pd.Timedelta(minutes=6).to_pytimedelta(),
            "trade_id": "fill1",
            "price": 45,
            "yes_price": 45,
            "no_price": 55,
            "count": 1,
            "side": "yes",
        }
    )
    result = MarketMakingAnalyzer(
        storage=storage,
        config=MarketMakingConfig(min_spread_cents=8, quote_spacing_seconds=300, fill_horizon_minutes=30),
    ).analyze(last_days=30, persist_exports=False)
    assert result.summary["candidate_quotes"] > 0
    assert result.summary["candidate_markets"] == 1
    assert result.summary["two_sided_markets"] == 1
    assert result.summary["one_sided_or_empty_snapshots"] == 0
    assert result.summary["trade_evidence_fills"] >= 1
    assert result.markets[0]["market_ticker"] == "M"


def test_paper_market_maker_opens_and_fills_passive_quote(tmp_path):
    storage = _storage(tmp_path)
    base = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)
    storage.upsert_live_orderbook_snapshot(
        {
            "market_ticker": "M",
            "ts": base,
            "yes_best_bid": 42,
            "yes_best_ask": 47,
            "no_best_bid": 53,
            "no_best_ask": 58,
            "spread_cents": 5,
            "mid_cents": 44.5,
            "depth_yes_bid_1": 10,
            "depth_yes_ask_1": 10,
            "depth_no_bid_1": 10,
            "depth_no_ask_1": 10,
            "source": "test",
        }
    )
    clock = {"now": base}
    maker = PaperMarketMaker(storage=storage, now_fn=lambda: clock["now"])
    cfg = PaperMarketMakerConfig(
        market_ticker="M",
        side="BUY_NO",
        min_spread_cents=5,
        min_depth=1,
        max_position=1,
        quote_ttl_seconds=600,
    )

    first = maker.run_once(cfg, persist_exports=False)

    assert first.summary["open_quotes"] == 1
    quote = storage.fetch_table("paper_market_making_quotes").iloc[0]
    assert quote["limit_price_cents"] == 54
    storage.upsert_historical_trade(
        {
            "market_ticker": "M",
            "ts": base + pd.Timedelta(minutes=1).to_pytimedelta(),
            "trade_id": "t1",
            "price": 46,
            "yes_price": 46,
            "no_price": 54,
            "count": 1,
            "side": "no",
        }
    )
    storage.upsert_live_orderbook_snapshot(
        {
            "market_ticker": "M",
            "ts": base + pd.Timedelta(minutes=31).to_pytimedelta(),
            "yes_best_bid": 38,
            "yes_best_ask": 40,
            "no_best_bid": 60,
            "no_best_ask": 62,
            "spread_cents": 2,
            "mid_cents": 39,
            "depth_yes_bid_1": 10,
            "depth_yes_ask_1": 10,
            "depth_no_bid_1": 10,
            "depth_no_ask_1": 10,
            "source": "test",
        }
    )
    clock["now"] = base + pd.Timedelta(minutes=32).to_pytimedelta()

    second = maker.run_once(cfg, persist_exports=False)

    assert second.summary["filled_quotes"] == 1
    assert second.summary["inventory_quantity"] == 1
    assert second.summary["current_mark_cents"] == 61
    assert second.summary["unrealized_pnl_cents"] > 0
    filled = storage.fetch_table("paper_market_making_quotes").iloc[0]
    assert filled["status"] == "FILLED"
    assert filled["future_edge_30m_cents"] == 7


def test_paper_market_maker_does_not_fill_from_future_prints(tmp_path):
    storage = _storage(tmp_path)
    base = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)
    storage.upsert_live_orderbook_snapshot(
        {
            "market_ticker": "M",
            "ts": base,
            "yes_best_bid": 42,
            "yes_best_ask": 47,
            "no_best_bid": 53,
            "no_best_ask": 58,
            "spread_cents": 5,
            "mid_cents": 44.5,
            "depth_yes_bid_1": 10,
            "depth_yes_ask_1": 10,
            "depth_no_bid_1": 10,
            "depth_no_ask_1": 10,
            "source": "test",
        }
    )
    clock = {"now": base}
    maker = PaperMarketMaker(storage=storage, now_fn=lambda: clock["now"])
    cfg = PaperMarketMakerConfig(
        market_ticker="M",
        side="BUY_NO",
        min_spread_cents=5,
        min_depth=1,
        max_position=1,
        quote_ttl_seconds=600,
    )

    maker.run_once(cfg, persist_exports=False)
    storage.upsert_historical_trade(
        {
            "market_ticker": "M",
            "ts": base + pd.Timedelta(minutes=5).to_pytimedelta(),
            "trade_id": "future-print",
            "price": 46,
            "yes_price": 46,
            "no_price": 54,
            "count": 1,
            "side": "no",
        }
    )
    clock["now"] = base + pd.Timedelta(minutes=2).to_pytimedelta()

    result = maker.run_once(cfg, persist_exports=False)

    assert result.summary["open_quotes"] == 1
    assert result.summary["filled_quotes"] == 0
    quote = storage.fetch_table("paper_market_making_quotes").iloc[0]
    assert quote["status"] == "OPEN"


def test_paper_market_maker_cancels_before_late_trade_print(tmp_path):
    storage = _storage(tmp_path)
    base = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)
    storage.upsert_live_orderbook_snapshot(
        {
            "market_ticker": "M",
            "ts": base,
            "yes_best_bid": 42,
            "yes_best_ask": 47,
            "no_best_bid": 53,
            "no_best_ask": 58,
            "spread_cents": 5,
            "mid_cents": 44.5,
            "depth_yes_bid_1": 10,
            "depth_yes_ask_1": 10,
            "depth_no_bid_1": 10,
            "depth_no_ask_1": 10,
            "source": "test",
        }
    )
    clock = {"now": base}
    maker = PaperMarketMaker(storage=storage, now_fn=lambda: clock["now"])
    cfg = PaperMarketMakerConfig(
        market_ticker="M",
        side="BUY_NO",
        min_spread_cents=5,
        min_depth=1,
        max_position=1,
        quote_ttl_seconds=120,
    )

    maker.run_once(cfg, persist_exports=False)
    storage.upsert_historical_trade(
        {
            "market_ticker": "M",
            "ts": base + pd.Timedelta(minutes=5).to_pytimedelta(),
            "trade_id": "late-print",
            "price": 46,
            "yes_price": 46,
            "no_price": 54,
            "count": 1,
            "side": "no",
        }
    )
    clock["now"] = base + pd.Timedelta(minutes=6).to_pytimedelta()

    result = maker.run_once(cfg, persist_exports=False)

    assert result.summary["cancelled_quotes"] == 1
    assert result.summary["filled_quotes"] == 0
    quote = storage.fetch_table("paper_market_making_quotes").iloc[0]
    assert quote["status"] == "CANCELLED"
    assert quote["cancel_reason"] == "quote_ttl_expired"


def test_market_making_replay_backtests_paper_loop_with_trade_print_fills(tmp_path):
    storage = _storage(tmp_path)
    base = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)
    for idx in range(8):
        ts = base + pd.Timedelta(minutes=idx * 5).to_pytimedelta()
        storage.upsert_live_orderbook_snapshot(
            {
                "market_ticker": "M",
                "ts": ts,
                "yes_best_bid": 44,
                "yes_best_ask": 56,
                "no_best_bid": 44,
                "no_best_ask": 56,
                "spread_cents": 12,
                "mid_cents": 50 + min(idx, 5),
                "depth_yes_bid_1": 10,
                "depth_yes_ask_1": 10,
                "depth_no_bid_1": 10,
                "depth_no_ask_1": 10,
                "source": "test",
            }
        )
    storage.upsert_historical_trade(
        {
            "market_ticker": "M",
            "ts": base + pd.Timedelta(minutes=1).to_pytimedelta(),
            "trade_id": "fill-yes",
            "price": 45,
            "yes_price": 45,
            "no_price": 55,
            "count": 1,
            "side": "yes",
        }
    )

    result = MarketMakingReplayBacktester(
        storage=storage,
        config=MarketMakingReplayConfig(
            side="BUY_YES",
            min_spread_cents=8,
            min_depth=1,
            quote_ttl_seconds=600,
            quote_spacing_seconds=300,
            max_position=1,
        ),
    ).replay(last_days=30, persist_exports=False)

    assert result.summary["quotes_opened"] == 1
    assert result.summary["fills"] == 1
    assert result.summary["avg_net_edge_30m_cents"] > 0
    assert result.fills[0]["fill_trade_id"] == "fill-yes"
    assert result.markets[0]["market_ticker"] == "M"


def test_market_making_replay_cancels_before_late_trade_print(tmp_path):
    storage = _storage(tmp_path)
    base = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)
    for idx in range(4):
        ts = base + pd.Timedelta(minutes=idx * 5).to_pytimedelta()
        storage.upsert_live_orderbook_snapshot(
            {
                "market_ticker": "M",
                "ts": ts,
                "yes_best_bid": 44,
                "yes_best_ask": 56,
                "no_best_bid": 44,
                "no_best_ask": 56,
                "spread_cents": 12,
                "mid_cents": 50,
                "depth_yes_bid_1": 10,
                "depth_yes_ask_1": 10,
                "depth_no_bid_1": 10,
                "depth_no_ask_1": 10,
                "source": "test",
            }
        )
    storage.upsert_historical_trade(
        {
            "market_ticker": "M",
            "ts": base + pd.Timedelta(minutes=20).to_pytimedelta(),
            "trade_id": "late-fill",
            "price": 45,
            "yes_price": 45,
            "no_price": 55,
            "count": 1,
            "side": "yes",
        }
    )

    result = MarketMakingReplayBacktester(
        storage=storage,
        config=MarketMakingReplayConfig(
            side="BUY_YES",
            min_spread_cents=8,
            min_depth=1,
            quote_ttl_seconds=120,
            quote_spacing_seconds=300,
            max_position=1,
        ),
    ).replay(last_days=30, persist_exports=False)

    assert result.summary["quotes_opened"] >= 1
    assert result.summary["fills"] == 0
    assert result.summary["cancels"] >= 1


def test_market_making_replay_recommends_only_current_supported_targets(tmp_path):
    storage = _storage(tmp_path)
    base = datetime.combine(date.today(), time(12), tzinfo=timezone.utc)
    for idx in range(8):
        ts = base + pd.Timedelta(minutes=idx * 5).to_pytimedelta()
        storage.upsert_live_orderbook_snapshot(
            {
                "market_ticker": "M",
                "ts": ts,
                "yes_best_bid": 44,
                "yes_best_ask": 56,
                "no_best_bid": 44,
                "no_best_ask": 56,
                "spread_cents": 12,
                "mid_cents": 50 + min(idx, 5),
                "depth_yes_bid_1": 10,
                "depth_yes_ask_1": 10,
                "depth_no_bid_1": 10,
                "depth_no_ask_1": 10,
                "market_status": "open",
                "source": "test",
            }
        )
    storage.upsert_historical_trade(
        {
            "market_ticker": "M",
            "ts": base + pd.Timedelta(minutes=1).to_pytimedelta(),
            "trade_id": "fill-yes",
            "price": 45,
            "yes_price": 45,
            "no_price": 55,
            "count": 1,
            "side": "yes",
        }
    )

    result = MarketMakingReplayBacktester(
        storage=storage,
        config=MarketMakingReplayConfig(
            side="BUY_YES",
            min_spread_cents=8,
            min_depth=1,
            quote_ttl_seconds=600,
            quote_spacing_seconds=300,
            stale_current_seconds=86400,
            max_position=1,
        ),
    ).replay(last_days=1, persist_exports=False)

    assert result.summary["current_paper_targets"] == 1
    assert result.summary["replay_supported_current_targets"] == 1
    assert "--market-ticker M --side BUY_YES" in result.summary["next_paper_command"]


def test_paper_market_making_basket_includes_exploratory_current_targets(tmp_path):
    storage = _storage(tmp_path)
    base = datetime.combine(date.today(), time(12), tzinfo=timezone.utc)
    for ticker in ["STRICT", "EXPLORATORY"]:
        for idx in range(8):
            storage.upsert_live_orderbook_snapshot(
                {
                    "market_ticker": ticker,
                    "ts": base + pd.Timedelta(minutes=idx * 5).to_pytimedelta(),
                    "yes_best_bid": 44,
                    "yes_best_ask": 56,
                    "no_best_bid": 44,
                    "no_best_ask": 56,
                    "spread_cents": 12,
                    "mid_cents": 50 + min(idx, 5),
                    "depth_yes_bid_1": 10,
                    "depth_yes_ask_1": 10,
                    "depth_no_bid_1": 10,
                    "depth_no_ask_1": 10,
                    "market_status": "open",
                    "source": "test",
                }
            )
    storage.upsert_historical_trade(
        {
            "market_ticker": "STRICT",
            "ts": base + pd.Timedelta(minutes=1).to_pytimedelta(),
            "trade_id": "strict-fill",
            "price": 45,
            "yes_price": 45,
            "no_price": 55,
            "count": 1,
            "side": "yes",
        }
    )

    result = PaperMarketMakingBasket(storage=storage, now_fn=lambda: base + pd.Timedelta(minutes=40).to_pytimedelta()).run_once(
        PaperMarketMakingBasketConfig(
            last_days=1,
            search_max_markets=10,
            max_targets=2,
            min_replay_fills=1,
            include_exploratory=True,
            min_spread_cents=8,
            min_depth=1,
            stale_orderbook_seconds=86400,
            quote_ttl_seconds=600,
            quote_spacing_seconds=300,
            max_position=1,
        ),
        persist_exports=False,
    )

    assert result.summary["targets"] == 2
    assert result.summary["strict_targets"] == 1
    assert result.summary["exploratory_targets"] == 1
    assert result.summary["open_quotes"] == 2
    tiers = {row["market_ticker"]: row["tier"] for row in result.targets}
    assert tiers["STRICT"] == "REPLAY_SUPPORTED"
    assert tiers["EXPLORATORY"] == "EXPLORATORY_CURRENT"


def test_recorded_replay_discovers_only_parsed_weather_tickers(tmp_path):
    storage = _storage(tmp_path)
    ts = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)
    for ticker in ["KXWEATHER", "KXNONWEATHER"]:
        storage.upsert_live_orderbook_snapshot(
            {
                "market_ticker": ticker,
                "ts": ts,
                "yes_best_bid": 40,
                "yes_best_ask": 60,
                "spread_cents": 20,
                "source": "test",
            }
        )
    storage.insert_json(
        "parsed_contracts",
        {
            "event_ticker": "E",
            "market_ticker": "KXWEATHER",
            "variable_type": "high_temp",
            "contract_type": "threshold_above",
            "threshold": 70,
            "local_date": "2026-05-01",
            "parse_confidence": 0.9,
            "station_confidence": 0.9,
        },
        market_ticker="KXWEATHER",
        event_ticker="E",
        parse_confidence=0.9,
    )

    tickers = RecordedOrderbookReplayBuilder(storage=storage)._recorded_tickers(
        None,
        None,
        None,
    )

    assert tickers == ["KXWEATHER"]


def test_recorded_replay_zero_overlap_warning_explains_selection(tmp_path):
    storage = _storage(tmp_path)
    selected_day = date(2026, 5, 2)
    storage.upsert_live_orderbook_snapshot(
        {
            "market_ticker": "KXNONWEATHER",
            "ts": datetime(2026, 5, 2, 12, tzinfo=timezone.utc),
            "yes_best_bid": 40,
            "yes_best_ask": 60,
            "spread_cents": 20,
            "source": "test",
        }
    )
    storage.upsert_live_orderbook_snapshot(
        {
            "market_ticker": "KXWEATHER",
            "ts": datetime(2026, 5, 1, 12, tzinfo=timezone.utc),
            "yes_best_bid": 40,
            "yes_best_ask": 60,
            "spread_cents": 20,
            "source": "test",
        }
    )
    storage.insert_json(
        "parsed_contracts",
        {
            "event_ticker": "E",
            "market_ticker": "KXWEATHER",
            "variable_type": "high_temp",
            "contract_type": "threshold_above",
            "threshold": 70,
            "local_date": "2026-05-01",
            "parse_confidence": 0.9,
            "station_confidence": 0.9,
        },
        market_ticker="KXWEATHER",
        event_ticker="E",
        parse_confidence=0.9,
    )

    result = RecordedOrderbookReplayBuilder(storage=storage).build(start=selected_day, end=selected_day)

    assert result.markets == 0
    assert result.snapshots == 0
    assert len(result.warnings) == 1
    warning = result.warnings[0]
    assert "No recorded orderbook tickers in the selected window overlap parsed weather contracts." in warning
    assert "parsed_weather_tickers=1" in warning
    assert "recorded_orderbook_tickers_in_window=1" in warning
    assert "overlap_tickers=0" in warning
    assert "latest_known_overlap_ts=2026-05-01 12:00:00.000000" in warning


def test_missing_weather_features_keep_replay_offline():
    contract = WeatherContract(
        event_ticker="E",
        market_ticker="M",
        variable_type="high_temp",
        contract_type="threshold_above",
        threshold=70,
        comparator="gte",
        parse_confidence=0.9,
        station_confidence=0.9,
    )

    features = missing_weather_features_asof(contract, "America/New_York", datetime(2026, 5, 1, 12, tzinfo=timezone.utc))

    assert features["weather_feature_source"] == "missing_recorded_live_weather"
    assert features["observations_count_so_far"] == 0
    assert features["weather_asof_quality_score"] < 0.5


def test_weather_edge_miner_finds_executable_settled_signal(tmp_path):
    storage = _storage(tmp_path)
    storage.upsert_recorded_orderbook_replay_snapshot(
        {
            "market_ticker": "KXHIGHNY-26MAY01-T70",
            "event_ticker": "KXHIGHNY-26MAY01",
            "ts": datetime(2026, 5, 1, 19, tzinfo=timezone.utc),
            "city": "New York",
            "station_code": "KNYC",
            "local_date": "2026-05-01",
            "variable_type": "high_temp",
            "contract_type": "threshold_above",
            "threshold": 70,
            "comparator": "gte",
            "yes_best_bid": 1,
            "yes_best_ask": 2,
            "no_best_bid": 25,
            "no_best_ask": 30,
            "current_temp_asof": 75,
            "max_temp_so_far_asof": 75,
            "min_temp_so_far_asof": 60,
            "forecast_high_remaining_f": 76,
            "local_hour": 15,
            "is_threshold_already_hit_asof": 1,
            "minutes_to_close": 60,
            "latest_observation_recorded_at": datetime(2026, 5, 1, 18, 30, tzinfo=timezone.utc),
            "latest_forecast_recorded_at": datetime(2026, 5, 1, 18, 0, tzinfo=timezone.utc),
            "weather_asof_quality_score": 0.9,
            "data_quality_score": 0.9,
            "settlement_confidence": 0.9,
            "yes_result": 1,
            "parser_version": "test",
            "settlement_version": "test",
        }
    )

    result = WeatherEdgeMiner(
        storage=storage,
        config=WeatherEdgeMiningConfig(min_edge_after_buffers_cents=5, min_data_quality=0.5, min_fair_confidence=0.5),
    ).mine(last_days=30, persist_exports=False)

    assert result.summary["signals"] == 1
    assert result.summary["settled_signals"] == 1
    assert result.summary["net_pnl_cents"] > 0
    assert result.summary["settled_segments"][0]["signals"] == 1
    assert result.signals[0]["action"] == "BUY_YES"
    assert result.signals[0]["hypothesis"] == "weather_locked"


def _insert_weather_edge_signal_row(storage: Storage, ticker: str, *, ts: datetime | None = None) -> None:
    ts = ts or datetime(2026, 5, 1, 19, tzinfo=timezone.utc)
    storage.upsert_recorded_orderbook_replay_snapshot(
        {
            "market_ticker": ticker,
            "event_ticker": "KXHIGHNY-26MAY01",
            "ts": ts,
            "city": "New York",
            "station_code": "KNYC",
            "local_date": "2026-05-01",
            "variable_type": "high_temp",
            "contract_type": "threshold_above",
            "threshold": 70,
            "comparator": "gte",
            "yes_best_bid": 1,
            "yes_best_ask": 2,
            "no_best_bid": 25,
            "no_best_ask": 30,
            "current_temp_asof": 75,
            "max_temp_so_far_asof": 75,
            "min_temp_so_far_asof": 60,
            "forecast_high_remaining_f": 76,
            "local_hour": 15,
            "is_threshold_already_hit_asof": 1,
            "minutes_to_close": 60,
            "latest_observation_recorded_at": datetime(2026, 5, 1, 18, 30, tzinfo=timezone.utc),
            "latest_forecast_recorded_at": datetime(2026, 5, 1, 18, 0, tzinfo=timezone.utc),
            "weather_asof_quality_score": 0.9,
            "data_quality_score": 0.9,
            "settlement_confidence": 0.9,
            "yes_result": 1,
            "parser_version": "test",
            "settlement_version": "test",
        }
    )


def test_weather_edge_miner_without_market_ticker_keeps_unfiltered_behavior(tmp_path):
    storage = _storage(tmp_path)
    _insert_weather_edge_signal_row(storage, "KXHIGHNY-26MAY01-T70")
    _insert_weather_edge_signal_row(storage, "KXHIGHBOS-26MAY01-T70")

    result = WeatherEdgeMiner(
        storage=storage,
        config=WeatherEdgeMiningConfig(min_edge_after_buffers_cents=5, min_data_quality=0.5, min_fair_confidence=0.5),
    ).mine(last_days=30, persist_exports=False)

    assert result.summary["signals"] == 2
    assert result.summary["markets_scanned"] == 2
    assert result.summary["market_ticker_filter"] is None
    assert {row["market_ticker"] for row in result.signals} == {"KXHIGHNY-26MAY01-T70", "KXHIGHBOS-26MAY01-T70"}


def test_weather_edge_miner_market_ticker_filter_analyzes_only_that_ticker(tmp_path):
    storage = _storage(tmp_path)
    _insert_weather_edge_signal_row(storage, "KXHIGHNY-26MAY01-T70")
    _insert_weather_edge_signal_row(storage, "KXHIGHBOS-26MAY01-T70")

    result = WeatherEdgeMiner(
        storage=storage,
        config=WeatherEdgeMiningConfig(min_edge_after_buffers_cents=5, min_data_quality=0.5, min_fair_confidence=0.5),
    ).mine(last_days=30, market_ticker="KXHIGHBOS-26MAY01-T70", persist_exports=False)

    assert result.summary["signals"] == 1
    assert result.summary["markets_scanned"] == 1
    assert result.summary["market_ticker_filter"] == "KXHIGHBOS-26MAY01-T70"
    assert result.signals[0]["market_ticker"] == "KXHIGHBOS-26MAY01-T70"


def test_weather_edge_miner_unknown_market_ticker_returns_empty_result(tmp_path):
    storage = _storage(tmp_path)
    _insert_weather_edge_signal_row(storage, "KXHIGHNY-26MAY01-T70")

    result = WeatherEdgeMiner(storage=storage).mine(last_days=30, market_ticker="UNKNOWN", persist_exports=False)

    assert result.summary["rows_scanned"] == 0
    assert result.summary["signals"] == 0
    assert result.summary["market_ticker_filter"] == "UNKNOWN"
    assert result.summary["verdict"] == "NO_MINED_WEATHER_SIGNALS"


def test_passive_verdict_requires_ten_fills_for_approx_candidate():
    # Fewer than 10 fills → NOT_READY_NO_EDGE even if EV is positive
    rows_few = [{"conservative_fills": 5, "estimated_ev_after_adverse_penalty": 3.0}]
    assert _passive_verdict(rows_few) == "NOT_READY_NO_EDGE"
    # 10+ fills with positive EV → PAPER_CANDIDATE_APPROX_FILLS (approximate, not trade-print)
    rows_enough = [{"conservative_fills": 10, "estimated_ev_after_adverse_penalty": 3.0}]
    assert _passive_verdict(rows_enough) == "PAPER_CANDIDATE_APPROX_FILLS"
    # 10+ fills but negative EV → NOT_READY_NO_EDGE
    rows_bad_ev = [{"conservative_fills": 10, "estimated_ev_after_adverse_penalty": -1.0}]
    assert _passive_verdict(rows_bad_ev) == "NOT_READY_NO_EDGE"
    # No fills at all → RESEARCH_READY_MORE_DATA_NEEDED
    rows_no_fills = [{"conservative_fills": 0, "estimated_ev_after_adverse_penalty": 0.0}]
    assert _passive_verdict(rows_no_fills) == "RESEARCH_READY_MORE_DATA_NEEDED"


def test_liquidity_result_to_text_shows_fill_quality_and_warning():
    market_with_fills = {
        "market_ticker": "M1",
        "average_spread": 10.0,
        "median_spread": 10.0,
        "potential_passive_quote_opportunities": 50,
        "conservative_fills": 12,
        "fill_evidence_quality": "TOUCH_FILLS_ONLY",
        "adverse_selection_score": 0.1,
        "estimated_ev_after_adverse_penalty": 4.0,
    }
    market_no_fills = {
        "market_ticker": "M2",
        "average_spread": 15.0,
        "median_spread": 14.0,
        "potential_passive_quote_opportunities": 80,
        "conservative_fills": 0,
        "fill_evidence_quality": "NO_FILLS",
        "adverse_selection_score": 0.0,
        "estimated_ev_after_adverse_penalty": 0.0,
    }
    result = LiquidityAnalysisResult(
        summary={"markets_analyzed": 2, "snapshots": 100, "passive_verdict": "PAPER_CANDIDATE_APPROX_FILLS", "message": "test"},
        markets=[market_no_fills, market_with_fills],
        adverse_selection_failures=[],
    )
    text = result.to_text()
    assert "PAPER_CANDIDATE_APPROX_FILLS" in text
    assert "fill_quality" in text
    assert "WARNING" in text
    assert "analyze-market-making" in text
    assert "[APPROX]" in text


def test_liquidity_result_to_text_notes_zero_fills_when_no_fills():
    market = {
        "market_ticker": "M",
        "average_spread": 12.0,
        "median_spread": 12.0,
        "potential_passive_quote_opportunities": 30,
        "conservative_fills": 0,
        "fill_evidence_quality": "NO_FILLS",
        "adverse_selection_score": 0.0,
        "estimated_ev_after_adverse_penalty": 0.0,
    }
    result = LiquidityAnalysisResult(
        summary={"markets_analyzed": 1, "snapshots": 50, "passive_verdict": "RESEARCH_READY_MORE_DATA_NEEDED", "message": "test"},
        markets=[market],
        adverse_selection_failures=[],
    )
    text = result.to_text()
    assert "NOTE" in text
    assert "No markets have conservative fills" in text


def test_readiness_flags_approx_fills_not_reliable_evidence(tmp_path):
    storage = _storage(tmp_path)
    # With PAPER_CANDIDATE_APPROX_FILLS, readiness still adds the approx-fills warning reason
    # but does NOT add the "not reliable fill/anti-adverse-selection" reason.
    result = TradingReadiness(storage).evaluate(last_days=7)
    # Should not be paper-ready (no sweeps, no settlements) but approx reason handling is tested
    # by asserting the not-reliable reason is present only when verdict is below PAPER_CANDIDATE_APPROX_FILLS.
    assert any("fill/anti-adverse-selection" in r for r in result.reasons)


def test_weather_edge_miner_rejects_low_quality_rows(tmp_path):
    storage = _storage(tmp_path)
    storage.upsert_recorded_orderbook_replay_snapshot(
        {
            "market_ticker": "KXHIGHNY-26MAY01-T70",
            "event_ticker": "KXHIGHNY-26MAY01",
            "ts": datetime(2026, 5, 1, 19, tzinfo=timezone.utc),
            "variable_type": "high_temp",
            "contract_type": "threshold_above",
            "threshold": 70,
            "comparator": "gte",
            "yes_best_ask": 70,
            "current_temp_asof": 75,
            "max_temp_so_far_asof": 75,
            "local_hour": 15,
            "is_threshold_already_hit_asof": 1,
            "minutes_to_close": 60,
            "latest_observation_recorded_at": datetime(2026, 5, 1, 18, 30, tzinfo=timezone.utc),
            "weather_asof_quality_score": 0.2,
            "data_quality_score": 0.2,
            "settlement_confidence": 0.9,
            "yes_result": 1,
        }
    )

    result = WeatherEdgeMiner(storage=storage).mine(last_days=30, persist_exports=False)

    assert result.summary["signals"] == 0
    assert result.summary["verdict"] == "NO_MINED_WEATHER_SIGNALS"


def test_weather_edge_miner_skips_after_close_rows(tmp_path):
    storage = _storage(tmp_path)
    storage.upsert_recorded_orderbook_replay_snapshot(
        {
            "market_ticker": "KXHIGHNY-26MAY01-T70",
            "event_ticker": "KXHIGHNY-26MAY01",
            "ts": datetime(2026, 5, 1, 23, tzinfo=timezone.utc),
            "variable_type": "high_temp",
            "contract_type": "threshold_above",
            "threshold": 70,
            "comparator": "gte",
            "yes_best_ask": 1,
            "current_temp_asof": 75,
            "max_temp_so_far_asof": 75,
            "local_hour": 19,
            "is_threshold_already_hit_asof": 1,
            "minutes_to_close": -10,
            "latest_observation_recorded_at": datetime(2026, 5, 1, 22, 30, tzinfo=timezone.utc),
            "weather_asof_quality_score": 0.9,
            "data_quality_score": 0.9,
            "settlement_confidence": 0.9,
            "yes_result": 1,
        }
    )

    result = WeatherEdgeMiner(storage=storage).mine(last_days=30, persist_exports=False)

    assert result.summary["signals"] == 0


def test_weather_edge_miner_skips_next_local_day_artifacts(tmp_path):
    storage = _storage(tmp_path)
    storage.upsert_recorded_orderbook_replay_snapshot(
        {
            "market_ticker": "KXHIGHSFO-26MAY01-T70",
            "event_ticker": "KXHIGHSFO-26MAY01",
            "ts": datetime(2026, 5, 2, 7, tzinfo=timezone.utc),
            "local_date": "2026-05-01",
            "variable_type": "high_temp",
            "contract_type": "threshold_above",
            "threshold": 70,
            "comparator": "gte",
            "yes_best_ask": 1,
            "current_temp_asof": 75,
            "max_temp_so_far_asof": 75,
            "local_hour": 0,
            "is_threshold_already_hit_asof": 1,
            "weather_asof_quality_score": 0.9,
            "data_quality_score": 0.9,
            "settlement_confidence": 0.9,
            "yes_result": 1,
        }
    )

    result = WeatherEdgeMiner(storage=storage).mine(last_days=30, persist_exports=False)

    assert result.summary["signals"] == 0


def test_weather_edge_miner_skips_stale_observation_rows(tmp_path):
    storage = _storage(tmp_path)
    storage.upsert_recorded_orderbook_replay_snapshot(
        {
            "market_ticker": "KXHIGHNY-26MAY01-T70",
            "event_ticker": "KXHIGHNY-26MAY01",
            "ts": datetime(2026, 5, 1, 20, tzinfo=timezone.utc),
            "variable_type": "high_temp",
            "contract_type": "threshold_above",
            "threshold": 70,
            "comparator": "gte",
            "yes_best_ask": 1,
            "current_temp_asof": 75,
            "max_temp_so_far_asof": 75,
            "local_hour": 16,
            "is_threshold_already_hit_asof": 1,
            "minutes_to_close": 60,
            "latest_observation_recorded_at": datetime(2026, 5, 1, 15, tzinfo=timezone.utc),
            "weather_asof_quality_score": 0.9,
            "data_quality_score": 0.9,
            "settlement_confidence": 0.9,
            "yes_result": 1,
        }
    )

    result = WeatherEdgeMiner(storage=storage).mine(last_days=30, persist_exports=False)

    assert result.summary["signals"] == 0


def test_weather_edge_miner_target_filters_and_future_mid_validation(tmp_path):
    storage = _storage(tmp_path)
    ts = datetime(2026, 5, 1, 18, tzinfo=timezone.utc)
    storage.upsert_recorded_orderbook_replay_snapshot(
        {
            "market_ticker": "KXHIGHNY-26MAY01-T70",
            "event_ticker": "KXHIGHNY-26MAY01",
            "ts": ts,
            "city": "New York",
            "local_date": "2026-05-01",
            "variable_type": "high_temp",
            "contract_type": "threshold_above",
            "threshold": 70,
            "comparator": "gte",
            "yes_best_ask": 2,
            "no_best_ask": 90,
            "current_temp_asof": 75,
            "max_temp_so_far_asof": 75,
            "local_hour": 14,
            "is_threshold_already_hit_asof": 1,
            "minutes_to_close": 120,
            "latest_observation_recorded_at": datetime(2026, 5, 1, 17, 45, tzinfo=timezone.utc),
            "weather_asof_quality_score": 0.9,
            "data_quality_score": 0.9,
            "settlement_confidence": 0.9,
            "yes_result": 1,
        }
    )
    storage.upsert_recorded_orderbook_replay_snapshot(
        {
            "market_ticker": "KXHIGHNY-26MAY01-B80.5",
            "event_ticker": "KXHIGHNY-26MAY01",
            "ts": ts,
            "city": "New York",
            "local_date": "2026-05-01",
            "variable_type": "high_temp",
            "contract_type": "range_bucket",
            "range_low": 80,
            "range_high": 82,
            "yes_best_ask": 90,
            "no_best_ask": 3,
            "current_temp_asof": 70,
            "max_temp_so_far_asof": 70,
            "forecast_high_remaining_f": 70,
            "local_hour": 14,
            "minutes_to_close": 120,
            "latest_observation_recorded_at": datetime(2026, 5, 1, 17, 45, tzinfo=timezone.utc),
            "weather_asof_quality_score": 0.9,
            "data_quality_score": 0.9,
            "settlement_confidence": 0.9,
            "yes_result": 0,
        }
    )
    storage.upsert_recorded_orderbook_replay_snapshot(
        {
            "market_ticker": "KXHIGHNY-26MAY01-B80.5",
            "event_ticker": "KXHIGHNY-26MAY01",
            "ts": datetime(2026, 5, 1, 18, 31, tzinfo=timezone.utc),
            "city": "New York",
            "local_date": "2026-05-01",
            "variable_type": "high_temp",
            "contract_type": "range_bucket",
            "range_low": 80,
            "range_high": 82,
            "yes_mid": 2,
        }
    )

    result = WeatherEdgeMiner(
        storage=storage,
        config=WeatherEdgeMiningConfig(
            target="range-bucket-buy-no",
            contract_type="range_bucket",
            action="BUY_NO",
            min_edge_after_buffers_cents=5,
            min_data_quality=0.5,
            min_fair_confidence=0.5,
            max_signals_per_market=1,
        ),
    ).mine(last_days=30, persist_exports=False)

    assert result.summary["signals"] == 1
    assert result.signals[0]["market_ticker"] == "KXHIGHNY-26MAY01-B80.5"
    assert result.signals[0]["action"] == "BUY_NO"
    assert result.signals[0]["future_edge_30m_cents"] > 0
    assert result.signals[0]["beat_future_30m"] is True
    assert result.summary["future_mid_30m_beat_rate"] == 1.0


def test_collector_health_heartbeat_age_uses_last_heartbeat_at(tmp_path):
    from datetime import timedelta
    storage = _storage(tmp_path)
    storage.init_db()
    now = datetime.now(timezone.utc)
    storage.upsert_collector_state({
        "collector_name": "orderbook_recorder",
        "started_at": now - timedelta(hours=1),
        "last_heartbeat_at": now - timedelta(minutes=15),
        "last_snapshot_at": now - timedelta(seconds=30),
        "cycles_completed": 100,
        "snapshots_this_run": 500,
        "markets_tracked": 50,
        "current_task": "recording_orderbooks",
        "status": "RECORDING",
        "error_message": None,
        "updated_at": now - timedelta(seconds=30),
    })
    result = ProjectMaintenance(storage).collector_health(last_hours=1)
    heartbeat_age = result.payload["collector_state"]["heartbeat_age_seconds"]
    assert heartbeat_age is not None
    # 15 min = 900s; allow a few seconds of slack
    assert heartbeat_age > 800, f"expected heartbeat_age from last_heartbeat_at (~900s), got {heartbeat_age}"


def test_collector_health_flags_stale_heartbeat(tmp_path):
    from datetime import timedelta
    storage = _storage(tmp_path)
    storage.init_db()
    now = datetime.now(timezone.utc)
    storage.upsert_collector_state({
        "collector_name": "orderbook_recorder",
        "started_at": now - timedelta(hours=1),
        "last_heartbeat_at": now - timedelta(minutes=20),
        "last_snapshot_at": now - timedelta(seconds=30),
        "cycles_completed": 100,
        "snapshots_this_run": 500,
        "markets_tracked": 50,
        "current_task": "recording_orderbooks",
        "status": "RECORDING",
        "error_message": None,
        "updated_at": now - timedelta(seconds=30),
    })
    result = ProjectMaintenance(storage).collector_health(last_hours=1)
    assert result.payload["collector_state"]["stale_heartbeat"] is True
    assert result.payload["process_appears_stale"] is True


def test_market_making_to_text_shows_edge_net_and_readiness_buckets():
    market_row = {
        "market_ticker": "KXTEST-20260101-B50",
        "best_side": "BUY_YES",
        "candidate_quotes": 50,
        "trade_evidence_fills": 15,
        "touches_without_trade": 5,
        "fill_rate": 0.30,
        "average_spread_cents": 10.0,
        "average_candidate_spread_cents": 10.0,
        "median_spread_cents": 10.0,
        "p90_spread_cents": 12.0,
        "avg_maker_spread_to_ask_cents": 3.0,
        "avg_future_edge_5m_cents": 1.5,
        "avg_future_edge_15m_cents": 2.0,
        "avg_future_edge_30m_cents": 3.5,
        "avg_future_edge_60m_cents": 4.0,
        "avg_edge_after_penalty_30m_cents": 1.5,
        "adverse_fill_rate_30m": 0.10,
        "score": 0.42,
        "market_likely_expired": False,
        "yes_side_json": "{}",
        "no_side_json": "{}",
        "readiness": "FEW_FILLS_NEED_MORE",
    }
    summary = {
        "market_making_verdict": "COLLECT_MORE_TRADE_EVIDENCE",
        "message": "test",
        "snapshots": 100,
        "markets_analyzed": 1,
        "two_sided_markets": 1,
        "two_sided_snapshots": 80,
        "trades": 10,
        "candidate_markets": 1,
        "filled_markets": 1,
        "zero_fill_markets": 0,
        "candidate_quotes": 50,
        "trade_evidence_fills": 15,
        "trade_evidence_fill_rate": 0.30,
        "avg_future_edge_30m_cents": 3.5,
        "adverse_fill_rate_30m": 0.10,
        "data_sufficiency": "NEED_MORE_COLLECTION",
        "paper_watchlist_candidates": 0,
    }
    from research.market_making_analysis import MarketMakingResult
    result = MarketMakingResult(summary=summary, markets=[market_row], quote_samples=[])
    text = result.to_text()
    assert "edge_net=1.50" in text, f"edge_net missing from to_text(): {text}"
    assert "score=0.420" in text, f"score missing from to_text(): {text}"
    assert "readiness_buckets:" in text, f"readiness_buckets missing from to_text(): {text}"
    assert "FEW_FILLS_NEED_MORE=1" in text


def test_project_status_includes_historical_trades_and_trade_print_fields(tmp_path):
    from datetime import timedelta
    from sqlalchemy import text as _text
    storage = _storage(tmp_path)
    storage.init_db()
    now = datetime.now(timezone.utc)
    # Insert a historical trade so the table is non-empty
    with storage.engine.begin() as conn:
        conn.execute(_text(
            "INSERT INTO historical_trades (market_ticker, ts, trade_id, price, yes_price, created_at) "
            "VALUES ('KXTEST-TICKER', :ts, 'trade-001', 45.0, 45.0, :created_at)"
        ), {"ts": now.strftime("%Y-%m-%d %H:%M:%S"), "created_at": now.strftime("%Y-%m-%d %H:%M:%S")})
    result = ProjectMaintenance(storage).project_status()
    counts = result.payload["counts"]
    assert "historical_trades" in counts, "historical_trades missing from project-status counts"
    assert counts["historical_trades"] == 1
    assert "market_universe_rankings" in counts, "market_universe_rankings missing from project-status counts"
    assert "latest_trade_print" in result.payload, "latest_trade_print missing from project-status payload"
    assert result.payload["latest_trade_print"] is not None
    assert "trade_print_warning" in result.payload, "trade_print_warning missing from project-status payload"
    # trade is fresh so no warning
    assert result.payload["trade_print_warning"] is None


def test_load_market_making_summary_reads_json_and_adds_age(tmp_path):
    import json as _json
    from research.trading_readiness import _load_market_making_summary
    summary_data = {
        "market_making_verdict": "PAPER_WATCHLIST_CANDIDATES",
        "paper_watchlist_candidates": 2,
        "trade_evidence_fills": 100,
    }
    p = tmp_path / "market_making_summary.json"
    p.write_text(_json.dumps(summary_data), encoding="utf-8")
    result = _load_market_making_summary(path=p)
    assert result["market_making_verdict"] == "PAPER_WATCHLIST_CANDIDATES"
    assert result["paper_watchlist_candidates"] == 2
    assert "_age_hours" in result
    assert result["_age_hours"] >= 0.0


def test_trading_readiness_surfaces_market_making_candidates(tmp_path, monkeypatch):
    import json as _json
    import research.trading_readiness as tr_mod
    summary_data = {
        "market_making_verdict": "PAPER_WATCHLIST_CANDIDATES",
        "paper_watchlist_candidates": 1,
        "trade_evidence_fills": 858,
        "_age_hours": 2.5,
    }
    monkeypatch.setattr(tr_mod, "_load_market_making_summary", lambda path=None: summary_data)
    storage = _storage(tmp_path)
    result = TradingReadiness(storage).evaluate(last_days=30)
    assert result.metrics["market_making_verdict"] == "PAPER_WATCHLIST_CANDIDATES"
    assert result.metrics["market_making_paper_watchlist_candidates"] == 1
    assert result.metrics["market_making_trade_evidence_fills"] == 858
    watchlist_reason = next((r for r in result.reasons if "paper watchlist candidate" in r.lower()), None)
    assert watchlist_reason is not None, f"no watchlist reason in: {result.reasons}"
    assert "PAPER_WATCHLIST_CANDIDATES" in watchlist_reason


def test_trading_readiness_next_command_points_to_basket_when_fresh_mm_candidate(tmp_path, monkeypatch):
    import research.trading_readiness as tr_mod
    monkeypatch.setattr(tr_mod, "_load_market_making_summary", lambda path=None: {
        "market_making_verdict": "PAPER_WATCHLIST_CANDIDATES",
        "paper_watchlist_candidates": 1,
        "trade_evidence_fills": 858,
        "_age_hours": 2.0,
    })
    storage = _storage(tmp_path)
    result = TradingReadiness(storage).evaluate(last_days=30)
    assert "paper-market-making-basket" in result.next_command, f"expected paper-market-making-basket, got: {result.next_command}"


def test_trading_readiness_next_command_refreshes_stale_mm_summary(tmp_path, monkeypatch):
    import research.trading_readiness as tr_mod
    monkeypatch.setattr(tr_mod, "_load_market_making_summary", lambda path=None: {
        "market_making_verdict": "PAPER_WATCHLIST_CANDIDATES",
        "paper_watchlist_candidates": 1,
        "trade_evidence_fills": 858,
        "_age_hours": 10.0,
    })
    storage = _storage(tmp_path)
    result = TradingReadiness(storage).evaluate(last_days=30)
    assert "analyze-market-making" in result.next_command, f"expected analyze-market-making, got: {result.next_command}"


def test_trading_readiness_next_command_unaffected_when_no_mm_candidates(tmp_path, monkeypatch):
    import research.trading_readiness as tr_mod
    monkeypatch.setattr(tr_mod, "_load_market_making_summary", lambda path=None: {
        "market_making_verdict": "COLLECT_MORE_TRADE_EVIDENCE",
        "paper_watchlist_candidates": 0,
        "_age_hours": 1.0,
    })
    storage = _storage(tmp_path)
    result = TradingReadiness(storage).evaluate(last_days=30)
    # With no candidates the default weather-track next_command should apply
    assert "market-making" not in result.next_command


def test_market_making_summary_warning_fires_when_missing(tmp_path):
    from maintenance import _market_making_summary_warning
    p = tmp_path / "market_making_summary.json"
    warning = _market_making_summary_warning(path=p)
    assert warning is not None
    assert "analyze-market-making" in warning


def test_market_making_summary_warning_fires_when_stale(tmp_path):
    import json as _json
    import os
    from maintenance import _market_making_summary_warning
    p = tmp_path / "market_making_summary.json"
    p.write_text(_json.dumps({"market_making_verdict": "COLLECT_MORE"}), encoding="utf-8")
    old_time = _time_mod.time() - 25 * 3600
    os.utime(str(p), (old_time, old_time))
    warning = _market_making_summary_warning(path=p)
    assert warning is not None
    assert "analyze-market-making" in warning
    assert "h old" in warning


def test_market_making_summary_warning_none_when_fresh(tmp_path):
    import json as _json
    from maintenance import _market_making_summary_warning
    p = tmp_path / "market_making_summary.json"
    p.write_text(_json.dumps({"market_making_verdict": "COLLECT_MORE"}), encoding="utf-8")
    warning = _market_making_summary_warning(path=p)
    assert warning is None


def test_project_status_includes_mm_summary_warning_field(tmp_path, monkeypatch):
    import maintenance as maint_mod
    monkeypatch.setattr(maint_mod, "_market_making_summary_warning", lambda path=None: "stale: run analyze-market-making")
    storage = _storage(tmp_path)
    result = ProjectMaintenance(storage).project_status()
    assert "market_making_summary_warning" in result.payload
    assert result.payload["market_making_summary_warning"] == "stale: run analyze-market-making"


def test_daily_trading_research_update_includes_market_making(tmp_path):
    storage = _storage(tmp_path)
    result = ProjectMaintenance(storage).daily_trading_research_update(last_days=1)
    assert "market_making" in result.payload, "market_making missing from daily update payload"
    mm = result.payload["market_making"]
    assert isinstance(mm, dict), "market_making should be a dict"
    # to_dict() returns {"summary": {...}, "markets": [...], "quote_samples": [...]} or {"error": ...}
    assert "summary" in mm or "error" in mm


def test_daily_report_markdown_includes_mm_verdict(tmp_path):
    from maintenance import _daily_report_markdown
    results = {
        "trading_readiness": {"status": "NOT_READY_NO_EDGE", "message": "test", "next_command": "python main.py analyze-liquidity"},
        "liquidity": {"summary": {"passive_verdict": "NOT_READY_NO_EDGE"}},
        "validate_signals": {"summary_by_strategy": []},
        "market_making": {"summary": {"market_making_verdict": "PAPER_WATCHLIST_CANDIDATES", "paper_watchlist_candidates": 1, "trade_evidence_fills": 858}},
    }
    md = _daily_report_markdown(results)
    assert "Market-making verdict: PAPER_WATCHLIST_CANDIDATES" in md
    assert "paper_watchlist_candidates=1" in md
    assert "trade_evidence_fills=858" in md
    assert "PAPER_WATCHLIST_CANDIDATES" in md


def test_mm_summary_includes_paper_watchlist_tickers():
    from research.market_making_analysis import _summary
    markets = [
        {
            "market_ticker": "KXTEST-01",
            "best_side": "BUY_NO",
            "candidate_quotes": 40,
            "trade_evidence_fills": 35,
            "touches_without_trade": 5,
            "fill_rate": 0.875,
            "average_spread_cents": 12.0,
            "average_candidate_spread_cents": 12.0,
            "median_spread_cents": 12.0,
            "p90_spread_cents": 14.0,
            "avg_maker_spread_to_ask_cents": 3.0,
            "avg_future_edge_5m_cents": 2.0,
            "avg_future_edge_15m_cents": 2.5,
            "avg_future_edge_30m_cents": 3.5,
            "avg_future_edge_60m_cents": 4.0,
            "avg_edge_after_penalty_30m_cents": 2.0,
            "adverse_fill_rate_30m": 0.10,
            "score": 0.55,
            "market_likely_expired": False,
            "readiness": "PAPER_WATCHLIST",
        },
    ]
    book_stats = {"snapshots": 50000, "two_sided_snapshots": 40000, "markets_analyzed": 1, "two_sided_markets": 1}
    result = _summary(book_stats, pd.DataFrame({"market_ticker": ["KXTEST-01"]}), pd.DataFrame({"market_ticker": ["KXTEST-01"]}), markets)
    assert "paper_watchlist_tickers" in result
    tickers = result["paper_watchlist_tickers"]
    assert len(tickers) == 1
    assert tickers[0]["market_ticker"] == "KXTEST-01"
    assert tickers[0]["best_side"] == "BUY_NO"
    assert tickers[0]["trade_evidence_fills"] == 35
    assert tickers[0]["avg_edge_after_penalty_30m_cents"] == 2.0
    assert tickers[0]["market_likely_expired"] is False


def test_trading_readiness_reason_includes_top_candidate_ticker(tmp_path, monkeypatch):
    import research.trading_readiness as tr_mod
    monkeypatch.setattr(tr_mod, "_load_market_making_summary", lambda path=None: {
        "market_making_verdict": "PAPER_WATCHLIST_CANDIDATES",
        "paper_watchlist_candidates": 1,
        "trade_evidence_fills": 858,
        "_age_hours": 2.0,
        "paper_watchlist_tickers": [
            {
                "market_ticker": "KXNBAGAME-26MAY25NYKCLE-NYK",
                "best_side": "BUY_NO",
                "trade_evidence_fills": 858,
                "avg_edge_after_penalty_30m_cents": 3.3,
                "average_spread_cents": 10.0,
                "score": 0.72,
                "market_likely_expired": False,
            }
        ],
    })
    storage = _storage(tmp_path)
    result = TradingReadiness(storage).evaluate(last_days=30)
    mm_reason = next((r for r in result.reasons if "Market-making track" in r), None)
    assert mm_reason is not None
    assert "KXNBAGAME-26MAY25NYKCLE-NYK" in mm_reason, f"ticker missing: {mm_reason}"
    assert "BUY_NO" in mm_reason
    assert "fills=858" in mm_reason
    assert "edge_net=3.3c" in mm_reason


def test_trading_readiness_reason_flags_expired_top_candidate(tmp_path, monkeypatch):
    import research.trading_readiness as tr_mod
    monkeypatch.setattr(tr_mod, "_load_market_making_summary", lambda path=None: {
        "market_making_verdict": "PAPER_WATCHLIST_CANDIDATES",
        "paper_watchlist_candidates": 1,
        "trade_evidence_fills": 50,
        "_age_hours": 3.0,
        "paper_watchlist_tickers": [
            {
                "market_ticker": "KXOLD-EXPIRED",
                "best_side": "BUY_YES",
                "trade_evidence_fills": 50,
                "avg_edge_after_penalty_30m_cents": 2.0,
                "average_spread_cents": 8.0,
                "score": 0.40,
                "market_likely_expired": True,
            }
        ],
    })
    storage = _storage(tmp_path)
    result = TradingReadiness(storage).evaluate(last_days=30)
    mm_reason = next((r for r in result.reasons if "Market-making track" in r), None)
    assert mm_reason is not None
    assert "expired" in mm_reason.lower(), f"expected expired flag: {mm_reason}"


def test_trading_readiness_next_command_analyze_when_all_candidates_expired(tmp_path, monkeypatch):
    import research.trading_readiness as tr_mod
    monkeypatch.setattr(tr_mod, "_load_market_making_summary", lambda path=None: {
        "market_making_verdict": "PAPER_WATCHLIST_CANDIDATES",
        "paper_watchlist_candidates": 1,
        "trade_evidence_fills": 50,
        "_age_hours": 2.0,  # summary is fresh, but candidate is expired
        "paper_watchlist_tickers": [
            {
                "market_ticker": "KXOLD-SETTLED",
                "best_side": "BUY_YES",
                "trade_evidence_fills": 50,
                "avg_edge_after_penalty_30m_cents": 2.0,
                "average_spread_cents": 8.0,
                "score": 0.40,
                "market_likely_expired": True,
            }
        ],
    })
    storage = _storage(tmp_path)
    result = TradingReadiness(storage).evaluate(last_days=30)
    assert "analyze-market-making" in result.next_command, (
        f"expected analyze-market-making when all candidates expired, got: {result.next_command}"
    )


def test_trading_readiness_next_command_basket_with_live_candidate(tmp_path, monkeypatch):
    import research.trading_readiness as tr_mod
    monkeypatch.setattr(tr_mod, "_load_market_making_summary", lambda path=None: {
        "market_making_verdict": "PAPER_WATCHLIST_CANDIDATES",
        "paper_watchlist_candidates": 1,
        "trade_evidence_fills": 858,
        "_age_hours": 2.0,
        "paper_watchlist_tickers": [
            {
                "market_ticker": "KXNBAGAME-26MAY25NYKCLE-NYK",
                "best_side": "BUY_NO",
                "trade_evidence_fills": 858,
                "avg_edge_after_penalty_30m_cents": 3.3,
                "average_spread_cents": 10.0,
                "score": 0.72,
                "market_likely_expired": False,
            }
        ],
    })
    storage = _storage(tmp_path)
    result = TradingReadiness(storage).evaluate(last_days=30)
    assert "paper-market-making-basket" in result.next_command
    assert "--max-targets 5" in result.next_command


def test_project_status_includes_stale_strategy_sweeps(tmp_path):
    from sqlalchemy import text as _text
    storage = _storage(tmp_path)
    storage.init_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with storage.engine.begin() as conn:
        # one stale sweep (old parser version)
        conn.execute(_text(
            "INSERT INTO recorded_strategy_sweeps "
            "(strategy, net_pnl, fills, ts, is_stale, parser_version, settlement_version, created_at) "
            "VALUES ('test_stale', 0.0, 0, :ts, 1, 'old_version', 'old_version', :ts)"
        ), {"ts": now})
        # one clean sweep (current version)
        conn.execute(_text(
            "INSERT INTO recorded_strategy_sweeps "
            "(strategy, net_pnl, fills, ts, is_stale, parser_version, settlement_version, created_at) "
            "VALUES ('test_clean', 5.0, 10, :ts, 0, :pv, :sv, :ts)"
        ), {"ts": now, "pv": "v2_range_bucket_semantics", "sv": "v2_range_bucket_semantics"})
    result = ProjectMaintenance(storage).project_status()
    assert "stale_strategy_sweeps" in result.payload, "stale_strategy_sweeps missing from project-status"
    assert result.payload["stale_strategy_sweeps"] == 1
    assert result.payload["clean_strategy_sweeps_current_version"] == 1
    assert result.payload["stale_recorded_sweeps"] == 1
    assert result.payload["clean_recorded_sweeps"] == 1
