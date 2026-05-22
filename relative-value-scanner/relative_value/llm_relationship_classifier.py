from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from relative_value.contract_relationship import (
    ALL_RELATIONSHIPS,
    ContractRelationship,
    RELATIONSHIP_EQUIVALENT,
    RELATIONSHIP_NEAR_EQUIVALENT,
)


LLM_RELATIONSHIP_SOURCE = "llm_review_proposal"
LLM_ALLOWED_FIELDS = frozenset(
    {
        "proposed_relationship",
        "confidence",
        "rationale",
        "extracted_terms",
        "uncertainties",
        "manual_review_required",
        "evidence_references",
    }
)
LLM_FORBIDDEN_FIELDS = frozenset({"same_payoff", "action", "trade_permission"})
LLM_FORBIDDEN_TOKENS = frozenset({"EQUIVALENT", "PAPER_CANDIDATE", "PAPER", "POSSIBLE_ARB"})
LLM_ALLOWED_RELATIONSHIPS = tuple(relationship for relationship in ALL_RELATIONSHIPS if relationship != RELATIONSHIP_EQUIVALENT)
_TOKEN_RE = re.compile(r"[A-Z_]+")


@dataclass(frozen=True)
class LLMRelationshipProposal:
    proposed_relationship: str
    confidence: float
    rationale: str
    extracted_terms: tuple[str, ...]
    uncertainties: tuple[str, ...]
    manual_review_required: bool
    evidence_references: tuple[str, ...]

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "proposed_relationship": self.proposed_relationship,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "extracted_terms": list(self.extracted_terms),
            "uncertainties": list(self.uncertainties),
            "manual_review_required": self.manual_review_required,
            "evidence_references": list(self.evidence_references),
        }


