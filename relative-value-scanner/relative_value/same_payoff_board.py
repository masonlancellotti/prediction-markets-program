from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.mlb_scope import mlb_world_series_profile
from relative_value.mlb_scope import same_mlb_world_series_team
from relative_value.nba_scope import nba_championship_profile
from relative_value.nba_scope import same_nba_championship_team
from relative_value.nhl_scope import nhl_stanley_cup_profile
from relative_value.nhl_scope import same_nhl_stanley_cup_team


SCHEMA_VERSION = 1
DEFAULT_SETTLEMENT_TOLERANCE_SECONDS = 3600.0
DEFAULT_MAX_QUOTE_AGE_SECONDS = 1800.0
ALLOWED_RECOMMENDATIONS = {
    "SKIP",
    "BETTER_SOURCE_TARGETING",
    "RELATIONSHIP_REVIEW",
    "ENRICH_IF_APPROVED",
    "WATCH_ONLY",
}
BOARD_DISCLAIMER = (
    "Saved-file deterministic diagnostic board only. It does not call live APIs, "
    "does not mutate matcher or evaluator output, and does not grant execution permission."
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_YES_TOKENS = {"yes", "y", "true"}
_NO_TOKENS = {"no", "n", "false"}
_NEGATION_TOKENS = {"not", "no", "never", "fail", "fails", "below", "under", "less"}
_OVER_TOKENS = {"over", "above", "exceed", "exceeds", "greater", "more", "atleast", "minimum"}
_UNDER_TOKENS = {"under", "below", "less", "fewer", "lower", "atmost", "maximum"}
_SPORT_LEAGUES = {"nba", "nfl", "mlb", "nhl", "mls", "uefa", "epl"}


def build_same_payoff_board_files(
    *,
    pairs_path: Path,
    polymarket_enriched_path: Path,
    kalshi_enriched_path: Path,
    json_output_path: Path | None = None,
    markdown_output_path: Path | None = None,
    now: datetime | None = None,
    settlement_tolerance_seconds: float = DEFAULT_SETTLEMENT_TOLERANCE_SECONDS,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
) -> dict[str, Any]:
    generated_at = now or datetime.now(timezone.utc)
    _require_tz_aware(generated_at, "now")
    pairs_payload = _load_json_object(pairs_path, "pairs")
    polymarket_payload = _load_json_object(polymarket_enriched_path, "polymarket_enriched")
    kalshi_payload = _load_json_object(kalshi_enriched_path, "kalshi_enriched")
    payload = build_same_payoff_board(
        pairs_payload=pairs_payload,
        polymarket_payload=polymarket_payload,
        kalshi_payload=kalshi_payload,
        inputs={
            "pairs": str(pairs_path),
            "polymarket_enriched": str(polymarket_enriched_path),
            "kalshi_enriched": str(kalshi_enriched_path),
        },
        generated_at=generated_at,
        settlement_tolerance_seconds=settlement_tolerance_seconds,
        max_quote_age_seconds=max_quote_age_seconds,
    )
    if json_output_path is not None:
        json_output_path.parent.mkdir(parents=True, exist_ok=True)
        json_output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    if markdown_output_path is not None:
        markdown_output_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_output_path.write_text(render_same_payoff_board_markdown(payload), encoding="utf-8")
    return payload


def diagnose_mlb_world_series_board_blockers_files(
    *,
    board_path: Path,
    pairs_path: Path,
    json_output_path: Path,
    markdown_output_path: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    generated_at = now or datetime.now(timezone.utc)
    _require_tz_aware(generated_at, "now")
    board_payload = _load_json_object(board_path, "same_payoff_board")
    pairs_payload = _load_json_object(pairs_path, "pairs")
    payload = diagnose_mlb_world_series_board_blockers(
        board_payload=board_payload,
        pairs_payload=pairs_payload,
        generated_at=generated_at,
        inputs={"board": str(board_path), "pairs": str(pairs_path)},
    )
    json_output_path.parent.mkdir(parents=True, exist_ok=True)
    json_output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_output_path.write_text(render_mlb_world_series_board_blockers_markdown(payload), encoding="utf-8")
    return payload


def diagnose_mlb_world_series_board_blockers(
    *,
    board_payload: dict[str, Any],
    pairs_payload: dict[str, Any],
    generated_at: datetime | None = None,
    inputs: dict[str, str] | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    rows = [row for row in board_payload.get("rows") or [] if isinstance(row, dict)]
    pairs = [pair for pair in pairs_payload.get("pairs") or [] if isinstance(pair, dict)]
    blocker_counts = Counter(blocker for row in rows for blocker in row.get("blockers", []))
    missing_counts = Counter(missing for row in rows for missing in row.get("missing_fields", []))
    strict_pass_distribution = Counter(str(row.get("strict_pass_count")) for row in rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "mlb_world_series_board_blocker_diagnostics_v1",
        "generated_at": generated.isoformat(),
        "inputs": inputs or {"board": "<in-memory>", "pairs": "<in-memory>"},
        "row_count": len(rows),
        "pair_count": len(pairs),
        "strict_pass_distribution": dict(sorted(strict_pass_distribution.items())),
        "blockers": [{"blocker": blocker, "count": count} for blocker, count in blocker_counts.most_common()],
        "missing_fields": [{"field": field, "count": count} for field, count in missing_counts.most_common()],
        "semantic_normalization_notes": _semantic_normalization_notes(rows),
        "safety": {
            "saved_file_only": True,
            "live_fetch_attempted": False,
            "execution_or_trading_logic_added": False,
            "thresholds_or_relationship_gates_lowered": False,
        },
        "disclaimer": BOARD_DISCLAIMER,
    }


def render_mlb_world_series_board_blockers_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# MLB World Series Board Blockers",
        "",
        "Saved-file diagnostic only.",
        "",
        f"Rows: {payload.get('row_count', 0)}",
        f"Pairs: {payload.get('pair_count', 0)}",
        "",
        "## Blockers",
        "",
        "| Blocker | Count |",
        "| --- | ---: |",
    ]
    blockers = payload.get("blockers") or []
    if not blockers:
        lines.append("| none | 0 |")
    for row in blockers:
        lines.append(f"| {_md(row.get('blocker'))} | {_md(row.get('count'))} |")
    lines.extend(["", "## Missing Fields", "", "| Field | Count |", "| --- | ---: |"])
    missing = payload.get("missing_fields") or []
    if not missing:
        lines.append("| none | 0 |")
    for row in missing:
        lines.append(f"| {_md(row.get('field'))} | {_md(row.get('count'))} |")
    lines.append("")
    return "\n".join(lines)


def build_same_payoff_board(
    *,
    pairs_payload: dict[str, Any],
    polymarket_payload: dict[str, Any],
    kalshi_payload: dict[str, Any],
    inputs: dict[str, str] | None = None,
    generated_at: datetime | None = None,
    settlement_tolerance_seconds: float = DEFAULT_SETTLEMENT_TOLERANCE_SECONDS,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    _validate_schema_one("pairs", pairs_payload)
    _validate_schema_one("polymarket_enriched", polymarket_payload)
    _validate_schema_one("kalshi_enriched", kalshi_payload)

    pairs = pairs_payload.get("pairs")
    if not isinstance(pairs, list):
        raise ValueError("pairs input must contain a pairs list")
    polymarket_by_id = {
        _string_or_empty(row.get("market_id")): row
        for row in _market_rows(polymarket_payload, "polymarket_enriched")
        if _string_or_empty(row.get("market_id"))
    }
    kalshi_by_ticker = {
        _string_or_empty(row.get("ticker") or row.get("market_id")): row
        for row in _market_rows(kalshi_payload, "kalshi_enriched")
        if _string_or_empty(row.get("ticker") or row.get("market_id"))
    }

    rows = [
        _board_row(
            pair,
            polymarket_by_id.get(_pair_polymarket_id(pair)),
            kalshi_by_ticker.get(_pair_kalshi_ticker(pair)),
            generated,
            settlement_tolerance_seconds,
            max_quote_age_seconds,
        )
        for pair in pairs
        if isinstance(pair, dict)
    ]
    rows.sort(key=_row_sort_key)
    blockers = Counter(blocker for row in rows for blocker in row["blockers"])
    recommendations = Counter(row["recommended_next_action"] for row in rows)
    strict_pass_count = sum(1 for row in rows if row["same_payoff"] is True)
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "same_payoff_candidate_board",
        "generated_at": generated.isoformat(),
        "inputs": inputs or {
            "pairs": "<in-memory>",
            "polymarket_enriched": "<in-memory>",
            "kalshi_enriched": "<in-memory>",
        },
        "parameters": {
            "settlement_tolerance_seconds": settlement_tolerance_seconds,
            "max_quote_age_seconds": max_quote_age_seconds,
        },
        "row_count": len(rows),
        "strict_same_payoff_pass_count": strict_pass_count,
        "counts_by_recommended_next_action": {key: recommendations.get(key, 0) for key in sorted(ALLOWED_RECOMMENDATIONS)},
        "top_blockers": [{"blocker": key, "count": count} for key, count in blockers.most_common(10)],
        "rows": rows,
        "disclaimer": BOARD_DISCLAIMER,
    }


def render_same_payoff_board_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Same-Payoff Candidate Board",
        "",
        "Saved-file deterministic diagnostics for Kalshi x Polymarket pairs.",
        "",
        f"Rows: {payload.get('row_count', 0)}. Strict same-payoff passes: {payload.get('strict_same_payoff_pass_count', 0)}.",
        "",
        "| Recommendation | Same payoff | Polymarket | Kalshi | Similarity | Passes | Blockers | Missing fields |",
        "| --- | --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    for row in payload.get("rows") or []:
        if not isinstance(row, dict):
            continue
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("recommended_next_action")),
                    _md(str(row.get("same_payoff")).lower()),
                    _md(_venue_label(row.get("polymarket"), "market_id")),
                    _md(_venue_label(row.get("kalshi"), "ticker")),
                    _md(row.get("similarity_score")),
                    _md(row.get("strict_pass_count")),
                    _md(", ".join(row.get("blockers") or []) or "none"),
                    _md(", ".join(row.get("missing_fields") or []) or "none"),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "This board is diagnostic only and does not grant execution permission.",
            "",
        ]
    )
    return "\n".join(lines)


