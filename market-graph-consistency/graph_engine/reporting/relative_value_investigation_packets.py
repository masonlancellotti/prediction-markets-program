from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from graph_engine.thresholds import compile_market_formula_rows
from graph_engine.reporting.safety import find_prohibited_rendered_text
from graph_engine.reporting.schema_validation import (
    DIAGNOSTIC_HINT_ACTIONS,
    SchemaValidationError,
    _reject_prohibited_tokens,
)


REPORT_BANNER = (
    "Saved-file-only graph-to-relative-value investigation packets. "
    "Packets are diagnostic review inputs and do not affect evaluator gates."
)
WHY_REVIEW_ONLY = (
    "RV review requires independent payoff relationship proof, settlement basis, source/date alignment, "
    "fee model, depth, and freshness evidence outside this graph packet."
)
REQUIRED_EVIDENCE_BEFORE_RV_REVIEW = [
    "typed_keys",
    "settlement_source_proof",
    "payoff_relationship_proof",
    "complement_subset_exhaustive_proof",
    "orderbook_depth_freshness",
    "fee_model",
    "unit_currency_collateral_mechanics",
    "void_cancellation_rules",
]
DISALLOWED_SHORTCUTS = [
    "title_similarity",
    "llm_assertion",
    "midpoint_only_gap",
    "stale_quote",
    "reference_only_source",
]
ALLOWED_NEXT_ACTIONS = {
    "MANUAL_REVIEW",
    "BUILD_TYPED_KEY_EXTRACTOR",
    "BUILD_SETTLEMENT_SOURCE_REGISTRY_ENTRY",
    "FETCH_OR_ENRICH_ORDERBOOKS",
    "IGNORE_LOW_CONFIDENCE",
}
PACKET_KINDS = {
    "STRUCTURAL_VIOLATION",
    "LLM_ONLY",
    "SIMILARITY_RESEARCH",
    "BTC_BASIS_RISK_REVIEW",
    "FAIR_VALUE_REFERENCE_ONLY",
}
REFERENCE_ONLY_VENUES = {
    "the_odds_api",
    "odds_api",
}
STRUCTURAL_SIGNAL_TYPES = {
    "EXACT_RELATIONSHIP_WATCH",
    "COMPLEMENT_PRICE_DIVERGENCE",
    "SUBSET_SUPERSET_PRICE_VIOLATION",
    "THRESHOLD_LADDER_INVERSION",
    "RANGE_BUCKET_INCONSISTENCY",
    "EVENT_FAMILY_OUTLIER",
    "CROSS_VENUE_DIVERGENCE",
}
WEAK_SIGNAL_TYPES = {
    "SIMILARITY_ONLY_RESEARCH",
    "THEMATIC_CORRELATION_WATCH",
    "STALE_OR_LAG_WATCH",
}
STRUCTURAL_HYPOTHESIS_TYPES = {
    "EXACT_EQUALITY_HYPOTHESIS",
    "COMPLEMENT_HYPOTHESIS",
    "SUBSET_HYPOTHESIS",
    "SUPERSET_HYPOTHESIS",
    "MUTUALLY_EXCLUSIVE_HYPOTHESIS",
    "EXHAUSTIVE_PARTITION_HYPOTHESIS",
    "THRESHOLD_LADDER_HYPOTHESIS",
    "RANGE_BUCKET_HYPOTHESIS",
}
LOW_PRIORITY_CAP = 35.0
LLM_ONLY_CAP = 45.0
# Concrete settlement-source problems surfaced by the upstream compilers. The
# baseline "requires_settlement_source_proof" blocker is *always* present (it
# is a default review reminder), so routing must distinguish a real upstream
# gap from the universal reminder before recommending a registry build.
CONCRETE_SETTLEMENT_SOURCE_BLOCKERS = {
    "missing_settlement_source",
    "settlement_source_mismatch",
}
UNKNOWN_BASIS_SOURCES = {"", "unknown", "missing", "none", "null", "reference_only_source"}


