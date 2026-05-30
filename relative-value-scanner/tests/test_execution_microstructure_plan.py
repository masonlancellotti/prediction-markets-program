"""Tests for the execution-microstructure planning layer (intents only; no network)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import scan
from relative_value.execution_microstructure_plan import (
    build_execution_plan_report,
    render_execution_plan_markdown,
    write_execution_plan_files,
)


DETECTION = "20260530T143909Z"
GEN = datetime(2026, 5, 30, 14, 39, 9, tzinfo=timezone.utc)  # == detection -> opportunity_age 0
FRESH_QUOTE = "2026-05-30T14:39:09Z"


def _leg(platform, side, mid, ask, fee=0.02, size=75.0, source="brti", quote_ts=FRESH_QUOTE, **extra):
    leg = {
        "platform": platform, "side": side, "market_id_or_ticker": mid, "market_shape": "point_in_time_threshold",
        "ask": ask, "fee": fee, "all_in_cost": None if ask is None else round(ask + fee, 8),
        "available_size_or_cap": size, "source_index": source, "quote_timestamp": quote_ts,
        "depth_status": "top", "complement_used": False, "complement_source": None,
        "condition_id": None, "token_id_yes": None, "token_id_no": None, "contract_id": None,
        "hard_blockers": [],
    }
    leg.update(extra)
    return leg


def _candidate(**over):
    legs = over.pop("legs", [_leg("kalshi", "YES", "K-A", 0.40), _leg("kalshi", "NO", "K-B", 0.50)])
    total = round(sum(l["all_in_cost"] for l in legs if l["all_in_cost"] is not None), 8)
    c = {
        "asset": "BTC", "candidate_type": "LONG_ONLY_GUARANTEED_PAYOFF", "paper_candidate_class": "STRICT_EXACT",
        "verdict": "VALID_FOR_PAPER_REVIEW", "candidate_execution_type": "BUY_ONLY",
        "tradable_buy_only": True, "requires_short_or_sell": False,
        "iteration_timestamp": DETECTION, "target_instant_utc": "2026-05-30T16:00:00+00:00",
        "min_payoff": 1.0, "max_payoff": None, "total_cost_after_fees": total,
        "net_edge_after_fees": round(1.0 - total, 8), "adjusted_net_edge_after_fees": round(1.0 - total, 8),
        "assumptions_accepted": [], "hard_blockers": [], "source_indexes": ["brti"],
        "state_grid": ["[-inf,1)", "[1,2)", "[2,+inf)"], "payoff_vector": [1, 1, 1], "basket_legs": legs,
    }
    c.update(over)
    return c


def _write_report(tmp_path: Path, candidates: list[dict[str, Any]], name="audit.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps({"schema_kind": "crypto_paper_candidate_audit_pack_v1", "candidates": candidates}), encoding="utf-8")
    return path


def _plan(tmp_path, candidates, **kw):
    path = _write_report(tmp_path, candidates)
    params = dict(candidate_report=path, generated_at=GEN, max_total_notional=10.0, max_leg_notional=5.0,
                  max_slippage_cents=1.0, max_quote_age_ms=750.0, execution_style="manual")
    params.update(kw)
    return build_execution_plan_report(**params)


def test_parallel_protected_limit_plan_generated(tmp_path: Path) -> None:
    rep = _plan(tmp_path, [_candidate()], execution_style="parallel_protected_limit")
    pl = rep["plans"][0]
    assert pl["effective_execution_style"] == "parallel_protected_limit"
    assert pl["leg_order_recommendation"]["mode"] == "parallel"
    assert pl["basket_quantity_cap"] > 0
    assert pl["net_edge_after_fees_at_max_limits"] is not None and pl["net_edge_after_fees_at_max_limits"] > 0
    assert pl["executable_intent"] is True


def test_fill_worst_leg_first_plan_generated(tmp_path: Path) -> None:
    rep = _plan(tmp_path, [_candidate()], execution_style="fill_worst_leg_first")
    pl = rep["plans"][0]
    assert pl["effective_execution_style"] == "fill_worst_leg_first"
    assert pl["leg_order_recommendation"]["mode"] == "worst_leg_first"
    assert pl["hedge_plan"]["hedge_quantity_basis"] == "EXACT_FILLED_QUANTITY"


def test_cdna_leg_forces_fill_first_plan(tmp_path: Path) -> None:
    legs = [_leg("cdna", "DISPLAY_NO", "BTCUSD_X.NXO", 0.45, size=1.0), _leg("polymarket", "NO", "P-B", 0.50)]
    rep = _plan(tmp_path, [_candidate(legs=legs)], execution_style="parallel_protected_limit")
    pl = rep["plans"][0]
    assert pl["has_cdna_leg"] is True
    assert pl["effective_execution_style"] == "fill_worst_leg_first"
    assert pl["execution_style_override_reason"] == "cdna_leg_requires_fill_first"
    assert pl["candidate_action"] == "FILL_CDNA_FIRST_THEN_HEDGE_EXACT_FILLED_QUANTITY"
    # CDNA leg is ordered first and flagged highest urgency.
    assert pl["leg_order_recommendation"]["sequence"][0] == "BTCUSD_X.NXO"
    cdna_leg = [l for l in pl["legs"] if l["platform"] == "cdna"][0]
    assert cdna_leg["per_leg_urgency"] == "HIGHEST_FILL_FIRST"
    assert cdna_leg["order_type"] == "DISPLAY_PRICE_FILL_FIRST"
    # CDNA has no slippage budget: max limit == quoted display price.
    assert cdna_leg["max_limit_price"] == cdna_leg["quoted_ask"]


def test_limit_price_cap_never_exceeds_max_slippage(tmp_path: Path) -> None:
    legs = [_leg("kalshi", "YES", "K-A", 0.40), _leg("polymarket", "NO", "P-B", 0.50)]
    rep = _plan(tmp_path, [_candidate(legs=legs)], max_slippage_cents=1.0)
    pl = rep["plans"][0]
    slip = 0.01
    for leg in pl["legs"]:
        assert leg["max_limit_price"] <= leg["quoted_ask"] + slip + 1e-9
        assert leg["max_limit_price"] <= 1.0 + 1e-9


def test_stale_quote_blocks_execution_plan(tmp_path: Path) -> None:
    legs = [_leg("kalshi", "YES", "K-A", 0.40, quote_ts="2026-05-30T14:00:00Z"),
            _leg("kalshi", "NO", "K-B", 0.50, quote_ts="2026-05-30T14:00:00Z")]
    rep = _plan(tmp_path, [_candidate(legs=legs)], max_quote_age_ms=750.0)
    pl = rep["plans"][0]
    assert pl["timing_budget"]["stale_leg_count"] == 2
    assert "stale_quote_at_detection" in pl["do_not_trade_reasons"]
    assert pl["executable_intent"] is False


def test_partial_fill_residual_exposure_computed(tmp_path: Path) -> None:
    rep = _plan(tmp_path, [_candidate()])
    pl = rep["plans"][0]
    pf = pl["partial_fill_plan"]
    assert pf["any_residual_risk"] is True
    assert len(pf["scenarios"]) == 2
    for s in pf["scenarios"]:
        assert s["hedge_quantity_basis"] == "EXACT_FILLED_QUANTITY_NOT_INTENDED_QUANTITY"
        assert s["worst_case_loss_per_contract_if_unhedged"] is not None
    assert pl["residual_exposure_plan"]["hedge_quantity_basis"] == "EXACT_FILLED_QUANTITY"


def test_edge_negative_after_slippage_blocks(tmp_path: Path) -> None:
    # net ~0.005 with 1c slippage on 2 legs (2c) -> negative at caps -> blocked.
    legs = [_leg("kalshi", "YES", "K-A", 0.50), _leg("kalshi", "NO", "K-B", 0.475)]
    c = _candidate(legs=legs)  # total 0.52+0.495=1.015? -> recompute net
    rep = _plan(tmp_path, [c])
    pl = rep["plans"][0]
    # With near-1.0 cost, slippage drives net at caps <= 0.
    assert pl["net_edge_after_fees_at_max_limits"] is not None and pl["net_edge_after_fees_at_max_limits"] <= 0
    assert "edge_non_positive_after_max_slippage" in pl["do_not_trade_reasons"]
    assert pl["executable_intent"] is False


def test_invalid_verdict_is_do_not_trade(tmp_path: Path) -> None:
    rep = _plan(tmp_path, [_candidate(verdict="INVALID_RECOMPUTE_FAIL")])
    pl = rep["plans"][0]
    assert "candidate_verdict_INVALID_RECOMPUTE_FAIL" in pl["do_not_trade_reasons"]
    assert pl["executable_intent"] is False


def test_opportunity_stale_when_planning_off_old_report(tmp_path: Path) -> None:
    # generated_at far after detection -> opportunity stale -> refresh required.
    path = _write_report(tmp_path, [_candidate()])
    rep = build_execution_plan_report(
        candidate_report=path, generated_at=datetime(2026, 5, 31, tzinfo=timezone.utc),
        max_quote_age_ms=750.0, execution_style="manual",
    )
    pl = rep["plans"][0]
    assert "opportunity_stale_refresh_quotes_before_trading" in pl["do_not_trade_reasons"]


def test_markdown_and_files_written(tmp_path: Path) -> None:
    path = _write_report(tmp_path, [_candidate()])
    write_execution_plan_files(
        candidate_report=path, generated_at=GEN, json_output=tmp_path / "p.json", markdown_output=tmp_path / "p.md",
        execution_style="manual",
    )
    md = (tmp_path / "p.md").read_text(encoding="utf-8")
    for header in (
        "## Executive Summary", "## Candidate Execution Plans", "#### Recommended Leg Order",
        "#### Limit Prices / Caps", "#### Timing Budget", "#### Partial-Fill Scenarios",
        "## Residual Exposure Table", "## What Could Go Wrong", "## Manual Micro-Test Instructions", "## Safety Notes",
    ):
        assert header in md, f"missing section {header}"
    payload = json.loads((tmp_path / "p.json").read_text(encoding="utf-8"))
    assert payload["schema_kind"] == "crypto_execution_plan_v1"
    assert payload["safety"]["live_order_placement"] is False


def test_missing_report_is_clean(tmp_path: Path) -> None:
    rep = build_execution_plan_report(candidate_report=tmp_path / "nope.json", generated_at=GEN)
    assert rep["candidate_report_exists"] is False
    assert rep["candidate_plans_total"] == 0
    assert "No candidate plans" in render_execution_plan_markdown(rep)


def test_scan_cli_runs_execution_plan(tmp_path: Path) -> None:
    path = _write_report(tmp_path, [_candidate()])
    rc = scan.main([
        "crypto-execution-plan",
        "--candidate-report", str(path),
        "--execution-style", "manual",
        "--json-output", str(tmp_path / "a.json"),
        "--markdown-output", str(tmp_path / "a.md"),
    ])
    assert rc == 0 and (tmp_path / "a.json").exists()


def test_no_live_order_auth_env_or_browser_code() -> None:
    src = Path("relative_value/execution_microstructure_plan.py").read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    code = re.sub(r"(?m)^\s*#.*$", "", code)
    forbidden = [
        r"\bplace_order\b", r"\bsubmit_order\b", r"\bcancel_order\b", r"\bsign_transaction\b",
        r"\bprivate_key\b", r"\bwallet\b", r"\bplaywright\b", r"\bselenium\b", r"\bwebdriver\b",
        r"requests\.(get|post|put|delete|patch)", r"\bhttpx\b", r"\burlopen\b", r"\burllib\b",
        r"\bAuthorization\b", r"\bapi_key\b", r"\bos\.environ\b", r"\bgetenv\b", r"\bdotenv\b",
        r"\bsmtp\b", r"\bslack\b", r"\bwebhook\b",
    ]
    for pat in forbidden:
        assert re.search(pat, code, re.IGNORECASE) is None, f"forbidden pattern {pat} in execution-plan module"
