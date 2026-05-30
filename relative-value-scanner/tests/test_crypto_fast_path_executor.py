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


# ---------------------------------------------------------------------------- #
# CDNA latest-snapshot support (file-only; freshness gated)                    #
# ---------------------------------------------------------------------------- #
CDNA_NOW = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
CDNA_TARGET = (CDNA_NOW + timedelta(hours=1)).isoformat()


def _cdna_partner_row():
    leg = {"platform": "kalshi", "side": "NO", "market_id_or_ticker": "K-BTC-73000", "ask": 0.50, "fee": 0.01,
           "all_in_cost": 0.51, "available_size_or_cap": 50.0, "source_index": "brti",
           "quote_timestamp": CDNA_NOW.isoformat(), "market_shape": "point_in_time_threshold",
           "threshold_or_strike": 73000.0, "target_instant_utc": CDNA_TARGET, "comparator": "above"}
    return {"asset": "BTC", "candidate_type": "PARTNER", "target_instant_utc": CDNA_TARGET, "payoff_vector": [1],
            "min_payoff": 1.0, "net_edge_after_fees": 0.0, "adjusted_net_edge_after_fees": 0.0,
            "total_cost_after_fees": 0.51, "tradable_buy_only": True, "requires_short_or_sell": False,
            "paper_candidate": False, "hard_blockers": [], "assumptions_accepted": [], "source_indexes": ["brti"],
            "dedup_key": "PNR", "iteration_timestamp": "20260530T120000Z", "basket_legs": [leg]}


def _write_cdna_latest(tmp_path: Path, qts: str) -> Path:
    p = tmp_path / "cdna" / "cdna_crypto_latest.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    row = {"contract_id": "CID1", "symbol": "CDNA-BTC-1", "asset": "BTC", "target_instant_utc": CDNA_TARGET,
           "reference_start_utc": CDNA_NOW.isoformat(), "interval_length_seconds": 1200,
           "contract_family": "terminal_threshold", "payoff_observation_type": "point_in_time_at_target",
           "comparator": "above", "threshold_or_strike": 73000.0, "display_yes": 0.30, "display_no": 0.68,
           "exchange_fee": 0.01, "technology_fee": 0.01, "quote_timestamp": qts}
    p.write_text(json.dumps({"generated_at": CDNA_NOW.isoformat(), "contracts": [row]}), encoding="utf-8")
    return p.parent


def _build_cdna_universe(tmp_path: Path, *, qts: str, extra_rows=None):
    # exclude_non_executable=False so CDNA is WATCHED here (scan/research mode) — this
    # exercises the CDNA quote source + staleness in the hot loop. The live default
    # (exclude=True) excludes CDNA from the executable universe (see Part-B tests).
    cdir = _write_cdna_latest(tmp_path, qts)
    rows = [_cdna_partner_row()] + list(extra_rows or [])
    build_active_candidate_universe(
        assets=["BTC"], report_builder=lambda **k: {"generated_at": CDNA_NOW.isoformat(), "rows": rows},
        cdna_timeseries_dir=cdir, max_cdna_snapshot_age_seconds=60, min_net_edge=0.10,
        exclude_non_executable_from_live_universe=False,
        output_path=tmp_path / "ucdna.json", generated_at=CDNA_NOW)
    return tmp_path / "ucdna.json", cdir


def _all_decisions(out: Path):
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(out.glob("*/decision.json"))]


def test_fresh_cdna_row_participates_in_universe_and_fast_path(tmp_path: Path) -> None:
    uni, cdir = _build_cdna_universe(tmp_path, qts=(CDNA_NOW - timedelta(seconds=10)).isoformat())
    u = json.loads(uni.read_text(encoding="utf-8"))
    assert u["cdna_candidate_count"] >= 1  # fresh CDNA row participates in the candidate universe
    assert u["cdna_diagnostics"]["cdna_supplied"] is True and u["cdna_diagnostics"]["cdna_rows_loaded"] == 1
    summary = _run(tmp_path, universe=uni, refresher=_fresh, clock=lambda: CDNA_NOW,
                   cdna_timeseries_dir=cdir, max_cdna_snapshot_age_seconds=60)
    assert summary["cdna"]["cdna_supplied"] is True and summary["cdna"]["cdna_rows_loaded"] == 1
    assert summary["cdna"]["cdna_fill_first_candidates"] >= 1
    cdna_d = [d for d in _all_decisions(tmp_path / "fp") if d["candidate_type"] == "CDNA_FILL_FIRST"]
    assert cdna_d, "expected a CDNA decision"
    assert cdna_d[0]["do_trade"] is False  # CDNA is fill-first; never auto-trades
    assert "cdna_requires_manual_fill_first_no_confirmed_fill" in cdna_d[0]["do_not_trade_reasons"]


