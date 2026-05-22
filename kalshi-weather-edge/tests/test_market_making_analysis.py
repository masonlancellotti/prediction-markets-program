from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import text

from config import settings
from data.storage import Storage
from live.paper_market_making_basket import PaperMarketMakingBasket, PaperMarketMakingBasketConfig
from research.market_making_analysis import (
    MarketMakingAnalyzer,
    MarketMakingConfig,
    _export_market_making,
    _market_likely_expired,
    _summary,
    _ticker_event_date_hint,
)


def _storage(tmp_path) -> Storage:
    storage = Storage(replace(settings, database_url=f"sqlite:///{tmp_path / 'test.db'}"))
    storage.init_db()
    return storage


def _insert_parsed_weather_contract(storage: Storage, ticker: str) -> None:
    storage.insert_json(
        "parsed_contracts",
        {
            "event_ticker": ticker.rsplit("-", 1)[0],
            "market_ticker": ticker,
            "variable_type": "high_temp",
            "contract_type": "threshold_above",
            "threshold": 70,
            "local_date": "2026-05-01",
            "parse_confidence": 0.95,
            "station_confidence": 0.95,
        },
        market_ticker=ticker,
        event_ticker=ticker.rsplit("-", 1)[0],
        parse_confidence=0.95,
    )


def _insert_books(storage: Storage, ticker: str, *, depth: float = 10.0, close_time: datetime | None = None) -> None:
    base = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)
    for idx in range(6):
        storage.upsert_live_orderbook_snapshot(
            {
                "market_ticker": ticker,
                "ts": base + pd.Timedelta(minutes=idx * 5).to_pytimedelta(),
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
                "market_status": "open",
                "market_close_time": close_time,
                "source": "test",
            }
        )


def _insert_recent_books(storage: Storage, ticker: str, *, depth: float = 10.0, close_time: datetime | None = None) -> None:
    base = datetime.now(timezone.utc) - timedelta(minutes=35)
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
                "mid_cents": 50,
                "depth_yes_bid_1": depth,
                "depth_yes_ask_1": depth,
                "depth_no_bid_1": depth,
                "depth_no_ask_1": depth,
                "market_status": "open",
                "market_close_time": close_time,
                "source": "test",
            }
        )


def _insert_trade(storage: Storage, ticker: str) -> None:
    with storage.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO historical_trades (market_ticker, ts, trade_id, price, yes_price, no_price, count, side, created_at) "
                "VALUES (:ticker, :ts, :trade_id, 40.0, 40.0, 60.0, 1, 'yes', :created_at)"
            ),
            {
                "ticker": ticker,
                "ts": "2026-05-01 12:01:00",
                "trade_id": f"{ticker}-trade-1",
                "created_at": "2026-05-01 12:01:00",
            },
        )


def _analyze(storage: Storage, *, weather_only: bool = False, **config_overrides):
    return MarketMakingAnalyzer(
        storage=storage,
        config=MarketMakingConfig(
            min_spread_cents=8,
            min_displayed_depth=1,
            quote_spacing_seconds=60,
            weather_only=weather_only,
            **config_overrides,
        ),
    ).analyze(start=date(2026, 5, 1), end=date(2026, 5, 1), persist_exports=False)


def test_market_making_scopes_have_distinct_metadata(tmp_path):
    storage = _storage(tmp_path)
    _insert_books(storage, "KXHIGHAUS-26MAY01-T70")
    _insert_books(storage, "KXNBA-26MAY01-BOS")
    _insert_parsed_weather_contract(storage, "KXHIGHAUS-26MAY01-T70")

    all_markets = _analyze(storage, weather_only=False)
    weather_only = _analyze(storage, weather_only=True)

    assert all_markets.summary["scope"] == "ALL_MARKETS"
    assert weather_only.summary["scope"] == "WEATHER_ONLY"
    assert all_markets.summary["research_only"] is True
    assert weather_only.summary["research_only"] is True
    assert all_markets.summary["separate_from_weather_fair_value"] is True
    assert weather_only.summary["separate_from_weather_fair_value"] is True
    assert all_markets.summary["markets_in_snapshot_window"] == 2
    assert weather_only.summary["markets_in_snapshot_window"] == 1
    assert "markets_analyzed" not in all_markets.summary
    assert "markets_analyzed" not in weather_only.summary
    assert "markets_in_snapshot_window=2" in all_markets.to_text()
    assert "markets_analyzed" not in all_markets.to_text()