def _board_row(
    pair: dict[str, Any],
    polymarket: dict[str, Any] | None,
    kalshi: dict[str, Any] | None,
    generated_at: datetime,
    settlement_tolerance_seconds: float,
    max_quote_age_seconds: float,
) -> dict[str, Any]:
    evidence: dict[str, dict[str, Any]] = {}
    strict_blockers: list[str] = []
    strict_missing_fields: list[str] = []
    info_blockers: list[str] = []
    info_missing_fields: list[str] = []
    if polymarket is None:
        strict_blockers.append("missing_polymarket_enriched_market")
        strict_missing_fields.append("polymarket_enriched_market")
        polymarket = {}
    if kalshi is None:
        strict_blockers.append("missing_kalshi_enriched_market")
        strict_missing_fields.append("kalshi_enriched_market")
        kalshi = {}

    comparators = [
        _reference_only_comparator(polymarket, kalshi),
        _settlement_source_comparator(polymarket, kalshi),
        _settlement_time_comparator(polymarket, kalshi, settlement_tolerance_seconds),
        _entity_comparator(pair, polymarket, kalshi),
        _sports_comparator(polymarket, kalshi),
        _threshold_comparator(polymarket, kalshi),
        _market_type_comparator(polymarket, kalshi),
        _polarity_comparator(polymarket, kalshi),
        _side_definition_comparator(polymarket, kalshi),
        _relationship_shape_comparator(polymarket, kalshi),
        _unit_comparator(polymarket, kalshi),
    ]
    info_comparators = [
        _quote_comparator("polymarket", polymarket, generated_at, max_quote_age_seconds),
        _quote_comparator("kalshi", kalshi, generated_at, max_quote_age_seconds),
        _fee_comparator(polymarket, kalshi),
    ]
    for comparator in comparators:
        evidence[comparator["name"]] = comparator
        strict_blockers.extend(comparator.get("blockers", []))
        strict_missing_fields.extend(comparator.get("missing_fields", []))
    for comparator in info_comparators:
        evidence[comparator["name"]] = comparator
        info_blockers.extend(comparator.get("blockers", []))
        info_missing_fields.extend(comparator.get("missing_fields", []))

    strict_comparators = comparators
    strict_pass_count = sum(1 for comparator in strict_comparators if comparator["status"] == "PASS")
    strict_same_payoff = strict_pass_count == len(strict_comparators)
    strict_blockers = sorted(set(strict_blockers))
    strict_missing_fields = sorted(set(strict_missing_fields))
    info_blockers = sorted(set(info_blockers))
    info_missing_fields = sorted(set(info_missing_fields))
    blockers = sorted(set(strict_blockers + info_blockers))
    missing_fields = sorted(set(strict_missing_fields + info_missing_fields))
    row = {
        "source_ids": {
            "polymarket": _source_id(polymarket, "polymarket"),
            "kalshi": _source_id(kalshi, "kalshi"),
        },
        "polymarket": {
            "market_id": _string_or_none(polymarket.get("market_id")) or _pair_polymarket_id(pair),
            "question": _market_question(pair.get("polymarket"), polymarket),
            "event_title": polymarket.get("event_title") or (pair.get("polymarket") or {}).get("event_title"),
        },
        "kalshi": {
            "ticker": _string_or_none(kalshi.get("ticker") or kalshi.get("market_id")) or _pair_kalshi_ticker(pair),
            "question": _market_question(pair.get("kalshi"), kalshi),
            "event_title": kalshi.get("event_title") or (pair.get("kalshi") or {}).get("event_title"),
        },
        "similarity_score": pair.get("similarity_score"),
        "existing_contract_relationship": pair.get("contract_relationship") if isinstance(pair.get("contract_relationship"), dict) else None,
        "same_payoff_evidence": evidence,
        "same_payoff": bool(strict_same_payoff),
        "strict_pass_count": strict_pass_count,
        "strict_comparator_count": len(strict_comparators),
        "strict_blockers": strict_blockers,
        "strict_missing_fields": strict_missing_fields,
        "info_blockers": info_blockers,
        "info_missing_fields": info_missing_fields,
        "blockers": blockers,
        "missing_fields": missing_fields,
        "recommended_next_action": _recommended_next_action(strict_same_payoff, blockers, missing_fields),
    }
    return row


