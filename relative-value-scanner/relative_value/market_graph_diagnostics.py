from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
RELATION_TYPES = {
    "EXACT_SAME_PAYOFF",
    "COMPLEMENT",
    "SUBSET",
    "SUPERSET",
    "MUTUALLY_EXCLUSIVE",
    "EXHAUSTIVE_GROUP",
    "OVERLAP_NOT_EQUIVALENT",
    "CORRELATED_ONLY",
    "UNRELATED",
    "MANUAL_REVIEW",
}
ALLOWED_ACTIONS = {"WATCH", "MANUAL_REVIEW"}
DISCLAIMER = (
    "Fixture-backed relationship diagnostics only. The graph writes WATCH and "
    "MANUAL_REVIEW rows, does not fetch data, and does not modify scanner gates."
)


@dataclass(frozen=True)
class GraphMarket:
    market_id: str
    question: str
    domain: str
    group_id: str | None = None
    entity: str | None = None
    predicate: str | None = None
    comparator: str | None = None
    threshold: float | None = None
    unit: str | None = None
    deadline: str | None = None
    source: str | None = None
    outcome: str | None = None
    parent_group_id: str | None = None
    mutually_exclusive_group: str | None = None
    exhaustive_group: str | None = None
    group_complete: bool | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "GraphMarket":
        tags = payload.get("tags")
        return cls(
            market_id=str(payload["market_id"]),
            question=str(payload.get("question") or ""),
            domain=str(payload.get("domain") or ""),
            group_id=_string_or_none(payload.get("group_id")),
            entity=_string_or_none(payload.get("entity")),
            predicate=_string_or_none(payload.get("predicate")),
            comparator=_string_or_none(payload.get("comparator")),
            threshold=_float_or_none(payload.get("threshold")),
            unit=_string_or_none(payload.get("unit")),
            deadline=_string_or_none(payload.get("deadline")),
            source=_string_or_none(payload.get("source")),
            outcome=_string_or_none(payload.get("outcome")),
            parent_group_id=_string_or_none(payload.get("parent_group_id")),
            mutually_exclusive_group=_string_or_none(payload.get("mutually_exclusive_group")),
            exhaustive_group=_string_or_none(payload.get("exhaustive_group")),
            group_complete=payload.get("group_complete") if isinstance(payload.get("group_complete"), bool) else None,
            tags=tuple(str(tag) for tag in tags) if isinstance(tags, list) else tuple(),
        )


@dataclass(frozen=True)
class RelationshipEdge:
    source_market_id: str
    target_market_id: str
    relation_type: str
    direction: str
    hard_bound_type: str
    required_conditions: tuple[str, ...]
    blockers: tuple[str, ...]
    confidence: float
    source: str
    diagnostic_only: bool = True
    action: str = "WATCH"

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "source_market_id": self.source_market_id,
            "target_market_id": self.target_market_id,
            "relation_type": self.relation_type,
            "direction": self.direction,
            "hard_bound_type": self.hard_bound_type,
            "required_conditions": list(self.required_conditions),
            "blockers": list(self.blockers),
            "confidence": self.confidence,
            "source": self.source,
            "diagnostic_only": self.diagnostic_only,
            "action": self.action,
        }


