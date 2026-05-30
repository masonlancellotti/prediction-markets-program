from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.fees import KalshiTieredFeeModel, PolymarketConservativeFeeModel
from relative_value.operator_paper_candidate_policy import (
    ACTION_PAPER,
    CLASS_CDNA,
    CLASS_OPERATOR,
    apply_operator_candidate_fields,
    candidate_counts,
    ensure_candidate_fields,
    has_hard_blocker,
    normalize_operator_risk_mode,
)


SCHEMA_KIND = "three_venue_operator_scout_v1"

ACTION_OPERATOR = "OPERATOR_ARB_PAPER_REVIEW"
ACTION_CDNA_FILL_FIRST = "CDNA_FILL_FIRST_REVIEW"
ACTION_CDNA_REFERENCE = "CDNA_REFERENCE_ONLY"
ACTION_MANUAL = "MANUAL_REVIEW"
ACTION_WATCH = "WATCH"
ACTION_IGNORE = "IGNORE_BLOCKED"

B_MISSING_PLATFORM_PEER = "missing_platform_peer"
B_MISSING_COMPLEMENT = "missing_complement_quote"
B_MISSING_QUOTE = "missing_quote"
B_STALE = "stale_quote"
B_MISSING_DEPTH = "missing_depth"
B_FEE_REVIEW = "fee_review_required"
B_SOURCE_MISMATCH = "source_mismatch"
B_SETTLEMENT_MISMATCH = "settlement_mismatch"
B_OTHER_UNMAPPED = "other_outcome_unmapped"
B_TITLE_ONLY = "title_similarity_only_not_equivalence"
B_CDNA_DISPLAY = "cdna_display_price_only"
B_CDNA_SIZE = "cdna_executable_size_unverified"
B_CDNA_DEPTH = "cdna_no_orderbook_depth"
B_CDNA_SERVER = "cdna_no_server_side_quote"
B_CDNA_PARTIAL = "cdna_partial_fill_risk"
B_CDNA_ACCEPT = "cdna_operator_acceptance_required"
B_INSUFFICIENT_NOTIONAL = "insufficient_available_notional"
B_NO_POSITIVE_EDGE = "no_positive_edge"
B_NO_ROWS = "no_candidate_rows_generated"
B_UNSUPPORTED_SCHEMA = "unsupported_schema"
B_NO_ACTIVE_OUTCOMES = "no_active_outcomes_loaded"
B_FAILED_MATCHING = "failed_team_matching"
B_MISSING_FILES = "missing_platform_files"

DEFAULT_CDNA_FEE = 0.02

CDNA_INFO_BLOCKERS = {B_CDNA_DISPLAY, B_CDNA_SIZE, B_CDNA_DEPTH, B_CDNA_SERVER, B_CDNA_PARTIAL}


def write_three_venue_operator_scout_files(
    *,
    family_folders: list[Path],
    include_cdna: bool,
    operator_accept_cdna_display_price_risk: bool,
    cdna_operator_size_cap: float,
    max_quote_age_seconds: float,
    min_available_notional: float,
    json_output: Path,
    markdown_output: Path,
    allow_stale_for_diagnostic: bool = False,
    operator_risk_mode: str = "conservative",
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_three_venue_operator_scout_report(
        family_folders=family_folders,
        include_cdna=include_cdna,
        operator_accept_cdna_display_price_risk=operator_accept_cdna_display_price_risk,
        cdna_operator_size_cap=cdna_operator_size_cap,
        max_quote_age_seconds=max_quote_age_seconds,
        min_available_notional=min_available_notional,
        allow_stale_for_diagnostic=allow_stale_for_diagnostic,
        operator_risk_mode=operator_risk_mode,
        generated_at=generated_at,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_three_venue_operator_scout_markdown(report), encoding="utf-8")
    return report


def build_three_venue_operator_scout_report(
    *,
    family_folders: list[Path],
    include_cdna: bool = False,
    operator_accept_cdna_display_price_risk: bool = False,
    cdna_operator_size_cap: float = 1.0,
    max_quote_age_seconds: float = 900.0,
    min_available_notional: float = 10.0,
    allow_stale_for_diagnostic: bool = False,
    operator_risk_mode: str = "conservative",
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    risk_mode = normalize_operator_risk_mode(operator_risk_mode)
    all_rows: list[dict[str, Any]] = []
    family_summaries: list[dict[str, Any]] = []
    warnings: list[str] = []
    for folder in family_folders:
        requested_folder = folder
        folder, resolution_warning = _resolve_family_folder(folder)
        if resolution_warning:
            warnings.append(resolution_warning)
        if not folder.exists():
            warnings.append(f"family_folder_missing:{requested_folder}")
            family_summaries.append(_missing_folder_diagnostics(requested_folder))
            continue
        family_result = _scan_family(
            folder=folder,
            include_cdna=include_cdna,
            operator_accept_cdna_display_price_risk=operator_accept_cdna_display_price_risk,
            cdna_operator_size_cap=cdna_operator_size_cap,
            max_quote_age_seconds=max_quote_age_seconds,
            min_available_notional=min_available_notional,
            allow_stale_for_diagnostic=allow_stale_for_diagnostic,
            operator_risk_mode=risk_mode,
            generated_at=generated,
        )
        if resolution_warning:
            family_result["summary"]["requested_family_folder"] = str(requested_folder)
            family_result["summary"]["load_warnings"] = sorted(set((family_result["summary"].get("load_warnings") or []) + [resolution_warning]))
        family_summaries.append(family_result["summary"])
        all_rows.extend(family_result["rows"])
    all_rows.sort(key=_row_sort_key, reverse=True)
    for idx, row in enumerate(all_rows, start=1):
        row["row_id"] = f"three_venue_{idx:04d}"
    summary = _summary(all_rows, warnings, family_summaries)
    return {
        "schema_kind": SCHEMA_KIND,
        "diagnostic_only": True,
        "saved_files_only": True,
        "strict_exact_arb": False,
        "exact_ready_rows": 0,
        "standard_paper_candidate_rows": summary.get("total_paper_candidate_rows", 0),
        "standard_paper_candidate_emitted": summary.get("total_paper_candidate_rows", 0) > 0,
        "candidate_pair_creation": False,
        "family_folders": [str(path) for path in family_folders],
        "include_cdna": bool(include_cdna),
        "operator_accept_cdna_display_price_risk": bool(operator_accept_cdna_display_price_risk),
        "operator_risk_mode": risk_mode,
        "parameters": {
            "cdna_operator_size_cap": cdna_operator_size_cap,
            "max_quote_age_seconds": max_quote_age_seconds,
            "min_available_notional": min_available_notional,
            "allow_stale_for_diagnostic": bool(allow_stale_for_diagnostic),
            "operator_risk_mode": risk_mode,
        },
        "family_summaries": family_summaries,
        "load_diagnostics": family_summaries,
        "rows": all_rows,
        "summary_counts": summary,
        "top_candidates_by_lane": _top_by_lane(all_rows),
        "top_blockers": summary["top_blockers"],
        "recommended_execution_notes": [
            "All venues are evaluated in one candidate-generation pass.",
            "CDNA fill-first is an execution plan for baskets that include CDNA, not a separate post-scan check.",
            "For CDNA rows, fill CDNA first at capped size, then hedge exact filled quantity on the partner venue after a manual fill is confirmed.",
        ],
        "warnings": warnings,
        "safety": {
            "diagnostic_only": True,
            "saved_files_only": True,
            "strict_exact_arb": False,
            "exact_ready_rows": 0,
            "standard_paper_candidate_rows": summary.get("total_paper_candidate_rows", 0),
            "candidate_pair_creation": False,
            "orders_or_execution_logic_added": False,
            "auth_or_account_logic_added": False,
            "uses_midpoint": False,
            "uses_asks_for_entry": True,
        },
    }


def _resolve_family_folder(folder: Path) -> tuple[Path, str | None]:
    if folder.exists():
        return folder, None
    lower_parts = [part.lower() for part in folder.parts]
    if "automation_batch_003" in lower_parts and folder.name.lower() in {"mlb_daily_games", "mlb_daily"}:
        batch_index = lower_parts.index("automation_batch_003")
        batch_root = Path(*folder.parts[: batch_index + 1])
        matches = [path for path in batch_root.rglob("*mlb_daily*") if path.is_dir() and any(path.glob("*.json"))]
        if len(matches) == 1:
            return matches[0], f"family_folder_resolved:{folder}->{matches[0]}"
    return folder, None


def render_three_venue_operator_scout_markdown(report: dict[str, Any]) -> str:
    counts = report.get("summary_counts") or {}
    rows = report.get("rows") or []
    lines = [
        "# Three-Venue Operator Scout",
        "",
        "All three venues are evaluated in one scan. CDNA fill-first is an execution plan for baskets involving CDNA, not a separate scan stage. CDNA candidates are ranked alongside Kalshi/Polymarket candidates, but CDNA still requires manual fill confirmation before hedging exact filled quantity.",
        "",
        "## Summary",
        "",
        f"- rows: `{counts.get('rows', 0)}`",
        f"- kalshi_poly_rows: `{counts.get('kalshi_poly_rows', 0)}`",
        f"- cdna_kalshi_rows: `{counts.get('cdna_kalshi_rows', 0)}`",
        f"- cdna_poly_rows: `{counts.get('cdna_poly_rows', 0)}`",
        f"- strict_paper_candidate_rows: `{counts.get('strict_paper_candidate_rows', 0)}`",
        f"- operator_paper_candidate_rows: `{counts.get('operator_paper_candidate_rows', 0)}`",
        f"- cdna_fill_first_paper_candidate_rows: `{counts.get('cdna_fill_first_paper_candidate_rows', 0)}`",
        f"- total_paper_candidate_rows: `{counts.get('total_paper_candidate_rows', 0)}`",
        f"- watch_rows: `{counts.get('watch_rows', 0)}`",
        f"- ignore_blocked_rows: `{counts.get('ignore_blocked_rows', 0)}`",
        f"- exact_ready_rows: `0`",
        "",
        "## Load Diagnostics",
        "",
        "| Family folder | Files | Kalshi | Polymarket | CDNA | Outcomes K/P/C | Warnings |",
        "|---|---:|---|---|---|---|---|",
    ]
    for diag in report.get("load_diagnostics") or []:
        lines.append(
            "| "
            f"{_md(diag.get('family_folder'))} | {_md(diag.get('files_found_count'))} | "
            f"{_md(diag.get('kalshi_file_path'))} | {_md(diag.get('polymarket_file_path'))} | {_md(diag.get('cdna_file_path'))} | "
            f"{_md(diag.get('kalshi_outcomes_loaded'))}/{_md(diag.get('polymarket_outcomes_loaded'))}/{_md(diag.get('cdna_outcomes_loaded'))} | "
            f"{_md(', '.join(diag.get('load_warnings') or []))} |"
        )
    lines.append("")
    lines.extend(_table("Paper Candidates", [r for r in rows if r.get("paper_candidate")][:25]))
    lines.extend(_table("Watch Rows", [r for r in rows if r.get("action") == ACTION_WATCH][:25]))
    lines.extend(_table("Ignored/Blocked Rows", [r for r in rows if r.get("action") == ACTION_IGNORE][:25]))
    lines.extend(["## Top Blockers", "", "| Blocker | Count |", "|---|---:|"])
    for item in report.get("top_blockers") or []:
        lines.append(f"| {_md(item.get('blocker'))} | {_md(item.get('count'))} |")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- diagnostic_only: `true`",
            "- saved_files_only: `true`",
            "- strict_exact_arb: `false`",
            "- exact_ready_rows: `0`",
            f"- total_paper_candidate_rows: `{counts.get('total_paper_candidate_rows', 0)}`",
            "- no order placement or execution logic",
        ]
    )
    return "\n".join(lines) + "\n"


