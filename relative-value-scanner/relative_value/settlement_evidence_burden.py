from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.canonical_convention_registry import (
    load_canonical_convention_registry,
    match_canonical_registry_entry,
)
from relative_value.quote_freshness_policy import DEFAULT_STALENESS_SECONDS, quote_freshness_status
from relative_value.venue_identity import (
    executable_venue_identity_from_mapping,
    ibkr_prediction_market_row_blockers,
)


SCHEMA_VERSION = 1
REPORT_SOURCE = "settlement_evidence_burden_v1"

# Evidence-burden tiers (per family).
BURDEN_HIGH_AMBIGUITY_STRICT_SOURCE_REQUIRED = "HIGH_AMBIGUITY_STRICT_SOURCE_REQUIRED"
BURDEN_STANDARDIZED_TYPED_KEYS_REVIEW_ALLOWED = "STANDARDIZED_TYPED_KEYS_REVIEW_ALLOWED"
BURDEN_VENUE_NATIVE_STRUCTURAL_REVIEW_ALLOWED = "VENUE_NATIVE_STRUCTURAL_REVIEW_ALLOWED"
BURDEN_DISCOVERY_ONLY = "DISCOVERY_ONLY"

# Family classifications.
FAMILY_FED_FOMC = "FED_FOMC"
FAMILY_CRYPTO_PRICE_THRESHOLD = "CRYPTO_PRICE_THRESHOLD"
FAMILY_SPORTS_GAME_RESULT = "SPORTS_GAME_RESULT"
FAMILY_SPORTS_FUTURES_CHAMPIONSHIP = "SPORTS_FUTURES_CHAMPIONSHIP"
FAMILY_WEATHER = "WEATHER"
FAMILY_POLITICS_NEWS = "POLITICS_NEWS"
FAMILY_ECONOMIC_DATA_RELEASE = "ECONOMIC_DATA_RELEASE"
FAMILY_FINANCIALS_EQUITIES = "FINANCIALS_EQUITIES"
FAMILY_OTHER_UNKNOWN = "OTHER_UNKNOWN"

# Per-market review-readiness tiers (escalating).
TIER_DISCOVERY_READY = "DISCOVERY_READY"
TIER_FAMILY_TYPED_REVIEW_READY = "FAMILY_TYPED_REVIEW_READY"
TIER_SETTLEMENT_SOURCE_REVIEW_READY = "SETTLEMENT_SOURCE_REVIEW_READY"
TIER_EXACT_PAYOFF_REVIEW_READY = "EXACT_PAYOFF_REVIEW_READY"
TIER_EXECUTION_EVALUATION_READY = "EXECUTION_EVALUATION_READY"
TIER_BLOCKED = "BLOCKED"

# Families that may produce EXACT_PAYOFF_REVIEW_READY when settlement source is proven.
# Politics/news markets stay below EXACT even with source URL because of subjective
# adjudication and frequent void/recount/committee edge cases — exact-payoff review
# requires manual reviewer judgement, not just a URL.
FAMILIES_ELIGIBLE_FOR_EXACT_PAYOFF = {
    FAMILY_FED_FOMC,
    FAMILY_CRYPTO_PRICE_THRESHOLD,
    FAMILY_SPORTS_GAME_RESULT,
    FAMILY_SPORTS_FUTURES_CHAMPIONSHIP,
    FAMILY_WEATHER,
    FAMILY_ECONOMIC_DATA_RELEASE,
    FAMILY_FINANCIALS_EQUITIES,
}

FAMILY_EVIDENCE_BURDEN = {
    FAMILY_FED_FOMC: BURDEN_STANDARDIZED_TYPED_KEYS_REVIEW_ALLOWED,
    FAMILY_CRYPTO_PRICE_THRESHOLD: BURDEN_STANDARDIZED_TYPED_KEYS_REVIEW_ALLOWED,
    FAMILY_SPORTS_GAME_RESULT: BURDEN_STANDARDIZED_TYPED_KEYS_REVIEW_ALLOWED,
    FAMILY_SPORTS_FUTURES_CHAMPIONSHIP: BURDEN_STANDARDIZED_TYPED_KEYS_REVIEW_ALLOWED,
    FAMILY_ECONOMIC_DATA_RELEASE: BURDEN_STANDARDIZED_TYPED_KEYS_REVIEW_ALLOWED,
    FAMILY_FINANCIALS_EQUITIES: BURDEN_VENUE_NATIVE_STRUCTURAL_REVIEW_ALLOWED,
    FAMILY_WEATHER: BURDEN_HIGH_AMBIGUITY_STRICT_SOURCE_REQUIRED,
    FAMILY_POLITICS_NEWS: BURDEN_HIGH_AMBIGUITY_STRICT_SOURCE_REQUIRED,
    FAMILY_OTHER_UNKNOWN: BURDEN_DISCOVERY_ONLY,
}

# Required typed keys per family. These are deterministic structural keys that must be
# extractable from ticker/event identifiers or anchored patterns in rules_text — never
# from title similarity alone.
REQUIRED_TYPED_KEYS = {
    FAMILY_FED_FOMC: (
        "meeting_date",
        "rate_bound",
        "threshold_percent",
        "source_convention",
    ),
    FAMILY_CRYPTO_PRICE_THRESHOLD: (
        "asset",
        "threshold_value",
        "threshold_operator",
        "measurement_date",
        "price_source_index",
    ),
    FAMILY_SPORTS_GAME_RESULT: (
        "league",
        "season",
        "game_date",
        "team_or_matchup",
        "official_source_convention",
    ),
    FAMILY_SPORTS_FUTURES_CHAMPIONSHIP: (
        "league",
        "season",
        "team",
        "championship_or_event_name",
        "official_source_convention",
    ),
    FAMILY_WEATHER: (
        "station",
        "local_date",
        "metric",
        "threshold_value",
        "threshold_operator",
        "observation_source",
        "timezone",
    ),
    FAMILY_ECONOMIC_DATA_RELEASE: (
        "indicator_name",
        "reference_period",
        "release_source",
        "threshold_or_comparison",
    ),
    FAMILY_FINANCIALS_EQUITIES: (
        "ticker_symbol",
        "measurement_date",
        "threshold_value",
    ),
    FAMILY_POLITICS_NEWS: (
        # Politics requires explicit settlement_source_url; typed keys never promote.
        "explicit_settlement_source_url_required",
    ),
    FAMILY_OTHER_UNKNOWN: (),
}

# Anchored regex patterns. Title is never used as the sole classification signal.
_DATE_PATTERN = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b"
)
_MONTH_YEAR_PATTERN = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+\d{4}\b"
)
_PERCENT_PATTERN = re.compile(r"-?\d+(?:\.\d+)?%")
_FED_THRESHOLD_TICKER_PATTERN = re.compile(r"-T(-?\d+(?:\.\d+)?)$")
_CRYPTO_THRESHOLD_TICKER_PATTERN = re.compile(r"-T(-?\d+(?:\.\d+)?)$")
_CPI_THRESHOLD_TICKER_PATTERN = re.compile(r"-T(-?\d+(?:\.\d+)?)$")
_OPERATOR_PATTERN = re.compile(
    r"\b(?:above|below|greater\s+than|less\s+than|at\s+least|at\s+most|>=|<=|>|<)\b",
    re.IGNORECASE,
)
_TIMEZONE_PATTERN = re.compile(r"\b(EST|EDT|CST|CDT|MST|MDT|PST|PDT|UTC|GMT|ET|CT|MT|PT)\b")
_TIME_PATTERN = re.compile(
    r"\b(\d{1,2}(?::\d{2})?\s*(?:AM|PM|a\.m\.|p\.m\.)?)\s*"
    r"(EST|EDT|CST|CDT|MST|MDT|PST|PDT|UTC|GMT|ET|CT|MT|PT)\b",
    re.IGNORECASE,
)
_CRYPTO_INDEX_PHRASES = (
    "cf benchmarks",
    "brti",
    "bitcoin real-time index",
    "ethereum real-time index",
    "coinbase",
    "kraken",
    "cme cf",
    "gemini",
    "bitstamp",
    "coindesk",
)
_FED_SOURCE_PHRASES = (
    "federal reserve",
    "fomc",
    "federal open market committee",
    "fed funds",
    "federal funds rate",
)
_CPI_SOURCE_PHRASES = (
    "consumer price index",
    "bureau of labor statistics",
    "bls",
    "u.s. bls",
)
_NBA_OFFICIAL_PHRASES = ("nba.com", "national basketball association", "nba's official")
_NHL_OFFICIAL_PHRASES = ("nhl.com", "national hockey league")
_MLB_OFFICIAL_PHRASES = ("mlb.com", "major league baseball")
_NFL_OFFICIAL_PHRASES = ("nfl.com", "national football league")

