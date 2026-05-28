from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
REPORT_SOURCE = "sx_bet_sports_typed_keys_v1"

SPORTS_TYPED_KEYS_COMPLETE = "SPORTS_TYPED_KEYS_COMPLETE"
SPORTS_TYPED_KEYS_PARTIAL = "SPORTS_TYPED_KEYS_PARTIAL"
SPORTS_TYPED_KEYS_BLOCKED = "SPORTS_TYPED_KEYS_BLOCKED"
REFERENCE_ONLY_UNUSABLE = "REFERENCE_ONLY_UNUSABLE"
UNKNOWN_OR_OUT_OF_SCOPE = "UNKNOWN_OR_OUT_OF_SCOPE"

MONEYLINE_TYPES = {"52", "226"}
SPREAD_TYPES = {"342"}
TOTAL_TYPES = {"28"}
FUTURES_TYPES = {"274"}

REFERENCE_BLOCKER = "reference_only_no_executable_market"


def build_sx_bet_sports_typed_keys_report(
    *,
    input_path: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    payload, warning = _load_input(input_path)
    if warning is not None:
        return _empty_report(input_path=input_path, generated_at=generated, warning=warning)
    records = _draft_records(payload)
    rows = [_typed_key_row(record, index) for index, record in enumerate(records)]
    return {
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "input": str(input_path),
        "summary": _summary(rows, warnings=[]),
        "rows": rows,
        "warnings": [],
        "safety": _safety_block(),
    }


def write_sx_bet_sports_typed_keys_files(
    *,
    input_path: Path,
    json_output: Path,
    markdown_output: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_sx_bet_sports_typed_keys_report(input_path=input_path, generated_at=generated_at)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_sx_bet_sports_typed_keys_markdown(report), encoding="utf-8")
    return report


def render_sx_bet_sports_typed_keys_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# SX Bet Sports Typed-Key Coverage",
        "",
        "Saved-file-only SX Bet sports typed-key audit. This report creates no candidates and does not affect evaluator gates.",
        "",
        "## Summary",
        "",
        f"- total_rows: `{summary.get('total_rows', 0)}`",
        f"- complete: `{summary.get('complete', 0)}`",
        f"- partial: `{summary.get('partial', 0)}`",
        f"- blocked: `{summary.get('blocked', 0)}`",
        f"- future_overlap_review_usable: `{summary.get('future_overlap_review_usable_count', 0)}`",
        f"- candidate_count: `{summary.get('candidate_count', 0)}`",
        "",
        "## Top Blockers",
        "",
    ]
    blockers = summary.get("top_blockers") or []
    if blockers:
        lines.extend(["| Blocker | Count |", "|---|---:|"])
        for row in blockers[:12]:
            lines.append(f"| {_md(row.get('blocker'))} | {_md(row.get('count'))} |")
    else:
        lines.append("(none)")
    lines.extend(
        [
            "",
            "## Coverage",
            "",
            "### By Sport",
            "",
        ]
    )
    lines.extend(_count_table(summary.get("by_sport") or [], "sport"))
    lines.extend(["", "### By League", ""])
    lines.extend(_count_table(summary.get("by_league") or [], "league"))
    lines.extend(["", "### By Market Type", ""])
    lines.extend(_count_table(summary.get("by_market_type") or [], "market_type"))
    lines.extend(
        [
            "",
            "## Sample Rows",
            "",
            "| Market | League | Type | Status | Overlap usable | Blockers |",
            "|---|---|---|---|---:|---|",
        ]
    )
    for row in (report.get("rows") or [])[:20]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("market_id")),
                    _md((row.get("typed_key") or {}).get("league")),
                    _md((row.get("typed_key") or {}).get("market_type")),
                    _md(row.get("classification")),
                    _md(str(row.get("usable_for_future_overlap_review")).lower()),
                    _md(",".join(row.get("blockers") or [])),
                ]
            )
            + " |"
        )
    if not report.get("rows"):
        lines.append("| (none) | (none) | (none) | false | (none) |")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- saved_files_only: `true`",
            "- live_api_calls_attempted: `false`",
            "- candidates_or_pairs_created: `false`",
            "- affects_evaluator_gates: `false`",
        ]
    )
    return "\n".join(lines) + "\n"


