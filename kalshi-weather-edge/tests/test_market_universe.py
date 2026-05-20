from datetime import datetime, timezone

from research.market_universe import (
    PRIORITY_HIGH,
    PRIORITY_IGNORE,
    MarketUniverseConfig,
    score_market_universe_row,
)


def test_market_universe_scores_active_two_sided_candidate_high():
    market = {
        "ticker": "KXTEST-YES",
        "category": "Test",
        "series_ticker": "KXTEST",
        "event_ticker": "KXTEST-EVENT",
        "status": "open",
        "volume_24h": 100,
        "open_interest": 50,
        "liquidity": 5000,
        "close_time": "2026-05-18T00:00:00Z",
    }
    raw_book = {"yes": [[40, 10]], "no": [[50, 12]]}
    stats = {
        "recent_snapshot_count": 20,
        "recent_two_sided_count": 20,
        "recent_candidate_count": 10,
        "recent_trade_count": 8,
    }

    row = score_market_universe_row(
        market,
        raw_book,
        stats,
        MarketUniverseConfig(min_spread_cents=8, min_displayed_depth=5),
        ranked_at=datetime(2026, 5, 17, tzinfo=timezone.utc),
        run_id="test",
    )

    assert row["priority"] == PRIORITY_HIGH
    assert row["has_two_sided_book"] == 1
    assert row["has_candidate_book"] == 1
    assert row["spread_cents"] == 10
    assert row["recent_trade_count"] == 8
    assert row["score"] > 55


def test_market_universe_ignores_empty_inactive_market():
    market = {
        "ticker": "KXEMPTY",
        "status": "open",
        "volume_24h": 0,
        "open_interest": 0,
        "liquidity": 0,
    }

    row = score_market_universe_row(
        market,
        {"yes": [], "no": []},
        {},
        MarketUniverseConfig(min_spread_cents=8, min_displayed_depth=5),
        ranked_at=datetime(2026, 5, 17, tzinfo=timezone.utc),
        run_id="test",
    )

    assert row["priority"] == PRIORITY_IGNORE
    assert row["has_two_sided_book"] == 0
    assert row["has_candidate_book"] == 0


def test_market_universe_excludes_multivariate_prefix_by_default():
    market = {
        "ticker": "KXMVECROSSCATEGORY-S2026ABC-DEF",
        "status": "open",
        "volume_24h": 100,
        "open_interest": 500,
        "liquidity": 1000,
    }
    raw_book = {"yes": [[40, 20]], "no": [[50, 20]]}
    stats = {"recent_trade_count": 20, "recent_candidate_count": 5}

    row = score_market_universe_row(
        market,
        raw_book,
        stats,
        MarketUniverseConfig(min_spread_cents=8, min_displayed_depth=5),
        ranked_at=datetime(2026, 5, 17, tzinfo=timezone.utc),
        run_id="test",
    )

    assert row["priority"] == PRIORITY_IGNORE
    assert row["excluded_by_prefix"] == 1
    assert row["ticker_family"] == "KXMVECROSSCATEGORY"
    assert "excluded ticker prefix" in row["reason"]
