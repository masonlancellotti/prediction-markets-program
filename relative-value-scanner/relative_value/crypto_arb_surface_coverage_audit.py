"""Crypto arb surface coverage verifier.

Reads a watcher ``watch_summary.json`` (totals) or a latest scout ``iteration.json``
and tells Mason whether the scanner is actually evaluating every plausible
buy-only arb combination — across contract families, platform pairs/triples, and
candidate types — or whether a class is silently uncovered.

For each candidate type it reports the attempted -> generated -> priced ->
paper funnel and a coverage_status of OK / GAP / EXPECTED_ZERO / NEEDS_DATA, with
the specific reason a zero is (or is not) expected.

Read-only over local report files. No network, no trading, no order/auth/browser.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_KIND = "crypto_arb_surface_coverage_audit_v1"
SCHEMA_VERSION = 1

# Per-candidate-type metadata: platforms, contract families, and whether the type
# is a diagnostic (never a tradable edge source) so zeros are expected.
_CANDIDATE_TYPES: list[dict[str, Any]] = [
    {"candidate_type": "LONG_ONLY_GUARANTEED_PAYOFF", "platforms": "any / cross-venue",
     "contract_families": "terminal_threshold, terminal_range", "diagnostic": False, "needs": "terminal"},
    {"candidate_type": "THRESHOLD_MONOTONICITY_COVER", "platforms": "same venue (Kalshi or Polymarket)",
     "contract_families": "terminal_threshold", "diagnostic": False, "needs": "thresholds_ladder"},
    {"candidate_type": "CROSS_VENUE_THRESHOLD_BASIS", "platforms": "Kalshi×Polymarket(×CDNA)",
     "contract_families": "terminal_threshold", "diagnostic": False, "needs": "cross_venue_threshold"},
    {"candidate_type": "BUCKET_TO_CUMULATIVE_THRESHOLD", "platforms": "Kalshi buckets → Polymarket/CDNA threshold",
     "contract_families": "terminal_range → terminal_threshold", "diagnostic": False, "needs": "buckets"},
    {"candidate_type": "SAME_PAYOFF_CHEAPER_BASKET", "platforms": "cross-venue",
     "contract_families": "terminal_threshold, terminal_range", "diagnostic": False, "needs": "same_vector_cross_venue"},
    {"candidate_type": "UP_DOWN_SAME_WINDOW", "platforms": "Kalshi×Polymarket",
     "contract_families": "directional_return", "diagnostic": False, "needs": "updown"},
    {"candidate_type": "CDNA_FILL_FIRST", "platforms": "CDNA×(Kalshi/Polymarket)",
     "contract_families": "terminal_threshold", "diagnostic": False, "needs": "cdna"},
    {"candidate_type": "THRESHOLD_TO_BUCKET_DIAGNOSTIC", "platforms": "same venue",
     "contract_families": "terminal_threshold", "diagnostic": True, "needs": "thresholds_ladder"},
    {"candidate_type": "MONOTONICITY_VIOLATION", "platforms": "same venue",
     "contract_families": "terminal_threshold", "diagnostic": True, "needs": "thresholds_ladder"},
    {"candidate_type": "DIAGNOSTIC_ONLY_REQUIRES_SHORT", "platforms": "n/a",
     "contract_families": "terminal", "diagnostic": True, "needs": "any"},
    {"candidate_type": "BARRIER_TOUCH_DIAGNOSTIC", "platforms": "isolated",
     "contract_families": "barrier_touch", "diagnostic": True, "needs": "barrier"},
]

_PLATFORM_PAIRS = ("Kalshi×Polymarket", "Kalshi×CDNA", "Polymarket×CDNA", "Kalshi×Polymarket×CDNA")
_CONTRACT_FAMILIES = ("terminal_threshold", "terminal_range", "directional_return", "barrier_touch")


# ---------------------------------------------------------------------------- #
# Public entry points                                                          #
# ---------------------------------------------------------------------------- #


def write_crypto_arb_surface_coverage_audit_files(
    *, json_output: Path, markdown_output: Path, **kwargs: Any
) -> dict[str, Any]:
    report = build_crypto_arb_surface_coverage_audit(**kwargs)
    Path(json_output).parent.mkdir(parents=True, exist_ok=True)
    Path(markdown_output).parent.mkdir(parents=True, exist_ok=True)
    Path(json_output).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    Path(markdown_output).write_text(render_coverage_audit_markdown(report), encoding="utf-8")
    return report


def build_crypto_arb_surface_coverage_audit(
    *, input_report: Path | None = None, latest_iteration_dir: Path | None = None,
    assets: list[str] | None = None, include_cdna: bool = False, generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    source, load_error = _load_source(input_report, latest_iteration_dir)

    cov = source["coverage"]
    grammar = source["grammar"]
    ctc = source["candidate_type_counts"]
    cdna_rows = source["cdna_rows_loaded"]
    cdna_candidates = source["cdna_candidates"]
    top_blocker = source["top_blocker"]
    updown_present = grammar.get("directional_return", 0) > 0 or ctc.get("UP_DOWN_SAME_WINDOW", 0) > 0
    cdna_present = cdna_rows > 0 or cdna_candidates > 0 or (include_cdna and bool(source.get("cdna_supplied")))
    terminal_threshold = grammar.get("terminal_threshold", 0)
    terminal_range = grammar.get("terminal_range", 0)
    barrier_present = grammar.get("barrier_touch", 0) > 0

    matrix = []
    for meta in _CANDIDATE_TYPES:
        entry = cov.get(meta["candidate_type"], {"attempted": 0, "generated": 0, "priced": 0, "paper": 0})
        matrix.append(_classify(
            meta=meta, entry=entry, terminal_threshold=terminal_threshold, terminal_range=terminal_range,
            updown_present=updown_present, cdna_present=cdna_present, barrier_present=barrier_present,
            top_blocker=top_blocker,
        ))

    gaps = [m for m in matrix if m["coverage_status"] == "GAP"]
    expected_zeros = [m for m in matrix if m["coverage_status"] == "EXPECTED_ZERO"]
    needs_data = [m for m in matrix if m["coverage_status"] == "NEEDS_DATA"]
    verdict = "GAPS_FOUND" if gaps else ("NEEDS_DATA" if (needs_data and not any(m["coverage_status"] == "OK" for m in matrix)) else "COVERAGE_OK")

    return {
        "schema_kind": SCHEMA_KIND,
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated.isoformat(),
        "source_kind": source["source_kind"],
        "source_path": source["source_path"],
        "load_error": load_error,
        "assets_requested": [a.strip().upper() for a in (assets or []) if a.strip()],
        "include_cdna": bool(include_cdna),
        "verdict": verdict,
        "gap_count": len(gaps),
        "expected_zero_count": len(expected_zeros),
        "needs_data_count": len(needs_data),
        "gaps": [m["candidate_type"] for m in gaps],
        "coverage_matrix": matrix,
        "contract_family_counts": grammar,
        "platform_pairs_considered": list(_PLATFORM_PAIRS),
        "cdna_participation": {"cdna_rows_loaded": cdna_rows, "cdna_candidates_considered": cdna_candidates,
                               "cdna_present": cdna_present, "cdna_supplied": source.get("cdna_supplied")},
        "top_quote_blocker": top_blocker,
        "safety": {"diagnostic_only": True, "public_read_only": True, "reads_local_reports_only": True,
                   "network_access": False, "orders_or_execution_logic_added": False,
                   "auth_or_account_logic_added": False, "browser_automation_added": False},
    }


# ---------------------------------------------------------------------------- #
# Classification                                                               #
# ---------------------------------------------------------------------------- #


def _classify(*, meta, entry, terminal_threshold, terminal_range, updown_present, cdna_present,
              barrier_present, top_blocker) -> dict[str, Any]:
    ct = meta["candidate_type"]
    attempted = int(entry.get("attempted", 0) or 0)
    generated = int(entry.get("generated", 0) or 0)
    priced = int(entry.get("priced", 0) or 0)
    paper = int(entry.get("paper", 0) or 0)
    diagnostic = meta["diagnostic"]
    status = "NEEDS_DATA"
    why = ""

    if diagnostic:
        # Diagnostics are never tradable edge sources; any count is fine, zero is expected.
        if ct == "BARRIER_TOUCH_DIAGNOSTIC":
            status, why = "EXPECTED_ZERO", "barrier/touch is path-dependent and intentionally isolated from terminal/up-down."
        elif attempted > 0:
            status, why = "OK", "diagnostic surfaced as expected (not a tradable edge source)."
        else:
            status, why = "EXPECTED_ZERO", "no qualifying diagnostic rows this scan; not a tradable edge source."
    elif attempted > 0 and generated > 0:
        status, why = "OK", f"attempted={attempted} -> generated={generated} -> priced={priced} -> paper={paper}."
    elif attempted > 0 and generated == 0:
        if ct == "CDNA_FILL_FIRST":
            status, why = "EXPECTED_ZERO", ("CDNA rows were attempted but produced no rows — no shared-instant compatible "
                                            "Kalshi/Polymarket terminal-threshold partner, or all CDNA rows were stale. "
                                            "CDNA is loaded and attempted, so this is not a coverage gap.")
        elif ct == "CROSS_VENUE_THRESHOLD_BASIS":
            status, why = "GAP", ("cross-venue covers were attempted but none were generated — a sampling/emission "
                                  "bug unless there is genuinely no exact (instant, strike) overlap between venues.")
        elif ct == "BUCKET_TO_CUMULATIVE_THRESHOLD":
            status, why = "EXPECTED_ZERO", ("synthetic bucket covers attempted but none reproduced a complement-venue "
                                            "threshold within max_basket_legs / exhaustive coverage — expected on sparse buckets.")
        else:
            status, why = "GAP", f"{attempted} attempts produced 0 rows — investigate the generator for this class."
    else:  # attempted == 0
        if ct == "UP_DOWN_SAME_WINDOW":
            if updown_present:
                status, why = "GAP", "directional_return / up-down rows exist but UP_DOWN_SAME_WINDOW was never attempted."
            else:
                status, why = "EXPECTED_ZERO", "no cross-platform same-window up/down rows discovered to pair."
        elif ct == "CDNA_FILL_FIRST":
            if cdna_present:
                status, why = "GAP", "CDNA rows are present but CDNA_FILL_FIRST was never attempted."
            else:
                status, why = "EXPECTED_ZERO", "no CDNA evidence supplied/loaded, so no CDNA fill-first to attempt."
        elif ct in ("LONG_ONLY_GUARANTEED_PAYOFF", "CROSS_VENUE_THRESHOLD_BASIS"):
            if terminal_threshold > 0 or terminal_range > 0:
                status, why = "GAP", "terminal rows exist but this class was never attempted at any shared instant."
            else:
                status, why = "NEEDS_DATA", "no terminal rows in this report to attempt over."
        elif ct == "THRESHOLD_MONOTONICITY_COVER":
            status, why = ("NEEDS_DATA" if terminal_threshold >= 2 else "EXPECTED_ZERO",
                           "needs >=2 same-source thresholds at one instant; not determinable from counts alone.")
        elif ct == "SAME_PAYOFF_CHEAPER_BASKET":
            status, why = "EXPECTED_ZERO", "requires two cross-venue instruments with an identical payoff vector — rare."
        elif ct == "BUCKET_TO_CUMULATIVE_THRESHOLD":
            status, why = ("EXPECTED_ZERO" if terminal_range > 0 else "NEEDS_DATA",
                           "needs Kalshi buckets aligning exactly to a complement-venue strike.")
        else:
            status, why = "NEEDS_DATA", "insufficient signal to judge."

    return {
        "candidate_type": ct,
        "platforms": meta["platforms"],
        "contract_families": meta["contract_families"],
        "attempted": attempted, "generated": generated, "priced": priced, "paper_candidate": paper,
        "top_blocker": top_blocker,
        "is_this_expected": status != "GAP",
        "if_zero_why": (why if generated == 0 else ""),
        "coverage_status": status,
        "is_diagnostic": diagnostic,
    }


# ---------------------------------------------------------------------------- #
# Source loading / normalization                                               #
# ---------------------------------------------------------------------------- #


def _load_source(input_report: Path | None, latest_iteration_dir: Path | None) -> tuple[dict[str, Any], str | None]:
    payload, path, kind, err = None, None, "missing", "no_source_provided"
    if latest_iteration_dir is not None:
        it = _find_iteration_json(Path(latest_iteration_dir))
        if it is not None:
            payload, path, kind, err = _read_json(it), str(it), "iteration_report", None
        else:
            err = "no_iteration_json_found"
    if payload is None and input_report is not None:
        p = Path(input_report)
        if p.exists():
            payload, path, kind, err = _read_json(p), str(p), "input_report", None
        else:
            err = "input_report_not_found"

    if not isinstance(payload, dict):
        return _empty_source(path, kind), err

    # Watch summary (has "totals") vs scout iteration report.
    container = payload.get("totals") if isinstance(payload.get("totals"), dict) else payload
    if payload.get("totals"):
        kind = "watch_summary"
    cov_list = container.get("candidate_generation_coverage") or payload.get("candidate_generation_coverage") or []
    coverage = {str(e.get("candidate_class")): {
        "attempted": e.get("attempted", 0), "generated": e.get("generated", 0),
        "priced": e.get("priced", 0), "paper": e.get("paper", 0)} for e in cov_list if isinstance(e, dict)}
    grammar = (payload.get("contract_grammar_counts") or container.get("contract_grammar_counts")
               or container.get("contract_family_counts") or {})
    ctc = container.get("candidate_type_counts") or payload.get("candidate_type_counts") or {}
    cdna = container.get("cdna_participation") or {}
    cdna_rows = int(cdna.get("cdna_rows_loaded", 0) or 0)
    cdna_candidates = int(cdna.get("cdna_candidates_considered", 0) or 0)
    cdna_supplied = bool(cdna.get("cdna_supplied"))
    if not cdna and payload.get("cdna_fill_first_candidates"):
        cdna_candidates = len(payload.get("cdna_fill_first_candidates") or [])
    qsd = container.get("quote_side_diagnostics") or payload.get("quote_side_diagnostic_counts") or {}
    top_blocker = max(qsd.items(), key=lambda kv: kv[1])[0] if qsd else None

    return {
        "source_kind": kind, "source_path": path, "coverage": coverage, "grammar": dict(grammar),
        "candidate_type_counts": dict(ctc), "cdna_rows_loaded": cdna_rows, "cdna_candidates": cdna_candidates,
        "cdna_supplied": cdna_supplied, "top_blocker": top_blocker,
    }, (None if coverage else (err or "no_candidate_generation_coverage_in_source"))


def _empty_source(path, kind) -> dict[str, Any]:
    return {"source_kind": kind, "source_path": path, "coverage": {}, "grammar": {}, "candidate_type_counts": {},
            "cdna_rows_loaded": 0, "cdna_candidates": 0, "cdna_supplied": False, "top_blocker": None}


def _find_iteration_json(d: Path) -> Path | None:
    if d.is_file() and d.name.endswith(".json"):
        return d
    direct = d / "iteration.json"
    if direct.exists():
        return direct
    candidates = sorted(d.glob("*/iteration.json"))
    return candidates[-1] if candidates else None


# ---------------------------------------------------------------------------- #
# Markdown                                                                      #
# ---------------------------------------------------------------------------- #


def render_coverage_audit_markdown(report: dict[str, Any]) -> str:
    matrix = report.get("coverage_matrix") or []
    lines = [
        "# Crypto Arb Surface Coverage Audit",
        "",
        "Is the scanner actually evaluating every plausible buy-only arb combination? "
        "Per candidate type: attempted -> generated -> priced -> paper, with a coverage verdict.",
        "",
        "## 1. Executive Summary",
        "",
        f"- source: `{report.get('source_kind')}` (`{report.get('source_path')}`)",
        f"- **verdict: {report.get('verdict')}**  gaps: `{report.get('gap_count')}`  "
        f"expected_zeros: `{report.get('expected_zero_count')}`  needs_data: `{report.get('needs_data_count')}`",
        f"- gaps: `{', '.join(report.get('gaps') or []) or 'none'}`",
        f"- top quote blocker: `{report.get('top_quote_blocker') or 'none'}`",
    ]
    if report.get("load_error"):
        lines.append(f"- load_error: `{report.get('load_error')}`")

    lines += [
        "",
        "## 2. Coverage Matrix By Candidate Type",
        "",
        "| Candidate type | Attempted | Generated | Priced | Paper | Status | Expected? |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for m in matrix:
        lines.append(
            f"| {_md(m['candidate_type'])} | {m['attempted']} | {m['generated']} | {m['priced']} | "
            f"{m['paper_candidate']} | {m['coverage_status']} | {'yes' if m['is_this_expected'] else 'NO'} |"
        )

    lines += ["", "## 3. Platform Pair / Triple Coverage", "",
              "Buy-only baskets are searched across these venue combinations (asks only, no shorting):", ""]
    for p in report.get("platform_pairs_considered") or []:
        lines.append(f"- {p}")

    lines += ["", "## 4. Contract Family Coverage", "", "| Family | Rows |", "|---|---:|"]
    fam = report.get("contract_family_counts") or {}
    if not fam:
        lines.append("| (none in source) | 0 |")
    for f in _CONTRACT_FAMILIES:
        if f in fam:
            lines.append(f"| {f} | {fam[f]} |")
    for f, n in fam.items():
        if f not in _CONTRACT_FAMILIES:
            lines.append(f"| {_md(f)} | {n} |")

    lines += ["", "## 5. Candidate Generation Gaps", ""]
    gaps = [m for m in matrix if m["coverage_status"] == "GAP"]
    if not gaps:
        lines.append("- none — no real generation gaps detected.")
    for m in gaps:
        lines.append(f"- **{_md(m['candidate_type'])}** (attempted={m['attempted']}, generated={m['generated']}): {_md(m['if_zero_why'])}")

    lines += ["", "## 6. Expected Zeros", ""]
    ez = [m for m in matrix if m["coverage_status"] in ("EXPECTED_ZERO", "NEEDS_DATA")]
    if not ez:
        lines.append("- none.")
    for m in ez:
        lines.append(f"- {_md(m['candidate_type'])} ({m['coverage_status']}): {_md(m['if_zero_why'])}")

    cdna = report.get("cdna_participation") or {}
    lines += [
        "",
        "## 7. Quote Coverage Blockers",
        "",
        f"- top quote blocker across buy-only rows: `{report.get('top_quote_blocker') or 'none'}`",
        "",
        "## 8. CDNA Participation",
        "",
        f"- cdna_present: `{cdna.get('cdna_present')}`  cdna_rows_loaded: `{cdna.get('cdna_rows_loaded')}`  "
        f"cdna_candidates_considered: `{cdna.get('cdna_candidates_considered')}`",
        "",
        "## 9. What To Fix Next",
        "",
    ]
    if not gaps:
        lines.append("- No generation gaps. Remaining zeros are expected (no peers/rows) or need richer evidence (supply CDNA, run during active up/down windows).")
    for m in gaps:
        if m["candidate_type"] == "CROSS_VENUE_THRESHOLD_BASIS":
            lines.append("- Cross-venue attempted>0 but generated=0: verify the pair emitter isn't dropping cross-venue covers (per-class sample cap) and that instant/strike keys align.")
        elif m["candidate_type"] == "UP_DOWN_SAME_WINDOW":
            lines.append("- Up/down rows exist but none attempted: confirm the directional-return lane runs and that reference_start + target_instant + interval match across venues.")
        elif m["candidate_type"] == "CDNA_FILL_FIRST":
            lines.append("- CDNA rows present but no fill-first attempts: confirm CDNA legs enter the candidate generator and are classed CDNA_FILL_FIRST.")
        else:
            lines.append(f"- {m['candidate_type']}: {m['if_zero_why']}")

    lines += ["", "## Safety", "",
              "- diagnostic_only: `true`  public_read_only: `true`  network_access: `false`  browser_automation_added: `false`"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------- #
# Helpers                                                                       #
# ---------------------------------------------------------------------------- #


def _read_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")
