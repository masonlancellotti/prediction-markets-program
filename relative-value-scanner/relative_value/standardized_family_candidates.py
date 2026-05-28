from __future__ import annotations

import csv
import json
import re
from collections import Counter
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

from relative_value.settlement_evidence_burden import (
    FAMILY_CRYPTO_PRICE_THRESHOLD,
    FAMILY_FED_FOMC,
    REPORT_SOURCE as SETTLEMENT_BURDEN_SOURCE,
)
from relative_value.venue_identity import (
    broker_route_fake_edge_blockers,
    executable_venue_identity_from_mapping,
    same_executable_venue,
)


SCHEMA_VERSION = 1
REPORT_SOURCE = "standardized_family_candidates_v1"

SUPPORTED_FAMILIES = {FAMILY_FED_FOMC, FAMILY_CRYPTO_PRICE_THRESHOLD}
REVIEW_TYPED_KEY_MATCH = "REVIEW_TYPED_KEY_MATCH"
NEEDS_SOURCE_REGISTRY = "NEEDS_SOURCE_REGISTRY"
NEEDS_ORDERBOOK_ENRICHMENT = "NEEDS_ORDERBOOK_ENRICHMENT"
DISCOVERY_ONLY = "DISCOVERY_ONLY"
BTC_BASIS_RISK_REVIEW = "BTC_BASIS_RISK_REVIEW"
CRYPTO_RELATED_FV_WATCH = "CRYPTO_RELATED_FV_WATCH"
CRYPTO_DEADLINE_RANGE_HIT_FV_WATCH = "CRYPTO_DEADLINE_RANGE_HIT_FV_WATCH"
MANUAL_BASIS_RISK_REVIEW = "MANUAL_BASIS_RISK_REVIEW"
FAIR_VALUE_WATCH = "FAIR_VALUE_WATCH"

KNOWN_REPUTABLE_BTC_SOURCE_TOKENS = (
    # CF Benchmarks family — Kalshi BTC contracts settle on BRTI (60-second pre-time average).
    "cf benchmarks",
    "brti",
    "brr",
    "bitcoin real-time index",
    "bitcoin reference rate",
    "cme cf",
    # Lukka/ICE/Blockstream are the data agents CDNA's U-BTC midpoint methodology cites.
    # See the CDNA Bitcoin Event Contract methodology filing.
    "lukka",
    "ice cryptocurrency data",
    "ice data",
    "blockstream",
    "u-btc",
    # Spot exchanges that sometimes appear as the named reference for non-BRTI venues
    # (ForecastEx Crypto Extremes uses Coinbase 60-second VWAP per its docs).
    "coinbase",
    "binance",
    "kraken",
    "gemini",
    "bitstamp",
    "coindesk",
)

CSV_FIELDS = [
    "family",
    "typed_key",
    "venues_involved",
    "executable_venues_involved",
    "market_count",
    "cross_venue",
    "review_readiness_tiers",
    "missing_typed_keys",
    "source_url_status",
    "quote_depth_ready_count",
    "quote_depth_missing_count",
    "allowed_next_action",
    "blockers",
]

OPERATOR_PATTERN = re.compile(
    r"(?:>=|<=|>|<|\babove\b|\bbelow\b|\bgreater\s+than\b|\bless\s+than\b|\bat\s+least\b|\bat\s+most\b)",
    re.IGNORECASE,
)
TIMEZONE_PATTERN = re.compile(r"\b(EST|EDT|CST|CDT|MST|MDT|PST|PDT|UTC|GMT|ET|CT|MT|PT)\b")
TIME_PATTERN = re.compile(r"\b(\d{1,2}(?::\d{2})?\s*(?:AM|PM|a\.m\.|p\.m\.)?)\s*(EST|EDT|CST|CDT|MST|MDT|PST|PDT|UTC|GMT|ET|CT|MT|PT)\b", re.IGNORECASE)