def _reference_only_comparator(polymarket: dict[str, Any], kalshi: dict[str, Any]) -> dict[str, Any]:
    blockers: list[str] = []
    for venue, market in (("polymarket", polymarket), ("kalshi", kalshi)):
        source_type = _source_type(market)
        venue_value = str(market.get("venue") or "").strip().lower()
        if source_type == "REFERENCE_ONLY" or venue_value not in {venue, ""}:
            blockers.append(f"{venue}_not_executable_kalshi_polymarket_leg")
    return _evidence("reference_only_blocker", "PASS" if not blockers else "FAIL", blockers=blockers)


def _settlement_source_comparator(polymarket: dict[str, Any], kalshi: dict[str, Any]) -> dict[str, Any]:
    poly = _settlement_source_text(polymarket)
    kal = _settlement_source_text(kalshi)
    missing = []
    if not poly:
        missing.append("polymarket_settlement_source_or_rule")
    if not kal:
        missing.append("kalshi_settlement_source_or_rule")
    if missing:
        if same_mlb_world_series_team(polymarket, kalshi):
            present = poly or kal
            if len(missing) == 1 and _explicit_mlb_world_series_source_text(present):
                return _evidence(
                    "settlement_source",
                    "PASS",
                    values={
                        "normalization": "mlb_world_series_named_primary_source_one_sided",
                        "missing_side": "polymarket" if not poly else "kalshi",
                        "polymarket": poly,
                        "kalshi": kal,
                    },
                )
        if same_nba_championship_team(polymarket, kalshi):
            present = poly or kal
            if len(missing) == 1 and _explicit_nba_championship_source_text(present):
                return _evidence(
                    "settlement_source",
                    "PASS",
                    values={
                        "normalization": "nba_championship_named_primary_source_one_sided",
                        "missing_side": "polymarket" if not poly else "kalshi",
                        "polymarket": poly,
                        "kalshi": kal,
                    },
                )
        if same_nhl_stanley_cup_team(polymarket, kalshi):
            return _evidence("settlement_source", "MISSING", missing_fields=missing, values={"polymarket": poly, "kalshi": kal})
        return _evidence("settlement_source", "MISSING", missing_fields=missing, values={"polymarket": poly, "kalshi": kal})
    poly_base = _normalize_without_tiebreak(poly)
    kal_base = _normalize_without_tiebreak(kal)
    if poly_base != kal_base:
        if same_nba_championship_team(polymarket, kalshi) and _explicit_nba_championship_source_text(poly) and _explicit_nba_championship_source_text(kal):
            return _evidence(
                "settlement_source",
                "PASS",
                values={
                    "normalization": "nba_championship_equivalent_resolution_wording",
                    "polymarket": poly,
                    "kalshi": kal,
                },
            )
        if same_nhl_stanley_cup_team(polymarket, kalshi) and _explicit_nhl_stanley_cup_source_text(poly) and _explicit_nhl_stanley_cup_source_text(kal):
            return _evidence(
                "settlement_source",
                "PASS",
                values={
                    "normalization": "nhl_stanley_cup_equivalent_resolution_wording",
                    "polymarket": poly,
                    "kalshi": kal,
                },
            )
        return _evidence(
            "settlement_source",
            "FAIL",
            blockers=["settlement_source_mismatch"],
            values={"polymarket": poly, "kalshi": kal},
        )
    if _tiebreak_tokens(poly) != _tiebreak_tokens(kal):
        return _evidence(
            "settlement_source",
            "FAIL",
            blockers=["settlement_rule_tiebreak_mismatch"],
            values={"polymarket": poly, "kalshi": kal},
        )
    return _evidence("settlement_source", "PASS", values={"normalized": poly_base})


def _settlement_time_comparator(polymarket: dict[str, Any], kalshi: dict[str, Any], tolerance_seconds: float) -> dict[str, Any]:
    poly_value = _time_value(polymarket)
    kal_value = _time_value(kalshi)
    poly_dt = _parse_datetime_or_none(poly_value)
    kal_dt = _parse_datetime_or_none(kal_value)
    missing = []
    if poly_dt is None:
        missing.append("polymarket_end_date_or_close_time")
    if kal_dt is None:
        missing.append("kalshi_end_date_or_close_time")
    if missing:
        return _evidence("settlement_time", "MISSING", missing_fields=missing, values={"polymarket": poly_value, "kalshi": kal_value})
    delta = abs((poly_dt - kal_dt).total_seconds())
    if delta > tolerance_seconds:
        if same_mlb_world_series_team(polymarket, kalshi) and _mlb_world_series_timezone_convention_match(poly_dt, kal_dt):
            return _evidence(
                "settlement_time",
                "PASS",
                values={
                    "normalization": "mlb_world_series_timezone_convention_drift",
                    "delta_seconds": delta,
                    "tolerance_seconds": tolerance_seconds,
                    "polymarket": _time_diagnostics(polymarket),
                    "kalshi": _time_diagnostics(kalshi),
                },
            )
        if same_nba_championship_team(polymarket, kalshi) and _nba_finals_same_local_date_convention_match(poly_dt, kal_dt):
            return _evidence(
                "settlement_time",
                "PASS",
                values={
                    "normalization": "nba_finals_timezone_convention_drift",
                    "delta_seconds": delta,
                    "tolerance_seconds": tolerance_seconds,
                    "polymarket": _time_diagnostics(polymarket),
                    "kalshi": _time_diagnostics(kalshi),
                },
            )
        return _evidence(
            "settlement_time",
            "FAIL",
            blockers=["settlement_date_drift"],
            values={
                "delta_seconds": delta,
                "tolerance_seconds": tolerance_seconds,
                "polymarket": _time_diagnostics(polymarket),
                "kalshi": _time_diagnostics(kalshi),
            },
        )
    return _evidence(
        "settlement_time",
        "PASS",
        values={
            "delta_seconds": delta,
            "tolerance_seconds": tolerance_seconds,
            "polymarket": _time_diagnostics(polymarket),
            "kalshi": _time_diagnostics(kalshi),
        },
    )


