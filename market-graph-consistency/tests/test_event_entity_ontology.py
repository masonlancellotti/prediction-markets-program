from __future__ import annotations

import json
from datetime import datetime, timezone

from graph_engine.models import GraphSnapshot
from graph_engine.reporting.event_entity_ontology import (
    build_event_entity_ontology_report,
    validate_event_entity_ontology_report,
    write_event_entity_ontology_report,
)
from tests.conftest import make_node


def _snapshot(*nodes) -> GraphSnapshot:
    return GraphSnapshot(
        snapshot_id="ontology-test",
        as_of=datetime(2026, 5, 25, tzinfo=timezone.utc),
        nodes={node.market_id: node for node in nodes},
    )


def _row_by_type_and_name(report: dict, entity_type: str, canonical_name: str) -> dict:
    return next(
        row
        for row in report["ontology_rows"]
        if row["entity_type"] == entity_type and row["canonical_name"] == canonical_name
    )


def test_btc_and_eth_assets_normalize() -> None:
    btc = make_node(
        "kalshi:btc_over_100k",
        0.5,
        title="BTC above 100k by June 30",
        canonical_text="Bitcoin is above 100000 USD by 2026-06-30.",
        entities=["BTC"],
        themes=["crypto", "threshold"],
        observable="BTC_USD",
        settlement_source="fixture_btc_index",
        window="2026-06-30",
        resolution_date="2026-06-30",
    )
    eth = make_node(
        "polymarket:eth_over_5000",
        0.4,
        title="Ethereum above 5000 by June 30",
        canonical_text="ETH is above 5000 USD by 2026-06-30.",
        entities=["Ethereum"],
        themes=["crypto", "threshold"],
        observable="ETH_USD",
        settlement_source="fixture_eth_index",
        window="2026-06-30",
        resolution_date="2026-06-30",
    )

    report = build_event_entity_ontology_report(_snapshot(btc, eth))
    btc_row = _row_by_type_and_name(report, "CRYPTO_ASSET", "BTC")
    eth_row = _row_by_type_and_name(report, "CRYPTO_ASSET", "ETH")

    assert btc_row["confidence_tier"] == "HIGH"
    assert btc_row["evidence_type"] == "structured_formula"
    assert "Bitcoin" in btc_row["aliases"]
    assert eth_row["confidence_tier"] == "MEDIUM"
    assert eth_row["evidence_type"] == "ticker_prefix"
    assert "Ethereum" in eth_row["aliases"]
    validate_event_entity_ontology_report(report)


def test_sports_team_aliases_normalize_with_structured_evidence() -> None:
    node = make_node(
        "kalshi:cleveland_world_series",
        0.3,
        title="Cleveland wins the World Series",
        canonical_text="Cleveland baseball wins the World Series.",
        entities=["Cleveland baseball"],
        themes=["sports", "mlb"],
        observable="mlb_cleveland_postseason",
        settlement_source="official_mlb_result",
        window="2026_mlb_postseason",
    )

    report = build_event_entity_ontology_report(_snapshot(node))
    row = _row_by_type_and_name(report, "SPORTS_TEAM", "Cleveland baseball")

    assert row["confidence_tier"] == "MEDIUM"
    assert row["evidence_type"] == "explicit_metadata"
    assert "Cleveland" in row["aliases"]


def test_title_only_sports_alias_remains_low_confidence() -> None:
    node = make_node(
        "polymarket:cleveland_title_only",
        0.3,
        title="Cleveland wins the World Series",
        canonical_text="Cleveland wins the World Series.",
        entities=[],
        themes=["sports"],
    )

    report = build_event_entity_ontology_report(_snapshot(node))
    row = _row_by_type_and_name(report, "SPORTS_TEAM", "Cleveland")

    assert row["confidence_tier"] == "LOW"
    assert row["evidence_type"] == "title_only_low_confidence"
    assert row["not_identity_proof_reason"] == "title_only_hint_not_identity_proof"
    assert "title_only_low_confidence" in row["blockers"]


def test_election_result_entity_is_not_payoff_equivalence() -> None:
    node = make_node(
        "kalshi:election_candidate_a",
        0.45,
        title="Candidate A wins the example election",
        canonical_text="Candidate A wins the example election.",
        entities=["Candidate A"],
        themes=["election"],
        resolution_date="2026-11-03",
    )

    report = build_event_entity_ontology_report(_snapshot(node))
    contest = _row_by_type_and_name(report, "ELECTION_CONTEST", "Example election 2026-11-03")
    candidate = _row_by_type_and_name(report, "CANDIDATE_OR_PARTY", "Candidate A")

    assert contest["confidence_tier"] == "MEDIUM"
    assert contest["not_identity_proof_reason"] == "contest_identity_not_payoff_equivalence"
    assert candidate["not_identity_proof_reason"] == "contest_identity_not_payoff_equivalence"


