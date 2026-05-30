from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.fees import KalshiTieredFeeModel, PolymarketConservativeFeeModel
from relative_value.operator_paper_candidate_policy import (
    CLASS_OPERATOR,
    ACTION_PAPER,
    ACTION_WATCH as VISIBLE_WATCH,
    apply_operator_candidate_fields,
    candidate_counts,
    ensure_candidate_fields,
    normalize_operator_risk_mode,
)


SCHEMA_VERSION = 1
SCHEMA_KIND = "sports_mlb_daily_residual_risk_scout_v1"
REPORT_SOURCE = "sports_mlb_daily_residual_risk_scout_v1"

ACTION_RESIDUAL_REVIEW = "RESIDUAL_RISK_SHADOW_PAPER_REVIEW"
ACTION_OPERATOR_REVIEW = "OPERATOR_ARB_PAPER_REVIEW"
ACTION_MANUAL_REVIEW = "MANUAL_REVIEW"
ACTION_WATCH = "WATCH"
ACTION_IGNORE_BLOCKED = "IGNORE_BLOCKED"

RESIDUAL_RISK_TYPE = "mlb_daily_postponement_suspension_cancellation_tail_risk"
DEFAULT_MAX_QUOTE_AGE_SECONDS = 900
DEFAULT_MIN_AVAILABLE_NOTIONAL = 10.0

B_NOT_MLB_DAILY_GAME_WINNER = "not_mlb_daily_game_winner_market"
B_UNSUPPORTED_SCOPE = "unsupported_market_scope"
B_RESIDUAL_NOT_ACCEPTED = "residual_contingency_risk_not_accepted"
B_UNMATCHED_TEAM = "team_mapping_missing_or_ambiguous"
B_MISSING_QUOTE = "missing_quote"
B_MISSING_DEPTH = "missing_quote_depth"
B_MISSING_TIMESTAMP = "stale_or_missing_quote"
B_STALE_QUOTE = "stale_or_missing_quote"
B_LIVE_GAME_EXCLUDED = "live_game_excluded_or_review_required"
B_LIVE_GAME_EXCLUDED_OPERATOR = "live_game_excluded_by_operator_flag"
B_LIVE_STATUS_UNKNOWN = "live_status_unknown"
B_MISSING_FEE_MODEL = "missing_or_uncertain_fee_model"
B_SIZE_UNIT_REVIEW = "quote_size_unit_review_required"
B_MISSING_KALSHI_SIZE = "missing_kalshi_size"
B_MISSING_POLYMARKET_SIZE = "missing_polymarket_size"
B_PARTIAL_DEPTH = "partial_or_missing_depth"
B_INSUFFICIENT_DEPTH = "insufficient_available_notional"
B_NO_POSITIVE_GROSS_EDGE = "no_positive_gross_edge"
B_NO_POSITIVE_NET_EDGE = "no_positive_net_edge_after_fees"

RESIDUAL_RULE_RISK_BLOCKERS = {
    "residual_postponement_rule_mismatch",
    "last_fair_market_price_vs_50_50_cancellation_mismatch",
    "polymarket_shortened_game_rule_not_explicit",
    "polymarket_extra_innings_rule_not_explicit",
    "missing_suspended_or_shortened_game_rules",
    "missing_extra_innings_rules",
}

