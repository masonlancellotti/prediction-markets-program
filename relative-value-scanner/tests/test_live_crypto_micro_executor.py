"""Tests for the guarded live crypto micro-test executor + adapters (no network)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.live_crypto_micro_executor import run_crypto_structural_trigger
from relative_value.live_crypto_execution_adapters import (
    KalshiLiveAdapter, PolymarketLiveAdapter, CdnaManualFillFirstAdapter, OrderRequest, redact,
    ORDER_SIDE_BUY, ORDER_TYPE_PROTECTED_LIMIT,
)


CLOCK = datetime(2026, 5, 30, 16, 0, 0, tzinfo=timezone.utc)
QTS = "2026-05-30T16:00:00Z"
ARMED_ENV = {"LIVE_CRYPTO_MICROTEST_ENABLED": "true"}


def _leg(platform, side, mid, ask, fee=0.02, size=75.0, qts=QTS):
    return {"platform": platform, "side": side, "market_id_or_ticker": mid, "market_shape": "point_in_time_threshold",
            "ask": ask, "fee": fee, "all_in_cost": round(ask + fee, 8), "available_size_or_cap": size,
            "source_index": "brti", "quote_timestamp": qts, "depth_status": "top", "condition_id": None,
            "token_id_yes": None, "token_id_no": None, "contract_id": None, "complement_used": False, "complement_source": None}


def _candidate(legs=None, **over):
    # Default edge ~0.18 (net@caps ~0.16) so it clears the live min-edge floor of 0.10.
    legs = legs if legs is not None else [_leg("kalshi", "NO", "K-A", 0.30, fee=0.01), _leg("polymarket", "NO", "K-B", 0.50, fee=0.01)]
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


def _report(candidates):
    return {"generated_at": QTS, "summary_counts": {}, "rows": candidates, "safety": {}}


def _fresh(*, leg, now):
    return {"platform": leg["platform"], "market_id_or_ticker": leg["market_id_or_ticker"], "side": leg["side"],
            "ask": leg["ask"], "bid": None, "ask_size": leg["available_size_or_cap"], "bid_size": None,
            "quote_timestamp": QTS, "quote_age_ms": 0.0, "depth_status": "top", "source": "test"}


def _stale(*, leg, now):
    q = _fresh(leg=leg, now=now)
    q["quote_age_ms"] = 5000.0
    return q


def _run(tmp_path, *, candidates, min_net_edge=0.10, execution_style="least_liquid_first", dry_run=True,
         live=False, i_understand=False, env=None, adapters=None, refresher=None, kill=False, order_timeout_ms=300.0):
    out = tmp_path / "trig"
    ks = tmp_path / "KILL"
    if kill:
        ks.write_text("stop", encoding="utf-8")
    summary = run_crypto_structural_trigger(
        assets=["BTC"], watch_once_or_loop="once", min_net_edge=min_net_edge, execution_style=execution_style,
        dry_run=dry_run, live=live, i_understand_this_places_real_orders=i_understand, order_timeout_ms=order_timeout_ms,
        output_dir=out, report_builder=lambda **k: _report(candidates), quote_refresher=refresher or _fresh,
        clock=lambda: CLOCK, sleep=lambda _s: None, console=lambda _m: None, env=env or {}, kill_switch_path=ks,
        adapters=adapters,
    )
    return summary, out


def _trigger(out: Path) -> dict[str, Any]:
    paths = sorted(out.glob("*/trigger_report.json"))
    return json.loads(paths[0].read_text(encoding="utf-8")) if paths else {}


class FakeClient:
    """Injected live client driving an adapter's live path deterministically."""

    def __init__(self, *, fill_qty=0.0, avg_px=0.41, place_status="resting", order_id="O1", extra_resp=None):
        self.fill_qty = fill_qty
        self.avg_px = avg_px
        self.place_status = place_status
        self.order_id = order_id
        self.extra_resp = extra_resp or {}
        self.placed: list[OrderRequest] = []
        self.canceled = False

    def place_limit_buy(self, req: OrderRequest):
        self.placed.append(req)
        return {"status": self.place_status, "order_id": self.order_id, "client_order_id": req.client_order_id, **self.extra_resp}

    def get_order_status(self, order_id):
        return {"status": "filled" if self.fill_qty > 0 else "resting", "filled_quantity": self.fill_qty, "avg_fill_price": self.avg_px}

    def get_fills(self, order_id):
        return [{"price": self.avg_px, "quantity": self.fill_qty, "fee": 0.0}] if self.fill_qty > 0 else []

    def cancel_order(self, order_id):
        self.canceled = True
        return {"status": "canceled", "order_id": order_id, "ok": True}


