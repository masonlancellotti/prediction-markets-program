from __future__ import annotations

import inspect
from datetime import datetime, timezone

from research.paper_market_making_target_review import (
    PaperMarketMakingTargetReviewConfig,
    build_target_review,
)


def _analyzer(ticker: str = "M", side: str = "BUY_YES", **overrides):
    row = {
        "market_ticker": ticker,
        "best_side": side,
        "candidate_quotes": 100,
        "trade_evidence_fills": 30,
        "fill_rate": 0.3,
        "avg_future_edge_30m_cents": 12.0,
        "avg_edge_after_penalty_30m_cents": 10.0,
        "adverse_fill_rate_30m": 0.1,
        "readiness": "PAPER_WATCHLIST",
    }
    row.update(overrides)
    return row


def _evidence(ticker: str = "M", side: str = "BUY_YES", **overrides):
    row = {
        "market_ticker": ticker,
        "side": side,
        "quotes_total": 20,
        "open_quotes": 0,
        "quotes_filled": 6,
        "fill_rate": 0.3,
        "avg_net_markout_30m_cents": 5.0,
        "gross_markout_30m_observations": 6,
        "adverse_selection_rate_30m": 0.0,
        "warning_flags": "",
    }
    row.update(overrides)
    return row


def _review(analyzer_rows, evidence_rows):
    return build_target_review(
        analyzer_markets=analyzer_rows,
        evidence_rows=evidence_rows,
        analyzer_summary={"market_making_verdict": "PAPER_WATCHLIST_CANDIDATES"},
        evidence_summary={"status": "PAPER_EVIDENCE_PROMISING_NOT_READY"},
        config=PaperMarketMakingTargetReviewConfig(),
        generated_at=datetime(2026, 5, 20, 12, tzinfo=timezone.utc),
        persist_exports=False,
    )


def test_target_review_summary_marks_weather_only_mode():
    result = build_target_review(
        analyzer_markets=[_analyzer()],
        evidence_rows=[_evidence()],
        analyzer_summary={"market_making_verdict": "PAPER_WATCHLIST_CANDIDATES"},
        evidence_summary={"status": "PAPER_EVIDENCE_PROMISING_NOT_READY"},
        config=PaperMarketMakingTargetReviewConfig(weather_only=True),
        generated_at=datetime(2026, 5, 20, 12, tzinfo=timezone.utc),
        persist_exports=False,
    )

    assert result.summary["weather_only"] is True
    assert "weather_only=true" in result.to_text()


def test_positive_net30_but_too_few_fills_is_not_over_promoted():
    result = _review(
        [_analyzer()],
        [_evidence(quotes_filled=2, gross_markout_30m_observations=2, warning_flags="too_few_fills")],
    )

    assert result.rows[0]["priority_bucket"] == "NEED_MORE_EVIDENCE"
    assert "paper_too_few_fills" in result.rows[0]["review_reasons"]


def test_high_adverse_selection_is_avoid_for_now():
    result = _review(
        [_analyzer()],
        [_evidence(adverse_selection_rate_30m=0.5, warning_flags="adverse_selection_high")],
    )

    assert result.rows[0]["priority_bucket"] == "AVOID_FOR_NOW"
    assert "paper_adverse_selection_high" in result.rows[0]["review_reasons"]


def test_analyzer_only_candidate_needs_more_evidence():
    result = _review([_analyzer()], [])

    assert result.rows[0]["source"] == "analyzer_watchlist"
    assert result.rows[0]["priority_bucket"] == "NEED_MORE_EVIDENCE"
    assert "analyzer_only_no_paper_evidence" in result.rows[0]["review_reasons"]


def test_red_flag_evidence_candidate_is_downgraded_or_avoided():
    result = _review(
        [],
        [_evidence(avg_net_markout_30m_cents=-2.0, warning_flags="current_unrealized_negative")],
    )

    assert result.rows[0]["source"] == "evidence_red_flag"
    assert result.rows[0]["priority_bucket"] in {"DOWNGRADE", "AVOID_FOR_NOW"}


def test_clean_joined_candidate_can_continue_paper_only():
    result = _review([_analyzer()], [_evidence()])

    assert result.rows[0]["source"] == "both"
    assert result.rows[0]["priority_bucket"] == "CONTINUE_PAPER"
    assert result.summary["priority_counts"]["CONTINUE_PAPER"] == 1


def test_target_review_module_has_no_live_trading_calls():
    import research.paper_market_making_target_review as target_review

    source = inspect.getsource(target_review)
    forbidden = ("KalshiClient", "create_order", "cancel_order", "private_key", "enable_live_trading", "TradingReadiness")
    for token in forbidden:
        assert token not in source