_LIVE_STATUS_RE = re.compile(
    r"\b(live|in[-\s]?progress|started|top|bot|bottom|mid|inning|half|period|active)\b",
    re.IGNORECASE,
)
_UNSUPPORTED_SCOPE_RE = re.compile(
    r"\b(championship|futures?|world\s+series|prop|spread|total|run\s*line|player\s+prop|innings?)\b",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[^a-z0-9]+")

_TEAM_ALIASES = {
    "ari": "ARI",
    "arizona diamondbacks": "ARI",
    "atl": "ATL",
    "atlanta braves": "ATL",
    "bal": "BAL",
    "baltimore orioles": "BAL",
    "bos": "BOS",
    "boston red sox": "BOS",
    "chc": "CHC",
    "chicago cubs": "CHC",
    "cws": "CWS",
    "chw": "CWS",
    "chicago white sox": "CWS",
    "cin": "CIN",
    "cincinnati reds": "CIN",
    "cle": "CLE",
    "cleveland guardians": "CLE",
    "col": "COL",
    "colorado rockies": "COL",
    "det": "DET",
    "detroit tigers": "DET",
    "hou": "HOU",
    "houston astros": "HOU",
    "kc": "KC",
    "kcr": "KC",
    "kansas city royals": "KC",
    "la angels": "LAA",
    "laa": "LAA",
    "los angeles angels": "LAA",
    "angels": "LAA",
    "lad": "LAD",
    "la dodgers": "LAD",
    "los angeles dodgers": "LAD",
    "dodgers": "LAD",
    "mia": "MIA",
    "miami marlins": "MIA",
    "mil": "MIL",
    "milwaukee brewers": "MIL",
    "min": "MIN",
    "minnesota twins": "MIN",
    "nym": "NYM",
    "new york mets": "NYM",
    "nyy": "NYY",
    "new york yankees": "NYY",
    "ath": "ATH",
    "oak": "ATH",
    "athletics": "ATH",
    "phi": "PHI",
    "philadelphia phillies": "PHI",
    "pit": "PIT",
    "pittsburgh pirates": "PIT",
    "sd": "SD",
    "sdp": "SD",
    "san diego padres": "SD",
    "sea": "SEA",
    "seattle mariners": "SEA",
    "sf": "SF",
    "sfg": "SF",
    "san francisco giants": "SF",
    "stl": "STL",
    "st louis cardinals": "STL",
    "st. louis cardinals": "STL",
    "tb": "TB",
    "tbr": "TB",
    "tampa bay rays": "TB",
    "tex": "TEX",
    "texas rangers": "TEX",
    "tor": "TOR",
    "toronto blue jays": "TOR",
    "wsh": "WSH",
    "was": "WSH",
    "washington nationals": "WSH",
}


def build_sports_mlb_daily_residual_risk_report(
    *,
    kalshi_evidence: Path,
    polymarket_evidence: Path,
    date: str,
    accept_mlb_daily_contingency_risk: bool = False,
    operator_accepted_as_arb: bool = False,
    include_live_games: bool = False,
    exclude_live_games: bool = False,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
    min_available_notional: float = DEFAULT_MIN_AVAILABLE_NOTIONAL,
    generated_at: datetime | None = None,
    fee_models_available: bool = True,
    operator_risk_mode: str = "conservative",
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    risk_mode = normalize_operator_risk_mode(operator_risk_mode)
    mode_accepts_residual_risk = risk_mode in {"standard", "aggressive"}
    effective_accept_residual_risk = bool(accept_mlb_daily_contingency_risk or mode_accepts_residual_risk)
    effective_operator_accepted = bool(operator_accepted_as_arb or mode_accepts_residual_risk)
    warnings: list[dict[str, Any]] = []
    kalshi_payload = _load_json(kalshi_evidence, warnings, "kalshi_evidence")
    polymarket_payload = _load_json(polymarket_evidence, warnings, "polymarket_evidence")
    payload_scope_blockers = (
        _payload_scope_blockers(kalshi_payload, expected_platform="Kalshi", expected_date=date)
        + _payload_scope_blockers(polymarket_payload, expected_platform="Polymarket", expected_date=date)
    )
    for blocker in payload_scope_blockers:
        warnings.append({"reason": "input_scope_validation_blocker", "blocker": blocker})
    kalshi_games = _games(kalshi_payload)
    polymarket_games = _games(polymarket_payload)
    polymarket_by_key = {
        str(game.get("cross_platform_game_key") or ""): game
        for game in polymarket_games
        if game.get("cross_platform_game_key")
    }

    rows: list[dict[str, Any]] = []
    matched_game_keys: list[str] = []
    unmatched_game_keys: list[str] = []
    for kalshi_game in kalshi_games:
        key = str(kalshi_game.get("cross_platform_game_key") or "")
        if not key:
            continue
        polymarket_game = polymarket_by_key.get(key)
        if not polymarket_game:
            unmatched_game_keys.append(key)
            continue
        game_rows = _rows_for_game(
            kalshi_game=kalshi_game,
            polymarket_game=polymarket_game,
            expected_date=date,
            generated_at=generated,
            max_quote_age_seconds=max_quote_age_seconds,
            min_available_notional=min_available_notional,
            accept_mlb_daily_contingency_risk=effective_accept_residual_risk,
            operator_accepted_as_arb=effective_operator_accepted,
            include_live_games=include_live_games,
            exclude_live_games=exclude_live_games,
            fee_models_available=fee_models_available,
            payload_scope_blockers=payload_scope_blockers,
            operator_risk_mode=risk_mode,
        )
        if game_rows:
            matched_game_keys.append(key)
            rows.extend(game_rows)
        else:
            unmatched_game_keys.append(key)

    rows.sort(key=_row_sort_key, reverse=True)
    summary = _summary(rows, kalshi_games, polymarket_games, matched_game_keys)
    return ensure_candidate_fields({
        "schema_version": SCHEMA_VERSION,
        "schema_kind": SCHEMA_KIND,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "diagnostic_only": True,
        "shadow_paper_only": True,
        "operator_arb_mode": bool(effective_accept_residual_risk and effective_operator_accepted),
        "human_accepted_residual_risk": bool(effective_accept_residual_risk),
        "operator_accepted_as_arb": bool(effective_operator_accepted),
        "operator_risk_mode": risk_mode,
        "live_games_included_by_default": not bool(exclude_live_games),
        "include_live_games": bool(include_live_games),
        "exclude_live_games": bool(exclude_live_games),
        "strict_exact_arb": False,
        "mathematical_strict_exact_arb": False,
        "paper_candidate_emitted": summary.get("total_paper_candidate_rows", 0) > 0,
        "global_paper_candidate_emitted": summary.get("total_paper_candidate_rows", 0) > 0,
        "standard_paper_candidate_rows": summary.get("total_paper_candidate_rows", 0),
        "operator_arb_review_rows": summary["operator_arb_review_rows"],
        "operator_paper_review_rows": summary["operator_arb_review_rows"],
        "date": date,
        "max_quote_age_seconds": max_quote_age_seconds,
        "min_available_notional": min_available_notional,
        "kalshi_evidence": str(kalshi_evidence),
        "polymarket_evidence": str(polymarket_evidence),
        "games_loaded": {
            "kalshi": len(kalshi_games),
            "polymarket": len(polymarket_games),
        },
        "matched_games": len(matched_game_keys),
        "matched_game_keys": matched_game_keys,
        "unmatched_game_keys": unmatched_game_keys,
        "summary_counts": summary,
        "top_blockers": summary["top_blockers"],
        "rows": rows,
        "warnings": warnings,
        "input_scope_blockers": sorted(set(payload_scope_blockers)),
        "safety": {
            "diagnostic_only": True,
            "shadow_paper_only": True,
            "operator_arb_mode": bool(effective_accept_residual_risk and effective_operator_accepted),
            "strict_exact_arb": False,
            "mathematical_strict_exact_arb": False,
            "exact_ready": False,
            "paper_candidate": summary.get("total_paper_candidate_rows", 0) > 0,
            "standard_paper_candidate_rows": summary.get("total_paper_candidate_rows", 0),
            "paper_candidate_emitted": summary.get("total_paper_candidate_rows", 0) > 0,
            "global_paper_candidate_emitted": summary.get("total_paper_candidate_rows", 0) > 0,
            "candidate_pair_creation": False,
            "evaluator_invoked": False,
            "affects_global_evaluator_gates": False,
            "override_default_enabled": False,
            "orders_or_execution_logic_added": False,
            "auth_or_account_logic_added": False,
            "saved_files_only": True,
        },
    })


def write_sports_mlb_daily_residual_risk_files(
    *,
    kalshi_evidence: Path,
    polymarket_evidence: Path,
    date: str,
    accept_mlb_daily_contingency_risk: bool,
    include_live_games: bool,
    json_output: Path,
    markdown_output: Path,
    operator_accepted_as_arb: bool = False,
    exclude_live_games: bool = False,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
    min_available_notional: float = DEFAULT_MIN_AVAILABLE_NOTIONAL,
    generated_at: datetime | None = None,
    operator_risk_mode: str = "conservative",
) -> dict[str, Any]:
    report = build_sports_mlb_daily_residual_risk_report(
        kalshi_evidence=kalshi_evidence,
        polymarket_evidence=polymarket_evidence,
        date=date,
        accept_mlb_daily_contingency_risk=accept_mlb_daily_contingency_risk,
        operator_accepted_as_arb=operator_accepted_as_arb,
        include_live_games=include_live_games,
        exclude_live_games=exclude_live_games,
        max_quote_age_seconds=max_quote_age_seconds,
        min_available_notional=min_available_notional,
        generated_at=generated_at,
        operator_risk_mode=operator_risk_mode,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_sports_mlb_daily_residual_risk_markdown(report), encoding="utf-8")
    return report


def render_sports_mlb_daily_residual_risk_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary_counts") or {}
    rows = report.get("rows") or []
    paper_rows = [row for row in rows if row.get("paper_candidate")]
    watch_rows = [row for row in rows if row.get("action") == VISIBLE_WATCH]
    ignored_rows = [row for row in rows if row.get("action") == ACTION_IGNORE_BLOCKED]
    lines = [
        "# MLB Daily Game Operator Arb Scout",
        "",
        "Saved-evidence-only diagnostic for MLB daily game-winner cross-venue baskets. This is operator-risk only, not strict exact arb, and it never creates standard paper candidates.",
        "",
        "## Summary",
        "",
        f"- date: `{_md(report.get('date'))}`",
        f"- human_accepted_residual_risk: `{str(bool(report.get('human_accepted_residual_risk'))).lower()}`",
        f"- operator_accepted_as_arb: `{str(bool(report.get('operator_accepted_as_arb'))).lower()}`",
        f"- operator_arb_mode: `{str(bool(report.get('operator_arb_mode'))).lower()}`",
        f"- live_games_included_by_default: `{str(bool(report.get('live_games_included_by_default'))).lower()}`",
        f"- exclude_live_games: `{str(bool(report.get('exclude_live_games'))).lower()}`",
        f"- strict_exact_arb: `false`",
        f"- mathematical_strict_exact_arb: `false`",
        f"- global_paper_candidate_emitted: `{str(bool(report.get('global_paper_candidate_emitted'))).lower()}`",
        f"- games_loaded_kalshi: `{(report.get('games_loaded') or {}).get('kalshi', 0)}`",
        f"- games_loaded_polymarket: `{(report.get('games_loaded') or {}).get('polymarket', 0)}`",
        f"- matched_games: `{report.get('matched_games', 0)}`",
        f"- rows: `{summary.get('rows', 0)}`",
        f"- strict_paper_candidate_rows: `{summary.get('strict_paper_candidate_rows', 0)}`",
        f"- operator_paper_candidate_rows: `{summary.get('operator_paper_candidate_rows', 0)}`",
        f"- cdna_fill_first_paper_candidate_rows: `{summary.get('cdna_fill_first_paper_candidate_rows', 0)}`",
        f"- total_paper_candidate_rows: `{summary.get('total_paper_candidate_rows', 0)}`",
        f"- manual_review_rows: `{summary.get('manual_review_rows', 0)}`",
        f"- watch_rows: `{summary.get('watch_rows', 0)}`",
        f"- exact_ready_rows: `0`",
        f"- total_paper_candidate_rows: `{summary.get('total_paper_candidate_rows', 0)}`",
        "",
        "## Paper Candidates",
        "",
    ]
    lines.extend(_row_table(paper_rows))
    lines.extend(["", "## Watch Rows", ""])
    lines.extend(_row_table(watch_rows[:50]))
    lines.extend(["", "## Ignored/Blocked Rows", ""])
    lines.extend(_row_table(ignored_rows[:50]))
    lines.extend(["", "## Top Blockers", "", "| Blocker | Count |", "|---|---:|"])
    blockers = report.get("top_blockers") or []
    if blockers:
        for item in blockers:
            lines.append(f"| {_md(item.get('blocker'))} | {_md(item.get('count'))} |")
    else:
        lines.append("| none | 0 |")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- diagnostic_only: `true`",
            "- shadow_paper_only: `true`",
            f"- operator_arb_mode: `{str(bool(report.get('operator_arb_mode'))).lower()}`",
            "- strict_exact_arb: `false`",
            "- mathematical_strict_exact_arb: `false`",
            "- candidate_pair_creation: `false`",
            "- exact_ready: `false`",
            f"- total_paper_candidate_rows: `{summary.get('total_paper_candidate_rows', 0)}`",
            f"- global_paper_candidate_emitted: `{str(bool(report.get('global_paper_candidate_emitted'))).lower()}`",
            "- affects_global_evaluator_gates: `false`",
            "- override_default_enabled: `false`",
            "- saved_files_only: `true`",
        ]
    )
    return "\n".join(lines) + "\n"


