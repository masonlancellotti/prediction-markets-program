from __future__ import annotations

import inspect
import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import text

from config import settings
from data.storage import Storage
from live.paper_market_making_basket import (
    PaperMarketMakingBasket,
    PaperMarketMakingBasketConfig,
    PaperMarketMakingBasketResult,
    _apply_lifecycle,
    _new_lifecycle,
    _quote_rejection_bucket,
    _update_lifecycle,
)
from research.market_making_analysis import (
    MarketMakingAnalyzer,
    MarketMakingConfig,
    _export_market_making,
    _market_likely_expired,
    _summary,
    _ticker_event_date_hint,
)
from research.paper_basket_diagnostics import PaperBasketDiagnosticsReporter


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


def _write_paper_basket_exports(
    reports_dir,
    *,
    summary: dict | None = None,
    actions: list[dict] | None = None,
    targets: list[dict] | None = None,
    target_summaries: list[dict] | None = None,
) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "paper_market_making_basket_summary.json").write_text(
        json.dumps(
            summary
            or {
                "status": "PAPER_BASKET_ACTIVE_NO_FILLS_YET",
                "target_hygiene_verdict": "TARGET_HYGIENE_OK",
                "targets": 0,
            }
        ),
        encoding="utf-8",
    )
    pd.DataFrame(actions or []).to_csv(reports_dir / "paper_market_making_basket_actions.csv", index=False)
    pd.DataFrame(targets or []).to_csv(reports_dir / "paper_market_making_basket_targets.csv", index=False)
    pd.DataFrame(target_summaries or []).to_csv(reports_dir / "paper_market_making_basket_target_summaries.csv", index=False)


def test_paper_basket_diagnostics_summarizes_latest_exports(tmp_path):
    reports_dir = tmp_path / "reports"
    _write_paper_basket_exports(
        reports_dir,
        summary={
            "status": "PAPER_BASKET_ACTIVE_NO_FILLS_YET",
            "message": "Final target set has no fills.",
            "target_hygiene_verdict": "TARGET_HYGIENE_OK",
            "raw_candidate_targets": 2,
            "survived_expiry_filter": 2,
            "targets": 2,
            "strict_targets": 0,
            "exploratory_targets": 2,
            "quotes_total": 12,
            "open_quotes": 2,
            "filled_quotes": 0,
            "cancelled_quotes": 10,
            "total_quotes_opened": 4,
            "total_quotes_cancelled": 3,
            "total_trade_print_fills_seen": 1,
            "final_open_quotes": 2,
            "final_filled_quotes": 0,
            "final_cancelled_quotes": 10,
            "fill_seen_in_history_but_not_final": True,
            "quote_rejection_breakdown": {
                "spread_below_minimum": 1,
                "max_open_quotes_reached": 0,
                "target_removed": 1,
                "stale_or_expired_target": 0,
                "no_valid_target": 0,
                "other": 0,
            },
            "targets_with_any_fill": [
                {"market_ticker": "KXRAINNYC-26MAY21-T0", "side": "BUY_NO", "tier": "REPLAY_SUPPORTED"}
            ],
            "targets_final": [
                {"market_ticker": "KXMLBEXTRAS-26MAY211310CLEDET-EXTRAS", "side": "BUY_YES", "tier": "EXPLORATORY_CURRENT"},
                {"market_ticker": "KXNBA2NDTEAMDEF-26-TCAM", "side": "BUY_YES", "tier": "EXPLORATORY_CURRENT"},
            ],
            "targets_removed_reason": {"KXRAINNYC-26MAY21-T0|BUY_NO": "target_removed_after_selector_refresh"},
        },
        actions=[
            {
                "action": "NO_QUOTE",
                "market_ticker": "KXMLBEXTRAS-26MAY211310CLEDET-EXTRAS",
                "side": "BUY_YES",
                "reason": "Current spread 4.00c below minimum 8.00c.",
            },
            {
                "action": "QUOTE_OPENED",
                "market_ticker": "KXNBA2NDTEAMDEF-26-TCAM",
                "side": "BUY_YES",
                "reason": "Opened paper quote id=1 at 7.00c.",
            },
        ],
        targets=[
            {"market_ticker": "KXMLBEXTRAS-26MAY211310CLEDET-EXTRAS", "side": "BUY_YES", "tier": "EXPLORATORY_CURRENT"},
            {"market_ticker": "KXNBA2NDTEAMDEF-26-TCAM", "side": "BUY_YES", "tier": "EXPLORATORY_CURRENT"},
        ],
        target_summaries=[],
    )

    result = PaperBasketDiagnosticsReporter(reports_dir=reports_dir).build()

    assert result.summary["diagnostics_status"] == "PAPER_BASKET_DIAGNOSTICS_OK"
    assert result.summary["research_only"] is True
    assert result.summary["readiness_promotion"] == "none"
    assert result.summary["strict_targets"] == 0
    assert result.summary["exploratory_targets"] == 2
    assert result.summary["fill_seen_in_history_but_not_final"] is True
    assert result.summary["quote_rejection_breakdown"]["spread_below_minimum"] == 1
    assert result.summary["spread_below_minimum_count"] == 1
    assert result.summary["targets_with_any_fill"][0]["market_ticker"] == "KXRAINNYC-26MAY21-T0"
    assert "Increase duration" in result.summary["suggested_next_settings"][0]
    text = result.to_text()
    assert "paper_basket_diagnostics_status=PAPER_BASKET_DIAGNOSTICS_OK" in text
    assert "PAPER_CANDIDATE" not in text
    assert "POSSIBLE_ARB" not in text


