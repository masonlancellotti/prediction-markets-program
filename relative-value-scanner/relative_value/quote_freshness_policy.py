from __future__ import annotations

from datetime import datetime
from typing import Any


DEFAULT_STALENESS_SECONDS = 900


def quote_freshness_status(
    captured_at: str | None,
    *,
    now: datetime,
    staleness_seconds: int,
) -> dict[str, Any]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must include timezone information")
    if not captured_at:
        return {
            "captured_at": captured_at,
            "age_seconds": None,
            "is_fresh": False,
            "blocker": "missing_quote_captured_at",
        }
    parsed = _parse_datetime(captured_at)
    if parsed is None:
        return {
            "captured_at": captured_at,
            "age_seconds": None,
            "is_fresh": False,
            "blocker": "missing_quote_captured_at",
        }
    age_seconds = int((now - parsed).total_seconds())
    if age_seconds < 0:
        return {
            "captured_at": captured_at,
            "age_seconds": age_seconds,
            "is_fresh": False,
            "blocker": "future_quote_captured_at",
        }
    is_fresh = age_seconds <= staleness_seconds
    return {
        "captured_at": captured_at,
        "age_seconds": age_seconds,
        "is_fresh": is_fresh,
        "blocker": None if is_fresh else "stale_quote",
    }


def _parse_datetime(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed
