"""Tests for the canonical, deduped crypto paper-candidate audit pack (no network)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import scan
from relative_value.extract_crypto_paper_candidate_audit_pack import (
    build_crypto_paper_candidate_audit_pack,
    render_audit_pack_markdown,
    write_crypto_paper_candidate_audit_pack_files,
)


def _leg(platform, side, mid, ask, fee=0.02, size=75.0, source="brti", shape="point_in_time_threshold", **extra):
    leg = {
        "platform": platform, "side": side, "market_id_or_ticker": mid, "market_shape": shape,
        "payoff_observation_type": "point_in_time_at_target",
        "ask": ask, "fee": fee, "all_in_cost": None if ask is None else round(ask + fee, 8),
        "available_size_or_cap": size, "source_index": source,
        "quote_timestamp": "2026-05-30T09:59:00Z", "depth_status": "top",
        "complement_used": False, "complement_source": None, "hard_blockers": [] if ask is not None else ["missing_ask"],
        "payoff_vector": [1, 1, 1],
    }
    leg.update(extra)
    return leg


def _paper_row(**over):
    legs = over.pop("legs", [_leg("kalshi", "YES", "K-A", 0.40), _leg("kalshi", "NO", "K-B", 0.50)])
    total = round(sum(l["all_in_cost"] for l in legs if l["all_in_cost"] is not None), 8)
    row = {
        "asset": "BTC", "candidate_type": "LONG_ONLY_GUARANTEED_PAYOFF",
        "paper_candidate": True, "paper_candidate_class": "STRICT_EXACT",
        "candidate_execution_type": "BUY_ONLY", "tradable_buy_only": True, "requires_short_or_sell": False,
        "target_instant_utc": "2026-05-30T10:00:00+00:00", "state_grid": ["[-inf,1)", "[1,2)", "[2,+inf)"],
        "payoff_vector": [1, 1, 1], "min_payoff": 1.0, "max_payoff": None,
        "total_cost_after_fees": total, "net_edge_after_fees": round(1.0 - total, 8),
        "adjusted_net_edge_after_fees": round(1.0 - total, 8), "source_basis_buffer": 0.0,
        "assumptions_accepted": [], "complement_quote_used": False, "hard_blockers": [],
        "quote_side_diagnostics": [], "candidate_action": "PAPER_TEST_OR_MANUAL_MICRO_TEST",
        "basket_legs": legs,
    }
    row.update(over)
    return row


def _summary_copy(net=0.06, asset="BTC", ctype="LONG_ONLY_GUARANTEED_PAYOFF", instant="2026-05-30T10:00:00+00:00"):
    return {
        "asset": asset, "candidate_type": ctype, "paper_candidate": True,
        "target_instant_utc": instant, "net_edge_after_fees": net, "adjusted_net_edge_after_fees": net,
        "hard_blockers": [], "near_miss": False,
    }


def _short_row():
    return {
        "asset": "ETH", "candidate_type": "THRESHOLD_TO_BUCKET_DIAGNOSTIC", "paper_candidate": False,
        "paper_candidate_class": "NONE", "candidate_execution_type": "REQUIRES_SHORT",
        "tradable_buy_only": False, "requires_short_or_sell": True,
        "target_instant_utc": "2026-05-30T10:00:00+00:00", "state_grid": [], "payoff_vector": [],
        "min_payoff": None, "total_cost_after_fees": None, "net_edge_after_fees": None,
        "adjusted_net_edge_after_fees": None, "assumptions_accepted": [],
        "hard_blockers": ["requires_short_or_not_guaranteed"], "quote_side_diagnostics": [], "basket_legs": [],
    }


def _write_watch_dir(tmp_path: Path, rows: list[dict[str, Any]], ts: str = "20260530T100000Z",
                     summary=None, summary_counts=None) -> Path:
    watch = tmp_path / "watch"
    iter_dir = watch / ts
    iter_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_kind": "crypto_structural_payoff_arb_scout_v1", "rows": rows,
        "top_buy_only_near_misses": summary or [],
        "summary_counts": {"top_buy_only_near_misses": summary_counts or []},
        "safety": {},
    }
    (iter_dir / "iteration.json").write_text(json.dumps(report), encoding="utf-8")
    return watch


def test_extracts_candidates_from_fixture(tmp_path: Path) -> None:
    watch = _write_watch_dir(tmp_path, [_paper_row(), _short_row()])
    pack = build_crypto_paper_candidate_audit_pack(watch_dir=watch)
    assert pack["unique_candidates"] == 1  # short-required row is excluded
    c = pack["candidates"][0]
    assert c["asset"] == "BTC" and c["candidate_type"] == "LONG_ONLY_GUARANTEED_PAYOFF"
    assert len(c["basket_legs"]) == 2 and c["basket_legs"][0]["market_id_or_ticker"] == "K-A"
    assert c["validation"]["all_passed"] is True
    assert c["verdict"] == "VALID_FOR_PAPER_REVIEW"


def test_dedupes_rows_vs_summary_copies(tmp_path: Path) -> None:
    # Canonical row + its two summary copies in one iteration -> 1 unique, 2 ignored.
    watch = _write_watch_dir(
        tmp_path, [_paper_row()],
        summary=[_summary_copy(net=0.06)],
        summary_counts=[_summary_copy(net=0.06)],
    )
    pack = build_crypto_paper_candidate_audit_pack(watch_dir=watch)
    assert pack["canonical_rows_seen"] == 1
    assert pack["summary_copies_seen"] == 2
    assert pack["naive_all_paths_total"] == 3
    assert pack["unique_candidates"] == 1
    assert pack["duplicates_ignored_count"] == 2
    assert pack["candidates"][0]["summary_duplicate_count"] == 2


def test_per_iteration_rows_are_distinct(tmp_path: Path) -> None:
    # Same basket in two iterations -> two distinct canonical candidates (key includes iter ts).
    _write_watch_dir(tmp_path, [_paper_row()], ts="20260530T100000Z")
    _write_watch_dir(tmp_path, [_paper_row(net_edge_after_fees=0.08, adjusted_net_edge_after_fees=0.08)], ts="20260530T100500Z")
    pack = build_crypto_paper_candidate_audit_pack(watch_dir=tmp_path / "watch")
    assert pack["unique_candidates"] == 2


def test_recomputes_net_edge(tmp_path: Path) -> None:
    watch = _write_watch_dir(tmp_path, [_paper_row()])
    c = build_crypto_paper_candidate_audit_pack(watch_dir=watch)["candidates"][0]
    rec = c["validation"]["recomputed"]
    assert rec["total_cost_after_fees"] == 0.94 and rec["min_payoff"] == 1.0 and rec["net_edge_after_fees"] == 0.06
    names = {ch["check"]: ch["passed"] for ch in c["validation"]["checks"]}
    assert names["net_edge_recomputed_matches"] and names["total_cost_recomputed_matches"]


def test_detects_missing_ask(tmp_path: Path) -> None:
    bad = _paper_row(legs=[_leg("kalshi", "YES", "K-A", 0.40), _leg("polymarket", "NO", "P-B", None)])
    watch = _write_watch_dir(tmp_path, [bad])
    c = build_crypto_paper_candidate_audit_pack(watch_dir=watch)["candidates"][0]
    assert "no_missing_ask" in c["validation"]["hard_failures"]
    assert c["verdict"] == "INVALID_RECOMPUTE_FAIL"


def test_detects_short_required_row_and_excludes_it(tmp_path: Path) -> None:
    watch = _write_watch_dir(tmp_path, [_short_row()])
    pack = build_crypto_paper_candidate_audit_pack(watch_dir=watch)
    assert pack["unique_candidates"] == 0 and pack["candidates"] == []


def test_detects_zero_width_state_grid(tmp_path: Path) -> None:
    bad = _paper_row(state_grid=["[-inf,1)", "[5,5)", "[5,+inf)"])
    watch = _write_watch_dir(tmp_path, [bad])
    c = build_crypto_paper_candidate_audit_pack(watch_dir=watch)["candidates"][0]
    assert "no_zero_width_state_grid_intervals" in c["validation"]["boundary_flags"]
    assert c["verdict"] == "NEEDS_BOUNDARY_REVIEW"


def test_detects_boundary_inclusivity_risk(tmp_path: Path) -> None:
    legs = [
        _leg("kalshi", "NO", "KXBTC-26MAY3012-B73750", 0.50, shape="range_bucket"),
        _leg("polymarket", "NO", "bitcoin-above-73800-on-may-30-2026", 0.40, shape="point_in_time_threshold"),
    ]
    watch = _write_watch_dir(tmp_path, [_paper_row(legs=legs)])
    c = build_crypto_paper_candidate_audit_pack(watch_dir=watch)["candidates"][0]
    assert "no_boundary_inclusivity_risk" in c["validation"]["boundary_flags"]
    assert c["verdict"] == "NEEDS_BOUNDARY_REVIEW"


def test_detects_missing_canonical_rows_surfaces_summary_only(tmp_path: Path) -> None:
    # Paper candidate ONLY in summary (no canonical row) -> surfaced + flagged INVALID_DUPLICATE.
    watch = _write_watch_dir(tmp_path, [], summary=[_summary_copy(net=0.06)])
    pack = build_crypto_paper_candidate_audit_pack(watch_dir=watch)
    assert pack["canonical_rows_seen"] == 0 and pack["unique_candidates"] == 1
    c = pack["candidates"][0]
    assert c["source"] == "summary_only"
    assert c["verdict"] == "INVALID_DUPLICATE"
    assert "from_canonical_rows_not_summary_only" in c["validation"]["review_flags"]


def test_cross_source_flags_basis_and_source_mismatch(tmp_path: Path) -> None:
    legs = [_leg("kalshi", "YES", "K-A", 0.40, source="brti"), _leg("polymarket", "NO", "P-B", 0.50, source="binance")]
    # no basis listed -> hard failure on basis check.
    watch = _write_watch_dir(tmp_path, [_paper_row(legs=legs, assumptions_accepted=[])])
    c = build_crypto_paper_candidate_audit_pack(watch_dir=watch)["candidates"][0]
    assert "cross_source_basis_listed" in c["validation"]["hard_failures"]
    # basis listed -> passes basis, but source mismatch is a review flag -> boundary review.
    watch2 = _write_watch_dir(tmp_path / "two", [_paper_row(legs=legs, assumptions_accepted=["source_index_mismatch"])])
    c2 = build_crypto_paper_candidate_audit_pack(watch_dir=watch2)["candidates"][0]
    assert "cross_source_basis_listed" not in c2["validation"]["hard_failures"]
    assert "no_source_index_mismatch" in c2["validation"]["boundary_flags"]
    assert c2["verdict"] == "NEEDS_BOUNDARY_REVIEW"


def test_missing_watch_dir_is_clean(tmp_path: Path) -> None:
    pack = build_crypto_paper_candidate_audit_pack(watch_dir=tmp_path / "does_not_exist")
    assert pack["watch_dir_exists"] is False and pack["unique_candidates"] == 0
    assert "No canonical PAPER_CANDIDATE rows found" in render_audit_pack_markdown(pack)


def test_markdown_and_files_written(tmp_path: Path) -> None:
    watch = _write_watch_dir(tmp_path, [_paper_row()], summary=[_summary_copy()])
    write_crypto_paper_candidate_audit_pack_files(
        watch_dir=watch, json_output=tmp_path / "p.json", markdown_output=tmp_path / "p.md",
    )
    md = (tmp_path / "p.md").read_text(encoding="utf-8")
    for header in (
        "## 1. Summary", "## 2. Unique Candidates", "## 3. Duplicate Rows Ignored",
        "## 4. Candidate Leg Details", "## 5. Payoff Vector Recomputation", "## 6. Fee Recomputation",
        "## 7. Boundary / Inclusivity Review Flags", "## 8. Source / Basis Assumptions",
        "## 9. Verdict Per Candidate",
    ):
        assert header in md, f"missing section {header}"
    payload = json.loads((tmp_path / "p.json").read_text(encoding="utf-8"))
    assert payload["schema_kind"] == "crypto_paper_candidate_audit_pack_v1"


def test_scan_cli_runs_audit_pack(tmp_path: Path) -> None:
    watch = _write_watch_dir(tmp_path, [_paper_row()])
    rc = scan.main([
        "extract-crypto-paper-candidate-audit-pack",
        "--watch-dir", str(watch),
        "--json-output", str(tmp_path / "a.json"),
        "--markdown-output", str(tmp_path / "a.md"),
    ])
    assert rc == 0 and (tmp_path / "a.json").exists()


def test_no_trading_auth_or_browser_code_in_audit_pack_module() -> None:
    src = Path("relative_value/extract_crypto_paper_candidate_audit_pack.py").read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    code = re.sub(r"(?m)^\s*#.*$", "", code)
    forbidden = [
        r"\bplace_order\b", r"\bsubmit_order\b", r"\bcancel_order\b", r"\bsign_transaction\b",
        r"\bprivate_key\b", r"\bwallet\b", r"\bplaywright\b", r"\bselenium\b", r"\bwebdriver\b",
        r"requests\.(get|post|put|delete|patch)", r"\bhttpx\b", r"\burlopen\b", r"\bAuthorization\b",
        r"\bsmtp\b", r"\bslack\b", r"\bwebhook\b", r"\bnotify\b",
    ]
    for pat in forbidden:
        assert re.search(pat, code, re.IGNORECASE) is None, f"forbidden pattern {pat} in audit-pack module"
