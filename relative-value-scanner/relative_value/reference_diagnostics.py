from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.live_snapshot_matcher import load_reference_snapshot
from relative_value.reference_odds import load_saved_reference_odds_rows


DEFAULT_REFERENCE_MATCH_MIN_SCORE = 0.35
REFERENCE_FV_SOURCE = "reference_odds_fv_residuals_v1"
DEFAULT_EVENT_TIME_TOLERANCE_SECONDS = 12 * 60 * 60
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "at",
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
}


def explain_reference_context_files(
    *,
    snapshot_path: Path,
    reference_snapshot_path: Path,
    now: datetime | None = None,
    min_similarity: float = DEFAULT_REFERENCE_MATCH_MIN_SCORE,
) -> dict[str, Any]:
    generated_at = now or datetime.now(timezone.utc)
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("now must include timezone information")
    executable_snapshot = _load_executable_snapshot(snapshot_path)
    reference_snapshot = load_reference_snapshot(reference_snapshot_path)
    if reference_snapshot.issues:
        raise ValueError(f"reference snapshot invalid: {','.join(reference_snapshot.issues)}")

    executable_markets = executable_snapshot.get("normalized_markets", [])
    reference_records = reference_snapshot.payload.get("normalized_records", [])
    diagnostic_rows: list[dict[str, Any]] = []
    skipped_reference_record_count = 0
    stale_reference_record_count = 0
    malformed_reference_record_count = 0
    for record in reference_records:
        if not isinstance(record, dict):
            skipped_reference_record_count += 1
            malformed_reference_record_count += 1
            continue
        issues = _reference_record_issues(record, generated_at)
        if "malformed_reference_record" in issues:
            skipped_reference_record_count += 1
            malformed_reference_record_count += 1
            continue
        if "stale_reference_record" in issues:
            stale_reference_record_count += 1
        for market in executable_markets:
            if not isinstance(market, dict):
                continue
            score = _reference_match_score(market, record)
            if score < min_similarity:
                continue
            diagnostic_rows.append(_diagnostic_row(market, record, score, issues))

    diagnostic_rows.sort(key=lambda row: (row["match_score"], row["executable_market_title"]), reverse=True)
    return {
        "schema_version": 1,
        "source": "reference_context_diagnostics",
        "generated_at": generated_at.isoformat(),
        "inputs": {
            "snapshot": str(snapshot_path),
            "reference_snapshot": str(reference_snapshot_path),
        },
        "reference_source_id": reference_snapshot.payload.get("source_id"),
        "reference_source_type": reference_snapshot.payload.get("source_type"),
        "executable_market_count": len(executable_markets),
        "reference_record_count": len(reference_records),
        "diagnostic_match_count": len(diagnostic_rows),
        "stale_reference_record_count": stale_reference_record_count,
        "malformed_reference_record_count": malformed_reference_record_count,
        "skipped_reference_record_count": skipped_reference_record_count,
        "diagnostic_rows": diagnostic_rows,
        "disclaimer": (
            "Reference-only diagnostics. Sportsbook odds are not executable prices; "
            "title similarity is not settlement equivalence; no action promotion is performed."
        ),
    }