def test_paper_basket_diagnostics_falls_back_to_csv_actions_and_target_summaries(tmp_path):
    reports_dir = tmp_path / "reports"
    _write_paper_basket_exports(
        reports_dir,
        summary={
            "status": "PAPER_BASKET_COLLECTING_FILLS",
            "target_hygiene_verdict": "TARGET_HYGIENE_OK",
            "targets": 1,
            "filled_quotes": 0,
        },
        actions=[
            {
                "action": "NO_QUOTE",
                "market_ticker": "KXHIGHAUS-26MAY21-B89.5",
                "side": "BUY_NO",
                "reason": "Already has 1 open paper quote(s).",
            },
            {
                "action": "NO_QUOTE",
                "market_ticker": "KXLOWNY-26MAY21-T60",
                "side": "BUY_YES",
                "reason": "Current spread 3.00c below minimum 8.00c.",
            },
            {
                "action": "QUOTE_OPENED",
                "market_ticker": "KXHIGHAUS-26MAY21-B89.5",
                "side": "BUY_NO",
                "reason": "Opened paper quote id=2 at 10.00c.",
            },
        ],
        targets=[{"market_ticker": "KXHIGHAUS-26MAY21-B89.5", "side": "BUY_NO", "tier": "REPLAY_SUPPORTED"}],
        target_summaries=[
            {
                "market_ticker": "KXHIGHAUS-26MAY21-B89.5",
                "side": "BUY_NO",
                "tier": "REPLAY_SUPPORTED",
                "filled_quotes": 1,
            }
        ],
    )

    result = PaperBasketDiagnosticsReporter(reports_dir=reports_dir).build()

    assert result.summary["total_quotes_opened"] == 1
    assert result.summary["quote_rejection_breakdown"]["max_open_quotes_reached"] == 1
    assert result.summary["quote_rejection_breakdown"]["spread_below_minimum"] == 1
    assert result.summary["strict_targets"] == 1
    assert result.summary["exploratory_targets"] == 0
    assert result.summary["targets_with_any_fill"] == [
        {"market_ticker": "KXHIGHAUS-26MAY21-B89.5", "side": "BUY_NO", "tier": "REPLAY_SUPPORTED"}
    ]


