import json
from datetime import datetime, timezone

import scan
from relative_value.contract_relationship import (
    ContractRelationship,
    RELATIONSHIP_NEAR_EQUIVALENT,
    RELATIONSHIP_SOURCE_DETERMINISTIC_RULES,
    RELATIONSHIP_SUBSET,
    classify_contract_relationship,
)
from relative_value.llm_relationship_classifier import (
    StubLLMRelationshipClient,
    build_llm_relationship_audit_sidecar,
    combine_deterministic_relationship_with_llm_proposal,
    run_stub_llm_relationship_review,
    valid_stub_llm_output,
    validate_llm_relationship_output,
)


NOW = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)


def test_valid_stub_output_validates() -> None:
    proposal, errors = validate_llm_relationship_output(valid_stub_llm_output())

    assert errors == []
    assert proposal is not None
    assert proposal.proposed_relationship == RELATIONSHIP_NEAR_EQUIVALENT
    assert proposal.manual_review_required is True


def test_forbidden_same_payoff_is_rejected() -> None:
    raw_output = valid_stub_llm_output() | {"same_payoff": True}

    proposal, errors = validate_llm_relationship_output(raw_output)

    assert proposal is None
    assert "forbidden_field:same_payoff" in errors


def test_forbidden_equivalent_is_rejected() -> None:
    raw_output = valid_stub_llm_output() | {"proposed_relationship": "EQUIVALENT"}

    proposal, errors = validate_llm_relationship_output(raw_output)

    assert proposal is None
    assert "forbidden_token:EQUIVALENT" in errors
    assert "invalid_proposed_relationship" in errors


def test_forbidden_action_outputs_are_rejected() -> None:
    raw_output = valid_stub_llm_output() | {"action": "PAPER_CANDIDATE because POSSIBLE_ARB"}

    proposal, errors = validate_llm_relationship_output(raw_output)

    assert proposal is None
    assert "forbidden_field:action" in errors
    assert "forbidden_token:PAPER_CANDIDATE" in errors
    assert "forbidden_token:POSSIBLE_ARB" in errors


def test_forbidden_paper_token_is_rejected_without_action_field() -> None:
    raw_output = valid_stub_llm_output() | {"rationale": "Recommend PAPER review."}

    proposal, errors = validate_llm_relationship_output(raw_output)

    assert proposal is None
    assert "forbidden_token:PAPER" in errors


def test_unknown_field_is_rejected() -> None:
    raw_output = valid_stub_llm_output() | {"freeform_notes": "not allowed"}

    proposal, errors = validate_llm_relationship_output(raw_output)

    assert proposal is None
    assert "unknown_field:freeform_notes" in errors


def test_deterministic_subset_beats_llm_near_equivalent() -> None:
    deterministic = classify_contract_relationship(["sports_competition_scope_mismatch"])

    review = run_stub_llm_relationship_review(
        deterministic_relationship=deterministic,
        input_payload={"polymarket": "ALCS", "kalshi": "World Series"},
        timestamp=NOW,
    )

    assert review["relationship"]["relationship"] == RELATIONSHIP_SUBSET
    assert review["relationship"]["same_payoff"] is False
    assert review["relationship"]["source"] == RELATIONSHIP_SOURCE_DETERMINISTIC_RULES
    assert review["llm_review"]["proposal"]["proposed_relationship"] == RELATIONSHIP_NEAR_EQUIVALENT


def test_llm_manual_review_true_can_escalate() -> None:
    deterministic = ContractRelationship(
        relationship=RELATIONSHIP_NEAR_EQUIVALENT,
        same_payoff=False,
        confidence=0.4,
        blocking_reasons=(),
        manual_review_required=False,
    )
    audit = build_llm_relationship_audit_sidecar(
        input_payload={"market": "text"},
        prompt="review",
        model_id="stub",
        model_version="v0",
        raw_output=valid_stub_llm_output() | {"manual_review_required": True},
        timestamp=NOW,
    )

    review = combine_deterministic_relationship_with_llm_proposal(deterministic, audit)

    assert review["manual_review_required"] is True


def test_llm_manual_review_false_cannot_deescalate_deterministic_review() -> None:
    deterministic = classify_contract_relationship(["sports_competition_scope_mismatch"])
    audit = build_llm_relationship_audit_sidecar(
        input_payload={"market": "text"},
        prompt="review",
        model_id="stub",
        model_version="v0",
        raw_output=valid_stub_llm_output() | {"manual_review_required": False},
        timestamp=NOW,
    )

    review = combine_deterministic_relationship_with_llm_proposal(deterministic, audit)

    assert deterministic.manual_review_required is True
    assert review["manual_review_required"] is True


def test_audit_sidecar_has_required_fields_and_serializes() -> None:
    audit = build_llm_relationship_audit_sidecar(
        input_payload={"polymarket": {"question": "Will X happen?"}},
        prompt="relationship-review-v1",
        model_id="stub",
        model_version="v0",
        raw_output=valid_stub_llm_output(),
        timestamp=NOW,
    )

    assert set(audit) == {
        "prompt_hash",
        "input_payload_hash",
        "model_id",
        "model_version",
        "timestamp",
        "raw_output",
        "parsed_output",
        "validation_errors",
    }
    assert audit["validation_errors"] == []
    json.dumps(audit)


def test_stub_client_is_deterministic_and_non_networked() -> None:
    client = StubLLMRelationshipClient()

    assert client.propose_relationship({"anything": "ignored"}) == valid_stub_llm_output()
    assert client.propose_relationship({"different": "payload"}) == valid_stub_llm_output()


def test_scanner_behavior_remains_unchanged(capsys) -> None:
    result = scan.main([])

    assert result == 0
    assert "relative_value_scan_status=OFFLINE_COMPLETE candidates=7 possible_arbs=0" in capsys.readouterr().out
