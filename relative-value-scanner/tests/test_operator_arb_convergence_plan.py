from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from relative_value.operator_arb_convergence_plan import (
    PLAN_EXIT_NOW,
    PLAN_EXIT_TARGET_MET,
    PLAN_ENTER_MONITOR,
    PLAN_HOLD,
    PLAN_IGNORE_LOW_RETURN,
    PLAN_IGNORE_SIZE,
    PLAN_MANUAL,
    SCHEMA_KIND,
    build_operator_arb_convergence_plan,
    write_operator_arb_convergence_plan_files,
)


NOW = datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc)


def test_positive_net_low_annualized_enters_monitor_not_hold(tmp_path: Path) -> None:
    report_path = _write_source_report(tmp_path, rows=[_source_row(net_edge=0.006018, gross_edge=0.016)])

    report = _build(report_path)
    row = report["rows"][0]

    assert row["recommended_plan"] == PLAN_ENTER_MONITOR
    assert row["recommended_plan"] != PLAN_HOLD
    assert row["annualized_return_estimate"] < 0.10
    assert "edge_too_small_for_long_hold" in row["blockers"]


def test_high_net_edge_and_acceptable_annualized_return_can_hold(tmp_path: Path) -> None:
    report_path = _write_source_report(tmp_path, rows=[_source_row(kalshi_ask=0.7, polymarket_ask=0.25, gross_edge=0.05, net_edge=0.04)])

    report = _build(report_path, settlement_date="2026-06-30")
    row = report["rows"][0]

    assert row["recommended_plan"] == PLAN_HOLD
    assert row["net_hold_edge"] == 0.04
    assert row["annualized_return_estimate"] >= 0.10


def test_negative_net_edge_ignores_low_return(tmp_path: Path) -> None:
    report_path = _write_source_report(tmp_path, rows=[_source_row(net_edge=-0.001, gross_edge=0.004)])

    report = _build(report_path)

    assert report["rows"][0]["recommended_plan"] == PLAN_IGNORE_LOW_RETURN


def test_missing_fees_requires_manual_review(tmp_path: Path) -> None:
    report_path = _write_source_report(tmp_path, rows=[_source_row(fee=None, net_edge=0.01)])

    report = _build(report_path)

    assert report["rows"][0]["recommended_plan"] == PLAN_MANUAL
    assert "missing_fee_model" in report["rows"][0]["blockers"]


def test_missing_size_or_depth_blocks(tmp_path: Path) -> None:
    report_path = _write_source_report(tmp_path, rows=[_source_row(available_notional=None, blockers=["missing_quote_depth"])])

    report = _build(report_path)
    row = report["rows"][0]

    assert row["recommended_plan"] in {PLAN_MANUAL, PLAN_IGNORE_SIZE}
    assert "missing_available_notional" in row["blockers"]


def test_available_notional_estimates_total_net_profit(tmp_path: Path) -> None:
    report_path = _write_source_report(tmp_path, rows=[_source_row(net_edge=0.01, available_notional=25)])

    report = _build(report_path)

    assert report["rows"][0]["estimated_total_net_profit_at_size"] == 0.25


def test_target_exit_fields_are_computed(tmp_path: Path) -> None:
    report_path = _write_source_report(tmp_path, rows=[_source_row(kalshi_ask=0.893, polymarket_ask=0.091, fee=0.009982)])

    report = _build(report_path, target_exit_edge=0.015)
    row = report["rows"][0]

    assert row["entry_cost"] == 0.984
    assert row["target_exit_pair_value"] == 0.999
    assert row["target_exit_profit_per_unit"] == 0.005018
    assert row["target_exit_return_on_capital"] == 0.00509959


def test_missing_exit_bid_data_is_recorded_not_guessed(tmp_path: Path) -> None:
    report_path = _write_source_report(tmp_path, rows=[_source_row()])

    report = _build(report_path)
    row = report["rows"][0]

    assert row["current_exit_value_if_available"] is None
    assert row["exit_bid_data_status"] == "exit_bid_data_missing"
    assert "exit_bid_data_missing" in row["blockers"]


def test_convergence_plan_consumes_current_exit_pair_value(tmp_path: Path) -> None:
    report_path = _write_source_report(tmp_path, rows=[_source_row(current_exit_pair_value=0.98)])

    report = _build(report_path)
    row = report["rows"][0]

    assert row["current_exit_value_if_available"] == 0.98
    assert row["exit_bid_data_status"] == "available"
    assert "exit_bid_data_missing" not in row["blockers"]


def test_target_exit_distance_computed_when_exit_value_available(tmp_path: Path) -> None:
    report_path = _write_source_report(tmp_path, rows=[_source_row(current_exit_pair_value=0.98)])

    report = _build(report_path, target_exit_edge=0.015)
    row = report["rows"][0]

    assert row["target_exit_pair_value"] == 0.999
    assert row["target_exit_distance"] == 0.019
    assert row["target_exit_distance_status"] == "available"