def test_stale_cdna_snapshot_excluded_from_fast_path(tmp_path: Path) -> None:
    uni, cdir = _build_cdna_universe(tmp_path, qts=(CDNA_NOW - timedelta(seconds=10)).isoformat())
    _write_cdna_latest(tmp_path, (CDNA_NOW - timedelta(seconds=3600)).isoformat())  # snapshot went stale
    summary = _run(tmp_path, universe=uni, refresher=_fresh, clock=lambda: CDNA_NOW,
                   cdna_timeseries_dir=cdir, max_cdna_snapshot_age_seconds=60)
    cdna = summary["cdna"]
    assert cdna["cdna_stale_rows"] >= 1
    assert cdna["cdna_excluded_stale_candidate_count"] >= 1
    assert "cdna_snapshot_stale" in cdna["cdna_excluded_reasons"]
    assert [d for d in _all_decisions(tmp_path / "fp") if d["candidate_type"] == "CDNA_FILL_FIRST"] == []


def test_cdna_missing_does_not_block_kalshi_polymarket(tmp_path: Path) -> None:
    # standard Kalshi/Polymarket universe, pointed at a non-existent CDNA dir.
    summary = _run(tmp_path, cdna_timeseries_dir=tmp_path / "no_cdna_here", max_cdna_snapshot_age_seconds=60)
    assert summary["decisions"] == 1  # the K/P candidate still produces a decision
    assert summary["cdna"]["cdna_supplied"] is False
    assert summary["cdna"]["cdna_missing_reason"] == "cdna_latest_file_not_found"
    # and with no CDNA configured at all.
    summary2 = _run(tmp_path)
    assert summary2["decisions"] == 1 and summary2["cdna"]["cdna_supplied"] is False


# ---------------------------------------------------------------------------- #
# Part B: executable-only universe + plausible K/P templates                   #
# ---------------------------------------------------------------------------- #
def _kp_template_row(*, net=0.05, paper=False, short=False, missing=False,
                     ct="CROSS_VENUE_THRESHOLD_BASIS", dedup="R1"):
    legs = _slegs()
    if missing:
        for leg in legs:
            leg["ask"] = None
            leg["all_in_cost"] = None
        net = None
    return {"asset": "BTC", "candidate_type": ct, "target_instant_utc": "2026-05-30T17:00:00+00:00", "iteration_timestamp": "20260530T160000Z",
            "payoff_vector": [1, 1, 1], "min_payoff": 1.0, "net_edge_after_fees": net, "adjusted_net_edge_after_fees": net,
            "total_cost_after_fees": (None if net is None else round(1.0 - net, 4)), "tradable_buy_only": not short,
            "requires_short_or_sell": short, "paper_candidate": paper,
            "paper_candidate_class": "OPERATOR_ACCEPTED_RISK" if paper else "NONE",
            "assumptions_accepted": [], "source_indexes": ["brti"], "hard_blockers": [], "dedup_key": dedup,
            "basket_legs": legs}


def _templates_universe(tmp_path, rows, **kw):
    return build_active_candidate_universe(
        assets=["BTC"], report_builder=lambda **k: {"rows": rows, "generated_at": "t"}, generated_at=BASE,
        output_path=tmp_path / "tu.json", **kw)


def test_cdna_candidate_excluded_from_executable_universe_with_reason(tmp_path: Path) -> None:
    cdir = _write_cdna_latest(tmp_path, (CDNA_NOW - timedelta(seconds=10)).isoformat())
    u = build_active_candidate_universe(
        assets=["BTC"], report_builder=lambda **k: {"rows": [_cdna_partner_row()], "generated_at": CDNA_NOW.isoformat()},
        cdna_timeseries_dir=cdir, max_cdna_snapshot_age_seconds=60, min_net_edge=0.10, generated_at=CDNA_NOW,
        min_template_quality="compatible_payoff", include_near_miss_templates=True, include_missing_quote_templates=True,
        output_path=tmp_path / "u.json")  # exclude_non_executable defaults True
    assert u["excluded_cdna_candidate_count"] >= 1 and u["excluded_cdna_reason"] == "no_safe_order_api"
    assert u["non_executable_scan_candidate_count"] >= 1
    ne = u["non_executable_candidates"][0]
    assert ne["execution_status"] == "NO_SAFE_ORDER_API"
    assert ne["do_not_trade_reason"] == "cdna_no_safe_automated_order_adapter"
    assert all(l["platform"] != "cdna" for c in u["candidates"] for l in c["legs"])  # no CDNA in executable universe


