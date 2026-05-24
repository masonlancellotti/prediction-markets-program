from __future__ import annotations

from copy import deepcopy
import json
import re

from graph_engine.consistency.runner import run_consistency_checks
import graph_engine.reporting.hints as hints_module
from graph_engine.reporting.hints import BANNER, build_relative_value_hints_report, write_relative_value_hints_report
from graph_engine.reporting.json_report import PROHIBITED_VIOLATION_FIELDS
from graph_engine.reporting.schema_validation import SchemaValidationError


PROHIBITED_REPORT_TOKENS = sorted(
    PROHIBITED_VIOLATION_FIELDS
    | {
        "PAPER_CANDIDATE",
        "PAPER",
        "POSSIBLE_ARB",
        "executable-arb",
        "fill-size",
        "trade-permission",
    }
)


def _hints_by_relation(report: dict, relation_type: str) -> list[dict]:
    return [hint for hint in report["hints"] if hint["relation_type"] == relation_type]


def test_relative_value_hints_export_is_diagnostic_only(fixture_snapshot) -> None:
    report = build_relative_value_hints_report(fixture_snapshot, run_consistency_checks(fixture_snapshot))
    serialized = json.dumps(report).lower()

    assert report["diagnostic_only"] is True
    assert report["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    assert report["banner"] == BANNER
    assert "Diagnostic-only" in report["banner"]
    assert "Not exact same-payoff evidence" in report["banner"]
    assert "Not permission for any market action" in report["banner"]
    assert report["hint_count"] > 0
    assert "MANUAL_REVIEW" not in {hint["relation_type"] for hint in report["hints"]}
    assert "exact_same_payoff" not in serialized
    assert "exact_same_payoff\": true" not in serialized
    for prohibited in PROHIBITED_VIOLATION_FIELDS:
        assert prohibited not in serialized
    for hint in report["hints"]:
        assert hint["diagnostic_only"] is True
        assert hint["max_action_cap"] in {"WATCH", "MANUAL_REVIEW"}
        assert hint["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
        assert "review_reason" in hint


def test_generated_fixture_report_never_uses_exact_same_payoff(fixture_snapshot) -> None:
    report = build_relative_value_hints_report(fixture_snapshot, run_consistency_checks(fixture_snapshot))

    assert "EXACT_SAME_PAYOFF" not in report["counts_by_relation_type"]
    assert "EXACT_SAME_PAYOFF" not in {hint["relation_type"] for hint in report["hints"]}


def test_written_relative_value_hint_reports_contain_no_prohibited_tokens(tmp_path, fixture_snapshot) -> None:
    json_path = tmp_path / "market_graph_relative_value_hints.json"
    md_path = tmp_path / "market_graph_relative_value_hints.md"
    write_relative_value_hints_report(fixture_snapshot, run_consistency_checks(fixture_snapshot), json_path, md_path)

    combined = json_path.read_text(encoding="utf-8") + md_path.read_text(encoding="utf-8")
    for token in PROHIBITED_REPORT_TOKENS:
        assert re.search(rf"\b{re.escape(token)}\b", combined, flags=re.IGNORECASE) is None


def _assert_hint_writer_rejects(tmp_path, monkeypatch, fixture_snapshot, mutate) -> None:
    report = build_relative_value_hints_report(fixture_snapshot, run_consistency_checks(fixture_snapshot))
    malformed = deepcopy(report)
    mutate(malformed)
    monkeypatch.setattr(hints_module, "build_relative_value_hints_report", lambda *_args, **_kwargs: malformed)

    try:
        write_relative_value_hints_report(
            fixture_snapshot,
            run_consistency_checks(fixture_snapshot),
            tmp_path / "bad_hints.json",
            tmp_path / "bad_hints.md",
        )
    except SchemaValidationError:
        return
    raise AssertionError("writer should reject malformed hint report before writing")


def test_hint_writer_rejects_unknown_top_level_field(tmp_path, monkeypatch, fixture_snapshot) -> None:
    _assert_hint_writer_rejects(
        tmp_path,
        monkeypatch,
        fixture_snapshot,
        lambda report: report.update({"unknown_contract_field": True}),
    )


def test_hint_writer_rejects_unknown_hint_field(tmp_path, monkeypatch, fixture_snapshot) -> None:
    _assert_hint_writer_rejects(
        tmp_path,
        monkeypatch,
        fixture_snapshot,
        lambda report: report["hints"][0].update({"unknown_hint_field": True}),
    )


def test_hint_writer_rejects_invalid_relation_type(tmp_path, monkeypatch, fixture_snapshot) -> None:
    _assert_hint_writer_rejects(
        tmp_path,
        monkeypatch,
        fixture_snapshot,
        lambda report: report["hints"][0].update({"relation_type": "NOT_A_RELATION"}),
    )


def test_hint_writer_rejects_invalid_action_cap(tmp_path, monkeypatch, fixture_snapshot) -> None:
    _assert_hint_writer_rejects(
        tmp_path,
        monkeypatch,
        fixture_snapshot,
        lambda report: report["hints"][0].update({"max_action_cap": "IGNORE"}),
    )


def test_hint_writer_rejects_exact_same_payoff(tmp_path, monkeypatch, fixture_snapshot) -> None:
    _assert_hint_writer_rejects(
        tmp_path,
        monkeypatch,
        fixture_snapshot,
        lambda report: report["hints"][0].update({"relation_type": "EXACT_SAME_PAYOFF"}),
    )


def test_hint_writer_rejects_prohibited_field(tmp_path, monkeypatch, fixture_snapshot) -> None:
    _assert_hint_writer_rejects(
        tmp_path,
        monkeypatch,
        fixture_snapshot,
        lambda report: report["hints"][0].update({"profit_usd": 1}),
    )


def test_hint_writer_rejects_prohibited_value(tmp_path, monkeypatch, fixture_snapshot) -> None:
    _assert_hint_writer_rejects(
        tmp_path,
        monkeypatch,
        fixture_snapshot,
        lambda report: report["hints"][0].update({"review_reason": "possible_arb"}),
    )


def test_saved_hint_diff_reports_contain_no_prohibited_tokens() -> None:
    combined = (
        open("reports/market_graph_hint_diff.json", encoding="utf-8").read()
        + open("reports/market_graph_hint_diff.md", encoding="utf-8").read()
    )

    for token in PROHIBITED_REPORT_TOKENS:
        assert re.search(rf"\b{re.escape(token)}\b", combined, flags=re.IGNORECASE) is None


def test_sports_structural_implication_hints_are_emitted(fixture_snapshot) -> None:
    report = build_relative_value_hints_report(fixture_snapshot, run_consistency_checks(fixture_snapshot))
    ids = {hint["graph_hint_id"] for hint in _hints_by_relation(report, "SUBSET")}

    assert "hint:IMPLICATION_VIOLATION:edge_world_series_implies_al_champion" in ids
    assert "hint:IMPLICATION_VIOLATION:edge_stanley_cup_implies_conference" in ids
    assert "hint:IMPLICATION_VIOLATION:edge_nba_champion_implies_conference" in ids


def test_btc_same_window_hint_and_different_window_downgrade(fixture_snapshot) -> None:
    report = build_relative_value_hints_report(fixture_snapshot, run_consistency_checks(fixture_snapshot))
    by_id = {hint["graph_hint_id"]: hint for hint in report["hints"]}

    same_window = by_id["hint:SUBSET_OVER_SUPERSET:edge_btc_120k_subset_btc_100k_same_window"]
    different_window = by_id["hint:AMBIGUOUS_WORDING:edge_btc_120k_subset_btc_100k_different_window"]

    assert same_window["relation_type"] == "SUBSET"
    assert same_window["hard_bound_type"] == "upper_probability_bound"
    assert different_window["relation_type"] == "AMBIGUOUS_WORDING"
    assert different_window["direction"] == "none"
    assert "threshold_basis_mismatch" in different_window["blockers"]


def test_complement_pair_hint_is_diagnostic_not_same_payoff(fixture_snapshot) -> None:
    report = build_relative_value_hints_report(fixture_snapshot, run_consistency_checks(fixture_snapshot))
    by_id = {hint["graph_hint_id"]: hint for hint in report["hints"]}
    hint = by_id["hint:COMPLEMENT_MISMATCH:edge_referendum_yes_complement_no"]

    assert hint["diagnostic_only"] is True
    assert hint["relation_type"] == "COMPLEMENT"
    assert hint["hard_bound_type"] == "complement_sum_to_one_only_if_proven"
    assert hint["max_action_cap"] in {"WATCH", "MANUAL_REVIEW"}
    assert "complements" in hint["review_reason"]
    assert "exact_same_payoff" not in hint


def test_same_event_rewording_without_proof_stays_ambiguous_review(fixture_snapshot) -> None:
    report = build_relative_value_hints_report(fixture_snapshot, run_consistency_checks(fixture_snapshot))
    by_id = {hint["graph_hint_id"]: hint for hint in report["hints"]}
    hint = by_id["hint:AMBIGUOUS_WORDING:edge_rate_cut_rewording_unproven"]

    assert hint["diagnostic_only"] is True
    assert hint["relation_type"] == "AMBIGUOUS_WORDING"
    assert hint["hard_bound_type"] == "none"
    assert hint["max_action_cap"] == "WATCH"
    assert "settlement_source_not_proven" in hint["blockers"]
    assert "same-payoff" not in hint["review_reason"].lower()


def test_same_payoff_hint_surfaces_settlement_source_proof(fixture_snapshot) -> None:
    report = build_relative_value_hints_report(fixture_snapshot, run_consistency_checks(fixture_snapshot))
    same_payoff = _hints_by_relation(report, "SAME_PAYOFF")

    assert same_payoff
    assert all("settlement_source_proven" in hint for hint in same_payoff)
    assert all(hint["settlement_source_proven"] is True for hint in same_payoff)
    assert all(hint["hard_bound_type"] == "same_payoff_equality_if_settlement_proven" for hint in same_payoff)
    assert all(hint["max_action_cap"] in {"WATCH", "MANUAL_REVIEW"} for hint in same_payoff)


def test_exhaustive_group_requires_complete_set_for_exhaustive_hint(fixture_snapshot) -> None:
    report = build_relative_value_hints_report(fixture_snapshot, run_consistency_checks(fixture_snapshot))
    complete = [
        hint
        for hint in _hints_by_relation(report, "EXHAUSTIVE_GROUP")
        if hint["source_market_id"].startswith("fixture:election_candidate")
    ]
    incomplete = [
        hint
        for hint in _hints_by_relation(report, "MUTUALLY_EXCLUSIVE")
        if hint["source_market_id"].startswith("fixture:award_nominee")
    ]

    assert complete
    assert incomplete
    assert all("exhaustive_group_not_complete" in hint["blockers"] for hint in incomplete)


def test_structural_fixture_coverage_maps_to_safe_hint_relations(fixture_snapshot) -> None:
    report = build_relative_value_hints_report(fixture_snapshot, run_consistency_checks(fixture_snapshot))
    relation_types = {hint["relation_type"] for hint in report["hints"]}

    assert {
        "AMBIGUOUS_WORDING",
        "COMPLEMENT",
        "EXHAUSTIVE_GROUP",
        "MUTUALLY_EXCLUSIVE",
        "SAME_PAYOFF",
        "SUBSET",
    } <= relation_types
    assert all(hint["diagnostic_only"] is True for hint in report["hints"])
    assert all(hint["max_action_cap"] in {"WATCH", "MANUAL_REVIEW"} for hint in report["hints"])
    assert all(hint["hard_bound_type"] != "same_payoff_equality_if_settlement_proven" for hint in _hints_by_relation(report, "SUBSET"))
    assert all(hint["hard_bound_type"] != "same_payoff_equality_if_settlement_proven" for hint in _hints_by_relation(report, "COMPLEMENT"))


def test_same_payoff_hard_bound_only_appears_on_same_payoff_rows(fixture_snapshot) -> None:
    report = build_relative_value_hints_report(fixture_snapshot, run_consistency_checks(fixture_snapshot))

    assert all(
        hint["relation_type"] == "SAME_PAYOFF"
        for hint in report["hints"]
        if hint["hard_bound_type"] == "same_payoff_equality_if_settlement_proven"
    )