def _typed_key_row(record: dict[str, Any], index: int) -> dict[str, Any]:
    sport = _string_or_none(record.get("sport"))
    league = _string_or_none(record.get("league"))
    event_time = _string_or_none(record.get("event_time"))
    participants, participants_confidence, participant_blockers = _participants(record)
    market_type, market_type_confidence, market_type_blockers = _market_type(record)
    outcomes = _outcomes(record)
    side = _side_key(outcomes, market_type)
    line = record.get("line")
    threshold = record.get("threshold")
    operator = _operator_for_market_type(market_type, outcomes)
    void_rule = _string_or_none(((record.get("settlement") or {}) if isinstance(record.get("settlement"), dict) else {}).get("void_rule"))
    settlement_text = _string_or_none(record.get("settlement_rules_text")) or _string_or_none(
        ((record.get("settlement") or {}) if isinstance(record.get("settlement"), dict) else {}).get("settlement_source_text")
    )
    title_derived = participants_confidence == "LOW"

    typed_key_blockers: list[str] = []
    if not sport:
        typed_key_blockers.append("missing_sport")
    if not league:
        typed_key_blockers.append("missing_league")
    if not event_time:
        typed_key_blockers.append("missing_event_time")
    if not participants:
        typed_key_blockers.append("missing_participants")
    if "ambiguous_team_names" in participant_blockers:
        typed_key_blockers.append("ambiguous_team_names")
    if not market_type:
        typed_key_blockers.append("missing_market_type")
    if "ambiguous_market_type" in market_type_blockers:
        typed_key_blockers.append("ambiguous_market_type")
    if market_type in {"spread", "total"} and line is None:
        typed_key_blockers.append("missing_line")
    if not side:
        typed_key_blockers.append("missing_side")
    if title_derived:
        typed_key_blockers.append("title_only_participants_low_confidence")

    review_blockers = [REFERENCE_BLOCKER]
    if not void_rule:
        review_blockers.append("missing_void_rules")
    if not settlement_text:
        review_blockers.append("missing_settlement_rules_source_text")

    classification = _classification(
        sport=sport,
        typed_key_blockers=typed_key_blockers,
        review_blockers=review_blockers,
    )
    exact_review_ready = classification == SPORTS_TYPED_KEYS_COMPLETE and "missing_void_rules" not in review_blockers
    usable_for_future_overlap_review = classification == SPORTS_TYPED_KEYS_COMPLETE
    typed_key = {
        "sport": sport,
        "league": league,
        "season": _season(event_time),
        "event_time": event_time,
        "home_team": None,
        "away_team": None,
        "participants": participants,
        "participants_confidence": participants_confidence,
        "participants_source": "title" if title_derived else "structured" if participants else None,
        "market_type": market_type,
        "market_type_confidence": market_type_confidence,
        "side": side,
        "line": line,
        "threshold": threshold,
        "operator": operator,
        "void_rule": void_rule,
        "settlement_or_rules_text": settlement_text,
    }
    return {
        "row_index": index,
        "venue": "sx_bet",
        "market_id": record.get("market_id"),
        "event_id": record.get("event_id"),
        "title": record.get("title") or record.get("question"),
        "classification": classification,
        "reference_only_status": REFERENCE_ONLY_UNUSABLE,
        "typed_key": typed_key,
        "typed_key_blockers": sorted(set(typed_key_blockers)),
        "review_blockers": sorted(set(review_blockers)),
        "blockers": sorted(set(typed_key_blockers + review_blockers)),
        "exact_review_ready": exact_review_ready,
        "usable_for_future_overlap_review": usable_for_future_overlap_review,
        "usable_as_executable_market": False,
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "candidate_or_pair_created": False,
        "raw_source_file": record.get("raw_source_file"),
        "raw_row_index": record.get("raw_row_index"),
        "low_confidence_fields": ["participants"] if title_derived else [],
    }