def test_paper_basket_diagnostics_prefers_nonzero_summary_rejection_breakdown(tmp_path):
    reports_dir = tmp_path / "reports"
    _write_paper_basket_exports(
        reports_dir,
        summary={
            "status": "PAPER_BASKET_ACTIVE_NO_FILLS_YET",
            "target_hygiene_verdict": "TARGET_HYGIENE_OK",
            "targets": 1,
            "quote_rejection_breakdown": {
                "spread_below_minimum": 1,
                "max_open_quotes_reached": 0,
                "target_removed": 0,
                "stale_or_expired_target": 0,
                "no_valid_target": 0,
                "other": 0,
            },
        },
        actions=[
            {
                "action": "NO_QUOTE",
                "market_ticker": "KXHIGHAUS-26MAY21-B89.5",
                "side": "BUY_NO",
                "reason": "Current spread 3.00c below minimum 8.00c.",
            }
        ],
        targets=[{"market_ticker": "KXHIGHAUS-26MAY21-B89.5", "side": "BUY_NO", "tier": "REPLAY_SUPPORTED"}],
        target_summaries=[],
    )

    result = PaperBasketDiagnosticsReporter(reports_dir=reports_dir).build()

    assert result.summary["quote_rejection_breakdown"]["spread_below_minimum"] == 1
    assert result.summary["spread_below_minimum_count"] == 1


def test_paper_basket_diagnostics_uses_csv_rejections_when_summary_breakdown_absent(tmp_path):
    reports_dir = tmp_path / "reports"
    _write_paper_basket_exports(
        reports_dir,
        summary={
            "status": "PAPER_BASKET_ACTIVE_NO_FILLS_YET",
            "target_hygiene_verdict": "TARGET_HYGIENE_OK",
            "targets": 1,
        },
        actions=[
            {
                "action": "NO_QUOTE",
                "market_ticker": "KXHIGHAUS-26MAY21-B89.5",
                "side": "BUY_NO",
                "reason": "Current spread 3.00c below minimum 8.00c.",
            },
            {
                "action": "NO_QUOTE",
                "market_ticker": "KXLOWNY-26MAY21-T60",
                "side": "BUY_YES",
                "reason": "Already has 1 open paper quote(s).",
            },
        ],
        targets=[{"market_ticker": "KXHIGHAUS-26MAY21-B89.5", "side": "BUY_NO", "tier": "REPLAY_SUPPORTED"}],
        target_summaries=[],
    )

    result = PaperBasketDiagnosticsReporter(reports_dir=reports_dir).build()

    assert result.summary["quote_rejection_breakdown"]["spread_below_minimum"] == 1
    assert result.summary["quote_rejection_breakdown"]["max_open_quotes_reached"] == 1


def test_paper_basket_diagnostics_missing_exports_fails_safely(tmp_path):
    result = PaperBasketDiagnosticsReporter(reports_dir=tmp_path / "reports").build()

    assert result.summary["diagnostics_status"] == "PAPER_BASKET_DIAGNOSTICS_MISSING_EXPORTS"
    assert result.summary["research_only"] is True
    assert result.summary["readiness_promotion"] == "none"
    assert set(result.summary["missing_exports"]) == {"summary", "actions", "targets", "target_summaries"}


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


def test_paper_basket_lifecycle_fill_persists_after_final_zero_state():
    lifecycle = _new_lifecycle()
    first = PaperMarketMakingBasketResult(
        summary={"filled_quotes": 1, "open_quotes": 0, "cancelled_quotes": 0},
        targets=[{"market_ticker": "OLD", "side": "BUY_YES", "tier": "REPLAY_SUPPORTED"}],
        target_results=[{"market_ticker": "OLD", "side": "BUY_YES", "filled_quotes": 1}],
        actions=[{"action": "QUOTE_FILLED", "reason": "filled"}],
    )
    final = PaperMarketMakingBasketResult(
        summary={"filled_quotes": 0, "open_quotes": 0, "cancelled_quotes": 0},
        targets=[],
        target_results=[],
        actions=[],
    )

    _update_lifecycle(lifecycle, first)
    _update_lifecycle(lifecycle, final)
    summary = {"filled_quotes": 0, "open_quotes": 0, "cancelled_quotes": 0, "_final_targets": []}
    _apply_lifecycle(summary, lifecycle)

    assert summary["total_trade_print_fills_seen"] == 1
    assert summary["final_filled_quotes"] == 0
    assert summary["fill_seen_in_history_but_not_final"] is True
    assert summary["targets_with_any_fill"] == [{"market_ticker": "OLD", "side": "BUY_YES", "tier": "REPLAY_SUPPORTED"}]
    assert summary["quote_rejection_breakdown"]["target_removed"] == 1
    assert summary["targets_removed_reason"] == {"OLD|BUY_YES": "target_removed_after_selector_refresh"}