def build_standardized_family_candidates_report(
    *,
    input_dir: Path,
    burden_report: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    payload, warning = _load_json(burden_report)
    warnings = [warning] if warning is not None else []
    markets = _burden_markets(payload)
    source_cache: dict[str, Any] = {}
    candidate_inputs = [
        _candidate_market(row, source_cache=source_cache)
        for row in markets
        if isinstance(row, dict) and row.get("family") in SUPPORTED_FAMILIES
    ]
    candidate_inputs = [row for row in candidate_inputs if row is not None]
    rows = _candidate_groups(candidate_inputs)
    pairs = _candidate_pairs(rows)
    basis_risk_rows = _btc_basis_risk_rows(candidate_inputs)
    crypto_related_fv_watch_rows = _crypto_related_fv_watch_rows(candidate_inputs)
    crypto_deadline_range_hit_fv_watch_rows = _crypto_deadline_range_hit_fv_watch_rows(candidate_inputs)
    return {
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "input_dir": str(input_dir),
        "burden_report": str(burden_report),
        "summary": _summary(
            rows,
            pairs,
            basis_risk_rows,
            crypto_related_fv_watch_rows,
            crypto_deadline_range_hit_fv_watch_rows,
            warnings,
        ),
        "rows": rows,
        "pairs": pairs,
        "basis_risk_rows": basis_risk_rows,
        "crypto_related_fv_watch_rows": crypto_related_fv_watch_rows,
        "crypto_deadline_range_hit_fv_watch_rows": crypto_deadline_range_hit_fv_watch_rows,
        "warnings": warnings,
        "safety": {
            "saved_files_only": True,
            "live_fetch_attempted": False,
            "execution_or_order_logic_added": False,
            "account_or_auth_logic_added": False,
            "paper_candidate_emitted": False,
            "affects_evaluator_gates": False,
            "same_payoff_equivalence_claimed": False,
            "exact_payoff_claimed": False,
            "title_similarity_can_fill_typed_keys": False,
            "graph_or_llm_hints_can_fill_typed_keys": False,
            "family_typed_review_can_promote_to_exact_or_execution": False,
            "basis_risk_review_can_promote_to_exact_or_execution": False,
            "crypto_related_fv_watch_can_promote_to_exact_or_execution": False,
            "crypto_deadline_range_hit_fv_watch_can_promote_to_exact_or_execution": False,
        },
    }


def write_standardized_family_candidates_files(
    *,
    input_dir: Path,
    burden_report: Path,
    json_output: Path,
    csv_output: Path | None = None,
    markdown_output: Path | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_standardized_family_candidates_report(
        input_dir=input_dir,
        burden_report=burden_report,
        generated_at=generated_at,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    if csv_output is not None:
        csv_output.parent.mkdir(parents=True, exist_ok=True)
        _write_csv(report["rows"], csv_output)
    if markdown_output is not None:
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(render_standardized_family_candidates_markdown(report), encoding="utf-8")
    return report


def render_standardized_family_candidates_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Standardized Family Candidates",
        "",
        "Saved-file-only exact-key diagnostic groups. These rows do not assert same-payoff equivalence and do not affect evaluator gates.",
        "",
        "| Family | Markets | Venues | Action | Source | Quote/depth | Blockers |",
        "|---|---:|---|---|---|---|---|",
    ]
    for row in report.get("rows") or []:
        quote = row.get("quote_depth_freshness") or {}
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("family")),
                    _md(row.get("market_count")),
                    _md(",".join(row.get("venues_involved") or [])),
                    _md(row.get("allowed_next_action")),
                    _md(row.get("source_url_status")),
                    _md(f"{quote.get('ready_count', 0)} ready / {quote.get('missing_count', 0)} missing"),
                    _md(",".join(row.get("blockers") or []) or "none"),
                ]
            )
            + " |"
        )
    basis_rows = report.get("basis_risk_rows") or []
    if basis_rows:
        summary = report.get("summary") or {}
        severity_counts = summary.get("basis_risk_severity_hint_counts") or {}
        lines.extend(
            [
                "",
                "## BTC Basis-Risk Review",
                "",
                "Rows below are fair-value diagnostics only. They are not exact-payoff evidence and do not affect evaluator gates.",
                "",
                f"- basis_risk_review_count: `{summary.get('btc_basis_risk_review_count', 0)}`",
                f"- basis_risk_discovery_count: `{summary.get('btc_basis_risk_discovery_count', 0)}`",
                f"- known_reputable_source_pair_count: `{summary.get('basis_risk_known_reputable_source_pair_count', 0)}`",
                f"- severity_hints: `{json.dumps(severity_counts, sort_keys=True)}`",
                "",
                "| Class | Severity | Sources | Windows | Markets | Action | Blockers |",
                "|---|---|---|---|---|---|---|",
            ]
        )
        for row in basis_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md(row.get("relationship_class")),
                        _md(row.get("basis_risk_severity_hint")),
                        _md(f"{row.get('source_a')} / {row.get('source_b')}"),
                        _md(f"{row.get('window_a')} / {row.get('window_b')}"),
                        _md(
                            f"{(row.get('market_a') or {}).get('ticker') or (row.get('market_a') or {}).get('market_id')}"
                            f" / {(row.get('market_b') or {}).get('ticker') or (row.get('market_b') or {}).get('market_id')}"
                        ),
                        _md(row.get("allowed_next_action")),
                        _md(",".join(row.get("blockers") or []) or "none"),
                    ]
                )
                + " |"
            )
    fv_rows = report.get("crypto_related_fv_watch_rows") or []
    if fv_rows:
        summary = report.get("summary") or {}
        lines.extend(
            [
                "",
                "## Crypto Related FV Watch",
                "",
                "Rows below are fair-value diagnostics only. Monthly-extreme and point-in-time markets are not exact same-payoff.",
                "",
                f"- crypto_related_fv_watch_rows: `{summary.get('crypto_related_fv_watch_rows', 0)}`",
                f"- by_asset: `{json.dumps(summary.get('crypto_related_fv_watch_by_asset') or {}, sort_keys=True)}`",
                "",
                "| Class | Sources | Windows | Markets | Action | Blockers |",
                "|---|---|---|---|---|---|",
            ]
        )
        for row in fv_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md(row.get("relationship_class")),
                        _md(f"{row.get('source_a')} / {row.get('source_b')}"),
                        _md(f"{row.get('window_a')} / {row.get('window_b')}"),
                        _md(
                            f"{(row.get('market_a') or {}).get('ticker') or (row.get('market_a') or {}).get('market_id')}"
                            f" / {(row.get('market_b') or {}).get('ticker') or (row.get('market_b') or {}).get('market_id')}"
                        ),
                        _md(row.get("allowed_next_action")),
                        _md(",".join(row.get("blockers") or []) or "none"),
                    ]
                )
                + " |"
            )
    deadline_fv_rows = report.get("crypto_deadline_range_hit_fv_watch_rows") or []
    if deadline_fv_rows:
        summary = report.get("summary") or {}
        lines.extend(
            [
                "",
                "## Crypto Deadline/Range-Hit FV Watch",
                "",
                "One-sided dominance diagnostic only: deadline/range-hit probability >= point-in-time probability"
                " at the same asset/threshold/direction. Not exact same-payoff. Not evaluator-eligible.",
                "",
                f"- crypto_deadline_range_hit_fv_watch_rows: `{summary.get('crypto_deadline_range_hit_fv_watch_rows', 0)}`",
                f"- by_asset: `{json.dumps(summary.get('crypto_deadline_range_hit_fv_watch_by_asset') or {}, sort_keys=True)}`",
                "",
                "| Class | Sources | Windows | Markets | Action | Blockers |",
                "|---|---|---|---|---|---|",
            ]
        )
        for row in deadline_fv_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md(row.get("relationship_class")),
                        _md(f"{row.get('source_a')} / {row.get('source_b')}"),
                        _md(f"{row.get('window_a')} / {row.get('window_b')}"),
                        _md(
                            f"{(row.get('market_a') or {}).get('ticker') or (row.get('market_a') or {}).get('market_id')}"
                            f" / {(row.get('market_b') or {}).get('ticker') or (row.get('market_b') or {}).get('market_id')}"
                        ),
                        _md(row.get("allowed_next_action")),
                        _md(",".join(row.get("blockers") or []) or "none"),
                    ]
                )
                + " |"
            )
    return "\n".join(lines) + "\n"