def _row_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| Action | Class | Candidate action | Gross edge | Net edge | Size/notional | Assumptions accepted | Blockers/risk notes |",
        "|---|---|---|---:|---:|---:|---|---|",
    ]
    if not rows:
        lines.append("| none |  |  |  |  |  |  |  |")
        return lines
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("action")),
                    _md(row.get("paper_candidate_class")),
                    _md(row.get("candidate_action")),
                    _fmt(row.get("gross_edge")),
                    _fmt(row.get("net_edge")),
                    _fmt(row.get("available_notional")),
                    _md(",".join(row.get("assumptions_accepted") or []) or "none"),
                    _md(",".join((row.get("blockers") or []) + (row.get("risk_notes") or row.get("residual_risk_notes") or [])) or "none"),
                ]
            )
            + " |"
        )
    return lines


def _rows_for_game(
    *,
    kalshi_game: dict[str, Any],
    polymarket_game: dict[str, Any],
    expected_date: str,
    generated_at: datetime,
    max_quote_age_seconds: float,
    min_available_notional: float,
    accept_mlb_daily_contingency_risk: bool,
    operator_accepted_as_arb: bool,
    include_live_games: bool,
    exclude_live_games: bool,
    fee_models_available: bool,
    payload_scope_blockers: list[str],
    operator_risk_mode: str,
) -> list[dict[str, Any]]:
    scope_blockers = _scope_blockers_for_matched_game(
        kalshi_game=kalshi_game,
        polymarket_game=polymarket_game,
        expected_date=expected_date,
        payload_scope_blockers=payload_scope_blockers,
    )
    if scope_blockers:
        return [
            _blocked_scope_row(
                kalshi_game=kalshi_game,
                polymarket_game=polymarket_game,
                blockers=scope_blockers,
                accept_mlb_daily_contingency_risk=accept_mlb_daily_contingency_risk,
                operator_accepted_as_arb=operator_accepted_as_arb,
            )
        ]
    kalshi_outcomes = _outcome_index(kalshi_game, venue="kalshi")
    polymarket_outcomes = _outcome_index(polymarket_game, venue="polymarket")
    ordered_teams = [
        team_key
        for team_key in kalshi_outcomes.get("_order", [])
        if team_key in polymarket_outcomes and team_key != "_order"
    ]
    if len(ordered_teams) < 2:
        return [
            _blocked_team_match_row(
                kalshi_game=kalshi_game,
                polymarket_game=polymarket_game,
                accept_mlb_daily_contingency_risk=accept_mlb_daily_contingency_risk,
                operator_accepted_as_arb=operator_accepted_as_arb,
            )
        ]
    team_a, team_b = ordered_teams[0], ordered_teams[1]
    return [
        _direction_row(
            kalshi_game=kalshi_game,
            polymarket_game=polymarket_game,
            kalshi_outcome=kalshi_outcomes[team_a],
            polymarket_outcome=polymarket_outcomes[team_b],
            direction="A",
            generated_at=generated_at,
            max_quote_age_seconds=max_quote_age_seconds,
            min_available_notional=min_available_notional,
            accept_mlb_daily_contingency_risk=accept_mlb_daily_contingency_risk,
            operator_accepted_as_arb=operator_accepted_as_arb,
            include_live_games=include_live_games,
            exclude_live_games=exclude_live_games,
            fee_models_available=fee_models_available,
            operator_risk_mode=operator_risk_mode,
        ),
        _direction_row(
            kalshi_game=kalshi_game,
            polymarket_game=polymarket_game,
            kalshi_outcome=kalshi_outcomes[team_b],
            polymarket_outcome=polymarket_outcomes[team_a],
            direction="B",
            generated_at=generated_at,
            max_quote_age_seconds=max_quote_age_seconds,
            min_available_notional=min_available_notional,
            accept_mlb_daily_contingency_risk=accept_mlb_daily_contingency_risk,
            operator_accepted_as_arb=operator_accepted_as_arb,
            include_live_games=include_live_games,
            exclude_live_games=exclude_live_games,
            fee_models_available=fee_models_available,
            operator_risk_mode=operator_risk_mode,
        ),
    ]


