from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from graph_engine.reporting.safety import (
    PROHIBITED_REPORT_PHRASES,
    PROHIBITED_REPORT_TOKENS,
    find_prohibited_rendered_text,
    find_prohibited_report_tokens,
)


class SchemaValidationError(ValueError):
    pass


DIAGNOSTIC_HINT_ACTIONS = ["WATCH", "MANUAL_REVIEW"]
DIAGNOSTIC_HINT_RELATION_TYPES = {
    "AMBIGUOUS_WORDING",
    "COMPLEMENT",
    "CORRELATED_ONLY",
    "EXHAUSTIVE_GROUP",
    "MUTUALLY_EXCLUSIVE",
    "NEEDS_MANUAL_REVIEW",
    "OVERLAP_NOT_EQUIVALENT",
    "SAME_PAYOFF",
    "SUBSET",
    "SUPERSET",
    "UNRELATED",
}
STRUCTURAL_NOT_SAME_PAYOFF_RELATIONS = {
    "SUBSET",
    "SUPERSET",
    "COMPLEMENT",
    "MUTUALLY_EXCLUSIVE",
    "EXHAUSTIVE_GROUP",
}
SAME_PAYOFF_BOUND = "same_payoff_equality_if_settlement_proven"
DISALLOWED_HINT_RELATION_TYPES = {"EXACT_SAME_PAYOFF"}
PROHIBITED_HINT_DIFF_TOKENS = PROHIBITED_REPORT_TOKENS | PROHIBITED_REPORT_PHRASES
MULTI_LEG_CONSTRAINT_TYPES = {
    "exhaustive_group",
    "mutually_exclusive_group",
    "threshold_ladder",
    "range_bucket_partition",
    "complement_parent_child",
    "nested_subset_chain",
}
MULTI_LEG_CONSTRAINT_FAMILIES = {
    "compound_bound",
    "mutual_exclusion",
    "threshold_sequence",
    "outcome_partition",
    "range_partition",
}
FORMULA_FAMILIES = {"BTC_THRESHOLD", "FED_MEETING_RANGE", "SPORTS_CHAMPION", "WEATHER_RANGE", "UNKNOWN"}
FORMULA_COMPARATORS = {">", ">=", "<", "<=", "=", "in_range", None}
FORMULA_RELATIONS = {
    "typed_formula_match_review_only",
    "threshold_ladder",
    "ambiguous_not_exact",
    "overlap_not_identical",
    "disjoint_ranges",
    "parse_blocked",
}
FORMULA_CLUSTER_CONSTRAINT_TYPES = {
    "blocked_exact_grouping",
    "derived_complement_pair",
    "derived_mutually_exclusive_group",
    "derived_overlapping_ranges",
    "derived_possible_exhaustive_group",
    "derived_range_bucket_partition",
    "derived_threshold_ladder",
}
FORMULA_CLUSTER_CONSTRAINT_FAMILIES = {
    "complement_pair",
    "formula_cluster",
    "mutual_exclusion",
    "threshold_sequence",
    "outcome_partition",
    "range_overlap",
    "range_partition",
}


RELATIVE_VALUE_HINT_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "relative_value_hint.schema.json"


