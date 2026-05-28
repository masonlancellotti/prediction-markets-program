from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.fees import KalshiTieredFeeModel, PolymarketConservativeFeeModel


SCHEMA_VERSION = 1
SCHEMA_KIND = "sports_mlb_daily_residual_risk_scout_v1"
REPORT_SOURCE = "sports_mlb_daily_residual_risk_scout_v1"

ACTION_RESIDUAL_REVIEW = "RESIDUAL_RISK_SHADOW_PAPER_REVIEW"
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
B_MISSING_FEE_MODEL = "missing_or_uncertain_fee_model"
B_SIZE_UNIT_REVIEW = "quote_size_unit_review_required"
B_INSUFFICIENT_DEPTH = "available_notional_below_minimum"
B_NO_POSITIVE_GROSS_EDGE = "no_positive_gross_edge"
B_NO_POSITIVE_NET_EDGE = "no_positive_net_edge_after_fees"

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
    include_live_games: bool = False,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
    min_available_notional: float = DEFAULT_MIN_AVAILABLE_NOTIONAL,
    generated_at: datetime | None = None,
    fee_models_available: bool = True,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
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
            accept_mlb_daily_contingency_risk=accept_mlb_daily_contingency_risk,
            include_live_games=include_live_games,
            fee_models_available=fee_models_available,
            payload_scope_blockers=payload_scope_blockers,
        )
        if game_rows:
            matched_game_keys.append(key)
            rows.extend(game_rows)
        else:
            unmatched_game_keys.append(key)

    rows.sort(key=_row_sort_key, reverse=True)
    summary = _summary(rows, kalshi_games, polymarket_games, matched_game_keys)
    return {
        "schema_version": SCHEMA_VERSION,
        "schema_kind": SCHEMA_KIND,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "diagnostic_only": True,
        "shadow_paper_only": True,
        "human_accepted_residual_risk": bool(accept_mlb_daily_contingency_risk),
        "strict_exact_arb": False,
        "paper_candidate_emitted": False,
        "date": date,
        "include_live_games": bool(include_live_games),
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
            "strict_exact_arb": False,
            "exact_ready": False,
            "paper_candidate": False,
            "paper_candidate_emitted": False,
            "affects_global_evaluator_gates": False,
            "override_default_enabled": False,
            "orders_or_execution_logic_added": False,
            "auth_or_account_logic_added": False,
            "saved_files_only": True,
        },
    }


def write_sports_mlb_daily_residual_risk_files(
    *,
    kalshi_evidence: Path,
    polymarket_evidence: Path,
    date: str,
    accept_mlb_daily_contingency_risk: bool,
    include_live_games: bool,
    json_output: Path,
    markdown_output: Path,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
    min_available_notional: float = DEFAULT_MIN_AVAILABLE_NOTIONAL,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_sports_mlb_daily_residual_risk_report(
        kalshi_evidence=kalshi_evidence,
        polymarket_evidence=polymarket_evidence,
        date=date,
        accept_mlb_daily_contingency_risk=accept_mlb_daily_contingency_risk,
        include_live_games=include_live_games,
        max_quote_age_seconds=max_quote_age_seconds,
        min_available_notional=min_available_notional,
        generated_at=generated_at,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_sports_mlb_daily_residual_risk_markdown(report), encoding="utf-8")
    return report


def render_sports_mlb_daily_residual_risk_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary_counts") or {}
    rows = report.get("rows") or []
    review_rows = [row for row in rows if row.get("action") == ACTION_RESIDUAL_REVIEW]
    other_rows = [row for row in rows if row.get("action") != ACTION_RESIDUAL_REVIEW]
    lines = [
        "# MLB Daily Residual-Risk Scout",
        "",
        "Saved-evidence-only diagnostic for MLB daily game-winner cross-venue baskets. This is not strict riskless arb; it only reviews normal-state opportunities when the MLB daily contingency-risk override is explicitly enabled.",
        "",
        "## Summary",
        "",
        f"- date: `{_md(report.get('date'))}`",
        f"- human_accepted_residual_risk: `{str(bool(report.get('human_accepted_residual_risk'))).lower()}`",
        f"- strict_exact_arb: `false`",
        f"- paper_candidate_emitted: `false`",
        f"- games_loaded_kalshi: `{(report.get('games_loaded') or {}).get('kalshi', 0)}`",
        f"- games_loaded_polymarket: `{(report.get('games_loaded') or {}).get('polymarket', 0)}`",
        f"- matched_games: `{report.get('matched_games', 0)}`",
        f"- rows: `{summary.get('rows', 0)}`",
        f"- residual_review_rows: `{summary.get('residual_review_rows', 0)}`",
        f"- manual_review_rows: `{summary.get('manual_review_rows', 0)}`",
        f"- watch_rows: `{summary.get('watch_rows', 0)}`",
        f"- exact_ready_rows: `0`",
        f"- paper_candidate_rows: `0`",
        "",
        "## Residual-Risk Shadow Review Rows",
        "",
    ]
    lines.extend(_row_table(review_rows))
    lines.extend(["", "## Watch / Manual Review Rows", ""])
    lines.extend(_row_table(other_rows[:50]))
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
            "- strict_exact_arb: `false`",
            "- exact_ready: `false`",
            "- paper_candidate: `false`",
            "- paper_candidate_emitted: `false`",
            "- affects_global_evaluator_gates: `false`",
            "- override_default_enabled: `false`",
            "- saved_files_only: `true`",
        ]
    )
    return "\n".join(lines) + "\n"