def test_weather_only_filter_does_not_affect_all_market_analysis(tmp_path):
    storage = _storage(tmp_path)
    _insert_books(storage, "KXHIGHAUS-26MAY01-T70")
    _insert_books(storage, "KXLOWNY-26MAY01-T55")
    _insert_books(storage, "KXNBA-26MAY01-BOS")
    _insert_parsed_weather_contract(storage, "KXHIGHAUS-26MAY01-T70")
    _insert_parsed_weather_contract(storage, "KXLOWNY-26MAY01-T55")

    all_markets = _analyze(storage, weather_only=False)
    weather_only = _analyze(storage, weather_only=True)

    assert {row["market_ticker"] for row in all_markets.markets} == {
        "KXHIGHAUS-26MAY01-T70",
        "KXLOWNY-26MAY01-T55",
        "KXNBA-26MAY01-BOS",
    }
    assert {row["market_ticker"] for row in weather_only.markets} == {
        "KXHIGHAUS-26MAY01-T70",
        "KXLOWNY-26MAY01-T55",
    }


def test_market_likely_expired_uses_past_close_time_even_when_status_open():
    books = pd.DataFrame(
        {
            "market_status": ["open", "open"],
            "market_close_time": [
                datetime.now(timezone.utc) - timedelta(hours=2),
                datetime.now(timezone.utc) - timedelta(hours=1),
            ],
        }
    )
    assert _market_likely_expired(books) is True


def test_ticker_event_date_hint_parses_only_supported_obvious_sports_patterns():
    assert _ticker_event_date_hint("KXNBATEAMTOTAL-26MAY19CLENYK-CLE91") == date(2026, 5, 19)
    assert _ticker_event_date_hint("KXMLBEXTRAS-26MAY211310CLEDET-EXTRAS") == date(2026, 5, 21)
    assert _ticker_event_date_hint("KXPRIMARYTURNOUT-KY4R26-130000") is None
    assert _ticker_event_date_hint("KXHIGHAUS-26MAY21-B81.5") is None
    assert _ticker_event_date_hint("KXNBA-NOTADATE-BOS") is None


def test_market_likely_expired_uses_supported_ticker_event_date_before_formal_close():
    books = pd.DataFrame(
        {
            "market_ticker": ["KXNBATEAMTOTAL-26MAY19CLENYK-CLE91"],
            "market_status": ["open"],
            "market_close_time": [datetime.now(timezone.utc) + timedelta(days=30)],
        }
    )
    assert _market_likely_expired(books) is True


def test_market_likely_expired_does_not_expire_future_event_ticker_by_date():
    books = pd.DataFrame(
        {
            "market_ticker": ["KXNBAGAME-99DEC31NYKCLE-NYK"],
            "market_status": ["open"],
            "market_close_time": [datetime.now(timezone.utc) + timedelta(days=30)],
        }
    )
    assert _market_likely_expired(books) is False


def test_market_likely_expired_handles_null_status_and_close_time():
    books = pd.DataFrame(
        {
            "market_ticker": ["KXPRIMARYTURNOUT-KY4R26-130000"],
            "market_status": [None],
            "market_close_time": [None],
        }
    )
    assert _market_likely_expired(books) is False


def test_market_likely_expired_ignores_missing_or_invalid_close_time():
    books = pd.DataFrame({"market_status": ["open"], "market_close_time": ["not-a-date"]})
    assert _market_likely_expired(books) is False