def build_reference_odds_fv_report(
    *,
    input_dir: Path,
    generated_at: datetime | None = None,
    event_time_tolerance_seconds: int = DEFAULT_EVENT_TIME_TOLERANCE_SECONDS,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None or generated.utcoffset() is None:
        raise ValueError("generated_at must include timezone information")

    reference_payload = load_saved_reference_odds_rows(input_dir)
    reference_rows = [row for row in reference_payload["rows"] if isinstance(row, dict)]
    targets = _load_saved_sports_targets(input_dir)
    residual_rows: list[dict[str, Any]] = []
    matched_reference_keys: set[tuple[Any, ...]] = set()
    for reference in reference_rows:
        reference_key = _reference_key(reference)
        for target in targets:
            match = _structured_sports_match(
                reference,
                target,
                event_time_tolerance_seconds=event_time_tolerance_seconds,
            )
            if not match["matched"]:
                continue
            matched_reference_keys.add(reference_key)
            residual_rows.append(_residual_row(reference, target, match))

    residual_rows.sort(
        key=lambda row: (
            row.get("residual_abs") is not None,
            row.get("residual_abs") or 0.0,
            str(row.get("reference_event_id") or ""),
            str(row.get("target_market_id") or ""),
        ),
        reverse=True,
    )
    blockers = Counter()
    for row in residual_rows:
        blockers.update(row.get("blockers") or [])
    unmatched_count = sum(1 for row in reference_rows if _reference_key(row) not in matched_reference_keys)
    summary = {
        "odds_events_read": reference_payload["odds_events_read"],
        "reference_markets_read": len(reference_rows),
        "matched_rows": len(residual_rows),
        "unmatched_reference_rows": unmatched_count,
        "residual_rows": len(residual_rows),
        "top_residuals": residual_rows[:10],
        "blockers_by_count": [{"blocker": key, "count": value} for key, value in blockers.most_common()],
        "target_rows_considered": len(targets),
        "warnings": len(reference_payload["warnings"]),
    }
    return {
        "schema_version": 1,
        "source": REFERENCE_FV_SOURCE,
        "generated_at": generated.isoformat(),
        "input_dir": str(input_dir),
        "summary": summary,
        "reference_summary": {
            "files_read": reference_payload["files_read"],
            "warnings": reference_payload["warnings"],
        },
        "residual_rows": residual_rows,
        "warnings": reference_payload["warnings"],
        "safety": {
            "saved_files_only": True,
            "live_api_calls_attempted": False,
            "authenticated_endpoints_used": False,
            "orders_or_cancellations": False,
            "reference_only_source": True,
            "executable_leg": False,
            "candidate_pair_creation": False,
            "paper_candidate_emitted": False,
            "affects_evaluator_gates": False,
        },
    }


def write_reference_odds_fv_files(
    *,
    input_dir: Path,
    json_output: Path,
    markdown_output: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_reference_odds_fv_report(input_dir=input_dir, generated_at=generated_at)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_reference_odds_fv_markdown(report), encoding="utf-8")
    return report


def render_reference_odds_fv_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# The Odds API Fair-Value Residual Diagnostics",
        "",
        "Saved-snapshot-only reference diagnostics. The Odds API is reference-only and never an executable leg.",
        "",
        "## Summary",
        "",
        f"- odds_events_read: `{summary.get('odds_events_read', 0)}`",
        f"- reference_markets_read: `{summary.get('reference_markets_read', 0)}`",
        f"- matched_rows: `{summary.get('matched_rows', 0)}`",
        f"- unmatched_reference_rows: `{summary.get('unmatched_reference_rows', 0)}`",
        f"- residual_rows: `{summary.get('residual_rows', 0)}`",
        "",
        "## Top Residuals",
        "",
    ]
    top = summary.get("top_residuals") or []
    if top:
        lines.extend(["| Reference | Target | Market | Residual | Action | Blockers |", "|---|---|---|---:|---|---|"])
        for row in top[:10]:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md(row.get("reference_event_title")),
                        _md(row.get("target_title")),
                        _md(row.get("market_type")),
                        _md(row.get("residual_abs")),
                        _md(row.get("allowed_next_action")),
                        _md(", ".join(row.get("blockers") or [])),
                    ]
                )
                + " |"
            )
    else:
        lines.append("(none)")
    lines.extend(["", "## Blockers", ""])
    blockers = summary.get("blockers_by_count") or []
    if blockers:
        lines.extend(["| Blocker | Count |", "|---|---:|"])
        for row in blockers[:12]:
            lines.append(f"| {_md(row.get('blocker'))} | {_md(row.get('count'))} |")
    else:
        lines.append("(none)")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- saved_files_only: `true`",
            "- live_api_calls_attempted: `false`",
            "- authenticated_endpoints_used: `false`",
            "- orders_or_cancellations: `false`",
            "- reference_only_source: `true`",
            "- executable_leg: `false`",
            "- candidate_pair_creation: `false`",
            "- paper_candidate_emitted: `false`",
            "- affects_evaluator_gates: `false`",
        ]
    )
    return "\n".join(lines) + "\n"


def _load_saved_sports_targets(input_dir: Path) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    targets.extend(_targets_from_sx_bet_typed_keys(input_dir / "sx_bet_sports_typed_keys.json"))
    targets.extend(_targets_from_normalized_markets(input_dir / "normalized_markets_v0.json"))
    return targets


