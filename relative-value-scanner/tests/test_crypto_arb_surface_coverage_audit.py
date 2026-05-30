"""Tests for the crypto arb surface coverage verifier (read-only; no network)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import scan
from relative_value.crypto_arb_surface_coverage_audit import (
    build_crypto_arb_surface_coverage_audit,
    render_coverage_audit_markdown,
    write_crypto_arb_surface_coverage_audit_files,
)


def _cov(**over):
    base = {
        "LONG_ONLY_GUARANTEED_PAYOFF": {"attempted": 100, "generated": 10, "priced": 5, "paper": 0},
        "THRESHOLD_MONOTONICITY_COVER": {"attempted": 40, "generated": 6, "priced": 2, "paper": 0},
        "CROSS_VENUE_THRESHOLD_BASIS": {"attempted": 72, "generated": 0, "priced": 0, "paper": 0},
        "BUCKET_TO_CUMULATIVE_THRESHOLD": {"attempted": 10, "generated": 0, "priced": 0, "paper": 0},
        "SAME_PAYOFF_CHEAPER_BASKET": {"attempted": 0, "generated": 0, "priced": 0, "paper": 0},
        "UP_DOWN_SAME_WINDOW": {"attempted": 0, "generated": 0, "priced": 0, "paper": 0},
        "CDNA_FILL_FIRST": {"attempted": 0, "generated": 0, "priced": 0, "paper": 0},
        "THRESHOLD_TO_BUCKET_DIAGNOSTIC": {"attempted": 20, "generated": 20, "priced": 0, "paper": 0},
        "MONOTONICITY_VIOLATION": {"attempted": 3, "generated": 3, "priced": 0, "paper": 0},
        "BARRIER_TOUCH_DIAGNOSTIC": {"attempted": 0, "generated": 0, "priced": 0, "paper": 0},
    }
    base.update(over)
    return [{"candidate_class": k, **v} for k, v in base.items()]


def _iter_report(tmp_path, *, coverage=None, grammar=None, cdna_rows=0, name="iteration.json"):
    payload = {
        "schema_kind": "crypto_structural_payoff_arb_scout_v1",
        "candidate_generation_coverage": coverage if coverage is not None else _cov(),
        "contract_grammar_counts": grammar if grammar is not None else {"terminal_threshold": 117, "terminal_range": 385},
        "candidate_type_counts": {}, "summary_counts": {},
        "quote_side_diagnostic_counts": {"missing_kalshi_no_ask": 50, "missing_polymarket_no_ask": 20},
        "cdna_fill_first_candidates": [],
    }
    d = tmp_path / "iterdir"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(payload), encoding="utf-8")
    return d


def _bytype(report):
    return {m["candidate_type"]: m for m in report["coverage_matrix"]}


def test_detects_missing_up_down_coverage(tmp_path: Path) -> None:
    # directional_return rows exist but UP_DOWN attempted=0 -> GAP.
    d = _iter_report(tmp_path, grammar={"terminal_threshold": 50, "directional_return": 4})
    rep = build_crypto_arb_surface_coverage_audit(latest_iteration_dir=d)
    m = _bytype(rep)["UP_DOWN_SAME_WINDOW"]
    assert m["coverage_status"] == "GAP" and m["is_this_expected"] is False
    assert "UP_DOWN_SAME_WINDOW" in rep["gaps"]


def test_no_updown_rows_is_expected_zero(tmp_path: Path) -> None:
    d = _iter_report(tmp_path, grammar={"terminal_threshold": 50})  # no directional_return
    m = _bytype(build_crypto_arb_surface_coverage_audit(latest_iteration_dir=d))["UP_DOWN_SAME_WINDOW"]
    assert m["coverage_status"] == "EXPECTED_ZERO"


def test_detects_cdna_present_but_no_attempts(tmp_path: Path) -> None:
    payload = {
        "candidate_generation_coverage": _cov(),
        "contract_grammar_counts": {"terminal_threshold": 50, "terminal_range": 10},
        "cdna_fill_first_candidates": [{"asset": "BTC"}, {"asset": "ETH"}],  # CDNA present
        "quote_side_diagnostic_counts": {},
    }
    d = tmp_path / "iterdir"
    d.mkdir(parents=True, exist_ok=True)
    (d / "iteration.json").write_text(json.dumps(payload), encoding="utf-8")
    m = _bytype(build_crypto_arb_surface_coverage_audit(latest_iteration_dir=d, include_cdna=True))["CDNA_FILL_FIRST"]
    assert m["coverage_status"] == "GAP"


def test_no_cdna_is_expected_zero(tmp_path: Path) -> None:
    d = _iter_report(tmp_path, cdna_rows=0)
    m = _bytype(build_crypto_arb_surface_coverage_audit(latest_iteration_dir=d, include_cdna=False))["CDNA_FILL_FIRST"]
    assert m["coverage_status"] == "EXPECTED_ZERO"


def test_detects_cross_venue_generated_zero_bug(tmp_path: Path) -> None:
    d = _iter_report(tmp_path)  # cross-venue attempted=72 generated=0
    m = _bytype(build_crypto_arb_surface_coverage_audit(latest_iteration_dir=d))["CROSS_VENUE_THRESHOLD_BASIS"]
    assert m["coverage_status"] == "GAP" and m["attempted"] > 0 and m["generated"] == 0


def test_separates_expected_zero_from_real_gap(tmp_path: Path) -> None:
    d = _iter_report(tmp_path)
    bt = _bytype(build_crypto_arb_surface_coverage_audit(latest_iteration_dir=d))
    assert bt["BUCKET_TO_CUMULATIVE_THRESHOLD"]["coverage_status"] == "EXPECTED_ZERO"  # gen=0 but expected
    assert bt["CROSS_VENUE_THRESHOLD_BASIS"]["coverage_status"] == "GAP"               # gen=0 = bug
    assert bt["BARRIER_TOUCH_DIAGNOSTIC"]["coverage_status"] == "EXPECTED_ZERO"
    assert bt["LONG_ONLY_GUARANTEED_PAYOFF"]["coverage_status"] == "OK"


def test_reads_watch_summary_totals(tmp_path: Path) -> None:
    ws = tmp_path / "watch_summary.json"
    ws.write_text(json.dumps({"schema_kind": "crypto_structural_watch_summary_v1", "totals": {
        "candidate_generation_coverage": _cov(),
        "cdna_participation": {"cdna_rows_loaded": 6, "cdna_candidates_considered": 0, "cdna_supplied": True},
        "quote_side_diagnostics": {"missing_kalshi_no_ask": 100},
        "candidate_type_counts": {},
    }}), encoding="utf-8")
    rep = build_crypto_arb_surface_coverage_audit(input_report=ws, include_cdna=True)
    assert rep["source_kind"] == "watch_summary"
    # CDNA present via participation -> GAP.
    assert _bytype(rep)["CDNA_FILL_FIRST"]["coverage_status"] == "GAP"


def test_markdown_and_files_written(tmp_path: Path) -> None:
    d = _iter_report(tmp_path)
    write_crypto_arb_surface_coverage_audit_files(
        latest_iteration_dir=d, assets=["BTC"], json_output=tmp_path / "c.json", markdown_output=tmp_path / "c.md",
    )
    md = (tmp_path / "c.md").read_text(encoding="utf-8")
    for header in (
        "## 1. Executive Summary", "## 2. Coverage Matrix By Candidate Type", "## 3. Platform Pair / Triple Coverage",
        "## 4. Contract Family Coverage", "## 5. Candidate Generation Gaps", "## 6. Expected Zeros",
        "## 7. Quote Coverage Blockers", "## 8. CDNA Participation", "## 9. What To Fix Next",
    ):
        assert header in md, f"missing section {header}"
    assert json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))["schema_kind"] == "crypto_arb_surface_coverage_audit_v1"


def test_missing_source_is_clean(tmp_path: Path) -> None:
    rep = build_crypto_arb_surface_coverage_audit(input_report=tmp_path / "nope.json")
    assert rep["load_error"] is not None
    assert "No generation gaps" in render_coverage_audit_markdown(rep) or rep["gap_count"] == 0


def test_scan_cli_runs_coverage_audit(tmp_path: Path) -> None:
    d = _iter_report(tmp_path)
    rc = scan.main(["crypto-arb-surface-coverage-audit", "--latest-iteration-dir", str(d),
                    "--json-output", str(tmp_path / "a.json"), "--markdown-output", str(tmp_path / "a.md")])
    assert rc == 0 and (tmp_path / "a.json").exists()


def test_no_trading_auth_or_browser_code() -> None:
    src = Path("relative_value/crypto_arb_surface_coverage_audit.py").read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    code = re.sub(r"(?m)^\s*#.*$", "", code)
    for pat in (r"\bplace_order\b", r"\bsubmit_order\b", r"\bplaywright\b", r"\bselenium\b", r"\bwebdriver\b",
                r"requests\.(get|post)", r"\bhttpx\b", r"\burlopen\b", r"\bAuthorization\b", r"\bapi_key\b"):
        assert re.search(pat, code, re.IGNORECASE) is None, f"forbidden {pat}"


def test_cdna_attempted_and_generated_is_ok(tmp_path: Path) -> None:
    # CDNA loaded + matched a compatible partner -> attempted>0, generated>0 -> OK (gap closed).
    d = _iter_report(tmp_path, coverage=_cov(
        CDNA_FILL_FIRST={"attempted": 26, "generated": 26, "priced": 10, "paper": 1}))
    rep = build_crypto_arb_surface_coverage_audit(latest_iteration_dir=d, include_cdna=True)
    m = _bytype(rep)["CDNA_FILL_FIRST"]
    assert m["coverage_status"] == "OK" and m["is_this_expected"] is True
    assert "CDNA_FILL_FIRST" not in rep["gaps"]


def test_cdna_attempted_but_zero_generated_is_expected_zero(tmp_path: Path) -> None:
    # CDNA loaded + attempted but no shared-instant partner / all stale -> EXPECTED_ZERO, not a gap.
    d = _iter_report(tmp_path, coverage=_cov(
        CDNA_FILL_FIRST={"attempted": 26, "generated": 0, "priced": 0, "paper": 0}))
    rep = build_crypto_arb_surface_coverage_audit(latest_iteration_dir=d, include_cdna=True)
    m = _bytype(rep)["CDNA_FILL_FIRST"]
    assert m["coverage_status"] == "EXPECTED_ZERO" and m["is_this_expected"] is True
    assert "CDNA_FILL_FIRST" not in rep["gaps"]
