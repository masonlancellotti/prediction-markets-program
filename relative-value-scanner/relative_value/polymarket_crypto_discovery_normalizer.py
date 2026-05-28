from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.polymarket_public_discovery import HARD_COMPOUND_CONTEXT_PATTERN, REPORT_SOURCE as DISCOVERY_SOURCE


SCHEMA_VERSION = 1
REPORT_SOURCE = "polymarket_crypto_discovery_normalized_v1"

SHAPE_POINT_IN_TIME_THRESHOLD = "POINT_IN_TIME_THRESHOLD"
SHAPE_HOURLY_UP_DOWN = "HOURLY_UP_DOWN"
SHAPE_SHORT_WINDOW_UP_DOWN = "SHORT_WINDOW_UP_DOWN"
SHAPE_DAILY_THRESHOLD = "DAILY_THRESHOLD"
SHAPE_DEADLINE_HIT_BY_DATE = "DEADLINE_HIT_BY_DATE"
SHAPE_MONTHLY_EXTREME_HIGH_LOW = "MONTHLY_EXTREME_HIGH_LOW"
SHAPE_YEAR_END_HIT_BY_DATE = "YEAR_END_HIT_BY_DATE"
SHAPE_UNKNOWN_OR_COMPOUND = "UNKNOWN_OR_COMPOUND"

# Backward-compatible names for existing tests/imports.
SHAPE_POINT_IN_TIME = SHAPE_POINT_IN_TIME_THRESHOLD
SHAPE_DEADLINE_OR_DATE_RANGE_HIT = SHAPE_DEADLINE_HIT_BY_DATE
SHAPE_UNKNOWN = SHAPE_UNKNOWN_OR_COMPOUND

MATCH_EXACT_REVIEW_IF_SOURCE_WINDOW_MATCH = "EXACT_REVIEW_POSSIBLE_ONLY_IF_SOURCE_WINDOW_MATCH"
MATCH_BTC_BASIS_RISK_POSSIBLE = "BTC_BASIS_RISK_POSSIBLE"
MATCH_ONE_SIDED_FV_ONLY = "ONE_SIDED_FV_ONLY"
MATCH_UP_DOWN_FV_ONLY = "UP_DOWN_FV_ONLY"
MATCH_DISCOVERY_ONLY = "DISCOVERY_ONLY"

_ASSET_PATTERN = re.compile(r"\b(bitcoin|btc|ethereum|eth)\b", re.IGNORECASE)
_THRESHOLD_PATTERN = re.compile(r"\$?\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*([kmb])?\b", re.IGNORECASE)
_MONTH_YEAR_PATTERN = re.compile(
    r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(\d{4})\b",
    re.IGNORECASE,
)
_DATE_PATTERN = re.compile(
    r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+\d{1,2}(?:st|nd|rd|th)?(?:,\s*\d{4})?\b",
    re.IGNORECASE,
)
_TIME_PATTERN = re.compile(
    r"\b(\d{1,2}(?::\d{2})?\s*(?:AM|PM|a\.m\.|p\.m\.)?)\s*"
    r"(EST|EDT|CST|CDT|MST|MDT|PST|PDT|UTC|GMT|ET|CT|MT|PT)\b",
    re.IGNORECASE,
)
_URL_PATTERN = re.compile(r"https?://[^\s),;\"']+")

_MONTH_NUMBERS = {
    "jan": "01",
    "january": "01",
    "feb": "02",
    "february": "02",
    "mar": "03",
    "march": "03",
    "apr": "04",
    "april": "04",
    "may": "05",
    "jun": "06",
    "june": "06",
    "jul": "07",
    "july": "07",
    "aug": "08",
    "august": "08",
    "sep": "09",
    "september": "09",
    "oct": "10",
    "october": "10",
    "nov": "11",
    "november": "11",
    "dec": "12",
    "december": "12",
}