def _entity_comparator(pair: dict[str, Any], polymarket: dict[str, Any], kalshi: dict[str, Any]) -> dict[str, Any]:
    poly_market = _merged_pair_market(pair.get("polymarket"), polymarket)
    kal_market = _merged_pair_market(pair.get("kalshi"), kalshi)
    if same_mlb_world_series_team(poly_market, kal_market):
        return _evidence(
            "market_event_entity",
            "PASS",
            values={
                "normalization": "mlb_world_series_team",
                "polymarket": mlb_world_series_profile(poly_market),
                "kalshi": mlb_world_series_profile(kal_market),
            },
        )
    if same_nba_championship_team(poly_market, kal_market):
        return _evidence(
            "market_event_entity",
            "PASS",
            values={
                "normalization": "nba_championship_team",
                "polymarket": nba_championship_profile(poly_market),
                "kalshi": nba_championship_profile(kal_market),
            },
        )
    if same_nhl_stanley_cup_team(poly_market, kal_market):
        return _evidence(
            "market_event_entity",
            "PASS",
            values={
                "normalization": "nhl_stanley_cup_team",
                "polymarket": nhl_stanley_cup_profile(poly_market),
                "kalshi": nhl_stanley_cup_profile(kal_market),
            },
        )
    poly = " ".join(str(value or "") for value in [_market_question(pair.get("polymarket"), polymarket), polymarket.get("event_title")])
    kal = " ".join(str(value or "") for value in [_market_question(pair.get("kalshi"), kalshi), kalshi.get("event_title")])
    poly_tokens = _entity_tokens(poly)
    kal_tokens = _entity_tokens(kal)
    if not poly_tokens or not kal_tokens:
        return _evidence("market_event_entity", "MISSING", missing_fields=["market_event_entity_tokens"])
    if poly_tokens != kal_tokens:
        return _evidence(
            "market_event_entity",
            "FAIL",
            blockers=["market_event_entity_mismatch"],
            values={"polymarket": sorted(poly_tokens), "kalshi": sorted(kal_tokens)},
        )
    return _evidence("market_event_entity", "PASS", values={"tokens": sorted(poly_tokens)})


def _sports_comparator(polymarket: dict[str, Any], kalshi: dict[str, Any]) -> dict[str, Any]:
    if same_mlb_world_series_team(polymarket, kalshi):
        return _evidence(
            "sports_league_team",
            "PASS",
            values={
                "normalization": "mlb_world_series_team_scope",
                "polymarket": mlb_world_series_profile(polymarket),
                "kalshi": mlb_world_series_profile(kalshi),
            },
        )
    if same_nba_championship_team(polymarket, kalshi):
        return _evidence(
            "sports_league_team",
            "PASS",
            values={
                "normalization": "nba_championship_team_scope",
                "polymarket": nba_championship_profile(polymarket),
                "kalshi": nba_championship_profile(kalshi),
            },
        )
    if same_nhl_stanley_cup_team(polymarket, kalshi):
        return _evidence(
            "sports_league_team",
            "PASS",
            values={
                "normalization": "nhl_stanley_cup_team_scope",
                "polymarket": nhl_stanley_cup_profile(polymarket),
                "kalshi": nhl_stanley_cup_profile(kalshi),
            },
        )
    poly = _sports_profile(polymarket)
    kal = _sports_profile(kalshi)
    if not poly["is_sports"] and not kal["is_sports"]:
        return _evidence("sports_league_team", "PASS", values={"sports_detected": False})
    if poly["league"] != kal["league"] or poly["teams"] != kal["teams"]:
        return _evidence(
            "sports_league_team",
            "FAIL",
            blockers=["sports_league_team_mismatch"],
            values={"polymarket": poly, "kalshi": kal},
        )
    return _evidence("sports_league_team", "PASS", values={"polymarket": poly, "kalshi": kal})


def _threshold_comparator(polymarket: dict[str, Any], kalshi: dict[str, Any]) -> dict[str, Any]:
    if same_mlb_world_series_team(polymarket, kalshi) and _is_non_threshold_championship_outright(polymarket, kalshi):
        return _evidence(
            "threshold_strike",
            "PASS",
            values={
                "normalization": "mlb_world_series_outright_no_threshold",
                "polymarket": [],
                "kalshi": [],
            },
        )
    if same_nba_championship_team(polymarket, kalshi) and _is_non_threshold_championship_outright(polymarket, kalshi):
        return _evidence(
            "threshold_strike",
            "PASS",
            values={
                "normalization": "nba_championship_outright_no_threshold",
                "polymarket": [],
                "kalshi": [],
            },
        )
    if same_nhl_stanley_cup_team(polymarket, kalshi) and _is_non_threshold_championship_outright(polymarket, kalshi):
        return _evidence(
            "threshold_strike",
            "PASS",
            values={
                "normalization": "nhl_stanley_cup_outright_no_threshold",
                "polymarket": [],
                "kalshi": [],
            },
        )
    poly = _numeric_tokens(_comparison_text(polymarket))
    kal = _numeric_tokens(_comparison_text(kalshi))
    if poly != kal:
        return _evidence("threshold_strike", "FAIL", blockers=["threshold_strike_mismatch"], values={"polymarket": poly, "kalshi": kal})
    return _evidence("threshold_strike", "PASS", values={"numbers": poly})