def _direction_row(
    *,
    kalshi_game: dict[str, Any],
    polymarket_game: dict[str, Any],
    kalshi_outcome: dict[str, Any],
    polymarket_outcome: dict[str, Any],
    direction: str,
    generated_at: datetime,
    max_quote_age_seconds: float,
    min_available_notional: float,
    accept_mlb_daily_contingency_risk: bool,
    operator_accepted_as_arb: bool,
    include_live_games: bool,
    exclude_live_games: bool,
    fee_models_available: bool,
    operator_risk_mode: str,
) -> dict[str, Any]:
    kalshi_ask = _float_or_none(kalshi_outcome.get("ask"))
    polymarket_ask = _float_or_none(polymarket_outcome.get("ask"))
    gross_edge = (
        round(1.0 - kalshi_ask - polymarket_ask, 6)
        if kalshi_ask is not None and polymarket_ask is not None
        else None
    )
    kalshi_size = _normalize_leg_notional(kalshi_outcome, venue="kalshi", price=kalshi_ask)
    polymarket_size = _normalize_leg_notional(polymarket_outcome, venue="polymarket", price=polymarket_ask)
    kalshi_notional = kalshi_size["notional"]
    polymarket_notional = polymarket_size["notional"]
    available_notional = (
        round(min(kalshi_notional, polymarket_notional), 6)
        if kalshi_notional is not None and polymarket_notional is not None
        else None
    )
    depth_gate_passed = _depth_is_acceptable(kalshi_outcome, venue="kalshi") and _depth_is_acceptable(
        polymarket_outcome,
        venue="polymarket",
    )
    size_gate_passed = (
        kalshi_size["status"] == "normalized"
        and polymarket_size["status"] == "normalized"
        and depth_gate_passed
        and available_notional is not None
        and available_notional >= min_available_notional
    )
    fee_estimate, net_edge, net_edge_status = _fee_estimate(
        kalshi_ask=kalshi_ask,
        polymarket_ask=polymarket_ask,
        fee_models_available=fee_models_available,
    )
    quote_timestamps = {
        "kalshi": kalshi_outcome.get("quote_timestamp"),
        "polymarket": polymarket_outcome.get("quote_timestamp"),
    }
    blockers: list[str] = []
    residual_rule_blockers = _residual_rule_risk_blockers(kalshi_game, polymarket_game)
    risk_accepted_for_operator = bool(accept_mlb_daily_contingency_risk and operator_accepted_as_arb)
    if not accept_mlb_daily_contingency_risk:
        blockers.append(B_RESIDUAL_NOT_ACCEPTED)
    if residual_rule_blockers and not risk_accepted_for_operator:
        blockers.extend(residual_rule_blockers)
    if kalshi_ask is None or polymarket_ask is None:
        blockers.append(B_MISSING_QUOTE)
    if kalshi_size["missing_size"]:
        blockers.append(B_MISSING_KALSHI_SIZE)
    if polymarket_size["missing_size"]:
        blockers.append(B_MISSING_POLYMARKET_SIZE)
    if kalshi_size["missing_size"] or polymarket_size["missing_size"] or available_notional is None or available_notional <= 0:
        blockers.append(B_MISSING_DEPTH)
    if kalshi_size["status"] == "blocked_unclear" or polymarket_size["status"] == "blocked_unclear":
        blockers.append(B_SIZE_UNIT_REVIEW)
    if not depth_gate_passed:
        blockers.append(B_PARTIAL_DEPTH)
    if available_notional is not None and available_notional < min_available_notional:
        blockers.append(B_INSUFFICIENT_DEPTH)
    blockers.extend(
        _timestamp_blockers(
            quote_timestamps=quote_timestamps,
            generated_at=generated_at,
            max_quote_age_seconds=max_quote_age_seconds,
        )
    )
    live_status = _is_live_game(kalshi_game) or _is_live_game(polymarket_game)
    live_status_unknown = _live_status_unknown(kalshi_game, polymarket_game)
    if live_status and exclude_live_games:
        blockers.append(B_LIVE_GAME_EXCLUDED_OPERATOR)
    if net_edge_status == "FEE_REVIEW_REQUIRED":
        blockers.append(B_MISSING_FEE_MODEL)
    if gross_edge is not None and gross_edge <= 0:
        blockers.append(B_NO_POSITIVE_GROSS_EDGE)
    if net_edge is not None and net_edge <= 0:
        blockers.append(B_NO_POSITIVE_NET_EDGE)
    blockers = list(dict.fromkeys(blockers))
    action = _action(
        blockers,
        net_edge_status=net_edge_status,
        net_edge=net_edge,
        gross_edge=gross_edge,
        operator_accepted_as_arb=operator_accepted_as_arb,
    )
    freshness_status = "stale_or_missing" if B_STALE_QUOTE in blockers else "fresh"
    live_status_text = "unknown" if live_status_unknown else ("live_or_in_progress" if live_status else "not_live")
    live_review_flags = []
    if live_status:
        live_review_flags.append("live_game_included_operator_risk" if not exclude_live_games else B_LIVE_GAME_EXCLUDED_OPERATOR)
    if live_status_unknown:
        live_review_flags.append(B_LIVE_STATUS_UNKNOWN)
    accepted_risk_notes = _accepted_risk_notes(residual_rule_blockers) if risk_accepted_for_operator else []
    row = {
        "cross_platform_game_key": kalshi_game.get("cross_platform_game_key"),
        "teams": kalshi_game.get("teams") or polymarket_game.get("teams"),
        "game": _game_label(kalshi_game),
        "direction": direction,
        "kalshi_team": kalshi_outcome.get("team"),
        "polymarket_team": polymarket_outcome.get("team"),
        "kalshi_ticker": kalshi_outcome.get("market_ticker"),
        "polymarket_market_id": polymarket_game.get("ids", {}).get("market_id"),
        "polymarket_token_id": polymarket_outcome.get("token_id"),
        "kalshi_leg": {
            "platform": "Kalshi",
            "side": "YES",
            "team": kalshi_outcome.get("team"),
            "market_ticker": kalshi_outcome.get("market_ticker"),
            "ask": kalshi_ask,
            "ask_size": kalshi_outcome.get("ask_size"),
        },
        "polymarket_leg": {
            "platform": "Polymarket",
            "side": "outcome",
            "team": polymarket_outcome.get("team"),
            "market_id": polymarket_game.get("ids", {}).get("market_id"),
            "token_id": polymarket_outcome.get("token_id"),
            "ask": polymarket_ask,
            "ask_size": polymarket_outcome.get("ask_size"),
        },
        "kalshi_ask": kalshi_ask,
        "polymarket_ask": polymarket_ask,
        "gross_edge": gross_edge,
        "conservative_fee_estimate": fee_estimate,
        "net_edge": net_edge,
        "net_edge_status": net_edge_status,
        "kalshi_leg_notional": kalshi_notional,
        "polymarket_leg_notional": polymarket_notional,
        "available_notional": available_notional,
        "available_size": available_notional,
        "kalshi_size_units": kalshi_size["units"],
        "polymarket_size_units": polymarket_size["units"],
        "kalshi_size_unit_interpretation": kalshi_size["interpretation"],
        "polymarket_size_unit_interpretation": polymarket_size["interpretation"],
        "size_unit_status": _size_unit_status(kalshi_size, polymarket_size, depth_gate_passed=depth_gate_passed),
        "size_gate_passed": size_gate_passed,
        "quote_timestamps": quote_timestamps,
        "freshness_status": freshness_status,
        "live_status": live_status_text,
        "live_review_flags": live_review_flags,
        "game_status": {
            "kalshi": _quote_status(kalshi_game, "game_status_at_fetch"),
            "polymarket": _quote_status(polymarket_game, "market_status_at_fetch"),
        },
        "blockers": blockers,
        "action": action,
        "human_accepted_residual_risk": bool(accept_mlb_daily_contingency_risk),
        "operator_accepted_as_arb": bool(operator_accepted_as_arb),
        "residual_risk_type": RESIDUAL_RISK_TYPE if accept_mlb_daily_contingency_risk else None,
        "mathematical_strict_exact_arb": False,
        "strict_exact_arb": False,
        "exact_ready": False,
        "paper_candidate": False,
        "standard_paper_candidate": False,
        "operator_paper_review": action == ACTION_OPERATOR_REVIEW,
        "diagnostic_only": True,
        "shadow_paper_only": True,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "residual_risk_notes": _residual_risk_notes(kalshi_game, polymarket_game),
        "accepted_risk_notes": accepted_risk_notes,
    }
    make_candidate = (
        operator_risk_mode in {"standard", "aggressive"}
        and action == ACTION_OPERATOR_REVIEW
        and net_edge is not None
        and net_edge > 0
    )
    if not make_candidate and row["action"] != ACTION_IGNORE_BLOCKED:
        row["action"] = VISIBLE_WATCH
    return apply_operator_candidate_fields(
        row,
        paper_class=CLASS_OPERATOR,
        assumptions_accepted=["sports_residual_rule_risk"],
        candidate_action="PAPER_CANDIDATE",
        make_candidate=make_candidate,
        mathematical_strict_exact_arb=False,
    )