def _armed_adapters(kalshi_client, poly_client):
    return {"kalshi": KalshiLiveAdapter(mode="live", client=kalshi_client),
            "polymarket": PolymarketLiveAdapter(mode="live", client=poly_client),
            "cdna": CdnaManualFillFirstAdapter(mode="live")}


def test_live_fails_closed_on_stub_adapter(tmp_path: Path) -> None:
    # default (stub) adapters -> live must fail closed (no credentialed client).
    summary, out = _run(tmp_path, candidates=[_candidate()], dry_run=False, live=True, i_understand=True, env=ARMED_ENV)
    tr = _trigger(out)
    assert tr["do_trade"] is False and "live_adapter_not_implemented" in tr["do_not_trade_reasons"]
    assert summary["adapter_status"]["all_live_adapters_ready"] is False
    assert summary["adapter_status"]["cdna_adapter_status"] == "NO_SAFE_ORDER_API"


# ---------------------------------------------------------------------------- #
# Trigger / gate behavior                                                      #
# ---------------------------------------------------------------------------- #


def test_trigger_fires_on_mocked_candidate(tmp_path: Path) -> None:
    summary, out = _run(tmp_path, candidates=[_candidate()])
    assert summary["triggers_created"] == 1
    tr = _trigger(out)
    assert tr["asset"] == "BTC"
    assert (out / "latest_scan_iteration.json").exists()


def test_dry_run_never_calls_place_limit_buy(tmp_path: Path) -> None:
    k, p = FakeClient(fill_qty=9), FakeClient(fill_qty=9)
    summary, out = _run(tmp_path, candidates=[_candidate()], dry_run=True, adapters=_armed_adapters(k, p))
    assert summary["mode"] == "dry_run"
    assert _trigger(out)["do_trade"] is False
    assert k.placed == [] and p.placed == []


def test_live_requires_env_and_flags(tmp_path: Path) -> None:
    k, p = FakeClient(fill_qty=9), FakeClient(fill_qty=9)
    # live flags but env missing -> no trade.
    summary, out = _run(tmp_path, candidates=[_candidate()], dry_run=False, live=True, i_understand=True,
                        env={}, execution_style="least_liquid_first", adapters=_armed_adapters(k, p))
    tr = _trigger(out)
    assert tr["do_trade"] is False
    assert "env_LIVE_CRYPTO_MICROTEST_ENABLED_not_true" in tr["do_not_trade_reasons"]
    assert k.placed == [] and p.placed == []


def test_candidate_below_min_edge_does_not_execute(tmp_path: Path) -> None:
    low = _candidate(legs=[_leg("kalshi", "NO", "K-A", 0.40, fee=0.01), _leg("polymarket", "NO", "K-B", 0.52, fee=0.01)])
    summary, out = _run(tmp_path, candidates=[low], min_net_edge=0.10)  # net ~0.06 < 0.10
    assert summary["triggers_created"] == 0


def test_stale_refreshed_quote_does_not_execute(tmp_path: Path) -> None:
    k, p = FakeClient(fill_qty=9), FakeClient(fill_qty=9)
    summary, out = _run(tmp_path, candidates=[_candidate()], dry_run=False, live=True, i_understand=True,
                        env=ARMED_ENV, refresher=_stale, adapters=_armed_adapters(k, p))
    tr = _trigger(out)
    assert "refreshed_quote_stale" in tr["do_not_trade_reasons"] and tr["do_trade"] is False
    assert k.placed == []


def test_edge_preserving_cap_blocks_when_insufficient(tmp_path: Path) -> None:
    # net ~0.10 qualifies, but at the 1c-per-leg slippage caps net@caps ~0.08 < 0.10 -> plan blocks.
    edge = _candidate(legs=[_leg("kalshi", "NO", "K-A", 0.40, fee=0.01), _leg("polymarket", "NO", "K-B", 0.48, fee=0.01)])
    k, p = FakeClient(fill_qty=9), FakeClient(fill_qty=9)
    summary, out = _run(tmp_path, candidates=[edge], min_net_edge=0.10, dry_run=False, live=True,
                        i_understand=True, env=ARMED_ENV, adapters=_armed_adapters(k, p))
    tr = _trigger(out)
    assert any("edge_below_min_after_max_slippage" in r for r in tr["do_not_trade_reasons"])
    assert tr["do_trade"] is False and k.placed == []


