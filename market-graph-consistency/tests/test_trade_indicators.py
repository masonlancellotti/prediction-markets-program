from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from graph_engine.consistency.runner import run_consistency_checks
from graph_engine.loader import load_fixture_markets
from graph_engine.models import GraphSnapshot, RelationshipEdge, RelationshipType
from graph_engine.reporting.schema_validation import SchemaValidationError
from graph_engine.reporting.trade_indicators import (
    SIGNAL_TYPES,
    build_trade_indicator_report,
    validate_trade_indicator_report,
    write_trade_indicator_report,
)
from tests.conftest import make_node


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = PROJECT_ROOT / "venues" / "fixtures"


def _fixture_report() -> dict:
    snapshot, _ = load_fixture_markets(FIXTURES_DIR)
    from graph_engine.relationships.registry import load_relationship_registry

    registry = load_relationship_registry(PROJECT_ROOT / "relationships", set(snapshot.nodes))
    snapshot.edges = registry.edges
    snapshot.exclusion_sets = registry.exclusion_sets
    return build_trade_indicator_report(snapshot, run_consistency_checks(snapshot))


def _edge(
    relation: RelationshipType,
    *,
    src: str,
    dst: str,
    confidence: float = 0.95,
) -> RelationshipEdge:
    return RelationshipEdge(
        edge_id=f"edge_{src.replace(':', '_')}_{dst.replace(':', '_')}",
        src_market_id=src,
        dst_market_id=dst,
        relation=relation,
        confidence=confidence,
        source="manual",
        rationale="test relationship",
        evidence=["fixture"],
        created_at="2026-05-19T18:00:00+00:00",
        reviewed_by="fixture-reviewer",
        observable="BTC_USD",
        window="2026-06-30",
    )