def _blocked_team_match_row(
    *,
    kalshi_game: dict[str, Any],
    polymarket_game: dict[str, Any],
    accept_mlb_daily_contingency_risk: bool,
    operator_accepted_as_arb: bool,
) -> dict[str, Any]:
    blockers = [B_UNMATCHED_TEAM]
    if not accept_mlb_daily_contingency_risk:
        blockers.append(B_RESIDUAL_NOT_ACCEPTED)
    return {
        "cross_platform_game_key": kalshi_game.get("cross_platform_game_key"),
        "game": _game_label(kalshi_game),
        "direction": "UNMATCHED",
        "kalshi_team": None,
        "polymarket_team": None,
        "kalshi_ticker": None,
        "polymarket_market_id": polymarket_game.get("ids", {}).get("market_id"),
        "polymarket_token_id": None,
        "kalshi_ask": None,
        "polymarket_ask": None,
        "gross_edge": None,
        "conservative_fee_estimate": None,
        "net_edge": None,
        "net_edge_status": "NOT_CALCULATED",
        "kalshi_leg_notional": None,
        "polymarket_leg_notional": None,
        "available_notional": None,
        "available_size": None,
        "kalshi_size_units": None,
        "polymarket_size_units": None,
        "size_unit_status": "not_calculated",
        "size_gate_passed": False,
        "quote_timestamps": {},
        "blockers": blockers,
        "action": ACTION_IGNORE_BLOCKED,
        "human_accepted_residual_risk": bool(accept_mlb_daily_contingency_risk),
        "operator_accepted_as_arb": bool(operator_accepted_as_arb),
        "residual_risk_type": RESIDUAL_RISK_TYPE if accept_mlb_daily_contingency_risk else None,
        "mathematical_strict_exact_arb": False,
        "strict_exact_arb": False,
        "exact_ready": False,
        "paper_candidate": False,
        "standard_paper_candidate": False,
        "operator_paper_review": False,
        "diagnostic_only": True,
        "shadow_paper_only": True,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "residual_risk_notes": _residual_risk_notes(kalshi_game, polymarket_game),
    }


def _blocked_scope_row(
    *,
    kalshi_game: dict[str, Any],
    polymarket_game: dict[str, Any],
    blockers: list[str],
    accept_mlb_daily_contingency_risk: bool,
    operator_accepted_as_arb: bool,
) -> dict[str, Any]:
    effective_blockers = list(dict.fromkeys([*blockers, B_NOT_MLB_DAILY_GAME_WINNER]))
    if not accept_mlb_daily_contingency_risk:
        effective_blockers.append(B_RESIDUAL_NOT_ACCEPTED)
    return ensure_candidate_fields({
        "cross_platform_game_key": kalshi_game.get("cross_platform_game_key") or polymarket_game.get("cross_platform_game_key"),
        "game": _game_label(kalshi_game) or _game_label(polymarket_game),
        "direction": "UNSUPPORTED_SCOPE",
        "kalshi_team": None,
        "polymarket_team": None,
        "kalshi_ticker": None,
        "polymarket_market_id": (polymarket_game.get("ids") or {}).get("market_id") if isinstance(polymarket_game.get("ids"), dict) else None,
        "polymarket_token_id": None,
        "kalshi_ask": None,
        "polymarket_ask": None,
        "gross_edge": None,
        "conservative_fee_estimate": None,
        "net_edge": None,
        "net_edge_status": "NOT_CALCULATED",
        "kalshi_leg_notional": None,
        "polymarket_leg_notional": None,
        "available_notional": None,
        "available_size": None,
        "kalshi_size_units": None,
        "polymarket_size_units": None,
        "size_unit_status": "not_calculated",
        "size_gate_passed": False,
        "quote_timestamps": {},
        "blockers": effective_blockers,
        "action": ACTION_IGNORE_BLOCKED,
        "human_accepted_residual_risk": bool(accept_mlb_daily_contingency_risk),
        "operator_accepted_as_arb": bool(operator_accepted_as_arb),
        "residual_risk_type": RESIDUAL_RISK_TYPE if accept_mlb_daily_contingency_risk else None,
        "mathematical_strict_exact_arb": False,
        "strict_exact_arb": False,
        "exact_ready": False,
        "paper_candidate": False,
        "standard_paper_candidate": False,
        "operator_paper_review": False,
        "diagnostic_only": True,
        "shadow_paper_only": True,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "residual_risk_notes": _residual_risk_notes(kalshi_game, polymarket_game),
    })


