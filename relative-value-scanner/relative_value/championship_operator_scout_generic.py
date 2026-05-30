from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.fees import KalshiTieredFeeModel, PolymarketConservativeFeeModel
from relative_value.operator_paper_candidate_policy import (
    CLASS_CDNA,
    CLASS_OPERATOR,
    apply_operator_candidate_fields,
    candidate_counts,
    ensure_candidate_fields,
    has_hard_blocker,
    normalize_operator_risk_mode,
)


SCHEMA_KIND = "championship_operator_scout_generic_v1"

ACTION_OPERATOR = "OPERATOR_ARB_PAPER_REVIEW"
ACTION_CDNA = "CDNA_FILL_FIRST_REVIEW"
ACTION_MANUAL = "MANUAL_REVIEW"
ACTION_WATCH = "WATCH"
ACTION_IGNORE = "IGNORE_BLOCKED"

B_OTHER_UNMAPPED = "other_outcome_unmapped"
B_TEAM_COUNT_MISMATCH = "team_count_mismatch"
B_MISSING_QUOTE = "missing_quote"
B_MISSING_DEPTH = "missing_depth"
B_STALE_QUOTE = "stale_quote"
B_FEE_REVIEW = "fee_review_required"
B_OPERATOR_RISK = "operator_risk_not_accepted"
B_CDNA_DISPLAY = "cdna_display_price_only"
B_CDNA_SIZE = "cdna_executable_size_unverified"
B_CDNA_DEPTH = "cdna_no_orderbook_depth"
B_CDNA_SERVER = "cdna_no_server_side_quote"
B_NO_EDGE = "no_positive_edge"

DEFAULT_MAX_QUOTE_AGE_SECONDS = 3600.0
DEFAULT_CDNA_FEE = 0.02