def _market_type_comparator(polymarket: dict[str, Any], kalshi: dict[str, Any]) -> dict[str, Any]:
    poly = _market_type(polymarket)
    kal = _market_type(kalshi)
    if not poly or not kal:
        return _evidence("market_type", "MISSING", missing_fields=["market_type"], values={"polymarket": poly, "kalshi": kal})
    if same_mlb_world_series_team(polymarket, kalshi) and _compatible_binary_market_types(poly, kal):
        return _evidence(
            "market_type",
            "PASS",
            values={"market_type": "binary_event", "polymarket": poly, "kalshi": kal, "normalization": "mlb_world_series_binary"},
        )
    if same_nba_championship_team(polymarket, kalshi) and _compatible_binary_market_types(poly, kal):
        return _evidence(
            "market_type",
            "PASS",
            values={"market_type": "binary_event", "polymarket": poly, "kalshi": kal, "normalization": "nba_championship_binary"},
        )
    if same_nhl_stanley_cup_team(polymarket, kalshi) and _compatible_binary_market_types(poly, kal):
        return _evidence(
            "market_type",
            "PASS",
            values={"market_type": "binary_event", "polymarket": poly, "kalshi": kal, "normalization": "nhl_stanley_cup_binary"},
        )
    if poly != kal:
        return _evidence("market_type", "FAIL", blockers=["market_type_mismatch"], values={"polymarket": poly, "kalshi": kal})
    return _evidence("market_type", "PASS", values={"market_type": poly})


def _polarity_comparator(polymarket: dict[str, Any], kalshi: dict[str, Any]) -> dict[str, Any]:
    poly = _polarity(_comparison_text(polymarket))
    kal = _polarity(_comparison_text(kalshi))
    if poly == "ambiguous" or kal == "ambiguous":
        return _evidence("outcome_direction_polarity", "FAIL", blockers=["outcome_direction_ambiguous"], values={"polymarket": poly, "kalshi": kal})
    if poly != kal:
        return _evidence("outcome_direction_polarity", "FAIL", blockers=["outcome_direction_polarity_mismatch"], values={"polymarket": poly, "kalshi": kal})
    return _evidence("outcome_direction_polarity", "PASS", values={"polarity": poly})


def _side_definition_comparator(polymarket: dict[str, Any], kalshi: dict[str, Any]) -> dict[str, Any]:
    poly_outcomes = _outcome_names(polymarket)
    kal_outcomes = _outcome_names(kalshi)
    if _has_no_side_spread_text(polymarket) or _has_no_side_spread_text(kalshi):
        return _evidence(
            "side_definition_tokens",
            "FAIL",
            blockers=["no_side_spread_or_side_definition_ambiguous"],
            values={"polymarket": poly_outcomes, "kalshi": kal_outcomes},
        )
    if not _binary_yes_no(poly_outcomes) or not _binary_yes_no(kal_outcomes):
        return _evidence(
            "side_definition_tokens",
            "FAIL",
            blockers=["side_definition_tokens_ambiguous"],
            values={"polymarket": poly_outcomes, "kalshi": kal_outcomes},
        )
    return _evidence("side_definition_tokens", "PASS", values={"polymarket": poly_outcomes, "kalshi": kal_outcomes})


def _unit_comparator(polymarket: dict[str, Any], kalshi: dict[str, Any]) -> dict[str, Any]:
    poly_units = _unit_tokens(polymarket)
    kal_units = _unit_tokens(kalshi)
    explicit_poly = bool(poly_units)
    explicit_kal = bool(kal_units)
    if explicit_poly and explicit_kal and poly_units != kal_units:
        return _evidence("unit_liquidity", "FAIL", blockers=["unit_or_liquidity_unit_mismatch"], values={"polymarket": sorted(poly_units), "kalshi": sorted(kal_units)})
    return _evidence("unit_liquidity", "PASS", values={"polymarket": sorted(poly_units), "kalshi": sorted(kal_units)})


def _relationship_shape_comparator(polymarket: dict[str, Any], kalshi: dict[str, Any]) -> dict[str, Any]:
    poly_text = _comparison_text(polymarket)
    kalshi_text = _comparison_text(kalshi)
    poly_tokens = set(_TOKEN_RE.findall(poly_text.lower()))
    kalshi_tokens = set(_TOKEN_RE.findall(kalshi_text.lower()))
    combined = poly_tokens | kalshi_tokens
    poly_numbers = _number_values(poly_text)
    kalshi_numbers = _number_values(kalshi_text)

    if {"browns", "guardians"} <= combined:
        return _evidence(
            "relationship_shape",
            "FAIL",
            blockers=["relationship_shape_unrelated"],
            values={"relationship": "UNRELATED", "reason": "different Cleveland teams"},
        )
    if _world_series_vs_league_title(poly_text, kalshi_text):
        return _evidence(
            "relationship_shape",
            "FAIL",
            blockers=["relationship_shape_subset_or_superset"],
            values={"relationship": "SUBSET_OR_SUPERSET", "reason": "World Series and league/conference title are nested but not equivalent"},
        )
    if _nba_championship_vs_conference_title(poly_text, kalshi_text):
        return _evidence(
            "relationship_shape",
            "FAIL",
            blockers=["relationship_shape_subset_or_superset"],
            values={"relationship": "SUBSET_OR_SUPERSET", "reason": "NBA championship and conference title are nested but not equivalent"},
        )
    if _nhl_stanley_cup_vs_conference_or_division_title(poly_text, kalshi_text):
        return _evidence(
            "relationship_shape",
            "FAIL",
            blockers=["relationship_shape_subset_or_superset"],
            values={"relationship": "SUBSET_OR_SUPERSET", "reason": "NHL Stanley Cup and conference/division title are nested but not equivalent"},
        )
    if _btc_threshold_pair(poly_tokens, kalshi_tokens) and poly_numbers and kalshi_numbers and max(poly_numbers) != max(kalshi_numbers):
        return _evidence(
            "relationship_shape",
            "FAIL",
            blockers=["relationship_shape_subset_or_superset"],
            values={"relationship": "SUBSET_OR_SUPERSET", "reason": "higher threshold implies lower threshold but payoffs are not equivalent"},
        )
    if "openai" in combined and "anthropic" in combined and "ipo" in combined:
        return _evidence(
            "relationship_shape",
            "FAIL",
            blockers=["relationship_shape_overlap_not_equivalent"],
            values={"relationship": "OVERLAP_NOT_EQUIVALENT", "reason": "IPO timing and relative IPO ordering can overlap without identical payoff"},
        )
    return _evidence("relationship_shape", "PASS", values={"relationship": "NO_STRUCTURAL_RELATIONSHIP_BLOCKER"})