def test_expired_markets_are_excluded_from_paper_watchlist_count():
    markets = [
        {
            "market_ticker": "KXNBATEAMTOTAL-26MAY19CLENYK-CLE91",
            "best_side": "BUY_NO",
            "candidate_quotes": 50,
            "trade_evidence_fills": 35,
            "avg_future_edge_30m_cents": 5.0,
            "avg_edge_after_penalty_30m_cents": 3.0,
            "average_spread_cents": 10.0,
            "adverse_fill_rate_30m": 0.0,
            "score": 1.0,
            "market_likely_expired": True,
            "readiness": "PAPER_WATCHLIST",
        },
        {
            "market_ticker": "KXNBAGAME-99DEC31NYKCLE-NYK",
            "best_side": "BUY_NO",
            "candidate_quotes": 50,
            "trade_evidence_fills": 35,
            "avg_future_edge_30m_cents": 5.0,
            "avg_edge_after_penalty_30m_cents": 3.0,
            "average_spread_cents": 10.0,
            "adverse_fill_rate_30m": 0.0,
            "score": 1.0,
            "market_likely_expired": False,
            "readiness": "PAPER_WATCHLIST",
        },
    ]
    summary = _summary(
        {"snapshots": 20000, "markets_in_snapshot_window": 2, "two_sided_snapshots": 20000, "two_sided_markets": 2},
        pd.DataFrame({"market_ticker": ["KXNBATEAMTOTAL-26MAY19CLENYK-CLE91", "KXNBAGAME-99DEC31NYKCLE-NYK"]}),
        pd.DataFrame({"market_ticker": ["KXNBATEAMTOTAL-26MAY19CLENYK-CLE91"] * 100}),
        markets,
    )
    assert summary["paper_watchlist_candidates"] == 1
    assert summary["raw_paper_watchlist_candidates"] == 2
    assert summary["expired_or_stale_watchlist_candidates_removed"] == 1
    assert summary["final_paper_watchlist_candidates"] == 1
    assert summary["paper_watchlist_tickers"][0]["market_ticker"] == "KXNBAGAME-99DEC31NYKCLE-NYK"
    assert summary["top_candidates_include_likely_expired"] is True
    assert summary["likely_expired_top_candidate_tickers"] == ["KXNBATEAMTOTAL-26MAY19CLENYK-CLE91"]
    assert "LIKELY_EXPIRED" in summary["top_candidate_warnings"][0]


def test_missing_depth_or_trade_prints_prevents_overconfident_candidates(tmp_path):
    storage = _storage(tmp_path)
    _insert_books(storage, "KXHIGHAUS-26MAY01-T70", depth=0)
    _insert_books(storage, "KXRAINNYC-26MAY01-T0", depth=10)
    _insert_parsed_weather_contract(storage, "KXHIGHAUS-26MAY01-T70")
    _insert_parsed_weather_contract(storage, "KXRAINNYC-26MAY01-T0")

    result = _analyze(storage, weather_only=True)

    assert result.summary["paper_watchlist_candidates"] == 0
    assert result.summary["rejection_reason_counts"]["no_candidate_quotes_after_spread_depth_spacing_filters"] == 1
    assert result.summary["rejection_reason_counts"]["missing_trade_print_confirmation"] == 1
    assert all(row["readiness"] != "PAPER_WATCHLIST" for row in result.markets)


def test_empty_market_making_summary_hygiene_counters_default_to_zero(tmp_path):
    storage = _storage(tmp_path)

    result = _analyze(storage)

    assert result.summary["paper_watchlist_candidates"] == 0
    assert result.summary["raw_paper_watchlist_candidates"] == 0
    assert result.summary["expired_or_stale_watchlist_candidates_removed"] == 0
    assert result.summary["final_paper_watchlist_candidates"] == 0
    assert result.summary["top_candidates_include_likely_expired"] is False


def test_expired_market_cannot_be_selected_by_paper_basket_targets(tmp_path):
    storage = _storage(tmp_path)
    expired_ticker = "KXNBATEAMTOTAL-26MAY19CLENYK-CLE91"
    valid_ticker = "KXNBAGAME-99DEC31NYKCLE-NYK"
    _insert_recent_books(storage, expired_ticker)
    _insert_recent_books(storage, valid_ticker)

    result = PaperMarketMakingBasket(storage=storage).run_once(
        PaperMarketMakingBasketConfig(
            last_days=1,
            search_max_markets=10,
            max_targets=5,
            include_exploratory=True,
            min_recent_trades=0,
            min_spread_cents=8,
            min_depth=1,
            stale_orderbook_seconds=86400,
            quote_ttl_seconds=600,
            quote_spacing_seconds=300,
            max_position=1,
        ),
        persist_exports=False,
    )

    assert result.summary["raw_candidate_targets"] == 2
    assert result.summary["expired_or_stale_targets_removed"] == 1
    assert result.summary["survived_expiry_filter"] == 1
    assert result.summary["final_candidate_targets"] == 1
    assert result.summary["expired_target_tickers_removed"] == [expired_ticker]
    assert result.summary["target_hygiene_verdict"] == "TARGET_HYGIENE_OK"
    assert {row["market_ticker"] for row in result.targets} == {valid_ticker}
    assert all(not row["market_likely_expired"] for row in result.targets)


