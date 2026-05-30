from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.fees import KalshiTieredFeeModel, PolymarketConservativeFeeModel


SCHEMA_KIND = "structural_basket_parlay_scout_v1"
SCHEMA_VERSION = 1

ACTION_STRUCTURAL_REVIEW = "STRUCTURAL_BASKET_REVIEW"
ACTION_OPERATOR_REVIEW = "OPERATOR_ARB_PAPER_REVIEW"
ACTION_CDNA_FILL_FIRST = "CDNA_FILL_FIRST_REVIEW"
ACTION_MANUAL_REVIEW = "MANUAL_REVIEW"
ACTION_WATCH = "WATCH"
ACTION_IGNORE = "IGNORE_BLOCKED"

B_MISSING_GRAPH = "missing_graph_relationship"
B_MISSING_PAYOFF = "missing_payoff_state_definition"
B_NON_EXHAUSTIVE = "non_exhaustive_family"
B_OTHER_UNMAPPED = "other_outcome_unmapped"
B_PARLAY_RULES = "parlay_rules_missing"
B_SETTLEMENT = "settlement_mismatch"
B_TIME_SCOPE = "time_scope_mismatch"
B_MISSING_QUOTE = "missing_quote"
B_STALE_QUOTE = "stale_quote"
B_MISSING_DEPTH = "missing_depth"
B_FEE_REVIEW = "fee_review_required"
B_SIZE_UNITS = "quote_size_unit_review_required"
B_CDNA_SIZE = "cdna_executable_size_unverified"
B_CDNA_DEPTH = "cdna_no_orderbook_depth"

DEFAULT_MAX_QUOTE_AGE_SECONDS = 1800.0


def write_structural_basket_parlay_scout_files(
    *,
    input_dir: Path,
    json_output: Path,
    markdown_output: Path,
    graph_hints_json: Path | None = None,
    generated_at: datetime | None = None,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
) -> dict[str, Any]:
    report = build_structural_basket_parlay_scout_report(
        input_dir=input_dir,
        graph_hints_json=graph_hints_json,
        generated_at=generated_at,
        max_quote_age_seconds=max_quote_age_seconds,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_structural_basket_parlay_scout_markdown(report), encoding="utf-8")
    return report


