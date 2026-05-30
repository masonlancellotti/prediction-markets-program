"""Watcher tests for crypto-structural-arb (mocked scout; no network)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import scan
import relative_value.watch_crypto_structural_arb as watch_mod
from relative_value.watch_crypto_structural_arb import run_watch


START = datetime(2026, 5, 30, 5, 0, 0, tzinfo=timezone.utc)


def _incrementing_clock(step_seconds: int = 30):
    state = {"t": START}

    def clock() -> datetime:
        value = state["t"]
        state["t"] = value + timedelta(seconds=step_seconds)
        return value

    return clock


def _report(*, paper: bool, net: float | None, blockers: list[str]) -> dict[str, Any]:
    rows = [
        {
            "asset": "BTC", "candidate_type": "THRESHOLD_MONOTONICITY_COVER",
            "paper_candidate": paper, "paper_candidate_class": "STRICT_EXACT" if paper else "NONE",
            "net_edge_after_fees": net, "adjusted_net_edge_after_fees": net,
            "total_cost_after_fees": (1.0 - net) if net is not None else None,
            "lower_strike": 74600.0, "higher_strike": 74800.0, "target_instant_utc": "2026-05-30T06:00:00+00:00",
            "hard_blockers": [] if paper else blockers,
        }
    ]
    return {
        "summary_counts": {"paper_candidate_rows": 1 if paper else 0},
        "candidate_type_counts": {"THRESHOLD_MONOTONICITY_COVER": 1},
        "monotonicity_cover_diagnostics": {
            "monotonicity_cover_candidates_generated": 1,
            "monotonicity_cover_paper_candidates": 1 if paper else 0,
            "missing_no_higher_ask": 0 if paper else 1,
            "missing_yes_lower_ask": 0,
            "complement_quote_used": 0,
            "monotonicity_pairs_checked": 1,
        },
        "top_blockers": [] if paper else [{"blocker": b, "count": 1} for b in blockers],
        "rows": rows,
        "state_grids": [{"target_instant_utc": "2026-05-30T06:00:00+00:00"}],
        "safety": {},
    }


def _make_builder(scripts: list[dict[str, Any]]):
    calls = {"n": 0, "kwargs": []}

    def builder(**kwargs):
        calls["kwargs"].append(kwargs)
        idx = min(calls["n"], len(scripts) - 1)
        calls["n"] += 1
        return scripts[idx]

    return builder, calls


def _run(tmp_path: Path, scripts: list[dict[str, Any]], **overrides):
    builder, calls = _make_builder(scripts)
    params: dict[str, Any] = dict(
        assets=["BTC", "ETH"], interval_seconds=30, iterations=len(scripts),
        operator_risk_mode="aggressive", allow_top_of_book_depth=True, operator_size_cap=10.0,
        output_dir=tmp_path / "watch", report_builder=builder, report_renderer=lambda r: "# iter\n",
        sleep=lambda _s: None, clock=_incrementing_clock(), console=lambda _m: None,
    )
    params.update(overrides)
    summary = run_watch(**params)
    return summary, calls


def test_watcher_runs_with_mocked_scout(tmp_path: Path) -> None:
    summary, calls = _run(tmp_path, [_report(paper=False, net=None, blockers=["missing_ask"])] * 3)
    assert summary["iterations_completed"] == 3
    assert calls["n"] == 3
    # Each iteration requested a live refresh and never fetched CDNA over the network.
    assert all(k.get("refresh_kalshi_polymarket") is True for k in calls["kwargs"])


def test_summary_aggregates_paper_candidate_counts(tmp_path: Path) -> None:
    scripts = [
        _report(paper=False, net=None, blockers=["missing_ask"]),
        _report(paper=True, net=0.04, blockers=[]),
        _report(paper=True, net=0.02, blockers=[]),
    ]
    summary, _ = _run(tmp_path, scripts)
    assert summary["totals"]["paper_candidates_found"] == 2
    assert summary["iterations"][1]["paper_candidates"] == 1


def test_summary_tracks_best_net_edge(tmp_path: Path) -> None:
    scripts = [
        _report(paper=True, net=0.01, blockers=[]),
        _report(paper=True, net=0.05, blockers=[]),
        _report(paper=True, net=0.03, blockers=[]),
    ]
    summary, _ = _run(tmp_path, scripts)
    assert summary["totals"]["best_net_edge_after_fees"] == 0.05
    assert summary["totals"]["best_adjusted_net_edge_after_fees"] == 0.05
    assert summary["best_post_fee_rows"][0]["net_edge_after_fees"] == 0.05


def test_summary_aggregates_top_blockers(tmp_path: Path) -> None:
    scripts = [
        _report(paper=False, net=None, blockers=["missing_ask"]),
        _report(paper=False, net=None, blockers=["missing_ask"]),
        _report(paper=False, net=None, blockers=["stale_or_missing_quote"]),
    ]
    summary, _ = _run(tmp_path, scripts)
    top = {b["blocker"]: b["count"] for b in summary["totals"]["top_hard_blockers"]}
    assert top.get("missing_ask") == 2
    assert top.get("stale_or_missing_quote") == 1


def test_no_candidates_handled_cleanly(tmp_path: Path) -> None:
    empty = {
        "summary_counts": {"paper_candidate_rows": 0}, "candidate_type_counts": {},
        "monotonicity_cover_diagnostics": {}, "top_blockers": [], "rows": [], "state_grids": [], "safety": {},
    }
    summary, _ = _run(tmp_path, [empty, empty])
    assert summary["iterations_completed"] == 2
    assert summary["totals"]["paper_candidates_found"] == 0
    assert summary["totals"]["best_net_edge_after_fees"] is None
    assert summary["best_post_fee_rows"] == []


def test_output_dirs_and_files_written(tmp_path: Path) -> None:
    scripts = [_report(paper=True, net=0.04, blockers=[]), _report(paper=False, net=None, blockers=["missing_ask"])]
    summary, _ = _run(tmp_path, scripts)
    out = tmp_path / "watch"
    assert (out / "watch_summary.json").exists()
    assert (out / "watch_summary.md").exists()
    iter_dirs = sorted(p for p in out.iterdir() if p.is_dir())
    assert len(iter_dirs) == 2
    for d in iter_dirs:
        assert (d / "iteration.json").exists()
        assert (d / "iteration.md").exists()
    payload = json.loads((out / "watch_summary.json").read_text(encoding="utf-8"))
    assert payload["schema_kind"] == "crypto_structural_watch_summary_v1"
    md = (out / "watch_summary.md").read_text(encoding="utf-8")
    assert "# Crypto Structural Arb Watch Summary" in md


def test_scan_cli_runs_watch_with_monkeypatched_builder(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        watch_mod, "build_crypto_structural_payoff_arb_scout_report",
        lambda **kwargs: _report(paper=True, net=0.04, blockers=[]),
    )
    rc = scan.main(
        [
            "watch-crypto-structural-arb",
            "--assets", "BTC",
            "--iterations", "1",
            "--interval-seconds", "30",
            "--operator-risk-mode", "aggressive",
            "--output-dir", str(tmp_path / "w"),
        ]
    )
    assert rc == 0
    assert (tmp_path / "w" / "watch_summary.json").exists()


def _report_focus(*, paper_net: float | None, near: list[dict[str, Any]], diag_short: int, mono_one_leg: int,
                  actionable: list[dict[str, Any]]) -> dict[str, Any]:
    """A scout-shaped report carrying the buy-only/short-required focus fields."""
    base = _report(paper=paper_net is not None, net=paper_net, blockers=[])
    base["summary_counts"].update({
        "buy_only_rows": len(near),
        "diagnostic_only_short_required_rows": diag_short,
    })
    base["top_blockers"] = actionable  # scout top_blockers is actionable-only
    base["diagnostic_only_short_required_rows"] = diag_short
    base["monotonicity_covers_one_leg_missing"] = mono_one_leg
    base["top_buy_only_near_misses"] = near
    base["actionable_buy_only_blockers"] = {b["blocker"]: b["count"] for b in actionable}
    return base


def test_watcher_tracks_diagnostic_short_separately(tmp_path: Path) -> None:
    nm = [{"asset": "BTC", "candidate_type": "THRESHOLD_MONOTONICITY_COVER", "paper_candidate": False,
           "net_edge_after_fees": -0.02, "adjusted_net_edge_after_fees": -0.02,
           "lower_strike": 74600.0, "higher_strike": 74800.0, "target_instant_utc": "2026-05-30T06:00:00+00:00",
           "hard_blockers": ["no_positive_net_edge_after_fees"]}]
    actionable = [{"blocker": "missing_no_higher_ask", "count": 3}, {"blocker": "missing_partner_no_ask", "count": 2}]
    scripts = [
        _report_focus(paper_net=None, near=nm, diag_short=110, mono_one_leg=4, actionable=actionable),
        _report_focus(paper_net=None, near=nm, diag_short=90, mono_one_leg=2, actionable=actionable),
    ]
    summary, _ = _run(tmp_path, scripts)
    t = summary["totals"]
    # Short-required diagnostics are tracked, but NEVER mixed into actionable blockers.
    assert t["diagnostic_only_short_required_rows"] == 200
    assert t["monotonicity_covers_one_leg_missing"] == 6
    top = {b["blocker"] for b in t["top_actionable_buy_only_blockers"]}
    assert "requires_short_or_not_guaranteed" not in top
    assert "missing_no_higher_ask" in top
    # Missing-ask blockers for buy-only rows are surfaced on their own.
    assert t["missing_ask_blockers_buy_only"].get("missing_no_higher_ask") == 6


def test_watcher_collects_buy_only_near_misses(tmp_path: Path) -> None:
    nm_lo = [{"asset": "BTC", "candidate_type": "THRESHOLD_MONOTONICITY_COVER", "paper_candidate": False,
              "net_edge_after_fees": -0.05, "adjusted_net_edge_after_fees": -0.05,
              "lower_strike": 70000.0, "higher_strike": 71000.0, "target_instant_utc": "i", "hard_blockers": []}]
    nm_hi = [{"asset": "ETH", "candidate_type": "CROSS_VENUE_THRESHOLD_BASIS", "paper_candidate": False,
              "net_edge_after_fees": -0.01, "adjusted_net_edge_after_fees": -0.01,
              "lower_strike": 3000.0, "higher_strike": 3100.0, "target_instant_utc": "i", "hard_blockers": []}]
    scripts = [
        _report_focus(paper_net=None, near=nm_lo, diag_short=1, mono_one_leg=0, actionable=[]),
        _report_focus(paper_net=None, near=nm_hi, diag_short=1, mono_one_leg=0, actionable=[]),
    ]
    summary, _ = _run(tmp_path, scripts)
    near = summary["top_buy_only_near_misses"]
    assert [n["net_edge_after_fees"] for n in near] == [-0.01, -0.05]  # sorted desc
    md = (tmp_path / "watch" / "watch_summary.md").read_text(encoding="utf-8")
    assert "## Top buy-only near-misses" in md
    assert "diagnostic_only_short_required_rows" in md
    assert "## Diagnostic-only: requires shorting" in md


def test_burst_mode_chooses_fast_interval_near_boundary() -> None:
    top_of_hour = datetime(2026, 5, 30, 6, 0, 0, tzinfo=timezone.utc)
    off = datetime(2026, 5, 30, 6, 7, 30, tzinfo=timezone.utc)  # 2.5 min from :05, > 90s window
    assert watch_mod._is_near_boundary(top_of_hour, 90) is True
    assert watch_mod._is_near_boundary(off, 90) is False
    fast, mode = watch_mod._choose_interval(
        now=top_of_hour, burst_mode=True, burst_interval=5, normal_interval=30, boundary_window=90
    )
    assert (fast, mode) == (5.0, "burst")
    slow, mode2 = watch_mod._choose_interval(
        now=off, burst_mode=True, burst_interval=5, normal_interval=30, boundary_window=90
    )
    assert (slow, mode2) == (30.0, "normal")
    # Burst off -> always normal, regardless of boundary proximity.
    no_burst, mode3 = watch_mod._choose_interval(
        now=top_of_hour, burst_mode=False, burst_interval=5, normal_interval=30, boundary_window=90
    )
    assert (no_burst, mode3) == (30.0, "normal")


def _report_rich() -> dict[str, Any]:
    return {
        "summary_counts": {
            "paper_candidate_rows": 1, "buy_only_rows": 5, "diagnostic_only_short_required_rows": 110,
            "manual_micro_test_candidate_rows": 1, "complement_quote_rows": 2,
        },
        "candidate_type_counts": {"CROSS_VENUE_THRESHOLD_BASIS": 1},
        "monotonicity_cover_diagnostics": {"monotonicity_cover_candidates_generated": 1, "complement_quote_used": 2},
        "monotonicity_covers_one_leg_missing": 1,
        "diagnostic_only_short_required_rows": 110,
        "manual_micro_test_candidate_rows": 1, "complement_quote_rows": 2,
        "top_blockers": [{"blocker": "missing_polymarket_no_ask", "count": 3}],
        "economic_rejections": {"no_positive_net_edge_after_fees": 4},
        "quote_side_diagnostic_counts": {"missing_polymarket_no_ask": 3, "complement_quote_used:no_ask = 1 - yes_bid": 2},
        "cdna_fill_first_candidates": [{
            "asset": "BTC", "candidate_type": "CDNA_FILL_FIRST",
            "target_instant_utc": "2026-05-30T07:00:00+00:00", "adjusted_net_edge_after_fees": 0.03,
            "candidate_action": "FILL_CDNA_FIRST_THEN_HEDGE_EXACT_FILLED_QUANTITY",
        }],
        "top_buy_only_near_misses": [{
            "asset": "BTC", "candidate_type": "THRESHOLD_MONOTONICITY_COVER", "paper_candidate": False,
            "near_miss": True, "near_miss_reason": "needs NO ask on higher threshold",
            "missing_to_candidate": ["NO ask on higher threshold"], "net_edge_after_fees": -0.01,
            "adjusted_net_edge_after_fees": -0.01, "lower_strike": 70000.0, "higher_strike": 71000.0,
            "target_instant_utc": "i", "hard_blockers": ["missing_no_higher_ask"],
        }],
        "rows": [{
            "asset": "BTC", "candidate_type": "CROSS_VENUE_THRESHOLD_BASIS", "paper_candidate": True,
            "paper_candidate_class": "OPERATOR_ACCEPTED_RISK", "net_edge_after_fees": 0.16,
            "adjusted_net_edge_after_fees": 0.15, "target_instant_utc": "2026-05-30T07:00:00+00:00",
            "hard_blockers": [],
        }],
        "state_grids": [{"target_instant_utc": "2026-05-30T07:00:00+00:00"}],
        "load_diagnostics": {"source": "live_refresh", "per_asset_diagnostics": [
            {"asset": "BTC", "polymarket_diagnostics": {"warnings": ["polymarket_events_fetch_failed:timeout"], "clob_fetch_failures": 2},
             "kalshi_diagnostics": {}, "cdna_diagnostics": {}}
        ]},
        "safety": {},
    }


def _watch_quality_report(
    *,
    paper: bool = False,
    net: float | None = None,
    adjusted: float | None = None,
    hard_blockers: list[str] | None = None,
    quote_side_diagnostics: list[str] | None = None,
    legs: list[dict[str, Any]] | None = None,
    economic: dict[str, int] | None = None,
    top_blockers: list[dict[str, Any]] | None = None,
    load_diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    blockers = list(hard_blockers or [])
    row = {
        "asset": "BTC",
        "candidate_type": "CROSS_VENUE_THRESHOLD_BASIS",
        "paper_candidate": paper,
        "paper_candidate_class": "STRICT_EXACT" if paper else "NONE",
        "tradable_buy_only": True,
        "candidate_execution_type": "BUY_ONLY",
        "net_edge_after_fees": net,
        "adjusted_net_edge_after_fees": adjusted if adjusted is not None else net,
        "total_cost_after_fees": (1.0 - net) if net is not None else None,
        "target_instant_utc": "2026-05-30T06:00:00+00:00",
        "hard_blockers": blockers,
        "quote_side_diagnostics": list(quote_side_diagnostics or []),
        "basket_legs": list(legs or []),
    }
    return {
        "summary_counts": {
            "rows": 1,
            "buy_only_rows": 1,
            "paper_candidate_rows": 1 if paper else 0,
            "manual_micro_test_candidate_rows": 1 if paper else 0,
        },
        "candidate_type_counts": {"CROSS_VENUE_THRESHOLD_BASIS": 1},
        "monotonicity_cover_diagnostics": {},
        "top_blockers": top_blockers if top_blockers is not None else [{"blocker": b, "count": 1} for b in blockers],
        "economic_rejections": dict(economic or {}),
        "quote_side_diagnostic_counts": {label: 1 for label in quote_side_diagnostics or []},
        "top_buy_only_near_misses": [] if paper else [{
            "asset": "BTC",
            "candidate_type": "CROSS_VENUE_THRESHOLD_BASIS",
            "paper_candidate": False,
            "near_miss": True,
            "near_miss_reason": "test near miss",
            "missing_to_candidate": [],
            "net_edge_after_fees": net,
            "adjusted_net_edge_after_fees": adjusted if adjusted is not None else net,
            "target_instant_utc": "2026-05-30T06:00:00+00:00",
            "hard_blockers": blockers,
        }],
        "rows": [row],
        "state_grids": [{"target_instant_utc": "2026-05-30T06:00:00+00:00"}],
        "load_diagnostics": load_diagnostics or {},
        "safety": {},
    }


def test_run_quality_poor_quote_coverage_classified(tmp_path: Path) -> None:
    scripts = [_watch_quality_report(
        net=None,
        hard_blockers=["missing_partner_no_ask"],
        quote_side_diagnostics=["missing_polymarket_no_ask"],
        legs=[
            {"platform": "kalshi", "side": "YES", "ask": 0.42},
            {"platform": "polymarket", "side": "NO", "ask": None},
        ],
        top_blockers=[{"blocker": "missing_partner_no_ask", "count": 1}],
    )]
    summary, _ = _run(tmp_path, scripts)
    assert summary["run_quality_label"] == "POOR_QUOTE_COVERAGE"
    coverage = summary["totals"]["quote_coverage_by_venue_side"]
    assert coverage["kalshi_yes_ask_present"] == 1
    assert coverage["polymarket_no_ask_present"] == 0
    funnel = summary["totals"]["candidate_readiness_funnel"]
    assert funnel["rows_with_all_required_asks"] == 0


def test_run_quality_quotes_ok_no_edge_classified(tmp_path: Path) -> None:
    scripts = [_watch_quality_report(
        net=-0.05,
        hard_blockers=["no_positive_net_edge_after_fees"],
        legs=[
            {"platform": "kalshi", "side": "YES", "ask": 0.65},
            {"platform": "polymarket", "side": "NO", "ask": 0.40},
        ],
        economic={"no_positive_net_edge_after_fees": 1},
    )]
    summary, _ = _run(tmp_path, scripts)
    assert summary["run_quality_label"] == "QUOTES_OK_NO_EDGE"
    funnel = summary["totals"]["candidate_readiness_funnel"]
    assert funnel["rows_with_all_required_asks"] == 1
    assert funnel["rows_fresh"] == 1
    assert funnel["rows_net_positive_before_buffer"] == 0
    near = summary["totals"]["near_miss_distance"]
    assert near["best_net_edge_after_fees"] == -0.05
    assert near["cents_needed_to_break_even"] == 5.0
    assert near["blocker_preventing_top_near_miss"] == "no_positive_net_edge_after_fees"


def test_run_quality_candidates_found_classified(tmp_path: Path) -> None:
    scripts = [_watch_quality_report(
        paper=True,
        net=0.03,
        legs=[
            {"platform": "kalshi", "side": "YES", "ask": 0.40},
            {"platform": "polymarket", "side": "NO", "ask": 0.55},
        ],
    )]
    summary, _ = _run(tmp_path, scripts)
    assert summary["run_quality_label"] == "CANDIDATES_FOUND"
    funnel = summary["totals"]["candidate_readiness_funnel"]
    assert funnel["rows_net_positive_before_buffer"] == 1
    assert funnel["rows_net_positive_after_buffer"] == 1
    assert funnel["paper_candidates"] == 1
    assert summary["totals"]["near_miss_distance"]["cents_needed_to_break_even"] == 0.0


def test_best_net_uses_highest_priced_buy_only_not_worst_or_short_required(tmp_path: Path) -> None:
    report = _watch_quality_report(
        net=-0.01,
        hard_blockers=["no_positive_net_edge_after_fees"],
        legs=[{"platform": "kalshi", "side": "YES", "ask": 0.51}],
        economic={"no_positive_net_edge_after_fees": 1},
    )
    report["rows"].append({
        "asset": "BTC",
        "candidate_type": "THRESHOLD_TO_BUCKET_DIAGNOSTIC",
        "tradable_buy_only": False,
        "requires_short_or_sell": True,
        "paper_candidate": False,
        "net_edge_after_fees": 0.25,
        "adjusted_net_edge_after_fees": 0.25,
        "hard_blockers": ["requires_short_or_not_guaranteed"],
        "quote_side_diagnostics": [],
        "basket_legs": [],
    })
    report["rows"].append({
        "asset": "BTC",
        "candidate_type": "LONG_ONLY_GUARANTEED_PAYOFF",
        "tradable_buy_only": True,
        "paper_candidate": False,
        "net_edge_after_fees": -0.99,
        "adjusted_net_edge_after_fees": -0.99,
        "hard_blockers": ["no_positive_net_edge_after_fees"],
        "quote_side_diagnostics": [],
        "basket_legs": [{"platform": "polymarket", "side": "NO", "ask": 0.99}],
    })
    report["summary_counts"]["rows"] = 3
    report["summary_counts"]["buy_only_rows"] = 2
    report["top_buy_only_near_misses"] = [
        {"asset": "BTC", "candidate_type": "LONG_ONLY_GUARANTEED_PAYOFF", "net_edge_after_fees": -0.99, "hard_blockers": ["no_positive_net_edge_after_fees"]},
        {"asset": "BTC", "candidate_type": "CROSS_VENUE_THRESHOLD_BASIS", "net_edge_after_fees": -0.01, "hard_blockers": ["no_positive_net_edge_after_fees"]},
    ]
    summary, _ = _run(tmp_path, [report])
    totals = summary["totals"]
    assert totals["best_priced_buy_only_net_edge_after_fees"] == -0.01
    assert totals["best_buy_only_net_edge_after_fees"] == -0.01
    assert totals["best_near_miss_net_edge_after_fees"] == -0.01
    assert totals["worst_net_edge_after_fees"] == -0.99


def test_no_priced_buy_only_rows_produces_null_best_with_reason(tmp_path: Path) -> None:
    report = _watch_quality_report(
        net=None,
        hard_blockers=["missing_polymarket_no_ask"],
        quote_side_diagnostics=["missing_polymarket_no_ask"],
        legs=[{"platform": "polymarket", "side": "NO", "ask": None}],
        top_blockers=[{"blocker": "missing_polymarket_no_ask", "count": 1}],
    )
    summary, _ = _run(tmp_path, [report])
    totals = summary["totals"]
    assert totals["best_priced_buy_only_net_edge_after_fees"] is None
    assert totals["best_priced_buy_only_net_edge_after_fees_reason"] == "no_priced_buy_only_rows"


def test_cdna_not_supplied_is_shown_clearly(tmp_path: Path) -> None:
    scripts = [_watch_quality_report(
        net=-0.01,
        hard_blockers=["no_positive_net_edge_after_fees"],
        legs=[
            {"platform": "kalshi", "side": "YES", "ask": 0.50},
            {"platform": "polymarket", "side": "NO", "ask": 0.51},
        ],
        economic={"no_positive_net_edge_after_fees": 1},
    )]
    summary, _ = _run(tmp_path, scripts, include_cdna=False)
    cdna = summary["totals"]["cdna_participation"]
    assert cdna["cdna_supplied"] is False
    assert cdna["cdna_rows_loaded"] == 0
    assert cdna["cdna_candidates_considered"] == 0
    assert cdna["cdna_fill_first_candidates"] == 0
    assert cdna["cdna_candidate_types_generated"] == {}
    md = (tmp_path / "watch" / "watch_summary.md").read_text(encoding="utf-8")
    assert "| cdna_supplied | false |" in md


def test_include_cdna_without_evidence_dir_states_third_venue_inactive(tmp_path: Path) -> None:
    scripts = [_watch_quality_report(
        net=-0.01,
        hard_blockers=["no_positive_net_edge_after_fees"],
        legs=[{"platform": "kalshi", "side": "YES", "ask": 0.50}],
        economic={"no_positive_net_edge_after_fees": 1},
    )]
    summary, _ = _run(tmp_path, scripts, include_cdna=True, cdna_evidence_dir=None)
    cdna = summary["totals"]["cdna_participation"]
    assert cdna["cdna_supplied"] is False
    assert cdna["cdna_missing_reason"] == "CDNA not supplied; third venue not active in this run."


def test_watcher_surfaces_focused_dashboard_sections(tmp_path: Path) -> None:
    summary, _ = _run(
        tmp_path, [_report_rich(), _report_rich()],
        burst_mode=True, burst_interval_seconds=5, normal_interval_seconds=30,
        boundary_window_seconds=90, clock=_incrementing_clock(),
    )
    t = summary["totals"]
    assert t["manual_micro_test_candidates"] == 2
    assert t["complement_quote_rows"] == 4
    assert t["quote_side_diagnostics"].get("missing_polymarket_no_ask") == 6
    assert t["economic_rejections"].get("no_positive_net_edge_after_fees") == 8
    assert summary["paper_candidates"], "paper candidates should be surfaced"
    assert summary["cdna_fill_first_candidates"], "cdna fill-first list should be surfaced"
    assert summary["latest_iteration_errors"], "collector warnings should surface as errors"
    md = (tmp_path / "watch" / "watch_summary.md").read_text(encoding="utf-8")
    for header in (
        "## Paper Candidates", "## Missing Buy-Side Quote Diagnostics", "## Economic Rejections",
        "## CDNA Fill-First Candidates", "## Window / Time Coverage", "## Latest Iteration Errors",
    ):
        assert header in md, f"missing watcher section: {header}"
    # Near-miss table names exactly what is missing.
    assert "NO ask on higher threshold" in md


def _report_with_coverage() -> dict[str, Any]:
    base = _watch_quality_report(
        net=-0.07,
        hard_blockers=["no_positive_net_edge_after_fees"],
        legs=[{"platform": "kalshi", "side": "YES", "ask": 0.60}, {"platform": "polymarket", "side": "NO", "ask": 0.47}],
        economic={"no_positive_net_edge_after_fees": 1},
    )
    base["candidate_generation_coverage"] = [
        {"candidate_class": "CROSS_VENUE_THRESHOLD_BASIS", "attempted": 4, "generated": 4, "priced": 4, "paper": 0},
        {"candidate_class": "THRESHOLD_MONOTONICITY_COVER", "attempted": 10, "generated": 6, "priced": 2, "paper": 0},
        {"candidate_class": "UP_DOWN_SAME_WINDOW", "attempted": 2, "generated": 2, "priced": 0, "paper": 0},
    ]
    base["near_miss_threshold_buckets"] = {"within_2c": 0, "within_5c": 1, "within_10c": 3}
    return base


def test_watcher_aggregates_candidate_generation_coverage(tmp_path: Path) -> None:
    summary, _ = _run(tmp_path, [_report_with_coverage(), _report_with_coverage()])
    cov = {c["candidate_class"]: c for c in summary["totals"]["candidate_generation_coverage"]}
    # Cross-venue and up/down ARE attempted (not only monotonicity covers).
    assert cov["CROSS_VENUE_THRESHOLD_BASIS"]["attempted"] == 8
    assert cov["UP_DOWN_SAME_WINDOW"]["attempted"] == 4
    # The attempted>generated gap on covers is preserved cumulatively.
    assert cov["THRESHOLD_MONOTONICITY_COVER"]["attempted"] == 20
    assert cov["THRESHOLD_MONOTONICITY_COVER"]["generated"] == 12
    buckets = summary["totals"]["near_miss_threshold_buckets"]
    assert buckets["within_2c"] == 0
    assert buckets["within_5c"] == 0
    assert buckets["within_10c"] == 2
    assert buckets["within_wide_threshold"] == 2
    md = (tmp_path / "watch" / "watch_summary.md").read_text(encoding="utf-8")
    assert "### Candidate Generation Coverage (per class)" in md
    assert "CROSS_VENUE_THRESHOLD_BASIS" in md
    assert "within_10c" in md


def test_watch_summary_shows_trigger_readiness_and_surface_coverage(tmp_path: Path) -> None:
    rep = {
        "summary_counts": {"paper_candidate_rows": 1},
        "candidate_type_counts": {"CROSS_VENUE_THRESHOLD_BASIS": 1},
        "monotonicity_cover_diagnostics": {},
        "candidate_generation_coverage": [
            {"candidate_class": "UP_DOWN_SAME_WINDOW", "attempted": 4, "generated": 0, "priced": 0, "paper": 0},
            {"candidate_class": "CROSS_VENUE_THRESHOLD_BASIS", "attempted": 10, "generated": 2, "priced": 2, "paper": 1},
        ],
        "contract_grammar_counts": {"terminal_threshold": 50, "directional_return": 4},
        "top_blockers": [],
        "rows": [{"asset": "BTC", "candidate_type": "CROSS_VENUE_THRESHOLD_BASIS", "paper_candidate": True,
                  "paper_candidate_class": "OPERATOR_ACCEPTED_RISK", "tradable_buy_only": True,
                  "net_edge_after_fees": 0.17, "adjusted_net_edge_after_fees": 0.17,
                  "target_instant_utc": "2026-05-30T17:00:00+00:00", "hard_blockers": []}],
        "state_grids": [{"target_instant_utc": "2026-05-30T17:00:00+00:00"}], "safety": {},
    }
    summary, _ = _run(tmp_path, [rep])
    t = summary["totals"]
    tr = t["trigger_readiness"]
    assert tr["trigger_would_have_fired"] is True   # paper found + best net 0.17 >= 0.10
    assert tr["live_execution_would_be_blocked"] is True
    assert tr["direct_up_down_coverage"]["attempted"] == 4 and tr["direct_up_down_coverage"]["generated"] == 0
    assert tr["cross_venue_threshold_coverage"]["generated"] == 2
    assert t["contract_grammar_counts"]["directional_return"] == 4
    md = (tmp_path / "watch" / "watch_summary.md").read_text(encoding="utf-8")
    assert "## Trigger Readiness & Surface Coverage" in md


def test_no_trading_auth_or_browser_code_in_watch_module() -> None:
    src = Path("relative_value/watch_crypto_structural_arb.py").read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    code = re.sub(r"(?m)^\s*#.*$", "", code)
    forbidden = [
        r"\bplace_order\b", r"\bsubmit_order\b", r"\bcancel_order\b", r"\bsign_transaction\b",
        r"\bprivate_key\b", r"\bwallet\b", r"\bplaywright\b", r"\bselenium\b", r"\bwebdriver\b",
        r"requests\.(get|post|put|delete|patch)", r"\bhttpx\b", r"\burlopen\b", r"\bAuthorization\b",
        r"\bsmtp\b", r"\bslack\b", r"\bwebhook\b", r"\bnotify\b",
    ]
    for pat in forbidden:
        assert re.search(pat, code, re.IGNORECASE) is None, f"forbidden pattern {pat} in watch module"
