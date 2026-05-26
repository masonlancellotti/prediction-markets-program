from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path

import pytest

from graph_engine.formula import (
    MarketFormula,
    build_formula_diagnostics_report,
    build_formula_diagnostics_report_from_formulas,
    import_proposed_market_formulas,
    load_proposed_market_formulas,
    parse_fixture_market_formula,
)
from graph_engine.formula_clusters import build_formula_cluster_constraints_report
from graph_engine.formula_adapters import (
    adapt_kalshi_market_formula,
    adapt_polymarket_market_formula,
    load_live_like_market_formulas,
)
from graph_engine.reporting.formula_watchlist import (
    build_formula_watchlist_report,
    build_investigation_requests_report,
    validate_formula_watchlist_report,
    validate_investigation_requests_report,
    write_formula_watchlist_reports,
)
from graph_engine.models import GraphSnapshot
from graph_engine.reporting.json_report import PROHIBITED_VIOLATION_FIELDS, build_json_report
from graph_engine.reporting.schema_validation import SchemaValidationError, validate_formula_diagnostics_contract
from tests.conftest import make_node


PROPOSED_FORMULAS_FIXTURE = Path(__file__).parent / "fixtures" / "proposed_market_formulas.json"
LIVE_LIKE_FIXTURE_DIR = Path(__file__).parents[1] / "venues" / "fixtures" / "live_like_formula_records"
KALSHI_LIVE_LIKE_FIXTURE = LIVE_LIKE_FIXTURE_DIR / "kalshi_markets.json"
POLYMARKET_LIVE_LIKE_FIXTURE = LIVE_LIKE_FIXTURE_DIR / "polymarket_markets.json"
PROHIBITED_TOKENS = sorted(
    PROHIBITED_VIOLATION_FIELDS
    | {
        "arb",
        "evaluator_ready",
        "PAPER_CANDIDATE",
        "POSSIBLE_ARB",
        "executable-arb",
        "fill",
        "fill-size",
        "order",
        "profit",
        "size",
        "trade",
        "trade-permission",
        "trusted_relationship",
    }
)


def _diagnostics_by_relation(report: dict, relation: str) -> list[dict]:
    return [item for item in report["formula_diagnostics"] if item["formula_relation"] == relation]


def _proposal_payload() -> dict:
    return json.loads(PROPOSED_FORMULAS_FIXTURE.read_text(encoding="utf-8"))


def _live_like_formulas() -> list:
    return [
        *load_live_like_market_formulas(KALSHI_LIVE_LIKE_FIXTURE),
        *load_live_like_market_formulas(POLYMARKET_LIVE_LIKE_FIXTURE),
    ]


def _assert_proposal_invalid(payload: dict) -> None:
    try:
        import_proposed_market_formulas(payload)
    except SchemaValidationError:
        return
    raise AssertionError("invalid proposed formula payload should fail closed")


def _btc_formula(
    market_id: str,
    threshold: float,
    source: str | None = "fixture_btc_index",
    date: str | None = "2026-06-30",
    comparator: str | None = ">",
    units: str | None = "USD",
) -> MarketFormula:
    return MarketFormula(
        market_id=market_id,
        family="BTC_THRESHOLD",
        subject="BTC",
        asset="BTC",
        source=source,
        date=date,
        settlement_time=date,
        comparator=comparator,
        threshold=threshold,
        units=units,
        parse_quality=0.95,
        blockers=[],
        provenance={"fixture": "formula_cluster_test"},
    )


def _fed_formula(market_id: str, lower: float, upper: float, source: str | None = "federal_reserve_official", meeting: str | None = "2026-06-17") -> MarketFormula:
    return MarketFormula(
        market_id=market_id,
        family="FED_MEETING_RANGE",
        subject="FED_FUNDS",
        source=source,
        meeting_date=meeting,
        settlement_time=meeting,
        comparator="in_range",
        lower_bound=lower,
        upper_bound=upper,
        units="percent",
        parse_quality=0.95,
        blockers=[],
        provenance={"fixture": "formula_cluster_test"},
    )