# Kalshi ticker-prefix → family mapping. Deterministic on event_ticker / ticker.
KALSHI_TICKER_PREFIX_FAMILIES = (
    # FOMC dissent count is also FED-family
    ("KXFOMC", FAMILY_FED_FOMC),
    ("KXFED", FAMILY_FED_FOMC),
    ("KXBTC", FAMILY_CRYPTO_PRICE_THRESHOLD),
    ("KXETH", FAMILY_CRYPTO_PRICE_THRESHOLD),
    ("KXCPI", FAMILY_ECONOMIC_DATA_RELEASE),
    ("KXCORE", FAMILY_ECONOMIC_DATA_RELEASE),
    ("KXJOBS", FAMILY_ECONOMIC_DATA_RELEASE),
    ("KXGDP", FAMILY_ECONOMIC_DATA_RELEASE),
    ("KXPCE", FAMILY_ECONOMIC_DATA_RELEASE),
    ("KXUR", FAMILY_ECONOMIC_DATA_RELEASE),
    ("KXNBA", FAMILY_SPORTS_FUTURES_CHAMPIONSHIP),
    ("KXMLB", FAMILY_SPORTS_FUTURES_CHAMPIONSHIP),
    ("KXNHL", FAMILY_SPORTS_FUTURES_CHAMPIONSHIP),
    ("KXNFL", FAMILY_SPORTS_FUTURES_CHAMPIONSHIP),
    ("KXEPL", FAMILY_SPORTS_FUTURES_CHAMPIONSHIP),
    ("KXNCAA", FAMILY_SPORTS_FUTURES_CHAMPIONSHIP),
    ("KXTSLA", FAMILY_FINANCIALS_EQUITIES),
    ("KXIPO", FAMILY_FINANCIALS_EQUITIES),
    ("KXACQ", FAMILY_FINANCIALS_EQUITIES),
    ("KXPRES", FAMILY_POLITICS_NEWS),
    ("KXPOTUS", FAMILY_POLITICS_NEWS),
    ("KXAPRPOTUS", FAMILY_POLITICS_NEWS),
    ("KXBULGARIAPRES", FAMILY_POLITICS_NEWS),
    ("KXARGENTINAPRES", FAMILY_POLITICS_NEWS),
    ("KXBRAZILPRES", FAMILY_POLITICS_NEWS),
    ("KXWEATHER", FAMILY_WEATHER),
    ("KXTEMP", FAMILY_WEATHER),
    ("KXSNOW", FAMILY_WEATHER),
    ("KXRAIN", FAMILY_WEATHER),
    ("KXHURRICANE", FAMILY_WEATHER),
    # KXE2E is the Kalshi end-to-end test series; treat as DISCOVERY_ONLY.
    ("KXE2E", FAMILY_OTHER_UNKNOWN),
    # Catch-all AI / corporate which are politics-news style for now.
    ("OAIAGI", FAMILY_POLITICS_NEWS),
    ("KXAGICO", FAMILY_POLITICS_NEWS),
    ("KXIPOOPENAI", FAMILY_FINANCIALS_EQUITIES),
)

# Polymarket event_slug prefixes (deterministic).
POLYMARKET_SLUG_PREFIX_FAMILIES = (
    ("mlb-", FAMILY_SPORTS_FUTURES_CHAMPIONSHIP),
    ("nba-", FAMILY_SPORTS_FUTURES_CHAMPIONSHIP),
    ("nfl-", FAMILY_SPORTS_FUTURES_CHAMPIONSHIP),
    ("nhl-", FAMILY_SPORTS_FUTURES_CHAMPIONSHIP),
    ("epl-", FAMILY_SPORTS_FUTURES_CHAMPIONSHIP),
    ("ncaa-", FAMILY_SPORTS_FUTURES_CHAMPIONSHIP),
    ("madden-", FAMILY_SPORTS_FUTURES_CHAMPIONSHIP),
    ("hockey-", FAMILY_SPORTS_FUTURES_CHAMPIONSHIP),
    ("pro-basketball", FAMILY_SPORTS_FUTURES_CHAMPIONSHIP),
    ("pro-baseball", FAMILY_SPORTS_FUTURES_CHAMPIONSHIP),
    ("pro-football", FAMILY_SPORTS_FUTURES_CHAMPIONSHIP),
    ("pro-hockey", FAMILY_SPORTS_FUTURES_CHAMPIONSHIP),
    ("fed-", FAMILY_FED_FOMC),
    ("fomc-", FAMILY_FED_FOMC),
    ("btc-", FAMILY_CRYPTO_PRICE_THRESHOLD),
    ("bitcoin-", FAMILY_CRYPTO_PRICE_THRESHOLD),
    ("ethereum-", FAMILY_CRYPTO_PRICE_THRESHOLD),
    ("eth-", FAMILY_CRYPTO_PRICE_THRESHOLD),
    ("cpi-", FAMILY_ECONOMIC_DATA_RELEASE),
    ("jobs-report-", FAMILY_ECONOMIC_DATA_RELEASE),
    ("unemployment-", FAMILY_ECONOMIC_DATA_RELEASE),
    ("election-", FAMILY_POLITICS_NEWS),
    ("president-", FAMILY_POLITICS_NEWS),
    ("politics-", FAMILY_POLITICS_NEWS),
    ("weather-", FAMILY_WEATHER),
    ("temperature-", FAMILY_WEATHER),
    ("snowfall-", FAMILY_WEATHER),
    ("hurricane-", FAMILY_WEATHER),
)

POLYMARKET_CRYPTO_THRESHOLD_SLUG_PATTERNS = (
    re.compile(
        r"^(?:when-will-|will-)?"
        r"(?:bitcoin|btc|ethereum|eth)-"
        r"(?:hit|reach|cross|be-above|be-below|above|below)-"
        r"\$?\d+(?:\.\d+)?k?"
        r"(?:-|$)",
        re.IGNORECASE,
    ),
)

_POLYMARKET_CRYPTO_COMPOUND_SUFFIX_TOKENS = (
    "before",
    "after",
    "gta",
    "microstrategy",
    "sell",
    "hold",
    "acquire",
    "buy",
    "purchase",
)


CSV_FIELDS = [
    "venue",
    "source_platform",
    "access_platform",
    "exchange_venue",
    "executable_venue",
    "event_id",
    "ticker",
    "title",
    "family",
    "evidence_burden",
    "review_readiness_tier",
    "settlement_source_kind",
    "settlement_source_url_present",
    "registry_match",
    "required_typed_keys",
    "present_typed_keys",
    "missing_typed_keys",
    "source_url_required_for_review",
    "source_url_required_for_exact_evaluator",
    "not_evaluator_reason",
    "quote_freshness_blocker",
    "blockers",
    "source_file",
]