DEFAULT_FIXTURE_MARKETS: tuple[dict[str, Any], ...] = (
    {
        "market_id": "mlb-world-series-cleveland",
        "question": "Will Cleveland win the World Series?",
        "domain": "sports",
        "group_id": "mlb-2026-cleveland",
        "entity": "cleveland baseball",
        "predicate": "wins_world_series",
        "parent_group_id": "mlb-2026",
        "source": "fixture",
    },
    {
        "market_id": "mlb-alcs-cleveland",
        "question": "Will Cleveland win the ALCS?",
        "domain": "sports",
        "group_id": "mlb-2026-cleveland",
        "entity": "cleveland baseball",
        "predicate": "wins_alcs",
        "parent_group_id": "mlb-2026",
        "source": "fixture",
    },
    {
        "market_id": "btc-over-120k-2026-06-30",
        "question": "Will BTC be above 120000 by 2026-06-30?",
        "domain": "crypto",
        "entity": "BTC",
        "predicate": "price_threshold",
        "comparator": ">",
        "threshold": 120000,
        "unit": "USD",
        "deadline": "2026-06-30",
        "source": "fixture_price_source",
    },
    {
        "market_id": "btc-over-100k-2026-06-30",
        "question": "Will BTC be above 100000 by 2026-06-30?",
        "domain": "crypto",
        "entity": "BTC",
        "predicate": "price_threshold",
        "comparator": ">",
        "threshold": 100000,
        "unit": "USD",
        "deadline": "2026-06-30",
        "source": "fixture_price_source",
    },
    {
        "market_id": "btc-not-over-100k-2026-06-30",
        "question": "Will BTC be at or below 100000 by 2026-06-30?",
        "domain": "crypto",
        "entity": "BTC",
        "predicate": "price_threshold",
        "comparator": "<=",
        "threshold": 100000,
        "unit": "USD",
        "deadline": "2026-06-30",
        "source": "fixture_price_source",
    },
    {
        "market_id": "election-candidate-a",
        "question": "Will Candidate A win the 2026 Example Election?",
        "domain": "election",
        "group_id": "example-election-2026",
        "outcome": "Candidate A",
        "mutually_exclusive_group": "example-election-2026",
        "exhaustive_group": "example-election-2026",
        "group_complete": True,
        "source": "fixture",
    },
    {
        "market_id": "election-candidate-b",
        "question": "Will Candidate B win the 2026 Example Election?",
        "domain": "election",
        "group_id": "example-election-2026",
        "outcome": "Candidate B",
        "mutually_exclusive_group": "example-election-2026",
        "exhaustive_group": "example-election-2026",
        "group_complete": True,
        "source": "fixture",
    },
    {
        "market_id": "election-candidate-c",
        "question": "Will Candidate C win the 2026 Example Election?",
        "domain": "election",
        "group_id": "example-election-2026",
        "outcome": "Candidate C",
        "mutually_exclusive_group": "example-election-2026",
        "exhaustive_group": "example-election-2026",
        "group_complete": True,
        "source": "fixture",
    },
    {
        "market_id": "award-nominee-a",
        "question": "Will Nominee A win the 2026 Example Award?",
        "domain": "award",
        "group_id": "example-award-2026",
        "outcome": "Nominee A",
        "mutually_exclusive_group": "example-award-2026",
        "exhaustive_group": "example-award-2026",
        "group_complete": False,
        "source": "fixture",
    },
    {
        "market_id": "award-nominee-b",
        "question": "Will Nominee B win the 2026 Example Award?",
        "domain": "award",
        "group_id": "example-award-2026",
        "outcome": "Nominee B",
        "mutually_exclusive_group": "example-award-2026",
        "exhaustive_group": "example-award-2026",
        "group_complete": False,
        "source": "fixture",
    },
    {
        "market_id": "cleveland-browns-win",
        "question": "Will the Cleveland Browns win?",
        "domain": "sports",
        "entity": "cleveland browns",
        "predicate": "wins_game",
        "source": "fixture",
        "tags": ["cleveland", "football"],
    },
    {
        "market_id": "cleveland-guardians-win",
        "question": "Will the Cleveland Guardians win?",
        "domain": "sports",
        "entity": "cleveland guardians",
        "predicate": "wins_game",
        "source": "fixture",
        "tags": ["cleveland", "baseball"],
    },
    {
        "market_id": "openai-ipo-before-2027",
        "question": "Will OpenAI IPO before 2027?",
        "domain": "companies",
        "entity": "OpenAI",
        "predicate": "ipo_timing",
        "deadline": "2027-01-01",
        "source": "fixture",
    },
    {
        "market_id": "openai-before-anthropic-ipo",
        "question": "Will OpenAI IPO before Anthropic?",
        "domain": "companies",
        "entity": "OpenAI Anthropic",
        "predicate": "ipo_ordering",
        "source": "fixture",
    },
    {
        "market_id": "nba-knicks-win-copy-a",
        "question": "Will New York Knicks win?",
        "domain": "sports",
        "group_id": "nba-knicks-cavaliers-2026",
        "entity": "new york knicks",
        "predicate": "wins_game",
        "deadline": "2026-05-24",
        "source": "official_box_score",
    },
    {
        "market_id": "nba-knicks-win-copy-b",
        "question": "Will New York Knicks win the game?",
        "domain": "sports",
        "group_id": "nba-knicks-cavaliers-2026",
        "entity": "new york knicks",
        "predicate": "wins_game",
        "deadline": "2026-05-24",
        "source": "official_box_score",
    },
)