def _scan_family(
    *,
    folder: Path,
    include_cdna: bool,
    operator_accept_cdna_display_price_risk: bool,
    cdna_operator_size_cap: float,
    max_quote_age_seconds: float,
    min_available_notional: float,
    allow_stale_for_diagnostic: bool,
    operator_risk_mode: str,
    generated_at: datetime,
) -> dict[str, Any]:
    payloads = _load_payloads(folder)
    family = _family_key(payloads[0] if payloads else {}, folder)
    payload_by_platform = {
        "kalshi": _first_payload(payloads, "kalshi"),
        "polymarket": _first_payload(payloads, "polymarket"),
        "cdna": _first_payload(payloads, "cdna") if include_cdna else None,
    }
    platform_rows = {
        "kalshi": _extract_rows(payload_by_platform["kalshi"], "kalshi"),
        "polymarket": _extract_rows(payload_by_platform["polymarket"], "polymarket"),
        "cdna": _extract_rows(payload_by_platform["cdna"], "cdna") if include_cdna else [],
    }
    by_platform = {platform: {row["outcome_key"]: row for row in rows if row.get("outcome_key")} for platform, rows in platform_rows.items()}
    outcome_keys = sorted(set().union(*(set(rows) for rows in by_platform.values())))
    rows: list[dict[str, Any]] = []
    for key in outcome_keys:
        k = by_platform["kalshi"].get(key)
        p = by_platform["polymarket"].get(key)
        c = by_platform["cdna"].get(key)
        if k and p:
            rows.append(
                _basket_row(
                    market_family=family,
                    outcome_key=key,
                    outcome=_outcome_name(k, p),
                    basket_type="kalshi_poly",
                    direction="KALSHI_YES_POLYMARKET_NO",
                    leg_1=_entry_leg(k, "YES", cdna_operator_size_cap),
                    leg_2=_entry_leg(p, "NO", cdna_operator_size_cap),
                    generated_at=generated_at,
                    max_quote_age_seconds=max_quote_age_seconds,
                    min_available_notional=min_available_notional,
                    operator_accept_cdna_display_price_risk=operator_accept_cdna_display_price_risk,
                    allow_stale_for_diagnostic=allow_stale_for_diagnostic,
                    operator_risk_mode=operator_risk_mode,
                )
            )
            rows.append(
                _basket_row(
                    market_family=family,
                    outcome_key=key,
                    outcome=_outcome_name(k, p),
                    basket_type="kalshi_poly",
                    direction="POLYMARKET_YES_KALSHI_NO",
                    leg_1=_entry_leg(p, "YES", cdna_operator_size_cap),
                    leg_2=_entry_leg(k, "NO", cdna_operator_size_cap),
                    generated_at=generated_at,
                    max_quote_age_seconds=max_quote_age_seconds,
                    min_available_notional=min_available_notional,
                    operator_accept_cdna_display_price_risk=operator_accept_cdna_display_price_risk,
                    allow_stale_for_diagnostic=allow_stale_for_diagnostic,
                    operator_risk_mode=operator_risk_mode,
                )
            )
        if c and k:
            rows.append(
                _basket_row(
                    market_family=family,
                    outcome_key=key,
                    outcome=_outcome_name(c, k),
                    basket_type="cdna_kalshi",
                    direction="CDNA_YES_KALSHI_NO",
                    leg_1=_entry_leg(c, "YES", cdna_operator_size_cap),
                    leg_2=_entry_leg(k, "NO", cdna_operator_size_cap),
                    generated_at=generated_at,
                    max_quote_age_seconds=max_quote_age_seconds,
                    min_available_notional=min_available_notional,
                    operator_accept_cdna_display_price_risk=operator_accept_cdna_display_price_risk,
                    allow_stale_for_diagnostic=allow_stale_for_diagnostic,
                    operator_risk_mode=operator_risk_mode,
                )
            )
            rows.append(
                _basket_row(
                    market_family=family,
                    outcome_key=key,
                    outcome=_outcome_name(c, k),
                    basket_type="cdna_kalshi",
                    direction="CDNA_NO_KALSHI_YES",
                    leg_1=_entry_leg(c, "NO", cdna_operator_size_cap),
                    leg_2=_entry_leg(k, "YES", cdna_operator_size_cap),
                    generated_at=generated_at,
                    max_quote_age_seconds=max_quote_age_seconds,
                    min_available_notional=min_available_notional,
                    operator_accept_cdna_display_price_risk=operator_accept_cdna_display_price_risk,
                    allow_stale_for_diagnostic=allow_stale_for_diagnostic,
                    operator_risk_mode=operator_risk_mode,
                )
            )
        if c and p:
            rows.append(
                _basket_row(
                    market_family=family,
                    outcome_key=key,
                    outcome=_outcome_name(c, p),
                    basket_type="cdna_poly",
                    direction="CDNA_YES_POLYMARKET_NO",
                    leg_1=_entry_leg(c, "YES", cdna_operator_size_cap),
                    leg_2=_entry_leg(p, "NO", cdna_operator_size_cap),
                    generated_at=generated_at,
                    max_quote_age_seconds=max_quote_age_seconds,
                    min_available_notional=min_available_notional,
                    operator_accept_cdna_display_price_risk=operator_accept_cdna_display_price_risk,
                    allow_stale_for_diagnostic=allow_stale_for_diagnostic,
                    operator_risk_mode=operator_risk_mode,
                )
            )
            rows.append(
                _basket_row(
                    market_family=family,
                    outcome_key=key,
                    outcome=_outcome_name(c, p),
                    basket_type="cdna_poly",
                    direction="CDNA_NO_POLYMARKET_YES",
                    leg_1=_entry_leg(c, "NO", cdna_operator_size_cap),
                    leg_2=_entry_leg(p, "YES", cdna_operator_size_cap),
                    generated_at=generated_at,
                    max_quote_age_seconds=max_quote_age_seconds,
                    min_available_notional=min_available_notional,
                    operator_accept_cdna_display_price_risk=operator_accept_cdna_display_price_risk,
                    allow_stale_for_diagnostic=allow_stale_for_diagnostic,
                    operator_risk_mode=operator_risk_mode,
                )
            )
        if sum(1 for x in (k, p, c) if x) < 2:
            source = k or p or c
            if source:
                rows.append(_missing_peer_row(family, key, source))
    diagnostics = _load_diagnostics(folder, payloads, payload_by_platform, platform_rows, rows, include_cdna)
    return {
        "summary": diagnostics,
        "rows": rows,
    }


