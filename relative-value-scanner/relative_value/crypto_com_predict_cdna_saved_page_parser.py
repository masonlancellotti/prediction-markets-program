from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
REPORT_SOURCE = "crypto_com_predict_cdna_research_snapshot_v1"
VENUE = "crypto_com_predict_cdna"
PERMISSION_RESEARCH_ONLY = "research_only"
CDNA_UBTC_SOURCE_INDEX = "CDNA U-BTC midpoint (Lukka/ICE/Blockstream)"
CDNA_UETH_SOURCE_INDEX = "CDNA U-ETH midpoint (Lukka/ICE/Blockstream)"
CDNA_RULE_1469_BTC_SOURCE_INDEX = "CDNA Rule 14.69 / Nadex BTC Index"
CDNA_RULE_1472_ETH_SOURCE_INDEX = "CDNA Rule 14.72 / CDNA ETH source"

SHAPE_YEAR_END_RANGE_BUCKET = "YEAR_END_RANGE_BUCKET"
SHAPE_RANGE_BUCKET = "RANGE_BUCKET"
SHAPE_DEADLINE_HIT_BY_DATE = "DEADLINE_HIT_BY_DATE"
SHAPE_EARLIEST_TIMEFRAME_THRESHOLD_TOUCH = "EARLIEST_TIMEFRAME_THRESHOLD_TOUCH"
SHAPE_ALL_TIME_HIGH_BY_DATE = "ALL_TIME_HIGH_BY_DATE"
SHAPE_POINT_IN_TIME_THRESHOLD = "POINT_IN_TIME_THRESHOLD"
SHAPE_AMBIGUOUS = "AMBIGUOUS"
MATCH_ONE_SIDED_FV_ONLY = "ONE_SIDED_FV_ONLY"
MATCH_EARLIEST_TIMEFRAME_FV_ONLY = "EARLIEST_TIMEFRAME_FV_ONLY"
MATCH_FV_ONLY = "FV_ONLY"
MATCH_BASIS_RISK_POSSIBLE = "BASIS_RISK_POSSIBLE"

_DATE_PATTERN = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b",
    re.IGNORECASE,
)
_TIME_PATTERN = re.compile(
    r"\b(\d{1,2}(?::\d{2})?\s*(?:AM|PM|a\.m\.|p\.m\.)?)\s*"
    r"(Eastern\s+Time|EST|EDT|CST|CDT|MST|MDT|PST|PDT|UTC|GMT|ET|CT|MT|PT)\b",
    re.IGNORECASE,
)
_OPERATOR_PATTERN = re.compile(
    r"(?:>=|<=|>|<|\babove\b|\bbelow\b|\bover\b|\bunder\b|\bgreater\s+than\b|\bless\s+than\b|\bat\s+least\b|\bat\s+most\b)",
    re.IGNORECASE,
)


