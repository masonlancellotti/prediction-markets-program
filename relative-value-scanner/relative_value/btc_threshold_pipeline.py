from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


REQUIRED_EXACT_KEYS = (
    "asset",
    "source_index",
    "date_window",
    "comparator",
    "threshold",
    "units",
    "side",
)
OPTIONAL_MATCH_KEYS = ("settlement_time",)
MISSING_BLOCKER_BY_KEY = {
    "source_index": "missing_source_index",
    "date_window": "missing_date_window",
    "comparator": "missing_comparator",
    "threshold": "missing_threshold",
    "units": "missing_units",
    "side": "missing_side",
}
SOURCE_PATTERNS = (
    ("coinbase_btc_usd", ("coinbase btc/usd", "coinbase btc-usd", "coinbase", "coinbase exchange")),
    ("binance_btc_usdt", ("binance btc/usdt", "binance btc-usdt", "binance")),
    ("kraken_btc_usd", ("kraken btc/usd", "kraken btc-usd", "kraken")),
    ("coindesk_xbx", ("coindesk xbx", "coindesk bitcoin price index", "coindesk")),
    ("coinmarketcap_btc_usd", ("coinmarketcap", "coin market cap")),
    ("cf_benchmarks_brti", ("cf benchmarks' bitcoin real-time index", "cf benchmarks bitcoin real-time index", "bitcoin real-time index", "brti")),
    ("cme_cf_brr", ("cme cf bitcoin reference rate", "cf bitcoin reference rate", "cme cf brr")),
)
DATE_WORD_RE = re.compile(
    r"\b("
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
    r"sep(?:tember)?|sept|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
    r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,)?\s+(\d{4})\b",
    re.IGNORECASE,
)
ISO_DATE_RE = re.compile(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b")
THRESHOLD_RE = re.compile(r"(?:\$|usd\s*)?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(k|m)?\b", re.IGNORECASE)
NOISY_BITCOIN_TERMS = (
    "microstrategy",
    "strategy",
    "mstr",
    "etf",
    "blackrock",
    "miners",
    "mining",
    "el salvador",
    "trump",
    "election",
    "senate",
    "congress",
    "gta",
    "treasury",
    "reserve",
)
PRICE_CONTEXT_TERMS = (
    "price",
    "hit",
    "above",
    "below",
    "under",
    "over",
    "range",
    "or above",
    "or below",
    "real-time index",
    "brti",
)


def build_btc_threshold_readiness_report(
    *,
    polymarket_snapshot: dict[str, Any] | None,
    kalshi_snapshot: dict[str, Any] | None,
    max_examples: int = 5,
) -> dict[str, Any]:
    polymarket_contracts = parse_btc_threshold_snapshot(polymarket_snapshot, "polymarket")
    kalshi_contracts = parse_btc_threshold_snapshot(kalshi_snapshot, "kalshi")
    contracts = polymarket_contracts + kalshi_contracts
    relevant_contracts = [contract for contract in contracts if contract["btc_inventory_classification"] == "btc_price_threshold"]
    exact_groups = _exact_key_groups(relevant_contracts)
    cross_venue_groups = [group for group in exact_groups if len(group["venues"]) > 1]
    exact_matches = _exact_key_matches(polymarket_contracts, kalshi_contracts)
    ladder_pairs = _threshold_ladder_pairs(polymarket_contracts, kalshi_contracts)
    summary = _summary(contracts, relevant_contracts, exact_groups, cross_venue_groups, exact_matches, ladder_pairs)
    return {
        "schema_version": 1,
        "source": "btc_exact_threshold_saved_snapshot_diagnostic_v1",
        "required_exact_keys": list(REQUIRED_EXACT_KEYS),
        "optional_exact_keys": list(OPTIONAL_MATCH_KEYS),
        "summary": summary,
        "contracts": contracts,
        "exact_key_matches": exact_matches[:max_examples],
        "top_exact_key_groups": cross_venue_groups[:max_examples],
        "threshold_ladder_examples": ladder_pairs[:max_examples],
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


def parse_btc_threshold_snapshot(payload: dict[str, Any] | None, venue: str) -> list[dict[str, Any]]:
    rows = payload.get("normalized_markets") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    contracts = []
    for row in rows:
        if isinstance(row, dict):
            contract = parse_btc_threshold_market(row, venue)
            if contract is not None:
                contracts.append(contract)
    return contracts


def parse_btc_threshold_market(row: dict[str, Any], venue: str) -> dict[str, Any] | None:
    text = _market_text(row)
    lowered = text.lower()
    if "bitcoin" not in lowered and not re.search(r"\bbtc\b", lowered):
        return None
    inventory_classification = _btc_inventory_classification(lowered)
    is_relevant = inventory_classification == "btc_price_threshold"
    typed_keys = {
        "asset": "BTC",
        "source_index": _parse_source_index(row, lowered),
        "date_window": _parse_date_window(row, lowered),
        "settlement_time": _parse_settlement_time(row),
        "comparator": _parse_comparator(lowered),
        "threshold": _parse_threshold(row, lowered),
        "units": _parse_units(lowered),
        "side": _parse_side(row, lowered),
        "venue": venue,
    }
    blockers = [] if is_relevant else [inventory_classification]
    if is_relevant:
        blockers.extend(_typed_key_blockers(typed_keys))
    if _broad_text_only(lowered):
        blockers.append("broad_text_overlap_not_exact_pipeline")
    if is_relevant and not _looks_like_threshold_formula(typed_keys):
        blockers.append("not_typed_btc_threshold_formula")
    if _is_range_bucket(row, lowered):
        blockers.append("range_bucket_not_exact_threshold")
    classification = "READY_FOR_BOARD" if is_relevant and not blockers else "NOT_EXACT_PIPELINE"
    return {
        "venue": venue,
        "market_id": _first_string(row, "market_id", "id", "condition_id", "slug"),
        "ticker": _first_string(row, "ticker", "series_ticker", "event_ticker"),
        "slug": _first_string(row, "slug", "market_slug", "event_slug"),
        "title": _first_string(row, "question", "title", "market_title", "event_title", "name") or "",
        "typed_keys": typed_keys,
        "parser_confidence": _parser_confidence(typed_keys, blockers, is_relevant),
        "btc_inventory_classification": inventory_classification,
        "blockers": blockers,
        "classification": classification,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "paper_candidate_emitted": False,
    }


def exact_scope_from_btc_report(base_scope: dict[str, Any], btc_report: dict[str, Any]) -> dict[str, Any]:
    summary = btc_report["summary"]
    scope = dict(base_scope)
    has_exact = int(summary.get("exact_key_match_count") or 0) > 0
    scope.update(
        {
            "status": "MANUAL_REVIEW" if has_exact else "NOT_EXACT_PIPELINE",
            "date_or_deadline": "TYPED_FROM_SAVED_SNAPSHOTS" if summary.get("typed_btc_formula_count") else "UNRESOLVED_FROM_INVENTORY",
            "threshold_or_numeric_condition": "TYPED_FROM_SAVED_SNAPSHOTS" if summary.get("typed_btc_formula_count") else "UNRESOLVED_FROM_INVENTORY",
            "required_exact_keys_present": has_exact,
            "pipeline_classification": "READY_FOR_BOARD" if has_exact else "NOT_EXACT_PIPELINE",
            "btc_exact_threshold_counts": summary,
            "btc_exact_threshold_diagnostic": {
                "source": btc_report["source"],
                "top_blockers": btc_report["top_blockers"],
                "exact_key_match_examples": btc_report["exact_key_matches"],
                "top_exact_key_groups": btc_report["top_exact_key_groups"],
                "threshold_ladder_examples": btc_report["threshold_ladder_examples"],
            },
            "title_similarity_settlement_equivalence": False,
            "paper_candidate_emitted": False,
            "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        }
    )
    if has_exact:
        scope["unresolved_ambiguity"] = [
            "Exact BTC key matches are diagnostic only and require manual board review before any same-payoff evidence attachment.",
            "No BTC threshold row can emit PAPER_CANDIDATE from this readiness diagnostic.",
        ]
    return scope


def _summary(
    contracts: list[dict[str, Any]],
    relevant_contracts: list[dict[str, Any]],
    exact_groups: list[dict[str, Any]],
    cross_venue_groups: list[dict[str, Any]],
    exact_matches: list[dict[str, Any]],
    ladder_pairs: list[dict[str, Any]],
) -> dict[str, int]:
    return {
        "btc_inventory_count": len(contracts),
        "btc_relevant_threshold_count": len(relevant_contracts),
        "typed_btc_formula_count": sum(1 for contract in relevant_contracts if _looks_like_threshold_formula(contract["typed_keys"])),
        "exact_key_group_count": len(exact_groups),
        "exact_cross_venue_key_group_count": len(cross_venue_groups),
        "exact_key_match_count": len(exact_matches),
        "ambiguous_count": sum(1 for contract in contracts if "ambiguous_contract_terms" in contract["blockers"]),
        "missing_source_count": sum(1 for contract in relevant_contracts if "missing_source_index" in contract["blockers"]),
        "missing_date_count": sum(1 for contract in relevant_contracts if "missing_date_window" in contract["blockers"]),
        "threshold_ladder_count": len(ladder_pairs),
        "noisy_inventory_count": sum(1 for contract in contracts if contract["btc_inventory_classification"] != "btc_price_threshold"),
        "not_exact_pipeline_count": sum(1 for contract in contracts if contract["classification"] == "NOT_EXACT_PIPELINE") + len(ladder_pairs),
        "paper_candidate_count": 0,
    }


def _exact_key_groups(contracts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for contract in contracts:
        keys = contract["typed_keys"]
        if contract["classification"] != "READY_FOR_BOARD":
            continue
        group_key = tuple(keys.get(key) for key in REQUIRED_EXACT_KEYS)
        groups.setdefault(group_key, []).append(contract)
    result = []
    for group_key, rows in groups.items():
        keyed = dict(zip(REQUIRED_EXACT_KEYS, group_key))
        result.append(
            {
                "classification": "READY_FOR_BOARD" if len({row["venue"] for row in rows}) > 1 else "MANUAL_REVIEW",
                "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
                "paper_candidate_emitted": False,
                "typed_keys": keyed,
                "venues": sorted({row["venue"] for row in rows}),
                "market_count": len(rows),
                "markets": [
                    {
                        "venue": row["venue"],
                        "market_id": row.get("market_id"),
                        "ticker": row.get("ticker"),
                        "slug": row.get("slug"),
                        "title": row.get("title"),
                    }
                    for row in rows[:5]
                ],
            }
        )
    return sorted(result, key=lambda item: (-len(item["venues"]), -item["market_count"], str(item["typed_keys"])))


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


def _threshold_ladder_pairs(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pairs = []
    for poly in left:
        for kalshi in right:
            poly_keys = poly["typed_keys"]
            kalshi_keys = kalshi["typed_keys"]
            if (
                poly["btc_inventory_classification"] == "btc_price_threshold"
                and kalshi["btc_inventory_classification"] == "btc_price_threshold"
                and
                poly_keys.get("asset") == kalshi_keys.get("asset") == "BTC"
                and poly_keys.get("source_index")
                and poly_keys.get("source_index") == kalshi_keys.get("source_index")
                and poly_keys.get("date_window")
                and poly_keys.get("date_window") == kalshi_keys.get("date_window")
                and poly_keys.get("comparator")
                and poly_keys.get("comparator") == kalshi_keys.get("comparator")
                and poly_keys.get("units")
                and poly_keys.get("units") == kalshi_keys.get("units")
                and poly_keys.get("threshold")
                and kalshi_keys.get("threshold")
                and poly_keys.get("threshold") != kalshi_keys.get("threshold")
            ):
                pairs.append(_pair_example(poly, kalshi, "NOT_EXACT_PIPELINE", ["threshold_ladder_not_exact_payoff"]))
    return pairs


def _pair_blockers(poly_keys: dict[str, Any], kalshi_keys: dict[str, Any]) -> list[str]:
    blockers = []
    for key in REQUIRED_EXACT_KEYS:
        if poly_keys.get(key) != kalshi_keys.get(key):
            blockers.append(f"{key}_mismatch")
    if poly_keys.get("settlement_time") and kalshi_keys.get("settlement_time") and poly_keys["settlement_time"] != kalshi_keys["settlement_time"]:
        blockers.append("settlement_time_mismatch")
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
    for key in REQUIRED_EXACT_KEYS:
        if not typed_keys.get(key):
            blockers.append(MISSING_BLOCKER_BY_KEY.get(key, f"missing_{key}"))
    return blockers


def _looks_like_threshold_formula(typed_keys: dict[str, Any]) -> bool:
    return bool(typed_keys.get("asset") == "BTC" and typed_keys.get("comparator") and typed_keys.get("threshold"))


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
        "yes_sub_title",
        "no_sub_title",
        "subtitle",
    ):
        value = row.get(key)
        if isinstance(value, str):
            parts.append(value)
    raw = row.get("raw")
    if isinstance(raw, dict):
        for key in (
            "title",
            "question",
            "subtitle",
            "yes_sub_title",
            "no_sub_title",
            "rules",
            "rules_primary",
            "rules_secondary",
            "description",
            "resolution_source",
            "settlement_source",
            "settlement_sources",
            "ticker",
            "series_ticker",
            "event_ticker",
        ):
            value = raw.get(key)
            if isinstance(value, str):
                parts.append(value)
    return " ".join(parts)


def _parse_source_index(row: dict[str, Any], lowered_text: str) -> str | None:
    for canonical, patterns in SOURCE_PATTERNS:
        if any(pattern in lowered_text for pattern in patterns):
            return canonical
    return None


def _parse_date_window(row: dict[str, Any], lowered_text: str) -> str | None:
    for key in ("end_date", "close_time", "close_date", "expiration_time", "settlement_time", "deadline"):
        parsed = _parse_datetime_string(row.get(key))
        if parsed:
            return parsed.date().isoformat()
    raw = row.get("raw")
    if isinstance(raw, dict):
        for key in ("end_date", "close_time", "close_date", "expiration_time", "settlement_time", "deadline"):
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


def _parse_settlement_time(row: dict[str, Any]) -> str | None:
    for key in ("settlement_time", "end_date", "close_time", "close_date", "expiration_time", "deadline"):
        parsed = _parse_datetime_string(row.get(key))
        if parsed:
            return parsed.isoformat()
    raw = row.get("raw")
    if isinstance(raw, dict):
        for key in ("settlement_time", "end_date", "close_time", "close_date", "expiration_time", "deadline"):
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


def _parse_comparator(lowered_text: str) -> str | None:
    if any(term in lowered_text for term in ("at or above", "greater than or equal", ">= ")):
        return "at_or_above"
    if any(term in lowered_text for term in ("above", "over", "greater than", "exceed", "hit", "reach")):
        return "above"
    if any(term in lowered_text for term in ("at or below", "less than or equal", "<= ")):
        return "at_or_below"
    if any(term in lowered_text for term in ("below", "under", "less than")):
        return "below"
    return None


def _parse_threshold(row: dict[str, Any], lowered_text: str) -> str | None:
    raw = row.get("raw")
    if isinstance(raw, dict):
        comparator = _parse_comparator(lowered_text)
        if comparator in {"above", "at_or_above"}:
            raw_threshold = raw.get("floor_strike") or raw.get("custom_strike")
            normalized = _normalize_decimal(raw_threshold)
            if normalized:
                return normalized
        if comparator in {"below", "at_or_below"}:
            raw_threshold = raw.get("cap_strike") or raw.get("custom_strike")
            normalized = _normalize_decimal(raw_threshold)
            if normalized:
                return normalized
    for match in THRESHOLD_RE.finditer(lowered_text):
        raw_number, suffix = match.groups()
        normalized = raw_number.replace(",", "")
        try:
            value = Decimal(normalized)
        except InvalidOperation:
            continue
        if suffix and suffix.lower() == "k":
            value *= Decimal(1000)
        elif suffix and suffix.lower() == "m":
            value *= Decimal(1000000)
        if value < Decimal(1000):
            continue
        if value == value.to_integral_value():
            return str(int(value))
        return str(value.normalize())
    return None


def _parse_units(lowered_text: str) -> str | None:
    if "$" in lowered_text or "usd" in lowered_text or "dollar" in lowered_text:
        return "USD"
    return None


def _parse_side(row: dict[str, Any], lowered_text: str) -> str | None:
    side = _first_string(row, "side", "outcome", "outcome_name")
    if side and side.strip().lower() in {"yes", "no"}:
        return side.strip().upper()
    if (
        "resolves to yes" in lowered_text
        or "resolve to yes" in lowered_text
        or "yes if" in lowered_text
        or "yes, if" in lowered_text
        or "then the market resolves to yes" in lowered_text
    ):
        return "YES"
    if (
        "resolves to no" in lowered_text
        or "resolve to no" in lowered_text
        or "no if" in lowered_text
        or "no, if" in lowered_text
        or "then the market resolves to no" in lowered_text
    ):
        return "NO"
    return None


def _broad_text_only(lowered_text: str) -> bool:
    broad_terms = ("by year-end", "year end", "above x", "date y", "bitcoin price by year")
    return any(term in lowered_text for term in broad_terms)


def _btc_inventory_classification(lowered_text: str) -> str:
    if any(term in lowered_text for term in NOISY_BITCOIN_TERMS) and not any(term in lowered_text for term in ("bitcoin price", "btc price")):
        return "noisy_bitcoin_adjacent_inventory"
    if not any(term in lowered_text for term in PRICE_CONTEXT_TERMS):
        return "generic_crypto_text_not_price_threshold"
    return "btc_price_threshold"


def _is_range_bucket(row: dict[str, Any], lowered_text: str) -> bool:
    raw = row.get("raw")
    if isinstance(raw, dict) and raw.get("floor_strike") is not None and raw.get("cap_strike") is not None:
        return True
    return False


def _parser_confidence(typed_keys: dict[str, Any], blockers: list[str], is_relevant: bool) -> str:
    if not is_relevant:
        return "excluded_noisy"
    present = sum(1 for key in REQUIRED_EXACT_KEYS if typed_keys.get(key))
    if not blockers and present == len(REQUIRED_EXACT_KEYS):
        return "high"
    if typed_keys.get("comparator") and typed_keys.get("threshold") and typed_keys.get("date_window"):
        return "medium"
    return "low"


def _normalize_decimal(value: Any) -> str | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value).replace(",", ""))
    except InvalidOperation:
        return None
    if parsed == parsed.to_integral_value():
        return str(int(parsed))
    return str(parsed.normalize())


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
