from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


REQUIRED_EXACT_KEYS = (
    "meeting_date",
    "lower_bound",
    "upper_bound",
    "units",
    "settlement_basis",
    "side",
    "market_family",
)
OPTIONAL_MATCH_KEYS = ("settlement_timing",)
MISSING_BLOCKER_BY_KEY = {
    "meeting_date": "missing_meeting_date",
    "lower_bound": "missing_range",
    "upper_bound": "missing_range",
    "units": "missing_units",
    "settlement_basis": "missing_settlement_basis",
    "side": "missing_side",
    "market_family": "missing_market_family",
}
DATE_WORD_RE = re.compile(
    r"\b("
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
    r"sep(?:tember)?|sept|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
    r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,)?\s+(\d{4})\b",
    re.IGNORECASE,
)
ISO_DATE_RE = re.compile(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b")
RANGE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:%|percent|pct)?\s*(?:-|to|through|and)\s*(\d+(?:\.\d+)?)\s*(%|percent|pct|bps|bp|basis points)?",
    re.IGNORECASE,
)


def build_fed_fomc_range_readiness_report(
    *,
    polymarket_snapshot: dict[str, Any] | None,
    kalshi_snapshot: dict[str, Any] | None,
    max_examples: int = 5,
) -> dict[str, Any]:
    polymarket_contracts = parse_fed_fomc_snapshot(polymarket_snapshot, "polymarket")
    kalshi_contracts = parse_fed_fomc_snapshot(kalshi_snapshot, "kalshi")
    contracts = polymarket_contracts + kalshi_contracts
    exact_matches = _exact_key_matches(polymarket_contracts, kalshi_contracts)
    overlaps = _overlapping_range_pairs(polymarket_contracts, kalshi_contracts)
    different_meetings = _different_meeting_pairs(polymarket_contracts, kalshi_contracts)
    summary = _summary(contracts, exact_matches, overlaps, different_meetings)
    return {
        "schema_version": 1,
        "source": "fed_fomc_exact_range_saved_snapshot_diagnostic_v1",
        "required_exact_keys": list(REQUIRED_EXACT_KEYS),
        "optional_exact_keys": list(OPTIONAL_MATCH_KEYS),
        "summary": summary,
        "contracts": contracts,
        "exact_meeting_range_matches": exact_matches[:max_examples],
        "overlapping_range_examples": overlaps[:max_examples],
        "different_meeting_examples": different_meetings[:max_examples],
        "top_blockers": _top_blockers(contracts),
        "safety": {
            "saved_files_only": True,
            "live_fetch_attempted": False,
            "title_similarity_used_as_exactness": False,
            "paper_candidate_emitted": False,
            "paper_candidate_count": 0,
            "affects_evaluator_gates": False,
            "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        },
    }


def parse_fed_fomc_snapshot(payload: dict[str, Any] | None, venue: str) -> list[dict[str, Any]]:
    rows = payload.get("normalized_markets") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    contracts = []
    for row in rows:
        if isinstance(row, dict):
            contract = parse_fed_fomc_market(row, venue)
            if contract is not None:
                contracts.append(contract)
    return contracts


def parse_fed_fomc_market(row: dict[str, Any], venue: str) -> dict[str, Any] | None:
    text = _market_text(row)
    lowered = text.lower()
    if not _looks_like_fed_text(lowered):
        return None
    lower, upper, units = _parse_range(lowered)
    typed_keys = {
        "meeting_date": _parse_meeting_date(row, lowered),
        "lower_bound": lower,
        "upper_bound": upper,
        "units": units,
        "settlement_basis": _parse_settlement_basis(row, lowered),
        "side": _parse_side(row, lowered),
        "market_family": _parse_market_family(lowered),
        "settlement_timing": _parse_settlement_timing(row),
    }
    blockers = _typed_key_blockers(typed_keys)
    if _broad_text_only(lowered):
        blockers.append("broad_text_overlap_not_exact_pipeline")
    if not _looks_like_range_formula(typed_keys):
        blockers.append("not_typed_fed_range_formula")
    classification = "READY_FOR_BOARD" if not blockers else "NOT_EXACT_PIPELINE"
    return {
        "venue": venue,
        "market_id": _first_string(row, "market_id", "id", "condition_id", "slug"),
        "ticker": _first_string(row, "ticker", "series_ticker", "event_ticker"),
        "title": _first_string(row, "question", "title", "market_title", "event_title", "name") or "",
        "typed_keys": typed_keys,
        "blockers": blockers,
        "classification": classification,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "paper_candidate_emitted": False,
    }


def exact_scope_from_fed_report(base_scope: dict[str, Any], fed_report: dict[str, Any]) -> dict[str, Any]:
    summary = fed_report["summary"]
    scope = dict(base_scope)
    has_exact = int(summary.get("exact_meeting_range_match_count") or 0) > 0
    scope.update(
        {
            "status": "MANUAL_REVIEW" if has_exact else "NOT_EXACT_PIPELINE",
            "date_or_deadline": "TYPED_FROM_SAVED_SNAPSHOTS" if summary.get("typed_fed_formula_count") else "UNRESOLVED_FROM_INVENTORY",
            "fed_meeting_or_fomc_event": "TYPED_FROM_SAVED_SNAPSHOTS" if summary.get("typed_fed_formula_count") else "UNRESOLVED_FROM_INVENTORY",
            "threshold_or_numeric_condition": "TYPED_FROM_SAVED_SNAPSHOTS" if summary.get("typed_fed_formula_count") else "UNRESOLVED_FROM_INVENTORY",
            "required_exact_keys_present": has_exact,
            "pipeline_classification": "READY_FOR_BOARD" if has_exact else "NOT_EXACT_PIPELINE",
            "fed_fomc_exact_range_counts": summary,
            "fed_fomc_exact_range_diagnostic": {
                "source": fed_report["source"],
                "top_blockers": fed_report["top_blockers"],
                "exact_meeting_range_examples": fed_report["exact_meeting_range_matches"],
                "overlapping_range_examples": fed_report["overlapping_range_examples"],
                "different_meeting_examples": fed_report["different_meeting_examples"],
            },
            "title_similarity_settlement_equivalence": False,
            "paper_candidate_emitted": False,
            "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        }
    )
    if has_exact:
        scope["unresolved_ambiguity"] = [
            "Exact Fed/FOMC meeting-range matches are diagnostic only and require manual board review before any same-payoff evidence attachment.",
            "No Fed/FOMC row can emit PAPER_CANDIDATE from this readiness diagnostic.",
        ]
    return scope


def _summary(
    contracts: list[dict[str, Any]],
    exact_matches: list[dict[str, Any]],
    overlaps: list[dict[str, Any]],
    different_meetings: list[dict[str, Any]],
) -> dict[str, int]:
    return {
        "fed_inventory_count": len(contracts),
        "typed_fed_formula_count": sum(1 for contract in contracts if _looks_like_range_formula(contract["typed_keys"])),
        "exact_meeting_range_match_count": len(exact_matches),
        "overlapping_range_count": len(overlaps),
        "different_meeting_count": len(different_meetings),
        "missing_meeting_count": sum(1 for contract in contracts if "missing_meeting_date" in contract["blockers"]),
        "missing_range_count": sum(1 for contract in contracts if "missing_range" in contract["blockers"]),
        "not_exact_pipeline_count": sum(1 for contract in contracts if contract["classification"] == "NOT_EXACT_PIPELINE") + len(overlaps) + len(different_meetings),
        "paper_candidate_count": 0,
    }


def _exact_key_matches(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches = []
    for poly in left:
        if poly["classification"] != "READY_FOR_BOARD":
            continue
        for kalshi in right:
            if kalshi["classification"] != "READY_FOR_BOARD":
                continue
            blockers = _pair_blockers(poly["typed_keys"], kalshi["typed_keys"])
            if blockers:
                continue
            matches.append(_pair_example(poly, kalshi, "READY_FOR_BOARD", []))
    return matches


def _overlapping_range_pairs(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pairs = []
    for poly in left:
        for kalshi in right:
            poly_keys = poly["typed_keys"]
            kalshi_keys = kalshi["typed_keys"]
            if (
                poly_keys.get("meeting_date")
                and poly_keys.get("meeting_date") == kalshi_keys.get("meeting_date")
                and poly_keys.get("market_family")
                and poly_keys.get("market_family") == kalshi_keys.get("market_family")
                and poly_keys.get("units")
                and poly_keys.get("units") == kalshi_keys.get("units")
                and _ranges_overlap(poly_keys, kalshi_keys)
                and (poly_keys.get("lower_bound"), poly_keys.get("upper_bound")) != (kalshi_keys.get("lower_bound"), kalshi_keys.get("upper_bound"))
            ):
                pairs.append(_pair_example(poly, kalshi, "NOT_EXACT_PIPELINE", ["overlap_not_identical_range"]))
    return pairs


def _different_meeting_pairs(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pairs = []
    for poly in left:
        for kalshi in right:
            poly_keys = poly["typed_keys"]
            kalshi_keys = kalshi["typed_keys"]
            if (
                poly_keys.get("meeting_date")
                and kalshi_keys.get("meeting_date")
                and poly_keys.get("meeting_date") != kalshi_keys.get("meeting_date")
                and poly_keys.get("lower_bound") == kalshi_keys.get("lower_bound")
                and poly_keys.get("upper_bound") == kalshi_keys.get("upper_bound")
            ):
                pairs.append(_pair_example(poly, kalshi, "NOT_EXACT_PIPELINE", ["different_meeting_date"]))
    return pairs


def _pair_blockers(poly_keys: dict[str, Any], kalshi_keys: dict[str, Any]) -> list[str]:
    blockers = []
    for key in REQUIRED_EXACT_KEYS:
        if poly_keys.get(key) != kalshi_keys.get(key):
            blockers.append(f"{key}_mismatch")
    if poly_keys.get("settlement_timing") and kalshi_keys.get("settlement_timing") and poly_keys["settlement_timing"] != kalshi_keys["settlement_timing"]:
        blockers.append("settlement_timing_mismatch")
    return blockers


def _pair_example(poly: dict[str, Any], kalshi: dict[str, Any], classification: str, blockers: list[str]) -> dict[str, Any]:
    return {
        "classification": classification,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "paper_candidate_emitted": False,
        "polymarket": {
            "market_id": poly.get("market_id"),
            "title": poly.get("title"),
            "typed_keys": poly.get("typed_keys"),
        },
        "kalshi": {
            "ticker": kalshi.get("ticker"),
            "title": kalshi.get("title"),
            "typed_keys": kalshi.get("typed_keys"),
        },
        "blockers": blockers,
    }


def _typed_key_blockers(typed_keys: dict[str, Any]) -> list[str]:
    blockers = []
    seen = set()
    for key in REQUIRED_EXACT_KEYS:
        if not typed_keys.get(key):
            blocker = MISSING_BLOCKER_BY_KEY.get(key, f"missing_{key}")
            if blocker not in seen:
                blockers.append(blocker)
                seen.add(blocker)
    return blockers


def _looks_like_fed_text(lowered_text: str) -> bool:
    terms = ("fomc", "fed", "federal reserve", "target rate", "fed funds", "federal funds")
    return any(term in lowered_text for term in terms)


def _looks_like_range_formula(typed_keys: dict[str, Any]) -> bool:
    return bool(
        typed_keys.get("meeting_date")
        and typed_keys.get("lower_bound")
        and typed_keys.get("upper_bound")
    )


def _ranges_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_low = _decimal(left.get("lower_bound"))
    left_high = _decimal(left.get("upper_bound"))
    right_low = _decimal(right.get("lower_bound"))
    right_high = _decimal(right.get("upper_bound"))
    if None in {left_low, left_high, right_low, right_high}:
        return False
    return left_low < right_high and right_low < left_high


def _top_blockers(contracts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counter = Counter(blocker for contract in contracts for blocker in contract["blockers"])
    return [{"blocker": blocker, "count": count} for blocker, count in counter.most_common(5)]


def _market_text(row: dict[str, Any]) -> str:
    parts = []
    for key in (
        "question",
        "title",
        "market_title",
        "event_title",
        "subtitle",
        "description",
        "rules",
        "resolution_source",
        "settlement_source",
        "ticker",
    ):
        value = row.get(key)
        if isinstance(value, str):
            parts.append(value)
    raw = row.get("raw")
    if isinstance(raw, dict):
        for key in ("title", "question", "subtitle", "rules", "description", "resolution_source", "settlement_source"):
            value = raw.get(key)
            if isinstance(value, str):
                parts.append(value)
    return " ".join(parts)


def _parse_meeting_date(row: dict[str, Any], lowered_text: str) -> str | None:
    for key in ("meeting_date", "fomc_meeting_date", "decision_date", "event_date"):
        parsed = _parse_datetime_string(row.get(key))
        if parsed:
            return parsed.date().isoformat()
    raw = row.get("raw")
    if isinstance(raw, dict):
        for key in ("meeting_date", "fomc_meeting_date", "decision_date", "event_date"):
            parsed = _parse_datetime_string(raw.get(key))
            if parsed:
                return parsed.date().isoformat()
    match = ISO_DATE_RE.search(lowered_text)
    if match:
        year, month, day = (int(value) for value in match.groups())
        try:
            return datetime(year, month, day, tzinfo=timezone.utc).date().isoformat()
        except ValueError:
            return None
    word = DATE_WORD_RE.search(lowered_text)
    if word:
        month = _month_number(word.group(1))
        if month:
            try:
                return datetime(int(word.group(3)), month, int(word.group(2)), tzinfo=timezone.utc).date().isoformat()
            except ValueError:
                return None
    return None


def _parse_range(lowered_text: str) -> tuple[str | None, str | None, str | None]:
    for match in RANGE_RE.finditer(lowered_text):
        low = _normalize_decimal(match.group(1))
        high = _normalize_decimal(match.group(2))
        units = _parse_units(match.group(3) or lowered_text)
        if low and high and _decimal(low) is not None and _decimal(high) is not None and _decimal(low) < _decimal(high):
            return low, high, units
    return None, None, _parse_units(lowered_text)


def _parse_units(text: str) -> str | None:
    lowered = text.lower()
    if any(term in lowered for term in ("bps", "bp", "basis point")):
        return "BPS"
    if any(term in lowered for term in ("%", "percent", "pct", "rate", "range")):
        return "PERCENT"
    return None


def _parse_settlement_basis(row: dict[str, Any], lowered_text: str) -> str | None:
    if "federal reserve" in lowered_text or "fomc" in lowered_text or "fed funds" in lowered_text or "target rate" in lowered_text:
        return "federal_reserve_fomc_target_range"
    return None


def _parse_side(row: dict[str, Any], lowered_text: str) -> str | None:
    side = _first_string(row, "side", "outcome", "outcome_name")
    if side and side.strip().lower() in {"yes", "no"}:
        return side.strip().upper()
    if " will " in lowered_text or lowered_text.startswith(("will ", "can ")):
        return "YES"
    return None


def _parse_market_family(lowered_text: str) -> str | None:
    if "target rate" in lowered_text or "target range" in lowered_text or "fed funds" in lowered_text or "federal funds" in lowered_text:
        return "fed_funds_target_range"
    if "fomc" in lowered_text or "fed" in lowered_text:
        return "fed_fomc_decision"
    return None


def _parse_settlement_timing(row: dict[str, Any]) -> str | None:
    for key in ("settlement_time", "close_time", "decision_time", "expiration_time", "deadline"):
        parsed = _parse_datetime_string(row.get(key))
        if parsed:
            return parsed.isoformat()
    raw = row.get("raw")
    if isinstance(raw, dict):
        for key in ("settlement_time", "close_time", "decision_time", "expiration_time", "deadline"):
            parsed = _parse_datetime_string(raw.get(key))
            if parsed:
                return parsed.isoformat()
    return None


def _parse_datetime_string(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _broad_text_only(lowered_text: str) -> bool:
    broad_terms = ("fed decision", "next fomc", "rates after meeting", "interest rates")
    has_range = RANGE_RE.search(lowered_text) is not None
    return any(term in lowered_text for term in broad_terms) and not has_range


def _first_string(row: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raw = row.get("raw")
    if isinstance(raw, dict):
        for key in keys:
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _normalize_decimal(value: str) -> str | None:
    parsed = _decimal(value)
    if parsed is None:
        return None
    if parsed == parsed.to_integral_value():
        return str(int(parsed))
    return str(parsed.normalize())


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _month_number(value: str) -> int | None:
    lookup = {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "sept": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
    return lookup.get(value[:4].lower()) or lookup.get(value[:3].lower())

