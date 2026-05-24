from __future__ import annotations

import json

import scan
from relative_value.market_graph_diagnostics import build_market_graph_diagnostics
from relative_value.market_graph_hints import build_market_graph_relative_value_hints


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
    assert payload["safety"]["info_only"] is True
    assert payload["safety"]["sets_same_payoff_true"] is False
    assert payload["safety"]["affects_evaluator_gates"] is False
    first = payload["hints"][0]
    assert first["finding_id"].startswith("graph_hint_")
    assert first["magnitude_probability"] is not None
    assert first["max_action_cap"] in {"WATCH", "MANUAL_REVIEW"}
    assert first["max_action_cap_reason"] == "graph_diagnostic_info_only"


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