def test_paper_basket_lifecycle_quote_rejection_breakdown_counts_reasons():
    lifecycle = _new_lifecycle()
    result = PaperMarketMakingBasketResult(
        summary={"filled_quotes": 0, "open_quotes": 0, "cancelled_quotes": 0},
        targets=[{"market_ticker": "A", "side": "BUY_YES", "tier": "EXPLORATORY_CURRENT"}],
        target_results=[],
        actions=[
            {"action": "NO_QUOTE", "reason": "Spread 4.0 below minimum 8.00c."},
            {"action": "NO_QUOTE", "reason": "Already has 1 open paper quote(s)."},
            {"action": "NO_QUOTE", "reason": "Latest orderbook is stale: 999s old."},
            {"action": "NO_QUOTE", "reason": "Book is missing same-side bid or opposing ask."},
            {"action": "QUOTE_OPENED", "reason": "Opened paper quote id=1 at 10.00c."},
            {"action": "QUOTE_CANCELLED", "reason": "Quote id=1 cancelled after 300s."},
        ],
    )

    _update_lifecycle(lifecycle, result)
    summary = {"filled_quotes": 0, "open_quotes": 0, "cancelled_quotes": 0, "_final_targets": result.targets}
    _apply_lifecycle(summary, lifecycle)

    assert summary["quote_rejection_breakdown"]["spread_below_minimum"] == 1
    assert summary["quote_rejection_breakdown"]["max_open_quotes_reached"] == 1
    assert summary["quote_rejection_breakdown"]["stale_or_expired_target"] == 1
    assert summary["quote_rejection_breakdown"]["other"] == 1
    assert summary["total_quotes_opened"] == 1
    assert summary["total_quotes_cancelled"] == 1


def test_paper_basket_lifecycle_reports_strict_and_exploratory_seen():
    lifecycle = _new_lifecycle()
    result = PaperMarketMakingBasketResult(
        summary={"filled_quotes": 0, "open_quotes": 0, "cancelled_quotes": 0},
        targets=[
            {"market_ticker": "STRICT", "side": "BUY_YES", "tier": "REPLAY_SUPPORTED"},
            {"market_ticker": "EXP", "side": "BUY_NO", "tier": "EXPLORATORY_CURRENT"},
        ],
        target_results=[],
        actions=[],
    )

    _update_lifecycle(lifecycle, result)
    summary = {"filled_quotes": 0, "open_quotes": 0, "cancelled_quotes": 0, "_final_targets": result.targets}
    _apply_lifecycle(summary, lifecycle)

    assert summary["target_sets_seen"] == 1
    assert summary["max_targets_seen"] == 2
    assert summary["strict_targets_seen"] == 1
    assert summary["exploratory_targets_seen"] == 1
    assert summary["targets_seen_over_run"] == [
        {"market_ticker": "STRICT", "side": "BUY_YES", "tier": "REPLAY_SUPPORTED"},
        {"market_ticker": "EXP", "side": "BUY_NO", "tier": "EXPLORATORY_CURRENT"},
    ]
    assert summary["targets_final"] == summary["targets_seen_over_run"]


def test_paper_basket_rejection_bucket_classifier():
    assert _quote_rejection_bucket("Spread 4.0 below minimum 8.00c.") == "spread_below_minimum"
    assert _quote_rejection_bucket("Already has 1 open paper quote(s).") == "max_open_quotes_reached"
    assert _quote_rejection_bucket("Latest orderbook is stale: 999s old.") == "stale_or_expired_target"
    assert _quote_rejection_bucket("Market status is closed; paper maker will not quote.") == "stale_or_expired_target"
    assert _quote_rejection_bucket("Book is missing same-side bid or opposing ask.") == "other"


def test_paper_basket_module_has_no_live_trading_calls():
    import live.paper_market_making_basket as basket_module

    source = inspect.getsource(basket_module)
    forbidden = ("create_order", "cancel_order", "private_key", "account_balance", "positions", "wallet")
    for token in forbidden:
        assert token not in source


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