def build_graph_to_relative_value_investigation_packets_report(
    *,
    trade_indicator_report: dict[str, Any] | None = None,
    probability_constraints_report: dict[str, Any] | None = None,
    llm_hypotheses_report: dict[str, Any] | None = None,
    signal_persistence_report: dict[str, Any] | None = None,
    event_entity_ontology_report: dict[str, Any] | None = None,
    max_packets: int = 25,
) -> dict[str, Any]:
    signals = _indicator_rows(trade_indicator_report)
    constraints = _constraint_rows(probability_constraints_report)
    hypotheses = _hypothesis_rows(llm_hypotheses_report)
    persistence = _persistence_by_item_id(signal_persistence_report)
    entity_ids_by_market = _entity_ids_by_market(event_entity_ontology_report)

    constraints_by_market_key = _group_by_market_key(constraints)
    hypotheses_by_market_key = _group_by_market_key(hypotheses)
    used_constraint_ids: set[str] = set()
    used_hypothesis_ids: set[str] = set()
    packets: list[dict[str, Any]] = []

    for signal in signals:
        market_key = _market_key(signal["markets_involved"])
        matching_constraints = constraints_by_market_key.get(market_key, [])
        matching_hypotheses = hypotheses_by_market_key.get(market_key, [])
        used_constraint_ids.update(row["constraint_id"] for row in matching_constraints)
        used_hypothesis_ids.update(row["hypothesis_id"] for row in matching_hypotheses)
        packets.append(
            _packet_from_components(
                source="signal",
                primary_id=signal["signal_id"],
                signal_rows=[signal],
                constraint_rows=matching_constraints,
                hypothesis_rows=matching_hypotheses,
                persistence_row=persistence.get(signal["signal_id"]),
                entity_ids_by_market=entity_ids_by_market,
            )
        )

    for constraint in constraints:
        if constraint["constraint_id"] in used_constraint_ids:
            continue
        matching_hypotheses = hypotheses_by_market_key.get(_market_key(constraint["markets_involved"]), [])
        used_hypothesis_ids.update(row["hypothesis_id"] for row in matching_hypotheses)
        packets.append(
            _packet_from_components(
                source="constraint",
                primary_id=constraint["constraint_id"],
                signal_rows=[],
                constraint_rows=[constraint],
                hypothesis_rows=matching_hypotheses,
                persistence_row=persistence.get(constraint["constraint_id"]),
                entity_ids_by_market=entity_ids_by_market,
            )
        )

    for hypothesis in hypotheses:
        if hypothesis["hypothesis_id"] in used_hypothesis_ids:
            continue
        packets.append(
            _packet_from_components(
                source="hypothesis",
                primary_id=hypothesis["hypothesis_id"],
                signal_rows=[],
                constraint_rows=[],
                hypothesis_rows=[hypothesis],
                persistence_row=None,
                entity_ids_by_market=entity_ids_by_market,
            )
        )

    packets = _dedupe_packets(packets)
    packets = sorted(
        packets,
        key=lambda row: (
            -row["priority_score"],
            _confidence_rank(row["confidence_tier"]),
            row["packet_id"],
        ),
    )[:max_packets]
    for index, packet in enumerate(packets, start=1):
        packet["diagnostic_rank"] = index

    action_counts = Counter(packet["allowed_next_action"] for packet in packets)
    kind_counts = Counter(packet["packet_kind"] for packet in packets)
    signal_counts = Counter(signal_type for packet in packets for signal_type in packet["signal_types"])
    report = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "banner": REPORT_BANNER,
        "packet_count": len(packets),
        "summary": {
            "total_packets": len(packets),
            "by_allowed_next_action": dict(sorted(action_counts.items())),
            "by_packet_kind": dict(sorted(kind_counts.items())),
            "by_signal_type": dict(sorted(signal_counts.items())),
            "high_confidence_count": sum(1 for packet in packets if packet["confidence_tier"] == "HIGH"),
            "midpoint_blocked_count": sum(
                1 for packet in packets if "midpoint_only_gap" in packet["packet_blockers"]
            ),
            "stale_or_missing_quote_count": sum(
                1 for packet in packets if "stale_or_missing_quote" in packet["packet_blockers"]
            ),
        },
        "investigation_packets": packets,
    }
    validate_graph_to_relative_value_investigation_packets_report(report)
    return report


def write_graph_to_relative_value_investigation_packets_report(
    *,
    json_output: Path | str,
    markdown_output: Path | str,
    trade_indicator_path: Path | str | None = None,
    probability_constraints_path: Path | str | None = None,
    llm_hypotheses_path: Path | str | None = None,
    signal_persistence_path: Path | str | None = None,
    event_entity_ontology_path: Path | str | None = None,
    event_entity_ontology_report: dict[str, Any] | None = None,
    max_packets: int = 25,
) -> dict[str, Any]:
    loaded_ontology_report = event_entity_ontology_report
    if loaded_ontology_report is None:
        loaded_ontology_report = _load_optional_report(event_entity_ontology_path)
    report = build_graph_to_relative_value_investigation_packets_report(
        trade_indicator_report=_load_optional_report(trade_indicator_path),
        probability_constraints_report=_load_optional_report(probability_constraints_path),
        llm_hypotheses_report=_load_optional_report(llm_hypotheses_path),
        signal_persistence_report=_load_optional_report(signal_persistence_path),
        event_entity_ontology_report=loaded_ontology_report,
        max_packets=max_packets,
    )
    markdown = render_graph_to_relative_value_investigation_packets_markdown(report)
    findings = find_prohibited_rendered_text(markdown)
    if findings:
        raise SchemaValidationError(
            "graph-to-relative-value packet Markdown contains prohibited vocabulary: "
            + ", ".join(findings)
        )

    json_path = Path(json_output)
    markdown_path = Path(markdown_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    return report


def validate_graph_to_relative_value_investigation_packets_report(report: dict[str, Any]) -> None:
    _reject_prohibited_tokens(report)
    if report.get("diagnostic_only") is not True:
        raise SchemaValidationError("graph-to-rv packet report must be diagnostic_only")
    if report.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError("graph-to-rv packet report must not affect evaluator gates")
    if report.get("allowed_actions") != list(DIAGNOSTIC_HINT_ACTIONS):
        raise SchemaValidationError("graph-to-rv packet report actions must be WATCH and MANUAL_REVIEW only")
    packets = report.get("investigation_packets")
    if not isinstance(packets, list):
        raise SchemaValidationError("investigation_packets must be a list")
    if report.get("packet_count") != len(packets):
        raise SchemaValidationError("packet_count must match investigation_packets")
    summary = report.get("summary")
    if not isinstance(summary, dict):
        raise SchemaValidationError("summary must be an object")
    if summary.get("total_packets") != len(packets):
        raise SchemaValidationError("summary.total_packets must match investigation_packets")
    for index, packet in enumerate(packets):
        _validate_packet(packet, f"investigation_packets[{index}]")


def render_graph_to_relative_value_investigation_packets_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Graph to Relative Value Investigation Packets",
        "",
        report["banner"],
        "",
        f"- Diagnostic only: `{str(report['diagnostic_only']).lower()}`",
        f"- Affects evaluator gates: `{str(report['affects_evaluator_gates']).lower()}`",
        f"- Allowed actions: `{', '.join(report['allowed_actions'])}`",
        f"- Packets: {summary['total_packets']}",
        f"- High confidence: {summary['high_confidence_count']}",
        f"- Midpoint blocked: {summary['midpoint_blocked_count']}",
        f"- Stale or missing quote: {summary['stale_or_missing_quote_count']}",
        "",
        "| Rank | Packet | Kind | Priority | Confidence | Next Action | Signals | Constraints | Markets | Entities | Why Interesting | Blockers |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for packet in report["investigation_packets"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(packet["diagnostic_rank"]),
                    _md(packet["packet_id"]),
                    _md(packet["packet_kind"]),
                    _md(packet["priority_score"]),
                    _md(packet["confidence_tier"]),
                    _md(packet["allowed_next_action"]),
                    _md(", ".join(packet["signal_types"]) or "none"),
                    _md(", ".join(packet["probability_constraint_types"]) or "none"),
                    _md(", ".join(packet["markets_involved"])),
                    _md(", ".join(packet["entity_ids"]) or "none"),
                    _md(packet["why_this_is_interesting"]),
                    _md(", ".join(packet["packet_blockers"]) or "none"),
                ]
            )
            + " |"
        )
    if not report["investigation_packets"]:
        lines.append("| none |  |  |  |  |  |  |  |  |  |  |  |")
    lines.append("")
    return "\n".join(lines)


def _indicator_rows(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(report, dict):
        return []
    rows: list[dict[str, Any]] = []
    for row in report.get("signals", []):
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "signal_id": str(row.get("signal_id") or ""),
                "signal_type": str(row.get("signal_type") or "UNKNOWN_SIGNAL"),
                "markets_involved": _string_list(row.get("markets_involved")),
                "venues_involved": _string_list(row.get("venues_involved")),
                "relationship_evidence_type": str(row.get("relationship_evidence_type") or ""),
                "severity_score": _number(row.get("severity_score")),
                "confidence_tier": _confidence(row.get("confidence_tier")),
                "probability_inputs": list(row.get("probability_inputs_used") or []),
                "observed_gap": None,
                "review_blockers": _string_list(row.get("review_blockers")),
                "market_formulas": _market_formula_rows(row),
            }
        )
    return [row for row in rows if row["signal_id"] and row["markets_involved"]]


def _constraint_rows(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(report, dict):
        return []
    rows: list[dict[str, Any]] = []
    for row in report.get("probability_constraints", []):
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "constraint_id": str(row.get("constraint_id") or ""),
                "constraint_type": str(row.get("constraint_type") or "UNKNOWN_CONSTRAINT"),
                "markets_involved": _string_list(row.get("markets_involved")),
                "venues_involved": _string_list(row.get("venues_involved")),
                "severity_score": _number(row.get("severity_score")),
                "confidence_tier": _confidence(row.get("confidence_tier")),
                "observed_gap": _optional_number(row.get("observed_gap")),
                "probability_inputs": list(row.get("probability_inputs") or []),
                "review_blockers": _string_list(row.get("review_blockers")),
                "midpoint_only": row.get("midpoint_only") is True,
                "has_stale_or_missing_quote": row.get("has_stale_or_missing_quote") is True,
                "market_formulas": _market_formula_rows(row),
            }
        )
    return [row for row in rows if row["constraint_id"] and row["markets_involved"]]


def _hypothesis_rows(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(report, dict):
        return []
    rows: list[dict[str, Any]] = []
    for row in report.get("validated_hypotheses", []):
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "hypothesis_id": str(row.get("hypothesis_id") or ""),
                "relationship_type": str(row.get("relationship_type") or "UNKNOWN_HYPOTHESIS"),
                "relationship_strength_tier": str(row.get("relationship_strength_tier") or ""),
                "markets_involved": _string_list(row.get("source_market_ids")),
                "confidence_tier": _confidence(row.get("confidence_tier")),
                "deterministic_support": row.get("deterministic_support") is True,
                "review_blockers": _string_list(row.get("review_blockers")),
            }
        )
    return [row for row in rows if row["hypothesis_id"] and row["markets_involved"]]