def test_paper_basket_no_valid_targets_after_expiry_filter_fails_safely(tmp_path):
    storage = _storage(tmp_path)
    expired_ticker = "KXNBATEAMTOTAL-26MAY19CLENYK-CLE91"
    _insert_recent_books(storage, expired_ticker)

    result = PaperMarketMakingBasket(storage=storage).run_once(
        PaperMarketMakingBasketConfig(
            last_days=1,
            search_max_markets=10,
            max_targets=5,
            include_exploratory=True,
            min_recent_trades=0,
            min_spread_cents=8,
            min_depth=1,
            stale_orderbook_seconds=86400,
            quote_ttl_seconds=600,
            quote_spacing_seconds=300,
            max_position=1,
        ),
        persist_exports=False,
    )

    assert result.summary["status"] == "PAPER_BASKET_NO_TARGETS"
    assert result.summary["raw_candidate_targets"] == 1
    assert result.summary["expired_or_stale_targets_removed"] == 1
    assert result.summary["survived_expiry_filter"] == 0
    assert result.summary["final_candidate_targets"] == 0
    assert result.summary["expired_target_tickers_removed"] == [expired_ticker]
    assert result.summary["target_hygiene_verdict"] == "NO_VALID_TARGETS_AFTER_EXPIRY_FILTER"
    assert result.targets == []


def test_paper_basket_raw_zero_reports_no_raw_candidates(tmp_path):
    storage = _storage(tmp_path)

    result = PaperMarketMakingBasket(storage=storage).run_once(
        PaperMarketMakingBasketConfig(
            last_days=1,
            search_max_markets=10,
            max_targets=5,
            include_exploratory=True,
            min_recent_trades=0,
            min_spread_cents=8,
            min_depth=1,
            stale_orderbook_seconds=86400,
            quote_ttl_seconds=600,
            quote_spacing_seconds=300,
            max_position=1,
        ),
        persist_exports=False,
    )

    assert result.summary["status"] == "PAPER_BASKET_NO_TARGETS"
    assert result.summary["raw_candidate_targets"] == 0
    assert result.summary["expired_or_stale_targets_removed"] == 0
    assert result.summary["survived_expiry_filter"] == 0
    assert result.summary["final_candidate_targets"] == 0
    assert result.summary["target_hygiene_verdict"] == "NO_RAW_CANDIDATES"


def test_paper_basket_final_targets_can_differ_after_cap(tmp_path):
    storage = _storage(tmp_path)
    first = "KXNBAGAME-99DEC31NYKCLE-NYK"
    second = "KXNBAGAME-99DEC31NYKBOS-BOS"
    _insert_recent_books(storage, first)
    _insert_recent_books(storage, second)

    result = PaperMarketMakingBasket(storage=storage).run_once(
        PaperMarketMakingBasketConfig(
            last_days=1,
            search_max_markets=10,
            max_targets=1,
            include_exploratory=True,
            min_recent_trades=0,
            min_spread_cents=8,
            min_depth=1,
            stale_orderbook_seconds=86400,
            quote_ttl_seconds=600,
            quote_spacing_seconds=300,
            max_position=1,
        ),
        persist_exports=False,
    )

    assert result.summary["raw_candidate_targets"] == 2
    assert result.summary["expired_or_stale_targets_removed"] == 0
    assert result.summary["survived_expiry_filter"] == 2
    assert result.summary["final_candidate_targets"] == 1
    assert result.summary["target_hygiene_verdict"] == "TARGET_HYGIENE_OK"
    assert len(result.targets) == 1


def test_trade_print_evidence_metadata_and_research_only_guardrails(tmp_path):
    storage = _storage(tmp_path)
    _insert_books(storage, "KXHIGHAUS-26MAY01-T70")
    _insert_trade(storage, "KXHIGHAUS-26MAY01-T70")
    _insert_parsed_weather_contract(storage, "KXHIGHAUS-26MAY01-T70")

    result = _analyze(storage, weather_only=True)
    assumptions = result.summary["evidence_assumptions"]

    assert result.summary["trade_print_evidence_available"] is True
    assert result.summary["readiness_promotion"] == "none"
    assert result.summary["paper_or_live_readiness"] == "not_promoted"
    assert assumptions["fill_model"] == "trade_print_confirmation_only"
    assert assumptions["trade_evidence_fills_are_not_our_actual_orders"] is True
    assert assumptions["midpoint_fill_assumption"] is False
    assert assumptions["requires_real_bid_ask_depth"] is True
    assert "research-only" in result.summary["paper_watchlist_disclaimer"]