def _classification(*, sport: str | None, typed_key_blockers: list[str], review_blockers: list[str]) -> str:
    if not sport:
        return UNKNOWN_OR_OUT_OF_SCOPE
    critical = {
        "missing_league",
        "missing_event_time",
        "missing_participants",
        "missing_market_type",
        "missing_side",
        "ambiguous_team_names",
        "ambiguous_market_type",
        "title_only_participants_low_confidence",
    }
    if critical & set(typed_key_blockers):
        return SPORTS_TYPED_KEYS_BLOCKED
    if "missing_line" in typed_key_blockers or "missing_void_rules" in review_blockers:
        return SPORTS_TYPED_KEYS_PARTIAL
    return SPORTS_TYPED_KEYS_COMPLETE


def _participants(record: dict[str, Any]) -> tuple[list[str], str | None, list[str]]:
    raw_participants = record.get("participants")
    if isinstance(raw_participants, list):
        participants = [_clean_participant(item) for item in raw_participants]
        participants = [item for item in participants if item]
        if participants:
            blockers = ["ambiguous_team_names"] if _ambiguous_participants(participants) else []
            return participants, "HIGH", blockers
    title = _string_or_none(record.get("title") or record.get("question"))
    if title and " vs " in title.lower():
        parts = re.split(r"\s+vs\.?\s+", title, flags=re.IGNORECASE)
        participants = [_clean_participant(part) for part in parts[:2]]
        participants = [item for item in participants if item]
        if len(participants) >= 2:
            blockers = ["ambiguous_team_names"] if _ambiguous_participants(participants) else []
            return participants, "LOW", blockers
    return [], None, []


def _clean_participant(value: Any) -> str | None:
    text = _string_or_none(value)
    if not text:
        return None
    cleaned = re.sub(r"\s+[+-]\d+(?:\.\d+)?$", "", text)
    cleaned = re.sub(r"^(Over|Under)\s+\d+(?:\.\d+)?$", r"\1", cleaned, flags=re.IGNORECASE)
    return cleaned.strip() or None


def _ambiguous_participants(participants: list[str]) -> bool:
    lowered = {participant.lower() for participant in participants}
    if len(lowered) < len(participants):
        return True
    ambiguous_tokens = {"field", "the field", "draw", "tie", "yes", "no", "over", "under"}
    return bool(lowered & ambiguous_tokens)


def _market_type(record: dict[str, Any]) -> tuple[str | None, str | None, list[str]]:
    raw_type = _string_or_none(record.get("market_type"))
    if raw_type:
        normalized = raw_type.rstrip(".0")
        if normalized in MONEYLINE_TYPES:
            return "moneyline", "HIGH", []
        if normalized in SPREAD_TYPES:
            return "spread", "HIGH", []
        if normalized in TOTAL_TYPES:
            return "total", "HIGH", []
        if normalized in FUTURES_TYPES:
            return "futures/championship", "HIGH", []
    outcomes = [str((outcome or {}).get("name") or "").lower() for outcome in _outcomes(record)]
    title = str(record.get("title") or record.get("question") or "").lower()
    if any(name.startswith("over") or name.startswith("under") for name in outcomes):
        return "total", "MEDIUM", []
    if record.get("line") is not None and outcomes:
        return "spread", "MEDIUM", []
    if any(term in title for term in ("champion", "championship", "winner", "outright", "vs the field")):
        return "futures/championship", "LOW", ["ambiguous_market_type"]
    if len(outcomes) >= 2:
        return "moneyline", "LOW", ["ambiguous_market_type"]
    return None, None, []


def _outcomes(record: dict[str, Any]) -> list[dict[str, Any]]:
    outcomes = record.get("outcomes")
    if not isinstance(outcomes, list):
        return []
    return [outcome for outcome in outcomes if isinstance(outcome, dict) and _string_or_none(outcome.get("name"))]


def _side_key(outcomes: list[dict[str, Any]], market_type: str | None) -> str | None:
    names = [_string_or_none(outcome.get("name")) for outcome in outcomes]
    names = [name for name in names if name]
    if not names:
        return None
    if market_type == "total":
        lowered = {name.lower().split()[0] for name in names if name}
        if {"over", "under"} <= lowered:
            return "over_under"
    if market_type == "spread":
        return "team_spread_sides"
    if market_type == "moneyline":
        return "team_win_sides"
    if market_type == "futures/championship":
        return "winner_or_field_sides"
    return "outcome_sides_present"


