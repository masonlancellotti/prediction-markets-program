"""Watcher for the crypto structural payoff-arb scout.

Repeatedly runs ``crypto-structural-payoff-arb-scout`` during live crypto windows
and maintains a clean rolling summary of the best post-fee opportunities. Each
iteration fetches fresh public-read-only Kalshi/Polymarket data via the scout;
CDNA is saved-evidence-only (never fetched). No alerts, no notifications, no
external calls beyond the scout's existing public market-data GETs, no trading.

Per-iteration reports land under ``<output_dir>/<timestamp>/`` and a rolling
``watch_summary.json`` / ``watch_summary.md`` is rewritten after every iteration,
so an interrupted run still leaves a complete summary of what was scanned.
"""
from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from relative_value.crypto_structural_payoff_arb_scout import (
    build_crypto_structural_payoff_arb_scout_report,
    render_crypto_structural_payoff_arb_scout_markdown,
)


SCHEMA_KIND = "crypto_structural_watch_summary_v1"
SCHEMA_VERSION = 1

# Side-specific missing-ask blockers surfaced separately for buy-only rows.
_MISSING_ASK_BLOCKERS = {
    "missing_ask",
    "missing_yes_lower_ask",
    "missing_no_higher_ask",
    "missing_lower_yes_ask",
    "missing_higher_no_ask",
    "missing_partner_yes_ask",
    "missing_partner_no_ask",
    "missing_partner_complement_ask",
    "missing_kalshi_yes_ask",
    "missing_kalshi_no_ask",
    "missing_polymarket_yes_ask",
    "missing_polymarket_no_ask",
    "missing_cdna_display_yes",
    "missing_cdna_display_no",
    "missing_bucket_leg_ask",
    "missing_cdna_display_price",
}

_QUOTE_COVERAGE_KEYS = (
    "kalshi_yes_ask_present",
    "kalshi_no_ask_present",
    "kalshi_yes_bid_present",
    "kalshi_no_bid_present",
    "polymarket_yes_ask_present",
    "polymarket_no_ask_present",
    "polymarket_yes_bid_present",
    "polymarket_no_bid_present",
    "cdna_display_yes_present",
    "cdna_display_no_present",
    "complement_quote_used_count",
    "complement_quote_possible_but_missing_bid",
    "explicit_ask_used_count",
    "gamma_top_of_book_fallback_count",
    "clob_book_used_count",
)

_CANDIDATE_GENERATION_CLASSES = (
    "UP_DOWN_SAME_WINDOW",
    "THRESHOLD_MONOTONICITY_COVER",
    "LONG_ONLY_GUARANTEED_PAYOFF",
    "CROSS_VENUE_THRESHOLD_BASIS",
    "BUCKET_TO_CUMULATIVE_THRESHOLD",
    "CDNA_FILL_FIRST",
    "SAME_PAYOFF_CHEAPER_BASKET",
    "DIAGNOSTIC_ONLY_REQUIRES_SHORT",
)

_FUNNEL_KEYS = (
    "total_rows",
    "buy_only_rows",
    "rows_with_all_required_asks",
    "rows_fresh",
    "rows_net_positive_before_buffer",
    "rows_net_positive_after_buffer",
    "paper_candidates",
)

_REQUIRES_SHORT_BLOCKERS = {"requires_short_or_not_guaranteed", "threshold_to_bucket_requires_short"}

# Burst cadence boundaries (period in seconds, aligned to the UNIX epoch, i.e.
# UTC clock marks). Covers 5m CDNA-ish, 15m Kalshi/Polymarket up/down, 20m CDNA
# threshold, hourly, 2h and 4h windows. When ``--burst-mode`` is on and the
# clock is within ``boundary_window_seconds`` of any of these, the watcher scans
# at the fast burst interval to catch fleeting top-of-hour/quarter-hour quotes.
BURST_BOUNDARY_PERIOD_SECONDS = (5 * 60, 15 * 60, 20 * 60, 3600, 2 * 3600, 4 * 3600)


def _seconds_to_nearest_boundary(now: datetime, periods=BURST_BOUNDARY_PERIOD_SECONDS) -> float:
    """Smallest distance (seconds) from ``now`` to any clock boundary in ``periods``."""
    epoch = now.timestamp()
    best = float("inf")
    for p in periods:
        if p <= 0:
            continue
        r = epoch % p
        best = min(best, r, p - r)
    return best


def _is_near_boundary(now: datetime, window_seconds: float, periods=BURST_BOUNDARY_PERIOD_SECONDS) -> bool:
    return _seconds_to_nearest_boundary(now, periods) <= float(window_seconds)


def _choose_interval(
    *, now: datetime, burst_mode: bool, burst_interval: float, normal_interval: float, boundary_window: float
) -> tuple[float, str]:
    """Return ``(interval_seconds, cadence_mode)`` for the wait AFTER this iteration."""
    if burst_mode and _is_near_boundary(now, boundary_window):
        return float(burst_interval), "burst"
    return float(normal_interval), "normal"

ReportBuilder = Callable[..., dict[str, Any]]
ReportRenderer = Callable[[dict[str, Any]], str]
Sleep = Callable[[float], None]
Clock = Callable[[], datetime]


