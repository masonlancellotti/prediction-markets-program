from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from relative_value.source_registry import (
    ImplementationStatus,
    SOURCE_REGISTRY,
    SourceType,
)
from relative_value.venue_identity import (
    IBKR_KALSHI_FAKE_EDGE_BLOCKERS,
    canonical_venue_token,
    executable_venue_identity_from_mapping,
)


SCHEMA_VERSION = 1
SCHEMA_KIND = "cross_venue_opportunity_scout_v1"
REPORT_SOURCE = "cross_venue_opportunity_scout_v1"


ACTION_WATCH = "WATCH"
ACTION_SOURCE_REVIEW = "SOURCE_REVIEW"
ACTION_MANUAL_REVIEW = "MANUAL_REVIEW"
ACTION_BASIS_RISK_REVIEW = "BASIS_RISK_REVIEW"
ACTION_IGNORE_BLOCKED = "IGNORE_BLOCKED"

ALLOWED_ACTIONS = (
    ACTION_WATCH,
    ACTION_SOURCE_REVIEW,
    ACTION_MANUAL_REVIEW,
    ACTION_BASIS_RISK_REVIEW,
    ACTION_IGNORE_BLOCKED,
)

LANE_IBKR_FF_VS_KALSHI_FED = "IBKR_FORECASTX_FED_FOMC_vs_KALSHI_FED_FOMC"
LANE_POLYMARKET_FED_VS_KALSHI_FED = "POLYMARKET_FED_vs_KALSHI_FED_FOMC"
LANE_POLYMARKET_CRYPTO_VS_KALSHI_CRYPTO = "POLYMARKET_CRYPTO_POINT_IN_TIME_vs_KALSHI_CRYPTO_THRESHOLD"
LANE_POLYMARKET_CRYPTO_VS_CDNA_CRYPTO = "POLYMARKET_CRYPTO_POINT_IN_TIME_vs_CDNA_CRYPTO_POINT_IN_TIME"
LANE_CDNA_BTC_VS_KALSHI_BTC = "CDNA_BTC_vs_KALSHI_BTC_THRESHOLD"
LANE_ODDS_API_REFERENCE = "THE_ODDS_API_REFERENCE_ONLY"
LANE_SX_BET = "SX_BET_RESEARCH_ONLY"

ALLOWED_LANES = (
    LANE_IBKR_FF_VS_KALSHI_FED,
    LANE_POLYMARKET_FED_VS_KALSHI_FED,
    LANE_POLYMARKET_CRYPTO_VS_KALSHI_CRYPTO,
    LANE_POLYMARKET_CRYPTO_VS_CDNA_CRYPTO,
    LANE_CDNA_BTC_VS_KALSHI_BTC,
    LANE_ODDS_API_REFERENCE,
    LANE_SX_BET,
)

DEFAULT_ACTIVE_PLATFORMS = ("kalshi", "polymarket", "cdna")
CORE_TRIO_CRYPTO_LANES = (
    LANE_POLYMARKET_CRYPTO_VS_KALSHI_CRYPTO,
    LANE_CDNA_BTC_VS_KALSHI_BTC,
    LANE_POLYMARKET_CRYPTO_VS_CDNA_CRYPTO,
)


# Blocker taxonomy enforced by tests.
B_TITLE_SIMILARITY = "title_similarity_only_not_equivalence"
B_REGISTRY_BLOCKS = "source_registry_blocks_pair_creation"
B_IBKR_PLANNED = "forecastex_ibkr_planned_not_implemented"
B_IBKR_UI_NOT_CAPTURED = "memo_ibkr_ui_not_captured"
B_SETTLEMENT_RULES_NEED_REVIEW = "settlement_rules_need_review"
B_SETTLEMENT_SOURCE_MISSING = "settlement_source_missing"
B_SETTLEMENT_SOURCE_MISMATCH = "settlement_source_mismatch"
B_DATE_OR_MEETING_MISSING = "date_or_meeting_missing"
B_DATE_OR_MEETING_MISMATCH = "date_or_meeting_mismatch"
B_THRESHOLD_MISSING = "threshold_missing"
B_THRESHOLD_MISMATCH = "threshold_mismatch"
B_COMPARATOR_MISSING = "comparator_missing"
B_COMPARATOR_MISMATCH = "comparator_mismatch"
B_MIDPOINT_VS_UPPER = "midpoint_vs_upper_bound_mismatch"
B_EFFECTIVE_VS_TARGET = "effective_rate_vs_target_range_mismatch"
B_POINT_VS_DEADLINE = "point_in_time_vs_deadline_mismatch"
B_RANGE_VS_CLOSE = "range_hit_vs_close_price_mismatch"
B_QUOTE_MISSING = "quote_missing"
B_QUOTE_STALE = "quote_stale"
B_INCOMPLETE_TOB = "incomplete_top_of_book"
B_FEE_MODEL_MISSING = "fee_model_missing"
B_REFERENCE_ONLY = "reference_only_source"
B_BROKER_ROUTE_NOT_INDEPENDENT = "broker_route_not_independent_venue"
B_IBKR_KALSHI_SAME = "ibkr_kalshi_is_same_exchange_as_direct_kalshi"
B_DO_NOT_CROSS_COMPARE = "do_not_cross_compare_as_independent_arb"
B_POLYMARKET_TITLE_ONLY = "title_only_match_not_equivalence"
B_POLYMARKET_REGISTRY_BLOCKS = "polymarket_registry_blocks_pair_creation_until_review"
B_POLYMARKET_MISSING_CLOB_BOOK = "missing_clob_book"
B_POLYMARKET_STALE_OR_MISSING_QUOTE = "stale_or_missing_quote"
B_CDNA_SETTLEMENT_BASIS_RISK = "cdna_settlement_basis_risk_unreviewed"
B_HIT_BY_DEADLINE_NOT_POINT_IN_TIME = "hit_by_deadline_not_point_in_time"
B_SETTLEMENT_WINDOW_MISMATCH = "settlement_window_mismatch"
B_EXACT_PAYOFF_NOT_PROVEN = "exact_payoff_not_proven"

SHAPE_POINT_IN_TIME = "point_in_time_threshold"
SHAPE_DEADLINE = "deadline_threshold_touch"
SHAPE_RANGE_HIT = "range_hit"
SHAPE_RANGE_BUCKET = "range_bucket"
SHAPE_CRYPTO_DEADLINE_RANGE_HIT = "crypto_deadline_range_hit"
SHAPE_ALL_TIME_HIGH_BY_DATE = "all_time_high_by_date"
SHAPE_AMBIGUOUS = "ambiguous"

_ALL_TIME_HIGH_BY_DEADLINE_RE = re.compile(
    r"\b(?:all[-\s]?time\s+high|ath)\b.{0,160}\b(?:by|before|at\s+any\s+time\s+before)\b",
    re.IGNORECASE,
)
_DEADLINE_TOUCH_BY_RE = re.compile(
    r"\b(?:hit|hits|reaches|reach|reached|touches|touch|touched)\b.{0,160}\b(?:by|before|at\s+any\s+time\s+before)\b",
    re.IGNORECASE,
)
_BY_DEADLINE_TOUCH_RE = re.compile(
    r"\b(?:by|before|at\s+any\s+time\s+before)\b.{0,160}\b(?:hit|hits|reaches|reach|reached|touches|touch|touched)\b",
    re.IGNORECASE,
)
_INTERVAL_TOUCH_RE = re.compile(
    r"\b(?:hit|hits|reaches|reach|reached|touches|touch|touched)\b.{0,160}\b(?:in|during)\s+"
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
    r"sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|q[1-4]|20\d{2})\b",
    re.IGNORECASE,
)
_AT_ANY_TIME_BEFORE_RE = re.compile(r"\bat\s+any\s+time\s+before\b", re.IGNORECASE)
_BEFORE_DEADLINE_RE = re.compile(r"\bbefore\b", re.IGNORECASE)


# Files this scout consumes (paths relative to input_dir).
_DEFAULT_INPUT_FILES = {
    "ibkr_quote_diagnostics": "ibkr_forecastex_quote_diagnostics.json",
    "ibkr_normalized_draft": "ibkr_forecastex_normalized_draft.json",
    "ibkr_memo_validation": "ibkr_forecastex_manual_ui_memo_validation.json",
    "ibkr_ff_jun26_memo": "manual_snapshots/ibkr_forecastex/ff_jun26_manual_ui_memo.json",
    "ibkr_ff_dec26_memo": "manual_snapshots/ibkr_forecastex/ff_manual_ui_memo.json",
    "normalized_markets_v0": "normalized_markets_v0.json",
    "standardized_family_candidates": "standardized_family_candidates.json",
    "cdna_research_snapshot": "crypto_com_predict_cdna_research_snapshot.json",
    "cdna_vs_kalshi_btc_basis_risk": "cdna_vs_kalshi_btc_basis_risk.json",
    "cross_platform_opportunity_triage": "cross_platform_opportunity_triage.json",
    "polymarket_taxonomy_shape_scout": "polymarket_taxonomy_shape_scout.json",
    "polymarket_clob_taxonomy_refresh": "polymarket_clob_taxonomy_refresh.json",
}


def _normalize_active_platforms(active_platforms: str | Iterable[str] | None) -> set[str] | None:
    if active_platforms is None:
        return None
    if isinstance(active_platforms, str):
        raw_items = [item.strip() for item in active_platforms.split(",")]
    else:
        raw_items = [str(item).strip() for item in active_platforms]
    tokens = {_platform_alias(item) for item in raw_items if item}
    return {token for token in tokens if token}