def _persistence_by_item_id(report: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(report, dict):
        return {}
    by_id: dict[str, dict[str, Any]] = {}
    for row in report.get("signal_persistence_rows", []):
        if not isinstance(row, dict):
            continue
        item_id = row.get("item_id")
        if isinstance(item_id, str) and item_id:
            by_id[item_id] = row
    return by_id


def _packet_from_components(
    *,
    source: str,
    primary_id: str,
    signal_rows: list[dict[str, Any]],
    constraint_rows: list[dict[str, Any]],
    hypothesis_rows: list[dict[str, Any]],
    persistence_row: dict[str, Any] | None,
    entity_ids_by_market: dict[str, set[str]],
) -> dict[str, Any]:
    markets = sorted(
        {
            market
            for row in [*signal_rows, *constraint_rows, *hypothesis_rows]
            for market in row.get("markets_involved", [])
        }
    )
    venues = sorted(
        {
            venue
            for row in [*signal_rows, *constraint_rows]
            for venue in row.get("venues_involved", [])
        }
    )
    if not venues:
        venues = sorted({market.split(":", 1)[0] for market in markets if ":" in market})
    signal_types = sorted({row["signal_type"] for row in signal_rows})
    constraint_types = sorted({row["constraint_type"] for row in constraint_rows})
    hypothesis_types = sorted({row["relationship_type"] for row in hypothesis_rows})
    component_rows = [*signal_rows, *constraint_rows, *hypothesis_rows]
    all_blockers = _combined_blockers(signal_rows, constraint_rows, hypothesis_rows)
    reference_only = _is_reference_only_packet(venues, component_rows, all_blockers)
    if reference_only:
        all_blockers = sorted(set(all_blockers) | {"reference_only_source"})
    formula_rows = _combined_market_formula_rows(signal_rows, constraint_rows)
    basis_risk = None if reference_only else _btc_basis_risk_context(markets, formula_rows, [*signal_rows, *constraint_rows])
    if basis_risk is not None:
        all_blockers = sorted(set(all_blockers) | {"requires_basis_source_distinction"})
    probability_inputs = _combined_probability_inputs(signal_rows, constraint_rows)
    observed_gap = _max_optional([row.get("observed_gap") for row in constraint_rows])
    severity = _max_number(
        [row.get("severity_score") for row in signal_rows]
        + [row.get("severity_score") for row in constraint_rows]
    )
    confidence_tier = _packet_confidence(signal_rows, constraint_rows, hypothesis_rows, all_blockers)
    priority_score = _priority_score(
        source=source,
        signal_types=signal_types,
        hypothesis_rows=hypothesis_rows,
        base_severity=severity,
        confidence_tier=confidence_tier,
        blockers=all_blockers,
        persistence_row=persistence_row,
    )
    packet_kind = _packet_kind(
        source=source,
        signal_types=signal_types,
        constraint_types=constraint_types,
        hypothesis_rows=hypothesis_rows,
        basis_risk=basis_risk,
        reference_only=reference_only,
    )
    if packet_kind == "BTC_BASIS_RISK_REVIEW":
        priority_score = min(priority_score, LLM_ONLY_CAP)
    if packet_kind == "FAIR_VALUE_REFERENCE_ONLY":
        priority_score = min(priority_score, LOW_PRIORITY_CAP)
    packet = {
        "packet_id": f"graph_rv_packet:{_safe_slug(primary_id)}",
        "packet_kind": packet_kind,
        "source_signal_ids": [row["signal_id"] for row in signal_rows],
        "source_constraint_ids": [row["constraint_id"] for row in constraint_rows],
        "source_hypothesis_ids": [row["hypothesis_id"] for row in hypothesis_rows],
        "markets_involved": markets,
        "venues_involved": venues,
        "signal_types": signal_types,
        "relationship_hypothesis_type": hypothesis_types[0] if hypothesis_types else None,
        "relationship_hypothesis_types": hypothesis_types,
        "probability_constraint_type": constraint_types[0] if constraint_types else None,
        "probability_constraint_types": constraint_types,
        "observed_gap": observed_gap,
        "severity_score": round(severity, 3),
        "confidence_tier": confidence_tier,
        "priority_score": priority_score,
        "persistence_status": _persistence_value(persistence_row, "persistence_status"),
        "persistence_count": int(persistence_row.get("persistence_count") or 0) if persistence_row else 0,
        "entity_ids": _packet_entity_ids(markets, entity_ids_by_market),
        "why_this_is_interesting": _interesting_reason(
            signal_types,
            constraint_types,
            hypothesis_types,
            severity,
            observed_gap,
            packet_kind=packet_kind,
            basis_risk=basis_risk,
        ),
        "why_review_only_yet": WHY_REVIEW_ONLY,
        "required_evidence_before_rv_review": list(REQUIRED_EVIDENCE_BEFORE_RV_REVIEW),
        "disallowed_shortcuts": list(DISALLOWED_SHORTCUTS),
        "allowed_next_action": _allowed_next_action(packet_kind, signal_types, hypothesis_types, all_blockers, confidence_tier),
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "packet_blockers": all_blockers,
        "probability_inputs_used": probability_inputs,
    }
    _validate_packet(packet, "investigation_packets[]")
    return packet


def _packet_kind(
    *,
    source: str,
    signal_types: list[str],
    constraint_types: list[str],
    hypothesis_rows: list[dict[str, Any]],
    basis_risk: dict[str, Any] | None,
    reference_only: bool,
) -> str:
    if reference_only:
        return "FAIR_VALUE_REFERENCE_ONLY"
    if basis_risk is not None:
        return "BTC_BASIS_RISK_REVIEW"
    if source == "hypothesis" and hypothesis_rows and not signal_types and not constraint_types:
        return "LLM_ONLY"
    if signal_types and all(signal_type in WEAK_SIGNAL_TYPES for signal_type in signal_types):
        return "SIMILARITY_RESEARCH"
    return "STRUCTURAL_VIOLATION"


def _combined_market_formula_rows(
    signal_rows: list[dict[str, Any]],
    constraint_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_market: dict[str, dict[str, Any]] = {}
    for row in [*signal_rows, *constraint_rows]:
        for item in row.get("market_formulas", []):
            if not isinstance(item, dict):
                continue
            market_id = item.get("market_id")
            if isinstance(market_id, str) and market_id and market_id not in by_market:
                by_market[market_id] = item
    return [by_market[market_id] for market_id in sorted(by_market)]


def _btc_basis_risk_context(
    markets: list[str],
    formula_rows: list[dict[str, Any]],
    component_rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    profiles = {row["market_id"]: row for row in formula_rows if isinstance(row.get("market_id"), str)}
    if len(markets) < 2 or any(market not in profiles for market in markets):
        return None
    profile_rows = [profiles[market] for market in markets]
    if any(row.get("family") != "BTC_THRESHOLD" or row.get("asset") != "BTC" for row in profile_rows):
        return None
    if any(_has_exact_relationship_marker(row) for row in component_rows):
        return None
    dates = {_text_or_empty(row.get("date")) for row in profile_rows}
    thresholds = {_threshold_key(row.get("threshold")) for row in profile_rows}
    comparators = {_text_or_empty(row.get("comparator")) for row in profile_rows}
    sources = {_normalise_basis_source(row.get("source")) for row in profile_rows}
    if "" in dates or None in thresholds or "" in comparators:
        return None
    if len(dates) != 1 or len(thresholds) != 1 or len(comparators) != 1:
        return None
    if any(not _known_basis_source(source) for source in sources) or len(sources) < 2:
        return None
    windows = {_text_or_empty(row.get("window")) for row in profile_rows if _text_or_empty(row.get("window"))}
    if len(windows) > 1:
        return None
    units = {_text_or_empty(row.get("unit")) for row in profile_rows if _text_or_empty(row.get("unit"))}
    if len(units) > 1:
        return None
    return {
        "sources": sorted(sources),
        "date": next(iter(dates)),
        "threshold": next(iter(thresholds)),
        "comparator": next(iter(comparators)),
        "window": next(iter(windows)) if windows else None,
    }


def _market_formula_rows(row: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ["market_formulas", "market_formula_rows", "formula_rows", "market_metadata", "markets_metadata"]:
        value = row.get(key)
        if isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))
    for key in ["probability_inputs_used", "probability_inputs"]:
        value = row.get(key)
        if isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))
    return compile_market_formula_rows(rows)