def build_market_graph_diagnostics_files(
    *,
    fixture_path: Path | None = None,
    json_output_path: Path | None = None,
    markdown_output_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    generated_at = now or datetime.now(timezone.utc)
    _require_tz_aware(generated_at, "now")
    fixtures = _load_fixture_markets(fixture_path)
    payload = build_market_graph_diagnostics(fixtures, generated_at=generated_at, input_path=fixture_path)
    if json_output_path is not None:
        json_output_path.parent.mkdir(parents=True, exist_ok=True)
        json_output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    if markdown_output_path is not None:
        markdown_output_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_output_path.write_text(render_market_graph_diagnostics_markdown(payload), encoding="utf-8")
    return payload


def build_market_graph_diagnostics(
    fixture_markets: list[dict[str, Any]] | None = None,
    *,
    generated_at: datetime | None = None,
    input_path: Path | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    markets = [GraphMarket.from_mapping(row) for row in (fixture_markets or list(DEFAULT_FIXTURE_MARKETS))]
    edges = _dedupe_edges(_relationship_edges(markets))
    serialized_edges = [edge.to_report_dict() for edge in edges]
    relation_counts = Counter(edge.relation_type for edge in edges)
    action_counts = Counter(edge.action for edge in edges)
    blockers = Counter(blocker for edge in edges for blocker in edge.blockers)
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "fixture_market_graph_consistency_diagnostics",
        "generated_at": generated.isoformat(),
        "input": str(input_path) if input_path is not None else "built_in_fixture",
        "data_source_mode": "STATIC_FIXTURE",
        "live_fetch_attempted": False,
        "diagnostic_only": True,
        "allowed_actions": sorted(ALLOWED_ACTIONS),
        "relation_types": sorted(RELATION_TYPES),
        "market_count": len(markets),
        "edge_count": len(edges),
        "counts_by_relation_type": {key: relation_counts.get(key, 0) for key in sorted(RELATION_TYPES)},
        "counts_by_action": {key: action_counts.get(key, 0) for key in sorted(ALLOWED_ACTIONS)},
        "top_blockers": [{"blocker": key, "count": count} for key, count in blockers.most_common(10)],
        "markets": [_market_report(row) for row in markets],
        "edges": serialized_edges,
        "disclaimer": DISCLAIMER,
    }


def render_market_graph_diagnostics_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Market Graph Consistency Diagnostics",
        "",
        "Fixture-backed relationship diagnostics for deterministic market graph edges.",
        "",
        f"Rows: {payload.get('edge_count', 0)} edges across {payload.get('market_count', 0)} fixture markets.",
        f"Mode: {payload.get('data_source_mode')}. Live fetch attempted: {str(payload.get('live_fetch_attempted')).lower()}.",
        "",
        "| Relation | Action | Source | Target | Direction | Bound | Blockers |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for edge in payload.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(edge.get("relation_type")),
                    _md(edge.get("action")),
                    _md(edge.get("source_market_id")),
                    _md(edge.get("target_market_id")),
                    _md(edge.get("direction")),
                    _md(edge.get("hard_bound_type")),
                    _md(", ".join(edge.get("blockers") or []) or "none"),
                ]
            )
            + " |"
        )
    lines.extend(["", "This report is diagnostic only and does not modify scanner gates.", ""])
    return "\n".join(lines)


def _relationship_edges(markets: list[GraphMarket]) -> list[RelationshipEdge]:
    edges: list[RelationshipEdge] = []
    for i, left in enumerate(markets):
        for right in markets[i + 1 :]:
            edges.extend(_pair_edges(left, right))
    edges.extend(_exhaustive_group_edges(markets))
    return edges