def test_btc_formula_parser_extracts_typed_threshold(fixture_snapshot) -> None:
    formula = parse_fixture_market_formula(fixture_snapshot.nodes["fixture:btc_over_120k_june"])

    assert formula.family == "BTC_THRESHOLD"
    assert formula.asset == "BTC"
    assert formula.source == "fixture_btc_index"
    assert formula.date == "2026-06-30"
    assert formula.comparator == ">"
    assert formula.threshold == 120000
    assert formula.units == "USD"
    assert formula.side == "YES"
    assert formula.blockers == []


def test_formula_diagnostics_include_btc_threshold_ladder_and_ambiguous_date_mismatch(fixture_snapshot) -> None:
    report = build_formula_diagnostics_report(fixture_snapshot)
    ladder = _diagnostics_by_relation(report, "threshold_ladder")
    ambiguous = _diagnostics_by_relation(report, "ambiguous_not_exact")

    assert any({"fixture:btc_over_140k_june", "fixture:btc_over_120k_june"} == set(item["market_ids"]) for item in ladder)
    assert any({"fixture:btc_over_120k_june", "fixture:btc_over_100k_other_window"} == set(item["market_ids"]) for item in ambiguous)
    assert all(item["diagnostic_only"] is True for item in report["formula_diagnostics"])
    assert all(item["affects_evaluator_gates"] is False for item in report["formula_diagnostics"])


def test_title_similarity_alone_does_not_create_trusted_equality(fixture_snapshot) -> None:
    report = build_formula_diagnostics_report(fixture_snapshot)
    ambiguous = _diagnostics_by_relation(report, "ambiguous_not_exact")

    assert any({"fixture:fed_june_425_450_a", "fixture:fed_july_425_450"} == set(item["market_ids"]) for item in ambiguous)
    assert all(item["formula_relation"] != "trusted_equality" for item in report["formula_diagnostics"])


def test_exact_typed_formula_match_is_still_diagnostic_only(fixture_snapshot) -> None:
    report = build_formula_diagnostics_report(fixture_snapshot)
    matches = _diagnostics_by_relation(report, "typed_formula_match_review_only")
    target = next(item for item in matches if {"fixture:fed_june_425_450_a", "fixture:fed_june_425_450_b"} == set(item["market_ids"]))

    assert target["diagnostic_only"] is True
    assert target["affects_evaluator_gates"] is False
    assert target["max_action_cap"] == "MANUAL_REVIEW"


def test_fed_range_overlap_is_diagnostic_only(fixture_snapshot) -> None:
    report = build_formula_diagnostics_report(fixture_snapshot)
    overlaps = _diagnostics_by_relation(report, "overlap_not_identical")

    assert any({"fixture:fed_june_425_450_a", "fixture:fed_june_400_450"} == set(item["market_ids"]) for item in overlaps)
    assert all(item["max_action_cap"] in {"WATCH", "MANUAL_REVIEW"} for item in overlaps)


def test_missing_formula_source_creates_blocker(fixture_snapshot) -> None:
    report = build_formula_diagnostics_report(fixture_snapshot)
    blocked = _diagnostics_by_relation(report, "parse_blocked")

    assert any("fixture:fed_missing_source" in item["market_ids"] for item in blocked)
    assert any("missing_source" in item["blockers"] for item in blocked)


def test_formula_diagnostics_are_in_json_report(fixture_snapshot) -> None:
    report = build_json_report(fixture_snapshot, [])

    assert report["formula_diagnostics"]["diagnostic_only"] is True
    assert report["formula_diagnostics"]["affects_evaluator_gates"] is False
    assert report["summary"]["formula_diagnostic_count"] == report["formula_diagnostics"]["comparison_count"]


def test_formula_contract_rejects_prohibited_value(fixture_snapshot) -> None:
    report = build_formula_diagnostics_report(fixture_snapshot)
    report["formula_diagnostics"][0]["review_reason"] = "POSSIBLE" + "_" + "ARB"

    try:
        validate_formula_diagnostics_contract(report)
    except SchemaValidationError:
        return
    raise AssertionError("prohibited formula diagnostic value should fail")


@pytest.mark.parametrize("token", ["trade", "fill", "size", "paper"])
def test_formula_contract_rejects_bare_prohibited_values(fixture_snapshot, token: str) -> None:
    report = build_formula_diagnostics_report(fixture_snapshot)
    report["formula_diagnostics"][0]["review_reason"] = token

    try:
        validate_formula_diagnostics_contract(report)
    except SchemaValidationError:
        return
    raise AssertionError("bare prohibited formula diagnostic value should fail")


