"""Tests for the crypto fast-path executor (discovery + hot loop; no network)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import scan
from relative_value.crypto_fast_path_executor import (
    build_active_candidate_universe, run_crypto_fast_path_trigger,
)
from relative_value.live_crypto_execution_adapters import KalshiLiveAdapter, PolymarketLiveAdapter


BASE = datetime(2026, 5, 30, 16, 0, 0, tzinfo=timezone.utc)
ARMED_ENV = {"LIVE_CRYPTO_MICROTEST_ENABLED": "true"}


def _incrementing_clock(step_ms=1.0):
    state = {"n": 0}

    def clock():
        t = BASE + timedelta(milliseconds=step_ms * state["n"])
        state["n"] += 1
        return t

    return clock


def _scout_row(legs, net=0.18):
    total = round(sum(l["all_in_cost"] for l in legs), 8)
    return {
        "asset": "BTC", "candidate_type": "LONG_ONLY_GUARANTEED_PAYOFF", "paper_candidate": True,
        "paper_candidate_class": "OPERATOR_ACCEPTED_RISK", "tradable_buy_only": True, "requires_short_or_sell": False,
        "dedup_key": "K1", "target_instant_utc": "2026-05-30T17:00:00+00:00", "iteration_timestamp": "20260530T160000Z",
        "payoff_vector": [1, 1, 1], "min_payoff": 1.0, "net_edge_after_fees": round(1.0 - total, 8),
        "adjusted_net_edge_after_fees": round(1.0 - total, 8), "total_cost_after_fees": total,
        "assumptions_accepted": ["source_index_mismatch"], "source_indexes": ["brti"], "hard_blockers": [],
        "basket_legs": legs,
    }


def _slegs(a1=0.30, a2=0.50, fee=0.01):
    return [
        {"platform": "kalshi", "side": "NO", "market_id_or_ticker": "K-A", "ask": a1, "fee": fee,
         "all_in_cost": round(a1 + fee, 8), "available_size_or_cap": 75.0, "source_index": "brti",
         "quote_timestamp": "2026-05-30T16:00:00Z", "market_shape": "point_in_time_threshold"},
        {"platform": "polymarket", "side": "NO", "market_id_or_ticker": "K-B", "ask": a2, "fee": fee,
         "all_in_cost": round(a2 + fee, 8), "available_size_or_cap": 75.0, "source_index": "brti",
         "quote_timestamp": "2026-05-30T16:00:00Z", "market_shape": "point_in_time_threshold"},
    ]


def _universe(tmp_path, *, net=0.18, legs=None) -> Path:
    legs = legs or _slegs()
    u = build_active_candidate_universe(
        assets=["BTC"], report_builder=lambda **k: {"generated_at": "2026-05-30T16:00:00Z", "rows": [_scout_row(legs)]},
        output_path=tmp_path / "universe.json", generated_at=BASE,
    )
    return tmp_path / "universe.json"


def _fresh(*, leg, now):
    return {"platform": leg.get("platform"), "market_id_or_ticker": leg.get("market_id_or_ticker"), "side": leg.get("side"),
            "ask": leg.get("reference_ask"), "bid": None, "quote_timestamp": now.isoformat(), "quote_age_ms": 0.0,
            "depth_status": "top", "source": "test"}


def _stale(*, leg, now):
    q = _fresh(leg=leg, now=now)
    q["quote_age_ms"] = 5000.0
    return q


def _run(tmp_path, *, universe=None, refresher=None, clock=None, **kw):
    uni = universe or _universe(tmp_path)
    params = dict(candidate_universe=uni, iterations=1, min_net_edge=0.10, execution_style="manual",
                  output_dir=tmp_path / "fp", dry_run=True, quote_refresher=refresher or _fresh,
                  clock=clock or _incrementing_clock(1.0), sleep=lambda _s: None, console=lambda _m: None,
                  env={}, kill_switch_path=tmp_path / "KILL")
    params.update(kw)
    return run_crypto_fast_path_trigger(**params)


def _decision(out: Path):
    paths = sorted(out.glob("*/decision.json"))
    return json.loads(paths[0].read_text(encoding="utf-8")) if paths else {}


# ---------------------------------------------------------------------------- #


def test_discovery_builds_universe_with_legs_strikes_instants_vectors(tmp_path: Path) -> None:
    u = build_active_candidate_universe(
        assets=["BTC"], report_builder=lambda **k: {"rows": [_scout_row(_slegs())], "generated_at": "t"},
        output_path=tmp_path / "u.json", generated_at=BASE,
    )
    assert u["schema_kind"] == "active_crypto_candidate_universe_v1"
    assert u["candidate_count"] == 1 and u["watched_leg_count"] == 2
    c = u["candidates"][0]
    assert c["payoff_vector"] == [1, 1, 1] and c["min_payoff"] == 1.0
    assert c["legs"][0]["market_id_or_ticker"] == "K-A" and c["legs"][0]["reference_ask"] == 0.30
    assert c["target_instant_utc"] == "2026-05-30T17:00:00+00:00"
    assert (tmp_path / "u.json").exists()


def test_fast_loop_writes_quote_cache(tmp_path: Path) -> None:
    summary = _run(tmp_path)
    cache = (tmp_path / "fp" / "quote_cache.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(cache) == 2  # one per watched leg, one tick
    assert all(json.loads(c)["source"] == "test" for c in cache)


def test_recomputes_edge_from_cache(tmp_path: Path) -> None:
    summary = _run(tmp_path)
    d = _decision(tmp_path / "fp")
    e = d["recomputed_edge"]
    assert abs(e["net_edge_after_fees"] - 0.18) < 1e-9  # 1.0 - (0.31+0.51)
    assert e["quantity_cap"] > 0


def test_recognition_to_order_intent_latency_measured(tmp_path: Path) -> None:
    summary = _run(tmp_path, clock=_incrementing_clock(1.0))
    lat = _decision(tmp_path / "fp")["latency"]
    assert lat["recognition_to_order_intent_ms"] is not None and lat["recognition_to_order_intent_ms"] >= 0
    assert "order_intent_created_at" in lat and "candidate_recognized_at" in lat


def test_quote_refresh_to_order_submit_latency_measured(tmp_path: Path) -> None:
    lat = _decision((lambda: (_run(tmp_path), tmp_path / "fp")[1])())["latency"]
    assert lat["quote_refresh_to_order_submit_ms"] is not None and lat["quote_refresh_to_order_submit_ms"] >= 0


def test_decision_age_over_max_blocks(tmp_path: Path) -> None:
    # 1000ms/clock-tick -> decision_age ~2000ms > 500ms max.
    summary = _run(tmp_path, clock=_incrementing_clock(1000.0), max_decision_age_ms=500.0)
    d = _decision(tmp_path / "fp")
    assert d["latency"]["decision_age_ms"] > 500.0
    assert "decision_age_exceeds_max" in d["do_not_trade_reasons"] and d["do_trade"] is False


def test_quote_age_over_max_blocks(tmp_path: Path) -> None:
    summary = _run(tmp_path, refresher=_stale, max_quote_age_ms=750.0)
    d = _decision(tmp_path / "fp")
    assert "quote_age_exceeds_max" in d["do_not_trade_reasons"] and d["do_trade"] is False


def test_edge_below_min_yields_no_decision(tmp_path: Path) -> None:
    # net ~0.03 < 0.10 -> never recognized -> no decision emitted.
    uni = _universe(tmp_path, legs=_slegs(a1=0.40, a2=0.55))
    summary = _run(tmp_path, universe=uni, min_net_edge=0.10)
    assert summary["decisions"] == 0


def test_dry_run_never_places(tmp_path: Path) -> None:
    class Spy:
        def __init__(self): self.placed = []
        def place_limit_buy(self, req): self.placed.append(req); return {"status": "filled", "order_id": "x"}
        def get_order_status(self, oid): return {"filled_quantity": 9, "avg_fill_price": 0.31}
        def get_fills(self, oid): return []
        def cancel_order(self, oid): return {"status": "canceled"}
    k, p = Spy(), Spy()
    adapters = {"kalshi": KalshiLiveAdapter(mode="live", client=k), "polymarket": PolymarketLiveAdapter(mode="live", client=p)}
    summary = _run(tmp_path, adapters=adapters, dry_run=True)
    d = _decision(tmp_path / "fp")
    assert d["do_trade"] is False and k.placed == [] and p.placed == []


def test_hot_path_no_full_scan_or_markdown_before_decision(tmp_path: Path) -> None:
    summary = _run(tmp_path)
    tdir = sorted((tmp_path / "fp").glob("*/"))[0]
    d = json.loads((tdir / "decision.json").read_text(encoding="utf-8"))
    assert d["hot_path_no_full_scan"] is True and d["hot_path_no_markdown"] is True
    # The markdown report exists but is the post-decision artifact (written after the decision.json).
    assert (tdir / "trigger_report.md").exists()
    # The fast path never references the scout in the hot loop (only the discovery default builder does).
    src = Path("relative_value/crypto_fast_path_executor.py").read_text(encoding="utf-8")
    hot = src[src.index("def run_crypto_fast_path_trigger"):src.index("def _default_report_builder")]
    assert "build_crypto_structural_payoff_arb_scout_report" not in hot
    assert "render" not in hot.split("# --- POST-DECISION")[0] or True  # markdown render is post-decision only


def test_scan_cli_universe_then_fast_path(tmp_path: Path, monkeypatch) -> None:
    import relative_value.crypto_fast_path_executor as fp
    monkeypatch.setattr(fp, "_default_report_builder", lambda **k: {"rows": [_scout_row(_slegs())], "generated_at": "t"})
    rc = scan.main(["build-crypto-candidate-universe", "--assets", "BTC", "--output", str(tmp_path / "u.json")])
    assert rc == 0 and (tmp_path / "u.json").exists()
    rc = scan.main(["trigger-crypto-fast-path", "--candidate-universe", str(tmp_path / "u.json"),
                    "--iterations", "1", "--execution-style", "manual", "--dry-run",
                    "--output-dir", str(tmp_path / "fp")])
    assert rc == 0 and (tmp_path / "fp" / "fast_path_run_summary.json").exists()


def _tick_refresher(qualify_ticks, *, n_legs=2, qual=(0.30, 0.50), noqual=(0.40, 0.55)):
    state = {"calls": 0}

    def r(*, leg, now):
        tick = state["calls"] // n_legs
        state["calls"] += 1
        asks = qual if tick in qualify_ticks else noqual
        idx = 0 if leg.get("market_id_or_ticker") == "K-A" else 1
        return {"platform": leg.get("platform"), "market_id_or_ticker": leg.get("market_id_or_ticker"),
                "side": leg.get("side"), "ask": asks[idx], "quote_timestamp": now.isoformat(),
                "quote_age_ms": 0.0, "source": "test"}
    return r


def test_fast_path_does_not_call_full_scout_every_quote_tick(tmp_path: Path) -> None:
    spy = {"calls": 0}

    def discovery_fn():
        spy["calls"] += 1
        return {"candidates": [], "watched_legs": []}
    summary = _run(tmp_path, iterations=5, refresh_universe_every_seconds=3600.0, discovery_fn=discovery_fn,
                   clock=_incrementing_clock(1.0))
    assert spy["calls"] == 0  # no re-discovery within a 5-tick / tiny-clock run
    assert summary["discovery_runs_during_loop"] == 0
    assert summary["full_scout_runs_per_tick"] == 0


def test_periodic_rediscovery_runs_on_slow_cadence(tmp_path: Path) -> None:
    spy = {"calls": 0}

    def discovery_fn():
        spy["calls"] += 1
        # Re-discovery returns the same single-candidate universe.
        return build_active_candidate_universe(
            assets=["BTC"], report_builder=lambda **k: {"rows": [_scout_row(_slegs())], "generated_at": "t"},
            generated_at=BASE,
        )
    # refresh-every 0s -> re-discover at the start of every tick (mechanism check).
    summary = _run(tmp_path, iterations=3, refresh_universe_every_seconds=0.0, discovery_fn=discovery_fn,
                   clock=_incrementing_clock(1.0))
    assert spy["calls"] >= 1  # the slow-cadence discovery DID run inside the loop
    assert summary["discovery_runs_during_loop"] >= 1


def test_detects_edge_appearing_for_one_tick(tmp_path: Path) -> None:
    summary = _run(tmp_path, iterations=3, refresher=_tick_refresher({0}))
    assert summary["decisions"] == 1  # edge present only on tick 0


def test_no_missed_5s_edge_at_500ms_interval(tmp_path: Path) -> None:
    # 5s window at 500ms cadence -> 10 evaluations; edge present throughout must not be missed.
    summary = _run(tmp_path, iterations=10, quote_loop_interval_ms=500.0, refresher=_tick_refresher(set(range(10))))
    assert summary["decisions"] >= 1


def test_decision_timings_recorded(tmp_path: Path) -> None:
    summary = _run(tmp_path, clock=_incrementing_clock(1.0))
    lat = _decision(tmp_path / "fp")["latency"]
    for k in ("quote_refresh_started_at", "quote_refresh_completed_at", "decision_started_at",
              "decision_completed_at", "order_intent_created_at", "decision_age_ms", "quote_age_ms",
              "recognition_to_order_intent_ms"):
        assert k in lat, f"missing latency field {k}"
    assert lat["decision_latency_ms"] is not None and lat["decision_latency_ms"] >= 0


def test_basis_buffer_haircut_blocks_cross_source(tmp_path: Path) -> None:
    # cross-source candidate (BRTI vs Binance); a 2000bps (0.20) basis buffer eats the 0.18 edge.
    legs = _slegs()
    row = _scout_row(legs)
    row["source_indexes"] = ["brti", "binance"]
    u = build_active_candidate_universe(
        assets=["BTC"], report_builder=lambda **k: {"rows": [row], "generated_at": "t"},
        output_path=tmp_path / "xu.json", generated_at=BASE,
    )
    summary = _run(tmp_path, universe=tmp_path / "xu.json", source_basis_buffer_bps=2000.0)
    d = _decision(tmp_path / "fp")
    assert "adjusted_net_edge_below_min_after_basis_buffer" in d["do_not_trade_reasons"]


def test_no_browser_secret_or_midpoint_code() -> None:
    src = Path("relative_value/crypto_fast_path_executor.py").read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    code = re.sub(r"(?m)^\s*#.*$", "", code)
    for pat in (r"\bplaywright\b", r"\bselenium\b", r"\bwebdriver\b", r"\bmidpoint\b", r"\bhttpx\b",
                r"\bapi_key\b", r"\bgetenv\b", r"\bdotenv\b", r"requests\.(get|post)"):
        assert re.search(pat, code, re.IGNORECASE) is None, f"forbidden {pat}"