def _operator_for_market_type(market_type: str | None, outcomes: list[dict[str, Any]]) -> str | None:
    if market_type == "total":
        return "over_under"
    if market_type == "spread":
        return "handicap"
    if market_type == "moneyline":
        return "wins"
    if market_type == "futures/championship":
        return "wins_or_field"
    names = " ".join(str((outcome or {}).get("name") or "") for outcome in outcomes).lower()
    if "over" in names or "under" in names:
        return "over_under"
    return None


def _season(event_time: str | None) -> str | None:
    if not event_time:
        return None
    match = re.match(r"(\d{4})", event_time)
    return match.group(1) if match else None


def _summary(rows: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    by_sport = Counter()
    by_league = Counter()
    by_market_type = Counter()
    blockers = Counter()
    classification_counts = Counter()
    for row in rows:
        typed = row.get("typed_key") or {}
        if typed.get("sport"):
            by_sport[str(typed["sport"])] += 1
        if typed.get("league"):
            by_league[str(typed["league"])] += 1
        if typed.get("market_type"):
            by_market_type[str(typed["market_type"])] += 1
        blockers.update(row.get("blockers") or [])
        classification_counts[str(row.get("classification"))] += 1
    return {
        "total_rows": len(rows),
        "complete": classification_counts[SPORTS_TYPED_KEYS_COMPLETE],
        "partial": classification_counts[SPORTS_TYPED_KEYS_PARTIAL],
        "blocked": classification_counts[SPORTS_TYPED_KEYS_BLOCKED],
        "reference_only_unusable": sum(1 for row in rows if row.get("reference_only_status") == REFERENCE_ONLY_UNUSABLE),
        "unknown_or_out_of_scope": classification_counts[UNKNOWN_OR_OUT_OF_SCOPE],
        "future_overlap_review_usable_count": sum(1 for row in rows if row.get("usable_for_future_overlap_review")),
        "candidate_count": 0,
        "pair_count": 0,
        "by_sport": [{"sport": key, "count": count} for key, count in sorted(by_sport.items())],
        "by_league": [{"league": key, "count": count} for key, count in sorted(by_league.items())],
        "by_market_type": [{"market_type": key, "count": count} for key, count in sorted(by_market_type.items())],
        "top_blockers": [{"blocker": key, "count": count} for key, count in blockers.most_common()],
        "warning_count": len(warnings),
    }


def _empty_report(*, input_path: Path, generated_at: datetime, warning: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "generated_at": generated_at.isoformat(),
        "input": str(input_path),
        "summary": _summary([], warnings=[warning]),
        "rows": [],
        "warnings": [warning],
        "safety": _safety_block(),
    }


def _draft_records(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    records = payload.get("records")
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict) and record.get("venue") == "sx_bet"]


def _load_input(path: Path) -> tuple[Any, dict[str, Any] | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, {"source_file": str(path), "blocker": "input_report_missing", "message": "SX Bet normalized draft report not found."}
    except json.JSONDecodeError:
        return None, {"source_file": str(path), "blocker": "input_report_invalid_json"}
    except OSError as exc:
        return None, {"source_file": str(path), "blocker": "input_report_read_failed", "message": str(exc)}
    if not isinstance(payload, dict) or payload.get("source") != "sx_bet_normalized_draft_v1":
        return None, {"source_file": str(path), "blocker": "unsupported_input_report_source"}
    return payload, None


def _count_table(rows: list[dict[str, Any]], label: str) -> list[str]:
    if not rows:
        return ["(none)"]
    lines = [f"| {label} | Count |", "|---|---:|"]
    for row in rows:
        lines.append(f"| {_md(row.get(label))} | {_md(row.get('count'))} |")
    return lines


def _safety_block() -> dict[str, Any]:
    return {
        "saved_files_only": True,
        "live_api_calls_attempted": False,
        "auth_or_account_flow_added": False,
        "wallet_or_signing_logic_added": False,
        "order_or_execution_logic_added": False,
        "candidates_or_pairs_created": False,
        "affects_evaluator_gates": False,
        "paper_candidate_emitted": False,
    }


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _md(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
