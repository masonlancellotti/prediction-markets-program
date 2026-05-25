from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from graph_engine.formula import MarketFormula


def adapt_live_like_market_formula(record: dict[str, Any]) -> MarketFormula:
    venue = _required_text(_first_present(record, ["venue", "platform", "exchange"]), "unknown")
    if venue.lower() == "kalshi":
        return adapt_kalshi_market_formula(record)
    if venue.lower() == "polymarket":
        return adapt_polymarket_market_formula(record)
    return _adapt_common_market_formula(record, venue=venue.lower())


def adapt_kalshi_market_formula(record: dict[str, Any]) -> MarketFormula:
    return _adapt_common_market_formula(record, venue="kalshi")


def adapt_polymarket_market_formula(record: dict[str, Any]) -> MarketFormula:
    return _adapt_common_market_formula(record, venue="polymarket")


def load_live_like_market_formulas(path: Path | str) -> list[MarketFormula]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    records = payload.get("markets") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError("live-like formula fixture must contain a markets list")
    return [adapt_live_like_market_formula(record) for record in records if isinstance(record, dict)]


def _adapt_common_market_formula(record: dict[str, Any], *, venue: str) -> MarketFormula:
    text = _market_text(record)
    family = _family(record, text)
    if family == "BTC_THRESHOLD":
        return _btc_formula(record, venue, text)
    if family == "FED_MEETING_RANGE":
        return _fed_formula(record, venue, text)
    if family == "SPORTS_CHAMPION":
        return _sports_formula(record, venue)
    if family == "WEATHER_RANGE":
        return _weather_formula(record, venue, text)
    return MarketFormula(
        market_id=_market_id(record, venue),
        family="UNKNOWN",
        source=_source(record),
        date=_date(record),
        parse_quality=0.0,
        blockers=["unsupported_live_like_fixture"],
        provenance=_provenance(record, venue),
    )


def _btc_formula(record: dict[str, Any], venue: str, text: str) -> MarketFormula:
    blockers: list[str] = []
    source = _source(record)
    date = _date(record)
    threshold = _first_present(record, ["threshold", "strike", "target"])
    if threshold is None:
        threshold = _first_number_after(text, ["above", "over", "greater than", "at least"])
    comparator = _comparator(record, text)

    if source is None:
        blockers.append("missing_source")
    if date is None:
        blockers.append("missing_date")
    if threshold is None:
        blockers.append("missing_threshold")
    if comparator is None:
        blockers.append("missing_comparator")

    return MarketFormula(
        market_id=_market_id(record, venue),
        family="BTC_THRESHOLD",
        subject="BTC",
        asset="BTC",
        source=source,
        date=date,
        settlement_time=_settlement_time(record),
        comparator=comparator,
        threshold=_optional_float(threshold),
        units=_units(record, "USD"),
        side=_side(record),
        parse_quality=_quality(blockers, 0.92, 0.52),
        blockers=blockers,
        provenance=_provenance(record, venue),
    )


def _fed_formula(record: dict[str, Any], venue: str, text: str) -> MarketFormula:
    blockers: list[str] = []
    source = _source(record)
    meeting_date = _first_text(record, ["meeting_date", "fomc_date", "event_date", "resolution_date", "end_date"])
    lower_bound = _first_present(record, ["lower_bound", "floor", "min_rate"])
    upper_bound = _first_present(record, ["upper_bound", "ceiling", "max_rate"])
    if lower_bound is None or upper_bound is None:
        parsed_range = _range_from_text(text)
        if parsed_range is not None:
            lower_bound, upper_bound = parsed_range

    if source is None:
        blockers.append("missing_source")
    if meeting_date is None:
        blockers.append("missing_meeting_date")
    if lower_bound is None or upper_bound is None:
        blockers.append("missing_range")

    return MarketFormula(
        market_id=_market_id(record, venue),
        family="FED_MEETING_RANGE",
        subject="FED_FUNDS",
        source=source,
        meeting_date=meeting_date,
        settlement_time=_settlement_time(record),
        comparator="in_range" if lower_bound is not None and upper_bound is not None else None,
        lower_bound=_optional_float(lower_bound),
        upper_bound=_optional_float(upper_bound),
        units=_units(record, "percent"),
        side=_side(record),
        parse_quality=_quality(blockers, 0.92, 0.50),
        blockers=blockers,
        provenance=_provenance(record, venue),
    )


def _sports_formula(record: dict[str, Any], venue: str) -> MarketFormula:
    blockers: list[str] = []
    source = _source(record)
    date = _date(record)
    team = _first_text(record, ["team", "competitor", "entity", "outcome"])
    if source is None:
        blockers.append("missing_source")
    if date is None:
        blockers.append("missing_date")
    if team is None:
        blockers.append("missing_team")
    return MarketFormula(
        market_id=_market_id(record, venue),
        family="SPORTS_CHAMPION",
        subject=team,
        team=team,
        source=source,
        date=date,
        side=_side(record),
        parse_quality=_quality(blockers, 0.84, 0.45),
        blockers=blockers,
        provenance=_provenance(record, venue),
    )