def build_structural_basket_parlay_scout_report(
    *,
    input_dir: Path,
    graph_hints_json: Path | None = None,
    generated_at: datetime | None = None,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    evidence, warnings = _load_evidence(input_dir)
    graph_hints = _load_graph_hints(graph_hints_json)
    rows: list[dict[str, Any]] = []
    rows.extend(_daily_game_complements(evidence, generated_at=generated, max_quote_age_seconds=max_quote_age_seconds))
    rows.extend(_championship_cross_venue(evidence, generated_at=generated, max_quote_age_seconds=max_quote_age_seconds))
    rows.extend(_family_sum_rows(evidence, generated_at=generated, max_quote_age_seconds=max_quote_age_seconds))
    rows.extend(_parlay_rows(evidence, graph_hints=graph_hints))
    rows.sort(key=_row_sort_key, reverse=True)
    summary = _summary(rows, evidence, warnings, graph_hints)
    return {
        "schema_kind": SCHEMA_KIND,
        "schema_version": SCHEMA_VERSION,
        "source": SCHEMA_KIND,
        "generated_at": generated.isoformat(),
        "input_dir": str(input_dir),
        "graph_hints_json": str(graph_hints_json) if graph_hints_json else None,
        "diagnostic_only": True,
        "saved_files_only": True,
        "strict_exact_arb": False,
        "mathematical_strict_exact_arb": False,
        "exact_ready": False,
        "exact_ready_rows": 0,
        "standard_paper_candidate_rows": 0,
        "standard_paper_candidate_emitted": False,
        "candidate_pair_creation": False,
        "evaluator_invoked": False,
        "rows": rows,
        "summary_counts": summary,
        "top_blockers": summary["top_blockers"],
        "warnings": warnings,
        "safety": {
            "diagnostic_only": True,
            "saved_files_only": True,
            "orders_or_execution_logic_added": False,
            "auth_or_account_logic_added": False,
            "candidate_pair_creation": False,
            "standard_paper_candidate_emitted": False,
            "exact_ready_rows": 0,
            "uses_midpoint": False,
            "uses_asks_for_entry": True,
            "uses_bids_for_exit": True,
            "cdna_strict_pre_fill_allowed": False,
        },
    }


def render_structural_basket_parlay_scout_markdown(report: dict[str, Any]) -> str:
    counts = report.get("summary_counts") or {}
    lines = [
        "# Structural Basket / Parlay Scout",
        "",
        "Saved-file-only diagnostic. This prices recognizable basket/parlay structures with entry asks and exit bids only. It does not create standard candidate pairs, does not invoke evaluator gates, and does not emit standard paper-candidate rows.",
        "",
        "## Summary",
        "",
        f"- rows: `{counts.get('rows', 0)}`",
        f"- structural_basket_review_rows: `{counts.get('structural_basket_review_rows', 0)}`",
        f"- cdna_fill_first_review_rows: `{counts.get('cdna_fill_first_review_rows', 0)}`",
        f"- manual_review_rows: `{counts.get('manual_review_rows', 0)}`",
        f"- watch_rows: `{counts.get('watch_rows', 0)}`",
        f"- ignore_blocked_rows: `{counts.get('ignore_blocked_rows', 0)}`",
        f"- exact_ready_rows: `0`",
        f"- standard_paper_candidate_rows: `0`",
        "",
        "## Top Candidates",
        "",
        "| Type | Description | Entry cost | Gross edge | Net edge | Available notional | Action | Blockers |",
        "|---|---|---:|---:|---:|---:|---|---|",
    ]
    for row in (report.get("rows") or [])[:30]:
        lines.append(
            "| "
            f"{_md(row.get('basket_type'))} | "
            f"{_md(row.get('description'))} | "
            f"{_md(row.get('entry_cost'))} | "
            f"{_md(row.get('gross_edge'))} | "
            f"{_md(row.get('net_edge'))} | "
            f"{_md(row.get('available_notional'))} | "
            f"{_md(row.get('action'))} | "
            f"{_md(', '.join(row.get('blockers') or []))} |"
        )
    if not report.get("rows"):
        lines.append("| none |  |  |  |  |  |  |  |")
    lines.extend(["", "## Top Blockers", "", "| Blocker | Count |", "|---|---:|"])
    for item in report.get("top_blockers") or []:
        lines.append(f"| {_md(item.get('blocker'))} | {_md(item.get('count'))} |")
    if not report.get("top_blockers"):
        lines.append("| none | 0 |")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- diagnostic_only: `true`",
            "- saved_files_only: `true`",
            "- exact_ready_rows: `0`",
            "- standard_paper_candidate_rows: `0`",
            "- candidate_pair_creation: `false`",
            "- evaluator_invoked: `false`",
            "- CDNA rows remain fill-first only.",
        ]
    )
    return "\n".join(lines) + "\n"