def _quote_comparator(venue: str, market: dict[str, Any], generated_at: datetime, max_quote_age_seconds: float) -> dict[str, Any]:
    enrichment = _enrichment(market)
    blockers: list[str] = []
    missing: list[str] = []
    if enrichment.get("enrichment_status") != "enriched":
        blockers.append(f"{venue}_orderbook_not_enriched")
    for field in ("best_bid", "best_ask", "depth_at_best_bid", "depth_at_best_ask"):
        if enrichment.get(field) is None:
            missing.append(f"{venue}_{field}")
    captured_at = _parse_datetime_or_none(enrichment.get("orderbook_captured_at"))
    age_seconds = None
    if captured_at is None:
        missing.append(f"{venue}_orderbook_captured_at")
    else:
        age_seconds = (generated_at - captured_at).total_seconds()
        if age_seconds < 0 or age_seconds > max_quote_age_seconds:
            blockers.append(f"{venue}_stale_quote")
    status = "PASS" if not blockers and not missing else "INFO_BLOCKED"
    return _evidence(
        f"{venue}_quote_depth",
        status,
        blockers=blockers,
        missing_fields=missing,
        values={"age_seconds": age_seconds, "max_quote_age_seconds": max_quote_age_seconds},
        strict=False,
    )


def _fee_comparator(polymarket: dict[str, Any], kalshi: dict[str, Any]) -> dict[str, Any]:
    missing = []
    if not _fee_available(polymarket):
        missing.append("polymarket_fee_model_or_rate")
    if not _fee_available(kalshi):
        missing.append("kalshi_fee_model_or_rate")
    return _evidence("fee_availability", "PASS" if not missing else "INFO_BLOCKED", missing_fields=missing, strict=False)


def _recommended_next_action(strict_same_payoff: bool, blockers: list[str], missing_fields: list[str]) -> str:
    hard_skip_prefixes = (
        "reference",
        "settlement_source_mismatch",
        "settlement_rule_tiebreak_mismatch",
        "settlement_date_drift",
        "threshold_strike_mismatch",
        "outcome_direction_polarity_mismatch",
        "outcome_direction_ambiguous",
        "no_side_spread_or_side_definition_ambiguous",
        "side_definition_tokens_ambiguous",
        "unit_or_liquidity_unit_mismatch",
    )
    if any(blocker.startswith(hard_skip_prefixes) or "not_executable" in blocker for blocker in blockers):
        return "SKIP"
    if "market_event_entity_mismatch" in blockers or "sports_league_team_mismatch" in blockers:
        return "BETTER_SOURCE_TARGETING"
    if strict_same_payoff and any(blocker.endswith("_orderbook_not_enriched") or blocker.endswith("_stale_quote") for blocker in blockers):
        return "ENRICH_IF_APPROVED"
    if strict_same_payoff:
        return "RELATIONSHIP_REVIEW"
    if missing_fields:
        return "WATCH_ONLY"
    return "RELATIONSHIP_REVIEW"


def _semantic_normalization_notes(rows: list[dict[str, Any]]) -> dict[str, int]:
    notes: Counter[str] = Counter()
    for row in rows:
        evidence = row.get("same_payoff_evidence") if isinstance(row.get("same_payoff_evidence"), dict) else {}
        for comparator in evidence.values():
            if not isinstance(comparator, dict):
                continue
            values = comparator.get("values") if isinstance(comparator.get("values"), dict) else {}
            normalization = values.get("normalization")
            if normalization:
                notes[str(normalization)] += 1
    return dict(sorted(notes.items()))


def _evidence(
    name: str,
    status: str,
    *,
    blockers: list[str] | None = None,
    missing_fields: list[str] | None = None,
    values: dict[str, Any] | None = None,
    strict: bool = True,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "strict": strict,
        "blockers": sorted(set(blockers or [])),
        "missing_fields": sorted(set(missing_fields or [])),
        "values": values or {},
    }


def _row_sort_key(row: dict[str, Any]) -> tuple[int, int, str, str]:
    recommendation_rank = {
        "RELATIONSHIP_REVIEW": 0,
        "ENRICH_IF_APPROVED": 1,
        "WATCH_ONLY": 2,
        "BETTER_SOURCE_TARGETING": 3,
        "SKIP": 4,
    }
    return (
        recommendation_rank.get(str(row.get("recommended_next_action")), 9),
        -int(row.get("strict_pass_count") or 0),
        str((row.get("polymarket") or {}).get("market_id") or ""),
        str((row.get("kalshi") or {}).get("ticker") or ""),
    )


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"{label} file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} JSON is invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} JSON must be an object")
    return payload