def _action(
    blockers: list[str],
    *,
    net_edge_status: str,
    net_edge: float | None,
    gross_edge: float | None,
    operator_accepted_as_arb: bool,
) -> str:
    if B_NOT_MLB_DAILY_GAME_WINNER in blockers or B_UNSUPPORTED_SCOPE in blockers:
        return ACTION_IGNORE_BLOCKED
    if B_RESIDUAL_NOT_ACCEPTED in blockers or B_LIVE_GAME_EXCLUDED in blockers or B_LIVE_GAME_EXCLUDED_OPERATOR in blockers:
        return ACTION_WATCH
    if (
        B_MISSING_QUOTE in blockers
        or B_MISSING_DEPTH in blockers
        or B_STALE_QUOTE in blockers
        or B_UNMATCHED_TEAM in blockers
        or B_INSUFFICIENT_DEPTH in blockers
        or B_MISSING_KALSHI_SIZE in blockers
        or B_MISSING_POLYMARKET_SIZE in blockers
        or B_PARTIAL_DEPTH in blockers
    ):
        return ACTION_WATCH
    if any(blocker in RESIDUAL_RULE_RISK_BLOCKERS for blocker in blockers):
        return ACTION_MANUAL_REVIEW if gross_edge is not None and gross_edge > 0 else ACTION_WATCH
    if B_SIZE_UNIT_REVIEW in blockers:
        return ACTION_MANUAL_REVIEW if gross_edge is not None and gross_edge > 0 else ACTION_WATCH
    if net_edge_status == "FEE_REVIEW_REQUIRED":
        return ACTION_MANUAL_REVIEW
    if blockers:
        return ACTION_WATCH
    if net_edge is not None and net_edge > 0:
        return ACTION_OPERATOR_REVIEW if operator_accepted_as_arb else ACTION_RESIDUAL_REVIEW
    return ACTION_WATCH


def _fee_estimate(
    *,
    kalshi_ask: float | None,
    polymarket_ask: float | None,
    fee_models_available: bool,
) -> tuple[float | None, float | None, str]:
    if kalshi_ask is None or polymarket_ask is None:
        return None, None, "NOT_CALCULATED"
    if not fee_models_available:
        return None, None, "FEE_REVIEW_REQUIRED"
    kalshi_fee = KalshiTieredFeeModel().fee_for_leg(kalshi_ask)
    polymarket_fee = PolymarketConservativeFeeModel().fee_for_leg_for_category(polymarket_ask, category="sports")
    fee = round(kalshi_fee + polymarket_fee, 6)
    gross = 1.0 - kalshi_ask - polymarket_ask
    return fee, round(gross - fee, 6), "OK"


def _timestamp_blockers(
    *,
    quote_timestamps: dict[str, Any],
    generated_at: datetime,
    max_quote_age_seconds: float,
) -> list[str]:
    blockers: list[str] = []
    for value in quote_timestamps.values():
        parsed = _parse_datetime(value)
        if parsed is None:
            blockers.append(B_MISSING_TIMESTAMP)
            continue
        age_seconds = (generated_at - parsed).total_seconds()
        if age_seconds > max_quote_age_seconds:
            blockers.append(B_STALE_QUOTE)
    return list(dict.fromkeys(blockers))


def _outcome_index(game: dict[str, Any], *, venue: str) -> dict[str, Any]:
    quotes = game.get("quotes") if isinstance(game.get("quotes"), dict) else {}
    outcomes = quotes.get("outcomes") if isinstance(quotes.get("outcomes"), list) else []
    token_ids = ((game.get("ids") or {}).get("token_ids") or {}) if venue == "polymarket" else {}
    index: dict[str, Any] = {"_order": []}
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        team = _string_or_none(outcome.get("team"))
        team_key = _team_key(team)
        if not team_key:
            continue
        quote_timestamp = (
            quotes.get("quote_timestamp_utc")
            if venue == "kalshi"
            else quotes.get("quote_timestamp_iso") or quotes.get("fetch_time_utc")
        )
        ask_size_key = _first_present_key(outcome, ("yes_ask_size_dollars", "yes_ask_size", "ask_size"))
        record = {
            "team": team,
            "team_key": team_key,
            "market_ticker": outcome.get("market_ticker"),
            "ask": _first(outcome, ("yes_ask", "ask")),
            "ask_size": outcome.get(ask_size_key) if ask_size_key else None,
            "ask_size_key": ask_size_key,
            "ask_size_unit": _first(outcome, ("ask_size_unit", "yes_ask_size_unit", "size_unit"))
            or quotes.get("size_unit")
            or quotes.get("size_unit_normalized"),
            "quote_size_unit_note": quotes.get("size_unit_note"),
            "quote_source": quotes.get("quote_source"),
            "depth_status": _first(outcome, ("depth_status", "depth_status_observed")),
            "partial_book": outcome.get("partial_book"),
            "book_blockers": outcome.get("book_blockers"),
            "asks_levels": outcome.get("asks_levels"),
            "bids_levels": outcome.get("bids_levels"),
            "quote_timestamp": quote_timestamp,
            "token_id": _token_id_for_team(team, token_ids),
        }
        index[team_key] = record
        index["_order"].append(team_key)
    return index


def _payload_scope_blockers(payload: Any, *, expected_platform: str, expected_date: str) -> list[str]:
    blockers: list[str] = []
    if not isinstance(payload, dict):
        return [B_UNSUPPORTED_SCOPE, B_NOT_MLB_DAILY_GAME_WINNER]
    platform = _string_or_none(payload.get("platform"))
    if platform != expected_platform:
        blockers.append(B_UNSUPPORTED_SCOPE)
    league = _string_or_none(payload.get("league"))
    if league != "MLB":
        blockers.append(B_NOT_MLB_DAILY_GAME_WINNER)
    date_label = _string_or_none(payload.get("date_label") or payload.get("game_date") or payload.get("date"))
    if date_label and date_label != expected_date:
        blockers.append(B_NOT_MLB_DAILY_GAME_WINNER)
    games = payload.get("games")
    if not isinstance(games, list):
        blockers.append(B_UNSUPPORTED_SCOPE)
    elif not date_label:
        game_dates = {
            _string_or_none(game.get("game_date") or game.get("date_label"))
            for game in games
            if isinstance(game, dict)
        }
        if not game_dates or any(game_date != expected_date for game_date in game_dates):
            blockers.append(B_NOT_MLB_DAILY_GAME_WINNER)
    marker_text = " ".join(
        str(payload.get(key) or "")
        for key in ("schema_kind", "source", "market_type", "title", "description", "name")
    )
    if _UNSUPPORTED_SCOPE_RE.search(marker_text):
        blockers.append(B_UNSUPPORTED_SCOPE)
    return list(dict.fromkeys(blockers))


def _scope_blockers_for_matched_game(
    *,
    kalshi_game: dict[str, Any],
    polymarket_game: dict[str, Any],
    expected_date: str,
    payload_scope_blockers: list[str],
) -> list[str]:
    blockers = list(payload_scope_blockers)
    blockers.extend(_game_scope_blockers(kalshi_game, venue="kalshi", expected_date=expected_date))
    blockers.extend(_game_scope_blockers(polymarket_game, venue="polymarket", expected_date=expected_date))
    k_teams = _expected_team_keys(kalshi_game)
    p_teams = _expected_team_keys(polymarket_game)
    if len(k_teams) != 2 or len(p_teams) != 2 or k_teams != p_teams:
        blockers.append(B_NOT_MLB_DAILY_GAME_WINNER)
    return list(dict.fromkeys(blockers))