def test_limit_price_never_exceeds_ask_plus_slippage(tmp_path: Path) -> None:
    summary, out = _run(tmp_path, candidates=[_candidate()])
    tr = _trigger(out)
    asks = {"K-A": 0.30, "K-B": 0.50}
    for o in tr["intended_orders"]:
        assert o["max_limit_price"] <= asks[o["market_id_or_ticker"]] + 0.01 + 1e-9


def test_intended_orders_are_buy_limit_only(tmp_path: Path) -> None:
    tr = _trigger(_run(tmp_path, candidates=[_candidate()])[1])
    assert tr["intended_orders"]
    for o in tr["intended_orders"]:
        assert o["side"] == ORDER_SIDE_BUY
        assert o["order_type"] == ORDER_TYPE_PROTECTED_LIMIT
        assert o["max_limit_price"] is not None


def test_kill_switch_aborts_before_order(tmp_path: Path) -> None:
    k, p = FakeClient(fill_qty=9), FakeClient(fill_qty=9)
    summary, out = _run(tmp_path, candidates=[_candidate()], dry_run=False, live=True, i_understand=True,
                        env=ARMED_ENV, kill=True, adapters=_armed_adapters(k, p))
    tr = _trigger(out)
    assert "kill_switch_present" in tr["do_not_trade_reasons"] and tr["do_trade"] is False
    assert k.placed == [] and p.placed == []


def test_cdna_forces_manual_fill_first(tmp_path: Path) -> None:
    legs = [_leg("cdna", "NO", "BTCUSD_X.NXO", 0.30, size=1.0), _leg("polymarket", "NO", "K-B", 0.50)]
    k = FakeClient(fill_qty=1)
    p = FakeClient(fill_qty=1)
    summary, out = _run(tmp_path, candidates=[_candidate(legs=legs)], dry_run=False, live=True, i_understand=True,
                        env=ARMED_ENV, adapters={"cdna": CdnaManualFillFirstAdapter(mode="live"),
                                                 "polymarket": PolymarketLiveAdapter(mode="live", client=p)})
    tr = _trigger(out)
    assert "cdna_requires_manual_fill_first_no_confirmed_fill" in tr["do_not_trade_reasons"]
    assert tr["plan"]["candidate_action"] == "FILL_CDNA_FIRST_THEN_HEDGE_EXACT_FILLED_QUANTITY"
    assert tr["do_trade"] is False and p.placed == []


# ---------------------------------------------------------------------------- #
# Armed protected-order logic                                                  #
# ---------------------------------------------------------------------------- #


def test_order_timeout_cancels_unfilled(tmp_path: Path) -> None:
    k = FakeClient(fill_qty=0.0)  # never fills -> timeout -> cancel
    p = FakeClient(fill_qty=9)
    _run(tmp_path, candidates=[_candidate()], dry_run=False, live=True, i_understand=True, env=ARMED_ENV,
         adapters=_armed_adapters(k, p), order_timeout_ms=300.0)
    assert len(k.placed) == 1 and k.canceled is True
    assert p.placed == []  # first leg never filled -> no hedge


def test_hedge_uses_exact_filled_quantity(tmp_path: Path) -> None:
    k = FakeClient(fill_qty=3)   # first leg fills 3 of 9
    p = FakeClient(fill_qty=3)   # hedge fills the exact 3
    summary, out = _run(tmp_path, candidates=[_candidate()], dry_run=False, live=True, i_understand=True,
                        env=ARMED_ENV, adapters=_armed_adapters(k, p), order_timeout_ms=300.0)
    assert len(p.placed) == 1
    assert p.placed[0].quantity == 3  # exact filled qty of first leg, NOT the intended 9
    assert p.placed[0].side == ORDER_SIDE_BUY
    tr = _trigger(out)
    assert tr["execution_result"]["emergency_review_required"] is False