def _candidate_market(row: dict[str, Any], *, source_cache: dict[str, Any]) -> dict[str, Any] | None:
    family = str(row.get("family") or "")
    evidence = row.get("typed_key_evidence") if isinstance(row.get("typed_key_evidence"), dict) else {}
    rules_text = _source_rules_text(row, source_cache=source_cache)
    typed_key = _candidate_typed_key(family, evidence, rules_text)
    if typed_key is None:
        return None
    missing = list(row.get("missing_typed_keys") or [])
    market_ref = {
        "venue": row.get("venue"),
        "source_platform": row.get("source_platform"),
        "access_platform": row.get("access_platform"),
        "exchange_venue": row.get("exchange_venue"),
        "executable_venue": row.get("executable_venue") or executable_venue_identity_from_mapping(row),
        "event_id": row.get("event_id"),
        "event_ticker": row.get("event_ticker"),
        "event_slug": row.get("event_slug"),
        "market_id": row.get("market_id") or row.get("ticker"),
        "ticker": row.get("ticker"),
        "review_readiness_tier": row.get("review_readiness_tier"),
        "source_file": row.get("source_file"),
        "row_index": row.get("row_index"),
        "typed_key_evidence": _evidence_subset(evidence, typed_key),
    }
    quote = _quote_depth_status(row, source_cache=source_cache)
    return {
        "family": family,
        "typed_key": typed_key,
        "typed_key_hash": _stable_key(family, typed_key),
        "market": market_ref,
        "review_readiness_tier": row.get("review_readiness_tier"),
        "missing_typed_keys": missing,
        "source_url_present": bool(row.get("settlement_source_url_present")),
        "source_url_status": "present" if row.get("settlement_source_url_present") else "missing",
        "quote_depth": quote,
        "blockers": list(row.get("blockers") or []),
    }


def _candidate_typed_key(family: str, evidence: dict[str, Any], rules_text: str | None) -> dict[str, Any] | None:
    value = lambda key: (evidence.get(key) or {}).get("value") if isinstance(evidence.get(key), dict) else None
    if family == FAMILY_FED_FOMC:
        typed_key = {
            "meeting_date": value("meeting_date"),
            "rate_bound": value("rate_bound"),
            "threshold_percent": value("threshold_percent"),
            "comparison_operator": _comparison_operator(rules_text),
            "source_convention": value("source_convention"),
        }
    elif family == FAMILY_CRYPTO_PRICE_THRESHOLD:
        typed_key = {
            "asset": value("asset"),
            "threshold_value": value("threshold_value"),
            "threshold_operator": _comparison_operator(str(value("threshold_operator") or "")) or value("threshold_operator"),
            "measurement_date": value("measurement_date"),
            "price_source_index": value("price_source_index"),
            "timezone": _timezone(rules_text),
            "timestamp": _timestamp(rules_text),
            "settlement_window": value("settlement_window") or _settlement_window(rules_text),
        }
    else:
        return None
    if not any(item is not None for item in typed_key.values()):
        return None
    return typed_key


def _candidate_groups(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for market in markets:
        grouped.setdefault(market["typed_key_hash"], []).append(market)
    rows = []
    for key_hash, items in sorted(grouped.items(), key=lambda item: (item[1][0]["family"], item[0])):
        family = items[0]["family"]
        venues = sorted({str(item["market"].get("venue")) for item in items if item["market"].get("venue")})
        executable_venues = sorted(
            {
                identity
                for item in items
                for identity in [executable_venue_identity_from_mapping(item.get("market") or {})]
                if identity
            }
        )
        missing = sorted({key for item in items for key in item.get("missing_typed_keys") or []})
        source_url_status = _source_url_status(items)
        quote = _quote_depth_summary(items)
        blockers = _group_blockers(items, missing, source_url_status, quote)
        action = _allowed_next_action(missing, source_url_status, quote)
        tiers = Counter(str(item.get("review_readiness_tier") or "UNKNOWN") for item in items)
        rows.append(
            {
                "row_id": f"{family}:{key_hash[:16]}",
                "family": family,
                "typed_key": items[0]["typed_key"],
                "venues_involved": venues,
                "executable_venues_involved": executable_venues,
                "market_ids": [item["market"].get("market_id") for item in items],
                "tickers": [item["market"].get("ticker") for item in items],
                "markets": [item["market"] for item in items],
                "market_count": len(items),
                "cross_venue": len(executable_venues) > 1,
                "review_readiness_tiers": dict(sorted(tiers.items())),
                "missing_typed_keys": missing,
                "source_url_status": source_url_status,
                "quote_depth_freshness": quote,
                "blockers": blockers,
                "allowed_next_action": action,
                "diagnostic_only": True,
                "affects_evaluator_gates": False,
                "paper_candidate_emitted": False,
                "same_payoff_equivalence_claimed": False,
                "exact_payoff_claimed": False,
            }
        )
    return rows


def _candidate_pairs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pairs = []
    for row in rows:
        markets = row.get("markets") or []
        for left, right in combinations(markets, 2):
            if same_executable_venue(left, right):
                continue
            pairs.append(
                {
                    "family": row.get("family"),
                    "typed_key": row.get("typed_key"),
                    "left": _pair_market(left),
                    "right": _pair_market(right),
                    "allowed_next_action": row.get("allowed_next_action"),
                    "blockers": row.get("blockers"),
                    "diagnostic_only": True,
                    "affects_evaluator_gates": False,
                    "paper_candidate_emitted": False,
                    "same_payoff_equivalence_claimed": False,
                    "exact_payoff_claimed": False,
                }
            )
    return pairs


def _btc_basis_risk_rows(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    crypto = [
        market
        for market in markets
        if market.get("family") == FAMILY_CRYPTO_PRICE_THRESHOLD
        and str((market.get("typed_key") or {}).get("asset") or "").upper() == "BTC"
    ]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for market in crypto:
        key = _btc_basis_key(market.get("typed_key") or {})
        if key is not None:
            grouped.setdefault(key, []).append(market)

    rows: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, str], tuple[str, str]]] = set()
    for items in grouped.values():
        for left, right in combinations(items, 2):
            if same_executable_venue(left.get("market") or {}, right.get("market") or {}):
                continue
            pair_key = _basis_pair_key(left, right)
            if pair_key in seen:
                continue
            seen.add(pair_key)
            row = _btc_basis_risk_row(left, right)
            if row is not None:
                rows.append(row)
    return sorted(
        rows,
        key=lambda row: (
            0 if row.get("relationship_class") == BTC_BASIS_RISK_REVIEW else 1,
            str((row.get("market_a") or {}).get("venue") or ""),
            str((row.get("market_a") or {}).get("market_id") or ""),
            str((row.get("market_b") or {}).get("venue") or ""),
            str((row.get("market_b") or {}).get("market_id") or ""),
        ),
    )


