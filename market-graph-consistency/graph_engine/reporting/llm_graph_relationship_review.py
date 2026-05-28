"""Offline LLM review workbench for graph relationship edges.

This module is the **prompt generator and schema validator** for human-in-
the-loop LLM review of RV-ingested graph relationship edges.  It never
calls an LLM API; it produces an offline prompt file plus a strict JSON
output schema that downstream operators paste into Claude / GPT, then
validates the LLM's JSON response with the validator command.

The validator enforces a strict diagnostic-only contract: the LLM is
permitted to *suggest* relationship_type / blockers / manual checks /
confidence, but it can never claim PAPER_CANDIDATE, executable=true, or
exact=true (unless deterministic evidence already says exact, which the
graph never asserts on its own).  Validated output cannot mutate
deterministic edges — that is reserved for a separate human-reviewed
apply command.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from graph_engine.relationships.rv_edge_taxonomy import (
    ALLOWED_EDGE_ACTIONS,
    CONFIDENCE_BUCKETS,
    RELATIONSHIP_VERSION,
    RV_RELATIONSHIP_TYPES,
    validate_rv_edge,
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
    "Offline graph relationship review prompt. Saved-file only. LLM output is "
    "ADVISORY ONLY: it may suggest a relationship_type, missing blockers, manual "
    "checks, and a confidence bucket. It MUST NOT claim evaluator-input "
    "promotion, execution-readiness, or exact-payoff equality. It MUST NOT "
    "lower any graph blocker."
)

ALLOWED_LLM_RELATIONSHIP_TYPES: tuple[str, ...] = tuple(sorted(RV_RELATIONSHIP_TYPES))
ALLOWED_LLM_ACTIONS: tuple[str, ...] = ALLOWED_EDGE_ACTIONS
ALLOWED_LLM_AGREEMENT: tuple[str, ...] = (
    "agree",
    "disagree_with_alternative",
    "downgrade_confidence",
    "insufficient_evidence",
)
ALLOWED_LLM_CONFIDENCE: tuple[str, ...] = CONFIDENCE_BUCKETS
LLM_VERSION = "llm-graph-relationship-review-schema-v1"


def write_llm_graph_relationship_review_assets(
    *,
    edges_report_path: Path | str,
    prompt_output: Path | str,
    schema_output: Path | str,
    sample_size: int = 50,
) -> dict[str, Any]:
    """Write a prompt Markdown file and a JSON schema for LLM output."""

    edges_path = Path(edges_report_path)
    if not edges_path.exists():
        edges_payload = {"edges": []}
    else:
        edges_payload = json.loads(edges_path.read_text(encoding="utf-8"))
    if not isinstance(edges_payload, dict):
        edges_payload = {"edges": []}
    sample = _select_sample(edges_payload.get("edges", []) or [], sample_size)
    prompt_markdown = _render_prompt_markdown(sample, edges_payload, sample_size)
    findings = find_prohibited_rendered_text(prompt_markdown)
    if findings:
        raise SchemaValidationError(
            "llm graph relationship review prompt Markdown contains prohibited vocabulary: "
            + ", ".join(findings)
        )
    schema = build_llm_graph_relationship_review_schema()
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
        "total_edges_seen": len(edges_payload.get("edges", []) or []),
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "allowed_llm_relationship_types": list(ALLOWED_LLM_RELATIONSHIP_TYPES),
        "allowed_llm_actions": list(ALLOWED_LLM_ACTIONS),
        "allowed_llm_agreement": list(ALLOWED_LLM_AGREEMENT),
        "allowed_llm_confidence": list(ALLOWED_LLM_CONFIDENCE),
        "llm_version": LLM_VERSION,
        "relationship_version": RELATIONSHIP_VERSION,
    }


def build_llm_graph_relationship_review_schema() -> dict[str, Any]:
    """Return the strict JSON-schema-like contract for LLM output."""

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "graph_engine.llm_graph_relationship_review.schema.json",
        "title": "LLM graph relationship review output (advisory-only)",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "diagnostic_only",
            "affects_evaluator_gates",
            "allowed_actions",
            "llm_version",
            "relationship_version",
            "reviewed_edges",
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
            "relationship_version": {"type": "string", "const": RELATIONSHIP_VERSION},
            "reviewer_id": {"type": ["string", "null"]},
            "reviewed_edges": {
                "type": "array",
                "items": {"$ref": "#/$defs/ReviewedEdge"},
            },
        },
        "$defs": {
            "ReviewedEdge": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "edge_id",
                    "diagnostic_only",
                    "affects_evaluator_gates",
                    "agreement",
                    "suggested_relationship_type",
                    "suggested_action",
                    "confidence_bucket",
                    "reviewer_notes",
                    "suggested_blockers",
                    "suggested_manual_checks",
                    "fake_edge_risks",
                    "exact_payoff_claim",
                    "can_create_evaluator_input_claim",
                ],
                "properties": {
                    "edge_id": {"type": "string", "minLength": 1},
                    "diagnostic_only": {"type": "boolean", "const": True},
                    "affects_evaluator_gates": {"type": "boolean", "const": False},
                    "agreement": {
                        "type": "string",
                        "enum": list(ALLOWED_LLM_AGREEMENT),
                    },
                    "suggested_relationship_type": {
                        "type": "string",
                        "enum": list(ALLOWED_LLM_RELATIONSHIP_TYPES),
                    },
                    "suggested_action": {
                        "type": "string",
                        "enum": list(ALLOWED_LLM_ACTIONS),
                    },
                    "confidence_bucket": {
                        "type": "string",
                        "enum": list(ALLOWED_LLM_CONFIDENCE),
                    },
                    "reviewer_notes": {"type": "string"},
                    "suggested_blockers": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "suggested_manual_checks": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "fake_edge_risks": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "exact_payoff_claim": {"type": "boolean", "const": False},
                    "can_create_evaluator_input_claim": {"type": "boolean", "const": False},
                },
            }
        },
    }


def validate_llm_graph_relationship_review_output(
    *,
    output_path: Path | str,
    schema_path: Path | str,
    edges_report_path: Path | str | None = None,
    json_output: Path | str | None = None,
) -> dict[str, Any]:
    """Validate an LLM output against the schema and the safety contract.

    Returns a structured validation report and (if requested) writes it
    to ``json_output``.  The output is considered REJECTED if any of
    these are present:

    - PAPER_CANDIDATE / executable=true claims
    - exact=true without deterministic exact evidence on the source edge
    - missing blockers
    - title-similarity used as equivalence
    - reference-only edges claiming executable counterpart
    """

    output_path = Path(output_path)
    schema_path = Path(schema_path)
    edges_payload: dict[str, Any] | None = None
    if edges_report_path is not None:
        edges_path_obj = Path(edges_report_path)
        if edges_path_obj.exists():
            try:
                payload = json.loads(edges_path_obj.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    edges_payload = payload
            except (OSError, ValueError):
                edges_payload = None

    schema = json.loads(schema_path.read_text(encoding="utf-8")) if schema_path.exists() else build_llm_graph_relationship_review_schema()
    raw_output: dict[str, Any]
    raw_output_error: str | None = None
    try:
        raw_output = json.loads(output_path.read_text(encoding="utf-8")) if output_path.exists() else {}
        if not isinstance(raw_output, dict):
            raw_output_error = "LLM output must be a JSON object"
            raw_output = {}
    except (OSError, ValueError) as exc:
        raw_output_error = f"could not read LLM output: {exc}"
        raw_output = {}

    accepted_rows: list[dict[str, Any]] = []
    rejected_rows: list[dict[str, Any]] = []
    structural_errors: list[str] = []
    if raw_output_error:
        structural_errors.append(raw_output_error)

    # Schema enforcement
    schema_errors = _schema_violations(raw_output, schema)
    structural_errors.extend(schema_errors)
    safety_findings = _find_prohibited_strings(raw_output)
    if safety_findings:
        structural_errors.append(
            "prohibited vocabulary in LLM output: " + ", ".join(sorted(safety_findings))
        )

    deterministic_edges_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(edges_payload, dict):
        for edge in edges_payload.get("edges") or []:
            if isinstance(edge, dict) and isinstance(edge.get("edge_id"), str):
                deterministic_edges_by_id[edge["edge_id"]] = edge

    reviewed = raw_output.get("reviewed_edges")
    if isinstance(reviewed, list):
        for index, row in enumerate(reviewed):
            row_errors = _row_violations(row, deterministic_edges_by_id, index)
            if row_errors:
                rejected_rows.append(
                    {
                        "row_index": index,
                        "edge_id": (row or {}).get("edge_id") if isinstance(row, dict) else None,
                        "errors": row_errors,
                        "diagnostic_only": True,
                        "affects_evaluator_gates": False,
                    }
                )
            else:
                accepted_rows.append(
                    {
                        "row_index": index,
                        "edge_id": row["edge_id"],
                        "agreement": row.get("agreement"),
                        "suggested_relationship_type": row.get("suggested_relationship_type"),
                        "suggested_action": row.get("suggested_action"),
                        "confidence_bucket": row.get("confidence_bucket"),
                        "suggested_blockers": list(row.get("suggested_blockers") or []),
                        "suggested_manual_checks": list(row.get("suggested_manual_checks") or []),
                        "fake_edge_risks": list(row.get("fake_edge_risks") or []),
                        "reviewer_notes": row.get("reviewer_notes"),
                        "diagnostic_only": True,
                        "affects_evaluator_gates": False,
                    }
                )

    is_acceptable = not structural_errors and not rejected_rows
    validation_status = "ACCEPTED" if is_acceptable else "REJECTED"

    validation_report = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "llm_version": raw_output.get("llm_version") if isinstance(raw_output, dict) else None,
        "relationship_version": RELATIONSHIP_VERSION,
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "validation_status": validation_status,
        "schema_path": str(schema_path),
        "output_path": str(output_path),
        "edges_report_path": str(edges_report_path) if edges_report_path is not None else None,
        "structural_errors": structural_errors,
        "rejected_rows": rejected_rows,
        "accepted_rows": accepted_rows,
        "summary": {
            "accepted_count": len(accepted_rows),
            "rejected_count": len(rejected_rows),
            "structural_error_count": len(structural_errors),
        },
        "note_to_operator": (
            "LLM output is advisory only. Validated rows may add reviewer notes, "
            "suggested blockers/manual checks, suggested relationship type, and a "
            "confidence bucket. They cannot mutate deterministic graph edges until "
            "a separate human-reviewed apply command runs."
        ),
    }
    if json_output is not None:
        out_path = Path(json_output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(validation_report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return validation_report


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _render_prompt_markdown(sample: list[dict[str, Any]], full_payload: dict[str, Any], sample_size: int) -> str:
    schema = build_llm_graph_relationship_review_schema()
    total_edges = full_payload.get("summary", {}).get(
        "total_edges", len(full_payload.get("edges") or [])
    )
    sample_count_label = (
        f"{len(sample)} of {sample_size} requested (total edges seen: {total_edges})"
    )
    lines = [
        "# Graph Relationship Review Prompt",
        "",
        PROMPT_BANNER,
        "",
        f"- Generated at: `{datetime.now(timezone.utc).isoformat()}`",
        f"- Sample count: {sample_count_label}",
        f"- Relationship version: `{RELATIONSHIP_VERSION}`",
        f"- LLM schema version: `{LLM_VERSION}`",
        "",
        "## What you may do",
        "",
        "- Suggest a relationship_type from the allowed taxonomy.",
        "- Suggest extra blockers the graph may have missed.",
        "- Suggest manual checks (settlement-source URL, time zone, fee model).",
        "- Suggest a confidence bucket (`low`/`medium`/`high`).",
        "- Flag fake-edge risks (title similarity, deadline-touch vs PIT, range vs threshold, etc.).",
        "",
        "## What you must NEVER do",
        "",
        "- NEVER promote an edge to an evaluator-input claim.",
        "- NEVER claim execution-readiness or any equivalent statement.",
        "- NEVER claim exact-payoff equality; the graph never proves equality.",
        "- NEVER drop or weaken the blockers the deterministic edge already carries.",
        "- NEVER recommend transacting, broker-instruction submission, or account / credential operations.",
        "- NEVER treat title similarity as settlement equivalence.",
        "- NEVER treat deadline-touch as point-in-time.",
        "- NEVER treat reference-only sources as execution-capable counterparts.",
        "",
        "## Allowed relationship types",
        "",
    ]
    for rt in ALLOWED_LLM_RELATIONSHIP_TYPES:
        lines.append(f"- `{rt}`")
    lines.extend([
        "",
        "## Allowed actions",
        "",
    ])
    for action in ALLOWED_LLM_ACTIONS:
        lines.append(f"- `{action}`")
    lines.extend([
        "",
        "## Output JSON schema",
        "",
        "```json",
        json.dumps(schema, indent=2, sort_keys=True),
        "```",
        "",
        "## Edges to review",
        "",
        "Each edge below is rendered as JSON.  Reply with a single JSON object that",
        "matches the schema above; one entry in `reviewed_edges` per edge_id.",
        "",
    ])
    for edge in sample:
        bounded = _bounded_edge_view(edge)
        lines.append("```json")
        lines.append(json.dumps(_sanitize_for_prompt(bounded), indent=2, sort_keys=True))
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def _bounded_edge_view(edge: dict[str, Any]) -> dict[str, Any]:
    return {
        "edge_id": edge["edge_id"],
        "relationship_type": edge["relationship_type"],
        "relationship_family": edge["relationship_family"],
        "left_market_id": edge["left_market_id"],
        "right_market_id": edge.get("right_market_id"),
        "right_reference_id": edge.get("right_reference_id"),
        "left_venue": edge["left_venue"],
        "right_venue": edge["right_venue"],
        "action": edge["action"],
        "confidence_bucket": edge["confidence_bucket"],
        "evidence_fields": edge.get("evidence_fields", {}),
        "blockers": list(edge.get("blockers", [])),
        "rationale": edge.get("rationale", ""),
        "source_report_paths": list(edge.get("source_report_paths", [])),
    }


def _sanitize_for_prompt(value: Any) -> Any:
    """Strip prohibited tokens from external title text used in the prompt.

    External market titles are passed through verbatim from RV diagnostics
    and may legitimately contain words like ``order`` or ``size``. The
    central safety vocabulary catches those words in rendered Markdown,
    so the prompt renderer redacts them on the way out. Field semantics
    are preserved (the LLM still sees the rest of the title).
    """

    if isinstance(value, dict):
        return {key: _sanitize_for_prompt(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_sanitize_for_prompt(item) for item in value]
    if isinstance(value, str):
        return _redact_prohibited_words(value)
    return value


def _redact_prohibited_words(value: str) -> str:
    import re

    redacted = value
    for token in PROHIBITED_REPORT_TOKENS:
        redacted = re.sub(rf"(?i)\b{re.escape(token)}\b", "[redacted]", redacted)
    for phrase in PROHIBITED_REPORT_PHRASES:
        redacted = redacted.replace(phrase, "[redacted]")
        redacted = redacted.replace(phrase.replace("_", "-"), "[redacted]")
        redacted = redacted.replace(phrase.upper(), "[REDACTED]")
    return redacted


def _select_sample(edges: list[Any], sample_size: int) -> list[dict[str, Any]]:
    if sample_size <= 0:
        return []
    rows = [edge for edge in edges if isinstance(edge, dict)]
    # Stable, deterministic sampling: sort by family priority + edge_id so
    # the prompt stays reproducible across runs.
    family_priority = {
        "near_exact_review": 0,
        "basis_risk": 1,
        "structural": 2,
        "reference_only": 3,
        "weak_signal": 4,
    }
    rows.sort(
        key=lambda edge: (
            family_priority.get(edge.get("relationship_family", "weak_signal"), 9),
            edge.get("relationship_type", ""),
            edge.get("edge_id", ""),
        )
    )
    return rows[:sample_size]


def _schema_violations(payload: Any, schema: dict[str, Any]) -> list[str]:
    """Subset JSON-schema-like enforcer for the bounded shape we emit."""

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
            required = spec.get("required", [])
            for key in required:
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
    deterministic_edges_by_id: dict[str, dict[str, Any]],
    index: int,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(row, dict):
        return [f"reviewed_edges[{index}] must be an object"]
    edge_id = row.get("edge_id")
    if not isinstance(edge_id, str) or not edge_id:
        return [f"reviewed_edges[{index}].edge_id must be a non-empty string"]
    deterministic = deterministic_edges_by_id.get(edge_id)
    suggested = row.get("suggested_relationship_type")
    suggested_blockers = row.get("suggested_blockers") or []
    if not isinstance(suggested_blockers, list):
        suggested_blockers = []
    suggested_action = row.get("suggested_action")
    confidence = row.get("confidence_bucket")

    # No paper/executable/exact claims (defence-in-depth on top of schema).
    if row.get("exact_payoff_claim") is not False:
        errors.append(f"reviewed_edges[{index}]: exact_payoff_claim must be false")
    if row.get("can_create_evaluator_input_claim") is not False:
        errors.append(f"reviewed_edges[{index}]: can_create_evaluator_input_claim must be false")
    for value in [
        row.get("reviewer_notes"),
        row.get("suggested_relationship_type"),
        row.get("suggested_action"),
    ] + list(suggested_blockers) + list(row.get("suggested_manual_checks") or []) + list(row.get("fake_edge_risks") or []):
        if isinstance(value, str) and contains_prohibited_report_token(value):
            errors.append(
                f"reviewed_edges[{index}]: prohibited vocabulary in field value {value!r}"
            )

    if suggested in {"SAME_PAYOFF_CANDIDATE_REVIEW"} and not deterministic:
        errors.append(
            f"reviewed_edges[{index}]: LLM cannot upgrade an unknown edge to near-exact review"
        )

    if deterministic is not None:
        det_blockers = set(deterministic.get("blockers", []))
        suggested_blocker_set = set(suggested_blockers)
        # The LLM may add blockers but it cannot drop any.
        missing = det_blockers - suggested_blocker_set
        # We only flag a drop if the LLM explicitly returned a non-empty
        # blockers list; an empty list means "no additions", which is fine.
        if suggested_blockers and missing:
            errors.append(
                f"reviewed_edges[{index}]: LLM cannot drop deterministic blockers {sorted(missing)!r}"
            )
        if deterministic.get("relationship_family") == "reference_only" and suggested in {
            "SAME_PAYOFF_CANDIDATE_REVIEW",
            "SAME_EVENT_SAME_THRESHOLD_REVIEW",
        }:
            errors.append(
                f"reviewed_edges[{index}]: reference-only edges cannot be upgraded to near-exact review"
            )
        if deterministic.get("relationship_type") == "TITLE_SIMILARITY_ONLY" and confidence != "low":
            errors.append(
                f"reviewed_edges[{index}]: title-similarity edge cannot be upgraded above low confidence"
            )

    if isinstance(suggested_action, str) and suggested_action.upper() == "PAPER_CANDIDATE":
        errors.append(f"reviewed_edges[{index}]: PAPER_CANDIDATE is not an allowed action")
    return errors


def _find_prohibited_strings(payload: Any) -> set[str]:
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
    "ALLOWED_LLM_ACTIONS",
    "ALLOWED_LLM_AGREEMENT",
    "ALLOWED_LLM_CONFIDENCE",
    "ALLOWED_LLM_RELATIONSHIP_TYPES",
    "LLM_VERSION",
    "PROMPT_BANNER",
    "build_llm_graph_relationship_review_schema",
    "validate_llm_graph_relationship_review_output",
    "write_llm_graph_relationship_review_assets",
]