def build_crypto_com_predict_cdna_research_snapshot(
    *,
    fixture_dir: Path | None = None,
    fixture_dirs: list[Path] | tuple[Path, ...] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    rows: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    dirs = list(fixture_dirs or ([fixture_dir] if fixture_dir is not None else []))

    for current_dir in dirs:
        if not current_dir.exists():
            warnings.append(
                {
                    "source_file": str(current_dir),
                    "reason_code": "fixture_dir_missing",
                    "blocker": "saved_fixture_dir_missing",
                }
            )
        elif not current_dir.is_dir():
            warnings.append(
                {
                    "source_file": str(current_dir),
                    "reason_code": "fixture_path_not_directory",
                    "blocker": "saved_fixture_path_not_directory",
                }
            )
        else:
            for path in sorted(current_dir.rglob("*")):
                if not path.is_file() or path.suffix.lower() not in {".json", ".html", ".htm"}:
                    continue
                parsed_rows, parsed_warnings = _parse_fixture(path, generated_at=generated)
                rows.extend(parsed_rows)
                warnings.extend(parsed_warnings)

    return {
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "fixture_dir": str(dirs[0]) if len(dirs) == 1 else None,
        "fixture_dirs": [str(path) for path in dirs],
        "summary": _summary(rows, warnings),
        "rows": rows,
        "warnings": warnings,
        "safety": {
            "saved_files_only": True,
            "live_fetch_attempted": False,
            "execution_or_order_logic_added": False,
            "account_or_auth_logic_added": False,
            "paper_candidate_emitted": False,
            "affects_evaluator_gates": False,
            "can_create_candidate_pair": False,
            "can_create_paper_candidate": False,
        },
    }


def write_crypto_com_predict_cdna_research_snapshot_file(
    *,
    fixture_dir: Path | None = None,
    fixture_dirs: list[Path] | tuple[Path, ...] | None = None,
    json_output: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_crypto_com_predict_cdna_research_snapshot(
        fixture_dir=fixture_dir,
        fixture_dirs=fixture_dirs,
        generated_at=generated_at,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def _parse_fixture(path: Path, *, generated_at: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        if path.suffix.lower() == ".json":
            return _parse_json_fixture(path, generated_at=generated_at), []
        return _parse_html_fixture(path, generated_at=generated_at), []
    except json.JSONDecodeError:
        return [], [{"source_file": str(path), "reason_code": "invalid_json", "blocker": "saved_fixture_invalid_json"}]
    except OSError as exc:
        return [], [{"source_file": str(path), "reason_code": "fixture_read_error", "blocker": f"fixture_read_error:{type(exc).__name__}"}]


def _parse_json_fixture(path: Path, *, generated_at: datetime) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        rows: list[dict[str, Any]] = []
        row_index = 0
        for event_index, event in enumerate(payload):
            if not isinstance(event, dict):
                continue
            for record in _record_candidates_from_combined_event(event, event_index=event_index):
                rows.append(
                    _normalize_record(
                        record,
                        path=path,
                        row_index=row_index,
                        generated_at=generated_at,
                        fixture_metadata=event,
                    )
                )
                row_index += 1
        return rows
    if not isinstance(payload, dict):
        return []
    return [
        _normalize_record(row, path=path, row_index=index, generated_at=generated_at, fixture_metadata=payload)
        for index, row in enumerate(_record_candidates_from_payload(payload))
    ]


def _parse_html_fixture(path: Path, *, generated_at: datetime) -> list[dict[str, Any]]:
    html = path.read_text(encoding="utf-8")
    parser = _SavedPageHTMLParser()
    parser.feed(html)
    embedded = _first_embedded_json(html)
    record: dict[str, Any] = {}
    if isinstance(embedded, dict):
        record.update(embedded)
    record.update({key: value for key, value in parser.metadata.items() if value})
    text = parser.visible_text()
    if text:
        record.setdefault("page_text", text)
    return [
        _normalize_record(row, path=path, row_index=index, generated_at=generated_at, fixture_metadata=record)
        for index, row in enumerate(_record_candidates_from_payload(record))
    ]


def _record_candidates_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    parent = {
        key: value
        for key, value in payload.items()
        if key not in {"markets", "selections", "records", "rows", "outcomes"}
    }
    records: list[dict[str, Any]] = []
    for collection_key in ("markets", "selections", "records", "rows", "outcomes"):
        collection = payload.get(collection_key)
        if not isinstance(collection, list):
            continue
        for item in collection:
            if not isinstance(item, dict):
                continue
            merged = dict(parent)
            merged.update(item)
            merged["_source_collection"] = collection_key
            records.append(merged)
    return records or [payload]


def _record_candidates_from_combined_event(event: dict[str, Any], *, event_index: int) -> list[dict[str, Any]]:
    parent = {
        key: value
        for key, value in event.items()
        if key not in {"thresholds", "markets", "selections", "records", "rows", "outcomes"}
    }
    collection = event.get("thresholds")
    if not isinstance(collection, list):
        return [{**parent, "raw_event_index": event_index, "raw_threshold_index": None}]
    records: list[dict[str, Any]] = []
    for threshold_index, item in enumerate(collection):
        if not isinstance(item, dict):
            continue
        merged = dict(parent)
        merged.update(item)
        merged["_source_collection"] = "thresholds"
        merged["raw_event_index"] = event_index
        merged["raw_threshold_index"] = threshold_index
        records.append(merged)
    return records


def _normalize_record(
    raw: dict[str, Any],
    *,
    path: Path,
    row_index: int,
    generated_at: datetime,
    fixture_metadata: dict[str, Any],
) -> dict[str, Any]:
    title = _first_str(
        raw.get("title"),
        raw.get("question"),
        raw.get("name"),
        raw.get("page_title"),
        raw.get("event_title"),
        fixture_metadata.get("event_title"),
    )
    rules_text = _first_str(
        raw.get("settlement_rule_text"),
        raw.get("settlement_rules_methodology_text"),
        raw.get("settlement_rules"),
        raw.get("rules_text"),
        raw.get("rules"),
        raw.get("methodology"),
        raw.get("source_methodology"),
        raw.get("methodology_text"),
        raw.get("description"),
        raw.get("resolutionSource"),
        raw.get("page_text"),
    )
    methodology_text = _first_str(
        raw.get("settlement_rules_methodology_text"),
        raw.get("methodology"),
        raw.get("source_methodology"),
        raw.get("methodology_text"),
        raw.get("settlement_rule_text"),
        raw.get("settlement_rules"),
        raw.get("rules_text"),
        raw.get("rules"),
        raw.get("description"),
        raw.get("resolutionSource"),
        raw.get("page_text"),
    )
    combined = " ".join(
        value
        for value in (
            title,
            rules_text,
            methodology_text,
            _first_str(raw.get("label"), raw.get("outcome_name"), raw.get("selection_name")),
        )
        if value
    )
    asset = _asset(raw, combined)
    threshold_value = _threshold(raw, combined)
    threshold_operator = _operator(raw, combined)
    measurement_date = _date(raw, combined)
    measurement_time, timezone_text = _time_and_timezone(raw, combined)
    price_source_index = _price_source_index(raw, rules_text or combined)
    settlement_window = _settlement_window(raw, rules_text or combined)
    market_type = _normalize_market_type(_first_str(raw.get("market_type"), raw.get("type")), combined)
    shape_class = _shape_class(market_type)
    market_shape_conservative = _market_shape_conservative(market_type, shape_class)
    matchability_class = _matchability_class(shape_class)
    measurement_window = _measurement_window(raw, shape_class)
    resolution_reference_time = _resolution_reference_time(raw)
    display_quote = _display_quote(raw)
    blockers = _blockers(
        asset=asset,
        threshold_value=threshold_value,
        threshold_operator=threshold_operator,
        measurement_date=measurement_date,
        price_source_index=price_source_index,
        settlement_window=settlement_window,
        shape_class=shape_class,
        rules_text=rules_text,
    )
    basis_risk_compatible = _basis_risk_compatible(asset=asset, price_source_index=price_source_index, shape_class=shape_class)

    return {
        "venue": VENUE,
        "permission": PERMISSION_RESEARCH_ONLY,
        "execution_allowed_in_project_now": False,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "exact_payoff_claimed": False,
        "paper_candidate_emitted": False,
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "source_platform": _first_str(raw.get("source_platform")),
        "capture_method": _first_str(raw.get("capture_method")),
        "captured_at_utc": _first_str(raw.get("captured_at_utc")),
        "source_url": _first_str(raw.get("source_url")),
        "event_id": _first_str(raw.get("event_id")),
        "platform_event_ref": _first_str(raw.get("platform_event_ref"), raw.get("event_ref"), raw.get("eventId")),
        "market_id": _first_str(raw.get("market_id")),
        "platform_market_ref": _first_str(raw.get("platform_market_ref"), raw.get("market_ref"), raw.get("marketId")),
        "title": title,
        "market_type": market_type,
        "shape_class": shape_class,
        "market_shape": shape_class,
        "market_shape_conservative": market_shape_conservative,
        "market_shape_normalized": market_shape_conservative,
        "matchability_class": matchability_class,
        "asset": asset,
        "strike": threshold_value,
        "threshold_value": threshold_value,
        "comparator": threshold_operator,
        "lower": _number_or_none(raw.get("lower")),
        "upper": _number_or_none(raw.get("upper")),
        "threshold_operator": threshold_operator,
        "selection_label": _first_str(raw.get("selection"), raw.get("label"), raw.get("outcome_name"), raw.get("selection_name")),
        "outcome_label": _first_str(raw.get("selection"), raw.get("label"), raw.get("outcome_name"), raw.get("selection_name")),
        "measurement_date": measurement_date,
        "target_date": measurement_date,
        "measurement_time": measurement_time,
        "timezone": timezone_text,
        "measurement_window": measurement_window,
        "deadline_or_expiry": measurement_window or resolution_reference_time,
        "resolution_reference_time": resolution_reference_time,
        "price_source_index": price_source_index,
        "settlement_window": settlement_window,
        "settlement_rule_text": rules_text,
        "settlement_rules_methodology_text": _first_str(raw.get("settlement_rules_methodology_text")),
        "source_methodology_text": methodology_text,
        "settlement_source": _first_str(raw.get("settlement_source"), raw.get("settlement_source_url")),
        "settlement_source_url": _settlement_source_url(raw, rules_text or ""),
        "rule_source_url": _settlement_source_url(raw, rules_text or ""),
        "basis_risk_compatible_with_kalshi": basis_risk_compatible,
        "source_exact_payoff_compatible_with_kalshi": False,
        "basis_risk_severity_hint_vs_kalshi_brti": (
            # Diagnostic-only severity hint that mirrors the standardized basis-risk hint
            # buckets so an operator can compare CDNA vs Kalshi BRTI without trusting
            # exact-payoff equivalence. The U-BTC vs BRTI methodologies are both reputable
            # 60-second-class minute aggregates, so the most-aligned case is
            # "moderate_known_different_sources_same_window"; everything else degrades.
            "moderate_known_different_sources_same_window"
            if basis_risk_compatible
            else "high_unreviewed"
        ),
        "not_exact_payoff_reason": (
            "CDNA U-BTC midpoint methodology differs from Kalshi BRTI settlement; compare only as basis-risk/fair-value review."
            if price_source_index == CDNA_UBTC_SOURCE_INDEX
            else "CDNA U-ETH methodology is not Kalshi BRTI and is not exact-payoff compatible with Kalshi BTC markets."
            if price_source_index == CDNA_UETH_SOURCE_INDEX
            else "CDNA exact settlement methodology is not reviewed for exact-payoff matching."
        ),
        "quote_display": display_quote,
        "yes_display_price": raw.get("yes_display_price"),
        "no_display_price": raw.get("no_display_price"),
        "chance_to_win_display": raw.get("chance_to_win_display"),
        "outcome": raw.get("outcome"),
        "captured_at": _first_str(raw.get("captured_at"), fixture_metadata.get("captured_at"), raw.get("quote_timestamp"))
        or _first_str(raw.get("captured_at_utc"), fixture_metadata.get("captured_at_utc"))
        or generated_at.isoformat(),
        "raw_source_file": str(path),
        "source_raw_file": str(path),
        "raw_row_index": row_index,
        "raw_event_index": raw.get("raw_event_index"),
        "raw_threshold_index": raw.get("raw_threshold_index"),
        "blockers": blockers,
    }


def _asset(raw: dict[str, Any], text: str) -> str | None:
    explicit = _first_str(raw.get("asset"), raw.get("underlying"))
    if explicit:
        value = explicit.upper()
        if "BTC" in value or "BITCOIN" in value:
            return "BTC"
        if "ETH" in value or "ETHEREUM" in value:
            return "ETH"
    lowered = text.lower()
    if any(token in lowered for token in ("u-btc", "bitcoin", " btc ", "btc ")):
        return "BTC"
    if any(token in lowered for token in ("u-eth", "ethereum", " eth ", "eth ")):
        return "ETH"
    return None


def _threshold(raw: dict[str, Any], text: str) -> float | None:
    for key in ("threshold_value", "threshold", "line", "strike", "target_price"):
        value = _number_or_none(raw.get(key))
        if value is not None:
            return value
    match = re.search(
        r"(?:above|below|over|under|greater\s+than|less\s+than|at\s+least|at\s+most)\s+\$?\s*"
        r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\d+(?:-?k))\b",
        text,
        re.IGNORECASE,
    )
    if match:
        return _threshold_token_to_float(match.group(1))
    return None


def _operator(raw: dict[str, Any], text: str) -> str | None:
    explicit = _first_str(raw.get("threshold_operator"), raw.get("operator"), raw.get("comparison"), raw.get("direction"))
    if explicit:
        return _normalize_operator(explicit)
    match = _OPERATOR_PATTERN.search(text)
    return _normalize_operator(match.group(0)) if match else None


def _date(raw: dict[str, Any], text: str) -> str | None:
    date_time = raw.get("date_time") if isinstance(raw.get("date_time"), dict) else {}
    explicit = _first_str(
        raw.get("measurement_date"),
        raw.get("event_date"),
        raw.get("expiration_date"),
        date_time.get("resolution_reference_time"),
        date_time.get("closes"),
    )
    if explicit:
        match = _DATE_PATTERN.search(explicit)
        return match.group(0) if match else explicit
    match = _DATE_PATTERN.search(text)
    return match.group(0) if match else None


def _time_and_timezone(raw: dict[str, Any], text: str) -> tuple[str | None, str | None]:
    date_time = raw.get("date_time") if isinstance(raw.get("date_time"), dict) else {}
    explicit_time = _first_str(
        raw.get("measurement_time"),
        raw.get("event_time"),
        raw.get("expiration_time"),
        date_time.get("resolution_reference_time"),
        date_time.get("closes"),
    )
    explicit_zone = _first_str(raw.get("timezone"), raw.get("time_zone"))
    if explicit_time:
        match = _TIME_PATTERN.search(explicit_time)
        if match:
            return " ".join(part.strip().upper().replace(".", "") for part in match.groups()), match.group(2).upper()
        return explicit_time, explicit_zone
    match = _TIME_PATTERN.search(text)
    if not match:
        return None, explicit_zone
    return " ".join(part.strip().upper().replace(".", "") for part in match.groups()), match.group(2).upper()


def _price_source_index(raw: dict[str, Any], text: str) -> str | None:
    explicit = _first_str(raw.get("price_source_index"), raw.get("source_index"), raw.get("settlement_source"))
    lowered = " ".join(value for value in (explicit, text) if value).lower()
    asset = _asset(raw, lowered) or ""
    if "rule 14.72" in lowered:
        return CDNA_RULE_1472_ETH_SOURCE_INDEX
    if "rule 14.69" in lowered and (asset == "BTC" or "nadex btc index" in lowered):
        return CDNA_RULE_1469_BTC_SOURCE_INDEX
    if "rule 14.69" in lowered and asset == "ETH":
        return "CDNA Rule 14.69 / source agency"
    cdna_terms = ("u-btc", "lukka", "ice cryptocurrency data", "ice data", "blockstream", "midpoint")
    if any(token in lowered for token in ("u-btc", "bitcoin", "btc")) and sum(1 for token in cdna_terms if token in lowered) >= 2:
        return CDNA_UBTC_SOURCE_INDEX
    eth_terms = ("u-eth", "lukka", "ice cryptocurrency data", "ice data", "blockstream", "midpoint")
    if any(token in lowered for token in ("u-eth", "ethereum", "eth")) and sum(1 for token in eth_terms if token in lowered) >= 2:
        return CDNA_UETH_SOURCE_INDEX
    if explicit:
        return explicit
    return None


def _settlement_window(raw: dict[str, Any], text: str) -> str | None:
    explicit = _first_str(raw.get("settlement_window"), raw.get("window"))
    if explicit:
        return explicit
    market_type = _normalize_market_type(_first_str(raw.get("market_type"), raw.get("type")), text)
    if market_type == "year_end_range_bucket":
        return "year_end_range_bucket"
    if market_type == "deadline_threshold_touch":
        return "deadline_threshold_touch"
    if market_type == "earliest_timeframe_threshold_touch":
        return "earliest_timeframe_threshold_touch"
    if market_type == "all_time_high_by_date":
        return "all_time_high_by_date"
    if market_type == "point_in_time_threshold":
        return "point_in_time"
    lowered = text.lower()
    has_60 = "60-second" in lowered or "60 second" in lowered or "60 seconds" in lowered
    has_25 = "25 midpoint" in lowered or "at least 25" in lowered
    if has_60 and has_25:
        return "60_seconds_preceding_at_least_25_midpoint_prices"
    if has_60:
        return "60_seconds_preceding"
    return None


def _normalize_market_type(value: str | None, text: str) -> str | None:
    token = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    known = {
        "year_end_range_bucket",
        "deadline_threshold_touch",
        "earliest_timeframe_threshold_touch",
        "all_time_high_by_date",
        "point_in_time_threshold",
    }
    if token in known:
        return token
    lowered = text.lower()
    if "price at the end of" in lowered or ("range" in lowered and "end of" in lowered):
        return "year_end_range_bucket"
    if "earliest specified timeframe" in lowered or "when will" in lowered:
        return "earliest_timeframe_threshold_touch"
    if "all time high" in lowered or "all-time high" in lowered:
        return "all_time_high_by_date"
    if "any time on or before" in lowered or "by next year" in lowered:
        return "deadline_threshold_touch"
    if "specified time" in lowered or _TIME_PATTERN.search(text):
        return "point_in_time_threshold"
    return token or None


def _shape_class(market_type: str | None) -> str | None:
    if market_type == "year_end_range_bucket":
        return SHAPE_YEAR_END_RANGE_BUCKET
    if market_type == "deadline_threshold_touch":
        return SHAPE_DEADLINE_HIT_BY_DATE
    if market_type == "earliest_timeframe_threshold_touch":
        return SHAPE_EARLIEST_TIMEFRAME_THRESHOLD_TOUCH
    if market_type == "all_time_high_by_date":
        return SHAPE_ALL_TIME_HIGH_BY_DATE
    if market_type == "point_in_time_threshold":
        return SHAPE_POINT_IN_TIME_THRESHOLD
    return SHAPE_AMBIGUOUS


def _market_shape_conservative(market_type: str | None, shape_class: str | None) -> str:
    if market_type in {
        "year_end_range_bucket",
        "deadline_threshold_touch",
        "earliest_timeframe_threshold_touch",
        "all_time_high_by_date",
        "point_in_time_threshold",
    }:
        return market_type
    if shape_class == SHAPE_AMBIGUOUS:
        return "ambiguous"
    return str(shape_class or "ambiguous").lower()


def _matchability_class(shape_class: str | None) -> str:
    if shape_class == SHAPE_POINT_IN_TIME_THRESHOLD:
        return MATCH_BASIS_RISK_POSSIBLE
    if shape_class == SHAPE_EARLIEST_TIMEFRAME_THRESHOLD_TOUCH:
        return MATCH_EARLIEST_TIMEFRAME_FV_ONLY
    if shape_class in {SHAPE_DEADLINE_HIT_BY_DATE, SHAPE_YEAR_END_RANGE_BUCKET, SHAPE_RANGE_BUCKET}:
        return MATCH_ONE_SIDED_FV_ONLY
    if shape_class == SHAPE_ALL_TIME_HIGH_BY_DATE:
        return MATCH_FV_ONLY
    return MATCH_FV_ONLY


def _measurement_window(raw: dict[str, Any], shape_class: str | None) -> str | None:
    date_time = raw.get("date_time") if isinstance(raw.get("date_time"), dict) else {}
    reference = _first_str(date_time.get("resolution_reference_time"))
    if shape_class == SHAPE_POINT_IN_TIME_THRESHOLD:
        return reference or "point_in_time"
    if shape_class == SHAPE_YEAR_END_RANGE_BUCKET:
        return reference or "year_end_range_bucket"
    if shape_class == SHAPE_DEADLINE_HIT_BY_DATE:
        return reference or "deadline_threshold_touch"
    if shape_class == SHAPE_EARLIEST_TIMEFRAME_THRESHOLD_TOUCH:
        selection = _first_str(raw.get("selection"))
        return selection or reference or "earliest_timeframe_threshold_touch"
    if shape_class == SHAPE_ALL_TIME_HIGH_BY_DATE:
        selection = _first_str(raw.get("selection"))
        return selection or reference or "all_time_high_by_date"
    return reference


def _resolution_reference_time(raw: dict[str, Any]) -> str | None:
    date_time = raw.get("date_time") if isinstance(raw.get("date_time"), dict) else {}
    return _first_str(date_time.get("resolution_reference_time"))


def _settlement_source_url(raw: dict[str, Any], text: str) -> str | None:
    explicit = _first_str(raw.get("settlement_source_url"), raw.get("rule_source_url"))
    if explicit:
        return explicit
    match = re.search(r"https?://[^\s),;\"']+", text)
    return match.group(0) if match else None


def _basis_risk_compatible(*, asset: str | None, price_source_index: str | None, shape_class: str | None) -> bool:
    if shape_class != SHAPE_POINT_IN_TIME_THRESHOLD:
        return False
    if asset == "BTC":
        return price_source_index in {CDNA_UBTC_SOURCE_INDEX, CDNA_RULE_1469_BTC_SOURCE_INDEX}
    if asset == "ETH":
        return price_source_index in {CDNA_UETH_SOURCE_INDEX, CDNA_RULE_1472_ETH_SOURCE_INDEX}
    return False


def _display_quote(raw: dict[str, Any]) -> dict[str, Any] | None:
    fields = {
        key: raw.get(key)
        for key in (
            "display_price",
            "yes_display_price",
            "no_display_price",
            "price",
            "best_bid",
            "best_ask",
            "bid_size",
            "ask_size",
            "depth_units",
            "quote_timestamp",
        )
        if raw.get(key) is not None
    }
    if not fields:
        return None
    return {
        **fields,
        "non_executable": True,
        "execution_allowed_in_project_now": False,
    }


def _blockers(
    *,
    asset: str | None,
    threshold_value: float | None,
    threshold_operator: str | None,
    measurement_date: str | None,
    price_source_index: str | None,
    settlement_window: str | None,
    shape_class: str | None = None,
    rules_text: str | None = None,
) -> list[str]:
    blockers = [
        "cdna_saved_fixture_only",
        "research_only_saved_fixture",
        "settlement_source_unverified",
        "not_integrated_with_matcher_or_evaluator",
        "execution_not_allowed_in_project_now",
        "candidate_pair_creation_forbidden",
    ]
    if not asset:
        blockers.append("missing_asset")
    if threshold_value is None and shape_class not in {SHAPE_YEAR_END_RANGE_BUCKET, SHAPE_ALL_TIME_HIGH_BY_DATE}:
        blockers.append("missing_threshold")
        blockers.append("missing_threshold_value")
    if not threshold_operator and shape_class not in {SHAPE_YEAR_END_RANGE_BUCKET, SHAPE_ALL_TIME_HIGH_BY_DATE}:
        blockers.append("missing_threshold_operator")
    if not measurement_date:
        blockers.append("missing_target_date")
        blockers.append("missing_measurement_date")
    if not price_source_index:
        blockers.append("price_source_unverified")
        blockers.append("missing_price_source")
        blockers.append("missing_price_source_index")
    elif price_source_index not in {
        CDNA_UBTC_SOURCE_INDEX,
        CDNA_UETH_SOURCE_INDEX,
        CDNA_RULE_1469_BTC_SOURCE_INDEX,
        CDNA_RULE_1472_ETH_SOURCE_INDEX,
    }:
        blockers.append("high_unreviewed")
    if not (rules_text or "").strip():
        blockers.append("missing_settlement_rules")
    if not settlement_window:
        blockers.append("missing_settlement_window")
    if shape_class == SHAPE_YEAR_END_RANGE_BUCKET:
        blockers.extend([
            "range_hit_vs_close_price_mismatch",
            "range_bucket_fv_only",
            "not_basis_risk_comparable_with_kalshi_point_in_time",
        ])
    elif shape_class == SHAPE_DEADLINE_HIT_BY_DATE:
        blockers.extend([
            "deadline_vs_point_in_time_mismatch",
            "deadline_threshold_touch_fv_only",
            "not_basis_risk_comparable_with_kalshi_point_in_time",
        ])
    elif shape_class == SHAPE_EARLIEST_TIMEFRAME_THRESHOLD_TOUCH:
        blockers.extend([
            "deadline_vs_point_in_time_mismatch",
            "earliest_timeframe_fv_only",
            "not_basis_risk_comparable_with_kalshi_point_in_time",
        ])
    elif shape_class == SHAPE_ALL_TIME_HIGH_BY_DATE:
        blockers.extend([
            "deadline_vs_point_in_time_mismatch",
            "all_time_high_by_date_fv_only",
            "all_time_high_methodology_unverified",
            "not_basis_risk_comparable_with_kalshi_point_in_time",
        ])
    elif shape_class == SHAPE_POINT_IN_TIME_THRESHOLD:
        blockers.append("basis_risk_possible_not_exact_payoff")
    elif shape_class == SHAPE_AMBIGUOUS:
        blockers.append("ambiguous_contract_shape")
    return blockers


def _summary(rows: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    blockers = Counter(blocker for row in rows for blocker in row.get("blockers") or [])
    severity = Counter(
        str(row.get("basis_risk_severity_hint_vs_kalshi_brti") or "unknown") for row in rows
    )
    events = {
        (row.get("raw_source_file"), row.get("raw_event_index"))
        for row in rows
        if row.get("raw_event_index") is not None
    }
    if not events:
        events = {(row.get("raw_source_file"), row.get("raw_row_index")) for row in rows}
    by_asset = Counter(str(row.get("asset") or "UNKNOWN") for row in rows)
    by_market_type = Counter(str(row.get("market_type") or "UNKNOWN") for row in rows)
    by_market_shape = Counter(str(row.get("market_shape_conservative") or "ambiguous") for row in rows)
    deadline_rows = sum(
        1
        for row in rows
        if row.get("shape_class") in {SHAPE_DEADLINE_HIT_BY_DATE, SHAPE_EARLIEST_TIMEFRAME_THRESHOLD_TOUCH}
    )
    range_bucket_rows = sum(1 for row in rows if row.get("shape_class") == SHAPE_YEAR_END_RANGE_BUCKET)
    all_time_high_rows = sum(1 for row in rows if row.get("shape_class") == SHAPE_ALL_TIME_HIGH_BY_DATE)
    return {
        "rows": len(rows),
        "rows_read": len(rows),
        "parsed_rows": len(rows),
        "events_read": len(events),
        "rows_by_asset": dict(sorted(by_asset.items())),
        "rows_by_market_type": dict(sorted(by_market_type.items())),
        "rows_by_market_shape": dict(sorted(by_market_shape.items())),
        "btc_rows": sum(1 for row in rows if row.get("asset") == "BTC"),
        "eth_rows": sum(1 for row in rows if row.get("asset") == "ETH"),
        "point_in_time_rows": sum(1 for row in rows if row.get("shape_class") == SHAPE_POINT_IN_TIME_THRESHOLD),
        "deadline_rows": deadline_rows,
        "range_bucket_rows": range_bucket_rows,
        "all_time_high_rows": all_time_high_rows,
        "deadline_or_range_hit_rows": deadline_rows + range_bucket_rows + all_time_high_rows,
        "basis_risk_compatible_with_kalshi": sum(1 for row in rows if row.get("basis_risk_compatible_with_kalshi")),
        "basis_risk_severity_hint_counts_vs_kalshi_brti": dict(sorted(severity.items())),
        "exact_payoff_compatible_with_kalshi": 0,
        "can_create_candidate_pair_count": 0,
        "can_create_paper_candidate_count": 0,
        "top_blockers": [{"blocker": key, "count": value} for key, value in blockers.most_common(10)],
        "warning_count": len(warnings),
    }


def _first_embedded_json(html: str) -> Any:
    for match in re.finditer(r"<script[^>]*type=[\"']application/(?:ld\+)?json[\"'][^>]*>(.*?)</script>", html, re.I | re.S):
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            continue
    return None


class _SavedPageHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._tag_stack: list[str] = []
        self._title_parts: list[str] = []
        self._text_parts: list[str] = []
        self.metadata: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._tag_stack.append(tag)
        attr_map = {key.lower(): value for key, value in attrs if value is not None}
        for source, target in (
            ("data-event-id", "event_id"),
            ("data-market-id", "market_id"),
            ("data-event-ref", "platform_event_ref"),
            ("data-market-ref", "platform_market_ref"),
            ("data-market-type", "market_type"),
        ):
            if source in attr_map:
                self.metadata[target] = attr_map[source]

    def handle_endtag(self, tag: str) -> None:
        if self._tag_stack:
            self._tag_stack.pop()

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text:
            return
        if self._tag_stack and self._tag_stack[-1] == "title":
            self._title_parts.append(text)
            self.metadata.setdefault("page_title", text)
        self._text_parts.append(text)

    def visible_text(self) -> str:
        return " ".join(self._text_parts)


def _normalize_operator(value: str) -> str:
    token = value.strip().lower().replace(" ", "_")
    if token in {"above", "over", "greater_than", ">"}:
        return ">"
    if token in {"below", "under", "less_than", "<"}:
        return "<"
    if token in {"at_least", ">="}:
        return ">="
    if token in {"at_most", "<="}:
        return "<="
    return token


def _threshold_token_to_float(token: str | None) -> float | None:
    if not token:
        return None
    text = token.strip().lower().replace("$", "").replace(",", "")
    if text.endswith("-k"):
        text = text[:-2] + "k"
    if text.endswith("k"):
        try:
            return float(text[:-1]) * 1000
        except ValueError:
            return None
    try:
        return float(text)
    except ValueError:
        return None


def _number_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_str(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