class StubLLMRelationshipClient:
    """Deterministic test client. It never calls a model or network service."""

    model_id = "stub-llm-relationship-classifier"
    model_version = "test-only-v0"

    def __init__(self, response: Mapping[str, Any] | None = None) -> None:
        self.response = dict(response or valid_stub_llm_output())

    def propose_relationship(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return dict(self.response)


def valid_stub_llm_output() -> dict[str, Any]:
    return {
        "proposed_relationship": RELATIONSHIP_NEAR_EQUIVALENT,
        "confidence": 0.42,
        "rationale": "Stub review notes similar wording but does not assert payoff equivalence.",
        "extracted_terms": ["team", "event", "time_window"],
        "uncertainties": ["settlement rules require human review"],
        "manual_review_required": True,
        "evidence_references": ["polymarket.question", "kalshi.title"],
    }


def validate_llm_relationship_output(raw_output: Any) -> tuple[LLMRelationshipProposal | None, list[str]]:
    errors: list[str] = []
    if not isinstance(raw_output, Mapping):
        return None, ["llm_output_must_be_object"]

    keys = set(raw_output)
    unknown_fields = sorted(keys - LLM_ALLOWED_FIELDS)
    forbidden_fields = sorted(keys & LLM_FORBIDDEN_FIELDS)
    for field in forbidden_fields:
        errors.append(f"forbidden_field:{field}")
    for field in unknown_fields:
        errors.append(f"unknown_field:{field}")

    forbidden_tokens = sorted(_forbidden_tokens_in_value(raw_output))
    for token in forbidden_tokens:
        errors.append(f"forbidden_token:{token}")

    proposed_relationship = raw_output.get("proposed_relationship")
    if proposed_relationship not in LLM_ALLOWED_RELATIONSHIPS:
        errors.append("invalid_proposed_relationship")
    confidence = _float_or_none(raw_output.get("confidence"))
    if confidence is None or confidence < 0.0 or confidence > 1.0:
        errors.append("invalid_confidence")
    rationale = raw_output.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        errors.append("invalid_rationale")
    manual_review_required = raw_output.get("manual_review_required")
    if not isinstance(manual_review_required, bool):
        errors.append("invalid_manual_review_required")

    extracted_terms = _string_tuple_or_none(raw_output.get("extracted_terms"))
    if extracted_terms is None:
        errors.append("invalid_extracted_terms")
    uncertainties = _string_tuple_or_none(raw_output.get("uncertainties"))
    if uncertainties is None:
        errors.append("invalid_uncertainties")
    evidence_references = _string_tuple_or_none(raw_output.get("evidence_references"))
    if evidence_references is None:
        errors.append("invalid_evidence_references")

    if errors:
        return None, errors
    return (
        LLMRelationshipProposal(
            proposed_relationship=str(proposed_relationship),
            confidence=round(float(confidence), 6),
            rationale=str(rationale).strip(),
            extracted_terms=extracted_terms or (),
            uncertainties=uncertainties or (),
            manual_review_required=bool(manual_review_required),
            evidence_references=evidence_references or (),
        ),
        [],
    )


def build_llm_relationship_audit_sidecar(
    *,
    input_payload: Mapping[str, Any],
    prompt: str,
    model_id: str,
    model_version: str,
    raw_output: Any,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    captured_at = timestamp or datetime.now(timezone.utc)
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise ValueError("timestamp must include timezone information")
    parsed_output, validation_errors = validate_llm_relationship_output(raw_output)
    return {
        "prompt_hash": _stable_hash(prompt),
        "input_payload_hash": _stable_hash(input_payload),
        "model_id": model_id,
        "model_version": model_version,
        "timestamp": captured_at.isoformat(),
        "raw_output": raw_output,
        "parsed_output": parsed_output.to_report_dict() if parsed_output is not None else None,
        "validation_errors": validation_errors,
    }


def run_stub_llm_relationship_review(
    *,
    deterministic_relationship: ContractRelationship,
    input_payload: Mapping[str, Any],
    prompt: str = "relationship-review-v1",
    client: StubLLMRelationshipClient | None = None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    active_client = client or StubLLMRelationshipClient()
    raw_output = active_client.propose_relationship(input_payload)
    audit = build_llm_relationship_audit_sidecar(
        input_payload=input_payload,
        prompt=prompt,
        model_id=active_client.model_id,
        model_version=active_client.model_version,
        raw_output=raw_output,
        timestamp=timestamp,
    )
    return combine_deterministic_relationship_with_llm_proposal(deterministic_relationship, audit)


def combine_deterministic_relationship_with_llm_proposal(
    deterministic_relationship: ContractRelationship,
    llm_audit_sidecar: Mapping[str, Any],
) -> dict[str, Any]:
    parsed_output = llm_audit_sidecar.get("parsed_output")
    llm_manual_review_required = bool(parsed_output.get("manual_review_required")) if isinstance(parsed_output, Mapping) else False
    return {
        "relationship": deterministic_relationship.to_report_dict(),
        "manual_review_required": deterministic_relationship.manual_review_required or llm_manual_review_required,
        "llm_review": {
            "source": LLM_RELATIONSHIP_SOURCE,
            "proposal": dict(parsed_output) if isinstance(parsed_output, Mapping) else None,
            "confidence": parsed_output.get("confidence") if isinstance(parsed_output, Mapping) else None,
            "validation_errors": list(llm_audit_sidecar.get("validation_errors", [])),
            "audit": dict(llm_audit_sidecar),
        },
    }


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _forbidden_tokens_in_value(value: Any) -> set[str]:
    tokens: set[str] = set()
    if isinstance(value, str):
        text = value.upper()
        tokens.update(token for token in _TOKEN_RE.findall(text) if token in LLM_FORBIDDEN_TOKENS)
        return tokens
    if isinstance(value, Mapping):
        for child in value.values():
            tokens.update(_forbidden_tokens_in_value(child))
        return tokens
    if isinstance(value, list | tuple | set):
        for child in value:
            tokens.update(_forbidden_tokens_in_value(child))
    return tokens


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:
        return None
    return parsed


def _string_tuple_or_none(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, list):
        return None
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        result.append(item)
    return tuple(result)