def test_llm_only_alias_cannot_be_high_confidence() -> None:
    node = make_node(
        "fixture:generic_question",
        0.5,
        title="Generic question",
        canonical_text="Generic question.",
        entities=[],
        themes=["misc"],
    )
    llm_report = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "validated_hypotheses": [
            {
                "hypothesis_id": "hyp:alias",
                "source_market_ids": ["fixture:generic_question"],
                "event_class": "crypto",
                "natural_language_claim": "This might be Ethereum related.",
                "confidence_tier": "HIGH",
            }
        ],
    }

    report = build_event_entity_ontology_report(_snapshot(node), llm_hypotheses_report=llm_report)
    row = next(row for row in report["ontology_rows"] if row["entity_type"] == "OTHER_UNKNOWN")

    assert row["confidence_tier"] == "LOW"
    assert row["evidence_type"] == "title_only_low_confidence"
    assert row["not_identity_proof_reason"] == "llm_alias_advisory_not_identity_proof"
    assert "Ethereum" in row["aliases"] or row["canonical_name"] == "Ethereum"


def test_cross_venue_candidates_dedup_by_venue_root() -> None:
    # Two venues sharing the same root (``fixture`` and ``fixture_payoff``)
    # represent the same underlying source and must NOT be reported as a
    # cross-venue entity candidate.
    base = make_node(
        "fixture:btc_over_100k",
        0.5,
        title="BTC above 100k by June 30",
        canonical_text="Bitcoin is above 100000 USD by 2026-06-30.",
        entities=["BTC"],
        themes=["crypto", "threshold"],
        observable="BTC_USD",
        settlement_source="fixture_btc_index",
        window="2026-06-30",
        resolution_date="2026-06-30",
    )
    sibling = make_node(
        "fixture_payoff:btc_over_100k",
        0.5,
        title="BTC above 100k by June 30",
        canonical_text="Bitcoin is above 100000 USD by 2026-06-30.",
        entities=["BTC"],
        themes=["crypto", "threshold"],
        observable="BTC_USD",
        settlement_source="fixture_btc_index",
        window="2026-06-30",
        resolution_date="2026-06-30",
    )

    report = build_event_entity_ontology_report(_snapshot(base, sibling))

    asset_row = _row_by_type_and_name(report, "CRYPTO_ASSET", "BTC")
    # The asset is present at both fixture venues but they share root "fixture".
    assert set(asset_row["venues"]) == {"fixture", "fixture_payoff"}
    assert asset_row["entity_id"] not in report["summary"]["cross_venue_entity_candidates"]


def test_range_bucket_turnout_node_is_not_classified_as_election_contest() -> None:
    # Range-bucket markets share the "Example Election" entity tag for context
    # but are not candidate or referendum outcomes. They must not be promoted
    # into an ELECTION_CONTEST identity row.
    node = make_node(
        "fixture:turnout_under_50",
        0.43,
        bid=0.41,
        ask=0.45,
        title="Example turnout below 50 percent",
        canonical_text="Example turnout is below 50 percent.",
        entities=["Example Election"],
        themes=["range-bucket", "partition", "fixture"],
        observable="example_turnout",
        settlement_source="fixture_election_board",
        window="2026-11-03",
        resolution_date="2026-11-03",
    )

    report = build_event_entity_ontology_report(_snapshot(node))

    assert not any(row["entity_type"] == "ELECTION_CONTEST" for row in report["ontology_rows"])
    assert not any(row["entity_type"] == "CANDIDATE_OR_PARTY" for row in report["ontology_rows"])


def test_output_remains_diagnostic_only_and_writes(tmp_path) -> None:
    node = make_node(
        "kalshi:btc_over_100k",
        0.5,
        title="BTC above 100k by June 30",
        canonical_text="Bitcoin is above 100000 USD by 2026-06-30.",
        entities=["BTC"],
        themes=["crypto", "threshold"],
        observable="BTC_USD",
        settlement_source="fixture_btc_index",
        window="2026-06-30",
        resolution_date="2026-06-30",
    )
    json_output = tmp_path / "market_graph_event_entity_ontology.json"
    markdown_output = tmp_path / "market_graph_event_entity_ontology.md"

    report = write_event_entity_ontology_report(_snapshot(node), json_output, markdown_output)

    assert report["diagnostic_only"] is True
    assert report["affects_evaluator_gates"] is False
    assert report["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    assert all(row["diagnostic_only"] is True for row in report["ontology_rows"])
    assert json.loads(json_output.read_text(encoding="utf-8")) == report
    assert "# Market Graph Event Entity Ontology" in markdown_output.read_text(encoding="utf-8")
    validate_event_entity_ontology_report(report)
