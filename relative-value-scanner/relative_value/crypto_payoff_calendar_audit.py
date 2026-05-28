"""Saved-file-only crypto payoff-calendar ontology audit.

Reads saved Kalshi, Polymarket, and CDNA crypto rows; classifies each row into a
*payoff-calendar shape* (e.g. ``daily_5pm_price_threshold``,
``hourly_point_in_time_price``, ``intraday_touch_threshold``,
``daily_direction_up_down``, etc.); applies a conservative cross-venue
compatibility matrix; and tags every row with a ``comparability_class``
(``exact_shape_possible`` / ``basis_risk_only`` / ``reference_only`` /
``manual_rules_needed`` / ``no_current_peer``).

Hard safety constraints respected by this module:
- Saved files only. No live API calls, no auth, no orders, no fills, no
  cancels, no account/balance/positions, no wallet/signing/private keys, no
  browser automation, no geolocation / proxy / VPN / Tor / Cloudflare bypass.
- Never claims exact same-payoff equivalence with a peer; every output is a
  *diagnostic* compatibility hint, never a candidate pair.
- Never emits a paper-candidate or exact-ready row. Evaluator/exact gates are
  unaffected.
- Never treats "hit X by DATE" / "touch X by DATE" / "up or down today" as
  point-in-time. Touch and direction shapes always carry a basis-risk-only or
  manual-rules-needed comparability class versus close-price shapes.
- Never infers bid/ask from midpoint, last, probability, title, or complement
  math.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
SCHEMA_KIND = "crypto_payoff_calendar_audit_v1"
REPORT_SOURCE = "crypto_payoff_calendar_audit_v1"

KALSHI_AUDIT_INPUT = "kalshi_crypto_typed_key_audit.json"
POLYMARKET_ENRICHED_INPUT = "polymarket_taxonomy_shape_scout_enriched.json"
POLYMARKET_PIT_AUDIT_INPUT = "polymarket_point_in_time_typed_key_audit.json"
CDNA_SNAPSHOT_INPUT = "crypto_com_predict_cdna_research_snapshot.json"
CDNA_BASIS_INPUT = "cdna_crypto_basis_risk_scout.json"
CORE_TRIO_INPUT = "core_trio_peer_coverage_audit.json"


# ---------------------------------------------------------------------------
# Payoff-calendar shape vocabulary
# ---------------------------------------------------------------------------


SHAPE_POINT_IN_TIME_PRICE_THRESHOLD = "point_in_time_price_threshold"
SHAPE_POINT_IN_TIME_PRICE_RANGE = "point_in_time_price_range"
SHAPE_HOURLY_POINT_IN_TIME_PRICE = "hourly_point_in_time_price"
SHAPE_DAILY_CLOSE_PRICE_THRESHOLD = "daily_close_price_threshold"
SHAPE_DAILY_5PM_PRICE_THRESHOLD = "daily_5pm_price_threshold"
SHAPE_WEEKLY_FRIDAY_CLOSE_THRESHOLD = "weekly_friday_close_threshold"
SHAPE_INTRADAY_TOUCH_THRESHOLD = "intraday_touch_threshold"
SHAPE_DEADLINE_TOUCH_THRESHOLD = "deadline_touch_threshold"
SHAPE_ALL_TIME_HIGH_BY_DATE = "all_time_high_by_date"
SHAPE_DAILY_DIRECTION_UP_DOWN = "daily_direction_up_down"
SHAPE_RANGE_BUCKET_AT_TIME = "range_bucket_at_time"
SHAPE_AMBIGUOUS = "ambiguous"

PAYOFF_SHAPES: tuple[str, ...] = (
    SHAPE_POINT_IN_TIME_PRICE_THRESHOLD,
    SHAPE_POINT_IN_TIME_PRICE_RANGE,
    SHAPE_HOURLY_POINT_IN_TIME_PRICE,
    SHAPE_DAILY_CLOSE_PRICE_THRESHOLD,
    SHAPE_DAILY_5PM_PRICE_THRESHOLD,
    SHAPE_WEEKLY_FRIDAY_CLOSE_THRESHOLD,
    SHAPE_INTRADAY_TOUCH_THRESHOLD,
    SHAPE_DEADLINE_TOUCH_THRESHOLD,
    SHAPE_ALL_TIME_HIGH_BY_DATE,
    SHAPE_DAILY_DIRECTION_UP_DOWN,
    SHAPE_RANGE_BUCKET_AT_TIME,
    SHAPE_AMBIGUOUS,
)


# ---------------------------------------------------------------------------
# Comparability classes
# ---------------------------------------------------------------------------


CLASS_EXACT_SHAPE_POSSIBLE = "exact_shape_possible"
CLASS_BASIS_RISK_ONLY = "basis_risk_only"
CLASS_REFERENCE_ONLY = "reference_only"
CLASS_MANUAL_RULES_NEEDED = "manual_rules_needed"
CLASS_NO_CURRENT_PEER = "no_current_peer"

COMPARABILITY_CLASSES: tuple[str, ...] = (
    CLASS_EXACT_SHAPE_POSSIBLE,
    CLASS_BASIS_RISK_ONLY,
    CLASS_REFERENCE_ONLY,
    CLASS_MANUAL_RULES_NEEDED,
    CLASS_NO_CURRENT_PEER,
)


# Ordering for "best-of" comparability resolution per row.
_CLASS_PRIORITY: dict[str, int] = {
    CLASS_EXACT_SHAPE_POSSIBLE: 0,
    CLASS_BASIS_RISK_ONLY: 1,
    CLASS_MANUAL_RULES_NEEDED: 2,
    CLASS_REFERENCE_ONLY: 3,
    CLASS_NO_CURRENT_PEER: 4,
}


# Asymmetric reading: compatibility[(my_shape, peer_shape)] = class.
# Same-shape pairings live on the diagonal; everything else is the lookup.
_COMPATIBILITY: dict[tuple[str, str], str] = {
    # Same-shape pairings (still need rules / source / time / threshold to align).
    (SHAPE_DAILY_5PM_PRICE_THRESHOLD, SHAPE_DAILY_5PM_PRICE_THRESHOLD): CLASS_EXACT_SHAPE_POSSIBLE,
    (SHAPE_DAILY_CLOSE_PRICE_THRESHOLD, SHAPE_DAILY_CLOSE_PRICE_THRESHOLD): CLASS_EXACT_SHAPE_POSSIBLE,
    (SHAPE_POINT_IN_TIME_PRICE_THRESHOLD, SHAPE_POINT_IN_TIME_PRICE_THRESHOLD): CLASS_EXACT_SHAPE_POSSIBLE,
    (SHAPE_HOURLY_POINT_IN_TIME_PRICE, SHAPE_HOURLY_POINT_IN_TIME_PRICE): CLASS_EXACT_SHAPE_POSSIBLE,
    (SHAPE_RANGE_BUCKET_AT_TIME, SHAPE_RANGE_BUCKET_AT_TIME): CLASS_EXACT_SHAPE_POSSIBLE,
    (SHAPE_ALL_TIME_HIGH_BY_DATE, SHAPE_ALL_TIME_HIGH_BY_DATE): CLASS_EXACT_SHAPE_POSSIBLE,
    (SHAPE_INTRADAY_TOUCH_THRESHOLD, SHAPE_INTRADAY_TOUCH_THRESHOLD): CLASS_EXACT_SHAPE_POSSIBLE,
    (SHAPE_DEADLINE_TOUCH_THRESHOLD, SHAPE_DEADLINE_TOUCH_THRESHOLD): CLASS_EXACT_SHAPE_POSSIBLE,
    (SHAPE_DAILY_DIRECTION_UP_DOWN, SHAPE_DAILY_DIRECTION_UP_DOWN): CLASS_MANUAL_RULES_NEEDED,
    (SHAPE_WEEKLY_FRIDAY_CLOSE_THRESHOLD, SHAPE_WEEKLY_FRIDAY_CLOSE_THRESHOLD): CLASS_EXACT_SHAPE_POSSIBLE,
    (SHAPE_POINT_IN_TIME_PRICE_RANGE, SHAPE_POINT_IN_TIME_PRICE_RANGE): CLASS_EXACT_SHAPE_POSSIBLE,
    # Close-vs-close cross-shape pairs.
    (SHAPE_DAILY_5PM_PRICE_THRESHOLD, SHAPE_POINT_IN_TIME_PRICE_THRESHOLD): CLASS_EXACT_SHAPE_POSSIBLE,
    (SHAPE_POINT_IN_TIME_PRICE_THRESHOLD, SHAPE_DAILY_5PM_PRICE_THRESHOLD): CLASS_EXACT_SHAPE_POSSIBLE,
    (SHAPE_DAILY_5PM_PRICE_THRESHOLD, SHAPE_DAILY_CLOSE_PRICE_THRESHOLD): CLASS_EXACT_SHAPE_POSSIBLE,
    (SHAPE_DAILY_CLOSE_PRICE_THRESHOLD, SHAPE_DAILY_5PM_PRICE_THRESHOLD): CLASS_EXACT_SHAPE_POSSIBLE,
    (SHAPE_WEEKLY_FRIDAY_CLOSE_THRESHOLD, SHAPE_DAILY_5PM_PRICE_THRESHOLD): CLASS_EXACT_SHAPE_POSSIBLE,
    (SHAPE_DAILY_5PM_PRICE_THRESHOLD, SHAPE_WEEKLY_FRIDAY_CLOSE_THRESHOLD): CLASS_EXACT_SHAPE_POSSIBLE,
    (SHAPE_WEEKLY_FRIDAY_CLOSE_THRESHOLD, SHAPE_POINT_IN_TIME_PRICE_THRESHOLD): CLASS_EXACT_SHAPE_POSSIBLE,
    (SHAPE_POINT_IN_TIME_PRICE_THRESHOLD, SHAPE_WEEKLY_FRIDAY_CLOSE_THRESHOLD): CLASS_EXACT_SHAPE_POSSIBLE,
    # Hourly pairings.
    (SHAPE_HOURLY_POINT_IN_TIME_PRICE, SHAPE_POINT_IN_TIME_PRICE_THRESHOLD): CLASS_EXACT_SHAPE_POSSIBLE,
    (SHAPE_POINT_IN_TIME_PRICE_THRESHOLD, SHAPE_HOURLY_POINT_IN_TIME_PRICE): CLASS_EXACT_SHAPE_POSSIBLE,
    # Range bucket vs daily 5pm — the bucket lower/upper bounds *imply* a
    # threshold-style payoff at the same observation time, but only manual rules
    # review can confirm that the bucket math matches the threshold.
    (SHAPE_RANGE_BUCKET_AT_TIME, SHAPE_DAILY_5PM_PRICE_THRESHOLD): CLASS_MANUAL_RULES_NEEDED,
    (SHAPE_DAILY_5PM_PRICE_THRESHOLD, SHAPE_RANGE_BUCKET_AT_TIME): CLASS_MANUAL_RULES_NEEDED,
    (SHAPE_RANGE_BUCKET_AT_TIME, SHAPE_POINT_IN_TIME_PRICE_THRESHOLD): CLASS_MANUAL_RULES_NEEDED,
    (SHAPE_POINT_IN_TIME_PRICE_THRESHOLD, SHAPE_RANGE_BUCKET_AT_TIME): CLASS_MANUAL_RULES_NEEDED,
    (SHAPE_POINT_IN_TIME_PRICE_RANGE, SHAPE_DAILY_5PM_PRICE_THRESHOLD): CLASS_MANUAL_RULES_NEEDED,
    (SHAPE_DAILY_5PM_PRICE_THRESHOLD, SHAPE_POINT_IN_TIME_PRICE_RANGE): CLASS_MANUAL_RULES_NEEDED,
    # Touch vs close — explicit basis-risk.
    (SHAPE_INTRADAY_TOUCH_THRESHOLD, SHAPE_DAILY_5PM_PRICE_THRESHOLD): CLASS_BASIS_RISK_ONLY,
    (SHAPE_DAILY_5PM_PRICE_THRESHOLD, SHAPE_INTRADAY_TOUCH_THRESHOLD): CLASS_BASIS_RISK_ONLY,
    (SHAPE_INTRADAY_TOUCH_THRESHOLD, SHAPE_POINT_IN_TIME_PRICE_THRESHOLD): CLASS_BASIS_RISK_ONLY,
    (SHAPE_POINT_IN_TIME_PRICE_THRESHOLD, SHAPE_INTRADAY_TOUCH_THRESHOLD): CLASS_BASIS_RISK_ONLY,
    (SHAPE_DEADLINE_TOUCH_THRESHOLD, SHAPE_DAILY_5PM_PRICE_THRESHOLD): CLASS_BASIS_RISK_ONLY,
    (SHAPE_DAILY_5PM_PRICE_THRESHOLD, SHAPE_DEADLINE_TOUCH_THRESHOLD): CLASS_BASIS_RISK_ONLY,
    (SHAPE_DEADLINE_TOUCH_THRESHOLD, SHAPE_POINT_IN_TIME_PRICE_THRESHOLD): CLASS_BASIS_RISK_ONLY,
    (SHAPE_POINT_IN_TIME_PRICE_THRESHOLD, SHAPE_DEADLINE_TOUCH_THRESHOLD): CLASS_BASIS_RISK_ONLY,
    (SHAPE_INTRADAY_TOUCH_THRESHOLD, SHAPE_DEADLINE_TOUCH_THRESHOLD): CLASS_MANUAL_RULES_NEEDED,
    (SHAPE_DEADLINE_TOUCH_THRESHOLD, SHAPE_INTRADAY_TOUCH_THRESHOLD): CLASS_MANUAL_RULES_NEEDED,
    (SHAPE_INTRADAY_TOUCH_THRESHOLD, SHAPE_WEEKLY_FRIDAY_CLOSE_THRESHOLD): CLASS_BASIS_RISK_ONLY,
    (SHAPE_WEEKLY_FRIDAY_CLOSE_THRESHOLD, SHAPE_INTRADAY_TOUCH_THRESHOLD): CLASS_BASIS_RISK_ONLY,
    (SHAPE_DEADLINE_TOUCH_THRESHOLD, SHAPE_WEEKLY_FRIDAY_CLOSE_THRESHOLD): CLASS_BASIS_RISK_ONLY,
    (SHAPE_WEEKLY_FRIDAY_CLOSE_THRESHOLD, SHAPE_DEADLINE_TOUCH_THRESHOLD): CLASS_BASIS_RISK_ONLY,
    # All-time high — always basis-risk versus point-in-time.
    (SHAPE_ALL_TIME_HIGH_BY_DATE, SHAPE_POINT_IN_TIME_PRICE_THRESHOLD): CLASS_BASIS_RISK_ONLY,
    (SHAPE_POINT_IN_TIME_PRICE_THRESHOLD, SHAPE_ALL_TIME_HIGH_BY_DATE): CLASS_BASIS_RISK_ONLY,
    (SHAPE_ALL_TIME_HIGH_BY_DATE, SHAPE_DAILY_5PM_PRICE_THRESHOLD): CLASS_BASIS_RISK_ONLY,
    (SHAPE_DAILY_5PM_PRICE_THRESHOLD, SHAPE_ALL_TIME_HIGH_BY_DATE): CLASS_BASIS_RISK_ONLY,
    (SHAPE_ALL_TIME_HIGH_BY_DATE, SHAPE_DEADLINE_TOUCH_THRESHOLD): CLASS_BASIS_RISK_ONLY,
    (SHAPE_DEADLINE_TOUCH_THRESHOLD, SHAPE_ALL_TIME_HIGH_BY_DATE): CLASS_BASIS_RISK_ONLY,
    # Daily direction — never matches threshold; only matches other direction
    # markets and only if open / close / source rules align (manual rules).
    (SHAPE_DAILY_DIRECTION_UP_DOWN, SHAPE_DAILY_5PM_PRICE_THRESHOLD): CLASS_BASIS_RISK_ONLY,
    (SHAPE_DAILY_5PM_PRICE_THRESHOLD, SHAPE_DAILY_DIRECTION_UP_DOWN): CLASS_BASIS_RISK_ONLY,
    (SHAPE_DAILY_DIRECTION_UP_DOWN, SHAPE_POINT_IN_TIME_PRICE_THRESHOLD): CLASS_BASIS_RISK_ONLY,
    (SHAPE_POINT_IN_TIME_PRICE_THRESHOLD, SHAPE_DAILY_DIRECTION_UP_DOWN): CLASS_BASIS_RISK_ONLY,
    (SHAPE_DAILY_DIRECTION_UP_DOWN, SHAPE_INTRADAY_TOUCH_THRESHOLD): CLASS_BASIS_RISK_ONLY,
    (SHAPE_INTRADAY_TOUCH_THRESHOLD, SHAPE_DAILY_DIRECTION_UP_DOWN): CLASS_BASIS_RISK_ONLY,
}


def _compatibility(my_shape: str, peer_shape: str) -> str:
    if my_shape == SHAPE_AMBIGUOUS or peer_shape == SHAPE_AMBIGUOUS:
        return CLASS_MANUAL_RULES_NEEDED
    if (my_shape, peer_shape) in _COMPATIBILITY:
        return _COMPATIBILITY[(my_shape, peer_shape)]
    if my_shape == peer_shape:
        return CLASS_EXACT_SHAPE_POSSIBLE
    return CLASS_BASIS_RISK_ONLY


# ---------------------------------------------------------------------------
# Blockers
# ---------------------------------------------------------------------------


B_PAYOFF_SHAPE_MISMATCH = "payoff_shape_mismatch"
B_INTRADAY_TOUCH_NOT_POINT_IN_TIME = "intraday_touch_not_point_in_time"
B_DEADLINE_TOUCH_NOT_CLOSE_PRICE = "deadline_touch_not_close_price"
B_DAILY_DIRECTION_RULES_MISSING = "daily_direction_rules_missing"
B_OPEN_CLOSE_REFERENCE_MISSING = "open_close_reference_missing"
B_SETTLEMENT_SOURCE_MISSING = "settlement_source_missing"
B_SETTLEMENT_SOURCE_MISMATCH = "settlement_source_mismatch"
B_SETTLEMENT_TIME_MISSING = "settlement_time_missing"
B_TIMEZONE_MISSING = "timezone_missing"
B_COMPARATOR_MISSING = "comparator_missing"
B_THRESHOLD_MISSING = "threshold_missing"
B_TARGET_TIME_MISSING = "target_time_missing"
B_SOURCE_PRICE_INDEX_UNVERIFIED = "source_price_index_unverified"
B_POLYMARKET_RULES_MISSING = "polymarket_rules_missing"
B_CDNA_RULES_MISSING = "cdna_rules_missing"
B_KALSHI_RULES_MISSING = "kalshi_rules_missing"
B_QUOTE_MISSING = "quote_missing"
B_STALE_QUOTE = "stale_quote"
B_MISSING_CLOB_BOOK = "missing_clob_book"
B_NO_CURRENT_PEER = "no_current_peer"
B_MANUAL_DISCOVERY_REQUIRED = "manual_discovery_required"


# ---------------------------------------------------------------------------
# Reference-price-type vocabulary
# ---------------------------------------------------------------------------


REF_CF_BRTI = "CF_BRTI"
REF_CF_ERTI = "CF_ERTI"
REF_SPOT_INDEX = "spot_index"
REF_EXCHANGE_INDEX = "exchange_index"
REF_POLYMARKET_UNKNOWN = "Polymarket_rules_unknown"
REF_CDNA_UNKNOWN = "CDNA_rules_unknown"
REF_UNKNOWN = "unknown"


_POLYMARKET_UPDOWN = re.compile(r"\bup\s*(?:or|-|/)\s*down\b", re.IGNORECASE)
_TOUCH_OR_HIT_BY = re.compile(
    r"\b(?:hit|touch|reach|cross|crosses|reaches|touches|hits)\b.*\b(?:by|before|any\s+time\s+before|by\s+the\s+end)\b",
    re.IGNORECASE | re.DOTALL,
)
_ALL_TIME_HIGH = re.compile(r"\ball[-\s]?time[-\s]?high\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Builder + writer
# ---------------------------------------------------------------------------


def build_crypto_payoff_calendar_audit_report(
    *,
    input_dir: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")

    warnings: list[dict[str, Any]] = []
    kalshi_payload = _load_json(input_dir / KALSHI_AUDIT_INPUT, warnings, "kalshi_crypto_typed_key_audit_input")
    polymarket_enriched = _load_json(input_dir / POLYMARKET_ENRICHED_INPUT, warnings, "polymarket_enriched_input")
    polymarket_pit_audit = _load_json(input_dir / POLYMARKET_PIT_AUDIT_INPUT, warnings, "polymarket_pit_audit_input")
    cdna_snapshot = _load_json(input_dir / CDNA_SNAPSHOT_INPUT, warnings, "cdna_snapshot_input")
    cdna_basis = _load_json(input_dir / CDNA_BASIS_INPUT, warnings, "cdna_basis_input")

    rows: list[dict[str, Any]] = []
    rows.extend(_classify_kalshi_rows(kalshi_payload))
    rows.extend(_classify_polymarket_rows(polymarket_enriched, polymarket_pit_audit))
    rows.extend(_classify_cdna_rows(cdna_snapshot, cdna_basis))

    rows_by_venue_asset: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (row["venue"], row["asset"] or "UNKNOWN")
        rows_by_venue_asset[key].append(row)

    # Compute comparability for each row by scanning peer-venue rows for same asset.
    for row in rows:
        _attach_comparability(row, rows_by_venue_asset=rows_by_venue_asset)

    rows.sort(key=_row_sort_key)
    summary = _summary(rows=rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "schema_kind": SCHEMA_KIND,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "input_dir": str(input_dir),
        "diagnostic_only": True,
        "saved_files_only": True,
        "inputs": {
            "kalshi_crypto_typed_key_audit": str(input_dir / KALSHI_AUDIT_INPUT),
            "polymarket_taxonomy_shape_scout_enriched": str(input_dir / POLYMARKET_ENRICHED_INPUT),
            "polymarket_point_in_time_typed_key_audit": str(input_dir / POLYMARKET_PIT_AUDIT_INPUT),
            "crypto_com_predict_cdna_research_snapshot": str(input_dir / CDNA_SNAPSHOT_INPUT),
            "cdna_crypto_basis_risk_scout": str(input_dir / CDNA_BASIS_INPUT),
        },
        "payoff_shapes": list(PAYOFF_SHAPES),
        "comparability_classes": list(COMPARABILITY_CLASSES),
        "compatibility_matrix": _compatibility_matrix_dump(),
        "summary": summary,
        "rows": rows,
        "warnings": warnings,
        "safety": _safety_block(),
    }


def write_crypto_payoff_calendar_audit_files(
    *,
    input_dir: Path,
    json_output: Path,
    markdown_output: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_crypto_payoff_calendar_audit_report(
        input_dir=input_dir, generated_at=generated_at
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_crypto_payoff_calendar_audit_markdown(report), encoding="utf-8")
    return report


def render_crypto_payoff_calendar_audit_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    rows = report.get("rows") or []
    lines: list[str] = [
        "# Crypto Payoff-Calendar Audit",
        "",
        "Saved-file-only diagnostic that classifies every saved Kalshi, Polymarket, and CDNA "
        "crypto row into a payoff-calendar shape and applies a conservative cross-venue compatibility "
        "matrix. Diagnostic-only: no candidate pairs, no exact-payoff equivalence, no paper actions. "
        "Touch / direction / range-hit shapes are never silently reclassified as point-in-time.",
        "",
        "## Executive Summary",
        "",
        f"- total_crypto_rows: `{summary.get('total_crypto_rows', 0)}`",
        f"- venues: `{','.join(summary.get('venues') or [])}`",
        f"- exact_shape_possible_rows: `{summary.get('exact_shape_possible_rows', 0)}`",
        f"- basis_risk_only_rows: `{summary.get('basis_risk_only_rows', 0)}`",
        f"- manual_rules_needed_rows: `{summary.get('manual_rules_needed_rows', 0)}`",
        f"- reference_only_rows: `{summary.get('reference_only_rows', 0)}`",
        f"- no_current_peer_rows: `{summary.get('no_current_peer_rows', 0)}`",
        f"- exact_ready_rows: `0`",
        f"- paper_candidate_rows: `0`",
        "",
        "## Counts by Payoff Shape × Venue",
        "",
        "| Shape | Kalshi | Polymarket | CDNA | Total |",
        "|---|---:|---:|---:|---:|",
    ]
    by_shape = summary.get("counts_by_shape_and_venue") or {}
    for shape in PAYOFF_SHAPES:
        cell = by_shape.get(shape) or {}
        kalshi = cell.get("kalshi", 0)
        poly = cell.get("polymarket", 0)
        cdna = cell.get("cdna", 0)
        total = kalshi + poly + cdna
        if total == 0:
            continue
        lines.append(f"| {shape} | {kalshi} | {poly} | {cdna} | {total} |")
    lines.extend(
        [
            "",
            "## Comparability Class × Venue",
            "",
            "| Class | Kalshi | Polymarket | CDNA | Total |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    by_class = summary.get("counts_by_class_and_venue") or {}
    for klass in COMPARABILITY_CLASSES:
        cell = by_class.get(klass) or {}
        total = cell.get("kalshi", 0) + cell.get("polymarket", 0) + cell.get("cdna", 0)
        if total == 0:
            continue
        lines.append(
            f"| {klass} | {cell.get('kalshi', 0)} | {cell.get('polymarket', 0)} | {cell.get('cdna', 0)} | {total} |"
        )
    lines.extend(
        [
            "",
            "## Compatibility Matrix Findings",
            "",
            "Conservative cross-venue compatibility — never asserts exact payoff equivalence; only what *could* be exact-shape after rules/source/time verification.",
            "",
            "| My Shape | Peer Shape | Comparability Class |",
            "|---|---|---|",
        ]
    )
    matrix_dump = report.get("compatibility_matrix") or {}
    for pair, klass in sorted(matrix_dump.items()):
        my_shape, peer_shape = pair.split("|", 1)
        lines.append(f"| {my_shape} | {peer_shape} | {klass} |")
    lines.extend(
        [
            "",
            "## Top 20 Exact-Shape-Possible Candidate Rows",
            "",
            "| # | Venue | Shape | Asset | Target Date | Target Time | TZ | Threshold | Source | Peer Hint |",
            "|---:|---|---|---|---|---|---|---:|---|---|",
        ]
    )
    top_exact = [r for r in rows if r.get("comparability_class") == CLASS_EXACT_SHAPE_POSSIBLE][:20]
    if not top_exact:
        lines.append("| _none_ | | | | | | | | | |")
    else:
        for i, row in enumerate(top_exact, start=1):
            peer = (row.get("best_peer") or {}).get("venue") or ""
            peer_shape = (row.get("best_peer") or {}).get("payoff_shape") or ""
            peer_str = f"{peer}:{peer_shape}" if peer else "—"
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(i),
                        _md(row.get("venue")),
                        _md(row.get("payoff_shape")),
                        _md(row.get("asset")),
                        _md(row.get("target_date")),
                        _md(row.get("target_time")),
                        _md(row.get("observation_timezone")),
                        _md(_qd(row.get("threshold"))),
                        _md((row.get("settlement_source") or "")[:40]),
                        _md(peer_str),
                    ]
                )
                + " |"
            )
    lines.extend(
        [
            "",
            "## Top Blockers",
            "",
            "| Blocker | Count |",
            "|---|---:|",
        ]
    )
    for item in summary.get("top_blockers") or []:
        lines.append(f"| {item.get('blocker')} | {item.get('count')} |")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- diagnostic_only: `true`",
            "- saved_files_only: `true`",
            "- can_create_candidate_pair: `false`",
            "- can_create_paper_candidate: `false`",
            "- exact_ready: `false`",
            "- execution_ready: `false`",
            "- paper_candidate: `false`",
            "- treats_intraday_touch_as_point_in_time: `false`",
            "- treats_daily_direction_as_threshold: `false`",
            "- treats_title_similarity_as_settlement_equivalence: `false`",
            "- infers_bid_or_ask_from_midpoint_or_complement: `false`",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Kalshi classification
# ---------------------------------------------------------------------------


def _classify_kalshi_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(_compose_kalshi_row(row))
    return out


def _compose_kalshi_row(row: dict[str, Any]) -> dict[str, Any]:
    asset = _string_or_none(row.get("asset"))
    target_date = _string_or_none(row.get("target_date"))
    target_time = _string_or_none(row.get("target_time"))
    timezone_label = _string_or_none(row.get("timezone"))
    threshold = _float_or_none(row.get("threshold"))
    threshold_lower = _float_or_none(row.get("threshold_lower"))
    comparator = _string_or_none(row.get("comparator"))
    settlement_source = _string_or_none(row.get("settlement_source"))
    settlement_source_url = _string_or_none(row.get("settlement_source_url"))
    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    rules_text = _string_or_none(row.get("settlement_rules_text_preview")) or ""
    ticker = _string_or_none(row.get("ticker"))
    event_ticker = _string_or_none(row.get("event_ticker"))
    title = _string_or_none(row.get("title"))
    market_shape_in = _string_or_none(row.get("market_shape"))

    payoff_shape = _classify_kalshi_shape(
        market_shape_in=market_shape_in,
        comparator=comparator,
        title=title,
        target_date=target_date,
        target_time=target_time,
        timezone_label=timezone_label,
        rules_text=rules_text,
    )
    reference_price_type = _classify_reference_price_type(
        settlement_source=settlement_source,
        rules_text=rules_text,
    )
    blockers = _row_blockers(
        venue="kalshi",
        payoff_shape=payoff_shape,
        asset=asset,
        threshold=threshold,
        comparator=comparator,
        target_date=target_date,
        target_time=target_time,
        timezone_label=timezone_label,
        settlement_source=settlement_source,
        settlement_source_url=settlement_source_url,
        rules_text=rules_text,
        quote_present=bool(quote.get("present")),
    )
    return _row_skeleton(
        venue="kalshi",
        source_platform="kalshi",
        market_id=_string_or_none(row.get("market_id")) or ticker,
        ticker=ticker,
        event_ticker=event_ticker,
        condition_id=None,
        token_id=None,
        title=title,
        question=None,
        asset=asset,
        payoff_shape=payoff_shape,
        observation_start=None,
        observation_end=None,
        observation_time=_string_or_none(row.get("settlement_close_time")),
        observation_timezone=timezone_label,
        target_date=target_date,
        target_time=target_time,
        settlement_source=settlement_source,
        settlement_source_url=settlement_source_url,
        reference_price_type=reference_price_type,
        comparator=comparator,
        threshold=threshold,
        threshold_lower=threshold_lower,
        quote_bid=quote.get("bid"),
        quote_ask=quote.get("ask"),
        quote_bid_size=quote.get("bid_size"),
        quote_ask_size=quote.get("ask_size"),
        quote_timestamp=quote.get("observed_at"),
        source_files=[_string_or_none(row.get("raw_source_file"))],
        rules_text_preview=rules_text[:240],
        blockers=blockers,
    )


def _classify_kalshi_shape(
    *,
    market_shape_in: str | None,
    comparator: str | None,
    title: str | None,
    target_date: str | None,
    target_time: str | None,
    timezone_label: str | None,
    rules_text: str,
) -> str:
    cmp_lower = (comparator or "").strip().lower()
    title_lower = (title or "").lower()
    rules_lower = (rules_text or "").lower()
    is_range = (market_shape_in == "range_bucket") or cmp_lower == "between"
    is_touch = (market_shape_in == "deadline_threshold_touch") or bool(_TOUCH_OR_HIT_BY.search(rules_lower))
    if is_touch:
        return SHAPE_DEADLINE_TOUCH_THRESHOLD
    if is_range:
        return SHAPE_RANGE_BUCKET_AT_TIME
    if market_shape_in not in {"point_in_time_threshold", None}:
        return SHAPE_AMBIGUOUS
    # Daily 5pm or weekly Friday vs hourly classification keys on target_time + date weekday.
    is_5pm = _is_5pm_close(target_time=target_time, timezone_label=timezone_label, rules_text=rules_text)
    weekday = _weekday_of(target_date)
    if is_5pm:
        if weekday is not None and weekday == 4:  # Monday=0 ... Friday=4
            return SHAPE_WEEKLY_FRIDAY_CLOSE_THRESHOLD
        return SHAPE_DAILY_5PM_PRICE_THRESHOLD
    if target_time:
        return SHAPE_HOURLY_POINT_IN_TIME_PRICE
    return SHAPE_POINT_IN_TIME_PRICE_THRESHOLD


def _is_5pm_close(
    *,
    target_time: str | None,
    timezone_label: str | None,
    rules_text: str,
) -> bool:
    if not target_time:
        return False
    text_lower = (rules_text or "").lower()
    explicit_5pm = bool(re.search(r"\b5\s*(?:pm|p\.?m\.?)\b", text_lower))
    if explicit_5pm:
        return True
    # Numeric target_time interpretation: HH:MM in UTC, EDT = UTC-4, EST = UTC-5.
    match = re.match(r"^(\d{1,2}):(\d{2})$", target_time)
    if not match:
        return False
    hour = int(match.group(1))
    tz_upper = (timezone_label or "").upper()
    if tz_upper == "UTC":
        return hour == 21
    if tz_upper in {"EDT", "ET"} and hour == 21:
        # EDT-tagged label with HH=21 means UTC representation of 5pm EDT.
        return True
    if tz_upper in {"EST"} and hour == 22:
        return True
    return False


def _weekday_of(target_date: str | None) -> int | None:
    if not target_date:
        return None
    try:
        return datetime.strptime(target_date, "%Y-%m-%d").date().weekday()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Polymarket classification
# ---------------------------------------------------------------------------


def _classify_polymarket_rows(
    enriched_payload: Any,
    pit_audit_payload: Any,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(enriched_payload, dict):
        rows = enriched_payload.get("rows")
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if (row.get("family") or "").upper() != "CRYPTO":
                    continue
                out.append(_compose_polymarket_row(row))
    return out


def _compose_polymarket_row(row: dict[str, Any]) -> dict[str, Any]:
    typed = row.get("typed_keys") if isinstance(row.get("typed_keys"), dict) else {}
    title = _string_or_none(row.get("title"))
    question = _string_or_none(row.get("question"))
    asset = _string_or_none(typed.get("asset"))
    threshold = _float_or_none(typed.get("threshold_value"))
    comparator = _string_or_none(typed.get("threshold_operator"))
    target_date_raw = _string_or_none(typed.get("measurement_date"))
    target_date = _normalize_date(target_date_raw)
    target_time = _string_or_none(typed.get("measurement_time"))
    timezone_label = _extract_timezone_from_text(target_time or "")
    settlement_source = _string_or_none(typed.get("price_source_index"))
    clob_refresh = row.get("clob_refresh") if isinstance(row.get("clob_refresh"), dict) else {}
    attached_quote = clob_refresh.get("attached_quote") or {}
    rules_text = _string_or_none(row.get("settlement_rules_text_preview")) or ""
    upstream_shape = _string_or_none(row.get("market_shape")) or ""
    payoff_shape = _classify_polymarket_shape(
        upstream_shape=upstream_shape,
        title=title,
        question=question,
        comparator=comparator,
        target_time=target_time,
        rules_text=rules_text,
    )
    reference_price_type = _classify_reference_price_type(
        settlement_source=settlement_source,
        rules_text=rules_text,
        polymarket_default=True,
    )
    rules_preview = rules_text or " ".join(filter(None, [title, question]))[:240]
    blockers = _row_blockers(
        venue="polymarket",
        payoff_shape=payoff_shape,
        asset=asset,
        threshold=threshold,
        comparator=comparator,
        target_date=target_date,
        target_time=target_time,
        timezone_label=timezone_label,
        settlement_source=settlement_source,
        settlement_source_url=None,
        rules_text=rules_preview,
        quote_present=bool(attached_quote.get("attached") and attached_quote.get("bid") is not None),
    )
    return _row_skeleton(
        venue="polymarket",
        source_platform="polymarket",
        market_id=_string_or_none(row.get("market_id")),
        ticker=None,
        event_ticker=_string_or_none(row.get("event_id") or row.get("event_slug")),
        condition_id=_string_or_none(row.get("condition_id")),
        token_id=_first_token_id(row.get("token_ids")),
        title=title,
        question=question,
        asset=asset,
        payoff_shape=payoff_shape,
        observation_start=None,
        observation_end=None,
        observation_time=target_time,
        observation_timezone=timezone_label,
        target_date=target_date,
        target_time=target_time,
        settlement_source=settlement_source,
        settlement_source_url=None,
        reference_price_type=reference_price_type,
        comparator=comparator,
        threshold=threshold,
        threshold_lower=None,
        quote_bid=attached_quote.get("bid"),
        quote_ask=attached_quote.get("ask"),
        quote_bid_size=attached_quote.get("bid_size"),
        quote_ask_size=attached_quote.get("ask_size"),
        quote_timestamp=attached_quote.get("observed_at"),
        source_files=[_string_or_none(row.get("raw_source_file"))],
        rules_text_preview=rules_preview[:240],
        blockers=blockers,
    )


def _classify_polymarket_shape(
    *,
    upstream_shape: str,
    title: str | None,
    question: str | None,
    comparator: str | None,
    target_time: str | None,
    rules_text: str,
) -> str:
    text_lower = " ".join(filter(None, [title, question, rules_text])).lower()
    if upstream_shape == "all_time_high_by_date" or _ALL_TIME_HIGH.search(text_lower):
        return SHAPE_ALL_TIME_HIGH_BY_DATE
    if _POLYMARKET_UPDOWN.search(text_lower):
        return SHAPE_DAILY_DIRECTION_UP_DOWN
    if _TOUCH_OR_HIT_BY.search(text_lower):
        # Default to deadline_touch_threshold: "touch / hit / reach X by DATE" is a multi-day
        # window observation, never point-in-time. Only flip to intraday_touch_threshold when the
        # rules explicitly carry an intraday window marker.
        if (
            " intraday " in f" {text_lower} "
            or " in 5m " in text_lower
            or " in the next 5 min" in text_lower
            or " in the next hour" in text_lower
            or "next 5-minute" in text_lower
            or "during the day" in text_lower
        ):
            return SHAPE_INTRADAY_TOUCH_THRESHOLD
        return SHAPE_DEADLINE_TOUCH_THRESHOLD
    if upstream_shape in {"crypto_deadline_range_hit", "deadline_threshold_touch"}:
        return SHAPE_DEADLINE_TOUCH_THRESHOLD
    if upstream_shape == "range_bucket":
        return SHAPE_RANGE_BUCKET_AT_TIME
    if upstream_shape == "range_hit":
        return SHAPE_INTRADAY_TOUCH_THRESHOLD
    if upstream_shape == "point_in_time_threshold":
        time_lower = (target_time or "").lower()
        if "5:00 pm et" in time_lower or "5 pm et" in time_lower or "17:00" in time_lower:
            return SHAPE_DAILY_5PM_PRICE_THRESHOLD
        if target_time:
            return SHAPE_HOURLY_POINT_IN_TIME_PRICE
        return SHAPE_POINT_IN_TIME_PRICE_THRESHOLD
    return SHAPE_AMBIGUOUS


# ---------------------------------------------------------------------------
# CDNA classification
# ---------------------------------------------------------------------------


def _classify_cdna_rows(snapshot_payload: Any, basis_payload: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(snapshot_payload, dict):
        return out
    rows = snapshot_payload.get("rows")
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(_compose_cdna_row(row))
    return out


def _compose_cdna_row(row: dict[str, Any]) -> dict[str, Any]:
    asset = _string_or_none(row.get("asset"))
    threshold = _float_or_none(row.get("threshold_value") or row.get("upper"))
    threshold_lower = _float_or_none(row.get("lower"))
    comparator = _string_or_none(row.get("comparator"))
    title = _string_or_none(row.get("title"))
    deadline = _string_or_none(row.get("deadline_or_expiry"))
    target_date, target_time, timezone_label = _split_cdna_deadline(deadline)
    settlement_source = _string_or_none(row.get("price_source_index"))
    settlement_source_url = _string_or_none(row.get("settlement_source_url"))
    market_type = (_string_or_none(row.get("market_type")) or "").lower()
    payoff_shape = _classify_cdna_shape(market_type=market_type, title=title)
    rules_text = _string_or_none(title) or ""
    reference_price_type = _classify_reference_price_type(
        settlement_source=settlement_source,
        rules_text=rules_text,
        cdna_default=True,
    )
    blockers = _row_blockers(
        venue="cdna",
        payoff_shape=payoff_shape,
        asset=asset,
        threshold=threshold,
        comparator=comparator,
        target_date=target_date,
        target_time=target_time,
        timezone_label=timezone_label,
        settlement_source=settlement_source,
        settlement_source_url=settlement_source_url,
        rules_text=rules_text,
        quote_present=False,
    )
    return _row_skeleton(
        venue="cdna",
        source_platform="crypto_com_predict",
        market_id=_string_or_none(row.get("market_id")) or _string_or_none(row.get("event_id")),
        ticker=None,
        event_ticker=_string_or_none(row.get("event_id")),
        condition_id=None,
        token_id=None,
        title=title,
        question=_string_or_none(row.get("outcome_label")),
        asset=asset,
        payoff_shape=payoff_shape,
        observation_start=None,
        observation_end=None,
        observation_time=deadline,
        observation_timezone=timezone_label,
        target_date=target_date,
        target_time=target_time,
        settlement_source=settlement_source,
        settlement_source_url=settlement_source_url,
        reference_price_type=reference_price_type,
        comparator=comparator,
        threshold=threshold,
        threshold_lower=threshold_lower,
        quote_bid=None,
        quote_ask=None,
        quote_bid_size=None,
        quote_ask_size=None,
        quote_timestamp=None,
        source_files=[_string_or_none(row.get("raw_source_file"))],
        rules_text_preview=(rules_text or "")[:240],
        blockers=blockers,
    )


def _classify_cdna_shape(*, market_type: str, title: str | None) -> str:
    title_lower = (title or "").lower()
    if market_type == "point_in_time_threshold":
        if " at " in title_lower and "5:00" in title_lower or "5 pm" in title_lower or "5:00pm" in title_lower:
            return SHAPE_DAILY_5PM_PRICE_THRESHOLD
        if " at " in title_lower:
            return SHAPE_HOURLY_POINT_IN_TIME_PRICE
        return SHAPE_POINT_IN_TIME_PRICE_THRESHOLD
    if market_type == "all_time_high_by_date":
        return SHAPE_ALL_TIME_HIGH_BY_DATE
    if market_type == "year_end_range_bucket":
        return SHAPE_RANGE_BUCKET_AT_TIME
    if market_type == "deadline_threshold_touch":
        return SHAPE_DEADLINE_TOUCH_THRESHOLD
    if market_type == "earliest_timeframe_threshold_touch":
        return SHAPE_INTRADAY_TOUCH_THRESHOLD
    return SHAPE_AMBIGUOUS


def _split_cdna_deadline(value: str | None) -> tuple[str | None, str | None, str | None]:
    if not value:
        return None, None, None
    text = value.strip()
    target_date: str | None = None
    target_time: str | None = None
    timezone_label: str | None = None
    match = re.search(r"\b([A-Z][a-z]+\s+\d{1,2},\s*\d{4})\b", text)
    if match:
        target_date = _normalize_date(match.group(1))
    time_match = re.search(r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM))\b", text)
    if time_match:
        target_time = time_match.group(1).strip()
    tz_match = re.search(r"\b(eastern\s+time|eastern|et|edt|est|utc|gmt|pacific|pst|pdt|central)\b", text, re.IGNORECASE)
    if tz_match:
        token = tz_match.group(1).lower()
        if "eastern" in token or token in {"et", "edt", "est"}:
            timezone_label = "ET"
        elif token == "utc" or token == "gmt":
            timezone_label = "UTC"
        elif "pacific" in token or token in {"pst", "pdt"}:
            timezone_label = "PT"
        elif "central" in token:
            timezone_label = "CT"
    return target_date, target_time, timezone_label


# ---------------------------------------------------------------------------
# Row skeleton, blockers, comparability
# ---------------------------------------------------------------------------


def _row_skeleton(
    *,
    venue: str,
    source_platform: str,
    market_id: str | None,
    ticker: str | None,
    event_ticker: str | None,
    condition_id: str | None,
    token_id: str | None,
    title: str | None,
    question: str | None,
    asset: str | None,
    payoff_shape: str,
    observation_start: str | None,
    observation_end: str | None,
    observation_time: str | None,
    observation_timezone: str | None,
    target_date: str | None,
    target_time: str | None,
    settlement_source: str | None,
    settlement_source_url: str | None,
    reference_price_type: str,
    comparator: str | None,
    threshold: float | None,
    threshold_lower: float | None,
    quote_bid: Any,
    quote_ask: Any,
    quote_bid_size: Any,
    quote_ask_size: Any,
    quote_timestamp: Any,
    source_files: list[str | None],
    rules_text_preview: str,
    blockers: list[str],
) -> dict[str, Any]:
    canonical_asset = (asset or "").upper() or None
    return {
        "row_id": _row_id(venue=venue, market_id=market_id, condition_id=condition_id, token_id=token_id, ticker=ticker),
        "venue": venue,
        "source_platform": source_platform,
        "market_id": market_id,
        "ticker": ticker,
        "event_ticker": event_ticker,
        "condition_id": condition_id,
        "token_id": token_id,
        "title": title,
        "question": question,
        "market_title": title or question,
        "asset": canonical_asset,
        "payoff_shape": payoff_shape,
        "observation_start": observation_start,
        "observation_end": observation_end,
        "observation_time": observation_time,
        "observation_timezone": observation_timezone,
        "target_date": target_date,
        "target_time": target_time,
        "settlement_source": settlement_source,
        "settlement_source_url": settlement_source_url,
        "reference_price_type": reference_price_type,
        "comparator": comparator,
        "threshold": threshold,
        "threshold_lower": threshold_lower,
        "quote_bid": _float_or_none(quote_bid),
        "quote_ask": _float_or_none(quote_ask),
        "quote_bid_size": _float_or_none(quote_bid_size),
        "quote_ask_size": _float_or_none(quote_ask_size),
        "quote_timestamp": quote_timestamp if isinstance(quote_timestamp, str) and quote_timestamp.strip() else None,
        "source_files": [s for s in source_files if s],
        "rules_text_preview": rules_text_preview,
        "blockers": blockers,
        "comparability_class": CLASS_NO_CURRENT_PEER,
        "best_peer": None,
        "peer_candidates": [],
        "diagnostic_only": True,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "exact_ready": False,
        "execution_ready": False,
        "paper_candidate": False,
        "affects_evaluator_gates": False,
        "treats_intraday_touch_as_point_in_time": False,
        "treats_daily_direction_as_threshold": False,
        "treats_title_similarity_as_settlement_equivalence": False,
    }


def _row_blockers(
    *,
    venue: str,
    payoff_shape: str,
    asset: str | None,
    threshold: float | None,
    comparator: str | None,
    target_date: str | None,
    target_time: str | None,
    timezone_label: str | None,
    settlement_source: str | None,
    settlement_source_url: str | None,
    rules_text: str,
    quote_present: bool,
) -> list[str]:
    blockers: list[str] = []
    if not asset:
        blockers.append("missing_asset")
    if threshold is None and payoff_shape not in {SHAPE_DAILY_DIRECTION_UP_DOWN}:
        blockers.append(B_THRESHOLD_MISSING)
    if not comparator and payoff_shape not in {SHAPE_DAILY_DIRECTION_UP_DOWN, SHAPE_ALL_TIME_HIGH_BY_DATE}:
        blockers.append(B_COMPARATOR_MISSING)
    if not target_time and payoff_shape in {
        SHAPE_HOURLY_POINT_IN_TIME_PRICE,
        SHAPE_DAILY_5PM_PRICE_THRESHOLD,
        SHAPE_DAILY_CLOSE_PRICE_THRESHOLD,
        SHAPE_WEEKLY_FRIDAY_CLOSE_THRESHOLD,
        SHAPE_RANGE_BUCKET_AT_TIME,
        SHAPE_POINT_IN_TIME_PRICE_THRESHOLD,
        SHAPE_POINT_IN_TIME_PRICE_RANGE,
    }:
        blockers.append(B_TARGET_TIME_MISSING)
        blockers.append(B_SETTLEMENT_TIME_MISSING)
    if not timezone_label:
        blockers.append(B_TIMEZONE_MISSING)
    if not settlement_source:
        blockers.append(B_SETTLEMENT_SOURCE_MISSING)
    if not settlement_source_url and venue != "kalshi":
        # Kalshi rules text references the BRTI explicitly without a separate URL.
        if not settlement_source:
            blockers.append(B_SOURCE_PRICE_INDEX_UNVERIFIED)
    if payoff_shape == SHAPE_INTRADAY_TOUCH_THRESHOLD:
        blockers.append(B_INTRADAY_TOUCH_NOT_POINT_IN_TIME)
    if payoff_shape == SHAPE_DEADLINE_TOUCH_THRESHOLD:
        blockers.append(B_DEADLINE_TOUCH_NOT_CLOSE_PRICE)
    if payoff_shape == SHAPE_DAILY_DIRECTION_UP_DOWN:
        blockers.append(B_DAILY_DIRECTION_RULES_MISSING)
        blockers.append(B_OPEN_CLOSE_REFERENCE_MISSING)
    if not quote_present:
        blockers.append(B_QUOTE_MISSING)
        blockers.append(B_STALE_QUOTE)
    if venue == "polymarket" and not rules_text:
        blockers.append(B_POLYMARKET_RULES_MISSING)
    if venue == "cdna" and not rules_text:
        blockers.append(B_CDNA_RULES_MISSING)
    if venue == "kalshi" and not rules_text:
        blockers.append(B_KALSHI_RULES_MISSING)
    return list(dict.fromkeys(blockers))


def _attach_comparability(
    row: dict[str, Any],
    *,
    rows_by_venue_asset: dict[tuple[str, str], list[dict[str, Any]]],
) -> None:
    asset = row.get("asset") or "UNKNOWN"
    own_venue = row["venue"]
    own_shape = row["payoff_shape"]
    own_date = row.get("target_date")
    candidates: list[dict[str, Any]] = []
    for (venue, peer_asset), peer_rows in rows_by_venue_asset.items():
        if venue == own_venue:
            continue
        if peer_asset != asset:
            continue
        for peer in peer_rows:
            klass = _compatibility(own_shape, peer["payoff_shape"])
            score = _CLASS_PRIORITY.get(klass, 99)
            date_match = own_date is not None and peer.get("target_date") == own_date
            same_source = _sources_compatible(row.get("settlement_source"), peer.get("settlement_source"))
            same_threshold = _thresholds_close(row.get("threshold"), peer.get("threshold"))
            if not date_match and klass == CLASS_EXACT_SHAPE_POSSIBLE:
                # Without a matching date the best we can claim is manual-rules.
                klass = CLASS_MANUAL_RULES_NEEDED
                score = _CLASS_PRIORITY[klass]
            candidates.append(
                {
                    "venue": peer["venue"],
                    "row_id": peer["row_id"],
                    "payoff_shape": peer["payoff_shape"],
                    "comparability_class": klass,
                    "target_date": peer.get("target_date"),
                    "target_time": peer.get("target_time"),
                    "threshold": peer.get("threshold"),
                    "settlement_source": peer.get("settlement_source"),
                    "date_match": date_match,
                    "source_match": same_source,
                    "threshold_close": same_threshold,
                    "score": score,
                }
            )
    candidates.sort(key=lambda c: (c["score"], 0 if c.get("date_match") else 1))
    row["peer_candidates"] = candidates[:5]
    if not candidates:
        row["comparability_class"] = CLASS_NO_CURRENT_PEER
        row["best_peer"] = None
        if B_NO_CURRENT_PEER not in row["blockers"]:
            row["blockers"].append(B_NO_CURRENT_PEER)
        return
    best = candidates[0]
    row["comparability_class"] = best["comparability_class"]
    row["best_peer"] = best
    if best["comparability_class"] == CLASS_MANUAL_RULES_NEEDED and B_MANUAL_DISCOVERY_REQUIRED not in row["blockers"]:
        row["blockers"].append(B_MANUAL_DISCOVERY_REQUIRED)
    if best["comparability_class"] == CLASS_BASIS_RISK_ONLY and B_PAYOFF_SHAPE_MISMATCH not in row["blockers"]:
        row["blockers"].append(B_PAYOFF_SHAPE_MISMATCH)
    if best["comparability_class"] == CLASS_EXACT_SHAPE_POSSIBLE:
        if not best.get("source_match") and B_SETTLEMENT_SOURCE_MISMATCH not in row["blockers"]:
            row["blockers"].append(B_SETTLEMENT_SOURCE_MISMATCH)


def _sources_compatible(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    al = a.strip().lower()
    bl = b.strip().lower()
    if al == bl:
        return True
    if "cf benchmarks" in al and "cf benchmarks" in bl:
        return True
    return False


def _thresholds_close(a: Any, b: Any) -> bool:
    af = _float_or_none(a)
    bf = _float_or_none(b)
    if af is None or bf is None or bf == 0:
        return False
    return abs(af - bf) / max(abs(bf), 1.0) <= 0.01


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _summary(*, rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts_by_shape_and_venue: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    counts_by_class_and_venue: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    blocker_counts: Counter[str] = Counter()
    asset_counts: Counter[str] = Counter()
    venues: set[str] = set()
    exact = basis = manual = reference = no_peer = 0
    for row in rows:
        venue = row["venue"]
        venues.add(venue)
        shape = row["payoff_shape"]
        klass = row["comparability_class"]
        counts_by_shape_and_venue[shape][venue] += 1
        counts_by_class_and_venue[klass][venue] += 1
        asset_counts[row.get("asset") or "UNKNOWN"] += 1
        for blocker in row.get("blockers") or []:
            blocker_counts[blocker] += 1
        if klass == CLASS_EXACT_SHAPE_POSSIBLE:
            exact += 1
        elif klass == CLASS_BASIS_RISK_ONLY:
            basis += 1
        elif klass == CLASS_MANUAL_RULES_NEEDED:
            manual += 1
        elif klass == CLASS_REFERENCE_ONLY:
            reference += 1
        elif klass == CLASS_NO_CURRENT_PEER:
            no_peer += 1
    top_blockers = [{"blocker": b, "count": c} for b, c in blocker_counts.most_common(20)]
    return {
        "total_crypto_rows": len(rows),
        "venues": sorted(venues),
        "asset_counts": dict(asset_counts),
        "exact_shape_possible_rows": exact,
        "basis_risk_only_rows": basis,
        "manual_rules_needed_rows": manual,
        "reference_only_rows": reference,
        "no_current_peer_rows": no_peer,
        "exact_ready_rows": 0,
        "paper_candidate_rows": 0,
        "execution_ready_rows": 0,
        "counts_by_shape_and_venue": {
            shape: dict(per_venue) for shape, per_venue in counts_by_shape_and_venue.items()
        },
        "counts_by_class_and_venue": {
            klass: dict(per_venue) for klass, per_venue in counts_by_class_and_venue.items()
        },
        "top_blockers": top_blockers,
    }


def _compatibility_matrix_dump() -> dict[str, str]:
    return {f"{a}|{b}": klass for (a, b), klass in _COMPATIBILITY.items()}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_sort_key(row: dict[str, Any]) -> tuple[int, int, str, str]:
    return (
        _CLASS_PRIORITY.get(row.get("comparability_class") or CLASS_NO_CURRENT_PEER, 99),
        0 if row.get("venue") == "kalshi" else 1,
        str(row.get("asset") or "Z"),
        str(row.get("row_id") or ""),
    )


def _row_id(*, venue: str, market_id: str | None, condition_id: str | None, token_id: str | None, ticker: str | None) -> str:
    key = market_id or condition_id or token_id or ticker or "unknown"
    return f"crypto_payoff::{venue}::{key}"


def _safety_block() -> dict[str, Any]:
    return {
        "diagnostic_only": True,
        "saved_files_only": True,
        "live_fetch_attempted": False,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "exact_ready": False,
        "execution_ready": False,
        "paper_candidate": False,
        "affects_evaluator_gates": False,
        "source_registry_unchanged": True,
        "treats_intraday_touch_as_point_in_time": False,
        "treats_daily_direction_as_threshold": False,
        "treats_title_similarity_as_settlement_equivalence": False,
        "infers_bid_or_ask_from_midpoint_or_complement": False,
    }


def _load_json(path: Path, warnings: list[dict[str, Any]], reason: str) -> Any:
    if not path.exists():
        warnings.append({"source_file": str(path), "reason_code": f"{reason}_missing", "blocker": f"{reason}_missing"})
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(
            {
                "source_file": str(path),
                "reason_code": f"{reason}_unreadable",
                "blocker": f"{reason}_unreadable:{type(exc).__name__}",
            }
        )
        return None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_token_id(value: Any) -> str | None:
    if not isinstance(value, list):
        return None
    for item in value:
        s = _string_or_none(item)
        if s:
            return s
    return None


_MONTHS = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
}


def _normalize_date(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip().rstrip(",")
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError:
        pass
    match = re.match(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", text)
    if match:
        month_word = match.group(1).lower()
        if month_word in _MONTHS:
            return f"{match.group(3)}-{_MONTHS[month_word]}-{int(match.group(2)):02d}"
    return text


def _extract_timezone_from_text(text: str) -> str | None:
    match = re.search(r"\b(ET|EDT|EST|UTC|GMT|PT|PST|PDT|CT|CDT|CST)\b", text or "", re.IGNORECASE)
    if match:
        token = match.group(1).upper()
        if token in {"ET", "EDT", "EST"}:
            return "ET"
        if token in {"UTC", "GMT"}:
            return "UTC"
        if token in {"PT", "PST", "PDT"}:
            return "PT"
        if token in {"CT", "CDT", "CST"}:
            return "CT"
    return None


def _classify_reference_price_type(
    *,
    settlement_source: str | None,
    rules_text: str,
    polymarket_default: bool = False,
    cdna_default: bool = False,
) -> str:
    text_pool = " ".join(filter(None, [settlement_source, rules_text])).lower()
    if "brti" in text_pool or "bitcoin real-time index" in text_pool:
        return REF_CF_BRTI
    if "erti" in text_pool or "ethereum real-time index" in text_pool:
        return REF_CF_ERTI
    if "cf benchmarks" in text_pool:
        return REF_EXCHANGE_INDEX
    if "binance" in text_pool or "coinbase" in text_pool or "kraken" in text_pool:
        return REF_EXCHANGE_INDEX
    if "chainlink" in text_pool:
        return REF_SPOT_INDEX
    if polymarket_default:
        return REF_POLYMARKET_UNKNOWN
    if cdna_default:
        return REF_CDNA_UNKNOWN
    return REF_UNKNOWN


def _md(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "/").replace("\n", " ")


def _qd(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".") or "0"
    return str(value)


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