def _crypto_related_fv_watch_rows(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    crypto = [
        market
        for market in markets
        if market.get("family") == FAMILY_CRYPTO_PRICE_THRESHOLD
        and str((market.get("typed_key") or {}).get("asset") or "").upper() in {"BTC", "ETH"}
    ]
    rows: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, str], tuple[str, str]]] = set()
    for left, right in combinations(crypto, 2):
        if same_executable_venue(left.get("market") or {}, right.get("market") or {}):
            continue
        pair_key = _basis_pair_key(left, right)
        if pair_key in seen:
            continue
        seen.add(pair_key)
        row = _crypto_related_fv_watch_row(left, right)
        if row is not None:
            rows.append(row)
    return sorted(
        rows,
        key=lambda row: (
            0 if row.get("relationship_class") == CRYPTO_RELATED_FV_WATCH else 1,
            str((row.get("market_a") or {}).get("venue") or ""),
            str((row.get("market_a") or {}).get("market_id") or ""),
            str((row.get("market_b") or {}).get("venue") or ""),
            str((row.get("market_b") or {}).get("market_id") or ""),
        ),
    )


def _crypto_deadline_range_hit_fv_watch_rows(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    crypto = [
        market
        for market in markets
        if market.get("family") == FAMILY_CRYPTO_PRICE_THRESHOLD
        and str((market.get("typed_key") or {}).get("asset") or "").upper() in {"BTC", "ETH"}
    ]
    rows: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, str], tuple[str, str]]] = set()
    for left, right in combinations(crypto, 2):
        if same_executable_venue(left.get("market") or {}, right.get("market") or {}):
            continue
        pair_key = _basis_pair_key(left, right)
        if pair_key in seen:
            continue
        seen.add(pair_key)
        row = _crypto_deadline_range_hit_fv_watch_row(left, right)
        if row is not None:
            rows.append(row)
    return sorted(
        rows,
        key=lambda row: (
            0 if row.get("relationship_class") == CRYPTO_DEADLINE_RANGE_HIT_FV_WATCH else 1,
            str((row.get("market_a") or {}).get("venue") or ""),
            str((row.get("market_a") or {}).get("market_id") or ""),
            str((row.get("market_b") or {}).get("venue") or ""),
            str((row.get("market_b") or {}).get("market_id") or ""),
        ),
    )


def _crypto_deadline_range_hit_fv_watch_row(
    left: dict[str, Any],
    right: dict[str, Any],
) -> dict[str, Any] | None:
    typed_left = left.get("typed_key") or {}
    typed_right = right.get("typed_key") or {}
    asset = _string_or_none(typed_left.get("asset"))
    if not asset or asset.upper() != str(typed_right.get("asset") or "").upper():
        return None
    if not _same_number(typed_left.get("threshold_value"), typed_right.get("threshold_value")):
        return None
    direction_left = _operator_direction(typed_left.get("threshold_operator"))
    direction_right = _operator_direction(typed_right.get("threshold_operator"))
    if not direction_left or direction_left != direction_right:
        return None
    window_left = _string_or_none(typed_left.get("settlement_window")) or "unknown"
    window_right = _string_or_none(typed_right.get("settlement_window")) or "unknown"
    left_range = _is_deadline_range_hit_window(window_left, left)
    right_range = _is_deadline_range_hit_window(window_right, right)
    left_point = _is_point_in_time_window(window_left)
    right_point = _is_point_in_time_window(window_right)
    # Require exactly one leg to be deadline/range-hit and the other to be point-in-time.
    if not ((left_range and right_point) or (right_range and left_point)):
        return None

    source_left = _string_or_none(typed_left.get("price_source_index"))
    source_right = _string_or_none(typed_right.get("price_source_index"))
    source_left_known = _known_reputable_btc_source(source_left) if asset.upper() == "BTC" else bool(source_left)
    source_right_known = _known_reputable_btc_source(source_right) if asset.upper() == "BTC" else bool(source_right)
    # Shape blockers always emitted; these are diagnostic, never evaluator-eligible.
    blockers: list[str] = [
        "deadline_or_date_range_hit_window_not_point_in_time",
        "not_same_payoff",
        "not_evaluator_eligible",
        "one_sided_dominance_only_deadline_hit_geq_point_in_time",
    ]
    if not source_left or not source_right or not (source_left_known and source_right_known):
        blockers.append("unknown_crypto_source")
        relationship_class = DISCOVERY_ONLY
        allowed_next_action = DISCOVERY_ONLY
        fair_value_relevance_reason = "asset_threshold_operator_align_but_source_review_missing"
    else:
        relationship_class = CRYPTO_DEADLINE_RANGE_HIT_FV_WATCH
        allowed_next_action = FAIR_VALUE_WATCH
        fair_value_relevance_reason = (
            "same_crypto_asset_threshold_direction_with_deadline_or_date_range_hit_vs_point_in_time_window"
        )

    return {
        "relationship_class": relationship_class,
        "family": FAMILY_CRYPTO_PRICE_THRESHOLD,
        "typed_key": {
            "asset": asset.upper(),
            "threshold_value": _number_or_none(typed_left.get("threshold_value")),
            "threshold_operator_a": typed_left.get("threshold_operator"),
            "threshold_operator_b": typed_right.get("threshold_operator"),
            "direction": direction_left,
            "measurement_date_or_deadline_a": typed_left.get("measurement_date"),
            "measurement_date_or_deadline_b": typed_right.get("measurement_date"),
        },
        "source_a": source_left,
        "source_b": source_right,
        "window_a": window_left,
        "window_b": window_right,
        "fair_value_relevance_reason": fair_value_relevance_reason,
        "not_exact_payoff_reason": "deadline_or_date_range_hit_vs_point_in_time",
        "dominance_hint": "deadline_or_range_hit_probability_geq_point_in_time_probability_at_same_threshold_direction",
        "allowed_next_action": allowed_next_action,
        "blockers": _unique_strings(blockers),
        "market_a": _pair_market(left.get("market") or {}),
        "market_b": _pair_market(right.get("market") or {}),
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "paper_candidate_emitted": False,
        "same_payoff_equivalence_claimed": False,
        "exact_payoff_claimed": False,
    }


