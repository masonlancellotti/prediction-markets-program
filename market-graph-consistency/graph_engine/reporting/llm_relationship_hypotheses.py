from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from graph_engine.formula import build_formula_diagnostics_report
from graph_engine.models import ExclusionCompleteness, GraphSnapshot, MarketNode, RelationshipType
from graph_engine.reporting.schema_validation import DIAGNOSTIC_HINT_ACTIONS, SchemaValidationError


REVIEW_PACKET_PATH = "reports/llm_relationship_review_packets.jsonl"
VALIDATED_REPORT_PATH = "reports/llm_relationship_hypotheses_validated.json"
CONFIDENCE_TIERS = {"HIGH", "MEDIUM", "LOW"}
EVENT_CLASSES = {
    "macro",
    "election",
    "sports",
    "crypto",
    "policy",
    "news_cycle",
    "weather",
    "regulatory",
    "entity",
    "other",
}
VALIDATION_STATUSES = {"ACCEPTED", "DOWNGRADED", "REJECTED", "IGNORED_INSUFFICIENT_EVIDENCE"}
RELATIONSHIP_STRENGTH_TIERS = {
    "DETERMINISTIC_SUPPORTED",
    "LOGICAL_HYPOTHESIS_ONLY",
    "PROBABILISTIC_HYPOTHESIS_ONLY",
    "THEMATIC_HYPOTHESIS_ONLY",
    "STALE_OR_LAG_HYPOTHESIS_ONLY",
    "SIMILARITY_ONLY_RESEARCH",
    "INSUFFICIENT_EVIDENCE_IGNORED",
    "REJECTED_UNSAFE",
}
ABSTAIN_RELATIONSHIP_TYPES = {"INSUFFICIENT_EVIDENCE", "ABSTAIN"}
LOGICAL_STRENGTH_TIERS = {"DETERMINISTIC_SUPPORTED", "LOGICAL_HYPOTHESIS_ONLY"}
ADVISORY_STRENGTH_TIERS = {
    "LOGICAL_HYPOTHESIS_ONLY",
    "PROBABILISTIC_HYPOTHESIS_ONLY",
    "THEMATIC_HYPOTHESIS_ONLY",
    "STALE_OR_LAG_HYPOTHESIS_ONLY",
    "SIMILARITY_ONLY_RESEARCH",
}
RELATIONSHIP_TYPES = {
    "EXACT_EQUALITY_HYPOTHESIS",
    "COMPLEMENT_HYPOTHESIS",
    "SUBSET_HYPOTHESIS",
    "SUPERSET_HYPOTHESIS",
    "MUTUALLY_EXCLUSIVE_HYPOTHESIS",
    "EXHAUSTIVE_PARTITION_HYPOTHESIS",
    "THRESHOLD_LADDER_HYPOTHESIS",
    "RANGE_BUCKET_HYPOTHESIS",
    "PROBABILISTIC_RELATED_HYPOTHESIS",
    "THEMATIC_CORRELATION_HYPOTHESIS",
    "STALE_OR_LAG_HYPOTHESIS",
    "SIMILARITY_ONLY_HYPOTHESIS",
}
STRUCTURAL_HYPOTHESES = {
    "EXACT_EQUALITY_HYPOTHESIS",
    "COMPLEMENT_HYPOTHESIS",
    "SUBSET_HYPOTHESIS",
    "SUPERSET_HYPOTHESIS",
    "MUTUALLY_EXCLUSIVE_HYPOTHESIS",
    "EXHAUSTIVE_PARTITION_HYPOTHESIS",
    "THRESHOLD_LADDER_HYPOTHESIS",
    "RANGE_BUCKET_HYPOTHESIS",
}
ADVISORY_ONLY_HYPOTHESES = {
    "PROBABILISTIC_RELATED_HYPOTHESIS",
    "THEMATIC_CORRELATION_HYPOTHESIS",
    "STALE_OR_LAG_HYPOTHESIS",
    "SIMILARITY_ONLY_HYPOTHESIS",
}
DISALLOWED_PERMISSION_ACTIONS = {"PAPER_CANDIDATE", "TRADE", "EXECUTE", "ORDER", "BUY", "SELL"}
DISALLOWED_VALUE_TOKENS = {
    "PAPER_CANDIDATE",
    "GUARANTEED_PNL",
    "EXACT_ARBITRAGE",
    "EXECUTABLE_ARBITRAGE",
    "PLACE_ORDER",
    "CANCEL_ORDER",
}
SECRET_MARKERS = {
    "api_key",
    "secret",
    "private_key",
    "mnemonic",
    "bearer",
    "authorization",
    "session",
    "cookie",
}
PACKET_INSTRUCTION = (
    "Offline review packet. Propose structured relationship hypotheses only. "
    "Do not provide execution instructions, routing guidance, account guidance, API usage, or final decisions."
)
REPORT_BANNER = (
    "Saved-file-only LLM relationship hypothesis validation report. LLM output is advisory evidence only."
)
NOT_EXECUTION_REASON = (
    "LLM hypothesis is advisory only; deterministic relationship evidence and independent settlement review are required outside this report."
)
DEFAULT_BLOCKERS = [
    "llm_hypothesis_advisory_only",
    "requires_deterministic_validation",
    "requires_independent_payoff_verification",
    "not_evaluator_input",
    "no_execution_permission",
]


