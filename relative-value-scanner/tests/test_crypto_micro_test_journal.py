"""Tests for the crypto micro-test forensic journal (manual; no network/trading)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import scan
from relative_value.crypto_micro_test_journal import (
    start_crypto_micro_test,
    record_crypto_micro_fill,
    finalize_crypto_micro_test,
    crypto_micro_test_report,
    append_crypto_micro_quote_snapshot,
)


NOW = datetime(2026, 5, 30, 16, 0, 0, tzinfo=timezone.utc)
T0 = "2026-05-30T16:00:00Z"
T_SUBMIT = "2026-05-30T16:00:00.300Z"
T_FIRST = "2026-05-30T16:00:00.900Z"
T_FINAL = "2026-05-30T16:00:01.100Z"


def _audit_pack(tmp_path: Path) -> Path:
    legs = [
        {"platform": "kalshi", "side": "YES", "market_id_or_ticker": "K-A", "ask": 0.40, "fee": 0.02,
         "all_in_cost": 0.42, "available_size_or_cap": 75.0, "source_index": "brti",
         "quote_timestamp": "2026-05-30T15:59:59Z", "depth_status": "top", "condition_id": None,
         "token_id_yes": None, "token_id_no": None, "contract_id": None},
        {"platform": "kalshi", "side": "NO", "market_id_or_ticker": "K-B", "ask": 0.50, "fee": 0.02,
         "all_in_cost": 0.52, "available_size_or_cap": 75.0, "source_index": "brti",
         "quote_timestamp": "2026-05-30T15:59:59Z", "depth_status": "top", "condition_id": None,
         "token_id_yes": None, "token_id_no": None, "contract_id": None},
    ]
    cand = {
        "dedup_key": "K1", "asset": "BTC", "candidate_type": "LONG_ONLY_GUARANTEED_PAYOFF",
        "paper_candidate_class": "STRICT_EXACT", "verdict": "VALID_FOR_PAPER_REVIEW",
        "target_instant_utc": "2026-05-30T16:00:00+00:00", "iteration_timestamp": "20260530T155900Z",
        "min_payoff": 1.0, "max_payoff": None, "payoff_vector": [1, 1, 1], "net_edge_after_fees": 0.06,
        "adjusted_net_edge_after_fees": 0.06, "total_cost_after_fees": 0.94, "assumptions_accepted": [],
        "source_indexes": ["brti"], "basket_legs": legs,
    }
    p = tmp_path / "audit.json"
    p.write_text(json.dumps({"schema_kind": "crypto_paper_candidate_audit_pack_v1", "candidates": [cand]}), encoding="utf-8")
    return p


def _exec_plan(tmp_path: Path) -> Path:
    plan = {
        "candidate_id": "K1", "asset": "BTC", "candidate_type": "LONG_ONLY_GUARANTEED_PAYOFF",
        "effective_execution_style": "manual", "candidate_action": "MANUAL_PLACE_LEGS_IN_RECOMMENDED_ORDER",
        "basket_quantity_cap": 6, "expected_min_payoff": 1.0, "expected_net_edge_after_fees": 0.06,
        "parameters": {"max_leg_notional": 5.0, "max_slippage_cents": 1.0, "max_quote_age_ms": 750.0},
        "leg_order_recommendation": {"sequence": ["K-A", "K-B"]},
        "do_not_trade_reasons": [], "risk_warnings": [],
        "legs": [
            {"platform": "kalshi", "side": "YES", "market_id_or_ticker": "K-A", "quoted_ask": 0.40,
             "max_limit_price": 0.41, "expected_fee": 0.02, "all_in_max_cost": 0.43, "quantity_cap": 6, "per_leg_urgency": "MEDIUM_SHORT_DATED_BOOK"},
            {"platform": "kalshi", "side": "NO", "market_id_or_ticker": "K-B", "quoted_ask": 0.50,
             "max_limit_price": 0.51, "expected_fee": 0.02, "all_in_max_cost": 0.53, "quantity_cap": 6, "per_leg_urgency": "MEDIUM_SHORT_DATED_BOOK"},
        ],
    }
    p = tmp_path / "exec.json"
    p.write_text(json.dumps({"schema_kind": "crypto_execution_plan_v1", "plans": [plan]}), encoding="utf-8")
    return p


def _start(tmp_path: Path, **kw) -> tuple[str, Path]:
    root = tmp_path / "mt"
    res = start_crypto_micro_test(
        candidate_audit_pack=_audit_pack(tmp_path), candidate_id="1", execution_plan=_exec_plan(tmp_path),
        max_total_notional=10.0, test_label="t", output_root=root, now=NOW, **kw,
    )
    return res["test_id"], root


def _fill(root, tid, market, side, price, qty, fees, status="filled"):
    return record_crypto_micro_fill(
        test_id=tid, platform="kalshi", market_id_or_ticker=market, side=side,
        filled_price=price, filled_quantity=qty, fees=fees, order_status=status,
        order_start_time_utc=T0, order_submit_time_utc=T_SUBMIT, first_fill_time_utc=T_FIRST,
        final_fill_time_utc=T_FINAL, output_root=root, now=NOW,
    )


def test_start_writes_test_plan(tmp_path: Path) -> None:
    tid, root = _start(tmp_path)
    plan_path = root / tid / "test_plan.json"
    assert plan_path.exists()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["schema_kind"] == "crypto_micro_test_journal_v1"
    assert len(plan["intended_legs"]) == 2
    assert plan["candidate_snapshot"]["asset"] == "BTC"
    assert plan["intended_legs"][0]["intended_limit_price"] == 0.41
    assert plan["intended_legs"][0]["intended_quantity"] == 6
    # initial scanner quote snapshot written + journal files exist.
    assert (root / tid / "quote_snapshots.jsonl").read_text(encoding="utf-8").strip()
    for f in ("event_log.jsonl", "fills.jsonl", "markouts.jsonl"):
        assert (root / tid / f).exists()


def test_record_fill_appends_and_computes_timing_and_slippage(tmp_path: Path) -> None:
    tid, root = _start(tmp_path)
    res = _fill(root, tid, "K-A", "YES", price=0.415, qty=6, fees=0.12)
    d = res["derived"]
    assert d["time_to_submit_ms"] == 300.0
    assert d["time_to_first_fill_ms"] == 600.0
    assert d["time_to_final_fill_ms"] == 800.0
    assert abs(d["slippage_vs_scanner_quote"] - 0.015) < 1e-9   # 0.415 - 0.40
    assert abs(d["slippage_vs_limit"] - 0.005) < 1e-9           # 0.415 - 0.41
    lines = (root / tid / "fills.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1


def test_finalized_complete_test_computes_actual_net_edge(tmp_path: Path) -> None:
    tid, root = _start(tmp_path)
    _fill(root, tid, "K-A", "YES", price=0.41, qty=6, fees=0.12)
    _fill(root, tid, "K-B", "NO", price=0.51, qty=6, fees=0.12)
    final = finalize_crypto_micro_test(test_id=tid, output_root=root, now=NOW)
    assert final["verdict"] == "CLEAN_COMPLETE"
    assert final["guarantee_holds"] is True
    assert abs(final["actual_net_edge_after_fees_if_all_filled"] - 0.04) < 1e-9  # 1.0 - 0.96
    assert final["residual_exposure"] == []


def test_partial_fill_computes_residual_exposure(tmp_path: Path) -> None:
    tid, root = _start(tmp_path)
    _fill(root, tid, "K-A", "YES", price=0.41, qty=6, fees=0.12)
    _fill(root, tid, "K-B", "NO", price=0.51, qty=4, fees=0.08, status="partial")
    final = finalize_crypto_micro_test(test_id=tid, output_root=root, now=NOW)
    assert final["verdict"] == "PARTIAL_FILL_RISK"
    assert final["matched_basket_quantity"] == 4
    resid = {r["leg_key"]: r for r in final["residual_exposure"]}
    a_key = "kalshi::K-A::YES"
    assert a_key in resid and resid[a_key]["residual_quantity"] == 2


def test_hedge_failed_when_one_leg_not_filled(tmp_path: Path) -> None:
    tid, root = _start(tmp_path)
    _fill(root, tid, "K-A", "YES", price=0.41, qty=6, fees=0.12)
    _fill(root, tid, "K-B", "NO", price=None, qty=0, fees=0.0, status="not_filled")
    final = finalize_crypto_micro_test(test_id=tid, output_root=root, now=NOW)
    assert final["verdict"] == "HEDGE_FAILED"
    assert final["guarantee_holds"] is False
    assert any(r["leg_key"] == "kalshi::K-A::YES" for r in final["residual_exposure"])


def test_missing_fill_data_yields_manual_data_incomplete(tmp_path: Path) -> None:
    tid, root = _start(tmp_path)
    _fill(root, tid, "K-A", "YES", price=0.41, qty=6, fees=0.12)  # leg B never recorded
    final = finalize_crypto_micro_test(test_id=tid, output_root=root, now=NOW)
    assert final["verdict"] == "MANUAL_DATA_INCOMPLETE"


def test_canceled_no_trade(tmp_path: Path) -> None:
    tid, root = _start(tmp_path)
    _fill(root, tid, "K-A", "YES", price=None, qty=0, fees=0.0, status="canceled")
    _fill(root, tid, "K-B", "NO", price=None, qty=0, fees=0.0, status="canceled")
    final = finalize_crypto_micro_test(test_id=tid, output_root=root, now=NOW)
    assert final["verdict"] == "CANCELED_NO_TRADE"


def test_report_writes_markdown(tmp_path: Path) -> None:
    tid, root = _start(tmp_path)
    _fill(root, tid, "K-A", "YES", price=0.41, qty=6, fees=0.12)
    _fill(root, tid, "K-B", "NO", price=0.51, qty=6, fees=0.12)
    finalize_crypto_micro_test(test_id=tid, output_root=root, now=NOW)
    res = crypto_micro_test_report(test_id=tid, output_root=root, now=NOW)
    md = (root / tid / "final_report.md").read_text(encoding="utf-8")
    for header in (
        "## 1. Test Summary", "## 2. Candidate Snapshot", "## 3. Intended Execution Plan",
        "## 4. Actual Fill Table", "## 5. Timeline", "## 6. Quote Drift Table", "## 7. Slippage Table",
        "## 8. Fee Comparison", "## 9. Residual Exposure", "## 10. Did the Guarantee Survive?",
        "## 11. Root Cause If Bad", "## 12. Lessons For Next Scanner / Execution Changes",
    ):
        assert header in md, f"missing section {header}"
    assert res["verdict"] == "CLEAN_COMPLETE"


def test_append_quote_snapshot(tmp_path: Path) -> None:
    tid, root = _start(tmp_path)
    qf = tmp_path / "q.json"
    qf.write_text(json.dumps([
        {"platform": "kalshi", "market_id_or_ticker": "K-A", "side": "YES", "bid": 0.39, "ask": 0.42,
         "quote_timestamp": "2026-05-30T16:00:05Z", "depth_status": "top"},
    ]), encoding="utf-8")
    res = append_crypto_micro_quote_snapshot(test_id=tid, source="manual", json_file=qf, output_root=root, now=NOW)
    assert res["appended"] == 1
    snaps = [json.loads(l) for l in (root / tid / "quote_snapshots.jsonl").read_text(encoding="utf-8").strip().splitlines()]
    manual = [s for s in snaps if s["snapshot_source"] == "manual"]
    assert manual and manual[0]["ask"] == 0.42


def test_event_log_records_every_command(tmp_path: Path) -> None:
    tid, root = _start(tmp_path)
    _fill(root, tid, "K-A", "YES", price=0.41, qty=6, fees=0.12)
    finalize_crypto_micro_test(test_id=tid, output_root=root, now=NOW)
    events = [json.loads(l) for l in (root / tid / "event_log.jsonl").read_text(encoding="utf-8").strip().splitlines()]
    types = {e["event_type"] for e in events}
    assert {"start_crypto_micro_test", "record_crypto_micro_fill", "finalize_crypto_micro_test"}.issubset(types)
    for e in events:
        assert "timestamp_utc" in e and "command" in e and "inputs" in e and "warnings" in e


def test_scan_cli_full_flow(tmp_path: Path) -> None:
    ap, ep, root = _audit_pack(tmp_path), _exec_plan(tmp_path), tmp_path / "mt"
    rc = scan.main(["start-crypto-micro-test", "--candidate-audit-pack", str(ap), "--candidate-id", "1",
                    "--execution-plan", str(ep), "--test-label", "cli", "--output-root", str(root)])
    assert rc == 0
    tid = sorted(p.name for p in root.iterdir() if p.is_dir())[0]
    rc = scan.main(["record-crypto-micro-fill", "--test-id", tid, "--platform", "kalshi",
                    "--market-id-or-ticker", "K-A", "--side", "YES", "--filled-price", "0.41",
                    "--filled-quantity", "6", "--fees", "0.12", "--order-status", "filled", "--output-root", str(root)])
    assert rc == 0
    rc = scan.main(["finalize-crypto-micro-test", "--test-id", tid, "--output-root", str(root)])
    assert rc == 0
    rc = scan.main(["crypto-micro-test-report", "--test-id", tid, "--output-root", str(root)])
    assert rc == 0
    assert (root / tid / "final_report.md").exists()


def test_no_trading_auth_order_or_browser_code() -> None:
    src = Path("relative_value/crypto_micro_test_journal.py").read_text(encoding="utf-8")
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
        assert re.search(pat, code, re.IGNORECASE) is None, f"forbidden pattern {pat} in micro-test journal"