def _pair_edges(left: GraphMarket, right: GraphMarket) -> list[RelationshipEdge]:
    if _exact_same_payoff(left, right):
        return [_edge(left, right, "EXACT_SAME_PAYOFF", "bidirectional", "equality", (), (), 0.98)]
    if _complement(left, right):
        return [_edge(left, right, "COMPLEMENT", "bidirectional", "sum_to_one_if_binary_complete", ("same entity, threshold, deadline, unit, source",), (), 0.96)]
    threshold_edges = _threshold_edges(left, right)
    if threshold_edges:
        return threshold_edges
    sports_scope_edges = _sports_scope_edges(left, right)
    if sports_scope_edges:
        return sports_scope_edges
    if _mutually_exclusive(left, right):
        return [
            _edge(
                left,
                right,
                "MUTUALLY_EXCLUSIVE",
                "bidirectional",
                "cannot_both_resolve_yes",
                ("same mutually exclusive group",),
                (),
                0.95,
                action="MANUAL_REVIEW",
            )
        ]
    if _unrelated_city_token(left, right):
        return [_edge(left, right, "UNRELATED", "none", "none", (), ("city_token_overlap_not_entity_match",), 0.99, action="MANUAL_REVIEW")]
    if _openai_ipo_overlap(left, right):
        return [
            _edge(
                left,
                right,
                "OVERLAP_NOT_EQUIVALENT",
                "none",
                "none",
                ("shared company and IPO token",),
                ("different_question_type_timing_vs_ordering",),
                0.9,
                action="MANUAL_REVIEW",
            )
        ]
    if _correlated_only(left, right):
        return [_edge(left, right, "CORRELATED_ONLY", "none", "none", ("same broad domain",), ("no_deterministic_payoff_bound",), 0.55, action="MANUAL_REVIEW")]
    return []


def _threshold_edges(left: GraphMarket, right: GraphMarket) -> list[RelationshipEdge]:
    if not _same_threshold_family(left, right):
        return []
    assert left.threshold is not None
    assert right.threshold is not None
    if left.comparator == ">" and right.comparator == ">":
        higher, lower = (left, right) if left.threshold > right.threshold else (right, left)
        return [
            _edge(higher, lower, "SUBSET", "source_implies_target", "upper_probability_bound", ("same entity, date, source, unit, greater-than comparator",), (), 0.98),
            _edge(lower, higher, "SUPERSET", "target_implies_source", "lower_probability_bound", ("same entity, date, source, unit, greater-than comparator",), (), 0.98),
        ]
    return []


def _sports_scope_edges(left: GraphMarket, right: GraphMarket) -> list[RelationshipEdge]:
    if left.domain != "sports" or right.domain != "sports":
        return []
    if left.group_id != right.group_id or left.entity != right.entity:
        return []
    if left.predicate == "wins_world_series" and right.predicate == "wins_alcs":
        return [
            _edge(left, right, "SUBSET", "source_implies_target", "upper_probability_bound", ("same team and season",), (), 0.96),
            _edge(right, left, "SUPERSET", "target_implies_source", "lower_probability_bound", ("same team and season",), (), 0.96),
        ]
    if left.predicate == "wins_alcs" and right.predicate == "wins_world_series":
        return [
            _edge(right, left, "SUBSET", "source_implies_target", "upper_probability_bound", ("same team and season",), (), 0.96),
            _edge(left, right, "SUPERSET", "target_implies_source", "lower_probability_bound", ("same team and season",), (), 0.96),
        ]
    return []


def _exhaustive_group_edges(markets: list[GraphMarket]) -> list[RelationshipEdge]:
    groups: dict[str, list[GraphMarket]] = {}
    for market in markets:
        if market.exhaustive_group:
            groups.setdefault(market.exhaustive_group, []).append(market)
    edges: list[RelationshipEdge] = []
    for group_id, rows in groups.items():
        complete = all(row.group_complete is True for row in rows)
        blockers = () if complete else ("exhaustive_group_not_marked_complete",)
        action = "WATCH" if complete else "MANUAL_REVIEW"
        confidence = 0.95 if complete else 0.6
        relation = "EXHAUSTIVE_GROUP" if complete else "MANUAL_REVIEW"
        for i, left in enumerate(rows):
            for right in rows[i + 1 :]:
                edges.append(
                    _edge(
                        left,
                        right,
                        relation,
                        "group_level",
                        "sum_to_one_only_if_complete",
                        (f"complete exhaustive group {group_id}",),
                        blockers,
                        confidence,
                        action=action,
                    )
                )
    return edges