def build_polymarket_crypto_discovery_normalization_report(
    *,
    discovery_path: Path,
    output_dir: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    payload = _load_json(discovery_path)
    candidates = payload.get("candidates") if isinstance(payload, dict) else []
    candidates = [row for row in candidates if isinstance(row, dict)] if isinstance(candidates, list) else []
    output_dir.mkdir(parents=True, exist_ok=True)

    normalized: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    shape_counts: Counter[str] = Counter()
    candidate_shape_counts: Counter[str] = Counter()
    token_ids_carried = 0
    markets_expanded = 0
    book_files_attached_total = 0
    fixtures_with_any_book = 0
    fixtures_with_all_tokens_with_books = 0

    for index, candidate in enumerate(candidates):
        candidate_shape_counts[_market_shape(_candidate_text(candidate))] += 1
        fixture, skip = _candidate_to_fixture(candidate, index=index, generated_at=generated)
        if skip is not None:
            skipped.append(skip)
            continue
        if fixture is None:
            skipped.append({"row_index": index, "reason": "unsupported_candidate_shape"})
            continue
        safe_name = _safe_slug(
            _string_or_none(fixture.get("event_slug"))
            or _string_or_none((fixture.get("markets") or [{}])[0].get("market_id"))
            or f"candidate-{index}"
        )
        fixture_path = output_dir / f"{index:04d}_{safe_name}.json"
        fixture_path.write_text(json.dumps(fixture, indent=2, sort_keys=True), encoding="utf-8")
        normalized.append(
            {
                "row_index": index,
                "fixture_path": str(fixture_path),
                "event_slug": fixture.get("event_slug"),
                "market_shape": fixture.get("market_shape"),
                "settlement_shape": fixture.get("settlement_shape"),
                "matchability_class": fixture.get("matchability_class"),
                "asset": fixture.get("asset"),
                "market_count": len(fixture.get("markets") or []),
                "blockers": list(fixture.get("blockers") or []),
                "settlement_source": fixture.get("settlement_source"),
                "settlement_source_url": fixture.get("settlement_source_url"),
            }
        )
        shape_counts[str(fixture.get("market_shape") or fixture.get("settlement_shape") or SHAPE_UNKNOWN_OR_COMPOUND)] += 1
        token_ids_carried += sum(len(market.get("token_ids") or []) for market in fixture.get("markets") or [])
        markets_expanded += len(fixture.get("markets") or [])
        fixture_book_count = 0
        all_tokens_have_books = bool(fixture.get("markets"))
        for market in fixture.get("markets") or []:
            book_files = market.get("book_files_by_token_id") or {}
            attached = sum(1 for path in book_files.values() if path)
            fixture_book_count += attached
            token_ids_for_market = market.get("token_ids") or []
            if not token_ids_for_market or not all(book_files.get(t) for t in token_ids_for_market):
                all_tokens_have_books = False
        book_files_attached_total += fixture_book_count
        if fixture_book_count > 0:
            fixtures_with_any_book += 1
        if all_tokens_have_books:
            fixtures_with_all_tokens_with_books += 1

    skipped_counts = Counter(str(row.get("reason") or "unknown") for row in skipped)
    return {
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "discovery": str(discovery_path),
        "discovery_source": payload.get("source") if isinstance(payload, dict) else None,
        "output_dir": str(output_dir),
        "summary": {
            "discovery_candidates_read": len(candidates),
            "normalized_fixtures_written": len(normalized),
            "markets_expanded": markets_expanded,
            "candidates_by_market_shape": dict(sorted(candidate_shape_counts.items())),
            "normalized_rows_by_market_shape": dict(sorted(shape_counts.items())),
            "point_in_time_count": shape_counts.get(SHAPE_POINT_IN_TIME_THRESHOLD, 0),
            "hourly_up_down_count": shape_counts.get(SHAPE_HOURLY_UP_DOWN, 0),
            "short_window_up_down_count": shape_counts.get(SHAPE_SHORT_WINDOW_UP_DOWN, 0),
            "daily_threshold_count": shape_counts.get(SHAPE_DAILY_THRESHOLD, 0),
            "monthly_extreme_count": shape_counts.get(SHAPE_MONTHLY_EXTREME_HIGH_LOW, 0),
            "deadline_count": shape_counts.get(SHAPE_DEADLINE_HIT_BY_DATE, 0),
            "year_end_count": shape_counts.get(SHAPE_YEAR_END_HIT_BY_DATE, 0),
            "range_hit_count": shape_counts.get(SHAPE_DEADLINE_HIT_BY_DATE, 0)
            + shape_counts.get(SHAPE_YEAR_END_HIT_BY_DATE, 0),
            "discovery_only_count": sum(
                1
                for row in normalized
                if row.get("matchability_class") == MATCH_DISCOVERY_ONLY
            )
            + skipped_counts.get("unknown_or_compound_market_shape", 0),
            "skipped_count_by_reason": dict(sorted(skipped_counts.items())),
            "token_ids_carried": token_ids_carried,
            "book_files_attached_total": book_files_attached_total,
            "fixtures_with_any_book_attached": fixtures_with_any_book,
            "fixtures_with_all_tokens_with_books": fixtures_with_all_tokens_with_books,
            "paper_candidate_count": 0,
        },
        "normalized_fixtures": normalized,
        "skipped": skipped,
        "safety": {
            "saved_files_only": True,
            "live_fetch_attempted": False,
            "authenticated_endpoints_used": False,
            "orders_or_cancellations": False,
            "account_or_wallet_or_signing_code": False,
            "candidate_pair_creation": False,
            "paper_candidate_emitted": False,
            "affects_evaluator_gates": False,
            "registry_proposal_is_trust": False,
        },
    }


def write_polymarket_crypto_discovery_normalization_files(
    *,
    discovery_path: Path,
    output_dir: Path,
    json_output: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_polymarket_crypto_discovery_normalization_report(
        discovery_path=discovery_path,
        output_dir=output_dir,
        generated_at=generated_at,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def _candidate_to_fixture(
    candidate: dict[str, Any],
    *,
    index: int,
    generated_at: datetime,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    text = _candidate_text(candidate)
    if HARD_COMPOUND_CONTEXT_PATTERN.search(text):
        return None, {"row_index": index, "reason": "compound_or_non_price_market", "market_slug": candidate.get("market_slug")}
    asset = _asset(text)
    if asset is None:
        return None, {"row_index": index, "reason": "missing_asset", "market_slug": candidate.get("market_slug")}
    shape = _market_shape(text)
    if shape == SHAPE_UNKNOWN_OR_COMPOUND:
        return None, {"row_index": index, "reason": "unknown_or_compound_market_shape", "market_slug": candidate.get("market_slug")}
    threshold = _threshold(text)
    if threshold is None and shape not in {SHAPE_HOURLY_UP_DOWN, SHAPE_SHORT_WINDOW_UP_DOWN}:
        return None, {"row_index": index, "reason": "missing_threshold", "market_slug": candidate.get("market_slug")}
    direction, operator = _direction_and_operator(text)
    if direction is None or operator is None:
        return None, {"row_index": index, "reason": "missing_direction", "market_slug": candidate.get("market_slug")}

    settlement_source = _settlement_source(candidate, asset=asset)
    settlement_source_url = _settlement_source_url(candidate)
    blockers: list[str] = ["manual_fixture_not_live_market_snapshot"]
    if shape == SHAPE_MONTHLY_EXTREME_HIGH_LOW:
        blockers.extend(
            [
                "monthly_extreme_window_not_point_in_time",
                "not_same_payoff_with_kalshi_point_in_time",
            ]
        )
    elif shape in {SHAPE_DEADLINE_HIT_BY_DATE, SHAPE_YEAR_END_HIT_BY_DATE}:
        blockers.extend(
            [
                "deadline_hit_by_date_not_point_in_time",
                "deadline_or_date_range_hit_window_not_point_in_time",
                "not_same_payoff_with_kalshi_point_in_time",
            ]
        )
    elif shape == SHAPE_DAILY_THRESHOLD:
        blockers.extend(["daily_threshold_window_not_point_in_time", "not_same_payoff_with_kalshi_point_in_time"])
    elif shape in {SHAPE_HOURLY_UP_DOWN, SHAPE_SHORT_WINDOW_UP_DOWN}:
        blockers.extend(["hourly_up_down_not_threshold_market", "not_same_payoff_with_kalshi_point_in_time"])
    if not settlement_source:
        blockers.append("missing_price_source_index")
        blockers.append("missing_settlement_source")

    measurement_month = _measurement_month(text) if shape == SHAPE_MONTHLY_EXTREME_HIGH_LOW else None
    measurement_date = _measurement_date(text)
    measurement_time, timezone_text = _measurement_time_and_timezone(text)
    settlement_window = _settlement_window(shape=shape, direction=direction, asset=asset, source=settlement_source)
    measurement_window_start, measurement_window_end = _measurement_window(shape=shape, text=text)
    matchability_class = _matchability_class(shape=shape, source=settlement_source)
    event_slug = _string_or_none(candidate.get("event_slug")) or _string_or_none(candidate.get("market_slug"))
    threshold_part = _threshold_token(threshold) if threshold is not None else shape.lower()
    market_id = _string_or_none(candidate.get("market_id")) or _string_or_none(candidate.get("market_slug")) or f"{event_slug}-{direction}-{threshold_part}"
    book_files_by_token_id = (
        candidate.get("book_files_by_token_id")
        if isinstance(candidate.get("book_files_by_token_id"), dict)
        else {}
    )
    token_ids = list(candidate.get("token_ids") or [])
    book_files_attached = {
        token_id: book_files_by_token_id.get(token_id)
        for token_id in token_ids
        if book_files_by_token_id.get(token_id)
    }
    market = {
        "market_id": market_id,
        "source_discovery_row_id": candidate.get("row_index", index),
        "direction": direction,
        "operator": operator,
        "threshold": threshold,
        "label": _string_or_none(candidate.get("question") or candidate.get("title") or candidate.get("market_slug")),
        "token_ids": token_ids,
        "book_files_by_token_id": book_files_attached,
        "market_shape": shape,
        "settlement_shape": shape,
        "settlement_window": settlement_window,
        "matchability_class": matchability_class,
        "blockers": list(blockers),
    }
    return (
        {
            "fixture_kind": "manual_polymarket_crypto_event_page_snapshot",
            "generated_from": REPORT_SOURCE,
            "source_discovery_report_source": DISCOVERY_SOURCE,
            "source_discovery_row_id": candidate.get("row_index", index),
            "venue": "polymarket",
            "source_url": _string_or_none(candidate.get("source_url")),
            "event_slug": event_slug,
            "event_title": _string_or_none(candidate.get("title") or candidate.get("question")),
            "asset": asset,
            "measurement_month": measurement_month,
            "measurement_date": measurement_date,
            "measurement_time": measurement_time,
            "timezone": timezone_text,
            "measurement_window_start": measurement_window_start,
            "measurement_window_end": measurement_window_end,
            "market_shape": shape,
            "settlement_shape": shape,
            "settlement_source": settlement_source,
            "price_source_index": settlement_source,
            "settlement_source_url": settlement_source_url,
            "settlement_window": settlement_window,
            "matchability_class": matchability_class,
            "rules_text": _rules_text(candidate),
            "description": _string_or_none(candidate.get("description")),
            "resolutionSource": _string_or_none(candidate.get("resolution_source")),
            "markets": [market],
            "blockers": blockers,
            "diagnostic_only": True,
            "can_create_candidate_pair": False,
            "can_create_paper_candidate": False,
            "normalized_from_public_discovery_at": generated_at.isoformat(),
        },
        None,
    )


def _candidate_text(candidate: dict[str, Any]) -> str:
    return " ".join(
        str(value)
        for value in (
            candidate.get("event_slug"),
            candidate.get("market_slug"),
            candidate.get("title"),
            candidate.get("question"),
            candidate.get("rules"),
            candidate.get("description"),
            candidate.get("resolution_source"),
        )
        if value
    )


def _rules_text(candidate: dict[str, Any]) -> str | None:
    return _string_or_none(
        "\n\n".join(
            str(value)
            for value in (candidate.get("rules"), candidate.get("description"), candidate.get("resolution_source"))
            if value
        )
    )


def _asset(text: str) -> str | None:
    match = _ASSET_PATTERN.search(text)
    if not match:
        return None
    token = match.group(1).lower()
    if token in {"bitcoin", "btc"}:
        return "BTC"
    if token in {"ethereum", "eth"}:
        return "ETH"
    return None


def _threshold(text: str) -> float | None:
    for match in _THRESHOLD_PATTERN.finditer(text):
        raw_token = match.group(0)
        value = match.group(1).replace(",", "")
        try:
            number = float(value)
        except ValueError:
            continue
        suffix = (match.group(2) or "").lower()
        has_price_hint = "$" in raw_token or "," in raw_token or bool(suffix)
        if not has_price_hint and 1900 <= number <= 2100:
            continue
        if suffix == "k":
            number *= 1_000
        elif suffix == "m":
            number *= 1_000_000
        elif suffix == "b":
            number *= 1_000_000_000
        if number >= 100:
            return number
    return None


def _direction_and_operator(text: str) -> tuple[str | None, str | None]:
    lowered = text.lower()
    if re.search(r"\bup[-\s]?or[-\s]?down\b", lowered) or re.search(r"\bclose\s*(?:>=|>|above|below|<|<=)\s*open\b", lowered):
        return "up_down", "close_vs_open"
    if re.search(r"\b(below|less\s+than|under|dip(?:s|ped)?\s+to|low)\b", lowered):
        return "below", "<="
    if re.search(r"\b(above|greater\s+than|over|hit|reach|cross|high)\b", lowered):
        return "above", ">="
    return None, None


def _market_shape(text: str) -> str:
    lowered = text.lower()
    if re.search(r"\bup[-\s]?or[-\s]?down\b", lowered) or re.search(r"\bclose\s*(?:>=|>|above|below|<|<=)\s*open\b", lowered):
        if re.search(r"\b(hour|hourly|1[-\s]?hour)\b", lowered):
            return SHAPE_HOURLY_UP_DOWN
        return SHAPE_SHORT_WINDOW_UP_DOWN
    if re.search(r"\bin\s+(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{4}\b", lowered):
        return SHAPE_MONTHLY_EXTREME_HIGH_LOW
    if re.search(r"\b(before|end\s+of)\s+20\d{2}\b", lowered):
        return SHAPE_YEAR_END_HIT_BY_DATE
    if re.search(r"\b(by|before|during)\b", lowered) and (
        _DATE_PATTERN.search(text) or _MONTH_YEAR_PATTERN.search(text) or re.search(r"\b20\d{2}\b", lowered)
    ):
        return SHAPE_DEADLINE_HIT_BY_DATE
    if _TIME_PATTERN.search(text) and _DATE_PATTERN.search(text) and re.search(r"\bat\b", lowered):
        return SHAPE_POINT_IN_TIME_THRESHOLD
    if _DATE_PATTERN.search(text) and re.search(r"\b(daily|on\s+[a-z]+\s+\d{1,2}|day)\b", lowered):
        return SHAPE_DAILY_THRESHOLD
    return SHAPE_UNKNOWN_OR_COMPOUND


def _settlement_shape(text: str) -> str:
    return _market_shape(text)


def _measurement_month(text: str) -> str | None:
    match = _MONTH_YEAR_PATTERN.search(text)
    if not match:
        return None
    month = _MONTH_NUMBERS.get(match.group(1).lower())
    return f"{match.group(2)}-{month}" if month else None


def _measurement_date(text: str) -> str | None:
    match = _DATE_PATTERN.search(text)
    return match.group(0) if match else None


def _measurement_time_and_timezone(text: str) -> tuple[str | None, str | None]:
    match = _TIME_PATTERN.search(text)
    if not match:
        return None, None
    return " ".join(part.strip().upper().replace(".", "") for part in match.groups()), match.group(2).upper()


def _settlement_source(candidate: dict[str, Any], *, asset: str) -> str | None:
    text = (_rules_text(candidate) or "").lower()
    if "binance" in text:
        pair = f"{asset}/USDT"
        return f"Binance {pair} 1-minute candles"
    if "chainlink" in text:
        return f"Chainlink {asset}/USD data stream"
    if "coinbase" in text:
        return f"Coinbase {asset} spot price"
    if "cf benchmarks" in text or "brti" in text:
        return "CF Benchmarks Bitcoin Real-Time Index" if asset == "BTC" else "CF Benchmarks Ethereum Real-Time Index"
    return None


def _settlement_source_url(candidate: dict[str, Any]) -> str | None:
    explicit = _string_or_none(candidate.get("resolution_source"))
    if explicit and explicit.startswith("http"):
        return explicit
    text = _rules_text(candidate) or ""
    match = _URL_PATTERN.search(text)
    return match.group(0) if match else None


def _settlement_window(*, shape: str, direction: str, asset: str, source: str | None) -> str:
    source_text = source or f"{asset} source"
    if shape == SHAPE_POINT_IN_TIME_THRESHOLD:
        return "point_in_time"
    if shape == SHAPE_MONTHLY_EXTREME_HIGH_LOW:
        high_low = "High" if direction == "above" else "Low"
        return f"any {source_text} final {high_low} during month"
    if shape == SHAPE_DAILY_THRESHOLD:
        return f"daily_{direction}_threshold"
    if shape == SHAPE_YEAR_END_HIT_BY_DATE:
        return f"year_end_deadline_{direction}_hit"
    if shape == SHAPE_DEADLINE_HIT_BY_DATE:
        return f"deadline_or_date_range_{direction}_hit"
    if shape in {SHAPE_HOURLY_UP_DOWN, SHAPE_SHORT_WINDOW_UP_DOWN}:
        return "interval_close_vs_open"
    return "unknown_window"


def _measurement_window(*, shape: str, text: str) -> tuple[str | None, str | None]:
    month = _measurement_month(text)
    if shape == SHAPE_MONTHLY_EXTREME_HIGH_LOW and month:
        return f"{month}-01T00:00:00", f"{month}-end"
    year_match = re.search(r"\b(20\d{2})\b", text)
    if shape == SHAPE_YEAR_END_HIT_BY_DATE and year_match:
        year = year_match.group(1)
        return f"{year}-01-01T00:00:00", f"{year}-12-31T23:59:59"
    return None, None


def _matchability_class(*, shape: str, source: str | None) -> str:
    if shape == SHAPE_POINT_IN_TIME_THRESHOLD and source:
        return MATCH_BTC_BASIS_RISK_POSSIBLE
    if shape in {SHAPE_MONTHLY_EXTREME_HIGH_LOW, SHAPE_DEADLINE_HIT_BY_DATE, SHAPE_YEAR_END_HIT_BY_DATE, SHAPE_DAILY_THRESHOLD}:
        return MATCH_ONE_SIDED_FV_ONLY if source else MATCH_DISCOVERY_ONLY
    if shape in {SHAPE_HOURLY_UP_DOWN, SHAPE_SHORT_WINDOW_UP_DOWN}:
        return MATCH_UP_DOWN_FV_ONLY if source else MATCH_DISCOVERY_ONLY
    return MATCH_DISCOVERY_ONLY


def _threshold_token(value: float | None) -> str:
    if value is None:
        return "none"
    return str(int(value)) if float(value).is_integer() else str(value).replace(".", "-")


def _safe_slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-._")[:96] or "candidate"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