def _platform_alias(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    compact = text.replace("-", "_").replace(" ", "_")
    if "ibkr" in compact or "forecastex" in compact:
        return "ibkr"
    if "kalshi" in compact:
        return "kalshi"
    if "polymarket" in compact:
        return "polymarket"
    if "cdna" in compact or "crypto_com_predict" in compact:
        return "cdna"
    if compact.startswith("sx") or "sx_bet" in compact:
        return "sx_bet"
    if "odds_api" in compact:
        return "the_odds_api"
    if compact in {"cme", "forecastx"}:
        return compact
    return compact


def _row_platform_tokens(row: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for side_key in ("left", "right"):
        side = row.get(side_key)
        if not isinstance(side, dict):
            continue
        token = _side_platform_token(side)
        if token:
            tokens.add(token)
    return tokens


def _side_platform_token(side: dict[str, Any]) -> str | None:
    # Platform filtering is about the data/access surface operators are actively
    # working, so source/access/venue take precedence over exchange_venue.
    for key in ("access_platform", "source_platform", "venue", "exchange_venue", "executable_venue"):
        token = _platform_alias(side.get(key))
        if token:
            return token
    return None


def _annotate_active_platform_status(row: dict[str, Any], active_platforms: set[str]) -> None:
    row_tokens = _row_platform_tokens(row)
    inactive = sorted(token for token in row_tokens if token not in active_platforms)
    row["active_platforms"] = sorted(active_platforms)
    row["platform_tokens"] = sorted(row_tokens)
    row["active_platform_status"] = "queued_inactive" if inactive else "active"
    row["excluded_inactive_platforms"] = inactive
    row["excluded_from_active_platform_ranking"] = bool(inactive)


def _active_ranked_rows(rows: list[dict[str, Any]], active_platforms: set[str] | None) -> list[dict[str, Any]]:
    if active_platforms is None:
        return rows
    return [row for row in rows if row.get("active_platform_status") == "active"]


def build_cross_venue_opportunity_scout_report(
    *,
    input_dir: Path,
    polymarket_enriched_json: Path | None = None,
    active_platforms: str | Iterable[str] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = (generated_at or datetime.now(timezone.utc))
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    inputs = _load_inputs(input_dir, polymarket_enriched_json=polymarket_enriched_json)
    warnings: list[dict[str, Any]] = list(inputs["warnings"])

    rows: list[dict[str, Any]] = []
    rows.extend(_lane_ibkr_ff_vs_kalshi_fed(inputs))
    rows.extend(_lane_polymarket_vs_kalshi_fed(inputs))
    rows.extend(_lane_polymarket_enriched_crypto_vs_kalshi_crypto(inputs))
    rows.extend(_lane_polymarket_enriched_crypto_vs_cdna_crypto(inputs))
    rows.extend(_lane_cdna_vs_kalshi_btc(inputs))
    rows.extend(_lane_odds_api_reference(inputs))
    rows.extend(_lane_sx_bet_research(inputs))

    active_platform_set = _normalize_active_platforms(active_platforms)
    if active_platform_set is not None:
        for row in rows:
            _annotate_active_platform_status(row, active_platform_set)

    # Sort by: (1) review_priority_score desc, (2) both quotes complete first,
    # (3) settlement convention distance desc (closer-to-arb first),
    # (4) row_id alphabetical for stable ordering.
    rows.sort(key=_row_sort_key)

    summary = _summary(rows, inputs, active_platforms=active_platform_set)
    return {
        "schema_kind": SCHEMA_KIND,
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "input_dir": str(input_dir),
        "diagnostic_only": True,
        "summary": summary,
        "rows": rows,
        "warnings": warnings,
        "safety": _safety_block(),
    }


def write_cross_venue_opportunity_scout_files(
    *,
    input_dir: Path,
    json_output: Path,
    markdown_output: Path,
    polymarket_enriched_json: Path | None = None,
    active_platforms: str | Iterable[str] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_cross_venue_opportunity_scout_report(
        input_dir=input_dir,
        polymarket_enriched_json=polymarket_enriched_json,
        active_platforms=active_platforms,
        generated_at=generated_at,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_cross_venue_opportunity_scout_markdown(report), encoding="utf-8")
    return report


def render_cross_venue_opportunity_scout_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    rows = report.get("rows") or []
    lines = [
        "# Cross-Venue Opportunity Scout",
        "",
        "Saved-file-only diagnostic. This report identifies the closest comparable cross-venue review targets and the exact blockers between them and any future exact-review. It does not create candidate pairs, paper-candidate rows, paper actions, or possible-arbitrage rows. No `arbitrage`, `profit`, or executable instructions appear in the rows.",
        "",
        "## IBKR Venue Warning",
        "",
        "- `IBKR unified UI can show Kalshi/CME/ForecastEx; exchange_venue, not tab/source platform alone, determines independence.`",
        "",
        "## Executive Summary",
        "",
        f"- scout_row_count: `{summary.get('scout_row_count', 0)}`",
        f"- exact_ready_rows: `{summary.get('exact_ready_rows', 0)}`",
        f"- paper_candidate_rows: `{summary.get('paper_candidate_rows', 0)}`",
        f"- execution_ready_rows: `{summary.get('execution_ready_rows', 0)}`",
        f"- top_lane: `{summary.get('top_lane') or 'none'}`",
        f"- all_platform_top_lane: `{summary.get('all_platform_top_lane') or 'none'}`",
        f"- active_platforms: `{','.join(summary.get('active_platforms') or []) or 'all'}`",
        f"- active_ranked_rows: `{summary.get('active_ranked_rows', summary.get('scout_row_count', 0))}`",
        f"- inactive_platform_rows: `{summary.get('inactive_platform_rows', 0)}`",
        f"- core_trio_top_lane: `{summary.get('core_trio_top_lane') or 'none'}`",
        f"- polymarket_rows_loaded: `{summary.get('polymarket_rows_loaded', 0)}`",
        f"- polymarket_enriched_rows_loaded: `{summary.get('polymarket_enriched_rows_loaded', 0)}`",
        f"- polymarket_rows_with_bid_ask_size: `{summary.get('polymarket_rows_with_bid_ask_size', 0)}`",
        f"- polymarket_rows_with_timestamp: `{summary.get('polymarket_rows_with_timestamp', 0)}`",
        f"- polymarket_overlap_rows: `{summary.get('polymarket_overlap_rows', 0)}`",
        "",
        "## Per-Lane Counts",
        "",
        "| Lane | Rows |",
        "|---|---:|",
    ]
    lane_counts = summary.get("lane_counts") or {}
    for lane in ALLOWED_LANES:
        lines.append(f"| {lane} | {lane_counts.get(lane, 0)} |")
    lines.extend(
        [
            "",
            "## Core Trio Top Lane Summary",
            "",
            "| Pair | Lane | Rows | Active-Ranked Rows | Best Action | Best Score | Top Blockers |",
            "|---|---|---:|---:|---|---:|---|",
        ]
    )
    for item in summary.get("core_trio_top_lane_summary") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md_cell(item.get("label")),
                    _md_cell(item.get("lane")),
                    str(item.get("rows", 0)),
                    str(item.get("active_ranked_rows", 0)),
                    _md_cell(item.get("best_action") or "none"),
                    _md_cell(item.get("best_score") if item.get("best_score") is not None else "none"),
                    _md_cell(", ".join((item.get("top_blockers") or [])[:3]) or "none"),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Action Counts",
            "",
            "| Action | Rows |",
            "|---|---:|",
        ]
    )
    action_counts = summary.get("action_counts") or {}
    for action in ALLOWED_ACTIONS:
        lines.append(f"| {action} | {action_counts.get(action, 0)} |")
    lines.extend(["", "## Top Blockers", "", "| Blocker | Count |", "|---|---:|"])
    top_blockers = summary.get("top_blockers") or []
    if top_blockers:
        for item in top_blockers:
            lines.append(f"| {item.get('blocker')} | {item.get('count')} |")
    else:
        lines.append("| _none_ |  |")
    reduction = summary.get("polymarket_clob_refresh_blocker_reduction") or {}
    lines.extend(
        [
            "",
            "## Polymarket CLOB Enrichment",
            "",
            f"- enriched_report_path: `{summary.get('polymarket_enriched_report_path')}`",
            f"- materially_reduced_quote_blockers: `{str(bool(summary.get('polymarket_clob_refresh_materially_reduced_quote_blockers'))).lower()}`",
            f"- refresh_rows_enriched: `{reduction.get('rows_enriched', 0)}`",
            f"- refresh_rows_with_bid_ask_size: `{reduction.get('rows_with_bid_ask_size', 0)}`",
            f"- refresh_rows_with_timestamp: `{reduction.get('rows_with_timestamp', 0)}`",
            f"- refresh_still_missing_clob: `{reduction.get('still_missing_clob', 0)}`",
            f"- refresh_still_stale_or_missing_quote: `{reduction.get('still_stale_or_missing_quote', 0)}`",
            "",
            "### Top Quoted Polymarket Overlap Rows",
            "",
        ]
    )
    top_poly = summary.get("top_enriched_polymarket_review_targets") or []
    if not top_poly:
        lines.append("_None._")
    else:
        lines.extend(
            [
                "| # | Score | Lane | Action | Polymarket Row | Quote | Top Blockers |",
                "|---:|---:|---|---|---|---|---|",
            ]
        )
        for index, row in enumerate(top_poly[:10], start=1):
            quote = (
                f"bid={row.get('bid')} ask={row.get('ask')} "
                f"bid_size={row.get('bid_size')} ask_size={row.get('ask_size')} "
                f"ts={row.get('quote_timestamp')}"
            )
            label = f"{row.get('underlying') or ''} {row.get('ticker_or_symbol') or row.get('market_id_or_conid') or ''}"
            blockers = ", ".join((row.get("top_blockers") or [])[:3])
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(index),
                        f"{row.get('review_priority_score', 0):.1f}",
                        _md_cell(row.get("lane")),
                        _md_cell(row.get("allowed_next_action")),
                        _md_cell(label),
                        _md_cell(quote),
                        _md_cell(blockers or "none"),
                    ]
                )
                + " |"
            )
    lines.extend(["", "## Top 10 Closest Review Targets", ""])
    top10 = summary.get("top_10_review_targets") or []
    if not top10:
        lines.append("_None._")
    else:
        lines.extend(
            [
                "| # | Score | Lane | Action | Left | Right | Top Blockers |",
                "|---:|---:|---|---|---|---|---|",
            ]
        )
        for index, row in enumerate(top10, start=1):
            left = row.get("left") or {}
            right = row.get("right") or {}
            blockers = ", ".join((row.get("blockers") or [])[:3])
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(index),
                        f"{row.get('review_priority_score', 0):.1f}",
                        _md_cell(row.get("lane")),
                        _md_cell(row.get("allowed_next_action")),
                        _md_cell(
                            f"{left.get('venue') or ''} {left.get('ticker_or_symbol') or left.get('market_id_or_conid') or ''}"
                        ),
                        _md_cell(
                            f"{right.get('venue') or ''} {right.get('ticker_or_symbol') or right.get('market_id_or_conid') or ''}"
                        ),
                        _md_cell(blockers or "none"),
                    ]
                )
                + " |"
            )
    lines.extend(
        [
            "",
            "## Next Recommended Action Per Row",
            "",
            "| Score | Lane | Action | Next Action | Evidence Summary |",
            "|---:|---|---|---|---|",
        ]
    )
    display_rows = (
        [row for row in rows if row.get("active_platform_status") == "active"]
        if summary.get("active_platform_filter_enabled")
        else rows
    )
    for row in display_rows[:50]:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"{row.get('review_priority_score', 0):.1f}",
                    _md_cell(row.get("lane")),
                    _md_cell(row.get("allowed_next_action")),
                    _md_cell(row.get("next_action_text")),
                    _md_cell(row.get("evidence_summary")),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Safety",
            "",
            f"- diagnostic_only: `true`",
            f"- can_create_candidate_pair: `false`",
            f"- can_create_paper_candidate: `false`",
            f"- exact_ready: `false`",
            f"- execution_ready: `false`",
            f"- paper_candidate: `false`",
            f"- affects_evaluator_gates: `false`",
            f"- source_registry_unchanged: `true`",
            f"- ibkr_kalshi_fake_edge_blockers_respected: `true`",
        ]
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _load_inputs(input_dir: Path, *, polymarket_enriched_json: Path | None = None) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    payloads: dict[str, Any] = {}
    for name, relpath in _DEFAULT_INPUT_FILES.items():
        path = input_dir / relpath
        if not path.exists():
            warnings.append(
                {
                    "source_file": str(path),
                    "reason_code": "input_missing",
                    "blocker": "saved_input_missing",
                    "input_name": name,
                }
            )
            payloads[name] = None
            continue
        try:
            payloads[name] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(
                {
                    "source_file": str(path),
                    "reason_code": "input_unreadable",
                    "blocker": "saved_input_unreadable",
                    "detail": f"{type(exc).__name__}",
                    "input_name": name,
                }
            )
            payloads[name] = None
    enriched_path = polymarket_enriched_json or (input_dir / "polymarket_taxonomy_shape_scout_enriched.json")
    payloads["polymarket_taxonomy_shape_scout_enriched_path"] = str(enriched_path)
    if enriched_path.exists():
        try:
            payloads["polymarket_taxonomy_shape_scout_enriched"] = json.loads(enriched_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(
                {
                    "source_file": str(enriched_path),
                    "reason_code": "polymarket_enriched_input_unreadable",
                    "blocker": "saved_input_unreadable",
                    "detail": f"{type(exc).__name__}",
                    "input_name": "polymarket_taxonomy_shape_scout_enriched",
                }
            )
            payloads["polymarket_taxonomy_shape_scout_enriched"] = None
    else:
        if polymarket_enriched_json is not None:
            warnings.append(
                {
                    "source_file": str(enriched_path),
                    "reason_code": "polymarket_enriched_input_missing",
                    "blocker": "saved_input_missing",
                    "input_name": "polymarket_taxonomy_shape_scout_enriched",
                }
            )
        payloads["polymarket_taxonomy_shape_scout_enriched"] = None
    # Odds API is glob-based by day directory.
    odds_files = sorted((input_dir / "manual_snapshots" / "the_odds_api").rglob("oddsapi_*_odds.json")) if (input_dir / "manual_snapshots" / "the_odds_api").exists() else []
    payloads["odds_api_files"] = [str(p) for p in odds_files]
    odds_rows: list[dict[str, Any]] = []
    for path in odds_files:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(
                {
                    "source_file": str(path),
                    "reason_code": "odds_api_unreadable",
                    "blocker": "saved_input_unreadable",
                    "detail": f"{type(exc).__name__}",
                }
            )
            continue
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    odds_rows.append({**item, "_source_file": str(path)})
    payloads["odds_api_rows"] = odds_rows
    # SX Bet
    sx_files = sorted((input_dir / "manual_snapshots" / "sx_bet").rglob("sx_bet_research_snapshot.json")) if (input_dir / "manual_snapshots" / "sx_bet").exists() else []
    sx_payload: dict[str, Any] | None = None
    if sx_files:
        try:
            sx_payload = json.loads(sx_files[-1].read_text(encoding="utf-8"))
            sx_payload["_source_file"] = str(sx_files[-1])
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(
                {
                    "source_file": str(sx_files[-1]),
                    "reason_code": "sx_bet_unreadable",
                    "blocker": "saved_input_unreadable",
                    "detail": f"{type(exc).__name__}",
                }
            )
    payloads["sx_bet"] = sx_payload
    payloads["warnings"] = warnings
    payloads["input_dir"] = input_dir
    return payloads


# ---------------------------------------------------------------------------
# Lane 1: IBKR FF JUN26 vs Kalshi KXFED-26JUN
# ---------------------------------------------------------------------------


def _lane_ibkr_ff_vs_kalshi_fed(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    ibkr_payload = inputs.get("ibkr_quote_diagnostics") or {}
    ibkr_rows = ibkr_payload.get("rows") if isinstance(ibkr_payload, dict) else None
    if not isinstance(ibkr_rows, list):
        ibkr_rows = []

    memo_jun26 = inputs.get("ibkr_ff_jun26_memo") if isinstance(inputs.get("ibkr_ff_jun26_memo"), dict) else None
    memo_validation = inputs.get("ibkr_memo_validation") if isinstance(inputs.get("ibkr_memo_validation"), dict) else None

    normalized = inputs.get("normalized_markets_v0") or {}
    norm_rows = normalized.get("normalized_markets") if isinstance(normalized, dict) else None
    if not isinstance(norm_rows, list):
        norm_rows = []
    kfed_rows_all = [r for r in norm_rows if isinstance(r, dict) and r.get("venue") == "kalshi" and isinstance(r.get("event_ticker"), str) and r.get("event_ticker", "").startswith("KXFED-")]
    # Dedupe Kalshi rows by ticker — normalized_markets_v0 can carry duplicates.
    kfed_rows: list[dict[str, Any]] = []
    seen_tickers: set[str] = set()
    for row in kfed_rows_all:
        ticker = str(row.get("ticker") or "")
        if ticker and ticker not in seen_tickers:
            seen_tickers.add(ticker)
            kfed_rows.append(row)

    rows: list[dict[str, Any]] = []
    seen_row_ids: set[str] = set()
    for ibkr_row in ibkr_rows:
        if not isinstance(ibkr_row, dict):
            continue
        ibkr_left = _build_ibkr_ff_left(ibkr_row, memo_jun26=memo_jun26, memo_validation=memo_validation, source_file=str(_input_path(inputs, "ibkr_quote_diagnostics")))
        ibkr_meeting = ibkr_left.get("settlement_event_date")
        same_meeting = [r for r in kfed_rows if _kalshi_meeting_date(r) == ibkr_meeting] if ibkr_meeting else []
        # For each IBKR row, choose at most one nearest-threshold Kalshi peer per meeting bucket
        # to keep the scout signal-dense rather than combinatorial.
        if same_meeting:
            best = _nearest_kalshi_by_threshold(same_meeting, ibkr_left.get("threshold"))
            kalshi_candidates = [best] if best else []
        elif kfed_rows:
            peer = _closest_kalshi_by_date(kfed_rows, ibkr_meeting)
            kalshi_candidates = [peer] if peer else []
        else:
            kalshi_candidates = []
        if not kalshi_candidates:
            row_id = f"ibkr_ff_kalshi_fed_{ibkr_left.get('ticker_or_symbol') or ibkr_left.get('market_id_or_conid')}_{ibkr_left.get('yes_no_side') or 'na'}_no_peer"
            if row_id in seen_row_ids:
                continue
            seen_row_ids.add(row_id)
            rows.append(_compose_row(
                lane=LANE_IBKR_FF_VS_KALSHI_FED,
                left=ibkr_left,
                right=None,
                comparison_extras={"reason": "no_kalshi_kxfed_peer_in_saved_data"},
                lane_specific_blockers=[B_DATE_OR_MEETING_MISSING],
                inputs=inputs,
                row_id=row_id,
            ))
            continue
        for kalshi_row in kalshi_candidates:
            kalshi_right = _build_kalshi_fed_right(kalshi_row, source_file=str(_input_path(inputs, "normalized_markets_v0")))
            row_id = f"ibkr_ff_kalshi_fed_{ibkr_left.get('ticker_or_symbol') or ibkr_left.get('market_id_or_conid')}_{ibkr_left.get('yes_no_side') or 'na'}__{kalshi_right.get('ticker_or_symbol') or kalshi_right.get('market_id_or_conid')}"
            if row_id in seen_row_ids:
                continue
            seen_row_ids.add(row_id)
            rows.append(_compose_row(
                lane=LANE_IBKR_FF_VS_KALSHI_FED,
                left=ibkr_left,
                right=kalshi_right,
                comparison_extras={},
                lane_specific_blockers=[],
                inputs=inputs,
                row_id=row_id,
            ))
    return rows


def _nearest_kalshi_by_threshold(rows: list[dict[str, Any]], target_threshold: Any) -> dict[str, Any] | None:
    try:
        target = float(target_threshold)
    except (TypeError, ValueError):
        return rows[0] if rows else None
    best: tuple[float, dict[str, Any]] | None = None
    for row in rows:
        kalshi_threshold = _kalshi_threshold_from_ticker(row.get("ticker"))
        if kalshi_threshold is None:
            continue
        # Convention: IBKR midpoint X.X75 approximates Kalshi upper-bound (X+0.125), so
        # compute distance after applying the +0.125 convention shift.
        shift_distance = abs((target + 0.125) - kalshi_threshold)
        raw_distance = abs(target - kalshi_threshold)
        score = min(shift_distance, raw_distance)
        if best is None or score < best[0]:
            best = (score, row)
    if best is None:
        # No Kalshi row had a parseable threshold; fall back so threshold_missing fires.
        return rows[0] if rows else None
    return best[1]


def _build_ibkr_ff_left(
    ibkr_row: dict[str, Any],
    *,
    memo_jun26: dict[str, Any] | None,
    memo_validation: dict[str, Any] | None,
    source_file: str,
) -> dict[str, Any]:
    strike = ibkr_row.get("strike")
    month = ibkr_row.get("month")
    maturity_date_raw = ibkr_row.get("maturity_date")
    settlement_event_date = _iso_from_yyyymmdd(maturity_date_raw)
    side_token = ibkr_row.get("yes_no_side") or ibkr_row.get("right") or ""
    threshold_semantics = (memo_jun26 or {}).get("threshold_semantics") if month == (memo_jun26 or {}).get("ibkr_forecastx_month_reviewed") else None
    comparator_semantics = (memo_jun26 or {}).get("comparator_semantics") if month == (memo_jun26 or {}).get("ibkr_forecastx_month_reviewed") else None
    quote = {
        "bid": ibkr_row.get("bid"),
        "ask": ibkr_row.get("ask"),
        "bid_size": ibkr_row.get("bid_size"),
        "ask_size": ibkr_row.get("ask_size"),
        "timestamp": ibkr_row.get("quote_timestamp"),
        "complete": bool(ibkr_row.get("quote_diagnostic_complete")),
        "marketdata_status_raw": ibkr_row.get("marketdata_status_raw"),
    }
    memo_evidence_status = _ibkr_memo_evidence_status(
        memo_jun26=memo_jun26,
        memo_validation=memo_validation,
        ibkr_month=month,
    )
    source_files = [source_file]
    if memo_jun26 is not None:
        source_files.append(str(memo_jun26.get("_source_file") or "manual_snapshots/ibkr_forecastex/ff_jun26_manual_ui_memo.json"))
    return {
        "venue": ibkr_row.get("venue") or "IBKR_FORECASTEX",
        "source_platform": ibkr_row.get("source_platform") or "IBKR",
        "access_platform": ibkr_row.get("access_platform") or "IBKR",
        "exchange_venue": ibkr_row.get("exchange_venue") or "FORECASTX",
        "executable_venue": ibkr_row.get("executable_venue") or "FORECASTX",
        "market_id_or_conid": ibkr_row.get("contract_conid"),
        "ticker_or_symbol": (
            f"FF_{maturity_date_raw}_{strike}_{side_token}"
            if maturity_date_raw and strike is not None and side_token
            else (
                f"FF_{maturity_date_raw}_{strike}"
                if maturity_date_raw and strike is not None
                else ibkr_row.get("symbol")
            )
        ),
        "event_family": "FED_FOMC",
        "underlying": "Federal Funds Target Rate (midpoint)" if threshold_semantics == "midpoint" else "Federal Funds Target Rate (unverified semantics)",
        "settlement_event_date": settlement_event_date,
        "fomc_meeting_date": settlement_event_date,
        "threshold": strike,
        "threshold_semantics": threshold_semantics or "unknown",
        "comparator": comparator_semantics or "unknown",
        "market_shape": "binary_yes_no",
        "settlement_source": (memo_jun26 or {}).get("settlement_source_name") if month == (memo_jun26 or {}).get("ibkr_forecastx_month_reviewed") else None,
        "settlement_source_url": (memo_jun26 or {}).get("settlement_source_url") if month == (memo_jun26 or {}).get("ibkr_forecastx_month_reviewed") else None,
        "settlement_time": (memo_jun26 or {}).get("expiration_and_last_trading_time") if month == (memo_jun26 or {}).get("ibkr_forecastx_month_reviewed") else None,
        "payout_unit": "1.00_USD",
        "quote": quote,
        "fee_model_status": "exchange_fee_documented_ibkr_brokerage_unknown" if memo_jun26 else "unknown",
        "source_registry_status": _registry_status("forecastex_ibkr"),
        "memo_evidence_status": memo_evidence_status,
        "yes_no_side": ibkr_row.get("yes_no_side"),
        "right": ibkr_row.get("right"),
        "source_files": source_files,
    }


def _build_kalshi_fed_right(kalshi_row: dict[str, Any], *, source_file: str) -> dict[str, Any]:
    title = kalshi_row.get("title") or ""
    threshold = _kalshi_threshold_from_ticker(kalshi_row.get("ticker"))
    rate_bound = "upper_bound" if "upper bound" in title.lower() else ("lower_bound" if "lower bound" in title.lower() else "unknown")
    lowered_title = title.lower()
    if "at or above" in lowered_title or "at-or-above" in lowered_title:
        comparator = "at_or_above"
    elif " above " in lowered_title:
        comparator = "greater_than"
    elif " below " in lowered_title or "at or below" in lowered_title:
        comparator = "less_than" if " below " in lowered_title and "at or below" not in lowered_title else "at_or_below"
    else:
        comparator = "unknown"
    settlement = kalshi_row.get("settlement") or {}
    quote_depth = kalshi_row.get("quote_depth") or {}
    quote = {
        "bid": quote_depth.get("best_yes_bid_price"),
        "ask": quote_depth.get("best_yes_ask_price"),
        "bid_size": quote_depth.get("best_yes_bid_size"),
        "ask_size": quote_depth.get("best_yes_ask_size"),
        "timestamp": quote_depth.get("captured_at"),
        "complete": bool(
            quote_depth.get("best_yes_bid_price") is not None
            and quote_depth.get("best_yes_ask_price") is not None
            and quote_depth.get("best_yes_bid_size") is not None
            and quote_depth.get("best_yes_ask_size") is not None
            and quote_depth.get("captured_at")
        ),
    }
    return {
        "venue": "kalshi",
        "source_platform": "kalshi",
        "access_platform": "kalshi",
        "exchange_venue": "KALSHI",
        "executable_venue": "KALSHI",
        "market_id_or_conid": kalshi_row.get("market_id") or kalshi_row.get("ticker"),
        "ticker_or_symbol": kalshi_row.get("ticker"),
        "event_family": "FED_FOMC",
        "underlying": "Federal Funds Target Rate (upper bound)" if rate_bound == "upper_bound" else "Federal Funds Target Rate (unverified)",
        "settlement_event_date": _date_from_iso(settlement.get("resolution_time") or settlement.get("close_time")),
        "fomc_meeting_date": _date_from_iso(settlement.get("resolution_time") or settlement.get("close_time")),
        "threshold": threshold,
        "threshold_semantics": rate_bound,
        "comparator": comparator,
        "market_shape": "binary_yes_no",
        "settlement_source": (settlement.get("settlement_source_kind") or "text_evidence") if settlement.get("settlement_rules_text") else None,
        "settlement_source_url": settlement.get("settlement_source_url"),
        "settlement_time": settlement.get("resolution_time"),
        "payout_unit": "1.00_USD",
        "quote": quote,
        "fee_model_status": "kalshi_tiered_documented",
        "source_registry_status": _registry_status("kalshi"),
        "memo_evidence_status": "kalshi_metadata_only",
        "source_files": [source_file],
    }


# ---------------------------------------------------------------------------
# Lane 2: Polymarket vs Kalshi Fed (only if Polymarket Fed exists in saved data)
# ---------------------------------------------------------------------------


def _lane_polymarket_vs_kalshi_fed(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    normalized = inputs.get("normalized_markets_v0") or {}
    norm_rows = normalized.get("normalized_markets") if isinstance(normalized, dict) else None
    if not isinstance(norm_rows, list):
        return []
    poly_fed = [
        r
        for r in norm_rows
        if isinstance(r, dict)
        and r.get("venue") == "polymarket"
        and _has_fomc_or_fed_hint(r)
    ]
    kfed = [
        r
        for r in norm_rows
        if isinstance(r, dict)
        and r.get("venue") == "kalshi"
        and isinstance(r.get("event_ticker"), str)
        and (r.get("event_ticker") or "").startswith("KXFED-")
    ]
    if not poly_fed or not kfed:
        poly_fed = []
    rows: list[dict[str, Any]] = []
    source_file = str(_input_path(inputs, "normalized_markets_v0"))
    enriched_source_file = str(inputs.get("polymarket_taxonomy_shape_scout_enriched_path") or "polymarket_taxonomy_shape_scout_enriched.json")
    for enriched_poly in _polymarket_enriched_fed_candidates(_polymarket_enriched_rows(inputs)):
        poly_left = _build_polymarket_enriched_fed_side(enriched_poly, source_file=enriched_source_file)
        peer = _closest_kalshi_by_date(kfed, poly_left.get("settlement_event_date")) if kfed else None
        if not peer:
            continue
        kalshi_right = _build_kalshi_fed_right(peer, source_file=source_file)
        lane_blockers = _polymarket_adjusted_blockers(enriched_poly)
        lane_blockers.append(B_SETTLEMENT_SOURCE_MISSING)
        rows.append(_compose_row(
            lane=LANE_POLYMARKET_FED_VS_KALSHI_FED,
            left=poly_left,
            right=kalshi_right,
            comparison_extras={
                "polymarket_enriched_taxonomy_used": True,
                "polymarket_shape": enriched_poly.get("market_shape"),
            },
            lane_specific_blockers=lane_blockers,
            inputs=inputs,
            row_id=f"poly_enriched_kalshi_fed_{poly_left.get('market_id_or_conid')}__{kalshi_right.get('market_id_or_conid')}",
        ))
    for poly in poly_fed:
        poly_left = _build_polymarket_fed_side(poly, source_file=source_file)
        peer = _closest_kalshi_by_date(kfed, poly_left.get("settlement_event_date"))
        if not peer:
            continue
        kalshi_right = _build_kalshi_fed_right(peer, source_file=source_file)
        rows.append(_compose_row(
            lane=LANE_POLYMARKET_FED_VS_KALSHI_FED,
            left=poly_left,
            right=kalshi_right,
            comparison_extras={},
            lane_specific_blockers=[B_TITLE_SIMILARITY],
            inputs=inputs,
            row_id=f"poly_kalshi_fed_{poly_left.get('market_id_or_conid')}__{kalshi_right.get('market_id_or_conid')}",
        ))
    return rows


def _polymarket_enriched_fed_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        shape = str(row.get("market_shape") or "").lower()
        family = str(row.get("family") or "").upper()
        if shape != "macro_rate_meeting" and family not in {"MACRO_FED_RATES", "FED_FOMC"}:
            continue
        if not _has_fomc_or_fed_hint({"title": row.get("title") or row.get("question")}):
            continue
        candidates.append(row)
    candidates.sort(key=lambda r: (-float(r.get("exact_matchability_score") or 0.0), str(r.get("row_id") or "")))
    return candidates[:50]


def _build_polymarket_enriched_fed_side(poly_row: dict[str, Any], *, source_file: str) -> dict[str, Any]:
    typed = poly_row.get("typed_keys") if isinstance(poly_row.get("typed_keys"), dict) else {}
    quote_raw = _polymarket_attached_quote(poly_row)
    quote = {
        "bid": quote_raw.get("bid"),
        "ask": quote_raw.get("ask"),
        "bid_size": quote_raw.get("bid_size"),
        "ask_size": quote_raw.get("ask_size"),
        "timestamp": _polymarket_quote_timestamp(poly_row),
        "complete": _polymarket_quote_complete(poly_row),
        "raw_book_file": quote_raw.get("raw_book_file"),
        "token_id": quote_raw.get("token_id"),
        "condition_id": quote_raw.get("condition_id") or poly_row.get("condition_id"),
    }
    source_files = [source_file]
    for candidate in (poly_row.get("raw_source_file"), quote_raw.get("raw_book_file")):
        if candidate:
            source_files.append(str(candidate))
    return {
        "venue": "polymarket",
        "source_platform": "polymarket",
        "access_platform": "polymarket",
        "exchange_venue": "POLYMARKET",
        "executable_venue": "POLYMARKET",
        "market_id_or_conid": poly_row.get("market_id") or poly_row.get("condition_id"),
        "ticker_or_symbol": poly_row.get("market_slug") or poly_row.get("event_slug") or poly_row.get("row_id"),
        "event_family": "FED_FOMC",
        "underlying": "Federal funds / macro rates (unverified)",
        "settlement_event_date": _date_from_any(typed.get("meeting_date") or typed.get("settlement_event_date")),
        "fomc_meeting_date": _date_from_any(typed.get("meeting_date") or typed.get("settlement_event_date")),
        "threshold": typed.get("threshold_value"),
        "threshold_semantics": typed.get("threshold_semantics") or "unknown",
        "comparator": _operator_to_comparator(typed.get("threshold_operator")),
        "market_shape": poly_row.get("market_shape") or "macro_rate_meeting",
        "settlement_source": "polymarket_source_url" if poly_row.get("source_url") else ("polymarket_rules_text" if poly_row.get("settlement_rules_text_present") else None),
        "settlement_source_url": poly_row.get("source_url"),
        "settlement_time": None,
        "payout_unit": "1.00_USDC",
        "quote": quote,
        "fee_model_status": "polymarket_conservative",
        "source_registry_status": _registry_status("polymarket"),
        "memo_evidence_status": "polymarket_enriched_taxonomy_macro_diagnostic",
        "source_files": list(dict.fromkeys(source_files)),
        "title": poly_row.get("title") or poly_row.get("question"),
        "question": poly_row.get("question"),
        "condition_id": poly_row.get("condition_id"),
        "raw_book_file": quote_raw.get("raw_book_file"),
    }


def _build_polymarket_fed_side(poly_row: dict[str, Any], *, source_file: str) -> dict[str, Any]:
    settlement = poly_row.get("settlement") or {}
    quote_depth = poly_row.get("quote_depth") or {}
    quote = {
        "bid": quote_depth.get("best_yes_bid_price"),
        "ask": quote_depth.get("best_yes_ask_price"),
        "bid_size": quote_depth.get("best_yes_bid_size"),
        "ask_size": quote_depth.get("best_yes_ask_size"),
        "timestamp": quote_depth.get("captured_at"),
        "complete": bool(
            quote_depth.get("best_yes_bid_price") is not None
            and quote_depth.get("best_yes_ask_price") is not None
        ),
    }
    return {
        "venue": "polymarket",
        "source_platform": "polymarket",
        "access_platform": "polymarket",
        "exchange_venue": "POLYMARKET",
        "executable_venue": "POLYMARKET",
        "market_id_or_conid": poly_row.get("market_id") or poly_row.get("token_id"),
        "ticker_or_symbol": poly_row.get("ticker") or poly_row.get("event_slug"),
        "event_family": "FED_FOMC",
        "underlying": "polymarket_fed_unverified",
        "settlement_event_date": _date_from_iso(settlement.get("close_time")),
        "fomc_meeting_date": None,
        "threshold": None,
        "threshold_semantics": "unknown",
        "comparator": "unknown",
        "market_shape": "binary_yes_no",
        "settlement_source": None,
        "settlement_source_url": settlement.get("settlement_source_url"),
        "settlement_time": settlement.get("close_time"),
        "payout_unit": "1.00_USDC",
        "quote": quote,
        "fee_model_status": "polymarket_conservative",
        "source_registry_status": _registry_status("polymarket"),
        "memo_evidence_status": "polymarket_no_dedicated_memo",
        "source_files": [source_file],
        "title": poly_row.get("title"),
    }


# ---------------------------------------------------------------------------
# Enriched Polymarket CLOB rows vs Kalshi/CDNA crypto thresholds
# ---------------------------------------------------------------------------


def _lane_polymarket_enriched_crypto_vs_kalshi_crypto(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    poly_rows = _polymarket_enriched_rows(inputs)
    if not poly_rows:
        return []
    normalized = inputs.get("normalized_markets_v0") or {}
    norm_rows = normalized.get("normalized_markets") if isinstance(normalized, dict) else None
    if not isinstance(norm_rows, list):
        return []
    kalshi_crypto = [r for r in norm_rows if isinstance(r, dict) and _is_kalshi_crypto_row(r)]
    if not kalshi_crypto:
        return []

    poly_source_file = str(inputs.get("polymarket_taxonomy_shape_scout_enriched_path") or "polymarket_taxonomy_shape_scout_enriched.json")
    kalshi_source_file = str(_input_path(inputs, "normalized_markets_v0"))
    rows: list[dict[str, Any]] = []
    seen_row_ids: set[str] = set()
    for poly_row in _polymarket_enriched_crypto_candidates(poly_rows):
        poly_left = _build_polymarket_enriched_side(poly_row, source_file=poly_source_file)
        peer = _nearest_kalshi_crypto_peer(kalshi_crypto, poly_left)
        if not peer:
            continue
        kalshi_right = _build_kalshi_crypto_right(peer, source_file=kalshi_source_file)
        lane_blockers = _polymarket_adjusted_blockers(poly_row)
        lane_blockers.extend(_polymarket_shape_basis_blockers(poly_left))
        lane_blockers.append(B_SETTLEMENT_SOURCE_MISSING)
        row_id = (
            f"poly_enriched_kalshi_crypto_{poly_left.get('market_id_or_conid')}"
            f"__{kalshi_right.get('market_id_or_conid')}"
        )
        if row_id in seen_row_ids:
            continue
        seen_row_ids.add(row_id)
        rows.append(_compose_row(
            lane=LANE_POLYMARKET_CRYPTO_VS_KALSHI_CRYPTO,
            left=poly_left,
            right=kalshi_right,
            comparison_extras={
                "polymarket_clob_evidence_used": bool(_polymarket_has_raw_book_evidence(poly_row)),
                "polymarket_shape": poly_row.get("market_shape"),
                "polymarket_registry_blocks_pair_creation_until_review": True,
            },
            lane_specific_blockers=lane_blockers,
            inputs=inputs,
            row_id=row_id,
        ))
    return rows


def _lane_polymarket_enriched_crypto_vs_cdna_crypto(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    poly_rows = _polymarket_enriched_rows(inputs)
    if not poly_rows:
        return []
    cdna_payload = inputs.get("cdna_research_snapshot") or {}
    cdna_rows = cdna_payload.get("rows") if isinstance(cdna_payload, dict) else None
    if not isinstance(cdna_rows, list):
        return []
    cdna_point_rows = [
        r
        for r in cdna_rows
        if isinstance(r, dict)
        and (r.get("asset") or "").upper() in {"BTC", "ETH"}
        and (r.get("market_shape_normalized") or r.get("market_shape")) == "point_in_time_threshold"
    ]
    if not cdna_point_rows:
        return []

    poly_source_file = str(inputs.get("polymarket_taxonomy_shape_scout_enriched_path") or "polymarket_taxonomy_shape_scout_enriched.json")
    cdna_source_file = str(_input_path(inputs, "cdna_research_snapshot"))
    rows: list[dict[str, Any]] = []
    seen_row_ids: set[str] = set()
    for poly_row in _polymarket_enriched_crypto_candidates(poly_rows, point_in_time_only=True):
        poly_left = _build_polymarket_enriched_side(poly_row, source_file=poly_source_file)
        peer = _nearest_cdna_crypto_peer(cdna_point_rows, poly_left)
        if not peer:
            continue
        cdna_right = _build_cdna_crypto_right(peer, source_file=cdna_source_file)
        lane_blockers = _polymarket_adjusted_blockers(poly_row)
        lane_blockers.extend([B_SETTLEMENT_SOURCE_MISSING, B_CDNA_SETTLEMENT_BASIS_RISK])
        row_id = (
            f"poly_enriched_cdna_crypto_{poly_left.get('market_id_or_conid')}"
            f"__{cdna_right.get('market_id_or_conid') or cdna_right.get('ticker_or_symbol')}"
        )
        if row_id in seen_row_ids:
            continue
        seen_row_ids.add(row_id)
        rows.append(_compose_row(
            lane=LANE_POLYMARKET_CRYPTO_VS_CDNA_CRYPTO,
            left=poly_left,
            right=cdna_right,
            comparison_extras={
                "polymarket_clob_evidence_used": bool(_polymarket_has_raw_book_evidence(poly_row)),
                "cdna_basis_risk_review_required": True,
                "polymarket_registry_blocks_pair_creation_until_review": True,
            },
            lane_specific_blockers=lane_blockers,
            inputs=inputs,
            row_id=row_id,
        ))
    return rows


def _polymarket_enriched_rows(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    payload = inputs.get("polymarket_taxonomy_shape_scout_enriched")
    rows = payload.get("rows") if isinstance(payload, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _polymarket_enriched_crypto_candidates(rows: list[dict[str, Any]], *, point_in_time_only: bool = False) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    allowed_shapes = {SHAPE_POINT_IN_TIME} if point_in_time_only else {
        SHAPE_POINT_IN_TIME,
        SHAPE_DEADLINE,
        SHAPE_CRYPTO_DEADLINE_RANGE_HIT,
        SHAPE_RANGE_HIT,
        SHAPE_RANGE_BUCKET,
        SHAPE_ALL_TIME_HIGH_BY_DATE,
        "year_end_range_bucket",
    }
    for row in rows:
        if (row.get("family") or "").upper() != "CRYPTO":
            continue
        typed = row.get("typed_keys") if isinstance(row.get("typed_keys"), dict) else {}
        asset = str(typed.get("asset") or _asset_from_text(row.get("title") or row.get("question"))).upper()
        threshold = _as_float(typed.get("threshold_value"))
        if asset not in {"BTC", "ETH"} or threshold is None:
            continue
        if (asset == "BTC" and threshold < 1000.0) or (asset == "ETH" and threshold < 100.0):
            continue
        shape = _polymarket_conservative_shape(row)
        if shape not in allowed_shapes:
            continue
        if not (_polymarket_has_raw_book_evidence(row) or _polymarket_quote_timestamp(row)):
            continue
        candidates.append(row)
    candidates.sort(
        key=lambda r: (
            0 if _polymarket_quote_complete(r) else 1,
            -float(r.get("exact_matchability_score") or 0.0),
            str(r.get("row_id") or r.get("market_id") or ""),
        )
    )
    return candidates[:100]


def _build_polymarket_enriched_side(poly_row: dict[str, Any], *, source_file: str) -> dict[str, Any]:
    typed = poly_row.get("typed_keys") if isinstance(poly_row.get("typed_keys"), dict) else {}
    asset = str(typed.get("asset") or "").upper() or _asset_from_text(poly_row.get("title") or poly_row.get("question"))
    raw_shape = str(poly_row.get("market_shape") or "unknown")
    shape = _polymarket_conservative_shape(poly_row)
    quote_raw = _polymarket_attached_quote(poly_row)
    quote = {
        "bid": quote_raw.get("bid"),
        "ask": quote_raw.get("ask"),
        "bid_size": quote_raw.get("bid_size"),
        "ask_size": quote_raw.get("ask_size"),
        "timestamp": _polymarket_quote_timestamp(poly_row),
        "complete": _polymarket_quote_complete(poly_row),
        "raw_book_file": quote_raw.get("raw_book_file"),
        "token_id": quote_raw.get("token_id"),
        "condition_id": quote_raw.get("condition_id") or poly_row.get("condition_id"),
    }
    source_files = [source_file]
    for candidate in (poly_row.get("raw_source_file"), quote_raw.get("raw_book_file")):
        if candidate:
            source_files.append(str(candidate))
    return {
        "venue": "polymarket",
        "source_platform": "polymarket",
        "access_platform": "polymarket",
        "exchange_venue": "POLYMARKET",
        "executable_venue": "POLYMARKET",
        "market_id_or_conid": poly_row.get("market_id") or poly_row.get("condition_id"),
        "ticker_or_symbol": poly_row.get("market_slug") or poly_row.get("event_slug") or poly_row.get("row_id"),
        "event_family": "CRYPTO_PRICE_THRESHOLD" if asset in {"BTC", "ETH"} else "CRYPTO",
        "underlying": asset,
        "settlement_event_date": _date_from_any(typed.get("measurement_date")),
        "fomc_meeting_date": None,
        "threshold": typed.get("threshold_value"),
        "threshold_semantics": _polymarket_threshold_semantics(poly_row),
        "comparator": _operator_to_comparator(typed.get("threshold_operator")),
        "market_shape": shape,
        "raw_market_shape": raw_shape,
        "conservative_shape_override": shape != raw_shape.strip().lower(),
        "shape_override_reason": _polymarket_deadline_touch_phrase_kind(poly_row),
        "settlement_source": "polymarket_source_url" if poly_row.get("source_url") else ("polymarket_rules_text" if poly_row.get("settlement_rules_text_present") else None),
        "settlement_source_url": poly_row.get("source_url"),
        "settlement_time": typed.get("measurement_time"),
        "payout_unit": "1.00_USDC",
        "quote": quote,
        "fee_model_status": "polymarket_conservative",
        "source_registry_status": _registry_status("polymarket"),
        "memo_evidence_status": "polymarket_enriched_taxonomy_clob_diagnostic",
        "source_files": list(dict.fromkeys(source_files)),
        "title": poly_row.get("title") or poly_row.get("question"),
        "question": poly_row.get("question"),
        "condition_id": poly_row.get("condition_id"),
        "token_ids": poly_row.get("token_ids"),
        "raw_book_file": quote_raw.get("raw_book_file"),
        "raw_blockers": poly_row.get("blockers") or [],
    }


def _build_kalshi_crypto_right(kalshi_row: dict[str, Any], *, source_file: str) -> dict[str, Any]:
    settlement = kalshi_row.get("settlement") or {}
    quote_depth = kalshi_row.get("quote_depth") or {}
    ticker = kalshi_row.get("ticker") or kalshi_row.get("market_id")
    return {
        "venue": "kalshi",
        "source_platform": "kalshi",
        "access_platform": "kalshi",
        "exchange_venue": "KALSHI",
        "executable_venue": "KALSHI",
        "market_id_or_conid": kalshi_row.get("market_id") or ticker,
        "ticker_or_symbol": ticker,
        "event_family": "CRYPTO_PRICE_THRESHOLD",
        "underlying": _kalshi_crypto_asset(kalshi_row) or "CRYPTO",
        "settlement_event_date": _date_from_iso(settlement.get("resolution_time") or settlement.get("close_time")),
        "fomc_meeting_date": None,
        "threshold": _kalshi_crypto_threshold_from_ticker(ticker),
        "threshold_semantics": "kalshi_rti_average",
        "comparator": _kalshi_crypto_comparator_from_ticker(ticker),
        "market_shape": "binary_yes_no",
        "settlement_source": (settlement.get("settlement_source_kind") or "kalshi_rules_text") if settlement.get("settlement_rules_text") else None,
        "settlement_source_url": settlement.get("settlement_source_url"),
        "settlement_time": settlement.get("resolution_time"),
        "payout_unit": "1.00_USD",
        "quote": {
            "bid": quote_depth.get("best_yes_bid_price"),
            "ask": quote_depth.get("best_yes_ask_price"),
            "bid_size": quote_depth.get("best_yes_bid_size"),
            "ask_size": quote_depth.get("best_yes_ask_size"),
            "timestamp": quote_depth.get("captured_at"),
            "complete": bool(
                quote_depth.get("best_yes_bid_price") is not None
                and quote_depth.get("best_yes_ask_price") is not None
                and quote_depth.get("best_yes_bid_size") is not None
                and quote_depth.get("best_yes_ask_size") is not None
                and quote_depth.get("captured_at")
            ),
        },
        "fee_model_status": "kalshi_tiered_documented",
        "source_registry_status": _registry_status("kalshi"),
        "memo_evidence_status": "kalshi_metadata_only",
        "source_files": [source_file],
        "title": kalshi_row.get("title"),
    }


def _build_cdna_crypto_right(cdna_row: dict[str, Any], *, source_file: str) -> dict[str, Any]:
    asset = str(cdna_row.get("asset") or "").upper()
    return {
        "venue": cdna_row.get("venue") or "crypto_com_predict_cdna",
        "source_platform": cdna_row.get("source_platform") or "crypto_com_predict_cdna",
        "access_platform": cdna_row.get("source_platform") or "crypto_com_predict_cdna",
        "exchange_venue": "CDNA",
        "executable_venue": "CDNA",
        "market_id_or_conid": cdna_row.get("market_id") or cdna_row.get("event_id") or cdna_row.get("title"),
        "ticker_or_symbol": cdna_row.get("title"),
        "event_family": "CRYPTO_PRICE_THRESHOLD",
        "underlying": asset or "CRYPTO",
        "settlement_event_date": _date_from_any(cdna_row.get("target_date") or cdna_row.get("measurement_date")),
        "fomc_meeting_date": None,
        "threshold": cdna_row.get("strike") or cdna_row.get("threshold_value"),
        "threshold_semantics": cdna_row.get("market_shape_normalized") or cdna_row.get("market_shape"),
        "comparator": _operator_to_comparator(cdna_row.get("threshold_operator") or cdna_row.get("comparator")),
        "market_shape": cdna_row.get("market_shape_normalized") or cdna_row.get("market_shape"),
        "settlement_source": cdna_row.get("settlement_source"),
        "settlement_source_url": cdna_row.get("settlement_source_url"),
        "settlement_time": cdna_row.get("measurement_time"),
        "payout_unit": "binary_unverified",
        "quote": {
            "bid": None,
            "ask": None,
            "bid_size": None,
            "ask_size": None,
            "timestamp": cdna_row.get("captured_at_utc") or cdna_row.get("captured_at"),
            "complete": False,
        },
        "fee_model_status": "unknown",
        "source_registry_status": _registry_status("crypto_com_predict_cdna"),
        "memo_evidence_status": "cdna_research_only_saved_fixture",
        "source_files": [source_file, cdna_row.get("raw_source_file")] if cdna_row.get("raw_source_file") else [source_file],
        "title": cdna_row.get("title"),
    }


# ---------------------------------------------------------------------------
# Lane 3: CDNA vs Kalshi BTC threshold
# ---------------------------------------------------------------------------


def _lane_cdna_vs_kalshi_btc(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    cdna_payload = inputs.get("cdna_research_snapshot") or {}
    cdna_rows = cdna_payload.get("rows") if isinstance(cdna_payload, dict) else None
    if not isinstance(cdna_rows, list):
        return []
    basis_risk_payload = inputs.get("cdna_vs_kalshi_btc_basis_risk") or {}
    basis_risk_rows = (
        basis_risk_payload.get("rows") if isinstance(basis_risk_payload, dict) else None
    )
    if not isinstance(basis_risk_rows, list):
        basis_risk_rows = []
    rows: list[dict[str, Any]] = []
    source_file = str(_input_path(inputs, "cdna_research_snapshot"))
    basis_file = str(_input_path(inputs, "cdna_vs_kalshi_btc_basis_risk"))
    for cdna_row in cdna_rows:
        if not isinstance(cdna_row, dict):
            continue
        left = _build_cdna_left(cdna_row, source_file=source_file)
        peer = _find_basis_risk_peer(basis_risk_rows, cdna_row)
        right = _build_kalshi_btc_right(peer, source_file=basis_file) if peer else None
        lane_specific = list(cdna_row.get("blockers") or [])
        rows.append(_compose_row(
            lane=LANE_CDNA_BTC_VS_KALSHI_BTC,
            left=left,
            right=right,
            comparison_extras={"cdna_basis_risk_compatible": cdna_row.get("basis_risk_compatible_with_kalshi"), "cdna_exact_payoff_compatible": cdna_row.get("source_exact_payoff_compatible_with_kalshi")},
            lane_specific_blockers=[
                blocker
                for blocker in lane_specific
                if blocker in {
                    B_RANGE_VS_CLOSE,
                    B_POINT_VS_DEADLINE,
                    B_REFERENCE_ONLY,
                    "not_basis_risk_comparable_with_kalshi_point_in_time",
                }
            ],
            inputs=inputs,
            row_id=f"cdna_kalshi_btc_{left.get('market_id_or_conid') or left.get('ticker_or_symbol')}",
        ))
    return rows


def _build_cdna_left(cdna_row: dict[str, Any], *, source_file: str) -> dict[str, Any]:
    return {
        "venue": cdna_row.get("venue") or "crypto_com_predict_cdna",
        "source_platform": cdna_row.get("source_platform") or "crypto_com_predict_cdna",
        "access_platform": cdna_row.get("source_platform") or "crypto_com_predict_cdna",
        "exchange_venue": "CDNA",
        "executable_venue": "CDNA",
        "market_id_or_conid": cdna_row.get("market_id") or cdna_row.get("event_id"),
        "ticker_or_symbol": cdna_row.get("title"),
        "event_family": "CRYPTO_PRICE_THRESHOLD",
        "underlying": cdna_row.get("asset") or "BTC",
        "settlement_event_date": cdna_row.get("target_date") or cdna_row.get("deadline_or_expiry"),
        "fomc_meeting_date": None,
        "threshold": cdna_row.get("strike") or cdna_row.get("threshold_value"),
        "threshold_semantics": cdna_row.get("market_shape_normalized") or cdna_row.get("market_shape"),
        "comparator": cdna_row.get("threshold_operator") or cdna_row.get("comparator"),
        "market_shape": cdna_row.get("market_shape_normalized"),
        "settlement_source": cdna_row.get("settlement_source"),
        "settlement_source_url": cdna_row.get("settlement_source_url"),
        "settlement_time": cdna_row.get("measurement_time"),
        "payout_unit": "binary_unverified",
        "quote": {
            "bid": None,
            "ask": None,
            "bid_size": None,
            "ask_size": None,
            "timestamp": cdna_row.get("captured_at_utc"),
            "complete": False,
        },
        "fee_model_status": "unknown",
        "source_registry_status": _registry_status("crypto_com_predict_cdna"),
        "memo_evidence_status": "cdna_research_only_saved_fixture",
        "source_files": [source_file, cdna_row.get("raw_source_file")] if cdna_row.get("raw_source_file") else [source_file],
    }


def _build_kalshi_btc_right(basis_row: dict[str, Any], *, source_file: str) -> dict[str, Any]:
    return {
        "venue": "kalshi",
        "source_platform": "kalshi",
        "access_platform": "kalshi",
        "exchange_venue": "KALSHI",
        "executable_venue": "KALSHI",
        "market_id_or_conid": basis_row.get("kalshi_market_id") or basis_row.get("kalshi_ticker"),
        "ticker_or_symbol": basis_row.get("kalshi_ticker"),
        "event_family": "CRYPTO_PRICE_THRESHOLD",
        "underlying": basis_row.get("kalshi_underlying") or "BTC",
        "settlement_event_date": basis_row.get("kalshi_settlement_event_date"),
        "fomc_meeting_date": None,
        "threshold": basis_row.get("kalshi_strike") or basis_row.get("kalshi_threshold"),
        "threshold_semantics": basis_row.get("kalshi_threshold_semantics") or "spot_close",
        "comparator": basis_row.get("kalshi_comparator") or "unknown",
        "market_shape": basis_row.get("kalshi_market_shape") or "binary_yes_no",
        "settlement_source": basis_row.get("kalshi_settlement_source") or "kalshi_metadata",
        "settlement_source_url": basis_row.get("kalshi_settlement_source_url"),
        "settlement_time": basis_row.get("kalshi_settlement_time"),
        "payout_unit": "1.00_USD",
        "quote": {
            "bid": None,
            "ask": None,
            "bid_size": None,
            "ask_size": None,
            "timestamp": None,
            "complete": False,
        },
        "fee_model_status": "kalshi_tiered_documented",
        "source_registry_status": _registry_status("kalshi"),
        "memo_evidence_status": "kalshi_metadata_only",
        "source_files": [source_file],
    }


# ---------------------------------------------------------------------------
# Lane 4: Odds API reference-only
# ---------------------------------------------------------------------------


def _lane_odds_api_reference(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    odds_rows = inputs.get("odds_api_rows") or []
    if not odds_rows:
        return []
    rows: list[dict[str, Any]] = []
    for odds in odds_rows[:200]:
        if not isinstance(odds, dict):
            continue
        sport = odds.get("sport_key") or "unknown_sport"
        home = odds.get("home_team")
        away = odds.get("away_team")
        commence = odds.get("commence_time")
        left = {
            "venue": "the_odds_api",
            "source_platform": "the_odds_api",
            "access_platform": "the_odds_api",
            "exchange_venue": None,
            "executable_venue": None,
            "market_id_or_conid": odds.get("id"),
            "ticker_or_symbol": f"{sport}:{away}@{home}" if home and away else odds.get("id"),
            "event_family": _odds_sport_family(sport),
            "underlying": sport,
            "settlement_event_date": _date_from_iso(commence),
            "fomc_meeting_date": None,
            "threshold": None,
            "threshold_semantics": "moneyline_reference",
            "comparator": "n/a",
            "market_shape": "moneyline",
            "settlement_source": "sportsbook_consensus_reference",
            "settlement_source_url": None,
            "settlement_time": commence,
            "payout_unit": "reference_probability",
            "quote": {
                "bid": None,
                "ask": None,
                "bid_size": None,
                "ask_size": None,
                "timestamp": odds.get("commence_time"),
                "complete": False,
            },
            "fee_model_status": "n/a_reference_only",
            "source_registry_status": _registry_status("the_odds_api"),
            "memo_evidence_status": "reference_only_source",
            "source_files": [odds.get("_source_file")] if odds.get("_source_file") else [],
        }
        rows.append(_compose_row(
            lane=LANE_ODDS_API_REFERENCE,
            left=left,
            right=None,
            comparison_extras={"note": "reference_only_source_not_executable"},
            lane_specific_blockers=[B_REFERENCE_ONLY],
            inputs=inputs,
            row_id=f"odds_api_{sport}_{odds.get('id')}",
        ))
    return rows


# ---------------------------------------------------------------------------
# Lane 5: SX Bet research only
# ---------------------------------------------------------------------------


def _lane_sx_bet_research(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    sx = inputs.get("sx_bet")
    if not isinstance(sx, dict):
        return []
    if sx.get("market_count", 0) == 0 and sx.get("order_count", 0) == 0:
        # Emit a single advisory row so the lane is visible.
        left = {
            "venue": "sx_bet",
            "source_platform": "sx_bet",
            "access_platform": "sx_bet",
            "exchange_venue": "SX_BET",
            "executable_venue": "SX_BET",
            "market_id_or_conid": "sx_bet_research_snapshot",
            "ticker_or_symbol": "sx_bet_research_snapshot",
            "event_family": "SX_BET_RESEARCH",
            "underlying": None,
            "settlement_event_date": None,
            "fomc_meeting_date": None,
            "threshold": None,
            "threshold_semantics": "unknown",
            "comparator": "unknown",
            "market_shape": "unknown",
            "settlement_source": None,
            "settlement_source_url": None,
            "settlement_time": None,
            "payout_unit": "unknown",
            "quote": {
                "bid": None,
                "ask": None,
                "bid_size": None,
                "ask_size": None,
                "timestamp": sx.get("captured_at"),
                "complete": False,
            },
            "fee_model_status": "unknown",
            "source_registry_status": _registry_status("sx_bet"),
            "memo_evidence_status": "sx_bet_research_snapshot_no_market_rows",
            "source_files": [sx.get("_source_file")] if sx.get("_source_file") else [],
        }
        return [_compose_row(
            lane=LANE_SX_BET,
            left=left,
            right=None,
            comparison_extras={"market_count": sx.get("market_count", 0), "order_count": sx.get("order_count", 0)},
            lane_specific_blockers=[],
            inputs=inputs,
            row_id="sx_bet_research_advisory",
        )]
    return []


# ---------------------------------------------------------------------------
# Composition + scoring
# ---------------------------------------------------------------------------


def _compose_row(
    *,
    lane: str,
    left: dict[str, Any],
    right: dict[str, Any] | None,
    comparison_extras: dict[str, Any],
    lane_specific_blockers: list[str],
    inputs: dict[str, Any],
    row_id: str,
) -> dict[str, Any]:
    comparison = _build_comparison(left, right)
    blockers = _compute_blockers(left=left, right=right, comparison=comparison, lane_specific=lane_specific_blockers)
    score = _review_priority_score(left=left, right=right, comparison=comparison, blockers=blockers)
    action = _allowed_next_action(blockers=blockers, comparison=comparison, left=left, right=right)
    next_action_text = _next_action_text(action=action, blockers=blockers, comparison=comparison)
    evidence_summary = _evidence_summary(left=left, right=right, comparison=comparison)
    source_files = list(dict.fromkeys([*(left.get("source_files") or []), *((right or {}).get("source_files") or [])]))
    return {
        "row_id": row_id,
        "lane": lane,
        "left": left,
        "right": right,
        "comparison": {**comparison, **comparison_extras},
        "blockers": blockers,
        "review_priority_score": round(score, 2),
        "allowed_next_action": action,
        "next_action_text": next_action_text,
        "evidence_summary": evidence_summary,
        "source_files": source_files,
        "diagnostic_only": True,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "exact_ready": False,
        "execution_ready": False,
        "paper_candidate": False,
        "affects_evaluator_gates": False,
    }


def _build_comparison(left: dict[str, Any], right: dict[str, Any] | None) -> dict[str, Any]:
    if right is None:
        return {
            "same_family": False,
            "same_meeting_date": False,
            "same_threshold_after_convention_translation": "unknown",
            "same_comparator": False,
            "same_market_shape": False,
            "settlement_source_relation": "no_peer",
            "executable_on_both_sides": False,
        }
    same_family = (left.get("event_family") == right.get("event_family")) and bool(left.get("event_family"))
    same_meeting = bool(
        left.get("settlement_event_date")
        and right.get("settlement_event_date")
        and left.get("settlement_event_date") == right.get("settlement_event_date")
    )
    same_threshold = _compare_threshold_after_conventions(left, right)
    left_comp = (left.get("comparator") or "").strip().lower()
    right_comp = (right.get("comparator") or "").strip().lower()
    same_comparator = bool(left_comp) and bool(right_comp) and _comparator_aliases(left_comp) == _comparator_aliases(right_comp)
    same_shape = bool(left.get("market_shape")) and bool(right.get("market_shape")) and left.get("market_shape") == right.get("market_shape")
    settlement_rel = _settlement_source_relation(left, right)
    return {
        "same_family": same_family,
        "same_meeting_date": same_meeting,
        "same_threshold_after_convention_translation": same_threshold,
        "same_comparator": same_comparator,
        "same_market_shape": same_shape,
        "settlement_source_relation": settlement_rel,
        "executable_on_both_sides": _both_executable_per_registry(left, right),
    }


def _compute_blockers(
    *,
    left: dict[str, Any],
    right: dict[str, Any] | None,
    comparison: dict[str, Any],
    lane_specific: list[str],
) -> list[str]:
    blockers: list[str] = []
    # Lane-specific blockers come first so they appear in deterministic order.
    blockers.extend(lane_specific)

    # IBKR-Kalshi broker-route fake-edge guard.
    left_identity = executable_venue_identity_from_mapping(left)
    right_identity = executable_venue_identity_from_mapping(right) if right else None
    left_is_ibkr = _is_ibkr_row(left)
    right_is_ibkr = _is_ibkr_row(right) if right else False
    if left_identity and right_identity and left_identity == right_identity == "KALSHI" and (left_is_ibkr or right_is_ibkr):
        blockers.extend(IBKR_KALSHI_FAKE_EDGE_BLOCKERS)

    # Source-registry blockers.
    left_registry = left.get("source_registry_status") or {}
    right_registry = (right or {}).get("source_registry_status") or {}
    if _is_forecastex_ibkr(left) or _is_forecastex_ibkr(right):
        blockers.append(B_IBKR_PLANNED)
        blockers.append(B_REGISTRY_BLOCKS)
    if right is None and _is_planned_not_implemented(left_registry):
        blockers.append(B_REGISTRY_BLOCKS)
    if right is not None and (_is_planned_not_implemented(left_registry) or _is_planned_not_implemented(right_registry)):
        blockers.append(B_REGISTRY_BLOCKS)

    # Memo / settlement evidence
    if left.get("memo_evidence_status") in {"jun26_memo_validated_with_ibkr_ui_not_captured", "dec26_memo_validated_with_ibkr_ui_not_captured"}:
        blockers.append(B_IBKR_UI_NOT_CAPTURED)
    if _is_ibkr_row(left) or _is_ibkr_row(right):
        blockers.append(B_SETTLEMENT_RULES_NEED_REVIEW)

    # Reference-only
    if _is_reference_only(left_registry) or _is_reference_only(right_registry):
        blockers.append(B_REFERENCE_ONLY)

    # Settlement source / threshold / comparator semantics
    if right is not None:
        rel = comparison.get("settlement_source_relation")
        if rel == "midpoint_vs_upper_bound_mismatch":
            blockers.append(B_MIDPOINT_VS_UPPER)
            blockers.append(B_SETTLEMENT_SOURCE_MISMATCH)
        elif rel == "effective_rate_vs_target_range_mismatch":
            blockers.append(B_EFFECTIVE_VS_TARGET)
            blockers.append(B_SETTLEMENT_SOURCE_MISMATCH)
        elif rel == "mismatch":
            blockers.append(B_SETTLEMENT_SOURCE_MISMATCH)
        elif rel == "left_missing" or rel == "right_missing" or rel == "both_missing":
            blockers.append(B_SETTLEMENT_SOURCE_MISSING)

        if not comparison.get("same_meeting_date"):
            if not left.get("settlement_event_date") or not right.get("settlement_event_date"):
                blockers.append(B_DATE_OR_MEETING_MISSING)
            else:
                blockers.append(B_DATE_OR_MEETING_MISMATCH)
        if left.get("threshold") is None or right.get("threshold") is None:
            blockers.append(B_THRESHOLD_MISSING)
        elif comparison.get("same_threshold_after_convention_translation") not in {True, "approx_equivalent"}:
            blockers.append(B_THRESHOLD_MISMATCH)
        if (left.get("comparator") or "unknown") == "unknown" or (right.get("comparator") or "unknown") == "unknown":
            blockers.append(B_COMPARATOR_MISSING)
        elif not comparison.get("same_comparator"):
            blockers.append(B_COMPARATOR_MISMATCH)

    # Quote / freshness / top-of-book
    if left.get("quote") and not left["quote"].get("complete"):
        blockers.append(B_INCOMPLETE_TOB)
    if right is not None and right.get("quote") and not right["quote"].get("complete"):
        blockers.append(B_INCOMPLETE_TOB)
    if left.get("quote") and not left["quote"].get("timestamp"):
        blockers.append(B_QUOTE_MISSING)
    if right is not None and right.get("quote") and not right["quote"].get("timestamp"):
        blockers.append(B_QUOTE_MISSING)

    # Fee model
    left_fee_status = (left.get("fee_model_status") or "").lower()
    right_fee_status = ((right or {}).get("fee_model_status") or "").lower() if right else ""
    if "unknown" in left_fee_status or (right is not None and "unknown" in right_fee_status):
        blockers.append(B_FEE_MODEL_MISSING)

    # Title-similarity heuristic (lane-specific blocker carried for Polymarket)
    # Already added by lane.

    return list(dict.fromkeys(blockers))


def _review_priority_score(
    *,
    left: dict[str, Any],
    right: dict[str, Any] | None,
    comparison: dict[str, Any],
    blockers: list[str],
) -> float:
    if right is None:
        # Lone-side rows are research-only and rank below any paired-lane row.
        if B_REFERENCE_ONLY in blockers:
            return 5.0
        return 2.0
    score = 0.0
    if comparison.get("same_family"):
        score += 12.0
    if comparison.get("same_meeting_date"):
        score += 12.0
    if comparison.get("same_threshold_after_convention_translation") is True:
        score += 6.0
    elif comparison.get("same_threshold_after_convention_translation") == "approx_equivalent":
        score += 4.0
    if comparison.get("same_comparator"):
        score += 6.0
    if comparison.get("same_market_shape"):
        score += 5.0
    if comparison.get("executable_on_both_sides"):
        score += 8.0
    if left.get("quote", {}).get("complete") and (right.get("quote") or {}).get("complete"):
        score += 6.0
    elif left.get("quote", {}).get("complete") or (right.get("quote") or {}).get("complete"):
        score += 2.0
    if left.get("memo_evidence_status") in {"jun26_memo_validated_with_ibkr_ui_not_captured", "dec26_memo_validated_with_ibkr_ui_not_captured", "jun26_memo_validated"}:
        score += 6.0
    if "unknown" not in (left.get("fee_model_status") or "").lower() and "unknown" not in (right.get("fee_model_status") or "").lower():
        score += 5.0

    # Penalties; avoid double-counting where two blockers describe the same root cause.
    blocker_set = set(blockers)
    penalty_lookup = {
        B_POINT_VS_DEADLINE: -10.0,
        B_RANGE_VS_CLOSE: -10.0,
        B_DATE_OR_MEETING_MISSING: -4.0,
        B_DATE_OR_MEETING_MISMATCH: -4.0,
        B_THRESHOLD_MISSING: -4.0,
        B_THRESHOLD_MISMATCH: -3.0,
        B_COMPARATOR_MISSING: -4.0,
        B_COMPARATOR_MISMATCH: -4.0,
        B_QUOTE_MISSING: -2.0,
        B_QUOTE_STALE: -4.0,
        B_INCOMPLETE_TOB: -3.0,
        B_FEE_MODEL_MISSING: -3.0,
        B_REFERENCE_ONLY: -15.0,
        B_IBKR_UI_NOT_CAPTURED: -6.0,
        B_SETTLEMENT_RULES_NEED_REVIEW: -3.0,
        B_TITLE_SIMILARITY: -4.0,
        B_BROKER_ROUTE_NOT_INDEPENDENT: -25.0,
        B_IBKR_KALSHI_SAME: -25.0,
        B_DO_NOT_CROSS_COMPARE: -10.0,
        B_POLYMARKET_TITLE_ONLY: -4.0,
        B_POLYMARKET_REGISTRY_BLOCKS: -6.0,
        B_CDNA_SETTLEMENT_BASIS_RISK: -10.0,
        B_HIT_BY_DEADLINE_NOT_POINT_IN_TIME: -8.0,
        B_SETTLEMENT_WINDOW_MISMATCH: -6.0,
        B_EXACT_PAYOFF_NOT_PROVEN: -8.0,
    }
    # Settlement convention mismatch: charge once (most specific wins).
    if B_MIDPOINT_VS_UPPER in blocker_set:
        score -= 8.0
    elif B_EFFECTIVE_VS_TARGET in blocker_set:
        score -= 8.0
    elif B_SETTLEMENT_SOURCE_MISMATCH in blocker_set:
        score -= 6.0
    elif B_SETTLEMENT_SOURCE_MISSING in blocker_set:
        score -= 4.0
    # Registry / IBKR-planned: charge once.
    if B_IBKR_PLANNED in blocker_set or B_REGISTRY_BLOCKS in blocker_set:
        score -= 8.0
    for blocker in blocker_set:
        score += penalty_lookup.get(blocker, 0.0)
    # Paired-lane floor: rows that have both same_family and same_meeting_date are real
    # review targets even when many blockers are present. Floor them above WATCH-only lanes.
    if comparison.get("same_family") and comparison.get("same_meeting_date"):
        score = max(score, 15.0)
    return max(0.0, min(100.0, score))


def _allowed_next_action(*, blockers: list[str], comparison: dict[str, Any], left: dict[str, Any], right: dict[str, Any] | None) -> str:
    blocker_set = set(blockers)
    # Hard fake-edge blocks first: IBKR-Kalshi broker route is never an independent pair.
    if B_IBKR_KALSHI_SAME in blocker_set or B_BROKER_ROUTE_NOT_INDEPENDENT in blocker_set:
        return ACTION_IGNORE_BLOCKED
    # Range-hit vs point-in-time and point-in-time vs deadline must be resolved before any review.
    if (
        B_RANGE_VS_CLOSE in blocker_set
        or B_POINT_VS_DEADLINE in blocker_set
        or B_CDNA_SETTLEMENT_BASIS_RISK in blocker_set
        or B_HIT_BY_DEADLINE_NOT_POINT_IN_TIME in blocker_set
        or B_SETTLEMENT_WINDOW_MISMATCH in blocker_set
        or B_EXACT_PAYOFF_NOT_PROVEN in blocker_set
    ):
        return ACTION_BASIS_RISK_REVIEW
    # Reference-only lanes stay as WATCH; cannot ever be executable, but the row is informative.
    if B_REFERENCE_ONLY in blocker_set:
        return ACTION_WATCH
    # Settlement / memo / rules review beats raw typed-key mismatches because those mismatches
    # become moot if the settlement convention itself is unverified.
    if (
        B_MIDPOINT_VS_UPPER in blocker_set
        or B_EFFECTIVE_VS_TARGET in blocker_set
        or B_SETTLEMENT_SOURCE_MISMATCH in blocker_set
        or B_SETTLEMENT_SOURCE_MISSING in blocker_set
        or B_IBKR_UI_NOT_CAPTURED in blocker_set
        or B_SETTLEMENT_RULES_NEED_REVIEW in blocker_set
        or B_IBKR_PLANNED in blocker_set
    ):
        return ACTION_SOURCE_REVIEW
    if (
        B_DATE_OR_MEETING_MISMATCH in blocker_set
        or B_THRESHOLD_MISMATCH in blocker_set
        or B_COMPARATOR_MISMATCH in blocker_set
        or B_DATE_OR_MEETING_MISSING in blocker_set
        or B_THRESHOLD_MISSING in blocker_set
        or B_COMPARATOR_MISSING in blocker_set
    ):
        return ACTION_MANUAL_REVIEW
    if (
        B_QUOTE_MISSING in blocker_set
        or B_INCOMPLETE_TOB in blocker_set
        or B_FEE_MODEL_MISSING in blocker_set
        or B_TITLE_SIMILARITY in blocker_set
        or B_POLYMARKET_TITLE_ONLY in blocker_set
        or B_POLYMARKET_REGISTRY_BLOCKS in blocker_set
        or B_REGISTRY_BLOCKS in blocker_set
    ):
        return ACTION_WATCH
    return ACTION_WATCH


def _next_action_text(*, action: str, blockers: list[str], comparison: dict[str, Any]) -> str:
    if action == ACTION_IGNORE_BLOCKED:
        return "Same-exchange or registry block prevents cross-venue review. Do not pair."
    if action == ACTION_BASIS_RISK_REVIEW:
        return "Resolve basis-risk: point-in-time vs deadline / range-hit vs close mismatch or CDNA settlement basis risk must be reconciled before exact-review."
    if action == ACTION_SOURCE_REVIEW:
        details = []
        if B_MIDPOINT_VS_UPPER in blockers:
            details.append("midpoint vs upper-bound settlement convention")
        if B_EFFECTIVE_VS_TARGET in blockers:
            details.append("effective rate vs target range settlement convention")
        if B_SETTLEMENT_SOURCE_MISSING in blockers:
            details.append("settlement source / URL missing")
        if B_IBKR_UI_NOT_CAPTURED in blockers:
            details.append("IBKR UI evidence not captured")
        if B_SETTLEMENT_RULES_NEED_REVIEW in blockers:
            details.append("IBKR settlement_rules_need_review still active")
        return "Source-review needed: " + (", ".join(details) or "settlement source review required") + "."
    if action == ACTION_MANUAL_REVIEW:
        details = []
        if B_DATE_OR_MEETING_MISMATCH in blockers:
            details.append("meeting date mismatch")
        if B_DATE_OR_MEETING_MISSING in blockers:
            details.append("meeting date missing")
        if B_THRESHOLD_MISMATCH in blockers:
            details.append("threshold mismatch")
        if B_THRESHOLD_MISSING in blockers:
            details.append("threshold missing")
        if B_COMPARATOR_MISMATCH in blockers:
            details.append("comparator mismatch")
        if B_COMPARATOR_MISSING in blockers:
            details.append("comparator missing")
        return "Manual-review required: " + (", ".join(details) or "typed-key mismatch") + "."
    return "Watch-only diagnostic; not yet a review target."


def _evidence_summary(*, left: dict[str, Any], right: dict[str, Any] | None, comparison: dict[str, Any]) -> str:
    parts = [f"left={left.get('venue')}:{left.get('ticker_or_symbol') or left.get('market_id_or_conid')}"]
    if right is not None:
        parts.append(f"right={right.get('venue')}:{right.get('ticker_or_symbol') or right.get('market_id_or_conid')}")
    parts.append(f"family={left.get('event_family')}")
    parts.append(f"settlement_relation={comparison.get('settlement_source_relation')}")
    if comparison.get("same_meeting_date"):
        parts.append(f"meeting={left.get('settlement_event_date')}")
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compare_threshold_after_conventions(left: dict[str, Any], right: dict[str, Any]) -> bool | str:
    lt = left.get("threshold")
    rt = right.get("threshold")
    if lt is None or rt is None:
        return False
    try:
        lf = float(lt)
        rf = float(rt)
    except (TypeError, ValueError):
        return False
    if math.isclose(lf, rf, abs_tol=1e-9):
        return True
    # FED upper-bound vs midpoint: midpoint X.X75 corresponds to upper-bound X.X75 (no shift)
    # at the same convention point only when range straddles. We do NOT auto-translate.
    if math.isclose(abs(lf - rf), 0.125, abs_tol=1e-9):
        return "approx_equivalent"
    return False


def _comparator_aliases(value: str) -> str:
    value = value.strip().lower()
    if value in {">", "greater_than", "above"}:
        return "greater_than"
    if value in {">=", "at_or_above", "gte"}:
        return "at_or_above"
    if value in {"<", "less_than", "below"}:
        return "less_than"
    if value in {"<=", "at_or_below", "lte"}:
        return "at_or_below"
    return value


def _settlement_source_relation(left: dict[str, Any], right: dict[str, Any]) -> str:
    left_sem = (left.get("threshold_semantics") or "").strip().lower()
    right_sem = (right.get("threshold_semantics") or "").strip().lower()
    if not left_sem and not right_sem:
        return "both_missing"
    if not left_sem:
        return "left_missing"
    if not right_sem:
        return "right_missing"
    if left_sem == right_sem:
        return "same"
    midpoint = {"midpoint"}
    upper = {"upper_bound", "upper"}
    lower = {"lower_bound", "lower"}
    effective = {"effective_rate"}
    if (left_sem in midpoint and right_sem in upper) or (left_sem in upper and right_sem in midpoint):
        return "midpoint_vs_upper_bound_mismatch"
    if (left_sem in midpoint and right_sem in lower) or (left_sem in lower and right_sem in midpoint):
        return "midpoint_vs_lower_bound_mismatch"
    if (left_sem in effective and (right_sem in midpoint or right_sem in upper or right_sem in lower)) or (right_sem in effective and (left_sem in midpoint or left_sem in upper or left_sem in lower)):
        return "effective_rate_vs_target_range_mismatch"
    return "mismatch"


def _is_planned_not_implemented(registry_status: Any) -> bool:
    if isinstance(registry_status, dict):
        return registry_status.get("implementation_status") == ImplementationStatus.PLANNED_NOT_IMPLEMENTED.value
    return False


def _is_reference_only(registry_status: Any) -> bool:
    if isinstance(registry_status, dict):
        return registry_status.get("source_type") == SourceType.REFERENCE_ONLY.value
    return False


def _both_executable_per_registry(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_reg = left.get("source_registry_status") or {}
    right_reg = right.get("source_registry_status") or {}
    return bool(left_reg.get("can_create_candidate_pair") and right_reg.get("can_create_candidate_pair"))


def _is_ibkr_row(row: dict[str, Any] | None) -> bool:
    if not isinstance(row, dict):
        return False
    venue_token = canonical_venue_token(row.get("venue")) or ""
    access_token = canonical_venue_token(row.get("access_platform")) or ""
    source_token = canonical_venue_token(row.get("source_platform")) or ""
    return access_token == "IBKR" or source_token == "IBKR" or venue_token.startswith("IBKR_")


def _is_forecastex_ibkr(row: dict[str, Any] | None) -> bool:
    if not isinstance(row, dict):
        return False
    if not _is_ibkr_row(row):
        return False
    exchange = canonical_venue_token(row.get("exchange_venue")) or ""
    venue = canonical_venue_token(row.get("venue")) or ""
    return exchange == "FORECASTX" or venue in {"IBKR_FORECASTEX", "IBKR_FORECASTX"}


def _ibkr_memo_evidence_status(*, memo_jun26: dict[str, Any] | None, memo_validation: dict[str, Any] | None, ibkr_month: Any) -> str:
    if memo_jun26 is None:
        return "no_memo"
    memo_month = memo_jun26.get("ibkr_forecastx_month_reviewed")
    if ibkr_month and memo_month and ibkr_month != memo_month:
        return f"memo_month_mismatch:{memo_month}_vs_{ibkr_month}"
    ui_status = (memo_jun26.get("ibkr_ui_capture_status") or "").lower()
    base_validated = bool(memo_validation and memo_validation.get("validation_passed"))
    if memo_month == "JUN26" and base_validated and ui_status == "not_captured":
        return "jun26_memo_validated_with_ibkr_ui_not_captured"
    if memo_month == "JUN26" and base_validated:
        return "jun26_memo_validated"
    if memo_month == "DEC26" and base_validated and ui_status == "not_captured":
        return "dec26_memo_validated_with_ibkr_ui_not_captured"
    return "memo_unverified"


def _registry_status(source_id: str) -> dict[str, Any]:
    entry = SOURCE_REGISTRY.get(source_id)
    if entry is None:
        return {
            "source_id": source_id,
            "implementation_status": "UNKNOWN_SOURCE",
            "source_type": "UNKNOWN_SOURCE",
            "can_create_candidate_pair": False,
        }
    return {
        "source_id": entry.source_id,
        "implementation_status": entry.implementation_status.value,
        "source_type": entry.source_type.value,
        "can_create_candidate_pair": entry.can_create_candidate_pair,
    }


def _iso_from_yyyymmdd(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text
    return None


def _date_from_iso(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return None


def _kalshi_threshold_from_ticker(ticker: Any) -> float | None:
    if not isinstance(ticker, str):
        return None
    match = re.search(r"-T([0-9]+\.?[0-9]*)$", ticker)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _kalshi_meeting_date(row: dict[str, Any]) -> str | None:
    settlement = row.get("settlement") or {}
    return _date_from_iso(settlement.get("resolution_time") or settlement.get("close_time"))


def _closest_kalshi_by_date(rows: list[dict[str, Any]], target_date: str | None) -> dict[str, Any] | None:
    if not target_date:
        return None
    best: tuple[int, dict[str, Any]] | None = None
    try:
        target = datetime.strptime(target_date, "%Y-%m-%d").date()
    except ValueError:
        return None
    for row in rows:
        date = _kalshi_meeting_date(row)
        if not date:
            continue
        try:
            cand = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            continue
        delta = abs((cand - target).days)
        if best is None or delta < best[0]:
            best = (delta, row)
    return best[1] if best else None


def _has_fomc_or_fed_hint(row: dict[str, Any]) -> bool:
    title = (row.get("title") or "").lower()
    return (
        ("fomc" in title)
        or ("federal funds" in title)
        or ("fed funds" in title)
        or ("federal reserve" in title)
        or bool(re.search(r"\bfed\b", title))
    )


def _odds_sport_family(sport_key: str) -> str:
    text = (sport_key or "").lower()
    if "nfl" in text:
        return "NFL_GAME"
    if "ncaaf" in text:
        return "NCAAF_GAME"
    if "mlb" in text or "baseball" in text:
        return "MLB_GAME"
    if "nba" in text or "basketball" in text:
        return "NBA_GAME"
    if "ncaab" in text:
        return "NCAAB_GAME"
    if "nhl" in text or "hockey" in text:
        return "NHL_GAME"
    return "SPORTS_REFERENCE"


def _find_basis_risk_peer(basis_rows: Iterable[dict[str, Any]], cdna_row: dict[str, Any]) -> dict[str, Any] | None:
    cdna_id = cdna_row.get("event_id") or cdna_row.get("market_id")
    for row in basis_rows or []:
        if not isinstance(row, dict):
            continue
        if row.get("cdna_event_id") == cdna_id or row.get("cdna_market_id") == cdna_id:
            return row
    return None


def _polymarket_attached_quote(row: dict[str, Any]) -> dict[str, Any]:
    clob_refresh = row.get("clob_refresh") if isinstance(row.get("clob_refresh"), dict) else {}
    attached = clob_refresh.get("attached_quote") if isinstance(clob_refresh.get("attached_quote"), dict) else None
    if isinstance(attached, dict):
        return attached
    clob_book = row.get("clob_book") if isinstance(row.get("clob_book"), dict) else {}
    return clob_book if isinstance(clob_book, dict) else {}


def _polymarket_has_raw_book_evidence(row: dict[str, Any]) -> bool:
    quote = _polymarket_attached_quote(row)
    return bool(
        quote.get("attached")
        and quote.get("raw_book_file")
        and not quote.get("missing_book")
        and not quote.get("inferred_from_midpoint_or_complement")
    )


def _polymarket_quote_timestamp(row: dict[str, Any]) -> Any:
    quote = _polymarket_attached_quote(row)
    return quote.get("quote_timestamp") or quote.get("observed_at") or quote.get("raw_book_source_timestamp")


def _polymarket_quote_complete(row: dict[str, Any]) -> bool:
    quote = _polymarket_attached_quote(row)
    return bool(
        _polymarket_has_raw_book_evidence(row)
        and quote.get("bid") is not None
        and quote.get("ask") is not None
        and quote.get("bid_size") is not None
        and quote.get("ask_size") is not None
        and _polymarket_quote_timestamp(row)
    )


def _polymarket_conservative_shape(row: dict[str, Any]) -> str:
    raw_shape = str(row.get("market_shape") or "").strip().lower()
    phrase_kind = _polymarket_deadline_touch_phrase_kind(row)
    family = str(row.get("family") or "").upper()
    if phrase_kind == "all_time_high_by_date":
        return SHAPE_ALL_TIME_HIGH_BY_DATE
    if raw_shape == SHAPE_POINT_IN_TIME and phrase_kind == "deadline_threshold_touch":
        return SHAPE_CRYPTO_DEADLINE_RANGE_HIT if family == "CRYPTO" else SHAPE_DEADLINE
    if raw_shape == SHAPE_POINT_IN_TIME and phrase_kind == "before_deadline":
        return SHAPE_CRYPTO_DEADLINE_RANGE_HIT if family == "CRYPTO" else SHAPE_AMBIGUOUS
    return raw_shape


def _polymarket_deadline_touch_phrase_kind(row: dict[str, Any]) -> str | None:
    text = _polymarket_combined_market_text(row)
    if not text:
        return None
    if _ALL_TIME_HIGH_BY_DEADLINE_RE.search(text):
        return "all_time_high_by_date"
    if _DEADLINE_TOUCH_BY_RE.search(text) or _BY_DEADLINE_TOUCH_RE.search(text) or _INTERVAL_TOUCH_RE.search(text):
        return "deadline_threshold_touch"
    if _AT_ANY_TIME_BEFORE_RE.search(text):
        return "deadline_threshold_touch"
    if _BEFORE_DEADLINE_RE.search(text):
        return "before_deadline"
    return None


def _polymarket_combined_market_text(row: dict[str, Any]) -> str:
    fields = (
        row.get("question"),
        row.get("title"),
        row.get("market_slug"),
        row.get("event_slug"),
        row.get("ticker_or_symbol"),
    )
    return " ".join(str(field) for field in fields if field)


def _polymarket_adjusted_blockers(row: dict[str, Any]) -> list[str]:
    raw = [str(b) for b in (row.get("blockers") or []) if str(b or "").strip()]
    adjusted: list[str] = []
    has_book = _polymarket_has_raw_book_evidence(row)
    has_timestamp = bool(_polymarket_quote_timestamp(row))
    quote = _polymarket_attached_quote(row)
    explicit_quote_blockers = {
        "polymarket_missing_bid": quote.get("bid") is not None,
        "polymarket_missing_ask": quote.get("ask") is not None,
        "polymarket_missing_bid_size": quote.get("bid_size") is not None,
        "polymarket_missing_ask_size": quote.get("ask_size") is not None,
        "polymarket_missing_quote_timestamp": has_timestamp,
    }
    for blocker in raw:
        if blocker == B_POLYMARKET_MISSING_CLOB_BOOK and has_book:
            continue
        if blocker == B_POLYMARKET_STALE_OR_MISSING_QUOTE and has_timestamp:
            continue
        if explicit_quote_blockers.get(blocker):
            continue
        adjusted.append(blocker)
    if B_POLYMARKET_TITLE_ONLY not in adjusted:
        adjusted.append(B_POLYMARKET_TITLE_ONLY)
    if B_POLYMARKET_REGISTRY_BLOCKS not in adjusted:
        adjusted.append(B_POLYMARKET_REGISTRY_BLOCKS)
    if _polymarket_deadline_touch_phrase_kind(row):
        adjusted.extend([B_HIT_BY_DEADLINE_NOT_POINT_IN_TIME, B_SETTLEMENT_WINDOW_MISMATCH, B_EXACT_PAYOFF_NOT_PROVEN])
    return list(dict.fromkeys(adjusted))


def _polymarket_shape_basis_blockers(side: dict[str, Any]) -> list[str]:
    shape = str(side.get("market_shape") or "").lower()
    blockers: list[str] = []
    if "range" in shape:
        blockers.append(B_RANGE_VS_CLOSE)
    if "deadline" in shape or shape == SHAPE_ALL_TIME_HIGH_BY_DATE:
        blockers.append(B_POINT_VS_DEADLINE)
    if shape in {SHAPE_DEADLINE, SHAPE_CRYPTO_DEADLINE_RANGE_HIT, SHAPE_ALL_TIME_HIGH_BY_DATE}:
        blockers.extend([B_HIT_BY_DEADLINE_NOT_POINT_IN_TIME, B_SETTLEMENT_WINDOW_MISMATCH])
    if shape in {SHAPE_DEADLINE, SHAPE_CRYPTO_DEADLINE_RANGE_HIT, SHAPE_ALL_TIME_HIGH_BY_DATE, SHAPE_RANGE_HIT, SHAPE_RANGE_BUCKET, "year_end_range_bucket"}:
        blockers.append(B_EXACT_PAYOFF_NOT_PROVEN)
    return list(dict.fromkeys(blockers))


def _polymarket_threshold_semantics(row: dict[str, Any]) -> str:
    shape = _polymarket_conservative_shape(row)
    typed = row.get("typed_keys") if isinstance(row.get("typed_keys"), dict) else {}
    source = str(typed.get("price_source_index") or row.get("source_url") or "").lower()
    if "range" in shape:
        return "range_hit_or_bucket"
    if "deadline" in shape:
        return "deadline_threshold_touch"
    if "binance" in source:
        return "binance_spot_close"
    if shape == SHAPE_POINT_IN_TIME:
        return "spot_close_unverified"
    return "unknown"


def _operator_to_comparator(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {">", "greater_than", "above"}:
        return "greater_than"
    if text in {">=", "at_or_above", "gte"}:
        return "at_or_above"
    if text in {"<", "less_than", "below"}:
        return "less_than"
    if text in {"<=", "at_or_below", "lte"}:
        return "at_or_below"
    return "unknown"


def _date_from_any(value: Any) -> str | None:
    direct = _date_from_iso(value)
    if direct:
        return direct
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value).strip())
    text = re.sub(r"^(by|on|before|after)\s+", "", text, flags=re.IGNORECASE)
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _is_kalshi_crypto_row(row: dict[str, Any]) -> bool:
    if row.get("venue") != "kalshi":
        return False
    return _kalshi_crypto_asset(row) in {"BTC", "ETH"}


def _kalshi_crypto_asset(row: dict[str, Any]) -> str | None:
    text = " ".join(str(row.get(key) or "") for key in ("event_ticker", "ticker", "market_id", "title")).upper()
    if "KXBTC" in text or "BITCOIN" in text or " BTC" in f" {text}":
        return "BTC"
    if "KXETH" in text or "ETHEREUM" in text or " ETH" in f" {text}":
        return "ETH"
    return None


def _asset_from_text(value: Any) -> str:
    text = str(value or "").upper()
    if "BITCOIN" in text or "BTC" in text:
        return "BTC"
    if "ETHEREUM" in text or " ETH" in f" {text}":
        return "ETH"
    return "CRYPTO"


def _kalshi_crypto_threshold_from_ticker(ticker: Any) -> float | None:
    if not isinstance(ticker, str):
        return None
    match = re.search(r"-[TB]([0-9]+\.?[0-9]*)$", ticker)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _kalshi_crypto_comparator_from_ticker(ticker: Any) -> str:
    text = str(ticker or "")
    if re.search(r"-T[0-9]+\.?[0-9]*$", text):
        return "greater_than"
    if re.search(r"-B[0-9]+\.?[0-9]*$", text):
        return "less_than"
    return "unknown"


def _nearest_kalshi_crypto_peer(kalshi_rows: Iterable[dict[str, Any]], poly_side: dict[str, Any]) -> dict[str, Any] | None:
    asset = str(poly_side.get("underlying") or "").upper()
    target_threshold = _as_float(poly_side.get("threshold"))
    target_date = poly_side.get("settlement_event_date")
    best: tuple[float, dict[str, Any]] | None = None
    for row in kalshi_rows:
        if _kalshi_crypto_asset(row) != asset:
            continue
        threshold = _kalshi_crypto_threshold_from_ticker(row.get("ticker") or row.get("market_id"))
        threshold_distance = _distance_or_default(target_threshold, threshold, default=1_000_000.0)
        date = _kalshi_meeting_date(row)
        date_penalty = 0.0 if target_date and date == target_date else 1000.0
        comparator_penalty = 0.0 if _kalshi_crypto_comparator_from_ticker(row.get("ticker")) == poly_side.get("comparator") else 100.0
        score = date_penalty + comparator_penalty + threshold_distance
        if best is None or score < best[0]:
            best = (score, row)
    return best[1] if best else None


def _nearest_cdna_crypto_peer(cdna_rows: Iterable[dict[str, Any]], poly_side: dict[str, Any]) -> dict[str, Any] | None:
    asset = str(poly_side.get("underlying") or "").upper()
    target_threshold = _as_float(poly_side.get("threshold"))
    target_date = poly_side.get("settlement_event_date")
    best: tuple[float, dict[str, Any]] | None = None
    for row in cdna_rows:
        if str(row.get("asset") or "").upper() != asset:
            continue
        threshold = _as_float(row.get("strike") or row.get("threshold_value"))
        threshold_distance = _distance_or_default(target_threshold, threshold, default=1_000_000.0)
        date = _date_from_any(row.get("target_date") or row.get("measurement_date"))
        date_penalty = 0.0 if target_date and date == target_date else 1000.0
        score = date_penalty + threshold_distance
        if best is None or score < best[0]:
            best = (score, row)
    return best[1] if best else None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _distance_or_default(left: float | None, right: float | None, *, default: float) -> float:
    if left is None or right is None:
        return default
    return abs(left - right)


def _polymarket_clob_refresh_materiality(inputs: dict[str, Any]) -> dict[str, Any]:
    payload = inputs.get("polymarket_clob_taxonomy_refresh")
    summary = payload.get("summary") if isinstance(payload, dict) and isinstance(payload.get("summary"), dict) else {}
    rows_with_bid_ask_size = int(summary.get("rows_with_bid_ask_size") or 0)
    rows_with_timestamp = int(summary.get("rows_with_timestamp") or 0)
    rows_enriched = int(summary.get("rows_enriched") or 0)
    return {
        "materially_reduced_quote_blockers": bool(rows_with_bid_ask_size > 0 or rows_with_timestamp > 0),
        "candidates_selected": int(summary.get("candidates_selected") or 0),
        "rows_enriched": rows_enriched,
        "books_requested": int(summary.get("books_requested") or 0),
        "books_saved": int(summary.get("books_saved") or 0),
        "rows_with_bid_ask_size": rows_with_bid_ask_size,
        "rows_with_timestamp": rows_with_timestamp,
        "still_missing_clob": int(summary.get("still_missing_clob") or 0),
        "still_stale_or_missing_quote": int(summary.get("still_stale_or_missing_quote") or 0),
    }


def _summary(
    rows: list[dict[str, Any]],
    inputs: dict[str, Any],
    *,
    active_platforms: set[str] | None = None,
) -> dict[str, Any]:
    lane_counts: dict[str, int] = Counter()
    action_counts: dict[str, int] = Counter()
    blocker_counts: Counter[str] = Counter()
    exact_ready = 0
    paper_candidate = 0
    execution_ready = 0
    for row in rows:
        lane_counts[row.get("lane") or "unknown"] += 1
        action_counts[row.get("allowed_next_action") or "WATCH"] += 1
        for blocker in row.get("blockers") or []:
            blocker_counts[blocker] += 1
        if row.get("exact_ready"):
            exact_ready += 1
        if row.get("paper_candidate"):
            paper_candidate += 1
        if row.get("execution_ready"):
            execution_ready += 1
    ranked_rows = _active_ranked_rows(rows, active_platforms)
    active_lane_counts: dict[str, int] = Counter(row.get("lane") or "unknown" for row in ranked_rows)
    inactive_platform_rows = len(rows) - len(ranked_rows) if active_platforms is not None else 0
    # top_lane prefers actionable (non-reference-only) lanes so operators see the closest review target.
    actionable_counts = {lane: count for lane, count in lane_counts.items() if lane != LANE_ODDS_API_REFERENCE}
    active_actionable_counts = {
        lane: count for lane, count in active_lane_counts.items() if lane != LANE_ODDS_API_REFERENCE
    }
    if active_platforms is not None and active_actionable_counts:
        top_lane = max(active_actionable_counts.items(), key=lambda kv: kv[1])[0]
    elif active_platforms is not None and active_lane_counts:
        top_lane = max(active_lane_counts.items(), key=lambda kv: kv[1])[0]
    elif actionable_counts:
        top_lane = max(actionable_counts.items(), key=lambda kv: kv[1])[0]
    elif lane_counts:
        top_lane = max(lane_counts.items(), key=lambda kv: kv[1])[0]
    else:
        top_lane = None
    if actionable_counts:
        all_platform_top_lane = max(actionable_counts.items(), key=lambda kv: kv[1])[0]
    elif lane_counts:
        all_platform_top_lane = max(lane_counts.items(), key=lambda kv: kv[1])[0]
    else:
        all_platform_top_lane = None
    top_blockers = [
        {"blocker": b, "count": c}
        for b, c in blocker_counts.most_common(15)
    ]
    top_targets = [
        {
            "row_id": r.get("row_id"),
            "lane": r.get("lane"),
            "review_priority_score": r.get("review_priority_score"),
            "allowed_next_action": r.get("allowed_next_action"),
            "blockers": r.get("blockers"),
            "left": {
                "venue": (r.get("left") or {}).get("venue"),
                "ticker_or_symbol": (r.get("left") or {}).get("ticker_or_symbol"),
                "settlement_event_date": (r.get("left") or {}).get("settlement_event_date"),
                "threshold": (r.get("left") or {}).get("threshold"),
                "threshold_semantics": (r.get("left") or {}).get("threshold_semantics"),
            },
            "right": (
                None
                if r.get("right") is None
                else {
                    "venue": (r.get("right") or {}).get("venue"),
                    "ticker_or_symbol": (r.get("right") or {}).get("ticker_or_symbol"),
                    "settlement_event_date": (r.get("right") or {}).get("settlement_event_date"),
                    "threshold": (r.get("right") or {}).get("threshold"),
                    "threshold_semantics": (r.get("right") or {}).get("threshold_semantics"),
                }
            ),
        }
        for r in ranked_rows[:10]
    ]
    base_poly_payload = inputs.get("polymarket_taxonomy_shape_scout")
    base_poly_rows = base_poly_payload.get("rows") if isinstance(base_poly_payload, dict) else []
    if not isinstance(base_poly_rows, list):
        base_poly_rows = []
    enriched_poly_rows = _polymarket_enriched_rows(inputs)
    enriched_rows_with_bid_ask_size = 0
    enriched_rows_with_timestamp = 0
    for poly_row in enriched_poly_rows:
        quote = _polymarket_attached_quote(poly_row)
        if (
            quote.get("bid") is not None
            and quote.get("ask") is not None
            and quote.get("bid_size") is not None
            and quote.get("ask_size") is not None
            and _polymarket_has_raw_book_evidence(poly_row)
        ):
            enriched_rows_with_bid_ask_size += 1
        if _polymarket_quote_timestamp(poly_row):
            enriched_rows_with_timestamp += 1
    polymarket_overlap_rows = [
        r for r in rows
        if ((r.get("left") or {}).get("venue") == "polymarket" or ((r.get("right") or {}).get("venue") == "polymarket"))
    ]
    top_enriched_polymarket_targets = []
    quoted_polymarket_overlap_rows = [
        r for r in polymarket_overlap_rows
        if ((r.get("left") or {}).get("quote") or {}).get("complete")
    ]
    for row in quoted_polymarket_overlap_rows[:10]:
        left = row.get("left") or {}
        quote = left.get("quote") or {}
        top_enriched_polymarket_targets.append(
            {
                "row_id": row.get("row_id"),
                "lane": row.get("lane"),
                "review_priority_score": row.get("review_priority_score"),
                "allowed_next_action": row.get("allowed_next_action"),
                "market_id_or_conid": left.get("market_id_or_conid"),
                "ticker_or_symbol": left.get("ticker_or_symbol"),
                "title": left.get("title"),
                "underlying": left.get("underlying"),
                "settlement_event_date": left.get("settlement_event_date"),
                "threshold": left.get("threshold"),
                "bid": quote.get("bid"),
                "ask": quote.get("ask"),
                "bid_size": quote.get("bid_size"),
                "ask_size": quote.get("ask_size"),
                "quote_timestamp": quote.get("timestamp"),
                "raw_book_file": quote.get("raw_book_file") or left.get("raw_book_file"),
                "top_blockers": (row.get("blockers") or [])[:5],
            }
        )
    refresh_summary = _polymarket_clob_refresh_materiality(inputs)
    core_trio_top_lane_summary = _core_trio_crypto_lane_summary(rows, active_platforms=active_platforms)
    return {
        "scout_row_count": len(rows),
        "lane_counts": dict(lane_counts),
        "active_platforms": sorted(active_platforms) if active_platforms is not None else [],
        "active_platform_filter_enabled": active_platforms is not None,
        "active_ranked_rows": len(ranked_rows),
        "inactive_platform_rows": inactive_platform_rows,
        "active_lane_counts": dict(active_lane_counts),
        "action_counts": dict(action_counts),
        "top_blockers": top_blockers,
        "exact_ready_rows": exact_ready,
        "paper_candidate_rows": paper_candidate,
        "execution_ready_rows": execution_ready,
        "top_lane": top_lane,
        "all_platform_top_lane": all_platform_top_lane,
        "core_trio_top_lane_summary": core_trio_top_lane_summary,
        "core_trio_top_lane": _core_trio_top_lane(core_trio_top_lane_summary),
        "top_10_review_targets": top_targets,
        "polymarket_rows_loaded": len(base_poly_rows) or len(enriched_poly_rows),
        "polymarket_enriched_rows_loaded": len(enriched_poly_rows),
        "polymarket_rows_with_bid_ask_size": enriched_rows_with_bid_ask_size,
        "polymarket_rows_with_timestamp": enriched_rows_with_timestamp,
        "polymarket_overlap_rows": len(polymarket_overlap_rows),
        "top_enriched_polymarket_review_targets": top_enriched_polymarket_targets,
        "polymarket_enriched_report_path": inputs.get("polymarket_taxonomy_shape_scout_enriched_path"),
        "polymarket_clob_refresh_materially_reduced_quote_blockers": refresh_summary.get("materially_reduced_quote_blockers"),
        "polymarket_clob_refresh_blocker_reduction": refresh_summary,
    }


def _core_trio_crypto_lane_summary(
    rows: list[dict[str, Any]],
    *,
    active_platforms: set[str] | None,
) -> list[dict[str, Any]]:
    labels = {
        LANE_POLYMARKET_CRYPTO_VS_KALSHI_CRYPTO: "Kalshi/Polymarket crypto",
        LANE_CDNA_BTC_VS_KALSHI_BTC: "Kalshi/CDNA crypto",
        LANE_POLYMARKET_CRYPTO_VS_CDNA_CRYPTO: "Polymarket/CDNA crypto",
    }
    out: list[dict[str, Any]] = []
    for lane in CORE_TRIO_CRYPTO_LANES:
        lane_rows = [row for row in rows if row.get("lane") == lane]
        active_rows = (
            [row for row in lane_rows if row.get("active_platform_status") == "active"]
            if active_platforms is not None
            else lane_rows
        )
        best = active_rows[0] if active_rows else (lane_rows[0] if lane_rows else None)
        out.append(
            {
                "label": labels[lane],
                "lane": lane,
                "rows": len(lane_rows),
                "active_ranked_rows": len(active_rows),
                "best_score": (best or {}).get("review_priority_score"),
                "best_action": (best or {}).get("allowed_next_action"),
                "best_row_id": (best or {}).get("row_id"),
                "top_blockers": ((best or {}).get("blockers") or [])[:5],
            }
        )
    return out


def _core_trio_top_lane(summary_rows: list[dict[str, Any]]) -> str | None:
    ranked = [row for row in summary_rows if int(row.get("active_ranked_rows") or row.get("rows") or 0) > 0]
    if not ranked:
        return None
    ranked.sort(
        key=lambda row: (
            int(row.get("active_ranked_rows") or 0),
            float(row.get("best_score") or 0),
        ),
        reverse=True,
    )
    return ranked[0].get("lane")


def _safety_block() -> dict[str, bool]:
    return {
        "diagnostic_only": True,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "exact_ready": False,
        "execution_ready": False,
        "paper_candidate": False,
        "affects_evaluator_gates": False,
        "source_registry_unchanged": True,
        "ibkr_kalshi_fake_edge_blockers_respected": True,
        "saved_files_only": True,
        "live_fetch_attempted": False,
        "execution_or_order_logic_added": False,
        "account_or_auth_logic_added": False,
    }


def _input_path(inputs: dict[str, Any], name: str) -> Path:
    input_dir = inputs.get("input_dir") or Path(".")
    relpath = _DEFAULT_INPUT_FILES.get(name, name)
    return Path(input_dir) / relpath


def _md_cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "/").replace("\n", " ")


def _row_sort_key(row: dict[str, Any]) -> tuple[float, int, float, str]:
    score = -float(row.get("review_priority_score") or 0.0)
    left = row.get("left") or {}
    right = row.get("right") or {}
    quote_complete_rank = 0 if (
        (left.get("quote") or {}).get("complete") and (right.get("quote") or {}).get("complete")
    ) else 1
    # Closer threshold distance ranks higher; for unpaired or non-numeric thresholds, default to large.
    try:
        lt = float(left.get("threshold"))
        rt = float(right.get("threshold"))
        threshold_proximity = abs(lt - rt)
    except (TypeError, ValueError):
        threshold_proximity = 99.0
    return (score, quote_complete_rank, threshold_proximity, str(row.get("row_id") or ""))