def _edge(
    source: GraphMarket,
    target: GraphMarket,
    relation_type: str,
    direction: str,
    hard_bound_type: str,
    required_conditions: tuple[str, ...],
    blockers: tuple[str, ...],
    confidence: float,
    *,
    action: str = "WATCH",
) -> RelationshipEdge:
    if relation_type not in RELATION_TYPES:
        raise ValueError(f"unsupported relation type: {relation_type}")
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"unsupported graph diagnostic action: {action}")
    return RelationshipEdge(
        source_market_id=source.market_id,
        target_market_id=target.market_id,
        relation_type=relation_type,
        direction=direction,
        hard_bound_type=hard_bound_type,
        required_conditions=required_conditions,
        blockers=blockers,
        confidence=round(confidence, 6),
        source="fixture_deterministic_rules",
        action=action,
    )


def _dedupe_edges(edges: list[RelationshipEdge]) -> list[RelationshipEdge]:
    by_key = {(edge.source_market_id, edge.target_market_id, edge.relation_type, edge.direction): edge for edge in edges}
    return sorted(by_key.values(), key=lambda edge: (edge.source_market_id, edge.target_market_id, edge.relation_type, edge.direction))


def _exact_same_payoff(left: GraphMarket, right: GraphMarket) -> bool:
    return (
        left.domain,
        left.group_id,
        left.entity,
        left.predicate,
        left.comparator,
        left.threshold,
        left.unit,
        left.deadline,
        left.source,
        left.outcome,
    ) == (
        right.domain,
        right.group_id,
        right.entity,
        right.predicate,
        right.comparator,
        right.threshold,
        right.unit,
        right.deadline,
        right.source,
        right.outcome,
    ) and left.market_id != right.market_id


def _complement(left: GraphMarket, right: GraphMarket) -> bool:
    if not _same_threshold_family(left, right):
        return False
    return {left.comparator, right.comparator} == {">", "<="} and left.threshold == right.threshold


def _same_threshold_family(left: GraphMarket, right: GraphMarket) -> bool:
    return (
        left.predicate == right.predicate == "price_threshold"
        and left.entity == right.entity
        and left.deadline == right.deadline
        and left.source == right.source
        and left.unit == right.unit
        and left.threshold is not None
        and right.threshold is not None
    )


def _mutually_exclusive(left: GraphMarket, right: GraphMarket) -> bool:
    return bool(
        left.mutually_exclusive_group
        and left.mutually_exclusive_group == right.mutually_exclusive_group
        and left.outcome
        and right.outcome
        and left.outcome != right.outcome
    )


def _unrelated_city_token(left: GraphMarket, right: GraphMarket) -> bool:
    return {"cleveland"} <= (set(left.tags) & set(right.tags)) and left.entity != right.entity


def _openai_ipo_overlap(left: GraphMarket, right: GraphMarket) -> bool:
    predicates = {left.predicate, right.predicate}
    entities = f"{left.entity or ''} {right.entity or ''}".lower()
    return predicates == {"ipo_timing", "ipo_ordering"} and "openai" in entities and "anthropic" in entities


def _correlated_only(left: GraphMarket, right: GraphMarket) -> bool:
    return left.domain == right.domain and left.domain in {"companies", "crypto", "sports"} and left.entity != right.entity


def _load_fixture_markets(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return list(DEFAULT_FIXTURE_MARKETS)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"graph fixture file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"graph fixture JSON is invalid: {path}") from exc
    rows = payload.get("markets") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("graph fixture must be a list or an object with a markets list")
    return [row for row in rows if isinstance(row, dict)]


def _market_report(market: GraphMarket) -> dict[str, Any]:
    return {
        "market_id": market.market_id,
        "question": market.question,
        "domain": market.domain,
        "group_id": market.group_id,
        "entity": market.entity,
        "predicate": market.predicate,
        "diagnostic_only": True,
    }


def _string_or_none(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
