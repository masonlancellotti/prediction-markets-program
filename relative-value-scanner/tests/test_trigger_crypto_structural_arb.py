"""Tests for the trigger-crypto-structural-arb command path (no network/trading)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import scan
from relative_value.live_crypto_micro_executor import run_crypto_structural_trigger


CLOCK = datetime(2026, 5, 30, 16, 0, 0, tzinfo=timezone.utc)
QTS = "2026-05-30T16:00:00Z"


def _leg(platform, side, mid, ask, fee=0.02, size=75.0):
    return {"platform": platform, "side": side, "market_id_or_ticker": mid, "market_shape": "point_in_time_threshold",
            "ask": ask, "fee": fee, "all_in_cost": round(ask + fee, 8), "available_size_or_cap": size,
            "source_index": "brti", "quote_timestamp": QTS, "depth_status": "top", "condition_id": None,
            "token_id_yes": None, "token_id_no": None, "contract_id": None}


def _candidate(legs=None, **over):
    legs = legs if legs is not None else [_leg("kalshi", "NO", "K-A", 0.40), _leg("polymarket", "NO", "K-B", 0.50)]
    total = round(sum(l["all_in_cost"] for l in legs), 8)
    c = {"asset": "BTC", "candidate_type": "LONG_ONLY_GUARANTEED_PAYOFF", "paper_candidate": True,
         "paper_candidate_class": "OPERATOR_ACCEPTED_RISK", "tradable_buy_only": True, "requires_short_or_sell": False,
         "candidate_execution_type": "BUY_ONLY", "hard_blockers": [], "min_payoff": 1.0, "max_payoff": None,
         "payoff_vector": [1, 1, 1], "net_edge_after_fees": round(1.0 - total, 8),
         "adjusted_net_edge_after_fees": round(1.0 - total, 8), "total_cost_after_fees": total,
         "assumptions_accepted": ["source_index_mismatch"], "source_indexes": ["brti"],
         "target_instant_utc": "2026-05-30T17:00:00+00:00", "iteration_timestamp": "20260530T160000Z",
         "verdict": "VALID_FOR_PAPER_REVIEW", "basket_legs": legs}
    c.update(over)
    return c


def _fresh(*, leg, now):
    return {"platform": leg["platform"], "market_id_or_ticker": leg["market_id_or_ticker"], "side": leg["side"],
            "ask": leg["ask"], "bid": None, "ask_size": leg["available_size_or_cap"], "bid_size": None,
            "quote_timestamp": QTS, "quote_age_ms": 0.0, "depth_status": "top", "source": "test"}


def _run(tmp_path, candidates, **kw):
    out = tmp_path / "trig"
    ks = tmp_path / "KILL"
    params = dict(assets=["BTC"], watch_once_or_loop="once", min_net_edge=0.03, execution_style="manual",
                  dry_run=True, output_dir=out, report_builder=lambda **k: {"generated_at": QTS, "rows": candidates, "summary_counts": {}},
                  quote_refresher=_fresh, clock=lambda: CLOCK, sleep=lambda _s: None, console=lambda _m: None,
                  env={}, kill_switch_path=ks)
    params.update(kw)
    return run_crypto_structural_trigger(**params), out


def _trigger(out: Path) -> dict[str, Any]:
    paths = sorted(out.glob("*/trigger_report.json"))
    return json.loads(paths[0].read_text(encoding="utf-8")) if paths else {}


def test_trigger_starts_micro_test_journal(tmp_path: Path) -> None:
    _summary, out = _run(tmp_path, [_candidate()])
    tdir = sorted(out.glob("*/"))[0]
    journals = sorted((tdir / "micro_test_journal").glob("*/test_plan.json"))
    assert journals, "trigger must start a micro-test journal with a test_plan"
    plan = json.loads(journals[0].read_text(encoding="utf-8"))
    assert plan["candidate_snapshot"]["asset"] == "BTC"


def test_trigger_finalizes_journal_on_no_trade(tmp_path: Path) -> None:
    _summary, out = _run(tmp_path, [_candidate()])  # manual + dry-run -> no trade
    tdir = sorted(out.glob("*/"))[0]
    finals = sorted((tdir / "micro_test_journal").glob("*/final_report.json"))
    assert finals, "journal must be finalized even when no order is placed"
    tr = _trigger(out)
    assert tr["do_trade"] is False
    assert "manual_execution_style_no_automated_orders" in tr["do_not_trade_reasons"]


def test_short_required_candidate_is_not_triggered(tmp_path: Path) -> None:
    short_cand = _candidate(requires_short_or_sell=True, candidate_execution_type="REQUIRES_SHORT")
    summary, _out = _run(tmp_path, [short_cand])
    assert summary["triggers_created"] == 0  # filtered out at qualification


def test_hard_blocked_candidate_is_not_triggered(tmp_path: Path) -> None:
    blocked = _candidate(hard_blockers=["missing_ask"])
    summary, _out = _run(tmp_path, [blocked])
    assert summary["triggers_created"] == 0


def test_boundary_unvalidated_candidate_does_not_trade(tmp_path: Path) -> None:
    legs = [_leg("kalshi", "NO", "KXBTC-26MAY3012-B73750", 0.30, size=75.0),
            _leg("polymarket", "NO", "bitcoin-above-73800-on-may-30-2026", 0.55)]
    summary, out = _run(tmp_path, [_candidate(legs=legs)], dry_run=False, live=True,
                        i_understand_this_places_real_orders=True, env={"LIVE_CRYPTO_MICROTEST_ENABLED": "true"},
                        execution_style="least_liquid_first")
    tr = _trigger(out)
    assert "boundary_inclusivity_unvalidated" in tr["do_not_trade_reasons"]
    assert tr["do_trade"] is False


def test_trigger_report_md_written_with_decision(tmp_path: Path) -> None:
    _summary, out = _run(tmp_path, [_candidate()])
    md = sorted(out.glob("*/trigger_report.md"))[0].read_text(encoding="utf-8")
    assert "Live Trigger Report" in md and "do_trade" in md
    assert "protected LIMIT BUY only" in md.lower() or "protected limit buy only" in md.lower()


def test_no_candidate_iteration_creates_no_trigger(tmp_path: Path) -> None:
    summary, out = _run(tmp_path, [])  # no rows
    assert summary["triggers_created"] == 0
    assert (out / "latest_scan_iteration.json").exists()


def test_scan_cli_dry_run_once(tmp_path: Path, monkeypatch) -> None:
    import relative_value.live_crypto_micro_executor as ex
    monkeypatch.setattr(ex, "_default_report_builder", lambda **k: {"generated_at": QTS, "rows": [_candidate()], "summary_counts": {}})
    rc = scan.main([
        "trigger-crypto-structural-arb", "--watch-once-or-loop", "once", "--assets", "BTC",
        "--min-net-edge", "0.03", "--execution-style", "manual", "--dry-run",
        "--output-dir", str(tmp_path / "trig"),
    ])
    assert rc == 0
    assert (tmp_path / "trig" / "trigger_run_summary.json").exists()


def test_no_browser_or_order_submit_outside_adapters() -> None:
    src = Path("relative_value/live_crypto_micro_executor.py").read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    code = re.sub(r"(?m)^\s*#.*$", "", code)
    for pat in (r"\bplaywright\b", r"\bselenium\b", r"\bwebdriver\b", r"\bmidpoint\b", r"\bhttpx\b"):
        assert re.search(pat, code, re.IGNORECASE) is None, f"forbidden {pat}"
