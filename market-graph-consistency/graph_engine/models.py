from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class RelationshipType(str, Enum):
    POSITIVE_CORRELATION = "POSITIVE_CORRELATION"
    NEGATIVE_CORRELATION = "NEGATIVE_CORRELATION"
    IMPLICATION = "IMPLICATION"
    MUTUAL_EXCLUSION = "MUTUAL_EXCLUSION"
    SUBSET = "SUBSET"
    SUPERSET = "SUPERSET"
    PROXY = "PROXY"
    SAME_EVENT_REWORDED = "SAME_EVENT_REWORDED"
    AMBIGUOUS = "AMBIGUOUS"


class RelationshipSource(str, Enum):
    MANUAL = "manual"
    LLM = "llm"
    HEURISTIC = "heuristic"
    FIXTURE = "fixture"
    MIXED = "mixed"


class ExclusionCompleteness(str, Enum):
    PARTITION = "partition"
    SUBSET = "subset"


class ViolationKind(str, Enum):
    IMPLICATION_VIOLATION = "IMPLICATION_VIOLATION"
    SUBSET_OVER_SUPERSET = "SUBSET_OVER_SUPERSET"
    SUM_OVER_ONE = "SUM_OVER_ONE"
    REWORD_MISMATCH = "REWORD_MISMATCH"
    STALE_DIVERGENCE = "STALE_DIVERGENCE"
    NEGCORR_COMOVEMENT = "NEGCORR_COMOVEMENT"
    AMBIGUOUS_WORDING = "AMBIGUOUS_WORDING"


class Action(str, Enum):
    IGNORE = "IGNORE"
    WATCH = "WATCH"
    MANUAL_REVIEW = "MANUAL_REVIEW"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_datetime(value: datetime | str, field_name: str = "datetime") -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    else:
        raise TypeError(f"{field_name} must be a datetime or ISO string")

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed


def _enum_value(value: Enum | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    return value


def _parse_enum(enum_type: type[Enum], value: Enum | str, field_name: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except ValueError:
        try:
            return enum_type[str(value)]
        except KeyError as exc:
            raise ValueError(f"Invalid {field_name}: {value}") from exc


def ensure_probability(value: float | int | None, field_name: str) -> float | None:
    if value is None:
        return None
    numeric = float(value)
    if numeric < 0 or numeric > 1:
        raise ValueError(f"{field_name} must be in [0, 1], got {numeric}")
    return numeric


def ensure_nonnegative(value: float | int | None, field_name: str) -> float | None:
    if value is None:
        return None
    numeric = float(value)
    if numeric < 0:
        raise ValueError(f"{field_name} must be nonnegative, got {numeric}")
    return numeric


def coerce_bool(value: bool | str | int | None) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


@dataclass
class MarketNode:
    market_id: str
    venue: str
    title: str
    canonical_text: str
    resolution_criteria: str
    resolution_date: str
    entities: list[str]
    themes: list[str]
    yes_price: float | None
    no_price: float | None
    bid: float | None
    ask: float | None
    volume_24h: float | None
    liquidity_score: float | None
    as_of: datetime
    raw: dict[str, Any] = field(default_factory=dict)
    source_snapshot_id: str | None = None
    reference_only: bool = False
    settlement_source: str | None = None
    settlement_source_proven: bool = False
    observable: str | None = None
    window: str | None = None

    def __post_init__(self) -> None:
        if not self.market_id or ":" not in self.market_id:
            raise ValueError("market_id must be globally unique and include a venue prefix")
        self.yes_price = ensure_probability(self.yes_price, "yes_price")
        self.no_price = ensure_probability(self.no_price, "no_price")
        self.bid = ensure_probability(self.bid, "bid")
        self.ask = ensure_probability(self.ask, "ask")
        self.volume_24h = ensure_nonnegative(self.volume_24h, "volume_24h")
        self.liquidity_score = ensure_probability(self.liquidity_score, "liquidity_score")
        self.as_of = parse_datetime(self.as_of, "as_of")
        self.entities = list(self.entities)
        self.themes = list(self.themes)
        self.raw = dict(self.raw)
        self.reference_only = coerce_bool(self.reference_only)
        self.settlement_source = str(self.settlement_source) if self.settlement_source is not None else None
        self.settlement_source_proven = coerce_bool(self.settlement_source_proven)
        self.observable = str(self.observable) if self.observable is not None else None
        self.window = str(self.window) if self.window is not None else None

    @property
    def probability(self) -> float:
        if self.yes_price is not None:
            return self.yes_price
        if self.bid is not None and self.ask is not None:
            return (self.bid + self.ask) / 2
        raise ValueError(f"Market {self.market_id} has no usable yes probability")

    @property
    def spread(self) -> float:
        if self.bid is None or self.ask is None:
            return 0.0
        return max(0.0, self.ask - self.bid)

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_id": self.market_id,
            "venue": self.venue,
            "title": self.title,
            "canonical_text": self.canonical_text,
            "resolution_criteria": self.resolution_criteria,
            "resolution_date": self.resolution_date,
            "entities": list(self.entities),
            "themes": list(self.themes),
            "yes_price": self.yes_price,
            "no_price": self.no_price,
            "bid": self.bid,
            "ask": self.ask,
            "volume_24h": self.volume_24h,
            "liquidity_score": self.liquidity_score,
            "as_of": self.as_of.isoformat(),
            "raw": dict(self.raw),
            "source_snapshot_id": self.source_snapshot_id,
            "reference_only": self.reference_only,
            "settlement_source": self.settlement_source,
            "settlement_source_proven": self.settlement_source_proven,
            "observable": self.observable,
            "window": self.window,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MarketNode":
        return cls(**payload)


@dataclass
class RelationshipEdge:
    edge_id: str
    src_market_id: str
    dst_market_id: str
    relation: RelationshipType
    confidence: float
    source: RelationshipSource
    rationale: str
    evidence: list[str]
    created_at: datetime
    reviewed_by: str | None = None
    settlement_source_proven: bool = False
    observable: str | None = None
    window: str | None = None

    def __post_init__(self) -> None:
        if not self.edge_id:
            raise ValueError("edge_id is required")
        self.relation = _parse_enum(RelationshipType, self.relation, "relation")  # type: ignore[assignment]
        self.source = _parse_enum(RelationshipSource, self.source, "source")  # type: ignore[assignment]
        self.confidence = ensure_probability(self.confidence, "confidence") or 0.0
        if self.source == RelationshipSource.LLM and self.reviewed_by is None:
            self.confidence = min(self.confidence, 0.6)
        self.created_at = parse_datetime(self.created_at, "created_at")
        self.evidence = list(self.evidence)
        self.settlement_source_proven = coerce_bool(self.settlement_source_proven)
        self.observable = str(self.observable) if self.observable is not None else None
        self.window = str(self.window) if self.window is not None else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "src_market_id": self.src_market_id,
            "dst_market_id": self.dst_market_id,
            "relation": _enum_value(self.relation),
            "confidence": self.confidence,
            "source": _enum_value(self.source),
            "rationale": self.rationale,
            "evidence": list(self.evidence),
            "created_at": self.created_at.isoformat(),
            "reviewed_by": self.reviewed_by,
            "settlement_source_proven": self.settlement_source_proven,
            "observable": self.observable,
            "window": self.window,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RelationshipEdge":
        return cls(**payload)


@dataclass
class ExclusionSet:
    set_id: str
    member_market_ids: list[str]
    completeness: ExclusionCompleteness
    tolerance: float

    def __post_init__(self) -> None:
        if not self.set_id:
            raise ValueError("set_id is required")
        if len(self.member_market_ids) < 2:
            raise ValueError("exclusion set must contain at least two markets")
        self.member_market_ids = list(self.member_market_ids)
        self.completeness = _parse_enum(ExclusionCompleteness, self.completeness, "completeness")  # type: ignore[assignment]
        self.tolerance = ensure_probability(self.tolerance, "tolerance") or 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "set_id": self.set_id,
            "member_market_ids": list(self.member_market_ids),
            "completeness": _enum_value(self.completeness),
            "tolerance": self.tolerance,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ExclusionSet":
        return cls(**payload)


@dataclass
class GraphSnapshot:
    snapshot_id: str
    as_of: datetime
    nodes: dict[str, MarketNode]
    edges: list[RelationshipEdge] = field(default_factory=list)
    exclusion_sets: list[ExclusionSet] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.snapshot_id:
            raise ValueError("snapshot_id is required")
        self.as_of = parse_datetime(self.as_of, "as_of")
        self.nodes = dict(self.nodes)
        if set(self.nodes) != {node.market_id for node in self.nodes.values()}:
            raise ValueError("nodes must be keyed by their market_id")
        self.edges = list(self.edges)
        self.exclusion_sets = list(self.exclusion_sets)
        self.notes = list(self.notes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "as_of": self.as_of.isoformat(),
            "nodes": [self.nodes[key].to_dict() for key in sorted(self.nodes)],
            "edges": [edge.to_dict() for edge in self.edges],
            "exclusion_sets": [exclusion.to_dict() for exclusion in self.exclusion_sets],
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GraphSnapshot":
        nodes = {
            node_payload["market_id"]: MarketNode.from_dict(node_payload)
            for node_payload in payload.get("nodes", [])
        }
        return cls(
            snapshot_id=payload["snapshot_id"],
            as_of=payload["as_of"],
            nodes=nodes,
            edges=[RelationshipEdge.from_dict(edge) for edge in payload.get("edges", [])],
            exclusion_sets=[
                ExclusionSet.from_dict(exclusion)
                for exclusion in payload.get("exclusion_sets", [])
            ],
            notes=payload.get("notes", []),
        )


@dataclass
class ConsistencyViolation:
    violation_id: str
    snapshot_id: str
    kind: ViolationKind
    involved_market_ids: list[str]
    involved_edge_ids: list[str]
    magnitude: float
    raw_gap: float
    spread_adjusted_gap: float
    confidence: float
    action: Action
    explanation: str
    review_questions: list[str]
    blockers: list[str] = field(default_factory=list)
    edge_source: str | None = None
    reviewed_by: str | None = None
    review_status: str = "unreviewed"
    max_action_cap: str = "MANUAL_REVIEW"
    max_action_cap_reason: str = "diagnostic_only"

    def __post_init__(self) -> None:
        if not self.violation_id:
            raise ValueError("violation_id is required")
        self.kind = _parse_enum(ViolationKind, self.kind, "kind")  # type: ignore[assignment]
        self.action = _parse_enum(Action, self.action, "action")  # type: ignore[assignment]
        self.involved_market_ids = list(self.involved_market_ids)
        self.involved_edge_ids = list(self.involved_edge_ids)
        self.magnitude = max(0.0, float(self.magnitude))
        self.raw_gap = float(self.raw_gap)
        self.spread_adjusted_gap = float(self.spread_adjusted_gap)
        self.confidence = ensure_probability(self.confidence, "confidence") or 0.0
        self.review_questions = list(self.review_questions)
        self.blockers = list(self.blockers)
        self.edge_source = str(self.edge_source) if self.edge_source is not None else None
        self.reviewed_by = str(self.reviewed_by) if self.reviewed_by is not None else None
        self.review_status = str(self.review_status)
        self.max_action_cap = str(self.max_action_cap)
        self.max_action_cap_reason = str(self.max_action_cap_reason)

    @property
    def rank_score(self) -> float:
        return self.confidence * self.magnitude

    def to_dict(self) -> dict[str, Any]:
        return {
            "violation_id": self.violation_id,
            "snapshot_id": self.snapshot_id,
            "kind": _enum_value(self.kind),
            "involved_market_ids": list(self.involved_market_ids),
            "involved_edge_ids": list(self.involved_edge_ids),
            "magnitude": round(self.magnitude, 6),
            "magnitude_unit": "probability",
            "raw_gap": round(self.raw_gap, 6),
            "spread_adjusted_gap": round(self.spread_adjusted_gap, 6),
            "confidence": round(self.confidence, 6),
            "action": _enum_value(self.action),
            "explanation": self.explanation,
            "review_questions": list(self.review_questions),
            "blockers": list(self.blockers),
            "edge_source": self.edge_source,
            "reviewed_by": self.reviewed_by,
            "review_status": self.review_status,
            "max_action_cap": self.max_action_cap,
            "max_action_cap_reason": self.max_action_cap_reason,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ConsistencyViolation":
        return cls(**payload)
