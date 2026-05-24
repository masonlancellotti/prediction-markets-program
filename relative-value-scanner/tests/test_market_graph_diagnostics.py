from __future__ import annotations

import json
from datetime import datetime, timezone

import scan
from relative_value.market_graph_diagnostics import build_market_graph_diagnostics
from relative_value.market_graph_hints import build_market_graph_relative_value_hints
from relative_value.paper_candidate_evaluator import (
    ACTION_MANUAL_REVIEW,
    ACTION_PAPER_CANDIDATE,
    ALLOWED_SAME_PAYOFF_RELATIONSHIP_SOURCES,
    PaperCandidateEvaluatorConfig,
    evaluate_paper_candidates,
)


def _payload() -> dict:
    return build_market_graph_diagnostics()


def _edges(payload: dict, relation_type: str | None = None) -> list[dict]:
    rows = payload["edges"]
    if relation_type is None:
        return rows
    return [row for row in rows if row["relation_type"] == relation_type]


def _edge(payload: dict, source: str, target: str, relation_type: str) -> dict:
    for row in _edges(payload, relation_type):
        if row["source_market_id"] == source and row["target_market_id"] == target:
            return row
    raise AssertionError(f"missing {relation_type} edge {source} -> {target}")


def test_world_series_to_alcs_is_subset_edge() -> None:
    payload = _payload()

    edge = _edge(payload, "mlb-world-series-cleveland", "mlb-alcs-cleveland", "SUBSET")

    assert edge["direction"] == "source_implies_target"
    assert edge["hard_bound_type"] == "upper_probability_bound"
    assert edge["diagnostic_only"] is True
    assert edge["action"] == "WATCH"


def test_btc_threshold_monotonicity() -> None:
    payload = _payload()

    subset = _edge(payload, "btc-over-120k-2026-06-30", "btc-over-100k-2026-06-30", "SUBSET")
    superset = _edge(payload, "btc-over-100k-2026-06-30", "btc-over-120k-2026-06-30", "SUPERSET")

    assert subset["direction"] == "source_implies_target"
    assert superset["direction"] == "target_implies_source"
    assert "greater-than comparator" in " ".join(subset["required_conditions"])


def test_mutually_exclusive_candidates() -> None:
    payload = _payload()

    edge = _edge(payload, "election-candidate-a", "election-candidate-b", "MUTUALLY_EXCLUSIVE")

    assert edge["hard_bound_type"] == "cannot_both_resolve_yes"
    assert edge["action"] == "MANUAL_REVIEW"
    assert edge["blockers"] == []


def test_exhaustive_group_requires_completeness() -> None:
    payload = _payload()

    complete = _edge(payload, "election-candidate-a", "election-candidate-b", "EXHAUSTIVE_GROUP")
    incomplete = _edge(payload, "award-nominee-a", "award-nominee-b", "MANUAL_REVIEW")

    assert complete["hard_bound_type"] == "sum_to_one_only_if_complete"
    assert complete["blockers"] == []
    assert "exhaustive_group_not_marked_complete" in incomplete["blockers"]
    assert not any(
        row["relation_type"] == "EXHAUSTIVE_GROUP"
        and row["source_market_id"] == "award-nominee-a"
        and row["target_market_id"] == "award-nominee-b"
        for row in payload["edges"]
    )


def test_unrelated_city_token_sports_teams_rejected() -> None:
    payload = _payload()

    edge = _edge(payload, "cleveland-browns-win", "cleveland-guardians-win", "UNRELATED")

    assert "city_token_overlap_not_entity_match" in edge["blockers"]
    assert edge["direction"] == "none"


def test_report_has_no_paper_possible_arb_or_trade_labels() -> None:
    serialized = json.dumps(_payload())

    assert "PAPER" not in serialized
    assert "POSSIBLE_ARB" not in serialized
    assert "trade" not in serialized.lower()


def test_default_scan_remains_static_fixture(capsys) -> None:
    result = scan.main([])

    assert result == 0
    output = capsys.readouterr().out
    assert "data_source_mode=STATIC_FIXTURE" in output
    assert "live_fetch_attempted=false" in output


