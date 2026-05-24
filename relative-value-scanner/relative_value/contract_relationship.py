from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


RELATIONSHIP_EQUIVALENT = "EQUIVALENT"
RELATIONSHIP_NEAR_EQUIVALENT = "NEAR_EQUIVALENT"
RELATIONSHIP_SUBSET = "SUBSET"
RELATIONSHIP_SUPERSET = "SUPERSET"
RELATIONSHIP_MUTUALLY_EXCLUSIVE = "MUTUALLY_EXCLUSIVE"
RELATIONSHIP_CORRELATED_PROXY = "CORRELATED_PROXY"
RELATIONSHIP_DIFFERENT_TIME_WINDOW = "DIFFERENT_TIME_WINDOW"
RELATIONSHIP_DIFFERENT_SETTLEMENT_SOURCE = "DIFFERENT_SETTLEMENT_SOURCE"
RELATIONSHIP_DIFFERENT_THRESHOLD = "DIFFERENT_THRESHOLD"
RELATIONSHIP_DIFFERENT_UNIT = "DIFFERENT_UNIT"
RELATIONSHIP_AMBIGUOUS = "AMBIGUOUS"
RELATIONSHIP_UNRELATED = "UNRELATED"

RELATIONSHIP_SOURCE_DETERMINISTIC_RULES = "deterministic_rules"

# Some relationship labels are reserved for planned manual, graph, API, or LLM-assisted
# classifiers. The deterministic first pass emits only labels it can support directly.
ALL_RELATIONSHIPS = (
    RELATIONSHIP_EQUIVALENT,
    RELATIONSHIP_NEAR_EQUIVALENT,
    RELATIONSHIP_SUBSET,
    RELATIONSHIP_SUPERSET,
    RELATIONSHIP_MUTUALLY_EXCLUSIVE,
    RELATIONSHIP_CORRELATED_PROXY,
    RELATIONSHIP_DIFFERENT_TIME_WINDOW,
    RELATIONSHIP_DIFFERENT_SETTLEMENT_SOURCE,
    RELATIONSHIP_DIFFERENT_THRESHOLD,
    RELATIONSHIP_DIFFERENT_UNIT,
    RELATIONSHIP_AMBIGUOUS,
    RELATIONSHIP_UNRELATED,
)

_UNIT_MISMATCH_REASONS = {
    "different_unit",
    "unit_mismatch_not_accepted",
    "polymarket_shares_vs_kalshi_contracts_not_normalized",
}
_TIME_WINDOW_REASONS = {
    "settlement_delta_exceeds_limit",
    "settlement_time_missing_or_naive",
}


@dataclass(frozen=True)
class ContractRelationship:
    relationship: str
    same_payoff: bool
    confidence: float
    blocking_reasons: tuple[str, ...]
    manual_review_required: bool
    source: str = RELATIONSHIP_SOURCE_DETERMINISTIC_RULES

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "relationship": self.relationship,
            "same_payoff": self.same_payoff,
            "confidence": self.confidence,
            "blocking_reasons": list(self.blocking_reasons),
            "manual_review_required": self.manual_review_required,
            "source": self.source,
        }


def classify_contract_relationship(
    reasons: Iterable[str] | None = None,
    *,
    unit_mismatch_reason: str | None = None,
) -> ContractRelationship:
    reason_set = _normalized_reason_set(reasons)
    if unit_mismatch_reason:
        reason_set.add(str(unit_mismatch_reason))

    blocking_reasons = _relationship_blocking_reasons(reason_set)
    if "sports_team_alias_mismatch" in reason_set:
        return _result(RELATIONSHIP_MUTUALLY_EXCLUSIVE, False, 0.95, blocking_reasons)
    if "sports_competition_scope_mismatch" in reason_set:
        return _result(RELATIONSHIP_SUBSET, False, 0.95, blocking_reasons)
    if "ambiguous_wording" in reason_set:
        return _result(RELATIONSHIP_AMBIGUOUS, False, 0.5, blocking_reasons)
    if "different_threshold" in reason_set:
        return _result(RELATIONSHIP_DIFFERENT_THRESHOLD, False, 0.8, blocking_reasons)
    if "different_settlement_source" in reason_set:
        return _result(RELATIONSHIP_DIFFERENT_SETTLEMENT_SOURCE, False, 0.8, blocking_reasons)
    if reason_set & _TIME_WINDOW_REASONS:
        return _result(RELATIONSHIP_DIFFERENT_TIME_WINDOW, False, 0.75, blocking_reasons)
    if reason_set & _UNIT_MISMATCH_REASONS:
        return _result(RELATIONSHIP_DIFFERENT_UNIT, False, 0.9, blocking_reasons)
    return _result(RELATIONSHIP_NEAR_EQUIVALENT, False, 0.4, blocking_reasons, manual_review_required=True)


def report_blocking_reasons(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    reasons = payload.get("blocking_reasons")
    if not isinstance(reasons, list):
        return []
    return [str(reason) for reason in reasons if reason is not None]


def _result(
    relationship: str,
    same_payoff: bool,
    confidence: float,
    blocking_reasons: list[str],
    manual_review_required: bool | None = None,
) -> ContractRelationship:
    return ContractRelationship(
        relationship=relationship,
        same_payoff=same_payoff,
        confidence=round(confidence, 6),
        blocking_reasons=tuple(sorted(set(blocking_reasons))),
        manual_review_required=bool(blocking_reasons) if manual_review_required is None else manual_review_required,
    )


def _normalized_reason_set(reasons: Iterable[str] | None) -> set[str]:
    if reasons is None:
        return set()
    return {str(reason) for reason in reasons if reason is not None}


def _relationship_blocking_reasons(reason_set: set[str]) -> list[str]:
    blocking_reasons: list[str] = []
    # This allowlist is intentionally relationship-level only. Do not include
    # pure data-quality, pricing, or evaluator reasons such as stale quotes,
    # missing orderbooks, no positive gap, or estimated net below minimum.
    for reason in (
        "sports_competition_scope_mismatch",
        "sports_team_alias_mismatch",
        "ambiguous_wording",
        "different_threshold",
        "different_settlement_source",
        "settlement_delta_exceeds_limit",
        "settlement_time_missing_or_naive",
        "unit_mismatch_not_accepted",
        "polymarket_shares_vs_kalshi_contracts_not_normalized",
        "different_unit",
        "relationship_same_payoff_not_proven",
    ):
        if reason in reason_set:
            blocking_reasons.append(reason)
    return blocking_reasons