def run_watch(
    *,
    assets: list[str],
    interval_seconds: float = 60.0,
    iterations: int = 30,
    burst_mode: bool = False,
    burst_interval_seconds: float = 5.0,
    normal_interval_seconds: float | None = None,
    boundary_window_seconds: float = 90.0,
    operator_risk_mode: str = "aggressive",
    include_cdna: bool = False,
    operator_accept_cdna_display_price_risk: bool = False,
    allow_top_of_book_depth: bool = False,
    operator_size_cap: float = 0.0,
    cdna_operator_size_cap: float = 1.0,
    cdna_evidence_dir: Path | None = None,
    cdna_timeseries_dir: Path | None = None,
    max_cdna_snapshot_age_seconds: float = 60.0,
    require_cdna_fresh_for_cdna_candidates: bool = True,
    max_quote_age_seconds: float = 180.0,
    min_available_notional: float = 1.0,
    max_basket_legs: int = 12,
    source_basis_buffer_bps: float = 0.0,
    source_basis_buffer_absolute: str | dict[str, float] | None = None,
    near_miss_net_edge_threshold: float = 0.02,
    wide_near_miss_net_edge_threshold: float = 0.10,
    lookahead_hours: float = 8.0,
    output_dir: Path = Path("reports/crypto_structural_watch"),
    report_builder: ReportBuilder | None = None,
    report_renderer: ReportRenderer | None = None,
    sleep: Sleep | None = None,
    clock: Clock | None = None,
    http_get: Any = None,
    scout_sleep: Sleep | None = None,
    console: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    builder = report_builder or build_crypto_structural_payoff_arb_scout_report
    renderer = report_renderer or render_crypto_structural_payoff_arb_scout_markdown
    sleeper = sleep or time.sleep
    now_fn = clock or (lambda: datetime.now(timezone.utc))
    emit = console or print
    asset_list = [str(a).strip().upper() for a in assets if str(a).strip()]
    normal_interval = float(normal_interval_seconds) if normal_interval_seconds is not None else float(interval_seconds)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    started = now_fn()

    iteration_records: list[dict[str, Any]] = []
    candidate_type_totals: Counter = Counter()
    blocker_totals: Counter = Counter()
    mono_totals: Counter = Counter()
    quote_side_totals: Counter = Counter()
    quote_coverage_totals: Counter = Counter()
    raw_quote_coverage_totals: Counter = Counter()
    funnel_totals: Counter = Counter()
    economic_totals: Counter = Counter()
    cadence_totals: Counter = Counter()
    run_quality_totals: Counter = Counter()
    coverage_totals: dict[str, Counter] = {}
    grammar_totals: Counter = Counter()
    near_bucket_totals: Counter = Counter()
    up_down_audit_totals = _empty_up_down_audit()
    cdna_participation_totals = {
        "cdna_supplied": False,
        "cdna_rows_loaded": 0,
        "cdna_candidates_considered": 0,
        "cdna_fill_first_candidates": 0,
        "cdna_candidate_types_generated": Counter(),
        "cdna_missing_reason": "",
    }
    best_rows: list[dict[str, Any]] = []
    near_miss_rows: list[dict[str, Any]] = []
    paper_rows: list[dict[str, Any]] = []
    cdna_rows: list[dict[str, Any]] = []
    latest_errors: list[str] = []
    best_net = None
    best_adjusted = None
    best_priced_buy_only_net = None
    best_near_miss_net = None
    worst_net = None
    best_priced_buy_only_reason = "no_priced_buy_only_rows"
    paper_candidates_total = 0
    diagnostic_short_total = 0
    buy_only_total = 0
    mono_one_leg_total = 0
    micro_test_total = 0
    complement_total = 0

    for i in range(max(0, int(iterations))):
        generated = now_fn()
        report = builder(
            assets=asset_list,
            operator_risk_mode=operator_risk_mode,
            include_cdna=include_cdna,
            operator_accept_cdna_display_price_risk=operator_accept_cdna_display_price_risk,
            allow_top_of_book_depth=allow_top_of_book_depth,
            operator_size_cap=operator_size_cap,
            cdna_operator_size_cap=cdna_operator_size_cap,
            cdna_evidence_dir=cdna_evidence_dir,
            cdna_timeseries_dir=cdna_timeseries_dir,
            max_cdna_snapshot_age_seconds=max_cdna_snapshot_age_seconds,
            require_cdna_fresh_for_cdna_candidates=require_cdna_fresh_for_cdna_candidates,
            max_quote_age_seconds=max_quote_age_seconds,
            min_available_notional=min_available_notional,
            max_basket_legs=max_basket_legs,
            source_basis_buffer_bps=source_basis_buffer_bps,
            source_basis_buffer_absolute=source_basis_buffer_absolute,
            lookahead_hours=lookahead_hours,
            generated_at=generated,
            refresh_kalshi_polymarket=True,
            http_get=http_get,
            sleep=scout_sleep,
        )

        ts = generated.strftime("%Y%m%dT%H%M%SZ")
        iter_dir = output_dir / ts
        suffix = 0
        while iter_dir.exists():
            suffix += 1
            iter_dir = output_dir / f"{ts}_{suffix:02d}"
        iter_dir.mkdir(parents=True, exist_ok=True)
        (iter_dir / "iteration.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        try:
            (iter_dir / "iteration.md").write_text(renderer(report), encoding="utf-8")
        except Exception:  # noqa: BLE001 (a malformed report must not abort the watch)
            (iter_dir / "iteration.md").write_text(f"# iteration {ts}\n\n_render failed_\n", encoding="utf-8")

        counts = report.get("summary_counts") or {}
        ctc = report.get("candidate_type_counts") or {}
        mcd = report.get("monotonicity_cover_diagnostics") or {}
        rows = report.get("rows") or []
        iter_paper = int(counts.get("paper_candidate_rows", 0))
        # Diagnostic-only short-required rows are tracked SEPARATELY and never
        # mixed into actionable blocker pressure (Mason cannot short).
        iter_diag_short = int(counts.get("diagnostic_only_short_required_rows", report.get("diagnostic_only_short_required_rows", 0)) or 0)
        iter_buy_only = int(counts.get("buy_only_rows", 0) or 0)
        iter_mono_one_leg = int(report.get("monotonicity_covers_one_leg_missing", 0) or 0)
        iter_net_diag = _buy_only_net_diagnostics(report)
        iter_best_net = iter_net_diag["best_buy_only_net_edge_after_fees"]
        iter_best_adj = iter_net_diag["best_buy_only_adjusted_net_edge_after_fees"]
        iter_quote_coverage = _quote_coverage_by_venue_side(report)
        iter_raw_quote_coverage = _raw_quote_coverage_by_venue_side(report)
        iter_funnel = _candidate_readiness_funnel(report)
        iter_cdna_participation = _cdna_participation(
            report, include_cdna=include_cdna, cdna_evidence_dir=cdna_evidence_dir
        )
        # ``top_blockers`` from the scout is ACTIONABLE buy-only only.
        iter_blockers = Counter()
        for item in report.get("top_blockers") or []:
            iter_blockers[str(item.get("blocker"))] += int(item.get("count") or 0)
        iter_quote_side = Counter({str(k): int(v) for k, v in (report.get("quote_side_diagnostic_counts") or {}).items()})
        iter_economic = Counter({str(k): int(v) for k, v in (report.get("economic_rejections") or {}).items()})
        iter_run_quality = _classify_run_quality(
            funnel=iter_funnel,
            paper_candidates=iter_paper,
            blocker_totals=iter_blockers,
            quote_side_totals=iter_quote_side,
            economic_totals=iter_economic,
            candidate_generation_coverage=report.get("candidate_generation_coverage") or [],
            up_down_audit=report.get("up_down_audit") or {},
            compatible_windows=len(report.get("state_grids") or []),
        )

        iter_micro = int(counts.get("manual_micro_test_candidate_rows", report.get("manual_micro_test_candidate_rows", 0)) or 0)
        iter_complement = int(counts.get("complement_quote_rows", report.get("complement_quote_rows", 0)) or 0)

        paper_candidates_total += iter_paper
        diagnostic_short_total += iter_diag_short
        buy_only_total += iter_buy_only
        mono_one_leg_total += iter_mono_one_leg
        micro_test_total += iter_micro
        complement_total += iter_complement
        candidate_type_totals.update({k: int(v) for k, v in ctc.items()})
        grammar_totals.update({str(k): int(v) for k, v in (report.get("contract_grammar_counts") or {}).items()})
        for entry in report.get("candidate_generation_coverage") or []:
            cls = str(entry.get("candidate_class") or "")
            if not cls:
                continue
            bucket = coverage_totals.setdefault(cls, Counter())
            for k in (
                "attempted",
                "generated",
                "priced",
                "net_positive",
                "paper_candidate",
                "paper",
                "blocked_missing_ask",
                "blocked_stale",
                "blocked_no_positive_net",
                "blocked_shape_or_time",
                "blocked_missing_cdna_display",
                "blocked_target_time_mismatch",
                "blocked_threshold_grid_mismatch",
            ):
                bucket[k] += int(entry.get(k) or 0)
        iter_near_buckets = _near_miss_threshold_buckets(
            report,
            near_threshold=float(near_miss_net_edge_threshold),
            wide_threshold=float(wide_near_miss_net_edge_threshold),
        )
        for k, v in iter_near_buckets.items():
            near_bucket_totals[str(k)] += int(v or 0)
        _merge_up_down_audit(up_down_audit_totals, report.get("up_down_audit") or {})
        blocker_totals.update(iter_blockers)
        quote_side_totals.update(iter_quote_side)
        quote_coverage_totals.update(iter_quote_coverage)
        raw_quote_coverage_totals.update(iter_raw_quote_coverage)
        funnel_totals.update(iter_funnel)
        economic_totals.update(iter_economic)
        run_quality_totals[iter_run_quality["label"]] += 1
        cdna_participation_totals["cdna_supplied"] = bool(
            cdna_participation_totals["cdna_supplied"] or iter_cdna_participation["cdna_supplied"]
        )
        for key in ("cdna_rows_loaded", "cdna_candidates_considered", "cdna_fill_first_candidates"):
            cdna_participation_totals[key] += int(iter_cdna_participation.get(key) or 0)
        cdna_participation_totals["cdna_candidate_types_generated"].update(
            iter_cdna_participation.get("cdna_candidate_types_generated") or {}
        )
        if iter_cdna_participation.get("cdna_missing_reason"):
            cdna_participation_totals["cdna_missing_reason"] = iter_cdna_participation["cdna_missing_reason"]
        for key in ("monotonicity_pairs_checked", "monotonicity_cover_candidates_generated",
                    "monotonicity_cover_paper_candidates", "missing_yes_lower_ask",
                    "missing_no_higher_ask", "complement_quote_used"):
            mono_totals[key] += int(mcd.get(key) or 0)
        best_net = _max_or_none([best_net, iter_best_net])
        best_adjusted = _max_or_none([best_adjusted, iter_best_adj])
        best_priced_buy_only_net = _max_or_none([best_priced_buy_only_net, iter_net_diag["best_priced_buy_only_net_edge_after_fees"]])
        best_near_miss_net = _max_or_none([best_near_miss_net, iter_net_diag["best_near_miss_net_edge_after_fees"]])
        worst_net = _min_or_none([worst_net, iter_net_diag["worst_net_edge_after_fees"]])
        if best_priced_buy_only_net is not None:
            best_priced_buy_only_reason = "priced_buy_only_rows_found"

        iter_near_miss_rows = []
        for nm in report.get("top_buy_only_near_misses") or []:
            nm_row = _near_miss_row(nm, ts)
            near_miss_rows.append(nm_row)
            iter_near_miss_rows.append(nm_row)
        for r in rows:
            if r.get("paper_candidate"):
                paper_rows.append(_leaderboard_row(r, ts))
        for d in report.get("cdna_fill_first_candidates") or []:
            cdna_rows.append({"iteration_timestamp": ts, **{k: d.get(k) for k in ("asset", "candidate_type", "target_instant_utc", "adjusted_net_edge_after_fees", "candidate_action")}})

        # Iteration errors: collector warnings + clob failures from load diagnostics.
        iter_errors = _collect_iteration_errors(report)
        if iter_errors:
            latest_errors = iter_errors  # keep only the most recent iteration's

        # Window coverage = upcoming settlement instants this iteration touched.
        window_instants = sorted({str(g.get("target_instant_utc")) for g in (report.get("state_grids") or []) if g.get("target_instant_utc")})

        # Cadence: how long we wait AFTER this iteration (burst near boundaries).
        next_interval, cadence_mode = _choose_interval(
            now=generated, burst_mode=burst_mode, burst_interval=burst_interval_seconds,
            normal_interval=normal_interval, boundary_window=boundary_window_seconds,
        )
        cadence_totals[cadence_mode] += 1

        # Keep the best few priced buy-only rows from this iteration for the leaderboard.
        priced = [r for r in rows if _row_is_buy_only(r) and _float(r.get("net_edge_after_fees")) is not None]
        priced.sort(key=lambda r: (1 if r.get("paper_candidate") else 0, _float(r.get("adjusted_net_edge_after_fees")) or -1e9), reverse=True)
        for r in priced[:5]:
            best_rows.append(_leaderboard_row(r, ts))

        record = {
            "iteration": i,
            "timestamp": ts,
            "generated_at": generated.isoformat(),
            "report_dir": str(iter_dir),
            "cadence_mode": cadence_mode,
            "next_interval_seconds": next_interval,
            "seconds_to_nearest_boundary": round(_seconds_to_nearest_boundary(generated), 1),
            "window_instants": window_instants,
            "paper_candidates": iter_paper,
            "manual_micro_test_candidates": iter_micro,
            "complement_quote_rows": iter_complement,
            "diagnostic_only_short_required_rows": iter_diag_short,
            "buy_only_rows": iter_buy_only,
            "monotonicity_covers_one_leg_missing": iter_mono_one_leg,
            "best_net_edge_after_fees": iter_best_net,
            "best_adjusted_net_edge_after_fees": iter_best_adj,
            "best_buy_only_net_edge_after_fees": iter_net_diag["best_buy_only_net_edge_after_fees"],
            "best_priced_buy_only_net_edge_after_fees": iter_net_diag["best_priced_buy_only_net_edge_after_fees"],
            "best_priced_buy_only_net_edge_after_fees_reason": iter_net_diag["best_priced_buy_only_net_edge_after_fees_reason"],
            "best_near_miss_net_edge_after_fees": iter_net_diag["best_near_miss_net_edge_after_fees"],
            "worst_net_edge_after_fees": iter_net_diag["worst_net_edge_after_fees"],
            "quote_coverage_by_venue_side": iter_quote_coverage,
            "raw_quote_coverage_by_venue_side": iter_raw_quote_coverage,
            "candidate_readiness_funnel": iter_funnel,
            "near_miss_threshold_buckets": iter_near_buckets,
            "up_down_audit": report.get("up_down_audit") or {},
            "run_quality_label": iter_run_quality["label"],
            "run_quality_reason": iter_run_quality["reason"],
            "near_miss_distance": _near_miss_distance(iter_near_miss_rows, iter_best_net),
            "cdna_participation": iter_cdna_participation,
            "candidate_type_counts": dict(ctc),
            "monotonicity_cover_candidates_generated": int(mcd.get("monotonicity_cover_candidates_generated") or 0),
            "errors": iter_errors,
            "top_actionable_blockers": [{"blocker": b, "count": c} for b, c in iter_blockers.most_common(5)],
            "top_blockers": [{"blocker": b, "count": c} for b, c in iter_blockers.most_common(5)],
        }
        iteration_records.append(record)

        summary = _build_summary(
            schema_started=started, updated=now_fn(), assets=asset_list, interval_seconds=interval_seconds,
            iterations_requested=int(iterations), operator_risk_mode=operator_risk_mode,
            source_basis_buffer_bps=source_basis_buffer_bps, include_cdna=include_cdna,
            iteration_records=iteration_records, paper_candidates_total=paper_candidates_total,
            best_net=best_net, best_adjusted=best_adjusted, candidate_type_totals=candidate_type_totals,
            blocker_totals=blocker_totals, mono_totals=mono_totals, best_rows=best_rows, output_dir=output_dir,
            best_priced_buy_only_net=best_priced_buy_only_net,
            best_priced_buy_only_reason=best_priced_buy_only_reason,
            best_near_miss_net=best_near_miss_net, worst_net=worst_net,
            diagnostic_short_total=diagnostic_short_total, buy_only_total=buy_only_total,
            mono_one_leg_total=mono_one_leg_total, near_miss_rows=near_miss_rows,
            quote_side_totals=quote_side_totals, economic_totals=economic_totals, cadence_totals=cadence_totals,
            paper_rows=paper_rows, cdna_rows=cdna_rows, latest_errors=latest_errors,
            quote_coverage_totals=quote_coverage_totals, funnel_totals=funnel_totals,
            raw_quote_coverage_totals=raw_quote_coverage_totals,
            run_quality_totals=run_quality_totals, cdna_participation_totals=cdna_participation_totals,
            coverage_totals=coverage_totals, near_bucket_totals=near_bucket_totals,
            grammar_totals=grammar_totals,
            up_down_audit_totals=up_down_audit_totals,
            near_miss_net_edge_threshold=near_miss_net_edge_threshold,
            wide_near_miss_net_edge_threshold=wide_near_miss_net_edge_threshold,
            micro_test_total=micro_test_total, complement_total=complement_total,
            burst_mode=burst_mode, burst_interval_seconds=burst_interval_seconds,
            normal_interval_seconds=normal_interval, boundary_window_seconds=boundary_window_seconds,
        )
        (output_dir / "watch_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        (output_dir / "watch_summary.md").write_text(_render_summary_markdown(summary), encoding="utf-8")

        emit(
            f"{ts} | cadence={cadence_mode} | quality={iter_run_quality['label']} | "
            f"paper_candidates={iter_paper} | buy_only_rows={iter_buy_only} | "
            f"micro_test={iter_micro} | diagnostic_short={iter_diag_short} | "
            f"best_priced_buy_only_net={_fmt(iter_net_diag['best_priced_buy_only_net_edge_after_fees'])} | "
            f"best_near_miss_net={_fmt(iter_net_diag['best_near_miss_net_edge_after_fees'])} | "
            f"actionable_blockers={_fmt_blockers(record['top_actionable_blockers'])}"
        )

        if i < int(iterations) - 1:
            sleeper(float(next_interval))

    return summary if iteration_records else _empty_summary(
        started=started, updated=now_fn(), assets=asset_list, interval_seconds=interval_seconds,
        iterations_requested=int(iterations), operator_risk_mode=operator_risk_mode, output_dir=output_dir,
    )


# ---------------------------------------------------------------------------- #
# Summary assembly                                                             #
# ---------------------------------------------------------------------------- #


def _build_summary(*, schema_started, updated, assets, interval_seconds, iterations_requested,
                   operator_risk_mode, source_basis_buffer_bps, include_cdna, iteration_records,
                   paper_candidates_total, best_net, best_adjusted, candidate_type_totals,
                   blocker_totals, mono_totals, best_rows, output_dir,
                   best_priced_buy_only_net=None, best_priced_buy_only_reason="no_priced_buy_only_rows",
                   best_near_miss_net=None, worst_net=None,
                   diagnostic_short_total=0, buy_only_total=0, mono_one_leg_total=0,
                   near_miss_rows=None, quote_side_totals=None, economic_totals=None,
                   cadence_totals=None, paper_rows=None, cdna_rows=None, latest_errors=None,
                   quote_coverage_totals=None, raw_quote_coverage_totals=None, funnel_totals=None, run_quality_totals=None,
                   cdna_participation_totals=None, coverage_totals=None, near_bucket_totals=None,
                   grammar_totals=None,
                   up_down_audit_totals=None,
                   near_miss_net_edge_threshold=0.02, wide_near_miss_net_edge_threshold=0.10,
                   micro_test_total=0, complement_total=0, burst_mode=False,
                   burst_interval_seconds=5.0, normal_interval_seconds=None,
                   boundary_window_seconds=90.0) -> dict[str, Any]:
    leaderboard = sorted(
        best_rows,
        key=lambda r: (1 if r.get("paper_candidate") else 0, _float(r.get("adjusted_net_edge_after_fees")) or -1e9),
        reverse=True,
    )
    # Dedup leaderboard by (asset, candidate_type, lower/higher strike, instant).
    seen: set[tuple] = set()
    deduped: list[dict[str, Any]] = []
    for r in leaderboard:
        key = (r.get("asset"), r.get("candidate_type"), r.get("lower_strike"), r.get("higher_strike"), r.get("target_instant_utc"), r.get("net_edge_after_fees"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    # Buy-only near-miss leaderboard (best net edge first), deduped likewise.
    near_sorted = sorted(
        near_miss_rows or [],
        key=lambda r: _float(r.get("net_edge_after_fees")) if _float(r.get("net_edge_after_fees")) is not None else -1e9,
        reverse=True,
    )
    near_seen: set[tuple] = set()
    near_deduped: list[dict[str, Any]] = []
    for r in near_sorted:
        key = (r.get("asset"), r.get("candidate_type"), r.get("lower_strike"), r.get("higher_strike"), r.get("target_instant_utc"), r.get("net_edge_after_fees"))
        if key in near_seen:
            continue
        near_seen.add(key)
        near_deduped.append(r)
    # Missing-ask blockers for buy-only rows, pulled from the actionable totals.
    missing_ask_buy_only = {b: c for b, c in blocker_totals.items() if b in _MISSING_ASK_BLOCKERS}
    quote_side_totals = quote_side_totals or Counter()
    economic_totals = economic_totals or Counter()
    cadence_totals = cadence_totals or Counter()
    quote_coverage = _with_int_defaults(quote_coverage_totals or Counter(), _QUOTE_COVERAGE_KEYS)
    raw_quote_coverage = _with_int_defaults(raw_quote_coverage_totals or Counter(), _QUOTE_COVERAGE_KEYS)
    funnel = _with_int_defaults(funnel_totals or Counter(), _FUNNEL_KEYS)
    cdna_participation = _normalize_cdna_participation(cdna_participation_totals)
    coverage_totals = coverage_totals or {}
    ordered_classes = list(_CANDIDATE_GENERATION_CLASSES)
    for cls in sorted(coverage_totals):
        if cls not in ordered_classes:
            ordered_classes.append(cls)
    candidate_generation_coverage = []
    for cls in ordered_classes:
        c = coverage_totals.get(cls) or Counter()
        candidate_generation_coverage.append(
            {
                "candidate_class": cls,
                "attempted": int(c.get("attempted", 0)),
                "generated": int(c.get("generated", 0)),
                "priced": int(c.get("priced", 0)),
                "net_positive": int(c.get("net_positive", 0)),
                "paper_candidate": int(c.get("paper_candidate", c.get("paper", 0))),
                "paper": int(c.get("paper", c.get("paper_candidate", 0))),
                "blocked_missing_ask": int(c.get("blocked_missing_ask", 0)),
                "blocked_stale": int(c.get("blocked_stale", 0)),
                "blocked_no_positive_net": int(c.get("blocked_no_positive_net", 0)),
                "blocked_shape_or_time": int(c.get("blocked_shape_or_time", 0)),
                "blocked_missing_cdna_display": int(c.get("blocked_missing_cdna_display", 0)),
                "blocked_target_time_mismatch": int(c.get("blocked_target_time_mismatch", 0)),
                "blocked_threshold_grid_mismatch": int(c.get("blocked_threshold_grid_mismatch", 0)),
            }
        )
    near_bucket_totals = near_bucket_totals or Counter()
    up_down_audit = _normalize_up_down_audit(up_down_audit_totals)
    run_quality = _classify_run_quality(
        funnel=funnel,
        paper_candidates=int(paper_candidates_total),
        blocker_totals=blocker_totals,
        quote_side_totals=quote_side_totals,
        economic_totals=economic_totals,
        candidate_generation_coverage=candidate_generation_coverage,
        up_down_audit=up_down_audit,
        compatible_windows=len({inst for rec in iteration_records for inst in (rec.get("window_instants") or [])}),
    )
    near_miss_distance = _near_miss_distance(near_deduped, best_priced_buy_only_net)

    # Paper-candidate leaderboard (deduped) — what actually qualified.
    paper_seen: set[tuple] = set()
    paper_deduped: list[dict[str, Any]] = []
    for r in sorted(paper_rows or [], key=lambda r: _float(r.get("adjusted_net_edge_after_fees")) or -1e9, reverse=True):
        key = (r.get("asset"), r.get("candidate_type"), r.get("lower_strike"), r.get("higher_strike"), r.get("target_instant_utc"))
        if key in paper_seen:
            continue
        paper_seen.add(key)
        paper_deduped.append(r)

    # Part C: would the guarded live trigger have fired, and would live execution
    # have been blocked? (Live floor is min_net_edge >= 0.10.)
    cgc_by_class = {c.get("candidate_class"): c for c in candidate_generation_coverage}
    cross_venue = cgc_by_class.get("CROSS_VENUE_THRESHOLD_BASIS") or {}
    updown_cov = cgc_by_class.get("UP_DOWN_SAME_WINDOW") or {}
    would_fire = bool(int(paper_candidates_total) > 0 and (best_net is not None and best_net >= 0.10))
    live_block_reasons = []
    if int(paper_candidates_total) <= 0:
        live_block_reasons.append("no_paper_candidate_at_min_edge_0.10")
    if best_net is None or best_net < 0.10:
        live_block_reasons.append("best_net_edge_below_min_0.10")
    live_block_reasons += [
        "manual_or_dry_run_default_no_live_orders",
        "boundary_inclusivity_unvalidated_for_cross_strike_baskets",
        "live_gates_require_env_and_explicit_flags",
    ]
    trigger_readiness = {
        "trigger_would_have_fired": would_fire,
        "min_net_edge_floor": 0.10,
        "best_net_edge_after_fees": best_net,
        "paper_candidates_found": int(paper_candidates_total),
        "live_execution_would_be_blocked": True,
        "live_execution_block_reasons": sorted(set(live_block_reasons)),
        "direct_up_down_coverage": {"attempted": int(updown_cov.get("attempted", 0)), "generated": int(updown_cov.get("generated", 0))},
        "cross_venue_threshold_coverage": {"attempted": int(cross_venue.get("attempted", 0)), "generated": int(cross_venue.get("generated", 0))},
    }
    return {
        "schema_kind": SCHEMA_KIND,
        "schema_version": SCHEMA_VERSION,
        "started_at": schema_started.isoformat(),
        "updated_at": updated.isoformat(),
        "assets": assets,
        "operator_risk_mode": operator_risk_mode,
        "interval_seconds": float(interval_seconds),
        "iterations_requested": int(iterations_requested),
        "iterations_completed": len(iteration_records),
        "include_cdna": bool(include_cdna),
        "source_basis_buffer_bps": float(source_basis_buffer_bps),
        "run_quality_label": run_quality["label"],
        "run_quality_reason": run_quality["reason"],
        "totals": {
            "paper_candidates_found": int(paper_candidates_total),
            "buy_only_rows": int(buy_only_total),
            "diagnostic_only_short_required_rows": int(diagnostic_short_total),
            "monotonicity_covers_one_leg_missing": int(mono_one_leg_total),
            "manual_micro_test_candidates": int(micro_test_total),
            "complement_quote_rows": int(complement_total),
            "best_net_edge_after_fees": best_net,
            "best_adjusted_net_edge_after_fees": best_adjusted,
            "best_buy_only_net_edge_after_fees": best_net,
            "best_priced_buy_only_net_edge_after_fees": best_priced_buy_only_net,
            "best_priced_buy_only_net_edge_after_fees_reason": (
                "priced_buy_only_rows_found" if best_priced_buy_only_net is not None else best_priced_buy_only_reason
            ),
            "best_near_miss_net_edge_after_fees": best_near_miss_net,
            "worst_net_edge_after_fees": worst_net,
            "quote_coverage_by_venue_side": quote_coverage,
            "raw_quote_coverage_by_venue_side": raw_quote_coverage,
            "candidate_readiness_funnel": funnel,
            "run_quality_label": run_quality["label"],
            "run_quality_reason": run_quality["reason"],
            "run_quality_iterations": dict(run_quality_totals or {}),
            "near_miss_distance": near_miss_distance,
            "near_miss_threshold_buckets": {
                "near_miss_net_edge_threshold": float(near_miss_net_edge_threshold),
                "wide_near_miss_net_edge_threshold": float(wide_near_miss_net_edge_threshold),
                "within_near_threshold": int(near_bucket_totals.get("within_near_threshold", 0)),
                "within_wide_threshold": int(near_bucket_totals.get("within_wide_threshold", 0)),
                "within_2c": int(near_bucket_totals.get("within_2c", 0)),
                "within_5c": int(near_bucket_totals.get("within_5c", 0)),
                "within_10c": int(near_bucket_totals.get("within_10c", 0)),
            },
            "candidate_generation_coverage": candidate_generation_coverage,
            "contract_grammar_counts": dict(grammar_totals or {}),
            "trigger_readiness": trigger_readiness,
            "up_down_audit": up_down_audit,
            "cdna_participation": cdna_participation,
            "candidate_type_counts": dict(candidate_type_totals),
            "monotonicity": dict(mono_totals),
            # Actionable buy-only only; short-required diagnostics are excluded.
            "top_actionable_buy_only_blockers": [{"blocker": b, "count": c} for b, c in blocker_totals.most_common(15)],
            "missing_ask_blockers_buy_only": missing_ask_buy_only,
            "quote_side_diagnostics": dict(quote_side_totals),
            "economic_rejections": dict(economic_totals),
            # Back-compat alias (still actionable buy-only only).
            "top_hard_blockers": [{"blocker": b, "count": c} for b, c in blocker_totals.most_common(15)],
        },
        "cadence": {
            "burst_mode": bool(burst_mode),
            "burst_interval_seconds": float(burst_interval_seconds),
            "normal_interval_seconds": float(normal_interval_seconds if normal_interval_seconds is not None else interval_seconds),
            "boundary_window_seconds": float(boundary_window_seconds),
            "iterations_by_mode": dict(cadence_totals),
        },
        "windows_scanned": sorted({inst for rec in iteration_records for inst in (rec.get("window_instants") or [])}),
        "iterations": iteration_records,
        "paper_candidates": paper_deduped[:25],
        "cdna_fill_first_candidates": (cdna_rows or [])[:25],
        "best_post_fee_rows": deduped[:25],
        "top_buy_only_near_misses": near_deduped[:15],
        "latest_iteration_errors": list(latest_errors or []),
        "safety": {
            "diagnostic_only": True,
            "public_read_only": True,
            "cdna_network_fetch_attempted": False,
            "uses_midpoint": False,
            "orders_or_execution_logic_added": False,
            "auth_or_account_logic_added": False,
            "browser_automation_added": False,
            "alerts_or_notifications_added": False,
        },
    }


def _collect_iteration_errors(report: dict[str, Any]) -> list[str]:
    """Surface collector/network warnings from the scout's load diagnostics."""
    out: list[str] = []
    load = report.get("load_diagnostics") or {}
    for key in ("source", "error", "errors"):
        val = load.get(key)
        if isinstance(val, str) and val and key != "source":
            out.append(val)
        elif isinstance(val, list):
            out.extend(str(v) for v in val[:5])
    per_asset = load.get("per_asset_diagnostics") or load.get("per_asset") or []
    if isinstance(per_asset, list):
        for rec in per_asset:
            if not isinstance(rec, dict):
                continue
            asset = rec.get("asset") or ""
            for venue in ("kalshi_diagnostics", "polymarket_diagnostics", "cdna_diagnostics"):
                diag = rec.get(venue) or {}
                for w in (diag.get("warnings") or [])[:3]:
                    out.append(f"{asset}/{venue.split('_')[0]}: {w}")
                fails = diag.get("clob_fetch_failures")
                if fails:
                    out.append(f"{asset}/{venue.split('_')[0]}: clob_fetch_failures={fails}")
    # De-dup, cap.
    seen: set[str] = set()
    deduped: list[str] = []
    for item in out:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped[:25]


def _empty_summary(*, started, updated, assets, interval_seconds, iterations_requested, operator_risk_mode, output_dir) -> dict[str, Any]:
    summary = _build_summary(
        schema_started=started, updated=updated, assets=assets, interval_seconds=interval_seconds,
        iterations_requested=iterations_requested, operator_risk_mode=operator_risk_mode,
        source_basis_buffer_bps=0.0, include_cdna=False, iteration_records=[], paper_candidates_total=0,
        best_net=None, best_adjusted=None, candidate_type_totals=Counter(), blocker_totals=Counter(),
        mono_totals=Counter(), best_rows=[], output_dir=output_dir,
    )
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    (Path(output_dir) / "watch_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (Path(output_dir) / "watch_summary.md").write_text(_render_summary_markdown(summary), encoding="utf-8")
    return summary


def _quote_coverage_by_venue_side(report: dict[str, Any]) -> dict[str, int]:
    scout_qcov = report.get("quote_coverage_diagnostics") or {}
    if scout_qcov:
        usable = dict(scout_qcov.get("usable") or scout_qcov.get("raw") or {})
        for key in _QUOTE_COVERAGE_KEYS:
            if key not in usable and key in scout_qcov:
                usable[key] = scout_qcov.get(key)
        for key in (
            "complement_quote_used_count",
            "complement_quote_possible_but_missing_bid",
            "explicit_ask_used_count",
            "gamma_top_of_book_fallback_count",
            "clob_book_used_count",
        ):
            usable[key] = scout_qcov.get(key, usable.get(key, 0))
        return _with_int_defaults(usable, _QUOTE_COVERAGE_KEYS)

    coverage: Counter = Counter({key: 0 for key in _QUOTE_COVERAGE_KEYS})
    row_complement_count = 0
    for row in report.get("rows") or []:
        if row.get("complement_quote_used"):
            row_complement_count += 1
        for leg in row.get("basket_legs") or []:
            key = _quote_presence_key(leg.get("platform"), leg.get("side"))
            if key and _float(leg.get("ask")) is not None:
                coverage[key] += 1
            if leg.get("complement_used"):
                row_complement_count += 1

    counts = report.get("summary_counts") or {}
    field_complement = int(counts.get("complement_quote_rows", report.get("complement_quote_rows", 0)) or 0)
    qsd_complement = sum(
        int(v)
        for k, v in (report.get("quote_side_diagnostic_counts") or {}).items()
        if str(k).startswith("complement_quote_used")
    )
    coverage["complement_quote_used_count"] = max(row_complement_count, field_complement, qsd_complement)
    return _with_int_defaults(coverage, _QUOTE_COVERAGE_KEYS)


def _raw_quote_coverage_by_venue_side(report: dict[str, Any]) -> dict[str, int]:
    scout_qcov = report.get("quote_coverage_diagnostics") or {}
    if scout_qcov:
        raw = dict(scout_qcov.get("raw") or {})
        for key in (
            "complement_quote_used_count",
            "complement_quote_possible_but_missing_bid",
            "explicit_ask_used_count",
            "gamma_top_of_book_fallback_count",
            "clob_book_used_count",
        ):
            raw[key] = scout_qcov.get(key, raw.get(key, 0))
        return _with_int_defaults(raw, _QUOTE_COVERAGE_KEYS)
    return _quote_coverage_by_venue_side(report)


def _quote_presence_key(platform: Any, side: Any) -> str | None:
    p = str(platform or "").strip().lower()
    s = str(side or "").strip().upper()
    if p == "cdna":
        if s.endswith("YES"):
            return "cdna_display_yes_present"
        if s.endswith("NO"):
            return "cdna_display_no_present"
    if p in {"kalshi", "polymarket"}:
        if s.endswith("YES"):
            return f"{p}_yes_ask_present"
        if s.endswith("NO"):
            return f"{p}_no_ask_present"
    return None


def _buy_only_net_diagnostics(report: dict[str, Any]) -> dict[str, Any]:
    rows = list(report.get("rows") or [])
    buy_only_priced = [
        r for r in rows
        if _row_is_buy_only(r) and _float(r.get("net_edge_after_fees")) is not None
    ]
    buy_only_adjusted = [
        r for r in rows
        if _row_is_buy_only(r) and _float(r.get("adjusted_net_edge_after_fees")) is not None
    ]
    near_rows = [
        r for r in (report.get("top_buy_only_near_misses") or [])
        if _float(r.get("net_edge_after_fees")) is not None
    ]
    best_priced = _max_or_none(_float(r.get("net_edge_after_fees")) for r in buy_only_priced)
    return {
        "best_buy_only_net_edge_after_fees": best_priced,
        "best_buy_only_adjusted_net_edge_after_fees": _max_or_none(
            _float(r.get("adjusted_net_edge_after_fees")) for r in buy_only_adjusted
        ),
        "best_priced_buy_only_net_edge_after_fees": best_priced,
        "best_priced_buy_only_net_edge_after_fees_reason": (
            "priced_buy_only_rows_found" if best_priced is not None else "no_priced_buy_only_rows"
        ),
        "best_near_miss_net_edge_after_fees": _max_or_none(
            _float(r.get("net_edge_after_fees")) for r in near_rows
        ),
        "worst_net_edge_after_fees": _min_or_none(_float(r.get("net_edge_after_fees")) for r in rows),
    }


def _near_miss_threshold_buckets(
    report: dict[str, Any], *, near_threshold: float, wide_threshold: float
) -> dict[str, int]:
    buckets = {
        "within_near_threshold": 0,
        "within_wide_threshold": 0,
        "within_2c": 0,
        "within_5c": 0,
        "within_10c": 0,
    }
    for row in report.get("rows") or []:
        if not _row_is_buy_only(row):
            continue
        net = _float(row.get("net_edge_after_fees"))
        if net is None or net > 0:
            continue
        if net >= -float(near_threshold):
            buckets["within_near_threshold"] += 1
        if net >= -float(wide_threshold):
            buckets["within_wide_threshold"] += 1
        if net >= -0.02:
            buckets["within_2c"] += 1
        if net >= -0.05:
            buckets["within_5c"] += 1
        if net >= -0.10:
            buckets["within_10c"] += 1
    return buckets


def _candidate_readiness_funnel(report: dict[str, Any]) -> dict[str, int]:
    rows = list(report.get("rows") or [])
    counts = report.get("summary_counts") or {}
    buy_only_rows = [r for r in rows if _row_is_buy_only(r)]
    complete_rows = [r for r in buy_only_rows if not _row_has_missing_required_ask(r)]
    fresh_rows = [r for r in complete_rows if _row_is_fresh(r)]
    net_positive = [r for r in fresh_rows if (_float(r.get("net_edge_after_fees")) or 0.0) > 0.0]
    adjusted_positive = [
        r for r in net_positive
        if (_float(r.get("adjusted_net_edge_after_fees")) if _float(r.get("adjusted_net_edge_after_fees")) is not None else _float(r.get("net_edge_after_fees")) or 0.0) > 0.0
    ]
    fallback_total = len(rows)
    paper = int(counts.get("paper_candidate_rows", sum(1 for r in rows if r.get("paper_candidate"))) or 0)
    return _with_int_defaults(
        {
            "total_rows": int(counts.get("rows", fallback_total) or fallback_total),
            "buy_only_rows": int(counts.get("buy_only_rows", len(buy_only_rows)) or len(buy_only_rows)),
            "rows_with_all_required_asks": len(complete_rows),
            "rows_fresh": len(fresh_rows),
            "rows_net_positive_before_buffer": len(net_positive),
            "rows_net_positive_after_buffer": len(adjusted_positive),
            "paper_candidates": paper,
        },
        _FUNNEL_KEYS,
    )


def _row_is_buy_only(row: dict[str, Any]) -> bool:
    if "tradable_buy_only" in row:
        return bool(row.get("tradable_buy_only"))
    if row.get("requires_short_or_sell"):
        return False
    blockers = set(row.get("hard_blockers") or [])
    if blockers & _REQUIRES_SHORT_BLOCKERS:
        return False
    execution_type = str(row.get("candidate_execution_type") or "")
    if execution_type and execution_type != "BUY_ONLY":
        return False
    return True


def _row_has_missing_required_ask(row: dict[str, Any]) -> bool:
    labels = list(row.get("hard_blockers") or []) + list(row.get("quote_side_diagnostics") or [])
    if any(_is_missing_required_ask_label(label) for label in labels):
        return True
    if not row.get("paper_candidate") and _float(row.get("net_edge_after_fees")) is None:
        return True
    return False


def _row_is_fresh(row: dict[str, Any]) -> bool:
    labels = list(row.get("hard_blockers") or []) + list(row.get("quote_side_diagnostics") or [])
    stale_labels = {"stale_or_missing_quote", "stale_quote", "quote_stale"}
    return not any(str(label) in stale_labels or str(label).startswith("stale_") for label in labels)


def _is_missing_required_ask_label(label: Any) -> bool:
    text = str(label or "")
    if text in _MISSING_ASK_BLOCKERS:
        return True
    if text in {"missing_cdna_display_yes", "missing_cdna_display_no"}:
        return True
    return text.startswith("missing_") and (text.endswith("_ask") or "_display_" in text)


def _cdna_participation(
    report: dict[str, Any], *, include_cdna: bool, cdna_evidence_dir: Path | None
) -> dict[str, Any]:
    load = report.get("load_diagnostics") or {}
    supplied_flags: list[bool] = []
    rows_loaded = 0

    def add_diag(diag: dict[str, Any]) -> None:
        nonlocal rows_loaded
        if "supplied" in diag:
            supplied_flags.append(bool(diag.get("supplied")))
        rows_loaded += int(diag.get("rows_loaded") or 0)

    if isinstance(load.get("cdna_diagnostics"), dict):
        add_diag(load.get("cdna_diagnostics") or {})
    for rec in load.get("per_asset_diagnostics") or load.get("per_asset") or []:
        if isinstance(rec, dict) and isinstance(rec.get("cdna_diagnostics"), dict):
            add_diag(rec.get("cdna_diagnostics") or {})
    if "cdna_rows_loaded" in load:
        rows_loaded = int(load.get("cdna_rows_loaded") or 0)

    # CDNA is "supplied" if any collector diag says so, if the latest-snapshot loader
    # loaded rows (timeseries path), or if an evidence dir was provided.
    snapshot_supplied = bool((load.get("cdna_snapshot") or {}).get("cdna_supplied"))
    supplied = (
        any(supplied_flags) or rows_loaded > 0 or snapshot_supplied
        or bool(include_cdna and cdna_evidence_dir is not None)
    )
    if not include_cdna and not supplied_flags and rows_loaded == 0 and not snapshot_supplied:
        supplied = False
    rows = report.get("rows") or []
    cdna_candidates = sum(1 for row in rows if _row_has_cdna_leg(row))
    cdna_types = Counter(
        str(row.get("candidate_type"))
        for row in rows
        if _row_has_cdna_leg(row) and row.get("candidate_type")
    )
    counts = report.get("summary_counts") or {}
    fill_first = len(report.get("cdna_fill_first_candidates") or [])
    if fill_first == 0:
        fill_first = int(counts.get("cdna_fill_first_paper_candidate_rows", 0) or 0)
    missing_reason = ""
    if include_cdna and not supplied:
        missing_reason = "CDNA not supplied; third venue not active in this run."
    elif supplied and rows_loaded == 0:
        missing_reason = "CDNA supplied but no rows loaded."
    return _normalize_cdna_participation(
        {
            "cdna_supplied": supplied,
            "cdna_rows_loaded": rows_loaded,
            "cdna_candidates_considered": cdna_candidates,
            "cdna_candidate_types_generated": dict(cdna_types),
            "cdna_fill_first_candidates": fill_first,
            "cdna_missing_reason": missing_reason,
        }
    )


def _row_has_cdna_leg(row: dict[str, Any]) -> bool:
    if str(row.get("paper_candidate_class") or "") == "CDNA_FILL_FIRST":
        return True
    return any(str(leg.get("platform") or "").lower() == "cdna" for leg in row.get("basket_legs") or [])


def _normalize_cdna_participation(value: Any) -> dict[str, Any]:
    data = value or {}
    return {
        "cdna_supplied": bool(data.get("cdna_supplied")),
        "cdna_rows_loaded": int(data.get("cdna_rows_loaded") or 0),
        "cdna_candidates_considered": int(data.get("cdna_candidates_considered") or 0),
        "cdna_fill_first_candidates": int(data.get("cdna_fill_first_candidates") or 0),
        "cdna_candidate_types_generated": dict(data.get("cdna_candidate_types_generated") or {}),
        "cdna_missing_reason": str(data.get("cdna_missing_reason") or ""),
    }


def _empty_up_down_audit() -> dict[str, Any]:
    return {
        "up_down_kalshi_rows": 0,
        "up_down_polymarket_rows": 0,
        "up_down_exact_window_matches": 0,
        "up_down_candidates_generated": 0,
        "up_down_paper_candidates": 0,
        "sample_kalshi_windows": [],
        "sample_polymarket_windows": [],
        "top_post_fee_up_down_rows": [],
        "warning": "",
    }


def _merge_up_down_audit(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key in (
        "up_down_kalshi_rows",
        "up_down_polymarket_rows",
        "up_down_exact_window_matches",
        "up_down_candidates_generated",
        "up_down_paper_candidates",
    ):
        target[key] = int(target.get(key, 0)) + int(source.get(key, 0) or 0)
    for key in ("sample_kalshi_windows", "sample_polymarket_windows", "top_post_fee_up_down_rows"):
        target.setdefault(key, [])
        target[key].extend((source.get(key) or [])[: max(0, 10 - len(target[key]))])
    if source.get("warning") and not target.get("warning"):
        target["warning"] = source.get("warning")


def _normalize_up_down_audit(value: Any) -> dict[str, Any]:
    out = _empty_up_down_audit()
    _merge_up_down_audit(out, value or {})
    return out


def _classify_run_quality(
    *,
    funnel: dict[str, int],
    paper_candidates: int,
    blocker_totals: Counter,
    quote_side_totals: Counter,
    economic_totals: Counter,
    candidate_generation_coverage: list[dict[str, Any]] | None = None,
    up_down_audit: dict[str, Any] | None = None,
    compatible_windows: int = 0,
) -> dict[str, str]:
    if int(paper_candidates or 0) > 0:
        return {"label": "CANDIDATES_FOUND", "reason": "at least one paper candidate passed every gate"}
    total_rows = int(funnel.get("total_rows") or 0)
    buy_only_rows = int(funnel.get("buy_only_rows") or 0)
    complete_rows = int(funnel.get("rows_with_all_required_asks") or 0)
    missing_quote_pressure = _missing_quote_pressure(blocker_totals, quote_side_totals)
    coverage = candidate_generation_coverage or []
    updown = up_down_audit or {}
    if total_rows == 0 and int(compatible_windows or 0) == 0 and int(updown.get("up_down_exact_window_matches") or 0) == 0:
        return {"label": "NO_COMPATIBLE_WINDOWS", "reason": "no same-time/same-grammar windows were available"}
    if any(int(c.get("attempted") or 0) > 0 and int(c.get("generated") or 0) == 0 for c in coverage):
        return {"label": "CANDIDATE_GENERATION_GAP", "reason": "at least one known candidate class was attempted but generated no rows"}
    if int(updown.get("up_down_exact_window_matches") or 0) > 0 and int(updown.get("up_down_candidates_generated") or 0) == 0:
        return {"label": "CANDIDATE_GENERATION_GAP", "reason": "up/down exact window matches existed but no up/down candidates were generated"}
    if total_rows == 0:
        return {"label": "CANDIDATE_GENERATION_GAP", "reason": "compatible windows existed but no scout rows were generated"}
    if buy_only_rows == 0:
        return {"label": "POOR_QUOTE_COVERAGE", "reason": "no tradable buy-only rows were generated"}
    if complete_rows == 0:
        return {"label": "POOR_QUOTE_COVERAGE", "reason": "no buy-only rows had all required asks"}
    coverage_ratio = complete_rows / max(buy_only_rows, 1)
    if missing_quote_pressure > 0 and coverage_ratio < 0.5:
        return {
            "label": "POOR_QUOTE_COVERAGE",
            "reason": f"only {complete_rows}/{buy_only_rows} buy-only rows had all required asks",
        }
    economic_pressure = sum(int(v) for v in (economic_totals or {}).values())
    if economic_pressure > 0:
        return {"label": "QUOTES_OK_NO_EDGE", "reason": "complete quotes existed, but priced rows lost to fees or buffer"}
    return {"label": "QUOTES_OK_NO_EDGE", "reason": "complete/fresh quote coverage existed, but no row cleared the edge gates"}


def _missing_quote_pressure(*counters: Counter) -> int:
    total = 0
    for counter in counters:
        for key, value in (counter or {}).items():
            if _is_missing_required_ask_label(key):
                total += int(value)
    return total


def _near_miss_distance(near_rows: list[dict[str, Any]], best_net: Any) -> dict[str, Any]:
    sorted_near = sorted(
        near_rows or [],
        key=lambda r: _float(r.get("net_edge_after_fees")) if _float(r.get("net_edge_after_fees")) is not None else -1e9,
        reverse=True,
    )
    top = sorted_near[0] if sorted_near else None
    net = _float(top.get("net_edge_after_fees")) if top else _float(best_net)
    return {
        "best_net_edge_after_fees": net,
        "cents_needed_to_break_even": None if net is None else round(max(0.0, -net) * 100.0, 4),
        "blocker_preventing_top_near_miss": _top_near_miss_blocker(top, net),
    }


def _top_near_miss_blocker(row: dict[str, Any] | None, net: float | None) -> str | None:
    if not row:
        return None
    blockers = [str(b) for b in (row.get("hard_blockers") or []) if str(b)]
    if blockers:
        return blockers[0]
    missing = [str(b) for b in (row.get("missing_to_candidate") or []) if str(b)]
    if missing:
        return missing[0]
    if net is not None and net <= 0:
        return "no_positive_net_edge_after_fees"
    reason = str(row.get("near_miss_reason") or "")
    return reason or None


def _with_int_defaults(values: Any, keys: tuple[str, ...]) -> dict[str, int]:
    src = values or {}
    out = {key: int(src.get(key) or 0) for key in keys}
    for key, value in src.items():
        if key not in out:
            out[str(key)] = int(value or 0)
    return out


def _leaderboard_row(r: dict[str, Any], ts: str) -> dict[str, Any]:
    return {
        "iteration_timestamp": ts,
        "asset": r.get("asset"),
        "candidate_type": r.get("candidate_type"),
        "paper_candidate": bool(r.get("paper_candidate")),
        "paper_candidate_class": r.get("paper_candidate_class"),
        "target_instant_utc": r.get("target_instant_utc"),
        "lower_strike": r.get("lower_strike"),
        "higher_strike": r.get("higher_strike"),
        "net_edge_after_fees": _float(r.get("net_edge_after_fees")),
        "adjusted_net_edge_after_fees": _float(r.get("adjusted_net_edge_after_fees")),
        "total_cost_after_fees": _float(r.get("total_cost_after_fees")),
        "hard_blockers": list(r.get("hard_blockers") or []),
    }


def _near_miss_row(nm: dict[str, Any], ts: str) -> dict[str, Any]:
    return {
        "iteration_timestamp": ts,
        "asset": nm.get("asset"),
        "candidate_type": nm.get("candidate_type"),
        "paper_candidate": bool(nm.get("paper_candidate")),
        "near_miss": bool(nm.get("near_miss")),
        "near_miss_reason": nm.get("near_miss_reason") or "",
        "near_miss_primary_reason": nm.get("near_miss_primary_reason") or "",
        "missing_to_candidate": list(nm.get("missing_to_candidate") or []),
        "complement_quote_used": bool(nm.get("complement_quote_used")),
        "target_instant_utc": nm.get("target_instant_utc"),
        "lower_strike": nm.get("lower_strike"),
        "higher_strike": nm.get("higher_strike"),
        "net_edge_after_fees": _float(nm.get("net_edge_after_fees")),
        "adjusted_net_edge_after_fees": _float(nm.get("adjusted_net_edge_after_fees")),
        "hard_blockers": list(nm.get("hard_blockers") or []),
    }


def _render_summary_markdown(summary: dict[str, Any]) -> str:
    t = summary.get("totals") or {}
    lines = [
        "# Crypto Structural Arb Watch Summary",
        "",
        "Rolling summary of repeated `crypto-structural-payoff-arb-scout` runs over live crypto "
        "windows. Public-read-only; CDNA saved-evidence-only; asks only; no midpoint; no alerts; no trading.",
        "",
        "## Summary",
        "",
        f"- assets: `{', '.join(summary.get('assets') or [])}`  operator_risk_mode: `{summary.get('operator_risk_mode')}`",
        f"- iterations: `{summary.get('iterations_completed')}` of `{summary.get('iterations_requested')}`  "
        f"interval_seconds: `{summary.get('interval_seconds')}`",
        f"- started_at: `{summary.get('started_at')}`  updated_at: `{summary.get('updated_at')}`",
        f"- run_quality_label: `{summary.get('run_quality_label')}`  "
        f"reason: `{_md(summary.get('run_quality_reason'))}`",
        f"- paper_candidates_found (cumulative): `{t.get('paper_candidates_found', 0)}`  "
        f"manual_micro_test_candidates (cumulative): `{t.get('manual_micro_test_candidates', 0)}`  "
        f"buy_only_rows (cumulative): `{t.get('buy_only_rows', 0)}`",
        f"- complement_quote_rows (cumulative): `{t.get('complement_quote_rows', 0)}` "
        f"(NO/YES ask derived from opposite bid; flagged, size-capped)",
        f"- diagnostic_only_short_required_rows (cumulative): `{t.get('diagnostic_only_short_required_rows', 0)}` "
        f"(excluded from actionable pressure — Mason cannot short)",
        f"- monotonicity_covers_one_leg_missing (cumulative): `{t.get('monotonicity_covers_one_leg_missing', 0)}`",
        f"- best_priced_buy_only_net_edge_after_fees: `{_fmt(t.get('best_priced_buy_only_net_edge_after_fees'))}`  "
        f"reason: `{_md(t.get('best_priced_buy_only_net_edge_after_fees_reason'))}`",
        f"- best_near_miss_net_edge_after_fees: `{_fmt(t.get('best_near_miss_net_edge_after_fees'))}`  "
        f"worst_net_edge_after_fees: `{_fmt(t.get('worst_net_edge_after_fees'))}`",
        f"- cadence: `{_cadence_line(summary.get('cadence') or {})}`",
        f"- candidate_type_counts: `{_fmt_counter(t.get('candidate_type_counts') or {})}`",
        f"- monotonicity: `{_fmt_counter(t.get('monotonicity') or {})}`",
        f"- missing_ask_blockers_buy_only: `{_fmt_counter(t.get('missing_ask_blockers_buy_only') or {})}`",
        f"- windows_scanned: `{len(summary.get('windows_scanned') or [])}`",
        "",
        "## Run Quality Diagnostics",
        "",
        f"- label: `{summary.get('run_quality_label')}`",
        f"- reason: `{_md(summary.get('run_quality_reason'))}`",
        "",
        "### Candidate Readiness Funnel",
        "",
        "| Gate | Rows |",
        "|---|---:|",
    ]
    funnel = t.get("candidate_readiness_funnel") or {}
    for key in _FUNNEL_KEYS:
        lines.append(f"| {_md(key)} | {_md(funnel.get(key, 0))} |")

    lines.extend([
        "",
        "### Candidate Generation Coverage (per class)",
        "",
        "attempted -> generated -> priced -> paper. attempted>generated means the class IS "
        "evaluated but sampled out / not yet quotable — proof it is not only monotonicity covers.",
        "",
        "| Candidate class | Attempted | Generated | Priced | Net+ | Paper | Missing ask | Stale | No positive net | Shape/time |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    cgc = t.get("candidate_generation_coverage") or []
    if not cgc:
        lines.append("| none | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |")
    for c in cgc:
        lines.append(
            f"| {_md(c.get('candidate_class'))} | {_md(c.get('attempted', 0))} | {_md(c.get('generated', 0))} | "
            f"{_md(c.get('priced', 0))} | {_md(c.get('net_positive', 0))} | "
            f"{_md(c.get('paper_candidate', c.get('paper', 0)))} | {_md(c.get('blocked_missing_ask', 0))} | "
            f"{_md(c.get('blocked_stale', 0))} | {_md(c.get('blocked_no_positive_net', 0))} | "
            f"{_md(c.get('blocked_shape_or_time', 0))} |"
        )
    nb = t.get("near_miss_threshold_buckets") or {}
    lines.append("")
    lines.append(
        f"- near-miss buckets (buy-only below break-even, cumulative): within_2c=`{nb.get('within_2c', 0)}`  "
        f"within_5c=`{nb.get('within_5c', 0)}`  within_10c=`{nb.get('within_10c', 0)}`  "
        f"within_configured=`{nb.get('within_near_threshold', 0)}`  within_wide=`{nb.get('within_wide_threshold', 0)}`"
    )
    if int(nb.get("within_wide_threshold", nb.get("within_10c", 0)) or 0) == 0:
        best_priced = t.get("best_priced_buy_only_net_edge_after_fees")
        if best_priced is None:
            lines.append("- No priced buy-only rows; no close buy-only near misses.")
        else:
            lines.append(
                f"- No close buy-only near misses; best priced row was `{_fmt(max(0.0, -float(best_priced)) * 100)}` "
                "cents below break-even."
            )

    lines.extend(
        [
            "",
            "### Quote Coverage by Venue/Side",
            "",
            "| Metric | Raw | Usable |",
            "|---|---:|---:|",
        ]
    )
    coverage = t.get("quote_coverage_by_venue_side") or {}
    raw_coverage = t.get("raw_quote_coverage_by_venue_side") or {}
    for key in _QUOTE_COVERAGE_KEYS:
        lines.append(f"| {_md(key)} | {_md(raw_coverage.get(key, 0))} | {_md(coverage.get(key, 0))} |")

    uda = t.get("up_down_audit") or {}
    lines.extend(
        [
            "",
            "### Direct Up/Down Audit",
            "",
            f"- up_down_kalshi_rows: `{uda.get('up_down_kalshi_rows', 0)}`",
            f"- up_down_polymarket_rows: `{uda.get('up_down_polymarket_rows', 0)}`",
            f"- up_down_exact_window_matches: `{uda.get('up_down_exact_window_matches', 0)}`",
            f"- up_down_candidates_generated: `{uda.get('up_down_candidates_generated', 0)}`",
            f"- up_down_paper_candidates: `{uda.get('up_down_paper_candidates', 0)}`",
            f"- warning: `{_md(uda.get('warning') or 'none')}`",
            "",
            "| Sample | Platform | Start | End | Interval seconds |",
            "|---|---|---|---|---:|",
        ]
    )
    samples = (uda.get("sample_kalshi_windows") or [])[:3] + (uda.get("sample_polymarket_windows") or [])[:3]
    if not samples:
        lines.append("| none |  |  |  |  |")
    for sample in samples:
        lines.append(
            f"| {_md(sample.get('asset'))} | {_md(sample.get('platform'))} | "
            f"{_md(sample.get('reference_start_utc'))} | {_md(sample.get('target_instant_utc'))} | "
            f"{_md(sample.get('interval_length_seconds'))} |"
        )

    near_distance = t.get("near_miss_distance") or {}
    cdna_participation = t.get("cdna_participation") or {}
    lines.extend(
        [
            "",
            "### Near-Miss Distance",
            "",
            f"- best_near_miss_net_edge_after_fees: `{_fmt(near_distance.get('best_net_edge_after_fees'))}`",
            f"- cents_needed_to_break_even: `{_fmt(near_distance.get('cents_needed_to_break_even'))}`",
            f"- blocker_preventing_top_near_miss: `{_md(near_distance.get('blocker_preventing_top_near_miss') or 'none')}`",
            "",
            "### CDNA Participation",
            "",
            "| Metric | Value |",
            "|---|---:|",
        ]
    )
    for key in (
        "cdna_supplied",
        "cdna_rows_loaded",
        "cdna_candidates_considered",
        "cdna_candidate_types_generated",
        "cdna_fill_first_candidates",
        "cdna_missing_reason",
    ):
        value = cdna_participation.get(key, False if key == "cdna_supplied" else 0)
        lines.append(f"| {_md(key)} | {_md(str(value).lower())} |")

    lines.extend([
        "",
        "## Paper Candidates",
        "",
        "Buy-only rows that passed every gate (post-fee positive, no unaccepted hard blockers). "
        "These are the manual-micro-test candidates.",
        "",
        "| Iter ts | Asset | Type | Class | Net edge | Adj net | Lower K | Higher K |",
        "|---|---|---|---|---:|---:|---:|---:|",
    ])
    pcs = summary.get("paper_candidates") or []
    if not pcs:
        lines.append("| none |  |  |  |  |  |  |  |")
    for r in pcs[:25]:
        lines.append(
            "| "
            f"{_md(r.get('iteration_timestamp'))} | {_md(r.get('asset'))} | {_md(r.get('candidate_type'))} | "
            f"{_md(r.get('paper_candidate_class'))} | {_md(r.get('net_edge_after_fees'))} | "
            f"{_md(r.get('adjusted_net_edge_after_fees'))} | {_md(r.get('lower_strike'))} | {_md(r.get('higher_strike'))} |"
        )
    lines.extend([
        "",
        "## Best priced buy-only rows (post-fee, incl. economic rejections)",
        "",
        "| Iter ts | Asset | Type | Class | Net edge | Adj net | Lower K | Higher K | Blockers |",
        "|---|---|---|---|---:|---:|---:|---:|---|",
    ])
    rows = summary.get("best_post_fee_rows") or []
    if not rows:
        lines.append("| none |  |  |  |  |  |  |  |  |")
    for r in rows[:25]:
        lines.append(
            "| "
            f"{_md(r.get('iteration_timestamp'))} | {_md(r.get('asset'))} | {_md(r.get('candidate_type'))} | "
            f"{_md(r.get('paper_candidate_class'))} | {_md(r.get('net_edge_after_fees'))} | "
            f"{_md(r.get('adjusted_net_edge_after_fees'))} | {_md(r.get('lower_strike'))} | "
            f"{_md(r.get('higher_strike'))} | {_md(', '.join(r.get('hard_blockers') or []))} |"
        )

    lines.extend(
        [
            "",
            "## Top buy-only near-misses (by net edge after fees)",
            "",
            "Buy-only, tradable rows ranked by net edge. Short-required diagnostics are excluded. "
            "`Primary reason` is one of missing_quote / stale_quote / negative_net / basis_buffer.",
            "",
            "| Iter ts | Asset | Type | Paper | Net edge | Primary reason | Missing to candidate |",
            "|---|---|---|---|---:|---|---|",
        ]
    )
    near = summary.get("top_buy_only_near_misses") or []
    if not near:
        lines.append("| none |  |  |  |  |  |  |")
    for r in near[:15]:
        lines.append(
            "| "
            f"{_md(r.get('iteration_timestamp'))} | {_md(r.get('asset'))} | {_md(r.get('candidate_type'))} | "
            f"{_md('yes' if r.get('paper_candidate') else 'no')} | {_md(r.get('net_edge_after_fees'))} | "
            f"{_md(r.get('near_miss_primary_reason') or r.get('near_miss_reason') or '')} | "
            f"{_md('; '.join(r.get('missing_to_candidate') or []) or ', '.join(r.get('hard_blockers') or []))} |"
        )

    lines.extend(["", "## Actionable buy-only blockers (cumulative)", "",
                  "Tradable buy-only rows only. Short-required diagnostics are NOT counted here.",
                  "", "| Blocker | Count |", "|---|---:|"])
    if not t.get("top_actionable_buy_only_blockers"):
        lines.append("| none | 0 |")
    for item in t.get("top_actionable_buy_only_blockers") or []:
        lines.append(f"| {_md(item.get('blocker'))} | {_md(item.get('count'))} |")

    lines.extend(["", "## Missing Buy-Side Quote Diagnostics (venue + side, cumulative)", "",
                  "Exactly which buy leg was unquoted, by venue and side. `complement_quote_used:*` = a "
                  "NO/YES ask was derived from the opposite executable bid (never a midpoint).",
                  "", "| Diagnostic | Count |", "|---|---:|"])
    qsd = sorted((t.get("quote_side_diagnostics") or {}).items(), key=lambda kv: (-kv[1], kv[0]))
    if not qsd:
        lines.append("| none | 0 |")
    for b, c in qsd[:20]:
        lines.append(f"| {_md(b)} | {_md(c)} |")

    lines.extend(["", "## Missing-ask blockers — buy-only (cumulative)", "", "| Blocker | Count |", "|---|---:|"])
    mab = sorted((t.get("missing_ask_blockers_buy_only") or {}).items(), key=lambda kv: (-kv[1], kv[0]))
    if not mab:
        lines.append("| none | 0 |")
    for b, c in mab:
        lines.append(f"| {_md(b)} | {_md(c)} |")

    lines.extend(["", "## Economic Rejections (priced but lost to fees/basis, cumulative)", "", "| Reason | Count |", "|---|---:|"])
    econ = sorted((t.get("economic_rejections") or {}).items(), key=lambda kv: (-kv[1], kv[0]))
    if not econ:
        lines.append("| none | 0 |")
    for b, c in econ:
        lines.append(f"| {_md(b)} | {_md(c)} |")

    lines.extend(["", "## CDNA Fill-First Candidates", "",
                  "Display-price/fill-first; fill CDNA first, then hedge the exact filled quantity.",
                  "", "| Iter ts | Asset | Type | Instant | Adj net | Action |", "|---|---|---|---|---:|---|"])
    cdna = summary.get("cdna_fill_first_candidates") or []
    if not cdna:
        lines.append("| none |  |  |  |  |  |")
    for r in cdna[:15]:
        lines.append(
            f"| {_md(r.get('iteration_timestamp'))} | {_md(r.get('asset'))} | {_md(r.get('candidate_type'))} | "
            f"{_md(r.get('target_instant_utc'))} | {_md(r.get('adjusted_net_edge_after_fees'))} | {_md(r.get('candidate_action'))} |"
        )

    lines.extend(["", "## Diagnostic-only: requires shorting (not tradable)", "",
                  f"- diagnostic_only_short_required_rows (cumulative): "
                  f"`{t.get('diagnostic_only_short_required_rows', 0)}`",
                  "- Excluded from actionable blocker pressure and never paper candidates (Mason cannot short)."])

    cad = summary.get("cadence") or {}
    lines.extend(["", "## Window / Time Coverage", "",
                  f"- burst_mode: `{bool(cad.get('burst_mode'))}`  "
                  f"burst_interval_seconds: `{cad.get('burst_interval_seconds')}`  "
                  f"normal_interval_seconds: `{cad.get('normal_interval_seconds')}`  "
                  f"boundary_window_seconds: `{cad.get('boundary_window_seconds')}`",
                  f"- iterations_by_cadence: `{_fmt_counter(cad.get('iterations_by_mode') or {})}`",
                  f"- distinct settlement windows scanned: `{len(summary.get('windows_scanned') or [])}`",
                  "", "| Settlement instant (UTC) |", "|---|"])
    for w in (summary.get("windows_scanned") or [])[:40]:
        lines.append(f"| {_md(w)} |")
    if not summary.get("windows_scanned"):
        lines.append("| none |")

    errs = summary.get("latest_iteration_errors") or []
    lines.extend(["", "## Latest Iteration Errors", "",
                  "Collector/network warnings from the most recent iteration (diagnostic only)."])
    if not errs:
        lines.append("- none")
    for e in errs[:25]:
        lines.append(f"- {_md(e)}")

    lines.extend(["", "## Iterations", "",
                  "| Iter | Timestamp | Cadence | Paper | Micro | Buy-only | Diag short | Best net | Actionable blockers |",
                  "|---:|---|---|---:|---:|---:|---:|---:|---|"])
    for rec in summary.get("iterations") or []:
        lines.append(
            "| "
            f"{_md(rec.get('iteration'))} | {_md(rec.get('timestamp'))} | {_md(rec.get('cadence_mode'))} | "
            f"{_md(rec.get('paper_candidates'))} | {_md(rec.get('manual_micro_test_candidates'))} | "
            f"{_md(rec.get('buy_only_rows'))} | {_md(rec.get('diagnostic_only_short_required_rows'))} | "
            f"{_md(rec.get('best_net_edge_after_fees'))} | "
            f"{_md(_fmt_blockers(rec.get('top_actionable_blockers') or rec.get('top_blockers') or []))} |"
        )

    tr = t.get("trigger_readiness") or {}
    udc = tr.get("direct_up_down_coverage") or {}
    cvc = tr.get("cross_venue_threshold_coverage") or {}
    lines.extend([
        "", "## Trigger Readiness & Surface Coverage", "",
        f"- trigger_would_have_fired (min_net_edge >= `{tr.get('min_net_edge_floor', 0.10)}`): "
        f"**{tr.get('trigger_would_have_fired')}**  best_net_edge_after_fees: `{_fmt(tr.get('best_net_edge_after_fees'))}`  "
        f"paper_candidates_found: `{tr.get('paper_candidates_found', 0)}`",
        f"- live_execution_would_be_blocked: `{tr.get('live_execution_would_be_blocked')}`  reasons: "
        f"`{', '.join(tr.get('live_execution_block_reasons') or []) or 'none'}`",
        f"- direct up/down coverage: attempted=`{udc.get('attempted', 0)}` generated=`{udc.get('generated', 0)}`",
        f"- cross-venue threshold coverage: attempted=`{cvc.get('attempted', 0)}` generated=`{cvc.get('generated', 0)}`",
        f"- contract_grammar_counts: `{_fmt_counter(t.get('contract_grammar_counts') or {})}`",
    ])

    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- diagnostic_only: `true`",
            "- public_read_only: `true`",
            "- cdna_network_fetch_attempted: `false`",
            "- uses_midpoint: `false`",
            "- alerts_or_notifications_added: `false`",
            "- orders_or_execution_logic_added: `false`",
            "- auth_or_account_logic_added: `false`",
            "- browser_automation_added: `false`",
        ]
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------- #
# Small helpers                                                                #
# ---------------------------------------------------------------------------- #


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _max_or_none(values: Any) -> float | None:
    vals = [v for v in values if v is not None]
    return max(vals) if vals else None


def _min_or_none(values: Any) -> float | None:
    vals = [v for v in values if v is not None]
    return min(vals) if vals else None


def _fmt(value: Any) -> str:
    return "n/a" if value is None else (f"{value:.6f}" if isinstance(value, float) else str(value))


def _cadence_line(cadence: dict[str, Any]) -> str:
    if not cadence:
        return "normal"
    if not cadence.get("burst_mode"):
        return f"fixed {cadence.get('normal_interval_seconds')}s"
    return (
        f"burst {cadence.get('burst_interval_seconds')}s near boundaries / "
        f"normal {cadence.get('normal_interval_seconds')}s (window {cadence.get('boundary_window_seconds')}s)"
    )


def _fmt_counter(counter: dict[str, int]) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))) or "none"


def _fmt_blockers(blockers: list[dict[str, Any]]) -> str:
    return ", ".join(f"{b.get('blocker')}={b.get('count')}" for b in (blockers or [])) or "none"


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")