def _validate_schema_one(label: str, payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{label} schema_version must be 1")


def _market_rows(payload: dict[str, Any], label: str) -> list[dict[str, Any]]:
    rows = payload.get("normalized_markets")
    if not isinstance(rows, list):
        raise ValueError(f"{label} input must contain normalized_markets list")
    return [row for row in rows if isinstance(row, dict)]


def _pair_polymarket_id(pair: dict[str, Any]) -> str:
    return _string_or_empty((pair.get("polymarket") or {}).get("market_id"))


def _pair_kalshi_ticker(pair: dict[str, Any]) -> str:
    kalshi = pair.get("kalshi") or {}
    return _string_or_empty(kalshi.get("ticker") or kalshi.get("market_id"))


def _market_question(pair_side: Any, enriched: dict[str, Any]) -> str:
    pair_side = pair_side if isinstance(pair_side, dict) else {}
    return str(enriched.get("question") or enriched.get("title") or pair_side.get("question") or pair_side.get("title") or "")


def _source_id(market: dict[str, Any], fallback: str) -> str:
    return str(market.get("source_id") or market.get("venue") or fallback)


def _source_type(market: dict[str, Any]) -> str:
    return str(market.get("source_type") or market.get("permission") or "").strip().upper()


def _settlement_source_text(market: dict[str, Any]) -> str:
    raw = market.get("raw") if isinstance(market.get("raw"), dict) else {}
    values = [
        market.get("settlement_source"),
        market.get("settlement_rule"),
        market.get("resolution_source"),
        market.get("rules_primary"),
        raw.get("settlement_source"),
        raw.get("settlementRule"),
        raw.get("settlement_rule"),
        raw.get("resolutionSource"),
        raw.get("rules_primary"),
        raw.get("rulesPrimary"),
        raw.get("rules"),
        raw.get("description"),
    ]
    return " ".join(str(value) for value in values if value)


def _time_value(market: dict[str, Any]) -> Any:
    return market.get("end_date") or market.get("close_time") or market.get("settlement_time")


def _time_diagnostics(market: dict[str, Any]) -> dict[str, Any]:
    raw = market.get("raw") if isinstance(market.get("raw"), dict) else {}
    return {
        "selected_value": _time_value(market),
        "end_date": market.get("end_date"),
        "close_time": market.get("close_time"),
        "settlement_time": market.get("settlement_time"),
        "raw_close_time": raw.get("close_time") or raw.get("closeTime"),
        "raw_expiration_time": raw.get("expiration_time") or raw.get("expirationTime"),
    }


def _mlb_world_series_timezone_convention_match(left: datetime, right: datetime) -> bool:
    earlier, later = sorted((left, right))
    convention_shift_seconds = 4 * 3600
    close_cutoff_slack_seconds = 5 * 60
    adjusted_delta = abs((later - earlier).total_seconds() - convention_shift_seconds)
    if adjusted_delta > close_cutoff_slack_seconds:
        return False
    shifted_to_utc_midnight = later.timestamp() - convention_shift_seconds
    shifted_dt = datetime.fromtimestamp(shifted_to_utc_midnight, tz=timezone.utc)
    return abs((shifted_dt - earlier).total_seconds()) <= close_cutoff_slack_seconds


def _nba_finals_same_local_date_convention_match(left: datetime, right: datetime) -> bool:
    earlier, later = sorted((left, right))
    convention_shift_seconds = 4 * 3600
    close_cutoff_slack_seconds = 5 * 60
    adjusted_delta = abs((later - earlier).total_seconds() - convention_shift_seconds)
    if adjusted_delta > close_cutoff_slack_seconds:
        return False
    shifted_to_utc_midnight = later.timestamp() - convention_shift_seconds
    shifted_dt = datetime.fromtimestamp(shifted_to_utc_midnight, tz=timezone.utc)
    return abs((shifted_dt - earlier).total_seconds()) <= close_cutoff_slack_seconds


def _comparison_text(market: dict[str, Any]) -> str:
    raw = market.get("raw") if isinstance(market.get("raw"), dict) else {}
    return " ".join(
        str(value or "")
        for value in (
            market.get("question"),
            market.get("title"),
            market.get("event_title"),
            market.get("market_type"),
            raw.get("event_slug"),
            raw.get("series_ticker"),
            raw.get("event_ticker"),
            raw.get("market_type"),
        )
    )


def _merged_pair_market(pair_side: Any, enriched: dict[str, Any]) -> dict[str, Any]:
    pair_side = pair_side if isinstance(pair_side, dict) else {}
    merged = dict(enriched)
    for key in ("question", "title", "event_title", "market_id", "ticker"):
        if not merged.get(key) and pair_side.get(key):
            merged[key] = pair_side.get(key)
    return merged


def _entity_tokens(text: str) -> set[str]:
    stop = {
        "a",
        "an",
        "and",
        "be",
        "by",
        "for",
        "in",
        "market",
        "no",
        "of",
        "on",
        "or",
        "the",
        "to",
        "will",
        "yes",
        "win",
        "happen",
        "happens",
        "this",
    }
    return {token for token in _TOKEN_RE.findall(text.lower()) if token not in stop and not _NUMBER_RE.fullmatch(token)}


def _sports_profile(market: dict[str, Any]) -> dict[str, Any]:
    text = _comparison_text(market).lower()
    tokens = set(_TOKEN_RE.findall(text))
    league = next((league.upper() for league in sorted(_SPORT_LEAGUES) if league in tokens or f"kx{league}" in text), None)
    teams = sorted(token for token in tokens if token not in _SPORT_LEAGUES and token not in {"will", "win", "game", "match", "yes", "no"})
    return {"is_sports": league is not None, "league": league, "teams": teams if league else []}


def _numeric_tokens(text: str) -> list[str]:
    return sorted(_NUMBER_RE.findall(text.lower()))


def _threshold_numbers(text: str) -> list[str]:
    tokens = set(_TOKEN_RE.findall(text.lower()))
    if not (tokens & (_OVER_TOKENS | _UNDER_TOKENS | {"line", "spread", "threshold", "total"})):
        return []
    return _numeric_tokens(text)


def _is_non_threshold_championship_outright(polymarket: dict[str, Any], kalshi: dict[str, Any]) -> bool:
    return not _threshold_numbers(_comparison_text(polymarket)) and not _threshold_numbers(_comparison_text(kalshi))


def _compatible_binary_market_types(left: str, right: str) -> bool:
    compatible = {"binary", "binary_event", "binary event", "yes_no", "yes no"}
    return left in compatible and right in compatible


def _market_type(market: dict[str, Any]) -> str:
    raw = market.get("raw") if isinstance(market.get("raw"), dict) else {}
    explicit = market.get("market_type") or raw.get("market_type") or raw.get("type")
    if explicit:
        return _normalize_text(str(explicit))
    text = _comparison_text(market).lower()
    if _numeric_tokens(text) and (_OVER_TOKENS & set(_TOKEN_RE.findall(text)) or _UNDER_TOKENS & set(_TOKEN_RE.findall(text))):
        return "threshold_binary"
    return "binary_event"


def _polarity(text: str) -> str:
    tokens = set(_TOKEN_RE.findall(text.lower()))
    over = bool(tokens & _OVER_TOKENS)
    under = bool(tokens & _UNDER_TOKENS)
    negated = bool(tokens & _NEGATION_TOKENS)
    if over and under:
        return "ambiguous"
    if under:
        return "negative"
    if over:
        return "positive"
    return "negative" if negated else "positive"


def _outcome_names(market: dict[str, Any]) -> list[str]:
    outcomes = market.get("outcomes")
    names: list[str] = []
    if isinstance(outcomes, list):
        for outcome in outcomes:
            if isinstance(outcome, dict) and outcome.get("name") is not None:
                names.append(str(outcome["name"]).strip().lower())
            elif outcome is not None:
                names.append(str(outcome).strip().lower())
    raw = market.get("raw") if isinstance(market.get("raw"), dict) else {}
    raw_outcomes = _maybe_json_array(raw.get("outcomes"))
    if not names and isinstance(raw_outcomes, list):
        names = [str(value).strip().lower() for value in raw_outcomes if value is not None]
    return [name for name in names if name]


def _binary_yes_no(outcomes: list[str]) -> bool:
    values = set(outcomes)
    return bool(values & _YES_TOKENS) and bool(values & _NO_TOKENS)


def _has_no_side_spread_text(market: dict[str, Any]) -> bool:
    text = _comparison_text(market).lower()
    return " no " in f" {text} " and any(token in text for token in ("spread", "handicap", "minus", "plus"))


def _unit_tokens(market: dict[str, Any]) -> set[str]:
    raw = market.get("raw") if isinstance(market.get("raw"), dict) else {}
    values = [
        market.get("currency"),
        market.get("quote_currency"),
        market.get("unit"),
        market.get("liquidity_unit"),
        market.get("depth_unit"),
        raw.get("currency"),
        raw.get("quote_currency"),
        raw.get("unit"),
        raw.get("liquidity_unit"),
        raw.get("depth_unit"),
    ]
    return {_normalize_text(str(value)) for value in values if value}


def _fee_available(market: dict[str, Any]) -> bool:
    if str(market.get("venue") or "").strip().lower() == "kalshi":
        return True
    if market.get("fee_model") or market.get("fee_rate") is not None:
        return True
    raw = market.get("raw") if isinstance(market.get("raw"), dict) else {}
    if raw.get("fee_model") or raw.get("fee_rate") is not None:
        return True
    if raw.get("feeSchedule") or raw.get("fee_schedule") or raw.get("feeType") or raw.get("fee_type"):
        return True
    enrichment = _enrichment(market)
    return enrichment.get("fee_model") is not None or enrichment.get("fee_rate") is not None


def _enrichment(market: dict[str, Any]) -> dict[str, Any]:
    enrichment = market.get("orderbook_enrichment")
    return enrichment if isinstance(enrichment, dict) else {}


def _tiebreak_tokens(text: str) -> set[str]:
    tokens = set(_TOKEN_RE.findall(text.lower()))
    return tokens & {"tie", "ties", "tiebreak", "tiebreaker", "void", "push", "cancel", "canceled", "cancelled"}


def _normalize_text(value: str) -> str:
    return " ".join(_TOKEN_RE.findall(value.lower()))


def _normalize_without_tiebreak(value: str) -> str:
    tiebreak = {"tie", "ties", "tiebreak", "tiebreaker", "void", "push", "cancel", "canceled", "cancelled"}
    return " ".join(token for token in _TOKEN_RE.findall(value.lower()) if token not in tiebreak)


def _explicit_mlb_world_series_source_text(value: str) -> bool:
    normalized = _normalize_text(value)
    tokens = set(_TOKEN_RE.findall(value.lower()))
    if not {"mlb", "world", "series"} <= tokens:
        return False
    explicit_phrases = (
        "team that wins",
        "wins the 2026 mlb world series",
        "official information from mlb",
        "official mlb",
        "mlb world series winner",
    )
    return any(phrase in normalized for phrase in explicit_phrases)


def _explicit_nba_championship_source_text(value: str) -> bool:
    normalized = _normalize_text(value)
    tokens = set(_TOKEN_RE.findall(value.lower()))
    explicit_phrases = (
        "resolution source for this market will be information from the nba",
        "official information from the nba",
    )
    if any(phrase in normalized for phrase in explicit_phrases):
        return True
    if re.search(r"\b(primary\s+source|resolves?\s+according\s+to|resolution\s+source)\b.*\bnba\b", normalized):
        return True
    explicit_scope = {"nba", "finals"} <= tokens or "pro basketball finals" in normalized
    if not explicit_scope:
        return False
    if re.search(r"\bwins?\s+the\s+\d{4}\s+nba\s+finals\b", normalized):
        return True
    if re.search(r"\bwins?\s+the\s+\d{4}\s+pro\s+basketball\s+finals\b", normalized):
        return True
    return False


def _explicit_nhl_stanley_cup_source_text(value: str) -> bool:
    normalized = _normalize_text(value)
    tokens = set(_TOKEN_RE.findall(value.lower()))
    if "resolution source for this market will be information from the nhl" in normalized:
        return True
    if "official information from the nhl" in normalized:
        return True
    explicit_scope = "stanley cup" in normalized or "pro hockey championship" in normalized
    if not explicit_scope:
        return False
    if "stanley cup" not in normalized and "nhl" not in tokens and "hockey" not in tokens:
        return False
    if re.search(r"\bwins?\s+the\s+(20\d{2}|20\d{2}\s+\d{2}|20\d{2}\s+20\d{2})\s+(nhl\s+)?stanley\s+cup(\s+finals)?\b", normalized):
        return True
    if re.search(r"\bwins?\s+the\s+(20\d{2}|20\d{2}\s+\d{2}|20\d{2}\s+20\d{2})\s+pro\s+hockey\s+championship\b", normalized):
        return True
    return False


def _number_values(text: str) -> list[float]:
    return [float(value) for value in _NUMBER_RE.findall(text.lower())]


def _world_series_vs_league_title(left: str, right: str) -> bool:
    left_norm = _normalize_text(left)
    right_norm = _normalize_text(right)
    league_phrases = ("alcs", "nlcs", "american league championship", "national league championship", "conference title")
    left_world = "world series" in left_norm
    right_world = "world series" in right_norm
    left_league = any(phrase in left_norm for phrase in league_phrases)
    right_league = any(phrase in right_norm for phrase in league_phrases)
    return (left_world and right_league) or (right_world and left_league)


def _nba_championship_vs_conference_title(left: str, right: str) -> bool:
    left_norm = _normalize_text(left)
    right_norm = _normalize_text(right)
    championship_phrases = ("nba finals", "nba champion", "nba championship", "pro basketball finals")
    conference_phrases = ("conference finals", "conference title", "conference winner")
    left_championship = any(phrase in left_norm for phrase in championship_phrases)
    right_championship = any(phrase in right_norm for phrase in championship_phrases)
    left_conference = any(phrase in left_norm for phrase in conference_phrases)
    right_conference = any(phrase in right_norm for phrase in conference_phrases)
    return (left_championship and right_conference) or (right_championship and left_conference)


def _nhl_stanley_cup_vs_conference_or_division_title(left: str, right: str) -> bool:
    left_norm = _normalize_text(left)
    right_norm = _normalize_text(right)
    championship_phrases = ("stanley cup", "pro hockey championship")
    nested_phrases = ("conference champion", "conference title", "conference winner", "division champion", "division title", "division winner")
    left_championship = any(phrase in left_norm for phrase in championship_phrases)
    right_championship = any(phrase in right_norm for phrase in championship_phrases)
    left_nested = any(phrase in left_norm for phrase in nested_phrases) or ("conference" in left_norm and not left_championship) or ("division" in left_norm and not left_championship)
    right_nested = any(phrase in right_norm for phrase in nested_phrases) or ("conference" in right_norm and not right_championship) or ("division" in right_norm and not right_championship)
    return (left_championship and right_nested) or (right_championship and left_nested)


def _btc_threshold_pair(left_tokens: set[str], right_tokens: set[str]) -> bool:
    combined = left_tokens | right_tokens
    return "btc" in combined or "bitcoin" in combined


def _parse_datetime_or_none(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _maybe_json_array(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return []
    if stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def _venue_label(payload: Any, id_key: str) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get(id_key) or payload.get("market_id") or "")


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def _string_or_empty(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _string_or_none(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
