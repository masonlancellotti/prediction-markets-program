from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


ReviewStatus = Literal["WATCH", "MANUAL_REVIEW"]
SourceKind = Literal["executable_venue", "reference_only", "sportsbook", "unknown"]

BTC_REQUIRED_FIELDS = (
    "source_basis",
    "date_or_deadline",
    "threshold",
    "comparator",
    "observation_window",
    "reference_price_index",
)
FED_REQUIRED_FIELDS = (
    "fomc_meeting_identity",
    "decision_date_or_deadline",
    "source_basis",
    "rate_or_bp_condition",
    "settlement_wording",
)
REFERENCE_ONLY_SOURCE_KINDS = {"reference_only", "sportsbook"}
DEFAULT_ALLOWED_ACTIONS = ("WATCH", "MANUAL_REVIEW")


@dataclass(frozen=True)
class BTCThresholdContract:
    source_basis: str | None = None
    date_or_deadline: str | None = None
    threshold: str | None = None
    comparator: str | None = None
    observation_window: str | None = None
    reference_price_index: str | None = None
    unresolved_ambiguity: tuple[str, ...] = field(default_factory=tuple)
    source_kind: SourceKind = "unknown"
    title: str | None = None


@dataclass(frozen=True)
class FedFomcMeetingContract:
    fomc_meeting_identity: str | None = None
    decision_date_or_deadline: str | None = None
    source_basis: str | None = None
    rate_or_bp_condition: str | None = None
    settlement_wording: str | None = None
    unresolved_ambiguity: tuple[str, ...] = field(default_factory=tuple)
    source_kind: SourceKind = "unknown"
    title: str | None = None


def btc_threshold_contract_diagnostic(contract: BTCThresholdContract) -> dict[str, object]:
    missing_fields = _missing_fields(contract, BTC_REQUIRED_FIELDS)
    blockers = _common_blockers(contract.source_kind, missing_fields)
    if _broad_text_only(contract.title):
        blockers.append("broad_text_title_only")
    status = _fail_closed_status(blockers)
    return {
        "contract_type": "btc_threshold",
        "status": status,
        "allowed_actions": list(DEFAULT_ALLOWED_ACTIONS),
        "missing_required_fields": missing_fields,
        "blockers": blockers,
        "requirements": {
            "source_basis": contract.source_basis,
            "date_or_deadline": contract.date_or_deadline,
            "threshold": contract.threshold,
            "comparator": contract.comparator,
            "observation_window": contract.observation_window,
            "reference_price_index": contract.reference_price_index,
            "unresolved_ambiguity": list(contract.unresolved_ambiguity),
        },
        "title_similarity_settlement_equivalence": False,
        "paper_candidate_emitted": False,
        "possible_arbitrage_claim": False,
        "executable_leg_claim": False,
        "tradable_result_claim": False,
    }


def fed_fomc_contract_diagnostic(contract: FedFomcMeetingContract) -> dict[str, object]:
    missing_fields = _missing_fields(contract, FED_REQUIRED_FIELDS)
    blockers = _common_blockers(contract.source_kind, missing_fields)
    if _broad_text_only(contract.title):
        blockers.append("broad_text_title_only")
    status = _fail_closed_status(blockers)
    return {
        "contract_type": "fed_fomc_meeting",
        "status": status,
        "allowed_actions": list(DEFAULT_ALLOWED_ACTIONS),
        "missing_required_fields": missing_fields,
        "blockers": blockers,
        "requirements": {
            "fomc_meeting_identity": contract.fomc_meeting_identity,
            "decision_date_or_deadline": contract.decision_date_or_deadline,
            "source_basis": contract.source_basis,
            "rate_or_bp_condition": contract.rate_or_bp_condition,
            "settlement_wording": contract.settlement_wording,
            "unresolved_ambiguity": list(contract.unresolved_ambiguity),
        },
        "title_similarity_settlement_equivalence": False,
        "paper_candidate_emitted": False,
        "possible_arbitrage_claim": False,
        "executable_leg_claim": False,
        "tradable_result_claim": False,
    }


def broad_title_overlap_diagnostic(left_title: str, right_title: str) -> dict[str, object]:
    return {
        "status": "MANUAL_REVIEW",
        "allowed_actions": list(DEFAULT_ALLOWED_ACTIONS),
        "left_title": left_title,
        "right_title": right_title,
        "blockers": ["title_similarity_is_not_settlement_equivalence"],
        "title_similarity_settlement_equivalence": False,
        "paper_candidate_emitted": False,
        "possible_arbitrage_claim": False,
        "executable_leg_claim": False,
        "tradable_result_claim": False,
    }


def default_btc_fed_contract_diagnostics() -> dict[str, object]:
    btc = BTCThresholdContract(
        unresolved_ambiguity=(
            "Explicit source basis is required.",
            "Deadline timezone, numeric threshold, comparator, observation window, and reference index must be exact.",
        )
    )
    fed = FedFomcMeetingContract(
        unresolved_ambiguity=(
            "FOMC meeting identity and decision deadline timezone are required.",
            "Target-rate or basis-point condition and settlement wording must be exact.",
        )
    )
    return {
        "schema_version": 1,
        "source": "btc_fed_exact_contract_diagnostics_v1",
        "paper_candidate_count": 0,
        "diagnostics": [
            btc_threshold_contract_diagnostic(btc),
            fed_fomc_contract_diagnostic(fed),
        ],
        "safety": {
            "paper_candidate_emitted": False,
            "possible_arbitrage_claim": False,
            "executable_signal_claim": False,
            "tradable_result_claim": False,
            "title_similarity_used_as_settlement_equivalence": False,
        },
    }


def _missing_fields(contract: object, required_fields: tuple[str, ...]) -> list[str]:
    missing: list[str] = []
    for field_name in required_fields:
        value = getattr(contract, field_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(field_name)
    return missing


def _common_blockers(source_kind: SourceKind, missing_fields: list[str]) -> list[str]:
    blockers: list[str] = []
    if missing_fields:
        blockers.append("missing_required_contract_terms")
    if source_kind in REFERENCE_ONLY_SOURCE_KINDS:
        blockers.append("reference_only_source")
    if source_kind == "unknown":
        blockers.append("unknown_source_kind")
    return blockers


def _fail_closed_status(blockers: list[str]) -> ReviewStatus:
    if "reference_only_source" in blockers or "broad_text_title_only" in blockers:
        return "MANUAL_REVIEW"
    return "WATCH"


def _broad_text_only(title: str | None) -> bool:
    if not title:
        return False
    lowered = title.lower()
    broad_terms = ("by year-end", "year end", "above x", "date y")
    return any(term in lowered for term in broad_terms)