def _targets_from_sx_bet_typed_keys(path: Path) -> list[dict[str, Any]]:
    payload = _load_json_or_none(path)
    if not isinstance(payload, dict) or payload.get("source") != "sx_bet_sports_typed_keys_v1":
        return []
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return []
    targets: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        typed = row.get("typed_key") if isinstance(row.get("typed_key"), dict) else {}
        participants = typed.get("participants") if isinstance(typed.get("participants"), list) else []
        targets.append(
            {
                "source_report": str(path),
                "venue": row.get("venue") or "sx_bet",
                "market_id": row.get("market_id"),
                "event_id": row.get("event_id"),
                "title": row.get("title"),
                "sport": typed.get("sport"),
                "league": typed.get("league"),
                "event_time": typed.get("event_time"),
                "participants": participants,
                "home_team": typed.get("home_team"),
                "away_team": typed.get("away_team"),
                "market_type": _market_type_to_reference(typed.get("market_type")),
                "line": typed.get("line") if typed.get("line") is not None else typed.get("threshold"),
                "outcome_name": typed.get("side"),
                "probability": _target_probability(row),
                "executable": bool(row.get("usable_as_executable_market")),
                "reference_only": row.get("reference_only_status") == "REFERENCE_ONLY_UNUSABLE",
            }
        )
    return targets


def _targets_from_normalized_markets(path: Path) -> list[dict[str, Any]]:
    payload = _load_json_or_none(path)
    if not isinstance(payload, dict):
        return []
    rows = payload.get("normalized_markets")
    if not isinstance(rows, list):
        return []
    targets: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        participants = _participants_from_any(row)
        if not participants:
            continue
        targets.append(
            {
                "source_report": str(path),
                "venue": row.get("venue"),
                "market_id": row.get("market_id") or row.get("ticker"),
                "event_id": row.get("event_id") or row.get("event_slug"),
                "title": row.get("title") or row.get("question") or row.get("event_title"),
                "sport": row.get("sport") or row.get("category"),
                "league": row.get("league") or row.get("sport"),
                "event_time": row.get("event_time") or row.get("close_time") or row.get("resolution_time"),
                "participants": participants,
                "home_team": row.get("home_team"),
                "away_team": row.get("away_team"),
                "market_type": _market_type_to_reference(row.get("market_type")),
                "line": row.get("line") or row.get("threshold"),
                "outcome_name": row.get("outcome_name") or row.get("side"),
                "probability": _target_probability(row),
                "executable": False,
                "reference_only": False,
            }
        )
    return targets


def _structured_sports_match(
    reference: dict[str, Any],
    target: dict[str, Any],
    *,
    event_time_tolerance_seconds: int,
) -> dict[str, Any]:
    reasons: list[str] = []
    mismatches: list[str] = []
    ref_league = _normalize_token(reference.get("league") or reference.get("sport"))
    target_league = _normalize_token(target.get("league") or target.get("sport"))
    if ref_league and target_league and ref_league != target_league:
        mismatches.append("league_mismatch")
    elif ref_league and target_league:
        reasons.append("league_exact")

    ref_teams = _team_set([reference.get("home_team"), reference.get("away_team")])
    target_teams = _team_set(target.get("participants") or [target.get("home_team"), target.get("away_team")])
    if not ref_teams or not target_teams:
        mismatches.append("missing_structured_participants")
    elif ref_teams != target_teams:
        mismatches.append("participant_mismatch")
    else:
        reasons.append("participants_exact")

    ref_time = _parse_datetime_or_none(str(reference.get("commence_time") or ""))
    target_time = _parse_datetime_or_none(str(target.get("event_time") or ""))
    if ref_time is None or target_time is None:
        mismatches.append("missing_event_time")
    else:
        delta = abs((ref_time - target_time).total_seconds())
        if delta > event_time_tolerance_seconds:
            mismatches.append("event_time_mismatch")
        else:
            reasons.append("event_time_within_tolerance")

    ref_market_type = _market_type_to_reference(reference.get("market_type"))
    target_market_type = _market_type_to_reference(target.get("market_type"))
    if not ref_market_type or not target_market_type:
        mismatches.append("missing_market_type")
    elif ref_market_type != target_market_type:
        mismatches.append("market_type_mismatch")
    else:
        reasons.append("market_type_exact")

    if ref_market_type in {"spreads", "totals"}:
        ref_line = _number_or_none(reference.get("line") if reference.get("line") is not None else reference.get("point"))
        target_line = _number_or_none(target.get("line"))
        if ref_line is None or target_line is None:
            mismatches.append("missing_line")
        elif abs(ref_line - target_line) > 0.000001:
            mismatches.append("line_mismatch")
        else:
            reasons.append("line_exact")

    return {
        "matched": not mismatches,
        "match_reasons": reasons,
        "mismatches": mismatches,
    }