def _is_deadline_range_hit_window(window: str, market: dict[str, Any]) -> bool:
    text = (window + " " + " ".join(market.get("blockers") or [])).lower()
    return (
        "deadline_or_date_range" in text
        or "deadline_or_date_range_hit_window_not_point_in_time" in text
    )


def _is_point_in_time_window(window: str | None) -> bool:
    # Treat all single-tick / 60-second-average settlements as point-in-time for the
    # FV-watch dominance diagnostic. Kalshi BRTI markets surface "60_seconds_preceding"
    # and "instant_tick" via standardized_family_candidates._settlement_window; the
    # Polymarket normalizer surfaces "point_in_time" for explicit point-in-time fixtures.
    text = _string_or_none(window)
    if text is None:
        return False
    return text in {"point_in_time", "60_seconds_preceding", "instant_tick"}


def _crypto_related_fv_watch_row(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any] | None:
    typed_left = left.get("typed_key") or {}
    typed_right = right.get("typed_key") or {}
    asset = _string_or_none(typed_left.get("asset"))
    if not asset or asset.upper() != str(typed_right.get("asset") or "").upper():
        return None
    if not _same_number(typed_left.get("threshold_value"), typed_right.get("threshold_value")):
        return None
    direction_left = _operator_direction(typed_left.get("threshold_operator"))
    direction_right = _operator_direction(typed_right.get("threshold_operator"))
    if not direction_left or direction_left != direction_right:
        return None
    window_left = _string_or_none(typed_left.get("settlement_window")) or "unknown"
    window_right = _string_or_none(typed_right.get("settlement_window")) or "unknown"
    left_monthly = _is_monthly_extreme_window(window_left, left)
    right_monthly = _is_monthly_extreme_window(window_right, right)
    if left_monthly == right_monthly:
        return None
    if not _monthly_window_contains_point_date(
        monthly_typed=typed_left if left_monthly else typed_right,
        point_typed=typed_right if left_monthly else typed_left,
    ):
        return None

    source_left = _string_or_none(typed_left.get("price_source_index"))
    source_right = _string_or_none(typed_right.get("price_source_index"))
    source_left_known = _known_reputable_btc_source(source_left) if asset.upper() == "BTC" else bool(source_left)
    source_right_known = _known_reputable_btc_source(source_right) if asset.upper() == "BTC" else bool(source_right)
    blockers = [
        "monthly_extreme_window_not_point_in_time",
        "not_same_payoff",
        "not_evaluator_eligible",
    ]
    if not source_left or not source_right or not (source_left_known and source_right_known):
        blockers.append("unknown_crypto_source")
        relationship_class = DISCOVERY_ONLY
        allowed_next_action = DISCOVERY_ONLY
        fair_value_relevance_reason = "asset_threshold_operator_align_but_source_review_missing"
    else:
        relationship_class = CRYPTO_RELATED_FV_WATCH
        allowed_next_action = FAIR_VALUE_WATCH
        fair_value_relevance_reason = "same_crypto_asset_threshold_direction_with_monthly_extreme_vs_point_in_time_window"

    return {
        "relationship_class": relationship_class,
        "family": FAMILY_CRYPTO_PRICE_THRESHOLD,
        "typed_key": {
            "asset": asset.upper(),
            "threshold_value": _number_or_none(typed_left.get("threshold_value")),
            "threshold_operator_a": typed_left.get("threshold_operator"),
            "threshold_operator_b": typed_right.get("threshold_operator"),
            "direction": direction_left,
            "measurement_date_or_month_a": typed_left.get("measurement_date"),
            "measurement_date_or_month_b": typed_right.get("measurement_date"),
        },
        "source_a": source_left,
        "source_b": source_right,
        "window_a": window_left,
        "window_b": window_right,
        "fair_value_relevance_reason": fair_value_relevance_reason,
        "not_exact_payoff_reason": "monthly_extreme_vs_point_in_time",
        "allowed_next_action": allowed_next_action,
        "blockers": _unique_strings(blockers),
        "market_a": _pair_market(left.get("market") or {}),
        "market_b": _pair_market(right.get("market") or {}),
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "paper_candidate_emitted": False,
        "same_payoff_equivalence_claimed": False,
        "exact_payoff_claimed": False,
    }


def _btc_basis_risk_row(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any] | None:
    typed_left = left.get("typed_key") or {}
    typed_right = right.get("typed_key") or {}
    source_a = _string_or_none(typed_left.get("price_source_index"))
    source_b = _string_or_none(typed_right.get("price_source_index"))
    window_a = _string_or_none(typed_left.get("settlement_window")) or "unknown"
    window_b = _string_or_none(typed_right.get("settlement_window")) or "unknown"
    timestamp_a = _string_or_none(typed_left.get("timestamp"))
    timestamp_b = _string_or_none(typed_right.get("timestamp"))
    blockers: list[str] = []

    if not timestamp_a or not timestamp_b:
        blockers.append("missing_measurement_time")
    elif _normalized_time(timestamp_a) != _normalized_time(timestamp_b):
        blockers.append("measurement_time_mismatch")

    source_a_known = _known_reputable_btc_source(source_a)
    source_b_known = _known_reputable_btc_source(source_b)
    if not source_a or not source_b:
        blockers.append("unknown_btc_source")
    elif not source_a_known or not source_b_known:
        blockers.append("btc_source_not_known_reputable")

    if source_a and source_b and _normalized_source(source_a) == _normalized_source(source_b):
        if _normalized_window(window_a) == _normalized_window(window_b):
            return None
        blockers.append("settlement_window_mismatch")

    relationship_class = BTC_BASIS_RISK_REVIEW if not blockers else DISCOVERY_ONLY
    if relationship_class == BTC_BASIS_RISK_REVIEW:
        basis_risk_reason = "same_btc_threshold_operator_date_time_with_different_known_reputable_sources"
        not_exact = "settlement_source_or_window_differs; exact review requires same source, same window, same operator, same date/time, and same threshold"
        action = MANUAL_BASIS_RISK_REVIEW
    else:
        basis_risk_reason = "basis_risk_review_blocked_until_source_and_timing_are_explicit"
        not_exact = "unclear source or mismatched timing blocks exact and basis-risk review"
        action = DISCOVERY_ONLY

    severity_hint = _basis_risk_severity_hint(
        relationship_class=relationship_class,
        source_a=source_a,
        source_b=source_b,
        window_a=window_a,
        window_b=window_b,
        source_a_known=source_a_known,
        source_b_known=source_b_known,
        blockers=blockers,
    )

    return {
        "relationship_class": relationship_class,
        "family": FAMILY_CRYPTO_PRICE_THRESHOLD,
        "typed_key": {
            "asset": typed_left.get("asset"),
            "threshold_value": typed_left.get("threshold_value"),
            "threshold_operator": typed_left.get("threshold_operator"),
            "measurement_date": typed_left.get("measurement_date"),
            "timestamp_a": timestamp_a,
            "timestamp_b": timestamp_b,
        },
        "source_a": source_a,
        "source_b": source_b,
        "window_a": window_a,
        "window_b": window_b,
        "source_pair_known_reputable": bool(source_a_known and source_b_known),
        "basis_risk_severity_hint": severity_hint,
        "basis_risk_reason": basis_risk_reason,
        "not_exact_payoff_reason": not_exact,
        "allowed_next_action": action,
        "blockers": _unique_strings(blockers),
        "market_a": _pair_market(left.get("market") or {}),
        "market_b": _pair_market(right.get("market") or {}),
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "paper_candidate_emitted": False,
        "same_payoff_equivalence_claimed": False,
        "exact_payoff_claimed": False,
    }


