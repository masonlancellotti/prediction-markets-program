from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from venues.sx_bet import build_sx_bet_research_snapshot


SCHEMA_VERSION = 1
REPORT_SOURCE = "sx_bet_normalized_draft_v1"
COVERAGE_SOURCE = "sx_bet_normalized_draft_coverage_v1"

READINESS_TIER = "SX_BET_DRAFT_RESEARCH_ONLY"

SKIPPED_SOURCES = {
    REPORT_SOURCE,
    COVERAGE_SOURCE,
    "platform_api_expansion_audit_v1",
    "sx_bet_reference_context",
    "sx_bet_sports_typed_keys_v1",
    "sx_bet_sports_overlap_v1",
}


def build_sx_bet_saved_normalization_report(
    *,
    project_root: Path,
    input_dir: Path,
    include_fixture_dir: bool = True,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    records: list[dict[str, Any]] = []
    input_files: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for path in _candidate_paths(project_root=project_root, input_dir=input_dir, include_fixture_dir=include_fixture_dir):
        payload, warning = _load_json(path)
        if warning:
            warnings.append(warning)
            input_files.append({"path": str(path), "status": "INVALID_JSON", "rows_read": 0, "normalized_records": 0})
            continue
        status, rows = _sx_bet_rows_from_payload(path=path, payload=payload, generated_at=generated)
        input_files.append(
            {
                "path": str(path),
                "status": status,
                "rows_read": len(rows),
                "normalized_records": 0 if status != "OK" else len(rows),
            }
        )
        if status == "UNSUPPORTED_SAVED_SHAPE":
            warnings.append({"source_file": str(path), "blocker": "unsupported_sx_bet_saved_shape"})
        if status != "OK":
            continue
        for row_index, row in enumerate(rows):
            records.append(_record_from_research_row(row, source_file=path, row_index=row_index))

    return {
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "input_dir": str(input_dir),
        "records": records,
        "input_files": input_files,
        "warnings": warnings,
        "coverage": build_sx_bet_saved_normalization_coverage(records=records, input_files=input_files, warnings=warnings),
        "safety": _safety_block(),
    }


def build_sx_bet_saved_normalization_coverage(
    *,
    records: list[dict[str, Any]],
    input_files: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    blockers = Counter()
    sports = Counter()
    for record in records:
        blockers.update(record.get("blockers") or [])
        sport = _string_or_none(record.get("sport"))
        if sport:
            sports[sport] += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "source": COVERAGE_SOURCE,
        "summary": {
            "rows_read": sum(int(row.get("rows_read") or 0) for row in input_files),
            "normalized_records": len(records),
            "unique_events": len({record.get("event_id") for record in records if record.get("event_id")}),
            "unique_markets": len({record.get("market_id") for record in records if record.get("market_id")}),
            "sports_detected": [{"sport": sport, "count": count} for sport, count in sorted(sports.items())],
            "quote_fields_present": sum(1 for record in records if (record.get("quote") or {}).get("quote_fields_present")),
            "depth_fields_present": sum(1 for record in records if (record.get("depth") or {}).get("depth_fields_present")),
            "settlement_text_present": sum(1 for record in records if record.get("settlement_rules_text")),
            "event_time_present": sum(1 for record in records if record.get("event_time")),
            "warning_count": len(warnings),
            "top_blockers": [{"blocker": blocker, "count": count} for blocker, count in blockers.most_common()],
        },
        "top_blockers": [{"blocker": blocker, "count": count} for blocker, count in blockers.most_common()],
        "input_files": input_files,
        "warnings": warnings,
        "safety": _safety_block(),
    }


def write_sx_bet_saved_normalization_files(
    *,
    project_root: Path,
    input_dir: Path,
    json_output: Path,
    coverage_output: Path,
    include_fixture_dir: bool = True,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_sx_bet_saved_normalization_report(
        project_root=project_root,
        input_dir=input_dir,
        include_fixture_dir=include_fixture_dir,
        generated_at=generated_at,
    )
    coverage = report["coverage"]
    json_output.parent.mkdir(parents=True, exist_ok=True)
    coverage_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    coverage_output.write_text(json.dumps(coverage, indent=2, sort_keys=True), encoding="utf-8")
    return {"report": report, "coverage": coverage}


def _candidate_paths(*, project_root: Path, input_dir: Path, include_fixture_dir: bool) -> list[Path]:
    paths: list[Path] = []
    roots = [input_dir]
    if include_fixture_dir:
        roots.append(project_root / "venues" / "fixtures")
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*sx_bet*.json")):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            paths.append(path)
    return paths


def _sx_bet_rows_from_payload(*, path: Path, payload: Any, generated_at: datetime) -> tuple[str, list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        return "UNSUPPORTED_SAVED_SHAPE", []
    if payload.get("source") in SKIPPED_SOURCES:
        return "SKIPPED_DIAGNOSTIC_REPORT", []
    if payload.get("schema_kind") == "sx_bet_research_snapshot_v1" or payload.get("source") == "sx_bet_research":
        rows = payload.get("research_markets")
        if isinstance(rows, list):
            return "OK", [row for row in rows if isinstance(row, dict)]
        return "UNSUPPORTED_SAVED_SHAPE", []
    if isinstance(payload.get("markets"), list) and isinstance(payload.get("orders"), list):
        snapshot = build_sx_bet_research_snapshot(payload, captured_at=generated_at)
        rows = snapshot.get("research_markets")
        if isinstance(rows, list):
            return "OK", [row for row in rows if isinstance(row, dict)]
    lowered = path.name.lower()
    if "sx_bet" in lowered:
        return "UNSUPPORTED_SAVED_SHAPE", []
    return "NOT_SX_BET", []


def _record_from_research_row(row: dict[str, Any], *, source_file: Path, row_index: int) -> dict[str, Any]:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    orderbook = row.get("research_orderbook") if isinstance(row.get("research_orderbook"), dict) else {}
    settlement = row.get("settlement_metadata") if isinstance(row.get("settlement_metadata"), dict) else {}
    fee_metadata = row.get("fee_metadata") if isinstance(row.get("fee_metadata"), dict) else {}

    market_id = _first_string(row, raw, keys=("market_hash", "marketHash"))
    event_id = _first_string(row, raw, keys=("event_id", "sportXeventId", "eventId"))
    title = _first_string(row, raw, keys=("event_title", "eventName", "title", "question"))
    sport = _first_string(row, raw, keys=("sport", "sportLabel"))
    league = _first_string(row, raw, keys=("league", "leagueLabel", "group1"))
    event_time_raw = _first_value(row, raw, keys=("starts_at", "gameTime", "event_time", "start_time"))
    event_time = _normalize_time(event_time_raw)
    outcomes = _outcomes(row, raw)
    settlement_rules_text = _string_or_none(settlement.get("settlement_rule")) or _first_string(row, raw, keys=("settlementRule", "settlement_rules_text"))
    settlement_source_text = _string_or_none(settlement.get("settlement_source")) or _first_string(row, raw, keys=("settlementSource", "settlement_source"))
    quote_fields_present = any(
        orderbook.get(key) is not None
        for key in ("best_taker_price_outcome_one", "best_taker_price_outcome_two")
    )
    depth_fields_present = any(
        orderbook.get(key) is not None
        for key in ("depth_usdc_at_best_outcome_one", "depth_usdc_at_best_outcome_two")
    ) or bool(orderbook.get("outcome_one_taker_levels") or orderbook.get("outcome_two_taker_levels"))
    blockers = _record_blockers(
        market_id=market_id,
        event_time=event_time,
        sport=sport,
        league=league,
        outcomes=outcomes,
        settlement_rules_text=settlement_rules_text,
        settlement_source_text=settlement_source_text,
        quote_fields_present=quote_fields_present,
        depth_fields_present=depth_fields_present,
        fee_metadata=fee_metadata,
    )
    participants = _participants(row, raw)
    return {
        "venue": "sx_bet",
        "readiness_tier": READINESS_TIER,
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "is_executable": False,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "raw_source_file": str(source_file),
        "raw_row_index": row_index,
        "raw_evidence_paths": _raw_evidence_paths(row, raw, orderbook, settlement, fee_metadata),
        "market_id": market_id,
        "event_id": event_id,
        "title": title,
        "question": title,
        "description": None,
        "sport": sport,
        "league": league,
        "category": sport or league,
        "participants": participants,
        "teams": participants,
        "event_time": event_time,
        "event_time_raw": event_time_raw,
        "market_type": _first_value(row, raw, keys=("market_type", "type")),
        "line": _first_value(row, raw, keys=("line",)),
        "threshold": _first_value(row, raw, keys=("line",)),
        "main_line": _first_value(row, raw, keys=("main_line", "mainLine")),
        "outcomes": outcomes,
        "settlement_rules_text": settlement_rules_text,
        "settlement": {
            "settlement_rules_text": settlement_rules_text,
            "settlement_source_text": settlement_source_text,
            "settlement_source_url": _url_or_none(settlement_source_text),
            "settlement_source_kind": "url" if _url_or_none(settlement_source_text) else "text_or_unknown",
            "void_rule": _string_or_none(settlement.get("void_rule")) or _first_string(row, raw, keys=("outcomeVoidName",)),
        },
        "quote": {
            "quote_fields_present": quote_fields_present,
            "captured_at": _first_string(row, raw, keys=("quote_captured_at", "captured_at")),
            "best_taker_price_outcome_one": orderbook.get("best_taker_price_outcome_one"),
            "best_taker_price_outcome_two": orderbook.get("best_taker_price_outcome_two"),
            "executable_quote": False,
        },
        "depth": {
            "depth_fields_present": depth_fields_present,
            "maker_stake_usdc_at_best_outcome_one": orderbook.get("depth_usdc_at_best_outcome_one"),
            "maker_stake_usdc_at_best_outcome_two": orderbook.get("depth_usdc_at_best_outcome_two"),
            "order_count": orderbook.get("order_count"),
            "unit_warning": orderbook.get("unit_warning"),
            "executable_depth": False,
        },
        "fee_metadata": fee_metadata,
        "fee_metadata_status": _string_or_none(fee_metadata.get("fee_model_status")) or "unknown",
        "blockers": blockers,
    }


def _record_blockers(
    *,
    market_id: str | None,
    event_time: str | None,
    sport: str | None,
    league: str | None,
    outcomes: list[dict[str, Any]],
    settlement_rules_text: str | None,
    settlement_source_text: str | None,
    quote_fields_present: bool,
    depth_fields_present: bool,
    fee_metadata: dict[str, Any],
) -> list[str]:
    blockers = {
        "sx_bet_draft_research_only",
        "sx_bet_not_executable_in_project",
        "not_integrated_with_evaluator_gates",
    }
    if not market_id:
        blockers.add("missing_market_id")
    if not event_time:
        blockers.add("missing_event_time")
    if not sport:
        blockers.add("missing_sport")
    if not league:
        blockers.add("missing_league")
    if len(outcomes) < 2:
        blockers.add("missing_outcomes")
    if not settlement_rules_text:
        blockers.add("missing_settlement_rules_text")
    if not _url_or_none(settlement_source_text):
        blockers.add("missing_explicit_settlement_source_url")
    if quote_fields_present:
        blockers.add("sx_bet_quote_fields_research_only")
    else:
        blockers.add("missing_quote_fields")
    if depth_fields_present:
        blockers.add("sx_bet_depth_units_not_executable")
    else:
        blockers.add("missing_depth_fields")
    if _string_or_none(fee_metadata.get("fee_model_status")) != "reviewed":
        blockers.add("fee_metadata_unreviewed")
    return sorted(blockers)


def _outcomes(row: dict[str, Any], raw: dict[str, Any]) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    for side, keys in (
        ("outcome_one", ("outcome_one_name", "outcomeOneName", "teamOneName")),
        ("outcome_two", ("outcome_two_name", "outcomeTwoName", "teamTwoName")),
        ("void", ("outcome_void_name", "outcomeVoidName")),
    ):
        name = _first_string(row, raw, keys=keys)
        if name:
            outcomes.append({"side": side, "name": name})
    return outcomes


def _participants(row: dict[str, Any], raw: dict[str, Any]) -> list[str]:
    participants: list[str] = []
    for keys in (("teamOneName", "outcome_one_name", "outcomeOneName"), ("teamTwoName", "outcome_two_name", "outcomeTwoName")):
        value = _first_string(row, raw, keys=keys)
        if value and value not in participants:
            participants.append(value)
    return participants


def _raw_evidence_paths(
    row: dict[str, Any],
    raw: dict[str, Any],
    orderbook: dict[str, Any],
    settlement: dict[str, Any],
    fee_metadata: dict[str, Any],
) -> list[str]:
    paths: list[str] = []
    for key in ("market_hash", "event_title", "sport", "league", "starts_at", "market_type"):
        if key in row:
            paths.append(f"research_markets[].{key}")
    for key in ("marketHash", "sportXeventId", "gameTime", "type"):
        if key in raw:
            paths.append(f"research_markets[].raw.{key}")
    for key in ("best_taker_price_outcome_one", "depth_usdc_at_best_outcome_one", "unit_warning"):
        if key in orderbook:
            paths.append(f"research_markets[].research_orderbook.{key}")
    for key in ("settlement_rule", "settlement_source", "void_rule"):
        if key in settlement:
            paths.append(f"research_markets[].settlement_metadata.{key}")
    for key in ("fee_model_status", "source_note"):
        if key in fee_metadata:
            paths.append(f"research_markets[].fee_metadata.{key}")
    return paths


def _first_string(*mappings: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    value = _first_value(*mappings, keys=keys)
    return _string_or_none(value)


def _first_value(*mappings: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for mapping in mappings:
        for key in keys:
            if key in mapping and mapping.get(key) is not None:
                return mapping.get(key)
    return None


def _normalize_time(value: Any) -> str | None:
    text = _string_or_none(value)
    if not text:
        return None
    if text.isdigit():
        try:
            return datetime.fromtimestamp(int(text), tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _url_or_none(value: Any) -> str | None:
    text = _string_or_none(value)
    if text and (text.startswith("http://") or text.startswith("https://")):
        return text
    return None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _load_json(path: Path) -> tuple[Any, dict[str, Any] | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except json.JSONDecodeError:
        return None, {"source_file": str(path), "blocker": "invalid_json"}
    except OSError as exc:
        return None, {"source_file": str(path), "blocker": "json_read_failed", "message": str(exc)}


def _safety_block() -> dict[str, Any]:
    return {
        "saved_files_only": True,
        "live_api_calls_attempted": False,
        "auth_or_account_flow_added": False,
        "wallet_or_signing_logic_added": False,
        "order_or_execution_logic_added": False,
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "candidate_actions_created": False,
    }


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
