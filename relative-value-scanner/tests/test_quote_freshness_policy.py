from __future__ import annotations

from datetime import datetime, timezone

from relative_value.quote_freshness_policy import quote_freshness_status


NOW = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)


def test_quote_freshness_status_accepts_fresh_timestamp() -> None:
    status = quote_freshness_status(
        "2026-05-25T11:55:00+00:00",
        now=NOW,
        staleness_seconds=900,
    )

    assert status == {
        "captured_at": "2026-05-25T11:55:00+00:00",
        "age_seconds": 300,
        "is_fresh": True,
        "blocker": None,
    }


def test_quote_freshness_status_flags_stale_timestamp() -> None:
    status = quote_freshness_status(
        "2026-05-25T11:00:00Z",
        now=NOW,
        staleness_seconds=900,
    )

    assert status["age_seconds"] == 3600
    assert status["is_fresh"] is False
    assert status["blocker"] == "stale_quote"


def test_quote_freshness_status_flags_missing_timestamp() -> None:
    status = quote_freshness_status(None, now=NOW, staleness_seconds=900)

    assert status["captured_at"] is None
    assert status["age_seconds"] is None
    assert status["is_fresh"] is False
    assert status["blocker"] == "missing_quote_captured_at"


def test_quote_freshness_status_flags_malformed_timestamp() -> None:
    status = quote_freshness_status("not-a-date", now=NOW, staleness_seconds=900)

    assert status["captured_at"] == "not-a-date"
    assert status["age_seconds"] is None
    assert status["is_fresh"] is False
    assert status["blocker"] == "missing_quote_captured_at"


def test_quote_freshness_status_flags_future_timestamp_as_blocker() -> None:
    status = quote_freshness_status(
        "2026-05-25T13:00:00Z",
        now=NOW,
        staleness_seconds=900,
    )

    assert status["captured_at"] == "2026-05-25T13:00:00Z"
    assert status["age_seconds"] == -3600
    assert status["is_fresh"] is False
    assert status["blocker"] == "future_quote_captured_at"