def test_trade_indicator_report_envelope_and_allowed_actions_are_capped() -> None:
    report = _fixture_report()

    assert report["diagnostic_only"] is True
    assert report["affects_evaluator_gates"] is False
    assert report["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    assert report["signals"]
    for row in report["signals"]:
        assert row["diagnostic_only"] is True
        assert row["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
        assert not {"PAPER_CANDIDATE", "TRADE", "EXECUTE", "ORDER", "BUY", "SELL"} & set(row["allowed_actions"])
        assert row["signal_type"] in SIGNAL_TYPES


def test_trade_indicator_validator_rejects_promoted_actions() -> None:
    report = _fixture_report()
    report["signals"][0]["allowed_actions"] = ["WATCH", "BUY"]

    with pytest.raises(SchemaValidationError):
        validate_trade_indicator_report(report)


@pytest.mark.parametrize("token", ["PAPER_CANDIDATE", "guaranteed_pnl", "EXACT_ARBITRAGE"])
def test_trade_indicator_validator_rejects_disallowed_output_tokens(token: str) -> None:
    report = _fixture_report()
    report["signals"][0]["why_not_tradeable_yet"] = token

    with pytest.raises(SchemaValidationError):
        validate_trade_indicator_report(report)


def test_similarity_only_research_cannot_be_high_confidence() -> None:
    report = _fixture_report()
    similarity_rows = [row for row in report["signals"] if row["signal_type"] == "SIMILARITY_ONLY_RESEARCH"]

    assert similarity_rows
    assert all(row["confidence_tier"] == "LOW" for row in similarity_rows)
    assert all(row["severity_score"] < 50 for row in similarity_rows)


def test_subset_superset_violation_scoring_works_on_fixture() -> None:
    report = _fixture_report()
    subset_rows = [row for row in report["signals"] if row["signal_type"] == "SUBSET_SUPERSET_PRICE_VIOLATION"]

    assert subset_rows
    assert subset_rows[0]["severity_score"] > 50
    assert subset_rows[0]["confidence_tier"] in {"MEDIUM", "HIGH"}
    assert subset_rows[0]["implied_direction"] in {
        "NARROWER_HIGH_RELATIVE_TO_BROADER",
        "STRICTER_THRESHOLD_HIGH_RELATIVE_TO_LOOSER",
    }


def test_threshold_ladder_inversion_scoring_works_on_fixture() -> None:
    report = _fixture_report()
    ladder_rows = [row for row in report["signals"] if row["signal_type"] == "THRESHOLD_LADDER_INVERSION"]

    assert ladder_rows
    assert ladder_rows[0]["severity_score"] > 50
    assert ladder_rows[0]["relationship_evidence_type"].startswith(("multi_leg_constraint:", "formula_diagnostic:"))


def test_midpoint_derived_signal_is_labeled_non_executable() -> None:
    strict = make_node(
        "test:btc_120",
        0.5,
        bid=0.70,
        ask=0.74,
        observable="BTC_USD",
        settlement_source="fixture_btc_index",
        window="2026-06-30",
    )
    strict.yes_price = None
    strict.no_price = None
    loose = make_node(
        "test:btc_100",
        0.5,
        bid=0.48,
        ask=0.52,
        observable="BTC_USD",
        settlement_source="fixture_btc_index",
        window="2026-06-30",
    )
    loose.yes_price = None
    loose.no_price = None
    snapshot = GraphSnapshot(
        snapshot_id="midpoint-test",
        as_of="2026-05-19T18:00:00+00:00",
        nodes={
            "test:btc_120": strict,
            "test:btc_100": loose,
        },
        edges=[_edge(RelationshipType.SUBSET, src="test:btc_120", dst="test:btc_100")],
    )

    report = build_trade_indicator_report(snapshot)
    row = next(item for item in report["signals"] if item["signal_type"] == "SUBSET_SUPERSET_PRICE_VIOLATION")

    assert any(item["probability_source"] == "diagnostic_midpoint" for item in row["probability_inputs_used"])
    assert all(item["non_actionable_input"] is True for item in row["probability_inputs_used"])
    assert "diagnostic_midpoint_not_actionable" in row["review_blockers"]


def test_stale_or_lag_rows_include_freshness_blockers() -> None:
    stale_time = datetime(2026, 5, 19, 18, 0, tzinfo=timezone.utc)
    snapshot = GraphSnapshot(
        snapshot_id="stale-test",
        as_of="2026-05-21T18:00:00+00:00",
        nodes={
            "test:a": make_node("test:a", 0.85, as_of=stale_time),
            "test:b": make_node("test:b", 0.10, as_of=stale_time),
        },
        edges=[_edge(RelationshipType.IMPLICATION, src="test:a", dst="test:b")],
    )

    report = build_trade_indicator_report(snapshot)
    stale_rows = [row for row in report["signals"] if row["signal_type"] == "STALE_OR_LAG_WATCH"]

    assert stale_rows
    assert any("stale_quote" in row["review_blockers"] for row in stale_rows)
    assert any(item["quote_age_seconds"] > 24 * 60 * 60 for row in stale_rows for item in row["probability_inputs_used"])


def test_trade_indicator_report_validates_before_writing(tmp_path) -> None:
    snapshot, _ = load_fixture_markets(FIXTURES_DIR)
    json_output = tmp_path / "market_graph_trade_indicators.json"
    csv_output = tmp_path / "market_graph_trade_indicators.csv"

    report = write_trade_indicator_report(snapshot, json_output, csv_output)

    assert json.loads(json_output.read_text(encoding="utf-8")) == report
    assert csv_output.exists()
    assert "signal_id,signal_type" in csv_output.read_text(encoding="utf-8")
    validate_trade_indicator_report(report)


# ---------------------------------------------------------------------------
# Advisory LLM hypothesis integration
# ---------------------------------------------------------------------------


def _fixture_snapshot_with_relationships():
    snapshot, _ = load_fixture_markets(FIXTURES_DIR)
    from graph_engine.relationships.registry import load_relationship_registry

    registry = load_relationship_registry(PROJECT_ROOT / "relationships", set(snapshot.nodes))
    snapshot.edges = registry.edges
    snapshot.exclusion_sets = registry.exclusion_sets
    return snapshot


def _build_hypothesis_report(snapshot, hypothesis_overrides) -> dict:
    from graph_engine.reporting.llm_relationship_hypotheses import (
        build_llm_relationship_hypotheses_report,
    )

    payload = {
        "hypothesis_id": hypothesis_overrides["hypothesis_id"],
        "market_ids": hypothesis_overrides["market_ids"],
        "relationship_type": hypothesis_overrides["relationship_type"],
        "natural_language_claim": hypothesis_overrides.get(
            "natural_language_claim",
            "Synthetic hypothesis for trade indicator advisory testing.",
        ),
        "directionality": hypothesis_overrides.get("directionality"),
        "evidence_fields_used": hypothesis_overrides.get("evidence_fields_used", ["title"]),
        "missing_evidence": hypothesis_overrides.get("missing_evidence", ["settlement_source_review"]),
        "falsification_checks": hypothesis_overrides.get(
            "falsification_checks", ["Confirm both markets resolve under the same rule set."]
        ),
        "confidence_tier": hypothesis_overrides.get("confidence_tier", "MEDIUM"),
        "trade_permission": False,
    }
    return build_llm_relationship_hypotheses_report(snapshot, [payload])


def test_deterministic_supported_llm_evidence_records_corroboration_without_boost() -> None:
    snapshot = _fixture_snapshot_with_relationships()
    base_report = build_trade_indicator_report(snapshot, run_consistency_checks(snapshot))
    subset_rows = [row for row in base_report["signals"] if row["signal_type"] == "SUBSET_SUPERSET_PRICE_VIOLATION"]
    assert subset_rows, "fixture must produce a subset-superset signal"
    target = subset_rows[0]
    market_ids = target["markets_involved"]
    baseline_severity = target["severity_score"]

    hypothesis_report = _build_hypothesis_report(
        snapshot,
        {
            "hypothesis_id": "advisory-logical-subset",
            "market_ids": market_ids,
            "relationship_type": "SUBSET_HYPOTHESIS",
            "confidence_tier": "MEDIUM",
        },
    )
    enriched = build_trade_indicator_report(
        snapshot,
        run_consistency_checks(snapshot),
        llm_hypotheses_report=hypothesis_report,
    )
    enriched_target = next(
        row for row in enriched["signals"] if row["signal_id"] == target["signal_id"]
    )

    assert "advisory-logical-subset" in enriched_target["llm_hypothesis_ids"]
    assert enriched_target["llm_advisory_evidence_role"] == "llm_hypothesis_advisory"
    assert enriched_target["llm_advisory_evidence_strength_tier"] in {
        "DETERMINISTIC_SUPPORTED",
        "LOGICAL_HYPOTHESIS_ONLY",
    }
    assert enriched_target["severity_score"] == baseline_severity
    assert enriched_target["llm_advisory_severity_boost"] == 0.0
    assert enriched_target["corroborating_llm_evidence"] is True
    assert "advisory_llm_evidence_requires_independent_verification" in enriched_target["review_blockers"]
    assert enriched_target["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]


def test_advisory_thematic_hypothesis_creates_thematic_watch_signal() -> None:
    snapshot = _fixture_snapshot_with_relationships()
    # pick two markets with no existing graph relation between them
    market_ids = ["fixture:election_candidate_a", "fixture:btc_over_140k_june"]
    hypothesis_report = _build_hypothesis_report(
        snapshot,
        {
            "hypothesis_id": "advisory-thematic-unrelated",
            "market_ids": market_ids,
            "relationship_type": "THEMATIC_CORRELATION_HYPOTHESIS",
            "confidence_tier": "MEDIUM",
        },
    )
    report = build_trade_indicator_report(
        snapshot,
        run_consistency_checks(snapshot),
        llm_hypotheses_report=hypothesis_report,
    )
    thematic_rows = [row for row in report["signals"] if row["signal_type"] == "THEMATIC_CORRELATION_WATCH"]

    assert thematic_rows
    row = thematic_rows[0]
    assert row["confidence_tier"] in {"LOW", "MEDIUM"}
    assert row["severity_score"] < 50
    assert "advisory-thematic-unrelated" in row["llm_hypothesis_ids"]
    assert row["llm_advisory_evidence_strength_tier"] == "THEMATIC_HYPOTHESIS_ONLY"
    assert row["llm_advisory_severity_boost"] == 0.0
    assert row["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    assert row["implied_direction"] == "NO_SAFE_DIRECTION"


def test_stale_or_lag_hypothesis_creates_stale_watch_not_thematic() -> None:
    snapshot = _fixture_snapshot_with_relationships()
    market_ids = ["fixture:election_candidate_a", "fixture:btc_over_140k_june"]
    hypothesis_report = _build_hypothesis_report(
        snapshot,
        {
            "hypothesis_id": "advisory-stale-lag",
            "market_ids": market_ids,
            "relationship_type": "STALE_OR_LAG_HYPOTHESIS",
            "confidence_tier": "HIGH",
        },
    )
    report = build_trade_indicator_report(
        snapshot,
        run_consistency_checks(snapshot),
        llm_hypotheses_report=hypothesis_report,
    )
    stale_rows = [
        row
        for row in report["signals"]
        if "advisory-stale-lag" in row.get("llm_hypothesis_ids", [])
    ]

    assert stale_rows
    row = stale_rows[0]
    assert row["signal_type"] == "STALE_OR_LAG_WATCH"
    assert row["signal_type"] != "THEMATIC_CORRELATION_WATCH"
    assert row["confidence_tier"] == "MEDIUM"
    assert row["llm_advisory_evidence_strength_tier"] == "STALE_OR_LAG_HYPOTHESIS_ONLY"
    assert row["llm_advisory_severity_boost"] == 0.0


def test_advisory_thematic_hypothesis_does_not_boost_severity_on_matching_signal() -> None:
    snapshot = _fixture_snapshot_with_relationships()
    base_report = build_trade_indicator_report(snapshot, run_consistency_checks(snapshot))
    subset_rows = [row for row in base_report["signals"] if row["signal_type"] == "SUBSET_SUPERSET_PRICE_VIOLATION"]
    target = subset_rows[0]
    market_ids = target["markets_involved"]
    baseline_severity = target["severity_score"]

    hypothesis_report = _build_hypothesis_report(
        snapshot,
        {
            "hypothesis_id": "advisory-thematic-overlap",
            "market_ids": market_ids,
            "relationship_type": "THEMATIC_CORRELATION_HYPOTHESIS",
            "confidence_tier": "MEDIUM",
        },
    )
    enriched = build_trade_indicator_report(
        snapshot,
        run_consistency_checks(snapshot),
        llm_hypotheses_report=hypothesis_report,
    )
    enriched_target = next(row for row in enriched["signals"] if row["signal_id"] == target["signal_id"])

    assert enriched_target["llm_advisory_severity_boost"] == 0.0
    assert enriched_target["severity_score"] == baseline_severity
    assert "advisory-thematic-overlap" in enriched_target["llm_hypothesis_ids"]
    assert enriched_target["llm_advisory_evidence_strength_tier"] == "THEMATIC_HYPOTHESIS_ONLY"


def test_incompatible_hypothesis_relationship_attaches_blocker_without_boost() -> None:
    snapshot = _fixture_snapshot_with_relationships()
    base_report = build_trade_indicator_report(snapshot, run_consistency_checks(snapshot))
    target = next(row for row in base_report["signals"] if row["signal_type"] == "SUBSET_SUPERSET_PRICE_VIOLATION")
    baseline_severity = target["severity_score"]
    hypothesis_report = _build_hypothesis_report(
        snapshot,
        {
            "hypothesis_id": "advisory-incompatible-complement",
            "market_ids": target["markets_involved"],
            "relationship_type": "COMPLEMENT_HYPOTHESIS",
            "confidence_tier": "HIGH",
        },
    )
    enriched = build_trade_indicator_report(
        snapshot,
        run_consistency_checks(snapshot),
        llm_hypotheses_report=hypothesis_report,
    )
    enriched_target = next(row for row in enriched["signals"] if row["signal_id"] == target["signal_id"])

    assert "advisory-incompatible-complement" in enriched_target["llm_hypothesis_ids"]
    assert enriched_target["severity_score"] == baseline_severity
    assert enriched_target["llm_advisory_severity_boost"] == 0.0
    assert enriched_target["corroborating_llm_evidence"] is False
    assert "advisory_llm_relationship_type_does_not_match_signal_type" in enriched_target["review_blockers"]


def test_rejected_hypothesis_does_not_affect_trade_indicators() -> None:
    snapshot = _fixture_snapshot_with_relationships()
    from graph_engine.reporting.llm_relationship_hypotheses import (
        build_llm_relationship_hypotheses_report,
    )

    # Use trade_permission=True to force rejection.
    bad_payload = {
        "hypothesis_id": "bad-perm",
        "market_ids": ["fixture:btc_over_140k_june", "fixture:btc_over_120k_june"],
        "relationship_type": "SUBSET_HYPOTHESIS",
        "natural_language_claim": "Synthetic bad claim.",
        "directionality": None,
        "evidence_fields_used": ["title"],
        "missing_evidence": [],
        "falsification_checks": ["dummy"],
        "confidence_tier": "MEDIUM",
        "trade_permission": True,
    }
    hypothesis_report = build_llm_relationship_hypotheses_report(snapshot, [bad_payload])
    enriched = build_trade_indicator_report(
        snapshot,
        run_consistency_checks(snapshot),
        llm_hypotheses_report=hypothesis_report,
    )

    assert hypothesis_report["rejected_hypothesis_count"] == 1
    for row in enriched["signals"]:
        assert "bad-perm" not in (row.get("llm_hypothesis_ids") or [])


def test_logical_hypothesis_only_does_not_stack_boosts() -> None:
    snapshot = _fixture_snapshot_with_relationships()
    base_report = build_trade_indicator_report(snapshot, run_consistency_checks(snapshot))
    target = next(
        row
        for row in base_report["signals"]
        if row["signal_type"] == "THRESHOLD_LADDER_INVERSION" and len(row["markets_involved"]) == 2
    )
    market_ids = target["markets_involved"]
    baseline_severity = target["severity_score"]

    from graph_engine.reporting.llm_relationship_hypotheses import (
        build_llm_relationship_hypotheses_report,
    )

    payloads = [
        {
            "hypothesis_id": f"advisory-multi-{index}",
            "market_ids": market_ids,
            "relationship_type": "THRESHOLD_LADDER_HYPOTHESIS",
            "natural_language_claim": f"Synthetic claim {index}.",
            "directionality": None,
            "evidence_fields_used": ["title"],
            "missing_evidence": [],
            "falsification_checks": ["check"],
            "confidence_tier": "MEDIUM",
            "trade_permission": False,
        }
        for index in range(5)
    ]
    hypothesis_report = build_llm_relationship_hypotheses_report(snapshot, payloads)
    enriched = build_trade_indicator_report(
        snapshot,
        run_consistency_checks(snapshot),
        llm_hypotheses_report=hypothesis_report,
    )
    enriched_target = next(row for row in enriched["signals"] if row["signal_id"] == target["signal_id"])

    assert enriched_target["llm_advisory_evidence_strength_tier"] == "LOGICAL_HYPOTHESIS_ONLY"
    assert enriched_target["llm_advisory_severity_boost"] == 0.0
    assert enriched_target["severity_score"] == baseline_severity
    assert enriched_target["corroborating_llm_evidence"] is True
    assert all(action in {"WATCH", "MANUAL_REVIEW"} for action in enriched_target["allowed_actions"])


def test_trade_indicator_advisory_integration_keeps_signals_diagnostic_only() -> None:
    snapshot = _fixture_snapshot_with_relationships()
    hypothesis_report = _build_hypothesis_report(
        snapshot,
        {
            "hypothesis_id": "advisory-diagnostic-check",
            "market_ids": ["fixture:btc_over_140k_june", "fixture:btc_over_120k_june"],
            "relationship_type": "THRESHOLD_LADDER_HYPOTHESIS",
            "confidence_tier": "MEDIUM",
        },
    )
    enriched = build_trade_indicator_report(
        snapshot,
        run_consistency_checks(snapshot),
        llm_hypotheses_report=hypothesis_report,
    )

    for row in enriched["signals"]:
        assert row["diagnostic_only"] is True
        assert row["affects_evaluator_gates"] is False
        assert row["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
        assert not {"PAPER_CANDIDATE", "TRADE", "EXECUTE", "ORDER", "BUY", "SELL"} & set(row["allowed_actions"])
        for field in row.get("llm_hypothesis_ids") or []:
            assert "TRADE" not in field.upper()
            assert "EXECUTE" not in field.upper()