def load_relative_value_hint_schema() -> dict[str, Any]:
    return json.loads(RELATIVE_VALUE_HINT_SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_relative_value_hint_report(report: dict[str, Any]) -> None:
    """Validate graph hint exports against schema and local safety contract."""

    validate_json_schema_subset(report, load_relative_value_hint_schema())
    validate_relative_value_hint_contract(report)


def validate_json_schema_subset(instance: Any, schema: dict[str, Any]) -> None:
    """Validate the JSON Schema subset used by local fixture contracts."""

    root = schema

    def resolve_ref(ref: str) -> dict[str, Any]:
        if not ref.startswith("#/"):
            raise SchemaValidationError(f"unsupported $ref {ref}")
        current: Any = root
        for part in ref[2:].split("/"):
            current = current[part]
        if not isinstance(current, dict):
            raise SchemaValidationError(f"invalid $ref target {ref}")
        return current

    def fail(path: str, message: str) -> None:
        raise SchemaValidationError(f"{path or '$'}: {message}")

    def validate(value: Any, spec: dict[str, Any], path: str) -> None:
        if "$ref" in spec:
            validate(value, resolve_ref(spec["$ref"]), path)
            return

        if "const" in spec and value != spec["const"]:
            fail(path, f"expected const {spec['const']!r}")
        if "enum" in spec and value not in spec["enum"]:
            fail(path, f"expected one of {spec['enum']!r}")

        expected_type = spec.get("type")
        if expected_type is not None and not _matches_type(value, expected_type):
            fail(path, f"expected type {expected_type!r}")

        if isinstance(value, dict):
            properties = spec.get("properties", {})
            required = spec.get("required", [])
            for key in required:
                if key not in value:
                    fail(path, f"missing required property {key!r}")
            if spec.get("additionalProperties") is False:
                extra = sorted(set(value) - set(properties))
                if extra:
                    fail(path, f"unknown properties {extra!r}")
            for key, nested in value.items():
                if key in properties:
                    validate(nested, properties[key], f"{path}.{key}" if path else key)
                else:
                    additional = spec.get("additionalProperties")
                    if isinstance(additional, dict):
                        validate(nested, additional, f"{path}.{key}" if path else key)

        if isinstance(value, list):
            if "minItems" in spec and len(value) < spec["minItems"]:
                fail(path, f"expected at least {spec['minItems']} items")
            if "maxItems" in spec and len(value) > spec["maxItems"]:
                fail(path, f"expected at most {spec['maxItems']} items")
            if spec.get("uniqueItems") and len({_hashable(item) for item in value}) != len(value):
                fail(path, "expected unique items")
            item_spec = spec.get("items")
            if isinstance(item_spec, dict):
                for index, item in enumerate(value):
                    validate(item, item_spec, f"{path}[{index}]")

        if isinstance(value, str) and "minLength" in spec and len(value) < spec["minLength"]:
            fail(path, f"expected minLength {spec['minLength']}")
        if isinstance(value, int) and not isinstance(value, bool) and "minimum" in spec and value < spec["minimum"]:
            fail(path, f"expected minimum {spec['minimum']}")

    validate(instance, schema, "")


def validate_relative_value_hint_contract(report: dict[str, Any]) -> None:
    """Validate hint constraints that the local JSON Schema subset cannot express."""

    _reject_prohibited_tokens(report)
    if report.get("diagnostic_only") is not True:
        raise SchemaValidationError("diagnostic_only must be true")
    if report.get("allowed_actions") != DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError("allowed_actions must be WATCH and MANUAL_REVIEW only")

    for index, hint in enumerate(report.get("hints", [])):
        path = f"hints[{index}]"
        if hint.get("diagnostic_only") is not True:
            raise SchemaValidationError(f"{path}.diagnostic_only must be true")
        if hint.get("allowed_actions") != DIAGNOSTIC_HINT_ACTIONS:
            raise SchemaValidationError(f"{path}.allowed_actions must be WATCH and MANUAL_REVIEW only")
        if hint.get("max_action_cap") not in DIAGNOSTIC_HINT_ACTIONS:
            raise SchemaValidationError(f"{path}.max_action_cap must be WATCH or MANUAL_REVIEW")

        relation_type = hint.get("relation_type")
        if relation_type in DISALLOWED_HINT_RELATION_TYPES:
            raise SchemaValidationError(f"{path}.relation_type is not allowed")
        hard_bound_type = hint.get("hard_bound_type")
        if relation_type in STRUCTURAL_NOT_SAME_PAYOFF_RELATIONS and hard_bound_type == SAME_PAYOFF_BOUND:
            raise SchemaValidationError(f"{path} structural relation cannot claim exact same-payoff")
        if relation_type == "SAME_PAYOFF":
            if hint.get("settlement_source_proven") is not True:
                raise SchemaValidationError(f"{path}.settlement_source_proven must be true for SAME_PAYOFF")
            if hard_bound_type != SAME_PAYOFF_BOUND:
                raise SchemaValidationError(f"{path}.hard_bound_type must be {SAME_PAYOFF_BOUND!r} for SAME_PAYOFF")


def validate_hint_diff_contract(report: dict[str, Any]) -> None:
    """Validate saved-file hint diff outputs before they are written."""

    _reject_prohibited_tokens(report)
    if report.get("diagnostic_only") is not True:
        raise SchemaValidationError("diagnostic_only must be true")
    if report.get("allowed_actions") != DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError("allowed_actions must be WATCH and MANUAL_REVIEW only")

    for section in [
        "added_hints",
        "new_hints",
        "removed_hints",
        "changed_hints",
        "top_watch_items",
        "top_manual_review_items",
    ]:
        for index, hint in enumerate(report.get(section, [])):
            _validate_diff_hint_summary(hint, f"{section}[{index}]")

    for section in ["severity_or_priority_change", "reason_change", "action_change", "field_changes"]:
        for index, change in enumerate(report.get(section, [])):
            _validate_diff_change(change, f"{section}[{index}]")

    for section in ["upgraded_hints", "downgraded_hints"]:
        for index, cap_change in enumerate(report.get(section, [])):
            path = f"{section}[{index}]"
            for key in ["old_max_action_cap", "new_max_action_cap"]:
                if cap_change.get(key) not in DIAGNOSTIC_HINT_ACTIONS:
                    raise SchemaValidationError(f"{path}.{key} must be WATCH or MANUAL_REVIEW")

    changes_by_field = report.get("summary", {}).get("changes_by_field", {})
    if isinstance(changes_by_field, dict):
        for field in changes_by_field:
            if field not in {"relation_type", "hard_bound_type", "blockers", "max_action_cap", "direction", "settlement_source_proven"}:
                raise SchemaValidationError(f"summary.changes_by_field contains unsupported field {field!r}")


def validate_multi_leg_constraints_contract(report: dict[str, Any]) -> None:
    _reject_prohibited_tokens(report)
    if report.get("diagnostic_only") is not True:
        raise SchemaValidationError("diagnostic_only must be true")
    if report.get("allowed_actions") != DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError("allowed_actions must be WATCH and MANUAL_REVIEW only")

    constraints = report.get("multi_leg_constraints", [])
    if not isinstance(constraints, list):
        raise SchemaValidationError("multi_leg_constraints must be a list")
    for index, constraint in enumerate(constraints):
        path = f"multi_leg_constraints[{index}]"
        if constraint.get("diagnostic_only") is not True:
            raise SchemaValidationError(f"{path}.diagnostic_only must be true")
        if constraint.get("allowed_actions") != DIAGNOSTIC_HINT_ACTIONS:
            raise SchemaValidationError(f"{path}.allowed_actions must be WATCH and MANUAL_REVIEW only")
        if constraint.get("max_action_cap") not in DIAGNOSTIC_HINT_ACTIONS:
            raise SchemaValidationError(f"{path}.max_action_cap must be WATCH or MANUAL_REVIEW")
        if constraint.get("diagnostic_priority") not in DIAGNOSTIC_HINT_ACTIONS:
            raise SchemaValidationError(f"{path}.diagnostic_priority must be WATCH or MANUAL_REVIEW")
        if constraint.get("constraint_type") not in MULTI_LEG_CONSTRAINT_TYPES:
            raise SchemaValidationError(f"{path}.constraint_type is not allowed")
        if constraint.get("constraint_family") not in MULTI_LEG_CONSTRAINT_FAMILIES:
            raise SchemaValidationError(f"{path}.constraint_family is not allowed")
        blockers = constraint.get("blockers")
        if not isinstance(blockers, list) or not all(isinstance(item, str) for item in blockers):
            raise SchemaValidationError(f"{path}.blockers must be a list of strings")
        blocked_threshold_review = (
            constraint.get("constraint_type") == "threshold_ladder"
            and bool(set(blockers) & {"mixed_threshold_comparators", "mixed_or_missing_threshold_units"})
        )
        if constraint.get("constraint_violation") is not True:
            if not (blocked_threshold_review and constraint.get("constraint_violation") is False):
                raise SchemaValidationError(f"{path}.constraint_violation must be true unless threshold grouping is blocked")
        if constraint.get("structural_inconsistency") is not True:
            if not (blocked_threshold_review and constraint.get("structural_inconsistency") is False):
                raise SchemaValidationError(f"{path}.structural_inconsistency must be true unless threshold grouping is blocked")
        market_ids = constraint.get("market_ids")
        if not isinstance(market_ids, list) or len(market_ids) < 3:
            raise SchemaValidationError(f"{path}.market_ids must contain three or more markets")
        if constraint.get("market_count") != len(market_ids):
            raise SchemaValidationError(f"{path}.market_count must match market_ids")
        for key in [
            "observed_value",
            "expected_lower_bound",
            "expected_upper_bound",
            "bound_gap",
            "normalized_bound_gap",
        ]:
            if not isinstance(constraint.get(key), (int, float)) or isinstance(constraint.get(key), bool):
                raise SchemaValidationError(f"{path}.{key} must be numeric")
        if constraint["expected_lower_bound"] > constraint["expected_upper_bound"]:
            raise SchemaValidationError(f"{path}.expected bounds are inverted")
        if constraint["bound_gap"] <= 0 and not blocked_threshold_review:
            raise SchemaValidationError(f"{path}.bound_gap must be positive")
        if constraint["normalized_bound_gap"] <= 0 and not blocked_threshold_review:
            raise SchemaValidationError(f"{path}.normalized_bound_gap must be positive")
        confidence_basis = constraint.get("confidence_basis")
        if not isinstance(confidence_basis, dict):
            raise SchemaValidationError(f"{path}.confidence_basis must be an object")
        if not isinstance(confidence_basis.get("description"), str) or not confidence_basis["description"]:
            raise SchemaValidationError(f"{path}.confidence_basis.description must be a non-empty string")
        score = confidence_basis.get("score")
        if not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= 1:
            raise SchemaValidationError(f"{path}.confidence_basis.score must be between 0 and 1")
        questions = constraint.get("required_review_questions")
        if not isinstance(questions, list) or not questions or not all(isinstance(item, str) and item for item in questions):
            raise SchemaValidationError(f"{path}.required_review_questions must contain non-empty strings")


def validate_formula_diagnostics_contract(report: dict[str, Any]) -> None:
    _reject_prohibited_tokens(report)
    if report.get("diagnostic_only") is not True:
        raise SchemaValidationError("formula diagnostics must be diagnostic_only")
    if report.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError("formula diagnostics must not affect evaluator gates")
    if report.get("allowed_actions") != DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError("formula diagnostics actions must be WATCH and MANUAL_REVIEW only")
    for index, formula in enumerate(report.get("formulas", [])):
        path = f"formulas[{index}]"
        if formula.get("family") not in FORMULA_FAMILIES:
            raise SchemaValidationError(f"{path}.family is not allowed")
        if formula.get("comparator") not in FORMULA_COMPARATORS:
            raise SchemaValidationError(f"{path}.comparator is not allowed")
        if formula.get("side") not in {"YES", "NO"}:
            raise SchemaValidationError(f"{path}.side is not allowed")
    for index, diagnostic in enumerate(report.get("formula_diagnostics", [])):
        path = f"formula_diagnostics[{index}]"
        if diagnostic.get("diagnostic_only") is not True:
            raise SchemaValidationError(f"{path}.diagnostic_only must be true")
        if diagnostic.get("affects_evaluator_gates") is not False:
            raise SchemaValidationError(f"{path}.affects_evaluator_gates must be false")
        if diagnostic.get("allowed_actions") != DIAGNOSTIC_HINT_ACTIONS:
            raise SchemaValidationError(f"{path}.allowed_actions must be WATCH and MANUAL_REVIEW only")
        if diagnostic.get("max_action_cap") not in DIAGNOSTIC_HINT_ACTIONS:
            raise SchemaValidationError(f"{path}.max_action_cap must be WATCH or MANUAL_REVIEW")
        if diagnostic.get("diagnostic_priority") not in DIAGNOSTIC_HINT_ACTIONS:
            raise SchemaValidationError(f"{path}.diagnostic_priority must be WATCH or MANUAL_REVIEW")
        if diagnostic.get("family") not in FORMULA_FAMILIES:
            raise SchemaValidationError(f"{path}.family is not allowed")
        if diagnostic.get("formula_relation") not in FORMULA_RELATIONS:
            raise SchemaValidationError(f"{path}.formula_relation is not allowed")
    if "formula_cluster_constraints" in report:
        validate_formula_cluster_constraints_contract(report)


def validate_formula_cluster_constraints_contract(report: dict[str, Any]) -> None:
    _reject_prohibited_tokens(report)
    if report.get("diagnostic_only") is not True:
        raise SchemaValidationError("formula cluster constraints must be diagnostic_only")
    if report.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError("formula cluster constraints must not affect evaluator gates")
    if report.get("allowed_actions") != DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError("formula cluster constraints actions must be WATCH and MANUAL_REVIEW only")
    constraints = report.get("formula_cluster_constraints", [])
    if not isinstance(constraints, list):
        raise SchemaValidationError("formula_cluster_constraints must be a list")
    for index, constraint in enumerate(constraints):
        path = f"formula_cluster_constraints[{index}]"
        if constraint.get("diagnostic_only") is not True:
            raise SchemaValidationError(f"{path}.diagnostic_only must be true")
        if constraint.get("affects_evaluator_gates") is not False:
            raise SchemaValidationError(f"{path}.affects_evaluator_gates must be false")
        if constraint.get("allowed_actions") != DIAGNOSTIC_HINT_ACTIONS:
            raise SchemaValidationError(f"{path}.allowed_actions must be WATCH and MANUAL_REVIEW only")
        if constraint.get("max_action_cap") not in DIAGNOSTIC_HINT_ACTIONS:
            raise SchemaValidationError(f"{path}.max_action_cap must be WATCH or MANUAL_REVIEW")
        if constraint.get("diagnostic_priority") not in DIAGNOSTIC_HINT_ACTIONS:
            raise SchemaValidationError(f"{path}.diagnostic_priority must be WATCH or MANUAL_REVIEW")
        if constraint.get("constraint_type") not in FORMULA_CLUSTER_CONSTRAINT_TYPES:
            raise SchemaValidationError(f"{path}.constraint_type is not allowed")
        if constraint.get("constraint_family") not in FORMULA_CLUSTER_CONSTRAINT_FAMILIES:
            raise SchemaValidationError(f"{path}.constraint_family is not allowed")
        market_ids = constraint.get("source_market_ids")
        if not isinstance(market_ids, list) or not market_ids:
            raise SchemaValidationError(f"{path}.source_market_ids must contain market ids")
        if constraint.get("formula_count") != len(market_ids):
            raise SchemaValidationError(f"{path}.formula_count must match source_market_ids")
        keys = constraint.get("requested_exact_keys_to_verify")
        if not isinstance(keys, list) or not keys or not all(isinstance(item, str) and item for item in keys):
            raise SchemaValidationError(f"{path}.requested_exact_keys_to_verify must contain strings")
        blockers = constraint.get("blockers")
        if not isinstance(blockers, list) or not all(isinstance(item, str) for item in blockers):
            raise SchemaValidationError(f"{path}.blockers must be a list of strings")
        if not isinstance(constraint.get("reason_for_review"), str) or not constraint["reason_for_review"]:
            raise SchemaValidationError(f"{path}.reason_for_review must be a non-empty string")


def _validate_diff_hint_summary(hint: dict[str, Any], path: str) -> None:
    if hint.get("diagnostic_only") is not True:
        raise SchemaValidationError(f"{path}.diagnostic_only must be true")
    if hint.get("allowed_actions") != DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError(f"{path}.allowed_actions must be WATCH and MANUAL_REVIEW only")
    if hint.get("max_action_cap") not in DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError(f"{path}.max_action_cap must be WATCH or MANUAL_REVIEW")
    relation_type = hint.get("relation_type")
    if relation_type not in DIAGNOSTIC_HINT_RELATION_TYPES:
        raise SchemaValidationError(f"{path}.relation_type is not allowed")
    if relation_type in DISALLOWED_HINT_RELATION_TYPES:
        raise SchemaValidationError(f"{path}.relation_type is not allowed")
    if "previous_max_action_cap" in hint and hint["previous_max_action_cap"] not in DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError(f"{path}.previous_max_action_cap must be WATCH or MANUAL_REVIEW")
    for section in ["severity_or_priority_change", "reason_change", "action_change", "field_changes"]:
        for index, change in enumerate(hint.get(section, [])):
            _validate_diff_change(change, f"{path}.{section}[{index}]")


def _validate_diff_change(change: dict[str, Any], path: str) -> None:
    field = change.get("field")
    if field == "relation_type":
        for key in ["old_value", "new_value"]:
            value = change.get(key)
            if value not in DIAGNOSTIC_HINT_RELATION_TYPES:
                raise SchemaValidationError(f"{path}.{key} relation_type is not allowed")
            if value in DISALLOWED_HINT_RELATION_TYPES:
                raise SchemaValidationError(f"{path}.{key} relation_type is not allowed")
    if field == "max_action_cap":
        for key in ["old_value", "new_value"]:
            if change.get(key) not in DIAGNOSTIC_HINT_ACTIONS:
                raise SchemaValidationError(f"{path}.{key} must be WATCH or MANUAL_REVIEW")


def _reject_prohibited_tokens(payload: Any) -> None:
    findings = find_prohibited_report_tokens(payload)
    if findings:
        raise SchemaValidationError(f"prohibited hint diff token present: {sorted(set(findings))}")


def _contains_prohibited_token(value: str) -> bool:
    normalized = value.lower().replace("-", "_")
    return any(re.search(rf"\b{re.escape(token)}\b", normalized) for token in PROHIBITED_HINT_DIFF_TOKENS)


def _matches_type(value: Any, expected_type: str | list[str]) -> bool:
    expected = [expected_type] if isinstance(expected_type, str) else expected_type
    return any(_matches_single_type(value, item) for item in expected)


def _matches_single_type(value: Any, expected_type: str) -> bool:
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "null":
        return value is None
    raise SchemaValidationError(f"unsupported type {expected_type!r}")


def _hashable(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((key, _hashable(nested)) for key, nested in value.items()))
    if isinstance(value, list):
        return tuple(_hashable(item) for item in value)
    return value