def build_llm_relationship_review_packets(snapshot: GraphSnapshot) -> list[dict[str, Any]]:
    formula_report = build_formula_diagnostics_report(snapshot)
    packet_market_groups = _packet_market_groups(snapshot, formula_report)
    packets = [
        _review_packet(snapshot, formula_report, market_ids, reason, index)
        for index, (market_ids, reason) in enumerate(packet_market_groups, start=1)
    ]
    validate_llm_review_packets(packets)
    return packets


def write_llm_relationship_review_packets(snapshot: GraphSnapshot, output_path: Path | str) -> list[dict[str, Any]]:
    packets = build_llm_relationship_review_packets(snapshot)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(packet, sort_keys=True) + "\n" for packet in packets), encoding="utf-8")
    return packets


def build_llm_relationship_hypotheses_report(
    snapshot: GraphSnapshot,
    hypotheses: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    hypothesis_ids = {
        hypothesis.get("hypothesis_id")
        for hypothesis in hypotheses or []
        if isinstance(hypothesis, dict) and isinstance(hypothesis.get("hypothesis_id"), str)
    }
    for index, hypothesis in enumerate(hypotheses or []):
        if _is_insufficient_evidence_hypothesis(hypothesis):
            rejected.append(_ignored_insufficient_evidence_row(hypothesis, index))
            continue
        try:
            rows.append(_validated_hypothesis_row(snapshot, hypothesis, index, known_hypothesis_ids=hypothesis_ids))
        except SchemaValidationError as exc:
            rejected.append(_rejected_hypothesis_row(hypothesis, str(exc), index))

    rows = sorted(rows, key=lambda row: (row["diagnostic_priority"], row["relationship_type"], row["hypothesis_id"]))
    for rank, row in enumerate(rows, start=1):
        row["diagnostic_rank"] = rank

    counts = Counter(row["relationship_type"] for row in rows)
    tier_counts: Counter[str] = Counter(row["relationship_strength_tier"] for row in rows)
    for entry in rejected:
        tier_counts[entry["relationship_strength_tier"]] += 1
    validation_counts = Counter(row["validation_status"] for row in rows)
    for entry in rejected:
        validation_counts[entry["validation_status"]] += 1

    report = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": DIAGNOSTIC_HINT_ACTIONS,
        "banner": REPORT_BANNER,
        "snapshot_id": snapshot.snapshot_id,
        "hypothesis_count": len(rows),
        "rejected_hypothesis_count": len(rejected),
        "counts_by_relationship_type": dict(sorted(counts.items())),
        "counts_by_strength_tier": dict(sorted(tier_counts.items())),
        "counts_by_validation_status": dict(sorted(validation_counts.items())),
        "validated_hypotheses": rows,
        "rejected_hypotheses": rejected,
    }
    validate_llm_relationship_hypotheses_report(report)
    return report


