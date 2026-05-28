"""Offline LLM review workbench for the graph manual-evidence inventory.

This module mirrors ``llm_graph_relationship_review`` but operates on
the manual-evidence inventory: it produces a bounded prompt and a strict
JSON schema for an LLM to suggest missing manual evidence, blockers, and
relationship-type corrections — *without* ever upgrading a row to a
paper / executable / exact claim.

The validator enforces:

- ``PAPER_CANDIDATE`` rejection.
- ``executable=true`` rejection.
- ``exact=true`` rejection.
- no-blockers rejection.
- title-similarity treated as equivalence rejection.
- reference-only treated as executable rejection.
- any attempt to "clear" a deterministic blocker rejection.
- any direct trade instruction rejection.

LLM output may only contain: ``suggested_blockers``,
``suggested_manual_checks``, ``suggested_relationship_type``,
``reviewer_notes``, ``confidence_bucket``, and the
``do_not_use_for_exact_gate`` flag — which must be ``true`` for every
reviewed row.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from graph_engine.relationships.rv_edge_taxonomy import (
    ALLOWED_EDGE_ACTIONS,
    CONFIDENCE_BUCKETS,
    RELATIONSHIP_VERSION,
)
from graph_engine.reporting.manual_relationship_evidence import (
    EVIDENCE_VERSION,
    MANUAL_RELATIONSHIP_TYPES,
)
from graph_engine.reporting.safety import (
    PROHIBITED_REPORT_PHRASES,
    PROHIBITED_REPORT_TOKENS,
    contains_prohibited_report_token,
    find_prohibited_rendered_text,
)
from graph_engine.reporting.schema_validation import (
    DIAGNOSTIC_HINT_ACTIONS,
    SchemaValidationError,
)


PROMPT_BANNER = (
    "Offline graph manual-evidence review prompt. Saved-file only. LLM output is "
    "ADVISORY ONLY: it may suggest missing blockers, manual checks, a relationship-"
    "type correction, and a confidence bucket. It MUST NOT claim evaluator-input "
    "promotion, execution-readiness, or exact-payoff equality. It MUST NOT lower "
    "any blocker the deterministic record already carries."
)

ALLOWED_LLM_RELATIONSHIP_TYPES: tuple[str, ...] = tuple(sorted(MANUAL_RELATIONSHIP_TYPES))
ALLOWED_LLM_CONFIDENCE: tuple[str, ...] = CONFIDENCE_BUCKETS
LLM_VERSION = "llm-graph-manual-evidence-review-schema-v1"


def write_llm_graph_manual_evidence_review_assets(
    *,
    relationships_path: Path | str,
    prompt_output: Path | str,
    schema_output: Path | str,
    sample_size: int = 50,
) -> dict[str, Any]:
    relationships_path = Path(relationships_path)
    if relationships_path.exists():
        payload = json.loads(relationships_path.read_text(encoding="utf-8"))
    else:
        payload = {"records": []}
    if not isinstance(payload, dict):
        payload = {"records": []}
    records = [record for record in payload.get("records", []) if isinstance(record, dict)]
    sample = _select_sample(records, sample_size)
    prompt_markdown = _render_prompt_markdown(sample, payload, sample_size)
    findings = find_prohibited_rendered_text(prompt_markdown)
    if findings:
        raise SchemaValidationError(
            "llm manual-evidence review prompt Markdown contains prohibited vocabulary: "
            + ", ".join(findings)
        )
    schema = build_llm_graph_manual_evidence_review_schema()
    prompt_path = Path(prompt_output)
    schema_path = Path(schema_output)
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    schema_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt_markdown, encoding="utf-8")
    schema_path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "prompt_path": str(prompt_path),
        "schema_path": str(schema_path),
        "sample_size_requested": sample_size,
        "sample_size_actual": len(sample),
        "total_records": len(records),
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "evidence_version": EVIDENCE_VERSION,
        "relationship_version": RELATIONSHIP_VERSION,
        "llm_version": LLM_VERSION,
    }


def build_llm_graph_manual_evidence_review_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "graph_engine.llm_graph_manual_evidence_review.schema.json",
        "title": "LLM graph manual-evidence review output (advisory-only)",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "diagnostic_only",
            "affects_evaluator_gates",
            "allowed_actions",
            "llm_version",
            "evidence_version",
            "relationship_version",
            "reviewed_records",
        ],
        "properties": {
            "diagnostic_only": {"type": "boolean", "const": True},
            "affects_evaluator_gates": {"type": "boolean", "const": False},
            "allowed_actions": {
                "type": "array",
                "items": {"type": "string", "enum": list(DIAGNOSTIC_HINT_ACTIONS)},
                "minItems": len(DIAGNOSTIC_HINT_ACTIONS),
                "maxItems": len(DIAGNOSTIC_HINT_ACTIONS),
                "uniqueItems": True,
            },
            "llm_version": {"type": "string", "const": LLM_VERSION},
            "evidence_version": {"type": "string", "const": EVIDENCE_VERSION},
            "relationship_version": {"type": "string", "const": RELATIONSHIP_VERSION},
            "reviewer_id": {"type": ["string", "null"]},
            "reviewed_records": {
                "type": "array",
                "items": {"$ref": "#/$defs/ReviewedRecord"},
            },
        },
        "$defs": {
            "ReviewedRecord": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "relationship_id",
                    "diagnostic_only",
                    "affects_evaluator_gates",
                    "suggested_relationship_type",
                    "suggested_blockers",
                    "suggested_manual_checks",
                    "reviewer_notes",
                    "confidence_bucket",
                    "do_not_use_for_exact_gate",
                ],
                "properties": {
                    "relationship_id": {"type": "string", "minLength": 1},
                    "diagnostic_only": {"type": "boolean", "const": True},
                    "affects_evaluator_gates": {"type": "boolean", "const": False},
                    "suggested_relationship_type": {
                        "type": "string",
                        "enum": list(ALLOWED_LLM_RELATIONSHIP_TYPES),
                    },
                    "suggested_blockers": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "suggested_manual_checks": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "reviewer_notes": {"type": "string"},
                    "confidence_bucket": {
                        "type": "string",
                        "enum": list(ALLOWED_LLM_CONFIDENCE),
                    },
                    "do_not_use_for_exact_gate": {"type": "boolean", "const": True},
                },
            }
        },
    }


def validate_llm_graph_manual_evidence_review_output(
    *,
    output_path: Path | str,
    schema_path: Path | str,
    relationships_path: Path | str | None = None,
    json_output: Path | str | None = None,
) -> dict[str, Any]:
    output_path = Path(output_path)
    schema_path = Path(schema_path)
    schema = (
        json.loads(schema_path.read_text(encoding="utf-8"))
        if schema_path.exists()
        else build_llm_graph_manual_evidence_review_schema()
    )
    raw_error: str | None = None
    raw: dict[str, Any]
    try:
        raw = json.loads(output_path.read_text(encoding="utf-8")) if output_path.exists() else {}
        if not isinstance(raw, dict):
            raw_error = "LLM output must be a JSON object"
            raw = {}
    except (OSError, ValueError) as exc:
        raw_error = f"could not read LLM output: {exc}"
        raw = {}

    deterministic: dict[str, dict[str, Any]] = {}
    if relationships_path is not None:
        path = Path(relationships_path)
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    for record in payload.get("records") or []:
                        if isinstance(record, dict) and isinstance(record.get("relationship_id"), str):
                            deterministic[record["relationship_id"]] = record
            except (OSError, ValueError):
                deterministic = {}

    structural_errors: list[str] = []
    if raw_error:
        structural_errors.append(raw_error)
    structural_errors.extend(_schema_violations(raw, schema))
    flagged = _find_prohibited(raw)
    if flagged:
        structural_errors.append(
            "prohibited vocabulary in LLM output: " + ", ".join(sorted(flagged))
        )

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    reviewed = raw.get("reviewed_records")
    if isinstance(reviewed, list):
        for index, row in enumerate(reviewed):
            errors = _row_violations(row, deterministic, index)
            if errors:
                rejected.append(
                    {
                        "row_index": index,
                        "relationship_id": (row or {}).get("relationship_id") if isinstance(row, dict) else None,
                        "errors": errors,
                        "diagnostic_only": True,
                        "affects_evaluator_gates": False,
                    }
                )
            else:
                accepted.append(
                    {
                        "row_index": index,
                        "relationship_id": row["relationship_id"],
                        "suggested_relationship_type": row["suggested_relationship_type"],
                        "suggested_blockers": list(row["suggested_blockers"]),
                        "suggested_manual_checks": list(row["suggested_manual_checks"]),
                        "reviewer_notes": row.get("reviewer_notes"),
                        "confidence_bucket": row["confidence_bucket"],
                        "do_not_use_for_exact_gate": row["do_not_use_for_exact_gate"],
                        "diagnostic_only": True,
                        "affects_evaluator_gates": False,
                    }
                )

    is_acceptable = not structural_errors and not rejected
    report = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "llm_version": raw.get("llm_version") if isinstance(raw, dict) else None,
        "evidence_version": EVIDENCE_VERSION,
        "relationship_version": RELATIONSHIP_VERSION,
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "validation_status": "ACCEPTED" if is_acceptable else "REJECTED",
        "schema_path": str(schema_path),
        "output_path": str(output_path),
        "relationships_path": str(relationships_path) if relationships_path else None,
        "structural_errors": structural_errors,
        "accepted_rows": accepted,
        "rejected_rows": rejected,
        "summary": {
            "accepted_count": len(accepted),
            "rejected_count": len(rejected),
            "structural_error_count": len(structural_errors),
        },
        "note_to_operator": (
            "Validated rows are advisory only. They cannot promote a graph "
            "record beyond review and cannot mutate deterministic blockers."
        ),
    }
    if json_output is not None:
        out_path = Path(json_output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _render_prompt_markdown(
    sample: list[dict[str, Any]],
    payload: dict[str, Any],
    sample_size: int,
) -> str:
    schema = build_llm_graph_manual_evidence_review_schema()
    total_records = (
        payload.get("summary", {}).get("total_records")
        if isinstance(payload.get("summary"), dict)
        else len(payload.get("records") or [])
    )
    lines = [
        "# Graph Manual Evidence Review Prompt",
        "",
        PROMPT_BANNER,
        "",
        f"- Generated at: `{datetime.now(timezone.utc).isoformat()}`",
        f"- Sample count: {len(sample)} of {sample_size} requested (total records seen: {total_records})",
        f"- Evidence version: `{EVIDENCE_VERSION}`",
        f"- LLM schema version: `{LLM_VERSION}`",
        "",
        "## What you may do",
        "",
        "- Identify missing manual evidence (settlement source URL, time zone, fee model, payoff shape).",
        "- Flag fake-edge risks (title similarity, deadline-touch vs PIT, range vs threshold).",
        "- Suggest a relationship-type correction from the allowed taxonomy.",
        "- Suggest extra manual checks (URLs to open, text to capture).",
        "- Choose a confidence bucket (`low`/`medium`/`high`).",
        "- Always set `do_not_use_for_exact_gate=true` for every reviewed row.",
        "",
        "## What you must NEVER do",
        "",
        "- NEVER promote a row to an evaluator-input claim.",
        "- NEVER claim execution-readiness or any equivalent statement.",
        "- NEVER claim exact-payoff equality.",
        "- NEVER drop or weaken the blockers the deterministic record already carries.",
        "- NEVER recommend transacting, broker-instruction submission, or account / credential operations.",
        "- NEVER treat title similarity as settlement equivalence.",
        "- NEVER treat reference-only sources as execution-capable counterparts.",
        "",
        "## Allowed relationship types",
        "",
    ]
    for rt in ALLOWED_LLM_RELATIONSHIP_TYPES:
        lines.append(f"- `{rt}`")
    lines.extend(["", "## Output JSON schema", "", "```json", json.dumps(schema, indent=2, sort_keys=True), "```", ""])
    lines.extend(["## Records to review", "", "Each record is rendered as JSON. Reply with one entry in `reviewed_records` per relationship_id.", ""])
    for record in sample:
        bounded = _bounded_record_view(record)
        lines.append("```json")
        lines.append(json.dumps(_sanitize_for_prompt(bounded), indent=2, sort_keys=True))
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def _bounded_record_view(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "relationship_id": record.get("relationship_id"),
        "vertical": record.get("vertical"),
        "family": record.get("family"),
        "relationship_type": record.get("relationship_type"),
        "left_market_or_source": record.get("left_market_or_source"),
        "right_market_or_source": record.get("right_market_or_source"),
        "venues": record.get("venues"),
        "payoff_shape_left": record.get("payoff_shape_left"),
        "payoff_shape_right": record.get("payoff_shape_right"),
        "why_related": record.get("why_related"),
        "why_not_exact": record.get("why_not_exact"),
        "blockers": list(record.get("blockers") or []),
        "manual_evidence_needed": list(record.get("manual_evidence_needed") or []),
        "current_action": record.get("current_action"),
        "can_go_to_relative_value_now": record.get("can_go_to_relative_value_now"),
        "source_reports": list(record.get("source_reports") or []),
    }


def _sanitize_for_prompt(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sanitize_for_prompt(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_sanitize_for_prompt(item) for item in value]
    if isinstance(value, str):
        return _redact(value)
    return value


def _redact(value: str) -> str:
    redacted = value
    for token in PROHIBITED_REPORT_TOKENS:
        redacted = re.sub(rf"(?i)\b{re.escape(token)}\b", "[redacted]", redacted)
    for phrase in PROHIBITED_REPORT_PHRASES:
        redacted = redacted.replace(phrase, "[redacted]")
        redacted = redacted.replace(phrase.replace("_", "-"), "[redacted]")
        redacted = redacted.replace(phrase.upper(), "[REDACTED]")
    return redacted


def _select_sample(records: list[dict[str, Any]], sample_size: int) -> list[dict[str, Any]]:
    if sample_size <= 0 or not records:
        return []
    priority = {"crypto": 0, "economics": 1, "sports": 2, "structural": 3}
    sorted_records = sorted(
        records,
        key=lambda r: (
            priority.get(r.get("vertical") or "structural", 9),
            r.get("relationship_type") or "",
            r.get("relationship_id") or "",
        ),
    )
    return sorted_records[:sample_size]


def _schema_violations(payload: Any, schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    def _check(value: Any, spec: dict[str, Any], path: str) -> None:
        if "$ref" in spec:
            ref = spec["$ref"]
            if ref.startswith("#/$defs/"):
                target = schema.get("$defs", {}).get(ref.split("/")[-1])
                if isinstance(target, dict):
                    _check(value, target, path)
            return
        expected_type = spec.get("type")
        if expected_type == "object" and not isinstance(value, dict):
            errors.append(f"{path or '$'}: expected object")
            return
        if expected_type == "array" and not isinstance(value, list):
            errors.append(f"{path or '$'}: expected array")
            return
        if expected_type == "string" and not isinstance(value, str):
            errors.append(f"{path or '$'}: expected string")
            return
        if expected_type == "boolean" and not isinstance(value, bool):
            errors.append(f"{path or '$'}: expected boolean")
            return
        if "const" in spec and value != spec["const"]:
            errors.append(f"{path or '$'}: expected const {spec['const']!r}")
        if "enum" in spec and value not in spec["enum"]:
            errors.append(f"{path or '$'}: expected one of {spec['enum']!r}")
        if isinstance(value, dict):
            properties = spec.get("properties", {})
            for key in spec.get("required", []):
                if key not in value:
                    errors.append(f"{path or '$'}: missing required property {key!r}")
            if spec.get("additionalProperties") is False:
                for key in value:
                    if key not in properties:
                        errors.append(f"{path or '$'}: unexpected property {key!r}")
            for key, nested_value in value.items():
                if key in properties:
                    _check(nested_value, properties[key], f"{path}.{key}" if path else key)
        if isinstance(value, list):
            items_spec = spec.get("items")
            if isinstance(items_spec, dict):
                for index, item in enumerate(value):
                    _check(item, items_spec, f"{path}[{index}]")

    _check(payload, schema, "")
    return errors


def _row_violations(
    row: Any,
    deterministic: dict[str, dict[str, Any]],
    index: int,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(row, dict):
        return [f"reviewed_records[{index}] must be an object"]
    rel_id = row.get("relationship_id")
    if not isinstance(rel_id, str) or not rel_id:
        return [f"reviewed_records[{index}].relationship_id must be a non-empty string"]
    if row.get("do_not_use_for_exact_gate") is not True:
        errors.append(f"reviewed_records[{index}].do_not_use_for_exact_gate must be true")
    suggested = row.get("suggested_relationship_type")
    suggested_blockers = row.get("suggested_blockers") or []
    if not isinstance(suggested_blockers, list):
        suggested_blockers = []
    if not suggested_blockers:
        errors.append(f"reviewed_records[{index}].suggested_blockers must not be empty")
    for text in (
        row.get("reviewer_notes"),
        row.get("suggested_relationship_type"),
    ) + tuple(row.get("suggested_manual_checks") or []) + tuple(suggested_blockers):
        if isinstance(text, str) and contains_prohibited_report_token(text):
            errors.append(f"reviewed_records[{index}]: prohibited vocabulary in field value {text!r}")
    deterministic_record = deterministic.get(rel_id)
    if deterministic_record is not None:
        det_blockers = set(deterministic_record.get("blockers") or [])
        if suggested_blockers and det_blockers - set(suggested_blockers):
            errors.append(
                f"reviewed_records[{index}]: cannot drop deterministic blockers {sorted(det_blockers - set(suggested_blockers))!r}"
            )
        if deterministic_record.get("family") == "weak_signal" and row.get("confidence_bucket") != "low":
            errors.append(
                f"reviewed_records[{index}]: weak-signal record cannot be upgraded above low confidence"
            )
        if deterministic_record.get("family") == "reference_anchor" and suggested in {
            "SAME_PAYOFF_CANDIDATE_REVIEW",
            "SAME_EVENT_SAME_THRESHOLD_REVIEW",
            "SAME_EVENT_DIFFERENT_SOURCE_REVIEW",
        }:
            errors.append(
                f"reviewed_records[{index}]: reference-only record cannot be upgraded to near-exact review"
            )
    return errors


def _find_prohibited(payload: Any) -> set[str]:
    findings: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if isinstance(key, str) and contains_prohibited_report_token(key):
                    findings.add(key)
                walk(nested)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str) and contains_prohibited_report_token(value):
            findings.add(value)

    walk(payload)
    return findings


__all__ = [
    "ALLOWED_LLM_CONFIDENCE",
    "ALLOWED_LLM_RELATIONSHIP_TYPES",
    "LLM_VERSION",
    "PROMPT_BANNER",
    "build_llm_graph_manual_evidence_review_schema",
    "validate_llm_graph_manual_evidence_review_output",
    "write_llm_graph_manual_evidence_review_assets",
]