def test_formula_contract_rejects_prohibited_field(fixture_snapshot) -> None:
    report = build_formula_diagnostics_report(fixture_snapshot)
    report["formula_diagnostics"][0]["profit_usd"] = 1

    try:
        validate_formula_diagnostics_contract(report)
    except SchemaValidationError:
        return
    raise AssertionError("prohibited formula diagnostic field should fail")


def test_unknown_or_missing_formula_fields_fail_closed() -> None:
    snapshot = GraphSnapshot(
        snapshot_id="test",
        as_of="2026-05-19T18:00:00+00:00",
        nodes={
            "test:unknown": make_node(
                "test:unknown",
                0.5,
                title="Similar looking BTC market",
                canonical_text="Bitcoin market with no threshold date or source.",
                settlement_source=None,
            )
        },
    )

    report = build_formula_diagnostics_report(snapshot)

    assert report["formulas"][0]["blockers"]
    assert report["formula_diagnostics"] == []


def test_formula_diagnostics_contain_no_prohibited_tokens(fixture_snapshot) -> None:
    report = build_formula_diagnostics_report(fixture_snapshot)
    serialized = json.dumps(report).lower()

    for token in PROHIBITED_TOKENS:
        assert re.search(rf"\b{re.escape(token)}\b", serialized, flags=re.IGNORECASE) is None


def test_valid_proposed_formula_fixture_imports() -> None:
    formulas = load_proposed_market_formulas(PROPOSED_FORMULAS_FIXTURE)

    assert len(formulas) == 5
    assert formulas[0].provenance["validator_required"] is True
    assert {formula.family for formula in formulas} == {"BTC_THRESHOLD", "FED_MEETING_RANGE"}


def test_proposed_formula_diagnostics_remain_review_only() -> None:
    formulas = load_proposed_market_formulas(PROPOSED_FORMULAS_FIXTURE)
    report = build_formula_diagnostics_report_from_formulas(formulas)
    matches = _diagnostics_by_relation(report, "typed_formula_match_review_only")

    assert any({"proposal:btc_over_100k_june_a", "proposal:btc_over_100k_june_b"} == set(item["market_ids"]) for item in matches)
    assert all(item["diagnostic_only"] is True for item in matches)
    assert all(item["affects_evaluator_gates"] is False for item in matches)
    assert all(item["max_action_cap"] == "MANUAL_REVIEW" for item in matches)


def test_proposed_formula_title_similarity_still_fails_closed() -> None:
    payload = _proposal_payload()
    payload["proposed_formulas"] = [
        {
            "market_id": "proposal:btc_similar_missing_date",
            "family": "BTC_THRESHOLD",
            "subject": "BTC",
            "asset": "BTC",
            "source": "fixture_btc_index",
            "comparator": ">",
            "threshold": 100000,
            "units": "USD",
            "side": "YES",
            "parse_quality": 0.9,
            "blockers": [],
            "provenance": {"proposed_by": "fixture_json"},
        }
    ]
    payload["formula_count"] = 1

    _assert_proposal_invalid(payload)


def test_proposed_formula_rejects_missing_family_specific_keys() -> None:
    payload = _proposal_payload()
    del payload["proposed_formulas"][0]["source"]

    _assert_proposal_invalid(payload)


def test_proposed_formula_rejects_unsupported_comparator() -> None:
    payload = _proposal_payload()
    payload["proposed_formulas"][0]["comparator"] = "approximately"

    _assert_proposal_invalid(payload)


def test_proposed_formula_rejects_threshold_without_units() -> None:
    payload = _proposal_payload()
    del payload["proposed_formulas"][0]["units"]

    _assert_proposal_invalid(payload)


def test_proposed_formula_rejects_fed_missing_meeting_or_range() -> None:
    payload = _proposal_payload()
    fed = payload["proposed_formulas"][3]
    del fed["meeting_date"]

    _assert_proposal_invalid(payload)


def test_proposed_formula_rejects_low_parse_quality() -> None:
    payload = _proposal_payload()
    payload["proposed_formulas"][0]["parse_quality"] = 0.2

    _assert_proposal_invalid(payload)