def _entity_ids_by_market(event_entity_ontology_report: dict[str, Any] | None) -> dict[str, set[str]]:
    by_market: dict[str, set[str]] = defaultdict(set)
    if not isinstance(event_entity_ontology_report, dict):
        return by_market
    for row in event_entity_ontology_report.get("ontology_rows", []):
        if not isinstance(row, dict):
            continue
        entity_id = row.get("entity_id")
        if not isinstance(entity_id, str) or not entity_id:
            continue
        for market_id in _string_list(row.get("source_market_ids")):
            by_market[market_id].add(entity_id)
    return by_market


def _packet_entity_ids(markets: list[str], entity_ids_by_market: dict[str, set[str]]) -> list[str]:
    return sorted({entity_id for market in markets for entity_id in entity_ids_by_market.get(market, set())})


def _priority_score(
    *,
    source: str,
    signal_types: list[str],
    hypothesis_rows: list[dict[str, Any]],
    base_severity: float,
    confidence_tier: str,
    blockers: list[str],
    persistence_row: dict[str, Any] | None,
) -> float:
    score = base_severity
    if confidence_tier == "HIGH":
        score += 6.0
    elif confidence_tier == "MEDIUM":
        score += 3.0
    if any(signal_type in STRUCTURAL_SIGNAL_TYPES for signal_type in signal_types):
        score += 5.0
    if persistence_row:
        status = str(persistence_row.get("persistence_status") or "")
        count = int(persistence_row.get("persistence_count") or 0)
        if status == "WORSENED_SIGNAL":
            score += 10.0
        elif status == "PERSISTENT_SIGNAL":
            score += min(8.0, 2.0 * max(1, count))
    if _has_midpoint_blocker(blockers):
        score -= 15.0
    if _has_stale_or_missing_blocker(blockers):
        score -= 15.0
    if _has_weak_relationship_blocker(blockers):
        score -= 12.0

    if signal_types and all(signal_type in WEAK_SIGNAL_TYPES for signal_type in signal_types):
        score = min(score, LOW_PRIORITY_CAP)
    if source == "hypothesis":
        score = min(max(score, 30.0), LLM_ONLY_CAP)
        if any(not row.get("deterministic_support") for row in hypothesis_rows):
            score = min(score, LLM_ONLY_CAP)
    if any(row.get("relationship_type") == "SIMILARITY_ONLY_HYPOTHESIS" for row in hypothesis_rows):
        score = min(score, 25.0)
    return round(max(0.0, min(100.0, score)), 3)


def _packet_confidence(
    signal_rows: list[dict[str, Any]],
    constraint_rows: list[dict[str, Any]],
    hypothesis_rows: list[dict[str, Any]],
    blockers: list[str],
) -> str:
    values = [row.get("confidence_tier") for row in [*signal_rows, *constraint_rows, *hypothesis_rows]]
    confidence = _strongest_confidence([value for value in values if isinstance(value, str)])
    if _has_midpoint_blocker(blockers) or _has_stale_or_missing_blocker(blockers):
        confidence = _min_confidence(confidence, "MEDIUM")
    if signal_rows and all(row["signal_type"] in WEAK_SIGNAL_TYPES for row in signal_rows):
        confidence = _min_confidence(confidence, "LOW")
    if hypothesis_rows and not signal_rows and not constraint_rows:
        if any(row.get("relationship_type") == "SIMILARITY_ONLY_HYPOTHESIS" for row in hypothesis_rows):
            confidence = "LOW"
        elif any(row.get("deterministic_support") is not True for row in hypothesis_rows):
            confidence = _min_confidence(confidence, "MEDIUM")
    return confidence