def _basket_row(
    *,
    market_family: str,
    outcome_key: str,
    outcome: str,
    basket_type: str,
    direction: str,
    leg_1: dict[str, Any],
    leg_2: dict[str, Any],
    generated_at: datetime,
    max_quote_age_seconds: float,
    min_available_notional: float,
    operator_accept_cdna_display_price_risk: bool,
    allow_stale_for_diagnostic: bool,
    operator_risk_mode: str,
) -> dict[str, Any]:
    price_1 = _float(leg_1.get("entry_price"))
    price_2 = _float(leg_2.get("entry_price"))
    fee_1 = _float(leg_1.get("fee"))
    fee_2 = _float(leg_2.get("fee"))
    entry_cost = round(price_1 + price_2, 8) if price_1 is not None and price_2 is not None else None
    gross = round(1.0 - entry_cost, 8) if entry_cost is not None else None
    fee = round((fee_1 or 0.0) + (fee_2 or 0.0), 8) if fee_1 is not None and fee_2 is not None and gross is not None else None
    net = round(gross - fee, 8) if gross is not None and fee is not None else None
    available = _available_notional(leg_1, leg_2)
    cdna = basket_type.startswith("cdna_")
    blockers: list[str] = []
    risk_notes: list[str] = []
    if price_1 is None or price_2 is None:
        blockers.append(B_MISSING_QUOTE)
        blockers.append(B_MISSING_COMPLEMENT)
    if fee is None:
        blockers.append(B_FEE_REVIEW)
    stale = _stale(leg_1.get("quote_timestamp"), generated_at, max_quote_age_seconds) or _stale(leg_2.get("quote_timestamp"), generated_at, max_quote_age_seconds)
    if stale:
        blockers.append(B_STALE)
    if available is None:
        blockers.append(B_MISSING_DEPTH)
    elif not cdna and available < min_available_notional:
        blockers.append(B_INSUFFICIENT_NOTIONAL)
    if gross is not None and gross <= 0:
        blockers.append(B_NO_POSITIVE_EDGE)
    if cdna:
        blockers.extend([B_CDNA_DISPLAY, B_CDNA_SIZE, B_CDNA_DEPTH, B_CDNA_SERVER, B_CDNA_PARTIAL])
        risk_notes.append("CDNA display prices are indicative; fill CDNA first at capped size, then hedge exact filled quantity after manual fill confirmation.")
        if not operator_accept_cdna_display_price_risk:
            blockers.append(B_CDNA_ACCEPT)
    blockers = sorted(set(blockers))
    action = _action(blockers, basket_type=basket_type, gross=gross, net=net, operator_accept_cdna_display_price_risk=operator_accept_cdna_display_price_risk, allow_stale_for_diagnostic=allow_stale_for_diagnostic)
    row = {
        "row_id": "",
        "market_family": market_family,
        "outcome_key": outcome_key,
        "outcome": outcome,
        "basket_type": basket_type,
        "direction": direction,
        "leg_1": leg_1,
        "leg_2": leg_2,
        "entry_cost": entry_cost,
        "normal_settlement_payoff": 1.0,
        "gross_edge": gross,
        "conservative_fee_estimate": fee,
        "net_edge": net,
        "available_notional": available,
        "execution_plan": "FILL_CDNA_FIRST_THEN_HEDGE_EXACT_FILLED_QUANTITY" if cdna else "TWO_EXECUTABLE_ORDERBOOK_LEGS",
        "requires_cdna_fill_first": cdna,
        "cdna_assumed_fill_quantity": _cdna_quantity(leg_1, leg_2) if cdna else None,
        "blockers": blockers,
        "risk_notes": risk_notes,
        "action": action,
        "standard_paper_candidate": False,
        "exact_ready": False,
        "strict_exact_arb": False,
        "diagnostic_only": True,
    }
    cdna = basket_type.startswith("cdna_")
    make_candidate = False
    paper_class = CLASS_OPERATOR
    assumptions: list[str] = []
    candidate_action = "PAPER_TEST_OPERATOR_ACCEPTED_BASKET"
    if cdna:
        paper_class = CLASS_CDNA
        assumptions = [
            "cdna_display_price_assumed_fillable_at_operator_cap",
            "cdna_executable_size_unverified_pre_fill",
        ]
        candidate_action = "FILL_CDNA_FIRST_THEN_HEDGE_EXACT_FILLED_QUANTITY"
        make_candidate = (
            operator_risk_mode == "aggressive"
            and operator_accept_cdna_display_price_risk
            and gross is not None
            and gross > 0
            and not has_hard_blocker(blockers, ignore_cdna_info=True)
        )
    else:
        assumptions = ["operator_accepted_sports_market_residual_risk"]
        make_candidate = (
            operator_risk_mode in {"standard", "aggressive"}
            and net is not None
            and net > 0
            and not has_hard_blocker(blockers)
        )
    return apply_operator_candidate_fields(
        row,
        paper_class=paper_class,
        assumptions_accepted=assumptions,
        candidate_action=candidate_action,
        make_candidate=make_candidate,
        mathematical_strict_exact_arb=False,
    )