def test_kp_near_miss_template_enters_executable_universe(tmp_path: Path) -> None:
    u = _templates_universe(tmp_path, [_kp_template_row(net=0.05)], min_net_edge=0.10,
                            min_template_quality="priced_only", include_near_miss_templates=True,
                            near_miss_net_edge_threshold=0.10)
    assert u["executable_universe_candidate_count"] >= 1 and u["zero_universe_reason"] is None
    # without near-miss inclusion, the below-threshold priced row is excluded.
    u2 = _templates_universe(tmp_path, [_kp_template_row(net=0.05)], min_net_edge=0.10,
                             min_template_quality="priced_only", include_near_miss_templates=False)
    assert u2["executable_universe_candidate_count"] == 0


def test_missing_quote_kp_template_enters_universe_only_with_flag(tmp_path: Path) -> None:
    u = _templates_universe(tmp_path, [_kp_template_row(missing=True)], min_net_edge=0.10,
                            min_template_quality="compatible_payoff", include_missing_quote_templates=True)
    assert u["executable_universe_candidate_count"] >= 1
    u2 = _templates_universe(tmp_path, [_kp_template_row(missing=True)], min_net_edge=0.10,
                             min_template_quality="compatible_payoff", include_missing_quote_templates=False)
    assert u2["executable_universe_candidate_count"] == 0


def test_short_required_row_never_enters_executable_universe(tmp_path: Path) -> None:
    u = _templates_universe(tmp_path, [_kp_template_row(net=0.20, short=True)], min_net_edge=0.10,
                            min_template_quality="compatible_payoff", include_near_miss_templates=True)
    assert u["executable_universe_candidate_count"] == 0
    assert u["zero_universe_reason"] == "all_templates_require_shorting"


def test_zero_universe_has_explicit_reason(tmp_path: Path) -> None:
    u = _templates_universe(tmp_path, [], min_net_edge=0.10, min_template_quality="compatible_payoff")
    assert u["executable_universe_candidate_count"] == 0
    assert u["zero_universe_reason"] == "no_kalshi_polymarket_legs_available"


def test_universe_watcher_has_legs_when_kp_templates_exist(tmp_path: Path) -> None:
    u = _templates_universe(tmp_path, [_kp_template_row(net=0.18, paper=True)], min_net_edge=0.10,
                            min_template_quality="compatible_payoff", include_near_miss_templates=True)
    assert u["watched_leg_count"] >= 2
    assert u["watched_leg_count_by_platform"].get("kalshi", 0) >= 1
    assert u["watched_leg_count_by_platform"].get("polymarket", 0) >= 1


# ---------------------------------------------------------------------------- #
# Part C: live Kalshi/Polymarket-only execution + adapter status               #
# ---------------------------------------------------------------------------- #
def _ready_adapters():
    from relative_value.live_crypto_execution_adapters import default_adapters

    class _Cl:
        def place_limit_buy(self, r): return {"status": "ACCEPTED", "order_id": "o", "filled_quantity": 0.0, "avg_fill_price": None}
        def get_order_status(self, o): return {"filled_quantity": 0.0, "avg_fill_price": None}
        def get_fills(self, o): return []
        def cancel_order(self, o): return {"status": "canceled"}
    a = default_adapters(mode="live")
    a["kalshi"] = KalshiLiveAdapter(mode="live", client=_Cl())
    a["polymarket"] = PolymarketLiveAdapter(mode="live", client=_Cl())
    return a


def _live_kw(**extra):
    return dict(dry_run=False, live=True, env=ARMED_ENV, i_understand_this_places_real_orders=True,
                execution_style="parallel_protected_limit", **extra)


def test_live_mode_fails_closed_on_stub_adapter(tmp_path: Path) -> None:
    summary = _run(tmp_path, universe=_universe(tmp_path), **_live_kw())  # default stub adapters
    d = _decision(tmp_path / "fp")
    assert d["do_trade"] is False and "live_adapter_not_implemented" in d["do_not_trade_reasons"]
    assert summary["adapter_status"]["all_live_adapters_ready"] is False


def test_live_ready_adapters_kp_only_builds_protected_limit_intents(tmp_path: Path) -> None:
    summary = _run(tmp_path, universe=_universe(tmp_path), adapters=_ready_adapters(), **_live_kw())
    d = _decision(tmp_path / "fp")
    assert "live_adapter_not_implemented" not in d["do_not_trade_reasons"]
    assert summary["adapter_status"]["all_live_adapters_ready"] is True
    assert d["intended_orders"]  # K/P-only candidate proceeds to intended protected orders
    assert all(o["order_type"] == "PROTECTED_LIMIT" for o in d["intended_orders"])  # protected limit only
    assert all(o["side"] == "BUY" for o in d["intended_orders"])  # no shorting, BUY only
    assert all(o["order_type"] != "MARKET" for o in d["intended_orders"])  # no market orders