def _basis_risk_severity_hint(
    *,
    relationship_class: str,
    source_a: str | None,
    source_b: str | None,
    window_a: str,
    window_b: str,
    source_a_known: bool,
    source_b_known: bool,
    blockers: list[str],
) -> str:
    # Diagnostic-only severity hint. Never used to upgrade exact/evaluator tiers.
    # Coarse buckets:
    #   - "high_unreviewed": missing source or unknown source on either side
    #   - "moderate_window_mismatch": same source but different windows
    #   - "moderate_known_different_sources_same_window": two reputable sources w/
    #     compatible window length (commonly 60-second pre-time average)
    #   - "low_same_source_same_window": same source + same window (no row emitted,
    #     but included here for completeness)
    if relationship_class == DISCOVERY_ONLY:
        return "high_unreviewed"
    if not (source_a_known and source_b_known):
        return "high_unreviewed"
    normalized_a = _normalized_source(source_a)
    normalized_b = _normalized_source(source_b)
    if normalized_a == normalized_b:
        if _normalized_window(window_a) == _normalized_window(window_b):
            return "low_same_source_same_window"
        return "moderate_window_mismatch"
    if _normalized_window(window_a) == _normalized_window(window_b) and window_a != "unknown":
        return "moderate_known_different_sources_same_window"
    return "high_unreviewed"


def _btc_basis_key(typed_key: dict[str, Any]) -> str | None:
    required = {
        "asset": _string_or_none(typed_key.get("asset")),
        "threshold_value": typed_key.get("threshold_value"),
        "threshold_operator": _string_or_none(typed_key.get("threshold_operator")),
        "measurement_date": _string_or_none(typed_key.get("measurement_date")),
    }
    if any(value is None for value in required.values()):
        return None
    return json.dumps(required, sort_keys=True, separators=(",", ":"))


def _basis_pair_key(left: dict[str, Any], right: dict[str, Any]) -> tuple[tuple[str, str], tuple[str, str]]:
    def leg(item: dict[str, Any]) -> tuple[str, str]:
        market = item.get("market") or {}
        venue_identity = executable_venue_identity_from_mapping(market) or market.get("venue")
        return (str(venue_identity or ""), str(market.get("market_id") or market.get("ticker") or ""))

    return tuple(sorted((leg(left), leg(right))))  # type: ignore[return-value]


def _operator_direction(value: Any) -> str | None:
    normalized = _comparison_operator(str(value or "")) or _string_or_none(value)
    if normalized in {">", ">=", "above", "greater_than", "at_least"}:
        return "above"
    if normalized in {"<", "<=", "below", "less_than", "at_most"}:
        return "below"
    return None


def _is_monthly_extreme_window(window: str, market: dict[str, Any]) -> bool:
    text = (window + " " + " ".join(market.get("blockers") or [])).lower()
    return "monthly_extreme" in text or ("during month" in text and ("final high" in text or "final low" in text))


def _monthly_window_contains_point_date(*, monthly_typed: dict[str, Any], point_typed: dict[str, Any]) -> bool:
    month_key = _month_key(monthly_typed.get("measurement_date"))
    point_month = _month_key(point_typed.get("measurement_date"))
    return bool(month_key and point_month and month_key == point_month)