def _residual_row(reference: dict[str, Any], target: dict[str, Any], match: dict[str, Any]) -> dict[str, Any]:
    reference_probability = _number_or_none(reference.get("no_vig_probability"))
    target_probability = _number_or_none(target.get("probability"))
    residual = None
    blockers = ["reference_only_source", "not_executable", "no_same_payoff_claim"]
    if reference_probability is None:
        blockers.append("vig_removal_ambiguous")
    if target_probability is None:
        blockers.append("missing_target_probability")
    if reference_probability is not None and target_probability is not None:
        residual = round(target_probability - reference_probability, 6)
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_next_action": "FAIR_VALUE_WATCH" if residual is not None else "IGNORE_LOW_CONFIDENCE",
        "reference_only_source": True,
        "executable_leg": False,
        "paper_candidate_emitted": False,
        "reference_event_id": reference.get("event_id"),
        "reference_event_title": _reference_event_title(reference),
        "reference_bookmaker": reference.get("bookmaker"),
        "reference_market_type": reference.get("market_type"),
        "reference_outcome_name": reference.get("outcome_name"),
        "reference_no_vig_probability": reference_probability,
        "reference_implied_probability": reference.get("implied_probability"),
        "target_venue": target.get("venue"),
        "target_market_id": target.get("market_id"),
        "target_title": target.get("title"),
        "target_probability": target_probability,
        "market_type": reference.get("market_type"),
        "line": reference.get("line"),
        "residual_probability": residual,
        "residual_abs": None if residual is None else round(abs(residual), 6),
        "match_reasons": match.get("match_reasons") or [],
        "blockers": sorted(set(blockers)),
    }


def _reference_key(reference: dict[str, Any]) -> tuple[Any, ...]:
    return (
        reference.get("raw_source_file"),
        reference.get("raw_event_index"),
        reference.get("bookmaker_key"),
        reference.get("market_type"),
        reference.get("outcome_name"),
        reference.get("point"),
    )


def _reference_event_title(reference: dict[str, Any]) -> str:
    away = reference.get("away_team")
    home = reference.get("home_team")
    if away and home:
        return f"{away} at {home}"
    return str(reference.get("event_id") or "")