def test_market_making_runtime_diagnostics_present_by_default(tmp_path):
    storage = _storage(tmp_path)
    _insert_books(storage, "KXHIGHAUS-26MAY01-T70")

    result = _analyze(storage)
    runtime = result.summary["runtime_diagnostics"]

    assert runtime["orderbook_rows_scanned"] == result.summary["snapshots"]
    assert runtime["markets_scanned"] == result.summary["markets_in_snapshot_window"]
    assert runtime["markets_analyzed_after_filters"] == len(result.markets)
    assert runtime["cap_settings"]["max_markets"] is None
    assert runtime["cap_settings"]["max_snapshots"] is None
    assert runtime["cap_settings"]["profile_runtime"] is False
    assert runtime["cap_settings"]["market_cap_applied_in_db"] is False
    assert runtime["cap_settings"]["snapshot_cap_applied_in_db"] is False
    assert runtime["caps_truncated_analysis"] is False
    assert runtime["research_only"] is True
    assert runtime["readiness_promotion"] == "none"
    assert "runtime=" in result.to_text()
    assert "KALSHI_API" not in result.to_text()


def test_market_making_caps_are_optional_and_truncation_is_reported(tmp_path):
    storage = _storage(tmp_path)
    _insert_books(storage, "KXHIGHAUS-26MAY01-T70")
    _insert_books(storage, "KXLOWNY-26MAY01-T55")
    _insert_books(storage, "KXNBA-26MAY01-BOS")

    uncapped = _analyze(storage)
    capped = _analyze(storage, max_markets=1, max_snapshots=3, profile_runtime=True)

    assert uncapped.summary["markets_in_snapshot_window"] == 3
    assert uncapped.summary["runtime_diagnostics"]["caps_truncated_analysis"] is False
    assert capped.summary["markets_in_snapshot_window"] == 3
    assert capped.summary["runtime_diagnostics"]["orderbook_rows_loaded_for_analysis"] <= 3
    assert capped.summary["runtime_diagnostics"]["markets_loaded_for_analysis"] <= 1
    assert capped.summary["runtime_diagnostics"]["caps_truncated_analysis"] is True
    cap_settings = capped.summary["runtime_diagnostics"]["cap_settings"]
    assert cap_settings["max_markets"] == 1
    assert cap_settings["max_snapshots"] == 3
    assert cap_settings["profile_runtime"] is True
    assert cap_settings["market_cap_applied_in_db"] is True
    assert cap_settings["snapshot_cap_applied_in_db"] is True
    assert cap_settings["market_cap_strategy"] == "latest_two_sided_snapshot_rows_before_full_book_load"
    assert "profile_steps" in capped.summary["runtime_diagnostics"]
    assert capped.summary["readiness_promotion"] == "none"


def test_market_making_index_diagnostics_are_read_only_metadata(tmp_path):
    storage = _storage(tmp_path)
    _insert_books(storage, "KXHIGHAUS-26MAY01-T70")

    result = _analyze(storage)
    indexes = result.summary["index_diagnostics"]

    assert indexes["read_only"] is True
    assert indexes["orderbook_time_index_present"] is True
    assert indexes["orderbook_market_index_present"] is True
    assert indexes["historical_trades_time_index_present"] is True
    assert indexes["historical_trades_market_time_index_present"] is True
    assert "index_diagnostics=" in result.to_text()


def test_weather_only_export_does_not_hide_legacy_all_market_summary(tmp_path, monkeypatch):
    monkeypatch.setattr("research.market_making_analysis.PROJECT_ROOT", tmp_path)
    all_market_summary = {
        "scope": "ALL_MARKETS",
        "market_making_verdict": "PAPER_WATCHLIST_CANDIDATES",
        "paper_watchlist_candidates": 2,
    }
    weather_summary = {
        "scope": "WEATHER_ONLY",
        "market_making_verdict": "RESEARCH_READY_NO_PAPER_EDGE_YET",
        "paper_watchlist_candidates": 0,
    }

    _export_market_making([], [], all_market_summary)
    _export_market_making([], [], weather_summary)

    legacy = (tmp_path / "reports" / "market_making_summary.json").read_text(encoding="utf-8")
    weather_scoped = (tmp_path / "reports" / "market_making_weather_only_summary.json").read_text(encoding="utf-8")
    assert '"scope": "ALL_MARKETS"' in legacy
    assert '"paper_watchlist_candidates": 2' in legacy
    assert '"scope": "WEATHER_ONLY"' in weather_scoped
