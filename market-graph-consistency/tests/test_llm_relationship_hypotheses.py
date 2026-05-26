from __future__ import annotations

import json
from pathlib import Path

import pytest

import scan
from graph_engine.reporting.llm_relationship_hypotheses import (
    EVENT_CLASSES,
    RELATIONSHIP_TYPES,
    build_llm_relationship_hypotheses_report,
    build_llm_relationship_review_packets,
    import_llm_relationship_hypotheses,
    validate_llm_relationship_hypotheses_report,
    validate_llm_review_packets,
    write_imported_llm_relationship_hypotheses_report,
    write_llm_relationship_hypotheses_report,
    write_llm_relationship_review_packets,
)
from graph_engine.reporting.schema_validation import SchemaValidationError


def _valid_hypothesis(market_ids: list[str], **overrides) -> dict:
    payload = {
        "hypothesis_id": "llm-hypothesis-1",
        "market_ids": market_ids,
        "relationship_type": "SUBSET_HYPOTHESIS",
        "natural_language_claim": "The first market appears narrower than the second based on the supplied rules text.",
        "directionality": f"{market_ids[0]} -> {market_ids[1]}",
        "evidence_fields_used": ["title", "rules_or_description_excerpt", "normalized_formula"],
        "missing_evidence": ["settlement_source_review"],
        "falsification_checks": ["Verify source, date, and settlement text match before using this relationship."],
        "confidence_tier": "MEDIUM",
        "action_permission": False,
    }
    payload.update(overrides)
    return payload