def test_market_graph_diagnostics_cli_writes_reports(tmp_path, capsys) -> None:
    json_output = tmp_path / "market_graph_consistency_diagnostics.json"
    markdown_output = tmp_path / "market_graph_consistency_diagnostics.md"

    result = scan.main(
        [
            "market-graph-diagnostics",
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )

    assert result == 0
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["data_source_mode"] == "STATIC_FIXTURE"
    assert payload["live_fetch_attempted"] is False
    assert payload["diagnostic_only"] is True
    assert markdown_output.exists()
    stdout = capsys.readouterr().out
    assert "market_graph_diagnostics_status=OK" in stdout
    assert "PAPER" not in stdout
    assert "POSSIBLE_ARB" not in stdout


def test_valid_graph_report_becomes_info_only_hints() -> None:
    payload = build_market_graph_relative_value_hints(graph_report=_payload())

    assert payload["diagnostic_only"] is True
    assert payload["allowed_actions"] == ["MANUAL_REVIEW", "WATCH"]
    assert payload["safety"]["diagnostic_only"] is True
    assert payload["safety"]["info_only"] is True
    assert payload["safety"]["sets_same_payoff_true"] is False
    assert payload["safety"]["sets_contract_relationship_equivalent"] is False
    assert payload["safety"]["sets_same_payoff_board_v1_source"] is False
    assert payload["safety"]["emits_paper_candidate"] is False
    assert payload["safety"]["affects_evaluator_gates"] is False
    assert payload["safety"]["evaluator_trusted_relationship_source_added"] is False
    first = payload["hints"][0]
    assert first["finding_id"].startswith("graph_hint_")
    assert first["magnitude_probability"] is not None
    assert first["max_action_cap"] in {"WATCH", "MANUAL_REVIEW"}
    assert first["max_action_cap_reason"] == "graph_diagnostic_info_only"
    assert first["sets_contract_relationship_equivalent"] is False
    assert first["sets_same_payoff_board_v1_source"] is False
    assert first["affects_evaluator_gates"] is False
    assert first["relation_type_trusted_for_same_payoff"] is False
    assert first["relationship_promotion_allowed"] is False


def test_non_diagnostic_graph_report_is_rejected() -> None:
    payload = _payload()
    payload["diagnostic_only"] = False

    try:
        build_market_graph_relative_value_hints(graph_report=payload)
    except ValueError as exc:
        assert "diagnostic_only=true" in str(exc)
    else:
        raise AssertionError("non-diagnostic graph report should fail closed")


def test_paper_possible_arb_trade_and_pnl_fields_are_rejected() -> None:
    for field in ("PAPER_CANDIDATE", "POSSIBLE_ARB", "trade_signal", "PnL"):
        payload = _payload()
        payload["edges"][0][field] = "unsafe"

        try:
            build_market_graph_relative_value_hints(graph_report=payload)
        except ValueError as exc:
            assert "prohibited field" in str(exc)
        else:
            raise AssertionError(f"{field} should fail closed")


def test_graph_actions_above_manual_review_are_rejected() -> None:
    payload = _payload()
    payload["allowed_actions"] = ["WATCH", "MANUAL_REVIEW", "PAPER_CANDIDATE"]

    try:
        build_market_graph_relative_value_hints(graph_report=payload)
    except ValueError as exc:
        assert "allowed_actions" in str(exc)
    else:
        raise AssertionError("unsafe allowed action should fail closed")

    payload = _payload()
    payload["edges"][0]["action"] = "PAPER_CANDIDATE"
    try:
        build_market_graph_relative_value_hints(graph_report=payload)
    except ValueError as exc:
        assert "edge action" in str(exc) or "prohibited field" in str(exc)
    else:
        raise AssertionError("unsafe edge action should fail closed")

    payload = _payload()
    payload["edges"][0]["max_action_cap"] = "PAPER_CANDIDATE"
    try:
        build_market_graph_relative_value_hints(graph_report=payload)
    except ValueError as exc:
        assert "max_action_cap" in str(exc)
    else:
        raise AssertionError("unsafe edge max_action_cap should fail closed")


def test_exact_same_payoff_graph_relation_is_downgraded_not_trusted() -> None:
    for relation_type in ("EXACT_SAME_PAYOFF", "SAME_PAYOFF"):
        payload = _payload()
        payload["edges"][0]["relation_type"] = relation_type

        result = build_market_graph_relative_value_hints(graph_report=payload)

        hint = result["hints"][0]
        assert hint["relation_type"] == relation_type
        assert hint["downgraded_from_exact_same_payoff_label"] is True
        assert hint["relation_type_trusted_for_same_payoff"] is False
        assert hint["relationship_promotion_allowed"] is False
        assert hint["sets_same_payoff_true"] is False
        assert hint["sets_contract_relationship_equivalent"] is False
        assert hint["sets_same_payoff_board_v1_source"] is False
        assert hint["affects_evaluator_gates"] is False
        assert "graph_exact_same_payoff_not_trusted" in hint["blockers"]
        assert hint["max_action_cap"] in {"WATCH", "MANUAL_REVIEW"}


def test_same_payoff_like_graph_values_are_rejected_not_converted_to_relationships() -> None:
    payload = _payload()
    payload["edges"][0]["source"] = "same_payoff_board_v1"

    try:
        build_market_graph_relative_value_hints(graph_report=payload)
    except ValueError as exc:
        assert "prohibited trusted source value" in str(exc)
    else:
        raise AssertionError("same_payoff_board_v1 graph source should fail closed")


def test_graph_hint_source_never_feeds_evaluator_trusted_sources() -> None:
    assert "market_graph_relative_value_hints_v1" not in ALLOWED_SAME_PAYOFF_RELATIONSHIP_SOURCES
    assert ALLOWED_SAME_PAYOFF_RELATIONSHIP_SOURCES == {"same_payoff_board_v1"}


def test_graph_hints_do_not_affect_paper_candidate_count() -> None:
    graph_payload = build_market_graph_relative_value_hints(graph_report=_payload())

    assert graph_payload["hint_count"] > 0
    assert graph_payload["safety"]["affects_evaluator_gates"] is False

    evaluator_payload = evaluate_paper_candidates(
        pairs_payload=_graph_source_pairs_payload(),
        polymarket_payload=_evaluator_market_payload("polymarket"),
        kalshi_payload=_evaluator_market_payload("kalshi"),
        config=PaperCandidateEvaluatorConfig(accept_unit_mismatch=True),
        detected_at=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert evaluator_payload["counts_by_action"][ACTION_PAPER_CANDIDATE] == 0
    assert evaluator_payload["counts_by_action"][ACTION_MANUAL_REVIEW] == 1
    assert evaluator_payload["ledger"][0]["missed_fill_reason"] == "relationship_same_payoff_not_proven"


def test_graph_hints_do_not_mutate_source_report() -> None:
    payload = _payload()
    before = json.dumps(payload, sort_keys=True)

    build_market_graph_relative_value_hints(graph_report=payload)

    assert json.dumps(payload, sort_keys=True) == before


def test_explain_market_graph_diagnostics_cli_writes_info_only_hints(tmp_path, capsys) -> None:
    graph_report = tmp_path / "graph.json"
    json_output = tmp_path / "hints.json"
    markdown_output = tmp_path / "hints.md"
    graph_report.write_text(json.dumps(_payload()), encoding="utf-8")

    result = scan.main(
        [
            "explain-market-graph-diagnostics",
            "--graph-report",
            str(graph_report),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )

    assert result == 0
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["diagnostic_only"] is True
    assert payload["hint_count"] > 0
    assert "not paper-trade permission" in payload["banner"]
    stdout = capsys.readouterr().out
    assert "market_graph_hints_status=OK" in stdout
    assert "PAPER" not in stdout
    assert "POSSIBLE_ARB" not in stdout


def _graph_source_pairs_payload() -> dict:
    return {
        "schema_version": 1,
        "pairs": [
            {
                "polymarket": {"market_id": "graph-poly"},
                "kalshi": {"ticker": "GRAPH-KALSHI"},
                "ineligibility_reasons": [],
                "contract_relationship": {
                    "relationship": "EQUIVALENT",
                    "same_payoff": True,
                    "blocking_reasons": [],
                    "manual_review_required": False,
                    "source": "market_graph_relative_value_hints_v1",
                    "same_payoff_board_evidence": {
                        "classifier_version": "same-payoff-board-v1",
                        "strict_pass_count": 1,
                        "strict_comparator_count": 1,
                    },
                },
            }
        ],
    }


def _evaluator_market_payload(venue: str) -> dict:
    is_poly = venue == "polymarket"
    row = {
        "venue": venue,
        "market_id": "graph-poly" if is_poly else "GRAPH-KALSHI",
        "question": "Will graph hint market resolve yes?",
        "end_date": "2026-05-20T13:00:00+00:00",
        "orderbook_enrichment": {
            "orderbook_captured_at": "2026-05-20T11:59:30+00:00",
            "best_bid": 0.66 if is_poly else 0.58,
            "best_ask": 0.68 if is_poly else 0.60,
            "depth_at_best_bid": 5.0,
            "depth_at_best_ask": 5.0,
            "enrichment_status": "enriched",
            "enrichment_warnings": [],
        },
    }
    if is_poly:
        row["outcomes"] = [{"name": "Yes"}, {"name": "No"}]
        row["raw"] = {"clobTokenIds": '["yes-token", "no-token"]'}
    else:
        row["ticker"] = "GRAPH-KALSHI"
    return {"schema_version": 1, "normalized_markets": [row]}
