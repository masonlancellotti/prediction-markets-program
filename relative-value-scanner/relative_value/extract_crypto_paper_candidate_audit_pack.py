"""Canonical, deduped audit pack for crypto structural-arb PAPER_CANDIDATE rows.

Reads watcher iteration reports (``<watch_dir>/<timestamp>/iteration.json``) and
builds the *canonical* set of unique paper candidates, then independently
re-validates each one and assigns a verdict.

Canonical-vs-summary discipline (this is the whole point):
  - A unique candidate must come from the iteration ``rows`` array.
  - The same candidate is *also* copied into ``top_buy_only_near_misses`` and
    ``summary_counts.top_buy_only_near_misses``. Those copies are reported as
    "duplicate rows ignored" and never counted as unique — unless NO canonical
    row exists for them, in which case the summary copy is surfaced (flagged
    ``source_only_summary`` -> verdict INVALID_DUPLICATE).
  - Dedup key: iteration timestamp + candidate_type + asset + target_instant_utc
    + basket leg IDs/sides + net_edge_after_fees.

Re-validation per candidate: recompute total cost from leg all-in, net edge from
min payoff, min payoff from the payoff vector; verify hard_blockers empty, all
legs buy-only, no short/sell, no midpoint; flag zero-width state-grid intervals,
source_index_mismatch, and boundary/inclusivity risk (a range-bucket edge close
to a threshold).

Strict scope: read-only over local report files. No network, no trading, no
order/cancel/account/auth/session/wallet/signing/browser/proxy code.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_KIND = "crypto_paper_candidate_audit_pack_v1"
SCHEMA_VERSION = 1

_FLOAT_TOL = 1e-6
# Two strike-like edges within this relative distance flag boundary/inclusivity risk.
_BOUNDARY_REL_TOL = 0.0025
_BOUNDARY_ABS_TOL = 0.0

VERDICT_VALID = "VALID_FOR_PAPER_REVIEW"
VERDICT_BOUNDARY = "NEEDS_BOUNDARY_REVIEW"
VERDICT_DUPLICATE = "INVALID_DUPLICATE"
VERDICT_RECOMPUTE_FAIL = "INVALID_RECOMPUTE_FAIL"

_SUMMARY_PATHS = ("top_buy_only_near_misses", "summary_counts.top_buy_only_near_misses")

_MISSING_ASK_LABELS = {
    "missing_ask", "missing_yes_lower_ask", "missing_no_higher_ask",
    "missing_lower_yes_ask", "missing_higher_no_ask",
    "missing_partner_yes_ask", "missing_partner_no_ask", "missing_partner_complement_ask",
    "missing_kalshi_yes_ask", "missing_kalshi_no_ask",
    "missing_polymarket_yes_ask", "missing_polymarket_no_ask",
    "missing_cdna_display_yes", "missing_cdna_display_no",
    "missing_bucket_leg_ask", "missing_cdna_display_price", "missing_quote_depth",
}
_REQUIRES_SHORT_LABELS = {"requires_short_or_not_guaranteed", "threshold_to_bucket_requires_short"}
_BASIS_ASSUMPTIONS = {"source_index_mismatch", "source_index_basis_risk_accepted", "source_mismatch", "basis_risk_review_required"}

# Checks whose failure invalidates the recompute (hard); the rest are review-only.
_HARD_CHECKS = {
    "min_payoff_matches_vector", "total_cost_recomputed_matches", "net_edge_recomputed_matches",
    "all_in_equals_ask_plus_fee_no_midpoint", "net_edge_positive", "adjusted_net_edge_positive",
    "no_missing_ask", "no_stale_quote", "buy_only", "no_short_or_sell_required",
    "no_midpoint_used", "cross_source_basis_listed", "paper_only_if_no_hard_blockers",
}


# ---------------------------------------------------------------------------- #
# Public entry points                                                          #
# ---------------------------------------------------------------------------- #


def write_crypto_paper_candidate_audit_pack_files(
    *, watch_dir: Path, json_output: Path, markdown_output: Path, generated_at: datetime | None = None
) -> dict[str, Any]:
    report = build_crypto_paper_candidate_audit_pack(watch_dir=watch_dir, generated_at=generated_at)
    Path(json_output).parent.mkdir(parents=True, exist_ok=True)
    Path(markdown_output).parent.mkdir(parents=True, exist_ok=True)
    Path(json_output).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    Path(markdown_output).write_text(render_audit_pack_markdown(report), encoding="utf-8")
    return report


def build_crypto_paper_candidate_audit_pack(
    *, watch_dir: Path, generated_at: datetime | None = None
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    watch_dir = Path(watch_dir)

    iterations = list(_iter_iteration_reports(watch_dir))

    # 1. Canonical candidates from iteration `rows`.
    canonical_by_key: dict[str, dict[str, Any]] = {}
    canonical_rows_seen = 0
    for iter_ts, source_file, report in iterations:
        for row in report.get("rows") or []:
            if not row.get("paper_candidate"):
                continue
            canonical_rows_seen += 1
            cand = _extract_candidate(row, iter_ts, source_file, source="canonical_rows")
            key = cand["dedup_key"]
            if key in canonical_by_key:
                canonical_by_key[key]["occurrences"] += 1
            else:
                cand["occurrences"] = 1
                canonical_by_key[key] = cand
    canonical_match_keys = {c["match_key"]: c for c in canonical_by_key.values()}

    # 2. Summary copies: ignored when a canonical row exists; surfaced only otherwise.
    summary_copies_seen = 0
    duplicates_ignored: list[dict[str, Any]] = []
    orphan_summary: dict[str, dict[str, Any]] = {}
    for iter_ts, source_file, report in iterations:
        for path_label in _SUMMARY_PATHS:
            for srow in _summary_rows(report, path_label):
                if not srow.get("paper_candidate"):
                    continue
                summary_copies_seen += 1
                mkey = _match_key(srow, iter_ts)
                if mkey in canonical_match_keys:
                    canonical_match_keys[mkey]["summary_duplicate_count"] += 1
                    duplicates_ignored.append(_duplicate_record(srow, iter_ts, path_label, "matches canonical row"))
                elif mkey in orphan_summary:
                    orphan_summary[mkey]["summary_duplicate_count"] += 1
                    duplicates_ignored.append(_duplicate_record(srow, iter_ts, path_label, "duplicate summary-only row"))
                else:
                    orphan_summary[mkey] = _extract_summary_candidate(srow, iter_ts, source_file, path_label)

    candidates = list(canonical_by_key.values()) + list(orphan_summary.values())
    for cand in candidates:
        cand["validation"] = _validate_candidate(cand)
        cand["verdict"] = _verdict(cand)
    candidates.sort(key=lambda c: (_verdict_rank(c["verdict"]), -_f(c.get("adjusted_net_edge_after_fees"))))

    verdict_counts: dict[str, int] = {}
    for c in candidates:
        verdict_counts[c["verdict"]] = verdict_counts.get(c["verdict"], 0) + 1
    best = max(
        (c for c in candidates if c["source"] == "canonical_rows"),
        key=lambda c: _f(c.get("adjusted_net_edge_after_fees")), default=None,
    )

    return {
        "schema_kind": SCHEMA_KIND,
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated.isoformat(),
        "watch_dir": str(watch_dir),
        "watch_dir_exists": watch_dir.exists(),
        "iteration_reports_scanned": len(iterations),
        "canonical_rows_seen": canonical_rows_seen,
        "summary_copies_seen": summary_copies_seen,
        "naive_all_paths_total": canonical_rows_seen + summary_copies_seen,
        "unique_candidates": len(candidates),
        "distinct_candidates": len(candidates),  # back-compat alias
        "duplicates_ignored_count": len(duplicates_ignored),
        "verdict_counts": verdict_counts,
        "best_candidate_adjusted_net_edge_after_fees": None if best is None else best.get("adjusted_net_edge_after_fees"),
        "candidates": candidates,
        "duplicates_ignored": duplicates_ignored,
        "safety": {
            "diagnostic_only": True,
            "public_read_only": True,
            "reads_local_reports_only": True,
            "network_access": False,
            "orders_or_execution_logic_added": False,
            "auth_or_account_logic_added": False,
            "browser_automation_added": False,
            "uses_midpoint": False,
        },
    }


# ---------------------------------------------------------------------------- #
# Extraction                                                                   #
# ---------------------------------------------------------------------------- #


def _iter_iteration_reports(watch_dir: Path):
    if not watch_dir.exists():
        return
    for iter_dir in sorted(p for p in watch_dir.iterdir() if p.is_dir()):
        path = iter_dir / "iteration.json"
        if not path.exists():
            continue
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(report, dict):
            yield iter_dir.name, _relative(path, watch_dir), report


def _summary_rows(report: dict[str, Any], path_label: str) -> list[dict[str, Any]]:
    node: Any = report
    for part in path_label.split("."):
        if isinstance(node, dict):
            node = node.get(part)
        else:
            node = None
            break
    return [r for r in (node or []) if isinstance(r, dict)]


def _extract_candidate(row: dict[str, Any], iter_ts: str, source_file: str, *, source: str) -> dict[str, Any]:
    legs = [_extract_leg(leg) for leg in (row.get("basket_legs") or [])]
    grid = list(row.get("state_grid") or [])
    cand = {
        "iteration_timestamp": iter_ts,
        "source_file": source_file,
        "source": source,
        "summary_duplicate_count": 0,
        "asset": row.get("asset"),
        "candidate_type": row.get("candidate_type"),
        "paper_candidate_class": row.get("paper_candidate_class"),
        "candidate_execution_type": row.get("candidate_execution_type"),
        "tradable_buy_only": bool(row.get("tradable_buy_only")),
        "requires_short_or_sell": bool(row.get("requires_short_or_sell")),
        "target_instant_utc": row.get("target_instant_utc"),
        "state_grid_states": len(grid),
        "state_grid": grid,
        "payoff_vector": list(row.get("payoff_vector") or []),
        "min_payoff": row.get("min_payoff"),
        "max_payoff": row.get("max_payoff"),
        "total_cost_after_fees": row.get("total_cost_after_fees"),
        "net_edge_after_fees": row.get("net_edge_after_fees"),
        "adjusted_net_edge_after_fees": row.get("adjusted_net_edge_after_fees"),
        "source_basis_buffer": row.get("source_basis_buffer"),
        "assumptions_accepted": list(row.get("assumptions_accepted") or []),
        "complement_quote_used": bool(row.get("complement_quote_used")),
        "hard_blockers": list(row.get("hard_blockers") or []),
        "quote_side_diagnostics": list(row.get("quote_side_diagnostics") or []),
        "candidate_action": row.get("candidate_action"),
        "source_indexes": sorted({str(l.get("source_index")) for l in legs if l.get("source_index")}),
        "basket_legs": legs,
    }
    cand["match_key"] = _match_key(row, iter_ts)
    cand["dedup_key"] = cand["match_key"] + "::" + _leg_signature(legs)
    return cand


def _extract_summary_candidate(srow: dict[str, Any], iter_ts: str, source_file: str, path_label: str) -> dict[str, Any]:
    cand = _extract_candidate(srow, iter_ts, source_file, source="summary_only")
    cand["summary_path"] = path_label
    cand["summary_duplicate_count"] = 0
    return cand


def _extract_leg(leg: dict[str, Any]) -> dict[str, Any]:
    token_ids = leg.get("token_ids") or {}
    return {
        "platform": leg.get("platform"),
        "side": leg.get("side"),
        "market_shape": leg.get("market_shape"),
        "payoff_observation_type": leg.get("payoff_observation_type"),
        "market_id_or_ticker": leg.get("market_id_or_ticker"),
        "condition_id": leg.get("condition_id"),
        "token_id_yes": (token_ids or {}).get("yes"),
        "token_id_no": (token_ids or {}).get("no"),
        "contract_id": leg.get("contract_id"),
        "ask": leg.get("ask"),
        "fee": leg.get("fee"),
        "all_in_cost": leg.get("all_in_cost"),
        "available_size_or_cap": leg.get("available_size_or_cap"),
        "source_index": leg.get("source_index"),
        "quote_timestamp": leg.get("quote_timestamp"),
        "depth_status": leg.get("depth_status"),
        "complement_used": bool(leg.get("complement_used")),
        "complement_source": leg.get("complement_source"),
        "hard_blockers": list(leg.get("hard_blockers") or []),
        "payoff_vector": list(leg.get("payoff_vector") or []) if leg.get("payoff_vector") is not None else None,
    }


def _leg_signature(legs: list[dict[str, Any]]) -> str:
    return "|".join(sorted(f"{l.get('platform')}:{l.get('side')}:{l.get('market_id_or_ticker')}" for l in legs)) or "no_legs"


def _match_key(row: dict[str, Any], iter_ts: str) -> str:
    net = _opt_f(row.get("net_edge_after_fees"))
    net_s = "na" if net is None else f"{round(net, 6):.6f}"
    return f"{iter_ts}::{row.get('candidate_type')}::{row.get('asset')}::{row.get('target_instant_utc')}::{net_s}"


def _duplicate_record(srow: dict[str, Any], iter_ts: str, path_label: str, reason: str) -> dict[str, Any]:
    return {
        "iteration_timestamp": iter_ts,
        "summary_path": path_label,
        "asset": srow.get("asset"),
        "candidate_type": srow.get("candidate_type"),
        "target_instant_utc": srow.get("target_instant_utc"),
        "net_edge_after_fees": srow.get("net_edge_after_fees"),
        "reason": reason,
    }


# ---------------------------------------------------------------------------- #
# Independent re-validation + verdict                                          #
# ---------------------------------------------------------------------------- #


def _validate_candidate(cand: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        checks.append({"check": name, "passed": bool(passed), "severity": "hard" if name in _HARD_CHECKS else "review", "detail": detail})

    legs = cand.get("basket_legs") or []
    payoff_vector = cand.get("payoff_vector") or []
    summary_only = cand.get("source") == "summary_only"

    # 1. min payoff from the payoff vector.
    recomputed_min = float(min(payoff_vector)) if payoff_vector else None
    reported_min = _opt_f(cand.get("min_payoff"))
    check("min_payoff_matches_vector",
          recomputed_min is not None and reported_min is not None and abs(recomputed_min - reported_min) < _FLOAT_TOL,
          f"recomputed={recomputed_min} reported={reported_min}")

    # 2. total cost from leg all-in; each all-in == ask + fee (asks only, no midpoint).
    leg_all_in: list[float] = []
    all_in_ok = bool(legs)
    for leg in legs:
        ask, fee, all_in = _opt_f(leg.get("ask")), _opt_f(leg.get("fee")), _opt_f(leg.get("all_in_cost"))
        if ask is None or fee is None or all_in is None:
            all_in_ok = False
            continue
        if abs((ask + fee) - all_in) > _FLOAT_TOL:
            all_in_ok = False
        leg_all_in.append(all_in)
    recomputed_total = round(sum(leg_all_in), 8) if (legs and len(leg_all_in) == len(legs)) else None
    reported_total = _opt_f(cand.get("total_cost_after_fees"))
    check("total_cost_recomputed_matches",
          recomputed_total is not None and reported_total is not None and abs(recomputed_total - reported_total) < _FLOAT_TOL,
          f"recomputed={recomputed_total} reported={reported_total}")
    check("all_in_equals_ask_plus_fee_no_midpoint", all_in_ok, "each leg all_in_cost must equal ask + fee")

    # 3. net edge = recomputed_min - recomputed_total.
    recomputed_net = (round(recomputed_min - recomputed_total, 8)
                      if (recomputed_min is not None and recomputed_total is not None) else None)
    reported_net = _opt_f(cand.get("net_edge_after_fees"))
    check("net_edge_recomputed_matches",
          recomputed_net is not None and reported_net is not None and abs(recomputed_net - reported_net) < _FLOAT_TOL,
          f"recomputed={recomputed_net} reported={reported_net}")
    check("net_edge_positive", reported_net is not None and reported_net > 0, f"net={reported_net}")
    adj = _opt_f(cand.get("adjusted_net_edge_after_fees"))
    check("adjusted_net_edge_positive", adj is not None and adj > 0, f"adjusted={adj}")

    # 4-7. quote / execution gates.
    leg_blockers = {b for leg in legs for b in (leg.get("hard_blockers") or [])}
    row_labels = set(cand.get("hard_blockers") or []) | set(cand.get("quote_side_diagnostics") or [])
    all_labels = leg_blockers | row_labels
    missing_ask = (not legs) or any(_opt_f(leg.get("ask")) is None for leg in legs) or bool(all_labels & _MISSING_ASK_LABELS)
    check("no_missing_ask", not missing_ask, "every buy leg must have an executable ask")
    check("no_stale_quote", not any("stale" in str(x) for x in all_labels), "no stale_* labels on row or legs")
    check("buy_only", bool(cand.get("tradable_buy_only")) and (cand.get("candidate_execution_type") in (None, "BUY_ONLY")),
          f"execution_type={cand.get('candidate_execution_type')} tradable_buy_only={cand.get('tradable_buy_only')}")
    check("no_short_or_sell_required", not (bool(cand.get("requires_short_or_sell")) or bool(all_labels & _REQUIRES_SHORT_LABELS)),
          "row must not require shorting/selling")

    # 8. no midpoint — complements must derive from a bid.
    midpoint_used = any("mid" in str(leg.get("complement_source") or "").lower() for leg in legs)
    complement_ok = all((not leg.get("complement_used")) or ("bid" in str(leg.get("complement_source") or "").lower()) for leg in legs)
    check("no_midpoint_used", (not midpoint_used) and complement_ok, "complement asks must derive from a bid (1 - opposite bid)")

    # 9. cross-source basis listed.
    sources = {str(leg.get("source_index")) for leg in legs if leg.get("source_index")}
    cross_source = len(sources) > 1
    basis_listed = bool(set(cand.get("assumptions_accepted") or []) & _BASIS_ASSUMPTIONS)
    check("cross_source_basis_listed", (not cross_source) or basis_listed,
          f"sources={sorted(sources)} assumptions={cand.get('assumptions_accepted')}")

    # 10. paper only if hard_blockers empty.
    check("paper_only_if_no_hard_blockers", not (cand.get("hard_blockers") or []), f"hard_blockers={cand.get('hard_blockers')}")

    # Review-only flags.
    zw, zw_detail = _zero_width_grid(cand.get("state_grid") or [])
    check("no_zero_width_state_grid_intervals", not zw, zw_detail)
    boundary, b_detail = _boundary_inclusivity_risk(legs)
    check("no_boundary_inclusivity_risk", not boundary, b_detail)
    check("no_source_index_mismatch", not (cross_source or ("source_index_mismatch" in (cand.get("assumptions_accepted") or []))),
          f"sources={sorted(sources)}")
    check("from_canonical_rows_not_summary_only", not summary_only,
          "candidate has no canonical iteration row; only a summary copy exists")

    failed = [c for c in checks if not c["passed"]]
    hard_failures = [c["check"] for c in failed if c["severity"] == "hard"]
    boundary_flags = [c["check"] for c in failed if c["severity"] == "review"]
    return {
        "all_passed": not failed,
        "review_flags": [c["check"] for c in failed],  # union (back-compat)
        "hard_failures": hard_failures,
        "boundary_flags": boundary_flags,
        "checks": checks,
        "recomputed": {
            "min_payoff": recomputed_min,
            "total_cost_after_fees": recomputed_total,
            "net_edge_after_fees": recomputed_net,
            "cross_source": cross_source,
            "sources": sorted(sources),
        },
    }


def _verdict(cand: dict[str, Any]) -> str:
    v = cand.get("validation") or {}
    if cand.get("source") == "summary_only":
        return VERDICT_DUPLICATE
    if v.get("hard_failures"):
        return VERDICT_RECOMPUTE_FAIL
    if v.get("boundary_flags"):
        return VERDICT_BOUNDARY
    return VERDICT_VALID


def _verdict_rank(verdict: str) -> int:
    return {VERDICT_VALID: 0, VERDICT_BOUNDARY: 1, VERDICT_RECOMPUTE_FAIL: 2, VERDICT_DUPLICATE: 3}.get(verdict, 4)


def _zero_width_grid(grid: list[Any]) -> tuple[bool, str]:
    bad: list[str] = []
    seen: set[str] = set()
    dup = False
    for raw in grid:
        text = str(raw)
        if text in seen:
            dup = True
        seen.add(text)
        m = re.match(r"\[\s*([^,]+)\s*,\s*([^)\]]+)\s*[)\]]", text)
        if not m:
            continue
        lo, hi = _grid_bound(m.group(1)), _grid_bound(m.group(2))
        if lo is not None and hi is not None and abs(hi - lo) < 1e-9:
            bad.append(text)
    if bad:
        return True, f"zero-width intervals: {bad[:5]}"
    if dup:
        return True, "duplicate state-grid intervals present"
    return False, ""


def _grid_bound(token: str) -> float | None:
    t = token.strip().lower()
    if t in {"-inf", "+inf", "inf", "-infinity", "infinity", ""}:
        return None
    try:
        return float(t.replace(",", ""))
    except ValueError:
        return None


def _boundary_inclusivity_risk(legs: list[dict[str, Any]]) -> tuple[bool, str]:
    edges: list[tuple[str, float]] = []
    for leg in legs:
        kind = "range" if "range" in str(leg.get("market_shape") or "").lower() or "bucket" in str(leg.get("market_shape") or "").lower() else "threshold"
        for s in _leg_strikes(leg):
            edges.append((kind, s))
    notes: list[str] = []
    for i in range(len(edges)):
        for j in range(i + 1, len(edges)):
            k1, s1 = edges[i]
            k2, s2 = edges[j]
            tol = max(_BOUNDARY_ABS_TOL, _BOUNDARY_REL_TOL * max(abs(s1), abs(s2)))
            if abs(s1 - s2) <= tol and not (s1 == s2 and k1 == k2):
                notes.append(f"{k1} {s1:g} ~ {k2} {s2:g} (|Δ|={abs(s1 - s2):g})")
    if notes:
        return True, "; ".join(notes[:4])
    return False, ""


def _leg_strikes(leg: dict[str, Any]) -> list[float]:
    out: list[float] = []
    text = str(leg.get("market_id_or_ticker") or "")
    for m in re.finditer(r"above[-_]?\$?([\d,]+(?:\.\d+)?)", text, re.IGNORECASE):
        v = _opt_f(m.group(1).replace(",", ""))
        if v is not None:
            out.append(v)
    for m in re.finditer(r"(?:^|[-_])B(\d+(?:\.\d+)?)", text):
        v = _opt_f(m.group(1))
        if v is not None:
            out.append(v)
    return out


# ---------------------------------------------------------------------------- #
# Markdown                                                                      #
# ---------------------------------------------------------------------------- #


def render_audit_pack_markdown(report: dict[str, Any]) -> str:
    candidates = report.get("candidates") or []
    vc = report.get("verdict_counts") or {}
    lines = [
        "# Crypto Paper-Candidate Audit Pack (canonical, deduped)",
        "",
        "Canonical paper candidates from iteration `rows`, deduped and independently re-validated. "
        "Summary copies are reported separately and never counted. Read-only; no trading; no midpoint.",
        "",
        "## 1. Summary",
        "",
        f"- watch_dir: `{_md(report.get('watch_dir'))}` (exists: `{report.get('watch_dir_exists')}`)",
        f"- iteration reports scanned: `{report.get('iteration_reports_scanned', 0)}`",
        f"- canonical paper rows seen: `{report.get('canonical_rows_seen', 0)}`  "
        f"summary copies seen: `{report.get('summary_copies_seen', 0)}`  "
        f"naive all-paths total: `{report.get('naive_all_paths_total', 0)}`",
        f"- **unique candidates: `{report.get('unique_candidates', 0)}`**  "
        f"duplicate rows ignored: `{report.get('duplicates_ignored_count', 0)}`",
        f"- verdicts: `{_fmt_counter(vc)}`",
        f"- best canonical adjusted net edge after fees: `{_fmt(report.get('best_candidate_adjusted_net_edge_after_fees'))}`",
    ]
    if not candidates:
        lines += ["", "_No canonical PAPER_CANDIDATE rows found in this watch directory._"]

    # 2. Unique candidates.
    lines += [
        "",
        "## 2. Unique Candidates",
        "",
        "| # | Iter ts | Asset | Type | Class | Instant (UTC) | Legs | Net edge | Adj net | Verdict |",
        "|---:|---|---|---|---|---|---:|---:|---:|---|",
    ]
    for i, c in enumerate(candidates, 1):
        lines.append(
            f"| {i} | {_md(c.get('iteration_timestamp'))} | {_md(c.get('asset'))} | {_md(c.get('candidate_type'))} | "
            f"{_md(c.get('paper_candidate_class'))} | {_md(c.get('target_instant_utc'))} | {len(c.get('basket_legs') or [])} | "
            f"{_md(c.get('net_edge_after_fees'))} | {_md(c.get('adjusted_net_edge_after_fees'))} | {_md(c.get('verdict'))} |"
        )

    # 3. Duplicate rows ignored.
    dups = report.get("duplicates_ignored") or []
    lines += [
        "",
        "## 3. Duplicate Rows Ignored",
        "",
        f"Summary copies of canonical candidates (`{len(dups)}`), excluded from the unique count.",
        "",
        "| Iter ts | Summary path | Asset | Type | Instant | Net edge | Reason |",
        "|---|---|---|---|---|---:|---|",
    ]
    if not dups:
        lines.append("| none |  |  |  |  |  |  |")
    for d in dups[:80]:
        lines.append(
            f"| {_md(d.get('iteration_timestamp'))} | {_md(d.get('summary_path'))} | {_md(d.get('asset'))} | "
            f"{_md(d.get('candidate_type'))} | {_md(d.get('target_instant_utc'))} | {_md(d.get('net_edge_after_fees'))} | {_md(d.get('reason'))} |"
        )

    # 4. Candidate leg details.
    lines += ["", "## 4. Candidate Leg Details"]
    for i, c in enumerate(candidates, 1):
        lines += [
            "",
            f"### Candidate {i}: {_md(c.get('asset'))} {_md(c.get('candidate_type'))} "
            f"({_md(c.get('paper_candidate_class'))}) @ {_md(c.get('target_instant_utc'))} — {_md(c.get('verdict'))}",
            "",
            f"- iteration: `{_md(c.get('iteration_timestamp'))}`  source_file: `{_md(c.get('source_file'))}`  "
            f"source: `{_md(c.get('source'))}`  summary_duplicates: `{c.get('summary_duplicate_count', 0)}`  "
            f"state_grid_states: `{c.get('state_grid_states', 0)}`",
            "",
            "| Platform | Side | Market id/ticker | condition_id | token_id (Y/N) | contract_id | Ask | Fee | All-in | Size/cap | Source index | Quote ts |",
            "|---|---|---|---|---|---|---:|---:|---:|---:|---|---|",
        ]
        if not c.get("basket_legs"):
            lines.append("| (no legs — summary-only) |  |  |  |  |  |  |  |  |  |  |  |")
        for leg in c.get("basket_legs") or []:
            tok = f"{_md(leg.get('token_id_yes'))}/{_md(leg.get('token_id_no'))}"
            lines.append(
                f"| {_md(leg.get('platform'))} | {_md(leg.get('side'))} | {_md(leg.get('market_id_or_ticker'))} | "
                f"{_md(leg.get('condition_id'))} | {tok} | {_md(leg.get('contract_id'))} | {_md(leg.get('ask'))} | "
                f"{_md(leg.get('fee'))} | {_md(leg.get('all_in_cost'))} | {_md(leg.get('available_size_or_cap'))} | "
                f"{_md(leg.get('source_index'))} | {_md(leg.get('quote_timestamp'))} |"
            )

    # 5. Payoff vector recomputation.
    lines += [
        "",
        "## 5. Payoff Vector Recomputation",
        "",
        "| # | States | Reported min | Recomputed min | Reported max | Match |",
        "|---:|---:|---:|---:|---:|---|",
    ]
    for i, c in enumerate(candidates, 1):
        rec = (c.get("validation") or {}).get("recomputed") or {}
        ok = _check_passed(c, "min_payoff_matches_vector")
        lines.append(
            f"| {i} | {c.get('state_grid_states', 0)} | {_md(c.get('min_payoff'))} | {_md(rec.get('min_payoff'))} | "
            f"{_md(c.get('max_payoff'))} | {'ok' if ok else 'MISMATCH'} |"
        )

    # 6. Fee recomputation.
    lines += [
        "",
        "## 6. Fee Recomputation",
        "",
        "| # | Reported total | Recomputed total | Reported net | Recomputed net | Match |",
        "|---:|---:|---:|---:|---:|---|",
    ]
    for i, c in enumerate(candidates, 1):
        rec = (c.get("validation") or {}).get("recomputed") or {}
        ok = _check_passed(c, "total_cost_recomputed_matches") and _check_passed(c, "net_edge_recomputed_matches")
        lines.append(
            f"| {i} | {_md(c.get('total_cost_after_fees'))} | {_md(rec.get('total_cost_after_fees'))} | "
            f"{_md(c.get('net_edge_after_fees'))} | {_md(rec.get('net_edge_after_fees'))} | {'ok' if ok else 'MISMATCH'} |"
        )

    # 7. Boundary / inclusivity review flags.
    lines += ["", "## 7. Boundary / Inclusivity Review Flags", ""]
    any_boundary = False
    for i, c in enumerate(candidates, 1):
        notes = []
        for ch in (c.get("validation") or {}).get("checks", []):
            if ch["check"] in {"no_zero_width_state_grid_intervals", "no_boundary_inclusivity_risk"} and not ch["passed"]:
                notes.append(f"{ch['check']}: {ch['detail']}")
        if notes:
            any_boundary = True
            lines.append(f"- **Candidate {i}** ({_md(c.get('asset'))} {_md(c.get('candidate_type'))} @ {_md(c.get('target_instant_utc'))}):")
            for n in notes:
                lines.append(f"  - {_md(n)}")
    if not any_boundary:
        lines.append("- none — no zero-width grid intervals or near-adjacent bucket/threshold edges detected.")

    # 8. Source / basis assumptions.
    lines += ["", "## 8. Source / Basis Assumptions", "", "| # | Assumptions | Cross-source | Source indexes |", "|---:|---|---|---|"]
    for i, c in enumerate(candidates, 1):
        rec = (c.get("validation") or {}).get("recomputed") or {}
        lines.append(
            f"| {i} | {_md(', '.join(c.get('assumptions_accepted') or []) or 'none')} | "
            f"{_md(rec.get('cross_source'))} | {_md(', '.join(c.get('source_indexes') or []) or 'single')} |"
        )

    # 9. Verdict per candidate.
    lines += [
        "",
        "## 9. Verdict Per Candidate",
        "",
        "| # | Asset | Type | Verdict | Hard failures | Boundary/review flags |",
        "|---:|---|---|---|---|---|",
    ]
    for i, c in enumerate(candidates, 1):
        v = c.get("validation") or {}
        lines.append(
            f"| {i} | {_md(c.get('asset'))} | {_md(c.get('candidate_type'))} | {_md(c.get('verdict'))} | "
            f"{_md(', '.join(v.get('hard_failures') or []) or 'none')} | {_md(', '.join(v.get('boundary_flags') or []) or 'none')} |"
        )

    lines += [
        "",
        "## Safety",
        "",
        "- diagnostic_only: `true`  public_read_only: `true`  reads_local_reports_only: `true`",
        "- network_access: `false`  uses_midpoint: `false`",
        "- orders_or_execution_logic_added: `false`  auth_or_account_logic_added: `false`  browser_automation_added: `false`",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------- #
# Small helpers                                                                #
# ---------------------------------------------------------------------------- #


def _check_passed(cand: dict[str, Any], name: str) -> bool:
    return any(ch["check"] == name and ch["passed"] for ch in (cand.get("validation") or {}).get("checks", []))


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _opt_f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _f(value: Any) -> float:
    v = _opt_f(value)
    return v if v is not None else -1e18


def _fmt(value: Any) -> str:
    v = _opt_f(value)
    return "n/a" if v is None else f"{v:.6f}"


def _fmt_counter(counter: dict[str, int]) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(counter.items())) or "none"


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")
