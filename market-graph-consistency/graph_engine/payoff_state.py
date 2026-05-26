"""Finite-state payoff compiler.

This module compiles fixture-defined market families into a finite-state payoff
representation.  It is strictly diagnostic-only: the compiler captures
``blockers`` whenever state definitions are missing, ambiguous, or fail to be
mutually exclusive and exhaustive.  Compiled artefacts feed the no-arb
consistency engine in :mod:`graph_engine.payoff_state_feasibility` and the
diagnostic report in :mod:`graph_engine.reporting.payoff_state_report`.

The compiler is intentionally distinct from
:mod:`graph_engine.bounded_noarb`.  ``bounded_noarb`` treats each fixture
family as an opaque vector and only checks aggregate inequality bounds.  The
``payoff_state`` compiler exposes per-state evidence, per-contract payoff
vectors, and required-review questions for every supported family type so that
manual reviewers can audit state coverage, payoff definitions, and settlement
basis before any human decision is made.

No output of this module may be used as evaluator evidence, as paper-trade
input, or as a claim of exact same-payoff equivalence.  Outputs are capped at
``WATCH`` and ``MANUAL_REVIEW`` and never reference executable size, profit,
fills, orders, or trade permission.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from graph_engine.models import GraphSnapshot, MarketNode


ALLOWED_ACTIONS = ["WATCH", "MANUAL_REVIEW"]
SUPPORTED_FAMILY_TYPES = {
    "exhaustive_group",
    "mutually_exclusive_group",
    "threshold_ladder",
    "range_bucket_partition",
    "child_parent_chain",
    "complement_pair",
    "formula_cluster_exact",
}
MIN_CONTRACTS = 2
MAX_CONTRACTS = 8
DEFAULT_TOLERANCE = 0.03
BID_ASK_INTERVAL = "BID_ASK_INTERVAL"
DIAGNOSTIC_MIDPOINT_FALLBACK = "DIAGNOSTIC_MIDPOINT_FALLBACK"


@dataclass(frozen=True)
class FiniteState:
    state_id: str
    state_description: str
    family_id: str
    exhaustive_membership: bool
    mutual_exclusion_membership: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_id": self.state_id,
            "state_description": self.state_description,
            "family_id": self.family_id,
            "exhaustive_membership": self.exhaustive_membership,
            "mutual_exclusion_membership": self.mutual_exclusion_membership,
        }


@dataclass(frozen=True)
class ContractPayoff:
    contract_id: str
    family_id: str
    payoff_by_state: dict[str, float]
    required_evidence_fields: list[str]
    blockers: list[str] = field(default_factory=list)
    observed_probability: float | None = None
    bid_bound: float | None = None
    ask_bound: float | None = None
    probability_input_mode: str = DIAGNOSTIC_MIDPOINT_FALLBACK
    probability_input_blockers: list[str] = field(default_factory=list)
    structural_role: str | None = None

    def __post_init__(self) -> None:
        blockers = set(self.probability_input_blockers)
        if self.bid_bound is not None and self.ask_bound is not None:
            blockers.discard("diagnostic_midpoint_used")
            blockers.discard("non_actionable_input")
            object.__setattr__(self, "probability_input_mode", BID_ASK_INTERVAL)
            object.__setattr__(self, "probability_input_blockers", sorted(blockers))
            return
        if self.observed_probability is not None:
            blockers.update({"diagnostic_midpoint_used", "non_actionable_input"})
        object.__setattr__(self, "probability_input_mode", DIAGNOSTIC_MIDPOINT_FALLBACK)
        object.__setattr__(self, "probability_input_blockers", sorted(blockers))

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "family_id": self.family_id,
            "payoff_by_state": {state: float(value) for state, value in self.payoff_by_state.items()},
            "required_evidence_fields": list(self.required_evidence_fields),
            "blockers": list(self.blockers),
            "observed_probability": (
                round(float(self.observed_probability), 6)
                if self.observed_probability is not None
                else None
            ),
            "bid_bound": round(float(self.bid_bound), 6) if self.bid_bound is not None else None,
            "ask_bound": round(float(self.ask_bound), 6) if self.ask_bound is not None else None,
            "probability_input_mode": self.probability_input_mode,
            "probability_input_blockers": list(self.probability_input_blockers),
            "structural_role": self.structural_role,
        }


@dataclass(frozen=True)
class PayoffMatrix:
    family_id: str
    family_type: str
    family_description: str
    states: list[FiniteState]
    contracts: list[ContractPayoff]
    structural_metadata: dict[str, Any]
    blockers: list[str]
    confidence_basis: dict[str, Any]

    @property
    def state_count(self) -> int:
        return len(self.states)

    @property
    def contract_count(self) -> int:
        return len(self.contracts)

    @property
    def is_ready_for_feasibility(self) -> bool:
        return not self.blockers

    def state_payoff_matrix(self) -> dict[str, list[float | None]]:
        matrix: dict[str, list[float | None]] = {}
        state_ids = [state.state_id for state in self.states]
        for contract in self.contracts:
            row: list[float | None] = []
            for state_id in state_ids:
                value = contract.payoff_by_state.get(state_id)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    row.append(float(value))
                else:
                    row.append(None)
            matrix[contract.contract_id] = row
        return matrix

    def to_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "family_type": self.family_type,
            "family_description": self.family_description,
            "state_count": self.state_count,
            "contract_count": self.contract_count,
            "states": [state.to_dict() for state in self.states],
            "contracts": [contract.to_dict() for contract in self.contracts],
            "state_payoff_matrix": self.state_payoff_matrix(),
            "structural_metadata": dict(self.structural_metadata),
            "blockers": list(self.blockers),
            "confidence_basis": dict(self.confidence_basis),
        }


def compile_payoff_families(snapshot: GraphSnapshot) -> list[PayoffMatrix]:
    """Build :class:`PayoffMatrix` records for every fixture-defined family.

    Families are identified by the ``payoff_state_family_id`` raw metadata key.
    Each family is validated against its declared ``payoff_state_family_type``
    and converted into a strict finite-state representation, with blockers
    captured whenever the fixture is incomplete or ambiguous.
    """

    grouped: dict[str, list[MarketNode]] = defaultdict(list)
    for node in snapshot.nodes.values():
        family_id = _family_id(node)
        if family_id:
            grouped[family_id].append(node)

    families: list[PayoffMatrix] = []
    for family_id in sorted(grouped):
        nodes = sorted(grouped[family_id], key=lambda node: node.market_id)
        matrix = _compile_family(family_id, nodes)
        families.append(matrix)
    return families


def _compile_family(family_id: str, nodes: list[MarketNode]) -> PayoffMatrix:
    metadata = [_metadata(node) for node in nodes]
    family_type = _first_text(metadata, "payoff_state_family_type") or "unknown"
    family_description = _first_text(metadata, "payoff_state_family_description") or family_id
    state_payload = _first_list_of_dicts(metadata, "payoff_state_states")
    structural_metadata = {
        "ladder_thresholds": _ladder_thresholds(metadata),
        "range_buckets": _range_buckets(metadata),
        "structural_roles": _roles(metadata),
        "evidence_keys": _first_list(metadata, "payoff_state_required_evidence_fields"),
    }
    structural_metadata = {key: value for key, value in structural_metadata.items() if value}

    states = _build_states(family_id, family_type, state_payload)
    contracts = _build_contracts(family_id, nodes, metadata, [state.state_id for state in states])
    blockers = sorted(
        set(
            _structural_blockers(family_id, family_type, nodes, metadata, states)
            + [blocker for contract in contracts for blocker in contract.blockers]
        )
    )
    confidence = _confidence_basis(family_type, blockers, len(states), len(contracts))
    return PayoffMatrix(
        family_id=family_id,
        family_type=family_type,
        family_description=family_description,
        states=states,
        contracts=contracts,
        structural_metadata=structural_metadata,
        blockers=blockers,
        confidence_basis=confidence,
    )


def _build_states(family_id: str, family_type: str, payload: list[dict[str, Any]]) -> list[FiniteState]:
    states: list[FiniteState] = []
    seen: set[str] = set()
    exhaustive_default = family_type in {"exhaustive_group", "threshold_ladder", "range_bucket_partition", "child_parent_chain"}
    for index, entry in enumerate(payload):
        state_id = entry.get("state_id")
        if not isinstance(state_id, str) or not state_id:
            continue
        if state_id in seen:
            continue
        seen.add(state_id)
        description = entry.get("state_description")
        if not isinstance(description, str) or not description:
            description = state_id
        states.append(
            FiniteState(
                state_id=state_id,
                state_description=description,
                family_id=family_id,
                exhaustive_membership=bool(entry.get("exhaustive_membership", exhaustive_default)),
                mutual_exclusion_membership=bool(entry.get("mutual_exclusion_membership", True)),
            )
        )
        if index >= 32:
            break
    return states


def _build_contracts(
    family_id: str,
    nodes: list[MarketNode],
    metadata: list[dict[str, Any]],
    state_ids: list[str],
) -> list[ContractPayoff]:
    contracts: list[ContractPayoff] = []
    for node, raw in zip(nodes, metadata):
        blockers: list[str] = []
        try:
            observed_probability: float | None = float(node.probability)
        except ValueError:
            observed_probability = None
            blockers.append(f"missing_probability:{node.market_id}")
        bid_bound = node.bid if isinstance(node.bid, (int, float)) and not isinstance(node.bid, bool) else None
        ask_bound = node.ask if isinstance(node.ask, (int, float)) and not isinstance(node.ask, bool) else None
        input_blockers: list[str] = []
        if bid_bound is not None and ask_bound is not None:
            probability_input_mode = BID_ASK_INTERVAL
        else:
            probability_input_mode = DIAGNOSTIC_MIDPOINT_FALLBACK
            if observed_probability is not None:
                input_blockers.extend(["diagnostic_midpoint_used", "non_actionable_input"])
        payoffs_payload = raw.get("payoff_state_payoffs")
        payoff_by_state: dict[str, float] = {}
        if not isinstance(payoffs_payload, dict):
            blockers.append(f"missing_payoff_vector:{node.market_id}")
        else:
            for state_id in state_ids:
                value = payoffs_payload.get(state_id)
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    blockers.append(f"missing_state_payoff:{node.market_id}:{state_id}")
                    continue
                if value < 0.0 or value > 1.0:
                    blockers.append(f"out_of_range_state_payoff:{node.market_id}:{state_id}")
                    continue
                payoff_by_state[state_id] = float(value)
        required_fields = _list_of_str(raw.get("payoff_state_required_evidence_fields"))
        if not required_fields:
            required_fields = _list_of_str(raw.get("payoff_state_required_evidence"))
        if not required_fields:
            required_fields = ["settlement_source", "settlement_window", "resolution_criteria"]
        contracts.append(
            ContractPayoff(
                contract_id=node.market_id,
                family_id=family_id,
                payoff_by_state=payoff_by_state,
                required_evidence_fields=sorted(set(required_fields)),
                blockers=sorted(set(blockers)),
                observed_probability=observed_probability,
                bid_bound=bid_bound,
                ask_bound=ask_bound,
                probability_input_mode=probability_input_mode,
                probability_input_blockers=sorted(set(input_blockers)),
                structural_role=_structural_role(raw),
            )
        )
    return contracts


def _structural_blockers(
    family_id: str,
    family_type: str,
    nodes: list[MarketNode],
    metadata: list[dict[str, Any]],
    states: list[FiniteState],
) -> list[str]:
    blockers: list[str] = []
    if family_type not in SUPPORTED_FAMILY_TYPES:
        blockers.append("unsupported_family_type")
    if len(nodes) < MIN_CONTRACTS or len(nodes) > MAX_CONTRACTS:
        blockers.append("contract_count_outside_supported_range")
    if not states:
        blockers.append("missing_state_definitions")
    else:
        if family_type == "exhaustive_group" and not all(state.exhaustive_membership for state in states):
            blockers.append("incomplete_exhaustive_state_definition")
        if not all(state.mutual_exclusion_membership for state in states):
            blockers.append("incomplete_mutually_exclusive_state_definition")
    if family_type == "child_parent_chain":
        roles = [_structural_role(raw) for raw in metadata]
        if "child" not in roles:
            blockers.append("missing_child_contract_role")
        if "parent" not in roles:
            blockers.append("missing_parent_contract_role")
    if family_type == "threshold_ladder":
        thresholds = [raw.get("payoff_state_threshold") for raw in metadata]
        if any(not isinstance(value, (int, float)) or isinstance(value, bool) for value in thresholds):
            blockers.append("missing_numeric_threshold")
        elif len({float(value) for value in thresholds if value is not None}) < len(nodes):
            blockers.append("duplicate_threshold_value")
    if family_type == "range_bucket_partition":
        buckets = [raw.get("payoff_state_range") for raw in metadata]
        parsed = [_parsed_range(bucket) for bucket in buckets]
        if any(item is None for item in parsed):
            blockers.append("missing_or_malformed_range_bucket")
        else:
            ordered = sorted(parsed, key=lambda item: item[0])  # type: ignore[index]
            for left, right in zip(ordered, ordered[1:]):
                if left[1] != right[0]:  # type: ignore[index]
                    if left[1] > right[0]:  # type: ignore[index]
                        blockers.append("range_bucket_overlap")
                    else:
                        blockers.append("range_bucket_gap")
                    break
    if family_type == "complement_pair":
        if len(nodes) != 2:
            blockers.append("complement_pair_requires_two_contracts")
        sides = [str(raw.get("payoff_state_side") or "YES").upper() for raw in metadata]
        if len(set(sides)) < 2:
            blockers.append("complement_pair_sides_not_opposed")
    if family_type == "formula_cluster_exact":
        keys = _first_list(metadata, "payoff_state_required_evidence_fields")
        if not keys:
            blockers.append("missing_required_evidence_for_formula_cluster")
    settlement_sources = {node.settlement_source for node in nodes if node.settlement_source}
    if len(settlement_sources) > 1:
        blockers.append("settlement_source_mismatch")
    if any(node.settlement_source is None for node in nodes):
        blockers.append("missing_settlement_source")
    return sorted(set(blockers))


def _confidence_basis(family_type: str, blockers: list[str], state_count: int, contract_count: int) -> dict[str, Any]:
    if blockers:
        return {
            "description": "Finite-state compilation blocked by missing or ambiguous fixture definitions.",
            "score": 0.2,
        }
    if family_type == "formula_cluster_exact":
        score = 0.7
        description = "Typed formula cluster compiled from fixture-declared evidence keys; equality requires manual review."
    elif family_type in {"exhaustive_group", "range_bucket_partition"}:
        score = 0.85
        description = "Fixture declares exhaustive states with full payoff vectors; review structural completeness."
    elif family_type == "threshold_ladder":
        score = 0.82
        description = "Fixture declares a monotonic threshold sequence with full payoff vectors; review comparator orientation."
    elif family_type == "child_parent_chain":
        score = 0.8
        description = "Fixture declares child/parent contracts with full payoff vectors; review subset boundary."
    elif family_type == "complement_pair":
        score = 0.78
        description = "Fixture declares complementary contracts; review settlement source equivalence."
    else:
        score = 0.75
        description = "Finite-state compilation succeeded for review-only diagnostics."
    score = min(1.0, max(0.0, score - 0.01 * max(0, state_count - contract_count)))
    return {"description": description, "score": round(score, 6)}


def _structural_role(raw: dict[str, Any]) -> str | None:
    value = raw.get("payoff_state_role") or raw.get("payoff_state_contract_role")
    if not isinstance(value, str) or not value:
        return None
    return value


def _ladder_thresholds(metadata: list[dict[str, Any]]) -> list[float]:
    values: list[float] = []
    for raw in metadata:
        value = raw.get("payoff_state_threshold")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values.append(float(value))
    return sorted(values, reverse=True)


def _range_buckets(metadata: list[dict[str, Any]]) -> list[list[float]]:
    buckets: list[list[float]] = []
    for raw in metadata:
        parsed = _parsed_range(raw.get("payoff_state_range"))
        if parsed is None:
            continue
        buckets.append([parsed[0], parsed[1]])
    return sorted(buckets, key=lambda item: item[0])


def _parsed_range(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    lower, upper = value
    if not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in (lower, upper)):
        return None
    lower_value = float(lower)
    upper_value = float(upper)
    if lower_value >= upper_value:
        return None
    return lower_value, upper_value


def _roles(metadata: list[dict[str, Any]]) -> list[str]:
    return [_structural_role(raw) or "" for raw in metadata]


def _metadata(node: MarketNode) -> dict[str, Any]:
    row = node.raw.get("normalized_row")
    if isinstance(row, dict):
        merged = dict(node.raw)
        merged.update(row)
        return merged
    return dict(node.raw)


def _family_id(node: MarketNode) -> str | None:
    value = _metadata(node).get("payoff_state_family_id")
    if isinstance(value, str) and value:
        return value
    return None


def _first_text(items: list[dict[str, Any]], key: str) -> str | None:
    for item in items:
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _first_list(items: list[dict[str, Any]], key: str) -> list[Any]:
    for item in items:
        value = item.get(key)
        if isinstance(value, list):
            return list(value)
    return []


def _first_list_of_dicts(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    for item in items:
        value = item.get(key)
        if isinstance(value, list) and all(isinstance(entry, dict) for entry in value):
            return list(value)
    return []


def _list_of_str(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]