def write_llm_relationship_hypotheses_report(
    snapshot: GraphSnapshot,
    output_path: Path | str,
    hypotheses: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    report = build_llm_relationship_hypotheses_report(snapshot, hypotheses)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def import_llm_relationship_hypotheses(path: Path | str) -> list[dict[str, Any]]:
    input_path = Path(path)
    text = input_path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if input_path.suffix.lower() == ".jsonl":
        hypotheses = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        payload = json.loads(text)
        if isinstance(payload, list):
            hypotheses = payload
        elif isinstance(payload, dict):
            hypotheses = payload.get("hypotheses", payload.get("relationship_hypotheses", []))
        else:
            raise SchemaValidationError("LLM hypothesis payload must be a list or object")
    if not isinstance(hypotheses, list):
        raise SchemaValidationError("LLM hypotheses must be a list")
    if not all(isinstance(item, dict) for item in hypotheses):
        raise SchemaValidationError("each LLM hypothesis must be an object")
    return hypotheses


def write_imported_llm_relationship_hypotheses_report(
    snapshot: GraphSnapshot,
    input_path: Path | str,
    output_path: Path | str,
) -> dict[str, Any]:
    hypotheses = import_llm_relationship_hypotheses(input_path)
    return write_llm_relationship_hypotheses_report(snapshot, output_path, hypotheses)


def validate_llm_review_packets(packets: list[dict[str, Any]]) -> None:
    if not isinstance(packets, list):
        raise SchemaValidationError("review packets must be a list")
    for index, packet in enumerate(packets):
        path = f"packets[{index}]"
        _reject_secret_markers(packet, path)
        _reject_disallowed_values(packet, path)
        if packet.get("diagnostic_only") is not True:
            raise SchemaValidationError(f"{path}.diagnostic_only must be true")
        if packet.get("allowed_actions") != DIAGNOSTIC_HINT_ACTIONS:
            raise SchemaValidationError(f"{path}.allowed_actions must be WATCH and MANUAL_REVIEW only")
        if not isinstance(packet.get("markets"), list) or len(packet["markets"]) < 2:
            raise SchemaValidationError(f"{path}.markets must contain at least two markets")
        if "llm_output_schema" not in packet:
            raise SchemaValidationError(f"{path}.llm_output_schema is required")


def validate_llm_relationship_hypotheses_report(report: dict[str, Any]) -> None:
    _reject_disallowed_values(report, "report")
    if report.get("diagnostic_only") is not True:
        raise SchemaValidationError("LLM hypothesis report must be diagnostic_only")
    if report.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError("LLM hypothesis report must not affect evaluator gates")
    if report.get("allowed_actions") != DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError("LLM hypothesis report actions must be WATCH and MANUAL_REVIEW only")
    rows = report.get("validated_hypotheses")
    if not isinstance(rows, list):
        raise SchemaValidationError("validated_hypotheses must be a list")
    if report.get("hypothesis_count") != len(rows):
        raise SchemaValidationError("hypothesis_count must match validated_hypotheses")
    for index, row in enumerate(rows):
        _validate_report_row(row, f"validated_hypotheses[{index}]")
    rejected = report.get("rejected_hypotheses")
    if not isinstance(rejected, list):
        raise SchemaValidationError("rejected_hypotheses must be a list")
    if report.get("rejected_hypothesis_count") != len(rejected):
        raise SchemaValidationError("rejected_hypothesis_count must match rejected_hypotheses")


def _packet_market_groups(
    snapshot: GraphSnapshot,
    formula_report: dict[str, Any],
) -> list[tuple[list[str], str]]:
    candidates: dict[tuple[str, ...], str] = {}
    for group, reason in _formula_groups(formula_report):
        if len(group) >= 2:
            candidates[tuple(sorted(group))] = reason
    for component in _edge_components(snapshot):
        if len(component) >= 2:
            candidates.setdefault(tuple(sorted(component)), "known_graph_edge_cluster")
    for exclusion in snapshot.exclusion_sets:
        if len(exclusion.member_market_ids) >= 2:
            candidates.setdefault(tuple(sorted(exclusion.member_market_ids)), f"known_exclusion_set:{exclusion.set_id}")

    ordered = sorted(candidates.items(), key=lambda item: (-len(item[0]), item[1], item[0]))
    return [(list(market_ids), reason) for market_ids, reason in ordered]


def _formula_groups(formula_report: dict[str, Any]) -> list[tuple[list[str], str]]:
    grouped: dict[tuple[Any, ...], list[str]] = defaultdict(list)
    for formula in formula_report.get("formulas", []):
        group_key = (
            formula.get("family"),
            formula.get("source"),
            formula.get("date") or formula.get("meeting_date") or formula.get("settlement_time"),
            formula.get("subject") or formula.get("asset") or formula.get("team") or formula.get("location"),
        )
        grouped[group_key].append(formula["market_id"])
    groups = [(market_ids, "typed_formula_cluster") for market_ids in grouped.values() if len(market_ids) >= 2]
    for diagnostic in formula_report.get("formula_diagnostics", []):
        groups.append((list(diagnostic["market_ids"]), f"formula_diagnostic:{diagnostic['formula_relation']}"))
    for constraint in formula_report.get("formula_cluster_constraints", []):
        groups.append((list(constraint["source_market_ids"]), f"formula_cluster_constraint:{constraint['constraint_type']}"))
    return groups


def _edge_components(snapshot: GraphSnapshot) -> list[list[str]]:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in snapshot.edges:
        adjacency[edge.src_market_id].add(edge.dst_market_id)
        adjacency[edge.dst_market_id].add(edge.src_market_id)
    seen: set[str] = set()
    components: list[list[str]] = []
    for market_id in sorted(adjacency):
        if market_id in seen:
            continue
        stack = [market_id]
        component: list[str] = []
        seen.add(market_id)
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbor in sorted(adjacency[current]):
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
        components.append(component)
    return components


def _review_packet(
    snapshot: GraphSnapshot,
    formula_report: dict[str, Any],
    market_ids: list[str],
    reason: str,
    index: int,
) -> dict[str, Any]:
    known_edges = [
        edge.to_dict()
        for edge in snapshot.edges
        if edge.src_market_id in market_ids and edge.dst_market_id in market_ids
    ]
    known_exclusions = [
        exclusion.to_dict()
        for exclusion in snapshot.exclusion_sets
        if set(exclusion.member_market_ids).issubset(set(market_ids))
    ]
    formula_by_id = {formula["market_id"]: formula for formula in formula_report.get("formulas", [])}
    packet = {
        "packet_id": f"llm_packet:{snapshot.snapshot_id}:{index:04d}",
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": DIAGNOSTIC_HINT_ACTIONS,
        "llm_instruction": PACKET_INSTRUCTION,
        "why_selected": reason,
        "snapshot_id": snapshot.snapshot_id,
        "markets": [_packet_market(snapshot.nodes[market_id], formula_by_id.get(market_id)) for market_id in market_ids if market_id in snapshot.nodes],
        "known_graph_edges": known_edges,
        "known_exclusion_sets": known_exclusions,
        "formula_diagnostics": [
            diagnostic
            for diagnostic in formula_report.get("formula_diagnostics", [])
            if set(diagnostic["market_ids"]).issubset(set(market_ids))
        ],
        "formula_cluster_constraints": [
            constraint
            for constraint in formula_report.get("formula_cluster_constraints", [])
            if set(constraint["source_market_ids"]).issubset(set(market_ids))
        ],
        "llm_output_schema": _llm_output_schema_description(),
    }
    return packet


def _packet_market(node: MarketNode, formula: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "market_id": node.market_id,
        "venue": node.venue,
        "title": node.title,
        "question": node.canonical_text,
        "rules_or_description_excerpt": _excerpt(node.resolution_criteria),
        "category_or_event": {
            "entities": list(node.entities),
            "themes": list(node.themes),
            "observable": node.observable,
            "window": node.window,
            "resolution_date": node.resolution_date,
            "settlement_source": node.settlement_source,
        },
        "normalized_formula": formula,
        "diagnostic_probability_inputs": _packet_probability_inputs(node),
    }


def _packet_probability_inputs(node: MarketNode) -> dict[str, Any]:
    source = "yes_price" if node.yes_price is not None else "diagnostic_midpoint" if node.bid is not None and node.ask is not None else "missing_probability"
    probability = node.yes_price if node.yes_price is not None else (node.bid + node.ask) / 2 if node.bid is not None and node.ask is not None else None
    return {
        "probability": round(probability, 6) if probability is not None else None,
        "probability_source": source,
        "bid": node.bid,
        "ask": node.ask,
        "as_of": node.as_of.isoformat(),
        "non_actionable_input": True,
    }


def _llm_output_schema_description() -> dict[str, Any]:
    return {
        "required_fields": [
            "hypothesis_id",
            "market_ids",
            "relationship_type",
            "natural_language_claim",
            "directionality",
            "evidence_fields_used",
            "missing_evidence",
            "falsification_checks",
            "confidence_tier",
            "action_permission",
        ],
        "relationship_types": sorted(RELATIONSHIP_TYPES),
        "confidence_tiers": sorted(CONFIDENCE_TIERS),
        "event_classes": sorted(EVENT_CLASSES),
        "optional_fields": ["counter_hypothesis_id", "event_class"],
        "action_permission_required_value": False,
    }


def _validated_hypothesis_row(
    snapshot: GraphSnapshot,
    hypothesis: dict[str, Any],
    index: int,
    *,
    known_hypothesis_ids: set[str],
) -> dict[str, Any]:
    _validate_hypothesis_payload(snapshot, hypothesis, index)
    market_ids = list(hypothesis["market_ids"])
    relationship_type = hypothesis["relationship_type"]
    deterministic_support = _has_deterministic_support(snapshot, relationship_type, market_ids)
    metadata_blockers = _metadata_blockers(hypothesis, known_hypothesis_ids)
    blockers = _hypothesis_blockers(relationship_type, deterministic_support, hypothesis, metadata_blockers)
    classification = _classification(relationship_type, deterministic_support)
    confidence = _downgraded_confidence(
        relationship_type,
        hypothesis["confidence_tier"],
        deterministic_support,
        metadata_blockers,
    )
    strength_tier = _relationship_strength_tier(relationship_type, deterministic_support)
    downgrade_reasons = _downgrade_reasons(
        relationship_type,
        deterministic_support,
        hypothesis["confidence_tier"],
        confidence,
        metadata_blockers,
    )
    validation_status = "DOWNGRADED" if downgrade_reasons else "ACCEPTED"
    priority = "MANUAL_REVIEW" if relationship_type in STRUCTURAL_HYPOTHESES and confidence != "LOW" else "WATCH"
    row = {
        "hypothesis_id": hypothesis["hypothesis_id"],
        "counter_hypothesis_id": hypothesis.get("counter_hypothesis_id"),
        "source_packet_id": hypothesis.get("source_packet_id"),
        "source_market_ids": market_ids,
        "event_class": _event_class(hypothesis),
        "input_event_class": hypothesis.get("event_class"),
        "relationship_type": relationship_type,
        "natural_language_claim": hypothesis["natural_language_claim"],
        "directionality": hypothesis.get("directionality"),
        "evidence_fields_used": list(hypothesis["evidence_fields_used"]),
        "missing_evidence": list(hypothesis["missing_evidence"]),
        "falsification_checks": list(hypothesis["falsification_checks"]),
        "input_confidence_tier": hypothesis["confidence_tier"],
        "confidence_tier": confidence,
        "hypothesis_classification": classification,
        "deterministic_support": deterministic_support,
        "llm_evidence_role": "llm_hypothesis_advisory",
        "action_permission": False,
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": DIAGNOSTIC_HINT_ACTIONS,
        "diagnostic_priority": priority,
        "not_execution_reason": NOT_EXECUTION_REASON,
        "review_blockers": blockers,
        "relationship_strength_tier": strength_tier,
        "validation_status": validation_status,
        "downgrade_reason": list(downgrade_reasons),
        "original_llm_claim": _preserve_original_claim(hypothesis),
    }
    _validate_report_row(row, f"validated_hypotheses[{index}]")
    return row


def _relationship_strength_tier(relationship_type: str, deterministic_support: bool) -> str:
    if relationship_type == "SIMILARITY_ONLY_HYPOTHESIS":
        return "SIMILARITY_ONLY_RESEARCH"
    if relationship_type == "STALE_OR_LAG_HYPOTHESIS":
        return "STALE_OR_LAG_HYPOTHESIS_ONLY"
    if relationship_type == "THEMATIC_CORRELATION_HYPOTHESIS":
        return "THEMATIC_HYPOTHESIS_ONLY"
    if relationship_type == "PROBABILISTIC_RELATED_HYPOTHESIS":
        return "PROBABILISTIC_HYPOTHESIS_ONLY"
    if relationship_type in STRUCTURAL_HYPOTHESES:
        if deterministic_support:
            return "DETERMINISTIC_SUPPORTED"
        return "LOGICAL_HYPOTHESIS_ONLY"
    return "LOGICAL_HYPOTHESIS_ONLY"


def _downgrade_reasons(
    relationship_type: str,
    deterministic_support: bool,
    input_confidence: str,
    final_confidence: str,
    metadata_blockers: list[str] | None = None,
) -> list[str]:
    reasons: list[str] = []
    if relationship_type == "EXACT_EQUALITY_HYPOTHESIS" and not deterministic_support:
        reasons.append("exact_equality_text_only_downgrade")
    if relationship_type == "SIMILARITY_ONLY_HYPOTHESIS" and input_confidence != "LOW":
        reasons.append("similarity_only_capped_low")
    if (
        relationship_type in {
            "THEMATIC_CORRELATION_HYPOTHESIS",
            "PROBABILISTIC_RELATED_HYPOTHESIS",
            "STALE_OR_LAG_HYPOTHESIS",
        }
        and input_confidence == "HIGH"
    ):
        reasons.append("advisory_only_capped_medium")
    if (
        relationship_type in STRUCTURAL_HYPOTHESES
        and not deterministic_support
        and relationship_type != "EXACT_EQUALITY_HYPOTHESIS"
        and input_confidence == "HIGH"
        and final_confidence != "HIGH"
    ):
        reasons.append("structural_unproven_capped")
    reasons.extend(metadata_blockers or [])
    return sorted(set(reasons))


def _preserve_original_claim(hypothesis: dict[str, Any]) -> dict[str, Any]:
    return {
        "hypothesis_id": str(hypothesis.get("hypothesis_id", "")),
        "counter_hypothesis_id": hypothesis.get("counter_hypothesis_id"),
        "event_class": hypothesis.get("event_class"),
        "relationship_type": str(hypothesis.get("relationship_type", "")),
        "market_ids": [str(item) for item in hypothesis.get("market_ids", [])],
        "natural_language_claim": str(hypothesis.get("natural_language_claim", "")),
        "directionality": hypothesis.get("directionality"),
        "confidence_tier": str(hypothesis.get("confidence_tier", "")),
        "action_permission": bool(_action_permission_value(hypothesis)),
    }


def _action_permission_value(hypothesis: dict[str, Any]) -> Any:
    if "action_permission" in hypothesis:
        return hypothesis.get("action_permission")
    return hypothesis.get("trade_permission")


def _is_insufficient_evidence_hypothesis(hypothesis: dict[str, Any]) -> bool:
    if not isinstance(hypothesis, dict):
        return False
    relationship_type = hypothesis.get("relationship_type")
    return isinstance(relationship_type, str) and relationship_type.upper() in ABSTAIN_RELATIONSHIP_TYPES


def _validate_hypothesis_payload(snapshot: GraphSnapshot, hypothesis: dict[str, Any], index: int) -> None:
    path = f"hypotheses[{index}]"
    _reject_disallowed_values(hypothesis, path)
    required_core = {
        "hypothesis_id",
        "market_ids",
        "relationship_type",
        "natural_language_claim",
        "directionality",
        "evidence_fields_used",
        "missing_evidence",
        "falsification_checks",
        "confidence_tier",
    }
    missing = sorted(required_core - set(hypothesis))
    if missing:
        raise SchemaValidationError(f"{path} missing required fields {missing!r}")
    if "action_permission" not in hypothesis and "trade_permission" not in hypothesis:
        raise SchemaValidationError(f"{path} missing required field 'action_permission'")
    extra_actions = set(str(action).upper() for action in hypothesis.get("allowed_actions", []))
    if extra_actions & DISALLOWED_PERMISSION_ACTIONS:
        raise SchemaValidationError(f"{path}.allowed_actions contains disallowed permission")
    permission_value = _action_permission_value(hypothesis)
    if permission_value is not False:
        raise SchemaValidationError(f"{path}.action_permission must be false")
    if not isinstance(hypothesis["hypothesis_id"], str) or not hypothesis["hypothesis_id"]:
        raise SchemaValidationError(f"{path}.hypothesis_id must be a non-empty string")
    if hypothesis.get("counter_hypothesis_id") is not None and not isinstance(hypothesis.get("counter_hypothesis_id"), str):
        raise SchemaValidationError(f"{path}.counter_hypothesis_id must be a string or null")
    if hypothesis.get("event_class") is not None and not isinstance(hypothesis.get("event_class"), str):
        raise SchemaValidationError(f"{path}.event_class must be a string or null")
    market_ids = hypothesis["market_ids"]
    if not isinstance(market_ids, list) or len(market_ids) < 2 or not all(isinstance(item, str) and item for item in market_ids):
        raise SchemaValidationError(f"{path}.market_ids must contain at least two market ids")
    unknown = sorted(set(market_ids) - set(snapshot.nodes))
    if unknown:
        raise SchemaValidationError(f"{path}.market_ids contains unknown markets {unknown!r}")
    if hypothesis["relationship_type"] not in RELATIONSHIP_TYPES:
        raise SchemaValidationError(f"{path}.relationship_type is not supported")
    if not isinstance(hypothesis["natural_language_claim"], str) or not hypothesis["natural_language_claim"]:
        raise SchemaValidationError(f"{path}.natural_language_claim must be a non-empty string")
    if hypothesis["directionality"] is not None and not isinstance(hypothesis["directionality"], str):
        raise SchemaValidationError(f"{path}.directionality must be a string or null")
    for key in ["evidence_fields_used", "missing_evidence", "falsification_checks"]:
        value = hypothesis[key]
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            raise SchemaValidationError(f"{path}.{key} must be a list of strings")
    if hypothesis["confidence_tier"] not in CONFIDENCE_TIERS:
        raise SchemaValidationError(f"{path}.confidence_tier is not supported")


def _has_deterministic_support(snapshot: GraphSnapshot, relationship_type: str, market_ids: list[str]) -> bool:
    relation_pairs = {
        "EXACT_EQUALITY_HYPOTHESIS": {RelationshipType.SAME_EVENT_REWORDED},
        "COMPLEMENT_HYPOTHESIS": {RelationshipType.COMPLEMENT},
        "SUBSET_HYPOTHESIS": {RelationshipType.SUBSET, RelationshipType.IMPLICATION},
        "SUPERSET_HYPOTHESIS": {RelationshipType.SUPERSET, RelationshipType.IMPLICATION},
        "MUTUALLY_EXCLUSIVE_HYPOTHESIS": {RelationshipType.MUTUAL_EXCLUSION},
    }
    supported_relations = relation_pairs.get(relationship_type)
    if supported_relations:
        return any(
            edge.relation in supported_relations
            and edge.src_market_id in market_ids
            and edge.dst_market_id in market_ids
            and (relationship_type != "EXACT_EQUALITY_HYPOTHESIS" or edge.settlement_source_proven)
            for edge in snapshot.edges
        )
    if relationship_type == "EXHAUSTIVE_PARTITION_HYPOTHESIS":
        return any(
            set(exclusion.member_market_ids) == set(market_ids)
            and exclusion.completeness == ExclusionCompleteness.PARTITION
            for exclusion in snapshot.exclusion_sets
        )
    if relationship_type in {"THRESHOLD_LADDER_HYPOTHESIS", "RANGE_BUCKET_HYPOTHESIS"}:
        supported_constraint_types = {
            "THRESHOLD_LADDER_HYPOTHESIS": {"derived_threshold_ladder"},
            "RANGE_BUCKET_HYPOTHESIS": {"derived_range_bucket_partition"},
        }[relationship_type]
        formulas = build_formula_diagnostics_report(snapshot)
        return any(
            set(constraint["source_market_ids"]) == set(market_ids)
            and constraint.get("constraint_type") in supported_constraint_types
            for constraint in formulas.get("formula_cluster_constraints", [])
        )
    return False


def _metadata_blockers(hypothesis: dict[str, Any], known_hypothesis_ids: set[str]) -> list[str]:
    blockers: list[str] = []
    counter_id = hypothesis.get("counter_hypothesis_id")
    if counter_id is not None and counter_id not in known_hypothesis_ids:
        blockers.append("unknown_counter_hypothesis_id")
    event_class = hypothesis.get("event_class")
    if event_class is not None and event_class not in EVENT_CLASSES:
        blockers.append("unsupported_event_class")
    return sorted(set(blockers))


def _event_class(hypothesis: dict[str, Any]) -> str | None:
    event_class = hypothesis.get("event_class")
    if event_class is None:
        return None
    if event_class in EVENT_CLASSES:
        return event_class
    return "other"


def _hypothesis_blockers(
    relationship_type: str,
    deterministic_support: bool,
    hypothesis: dict[str, Any],
    metadata_blockers: list[str] | None = None,
) -> list[str]:
    blockers = set(DEFAULT_BLOCKERS)
    blockers.update(hypothesis.get("missing_evidence", []))
    blockers.update(metadata_blockers or [])
    if relationship_type == "EXACT_EQUALITY_HYPOTHESIS" and not deterministic_support:
        blockers.add("exact_equality_not_deterministically_supported")
    if relationship_type in ADVISORY_ONLY_HYPOTHESES:
        blockers.add("relationship_is_advisory_not_structural_proof")
    if relationship_type == "SIMILARITY_ONLY_HYPOTHESIS":
        blockers.add("similarity_only_research")
    if not hypothesis.get("falsification_checks"):
        blockers.add("missing_falsification_checks")
    return sorted(blockers)


def _classification(relationship_type: str, deterministic_support: bool) -> str:
    if relationship_type == "SIMILARITY_ONLY_HYPOTHESIS":
        return "research_only"
    if relationship_type in ADVISORY_ONLY_HYPOTHESES:
        return "advisory_only"
    if relationship_type == "EXACT_EQUALITY_HYPOTHESIS" and not deterministic_support:
        return "advisory_only_exact_claim_unproven"
    if deterministic_support:
        return "deterministic_review_supported"
    return "structural_hypothesis_requires_review"


def _downgraded_confidence(
    relationship_type: str,
    confidence: str,
    deterministic_support: bool,
    metadata_blockers: list[str] | None = None,
) -> str:
    if metadata_blockers:
        return "LOW"
    if relationship_type == "SIMILARITY_ONLY_HYPOTHESIS":
        return "LOW"
    if relationship_type in {
        "THEMATIC_CORRELATION_HYPOTHESIS",
        "PROBABILISTIC_RELATED_HYPOTHESIS",
        "STALE_OR_LAG_HYPOTHESIS",
    }:
        return "LOW" if confidence == "LOW" else "MEDIUM"
    if relationship_type in STRUCTURAL_HYPOTHESES and not deterministic_support:
        return "LOW" if confidence == "LOW" else "MEDIUM"
    return confidence


def _validate_report_row(row: dict[str, Any], path: str) -> None:
    if row.get("diagnostic_only") is not True:
        raise SchemaValidationError(f"{path}.diagnostic_only must be true")
    if row.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError(f"{path}.affects_evaluator_gates must be false")
    if row.get("allowed_actions") != DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError(f"{path}.allowed_actions must be WATCH and MANUAL_REVIEW only")
    if row.get("action_permission") is not False:
        raise SchemaValidationError(f"{path}.action_permission must be false")
    if row.get("relationship_type") not in RELATIONSHIP_TYPES:
        raise SchemaValidationError(f"{path}.relationship_type is not supported")
    if row.get("event_class") is not None and row.get("event_class") not in EVENT_CLASSES:
        raise SchemaValidationError(f"{path}.event_class is not supported")
    if row.get("counter_hypothesis_id") is not None and not isinstance(row.get("counter_hypothesis_id"), str):
        raise SchemaValidationError(f"{path}.counter_hypothesis_id must be a string or null")
    if row.get("llm_evidence_role") != "llm_hypothesis_advisory":
        raise SchemaValidationError(f"{path}.llm_evidence_role must be advisory")
    if row.get("confidence_tier") not in CONFIDENCE_TIERS:
        raise SchemaValidationError(f"{path}.confidence_tier is not supported")
    if row.get("diagnostic_priority") not in DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError(f"{path}.diagnostic_priority must be WATCH or MANUAL_REVIEW")
    for key in ["source_market_ids", "evidence_fields_used", "missing_evidence", "falsification_checks", "review_blockers"]:
        if not isinstance(row.get(key), list):
            raise SchemaValidationError(f"{path}.{key} must be a list")
    if row["relationship_type"] in ADVISORY_ONLY_HYPOTHESES and row["diagnostic_priority"] not in DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError(f"{path}.advisory hypothesis priority is invalid")
    if row["relationship_type"] == "SIMILARITY_ONLY_HYPOTHESIS" and row["confidence_tier"] != "LOW":
        raise SchemaValidationError(f"{path}.similarity_only must be low confidence")
    if row["relationship_type"] == "STALE_OR_LAG_HYPOTHESIS" and row["confidence_tier"] == "HIGH":
        raise SchemaValidationError(f"{path}.stale_or_lag must not be high confidence without deterministic temporal evidence")
    if (
        row["relationship_type"] in STRUCTURAL_HYPOTHESES
        and row.get("deterministic_support") is not True
        and row["confidence_tier"] == "HIGH"
    ):
        raise SchemaValidationError(f"{path}.unsupported structural hypothesis cannot be high confidence")
    tier = row.get("relationship_strength_tier")
    if tier not in RELATIONSHIP_STRENGTH_TIERS or tier == "REJECTED_UNSAFE":
        raise SchemaValidationError(f"{path}.relationship_strength_tier is not supported for an accepted row")
    if row.get("validation_status") not in {"ACCEPTED", "DOWNGRADED"}:
        raise SchemaValidationError(f"{path}.validation_status must be ACCEPTED or DOWNGRADED")
    if not isinstance(row.get("downgrade_reason"), list):
        raise SchemaValidationError(f"{path}.downgrade_reason must be a list")
    if row["validation_status"] == "ACCEPTED" and row["downgrade_reason"]:
        raise SchemaValidationError(f"{path}.downgrade_reason must be empty for ACCEPTED rows")
    if row["validation_status"] == "DOWNGRADED" and not row["downgrade_reason"]:
        raise SchemaValidationError(f"{path}.downgrade_reason is required for DOWNGRADED rows")
    original = row.get("original_llm_claim")
    if not isinstance(original, dict) or original.get("hypothesis_id") != row["hypothesis_id"]:
        raise SchemaValidationError(f"{path}.original_llm_claim must preserve the original hypothesis")
    if original.get("action_permission") is not False:
        raise SchemaValidationError(f"{path}.original_llm_claim.action_permission must be false")
    _reject_disallowed_values(row, path)


def _rejected_hypothesis_row(hypothesis: dict[str, Any], reason: str, index: int) -> dict[str, Any]:
    market_ids = list(hypothesis.get("market_ids", [])) if isinstance(hypothesis.get("market_ids"), list) else []
    original = {
        "hypothesis_id": str(hypothesis.get("hypothesis_id", "")),
        "counter_hypothesis_id": hypothesis.get("counter_hypothesis_id"),
        "event_class": hypothesis.get("event_class"),
        "relationship_type": str(hypothesis.get("relationship_type", "")) if isinstance(hypothesis.get("relationship_type"), str) else None,
        "market_ids": [str(item) for item in market_ids if isinstance(item, (str, int))],
        "natural_language_claim": str(hypothesis.get("natural_language_claim", "")) if isinstance(hypothesis.get("natural_language_claim"), str) else "",
        "directionality": hypothesis.get("directionality") if isinstance(hypothesis.get("directionality"), (str, type(None))) else None,
        "confidence_tier": str(hypothesis.get("confidence_tier", "")) if isinstance(hypothesis.get("confidence_tier"), str) else "",
        "action_permission": bool(_action_permission_value(hypothesis)) if isinstance(_action_permission_value(hypothesis), bool) else None,
    }
    return {
        "rejected_id": str(hypothesis.get("hypothesis_id") or f"rejected:{index}"),
        "source_market_ids": market_ids,
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": DIAGNOSTIC_HINT_ACTIONS,
        "rejection_reason": reason,
        "relationship_strength_tier": "REJECTED_UNSAFE",
        "validation_status": "REJECTED",
        "original_llm_claim": original,
    }


def _ignored_insufficient_evidence_row(hypothesis: dict[str, Any], index: int) -> dict[str, Any]:
    market_ids = list(hypothesis.get("market_ids", [])) if isinstance(hypothesis.get("market_ids"), list) else []
    original = {
        "hypothesis_id": str(hypothesis.get("hypothesis_id", "")),
        "counter_hypothesis_id": hypothesis.get("counter_hypothesis_id"),
        "event_class": hypothesis.get("event_class"),
        "relationship_type": str(hypothesis.get("relationship_type", "")),
        "market_ids": [str(item) for item in market_ids if isinstance(item, (str, int))],
        "natural_language_claim": str(hypothesis.get("natural_language_claim", "")) if isinstance(hypothesis.get("natural_language_claim"), str) else "",
        "directionality": hypothesis.get("directionality") if isinstance(hypothesis.get("directionality"), (str, type(None))) else None,
        "confidence_tier": str(hypothesis.get("confidence_tier", "")) if isinstance(hypothesis.get("confidence_tier"), str) else "",
        "action_permission": bool(_action_permission_value(hypothesis)) if isinstance(_action_permission_value(hypothesis), bool) else None,
    }
    return {
        "rejected_id": str(hypothesis.get("hypothesis_id") or f"insufficient-evidence:{index}"),
        "source_market_ids": market_ids,
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": DIAGNOSTIC_HINT_ACTIONS,
        "rejection_reason": "insufficient_evidence_abstain_marker",
        "relationship_strength_tier": "INSUFFICIENT_EVIDENCE_IGNORED",
        "validation_status": "IGNORED_INSUFFICIENT_EVIDENCE",
        "original_llm_claim": original,
    }


def _reject_secret_markers(payload: Any, path: str) -> None:
    findings: list[str] = []

    def visit(value: Any, nested_path: str) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                normalized_key = str(key).lower().replace("-", "_")
                if any(marker in normalized_key for marker in SECRET_MARKERS):
                    findings.append(f"{nested_path}.{key}" if nested_path else str(key))
                visit(nested, f"{nested_path}.{key}" if nested_path else str(key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{nested_path}[{index}]")
        elif isinstance(value, str):
            normalized = value.lower().replace("-", "_")
            if any(marker in normalized for marker in SECRET_MARKERS):
                findings.append(nested_path)

    visit(payload, path)
    if findings:
        raise SchemaValidationError(f"secret-like field present in LLM packet: {sorted(set(findings))}")


def _reject_disallowed_values(payload: Any, path: str) -> None:
    findings: list[str] = []

    def visit(value: Any, nested_path: str) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                normalized_key = str(key).upper().replace("-", "_")
                if normalized_key in DISALLOWED_VALUE_TOKENS:
                    findings.append(f"{nested_path}.{key}" if nested_path else str(key))
                visit(nested, f"{nested_path}.{key}" if nested_path else str(key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, str) and item.upper().replace("-", "_") in DISALLOWED_PERMISSION_ACTIONS:
                    findings.append(f"{nested_path}[{index}]")
                visit(item, f"{nested_path}[{index}]")
        elif isinstance(value, str):
            normalized = value.upper().replace("-", "_").replace(" ", "_")
            if any(token in normalized for token in DISALLOWED_VALUE_TOKENS):
                findings.append(nested_path)

    visit(payload, path)
    if findings:
        raise SchemaValidationError(f"LLM relationship payload contains disallowed output token: {sorted(set(findings))}")


def _excerpt(value: str, limit: int = 500) -> str:
    normalized = " ".join(str(value).split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


__all__ = [
    "ADVISORY_STRENGTH_TIERS",
    "EVENT_CLASSES",
    "LOGICAL_STRENGTH_TIERS",
    "RELATIONSHIP_STRENGTH_TIERS",
    "RELATIONSHIP_TYPES",
    "VALIDATION_STATUSES",
    "build_llm_relationship_hypotheses_report",
    "build_llm_relationship_review_packets",
    "import_llm_relationship_hypotheses",
    "validate_llm_relationship_hypotheses_report",
    "validate_llm_review_packets",
    "write_imported_llm_relationship_hypotheses_report",
    "write_llm_relationship_hypotheses_report",
    "write_llm_relationship_review_packets",
]
