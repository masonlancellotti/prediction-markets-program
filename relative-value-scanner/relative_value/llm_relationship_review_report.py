from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from relative_value.contract_relationship import ContractRelationship
from relative_value.llm_relationship_classifier import (
    StubLLMRelationshipClient,
    build_llm_relationship_audit_sidecar,
    combine_deterministic_relationship_with_llm_proposal,
)


SUPPORTED_REVIEW_SOURCES = {"live_snapshot_matcher", "paper_candidate_evaluator"}


def review_relationship_report_file(
    *,
    input_path: Path,
    output_path: Path,
    markdown_output_path: Path | None = None,
    client: StubLLMRelationshipClient | None = None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    payload = _load_json_report(input_path)
    reviewed = review_relationship_report_payload(
        payload,
        input_path=input_path,
        client=client,
        timestamp=timestamp,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(reviewed, indent=2, sort_keys=True), encoding="utf-8")
    if markdown_output_path is not None:
        markdown_output_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_output_path.write_text(_markdown_summary(reviewed), encoding="utf-8")
    return reviewed


def review_relationship_report_payload(
    payload: Mapping[str, Any],
    *,
    input_path: Path | None = None,
    client: StubLLMRelationshipClient | None = None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    if payload.get("schema_version") != 1:
        raise ValueError("input schema_version must be 1")
    source = payload.get("source")
    if source not in SUPPORTED_REVIEW_SOURCES:
        raise ValueError("input source must be live_snapshot_matcher or paper_candidate_evaluator")
    generated_at = timestamp or datetime.now(timezone.utc)
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("timestamp must include timezone information")

    active_client = client or StubLLMRelationshipClient()
    reviewed = copy.deepcopy(dict(payload))
    rows = _relationship_rows(reviewed)
    reviewed_count = 0
    validation_error_count = 0
    manual_review_escalation_count = 0
    for index, row in rows:
        relationship_payload = row.get("contract_relationship")
        if not isinstance(relationship_payload, Mapping):
            continue
        deterministic = _contract_relationship_from_report(relationship_payload)
        raw_output = active_client.propose_relationship(_llm_input_payload(row, source=str(source), index=index))
        audit = build_llm_relationship_audit_sidecar(
            input_payload=_llm_input_payload(row, source=str(source), index=index),
            prompt="relationship-review-v1",
            model_id=active_client.model_id,
            model_version=active_client.model_version,
            raw_output=raw_output,
            timestamp=generated_at,
        )
        combined = combine_deterministic_relationship_with_llm_proposal(deterministic, audit)
        validation_errors = list(combined["llm_review"].get("validation_errors", []))
        llm_review = dict(combined["llm_review"])
        llm_review["combined_manual_review_required"] = combined["manual_review_required"]
        llm_review["deterministic_relationship_unchanged"] = True
        row["llm_review"] = llm_review
        reviewed_count += 1
        if validation_errors:
            validation_error_count += 1
        if combined["manual_review_required"] and not deterministic.manual_review_required:
            manual_review_escalation_count += 1

    reviewed["llm_relationship_review"] = {
        "schema_version": 1,
        "source": "llm_relationship_review_audit",
        "input_source": source,
        "input_path": None if input_path is None else str(input_path),
        "generated_at": generated_at.isoformat(),
        "client": {
            "model_id": active_client.model_id,
            "model_version": active_client.model_version,
            "mode": "stub_no_network",
        },
        "rows_seen": len(rows),
        "rows_reviewed": reviewed_count,
        "validation_error_count": validation_error_count,
        "manual_review_escalation_count": manual_review_escalation_count,
        "disclaimer": (
            "Saved-file audit only. LLM proposals cannot approve trades, assert same_payoff, "
            "emit EQUIVALENT, or change candidate actions."
        ),
    }
    return reviewed


def _relationship_rows(payload: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    source = payload.get("source")
    key = "pairs" if source == "live_snapshot_matcher" else "ledger"
    rows = payload.get(key)
    if not isinstance(rows, list):
        return []
    return [(index, row) for index, row in enumerate(rows) if isinstance(row, dict) and isinstance(row.get("contract_relationship"), dict)]


def _contract_relationship_from_report(payload: Mapping[str, Any]) -> ContractRelationship:
    return ContractRelationship(
        relationship=str(payload.get("relationship") or "AMBIGUOUS"),
        same_payoff=bool(payload.get("same_payoff")),
        confidence=_float_or_zero(payload.get("confidence")),
        blocking_reasons=tuple(str(reason) for reason in payload.get("blocking_reasons", []) if reason is not None)
        if isinstance(payload.get("blocking_reasons"), list)
        else (),
        manual_review_required=bool(payload.get("manual_review_required")),
        source=str(payload.get("source") or "deterministic_rules"),
    )


def _llm_input_payload(row: Mapping[str, Any], *, source: str, index: int) -> dict[str, Any]:
    return {
        "source": source,
        "row_index": index,
        "action": row.get("action"),
        "polymarket": row.get("polymarket"),
        "kalshi": row.get("kalshi"),
        "contract_relationship": row.get("contract_relationship"),
        "ineligibility_reasons": row.get("ineligibility_reasons"),
        "missed_fill_reason": row.get("missed_fill_reason"),
    }


def _load_json_report(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"input file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("input JSON is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("input JSON must be an object")
    return payload


def _markdown_summary(payload: Mapping[str, Any]) -> str:
    summary = payload.get("llm_relationship_review")
    summary = summary if isinstance(summary, Mapping) else {}
    lines = [
        "# LLM Relationship Review Audit",
        "",
        "Saved-file audit only. Deterministic relationship rules remain authoritative.",
        "",
        f"- input_source: {summary.get('input_source')}",
        f"- rows_seen: {summary.get('rows_seen', 0)}",
        f"- rows_reviewed: {summary.get('rows_reviewed', 0)}",
        f"- validation_error_count: {summary.get('validation_error_count', 0)}",
        f"- manual_review_escalation_count: {summary.get('manual_review_escalation_count', 0)}",
        "",
    ]
    return "\n".join(lines)


def _float_or_zero(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    if parsed != parsed:
        return 0.0
    return round(parsed, 6)