def _combined_blockers(
    signal_rows: list[dict[str, Any]],
    constraint_rows: list[dict[str, Any]],
    hypothesis_rows: list[dict[str, Any]],
) -> list[str]:
    blockers = {
        "requires_typed_key_verification",
        "requires_settlement_source_proof",
        "requires_payoff_relationship_proof",
        "requires_orderbook_depth_freshness",
        "requires_fee_model_review",
        "not_evaluator_input",
        "graph_packet_review_only",
    }
    for row in [*signal_rows, *constraint_rows, *hypothesis_rows]:
        blockers.update(_string_list(row.get("review_blockers")))
    if any(_row_uses_midpoint(row) for row in [*signal_rows, *constraint_rows]):
        blockers.add("midpoint_only_gap")
        blockers.add("midpoint_input_not_rv_ready")
    if any(_row_has_stale_or_missing_quote(row) for row in [*signal_rows, *constraint_rows]):
        blockers.add("stale_or_missing_quote")
    if any(row.get("signal_type") == "SIMILARITY_ONLY_RESEARCH" for row in signal_rows):
        blockers.add("title_similarity_not_structural_evidence")
    if any(row.get("relationship_strength_tier") not in {"", "DETERMINISTIC_SUPPORTED"} for row in hypothesis_rows):
        blockers.add("llm_assertion_not_deterministic_evidence")
    if any(
        row.get("relationship_type") in STRUCTURAL_HYPOTHESIS_TYPES and row.get("deterministic_support") is not True
        for row in hypothesis_rows
    ):
        blockers.add("structural_hypothesis_requires_deterministic_backing")
    return sorted(blockers)