def write_championship_operator_scout_generic_files(
    *,
    family_folder: Path,
    json_output: Path,
    markdown_output: Path,
    accept_operator_risk: bool = False,
    include_cdna_fill_first: bool = False,
    operator_accept_cdna_display_price_risk: bool = False,
    operator_risk_mode: str = "conservative",
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_championship_operator_scout_generic_report(
        family_folder=family_folder,
        accept_operator_risk=accept_operator_risk,
        include_cdna_fill_first=include_cdna_fill_first,
        operator_accept_cdna_display_price_risk=operator_accept_cdna_display_price_risk,
        operator_risk_mode=operator_risk_mode,
        max_quote_age_seconds=max_quote_age_seconds,
        generated_at=generated_at,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_championship_operator_scout_generic_markdown(report), encoding="utf-8")
    return report


def build_championship_operator_scout_generic_report(
    *,
    family_folder: Path,
    accept_operator_risk: bool = False,
    include_cdna_fill_first: bool = False,
    operator_accept_cdna_display_price_risk: bool = False,
    operator_risk_mode: str = "conservative",
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    risk_mode = normalize_operator_risk_mode(operator_risk_mode)
    effective_accept_operator_risk = bool(accept_operator_risk or risk_mode in {"standard", "aggressive"})
    payloads = _load_payloads(family_folder)
    kalshi = _first_payload(payloads, "kalshi")
    polymarket = _first_payload(payloads, "polymarket")
    cdna = _first_payload(payloads, "cdna")
    family = _family_key(kalshi or polymarket or cdna or {}, family_folder)
    kalshi_rows = _extract_platform_rows(kalshi, "kalshi")
    poly_rows = _extract_platform_rows(polymarket, "polymarket")
    cdna_rows = _extract_cdna_rows(cdna)
    rows: list[dict[str, Any]] = []
    family_blockers = _family_blockers(kalshi_rows, poly_rows, cdna_rows)
    poly_by_team = {row["team_key"]: row for row in poly_rows}
    kalshi_by_team = {row["team_key"]: row for row in kalshi_rows}
    for team_key, k_row in kalshi_by_team.items():
        p_row = poly_by_team.get(team_key)
        if p_row is None:
            continue
        rows.append(
            _operator_row(
                family=family,
                team_key=team_key,
                team_name=k_row.get("team_name") or p_row.get("team_name"),
                direction="KALSHI_YES_POLYMARKET_NO",
                venue_a="kalshi",
                venue_b="polymarket",
                leg_a=_leg(k_row, "YES"),
                leg_b=_leg(p_row, "NO"),
                family_blockers=family_blockers,
                accept_operator_risk=effective_accept_operator_risk,
                operator_risk_mode=risk_mode,
                generated_at=generated,
                max_quote_age_seconds=max_quote_age_seconds,
            )
        )
        rows.append(
            _operator_row(
                family=family,
                team_key=team_key,
                team_name=k_row.get("team_name") or p_row.get("team_name"),
                direction="POLYMARKET_YES_KALSHI_NO",
                venue_a="polymarket",
                venue_b="kalshi",
                leg_a=_leg(p_row, "YES"),
                leg_b=_leg(k_row, "NO"),
                family_blockers=family_blockers,
                accept_operator_risk=effective_accept_operator_risk,
                operator_risk_mode=risk_mode,
                generated_at=generated,
                max_quote_age_seconds=max_quote_age_seconds,
            )
        )
    if include_cdna_fill_first:
        for cdna_row in cdna_rows:
            team_key = cdna_row.get("team_key")
            partner = kalshi_by_team.get(team_key or "") or poly_by_team.get(team_key or "")
            if partner is None:
                continue
            partner_platform = partner.get("platform")
            rows.append(_cdna_row(family=family, cdna=cdna_row, partner=partner, partner_side="NO", cdna_side="YES", partner_platform=partner_platform, operator_accept_cdna_display_price_risk=operator_accept_cdna_display_price_risk, operator_risk_mode=risk_mode))
            rows.append(_cdna_row(family=family, cdna=cdna_row, partner=partner, partner_side="YES", cdna_side="NO", partner_platform=partner_platform, operator_accept_cdna_display_price_risk=operator_accept_cdna_display_price_risk, operator_risk_mode=risk_mode))
    rows.sort(key=_row_sort_key, reverse=True)
    summary = _summary(rows, kalshi_rows, poly_rows, cdna_rows)
    return {
        "schema_kind": SCHEMA_KIND,
        "diagnostic_only": True,
        "saved_files_only": True,
        "strict_exact_arb": False,
        "exact_ready_rows": 0,
        "standard_paper_candidate_rows": summary.get("total_paper_candidate_rows", 0),
        "standard_paper_candidate_emitted": summary.get("total_paper_candidate_rows", 0) > 0,
        "candidate_pair_creation": False,
        "family_folder": str(family_folder),
        "market_family": family,
        "accept_operator_risk": bool(effective_accept_operator_risk),
        "include_cdna_fill_first": bool(include_cdna_fill_first),
        "operator_accept_cdna_display_price_risk": bool(operator_accept_cdna_display_price_risk),
        "operator_risk_mode": risk_mode,
        "kalshi_rows_loaded": len(kalshi_rows),
        "polymarket_rows_loaded": len(poly_rows),
        "cdna_rows_loaded": len(cdna_rows),
        "rows": rows,
        "summary_counts": summary,
        "top_blockers": summary["top_blockers"],
        "safety": {
            "diagnostic_only": True,
            "saved_files_only": True,
            "strict_exact_arb": False,
            "exact_ready_rows": 0,
            "standard_paper_candidate_rows": summary.get("total_paper_candidate_rows", 0),
            "candidate_pair_creation": False,
            "orders_or_execution_logic_added": False,
            "auth_or_account_logic_added": False,
        },
    }


def render_championship_operator_scout_generic_markdown(report: dict[str, Any]) -> str:
    counts = report.get("summary_counts") or {}
    lines = [
        "# Generic Championship Operator Scout",
        "",
        "Saved-evidence diagnostic scout for championship/categorical winner families. CDNA rows are fill-first only. No standard candidate pairs or strict exact rows are created.",
        "",
        "## Summary",
        "",
        f"- market_family: `{_md(report.get('market_family'))}`",
        f"- rows: `{counts.get('rows', 0)}`",
        f"- strict_paper_candidate_rows: `{counts.get('strict_paper_candidate_rows', 0)}`",
        f"- operator_paper_candidate_rows: `{counts.get('operator_paper_candidate_rows', 0)}`",
        f"- cdna_fill_first_paper_candidate_rows: `{counts.get('cdna_fill_first_paper_candidate_rows', 0)}`",
        f"- total_paper_candidate_rows: `{counts.get('total_paper_candidate_rows', 0)}`",
        f"- watch_rows: `{counts.get('watch_rows', 0)}`",
        f"- ignore_blocked_rows: `{counts.get('ignore_blocked_rows', 0)}`",
        f"- exact_ready_rows: `0`",
        f"- total_paper_candidate_rows: `{counts.get('total_paper_candidate_rows', 0)}`",
        "",
        "## Paper Candidates",
        "",
        "| Class | Candidate action | Gross edge | Net edge | Size/notional | Assumptions accepted | Blockers/risk notes |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    paper_rows = [row for row in report.get("rows") or [] if row.get("paper_candidate")]
    if not paper_rows:
        lines.append("| none |  |  |  |  |  |  |")
    for row in paper_rows[:50]:
        lines.append(
            "| "
            f"{_md(row.get('paper_candidate_class'))} | {_md(row.get('candidate_action'))} | "
            f"{_md(row.get('gross_edge'))} | {_md(row.get('net_edge'))} | {_md(row.get('available_notional'))} | "
            f"{_md(', '.join(row.get('assumptions_accepted') or []))} | "
            f"{_md(', '.join((row.get('blockers') or []) + (row.get('risk_notes') or [])))} |"
        )
    lines.extend(["", "## Watch Rows", ""])
    lines.extend(_row_list([row for row in report.get("rows") or [] if row.get("action") == ACTION_WATCH][:50]))
    lines.extend(["", "## Ignored/Blocked Rows", ""])
    lines.extend(_row_list([row for row in report.get("rows") or [] if row.get("action") == ACTION_IGNORE][:50]))
    lines.extend(["", "## Top Blockers", "", "| Blocker | Count |", "|---|---:|"])
    for item in report.get("top_blockers") or []:
        lines.append(f"| {_md(item.get('blocker'))} | {_md(item.get('count'))} |")
    lines.extend(["", "## Safety", "", "- diagnostic_only: `true`", "- strict_exact_arb: `false`", "- exact_ready_rows: `0`", f"- total_paper_candidate_rows: `{counts.get('total_paper_candidate_rows', 0)}`"])
    return "\n".join(lines) + "\n"


def _operator_row(
    *,
    family: str,
    team_key: str,
    team_name: str | None,
    direction: str,
    venue_a: str,
    venue_b: str,
    leg_a: dict[str, Any],
    leg_b: dict[str, Any],
    family_blockers: list[str],
    accept_operator_risk: bool,
    operator_risk_mode: str,
    generated_at: datetime,
    max_quote_age_seconds: float,
) -> dict[str, Any]:
    ask_a = leg_a.get("ask")
    ask_b = leg_b.get("ask")
    gross = round(1.0 - ask_a - ask_b, 8) if ask_a is not None and ask_b is not None else None
    fee, net, fee_status = _fee(venue_a, ask_a, venue_b, ask_b, gross)
    available = _available_notional(leg_a, leg_b)
    blockers = list(family_blockers)
    if ask_a is None or ask_b is None:
        blockers.append(B_MISSING_QUOTE)
    if available is None:
        blockers.append(B_MISSING_DEPTH)
    if _stale(leg_a.get("quote_timestamp"), generated_at, max_quote_age_seconds) or _stale(leg_b.get("quote_timestamp"), generated_at, max_quote_age_seconds):
        blockers.append(B_STALE_QUOTE)
    if fee_status != "OK":
        blockers.append(B_FEE_REVIEW)
    if not accept_operator_risk:
        blockers.append(B_OPERATOR_RISK)
    if gross is not None and gross <= 0:
        blockers.append(B_NO_EDGE)
    blockers = sorted(set(blockers))
    eligible = accept_operator_risk and net is not None and net > 0 and not _has_hard_blocker(blockers)
    action = ACTION_WATCH if eligible else _non_positive_action(blockers, gross)
    row = {
        "market_family": family,
        "canonical_team_key": team_key,
        "team_name": team_name,
        "direction": direction,
        "leg_a": leg_a,
        "leg_b": leg_b,
        "gross_edge": gross,
        "conservative_fee_estimate": fee,
        "net_edge": net,
        "net_edge_status": fee_status,
        "available_notional": available,
        "blockers": blockers,
        "action": action,
        "strict_exact_arb": False,
        "exact_ready": False,
        "standard_paper_candidate": False,
        "diagnostic_only": True,
    }
    return apply_operator_candidate_fields(
        row,
        paper_class=CLASS_OPERATOR,
        assumptions_accepted=["championship_operator_tail_risk"],
        candidate_action="PAPER_CANDIDATE",
        make_candidate=operator_risk_mode in {"standard", "aggressive"} and eligible,
        mathematical_strict_exact_arb=False,
    )


def _cdna_row(*, family: str, cdna: dict[str, Any], partner: dict[str, Any], partner_side: str, cdna_side: str, partner_platform: str | None, operator_accept_cdna_display_price_risk: bool, operator_risk_mode: str) -> dict[str, Any]:
    display = cdna.get("display_price") if cdna_side == "YES" else cdna.get("display_no_price")
    partner_leg = _leg(partner, partner_side)
    partner_ask = partner_leg.get("ask")
    all_in = round(display + DEFAULT_CDNA_FEE, 8) if display is not None else None
    gross = round(1.0 - all_in - partner_ask, 8) if all_in is not None and partner_ask is not None else None
    blockers = [B_CDNA_DISPLAY, B_CDNA_SIZE, B_CDNA_DEPTH, B_CDNA_SERVER]
    if display is None or partner_ask is None:
        blockers.append(B_MISSING_QUOTE)
    if partner_leg.get("notional") is None:
        blockers.append(B_MISSING_DEPTH)
    if not operator_accept_cdna_display_price_risk:
        blockers.append("cdna_operator_acceptance_required")
    if gross is not None and gross <= 0:
        blockers.append(B_NO_EDGE)
    eligible = gross is not None and gross > 0 and not has_hard_blocker(blockers, ignore_cdna_info=True) and operator_accept_cdna_display_price_risk
    action = ACTION_WATCH if eligible else _non_positive_action(blockers, gross)
    row = {
        "market_family": family,
        "canonical_team_key": cdna.get("team_key"),
        "team_name": cdna.get("team_name"),
        "direction": f"CDNA_{cdna_side}_{str(partner_platform or 'PARTNER').upper()}_{partner_side}",
        "cdna_leg": {
            "side": cdna_side,
            "display_price": display,
            "fee_per_contract": DEFAULT_CDNA_FEE,
            "all_in_cost_per_contract": all_in,
            "contract_id": cdna.get("contract_id"),
            "symbol": cdna.get("symbol"),
            "depth_status": "display_price_only",
        },
        "partner_leg": partner_leg,
        "gross_edge": gross,
        "net_edge": None,
        "net_edge_status": "CDNA_DISPLAY_PRICE_ONLY",
        "available_notional": partner_leg.get("notional"),
        "blockers": sorted(set(blockers)),
        "action": action,
        "strict_exact_arb": False,
        "exact_ready": False,
        "standard_paper_candidate": False,
        "diagnostic_only": True,
    }
    return apply_operator_candidate_fields(
        row,
        paper_class=CLASS_CDNA,
        assumptions_accepted=["cdna_display_price_assumed_fillable_at_operator_cap", "cdna_executable_size_unverified_pre_fill"],
        candidate_action="FILL_CDNA_FIRST_THEN_HEDGE_EXACT_FILLED_QUANTITY",
        make_candidate=operator_risk_mode == "aggressive" and eligible,
        mathematical_strict_exact_arb=False,
    )


def _load_payloads(folder: Path) -> list[dict[str, Any]]:
    payloads = []
    if folder.is_file():
        paths = [folder]
    else:
        paths = list(folder.glob("*.json"))
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            payload["_source_file"] = str(path)
            payloads.append(payload)
    return payloads


def _first_payload(payloads: list[dict[str, Any]], platform: str) -> dict[str, Any] | None:
    for payload in payloads:
        if platform in str(payload.get("platform") or "").lower():
            return payload
    return None


def _extract_platform_rows(payload: dict[str, Any] | None, platform: str) -> list[dict[str, Any]]:
    if not payload:
        return []
    rows = []
    for item in payload.get("outcomes") or []:
        if not isinstance(item, dict):
            continue
        key = _canonical_team(item.get("team_name") or item.get("outcome_name"))
        if not key:
            continue
        rows.append(
            {
                "platform": platform,
                "team_key": key,
                "team_name": item.get("team_name") or item.get("outcome_name"),
                "ticker_or_token": item.get("market_ticker") or item.get("market_id") or item.get("token_id_yes"),
                "yes_bid": _float(item.get("yes_bid")),
                "yes_ask": _float(item.get("yes_ask")),
                "yes_bid_size": _float(item.get("yes_bid_size")),
                "yes_ask_size": _float(item.get("yes_ask_size")),
                "no_bid": _float(item.get("no_bid")),
                "no_ask": _float(item.get("no_ask")),
                "no_bid_size": _float(item.get("no_bid_size")),
                "no_ask_size": _float(item.get("no_ask_size")),
                "depth_status": item.get("depth_status"),
                "quote_timestamp": item.get("quote_timestamp"),
            }
        )
    return rows


def _extract_cdna_rows(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not payload:
        return []
    rows = []
    for item in payload.get("outcomes") or []:
        if not isinstance(item, dict) or str(item.get("outcome_status") or "").lower() not in {"active", "open"}:
            continue
        key = _canonical_team(item.get("team_name") or item.get("outcome_name"))
        rows.append(
            {
                "team_key": key,
                "team_name": item.get("team_name") or item.get("outcome_name"),
                "display_price": _float(item.get("display_price")),
                "display_no_price": _float(item.get("display_no_price")),
                "contract_id": item.get("contract_id"),
                "symbol": item.get("symbol"),
                "quote_timestamp": item.get("quote_timestamp"),
            }
        )
    return rows


def _leg(row: dict[str, Any], side: str) -> dict[str, Any]:
    prefix = side.lower()
    ask = row.get(f"{prefix}_ask")
    size = row.get(f"{prefix}_ask_size")
    return {
        "platform": row.get("platform"),
        "side": side,
        "ticker_or_token": row.get("ticker_or_token"),
        "ask": ask,
        "bid": row.get(f"{prefix}_bid"),
        "ask_size": size,
        "notional": round(ask * size, 8) if ask is not None and size is not None else None,
        "depth_status": row.get("depth_status"),
        "quote_timestamp": row.get("quote_timestamp"),
    }


def _available_notional(a: dict[str, Any], b: dict[str, Any]) -> float | None:
    if a.get("notional") is None or b.get("notional") is None:
        return None
    return round(min(a["notional"], b["notional"]), 8)


def _fee(venue_a: str, ask_a: float | None, venue_b: str, ask_b: float | None, gross: float | None) -> tuple[float | None, float | None, str]:
    if ask_a is None or ask_b is None or gross is None:
        return None, None, "NOT_CALCULATED"
    try:
        fee = _fee_for(venue_a, ask_a) + _fee_for(venue_b, ask_b)
    except ValueError:
        return None, None, "FEE_REVIEW_REQUIRED"
    return round(fee, 8), round(gross - fee, 8), "OK"


def _fee_for(venue: str, price: float) -> float:
    if venue == "kalshi":
        return KalshiTieredFeeModel().fee_for_leg(price)
    if venue == "polymarket":
        return PolymarketConservativeFeeModel().fee_for_leg_for_category(price, category="sports")
    raise ValueError(venue)


def _family_blockers(kalshi: list[dict[str, Any]], poly: list[dict[str, Any]], cdna: list[dict[str, Any]]) -> list[str]:
    blockers = []
    counts = {len(kalshi), len(poly)}
    if cdna:
        counts.add(len(cdna))
    if len([count for count in counts if count]) > 1:
        blockers.append(B_TEAM_COUNT_MISMATCH)
    if counts and max(counts) > min(counts or {0}) and max(counts) >= 31:
        blockers.append(B_OTHER_UNMAPPED)
    return blockers


def _has_hard_blocker(blockers: list[str]) -> bool:
    return any(b in blockers for b in {B_MISSING_QUOTE, B_MISSING_DEPTH, B_STALE_QUOTE, B_FEE_REVIEW, B_OPERATOR_RISK, B_NO_EDGE, B_OTHER_UNMAPPED})


def _non_positive_action(blockers: list[str], gross: float | None) -> str:
    if B_MISSING_QUOTE in blockers or B_MISSING_DEPTH in blockers or B_STALE_QUOTE in blockers:
        return ACTION_WATCH
    if gross is not None and gross > 0:
        return ACTION_WATCH
    return ACTION_IGNORE


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


def _family_key(payload: dict[str, Any], folder: Path) -> str:
    value = payload.get("market_family") or payload.get("event_slug") or folder.name
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def _canonical_team(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
    aliases = {
        "oklahomacitythunder": "okc",
        "newyorkknicks": "nyk",
        "sanantoniospurs": "sas",
        "losangelesdodgers": "lad",
        "newyorkyankees": "nyy",
        "atlantabraves": "atl",
        "torontobluejays": "tor",
        "chicagowhitesox": "cws",
        "athletics": "ath",
        "as": "ath",
    }
    return aliases.get(text, text)


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _summary(rows: list[dict[str, Any]], kalshi: list[dict[str, Any]], poly: list[dict[str, Any]], cdna: list[dict[str, Any]]) -> dict[str, Any]:
    actions = Counter(row.get("action") for row in rows)
    blockers = Counter()
    for row in rows:
        blockers.update(row.get("blockers") or [])
    summary = {
        "rows": len(rows),
        "kalshi_rows_loaded": len(kalshi),
        "polymarket_rows_loaded": len(poly),
        "cdna_rows_loaded": len(cdna),
        "operator_review_rows": actions.get(ACTION_OPERATOR, 0),
        "cdna_fill_first_review_rows": actions.get(ACTION_CDNA, 0),
        "manual_review_rows": actions.get(ACTION_MANUAL, 0),
        "watch_rows": actions.get(ACTION_WATCH, 0),
        "ignore_blocked_rows": actions.get(ACTION_IGNORE, 0),
        "exact_ready_rows": 0,
        "standard_paper_candidate_rows": 0,
        "top_blockers": [{"blocker": key, "count": value} for key, value in blockers.most_common(15)],
    }
    summary.update(candidate_counts(rows))
    summary["standard_paper_candidate_rows"] = summary["total_paper_candidate_rows"]
    return summary


def _row_sort_key(row: dict[str, Any]) -> tuple[int, float]:
    action_score = {ACTION_OPERATOR: 5, ACTION_CDNA: 4, ACTION_MANUAL: 3, ACTION_WATCH: 2, ACTION_IGNORE: 1}.get(row.get("action"), 0)
    return (action_score, _float(row.get("net_edge")) or _float(row.get("gross_edge")) or -999.0)


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def _row_list(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["_None._"]
    return [
        f"- `{_md(row.get('direction'))}` action=`{_md(row.get('action'))}` net=`{_md(row.get('net_edge'))}` blockers=`{_md(', '.join(row.get('blockers') or []))}`"
        for row in rows
    ]