def _market_type_to_reference(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if text in {"moneyline", "h2h", "head_to_head"}:
        return "h2h"
    if text in {"spread", "spreads"}:
        return "spreads"
    if text in {"total", "totals", "over_under"}:
        return "totals"
    return text or None


def _participants_from_any(row: dict[str, Any]) -> list[str]:
    for key in ("participants", "teams", "outcomes"):
        value = row.get(key)
        if isinstance(value, list):
            output = []
            for item in value:
                if isinstance(item, str):
                    output.append(item)
                elif isinstance(item, dict):
                    name = item.get("name") or item.get("label") or item.get("team")
                    if name:
                        output.append(str(name))
            if output:
                return output
    home = row.get("home_team")
    away = row.get("away_team")
    return [str(value) for value in (away, home) if value]


def _team_set(values: Any) -> set[str]:
    if not isinstance(values, list):
        values = list(values) if isinstance(values, tuple) else [values]
    return {_normalize_team(value) for value in values if _normalize_team(value)}


def _normalize_team(value: Any) -> str:
    return " ".join(_TOKEN_RE.findall(str(value or "").lower()))


def _normalize_token(value: Any) -> str:
    return " ".join(_TOKEN_RE.findall(str(value or "").lower()))


def _target_probability(row: dict[str, Any]) -> float | None:
    for key in (
        "no_vig_probability",
        "implied_probability",
        "reference_probability",
        "yes_reference_probability",
        "probability",
    ):
        probability = _number_or_none(row.get(key))
        if probability is not None:
            return probability
    quote = row.get("quote_depth") if isinstance(row.get("quote_depth"), dict) else {}
    for key in ("best_yes_ask_price", "best_yes_bid_price"):
        probability = _number_or_none(quote.get(key))
        if probability is not None:
            return probability
    return None


def _load_json_or_none(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _md(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _load_executable_snapshot(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"snapshot file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("snapshot JSON is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("snapshot JSON must be an object")
    if payload.get("schema_version") != 1:
        raise ValueError("snapshot schema_version must be 1")
    if payload.get("schema_kind") == "reference_snapshot_v1":
        raise ValueError("snapshot must be an executable venue snapshot, not reference_snapshot_v1")
    if not isinstance(payload.get("normalized_markets"), list):
        raise ValueError("snapshot normalized_markets must be a list")
    return payload


def _diagnostic_row(market: dict[str, Any], record: dict[str, Any], score: float, reference_issues: list[str]) -> dict[str, Any]:
    stale = "stale_reference_record" in reference_issues
    return {
        "action": "REFERENCE_ONLY_DIAGNOSTIC",
        "executable_market_id": market.get("market_id") or market.get("ticker"),
        "executable_market_title": _market_title(market),
        "executable_venue": market.get("venue") or market.get("source"),
        "reference_event_title": record.get("event_title"),
        "bookmaker": record.get("bookmaker"),
        "market_type": record.get("market_type"),
        "reference_outcome_name": record.get("outcome_name"),
        "no_vig_probability": record.get("no_vig_probability"),
        "implied_probability": record.get("implied_probability"),
        "retrieved_at": record.get("retrieved_at"),
        "stale_after": record.get("stale_after"),
        "reference_status": "stale" if stale else "fresh",
        "reference_diagnostics": sorted(set(reference_issues)),
        "match_score": round(score, 6),
        "match_reason": "title_entity_similarity_only",
        "notes": "Diagnostic only; no sportsbook execution, payoff equivalence, gap, fee, depth, or action claim.",
    }


def _reference_record_issues(record: dict[str, Any], generated_at: datetime) -> list[str]:
    issues: list[str] = []
    if record.get("source_type") != "REFERENCE_ONLY" or record.get("permission") != "REFERENCE_ONLY":
        issues.append("reference_record_not_reference_only")
    if record.get("is_executable") is not False:
        issues.append("reference_record_not_non_executable")
    if record.get("usable_for_trade_decision") is not False:
        issues.append("reference_record_trade_decision_not_disabled")
    if not (record.get("event_title") and record.get("bookmaker") and record.get("market_type")):
        issues.append("malformed_reference_record")
    stale_after = _parse_datetime_or_none(str(record.get("stale_after") or ""))
    if stale_after is None:
        issues.append("missing_reference_stale_after")
    elif generated_at > stale_after:
        issues.append("stale_reference_record")
    return issues


def _reference_match_score(market: dict[str, Any], record: dict[str, Any]) -> float:
    market_tokens = _meaningful_tokens(_market_text(market))
    reference_tokens = _meaningful_tokens(_reference_text(record))
    if not market_tokens or not reference_tokens:
        return 0.0
    overlap = len(market_tokens & reference_tokens)
    return (2.0 * overlap) / (len(market_tokens) + len(reference_tokens))


def _market_text(market: dict[str, Any]) -> str:
    return " ".join(str(value or "") for value in (market.get("event_title"), market.get("question"), market.get("title")))


def _reference_text(record: dict[str, Any]) -> str:
    return " ".join(
        str(value or "")
        for value in (
            record.get("event_title"),
            record.get("outcome_name"),
            record.get("market_type"),
        )
    )


def _market_title(market: dict[str, Any]) -> str:
    return str(market.get("question") or market.get("title") or market.get("event_title") or market.get("market_id") or "")


def _meaningful_tokens(value: str) -> set[str]:
    return {token for token in _TOKEN_RE.findall(value.lower()) if token not in _STOPWORDS}


def _parse_datetime_or_none(value: str) -> datetime | None:
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