def build_settlement_evidence_burden_report(
    *,
    input_dir: Path,
    registry_path: Path | None = None,
    generated_at: datetime | None = None,
    staleness_seconds: int = DEFAULT_STALENESS_SECONDS,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    registry, registry_warnings, registry_audit_summary = _load_registry(registry_path)
    normalized_index = _normalized_index(_normalized_rows(input_dir / "normalized_markets_v0.json"))
    market_rows: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = list(registry_warnings)

    if input_dir.exists():
        for path in sorted(input_dir.rglob("*.json")):
            payload, warning = _load_json(path)
            if warning is not None:
                warnings.append(warning)
                continue
            if _skip_payload(payload):
                continue
            for index, raw in enumerate(_market_objects(payload)):
                if not isinstance(raw, dict):
                    continue
                row = _evaluate_market(
                    raw,
                    payload=payload,
                    source_file=path,
                    row_index=index,
                    registry=registry,
                    normalized_index=normalized_index,
                    now=generated,
                    staleness_seconds=staleness_seconds,
                )
                if row is not None:
                    market_rows.append(row)
    else:
        warnings.append(
            {
                "source_file": str(input_dir),
                "reason_code": "input_dir_missing",
                "blocker": "saved_input_directory_missing",
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "input_dir": str(input_dir),
        "quote_freshness_policy": {
            "staleness_seconds": staleness_seconds,
            "source": "normalized_markets_v0.quote_depth.captured_at_when_available",
        },
        "registry_path": str(registry_path) if registry_path is not None else None,
        "registry_entry_count": len(registry),
        "registry_audit_summary": registry_audit_summary,
        "summary": _summary(market_rows, warnings),
        "venues": _venue_summaries(market_rows),
        "markets": market_rows,
        "warnings": warnings,
        "safety": {
            "saved_files_only": True,
            "live_fetch_attempted": False,
            "execution_or_order_logic_added": False,
            "account_or_auth_logic_added": False,
            "tradability_claimed": False,
            "paper_candidate_emitted": False,
            "affects_evaluator_gates": False,
            "title_similarity_can_promote_typed_keys": False,
            "graph_or_llm_can_satisfy_typed_keys": False,
            "family_classification_can_force_exact_payoff": False,
            "registry_can_promote_to_evaluator_without_quote_depth_or_fees": False,
        },
    }


def write_settlement_evidence_burden_files(
    *,
    input_dir: Path,
    json_output: Path,
    csv_output: Path | None = None,
    markdown_output: Path | None = None,
    registry_path: Path | None = None,
    generated_at: datetime | None = None,
    staleness_seconds: int = DEFAULT_STALENESS_SECONDS,
) -> dict[str, Any]:
    report = build_settlement_evidence_burden_report(
        input_dir=input_dir,
        registry_path=registry_path,
        generated_at=generated_at,
        staleness_seconds=staleness_seconds,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    if csv_output is not None:
        csv_output.parent.mkdir(parents=True, exist_ok=True)
        _write_csv(report["markets"], csv_output)
    if markdown_output is not None:
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(render_settlement_evidence_burden_markdown(report), encoding="utf-8")
    return report


def render_settlement_evidence_burden_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Settlement Evidence Burden Audit",
        "",
        "Saved-file-only diagnostic. Family classification and typed-key extraction are deterministic.",
        "Title similarity, graph hints, and LLM output cannot satisfy required typed keys.",
        "No row in this report is a PAPER_CANDIDATE.",
        "",
        "## Summary by family",
        "",
        "| Family | Evidence burden | Markets | Family-typed review ready | Settlement-source review ready | Exact-payoff review ready | Execution evaluation ready |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    family_summary = (report.get("summary") or {}).get("by_family") or {}
    for family in sorted(family_summary):
        row = family_summary[family]
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(family),
                    _md(row.get("evidence_burden")),
                    _md(row.get("market_count")),
                    _md(row.get("family_typed_review_ready")),
                    _md(row.get("settlement_source_review_ready")),
                    _md(row.get("exact_payoff_review_ready")),
                    _md(row.get("execution_evaluation_ready")),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _evaluate_market(
    raw: dict[str, Any],
    *,
    payload: Any,
    source_file: Path,
    row_index: int,
    registry: list[dict[str, Any]],
    normalized_index: dict[tuple[str, str], dict[str, Any]],
    now: datetime,
    staleness_seconds: int,
) -> dict[str, Any] | None:
    venue = _string_or_none(raw.get("venue")) or _venue_from_payload(payload)
    if not venue:
        return None
    source_platform = _string_or_none(raw.get("source_platform"))
    access_platform = _string_or_none(raw.get("access_platform"))
    exchange_venue = _string_or_none(raw.get("exchange_venue"))
    executable_venue = _string_or_none(raw.get("executable_venue")) or executable_venue_identity_from_mapping(raw)
    raw_nested = raw.get("raw") if isinstance(raw.get("raw"), dict) else {}
    event_id = _string_or_none(raw.get("event_id") or raw.get("event_ticker") or raw.get("event_slug"))
    event_ticker = _string_or_none(raw.get("event_ticker") or raw_nested.get("event_ticker"))
    event_slug = _string_or_none(raw.get("event_slug") or raw_nested.get("event_slug"))
    ticker = _string_or_none(raw.get("ticker") or raw.get("market_id") or raw_nested.get("ticker"))
    title = _string_or_none(raw.get("title") or raw.get("question"))
    category = _string_or_none(raw.get("category") or raw_nested.get("category"))
    settlement = raw.get("settlement") if isinstance(raw.get("settlement"), dict) else None
    rules_text = _rules_text(raw, raw_nested, settlement)
    settlement_source_url = _string_or_none(
        (settlement or {}).get("settlement_source_url")
        or raw.get("settlement_source_url")
        or raw_nested.get("settlement_source_url")
        or _string_or_none(raw_nested.get("resolutionSource"))
    )
    settlement_source_url = _extract_url(settlement_source_url) if settlement_source_url else None
    settlement_source_kind = _string_or_none((settlement or {}).get("settlement_source_kind")) or "unknown"
    outcomes = raw.get("outcomes")
    outcome_present = bool(outcomes) if isinstance(outcomes, list) else False

    classification = classify_family(
        venue=venue,
        event_ticker=event_ticker,
        event_slug=event_slug,
        ticker=ticker,
        category=category,
        rules_text=rules_text,
    )
    family = classification["family"]
    evidence_burden = FAMILY_EVIDENCE_BURDEN[family]
    typed = extract_typed_keys(
        family=family,
        venue=venue,
        ticker=ticker,
        event_ticker=event_ticker,
        event_slug=event_slug,
        rules_text=rules_text,
    )
    typed = _merge_typed_key_overrides(typed, raw.get("typed_key_overrides"))
    registry_match = apply_canonical_registry(
        registry,
        venue=venue,
        family=family,
        event_ticker=event_ticker,
        ticker=ticker,
        event_slug=event_slug,
        typed_keys=typed,
    )

    normalized = _matching_normalized(
        venue=venue,
        raw=raw,
        event_id=event_id,
        event_ticker=event_ticker,
        event_slug=event_slug,
        ticker=ticker,
        normalized_index=normalized_index,
    )
    quote_freshness = _quote_freshness_for_row(
        raw,
        normalized=normalized,
        now=now,
        staleness_seconds=staleness_seconds,
    )
    quote_depth_ready = _quote_depth_ready(raw)
    fee_metadata_ready = _fee_metadata_ready(raw, venue)

    tier_info = assign_review_readiness_tier(
        family=family,
        evidence_burden=evidence_burden,
        typed_keys=typed,
        settlement_source_url_present=bool(settlement_source_url),
        registry_match=registry_match,
        outcome_present=outcome_present,
        quote_depth_ready=quote_depth_ready,
        quote_freshness=quote_freshness,
        fee_metadata_ready=fee_metadata_ready,
        identity_ready=bool(venue and (event_id or event_ticker or event_slug) and ticker),
    )
    tier_info = _apply_max_review_tier(tier_info, raw.get("max_review_readiness_tier"))
    row_blockers = _row_blockers(raw)
    row_blockers.extend(ibkr_prediction_market_row_blockers(raw))
    if row_blockers:
        tier_info["blockers"] = _unique_strings(list(tier_info.get("blockers") or []) + row_blockers)

    return {
        "venue": venue,
        "source_platform": source_platform,
        "access_platform": access_platform,
        "exchange_venue": exchange_venue,
        "executable_venue": executable_venue,
        "event_id": event_id,
        "event_ticker": event_ticker,
        "event_slug": event_slug,
        "market_id": _string_or_none(raw.get("market_id")),
        "ticker": ticker,
        "title": title,
        "direction": _string_or_none(raw.get("direction")),
        "measurement_month": _string_or_none(raw.get("measurement_month")),
        "measurement_window_start": _string_or_none(raw.get("measurement_window_start")),
        "measurement_window_end": _string_or_none(raw.get("measurement_window_end")),
        "settlement_window": _string_or_none(raw.get("settlement_window")),
        "quote_captured_at": _string_or_none(raw.get("quote_captured_at")),
        "can_create_candidate_pair": raw.get("can_create_candidate_pair"),
        "can_create_paper_candidate": raw.get("can_create_paper_candidate"),
        "family": family,
        "family_signals": classification["signals"],
        "family_classification_paths": classification["paths"],
        "evidence_burden": evidence_burden,
        "review_readiness_tier": tier_info["tier"],
        "settlement_source_kind": settlement_source_kind,
        "settlement_source_url_present": bool(settlement_source_url),
        "registry_match": registry_match,
        "required_typed_keys": list(typed["required"]),
        "present_typed_keys": list(typed["present"]),
        "missing_typed_keys": list(typed["missing"]),
        "typed_key_evidence": typed["evidence"],
        "source_url_required_for_review": tier_info["source_url_required_for_review"],
        "source_url_required_for_exact_evaluator": tier_info["source_url_required_for_exact_evaluator"],
        "not_evaluator_reason": tier_info["not_evaluator_reason"],
        "quote_freshness_status": quote_freshness,
        "blockers": tier_info["blockers"],
        "source_file": str(source_file),
        "row_index": row_index,
        "diagnostic_only": True,
        "paper_candidate_emitted": False,
        "affects_evaluator_gates": False,
    }


def classify_family(
    *,
    venue: str,
    event_ticker: str | None,
    event_slug: str | None,
    ticker: str | None,
    category: str | None,
    rules_text: str | None,
) -> dict[str, Any]:
    paths: list[str] = []
    signals: list[str] = []
    family = FAMILY_OTHER_UNKNOWN
    venue_lower = venue.lower()

    if venue_lower == "kalshi":
        ticker_source = (event_ticker or ticker or "").upper()
        for prefix, candidate in KALSHI_TICKER_PREFIX_FAMILIES:
            if ticker_source.startswith(prefix):
                family = candidate
                paths.append(f"kalshi.ticker_prefix:{prefix}")
                signals.append(f"deterministic_ticker_prefix:{prefix}")
                return {"family": family, "signals": signals, "paths": paths}

    if venue_lower == "polymarket":
        slug_source = (event_slug or "").lower()
        for prefix, candidate in POLYMARKET_SLUG_PREFIX_FAMILIES:
            if slug_source.startswith(prefix):
                family = candidate
                paths.append(f"polymarket.event_slug_prefix:{prefix}")
                signals.append(f"deterministic_event_slug_prefix:{prefix}")
                return {"family": family, "signals": signals, "paths": paths}
        if _polymarket_crypto_threshold_slug_matches(slug_source):
            family = FAMILY_CRYPTO_PRICE_THRESHOLD
            paths.append("polymarket.event_slug_threshold_pattern")
            signals.append("polymarket.event_slug_threshold_pattern")
            return {"family": family, "signals": signals, "paths": paths}

    if category:
        normalized_category = category.lower()
        category_family = _category_family(normalized_category)
        if category_family is not None:
            paths.append(f"category:{normalized_category}")
            signals.append(f"category_field:{normalized_category}")
            return {"family": category_family, "signals": signals, "paths": paths}

    if rules_text:
        rules_lower = rules_text.lower()
        if any(phrase in rules_lower for phrase in _FED_SOURCE_PHRASES):
            paths.append("rules_text.fed_phrase")
            signals.append("rules_text_phrase:federal_reserve_or_fomc")
            return {"family": FAMILY_FED_FOMC, "signals": signals, "paths": paths}
        if any(phrase in rules_lower for phrase in _CRYPTO_INDEX_PHRASES):
            paths.append("rules_text.crypto_index_phrase")
            signals.append("rules_text_phrase:crypto_index")
            return {"family": FAMILY_CRYPTO_PRICE_THRESHOLD, "signals": signals, "paths": paths}
        if any(phrase in rules_lower for phrase in _CPI_SOURCE_PHRASES):
            paths.append("rules_text.cpi_phrase")
            signals.append("rules_text_phrase:cpi_source")
            return {"family": FAMILY_ECONOMIC_DATA_RELEASE, "signals": signals, "paths": paths}
        for phrases, family_candidate in (
            (_NBA_OFFICIAL_PHRASES, FAMILY_SPORTS_FUTURES_CHAMPIONSHIP),
            (_NHL_OFFICIAL_PHRASES, FAMILY_SPORTS_FUTURES_CHAMPIONSHIP),
            (_MLB_OFFICIAL_PHRASES, FAMILY_SPORTS_FUTURES_CHAMPIONSHIP),
            (_NFL_OFFICIAL_PHRASES, FAMILY_SPORTS_FUTURES_CHAMPIONSHIP),
        ):
            if any(phrase in rules_lower for phrase in phrases):
                paths.append("rules_text.sports_official_source_phrase")
                signals.append("rules_text_phrase:sports_official_source")
                return {"family": family_candidate, "signals": signals, "paths": paths}

    paths.append("no_deterministic_signal")
    signals.append("title_only_or_unknown")
    return {"family": FAMILY_OTHER_UNKNOWN, "signals": signals, "paths": paths}


def extract_typed_keys(
    *,
    family: str,
    venue: str,
    ticker: str | None,
    event_ticker: str | None,
    event_slug: str | None,
    rules_text: str | None,
) -> dict[str, Any]:
    required = REQUIRED_TYPED_KEYS.get(family, ())
    present: list[str] = []
    missing: list[str] = []
    evidence: dict[str, Any] = {}
    rules_lower = rules_text.lower() if rules_text else ""

    def record(key: str, value: Any, source: str) -> None:
        present.append(key)
        evidence[key] = {"value": value, "source": source}

    if family == FAMILY_FED_FOMC:
        meeting_date = _first_match(rules_text, _DATE_PATTERN)
        if meeting_date:
            record("meeting_date", meeting_date, "rules_text:date_pattern")
        if rules_lower and ("upper bound" in rules_lower or "lower bound" in rules_lower or "target range" in rules_lower):
            bound = "upper_bound" if "upper bound" in rules_lower else "lower_bound" if "lower bound" in rules_lower else "target_range"
            record("rate_bound", bound, "rules_text:bound_phrase")
        threshold_value: float | None = None
        if event_ticker or ticker:
            match = _FED_THRESHOLD_TICKER_PATTERN.search((event_ticker or "") + "|" + (ticker or ""))
            if match:
                try:
                    threshold_value = float(match.group(1))
                except ValueError:
                    threshold_value = None
        if threshold_value is None and rules_text:
            percent_match = _PERCENT_PATTERN.search(rules_text)
            if percent_match:
                try:
                    threshold_value = float(percent_match.group(0).rstrip("%"))
                except ValueError:
                    threshold_value = None
        if threshold_value is not None:
            record("threshold_percent", threshold_value, "ticker_T_pattern_or_rules_percent")
        if rules_lower and any(phrase in rules_lower for phrase in _FED_SOURCE_PHRASES):
            record("source_convention", "federal_reserve_official_website", "rules_text:fed_source_phrase")

    elif family == FAMILY_CRYPTO_PRICE_THRESHOLD:
        asset: str | None = None
        ticker_upper = (event_ticker or ticker or "").upper()
        slug_lower = (event_slug or "").lower()
        if ticker_upper.startswith("KXBTC"):
            asset = "BTC"
        elif ticker_upper.startswith("KXETH"):
            asset = "ETH"
        else:
            asset = _crypto_asset_from_slug(slug_lower)
        if asset is None and rules_lower:
            if "bitcoin" in rules_lower:
                asset = "BTC"
            elif "ethereum" in rules_lower:
                asset = "ETH"
        if asset:
            record("asset", asset, "ticker_prefix_or_rules_text")
        threshold_match = _CRYPTO_THRESHOLD_TICKER_PATTERN.search((event_ticker or "") + "|" + (ticker or ""))
        if threshold_match:
            try:
                record("threshold_value", float(threshold_match.group(1)), "ticker_T_pattern")
            except ValueError:
                pass
        if "threshold_value" not in evidence:
            slug_threshold = _crypto_threshold_from_slug(event_slug)
            if slug_threshold is not None:
                record("threshold_value", slug_threshold, "event_slug:crypto_threshold_pattern")
        if "threshold_value" not in evidence:
            rules_threshold = _crypto_threshold_from_rules(rules_text)
            if rules_threshold is not None:
                record("threshold_value", rules_threshold, "rules_text:operator_threshold_pattern")
        operator_match = _OPERATOR_PATTERN.search(rules_text or "")
        if operator_match:
            record("threshold_operator", operator_match.group(0).lower(), "rules_text:operator_phrase")
        measurement_date = _first_match(rules_text, _DATE_PATTERN)
        if measurement_date:
            record("measurement_date", measurement_date, "rules_text:date_pattern")
        time_match = _TIME_PATTERN.search(rules_text or "")
        if time_match:
            record(
                "measurement_time",
                " ".join(part.strip().upper().replace(".", "") for part in time_match.groups()),
                "rules_text:time_pattern",
            )
        timezone_match = _TIMEZONE_PATTERN.search(rules_text or "")
        if timezone_match:
            record("timezone", timezone_match.group(1), "rules_text:timezone_pattern")
        for phrase in _CRYPTO_INDEX_PHRASES:
            if phrase in rules_lower:
                record("price_source_index", phrase, "rules_text:index_phrase")
                break

    elif family == FAMILY_SPORTS_FUTURES_CHAMPIONSHIP:
        league: str | None = None
        season: str | None = None
        team: str | None = None
        if venue.lower() == "kalshi":
            # Market ticker is the long form (e.g., KXNBA-26-SAS); event_ticker is short
            # (KXNBA-26). Use the longer one for segment decomposition.
            ticker_source = ticker if (ticker and (event_ticker is None or len(ticker) > len(event_ticker))) else (event_ticker or ticker or "")
            for prefix, abbreviation in (("KXNBA", "NBA"), ("KXMLB", "MLB"), ("KXNHL", "NHL"), ("KXNFL", "NFL"), ("KXEPL", "EPL")):
                if ticker_source.upper().startswith(prefix):
                    league = abbreviation
                    break
            if ticker_source:
                segments = ticker_source.split("-")
                if len(segments) >= 2:
                    season_segment = segments[1]
                    if re.fullmatch(r"\d{2,4}", season_segment):
                        season = season_segment
                if len(segments) >= 3:
                    team_segment = segments[2]
                    if team_segment and team_segment.isalpha():
                        team = team_segment
        elif venue.lower() == "polymarket":
            slug_source = (event_ticker or "") + "/" + (ticker or "") + "/" + ""
            if "nba" in slug_source.lower():
                league = "NBA"
            elif "mlb" in slug_source.lower():
                league = "MLB"
            elif "nhl" in slug_source.lower():
                league = "NHL"
            elif "nfl" in slug_source.lower():
                league = "NFL"
        if league:
            record("league", league, "ticker_or_slug_prefix")
        if season:
            record("season", season, "ticker_season_segment")
        if team:
            record("team", team, "ticker_team_segment")
        if rules_lower and any(
            phrase in rules_lower
            for phrase in (
                "world series",
                "stanley cup",
                "nba finals",
                "super bowl",
                "premier league",
                "championship",
                "pro basketball finals",
                "pro baseball finals",
                "pro hockey finals",
                "pro football finals",
            )
        ):
            record("championship_or_event_name", "championship_phrase_detected", "rules_text:championship_phrase")
        for phrases, label in (
            (_NBA_OFFICIAL_PHRASES, "NBA_official"),
            (_NHL_OFFICIAL_PHRASES, "NHL_official"),
            (_MLB_OFFICIAL_PHRASES, "MLB_official"),
            (_NFL_OFFICIAL_PHRASES, "NFL_official"),
        ):
            if any(phrase in rules_lower for phrase in phrases):
                record("official_source_convention", label, "rules_text:league_official_phrase")
                break

    elif family == FAMILY_SPORTS_GAME_RESULT:
        if rules_lower and any(phrase in rules_lower for phrase in _NBA_OFFICIAL_PHRASES + _MLB_OFFICIAL_PHRASES + _NHL_OFFICIAL_PHRASES + _NFL_OFFICIAL_PHRASES):
            record("official_source_convention", "league_official_phrase", "rules_text:league_official_phrase")
        game_date = _first_match(rules_text, _DATE_PATTERN)
        if game_date:
            record("game_date", game_date, "rules_text:date_pattern")

    elif family == FAMILY_ECONOMIC_DATA_RELEASE:
        if rules_lower:
            if "consumer price index" in rules_lower:
                record("indicator_name", "CPI", "rules_text:indicator_phrase")
            elif "personal consumption expenditures" in rules_lower or "pce" in rules_lower:
                record("indicator_name", "PCE", "rules_text:indicator_phrase")
            elif "gross domestic product" in rules_lower or "gdp" in rules_lower:
                record("indicator_name", "GDP", "rules_text:indicator_phrase")
            elif "unemployment" in rules_lower or "jobs report" in rules_lower:
                record("indicator_name", "JOBS", "rules_text:indicator_phrase")
            if any(phrase in rules_lower for phrase in _CPI_SOURCE_PHRASES):
                record("release_source", "BLS", "rules_text:cpi_source_phrase")
        ref_period = _first_match(rules_text, _MONTH_YEAR_PATTERN)
        if ref_period:
            record("reference_period", ref_period, "rules_text:month_year_pattern")
        operator_match = _OPERATOR_PATTERN.search(rules_text or "")
        if operator_match:
            record("threshold_or_comparison", operator_match.group(0).lower(), "rules_text:operator_phrase")

    elif family == FAMILY_FINANCIALS_EQUITIES:
        if event_ticker or ticker:
            symbol_match = re.match(r"KX([A-Z]+)", (event_ticker or ticker or "").upper())
            if symbol_match:
                record("ticker_symbol", symbol_match.group(1), "ticker_alpha_segment")
        threshold_match = _FED_THRESHOLD_TICKER_PATTERN.search((event_ticker or "") + "|" + (ticker or ""))
        if threshold_match:
            try:
                record("threshold_value", float(threshold_match.group(1)), "ticker_T_pattern")
            except ValueError:
                pass
        measurement_date = _first_match(rules_text, _DATE_PATTERN)
        if measurement_date:
            record("measurement_date", measurement_date, "rules_text:date_pattern")

    elif family == FAMILY_WEATHER:
        # Weather intentionally has no deterministic title-promotion.
        # Station, metric, threshold, and observation_source must come from rules_text or registry.
        if rules_text:
            # Anchored "Station X" patterns; never accept title-only city guesses.
            station_match = re.search(r"\b(?:station|airport|weather\s+observation\s+station)\s+([A-Z]{3,5})\b", rules_text, re.IGNORECASE)
            if station_match:
                record("station", station_match.group(1).upper(), "rules_text:station_pattern")
            metric_match = re.search(r"\b(temperature|snowfall|precipitation|rainfall|wind\s+speed|hurricane\s+strength)\b", rules_text, re.IGNORECASE)
            if metric_match:
                record("metric", metric_match.group(1).lower(), "rules_text:metric_phrase")
            threshold_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:degrees|°[FC]|inches|mph|knots)", rules_text, re.IGNORECASE)
            if threshold_match:
                try:
                    record("threshold_value", float(threshold_match.group(1)), "rules_text:threshold_with_unit")
                except ValueError:
                    pass
            operator_match = _OPERATOR_PATTERN.search(rules_text)
            if operator_match:
                record("threshold_operator", operator_match.group(0).lower(), "rules_text:operator_phrase")
            timezone_match = re.search(r"\b(EST|EDT|CST|CDT|MST|MDT|PST|PDT|UTC|GMT|ET|CT|MT|PT)\b", rules_text)
            if timezone_match:
                record("timezone", timezone_match.group(1), "rules_text:timezone_phrase")
            obs_match = re.search(r"\b(NOAA|National\s+Weather\s+Service|NWS|weather\.gov|Weather\s+Underground|wunderground)\b", rules_text, re.IGNORECASE)
            if obs_match:
                record("observation_source", obs_match.group(1).upper(), "rules_text:observation_source_phrase")
            local_date = _first_match(rules_text, _DATE_PATTERN)
            if local_date:
                record("local_date", local_date, "rules_text:date_pattern")

    elif family == FAMILY_POLITICS_NEWS:
        # Politics never auto-promotes; the only "required" key is explicit URL,
        # which is checked at the tier-assignment level, not extracted here.
        pass

    missing = [key for key in required if key not in evidence]
    return {
        "required": list(required),
        "present": present,
        "missing": missing,
        "evidence": evidence,
    }


def apply_canonical_registry(
    registry: list[dict[str, Any]],
    *,
    venue: str,
    family: str,
    event_ticker: str | None,
    ticker: str | None,
    event_slug: str | None,
    typed_keys: dict[str, Any],
) -> dict[str, Any] | None:
    return match_canonical_registry_entry(
        registry,
        venue=venue,
        family=family,
        event_ticker=event_ticker,
        ticker=ticker,
        event_slug=event_slug,
        typed_keys=typed_keys,
    )


def _merge_typed_key_overrides(typed: dict[str, Any], overrides: Any) -> dict[str, Any]:
    if not isinstance(overrides, dict) or not overrides:
        return typed
    merged = {
        "required": list(typed.get("required") or []),
        "present": list(typed.get("present") or []),
        "missing": list(typed.get("missing") or []),
        "evidence": dict(typed.get("evidence") or {}),
    }
    for key, raw_value in overrides.items():
        if raw_value is None:
            continue
        if isinstance(raw_value, dict):
            value = raw_value.get("value")
            source = _string_or_none(raw_value.get("source")) or "typed_key_override"
        else:
            value = raw_value
            source = "typed_key_override"
        if value is None:
            continue
        merged["evidence"][str(key)] = {"value": value, "source": source}
        if key not in merged["present"]:
            merged["present"].append(str(key))
        merged["missing"] = [missing for missing in merged["missing"] if missing != key]
    return merged


def _row_blockers(raw: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    for key in ("blockers", "ingestion_blockers", "manual_fixture_blockers"):
        value = raw.get(key)
        if isinstance(value, list):
            blockers.extend(str(item) for item in value if item)
    return _unique_strings(blockers)


def _apply_max_review_tier(tier_info: dict[str, Any], max_tier: Any) -> dict[str, Any]:
    if max_tier != TIER_SETTLEMENT_SOURCE_REVIEW_READY:
        return tier_info
    if tier_info.get("tier") not in {TIER_EXACT_PAYOFF_REVIEW_READY, TIER_EXECUTION_EVALUATION_READY}:
        return tier_info
    downgraded = dict(tier_info)
    downgraded["tier"] = TIER_SETTLEMENT_SOURCE_REVIEW_READY
    downgraded["not_evaluator_reason"] = "manual_fixture_not_live_market_snapshot"
    downgraded["blockers"] = _unique_strings(
        list(downgraded.get("blockers") or []) + ["manual_fixture_not_live_market_snapshot"]
    )
    return downgraded


def assign_review_readiness_tier(
    *,
    family: str,
    evidence_burden: str,
    typed_keys: dict[str, Any],
    settlement_source_url_present: bool,
    registry_match: dict[str, Any] | None,
    outcome_present: bool,
    quote_depth_ready: bool,
    quote_freshness: dict[str, Any],
    fee_metadata_ready: bool,
    identity_ready: bool,
) -> dict[str, Any]:
    blockers: list[str] = []
    identity_or_outcome_blockers: list[str] = []
    if not identity_ready:
        identity_or_outcome_blockers.append("missing_identity")
    if not outcome_present:
        identity_or_outcome_blockers.append("missing_outcomes")
    quote_freshness_blocker = _string_or_none(quote_freshness.get("blocker"))
    quote_is_fresh = quote_freshness.get("is_fresh") is True
    if quote_freshness_blocker:
        blockers.append(quote_freshness_blocker)
    blockers.extend(identity_or_outcome_blockers)
    if identity_or_outcome_blockers:
        return {
            "tier": TIER_BLOCKED,
            "blockers": blockers,
            "source_url_required_for_review": _requires_source_for_review(evidence_burden),
            "source_url_required_for_exact_evaluator": True,
            "not_evaluator_reason": "identity_or_outcomes_missing",
        }

    typed_complete = bool(typed_keys["required"]) and not typed_keys["missing"]
    has_settlement_source = settlement_source_url_present or bool(registry_match)
    requires_source_for_review = _requires_source_for_review(evidence_burden)

    # Step 1: DISCOVERY_READY is the floor once identity + outcomes are present.
    tier = TIER_DISCOVERY_READY
    not_evaluator_reason = "discovery_only_tier"

    # Step 2: FAMILY_TYPED_REVIEW_READY only for standardized/structural burdens and only if
    # required typed keys are complete and the family actually has any typed-key contract.
    if evidence_burden in {BURDEN_STANDARDIZED_TYPED_KEYS_REVIEW_ALLOWED, BURDEN_VENUE_NATIVE_STRUCTURAL_REVIEW_ALLOWED}:
        if typed_complete:
            tier = TIER_FAMILY_TYPED_REVIEW_READY
            not_evaluator_reason = "missing_settlement_source_for_evaluator"
        else:
            blockers.append("missing_required_typed_keys")
            not_evaluator_reason = "missing_required_typed_keys"
    elif evidence_burden == BURDEN_HIGH_AMBIGUITY_STRICT_SOURCE_REQUIRED:
        blockers.append("high_ambiguity_requires_explicit_source")
        not_evaluator_reason = "high_ambiguity_family_requires_explicit_source"
    elif evidence_burden == BURDEN_DISCOVERY_ONLY:
        blockers.append("family_not_classified")
        not_evaluator_reason = "family_not_classified_for_review"

    # Step 3: SETTLEMENT_SOURCE_REVIEW_READY requires explicit URL or registry-reviewed entry.
    # High-ambiguity families MUST have a source URL or registry entry. Standardized/structural
    # families also escalate to this tier when source is proven; otherwise they stay at the
    # FAMILY_TYPED_REVIEW_READY ceiling.
    if has_settlement_source:
        # For HIGH_AMBIGUITY, the source is a hard prerequisite — typed keys are not enough.
        if evidence_burden == BURDEN_HIGH_AMBIGUITY_STRICT_SOURCE_REQUIRED and not typed_complete:
            blockers.append("high_ambiguity_requires_typed_keys")
            not_evaluator_reason = "high_ambiguity_typed_keys_incomplete"
        else:
            tier = TIER_SETTLEMENT_SOURCE_REVIEW_READY
            not_evaluator_reason = "settlement_source_present_but_not_eligible_for_exact_or_execution"

    # Step 4: EXACT_PAYOFF_REVIEW_READY requires settlement_source + family-eligible.
    if (
        tier == TIER_SETTLEMENT_SOURCE_REVIEW_READY
        and family in FAMILIES_ELIGIBLE_FOR_EXACT_PAYOFF
        and typed_complete
        and quote_is_fresh
    ):
        tier = TIER_EXACT_PAYOFF_REVIEW_READY
        not_evaluator_reason = "missing_quote_depth_or_fee_metadata"
    elif (
        tier == TIER_SETTLEMENT_SOURCE_REVIEW_READY
        and family in FAMILIES_ELIGIBLE_FOR_EXACT_PAYOFF
        and typed_complete
        and quote_freshness_blocker
    ):
        not_evaluator_reason = quote_freshness_blocker

    # Step 5: EXECUTION_EVALUATION_READY layered ONLY on EXACT_PAYOFF_REVIEW_READY plus quote/fee.
    if tier == TIER_EXACT_PAYOFF_REVIEW_READY and quote_depth_ready and fee_metadata_ready:
        tier = TIER_EXECUTION_EVALUATION_READY
        not_evaluator_reason = "execution_metadata_complete_pending_pair_review"
    elif tier == TIER_EXACT_PAYOFF_REVIEW_READY and not quote_depth_ready:
        blockers.append("missing_quote_depth_for_execution")
    elif tier == TIER_EXACT_PAYOFF_REVIEW_READY and not fee_metadata_ready:
        blockers.append("missing_fee_metadata_for_execution")

    # Politics never reaches FAMILY_TYPED_REVIEW_READY through typed keys; only source URL +
    # manual review can move it forward. Even then, EXACT_PAYOFF is disallowed without
    # reviewer judgement (see FAMILIES_ELIGIBLE_FOR_EXACT_PAYOFF).
    if family == FAMILY_POLITICS_NEWS and not has_settlement_source:
        blockers.append("politics_news_requires_explicit_source")

    return {
        "tier": tier,
        "blockers": blockers,
        "source_url_required_for_review": requires_source_for_review,
        "source_url_required_for_exact_evaluator": True,
        "not_evaluator_reason": not_evaluator_reason,
    }


def _requires_source_for_review(evidence_burden: str) -> bool:
    return evidence_burden in {BURDEN_HIGH_AMBIGUITY_STRICT_SOURCE_REQUIRED, BURDEN_DISCOVERY_ONLY}


def _rules_text(raw: dict[str, Any], raw_nested: dict[str, Any], settlement: dict[str, Any] | None) -> str | None:
    if isinstance(settlement, dict) and _string_or_none(settlement.get("settlement_rules_text")):
        return _string_or_none(settlement.get("settlement_rules_text"))
    for source in (raw, raw_nested):
        for key in ("settlement_rules", "rules", "rules_primary", "rules_secondary", "resolution_text", "resolution_criteria"):
            text = _string_or_none(source.get(key))
            if text:
                return text
    return None


def _category_family(category: str) -> str | None:
    if category in {"sports", "sport"}:
        return FAMILY_SPORTS_FUTURES_CHAMPIONSHIP
    if category in {"weather", "climate"}:
        return FAMILY_WEATHER
    if category in {"politics", "election", "news"}:
        return FAMILY_POLITICS_NEWS
    if category in {"economics", "economic_data", "macro"}:
        return FAMILY_ECONOMIC_DATA_RELEASE
    if category in {"crypto", "cryptocurrency"}:
        return FAMILY_CRYPTO_PRICE_THRESHOLD
    if category in {"fed", "fomc", "rates"}:
        return FAMILY_FED_FOMC
    if category in {"equities", "stocks", "finance"}:
        return FAMILY_FINANCIALS_EQUITIES
    return None


def _quote_depth_ready(raw: dict[str, Any]) -> bool:
    readiness = raw.get("readiness") if isinstance(raw.get("readiness"), dict) else {}
    if "quote_depth_ready" in readiness:
        return bool(readiness.get("quote_depth_ready"))
    enrichment = raw.get("orderbook_enrichment") if isinstance(raw.get("orderbook_enrichment"), dict) else {}
    if not enrichment:
        return False
    captured = enrichment.get("orderbook_captured_at") or enrichment.get("orderbookCapturedAt")
    if not captured:
        return False
    return (
        _number_or_none(enrichment.get("best_ask")) is not None
        and _number_or_none(enrichment.get("best_bid")) is not None
        and (
            _number_or_none(enrichment.get("depth_at_best_ask")) is not None
            or _number_or_none(enrichment.get("depth_at_best_bid")) is not None
        )
    )


def _quote_freshness_for_row(
    raw: dict[str, Any],
    *,
    normalized: dict[str, Any] | None,
    now: datetime,
    staleness_seconds: int,
) -> dict[str, Any]:
    captured_at, source = _quote_captured_at_for_freshness(raw, normalized=normalized)
    status = quote_freshness_status(captured_at, now=now, staleness_seconds=staleness_seconds)
    return {
        **status,
        "source": source,
        "staleness_seconds": staleness_seconds,
    }


def _quote_captured_at_for_freshness(
    raw: dict[str, Any],
    *,
    normalized: dict[str, Any] | None,
) -> tuple[str | None, str]:
    if normalized:
        quote = normalized.get("quote_depth") if isinstance(normalized.get("quote_depth"), dict) else {}
        captured = _string_or_none(quote.get("captured_at"))
        if captured:
            return captured, "normalized_markets_v0.quote_depth.captured_at"
        return None, "normalized_markets_v0.quote_depth.captured_at"
    quote = raw.get("quote_depth") if isinstance(raw.get("quote_depth"), dict) else {}
    captured = _string_or_none(quote.get("captured_at"))
    if captured:
        return captured, "row.quote_depth.captured_at"
    enrichment = raw.get("orderbook_enrichment") if isinstance(raw.get("orderbook_enrichment"), dict) else {}
    captured = _string_or_none(enrichment.get("orderbook_captured_at") or enrichment.get("orderbookCapturedAt"))
    if captured:
        return captured, "row.orderbook_enrichment.orderbook_captured_at"
    return None, "missing"


def _matching_normalized(
    *,
    venue: str,
    raw: dict[str, Any],
    event_id: str | None,
    event_ticker: str | None,
    event_slug: str | None,
    ticker: str | None,
    normalized_index: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any] | None:
    for value in (
        ticker,
        raw.get("market_id"),
        event_id,
        event_ticker,
        event_slug,
        raw.get("token_id"),
    ):
        text = _string_or_none(value)
        if text and (venue, text) in normalized_index:
            return normalized_index[(venue, text)]
    return None


def _normalized_rows(path: Path) -> list[dict[str, Any]]:
    payload, warning = _load_json(path)
    if warning is not None or not isinstance(payload, dict):
        return []
    rows = payload.get("normalized_markets")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _normalized_index(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        venue = _string_or_none(row.get("venue"))
        if not venue:
            continue
        for key in ("ticker", "market_id", "event_id", "event_ticker", "event_slug", "token_id"):
            value = _string_or_none(row.get(key))
            if value:
                index[(venue, value)] = row
    return index


def _fee_metadata_ready(raw: dict[str, Any], venue: str | None) -> bool:
    readiness = raw.get("readiness") if isinstance(raw.get("readiness"), dict) else {}
    if "fee_metadata_ready" in readiness:
        return bool(readiness.get("fee_metadata_ready"))
    fee_metadata = raw.get("fee_metadata") if isinstance(raw.get("fee_metadata"), dict) else {}
    status = _string_or_none(fee_metadata.get("fee_model_status")) if fee_metadata else None
    if status in {"conservative_venue_default", "known_default_fee_model", "explicit_reviewed", "reviewed"}:
        return True
    # Fall back to venue default — same conservative model normalized_markets_v0 uses.
    return _string_or_none(venue or "").lower() in {"kalshi", "polymarket"} if venue else False


def _scope_matches(scope: dict[str, Any], *, event_ticker: str | None, ticker: str | None, event_slug: str | None) -> bool:
    prefix = scope.get("event_ticker_prefix")
    if prefix and isinstance(prefix, str):
        if not (event_ticker or "").upper().startswith(prefix.upper()) and not (ticker or "").upper().startswith(prefix.upper()):
            return False
    slug_prefix = scope.get("event_slug_prefix")
    if slug_prefix and isinstance(slug_prefix, str):
        if not (event_slug or "").lower().startswith(slug_prefix.lower()):
            return False
    return True


def _market_objects(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    if payload.get("fixture_kind") == "manual_polymarket_crypto_event_page_snapshot":
        return _manual_polymarket_crypto_event_page_rows(payload)
    normalized = payload.get("normalized_markets")
    if isinstance(normalized, list):
        return normalized
    for key in ("markets", "records"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _manual_polymarket_crypto_event_page_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    markets = payload.get("markets")
    if not isinstance(markets, list):
        return []
    rows: list[dict[str, Any]] = []
    event_slug = _string_or_none(payload.get("event_slug")) or "manual-polymarket-crypto-event"
    asset = _string_or_none(payload.get("asset")) or "BTC"
    measurement_month = _string_or_none(payload.get("measurement_month"))
    measurement_date = _string_or_none(payload.get("measurement_date")) or measurement_month
    measurement_time = _string_or_none(payload.get("measurement_time"))
    timezone_text = _string_or_none(payload.get("timezone"))
    source_index = _string_or_none(payload.get("settlement_source") or payload.get("price_source_index"))
    source_url = _string_or_none(payload.get("settlement_source_url"))
    rules_text = _string_or_none(payload.get("rules_text")) or _string_or_none(payload.get("settlement_rules_text"))
    captured_at = _string_or_none(payload.get("quote_captured_at") or payload.get("captured_at"))
    payload_blockers = [str(item) for item in payload.get("blockers") or [] if item] if isinstance(payload.get("blockers"), list) else []
    settlement_shape = _string_or_none(payload.get("settlement_shape"))
    for index, item in enumerate(markets):
        if not isinstance(item, dict):
            continue
        direction = (_string_or_none(item.get("direction")) or "").lower()
        threshold = _number_or_none(item.get("threshold"))
        operator = _string_or_none(item.get("operator")) or _operator_for_direction(direction)
        if threshold is None or operator is None:
            continue
        threshold_token = _threshold_market_id_token(threshold)
        direction_token = _safe_id_token(direction or "threshold")
        market_id = _string_or_none(item.get("market_id")) or f"{event_slug}::{direction_token}::{threshold_token}"
        settlement_window = (
            _string_or_none(item.get("settlement_window"))
            or _string_or_none(payload.get("settlement_window"))
            or _manual_polymarket_settlement_window(direction)
        )
        item_blockers = [str(value) for value in item.get("blockers") or [] if value] if isinstance(item.get("blockers"), list) else []
        manual_blockers = _manual_polymarket_blockers(
            source_index=source_index,
            settlement_shape=settlement_shape,
            settlement_window=settlement_window,
            payload_blockers=payload_blockers,
            item_blockers=item_blockers,
        )
        rows.append(
            {
                "venue": "polymarket",
                "event_id": event_slug,
                "event_slug": event_slug,
                "market_id": market_id,
                "ticker": market_id,
                "title": _string_or_none(item.get("label")) or _string_or_none(payload.get("event_title")),
                "category": "crypto",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "direction": direction or None,
                "measurement_month": measurement_month,
                "measurement_date": measurement_date,
                "measurement_time": measurement_time,
                "timezone": timezone_text,
                "measurement_window_start": _string_or_none(payload.get("measurement_window_start")),
                "measurement_window_end": _string_or_none(payload.get("measurement_window_end")),
                "settlement_window": settlement_window,
                "quote_captured_at": captured_at,
                "token_ids": list(item.get("token_ids") or []) if isinstance(item.get("token_ids"), list) else [],
                "settlement_shape": _string_or_none(item.get("settlement_shape")) or settlement_shape,
                "source_discovery_row_id": _string_or_none(item.get("source_discovery_row_id") or payload.get("source_discovery_row_id")),
                "settlement": {
                    "settlement_rules_text": rules_text,
                    "settlement_source_url": source_url,
                    "settlement_source_kind": "manual_fixture_source_url",
                },
                "typed_key_overrides": {
                    "asset": {"value": asset.upper(), "source": "manual_polymarket_crypto_fixture:asset"},
                    "threshold_value": {
                        "value": threshold,
                        "source": "manual_polymarket_crypto_fixture:markets.threshold",
                    },
                    "threshold_operator": {
                        "value": operator,
                        "source": "manual_polymarket_crypto_fixture:markets.operator",
                    },
                    "measurement_date": {
                        "value": measurement_date,
                        "source": "manual_polymarket_crypto_fixture:measurement_date",
                    },
                    "measurement_month": {
                        "value": measurement_month,
                        "source": "manual_polymarket_crypto_fixture:measurement_month",
                    },
                    "measurement_time": {
                        "value": measurement_time,
                        "source": "manual_polymarket_crypto_fixture:measurement_time",
                    },
                    "timezone": {
                        "value": timezone_text,
                        "source": "manual_polymarket_crypto_fixture:timezone",
                    },
                    "price_source_index": {
                        "value": source_index,
                        "source": "manual_polymarket_crypto_fixture:settlement_source",
                    },
                    "settlement_window": {
                        "value": settlement_window,
                        "source": "manual_polymarket_crypto_fixture:settlement_window",
                    },
                },
                "manual_fixture_blockers": manual_blockers,
                "max_review_readiness_tier": TIER_SETTLEMENT_SOURCE_REVIEW_READY,
                "diagnostic_only": True,
                "can_create_candidate_pair": False,
                "can_create_paper_candidate": False,
                "raw_manual_fixture_index": index,
            }
        )
    return rows


def _manual_polymarket_settlement_window(direction: str) -> str:
    if direction == "below":
        return "any Binance BTC/USDT 1-minute candle final Low during month"
    return "any Binance BTC/USDT 1-minute candle final High during month"


def _manual_polymarket_blockers(
    *,
    source_index: str | None,
    settlement_shape: str | None,
    settlement_window: str | None,
    payload_blockers: list[str],
    item_blockers: list[str],
) -> list[str]:
    blockers = list(payload_blockers) + list(item_blockers) + ["manual_fixture_not_live_market_snapshot"]
    lowered_window = (settlement_window or "").lower()
    if not source_index:
        blockers.append("missing_price_source_index")
    if "binance" in (source_index or "").lower():
        blockers.append("polymarket_binance_source_not_exact_with_kalshi_brti")
    if settlement_shape == "MONTHLY_EXTREME_HIGH_LOW" or "during month" in lowered_window:
        blockers.append("monthly_extreme_window_not_point_in_time")
    elif settlement_shape == "DEADLINE_OR_DATE_RANGE_HIT" or "deadline_or_date_range" in lowered_window:
        blockers.append("deadline_or_date_range_hit_window_not_point_in_time")
    return _unique_strings(blockers)


def _operator_for_direction(direction: str) -> str | None:
    if direction == "above":
        return ">="
    if direction == "below":
        return "<="
    return None


def _threshold_market_id_token(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return str(value).replace(".", "-")


def _safe_id_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "value"


def _skip_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    # Audit-derived outputs are skipped so each diagnostic module stands on raw snapshots
    # and never amplifies counts via cross-ingestion.
    return payload.get("source") in {
        REPORT_SOURCE,
        "venue_metadata_coverage_audit_v1",
        "settlement_evidence_burden_v1",
        "normalized_market_contract_v0",
        "normalized_market_contract_v0_coverage",
        "cross_platform_opportunity_triage_v1",
        "standardized_family_candidates_v1",
        "relative_value_ops_status_v1",
        "existing_paper_candidate_audit_v1",
        "platform_api_expansion_audit_v1",
        "polymarket_crypto_public_discovery_v1",
        "polymarket_crypto_public_discovery_raw_response_v1",
        "polymarket_crypto_public_discovery_candidate_v1",
        "polymarket_crypto_discovery_normalized_v1",
        "crypto_com_predict_cdna_research_snapshot_v1",
        "sx_bet_normalized_draft_v1",
        "sx_bet_normalized_draft_coverage_v1",
        "sx_bet_sports_typed_keys_v1",
        "sx_bet_sports_overlap_v1",
        "mlb_world_series_revival_status_v1",
        "stale_report_archive_plan_v1",
    }


def _venue_from_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    venue = _string_or_none(payload.get("venue"))
    if venue:
        return venue
    source = _string_or_none(payload.get("source"))
    if source == "kalshi_markets":
        return "kalshi"
    if source == "polymarket_gamma":
        return "polymarket"
    return None


def _load_registry(path: Path | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    if path is None:
        return [], [], None
    loaded = load_canonical_convention_registry(path)
    warnings = list(loaded.warnings)
    for invalid_entry in loaded.invalid_entries:
        warnings.append(
            {
                "source_file": str(path),
                "reason_code": "registry_entry_invalid",
                "blocker": "invalid_canonical_convention_registry_entry",
                "entry_id": invalid_entry.get("entry_id"),
                "entry_index": invalid_entry.get("index"),
                "entry_blockers": list(invalid_entry.get("blockers") or []),
            }
        )
    return loaded.valid_entries, warnings, loaded.summary


def _load_json(path: Path) -> tuple[Any, dict[str, Any] | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig")), None
    except FileNotFoundError:
        return None, {"source_file": str(path), "reason_code": "json_file_missing", "blocker": "saved_json_file_missing"}
    except json.JSONDecodeError:
        return None, {"source_file": str(path), "reason_code": "invalid_json", "blocker": "saved_json_invalid"}
    except OSError as exc:
        return None, {"source_file": str(path), "reason_code": "json_read_error", "blocker": f"saved_json_read_error:{type(exc).__name__}"}


def _summary(rows: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    unique_keys: set[tuple[str, str]] = set()
    by_family: dict[str, dict[str, int]] = defaultdict(lambda: {
        "market_count": 0,
        "family_typed_review_ready": 0,
        "settlement_source_review_ready": 0,
        "exact_payoff_review_ready": 0,
        "execution_evaluation_ready": 0,
    })
    by_burden: Counter[str] = Counter()
    by_tier: Counter[str] = Counter()
    by_quote_freshness_blocker: Counter[str] = Counter()
    for row in rows:
        family = row.get("family") or FAMILY_OTHER_UNKNOWN
        tier = row.get("review_readiness_tier") or TIER_DISCOVERY_READY
        by_burden[row.get("evidence_burden") or "UNKNOWN"] += 1
        by_tier[tier] += 1
        bucket = by_family[family]
        bucket["market_count"] += 1
        bucket["evidence_burden"] = row.get("evidence_burden") or "UNKNOWN"
        if tier == TIER_FAMILY_TYPED_REVIEW_READY:
            bucket["family_typed_review_ready"] += 1
        elif tier == TIER_SETTLEMENT_SOURCE_REVIEW_READY:
            bucket["settlement_source_review_ready"] += 1
        elif tier == TIER_EXACT_PAYOFF_REVIEW_READY:
            bucket["exact_payoff_review_ready"] += 1
        elif tier == TIER_EXECUTION_EVALUATION_READY:
            bucket["execution_evaluation_ready"] += 1
        quote_status = row.get("quote_freshness_status") if isinstance(row.get("quote_freshness_status"), dict) else {}
        blocker = _string_or_none(quote_status.get("blocker"))
        if blocker:
            by_quote_freshness_blocker[blocker] += 1
        else:
            by_quote_freshness_blocker["fresh"] += 1
        venue = row.get("venue")
        market_id = row.get("ticker") or row.get("event_id")
        if venue and market_id:
            unique_keys.add((str(venue), str(market_id)))
    return {
        "market_row_count": len(rows),
        "unique_market_count": len(unique_keys),
        "by_family": {family: dict(bucket) for family, bucket in sorted(by_family.items())},
        "by_evidence_burden": dict(sorted(by_burden.items())),
        "by_review_readiness_tier": dict(sorted(by_tier.items())),
        "by_quote_freshness_blocker": dict(sorted(by_quote_freshness_blocker.items())),
        "warning_count": len(warnings),
    }


def _venue_summaries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("venue") or "unknown")].append(row)
    output = []
    for venue, venue_rows in sorted(grouped.items()):
        tier_counts = Counter(row.get("review_readiness_tier") for row in venue_rows)
        family_counts = Counter(row.get("family") for row in venue_rows)
        output.append(
            {
                "venue": venue,
                "market_count": len(venue_rows),
                "by_review_readiness_tier": dict(sorted(tier_counts.items(), key=lambda item: item[0] or "")),
                "by_family": dict(sorted(family_counts.items(), key=lambda item: item[0] or "")),
            }
        )
    return output


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "venue": row.get("venue"),
                    "source_platform": row.get("source_platform"),
                    "access_platform": row.get("access_platform"),
                    "exchange_venue": row.get("exchange_venue"),
                    "executable_venue": row.get("executable_venue"),
                    "event_id": row.get("event_id"),
                    "ticker": row.get("ticker"),
                    "title": row.get("title"),
                    "family": row.get("family"),
                    "evidence_burden": row.get("evidence_burden"),
                    "review_readiness_tier": row.get("review_readiness_tier"),
                    "settlement_source_kind": row.get("settlement_source_kind"),
                    "settlement_source_url_present": row.get("settlement_source_url_present"),
                    "registry_match": bool(row.get("registry_match")),
                    "required_typed_keys": ";".join(row.get("required_typed_keys") or []),
                    "present_typed_keys": ";".join(row.get("present_typed_keys") or []),
                    "missing_typed_keys": ";".join(row.get("missing_typed_keys") or []),
                    "source_url_required_for_review": row.get("source_url_required_for_review"),
                    "source_url_required_for_exact_evaluator": row.get("source_url_required_for_exact_evaluator"),
                    "not_evaluator_reason": row.get("not_evaluator_reason"),
                    "quote_freshness_blocker": (row.get("quote_freshness_status") or {}).get("blocker"),
                    "blockers": ";".join(row.get("blockers") or []),
                    "source_file": row.get("source_file"),
                }
            )


def _first_match(text: str | None, pattern: re.Pattern[str]) -> str | None:
    if not text:
        return None
    match = pattern.search(text)
    return match.group(0) if match else None


def _polymarket_crypto_threshold_slug_matches(event_slug: str | None) -> bool:
    if not event_slug:
        return False
    slug = event_slug.lower().replace("_", "-")
    if not any(pattern.search(slug) for pattern in POLYMARKET_CRYPTO_THRESHOLD_SLUG_PATTERNS):
        return False
    threshold = _crypto_threshold_from_slug(slug)
    if threshold is None:
        return False
    suffix = _slug_suffix_after_threshold(slug)
    if suffix and any(token in suffix.split("-") for token in _POLYMARKET_CRYPTO_COMPOUND_SUFFIX_TOKENS):
        return False
    return True


def _slug_suffix_after_threshold(slug: str) -> str:
    match = re.search(
        r"(?:hit|reach|cross|be-above|be-below|above|below)-\$?\d+(?:\.\d+)?k?(?:-dollars?)?",
        slug,
    )
    if not match:
        return ""
    return slug[match.end() :].strip("-")


def _crypto_asset_from_slug(event_slug: str | None) -> str | None:
    if not event_slug:
        return None
    slug = event_slug.lower().replace("_", "-")
    if re.search(r"(?:^|-)(?:bitcoin|btc)(?:-|$)", slug):
        return "BTC"
    if re.search(r"(?:^|-)(?:ethereum|eth)(?:-|$)", slug):
        return "ETH"
    return None


def _crypto_threshold_from_slug(event_slug: str | None) -> float | None:
    if not event_slug:
        return None
    slug = event_slug.lower().replace("_", "-")
    patterns = (
        r"(?:above|below|over|under|greater-than|less-than|at-least|at-most|hit|reach|reaches)-\$?(\d+(?:-\d+)?(?:-?k)?)(?:-dollars?)?",
        r"\$?(\d+-?k)(?:-|$)",
        r"\$?(\d+(?:-\d+)?)-dollars?(?:-|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, slug)
        if not match:
            continue
        value = _crypto_threshold_token_to_float(match.group(1))
        if value is not None:
            return value
    return None


def _crypto_threshold_from_rules(rules_text: str | None) -> float | None:
    if not rules_text:
        return None
    operator = _OPERATOR_PATTERN.search(rules_text)
    if not operator:
        return None
    segment = rules_text[operator.end() : operator.end() + 100]
    match = re.search(
        r"\$?\s*(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\d+(?:-?k))\s*(?:dollars?|usd)?\b",
        segment,
        re.IGNORECASE,
    )
    if not match:
        return None
    return _crypto_threshold_token_to_float(match.group(1))


def _crypto_threshold_token_to_float(token: str | None) -> float | None:
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
    if re.fullmatch(r"\d+-\d{1,4}", text):
        whole, fractional = text.split("-", 1)
        try:
            return float(f"{whole}.{fractional}")
        except ValueError:
            return None
    try:
        return float(text)
    except ValueError:
        return None


def _extract_url(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"https?://[^\s<>\"')]+", value)
    if not match:
        return None
    return match.group(0).rstrip(".,;:")


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            output.append(text)
            seen.add(text)
    return output


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