def test_target_exit_pair_value_is_capped_at_settlement_payoff(tmp_path: Path) -> None:
    report_path = _write_source_report(tmp_path, rows=[_source_row(current_exit_pair_value=0.98)])

    report = _build(report_path, target_exit_edge=0.05)
    row = report["rows"][0]

    assert row["uncapped_target_exit_pair_value"] == 1.034
    assert row["target_exit_pair_value"] == 1.0
    assert row["target_exit_capped_by_settlement_payoff"] is True
    assert row["target_exit_distance"] == 0.02
    assert "target exit capped at normal settlement payoff" in row["notes"]
    assert "target_exit_pair_value_above_normal_payoff" not in row["blockers"]


def test_paired_basket_carry_fields_are_present(tmp_path: Path) -> None:
    report_path = _write_source_report(tmp_path, rows=[_source_row()])

    report = _build(report_path)
    row = report["rows"][0]

    assert row["paired_basket_entry_cost"] == row["entry_cost"]
    assert row["paired_basket_settlement_payoff"] == 1.0
    assert row["settlement_carry_net_edge"] == row["net_hold_edge"]
    assert row["settlement_carry_return_on_capital"] == row["return_on_capital"]
    assert row["convergence_exit_plan"] == row["recommended_plan"]


def test_positive_immediate_unwind_after_fees_can_exit_now_review(tmp_path: Path) -> None:
    report_path = _write_source_report(
        tmp_path,
        rows=[
            _source_row(
                current_exit_pair_value=1.02,
                immediate_unwind_before_fees=0.036,
                immediate_unwind_after_estimated_fees=0.02,
            )
        ],
    )

    report = _build(report_path)

    assert report["rows"][0]["recommended_plan"] == PLAN_EXIT_NOW
    assert report["summary_counts"]["exit_now_review_rows"] == 1


def test_target_exit_already_met_can_be_reviewed(tmp_path: Path) -> None:
    report_path = _write_source_report(
        tmp_path,
        rows=[
            _source_row(
                current_exit_pair_value=1.0,
                immediate_unwind_before_fees=0.016,
                immediate_unwind_after_estimated_fees=-0.001,
            )
        ],
    )

    report = _build(report_path, target_exit_edge=0.01)

    assert report["rows"][0]["recommended_plan"] == PLAN_EXIT_TARGET_MET
    assert report["summary_counts"]["exit_target_already_met_rows"] == 1


def test_markdown_explains_paired_basket_carry_not_directional_hold(tmp_path: Path) -> None:
    report_path = _write_source_report(tmp_path, rows=[_source_row()])
    json_output = tmp_path / "plan.json"
    markdown_output = tmp_path / "plan.md"

    write_operator_arb_convergence_plan_files(
        input_report=report_path,
        json_output=json_output,
        markdown_output=markdown_output,
        target_exit_edge=0.015,
        min_hold_net_edge=0.02,
        min_annualized_return=0.10,
        settlement_date="2026-10-31",
        max_capital_tieup_days=45,
        generated_at=NOW,
    )

    markdown = markdown_output.read_text(encoding="utf-8")
    assert "Carry-to-settlement means carrying both hedged legs" in markdown
    assert "not a directional single-leg hold" in markdown


def test_exit_gross_positive_but_exit_fee_unavailable_requires_manual_review(tmp_path: Path) -> None:
    report_path = _write_source_report(
        tmp_path,
        rows=[
            _source_row(
                current_exit_pair_value=1.0,
                immediate_unwind_before_fees=0.016,
                immediate_unwind_after_estimated_fees=None,
                exit_fee_status="FEE_REVIEW_REQUIRED",
            )
        ],
    )

    report = _build(report_path)
    row = report["rows"][0]

    assert row["recommended_plan"] == PLAN_MANUAL
    assert "exit_fee_review_required" in row["blockers"]


def test_no_standard_paper_candidate_and_no_exact_ready(tmp_path: Path) -> None:
    report_path = _write_source_report(tmp_path, rows=[_source_row()])

    report = _build(report_path)
    encoded = json.dumps(report)

    assert report["schema_kind"] == SCHEMA_KIND
    assert report["standard_paper_candidate_emitted"] is False
    assert report["exact_ready_rows"] == 0
    assert all(row["standard_paper_candidate"] is False and row["exact_ready"] is False for row in report["rows"])
    assert ("PAPER" + "_CANDIDATE") not in encoded


def test_adapter_loads_simplified_world_series_fixture(tmp_path: Path) -> None:
    report_path = _write_source_report(tmp_path, rows=[_source_row(team="Atlanta Braves", row_id="ATL:POLYMARKET_YES_KALSHI_NO")])

    report = _build(report_path)
    row = report["rows"][0]

    assert row["market_family"] == "sports_mlb_world_series_championship_futures"
    assert row["event_key"] == "MLB_WORLD_SERIES_2026"
    assert row["team_or_outcome"] == "Atlanta Braves"
    assert row["source_row_id"] == "ATL:POLYMARKET_YES_KALSHI_NO"