def test_proposed_formula_rejects_exact_same_payoff_claim() -> None:
    payload = _proposal_payload()
    payload["proposed_formulas"][0]["exact_same_payoff"] = True

    _assert_proposal_invalid(payload)


def test_proposed_formula_rejects_evaluator_trading_or_paper_fields() -> None:
    for field_name in ["affects_evaluator_gates", "trade_permission", "profit_usd", "PAPER_CANDIDATE"]:
        payload = _proposal_payload()
        payload["proposed_formulas"][0][field_name] = True

        _assert_proposal_invalid(payload)


def test_proposed_formula_rejects_prohibited_value() -> None:
    payload = _proposal_payload()
    payload["proposed_formulas"][0]["provenance"] = {"note": "POSSIBLE" + "_" + "ARB"}

    _assert_proposal_invalid(payload)


def test_validated_proposals_contain_no_prohibited_tokens() -> None:
    formulas = load_proposed_market_formulas(PROPOSED_FORMULAS_FIXTURE)
    report = build_formula_diagnostics_report_from_formulas(formulas)
    serialized = json.dumps(report).lower()

    for token in PROHIBITED_TOKENS:
        assert re.search(rf"\b{re.escape(token)}\b", serialized, flags=re.IGNORECASE) is None


def test_kalshi_live_like_adapter_preserves_typed_btc_fields() -> None:
    record = json.loads(KALSHI_LIVE_LIKE_FIXTURE.read_text(encoding="utf-8"))["markets"][0]
    formula = adapt_kalshi_market_formula(record)

    assert formula.market_id == "kalshi:kxbtc-26jun30-t120000"
    assert formula.family == "BTC_THRESHOLD"
    assert formula.asset == "BTC"
    assert formula.source == "fixture_btc_index"
    assert formula.date == "2026-06-30"
    assert formula.settlement_time == "2026-06-30T16:00:00Z"
    assert formula.comparator == ">"
    assert formula.threshold == 120000
    assert formula.units == "USD"
    assert formula.blockers == []
    assert formula.parse_quality >= 0.9
    assert formula.provenance["venue"] == "kalshi"
    assert formula.provenance["source_record_id"] == "KXBTC-26JUN30-T120000"


def test_polymarket_live_like_adapter_preserves_typed_fed_fields() -> None:
    record = json.loads(POLYMARKET_LIVE_LIKE_FIXTURE.read_text(encoding="utf-8"))["markets"][2]
    formula = adapt_polymarket_market_formula(record)

    assert formula.market_id == "polymarket:fed-target-range-425-450-june-fomc"
    assert formula.family == "FED_MEETING_RANGE"
    assert formula.subject == "FED_FUNDS"
    assert formula.source == "federal_reserve_official"
    assert formula.meeting_date == "2026-06-17"
    assert formula.settlement_time == "post-meeting"
    assert formula.lower_bound == 4.25
    assert formula.upper_bound == 4.5
    assert formula.units == "percent"
    assert formula.blockers == []
    assert formula.provenance["venue"] == "polymarket"


def test_live_like_adapter_supports_sports_and_weather_without_comparison_claims() -> None:
    formulas = _live_like_formulas()
    sports = [formula for formula in formulas if formula.family == "SPORTS_CHAMPION"]
    weather = [formula for formula in formulas if formula.family == "WEATHER_RANGE"]
    report = build_formula_diagnostics_report_from_formulas(formulas)

    assert {formula.team for formula in sports} == {"Kansas City", "Boston"}
    assert {formula.location for formula in weather} == {"New York City"}
    assert all("SPORTS_CHAMPION" != item["family"] for item in report["formula_diagnostics"])
    assert all("WEATHER_RANGE" != item["family"] for item in report["formula_diagnostics"])


def test_live_like_missing_source_creates_parse_blocker_not_inferred_exactness() -> None:
    formulas = _live_like_formulas()
    missing = next(formula for formula in formulas if formula.market_id == "kalshi:kxfed-26jun17-missing-source")

    assert missing.family == "FED_MEETING_RANGE"
    assert "missing_source" in missing.blockers
    assert missing.parse_quality < 0.7

    report = build_formula_diagnostics_report_from_formulas(formulas)
    blocked = _diagnostics_by_relation(report, "parse_blocked")

    assert any("kalshi:kxfed-26jun17-missing-source" in item["market_ids"] for item in blocked)
    assert all(item["diagnostic_only"] is True for item in blocked)
    assert all(item["max_action_cap"] in {"WATCH", "MANUAL_REVIEW"} for item in blocked)