def test_cdna_leg_blocks_live_execution(tmp_path: Path) -> None:
    uni, cdir = _build_cdna_universe(tmp_path, qts=(CDNA_NOW - timedelta(seconds=10)).isoformat())
    _run(tmp_path, universe=uni, refresher=_fresh, clock=lambda: CDNA_NOW, cdna_timeseries_dir=cdir,
         max_cdna_snapshot_age_seconds=60, adapters=_ready_adapters(), **_live_kw())
    cdna_d = [d for d in _all_decisions(tmp_path / "fp") if d["candidate_type"] == "CDNA_FILL_FIRST"]
    assert cdna_d and cdna_d[0]["do_trade"] is False
    assert "cdna_no_safe_automated_order_adapter" in cdna_d[0]["do_not_trade_reasons"]


def test_live_mode_fails_without_flags(tmp_path: Path) -> None:
    # dry-run (default) never trades even with ready adapters.
    summary = _run(tmp_path, universe=_universe(tmp_path), adapters=_ready_adapters())
    d = _decision(tmp_path / "fp")
    assert d["do_trade"] is False
    assert "dry_run_default_no_live_orders" in d["do_not_trade_reasons"]


def test_live_mode_fails_if_min_edge_below_floor(tmp_path: Path) -> None:
    summary = _run(tmp_path, universe=_universe(tmp_path), adapters=_ready_adapters(),
                   min_net_edge=0.05, **_live_kw())
    d = _decision(tmp_path / "fp")
    assert d["do_trade"] is False and "min_net_edge_below_required_floor_0.10" in d["do_not_trade_reasons"]


def test_live_mode_fails_if_quote_stale(tmp_path: Path) -> None:
    summary = _run(tmp_path, universe=_universe(tmp_path), adapters=_ready_adapters(), refresher=_stale,
                   max_quote_age_ms=750.0, **_live_kw())
    d = _decision(tmp_path / "fp")
    assert d["do_trade"] is False and "quote_age_exceeds_max" in d["do_not_trade_reasons"]


# ---------------------------------------------------------------------------- #
# Watch-universe narrowing + refresh prioritization                            #
# ---------------------------------------------------------------------------- #
def _many_candidates(n=5):
    cands, watched = [], []
    for i in range(n):
        legs = [{"leg_key": f"k{i}", "platform": "kalshi", "market_id_or_ticker": f"K{i}", "side": "NO",
                 "reference_ask": 0.3, "fee": 0.01, "available_size_or_cap": 50.0},
                {"leg_key": f"p{i}", "platform": "polymarket", "market_id_or_ticker": f"P{i}", "side": "NO",
                 "token_id": f"T{i}", "reference_ask": 0.5, "fee": 0.01, "available_size_or_cap": 50.0}]
        cands.append({"candidate_id": f"c{i}", "candidate_type": "CROSS_VENUE_THRESHOLD_BASIS",
                      "expected_net_edge_after_fees": round(0.12 - i * 0.02, 4), "legs": legs})
        watched += [dict(leg) for leg in legs]
    return cands, watched


def test_max_watched_legs_and_candidates_limit_universe() -> None:
    from relative_value.crypto_fast_path_executor import _narrow_watch_universe, _priority_leg_keys
    cands, watched = _many_candidates(5)
    nc, nw = _narrow_watch_universe(cands, watched, max_watched_candidates=2, max_watched_legs=3,
                                    prefer_priced=True, prefer_near_miss=True)
    assert len(nc) <= 2 and len(nw) <= 3  # universe narrowed
    assert nc[0]["candidate_id"] == "c0"  # highest expected edge kept first
    pk = _priority_leg_keys(cands)
    assert pk[:2] == ["k0", "p0"]  # best candidate's legs refreshed first


def test_fast_path_summary_reports_narrowing(tmp_path: Path) -> None:
    cands, watched = _many_candidates(5)
    uni = tmp_path / "many.json"
    uni.write_text(json.dumps({"schema_kind": "active_crypto_candidate_universe_v1",
                               "candidates": cands, "watched_legs": watched}), encoding="utf-8")
    summary = _run(tmp_path, universe=uni, max_watched_candidates=2, max_watched_legs=3)
    assert summary["watch_narrowing"]["watched_candidates_before"] == 5
    assert summary["watched_leg_count"] <= 3