def test_generated_packets_are_sanitized_and_schema_described(fixture_snapshot) -> None:
    packets = build_llm_relationship_review_packets(fixture_snapshot)

    assert packets
    validate_llm_review_packets(packets)
    packet = packets[0]
    assert packet["diagnostic_only"] is True
    assert packet["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    assert "llm_output_schema" in packet
    assert set(packet["llm_output_schema"]["relationship_types"]) == RELATIONSHIP_TYPES
    assert set(packet["llm_output_schema"]["event_classes"]) == EVENT_CLASSES
    assert "counter_hypothesis_id" in packet["llm_output_schema"]["optional_fields"]
    assert "event_class" in packet["llm_output_schema"]["optional_fields"]
    rendered = json.dumps(packet).lower()
    assert "api_key" not in rendered
    assert "private_key" not in rendered
    assert "bearer" not in rendered
    assert "propose structured relationship hypotheses only" in rendered
    assert "routing guidance" in rendered or "execution instructions" in rendered


def test_valid_saved_llm_hypotheses_import_successfully(fixture_snapshot, tmp_path) -> None:
    market_ids = sorted(fixture_snapshot.nodes)[:2]
    input_path = tmp_path / "hypotheses.jsonl"
    input_path.write_text(json.dumps(_valid_hypothesis(market_ids)) + "\n", encoding="utf-8")

    hypotheses = import_llm_relationship_hypotheses(input_path)
    report = build_llm_relationship_hypotheses_report(fixture_snapshot, hypotheses)

    assert report["hypothesis_count"] == 1
    assert report["rejected_hypothesis_count"] == 0
    row = report["validated_hypotheses"][0]
    assert row["relationship_type"] == "SUBSET_HYPOTHESIS"
    assert row["llm_evidence_role"] == "llm_hypothesis_advisory"
    assert row["action_permission"] is False
    assert row["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]


def test_counter_hypothesis_id_references_same_import_batch(fixture_snapshot) -> None:
    market_ids = sorted(fixture_snapshot.nodes)[:2]
    report = build_llm_relationship_hypotheses_report(
        fixture_snapshot,
        [
            _valid_hypothesis(
                market_ids,
                hypothesis_id="hyp-a",
                counter_hypothesis_id="hyp-b",
                event_class="crypto",
            ),
            _valid_hypothesis(
                market_ids,
                hypothesis_id="hyp-b",
                relationship_type="SIMILARITY_ONLY_HYPOTHESIS",
                event_class="crypto",
            ),
        ],
    )

    rows = {row["hypothesis_id"]: row for row in report["validated_hypotheses"]}
    assert rows["hyp-a"]["counter_hypothesis_id"] == "hyp-b"
    assert rows["hyp-a"]["event_class"] == "crypto"
    assert "unknown_counter_hypothesis_id" not in rows["hyp-a"]["review_blockers"]


def test_unknown_counter_hypothesis_id_downgrades_with_blocker(fixture_snapshot) -> None:
    market_ids = sorted(fixture_snapshot.nodes)[:2]
    report = build_llm_relationship_hypotheses_report(
        fixture_snapshot,
        [_valid_hypothesis(market_ids, counter_hypothesis_id="missing-hypothesis", confidence_tier="HIGH")],
    )

    row = report["validated_hypotheses"][0]
    assert row["validation_status"] == "DOWNGRADED"
    assert row["confidence_tier"] == "LOW"
    assert "unknown_counter_hypothesis_id" in row["review_blockers"]
    assert "unknown_counter_hypothesis_id" in row["downgrade_reason"]


def test_unsupported_event_class_downgrades_to_low_other(fixture_snapshot) -> None:
    market_ids = sorted(fixture_snapshot.nodes)[:2]
    report = build_llm_relationship_hypotheses_report(
        fixture_snapshot,
        [_valid_hypothesis(market_ids, event_class="unsupported-cycle", confidence_tier="HIGH")],
    )

    row = report["validated_hypotheses"][0]
    assert row["validation_status"] == "DOWNGRADED"
    assert row["confidence_tier"] == "LOW"
    assert row["event_class"] == "other"
    assert row["input_event_class"] == "unsupported-cycle"
    assert "unsupported_event_class" in row["review_blockers"]


def test_action_permission_true_is_rejected(fixture_snapshot) -> None:
    market_ids = sorted(fixture_snapshot.nodes)[:2]
    report = build_llm_relationship_hypotheses_report(
        fixture_snapshot,
        [_valid_hypothesis(market_ids, action_permission=True)],
    )

    assert report["hypothesis_count"] == 0
    assert report["rejected_hypothesis_count"] == 1
    assert "action_permission must be false" in report["rejected_hypotheses"][0]["rejection_reason"]


def test_exact_equality_hypothesis_remains_advisory_without_deterministic_evidence(fixture_snapshot) -> None:
    market_ids = sorted(fixture_snapshot.nodes)[:2]
    report = build_llm_relationship_hypotheses_report(
        fixture_snapshot,
        [
            _valid_hypothesis(
                market_ids,
                relationship_type="EXACT_EQUALITY_HYPOTHESIS",
                confidence_tier="HIGH",
                natural_language_claim="These markets may be equivalent, pending exact settlement verification.",
            )
        ],
    )

    row = report["validated_hypotheses"][0]
    assert row["deterministic_support"] is False
    assert row["hypothesis_classification"] == "advisory_only_exact_claim_unproven"
    assert row["confidence_tier"] == "MEDIUM"
    assert "exact_equality_not_deterministically_supported" in row["review_blockers"]


@pytest.mark.parametrize("relationship_type", ["THEMATIC_CORRELATION_HYPOTHESIS", "PROBABILISTIC_RELATED_HYPOTHESIS"])
def test_thematic_and_probabilistic_hypotheses_never_become_executable(fixture_snapshot, relationship_type: str) -> None:
    market_ids = sorted(fixture_snapshot.nodes)[:2]
    report = build_llm_relationship_hypotheses_report(
        fixture_snapshot,
        [_valid_hypothesis(market_ids, relationship_type=relationship_type, confidence_tier="HIGH")],
    )

    row = report["validated_hypotheses"][0]
    assert row["diagnostic_only"] is True
    assert row["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    assert row["confidence_tier"] == "MEDIUM"
    assert row["hypothesis_classification"] == "advisory_only"
    assert "relationship_is_advisory_not_structural_proof" in row["review_blockers"]


def test_stale_or_lag_hypothesis_gets_distinct_strength_tier(fixture_snapshot) -> None:
    market_ids = sorted(fixture_snapshot.nodes)[:2]
    report = build_llm_relationship_hypotheses_report(
        fixture_snapshot,
        [_valid_hypothesis(market_ids, relationship_type="STALE_OR_LAG_HYPOTHESIS", confidence_tier="HIGH")],
    )

    row = report["validated_hypotheses"][0]
    assert row["relationship_strength_tier"] == "STALE_OR_LAG_HYPOTHESIS_ONLY"
    assert row["relationship_strength_tier"] != "THEMATIC_HYPOTHESIS_ONLY"
    assert row["confidence_tier"] == "MEDIUM"
    assert "advisory_only_capped_medium" in row["downgrade_reason"]


@pytest.mark.parametrize(
    "relationship_type",
    [
        "SUBSET_HYPOTHESIS",
        "SUPERSET_HYPOTHESIS",
        "COMPLEMENT_HYPOTHESIS",
        "MUTUALLY_EXCLUSIVE_HYPOTHESIS",
        "EXHAUSTIVE_PARTITION_HYPOTHESIS",
        "THRESHOLD_LADDER_HYPOTHESIS",
        "RANGE_BUCKET_HYPOTHESIS",
    ],
)
def test_unsupported_structural_hypotheses_are_capped_at_medium(fixture_snapshot, relationship_type: str) -> None:
    market_ids = ["kalshi:microsoft_first_agi_2027", "manifold:agi_by_2027"]
    report = build_llm_relationship_hypotheses_report(
        fixture_snapshot,
        [_valid_hypothesis(market_ids, relationship_type=relationship_type, confidence_tier="HIGH")],
    )

    row = report["validated_hypotheses"][0]
    assert row["deterministic_support"] is False
    assert row["relationship_strength_tier"] == "LOGICAL_HYPOTHESIS_ONLY"
    assert row["confidence_tier"] == "MEDIUM"
    assert "structural_unproven_capped" in row["downgrade_reason"]


def test_similarity_only_hypothesis_is_low_confidence_research_only(fixture_snapshot) -> None:
    market_ids = sorted(fixture_snapshot.nodes)[:2]
    report = build_llm_relationship_hypotheses_report(
        fixture_snapshot,
        [_valid_hypothesis(market_ids, relationship_type="SIMILARITY_ONLY_HYPOTHESIS", confidence_tier="HIGH")],
    )

    row = report["validated_hypotheses"][0]
    assert row["confidence_tier"] == "LOW"
    assert row["hypothesis_classification"] == "research_only"
    assert row["diagnostic_priority"] == "WATCH"


def test_malformed_hypothesis_fails_closed(fixture_snapshot) -> None:
    market_ids = sorted(fixture_snapshot.nodes)[:2]
    report = build_llm_relationship_hypotheses_report(
        fixture_snapshot,
        [_valid_hypothesis(market_ids, relationship_type="UNSUPPORTED")],
    )

    assert report["hypothesis_count"] == 0
    assert report["rejected_hypothesis_count"] == 1
    assert "relationship_type is not supported" in report["rejected_hypotheses"][0]["rejection_reason"]


@pytest.mark.parametrize("relationship_type", ["INSUFFICIENT_EVIDENCE", "ABSTAIN"])
def test_insufficient_evidence_marker_is_ignored_safely(fixture_snapshot, relationship_type: str) -> None:
    market_ids = sorted(fixture_snapshot.nodes)[:2]
    report = build_llm_relationship_hypotheses_report(
        fixture_snapshot,
        [_valid_hypothesis(market_ids, relationship_type=relationship_type, confidence_tier="LOW")],
    )

    assert report["hypothesis_count"] == 0
    assert report["rejected_hypothesis_count"] == 1
    ignored = report["rejected_hypotheses"][0]
    assert ignored["relationship_strength_tier"] == "INSUFFICIENT_EVIDENCE_IGNORED"
    assert ignored["validation_status"] == "IGNORED_INSUFFICIENT_EVIDENCE"


def test_disallowed_permission_action_is_rejected(fixture_snapshot) -> None:
    market_ids = sorted(fixture_snapshot.nodes)[:2]
    report = build_llm_relationship_hypotheses_report(
        fixture_snapshot,
        [_valid_hypothesis(market_ids, allowed_actions=["WATCH", "BUY"])],
    )

    assert report["hypothesis_count"] == 0
    assert report["rejected_hypothesis_count"] == 1


def test_disallowed_output_token_is_rejected_by_report_validator(fixture_snapshot) -> None:
    market_ids = sorted(fixture_snapshot.nodes)[:2]
    report = build_llm_relationship_hypotheses_report(fixture_snapshot, [_valid_hypothesis(market_ids)])
    report["validated_hypotheses"][0]["natural_language_claim"] = "PAPER_CANDIDATE"

    with pytest.raises(SchemaValidationError):
        validate_llm_relationship_hypotheses_report(report)


def test_write_functions_validate_before_writing(fixture_snapshot, tmp_path) -> None:
    packets_path = tmp_path / "llm_relationship_review_packets.jsonl"
    report_path = tmp_path / "llm_relationship_hypotheses_validated.json"
    input_path = tmp_path / "llm_output.json"
    market_ids = sorted(fixture_snapshot.nodes)[:2]
    input_path.write_text(json.dumps({"hypotheses": [_valid_hypothesis(market_ids)]}), encoding="utf-8")

    packets = write_llm_relationship_review_packets(fixture_snapshot, packets_path)
    report = write_imported_llm_relationship_hypotheses_report(fixture_snapshot, input_path, report_path)

    assert packets_path.exists()
    assert report_path.exists()
    assert len(packets_path.read_text(encoding="utf-8").splitlines()) == len(packets)
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    validate_llm_relationship_hypotheses_report(report)


def test_empty_report_still_writes(fixture_snapshot, tmp_path) -> None:
    output = tmp_path / "empty_llm_relationship_hypotheses_validated.json"

    report = write_llm_relationship_hypotheses_report(fixture_snapshot, output)

    assert output.exists()
    assert report["hypothesis_count"] == 0
    assert report["validated_hypotheses"] == []


def test_scan_still_runs_and_writes_llm_reports(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(scan, "REPORTS_DIR", tmp_path)

    exit_code = scan.main([])

    assert exit_code == 0
    assert (tmp_path / "llm_relationship_review_packets.jsonl").exists()
    assert (tmp_path / "llm_relationship_hypotheses_validated.json").exists()


# ---------------------------------------------------------------------------
# Operator-ready prompt template
# ---------------------------------------------------------------------------


PROMPT_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "docs" / "LLM_RELATIONSHIP_REVIEW.md"


def test_prompt_template_file_exists_and_describes_workflow() -> None:
    assert PROMPT_TEMPLATE_PATH.exists()
    text = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert "action_permission must always be the literal boolean false" in text
    assert "diagnostic_only" in text
    assert "WATCH" in text
    assert "MANUAL_REVIEW" in text
    assert "SIMILARITY_ONLY_HYPOTHESIS" in text
    assert "THEMATIC_CORRELATION_HYPOTHESIS" in text
    assert "STALE_OR_LAG_HYPOTHESIS_ONLY" in text
    assert "INSUFFICIENT_EVIDENCE" in text
    assert "ABSTAIN" in text
    assert "counter_hypothesis_id" in text
    assert "event_class" in text


def test_prompt_template_explicitly_disallows_execution_vocabulary() -> None:
    text = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8").lower()

    # Each prohibited token must appear inside an explicit disallow block.
    forbidden = ["paper_candidate", "possible_arb", "guaranteed_pnl", "executable_arbitrage"]
    for token in forbidden:
        assert token in text, f"prompt should call out {token!r} so reviewers know it is forbidden"

    # The prompt must explicitly forbid the operator side as well.
    assert "do not output" in text or "do not include" in text
    assert "must not output" in text or "must not include" in text or "you must not" in text
    assert "action_permission must always be the literal boolean false" in text
    assert "diagnostic-only" in text or "diagnostic_only" in text


def test_prompt_template_does_not_instruct_llm_to_perform_execution() -> None:
    text = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8").lower()

    forbidden_instructions = [
        "place an order",
        "submit a trade",
        "execute the trade",
        "open a position",
        "use your api key",
        "share your private key",
        "share your wallet",
    ]
    for phrase in forbidden_instructions:
        assert phrase not in text, f"prompt must not request {phrase!r}"


def test_architecture_documents_safety_vocabulary() -> None:
    architecture = (Path(__file__).resolve().parents[1] / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")

    for term in [
        "action_permission",
        "review_blockers",
        "why_review_only_yet",
        "non_actionable_input",
        "llm_evidence_role",
        "affects_evaluator_gates=false",
    ]:
        assert term in architecture
    assert "does not add numeric severity" in architecture


# ---------------------------------------------------------------------------
# Strength-tier classification
# ---------------------------------------------------------------------------


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "llm_relationship_hypotheses"


def test_sample_subset_fixture_imports_and_is_logical_hypothesis_only(fixture_snapshot) -> None:
    payload = json.loads((FIXTURE_DIR / "valid_subset.json").read_text(encoding="utf-8"))
    report = build_llm_relationship_hypotheses_report(fixture_snapshot, payload["hypotheses"])

    assert report["hypothesis_count"] == 1
    row = report["validated_hypotheses"][0]
    assert row["relationship_type"] == "SUBSET_HYPOTHESIS"
    # The fixture snapshot has a subset edge between these markets so it should be deterministic-supported.
    assert row["relationship_strength_tier"] in {"DETERMINISTIC_SUPPORTED", "LOGICAL_HYPOTHESIS_ONLY"}
    assert row["validation_status"] in {"ACCEPTED", "DOWNGRADED"}
    assert row["original_llm_claim"]["confidence_tier"] == "MEDIUM"


def test_sample_threshold_sequence_fixture_imports_with_strength_tier(fixture_snapshot, tmp_path) -> None:
    input_path = tmp_path / "valid_threshold_sequence.jsonl"
    input_path.write_text((FIXTURE_DIR / "valid_threshold_sequence.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    hypotheses = import_llm_relationship_hypotheses(input_path)
    report = build_llm_relationship_hypotheses_report(fixture_snapshot, hypotheses)

    row = report["validated_hypotheses"][0]
    assert row["relationship_type"] == "THRESHOLD_LADDER_HYPOTHESIS"
    assert row["relationship_strength_tier"] in {"DETERMINISTIC_SUPPORTED", "LOGICAL_HYPOTHESIS_ONLY"}
    assert row["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]


def test_sample_thematic_fixture_stays_advisory(fixture_snapshot) -> None:
    payload = json.loads((FIXTURE_DIR / "valid_thematic.json").read_text(encoding="utf-8"))
    report = build_llm_relationship_hypotheses_report(fixture_snapshot, payload["hypotheses"])

    row = report["validated_hypotheses"][0]
    assert row["relationship_type"] == "THEMATIC_CORRELATION_HYPOTHESIS"
    assert row["relationship_strength_tier"] == "THEMATIC_HYPOTHESIS_ONLY"
    assert row["confidence_tier"] == "MEDIUM"
    assert row["hypothesis_classification"] == "advisory_only"
    assert row["diagnostic_priority"] in {"WATCH", "MANUAL_REVIEW"}


def test_sample_action_permission_fixture_is_rejected_unsafe(fixture_snapshot) -> None:
    payload = json.loads((FIXTURE_DIR / "invalid_action_permission.json").read_text(encoding="utf-8"))
    report = build_llm_relationship_hypotheses_report(fixture_snapshot, payload["hypotheses"])

    assert report["hypothesis_count"] == 0
    assert report["rejected_hypothesis_count"] == 1
    rejected = report["rejected_hypotheses"][0]
    assert rejected["relationship_strength_tier"] == "REJECTED_UNSAFE"
    assert rejected["validation_status"] == "REJECTED"
    assert rejected["original_llm_claim"]["action_permission"] is True


def test_sample_text_only_equality_fixture_is_downgraded(fixture_snapshot) -> None:
    payload = json.loads((FIXTURE_DIR / "invalid_exact_equality_text_only.json").read_text(encoding="utf-8"))
    report = build_llm_relationship_hypotheses_report(fixture_snapshot, payload["hypotheses"])

    row = report["validated_hypotheses"][0]
    assert row["relationship_type"] == "EXACT_EQUALITY_HYPOTHESIS"
    assert row["deterministic_support"] is False
    assert row["relationship_strength_tier"] == "LOGICAL_HYPOTHESIS_ONLY"
    assert row["validation_status"] == "DOWNGRADED"
    assert "exact_equality_text_only_downgrade" in row["downgrade_reason"]
    assert row["confidence_tier"] != "HIGH"
    assert row["original_llm_claim"]["confidence_tier"] == "HIGH"


def test_thematic_hypothesis_high_input_is_downgraded(fixture_snapshot) -> None:
    market_ids = sorted(fixture_snapshot.nodes)[:2]
    report = build_llm_relationship_hypotheses_report(
        fixture_snapshot,
        [_valid_hypothesis(market_ids, relationship_type="THEMATIC_CORRELATION_HYPOTHESIS", confidence_tier="HIGH")],
    )

    row = report["validated_hypotheses"][0]
    assert row["relationship_strength_tier"] == "THEMATIC_HYPOTHESIS_ONLY"
    assert row["validation_status"] == "DOWNGRADED"
    assert "advisory_only_capped_medium" in row["downgrade_reason"]


def test_similarity_only_downgrade_is_recorded(fixture_snapshot) -> None:
    market_ids = sorted(fixture_snapshot.nodes)[:2]
    report = build_llm_relationship_hypotheses_report(
        fixture_snapshot,
        [_valid_hypothesis(market_ids, relationship_type="SIMILARITY_ONLY_HYPOTHESIS", confidence_tier="HIGH")],
    )

    row = report["validated_hypotheses"][0]
    assert row["relationship_strength_tier"] == "SIMILARITY_ONLY_RESEARCH"
    assert row["validation_status"] == "DOWNGRADED"
    assert "similarity_only_capped_low" in row["downgrade_reason"]


def test_report_includes_tier_and_validation_counts(fixture_snapshot) -> None:
    market_ids = sorted(fixture_snapshot.nodes)[:2]
    report = build_llm_relationship_hypotheses_report(
        fixture_snapshot,
        [
            _valid_hypothesis(market_ids, relationship_type="THEMATIC_CORRELATION_HYPOTHESIS", confidence_tier="LOW"),
            _valid_hypothesis(market_ids, hypothesis_id="bad-perm", action_permission=True),
        ],
    )

    assert "counts_by_strength_tier" in report
    assert report["counts_by_strength_tier"].get("THEMATIC_HYPOTHESIS_ONLY", 0) == 1
    assert report["counts_by_strength_tier"].get("REJECTED_UNSAFE", 0) == 1
    assert report["counts_by_validation_status"].get("REJECTED", 0) == 1