def _missing_peer_row(family: str, outcome_key: str, source: dict[str, Any]) -> dict[str, Any]:
    return ensure_candidate_fields({
        "row_id": "",
        "market_family": family,
        "outcome_key": outcome_key,
        "outcome": source.get("outcome"),
        "basket_type": "unmatched",
        "direction": "UNMATCHED",
        "leg_1": {"platform": source.get("platform"), "side": None},
        "leg_2": None,
        "entry_cost": None,
        "normal_settlement_payoff": 1.0,
        "gross_edge": None,
        "net_edge": None,
        "available_notional": None,
        "execution_plan": "NO_MATCHED_BASKET",
        "requires_cdna_fill_first": False,
        "cdna_assumed_fill_quantity": None,
        "blockers": [B_MISSING_PLATFORM_PEER],
        "risk_notes": [],
        "action": ACTION_IGNORE,
        "standard_paper_candidate": False,
        "exact_ready": False,
        "strict_exact_arb": False,
        "diagnostic_only": True,
    })


def _action(
    blockers: list[str],
    *,
    basket_type: str,
    gross: float | None,
    net: float | None,
    operator_accept_cdna_display_price_risk: bool,
    allow_stale_for_diagnostic: bool,
) -> str:
    stale_only = allow_stale_for_diagnostic and B_STALE in blockers
    effective_blockers = [b for b in blockers if b != B_STALE] if allow_stale_for_diagnostic else blockers
    if basket_type.startswith("cdna_"):
        hard = set(effective_blockers) - CDNA_INFO_BLOCKERS
        if gross is not None and gross > 0 and operator_accept_cdna_display_price_risk and hard <= set():
            return ACTION_WATCH
        if B_CDNA_ACCEPT in blockers or (gross is not None and gross > 0):
            return ACTION_WATCH
        if B_MISSING_QUOTE in blockers or B_STALE in blockers:
            return ACTION_WATCH
        return ACTION_WATCH
    if B_MISSING_QUOTE in blockers or B_MISSING_DEPTH in blockers or (B_STALE in blockers and not allow_stale_for_diagnostic):
        return ACTION_WATCH
    if B_FEE_REVIEW in blockers:
        return ACTION_WATCH
    if net is not None and net > 0 and B_INSUFFICIENT_NOTIONAL not in blockers and B_NO_POSITIVE_EDGE not in blockers:
        return ACTION_WATCH
    if gross is not None and gross > 0:
        return ACTION_WATCH
    return ACTION_IGNORE


def _load_payloads(folder: Path) -> list[dict[str, Any]]:
    paths = [folder] if folder.is_file() else list(folder.glob("*.json"))
    payloads = []
    for path in paths:
        if path.name.lower() == "collection_summary.md":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            payload["_source_file"] = str(path)
            payloads.append(payload)
    return payloads


def _missing_folder_diagnostics(folder: Path) -> dict[str, Any]:
    return {
        "family_folder": str(folder),
        "market_family": _family_key({}, folder),
        "files_found": [],
        "files_found_count": 0,
        "kalshi_file_path": None,
        "polymarket_file_path": None,
        "cdna_file_path": None,
        "kalshi_outcomes_loaded": 0,
        "polymarket_outcomes_loaded": 0,
        "cdna_outcomes_loaded": 0,
        "kalshi_active_outcomes_loaded": 0,
        "polymarket_active_outcomes_loaded": 0,
        "cdna_active_outcomes_loaded": 0,
        "platform_schema_kinds": {},
        "load_warnings": [f"family_folder_missing:{folder}", B_NO_ROWS],
        "parse_warnings": [],
        "top_level_blockers": [B_NO_ROWS, B_MISSING_FILES],
        "candidate_rows": 0,
    }


def _load_diagnostics(
    folder: Path,
    payloads: list[dict[str, Any]],
    payload_by_platform: dict[str, dict[str, Any] | None],
    platform_rows: dict[str, list[dict[str, Any]]],
    rows: list[dict[str, Any]],
    include_cdna: bool,
) -> dict[str, Any]:
    warnings: list[str] = []
    parse_warnings: list[str] = []
    blockers: list[str] = []
    if not payloads:
        warnings.append("no_json_payloads_loaded")
        blockers.extend([B_NO_ROWS, B_MISSING_FILES])
    for platform in ("kalshi", "polymarket", "cdna"):
        if platform == "cdna" and not include_cdna:
            continue
        if payload_by_platform.get(platform) is None:
            warnings.append(f"{platform}_file_missing")
            blockers.append(B_MISSING_FILES)
    supported_prefixes = (
        "raw_market_family_evidence_v",
        "new_market_family_evidence_v",
        "polished_crypto_market_family_evidence_v1",
        "cdna_fill_first_family_evidence_v1",
        "sports_",
        "test_",
    )
    for payload in payloads:
        schema = str(payload.get("schema_kind") or "")
        if schema and not schema.startswith(supported_prefixes):
            parse_warnings.append(f"unsupported_schema:{schema}")
            blockers.append(B_UNSUPPORTED_SCHEMA)
    if not any(platform_rows.values()):
        blockers.append(B_NO_ACTIVE_OUTCOMES)
    if rows == []:
        blockers.append(B_NO_ROWS)
        if any(platform_rows.values()):
            blockers.append(B_FAILED_MATCHING)
    schema_kinds = {
        platform: payload.get("schema_kind")
        for platform, payload in payload_by_platform.items()
        if payload is not None
    }
    return {
        "family_folder": str(folder),
        "market_family": _family_key(payloads[0] if payloads else {}, folder),
        "files_found": [str(payload.get("_source_file")) for payload in payloads if payload.get("_source_file")],
        "files_found_count": len(payloads),
        "kalshi_file_path": _source_path(payload_by_platform.get("kalshi")),
        "polymarket_file_path": _source_path(payload_by_platform.get("polymarket")),
        "cdna_file_path": _source_path(payload_by_platform.get("cdna")),
        "kalshi_outcomes_loaded": len(platform_rows.get("kalshi") or []),
        "polymarket_outcomes_loaded": len(platform_rows.get("polymarket") or []),
        "cdna_outcomes_loaded": len(platform_rows.get("cdna") or []),
        "kalshi_active_outcomes_loaded": _active_count(platform_rows.get("kalshi") or []),
        "polymarket_active_outcomes_loaded": _active_count(platform_rows.get("polymarket") or []),
        "cdna_active_outcomes_loaded": _active_count(platform_rows.get("cdna") or []),
        "platform_schema_kinds": schema_kinds,
        "load_warnings": sorted(set(warnings)),
        "parse_warnings": sorted(set(parse_warnings)),
        "top_level_blockers": sorted(set(blockers)),
        "candidate_rows": len(rows),
    }