def _weather_formula(record: dict[str, Any], venue: str, text: str) -> MarketFormula:
    blockers: list[str] = []
    source = _source(record)
    date = _date(record)
    threshold = _first_present(record, ["threshold", "temperature_threshold", "precipitation_threshold"])
    if threshold is None:
        threshold = _first_number_after(text, ["above", "over", "at least", "below", "under"])
    comparator = _comparator(record, text)
    if source is None:
        blockers.append("missing_source")
    if date is None:
        blockers.append("missing_date")
    if threshold is None:
        blockers.append("missing_threshold")
    return MarketFormula(
        market_id=_market_id(record, venue),
        family="WEATHER_RANGE",
        subject=_first_text(record, ["observable", "weather_metric", "metric"]),
        location=_first_text(record, ["location", "city", "region"]),
        source=source,
        date=date,
        settlement_time=_settlement_time(record),
        comparator=comparator,
        threshold=_optional_float(threshold),
        units=_units(record, None),
        side=_side(record),
        parse_quality=_quality(blockers, 0.78, 0.42),
        blockers=blockers,
        provenance=_provenance(record, venue),
    )


def _family(record: dict[str, Any], text: str) -> str:
    explicit = _first_text(record, ["family", "market_family", "category"])
    if explicit:
        normalized = explicit.strip().upper()
        aliases = {
            "CRYPTO": "BTC_THRESHOLD",
            "BTC": "BTC_THRESHOLD",
            "FED": "FED_MEETING_RANGE",
            "FOMC": "FED_MEETING_RANGE",
            "SPORTS": "SPORTS_CHAMPION",
            "WEATHER": "WEATHER_RANGE",
        }
        if normalized in aliases:
            return aliases[normalized]
        if normalized in {"BTC_THRESHOLD", "FED_MEETING_RANGE", "SPORTS_CHAMPION", "WEATHER_RANGE"}:
            return normalized
    if "btc" in text or "bitcoin" in text:
        return "BTC_THRESHOLD"
    if "fed" in text or "fomc" in text or "target rate" in text:
        return "FED_MEETING_RANGE"
    if "champion" in text or "winner" in text or "wins" in text:
        return "SPORTS_CHAMPION"
    if "weather" in text or "temperature" in text or "rain" in text or "snow" in text:
        return "WEATHER_RANGE"
    return "UNKNOWN"


def _market_text(record: dict[str, Any]) -> str:
    parts = [
        _first_text(record, ["title", "question", "name", "subtitle"]),
        _first_text(record, ["description", "rules", "resolution_criteria"]),
    ]
    return " ".join(part for part in parts if part).lower()


def _market_id(record: dict[str, Any], venue: str) -> str:
    value = _first_text(record, ["market_id", "ticker", "slug", "condition_id", "id"])
    if value is None:
        value = "unknown"
    if ":" in value:
        return value
    return f"{venue}:{_slug(value)}"


def _source(record: dict[str, Any]) -> str | None:
    return _first_text(record, ["settlement_source", "resolution_source", "source", "oracle", "data_source"])


def _date(record: dict[str, Any]) -> str | None:
    return _first_text(record, ["date", "target_date", "resolution_date", "end_date", "close_time", "expiration_time"])


def _settlement_time(record: dict[str, Any]) -> str | None:
    return _first_text(record, ["settlement_time", "settlement_window", "window"])


def _units(record: dict[str, Any], default: str | None) -> str | None:
    return _first_text(record, ["units", "unit"]) or default


def _side(record: dict[str, Any]) -> str:
    side = _first_text(record, ["side", "outcome_side"]) or "YES"
    return "NO" if side.upper() == "NO" else "YES"


def _comparator(record: dict[str, Any], text: str) -> str | None:
    value = _first_text(record, ["comparator", "operator"])
    if value in {">", ">=", "<", "<=", "="}:
        return value
    if "at least" in text or "greater than or equal" in text:
        return ">="
    if "above" in text or "over" in text or "greater than" in text:
        return ">"
    if "at most" in text or "less than or equal" in text:
        return "<="
    if "below" in text or "under" in text or "less than" in text:
        return "<"
    return None


def _provenance(record: dict[str, Any], venue: str) -> dict[str, Any]:
    return {
        "adapter": "fixture_live_like_market_json",
        "venue": venue,
        "source_record_id": _first_text(record, ["market_id", "ticker", "slug", "condition_id", "id"]),
        "title": _first_text(record, ["title", "question", "name"]),
        "fixture_only": True,
    }


def _quality(blockers: list[str], clear: float, blocked: float) -> float:
    if not blockers:
        return clear
    return max(0.0, blocked - 0.05 * (len(blockers) - 1))


def _first_present(record: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = record.get(key)
        if value is not None and value != "":
            return value
    return None


def _first_text(record: dict[str, Any], keys: list[str]) -> str | None:
    value = _first_present(record, keys)
    if value is None:
        return None
    return str(value)


def _required_text(value: Any, default: str) -> str:
    if value is None or str(value).strip() == "":
        return default
    return str(value)


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _range_from_text(text: str) -> tuple[float, float] | None:
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:-|to)\s*([0-9]+(?:\.[0-9]+)?)", text)
    if not match:
        return None
    return float(match.group(1)), float(match.group(2))


def _first_number_after(text: str, prefixes: list[str]) -> float | None:
    for prefix in prefixes:
        match = re.search(rf"{re.escape(prefix)}\s+\$?([0-9]+(?:\.[0-9]+)?)\s*(k|m|b|t)?", text)
        if not match:
            continue
        value = float(match.group(1))
        suffix = match.group(2)
        if suffix == "k":
            value *= 1_000
        elif suffix == "m":
            value *= 1_000_000
        elif suffix == "b":
            value *= 1_000_000_000
        elif suffix == "t":
            value *= 1_000_000_000_000
        return value
    return None


def _slug(value: str) -> str:
    lowered = value.lower().strip()
    slugged = re.sub(r"[^a-z0-9_.-]+", "_", lowered)
    return slugged.strip("_") or "unknown"