def _daily_game_complements(evidence: list[dict[str, Any]], *, generated_at: datetime, max_quote_age_seconds: float) -> list[dict[str, Any]]:
    by_platform_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for payload in evidence:
        platform = _platform(payload)
        for game in _games(payload):
            key = str(game.get("cross_platform_game_key") or "")
            if key:
                by_platform_key[(platform, key)].append({"payload": payload, "game": game})
    rows: list[dict[str, Any]] = []
    keys = {key for _, key in by_platform_key}
    for key in sorted(keys):
        kalshi_games = by_platform_key.get(("kalshi", key), [])
        poly_games = by_platform_key.get(("polymarket", key), [])
        if not kalshi_games or not poly_games:
            continue
        kalshi_game = kalshi_games[0]["game"]
        poly_game = poly_games[0]["game"]
        teams = _game_team_keys(kalshi_game) or _game_team_keys(poly_game)
        if len(teams) != 2:
            continue
        first, second = teams[0], teams[1]
        k_outcomes = _game_outcomes(kalshi_game)
        p_outcomes = _game_outcomes(poly_game)
        rows.append(
            _priced_row(
                basket_type="two_outcome_complement",
                description=f"{key}: Kalshi {first} YES + Polymarket {second} YES",
                legs=[
                    _outcome_leg(k_outcomes.get(first), platform="kalshi", side="YES"),
                    _outcome_leg(p_outcomes.get(second), platform="polymarket", side="YES"),
                ],
                payoff_state_summary="Two-team daily game winner complement; normal-state payoff is 1 if exactly one team wins.",
                base_blockers=_scope_blockers_for_daily_game(kalshi_game, poly_game),
                action_hint=ACTION_STRUCTURAL_REVIEW,
                generated_at=generated_at,
                max_quote_age_seconds=max_quote_age_seconds,
            )
        )
        rows.append(
            _priced_row(
                basket_type="two_outcome_complement",
                description=f"{key}: Kalshi {second} YES + Polymarket {first} YES",
                legs=[
                    _outcome_leg(k_outcomes.get(second), platform="kalshi", side="YES"),
                    _outcome_leg(p_outcomes.get(first), platform="polymarket", side="YES"),
                ],
                payoff_state_summary="Two-team daily game winner complement; normal-state payoff is 1 if exactly one team wins.",
                base_blockers=_scope_blockers_for_daily_game(kalshi_game, poly_game),
                action_hint=ACTION_STRUCTURAL_REVIEW,
                generated_at=generated_at,
                max_quote_age_seconds=max_quote_age_seconds,
            )
        )
    return rows


def _championship_cross_venue(evidence: list[dict[str, Any]], *, generated_at: datetime, max_quote_age_seconds: float) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for payload in evidence:
        if not _is_championship_payload(payload):
            continue
        groups[(str(payload.get("league") or "").upper(), str(payload.get("actual_market_season") or payload.get("requested_season") or payload.get("season") or ""))].append(payload)
    rows: list[dict[str, Any]] = []
    for (league, season), payloads in groups.items():
        for left_index, left in enumerate(payloads):
            for right in payloads[left_index + 1 :]:
                left_platform = _platform(left)
                right_platform = _platform(right)
                if left_platform == right_platform:
                    continue
                left_outcomes = _outcome_map(left)
                right_outcomes = _outcome_map(right)
                for team_key in sorted(set(left_outcomes) & set(right_outcomes)):
                    left_outcome = left_outcomes[team_key]
                    right_outcome = right_outcomes[team_key]
                    rows.append(
                        _priced_row(
                            basket_type="championship_family_synthetic_complement",
                            description=f"{league} {season} {team_key}: {left_platform} YES + {right_platform} NO",
                            legs=[
                                _outcome_leg(left_outcome, platform=left_platform, side="YES"),
                                _outcome_leg(right_outcome, platform=right_platform, side="NO"),
                            ],
                            payoff_state_summary="Same-team championship YES/NO synthetic complement; no-champion/Other/proportional tails require review.",
                            base_blockers=_championship_blockers(left, right),
                            action_hint=_action_hint_for_platforms(left_platform, right_platform),
                            generated_at=generated_at,
                            max_quote_age_seconds=max_quote_age_seconds,
                        )
                    )
                    rows.append(
                        _priced_row(
                            basket_type="championship_family_synthetic_complement",
                            description=f"{league} {season} {team_key}: {right_platform} YES + {left_platform} NO",
                            legs=[
                                _outcome_leg(right_outcome, platform=right_platform, side="YES"),
                                _outcome_leg(left_outcome, platform=left_platform, side="NO"),
                            ],
                            payoff_state_summary="Same-team championship YES/NO synthetic complement; no-champion/Other/proportional tails require review.",
                            base_blockers=_championship_blockers(left, right),
                            action_hint=_action_hint_for_platforms(left_platform, right_platform),
                            generated_at=generated_at,
                            max_quote_age_seconds=max_quote_age_seconds,
                        )
                    )
    return rows