def test_partial_fill_creates_residual_exposure(tmp_path: Path) -> None:
    k = FakeClient(fill_qty=3)   # first leg fills 3
    p = FakeClient(fill_qty=1)   # hedge fills only 1 -> 2 unhedged
    summary, out = _run(tmp_path, candidates=[_candidate()], dry_run=False, live=True, i_understand=True,
                        env=ARMED_ENV, adapters=_armed_adapters(k, p), order_timeout_ms=300.0)
    tr = _trigger(out)
    er = tr["execution_result"]
    assert er["emergency_review_required"] is True
    assert er["residual_exposure"] and er["residual_exposure"][0]["unhedged_quantity"] == 2
    assert (out / tr["trigger_id"].split("/")[-1]).exists() or Path(tr["trigger_dir"]).exists()


def test_logs_written_and_redacted(tmp_path: Path) -> None:
    k = FakeClient(fill_qty=9, extra_resp={"api_key": "SUPER-SECRET-XYZ"})
    p = FakeClient(fill_qty=9)
    summary, out = _run(tmp_path, candidates=[_candidate()], dry_run=False, live=True, i_understand=True,
                        env=ARMED_ENV, adapters=_armed_adapters(k, p), order_timeout_ms=300.0)
    tdir = Path(_trigger(out)["trigger_dir"])
    for name in ("order_requests_redacted.jsonl", "order_responses_redacted.jsonl"):
        assert (tdir / name).exists()
    resp_text = (tdir / "order_responses_redacted.jsonl").read_text(encoding="utf-8")
    assert "SUPER-SECRET-XYZ" not in resp_text


def test_adapter_place_limit_buy_rejects_non_buy_or_market(tmp_path: Path) -> None:
    a = KalshiLiveAdapter(mode="live", client=FakeClient(fill_qty=1))
    sell = OrderRequest(client_order_id="x", platform="kalshi", market_id_or_ticker="K-A",
                        side="SELL", order_type=ORDER_TYPE_PROTECTED_LIMIT, max_limit_price=0.4, quantity=1)
    assert a.place_limit_buy(sell)["status"] == "REJECTED"
    no_price = OrderRequest(client_order_id="x", platform="kalshi", market_id_or_ticker="K-A",
                            side="BUY", order_type=ORDER_TYPE_PROTECTED_LIMIT, max_limit_price=None, quantity=1)
    assert a.place_limit_buy(no_price)["status"] == "REJECTED"


def test_dry_run_adapter_never_places(tmp_path: Path) -> None:
    a = KalshiLiveAdapter(mode="dry_run")
    req = OrderRequest(client_order_id="x", platform="kalshi", market_id_or_ticker="K-A",
                       side="BUY", order_type=ORDER_TYPE_PROTECTED_LIMIT, max_limit_price=0.4, quantity=1)
    assert a.place_limit_buy(req)["status"] == "DRY_RUN_NOT_PLACED"


def test_cdna_adapter_is_manual_only() -> None:
    a = CdnaManualFillFirstAdapter(mode="live", client=object())
    req = OrderRequest(client_order_id="x", platform="cdna", market_id_or_ticker="C",
                       side="BUY", order_type=ORDER_TYPE_PROTECTED_LIMIT, max_limit_price=0.4, quantity=1)
    resp = a.place_limit_buy(req)
    assert resp["status"] == "MANUAL_REQUIRED"


def test_redact_strips_secrets() -> None:
    out = redact({"api_key": "x", "ok": 1, "nested": {"password": "y", "token_id_yes": "keepme"}})
    assert out["api_key"] == "***REDACTED***" and out["ok"] == 1
    assert out["nested"]["password"] == "***REDACTED***"


def test_no_secret_print_or_browser_code_in_executor_and_adapters() -> None:
    for mod in ("relative_value/live_crypto_micro_executor.py", "relative_value/live_crypto_execution_adapters.py"):
        src = Path(mod).read_text(encoding="utf-8")
        code = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
        code = re.sub(r"(?m)^\s*#.*$", "", code)
        forbidden = [
            r"\bplaywright\b", r"\bselenium\b", r"\bwebdriver\b", r"requests\.(get|post|put|delete|patch)",
            r"\bhttpx\b", r"\burlopen\b", r"\bAuthorization\b", r"\bapi_key\b", r"\bgetenv\b", r"\bdotenv\b",
            r"\.env\b", r"\bsmtp\b", r"\bslack\b", r"\bwebhook\b", r"\bmidpoint\b",
        ]
        for pat in forbidden:
            assert re.search(pat, code, re.IGNORECASE) is None, f"forbidden {pat} in {mod}"