def test_adapter_fails_closed_on_unknown_report_schema(tmp_path: Path) -> None:
    report_path = tmp_path / "unknown.json"
    report_path.write_text(json.dumps({"schema_kind": "unknown_schema_v1", "rows": [_source_row()]}), encoding="utf-8")

    report = _build(report_path)
    row = report["rows"][0]

    assert row["recommended_plan"] == PLAN_MANUAL
    assert row["blockers"] == ["unsupported_operator_arb_report_schema"]
    assert report["summary_counts"]["manual_review_rows"] == 1


def test_writer_outputs_json_and_markdown(tmp_path: Path) -> None:
    report_path = _write_source_report(tmp_path, rows=[_source_row()])
    json_output = tmp_path / "plan.json"
    markdown_output = tmp_path / "plan.md"

    report = write_operator_arb_convergence_plan_files(
        input_report=report_path,
        json_output=json_output,
        markdown_output=markdown_output,
        target_exit_edge=0.015,
        min_hold_net_edge=0.02,
        min_annualized_return=0.10,
        settlement_date="2026-10-31",
        max_capital_tieup_days=45,
        generated_at=NOW,
    )

    assert json_output.exists()
    assert markdown_output.exists()
    assert "Operator Arb Convergence / Exit Plan" in markdown_output.read_text(encoding="utf-8")
    assert report["diagnostic_only"] is True


def _build(
    report_path: Path,
    *,
    target_exit_edge: float = 0.015,
    settlement_date: str = "2026-10-31",
) -> dict:
    return build_operator_arb_convergence_plan(
        input_report=report_path,
        target_exit_edge=target_exit_edge,
        min_hold_net_edge=0.02,
        min_annualized_return=0.10,
        settlement_date=settlement_date,
        max_capital_tieup_days=45,
        generated_at=NOW,
    )


def _write_source_report(tmp_path: Path, *, rows: list[dict]) -> Path:
    path = tmp_path / "operator_scout.json"
    payload = {
        "schema_kind": "sports_mlb_world_series_operator_arb_scout_v1",
        "season": "2026",
        "diagnostic_only": True,
        "operator_arb_mode": True,
        "rows": rows,
        "standard_paper_candidate_rows": 0,
        "exact_ready_rows": 0,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _source_row(
    *,
    row_id: str = "ATL:POLYMARKET_YES_KALSHI_NO",
    team: str = "Atlanta Braves",
    action: str = "OPERATOR_ARB_PAPER_REVIEW",
    kalshi_ask: float = 0.893,
    polymarket_ask: float = 0.091,
    gross_edge: float = 0.016,
    fee: float | None = 0.009982,
    net_edge: float | None = 0.006018,
    available_notional: float | None = 18.291,
    blockers: list[str] | None = None,
    current_exit_pair_value: float | None = None,
    current_exit_pair_value_status: str = "available",
    immediate_unwind_before_fees: float | None = None,
    immediate_unwind_after_estimated_fees: float | None = None,
    exit_fee_status: str = "OK",
) -> dict:
    row = {
        "row_id": row_id,
        "team_name": team,
        "action": action,
        "direction": "POLYMARKET_YES_KALSHI_NO",
        "kalshi_ask": kalshi_ask,
        "polymarket_ask": polymarket_ask,
        "gross_edge": gross_edge,
        "conservative_fee_estimate": fee,
        "net_edge": net_edge,
        "available_notional": available_notional,
        "kalshi_leg": {"platform": "Kalshi", "side": "NO", "market_ticker": "KXMLB-26-ATL"},
        "polymarket_leg": {
            "platform": "Polymarket",
            "side": "YES",
            "market_id": "1235562",
            "condition_id": "0xabc",
            "yes_token_id": "yes-atl",
            "no_token_id": "no-atl",
        },
        "blockers": blockers
        if blockers is not None
        else ["proportional_payout_vs_other_outcome_mismatch", "remote_tail_risk_human_accepted_but_not_exact"],
        "exact_ready": False,
        "standard_paper_candidate": False,
    }
    if current_exit_pair_value is not None:
        row["current_exit_pair_value"] = current_exit_pair_value
        row["current_exit_pair_value_status"] = current_exit_pair_value_status
        row["exit_bid_legs"] = [
            {"platform": "Kalshi", "side": "NO", "bid": 0.90, "size": 100, "notional_estimate": 90},
            {"platform": "Polymarket", "side": "YES", "bid": 0.10, "size": 100, "notional_estimate": 10},
        ]
        row["exit_fee_status"] = exit_fee_status
    if immediate_unwind_before_fees is not None:
        row["immediate_unwind_pnl_before_fees"] = immediate_unwind_before_fees
    if immediate_unwind_after_estimated_fees is not None:
        row["immediate_unwind_pnl_after_estimated_fees"] = immediate_unwind_after_estimated_fees
    return row