def _game_scope_blockers(game: dict[str, Any], *, venue: str, expected_date: str) -> list[str]:
    blockers: list[str] = []
    if _string_or_none(game.get("league")) not in (None, "MLB"):
        blockers.append(B_NOT_MLB_DAILY_GAME_WINNER)
    if _string_or_none(game.get("market_type")) != "game_winner":
        blockers.append(B_NOT_MLB_DAILY_GAME_WINNER)
    key = _string_or_none(game.get("cross_platform_game_key"))
    if not key or not key.startswith("MLB-") or expected_date not in key:
        blockers.append(B_NOT_MLB_DAILY_GAME_WINNER)
    game_date = _string_or_none(game.get("game_date") or game.get("date_label"))
    if game_date and game_date != expected_date:
        blockers.append(B_NOT_MLB_DAILY_GAME_WINNER)
    marker_text = " ".join(
        str(game.get(key) or "")
        for key in ("market_type", "teams", "title", "question", "name")
    )
    if _UNSUPPORTED_SCOPE_RE.search(marker_text):
        blockers.append(B_UNSUPPORTED_SCOPE)
    expected_teams = _expected_team_keys(game)
    if len(expected_teams) != 2:
        blockers.append(B_NOT_MLB_DAILY_GAME_WINNER)
    outcome_records = _raw_outcome_records(game)
    outcome_team_keys = [_team_key(outcome.get("team")) for outcome in outcome_records if isinstance(outcome, dict)]
    if len(outcome_records) != 2 or len([key for key in outcome_team_keys if key]) != 2:
        blockers.append(B_NOT_MLB_DAILY_GAME_WINNER)
    elif set(outcome_team_keys) != expected_teams:
        blockers.append(B_NOT_MLB_DAILY_GAME_WINNER)
    return list(dict.fromkeys(blockers))


def _expected_team_keys(game: dict[str, Any]) -> set[str]:
    home = _team_key(game.get("home_team"))
    away = _team_key(game.get("away_team"))
    if home and away and home != away:
        return {home, away}
    teams_text = _string_or_none(game.get("teams"))
    if teams_text:
        parts = re.split(r"\s+(?:vs\.?|at|@)\s+", teams_text, flags=re.IGNORECASE)
        keys = {_team_key(part) for part in parts if _team_key(part)}
        if len(keys) == 2:
            return keys
    return set()


def _raw_outcome_records(game: dict[str, Any]) -> list[dict[str, Any]]:
    quotes = game.get("quotes") if isinstance(game.get("quotes"), dict) else {}
    outcomes = quotes.get("outcomes") if isinstance(quotes.get("outcomes"), list) else []
    return [outcome for outcome in outcomes if isinstance(outcome, dict)]


def _normalize_leg_notional(outcome: dict[str, Any], *, venue: str, price: float | None) -> dict[str, Any]:
    size = _float_or_none(outcome.get("ask_size"))
    if price is None or size is None:
        return {
            "notional": None,
            "status": "missing",
            "missing_size": size is None,
            "units": None,
            "interpretation": "missing_price_or_size",
        }
    unit_text = " ".join(
        str(value or "")
        for value in (
            outcome.get("ask_size_unit"),
            outcome.get("ask_size_key"),
            outcome.get("quote_size_unit_note"),
            outcome.get("quote_source"),
            outcome.get("depth_status"),
        )
    ).lower()
    if venue == "kalshi":
        if outcome.get("ask_size_key") == "yes_ask_size_dollars" or any(
            token in unit_text for token in ("notional", "dollar", "usd")
        ):
            return {
                "notional": round(size, 6),
                "status": "normalized",
                "missing_size": False,
                "units": "dollar_notional",
                "interpretation": "explicit_dollar_notional_size_used_directly",
            }
        if any(token in unit_text for token in ("orderbook", "resting", "level", "contract", "share")) or outcome.get(
            "partial_book"
        ) is not None:
            return {
                "notional": round(price * size, 6),
                "status": "normalized",
                "missing_size": False,
                "units": "orderbook_contract_quantity",
                "interpretation": "raw_orderbook_size_converted_to_notional_as_ask_price_times_size",
            }
        return {
            "notional": None,
            "status": "blocked_unclear",
            "missing_size": False,
            "units": _string_or_none(unit_text),
            "interpretation": "kalshi_size_units_unclear",
        }
    if venue == "polymarket":
        if any(token in unit_text for token in ("notional", "dollar", "usd")):
            return {
                "notional": round(size, 6),
                "status": "normalized",
                "missing_size": False,
                "units": "dollar_notional",
                "interpretation": "explicit_dollar_notional_size_used_directly",
            }
        if any(token in unit_text for token in ("share", "token", "contract", "clob")) or _level_count_present(outcome):
            return {
                "notional": round(price * size, 6),
                "status": "normalized",
                "missing_size": False,
                "units": "token_or_share_quantity",
                "interpretation": "token_or_share_quantity_converted_to_notional_as_ask_price_times_size",
            }
        return {
            "notional": None,
            "status": "blocked_unclear",
            "missing_size": False,
            "units": _string_or_none(unit_text),
            "interpretation": "polymarket_size_units_unclear",
        }
    return {
        "notional": None,
        "status": "blocked_unclear",
        "missing_size": False,
        "units": _string_or_none(unit_text),
        "interpretation": "unknown_venue_size_units",
    }


def _depth_is_acceptable(outcome: dict[str, Any], *, venue: str) -> bool:
    depth_status = str(outcome.get("depth_status") or "").strip().lower()
    if "full_clob" in depth_status or "full clob" in depth_status or "present_full_clob" in depth_status:
        return True
    if outcome.get("partial_book") is False:
        return True
    if venue == "polymarket" and _level_count_present(outcome):
        return True
    blockers = outcome.get("book_blockers")
    if isinstance(blockers, list) and blockers:
        return False
    return False


def _level_count_present(outcome: dict[str, Any]) -> bool:
    asks = _float_or_none(outcome.get("asks_levels"))
    bids = _float_or_none(outcome.get("bids_levels"))
    return asks is not None and asks > 0 and bids is not None and bids > 0


def _size_unit_status(kalshi_size: dict[str, Any], polymarket_size: dict[str, Any], *, depth_gate_passed: bool) -> str:
    if kalshi_size["status"] == "missing" or polymarket_size["status"] == "missing" or not depth_gate_passed:
        return "missing"
    if kalshi_size["status"] != "normalized" or polymarket_size["status"] != "normalized":
        return "blocked_unclear"
    return "normalized"


def _token_id_for_team(team: str | None, token_ids: Any) -> Any:
    if not isinstance(token_ids, dict) or not team:
        return None
    team_key = _team_key(team)
    for candidate, token_id in token_ids.items():
        if _team_key(candidate) == team_key:
            return token_id
    return token_ids.get(team)


def _team_key(team: Any) -> str | None:
    text = _string_or_none(team)
    if not text:
        return None
    normalized = _normalize_team_text(text)
    return _TEAM_ALIASES.get(normalized, normalized.upper() if len(normalized) <= 4 else normalized)


def _normalize_team_text(value: str) -> str:
    lowered = value.strip().lower().replace("&", "and")
    lowered = lowered.replace(".", "")
    lowered = _TOKEN_RE.sub(" ", lowered)
    return " ".join(lowered.split())