def _combined_probability_inputs(
    signal_rows: list[dict[str, Any]],
    constraint_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_market: dict[str, dict[str, Any]] = {}
    for row in [*signal_rows, *constraint_rows]:
        for item in row.get("probability_inputs", []):
            if not isinstance(item, dict):
                continue
            market_id = item.get("market_id")
            if isinstance(market_id, str) and market_id and market_id not in by_market:
                by_market[market_id] = {
                    "market_id": market_id,
                    "probability": item.get("probability"),
                    "probability_source": item.get("probability_source"),
                    "bid_bound": item.get("bid_bound", item.get("bid")),
                    "ask_bound": item.get("ask_bound", item.get("ask")),
                    "midpoint": item.get("midpoint"),
                    "diagnostic_midpoint_used": item.get("diagnostic_midpoint_used", False),
                    "non_actionable_input": item.get("non_actionable_input", True),
                    "quote_age_seconds": item.get("quote_age_seconds"),
                }
    return [by_market[market_id] for market_id in sorted(by_market)]


def _interesting_reason(
    signal_types: list[str],
    constraint_types: list[str],
    hypothesis_types: list[str],
    severity: float,
    observed_gap: float | None,
    *,
    packet_kind: str,
    basis_risk: dict[str, Any] | None,
) -> str:
    if packet_kind == "FAIR_VALUE_REFERENCE_ONLY":
        return "Reference-only venue/source can inform fair-value context but is not a graph handoff target."
    if packet_kind == "BTC_BASIS_RISK_REVIEW" and basis_risk is not None:
        return (
            "BTC threshold terms align by asset, date, threshold, comparator, and available window, "
            "but settlement sources differ; route for manual basis-risk review."
        )
    if constraint_types:
        gap = f" with observed diagnostic gap {observed_gap:.6g}" if observed_gap is not None else ""
        return (
            f"Formal probability constraint review: {', '.join(constraint_types)}{gap}; "
            f"source severity {severity:.3g}."
        )
    if signal_types:
        return f"Graph signal review: {', '.join(signal_types)} with source severity {severity:.3g}."
    if hypothesis_types:
        return f"Offline hypothesis review: {', '.join(hypothesis_types)} requires deterministic evidence before RV review."
    return "Graph relationship cluster requires manual RV scoping."


def _allowed_next_action(
    packet_kind: str,
    signal_types: list[str],
    hypothesis_types: list[str],
    blockers: list[str],
    confidence_tier: str,
) -> str:
    if packet_kind == "FAIR_VALUE_REFERENCE_ONLY":
        return "IGNORE_LOW_CONFIDENCE"
    if packet_kind == "BTC_BASIS_RISK_REVIEW":
        return "MANUAL_REVIEW"
    if confidence_tier == "LOW" or any(signal_type == "SIMILARITY_ONLY_RESEARCH" for signal_type in signal_types):
        return "IGNORE_LOW_CONFIDENCE"
    if _has_midpoint_blocker(blockers) or _has_stale_or_missing_blocker(blockers):
        return "FETCH_OR_ENRICH_ORDERBOOKS"
    if hypothesis_types and not signal_types:
        return "BUILD_TYPED_KEY_EXTRACTOR"
    if CONCRETE_SETTLEMENT_SOURCE_BLOCKERS & set(blockers):
        return "BUILD_SETTLEMENT_SOURCE_REGISTRY_ENTRY"
    return "MANUAL_REVIEW"


def _dedupe_packets(packets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    for packet in packets:
        key = (
            tuple(packet["source_signal_ids"]),
            tuple(packet["source_constraint_ids"]),
            tuple(packet["source_hypothesis_ids"]),
            tuple(packet["markets_involved"]),
            tuple(packet["signal_types"]),
            tuple(packet["probability_constraint_types"]),
            packet["packet_kind"],
        )
        existing = by_key.get(key)
        if existing is None or packet["priority_score"] > existing["priority_score"]:
            by_key[key] = packet
    return list(by_key.values())


def _validate_packet(packet: dict[str, Any], path: str) -> None:
    required = [
        "packet_id",
        "packet_kind",
        "source_signal_ids",
        "source_constraint_ids",
        "markets_involved",
        "venues_involved",
        "signal_types",
        "relationship_hypothesis_type",
        "relationship_hypothesis_types",
        "probability_constraint_type",
        "probability_constraint_types",
        "observed_gap",
        "severity_score",
        "confidence_tier",
        "priority_score",
        "entity_ids",
        "why_this_is_interesting",
        "why_review_only_yet",
        "required_evidence_before_rv_review",
        "disallowed_shortcuts",
        "allowed_next_action",
        "diagnostic_only",
        "affects_evaluator_gates",
        "allowed_actions",
        "packet_blockers",
        "probability_inputs_used",
    ]
    for key in required:
        if key not in packet:
            raise SchemaValidationError(f"{path}.{key} is required")
    if packet["diagnostic_only"] is not True:
        raise SchemaValidationError(f"{path}.diagnostic_only must be true")
    if packet["affects_evaluator_gates"] is not False:
        raise SchemaValidationError(f"{path}.affects_evaluator_gates must be false")
    if packet["allowed_actions"] != list(DIAGNOSTIC_HINT_ACTIONS):
        raise SchemaValidationError(f"{path}.allowed_actions must be WATCH and MANUAL_REVIEW only")
    if packet["allowed_next_action"] not in ALLOWED_NEXT_ACTIONS:
        raise SchemaValidationError(f"{path}.allowed_next_action is not supported")
    if packet["packet_kind"] not in PACKET_KINDS:
        raise SchemaValidationError(f"{path}.packet_kind is not supported")
    if packet["confidence_tier"] not in {"HIGH", "MEDIUM", "LOW"}:
        raise SchemaValidationError(f"{path}.confidence_tier is not supported")
    for key in [
        "source_signal_ids",
        "source_constraint_ids",
        "source_hypothesis_ids",
        "entity_ids",
        "markets_involved",
        "venues_involved",
        "signal_types",
        "relationship_hypothesis_types",
        "probability_constraint_types",
        "required_evidence_before_rv_review",
        "disallowed_shortcuts",
        "packet_blockers",
        "probability_inputs_used",
    ]:
        if not isinstance(packet.get(key), list):
            raise SchemaValidationError(f"{path}.{key} must be a list")
    if not packet["markets_involved"]:
        raise SchemaValidationError(f"{path}.markets_involved must not be empty")
    if packet["required_evidence_before_rv_review"] != REQUIRED_EVIDENCE_BEFORE_RV_REVIEW:
        raise SchemaValidationError(f"{path}.required_evidence_before_rv_review is incomplete")
    if packet["disallowed_shortcuts"] != DISALLOWED_SHORTCUTS:
        raise SchemaValidationError(f"{path}.disallowed_shortcuts is incomplete")
    for key in ["severity_score", "priority_score"]:
        if not isinstance(packet[key], (int, float)) or isinstance(packet[key], bool):
            raise SchemaValidationError(f"{path}.{key} must be numeric")
        if not 0 <= float(packet[key]) <= 100:
            raise SchemaValidationError(f"{path}.{key} must be in [0, 100]")
    if packet["observed_gap"] is not None and (
        not isinstance(packet["observed_gap"], (int, float)) or isinstance(packet["observed_gap"], bool)
    ):
        raise SchemaValidationError(f"{path}.observed_gap must be numeric or null")
    if "SIMILARITY_ONLY_RESEARCH" in packet["signal_types"] and packet["priority_score"] > LOW_PRIORITY_CAP:
        raise SchemaValidationError(f"{path}.similarity-only packet priority is too high")
    if (
        packet["relationship_hypothesis_type"] == "EXACT_EQUALITY_HYPOTHESIS"
        and not packet["source_signal_ids"]
        and not packet["source_constraint_ids"]
        and packet["priority_score"] > LLM_ONLY_CAP
    ):
        raise SchemaValidationError(f"{path}.LLM-only exact hypothesis priority is too high")
    if packet["packet_kind"] == "BTC_BASIS_RISK_REVIEW":
        if packet["allowed_next_action"] != "MANUAL_REVIEW":
            raise SchemaValidationError(f"{path}.basis-risk packets must route to manual review")
        if "requires_basis_source_distinction" not in packet["packet_blockers"]:
            raise SchemaValidationError(f"{path}.basis-risk packets require basis-source distinction blocker")
        if packet["priority_score"] > LLM_ONLY_CAP:
            raise SchemaValidationError(f"{path}.basis-risk packet priority is too high")
    if packet["packet_kind"] == "FAIR_VALUE_REFERENCE_ONLY":
        if packet["allowed_next_action"] != "IGNORE_LOW_CONFIDENCE":
            raise SchemaValidationError(f"{path}.reference-only packets must route to ignore low confidence")
        if "reference_only_source" not in packet["packet_blockers"]:
            raise SchemaValidationError(f"{path}.reference-only packets require reference-only blocker")
        if packet["priority_score"] > LOW_PRIORITY_CAP:
            raise SchemaValidationError(f"{path}.reference-only packet priority is too high")
    _reject_prohibited_tokens(packet)


def _load_optional_report(path: Path | str | None) -> dict[str, Any] | None:
    if path is None:
        return None
    file_path = Path(path)
    if not file_path.exists():
        return None
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SchemaValidationError(f"{path} must contain a JSON object")
    return payload


def _group_by_market_key(rows: list[dict[str, Any]]) -> dict[tuple[str, ...], list[dict[str, Any]]]:
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_market_key(row["markets_involved"])].append(row)
    return grouped


def _market_key(markets: list[str]) -> tuple[str, ...]:
    return tuple(sorted(str(market) for market in markets))


def _is_reference_only_packet(
    venues: list[str],
    component_rows: list[dict[str, Any]],
    blockers: list[str],
) -> bool:
    if "reference_only_source" in blockers:
        return True
    if any("reference_only_source" in _string_list(row.get("review_blockers")) for row in component_rows):
        return True
    normalized_venues = [_normalise_venue(venue) for venue in venues]
    normalized_venues = [venue for venue in normalized_venues if venue]
    return bool(normalized_venues) and all(venue in REFERENCE_ONLY_VENUES for venue in normalized_venues)


def _normalise_venue(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower().replace("-", "_").replace(" ", "_").replace(".", "_")


def _safe_slug(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.:-]+", "_", value)
    text = text.strip("_")
    return text[:160] or "unknown"


def _row_uses_midpoint(row: dict[str, Any]) -> bool:
    if row.get("midpoint_only") is True:
        return True
    return any(
        isinstance(item, dict)
        and (
            item.get("diagnostic_midpoint_used") is True
            or item.get("probability_source") == "diagnostic_midpoint"
        )
        for item in row.get("probability_inputs", [])
    )


def _row_has_stale_or_missing_quote(row: dict[str, Any]) -> bool:
    if row.get("has_stale_or_missing_quote") is True:
        return True
    blockers = set(_string_list(row.get("review_blockers")))
    if blockers & {
        "missing_probability_input",
        "missing_bid_or_ask",
        "missing_quote_timestamp",
        "stale_quote",
    }:
        return True
    return any(
        isinstance(item, dict)
        and (
            item.get("probability") is None
            or item.get("quote_age_seconds") is None
        )
        for item in row.get("probability_inputs", [])
    )


def _has_midpoint_blocker(blockers: list[str]) -> bool:
    return bool({"midpoint_only_gap", "midpoint_input_not_rv_ready", "diagnostic_midpoint_not_actionable"} & set(blockers))


def _has_stale_or_missing_blocker(blockers: list[str]) -> bool:
    return bool(
        {
            "stale_or_missing_quote",
            "stale_quote",
            "missing_quote_timestamp",
            "missing_probability_input",
            "missing_bid_or_ask",
        }
        & set(blockers)
    )


def _has_weak_relationship_blocker(blockers: list[str]) -> bool:
    return bool(
        {
            "title_similarity_not_structural_evidence",
            "llm_assertion_not_deterministic_evidence",
            "structural_hypothesis_requires_deterministic_backing",
        }
        & set(blockers)
    )


def _confidence(value: Any) -> str:
    return str(value) if value in {"HIGH", "MEDIUM", "LOW"} else "LOW"


def _strongest_confidence(values: list[str]) -> str:
    for tier in ["HIGH", "MEDIUM", "LOW"]:
        if tier in values:
            return tier
    return "LOW"


def _min_confidence(left: str, right: str) -> str:
    order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    return left if order[left] <= order[right] else right


def _confidence_rank(value: str) -> int:
    return {"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(value, 3)


def _persistence_value(row: dict[str, Any] | None, key: str) -> Any:
    return row.get(key) if isinstance(row, dict) else None


def _max_number(values: list[Any]) -> float:
    parsed = [_optional_number(value) for value in values]
    numeric = [value for value in parsed if value is not None]
    return max(numeric) if numeric else 0.0


def _max_optional(values: list[Any]) -> float | None:
    parsed = [_optional_number(value) for value in values]
    numeric = [value for value in parsed if value is not None]
    return round(max(numeric), 6) if numeric else None


def _number(value: Any) -> float:
    numeric = _optional_number(value)
    return numeric if numeric is not None else 0.0


def _optional_number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(float(value), 6)
    return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, (str, int, float))]


def _first_text(row: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
    return ""


def _threshold_key(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(float(value), 8)
    return None


def _text_or_empty(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _normalise_basis_source(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _known_basis_source(value: str) -> bool:
    return bool(value) and value not in UNKNOWN_BASIS_SOURCES and not value.startswith(("unknown", "missing"))


def _has_exact_relationship_marker(row: dict[str, Any]) -> bool:
    text = " ".join(
        str(row.get(key) or "")
        for key in [
            "relationship_evidence_type",
            "signal_type",
            "constraint_type",
            "relationship_type",
        ]
    ).lower()
    return "typed_formula_match_review_only" in text


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


__all__ = [
    "ALLOWED_NEXT_ACTIONS",
    "DISALLOWED_SHORTCUTS",
    "PACKET_KINDS",
    "REFERENCE_ONLY_VENUES",
    "REQUIRED_EVIDENCE_BEFORE_RV_REVIEW",
    "build_graph_to_relative_value_investigation_packets_report",
    "render_graph_to_relative_value_investigation_packets_markdown",
    "validate_graph_to_relative_value_investigation_packets_report",
    "write_graph_to_relative_value_investigation_packets_report",
]