def _source_path(payload: dict[str, Any] | None) -> str | None:
    return str(payload.get("_source_file")) if payload is not None and payload.get("_source_file") else None


def _active_count(rows: list[dict[str, Any]]) -> int:
    return sum(1 for row in rows if str(row.get("outcome_status") or "active").lower() in {"active", "open", ""})


def _first_payload(payloads: list[dict[str, Any]], platform: str) -> dict[str, Any] | None:
    for payload in payloads:
        text = str(payload.get("platform") or "").lower()
        if platform == "cdna" and ("cdna" in text or "crypto.com" in text):
            return payload
        if platform in text:
            return payload
    return None


def _extract_rows(payload: dict[str, Any] | None, platform: str) -> list[dict[str, Any]]:
    if not payload:
        return []
    outcomes = payload.get("outcomes") if isinstance(payload.get("outcomes"), list) else []
    quote_defaults = _quote_defaults(payload, platform)
    rows = []
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        if platform == "polymarket" and _is_non_moneyline_sibling(outcome):
            continue
        name = outcome.get("team_name") or outcome.get("team") or outcome.get("outcome") or outcome.get("market") or outcome.get("outcome_name")
        key = _canonical_outcome(name)
        if not key:
            continue
        quote_key = quote_defaults.get("_quote_market_key")
        apply_defaults = not quote_key or quote_key == key or len(outcomes) == 1
        merged = dict(quote_defaults if apply_defaults else {"quote_timestamp": quote_defaults.get("quote_timestamp")})
        merged.update(outcome)
        row = {
            "platform": platform,
            "outcome_key": key,
            "outcome": str(name),
            "id": merged.get("ticker") or merged.get("market_ticker") or merged.get("market_id") or merged.get("contract_id") or merged.get("symbol"),
            "yes_bid": _float(merged.get("yes_bid") or merged.get("bestBid") or merged.get("best_bid") or merged.get("yes_dollars_bid")),
            "yes_ask": _float(merged.get("yes_ask") or merged.get("bestAsk") or merged.get("best_ask") or merged.get("yes_dollars_ask")),
            "yes_bid_size": _float(merged.get("yes_bid_size")),
            "yes_ask_size": _float(merged.get("yes_ask_size")),
            "no_bid": _float(merged.get("no_bid") or merged.get("no_dollars_bid")),
            "no_ask": _float(merged.get("no_ask") or merged.get("no_dollars_ask")),
            "no_bid_size": _float(merged.get("no_bid_size")),
            "no_ask_size": _float(merged.get("no_ask_size")),
            "display_price": _float(merged.get("display_price") or merged.get("display_yes") or merged.get("yes")),
            "display_no_price": _float(merged.get("display_no_price") or merged.get("display_no") or merged.get("no")),
            "outcome_status": merged.get("outcome_status") or merged.get("status") or "active",
            "symbol": merged.get("symbol"),
            "contract_id": merged.get("contract_id"),
            "depth_status": merged.get("depth_status") or quote_defaults.get("depth_status"),
            "quote_timestamp": merged.get("quote_timestamp") or quote_defaults.get("quote_timestamp"),
        }
        _fill_complement_quotes(row, platform)
        rows.append(row)
    return _augment_two_team_complements(rows, payload)


def _quote_defaults(payload: dict[str, Any], platform: str) -> dict[str, Any]:
    quotes = payload.get("quotes") if isinstance(payload.get("quotes"), dict) else {}
    defaults = {
        "quote_timestamp": quotes.get("quote_timestamp_utc") or quotes.get("quote_timestamp") or payload.get("quote_timestamp"),
        "_quote_market_key": _canonical_outcome(quotes.get("market")),
    }
    if platform == "kalshi":
        orderbook = quotes.get("orderbook_fp") if isinstance(quotes.get("orderbook_fp"), dict) else {}
        yes_top = _top_level(orderbook.get("yes_dollars_bids"))
        no_top = _top_level(orderbook.get("no_dollars_bids"))
        if yes_top:
            defaults["yes_bid"], defaults["yes_bid_size"] = yes_top
        if no_top:
            defaults["no_bid"], defaults["no_bid_size"] = no_top
            defaults["yes_ask"] = round(1.0 - no_top[0], 8)
            defaults["yes_ask_size"] = no_top[1]
        if yes_top:
            defaults["no_ask"] = round(1.0 - yes_top[0], 8)
            defaults["no_ask_size"] = yes_top[1]
        if yes_top and no_top:
            defaults["depth_status"] = "full_clob"
    elif platform == "polymarket":
        bid_top = _top_level(quotes.get("bids_top"))
        ask_top = _top_level(quotes.get("asks_top"))
        if bid_top:
            defaults["yes_bid"], defaults["yes_bid_size"] = bid_top
            defaults["no_ask"] = round(1.0 - bid_top[0], 8)
            defaults["no_ask_size"] = bid_top[1]
        if ask_top:
            defaults["yes_ask"], defaults["yes_ask_size"] = ask_top
            defaults["no_bid"] = round(1.0 - ask_top[0], 8)
            defaults["no_bid_size"] = ask_top[1]
        if bid_top or ask_top:
            defaults["depth_status"] = "top_of_book_only"
    elif platform == "cdna":
        defaults["depth_status"] = "display_price_only"
    return defaults