def _residual_risk_notes(kalshi_game: dict[str, Any], polymarket_game: dict[str, Any]) -> list[str]:
    notes = [
        "Kalshi postponed/suspended/canceled tail cases may resolve at last fair market price.",
        "Polymarket canceled/no-makeup/tie tail cases may resolve 50-50.",
        "Polymarket shortened/suspended/extra-innings wording may be missing or less explicit in manual evidence.",
    ]
    for game, prefix in ((kalshi_game, "Kalshi"), (polymarket_game, "Polymarket")):
        for key in ("postponement_rules", "cancellation_rules", "suspended_or_shortened_game_rules", "extra_innings_rules"):
            value = _string_or_none(game.get(key))
            if value:
                notes.append(f"{prefix} {key}: {value}")
    return notes


def _residual_rule_risk_blockers(kalshi_game: dict[str, Any], polymarket_game: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    kalshi_cancel = _normalize_rule_text(kalshi_game.get("cancellation_rules"))
    poly_cancel = _normalize_rule_text(polymarket_game.get("cancellation_rules"))
    if "last fair market price" in kalshi_cancel and "50-50" in poly_cancel:
        blockers.append("last_fair_market_price_vs_50_50_cancellation_mismatch")
    kalshi_postpone = _normalize_rule_text(kalshi_game.get("postponement_rules"))
    poly_postpone = _normalize_rule_text(polymarket_game.get("postponement_rules"))
    if kalshi_postpone and poly_postpone and kalshi_postpone != poly_postpone:
        blockers.append("residual_postponement_rule_mismatch")
    blockers.extend(_rule_blockers_from_game(polymarket_game))
    return list(dict.fromkeys(blockers))


def _rule_blockers_from_game(game: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    remaining = game.get("blockers_remaining")
    if isinstance(remaining, list):
        blockers.extend(str(item) for item in remaining if str(item) in RESIDUAL_RULE_RISK_BLOCKERS)
    shortened = _normalize_rule_text(game.get("suspended_or_shortened_game_rules"))
    if "not explicitly stated" in shortened:
        blockers.append("polymarket_shortened_game_rule_not_explicit")
        blockers.append("missing_suspended_or_shortened_game_rules")
    extras = _normalize_rule_text(game.get("extra_innings_rules"))
    if "not explicitly stated" in extras:
        blockers.append("polymarket_extra_innings_rule_not_explicit")
        blockers.append("missing_extra_innings_rules")
    return blockers


def _accepted_risk_notes(blockers: list[str]) -> list[str]:
    return [f"operator_accepted_residual_rule_risk:{blocker}" for blocker in blockers]


def _normalize_rule_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _summary(
    rows: list[dict[str, Any]],
    kalshi_games: list[dict[str, Any]],
    polymarket_games: list[dict[str, Any]],
    matched_game_keys: list[str],
) -> dict[str, Any]:
    action_counts = Counter(row.get("action") for row in rows)
    blocker_counts: Counter[str] = Counter()
    for row in rows:
        blocker_counts.update(row.get("blockers") or [])
    summary = {
        "kalshi_games_loaded": len(kalshi_games),
        "polymarket_games_loaded": len(polymarket_games),
        "matched_games": len(matched_game_keys),
        "rows": len(rows),
        "operator_arb_review_rows": action_counts[ACTION_OPERATOR_REVIEW],
        "operator_paper_review_rows": action_counts[ACTION_OPERATOR_REVIEW],
        "residual_review_rows": action_counts[ACTION_RESIDUAL_REVIEW],
        "manual_review_rows": action_counts[ACTION_MANUAL_REVIEW],
        "watch_rows": action_counts[ACTION_WATCH],
        "ignore_blocked_rows": action_counts[ACTION_IGNORE_BLOCKED],
        "rows_with_positive_gross_edge": sum(1 for row in rows if (row.get("gross_edge") is not None and row.get("gross_edge") > 0)),
        "rows_with_positive_net_edge": sum(1 for row in rows if (row.get("net_edge") is not None and row.get("net_edge") > 0)),
        "rows_with_size_gate_passed": sum(1 for row in rows if row.get("size_gate_passed") is True),
        "rows_missing_quote_or_depth": sum(1 for row in rows if B_MISSING_QUOTE in row.get("blockers", []) or B_MISSING_DEPTH in row.get("blockers", [])),
        "fee_review_required_rows": sum(1 for row in rows if row.get("net_edge_status") == "FEE_REVIEW_REQUIRED"),
        "live_game_rows": sum(1 for row in rows if row.get("live_status") == "live_or_in_progress"),
        "live_game_excluded_rows": sum(
            1
            for row in rows
            if B_LIVE_GAME_EXCLUDED in row.get("blockers", [])
            or B_LIVE_GAME_EXCLUDED_OPERATOR in row.get("blockers", [])
        ),
        "exact_ready_rows": 0,
        "paper_candidate_rows": 0,
        "standard_paper_candidate_rows": 0,
        "top_blockers": [
            {"blocker": blocker, "count": count}
            for blocker, count in blocker_counts.most_common(15)
        ],
    }
    summary.update(candidate_counts(rows))
    summary["paper_candidate_rows"] = summary["total_paper_candidate_rows"]
    summary["standard_paper_candidate_rows"] = summary["total_paper_candidate_rows"]
    return summary


def _row_sort_key(row: dict[str, Any]) -> tuple[float, float, str]:
    net = row.get("net_edge")
    gross = row.get("gross_edge")
    return (
        float(net) if net is not None else -999.0,
        float(gross) if gross is not None else -999.0,
        str(row.get("cross_platform_game_key") or ""),
    )


def _load_json(path: Path, warnings: list[dict[str, Any]], label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append({"input": label, "source_file": str(path), "error": type(exc).__name__})
        return {}


def _games(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    games = payload.get("games")
    if not isinstance(games, list):
        return []
    return [game for game in games if isinstance(game, dict)]


def _is_live_game(game: dict[str, Any]) -> bool:
    status = _quote_status(game, "game_status_at_fetch") or _quote_status(game, "market_status_at_fetch")
    return bool(status and _LIVE_STATUS_RE.search(status))


def _live_status_unknown(kalshi_game: dict[str, Any], polymarket_game: dict[str, Any]) -> bool:
    return not (
        _quote_status(kalshi_game, "game_status_at_fetch")
        or _quote_status(kalshi_game, "market_status_at_fetch")
        or _quote_status(polymarket_game, "game_status_at_fetch")
        or _quote_status(polymarket_game, "market_status_at_fetch")
    )


def _quote_status(game: dict[str, Any], key: str) -> str | None:
    quotes = game.get("quotes") if isinstance(game.get("quotes"), dict) else {}
    return _string_or_none(quotes.get(key))


def _game_label(game: dict[str, Any]) -> str:
    teams = _string_or_none(game.get("teams"))
    game_date = _string_or_none(game.get("game_date"))
    if teams and game_date:
        return f"{game_date} {teams}"
    return teams or _string_or_none(game.get("cross_platform_game_key")) or "unknown game"


def _parse_datetime(value: Any) -> datetime | None:
    text = _string_or_none(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _first(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in mapping:
            return mapping.get(key)
    return None


def _first_present_key(mapping: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        if key in mapping:
            return key
    return None


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return _md(value)


def _md(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")