def _row_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| Action | Gross Edge | Net Edge | Size | Game | Direction | Kalshi Team | Polymarket Team | Blockers |",
        "|---|---:|---:|---:|---|---|---|---|---|",
    ]
    if not rows:
        lines.append("| none |  |  |  |  |  |  |  |  |")
        return lines
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("action")),
                    _fmt(row.get("gross_edge")),
                    _fmt(row.get("net_edge")),
                    _fmt(row.get("available_size")),
                    _md(row.get("game")),
                    _md(row.get("direction")),
                    _md(row.get("kalshi_team")),
                    _md(row.get("polymarket_team")),
                    _md(",".join(row.get("blockers") or []) or "none"),
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
    include_live_games: bool,
    fee_models_available: bool,
    payload_scope_blockers: list[str],
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
            include_live_games=include_live_games,
            fee_models_available=fee_models_available,
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
            include_live_games=include_live_games,
            fee_models_available=fee_models_available,
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
    include_live_games: bool,
    fee_models_available: bool,
) -> dict[str, Any]:
    kalshi_ask = _float_or_none(kalshi_outcome.get("ask"))
    polymarket_ask = _float_or_none(polymarket_outcome.get("ask"))
    kalshi_size = _float_or_none(kalshi_outcome.get("ask_size"))
    polymarket_size = _float_or_none(polymarket_outcome.get("ask_size"))
    gross_edge = (
        round(1.0 - kalshi_ask - polymarket_ask, 6)
        if kalshi_ask is not None and polymarket_ask is not None
        else None
    )
    available_size = (
        round(min(kalshi_size, polymarket_size), 6)
        if kalshi_size is not None and polymarket_size is not None
        else None
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
    if not accept_mlb_daily_contingency_risk:
        blockers.append(B_RESIDUAL_NOT_ACCEPTED)
    if kalshi_ask is None or polymarket_ask is None:
        blockers.append(B_MISSING_QUOTE)
    if kalshi_size is None or polymarket_size is None or available_size is None or available_size <= 0:
        blockers.append(B_MISSING_DEPTH)
    elif available_size < min_available_notional:
        blockers.append(B_INSUFFICIENT_DEPTH)
    if available_size is not None and not (_size_unit_reviewed(kalshi_outcome) and _size_unit_reviewed(polymarket_outcome)):
        blockers.append(B_SIZE_UNIT_REVIEW)
    blockers.extend(
        _timestamp_blockers(
            quote_timestamps=quote_timestamps,
            generated_at=generated_at,
            max_quote_age_seconds=max_quote_age_seconds,
        )
    )
    live_status = _is_live_game(kalshi_game) or _is_live_game(polymarket_game)
    if live_status and not include_live_games:
        blockers.append(B_LIVE_GAME_EXCLUDED)
    if net_edge_status == "FEE_REVIEW_REQUIRED":
        blockers.append(B_MISSING_FEE_MODEL)
    if gross_edge is not None and gross_edge <= 0:
        blockers.append(B_NO_POSITIVE_GROSS_EDGE)
    if net_edge is not None and net_edge <= 0:
        blockers.append(B_NO_POSITIVE_NET_EDGE)
    blockers = list(dict.fromkeys(blockers))
    action = _action(blockers, net_edge_status=net_edge_status, net_edge=net_edge)
    return {
        "cross_platform_game_key": kalshi_game.get("cross_platform_game_key"),
        "game": _game_label(kalshi_game),
        "direction": direction,
        "kalshi_team": kalshi_outcome.get("team"),
        "polymarket_team": polymarket_outcome.get("team"),
        "kalshi_ticker": kalshi_outcome.get("market_ticker"),
        "polymarket_market_id": polymarket_game.get("ids", {}).get("market_id"),
        "polymarket_token_id": polymarket_outcome.get("token_id"),
        "kalshi_ask": kalshi_ask,
        "polymarket_ask": polymarket_ask,
        "gross_edge": gross_edge,
        "conservative_fee_estimate": fee_estimate,
        "net_edge": net_edge,
        "net_edge_status": net_edge_status,
        "available_size": available_size,
        "quote_timestamps": quote_timestamps,
        "game_status": {
            "kalshi": _quote_status(kalshi_game, "game_status_at_fetch"),
            "polymarket": _quote_status(polymarket_game, "market_status_at_fetch"),
        },
        "blockers": blockers,
        "action": action,
        "human_accepted_residual_risk": bool(accept_mlb_daily_contingency_risk),
        "residual_risk_type": RESIDUAL_RISK_TYPE if accept_mlb_daily_contingency_risk else None,
        "strict_exact_arb": False,
        "exact_ready": False,
        "paper_candidate": False,
        "diagnostic_only": True,
        "shadow_paper_only": True,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "residual_risk_notes": _residual_risk_notes(kalshi_game, polymarket_game),
    }


def _blocked_team_match_row(
    *,
    kalshi_game: dict[str, Any],
    polymarket_game: dict[str, Any],
    accept_mlb_daily_contingency_risk: bool,
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
        "available_size": None,
        "quote_timestamps": {},
        "blockers": blockers,
        "action": ACTION_IGNORE_BLOCKED,
        "human_accepted_residual_risk": bool(accept_mlb_daily_contingency_risk),
        "residual_risk_type": RESIDUAL_RISK_TYPE if accept_mlb_daily_contingency_risk else None,
        "strict_exact_arb": False,
        "exact_ready": False,
        "paper_candidate": False,
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
) -> dict[str, Any]:
    effective_blockers = list(dict.fromkeys([*blockers, B_NOT_MLB_DAILY_GAME_WINNER]))
    if not accept_mlb_daily_contingency_risk:
        effective_blockers.append(B_RESIDUAL_NOT_ACCEPTED)
    return {
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
        "available_size": None,
        "quote_timestamps": {},
        "blockers": effective_blockers,
        "action": ACTION_IGNORE_BLOCKED,
        "human_accepted_residual_risk": bool(accept_mlb_daily_contingency_risk),
        "residual_risk_type": RESIDUAL_RISK_TYPE if accept_mlb_daily_contingency_risk else None,
        "strict_exact_arb": False,
        "exact_ready": False,
        "paper_candidate": False,
        "diagnostic_only": True,
        "shadow_paper_only": True,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "residual_risk_notes": _residual_risk_notes(kalshi_game, polymarket_game),
    }


def _action(blockers: list[str], *, net_edge_status: str, net_edge: float | None) -> str:
    if B_NOT_MLB_DAILY_GAME_WINNER in blockers or B_UNSUPPORTED_SCOPE in blockers:
        return ACTION_IGNORE_BLOCKED
    if B_RESIDUAL_NOT_ACCEPTED in blockers or B_LIVE_GAME_EXCLUDED in blockers:
        return ACTION_WATCH
    if (
        B_MISSING_QUOTE in blockers
        or B_MISSING_DEPTH in blockers
        or B_STALE_QUOTE in blockers
        or B_UNMATCHED_TEAM in blockers
        or B_INSUFFICIENT_DEPTH in blockers
    ):
        return ACTION_WATCH
    if B_SIZE_UNIT_REVIEW in blockers:
        return ACTION_MANUAL_REVIEW if net_edge is not None and net_edge > 0 else ACTION_WATCH
    if net_edge_status == "FEE_REVIEW_REQUIRED":
        return ACTION_MANUAL_REVIEW
    if blockers:
        return ACTION_WATCH
    if net_edge is not None and net_edge > 0:
        return ACTION_RESIDUAL_REVIEW
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
            "ask_size_unit": _first(outcome, ("ask_size_unit", "yes_ask_size_unit", "size_unit")) or quotes.get("size_unit") or quotes.get("size_unit_normalized"),
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


def _size_unit_reviewed(outcome: dict[str, Any]) -> bool:
    unit = _string_or_none(outcome.get("ask_size_unit"))
    if unit and unit.lower() in {"notional_usd", "usd", "dollars", "dollar_notional"}:
        return True
    return outcome.get("ask_size_key") == "yes_ask_size_dollars"


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
    return {
        "kalshi_games_loaded": len(kalshi_games),
        "polymarket_games_loaded": len(polymarket_games),
        "matched_games": len(matched_game_keys),
        "rows": len(rows),
        "residual_review_rows": action_counts[ACTION_RESIDUAL_REVIEW],
        "manual_review_rows": action_counts[ACTION_MANUAL_REVIEW],
        "watch_rows": action_counts[ACTION_WATCH],
        "ignore_blocked_rows": action_counts[ACTION_IGNORE_BLOCKED],
        "rows_with_positive_gross_edge": sum(1 for row in rows if (row.get("gross_edge") is not None and row.get("gross_edge") > 0)),
        "rows_with_positive_net_edge": sum(1 for row in rows if (row.get("net_edge") is not None and row.get("net_edge") > 0)),
        "rows_missing_quote_or_depth": sum(1 for row in rows if B_MISSING_QUOTE in row.get("blockers", []) or B_MISSING_DEPTH in row.get("blockers", [])),
        "fee_review_required_rows": sum(1 for row in rows if row.get("net_edge_status") == "FEE_REVIEW_REQUIRED"),
        "live_game_rows": sum(1 for row in rows if B_LIVE_GAME_EXCLUDED in row.get("blockers", [])),
        "exact_ready_rows": 0,
        "paper_candidate_rows": 0,
        "top_blockers": [
            {"blocker": blocker, "count": count}
            for blocker, count in blocker_counts.most_common(15)
        ],
    }


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