def _family_sum_rows(evidence: list[dict[str, Any]], *, generated_at: datetime, max_quote_age_seconds: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for payload in evidence:
        if not _is_championship_payload(payload):
            continue
        outcomes = [outcome for outcome in _outcomes(payload) if _active_or_unknown(outcome)]
        if len(outcomes) < 3:
            continue
        platform = _platform(payload)
        base_blockers = [B_NON_EXHAUSTIVE]
        other_text = " ".join(str(payload.get(key) or "") for key in ("other_or_no_champion_rule", "market_structure_notes", "rules_text")).lower()
        if "other" in other_text or "no champion" in other_text or "no-champion" in other_text:
            base_blockers.append(B_OTHER_UNMAPPED)
        legs = [_outcome_leg(outcome, platform=platform, side="YES") for outcome in outcomes]
        rows.append(
            _priced_row(
                basket_type="mutually_exclusive_family_sum",
                description=f"{platform} {payload.get('league') or ''} {payload.get('requested_season') or ''}: sum listed team YES asks",
                legs=legs,
                payoff_state_summary="Sum of listed mutually-exclusive championship outcomes versus 1; requires exhaustive family and Other/no-champion mapping.",
                base_blockers=base_blockers,
                action_hint=ACTION_STRUCTURAL_REVIEW,
                generated_at=generated_at,
                max_quote_age_seconds=max_quote_age_seconds,
            )
        )
    return rows


def _parlay_rows(evidence: list[dict[str, Any]], *, graph_hints: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for payload in evidence:
        text = json.dumps({key: payload.get(key) for key in ("market_title", "batch", "market_structure_notes", "rules_text")}, default=str).lower()
        if "parlay" not in text:
            continue
        rows.append(
            {
                "row_id": f"parlay:{_platform(payload)}:{len(rows)+1}",
                "basket_type": "native_parlay_vs_synthetic_parlay",
                "description": str(payload.get("market_title") or "native parlay candidate"),
                "legs": [],
                "entry_cost": None,
                "current_exit_value": None,
                "conservative_fee_estimate": None,
                "gross_edge": None,
                "net_edge": None,
                "available_notional": None,
                "payoff_state_summary": "Native parlay comparison requires explicit parlay rules and payoff-equivalent synthetic leg proof.",
                "blockers": [B_PARLAY_RULES, B_MISSING_PAYOFF] + ([] if graph_hints else [B_MISSING_GRAPH]),
                "action": ACTION_MANUAL_REVIEW,
                "strict_exact_arb": False,
                "exact_ready": False,
                "standard_paper_candidate": False,
                "diagnostic_only": True,
            }
        )
    return rows


def _priced_row(
    *,
    basket_type: str,
    description: str,
    legs: list[dict[str, Any] | None],
    payoff_state_summary: str,
    base_blockers: list[str],
    action_hint: str,
    generated_at: datetime,
    max_quote_age_seconds: float,
) -> dict[str, Any]:
    clean_legs = [leg for leg in legs if leg is not None]
    blockers = list(base_blockers)
    if len(clean_legs) != len(legs):
        blockers.append(B_MISSING_QUOTE)
    entry_cost = None
    exit_value = None
    fee_estimate = None
    available_notional = None
    if clean_legs:
        asks = [leg.get("ask") for leg in clean_legs]
        bids = [leg.get("bid") for leg in clean_legs]
        notionals = [leg.get("available_notional") for leg in clean_legs if leg.get("platform") != "cdna"]
        if any(value is None for value in asks):
            blockers.append(B_MISSING_QUOTE)
        else:
            entry_cost = round(sum(float(value) for value in asks), 8)
        if any(value is None for value in bids):
            exit_value = None
        else:
            exit_value = round(sum(float(value) for value in bids), 8)
        if any(value is None for value in notionals):
            blockers.append(B_MISSING_DEPTH)
        elif notionals:
            available_notional = round(min(float(value) for value in notionals), 8)
        if any(_timestamp_stale(leg.get("quote_timestamp"), generated_at, max_quote_age_seconds) for leg in clean_legs):
            blockers.append(B_STALE_QUOTE)
        fee_result = _fees(clean_legs)
        fee_estimate = fee_result["fee"]
        if fee_result["status"] != "OK":
            blockers.append(B_FEE_REVIEW)
        if any(leg.get("platform") == "cdna" for leg in clean_legs):
            blockers.extend([B_CDNA_SIZE, B_CDNA_DEPTH])
    else:
        blockers.append(B_MISSING_QUOTE)
    gross_edge = round(1.0 - entry_cost, 8) if entry_cost is not None else None
    net_edge = round(gross_edge - fee_estimate, 8) if gross_edge is not None and fee_estimate is not None else None
    blockers = list(dict.fromkeys(blockers))
    action = _action(blockers, gross_edge= gross_edge, net_edge=net_edge, action_hint=action_hint)
    return {
        "row_id": _row_id(basket_type, description),
        "basket_type": basket_type,
        "description": description,
        "legs": clean_legs,
        "entry_cost": entry_cost,
        "current_exit_value": exit_value,
        "conservative_fee_estimate": fee_estimate,
        "gross_edge": gross_edge,
        "net_edge": net_edge,
        "available_notional": available_notional,
        "payoff_state_summary": payoff_state_summary,
        "blockers": blockers,
        "action": action,
        "strict_exact_arb": False,
        "mathematical_strict_exact_arb": False,
        "exact_ready": False,
        "standard_paper_candidate": False,
        "diagnostic_only": True,
    }


def _action(blockers: list[str], *, gross_edge: float | None, net_edge: float | None, action_hint: str) -> str:
    blocking = set(blockers)
    hard_watch = {B_MISSING_QUOTE, B_STALE_QUOTE, B_MISSING_DEPTH, B_FEE_REVIEW, B_SIZE_UNITS}
    if blocking & {B_PARLAY_RULES, B_MISSING_PAYOFF, B_NON_EXHAUSTIVE, B_OTHER_UNMAPPED, B_SETTLEMENT, B_TIME_SCOPE}:
        if action_hint == ACTION_CDNA_FILL_FIRST and gross_edge is not None and gross_edge > 0 and not (blocking & hard_watch):
            return ACTION_CDNA_FILL_FIRST
        return ACTION_MANUAL_REVIEW if gross_edge is not None and gross_edge > 0 else ACTION_WATCH
    if blocking & hard_watch:
        return ACTION_WATCH
    if gross_edge is None or gross_edge <= 0:
        return ACTION_WATCH
    if action_hint == ACTION_CDNA_FILL_FIRST:
        return ACTION_CDNA_FILL_FIRST
    if net_edge is not None and net_edge > 0:
        return action_hint
    return ACTION_MANUAL_REVIEW


def _outcome_leg(outcome: dict[str, Any] | None, *, platform: str, side: str) -> dict[str, Any] | None:
    if outcome is None:
        return None
    platform = _normalize_platform(platform)
    prefix = side.lower()
    if platform == "cdna":
        display_key = "display_price" if side == "YES" else "display_no_price"
        price = _float_or_none(outcome.get(display_key))
        cdna_fee = _cdna_fee(outcome)
        ask = round(price + cdna_fee, 8) if price is not None else None
        bid = None
        ask_size = None
        notional = None
        depth_status = "display_price_only"
    else:
        ask = _float_or_none(outcome.get(f"{prefix}_ask"))
        bid = _float_or_none(outcome.get(f"{prefix}_bid"))
        ask_size = _float_or_none(outcome.get(f"{prefix}_ask_size"))
        notional = round(ask * ask_size, 8) if ask is not None and ask_size is not None else None
        depth_status = _string_or_none(outcome.get("depth_status"))
    return {
        "platform": platform,
        "side": side,
        "team_key": _team_key_from_outcome(outcome),
        "team_name": _string_or_none(outcome.get("team_name") or outcome.get("outcome_name")),
        "ticker_or_token": _ticker_or_token(outcome, side=side, platform=platform),
        "ask": ask,
        "bid": bid,
        "ask_size": ask_size,
        "available_notional": notional,
        "depth_status": depth_status,
        "quote_timestamp": _string_or_none(outcome.get("quote_timestamp")),
    }


def _fees(legs: list[dict[str, Any]]) -> dict[str, Any]:
    total = 0.0
    for leg in legs:
        ask = _float_or_none(leg.get("ask"))
        platform = leg.get("platform")
        if ask is None:
            return {"fee": None, "status": "NOT_CALCULATED"}
        if platform == "kalshi":
            total += KalshiTieredFeeModel().fee_for_leg(ask)
        elif platform == "polymarket":
            total += PolymarketConservativeFeeModel().fee_for_leg_for_category(ask, category="sports")
        elif platform == "cdna":
            continue
        else:
            return {"fee": None, "status": "FEE_REVIEW_REQUIRED"}
    return {"fee": round(total, 8), "status": "OK"}


def _daily_game_quote_outcome(outcome: dict[str, Any]) -> dict[str, Any]:
    quote = outcome.get("quote") if isinstance(outcome.get("quote"), dict) else {}
    merged = dict(outcome)
    for key, value in quote.items():
        merged.setdefault(key, value)
    return merged


def _game_outcomes(game: dict[str, Any]) -> dict[str, dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for key in ("outcomes", "teams", "quotes"):
        value = game.get(key)
        if isinstance(value, list):
            candidates.extend(item for item in value if isinstance(item, dict))
        elif isinstance(value, dict):
            candidates.extend(item for item in value.values() if isinstance(item, dict))
    mapped: dict[str, dict[str, Any]] = {}
    for outcome in candidates:
        merged = _daily_game_quote_outcome(outcome)
        key = _team_key_from_outcome(merged)
        if key:
            mapped[key] = merged
    return mapped


def _game_team_keys(game: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    for key in ("away_team", "home_team"):
        values.append(game.get(key))
    teams = game.get("teams")
    if isinstance(teams, list):
        for team in teams:
            if isinstance(team, dict):
                values.append(team.get("team_name") or team.get("name") or team.get("outcome_name"))
            else:
                values.append(team)
    keys: list[str] = []
    for value in values:
        key = _team_key_any(value)
        if key and key not in keys:
            keys.append(key)
    return keys


def _scope_blockers_for_daily_game(kalshi_game: dict[str, Any], poly_game: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    for game in (kalshi_game, poly_game):
        if str(game.get("market_type") or "").lower() not in {"game_winner", "moneyline", ""}:
            blockers.append(B_MISSING_PAYOFF)
    if len(_game_team_keys(kalshi_game) or _game_team_keys(poly_game)) != 2:
        blockers.append(B_MISSING_PAYOFF)
    return blockers


def _championship_blockers(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    left_league = str(left.get("league") or "").upper()
    right_league = str(right.get("league") or "").upper()
    if left_league and right_league and left_league != right_league:
        blockers.append(B_SETTLEMENT)
    left_season = str(left.get("actual_market_season") or left.get("requested_season") or left.get("season") or "")
    right_season = str(right.get("actual_market_season") or right.get("requested_season") or right.get("season") or "")
    if left_season and right_season and left_season != right_season:
        blockers.append(B_TIME_SCOPE)
    notes = " ".join(
        str(payload.get(key) or "")
        for payload in (left, right)
        for key in ("other_or_no_champion_rule", "market_structure_notes", "void_cancellation_rules")
    ).lower()
    if "other" in notes or "no champion" in notes or "proportional" in notes:
        blockers.append(B_OTHER_UNMAPPED)
    return blockers


def _action_hint_for_platforms(left_platform: str, right_platform: str) -> str:
    if "cdna" in {left_platform, right_platform}:
        return ACTION_CDNA_FILL_FIRST
    return ACTION_STRUCTURAL_REVIEW


def _load_evidence(input_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    warnings: list[dict[str, Any]] = []
    paths = [input_dir] if input_dir.is_file() else sorted(input_dir.rglob("*.json")) if input_dir.exists() else []
    evidence: list[dict[str, Any]] = []
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            warnings.append({"source_file": str(path), "reason": f"json_unreadable:{type(exc).__name__}"})
            continue
        if not isinstance(payload, dict):
            continue
        if not (isinstance(payload.get("outcomes"), list) or isinstance(payload.get("games"), list)):
            continue
        if not payload.get("platform"):
            continue
        payload = dict(payload)
        payload["_source_file"] = str(path)
        evidence.append(payload)
    return evidence, warnings


def _load_graph_hints(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return payload if isinstance(payload, dict) else None


def _games(payload: dict[str, Any]) -> list[dict[str, Any]]:
    games = payload.get("games")
    return [game for game in games if isinstance(game, dict)] if isinstance(games, list) else []


def _outcomes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    outcomes = payload.get("outcomes")
    return [outcome for outcome in outcomes if isinstance(outcome, dict)] if isinstance(outcomes, list) else []


def _outcome_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for outcome in _outcomes(payload):
        key = _team_key_from_outcome(outcome)
        if key:
            mapped[key] = outcome
    return mapped


def _is_championship_payload(payload: dict[str, Any]) -> bool:
    text = " ".join(str(payload.get(key) or "") for key in ("batch", "market_title", "market_structure_notes")).lower()
    return "championship" in text or "champion" in text or "world series" in text


def _active_or_unknown(outcome: dict[str, Any]) -> bool:
    status = str(outcome.get("outcome_status") or outcome.get("status") or "").lower()
    return not any(token in status for token in ("inactive", "expired", "resolved", "closed", "finalized", "settled"))


def _platform(payload: dict[str, Any]) -> str:
    return _normalize_platform(str(payload.get("platform") or ""))


def _normalize_platform(value: str) -> str:
    text = value.strip().lower()
    if "crypto.com" in text or "cdna" in text:
        return "cdna"
    if "poly" in text:
        return "polymarket"
    if "kalshi" in text:
        return "kalshi"
    return text or "unknown"


def _ticker_or_token(outcome: dict[str, Any], *, side: str, platform: str) -> str | None:
    if platform == "polymarket":
        return _string_or_none(outcome.get(f"token_id_{side.lower()}") or outcome.get(f"{side.lower()}_token_id") or outcome.get("market_id"))
    if platform == "kalshi":
        return _string_or_none(outcome.get("market_ticker") or outcome.get("ticker"))
    if platform == "cdna":
        return _string_or_none(outcome.get("contract_id") or outcome.get("symbol"))
    return None


def _cdna_fee(outcome: dict[str, Any]) -> float:
    for key in ("fee_per_contract", "exchange_fee", "exchange_fee_per_contract"):
        value = _float_or_none(outcome.get(key))
        if value is not None:
            return value
    return 0.02


def _timestamp_stale(value: Any, generated_at: datetime, max_quote_age_seconds: float) -> bool:
    parsed = _parse_datetime(value)
    if parsed is None:
        return True
    return (generated_at - parsed).total_seconds() > max_quote_age_seconds


def _parse_datetime(value: Any) -> datetime | None:
    text = _string_or_none(value)
    if not text:
        return None
    if re.fullmatch(r"\d{10,}", text):
        number = int(text)
        if number > 10_000_000_000:
            return datetime.fromtimestamp(number / 1000.0, tz=timezone.utc)
        return datetime.fromtimestamp(number, tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


TEAM_ALIASES = {
    "okc": "OKC", "oklahoma city thunder": "OKC", "thunder": "OKC",
    "nyk": "NYK", "new york knicks": "NYK", "knicks": "NYK",
    "sas": "SAS", "san antonio spurs": "SAS", "spurs": "SAS",
    "lad": "LAD", "los angeles dodgers": "LAD", "dodgers": "LAD",
    "nyy": "NYY", "new york yankees": "NYY", "yankees": "NYY",
    "atl": "ATL", "atlanta braves": "ATL", "braves": "ATL",
    "tor": "TOR", "toronto blue jays": "TOR", "blue jays": "TOR",
    "cws": "CWS", "chicago white sox": "CWS", "white sox": "CWS",
    "ath": "ATH", "athletics": "ATH", "oakland athletics": "ATH", "a s": "ATH",
    "laa": "LAA", "los angeles angels": "LAA", "angels": "LAA",
    "hou": "HOU", "houston astros": "HOU", "astros": "HOU",
    "tex": "TEX", "texas rangers": "TEX", "rangers": "TEX",
    "det": "DET", "detroit tigers": "DET", "tigers": "DET",
}
_TOKEN_RE = re.compile(r"[^a-z0-9]+")


def _team_key_from_outcome(outcome: dict[str, Any]) -> str | None:
    values = [outcome.get("team_name"), outcome.get("outcome_name"), outcome.get("name"), outcome.get("market_ticker"), outcome.get("symbol")]
    aliases = outcome.get("team_aliases")
    if isinstance(aliases, list):
        values.extend(aliases)
    for value in values:
        key = _team_key_any(value)
        if key:
            return key
    return None


def _team_key_any(value: Any) -> str | None:
    text = _string_or_none(value)
    if not text:
        return None
    if "-" in text or "_" in text:
        for part in reversed([part for part in re.split(r"[-_]", text) if part]):
            key = _team_key_any(part)
            if key:
                return key
    normalized = _TOKEN_RE.sub(" ", text.lower()).strip()
    return TEAM_ALIASES.get(normalized)


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


def _row_id(basket_type: str, description: str) -> str:
    safe = _TOKEN_RE.sub("_", description.lower()).strip("_")[:120]
    return f"{basket_type}:{safe}"


def _row_sort_key(row: dict[str, Any]) -> tuple[int, float, float]:
    action_score = {
        ACTION_OPERATOR_REVIEW: 6,
        ACTION_CDNA_FILL_FIRST: 5,
        ACTION_STRUCTURAL_REVIEW: 4,
        ACTION_MANUAL_REVIEW: 3,
        ACTION_WATCH: 2,
        ACTION_IGNORE: 1,
    }.get(row.get("action"), 0)
    net = _float_or_none(row.get("net_edge"))
    gross = _float_or_none(row.get("gross_edge"))
    return (action_score, net if net is not None else -999.0, gross if gross is not None else -999.0)


def _summary(rows: list[dict[str, Any]], evidence: list[dict[str, Any]], warnings: list[dict[str, Any]], graph_hints: dict[str, Any] | None) -> dict[str, Any]:
    actions = Counter(row.get("action") for row in rows)
    types = Counter(row.get("basket_type") for row in rows)
    blockers = Counter()
    for row in rows:
        blockers.update(row.get("blockers") or [])
    return {
        "rows": len(rows),
        "evidence_files_loaded": len(evidence),
        "warnings": len(warnings),
        "graph_hints_loaded": graph_hints is not None,
        "structural_basket_review_rows": actions.get(ACTION_STRUCTURAL_REVIEW, 0),
        "operator_arb_review_rows": actions.get(ACTION_OPERATOR_REVIEW, 0),
        "cdna_fill_first_review_rows": actions.get(ACTION_CDNA_FILL_FIRST, 0),
        "manual_review_rows": actions.get(ACTION_MANUAL_REVIEW, 0),
        "watch_rows": actions.get(ACTION_WATCH, 0),
        "ignore_blocked_rows": actions.get(ACTION_IGNORE, 0),
        "positive_gross_rows": sum(1 for row in rows if (_float_or_none(row.get("gross_edge")) or 0) > 0),
        "positive_net_rows": sum(1 for row in rows if (_float_or_none(row.get("net_edge")) or 0) > 0),
        "basket_type_counts": dict(sorted(types.items())),
        "exact_ready_rows": 0,
        "standard_paper_candidate_rows": 0,
        "top_blockers": [{"blocker": key, "count": value} for key, value in blockers.most_common(15)],
    }


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")
