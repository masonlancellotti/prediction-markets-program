from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
ALLOWED_HINT_ACTIONS = {"WATCH", "MANUAL_REVIEW"}
PROHIBITED_FIELD_TERMS = ("paper", "possible_arb", "pnl", "profit", "trade", "execution")
PROHIBITED_EXACT_RELATION_VALUES = {"EXACT_SAME_PAYOFF", "SAME_PAYOFF", "EQUIVALENT"}
PROHIBITED_TRUSTED_SOURCE_VALUES = {"same_payoff_board_v1"}
BANNER = "INFO ONLY: market graph hints are not paper-trade permission."


def explain_market_graph_diagnostics_files(
    *,
    graph_report_path: Path,
    json_output_path: Path,
    markdown_output_path: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    generated_at = now or datetime.now(timezone.utc)
    _require_tz_aware(generated_at, "now")
    graph_report = _load_json_object(graph_report_path, "graph_report")
    payload = build_market_graph_relative_value_hints(
        graph_report=graph_report,
        generated_at=generated_at,
        inputs={"graph_report": str(graph_report_path)},
        outputs={"json": str(json_output_path), "markdown": str(markdown_output_path)},
    )
    json_output_path.parent.mkdir(parents=True, exist_ok=True)
    json_output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_output_path.write_text(render_market_graph_relative_value_hints_markdown(payload), encoding="utf-8")
    return payload


def build_market_graph_relative_value_hints(
    *,
    graph_report: dict[str, Any],
    generated_at: datetime | None = None,
    inputs: dict[str, str] | None = None,
    outputs: dict[str, str] | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    source_report = copy.deepcopy(graph_report)
    _validate_graph_report(source_report)

    hints = [_hint_from_edge(index, edge) for index, edge in enumerate(source_report.get("edges") or [], start=1) if isinstance(edge, dict)]
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "market_graph_relative_value_hints_v1",
        "generated_at": generated.isoformat(),
        "diagnostic_only": True,
        "allowed_actions": sorted(ALLOWED_HINT_ACTIONS),
        "banner": BANNER,
        "inputs": inputs or {"graph_report": "<in-memory>"},
        "outputs": outputs or {},
        "hint_count": len(hints),
        "hints": hints,
        "safety": {
            "diagnostic_only": True,
            "info_only": True,
            "sets_same_payoff_true": False,
            "sets_contract_relationship_equivalent": False,
            "sets_same_payoff_board_v1_source": False,
            "emits_paper_candidate": False,
            "mutates_matcher_or_evaluator_output": False,
            "affects_evaluator_gates": False,
            "evaluator_trusted_relationship_source_added": False,
            "live_fetch_attempted": False,
            "execution_or_trading_logic_added": False,
        },
    }


def render_market_graph_relative_value_hints_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Market Graph Relative-Value Hints",
        "",
        str(payload.get("banner") or BANNER),
        "",
        f"Hints: {payload.get('hint_count', 0)}",
        "",
        "| Finding | Relation | Source | Target | Magnitude | Cap | Cap reason | Blockers |",
        "| --- | --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for hint in payload.get("hints") or []:
        if not isinstance(hint, dict):
            continue
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(hint.get("finding_id")),
                    _md(hint.get("relation_type")),
                    _md(hint.get("source_market_id")),
                    _md(hint.get("target_market_id")),
                    _md(hint.get("magnitude_probability")),
                    _md(hint.get("max_action_cap")),
                    _md(hint.get("max_action_cap_reason")),
                    _md(", ".join(hint.get("blockers") or []) or "none"),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def _validate_graph_report(payload: dict[str, Any]) -> None:
    if payload.get("diagnostic_only") is not True:
        raise ValueError("graph report must have diagnostic_only=true")
    allowed_actions = payload.get("allowed_actions")
    if not isinstance(allowed_actions, list):
        raise ValueError("graph report must include allowed_actions list")
    if set(str(action) for action in allowed_actions) - ALLOWED_HINT_ACTIONS:
        raise ValueError("graph report allowed_actions may only contain WATCH and MANUAL_REVIEW")
    _reject_prohibited_fields(payload)
    edges = payload.get("edges")
    if not isinstance(edges, list):
        raise ValueError("graph report must include edges list")
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        if edge.get("diagnostic_only") is not True:
            raise ValueError("every graph edge must have diagnostic_only=true")
        action = str(edge.get("action") or "")
        if action not in ALLOWED_HINT_ACTIONS:
            raise ValueError("graph edge action may only be WATCH or MANUAL_REVIEW")
        max_action_cap = edge.get("max_action_cap")
        if max_action_cap is not None and str(max_action_cap) not in ALLOWED_HINT_ACTIONS:
            raise ValueError("graph edge max_action_cap may only be WATCH or MANUAL_REVIEW")


def _reject_prohibited_fields(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(term in lowered for term in PROHIBITED_FIELD_TERMS):
                raise ValueError(f"graph report contains prohibited field: {path}.{key}")
            _reject_prohibited_fields(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_prohibited_fields(child, f"{path}[{index}]")
    elif isinstance(value, str):
        normalized = value.strip()
        if normalized in PROHIBITED_TRUSTED_SOURCE_VALUES:
            raise ValueError(f"graph report contains prohibited trusted source value: {path}")


def _hint_from_edge(index: int, edge: dict[str, Any]) -> dict[str, Any]:
    action = str(edge.get("action") or "WATCH")
    max_action_cap = str(edge.get("max_action_cap") or action)
    max_action_cap_reason = str(edge.get("max_action_cap_reason") or "graph_diagnostic_info_only")
    relation_type = str(edge.get("relation_type") or "")
    normalized_relation_type = relation_type.strip().upper()
    exact_like_relation = (
        normalized_relation_type in PROHIBITED_EXACT_RELATION_VALUES
        or "SAME_PAYOFF" in normalized_relation_type
    )
    blockers = [str(blocker) for blocker in edge.get("blockers") or []]
    if exact_like_relation and "graph_exact_same_payoff_not_trusted" not in blockers:
        blockers.append("graph_exact_same_payoff_not_trusted")
    return {
        "finding_id": str(edge.get("finding_id") or f"graph_hint_{index:04d}"),
        "relation_type": relation_type or None,
        "relation_type_trusted_for_same_payoff": False,
        "downgraded_from_exact_same_payoff_label": exact_like_relation,
        "relationship_promotion_allowed": False,
        "source_market_id": edge.get("source_market_id"),
        "target_market_id": edge.get("target_market_id"),
        "magnitude_probability": _probability_or_none(edge.get("magnitude_probability", edge.get("confidence"))),
        "max_action_cap": max_action_cap,
        "max_action_cap_reason": max_action_cap_reason,
        "blockers": blockers,
        "diagnostic_only": True,
        "allowed_actions": sorted(ALLOWED_HINT_ACTIONS),
        "info_only_hint": True,
        "sets_same_payoff_true": False,
        "sets_contract_relationship_equivalent": False,
        "sets_same_payoff_board_v1_source": False,
        "affects_evaluator_gates": False,
    }


def _probability_or_none(value: Any) -> float | None:
    try:
        probability = float(value)
    except (TypeError, ValueError):
        return None
    if probability < 0 or probability > 1:
        return None
    return round(probability, 6)


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"{label} file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} JSON is invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} JSON must be an object")
    return payload


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
