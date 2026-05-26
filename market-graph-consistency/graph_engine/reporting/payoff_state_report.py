"""Diagnostic JSON+Markdown report for finite-state payoff diagnostics.

The report aggregates the output of
:func:`graph_engine.payoff_state.compile_payoff_families` and
:func:`graph_engine.payoff_state_feasibility.check_no_arb_consistency` into a
single saved-file artefact.  Every row of the report is diagnostic only and
capped at ``WATCH`` or ``MANUAL_REVIEW``.

The report contract enforces the project safety guardrails:

* ``diagnostic_only`` must be ``true``.
* ``affects_evaluator_gates`` must be ``false``.
* ``allowed_actions`` must equal ``["WATCH", "MANUAL_REVIEW"]``.
* Prohibited tokens listed in
  :data:`graph_engine.reporting.safety.PROHIBITED_REPORT_TOKENS` must not
  appear in any key or value.
* Graph hints in this report are NOT exact-same-payoff evidence -- the
  validator rejects any attempt to claim equality.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from graph_engine.models import GraphSnapshot
from graph_engine.payoff_state import (
    ALLOWED_ACTIONS,
    SUPPORTED_FAMILY_TYPES,
    PayoffMatrix,
    compile_payoff_families,
)
from graph_engine.payoff_state_feasibility import (
    FEASIBILITY_STATUSES,
    FeasibilityResult,
    check_no_arb_consistency,
)
from graph_engine.state_family_registry import explain_payoff_state_diagnostic
from graph_engine.reporting.safety import find_prohibited_rendered_text
from graph_engine.reporting.schema_validation import (
    DIAGNOSTIC_HINT_ACTIONS,
    SchemaValidationError,
    _reject_prohibited_tokens,
)


BANNER = (
    "Diagnostic-only finite-state payoff consistency report. "
    "Outputs are capped at WATCH and MANUAL_REVIEW. Graph hints in this report are NOT "
    "same-payoff equality evidence and may NOT be used as evaluator gate input."
)
PROHIBITED_DIAGNOSTIC_KEYS = {
    "paper_candidate",
    "possible_arb",
    "executable",
    "executable_arb",
    "trade_permission",
    "trusted_relationship",
    "profit",
    "profit_usd",
    "pnl",
    "size",
    "size_usd",
    "fill",
    "fill_size",
    "order",
    "evaluator_ready",
    "exact_same_payoff",
}


def build_payoff_state_diagnostics_report(snapshot: GraphSnapshot) -> dict[str, Any]:
    families = compile_payoff_families(snapshot)
    diagnostics: list[dict[str, Any]] = []
    for matrix in families:
        result = check_no_arb_consistency(matrix)
        diagnostics.append(_assemble_diagnostic_row(snapshot, matrix, result))

    diagnostics = sorted(
        diagnostics,
        key=lambda item: (
            _priority(item["max_action_cap"]),
            -item["normalized_bound_gap"],
            item["family_id"],
        ),
    )
    for index, item in enumerate(diagnostics, start=1):
        item["diagnostic_rank"] = index

    feasibility_counts = Counter(item["feasibility_status"] for item in diagnostics)
    family_type_counts = Counter(item["family_type"] for item in diagnostics)
    action_counts = Counter(item["max_action_cap"] for item in diagnostics)
    report = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ALLOWED_ACTIONS,
        "banner": BANNER,
        "snapshot_id": snapshot.snapshot_id,
        "family_count": len(diagnostics),
        "counts_by_feasibility_status": dict(sorted(feasibility_counts.items())),
        "counts_by_family_type": dict(sorted(family_type_counts.items())),
        "counts_by_action_cap": dict(sorted(action_counts.items())),
        "payoff_state_diagnostics": diagnostics,
    }
    validate_payoff_state_diagnostics_report(report)
    return report


def write_payoff_state_diagnostics_report(
    snapshot: GraphSnapshot,
    json_output: Path | str,
    md_output: Path | str,
) -> dict[str, Any]:
    report = build_payoff_state_diagnostics_report(snapshot)
    validate_payoff_state_diagnostics_report(report)
    markdown = render_payoff_state_diagnostics_markdown(report)
    _reject_prohibited_rendered_markdown(markdown)
    json_path = Path(json_output)
    md_path = Path(md_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")
    return report


def _reject_prohibited_rendered_markdown(markdown: str) -> None:
    """Validate that rendered Markdown contains no unsafe vocabulary."""

    hits = find_prohibited_rendered_text(markdown)
    if hits:
        raise SchemaValidationError(
            "payoff-state Markdown contains prohibited diagnostic vocabulary: "
            + ", ".join(hits)
        )


def validate_payoff_state_diagnostics_report(report: dict[str, Any]) -> None:
    _reject_prohibited_tokens(report)
    if report.get("diagnostic_only") is not True:
        raise SchemaValidationError("payoff state diagnostics must be diagnostic_only")
    if report.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError("payoff state diagnostics must not affect evaluator gates")
    if report.get("allowed_actions") != ALLOWED_ACTIONS:
        raise SchemaValidationError("payoff state diagnostics actions must be WATCH and MANUAL_REVIEW only")
    diagnostics = report.get("payoff_state_diagnostics")
    if not isinstance(diagnostics, list):
        raise SchemaValidationError("payoff_state_diagnostics must be a list")
    if report.get("family_count") != len(diagnostics):
        raise SchemaValidationError("family_count must match payoff_state_diagnostics length")
    for index, diagnostic in enumerate(diagnostics):
        _validate_diagnostic(diagnostic, f"payoff_state_diagnostics[{index}]")


def render_payoff_state_diagnostics_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = [
        "# Finite-State Payoff Diagnostics",
        "",
        report.get("banner", BANNER),
        "",
        f"- Snapshot: `{report.get('snapshot_id', 'unknown')}`",
        f"- Diagnostic only: `{str(report['diagnostic_only']).lower()}`",
        f"- Affects evaluator gates: `{str(report['affects_evaluator_gates']).lower()}`",
        f"- Allowed actions: `{', '.join(report['allowed_actions'])}`",
        f"- Family count: {report['family_count']}",
        "",
        "Graph hints in this report are NOT same-payoff equality evidence and may "
        "NOT be used as evaluator gate input.",
        "",
        "## Counts",
        "",
        f"- By feasibility status: {_counts_inline(report['counts_by_feasibility_status'])}",
        f"- By family type: {_counts_inline(report['counts_by_family_type'])}",
        f"- By action cap: {_counts_inline(report['counts_by_action_cap'])}",
        "",
        "## Diagnostics",
        "",
        "| Rank | Family | Type | Status | Cap | States | Contracts | Bound Gap | Violated | Blockers |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in report["payoff_state_diagnostics"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item["diagnostic_rank"]),
                    _md(item["family_id"]),
                    _md(item["family_type"]),
                    _md(item["feasibility_status"]),
                    _md(item["max_action_cap"]),
                    _md(item["state_count"]),
                    _md(item["contract_count"]),
                    _md(item["bound_gap"]),
                    _md(", ".join(item["violated_constraints"]) or "none"),
                    _md(", ".join(item["blockers"]) or "none"),
                ]
            )
            + " |"
        )
    lines.append("")
    for item in report["payoff_state_diagnostics"]:
        lines.extend(_render_family_details(item))
    return "\n".join(lines)


def _render_family_details(item: dict[str, Any]) -> list[str]:
    lines = [
        f"### `{item['diagnostic_id']}`",
        "",
        f"- Family type: `{item['family_type']}`",
        f"- Family description: {item['family_description']}",
        f"- Feasibility status: `{item['feasibility_status']}`",
        f"- Max action cap: `{item['max_action_cap']}` (priority `{item['diagnostic_priority']}`)",
        f"- Bound gap: {item['bound_gap']:.4f}",
        f"- Normalized bound gap: {item['normalized_bound_gap']:.4f}",
        f"- Probability input mode: `{item['probability_input_mode']}`",
        f"- Bound gap semantics: {item['bound_gap_semantics']}",
        f"- Violated constraints: {', '.join(item['violated_constraints']) if item['violated_constraints'] else 'none'}",
        f"- Blockers: {', '.join(item['blockers']) if item['blockers'] else 'none'}",
        f"- Confidence basis: {item['confidence_basis']['description']} ({item['confidence_basis']['score']:.3f})",
        "",
        "Finite states:",
    ]
    for state in item["states"]:
        lines.append(
            f"- `{state['state_id']}` — {state['state_description']}"
            f" (exhaustive={state['exhaustive_membership']}, mutually_exclusive={state['mutual_exclusion_membership']})"
        )
    lines.append("")
    lines.append("Contracts:")
    for contract in item["contracts"]:
        prob = contract.get("observed_probability")
        prob_text = f"{prob:.3f}" if isinstance(prob, (int, float)) else "n/a"
        role = contract.get("structural_role") or "—"
        lines.append(
            f"- `{contract['contract_id']}` | role={role} | yes={prob_text}"
            f" | required_evidence={', '.join(contract['required_evidence_fields'])}"
        )
    lines.append("")
    lines.append("Required review questions:")
    lines.extend(f"  - {question}" for question in item["required_review_questions"])
    lines.append("")
    return lines


def _counts_inline(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"`{key}`: {value}" for key, value in counts.items())


def _md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _assemble_diagnostic_row(
    snapshot: GraphSnapshot,
    matrix: PayoffMatrix,
    result: FeasibilityResult,
) -> dict[str, Any]:
    blockers = sorted(set(matrix.blockers + result.blockers))
    confidence_score = float(result.confidence_basis.get("score", 0.5))
    action_cap = "MANUAL_REVIEW" if result.feasibility_status == "infeasible" and confidence_score >= 0.6 else "WATCH"
    row = {
        "diagnostic_id": f"payoff_state:{matrix.family_id}",
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ALLOWED_ACTIONS,
        "max_action_cap": action_cap,
        "diagnostic_priority": action_cap,
        "snapshot_id": snapshot.snapshot_id,
        "family_id": matrix.family_id,
        "family_type": matrix.family_type,
        "family_description": matrix.family_description,
        "feasibility_status": result.feasibility_status,
        "violated_constraints": list(result.violated_constraints),
        "bound_gap": round(max(0.0, result.bound_gap), 6),
        "normalized_bound_gap": round(max(0.0, result.normalized_bound_gap), 6),
        "probability_input_mode": result.probability_input_mode,
        "bound_gap_semantics": result.bound_gap_semantics,
        "state_count": matrix.state_count,
        "contract_count": matrix.contract_count,
        "contract_ids": [contract.contract_id for contract in matrix.contracts],
        "states": [state.to_dict() for state in matrix.states],
        "contracts": [contract.to_dict() for contract in matrix.contracts],
        "state_payoff_matrix": matrix.state_payoff_matrix(),
        "structural_metadata": dict(matrix.structural_metadata),
        "confidence_basis": dict(result.confidence_basis),
        "blockers": blockers,
        "required_review_questions": list(result.required_review_questions),
        "review_reason": _review_reason(result.feasibility_status, matrix.family_type),
        "graph_artifact_not_equality_evidence": True,
        "review_artifact_not_candidate": True,
        "constraint_explanation": explain_payoff_state_diagnostic(matrix, result).to_dict(),
    }
    return row


def _review_reason(feasibility_status: str, family_type: str) -> str:
    if feasibility_status == "infeasible":
        return (
            f"Finite-state feasibility violation for {family_type}; manual review of payoff vectors and "
            "settlement basis is required."
        )
    if feasibility_status == "blocked":
        return (
            f"Finite-state feasibility check blocked for {family_type}; fixture state definitions or observed "
            "probabilities are incomplete."
        )
    return f"Finite-state feasibility check consistent for {family_type} within fixture tolerance."


def _priority(action_cap: str) -> int:
    return {"MANUAL_REVIEW": 0, "WATCH": 1}.get(action_cap, 2)


def _validate_diagnostic(item: dict[str, Any], path: str) -> None:
    required_keys = [
        "diagnostic_id",
        "diagnostic_only",
        "affects_evaluator_gates",
        "allowed_actions",
        "max_action_cap",
        "diagnostic_priority",
        "snapshot_id",
        "family_id",
        "family_type",
        "feasibility_status",
        "violated_constraints",
        "bound_gap",
        "normalized_bound_gap",
        "probability_input_mode",
        "bound_gap_semantics",
        "state_count",
        "contract_count",
        "contract_ids",
        "states",
        "contracts",
        "state_payoff_matrix",
        "confidence_basis",
        "blockers",
        "required_review_questions",
        "review_reason",
        "graph_artifact_not_equality_evidence",
        "review_artifact_not_candidate",
        "constraint_explanation",
    ]
    for key in required_keys:
        if key not in item:
            raise SchemaValidationError(f"{path}.{key} is required")
    if item["diagnostic_only"] is not True:
        raise SchemaValidationError(f"{path}.diagnostic_only must be true")
    if item["affects_evaluator_gates"] is not False:
        raise SchemaValidationError(f"{path}.affects_evaluator_gates must be false")
    if item["allowed_actions"] != ALLOWED_ACTIONS:
        raise SchemaValidationError(f"{path}.allowed_actions must be WATCH and MANUAL_REVIEW only")
    if item["max_action_cap"] not in DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError(f"{path}.max_action_cap must be WATCH or MANUAL_REVIEW")
    if item["diagnostic_priority"] not in DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError(f"{path}.diagnostic_priority must be WATCH or MANUAL_REVIEW")
    if item["feasibility_status"] not in FEASIBILITY_STATUSES:
        raise SchemaValidationError(f"{path}.feasibility_status is not allowed")
    if item["family_type"] not in (SUPPORTED_FAMILY_TYPES | {"unknown"}):
        raise SchemaValidationError(f"{path}.family_type is not allowed")
    if item["probability_input_mode"] not in {"BID_ASK_INTERVAL", "DIAGNOSTIC_MIDPOINT_FALLBACK"}:
        raise SchemaValidationError(f"{path}.probability_input_mode is not supported")
    if not isinstance(item["bound_gap_semantics"], str) or not item["bound_gap_semantics"]:
        raise SchemaValidationError(f"{path}.bound_gap_semantics must be a non-empty string")
    for key in ("bound_gap", "normalized_bound_gap"):
        value = item[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise SchemaValidationError(f"{path}.{key} must be a non-negative number")
    for key in ("state_count", "contract_count"):
        value = item[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise SchemaValidationError(f"{path}.{key} must be a non-negative integer")
    if not isinstance(item["violated_constraints"], list):
        raise SchemaValidationError(f"{path}.violated_constraints must be a list")
    if not isinstance(item["blockers"], list) or not all(isinstance(value, str) for value in item["blockers"]):
        raise SchemaValidationError(f"{path}.blockers must be a list of strings")
    questions = item["required_review_questions"]
    if not isinstance(questions, list) or not questions or not all(isinstance(value, str) and value for value in questions):
        raise SchemaValidationError(f"{path}.required_review_questions must contain non-empty strings")
    confidence = item["confidence_basis"]
    if not isinstance(confidence, dict):
        raise SchemaValidationError(f"{path}.confidence_basis must be an object")
    if not isinstance(confidence.get("description"), str) or not confidence["description"]:
        raise SchemaValidationError(f"{path}.confidence_basis.description must be a non-empty string")
    score = confidence.get("score")
    if not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= 1:
        raise SchemaValidationError(f"{path}.confidence_basis.score must be between 0 and 1")
    if item["graph_artifact_not_equality_evidence"] is not True:
        raise SchemaValidationError(
            f"{path}.graph_artifact_not_equality_evidence must be true (graph hints are not equality evidence)"
        )
    if item["review_artifact_not_candidate"] is not True:
        raise SchemaValidationError(
            f"{path}.review_artifact_not_candidate must be true (review artefacts are not selection candidates)"
        )
    for key in item.keys():
        normalized = str(key).lower().replace("-", "_")
        if normalized in PROHIBITED_DIAGNOSTIC_KEYS:
            raise SchemaValidationError(f"{path}.{key} is a prohibited diagnostic field")
    _reject_prohibited_tokens(item)


__all__ = [
    "BANNER",
    "build_payoff_state_diagnostics_report",
    "render_payoff_state_diagnostics_markdown",
    "validate_payoff_state_diagnostics_report",
    "write_payoff_state_diagnostics_report",
]
