"""Crypto structural payoff-state arb engine — behavioral tests.

Covers the 16 required scenarios: payoff-vector correctness, YES-only synthetic
buckets, long-only guaranteed baskets, same-payoff-cheaper, diagnostics,
monotonicity, up/down same-window, CDNA fill-first, hard blockers, accepted
basis, no-midpoint, and no-execution-code.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import scan
from relative_value.crypto_structural_payoff_arb_scout import (
    _build_state_grid,
    _complement,
    _vector_above,
    _vector_at_or_below,
    _vector_bucket,
    build_crypto_structural_payoff_arb_scout_report as build_report,
)


NOW = datetime(2026, 5, 30, 5, 0, tzinfo=timezone.utc)
INST = "2026-05-30T06:00:00+00:00"
OTHER = "2026-05-30T07:00:00+00:00"
REF = "2026-05-30T05:00:00+00:00"


def _tk(
    platform: str,
    *,
    obs: str = "point_in_time_at_target",
    comp: str = "above",
    strike: float | None = 70000.0,
    yes: float | None = 0.45,
    no: float | None = 0.55,
    floor: float | None = None,
    cap: float | None = None,
    instant: str = INST,
    ref_start: str | None = None,
    src: str | None = None,
    quote_ts: str = "2026-05-30T04:59:00Z",
) -> dict:
    shape = {
        "point_in_time_at_target": "point_in_time_threshold",
        "range_at_target": "range_bucket",
        "interval_start_to_end_change": "up_down",
    }.get(obs, "unknown")
    return {
        "asset": "BTC", "platform": platform, "market_shape": shape, "payoff_observation_type": obs,
        "comparator": comp, "threshold_or_strike": strike, "bucket_floor": floor, "bucket_cap": cap,
        "reference_start_utc": ref_start, "target_instant_utc": instant, "interval_length_seconds": None,
        "price_source": src or f"{platform}_index", "market_id_or_ticker": f"{platform}-{strike}-{floor}-{cap}-{comp}",
        "quote": {
            "yes_ask": yes, "yes_ask_size": 100.0, "no_ask": no, "no_ask_size": 100.0,
            "depth_status": "top", "quote_timestamp": quote_ts, "quote_diagnostics": [], "blockers_remaining": [],
        },
    }


def _build(*, kalshi=None, polymarket=None, cdna=None, **opts) -> dict:
    params: dict[str, Any] = dict(
        assets=["BTC"], operator_risk_mode="aggressive", include_cdna=True,
        operator_accept_cdna_display_price_risk=True, allow_top_of_book_depth=True, operator_size_cap=10.0,
        cdna_operator_size_cap=1.0, max_quote_age_seconds=999999.0, min_available_notional=1.0,
        max_basket_legs=12, generated_at=NOW,
        rows_by_asset={"BTC": {"kalshi_rows": kalshi or [], "polymarket_rows": polymarket or [], "cdna_rows": cdna or []}},
    )
    params.update(opts)
    return build_report(**params)


def _rows(report: dict, ct: str | None = None, paper: bool | None = None) -> list:
    out = report.get("rows") or []
    if ct:
        out = [r for r in out if r.get("candidate_type") == ct]
    if paper is not None:
        out = [r for r in out if r.get("paper_candidate") is paper]
    return out


# ---------------------------------------------------------------------------- #
# 1-3: payoff vectors                                                          #
# ---------------------------------------------------------------------------- #


def test_range_bucket_payoff_vector_is_correct() -> None:
    grid = _build_state_grid([_tk("kalshi", obs="range_at_target", floor=72000.0, cap=73000.0, strike=72000.0)])
    # boundaries {72000, 73000} -> states [-inf,72000),[72000,73000),[73000,inf)
    assert _vector_bucket(grid, 72000.0, 73000.0) == (0, 1, 0)
    assert _vector_bucket(grid, None, 72000.0) == (1, 0, 0)  # bottom tail <= 72000
    assert _vector_bucket(grid, 73000.0, None) == (0, 0, 1)  # top tail >= 73000


def test_above_threshold_payoff_vector_is_correct() -> None:
    grid = _build_state_grid([{"threshold_or_strike": 72000.0}, {"threshold_or_strike": 73000.0}])
    assert _vector_above(grid, 73000.0) == (0, 0, 1)
    assert _vector_above(grid, 72000.0) == (0, 1, 1)
    assert _vector_at_or_below(grid, 72000.0) == (1, 0, 0)


def test_no_side_complement_payoff_vector_is_correct() -> None:
    grid = _build_state_grid([{"threshold_or_strike": 72000.0}, {"threshold_or_strike": 73000.0}])
    above = _vector_above(grid, 73000.0)
    assert _complement(above) == (1, 1, 0)
    assert _complement(above) == _vector_at_or_below(grid, 73000.0)


# ---------------------------------------------------------------------------- #
# 4-10: candidate generators                                                   #
# ---------------------------------------------------------------------------- #


def _exhaustive_kalshi_family():
    # bottom tail <=72000, bucket 72000-73000, top tail >=73000. YES asks sum < 1.
    return [
        _tk("kalshi", obs="range_at_target", comp="below", strike=72000.0, floor=None, cap=72000.0, yes=0.30, no=0.70),
        _tk("kalshi", obs="range_at_target", comp="range", strike=72500.0, floor=72000.0, cap=73000.0, yes=0.25, no=0.75),
        _tk("kalshi", obs="range_at_target", comp="above", strike=73000.0, floor=73000.0, cap=None, yes=0.30, no=0.70),
    ]


def test_bucket_to_above_synthetic_uses_yes_buckets_only(tmp_path: Path) -> None:
    from relative_value.crypto_structural_payoff_arb_scout import (
        _Opts,
        _build_state_grid as build_grid,
        _synthetic_bucket_instruments,
    )

    family = _exhaustive_kalshi_family()
    rows = family + [_tk("polymarket", comp="above", strike=73000.0, yes=0.28, no=0.72)]
    grid = build_grid(rows)
    opts = _Opts(
        risk_mode="aggressive", include_cdna=False, operator_accept_cdna=False, depth_permissive=True,
        operator_size_cap=10.0, cdna_operator_size_cap=1.0, max_quote_age_seconds=999999.0,
        min_available_notional=1.0, max_basket_legs=12, generated=NOW,
    )
    synth = _synthetic_bucket_instruments(rows, grid, opts)
    assert synth, "expected synthetic bucket instruments"
    # EVERY leg of EVERY synthetic instrument must be a YES bucket leg (never NO).
    for inst in synth:
        for leg in inst.legs:
            assert leg.platform == "kalshi" and leg.side == "YES", "synthetic baskets must use YES buckets only"
    # And the engine flags the invariant.
    report = _build(kalshi=family, polymarket=[_tk("polymarket", comp="above", strike=73000.0, yes=0.28, no=0.72)])
    assert report["safety"]["synthetic_uses_yes_buckets_only"] is True


def test_long_only_guaranteed_payoff_basket_becomes_paper_candidate() -> None:
    # Exhaustive Kalshi family with YES asks 0.30+0.25+0.30 = 0.85 (+fees) < $1 -> guaranteed.
    report = _build(kalshi=_exhaustive_kalshi_family())
    paper = _rows(report, "LONG_ONLY_GUARANTEED_PAYOFF", paper=True)
    assert paper, f"expected a long-only guaranteed paper candidate; rows={[(r['candidate_type'],r['net_edge_after_fees'],r['hard_blockers']) for r in report['rows']]}"
    r = paper[0]
    assert r["min_payoff"] >= 1.0
    assert r["total_cost_after_fees"] < 1.0
    assert r["net_edge_after_fees"] > 0
    assert all(v >= 1 for v in r["payoff_vector"])


def test_same_payoff_cheaper_basket_is_detected() -> None:
    report = _build(
        kalshi=[_tk("kalshi", comp="above", strike=70000.0, yes=0.40, no=0.62)],
        polymarket=[_tk("polymarket", comp="above", strike=70000.0, yes=0.46, no=0.55)],
    )
    same = _rows(report, "SAME_PAYOFF_CHEAPER_BASKET")
    assert same, "Kalshi above-70000 YES (0.40) and Polymarket above-70000 YES (0.46) share a payoff vector"


def test_threshold_to_bucket_diagnostic_not_paper_candidate() -> None:
    report = _build(
        kalshi=[
            _tk("kalshi", comp="above", strike=70000.0, yes=0.40, no=0.62),
            _tk("kalshi", comp="above", strike=71000.0, yes=0.30, no=0.72),
        ],
    )
    diag = _rows(report, "THRESHOLD_TO_BUCKET_DIAGNOSTIC")
    assert diag
    assert all(not r["paper_candidate"] for r in diag)
    assert all("requires_short_or_not_guaranteed" in r["hard_blockers"] for r in diag)


def test_monotonicity_violation_is_detected() -> None:
    # above 70000 cheaper (0.30) than above 71000 (0.45) -> P(>70000) < P(>71000): violation.
    report = _build(
        kalshi=[
            _tk("kalshi", comp="above", strike=70000.0, yes=0.30, no=0.72),
            _tk("kalshi", comp="above", strike=71000.0, yes=0.45, no=0.57),
        ],
    )
    mono = _rows(report, "MONOTONICITY_VIOLATION")
    assert mono, "expected a monotonicity violation diagnostic"


def test_updown_same_window_matches_only_when_start_and_target_match() -> None:
    # Generator buys kalshi UP (yes) + polymarket DOWN (no): 0.40 + 0.48 + fees < $1.
    matched = _build(
        polymarket=[_tk("polymarket", obs="interval_start_to_end_change", comp="up", strike=None, ref_start=REF, yes=0.55, no=0.48)],
        kalshi=[_tk("kalshi", obs="interval_start_to_end_change", comp="up", strike=None, ref_start=REF, yes=0.40, no=0.60)],
    )
    ud = _rows(matched, "UP_DOWN_SAME_WINDOW")
    assert ud and ud[0]["paper_candidate"] is True

    mismatched = _build(
        polymarket=[_tk("polymarket", obs="interval_start_to_end_change", comp="up", strike=None, ref_start=REF)],
        kalshi=[_tk("kalshi", obs="interval_start_to_end_change", comp="up", strike=None, ref_start="2026-05-30T04:00:00+00:00")],
    )
    assert _rows(mismatched, "UP_DOWN_SAME_WINDOW") == []


def test_cdna_threshold_becomes_cdna_fill_first_if_net_positive() -> None:
    report = _build(
        cdna=[_tk("cdna", comp="above", strike=70000.0, yes=0.40, no=0.62)],
        polymarket=[_tk("polymarket", comp="above", strike=70000.0, yes=0.45, no=0.55)],
    )
    cdna_paper = [r for r in _rows(report, paper=True) if r["paper_candidate_class"] == "CDNA_FILL_FIRST"]
    assert cdna_paper, f"expected CDNA_FILL_FIRST; rows={[(r['candidate_type'],r['paper_candidate_class'],r['net_edge_after_fees']) for r in _rows(report, paper=True)]}"
    r = cdna_paper[0]
    assert r["candidate_action"] == "FILL_CDNA_FIRST_THEN_HEDGE_EXACT_FILLED_QUANTITY"
    assert any(l["platform"] == "cdna" and l["side"].startswith("DISPLAY") for l in r["basket_legs"])
    assert r["strict_exact_arb"] is False


# ---------------------------------------------------------------------------- #
# 11-14: hard blockers + accepted basis                                        #
# ---------------------------------------------------------------------------- #


def test_target_instant_mismatch_hard_blocks() -> None:
    # Different instants -> different price-state groups -> never combined into a cover.
    report = _build(
        kalshi=[_tk("kalshi", comp="above", strike=70000.0, yes=0.40, no=0.62, instant=INST)],
        polymarket=[_tk("polymarket", comp="above", strike=70000.0, yes=0.45, no=0.55, instant=OTHER)],
    )
    assert _rows(report, paper=True) == []


def test_missing_ask_hard_blocks() -> None:
    # The only cover (Kalshi above YES + Polymarket above NO) needs the Polymarket NO ask.
    report = _build(
        kalshi=[_tk("kalshi", comp="above", strike=70000.0, yes=0.40, no=0.62)],
        polymarket=[_tk("polymarket", comp="above", strike=70000.0, yes=0.45, no=None)],
    )
    assert all(not r["paper_candidate"] for r in report["rows"])
    assert any("missing_ask" in (r.get("hard_blockers") or []) for r in report["rows"])


def test_stale_quote_hard_blocks() -> None:
    report = _build(
        kalshi=[_tk("kalshi", comp="above", strike=70000.0, yes=0.40, no=0.62, quote_ts="2026-05-29T00:00:00Z")],
        polymarket=[_tk("polymarket", comp="above", strike=70000.0, yes=0.45, no=0.55, quote_ts="2026-05-29T00:00:00Z")],
        max_quote_age_seconds=300.0,
    )
    assert all(not r["paper_candidate"] for r in report["rows"])
    assert any("stale_or_missing_quote" in (r.get("hard_blockers") or []) for r in report["rows"])


def test_source_index_mismatch_accepted_only_in_aggressive() -> None:
    kalshi = [_tk("kalshi", comp="above", strike=70000.0, yes=0.40, no=0.62, src="cf_benchmarks_brti")]
    poly = [_tk("polymarket", comp="above", strike=70000.0, yes=0.45, no=0.55, src="binance")]
    aggressive = _build(kalshi=kalshi, polymarket=poly, operator_risk_mode="aggressive")
    paper = _rows(aggressive, "CROSS_VENUE_THRESHOLD_BASIS", paper=True)
    assert paper and "source_index_mismatch" in paper[0]["assumptions_accepted"]

    conservative = _build(kalshi=kalshi, polymarket=poly, operator_risk_mode="conservative")
    assert _rows(conservative, paper=True) == [], "cross-source basis must not auto-accept in conservative"


def test_no_midpoint_uses_asks_only() -> None:
    report = _build(
        kalshi=[_tk("kalshi", comp="above", strike=70000.0, yes=0.40, no=0.62)],
        polymarket=[_tk("polymarket", comp="above", strike=70000.0, yes=0.45, no=0.55)],
    )
    r = _rows(report, "CROSS_VENUE_THRESHOLD_BASIS", paper=True)[0]
    # net = min_payoff - sum(all_in_cost) where each all_in = ask + fee (asks only).
    total = sum(l["all_in_cost"] for l in r["basket_legs"])
    assert abs(r["net_edge_after_fees"] - (r["min_payoff"] - total)) < 1e-9
    for leg in r["basket_legs"]:
        assert abs(leg["all_in_cost"] - (leg["ask"] + leg["fee"])) < 1e-9


def test_scan_command_runs_end_to_end(tmp_path: Path) -> None:
    root = tmp_path / "ev"
    (root / "btc").mkdir(parents=True, exist_ok=True)
    snap = {
        "kalshi_rows": [_tk("kalshi", comp="above", strike=70000.0, yes=0.40, no=0.62)],
        "polymarket_rows": [_tk("polymarket", comp="above", strike=70000.0, yes=0.45, no=0.55)],
        "cdna_rows": [],
    }
    (root / "btc" / "interval_typed_keys.json").write_text(json.dumps(snap), encoding="utf-8")
    json_out = tmp_path / "s.json"
    md_out = tmp_path / "s.md"
    rc = scan.main(
        [
            "crypto-structural-payoff-arb-scout",
            "--assets", "BTC",
            "--operator-risk-mode", "aggressive",
            "--allow-top-of-book-depth", "--operator-size-cap", "10",
            "--max-quote-age-seconds", "999999",
            "--evidence-roots", str(root),
            "--json-output", str(json_out),
            "--markdown-output", str(md_out),
        ]
    )
    assert rc == 0
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["schema_kind"] == "crypto_structural_payoff_arb_scout_v1"
    assert payload["safety"]["uses_midpoint"] is False
    assert payload["safety"]["synthetic_uses_yes_buckets_only"] is True
    md = md_out.read_text(encoding="utf-8")
    assert "# Crypto Structural Payoff-State Arb Scout" in md
    assert "Long-only guaranteed payoff" in md


# ---------------------------------------------------------------------------- #
# Grammar-aware family separation, native-vs-synthetic, basis buffer           #
# ---------------------------------------------------------------------------- #


def _tk_barrier(**kw):
    row = _tk("polymarket", obs="point_in_time_at_target", comp="above", strike=80000.0, **kw)
    row["market_id_or_ticker"] = "polymarket-will-btc-hit-80000-by-friday"  # "hit" -> barrier
    row["rules_text"] = "Resolves YES if BTC hits $80,000 at any point before expiry."
    return row


def test_barrier_touch_not_matched_to_terminal_threshold() -> None:
    report = _build(
        polymarket=[_tk_barrier()],
        kalshi=[_tk("kalshi", comp="above", strike=80000.0, yes=0.40, no=0.62)],
    )
    # No candidate combines barrier with terminal; the barrier row is flagged.
    assert all(r.get("contract_family") != "barrier_touch" or not r.get("paper_candidate") for r in report["rows"])
    barrier_rows = [r for r in report["rows"] if r.get("contract_family") == "barrier_touch"]
    assert barrier_rows, "barrier row should be surfaced"
    assert any("barrier_vs_terminal_mismatch" in (r.get("hard_blockers") or []) for r in barrier_rows)
    # No cross-venue terminal candidate paired the barrier in.
    for r in report["rows"]:
        legs = r.get("basket_legs") or []
        ids = " ".join(str((l or {}).get("market_id_or_ticker")) for l in legs)
        assert "hit-80000" not in ids or not r.get("paper_candidate")


def test_updown_not_matched_to_terminal_threshold() -> None:
    report = _build(
        polymarket=[_tk("polymarket", obs="interval_start_to_end_change", comp="up", strike=None, ref_start=REF, yes=0.5, no=0.5)],
        kalshi=[_tk("kalshi", comp="above", strike=70000.0, yes=0.40, no=0.62)],
    )
    # The up/down (directional_return) is never combined into the terminal price grid.
    for r in report["rows"]:
        fams = {(l or {}).get("payoff_observation_type") for l in (r.get("basket_legs") or [])}
        assert not ("interval_start_to_end_change" in fams and "point_in_time_at_target" in fams)


def test_native_range_vs_synthetic_range_payoff_vectors() -> None:
    from relative_value.crypto_structural_payoff_arb_scout import (
        _build_state_grid as bg,
        _vector_above,
        _vector_at_or_below,
        _vector_bucket,
    )

    grid = bg([{"threshold_or_strike": 72000.0}, {"threshold_or_strike": 73000.0}])
    # Native range bucket 72000-73000 pays in the middle state.
    native = _vector_bucket(grid, 72000.0, 73000.0)
    # Synthetic range "72000 < P <= 73000" = above(72000) AND not above(73000)
    #   = above(72000) - above(73000) elementwise (since states partition cleanly).
    synthetic = tuple(a - b for a, b in zip(_vector_above(grid, 72000.0), _vector_above(grid, 73000.0)))
    assert native == (0, 1, 0)
    assert synthetic == native
    assert _vector_at_or_below(grid, 73000.0) == (1, 1, 0)


def test_source_basis_buffer_removes_candidate_when_adjusted_net_le_zero() -> None:
    kalshi = [_tk("kalshi", comp="above", strike=70000.0, yes=0.40, no=0.62, src="cf_benchmarks_brti")]
    poly = [_tk("polymarket", comp="above", strike=70000.0, yes=0.45, no=0.55, src="binance")]
    # net ~ +0.0127. With 0 bps it survives; with 200 bps (0.02 edge) it is removed.
    survives = _build(kalshi=kalshi, polymarket=poly, source_basis_buffer_bps=0.0)
    cv = [r for r in survives["rows"] if r["candidate_type"] == "CROSS_VENUE_THRESHOLD_BASIS"]
    assert any(r["paper_candidate"] for r in cv)

    removed = _build(kalshi=kalshi, polymarket=poly, source_basis_buffer_bps=200.0, source_basis_buffer_absolute="BTC=25,ETH=2")
    cv2 = [r for r in removed["rows"] if r["candidate_type"] == "CROSS_VENUE_THRESHOLD_BASIS"]
    assert cv2 and all(not r["paper_candidate"] for r in cv2)
    assert any("no_positive_adjusted_net_edge_after_basis_buffer" in r["hard_blockers"] for r in cv2)
    assert removed["basis_buffer_sensitivity"]["rows_removed_by_buffer"] >= 1
    assert removed["source_basis_buffer_absolute"] == {"BTC": 25.0, "ETH": 2.0}


def test_contract_grammar_counts_and_tiers_present() -> None:
    report = _build(
        kalshi=[_tk("kalshi", comp="above", strike=70000.0, yes=0.40, no=0.62)],
        polymarket=[_tk("polymarket", comp="above", strike=70000.0, yes=0.45, no=0.55)],
    )
    assert report["contract_grammar_counts"].get("terminal_threshold", 0) >= 2
    assert "comparability_tier_counts" in report["summary_counts"]
    cv = [r for r in report["rows"] if r["candidate_type"] == "CROSS_VENUE_THRESHOLD_BASIS"]
    assert cv and cv[0]["comparability_tier"] == "OPERATOR_RELATIVE_VALUE"
    assert cv[0]["contract_family"] == "terminal_threshold"


# ---------------------------------------------------------------------------- #
# Threshold monotonicity covers: YES(>L) + NO(>U)                              #
# ---------------------------------------------------------------------------- #


def _mtk(platform: str, strike: float, *, yes: float | None, no: float | None, yes_bid: float | None = None, src: str | None = None) -> dict:
    row = _tk(platform, comp="above", strike=strike, yes=yes, no=no, src=src)
    row["quote"]["yes_bid"] = yes_bid
    row["quote"]["yes_bid_size"] = 100.0 if yes_bid is not None else None
    return row


def _mono(report: dict) -> list:
    return [r for r in report["rows"] if r.get("candidate_type") == "THRESHOLD_MONOTONICITY_COVER"]


def test_monotonicity_cover_payoff_vector_min1_max2() -> None:
    report = _build(
        polymarket=[_mtk("polymarket", 74600, yes=0.55, no=0.46), _mtk("polymarket", 74800, yes=0.99, no=0.02)],
    )
    cover = _mono(report)[0]
    assert cover["min_payoff"] == 1.0 and cover["max_payoff"] == 2.0
    assert cover["payoff_vector"] == [1, 2, 1]
    assert cover["lower_strike"] == 74600.0 and cover["higher_strike"] == 74800.0


def test_monotonicity_cover_positive_becomes_paper_candidate() -> None:
    report = _build(
        polymarket=[_mtk("polymarket", 74600, yes=0.55, no=0.46), _mtk("polymarket", 74800, yes=0.99, no=0.02)],
    )
    paper = [r for r in _mono(report) if r["paper_candidate"]]
    assert paper, f"expected a monotonicity-cover paper candidate; rows={[(r['net_edge_after_fees'], r['hard_blockers']) for r in _mono(report)]}"
    r = paper[0]
    assert r["net_edge_after_fees"] > 0
    assert r["yes_lower_ask"] == 0.55 and r["no_higher_ask"] == 0.02
    assert report["monotonicity_cover_diagnostics"]["monotonicity_cover_paper_candidates"] >= 1


def test_monotonicity_cover_negative_stays_blocked() -> None:
    # YES(>70000)@0.55 + NO(>71000)@0.51 -> cost > 1 -> no positive net.
    report = _build(
        polymarket=[_mtk("polymarket", 70000, yes=0.55, no=0.46), _mtk("polymarket", 71000, yes=0.50, no=0.51)],
    )
    assert all(not r["paper_candidate"] for r in _mono(report))
    assert any("no_positive_net_edge_after_fees" in r["hard_blockers"] for r in _mono(report))


def test_monotonicity_missing_yes_lower_ask_hard_blocks() -> None:
    report = _build(
        polymarket=[_mtk("polymarket", 74600, yes=None, no=0.46), _mtk("polymarket", 74800, yes=0.99, no=0.02)],
    )
    cover = _mono(report)
    assert cover and all(not r["paper_candidate"] for r in cover)
    assert any("missing_yes_lower_ask" in r["hard_blockers"] for r in cover)
    assert report["monotonicity_cover_diagnostics"]["missing_yes_lower_ask"] >= 1


def test_monotonicity_missing_no_higher_ask_hard_blocks() -> None:
    # Higher market has no NO ask AND no yes_bid -> no complement available.
    report = _build(
        polymarket=[_mtk("polymarket", 74600, yes=0.55, no=0.46), _mtk("polymarket", 74800, yes=0.99, no=None, yes_bid=None)],
    )
    cover = _mono(report)
    assert cover and all(not r["paper_candidate"] for r in cover)
    assert any("missing_no_higher_ask" in r["hard_blockers"] for r in cover)


def test_monotonicity_cross_platform_is_operator_accepted_risk() -> None:
    report = _build(
        kalshi=[_mtk("kalshi", 74600, yes=0.55, no=0.46, src="brti")],
        polymarket=[_mtk("polymarket", 74800, yes=0.99, no=0.02, src="binance")],
    )
    paper = [r for r in _mono(report) if r["paper_candidate"]]
    assert paper and paper[0]["paper_candidate_class"] == "OPERATOR_ACCEPTED_RISK"
    assert "source_index_basis_risk_accepted" in paper[0]["assumptions_accepted"]


def test_monotonicity_same_platform_is_strict_exact() -> None:
    report = _build(
        polymarket=[_mtk("polymarket", 74600, yes=0.55, no=0.46, src="binance"), _mtk("polymarket", 74800, yes=0.99, no=0.02, src="binance")],
    )
    paper = [r for r in _mono(report) if r["paper_candidate"]]
    assert paper and paper[0]["paper_candidate_class"] == "STRICT_EXACT"
    assert paper[0]["strict_exact_arb"] is True


def test_monotonicity_complement_quote_is_labeled_when_used() -> None:
    # Higher market has no direct NO ask but an executable YES bid (0.97) -> NO ask = 0.03.
    report = _build(
        polymarket=[_mtk("polymarket", 74600, yes=0.55, no=0.46), _mtk("polymarket", 74800, yes=0.99, no=None, yes_bid=0.97)],
    )
    paper = [r for r in _mono(report) if r["paper_candidate"]]
    assert paper, "complement-derived NO ask should still allow a candidate"
    r = paper[0]
    assert r["complement_quote_used"] is True
    assert r["no_higher_ask"] == 0.03
    assert "complement_quote_used" in r["assumptions_accepted"]
    assert "limited_depth_operator_size_cap_applied" in r["assumptions_accepted"]
    assert report["monotonicity_cover_diagnostics"]["complement_quote_used"] >= 1


def test_monotonicity_cover_no_midpoint_uses_asks_only() -> None:
    report = _build(
        polymarket=[_mtk("polymarket", 74600, yes=0.55, no=0.46), _mtk("polymarket", 74800, yes=0.99, no=0.02)],
    )
    r = [x for x in _mono(report) if x["paper_candidate"]][0]
    legs = r["basket_legs"]
    # net = 1 - sum(all_in) ; each all_in = ask + fee (asks only, no midpoint).
    total = sum(l["all_in_cost"] for l in legs)
    assert abs(r["net_edge_after_fees"] - (1.0 - total)) < 1e-9
    assert r["yes_lower_ask"] == legs[0]["ask"] and r["no_higher_ask"] == legs[1]["ask"]


def test_no_trading_auth_or_browser_code_in_structural_module() -> None:
    src = Path("relative_value/crypto_structural_payoff_arb_scout.py").read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    code = re.sub(r"(?m)^\s*#.*$", "", code)
    forbidden = [
        r"\bplace_order\b", r"\bsubmit_order\b", r"\bcancel_order\b", r"\bsign_transaction\b",
        r"\bprivate_key\b", r"\bwallet\b", r"\bplaywright\b", r"\bselenium\b", r"\bwebdriver\b",
        r"requests\.(get|post|put|delete|patch)", r"\bhttpx\b", r"\burlopen\b", r"\bAuthorization\b",
    ]
    for pat in forbidden:
        assert re.search(pat, code, re.IGNORECASE) is None, f"forbidden pattern {pat} in structural module"