def test_live_like_formula_diagnostics_cover_match_mismatch_ladder_overlap_and_blocked() -> None:
    report = build_formula_diagnostics_report_from_formulas(_live_like_formulas())

    matches = _diagnostics_by_relation(report, "typed_formula_match_review_only")
    ambiguous = _diagnostics_by_relation(report, "ambiguous_not_exact")
    ladder = _diagnostics_by_relation(report, "threshold_ladder")
    overlap = _diagnostics_by_relation(report, "overlap_not_identical")
    blocked = _diagnostics_by_relation(report, "parse_blocked")

    assert any(
        {"kalshi:kxbtc-26jun30-t120000", "polymarket:bitcoin-above-120000-june-30-2026"} == set(item["market_ids"])
        for item in matches
    )
    assert any(
        {"kalshi:kxbtc-26jun30-t120000", "polymarket:bitcoin-above-120000-july-31-2026"} == set(item["market_ids"])
        for item in ambiguous
    )
    assert any(
        {"kalshi:kxbtc-26jun30-t140000", "polymarket:bitcoin-above-120000-june-30-2026"} == set(item["market_ids"])
        for item in ladder
    )
    assert any(
        {"kalshi:kxfed-26jun17-425-450", "polymarket:fed-target-range-400-450-june-fomc"} == set(item["market_ids"])
        for item in overlap
    )
    assert any("kalshi:kxfed-26jun17-missing-source" in item["market_ids"] for item in blocked)
    assert report["diagnostic_only"] is True
    assert report["affects_evaluator_gates"] is False
    assert report["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]


def test_live_like_title_similarity_alone_does_not_create_equality() -> None:
    report = build_formula_diagnostics_report_from_formulas(_live_like_formulas())
    ambiguous = _diagnostics_by_relation(report, "ambiguous_not_exact")

    assert any(
        {"kalshi:kxfed-26jun17-425-450", "polymarket:fed-target-range-425-450-july-fomc"} == set(item["market_ids"])
        for item in ambiguous
    )
    assert all(item["formula_relation"] != "trusted_equality" for item in report["formula_diagnostics"])
    assert all(item["affects_evaluator_gates"] is False for item in report["formula_diagnostics"])


def test_formula_watchlist_is_diagnostic_only_and_review_capped(fixture_snapshot) -> None:
    report = build_formula_watchlist_report(fixture_snapshot)

    assert report["diagnostic_only"] is True
    assert report["affects_evaluator_gates"] is False
    assert report["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    assert report["watchlist"]
    assert all(row["diagnostic_only"] is True for row in report["watchlist"])
    assert all(row["affects_evaluator_gates"] is False for row in report["watchlist"])
    assert all(row["max_action_cap"] in {"WATCH", "MANUAL_REVIEW"} for row in report["watchlist"])


def test_exact_looking_formula_watchlist_matches_remain_manual_review(fixture_snapshot) -> None:
    report = build_formula_watchlist_report(fixture_snapshot)
    matches = [
        row
        for row in report["watchlist"]
        if row["watchlist_type"] == "possible_exact_typed_formula_match_review_only"
    ]

    assert matches
    assert all(row["max_action_cap"] == "MANUAL_REVIEW" for row in matches)
    assert all(row["requested_exact_keys_to_verify"] for row in matches)


def test_investigation_requests_are_diagnostic_only_and_include_required_fields(fixture_snapshot) -> None:
    report = build_investigation_requests_report(fixture_snapshot)

    assert report["diagnostic_only"] is True
    assert report["affects_evaluator_gates"] is False
    assert report["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    assert report["investigation_requests"]
    assert any(row["request_type"] == "complex_multi_leg_group" for row in report["investigation_requests"])
    for row in report["investigation_requests"]:
        assert row["diagnostic_only"] is True
        assert row["affects_evaluator_gates"] is False
        assert row["max_action_cap"] in {"WATCH", "MANUAL_REVIEW"}
        assert row["requested_exact_keys_to_verify"]
        assert row["source_market_ids"]
        assert "reason_for_review" in row


def test_investigation_request_contract_rejects_evaluator_or_prohibited_fields(fixture_snapshot) -> None:
    report = build_investigation_requests_report(fixture_snapshot)
    report["investigation_requests"][0]["evaluator_ready"] = True

    try:
        validate_investigation_requests_report(report)
    except SchemaValidationError:
        return
    raise AssertionError("prohibited investigation request field should fail")


def test_formula_watchlist_and_investigation_reports_contain_no_prohibited_tokens(fixture_snapshot) -> None:
    reports = [
        build_formula_watchlist_report(fixture_snapshot),
        build_investigation_requests_report(fixture_snapshot),
    ]
    serialized = json.dumps(reports).lower()

    for token in PROHIBITED_TOKENS:
        assert re.search(rf"\b{re.escape(token)}\b", serialized, flags=re.IGNORECASE) is None


@pytest.mark.parametrize("token", ["trade", "fill", "size", "paper"])
def test_formula_watchlist_rejects_bare_prohibited_values(fixture_snapshot, token: str) -> None:
    report = build_formula_watchlist_report(fixture_snapshot)
    report["watchlist"][0]["reason_for_review"] = token

    try:
        validate_formula_watchlist_report(report)
    except SchemaValidationError:
        return
    raise AssertionError("bare prohibited watchlist value should fail")


@pytest.mark.parametrize("token", ["trade", "fill", "size", "paper"])
def test_investigation_requests_reject_bare_prohibited_values(fixture_snapshot, token: str) -> None:
    report = build_investigation_requests_report(fixture_snapshot)
    report["investigation_requests"][0]["reason_for_review"] = token

    try:
        validate_investigation_requests_report(report)
    except SchemaValidationError:
        return
    raise AssertionError("bare prohibited investigation request value should fail")


def test_formula_watchlist_reports_validate_before_writing(fixture_snapshot, tmp_path) -> None:
    watchlist_json = tmp_path / "market_graph_formula_watchlist.json"
    watchlist_md = tmp_path / "market_graph_formula_watchlist.md"
    requests_json = tmp_path / "rel_value_investigation_requests.json"
    requests_md = tmp_path / "rel_value_investigation_requests.md"

    write_formula_watchlist_reports(fixture_snapshot, watchlist_json, watchlist_md, requests_json, requests_md)

    watchlist = json.loads(watchlist_json.read_text(encoding="utf-8"))
    requests = json.loads(requests_json.read_text(encoding="utf-8"))
    assert watchlist["diagnostic_only"] is True
    assert requests["affects_evaluator_gates"] is False
    combined_markdown = watchlist_md.read_text(encoding="utf-8") + requests_md.read_text(encoding="utf-8")
    for token in PROHIBITED_TOKENS:
        assert re.search(rf"\b{re.escape(token)}\b", combined_markdown, flags=re.IGNORECASE) is None


def test_formula_cluster_synthesizes_btc_threshold_ladder() -> None:
    report = build_formula_cluster_constraints_report(
        [
            _btc_formula("cluster:btc_100k", 100000),
            _btc_formula("cluster:btc_120k", 120000),
            _btc_formula("cluster:btc_140k", 140000),
        ]
    )
    ladders = [item for item in report["formula_cluster_constraints"] if item["constraint_type"] == "derived_threshold_ladder"]

    assert len(ladders) == 1
    assert ladders[0]["diagnostic_only"] is True
    assert ladders[0]["max_action_cap"] == "MANUAL_REVIEW"
    assert ladders[0]["source_market_ids"] == ["cluster:btc_140k", "cluster:btc_120k", "cluster:btc_100k"]
    assert ladders[0]["derived_structure"]["thresholds"] == [140000, 120000, 100000]


def test_formula_cluster_blocks_mixed_threshold_comparators() -> None:
    report = build_formula_cluster_constraints_report(
        [
            _btc_formula("cluster:btc_100k", 100000),
            _btc_formula("cluster:btc_120k", 120000),
            _btc_formula("cluster:btc_le_140k", 140000, comparator="<="),
        ]
    )
    blocked = [
        item
        for item in report["formula_cluster_constraints"]
        if item["constraint_type"] == "blocked_exact_grouping"
        and "mixed_threshold_comparators" in item["blockers"]
    ]
    ladders = [item for item in report["formula_cluster_constraints"] if item["constraint_type"] == "derived_threshold_ladder"]

    assert len(blocked) == 1
    assert ladders == []
    assert blocked[0]["max_action_cap"] == "WATCH"


def test_formula_cluster_blocks_missing_threshold_units() -> None:
    report = build_formula_cluster_constraints_report(
        [
            _btc_formula("cluster:btc_100k", 100000, units=None),
            _btc_formula("cluster:btc_120k", 120000, units="USD"),
            _btc_formula("cluster:btc_140k", 140000, units="USD"),
        ]
    )
    blocked = [
        item
        for item in report["formula_cluster_constraints"]
        if item["constraint_type"] == "blocked_exact_grouping"
        and "mixed_or_missing_threshold_units" in item["blockers"]
    ]
    ladders = [item for item in report["formula_cluster_constraints"] if item["constraint_type"] == "derived_threshold_ladder"]

    assert len(blocked) == 1
    assert ladders == []


def test_formula_cluster_synthesizes_fed_overlap_diagnostics() -> None:
    report = build_formula_cluster_constraints_report(
        [
            _fed_formula("cluster:fed_400_450", 4.0, 4.5),
            _fed_formula("cluster:fed_425_475", 4.25, 4.75),
            _fed_formula("cluster:fed_475_500", 4.75, 5.0),
        ]
    )
    overlaps = [item for item in report["formula_cluster_constraints"] if item["constraint_type"] == "derived_overlapping_ranges"]

    assert overlaps
    assert overlaps[0]["max_action_cap"] == "WATCH"
    assert "range_overlap_not_identical" in overlaps[0]["blockers"]


def test_formula_cluster_synthesizes_range_bucket_partition() -> None:
    report = build_formula_cluster_constraints_report(
        [
            _fed_formula("cluster:fed_400_425", 4.0, 4.25),
            _fed_formula("cluster:fed_425_450", 4.25, 4.5),
            _fed_formula("cluster:fed_450_475", 4.5, 4.75),
        ]
    )
    partitions = [item for item in report["formula_cluster_constraints"] if item["constraint_type"] == "derived_range_bucket_partition"]

    assert len(partitions) == 1
    assert partitions[0]["constraint_family"] == "range_partition"
    assert partitions[0]["diagnostic_only"] is True
    assert partitions[0]["max_action_cap"] == "MANUAL_REVIEW"


def test_formula_cluster_missing_source_or_date_prevents_exact_grouping() -> None:
    report = build_formula_cluster_constraints_report(
        [
            _btc_formula("cluster:btc_missing_source", 100000, source=None),
            _btc_formula("cluster:btc_missing_date", 120000, date=None),
            _btc_formula("cluster:btc_valid", 140000),
        ]
    )
    blocked = [item for item in report["formula_cluster_constraints"] if item["constraint_type"] == "blocked_exact_grouping"]
    ladders = [item for item in report["formula_cluster_constraints"] if item["constraint_type"] == "derived_threshold_ladder"]

    assert len(blocked) == 2
    assert any("missing_source" in item["blockers"] for item in blocked)
    assert any("missing_date" in item["blockers"] for item in blocked)
    assert ladders == []


def test_formula_cluster_constraints_have_no_prohibited_language() -> None:
    report = build_formula_cluster_constraints_report(
        [
            _btc_formula("cluster:btc_100k", 100000),
            _btc_formula("cluster:btc_120k", 120000),
            _btc_formula("cluster:btc_140k", 140000),
            _fed_formula("cluster:fed_400_450", 4.0, 4.5),
            _fed_formula("cluster:fed_425_475", 4.25, 4.75),
        ]
    )
    serialized = json.dumps(report).lower()

    assert report["diagnostic_only"] is True
    assert report["affects_evaluator_gates"] is False
    assert report["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    for token in PROHIBITED_TOKENS:
        assert re.search(rf"\b{re.escape(token)}\b", serialized, flags=re.IGNORECASE) is None