def _top_level(levels: Any) -> tuple[float, float] | None:
    if not isinstance(levels, list) or not levels:
        return None
    first = levels[0]
    if isinstance(first, dict):
        price = _float(first.get("price"))
        size = _float(first.get("size"))
    elif isinstance(first, list | tuple) and len(first) >= 2:
        price = _float(first[0])
        size = _float(first[1])
    else:
        return None
    if price is None or size is None:
        return None
    return price, size


def _fill_complement_quotes(row: dict[str, Any], platform: str) -> None:
    if platform == "cdna":
        return
    if row.get("yes_ask") is None and row.get("no_bid") is not None:
        row["yes_ask"] = round(1.0 - row["no_bid"], 8)
        row["yes_ask_size"] = row.get("no_bid_size")
    if row.get("no_ask") is None and row.get("yes_bid") is not None:
        row["no_ask"] = round(1.0 - row["yes_bid"], 8)
        row["no_ask_size"] = row.get("yes_bid_size")


def _augment_two_team_complements(rows: list[dict[str, Any]], payload: dict[str, Any]) -> list[dict[str, Any]]:
    if len(rows) == 1:
        extra = _derived_binary_opponent(rows[0], payload)
        return rows + ([extra] if extra else [])
    if len(rows) != 2:
        return rows
    quoted = [row for row in rows if row.get("yes_ask") is not None or row.get("no_ask") is not None]
    missing = [row for row in rows if row.get("yes_ask") is None and row.get("no_ask") is None]
    if len(quoted) != 1 or len(missing) != 1:
        return rows
    text = " ".join(str(payload.get(key) or "") for key in ("market_family", "market_title", "rules_text")).lower()
    if not any(token in text for token in ("daily", "game winner", " vs ", " vs.", "winner")):
        return rows
    source = quoted[0]
    target = dict(missing[0])
    target["yes_bid"] = source.get("no_bid")
    target["yes_bid_size"] = source.get("no_bid_size")
    target["yes_ask"] = source.get("no_ask")
    target["yes_ask_size"] = source.get("no_ask_size")
    target["no_bid"] = source.get("yes_bid")
    target["no_bid_size"] = source.get("yes_bid_size")
    target["no_ask"] = source.get("yes_ask")
    target["no_ask_size"] = source.get("yes_ask_size")
    target["depth_status"] = source.get("depth_status")
    target["quote_timestamp"] = target.get("quote_timestamp") or source.get("quote_timestamp")
    return [source, target] if rows.index(source) < rows.index(missing[0]) else [target, source]


def _derived_binary_opponent(source: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any] | None:
    if source.get("no_ask") is None and source.get("no_bid") is None:
        return None
    title = str(payload.get("market_title") or payload.get("rules_text") or "")
    parts = re.split(r"\s+v(?:s\.?|ersus)?\s+|@", title, flags=re.IGNORECASE)
    if len(parts) < 2:
        return None
    first_key = _canonical_outcome(parts[0])
    second_key = _canonical_outcome(parts[1])
    if source.get("outcome_key") not in {first_key, second_key}:
        return None
    opponent_key = second_key if source.get("outcome_key") == first_key else first_key
    opponent_name = parts[1].strip() if opponent_key == second_key else parts[0].strip()
    target = dict(source)
    target["outcome_key"] = opponent_key
    target["outcome"] = opponent_name
    target["id"] = f"derived_complement:{source.get('id')}"
    target["yes_bid"] = source.get("no_bid")
    target["yes_bid_size"] = source.get("no_bid_size")
    target["yes_ask"] = source.get("no_ask")
    target["yes_ask_size"] = source.get("no_ask_size")
    target["no_bid"] = source.get("yes_bid")
    target["no_bid_size"] = source.get("yes_bid_size")
    target["no_ask"] = source.get("yes_ask")
    target["no_ask_size"] = source.get("yes_ask_size")
    return target


def _entry_leg(row: dict[str, Any], side: str, cdna_operator_size_cap: float) -> dict[str, Any]:
    platform = row.get("platform")
    if platform == "cdna":
        price = row.get("display_price") if side == "YES" else row.get("display_no_price")
        quantity = cdna_operator_size_cap if price is not None else None
        fee = DEFAULT_CDNA_FEE if price is not None else None
        return {
            "platform": "cdna",
            "side": side,
            "entry_price": round(price + DEFAULT_CDNA_FEE, 8) if price is not None else None,
            "display_price": price,
            "fee": fee,
            "depth_status": "display_price_only",
            "available_quantity": quantity,
            "available_notional": round((price + DEFAULT_CDNA_FEE) * quantity, 8) if price is not None and quantity is not None else None,
            "quote_timestamp": row.get("quote_timestamp"),
            "id": row.get("contract_id") or row.get("symbol"),
        }
    prefix = side.lower()
    ask = row.get(f"{prefix}_ask")
    size = row.get(f"{prefix}_ask_size")
    platform_name = str(platform)
    fee = _fee_for(platform_name, ask)
    return {
        "platform": platform_name,
        "side": side,
        "entry_price": ask,
        "fee": fee,
        "depth_status": row.get("depth_status"),
        "available_quantity": size,
        "available_notional": round(ask * size, 8) if ask is not None and size is not None else None,
        "quote_timestamp": row.get("quote_timestamp"),
        "id": row.get("id"),
    }


def _fee_for(platform: str, price: float | None) -> float | None:
    if price is None:
        return None
    if platform == "kalshi":
        return KalshiTieredFeeModel().fee_for_leg(price)
    if platform == "polymarket":
        return PolymarketConservativeFeeModel().fee_for_leg_for_category(price, category="sports")
    return None


