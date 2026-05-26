"""Maps formula families to finite-state-safe family declarations.

Reviewers ask three questions before any rel-value investigation can begin:

1.  Can this formula family be represented as a finite-state payoff matrix
    at all?
2.  If not, why not -- which evidence fields, settlement-rule clarifications,
    or wording disambiguations are missing?
3.  What review questions does the reviewer need to answer before a finite
    state matrix would be trustworthy?

This module answers those questions in a single declarative table.  The
table is purely a review aid -- it never emits trade language, never claims
exact same-payoff equivalence, and never produces a paper-trade candidate.
Every entry is diagnostic-only with action cap WATCH or MANUAL_REVIEW.

The :func:`build_state_family_registry_report` function returns a saved-file
shaped dictionary that is validated for safety vocabulary before it is
written.  :func:`explain_payoff_state_diagnostic` then composes the entry
with a per-family :class:`graph_engine.payoff_state_feasibility.FeasibilityResult`
into a structured human-readable explanation suitable for review packets.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from graph_engine.payoff_state import (
    ALLOWED_ACTIONS,
    SUPPORTED_FAMILY_TYPES,
    PayoffMatrix,
)
from graph_engine.payoff_state_feasibility import FeasibilityResult
from graph_engine.reporting.safety import find_prohibited_rendered_text
from graph_engine.reporting.schema_validation import (
    DIAGNOSTIC_HINT_ACTIONS,
    SchemaValidationError,
    _reject_prohibited_tokens,
)


REGISTRY_BANNER = (
    "Diagnostic-only state-family registry. Each entry explains whether a "
    "formula family can be represented as a finite-state payoff matrix and "
    "what review evidence is required before any rel-value investigation. "
    "Outputs are capped at WATCH and MANUAL_REVIEW and may NOT be used as "
    "evaluator gate input."
)


@dataclass(frozen=True)
class StateFamilyRegistryEntry:
    formula_family: str
    finite_state_family_type: str | None
    is_finite_state_safe: bool
    block_reasons: list[str]
    required_evidence_fields: list[str]
    required_review_questions: list[str]
    structural_notes: str
    suggested_max_action_cap: str = "WATCH"

    def to_dict(self) -> dict[str, Any]:
        return {
            "formula_family": self.formula_family,
            "finite_state_family_type": self.finite_state_family_type,
            "is_finite_state_safe": self.is_finite_state_safe,
            "block_reasons": list(self.block_reasons),
            "required_evidence_fields": list(self.required_evidence_fields),
            "required_review_questions": list(self.required_review_questions),
            "structural_notes": self.structural_notes,
            "suggested_max_action_cap": self.suggested_max_action_cap,
            "diagnostic_only": True,
            "affects_evaluator_gates": False,
            "allowed_actions": list(ALLOWED_ACTIONS),
        }


_REGISTRY: list[StateFamilyRegistryEntry] = [
    StateFamilyRegistryEntry(
        formula_family="BTC_THRESHOLD",
        finite_state_family_type="threshold_ladder",
        is_finite_state_safe=True,
        block_reasons=[],
        required_evidence_fields=[
            "asset",
            "source",
            "settlement_date",
            "settlement_time",
            "comparator",
            "threshold",
            "units",
        ],
        required_review_questions=[
            "Do all ladder markets share the same asset, source, and settlement_time?",
            "Is each comparator oriented consistently (all '>' or all '>=' but not mixed)?",
            "Does each stricter threshold imply the looser threshold?",
        ],
        structural_notes=(
            "BTC threshold ladders form a monotonic threshold sequence over a single "
            "settlement source; the finite states are disjoint price ranges between "
            "consecutive thresholds."
        ),
        suggested_max_action_cap="MANUAL_REVIEW",
    ),
    StateFamilyRegistryEntry(
        formula_family="FED_MEETING_RANGE",
        finite_state_family_type="range_bucket_partition",
        is_finite_state_safe=True,
        block_reasons=[],
        required_evidence_fields=[
            "subject",
            "source",
            "meeting_date",
            "lower_bound",
            "upper_bound",
            "units",
        ],
        required_review_questions=[
            "Do the bucket boundaries cover the full target rate range without gaps?",
            "Is any bucket boundary inclusive on both adjacent markets?",
            "Do all markets refer to the same meeting date and source?",
        ],
        structural_notes=(
            "Fed target-range markets form a partition of the target rate into "
            "disjoint half-open intervals; the finite states are the buckets."
        ),
        suggested_max_action_cap="MANUAL_REVIEW",
    ),
    StateFamilyRegistryEntry(
        formula_family="SPORTS_CHAMPION",
        finite_state_family_type="mutually_exclusive_group",
        is_finite_state_safe=False,
        block_reasons=[
            "exhaustiveness_not_proven",
            "tie_or_cancellation_rules_required",
        ],
        required_evidence_fields=[
            "team",
            "source",
            "date",
            "league_scope",
            "tie_or_cancellation_rules",
        ],
        required_review_questions=[
            "Is the full set of competing teams represented in the market group?",
            "Are tie and cancellation outcomes resolved consistently?",
            "Do all markets share the same season scope and settlement source?",
        ],
        structural_notes=(
            "Sports winner markets are mutually exclusive but only become "
            "exhaustive once the full team list, tie rules, and cancellation "
            "rules are documented; until then the finite-state family is blocked."
        ),
        suggested_max_action_cap="WATCH",
    ),
    StateFamilyRegistryEntry(
        formula_family="WEATHER_RANGE",
        finite_state_family_type="range_bucket_partition",
        is_finite_state_safe=False,
        block_reasons=[
            "weather_buckets_not_proven_exhaustive",
            "measurement_station_required",
        ],
        required_evidence_fields=[
            "observable",
            "location",
            "source",
            "date",
            "measurement_station",
            "units",
        ],
        required_review_questions=[
            "Are the weather buckets exhaustive over the observable's domain?",
            "Is the measurement station and timing definition pinned to one source?",
            "Does the source publish a tie-breaking rule for boundary readings?",
        ],
        structural_notes=(
            "Weather range markets can map to a range bucket partition, but the "
            "underlying observable definition and measurement station must be "
            "pinned before the finite-state matrix is trustworthy."
        ),
        suggested_max_action_cap="WATCH",
    ),
    StateFamilyRegistryEntry(
        formula_family="UNKNOWN",
        finite_state_family_type=None,
        is_finite_state_safe=False,
        block_reasons=["unsupported_formula_family"],
        required_evidence_fields=[
            "family",
            "settlement_source",
            "settlement_window",
            "resolution_criteria",
        ],
        required_review_questions=[
            "Which typed formula family does this market belong to?",
            "What evidence keys would make this family finite-state-safe?",
        ],
        structural_notes=(
            "Unknown families cannot be compiled into a finite-state matrix. "
            "Manual review is required to assign a typed family before any "
            "rel-value investigation."
        ),
        suggested_max_action_cap="WATCH",
    ),
]


_REGISTRY_BY_FAMILY = {entry.formula_family: entry for entry in _REGISTRY}


def state_family_registry() -> list[StateFamilyRegistryEntry]:
    """Return the canonical registry."""

    return list(_REGISTRY)


def registry_entry_for_formula_family(formula_family: str) -> StateFamilyRegistryEntry:
    """Look up the registry entry for ``formula_family`` (falls back to UNKNOWN)."""

    return _REGISTRY_BY_FAMILY.get(formula_family, _REGISTRY_BY_FAMILY["UNKNOWN"])


def build_state_family_registry_report() -> dict[str, Any]:
    """Build a saved-file shaped registry report (diagnostic only)."""

    entries = [entry.to_dict() for entry in _REGISTRY]
    report = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": list(ALLOWED_ACTIONS),
        "banner": REGISTRY_BANNER,
        "entry_count": len(entries),
        "state_family_registry_entries": entries,
        "supported_finite_state_family_types": sorted(SUPPORTED_FAMILY_TYPES),
    }
    validate_state_family_registry_report(report)
    return report


def write_state_family_registry_report(
    json_output: Path | str,
    md_output: Path | str,
) -> dict[str, Any]:
    report = build_state_family_registry_report()
    markdown = render_state_family_registry_markdown(report)
    hits = find_prohibited_rendered_text(markdown)
    if hits:
        raise SchemaValidationError(
            "state-family registry Markdown contains prohibited vocabulary: "
            + ", ".join(hits)
        )
    json_path = Path(json_output)
    md_path = Path(md_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")
    return report


def validate_state_family_registry_report(report: dict[str, Any]) -> None:
    _reject_prohibited_tokens(report)
    if report.get("diagnostic_only") is not True:
        raise SchemaValidationError("state-family registry must be diagnostic_only")
    if report.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError("state-family registry must not affect evaluator gates")
    if report.get("allowed_actions") != list(ALLOWED_ACTIONS):
        raise SchemaValidationError("state-family registry actions must be WATCH and MANUAL_REVIEW only")
    entries = report.get("state_family_registry_entries")
    if not isinstance(entries, list):
        raise SchemaValidationError("state_family_registry_entries must be a list")
    if report.get("entry_count") != len(entries):
        raise SchemaValidationError("entry_count must match state_family_registry_entries length")
    for index, entry in enumerate(entries):
        path = f"state_family_registry_entries[{index}]"
        if entry.get("diagnostic_only") is not True:
            raise SchemaValidationError(f"{path}.diagnostic_only must be true")
        if entry.get("affects_evaluator_gates") is not False:
            raise SchemaValidationError(f"{path}.affects_evaluator_gates must be false")
        if entry.get("allowed_actions") != list(ALLOWED_ACTIONS):
            raise SchemaValidationError(f"{path}.allowed_actions must be WATCH and MANUAL_REVIEW only")
        if entry.get("suggested_max_action_cap") not in DIAGNOSTIC_HINT_ACTIONS:
            raise SchemaValidationError(f"{path}.suggested_max_action_cap must be WATCH or MANUAL_REVIEW")
        family_type = entry.get("finite_state_family_type")
        if family_type is not None and family_type not in SUPPORTED_FAMILY_TYPES:
            raise SchemaValidationError(f"{path}.finite_state_family_type is not allowed")
        for key in ("block_reasons", "required_evidence_fields", "required_review_questions"):
            value = entry.get(key)
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise SchemaValidationError(f"{path}.{key} must be a list of strings")
        if not isinstance(entry.get("structural_notes"), str) or not entry["structural_notes"]:
            raise SchemaValidationError(f"{path}.structural_notes must be a non-empty string")


def render_state_family_registry_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = [
        "# State-Family Registry",
        "",
        report["banner"],
        "",
        f"- Diagnostic only: `{str(report['diagnostic_only']).lower()}`",
        f"- Affects evaluator gates: `{str(report['affects_evaluator_gates']).lower()}`",
        f"- Allowed actions: `{', '.join(report['allowed_actions'])}`",
        f"- Entry count: {report['entry_count']}",
        f"- Supported finite-state family types: {', '.join(f'`{name}`' for name in report['supported_finite_state_family_types'])}",
        "",
    ]
    for entry in report["state_family_registry_entries"]:
        lines.extend(
            [
                f"## `{entry['formula_family']}`",
                "",
                f"- Finite-state family type: `{entry['finite_state_family_type'] or 'unsupported'}`",
                f"- Finite-state safe: `{str(entry['is_finite_state_safe']).lower()}`",
                f"- Suggested max action cap: `{entry['suggested_max_action_cap']}`",
                f"- Block reasons: {', '.join(entry['block_reasons']) if entry['block_reasons'] else 'none'}",
                "- Required evidence fields:",
            ]
        )
        lines.extend(f"  - `{field_name}`" for field_name in entry["required_evidence_fields"])
        lines.append("- Required review questions:")
        lines.extend(f"  - {question}" for question in entry["required_review_questions"])
        lines.extend(["", f"_{entry['structural_notes']}_", ""])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Constraint explanation engine
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConstraintExplanation:
    family_id: str
    finite_state_family_type: str
    explanation_text: str
    missing_evidence: list[str]
    review_questions: list[str]
    diagnostic_only: bool = True
    affects_evaluator_gates: bool = False
    max_action_cap: str = "WATCH"
    blockers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "finite_state_family_type": self.finite_state_family_type,
            "explanation_text": self.explanation_text,
            "missing_evidence": list(self.missing_evidence),
            "review_questions": list(self.review_questions),
            "diagnostic_only": self.diagnostic_only,
            "affects_evaluator_gates": self.affects_evaluator_gates,
            "max_action_cap": self.max_action_cap,
            "blockers": list(self.blockers),
            "allowed_actions": list(ALLOWED_ACTIONS),
        }


def explain_payoff_state_diagnostic(
    matrix: PayoffMatrix,
    result: FeasibilityResult,
) -> ConstraintExplanation:
    """Compose a registry entry with feasibility output into a review packet."""

    base_evidence = _evidence_from_matrix(matrix)
    cap = "MANUAL_REVIEW" if result.feasibility_status == "infeasible" and result.confidence_basis.get("score", 0.0) >= 0.6 else "WATCH"
    if result.feasibility_status == "infeasible":
        text = (
            f"Family `{matrix.family_id}` ({matrix.family_type}) is finite-state "
            f"infeasible: violated constraints are {', '.join(result.violated_constraints)}. "
            f"Bound gap is {result.bound_gap:.4f}; manual review of payoff vectors and "
            "settlement basis is required before any rel-value investigation."
        )
    elif result.feasibility_status == "blocked":
        text = (
            f"Family `{matrix.family_id}` ({matrix.family_type}) cannot be checked for "
            f"finite-state feasibility yet. Blockers: {', '.join(result.blockers) or 'none reported'}. "
            "Resolve missing evidence and resubmit before downstream review."
        )
    else:
        text = (
            f"Family `{matrix.family_id}` ({matrix.family_type}) is finite-state "
            f"consistent within tolerance. This is review-only context, not "
            "equality-of-payoff evidence."
        )

    questions = list(result.required_review_questions)
    questions.extend(
        [
            "Are payoff vectors validated against native venue resolution rules?",
            "Is the settlement source proven the same across every contract?",
        ]
    )
    return ConstraintExplanation(
        family_id=matrix.family_id,
        finite_state_family_type=matrix.family_type,
        explanation_text=text,
        missing_evidence=base_evidence,
        review_questions=sorted(set(questions)),
        max_action_cap=cap,
        blockers=list(result.blockers),
    )


def _evidence_from_matrix(matrix: PayoffMatrix) -> list[str]:
    fields: set[str] = set()
    for contract in matrix.contracts:
        for evidence_field in contract.required_evidence_fields:
            fields.add(evidence_field)
    return sorted(fields)


__all__ = [
    "ConstraintExplanation",
    "REGISTRY_BANNER",
    "StateFamilyRegistryEntry",
    "build_state_family_registry_report",
    "explain_payoff_state_diagnostic",
    "registry_entry_for_formula_family",
    "render_state_family_registry_markdown",
    "state_family_registry",
    "validate_state_family_registry_report",
    "write_state_family_registry_report",
]