def _month_key(value: Any) -> str | None:
    text = _string_or_none(value)
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}", text):
        return text
    cleaned = re.sub(r"\b(\d{1,2})(?:st|nd|rd|th)\b", r"\1", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned.replace(",", " ")).strip()
    for fmt in ("%b %d %Y", "%B %d %Y", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(cleaned, fmt)
            return f"{parsed.year:04d}-{parsed.month:02d}"
        except ValueError:
            continue
    return None


def _same_number(left: Any, right: Any) -> bool:
    left_number = _number_or_none(left)
    right_number = _number_or_none(right)
    if left_number is None or right_number is None:
        return False
    return abs(left_number - right_number) < 0.000001


def _pair_market(market: dict[str, Any]) -> dict[str, Any]:
    return {
        "venue": market.get("venue"),
        "source_platform": market.get("source_platform"),
        "access_platform": market.get("access_platform"),
        "exchange_venue": market.get("exchange_venue"),
        "executable_venue": market.get("executable_venue") or executable_venue_identity_from_mapping(market),
        "market_id": market.get("market_id"),
        "ticker": market.get("ticker"),
        "review_readiness_tier": market.get("review_readiness_tier"),
        "source_file": market.get("source_file"),
        "row_index": market.get("row_index"),
    }


def _group_blockers(
    items: list[dict[str, Any]],
    missing: list[str],
    source_url_status: str,
    quote: dict[str, Any],
) -> list[str]:
    blockers = []
    if missing:
        blockers.append("missing_required_typed_keys")
        blockers.extend(f"missing_typed_key:{key}" for key in missing)
    if source_url_status != "all_present":
        blockers.append("needs_source_registry")
    if quote.get("missing_count"):
        blockers.append("needs_orderbook_enrichment")
    for item in items:
        for blocker in item.get("blockers") or []:
            blockers.append(blocker)
    for left, right in combinations([item.get("market") or {} for item in items], 2):
        blockers.extend(broker_route_fake_edge_blockers(left, right))
    return _unique_strings(blockers)


def _allowed_next_action(missing: list[str], source_url_status: str, quote: dict[str, Any]) -> str:
    if missing:
        return DISCOVERY_ONLY
    if source_url_status != "all_present":
        return NEEDS_SOURCE_REGISTRY
    if quote.get("missing_count"):
        return NEEDS_ORDERBOOK_ENRICHMENT
    return REVIEW_TYPED_KEY_MATCH


def _source_url_status(items: list[dict[str, Any]]) -> str:
    present = sum(1 for item in items if item.get("source_url_present"))
    if present == len(items):
        return "all_present"
    if present == 0:
        return "all_missing"
    return "mixed"


def _quote_depth_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    ready = [item for item in items if (item.get("quote_depth") or {}).get("quote_depth_ready") is True]
    missing = [item for item in items if (item.get("quote_depth") or {}).get("quote_depth_ready") is not True]
    captured = [
        (item.get("quote_depth") or {}).get("captured_at")
        for item in items
        if (item.get("quote_depth") or {}).get("captured_at")
    ]
    return {
        "ready_count": len(ready),
        "missing_count": len(missing),
        "captured_at_values": sorted(set(captured)),
    }


def _quote_depth_status(row: dict[str, Any], *, source_cache: dict[str, Any]) -> dict[str, Any]:
    raw = _source_row(row, source_cache=source_cache)
    if not isinstance(raw, dict):
        return {"quote_depth_ready": False, "captured_at": None, "source": "unavailable"}
    readiness = raw.get("readiness") if isinstance(raw.get("readiness"), dict) else {}
    if "quote_depth_ready" in readiness:
        quote_depth = raw.get("quote_depth") if isinstance(raw.get("quote_depth"), dict) else {}
        return {
            "quote_depth_ready": bool(readiness.get("quote_depth_ready")),
            "captured_at": quote_depth.get("captured_at"),
            "source": "normalized_market_v0",
        }
    enrichment = raw.get("orderbook_enrichment") if isinstance(raw.get("orderbook_enrichment"), dict) else {}
    captured_at = enrichment.get("orderbook_captured_at") or enrichment.get("orderbookCapturedAt")
    ready = bool(
        captured_at
        and _number_or_none(enrichment.get("best_bid")) is not None
        and _number_or_none(enrichment.get("best_ask")) is not None
        and (
            _number_or_none(enrichment.get("depth_at_best_bid")) is not None
            or _number_or_none(enrichment.get("depth_at_best_ask")) is not None
        )
    )
    return {"quote_depth_ready": ready, "captured_at": captured_at, "source": "source_row_orderbook_enrichment"}


def _source_rules_text(row: dict[str, Any], *, source_cache: dict[str, Any]) -> str | None:
    raw = _source_row(row, source_cache=source_cache)
    if not isinstance(raw, dict):
        return None
    settlement = raw.get("settlement") if isinstance(raw.get("settlement"), dict) else {}
    raw_nested = raw.get("raw") if isinstance(raw.get("raw"), dict) else {}
    for mapping in (settlement, raw, raw_nested):
        for key in ("settlement_rules_text", "settlement_rules", "rules", "rules_primary", "rules_secondary", "resolution_text", "resolution_criteria"):
            text = _string_or_none(mapping.get(key))
            if text:
                return text
    return None


def _source_row(row: dict[str, Any], *, source_cache: dict[str, Any]) -> Any:
    source_file = _string_or_none(row.get("source_file"))
    row_index = row.get("row_index")
    if source_file is None or row_index is None:
        return None
    payload = source_cache.get(source_file)
    if payload is None:
        loaded, warning = _load_json(Path(source_file))
        payload = loaded if warning is None else {}
        source_cache[source_file] = payload
    rows = _market_objects(payload)
    try:
        return rows[int(row_index)]
    except (TypeError, ValueError, IndexError):
        return None


def _market_objects(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("normalized_markets", "markets", "records"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _burden_markets(payload: Any) -> list[Any]:
    if not isinstance(payload, dict):
        return []
    if payload.get("source") != SETTLEMENT_BURDEN_SOURCE:
        return []
    markets = payload.get("markets")
    return markets if isinstance(markets, list) else []


def _evidence_subset(evidence: dict[str, Any], typed_key: dict[str, Any]) -> dict[str, Any]:
    return {key: evidence.get(key) for key in typed_key if key in evidence}


def _comparison_operator(text: str | None) -> str | None:
    if not text:
        return None
    match = OPERATOR_PATTERN.search(text)
    if not match:
        return None
    token = match.group(0).lower().replace(" ", "_")
    if token in {"above", "greater_than", ">"}:
        return ">"
    if token in {"below", "less_than", "<"}:
        return "<"
    if token in {"at_least", ">="}:
        return ">="
    if token in {"at_most", "<="}:
        return "<="
    return token


def _timezone(text: str | None) -> str | None:
    if not text:
        return None
    match = TIMEZONE_PATTERN.search(text)
    return match.group(1) if match else None


def _timestamp(text: str | None) -> str | None:
    if not text:
        return None
    match = TIME_PATTERN.search(text)
    if not match:
        return None
    return " ".join(part.strip().upper().replace(".", "") for part in match.groups())


def _settlement_window(text: str | None) -> str | None:
    if not text:
        return None
    lowered = text.lower()
    if re.search(r"\b(?:60\s*seconds?|sixty\s*seconds?|one\s*minute|1\s*minute|minute)\b", lowered):
        return "60_seconds_preceding"
    if re.search(r"\b(?:single\s+tick|instant(?:aneous)?|at\s+exactly|5:00:00)\b", lowered):
        return "instant_tick"
    return None


def _known_reputable_btc_source(value: str | None) -> bool:
    normalized = _normalized_source(value)
    return bool(normalized and any(token in normalized for token in KNOWN_REPUTABLE_BTC_SOURCE_TOKENS))


def _normalized_source(value: str | None) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _normalized_time(value: str | None) -> str:
    text = str(value or "").strip().upper().replace(".", "")
    return re.sub(r"\s+", " ", text)


def _normalized_window(value: str | None) -> str:
    return str(value or "").strip().lower()


def _stable_key(family: str, typed_key: dict[str, Any]) -> str:
    return json.dumps({"family": family, "typed_key": typed_key}, sort_keys=True, separators=(",", ":"))


def _summary(
    rows: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
    basis_risk_rows: list[dict[str, Any]],
    crypto_related_fv_watch_rows: list[dict[str, Any]],
    crypto_deadline_range_hit_fv_watch_rows: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    by_family = Counter(str(row.get("family")) for row in rows)
    by_action = Counter(str(row.get("allowed_next_action")) for row in rows)
    blockers = Counter(blocker for row in rows for blocker in row.get("blockers") or [])
    cross_by_family = Counter(str(row.get("family")) for row in rows if row.get("cross_venue"))
    basis_by_class = Counter(str(row.get("relationship_class")) for row in basis_risk_rows)
    basis_by_action = Counter(str(row.get("allowed_next_action")) for row in basis_risk_rows)
    basis_by_severity = Counter(str(row.get("basis_risk_severity_hint") or "unknown") for row in basis_risk_rows)
    basis_known_reputable_pairs = sum(1 for row in basis_risk_rows if row.get("source_pair_known_reputable"))
    fv_by_class = Counter(str(row.get("relationship_class")) for row in crypto_related_fv_watch_rows)
    fv_watch_only = [row for row in crypto_related_fv_watch_rows if row.get("relationship_class") == CRYPTO_RELATED_FV_WATCH]
    fv_by_asset = Counter(str((row.get("typed_key") or {}).get("asset") or "unknown") for row in fv_watch_only)
    deadline_by_class = Counter(
        str(row.get("relationship_class")) for row in crypto_deadline_range_hit_fv_watch_rows
    )
    deadline_watch_only = [
        row
        for row in crypto_deadline_range_hit_fv_watch_rows
        if row.get("relationship_class") == CRYPTO_DEADLINE_RANGE_HIT_FV_WATCH
    ]
    deadline_by_asset = Counter(
        str((row.get("typed_key") or {}).get("asset") or "unknown") for row in deadline_watch_only
    )
    for row in basis_risk_rows:
        for blocker in row.get("blockers") or []:
            blockers[blocker] += 1
    for row in crypto_related_fv_watch_rows:
        for blocker in row.get("blockers") or []:
            blockers[blocker] += 1
    for row in crypto_deadline_range_hit_fv_watch_rows:
        for blocker in row.get("blockers") or []:
            blockers[blocker] += 1
    return {
        "candidate_group_count": len(rows),
        "candidate_pair_count": len(pairs),
        "basis_risk_row_count": len(basis_risk_rows),
        "btc_basis_risk_review_count": basis_by_class.get(BTC_BASIS_RISK_REVIEW, 0),
        "btc_basis_risk_discovery_count": basis_by_class.get(DISCOVERY_ONLY, 0),
        "cross_venue_candidate_group_count": sum(1 for row in rows if row.get("cross_venue")),
        "cross_venue_candidate_pair_count": len(pairs),
        "candidate_counts_by_family": dict(sorted(by_family.items())),
        "cross_venue_candidate_counts_by_family": dict(sorted(cross_by_family.items())),
        "allowed_next_action_counts": dict(sorted(by_action.items())),
        "basis_risk_relationship_class_counts": dict(sorted(basis_by_class.items())),
        "basis_risk_allowed_next_action_counts": dict(sorted(basis_by_action.items())),
        "basis_risk_severity_hint_counts": dict(sorted(basis_by_severity.items())),
        "basis_risk_known_reputable_source_pair_count": basis_known_reputable_pairs,
        "crypto_related_fv_watch_rows": fv_by_class.get(CRYPTO_RELATED_FV_WATCH, 0),
        "crypto_related_fv_watch_row_count": len(crypto_related_fv_watch_rows),
        "crypto_related_fv_watch_by_asset": dict(sorted(fv_by_asset.items())),
        "crypto_related_fv_watch_relationship_class_counts": dict(sorted(fv_by_class.items())),
        "crypto_deadline_range_hit_fv_watch_rows": deadline_by_class.get(CRYPTO_DEADLINE_RANGE_HIT_FV_WATCH, 0),
        "crypto_deadline_range_hit_fv_watch_row_count": len(crypto_deadline_range_hit_fv_watch_rows),
        "crypto_deadline_range_hit_fv_watch_by_asset": dict(sorted(deadline_by_asset.items())),
        "crypto_deadline_range_hit_fv_watch_relationship_class_counts": dict(sorted(deadline_by_class.items())),
        "manual_registry_review_ready_count": by_action.get(NEEDS_SOURCE_REGISTRY, 0),
        "review_typed_key_match_ready_count": by_action.get(REVIEW_TYPED_KEY_MATCH, 0),
        "top_blockers": [{"blocker": key, "count": value} for key, value in blockers.most_common(10)],
        "paper_candidate_count": 0,
        "warning_count": len(warnings),
    }


def _write_csv(rows: list[dict[str, Any]], csv_output: Path) -> None:
    with csv_output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            quote = row.get("quote_depth_freshness") or {}
            writer.writerow(
                {
                    "family": row.get("family"),
                    "typed_key": json.dumps(row.get("typed_key"), sort_keys=True),
                    "venues_involved": ";".join(row.get("venues_involved") or []),
                    "executable_venues_involved": ";".join(row.get("executable_venues_involved") or []),
                    "market_count": row.get("market_count"),
                    "cross_venue": row.get("cross_venue"),
                    "review_readiness_tiers": json.dumps(row.get("review_readiness_tiers"), sort_keys=True),
                    "missing_typed_keys": ";".join(row.get("missing_typed_keys") or []),
                    "source_url_status": row.get("source_url_status"),
                    "quote_depth_ready_count": quote.get("ready_count"),
                    "quote_depth_missing_count": quote.get("missing_count"),
                    "allowed_next_action": row.get("allowed_next_action"),
                    "blockers": ";".join(row.get("blockers") or []),
                }
            )


def _load_json(path: Path) -> tuple[Any, dict[str, Any] | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig")), None
    except FileNotFoundError:
        return None, {"source_file": str(path), "reason_code": "json_file_missing", "blocker": "saved_json_file_missing"}
    except json.JSONDecodeError:
        return None, {"source_file": str(path), "reason_code": "invalid_json", "blocker": "saved_json_invalid"}
    except OSError as exc:
        return None, {"source_file": str(path), "reason_code": "json_read_error", "blocker": f"saved_json_read_error:{type(exc).__name__}"}


def _number_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _unique_strings(values: Any) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _string_or_none(value)
        if text is None or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