def _available_notional(a: dict[str, Any], b: dict[str, Any]) -> float | None:
    a_value = _float(a.get("available_notional"))
    b_value = _float(b.get("available_notional"))
    if a_value is None or b_value is None:
        return None
    return round(min(a_value, b_value), 8)


def _cdna_quantity(a: dict[str, Any], b: dict[str, Any]) -> float | None:
    for leg in (a, b):
        if leg.get("platform") == "cdna":
            return _float(leg.get("available_quantity"))
    return None


def _stale(value: Any, generated_at: datetime, max_age: float) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (generated_at - parsed.astimezone(timezone.utc)).total_seconds() > max_age


def _summary(rows: list[dict[str, Any]], warnings: list[str], family_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    actions = Counter(row.get("action") for row in rows)
    lanes = Counter(row.get("basket_type") for row in rows)
    blockers = Counter()
    for row in rows:
        blockers.update(row.get("blockers") or [])
    for item in family_summaries:
        blockers.update(item.get("top_level_blockers") or [])
    if not rows:
        blockers[B_NO_ROWS] += 1
    summary = {
        "rows": len(rows),
        "kalshi_poly_rows": lanes.get("kalshi_poly", 0),
        "cdna_kalshi_rows": lanes.get("cdna_kalshi", 0),
        "cdna_poly_rows": lanes.get("cdna_poly", 0),
        "operator_review_rows": actions.get(ACTION_OPERATOR, 0),
        "cdna_fill_first_review_rows": actions.get(ACTION_CDNA_FILL_FIRST, 0),
        "cdna_reference_only_rows": actions.get(ACTION_CDNA_REFERENCE, 0),
        "manual_review_rows": actions.get(ACTION_MANUAL, 0),
        "watch_rows": actions.get(ACTION_WATCH, 0),
        "ignore_blocked_rows": actions.get(ACTION_IGNORE, 0),
        "exact_ready_rows": 0,
        "standard_paper_candidate_rows": 0,
        "warnings": len(warnings),
        "top_blockers": [{"blocker": key, "count": value} for key, value in blockers.most_common(15)],
    }
    summary.update(candidate_counts(rows))
    return summary


def _top_by_lane(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out = {}
    for lane in ("kalshi_poly", "cdna_kalshi", "cdna_poly"):
        out[lane] = [
            {
                "row_id": row.get("row_id"),
                "market_family": row.get("market_family"),
                "outcome": row.get("outcome"),
                "direction": row.get("direction"),
                "action": row.get("action"),
                "gross_edge": row.get("gross_edge"),
                "net_edge": row.get("net_edge"),
                "available_notional": row.get("available_notional"),
                "blockers": row.get("blockers"),
            }
            for row in rows
            if row.get("basket_type") == lane
        ][:10]
    return out


def _row_sort_key(row: dict[str, Any]) -> tuple[int, float, float]:
    action_score = {ACTION_PAPER: 6, ACTION_OPERATOR: 5, ACTION_CDNA_FILL_FIRST: 4, ACTION_MANUAL: 3, ACTION_CDNA_REFERENCE: 2, ACTION_WATCH: 1, ACTION_IGNORE: 0}.get(row.get("action"), 0)
    return (action_score, _float(row.get("net_edge")) or -999.0, _float(row.get("gross_edge")) or -999.0)


def _canonical_outcome(value: Any) -> str:
    text = str(value or "").lower()
    aliases = {
        "psg": "psg",
        "paris saint germain": "psg",
        "paris saint-germain": "psg",
        "arsenal": "arsenal",
        "cubs": "chicago_cubs",
        "chicago cubs": "chicago_cubs",
        "chicago c": "chicago_cubs",
        "cardinals": "st_louis_cardinals",
        "st louis": "st_louis_cardinals",
        "st. louis": "st_louis_cardinals",
        "okc": "oklahoma_city_thunder",
        "thunder": "oklahoma_city_thunder",
        "oklahoma city": "oklahoma_city_thunder",
        "knicks": "new_york_knicks",
        "new york": "new_york_knicks",
        "spurs": "san_antonio_spurs",
        "san antonio": "san_antonio_spurs",
    }
    for key, alias in aliases.items():
        if key in text:
            return alias
    paren_match = re.search(r"\(([^)]*)\)", text)
    if paren_match:
        for key, alias in aliases.items():
            if key in paren_match.group(1):
                return alias
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text


def _is_non_moneyline_sibling(outcome: dict[str, Any]) -> bool:
    text = str(outcome.get("market") or outcome.get("market_title") or outcome.get("outcome_name") or "").lower()
    if not text:
        return False
    if "moneyline" in text:
        return False
    return any(token in text for token in ("spread", "o/u", "over/under", "nrfi", "total", "run line"))


def _outcome_name(*rows: dict[str, Any]) -> str:
    for row in rows:
        if row.get("outcome"):
            return str(row["outcome"])
    return ""


def _family_key(payload: dict[str, Any], folder: Path) -> str:
    value = payload.get("market_family") or payload.get("event_slug") or folder.name
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _table(title: str, rows: list[dict[str, Any]]) -> list[str]:
    lines = [f"## {title}", "", "| Action | Class | Candidate action | Lane | Gross edge | Net edge | Size/notional | Assumptions accepted | Blockers/risk notes |", "|---|---|---|---|---:|---:|---:|---|---|"]
    if not rows:
        lines.append("| none |  |  |  |  |  |  |  |  |")
    for row in rows:
        lines.append(
            "| "
            f"{_md(row.get('action'))} | {_md(row.get('paper_candidate_class'))} | {_md(row.get('candidate_action'))} | "
            f"{_md(row.get('basket_type'))} | "
            f"{_md(row.get('gross_edge'))} | {_md(row.get('net_edge'))} | {_md(row.get('available_notional'))} | "
            f"{_md(', '.join(row.get('assumptions_accepted') or []))} | "
            f"{_md(', '.join((row.get('blockers') or []) + (row.get('risk_notes') or [])))} |"
        )
    lines.append("")
    return lines


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")
