"""Daily crypto three-venue check.

Saved-evidence-only orchestrator. For each requested asset (BTC/ETH/SOL/XRP/DOGE):
  - auto-discover the most recent Kalshi/Polymarket/CDNA polished evidence file
    under the configured evidence roots (preferring polished schemas);
  - delegate to ``build_crypto_threshold_basis_review_scout_report`` (which already
    does typed-key matching, conservative fees, CDNA fill-first basket generation,
    and operator-risk-mode policy);
  - re-shape each row into the daily-check output spec
    (action / paper_candidate / paper_candidate_class / leg_1 / leg_2 /
    net_edge_after_fees / available_notional_or_size_cap / assumptions_accepted /
    hard_blockers / risk_notes / candidate_action);
  - aggregate across all assets and write a combined JSON + Markdown.

Hard guarantees:
  - No order placement, no broker auth, no browser automation, no .env writes,
    no secret printing. Saved files only.
  - No midpoint pricing. Asks only for entry, $0.02 CDNA fee.
  - Settlement-time discipline preserved: ``target_time_mismatch`` /
    ``timezone_mismatch`` / ``threshold_grid_mismatch`` remain hard blockers
    in every operator-risk-mode.
  - CDNA rows never claim ``strict_exact_arb`` pre-fill.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from relative_value.crypto_threshold_basis_review_scout import (
    build_crypto_threshold_basis_review_scout_report,
)


HttpGet = Callable[[str, float], Any]
Sleep = Callable[[float], None]
from relative_value.operator_paper_candidate_policy import (
    ACTION_IGNORE,
    ACTION_PAPER,
    ACTION_WATCH,
    CLASS_CDNA,
    CLASS_NONE,
    CLASS_OPERATOR,
    CLASS_STRICT,
    collect_hard_blockers,
    normalize_operator_risk_mode,
)


SCHEMA_KIND = "daily_crypto_three_venue_check_v1"
SCHEMA_VERSION = 1

DEFAULT_ASSETS = ("BTC", "ETH", "SOL", "XRP", "DOGE")
DEFAULT_EVIDENCE_ROOTS = (
    Path("reports/manual_evidence/automation_batch_002/crypto"),
    Path("reports/manual_evidence/automation_batch_001_polished/crypto"),
)
DEFAULT_MAX_QUOTE_AGE_SECONDS = 300.0
DEFAULT_MIN_AVAILABLE_NOTIONAL = 1.0
DEFAULT_CDNA_OPERATOR_SIZE_CAP = 1.0

POLISHED_SCHEMA = "polished_crypto_market_family_evidence_v1"


def _default_refresh_root(*, date: str | None, generated_at: datetime) -> Path:
    date_label = date or generated_at.date().isoformat()
    timestamp = generated_at.strftime("%H%M%S")
    return Path("reports") / "manual_evidence" / "daily_crypto_live" / date_label / timestamp


# ---------------------------------------------------------------------------- #
# Public entry points                                                          #
# ---------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AssetDiscovery:
    asset: str
    folder: Path | None
    kalshi_evidence: Path | None
    polymarket_evidence: Path | None
    cdna_evidence: Path | None
    schemas: dict[str, str | None]
    warnings: tuple[str, ...]


def write_daily_crypto_three_venue_check_files(
    *,
    assets: list[str],
    date: str | None,
    operator_risk_mode: str,
    include_cdna: bool,
    operator_accept_cdna_display_price_risk: bool,
    cdna_operator_size_cap: float,
    max_quote_age_seconds: float,
    min_available_notional: float,
    json_output: Path,
    markdown_output: Path,
    evidence_roots: list[Path] | None = None,
    generated_at: datetime | None = None,
    allow_top_of_book_depth: bool = False,
    operator_size_cap: float = 0.0,
    refresh_kalshi_polymarket: bool = False,
    cdna_evidence_dir: Path | None = None,
    write_refreshed_evidence_root: Path | None = None,
    http_get: "HttpGet | None" = None,
    sleep: "Sleep | None" = None,
) -> dict[str, Any]:
    report = build_daily_crypto_three_venue_check_report(
        assets=assets,
        date=date,
        operator_risk_mode=operator_risk_mode,
        include_cdna=include_cdna,
        operator_accept_cdna_display_price_risk=operator_accept_cdna_display_price_risk,
        cdna_operator_size_cap=cdna_operator_size_cap,
        max_quote_age_seconds=max_quote_age_seconds,
        min_available_notional=min_available_notional,
        evidence_roots=evidence_roots,
        generated_at=generated_at,
        allow_top_of_book_depth=allow_top_of_book_depth,
        operator_size_cap=operator_size_cap,
        refresh_kalshi_polymarket=refresh_kalshi_polymarket,
        cdna_evidence_dir=cdna_evidence_dir,
        write_refreshed_evidence_root=write_refreshed_evidence_root,
        http_get=http_get,
        sleep=sleep,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_daily_crypto_three_venue_check_markdown(report), encoding="utf-8")
    return report


def build_daily_crypto_three_venue_check_report(
    *,
    assets: list[str],
    date: str | None,
    operator_risk_mode: str,
    include_cdna: bool,
    operator_accept_cdna_display_price_risk: bool,
    cdna_operator_size_cap: float = DEFAULT_CDNA_OPERATOR_SIZE_CAP,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
    min_available_notional: float = DEFAULT_MIN_AVAILABLE_NOTIONAL,
    evidence_roots: list[Path] | None = None,
    generated_at: datetime | None = None,
    allow_top_of_book_depth: bool = False,
    operator_size_cap: float = 0.0,
    refresh_kalshi_polymarket: bool = False,
    cdna_evidence_dir: Path | None = None,
    write_refreshed_evidence_root: Path | None = None,
    http_get: "HttpGet | None" = None,
    sleep: "Sleep | None" = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    risk_mode = normalize_operator_risk_mode(operator_risk_mode)
    asset_list = [str(a).strip().upper() for a in assets if str(a).strip()]

    refresh_summary: dict[str, Any] | None = None
    if refresh_kalshi_polymarket:
        # Local import so the collector module is only loaded when refresh is requested;
        # keeps the import graph small for tests that never refresh.
        from relative_value.daily_crypto_evidence_collector import (  # noqa: WPS433
            write_daily_crypto_live_evidence,
        )

        refresh_root = (
            write_refreshed_evidence_root
            or _default_refresh_root(date=date, generated_at=generated)
        )
        refresh_summary = write_daily_crypto_live_evidence(
            assets=asset_list,
            output_root=refresh_root,
            generated_at=generated,
            http_get=http_get,
            cdna_evidence_dir=cdna_evidence_dir,
            target_date=date,
            sleep=sleep,
        )
        evidence_roots = [refresh_root, *list(evidence_roots or [])]

    roots = [Path(p) for p in (evidence_roots or DEFAULT_EVIDENCE_ROOTS)]

    asset_reports: list[dict[str, Any]] = []
    combined_rows: list[dict[str, Any]] = []
    unmatched_time_rows: list[dict[str, Any]] = []
    unmatched_venue_rows: list[dict[str, Any]] = []

    refresh_records_by_asset: dict[str, dict[str, Any]] = {}
    if refresh_summary:
        for rec in refresh_summary.get("per_asset") or []:
            refresh_records_by_asset[str(rec.get("asset") or "").upper()] = rec

    for asset in asset_list:
        discovery = _discover_asset_evidence(asset, roots, include_cdna=include_cdna)
        asset_summary: dict[str, Any] = {
            "asset": asset,
            "folder": str(discovery.folder) if discovery.folder else None,
            "kalshi_evidence": str(discovery.kalshi_evidence) if discovery.kalshi_evidence else None,
            "polymarket_evidence": str(discovery.polymarket_evidence) if discovery.polymarket_evidence else None,
            "cdna_evidence": str(discovery.cdna_evidence) if discovery.cdna_evidence else None,
            "schemas": discovery.schemas,
            "warnings": list(discovery.warnings),
        }
        refresh_record = refresh_records_by_asset.get(asset)
        if refresh_record:
            for k in (
                "kalshi_markets_found",
                "kalshi_markets_discovered",
                "kalshi_markets_after_shape_filter",
                "kalshi_series_queried",
                "kalshi_endpoints_queried",
                "kalshi_events_found",
                "kalshi_rejection_reasons",
                "polymarket_markets_found",
                "polymarket_search_queries_attempted",
                "polymarket_events_found",
                "polymarket_candidate_markets_found",
                "polymarket_markets_after_shape_filter",
                "polymarket_rejection_reasons",
                "polymarket_query_strategies",
                "polymarket_clob_fetch_failures",
                "polymarket_gamma_fallback_used",
                "polymarket_missing_ask_outcomes",
                "polymarket_clob_error_samples",
                "cdna_files_copied",
            ):
                if k in refresh_record:
                    asset_summary[k] = refresh_record[k]
        if discovery.kalshi_evidence is None or discovery.polymarket_evidence is None:
            asset_summary["status"] = "MISSING_EVIDENCE"
            asset_summary["rows"] = 0
            asset_summary["paper_candidate_rows"] = 0
            asset_summary["kalshi_market_count"] = int(refresh_record.get("kalshi_markets_found", 0)) if refresh_record else 0
            asset_summary["polymarket_market_count"] = int(refresh_record.get("polymarket_markets_found", 0)) if refresh_record else 0
            asset_summary["cdna_market_count"] = 0
            asset_reports.append(asset_summary)
            continue
        try:
            scout_report = build_crypto_threshold_basis_review_scout_report(
                kalshi_evidence=discovery.kalshi_evidence,
                polymarket_evidence=discovery.polymarket_evidence,
                cdna_evidence=discovery.cdna_evidence if include_cdna else None,
                asset=asset,
                generated_at=generated,
                max_quote_age_seconds=max_quote_age_seconds,
                operator_risk_mode=risk_mode,
            )
        except (OSError, ValueError) as exc:
            asset_summary["status"] = "EVIDENCE_PARSE_FAILED"
            asset_summary["error"] = str(exc)
            asset_summary["rows"] = 0
            asset_summary["paper_candidate_rows"] = 0
            asset_summary["kalshi_market_count"] = 0
            asset_summary["polymarket_market_count"] = 0
            asset_summary["cdna_market_count"] = 0
            asset_reports.append(asset_summary)
            continue
        asset_rows: list[dict[str, Any]] = []
        for row in scout_report.get("rows") or []:
            if (row.get("direction") or "") == "UNMATCHED":
                # A market that exists on exactly one venue. Do not silently drop
                # it — surface it as a single-venue diagnostic row so a one-sided
                # universe is visible rather than producing an unexplained zero.
                if date and row.get("target_date") and str(row.get("target_date")) != str(date):
                    continue
                unmatched_venue_rows.append(
                    {
                        "asset": asset,
                        "venue": row.get("source_platform"),
                        "threshold": row.get("threshold"),
                        "target_date": row.get("target_date"),
                        "blockers": list(row.get("blockers") or []),
                    }
                )
                continue
            shaped = _shape_row(
                row,
                asset=asset,
                target_date_filter=date,
                operator_risk_mode=risk_mode,
                cdna_operator_size_cap=cdna_operator_size_cap,
                min_available_notional=min_available_notional,
                operator_accept_cdna_display_price_risk=operator_accept_cdna_display_price_risk,
                allow_top_of_book_depth=allow_top_of_book_depth,
                operator_size_cap=operator_size_cap,
            )
            if shaped is None:
                continue
            if "target_time_mismatch" in (shaped.get("hard_blockers") or []):
                # Surface these in a dedicated "Unmatched due to target time"
                # section instead of polluting the candidate table. They are
                # already correctly tagged action=IGNORE_BLOCKED.
                unmatched_time_rows.append(shaped)
            else:
                asset_rows.append(shaped)
        matching = _matching_diagnostics(scout_report, asset_rows=asset_rows)
        asset_summary["status"] = "OK"
        asset_summary["rows"] = len(asset_rows)
        asset_summary["paper_candidate_rows"] = sum(1 for r in asset_rows if r.get("paper_candidate"))
        asset_summary["unmatched_target_time_rows"] = sum(
            1 for r in unmatched_time_rows if r.get("asset") == asset
        )
        asset_summary["unmatched_single_venue_rows"] = sum(
            1 for r in unmatched_venue_rows if r.get("asset") == asset
        )
        asset_summary["kalshi_market_count"] = matching["kalshi_market_count"]
        asset_summary["polymarket_market_count"] = matching["polymarket_market_count"]
        asset_summary["cdna_market_count"] = matching["cdna_market_count"]
        asset_summary["matching_diagnostics"] = matching
        asset_reports.append(asset_summary)
        combined_rows.extend(asset_rows)

    combined_rows.sort(
        key=lambda r: (
            1 if r.get("paper_candidate") else 0,
            _safe_float(r.get("net_edge_after_fees")),
        ),
        reverse=True,
    )
    summary = _summary(combined_rows)
    summary["unmatched_target_time_rows"] = len(unmatched_time_rows)
    summary["unmatched_single_venue_rows"] = len(unmatched_venue_rows)

    venue_counts = {
        "kalshi_markets": sum(int(a.get("kalshi_market_count", 0)) for a in asset_reports),
        "polymarket_markets": sum(int(a.get("polymarket_market_count", 0)) for a in asset_reports),
        "cdna_markets": sum(int(a.get("cdna_market_count", 0)) for a in asset_reports),
        "typed_key_candidates": sum(
            int((a.get("matching_diagnostics") or {}).get("typed_key_candidates", 0)) for a in asset_reports
        ),
    }
    reasons = _zero_row_reasons(
        combined_rows=combined_rows,
        venue_counts=venue_counts,
        asset_reports=asset_reports,
        include_cdna=include_cdna,
        refreshed=bool(refresh_kalshi_polymarket),
        unmatched_time_rows=unmatched_time_rows,
    )
    summary["venue_market_counts"] = venue_counts
    return {
        "schema_kind": SCHEMA_KIND,
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated.isoformat(),
        "diagnostic_only": True,
        "saved_files_only": True,
        "strict_exact_arb": False,
        "operator_risk_mode": risk_mode,
        "date_filter": date,
        "assets_requested": asset_list,
        "evidence_roots": [str(p) for p in roots],
        "include_cdna": bool(include_cdna),
        "operator_accept_cdna_display_price_risk": bool(operator_accept_cdna_display_price_risk),
        "allow_top_of_book_depth": bool(allow_top_of_book_depth),
        "operator_size_cap": float(operator_size_cap) if operator_size_cap else 0.0,
        "refresh_kalshi_polymarket": bool(refresh_kalshi_polymarket),
        "refresh_summary": refresh_summary,
        "cdna_evidence_dir": str(cdna_evidence_dir) if cdna_evidence_dir else None,
        "parameters": {
            "cdna_operator_size_cap": cdna_operator_size_cap,
            "max_quote_age_seconds": max_quote_age_seconds,
            "min_available_notional": min_available_notional,
            "operator_risk_mode": risk_mode,
            "allow_top_of_book_depth": bool(allow_top_of_book_depth),
            "operator_size_cap": float(operator_size_cap) if operator_size_cap else 0.0,
        },
        "asset_reports": asset_reports,
        "rows": combined_rows,
        "unmatched_target_time_rows": unmatched_time_rows,
        "unmatched_single_venue_rows": unmatched_venue_rows,
        "venue_market_counts": venue_counts,
        "no_cross_venue_rows_reason": reasons["no_cross_venue_rows_reason"],
        "kalshi_zero_reason": reasons["kalshi_zero_reason"],
        "polymarket_zero_reason": reasons["polymarket_zero_reason"],
        "cdna_zero_reason": reasons["cdna_zero_reason"],
        "summary_counts": summary,
        "top_hard_blockers": summary["top_hard_blockers"],
        "safety": {
            "diagnostic_only": True,
            "saved_files_only": True,
            "uses_midpoint": False,
            "uses_asks_for_entry": True,
            "orders_or_execution_logic_added": False,
            "auth_or_account_logic_added": False,
            "browser_automation_added": False,
            "strict_exact_arb": False,
        },
    }


# ---------------------------------------------------------------------------- #
# Markdown                                                                     #
# ---------------------------------------------------------------------------- #


def render_daily_crypto_three_venue_check_markdown(report: dict[str, Any]) -> str:
    counts = report.get("summary_counts") or {}
    rows = report.get("rows") or []
    paper_rows = [r for r in rows if r.get("paper_candidate")]
    watch_rows = [r for r in rows if r.get("action") == ACTION_WATCH]
    blocked_rows = [r for r in rows if r.get("action") == ACTION_IGNORE]
    cdna_paper = [r for r in paper_rows if r.get("paper_candidate_class") == CLASS_CDNA]
    assumptions = sorted(
        {a for r in paper_rows for a in r.get("assumptions_accepted") or []}
    )

    lines = [
        "# Daily Crypto Three-Venue Check",
        "",
        "Saved-evidence-only, three-platform (Kalshi / Polymarket / Crypto.com Predict-CDNA) daily crypto point-in-time threshold check. Settlement-time discipline is preserved: same asset, same threshold, same comparator, same target date, same target time required. Source/index basis is operator-accepted. CDNA is display-price/fill-first only.",
        "",
        "## Summary",
        "",
        f"- operator_risk_mode: `{_md(report.get('operator_risk_mode'))}`",
        f"- assets_requested: `{', '.join(report.get('assets_requested') or [])}`",
        f"- date_filter: `{_md(report.get('date_filter') or 'any')}`",
        f"- rows: `{counts.get('rows', 0)}`",
        f"- paper_candidate_rows: `{counts.get('total_paper_candidate_rows', 0)}`",
        f"  - strict_exact: `{counts.get('strict_paper_candidate_rows', 0)}`",
        f"  - operator_accepted_risk: `{counts.get('operator_paper_candidate_rows', 0)}`",
        f"  - cdna_fill_first: `{counts.get('cdna_fill_first_paper_candidate_rows', 0)}`",
        f"- watch_rows: `{counts.get('watch_rows', 0)}`",
        f"- ignore_blocked_rows: `{counts.get('ignore_blocked_rows', 0)}`",
        f"- unmatched_target_time_rows: `{counts.get('unmatched_target_time_rows', 0)}`",
        f"- unmatched_single_venue_rows: `{counts.get('unmatched_single_venue_rows', 0)}`",
        f"- venue_market_counts: `kalshi={_venue(report, 'kalshi_markets')} "
        f"polymarket={_venue(report, 'polymarket_markets')} "
        f"cdna={_venue(report, 'cdna_markets')} "
        f"typed_key_candidates={_venue(report, 'typed_key_candidates')}`",
        "",
        "## Cross-Venue Diagnostics & Reasons",
        "",
        f"- no_cross_venue_rows_reason: `{_md(report.get('no_cross_venue_rows_reason') or 'none — cross-venue rows present')}`",
        f"- kalshi_zero_reason: `{_md(report.get('kalshi_zero_reason') or 'kalshi markets present')}`",
        f"- polymarket_zero_reason: `{_md(report.get('polymarket_zero_reason') or 'polymarket markets present')}`",
        f"- cdna_zero_reason: `{_md(report.get('cdna_zero_reason') or 'cdna markets present')}`",
        "",
        "## Evidence Load",
        "",
        "| Asset | Status | Folder | Kalshi | Polymarket | CDNA | Warnings |",
        "|---|---|---|---|---|---|---|",
    ]
    for asset_report in report.get("asset_reports") or []:
        lines.append(
            "| "
            f"{_md(asset_report.get('asset'))} | {_md(asset_report.get('status'))} | "
            f"{_md(asset_report.get('folder'))} | "
            f"{'yes' if asset_report.get('kalshi_evidence') else 'no'} | "
            f"{'yes' if asset_report.get('polymarket_evidence') else 'no'} | "
            f"{'yes' if asset_report.get('cdna_evidence') else 'no'} | "
            f"{_md(', '.join(asset_report.get('warnings') or []))} |"
        )

    # Kalshi discovery diagnostics — when refresh ran, surface which series and
    # endpoints were queried so a K=0 is explainable (series rename, all filtered).
    kalshi_diag_rows = [
        a for a in (report.get("asset_reports") or [])
        if "kalshi_series_queried" in a
    ]
    if kalshi_diag_rows:
        lines.extend(
            [
                "",
                "## Kalshi Discovery Diagnostics",
                "",
                "| Asset | Series queried | Events found | Markets discovered | After shape filter | Markets kept | Rejections |",
                "|---|---|---:|---:|---:|---:|---|",
            ]
        )
        for a in kalshi_diag_rows:
            rejections = a.get("kalshi_rejection_reasons") or {}
            rej_str = ", ".join(f"{k}={v}" for k, v in sorted(rejections.items(), key=lambda kv: -kv[1]))
            lines.append(
                "| "
                f"{_md(a.get('asset'))} | "
                f"{_md(', '.join(a.get('kalshi_series_queried') or []))} | "
                f"{_md(a.get('kalshi_events_found', 0))} | "
                f"{_md(a.get('kalshi_markets_discovered', 0))} | "
                f"{_md(a.get('kalshi_markets_after_shape_filter', 0))} | "
                f"{_md(a.get('kalshi_markets_found', 0))} | "
                f"{_md(rej_str)} |"
            )

    # Polymarket discovery diagnostics — surface every search strategy attempted
    # so a P=0 outcome is explainable rather than silent. CLOB/fallback columns
    # explain the historical "every CLOB book reports RuntimeError" failure.
    poly_diag_rows = [
        a for a in (report.get("asset_reports") or [])
        if "polymarket_search_queries_attempted" in a
    ]
    if poly_diag_rows:
        lines.extend(
            [
                "",
                "## Polymarket Discovery Diagnostics",
                "",
                "| Asset | Queries | Strategies | Events | Cand. | Kept | CLOB fails | Gamma fallback | Missing ask | Rejections |",
                "|---|---:|---|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for a in poly_diag_rows:
            rejections = a.get("polymarket_rejection_reasons") or {}
            rej_str = ", ".join(f"{k}={v}" for k, v in sorted(rejections.items(), key=lambda kv: -kv[1]))
            lines.append(
                "| "
                f"{_md(a.get('asset'))} | "
                f"{_md(a.get('polymarket_search_queries_attempted', 0))} | "
                f"{_md(', '.join(a.get('polymarket_query_strategies') or []))} | "
                f"{_md(a.get('polymarket_events_found', 0))} | "
                f"{_md(a.get('polymarket_candidate_markets_found', 0))} | "
                f"{_md(a.get('polymarket_markets_found', 0))} | "
                f"{_md(a.get('polymarket_clob_fetch_failures', 0))} | "
                f"{_md(a.get('polymarket_gamma_fallback_used', 0))} | "
                f"{_md(a.get('polymarket_missing_ask_outcomes', 0))} | "
                f"{_md(rej_str)} |"
            )
        # Sample the real CLOB error message(s) so an outage is diagnosable.
        clob_samples = [
            s
            for a in poly_diag_rows
            for s in (a.get("polymarket_clob_error_samples") or [])
        ]
        if clob_samples:
            lines.append("")
            lines.append(f"- polymarket_clob_error_samples: `{_md('; '.join(clob_samples[:5]))}`")

    # Matching diagnostics — typed-key counts before exact-time matching and why
    # candidates were rejected; sample unmatched target times when zero rows.
    match_rows = [
        a for a in (report.get("asset_reports") or [])
        if isinstance(a.get("matching_diagnostics"), dict)
    ]
    if match_rows:
        lines.extend(
            [
                "",
                "## Matching Diagnostics (typed-key -> exact-time)",
                "",
                "| Asset | K mkts | P mkts | Typed-key candidates | Exact-time rows | Rej: threshold | Rej: date | Rej: time | Rej: comparator | Rej: missing ask |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for a in match_rows:
            md = a.get("matching_diagnostics") or {}
            rej = md.get("rejected") or {}
            lines.append(
                "| "
                f"{_md(a.get('asset'))} | "
                f"{_md(md.get('kalshi_market_count', 0))} | "
                f"{_md(md.get('polymarket_market_count', 0))} | "
                f"{_md(md.get('typed_key_candidates', 0))} | "
                f"{_md(md.get('exact_time_rows', 0))} | "
                f"{_md(rej.get('threshold_mismatch', 0))} | "
                f"{_md(rej.get('date_mismatch', 0))} | "
                f"{_md(rej.get('target_time_mismatch', 0))} | "
                f"{_md(rej.get('comparator_mismatch', 0))} | "
                f"{_md(rej.get('missing_ask', 0))} |"
            )
        # When typed keys exist but no exact-time row survived, list sample times.
        for a in match_rows:
            md = a.get("matching_diagnostics") or {}
            if md.get("typed_key_candidates", 0) and not md.get("exact_time_rows", 0):
                lines.append("")
                lines.append(
                    f"- {_md(a.get('asset'))} exact-time match produced 0 rows. "
                    f"Sample Kalshi target times: `{_md(', '.join(str(t) for t in md.get('sample_kalshi_target_times') or []) or 'none')}`; "
                    f"sample Polymarket target times: `{_md(', '.join(str(t) for t in md.get('sample_polymarket_target_times') or []) or 'none')}`."
                )

    lines.extend(
        [
            "",
            "## 1. Paper Candidates (sorted by net edge after fees)",
            "",
            "| Class | Asset | Threshold | Date | Time | Direction | Net edge after fees | Size/cap | Assumptions | Candidate action |",
            "|---|---|---:|---|---|---|---:|---:|---|---|",
        ]
    )
    if not paper_rows:
        lines.append("| none |  |  |  |  |  |  |  |  |  |")
    for r in paper_rows[:50]:
        lines.append(
            "| "
            f"{_md(r.get('paper_candidate_class'))} | {_md(r.get('asset'))} | "
            f"{_md(r.get('threshold'))} | {_md(r.get('target_date'))} | "
            f"{_md(r.get('target_time'))} | {_md(r.get('direction'))} | "
            f"{_md(r.get('net_edge_after_fees'))} | "
            f"{_md(r.get('available_notional_or_size_cap'))} | "
            f"{_md(', '.join(r.get('assumptions_accepted') or []))} | "
            f"{_md(r.get('candidate_action'))} |"
        )

    lines.extend(
        [
            "",
            "## 2. Watch Rows",
            "",
            "| Asset | Threshold | Direction | Net edge after fees | Hard blockers | Risk notes |",
            "|---|---:|---|---:|---|---|",
        ]
    )
    if not watch_rows:
        lines.append("| none |  |  |  |  |  |")
    for r in watch_rows[:50]:
        lines.append(
            "| "
            f"{_md(r.get('asset'))} | {_md(r.get('threshold'))} | "
            f"{_md(r.get('direction'))} | {_md(r.get('net_edge_after_fees'))} | "
            f"{_md(', '.join(r.get('hard_blockers') or []))} | "
            f"{_md(', '.join(r.get('risk_notes') or []))} |"
        )

    lines.extend(
        [
            "",
            "## 3. Hard Blockers (top across all rows)",
            "",
            "| Blocker | Count |",
            "|---|---:|",
        ]
    )
    if not report.get("top_hard_blockers"):
        lines.append("| none | 0 |")
    for item in report.get("top_hard_blockers") or []:
        lines.append(f"| {_md(item.get('blocker'))} | {_md(item.get('count'))} |")

    lines.extend(
        [
            "",
            "## 4. Assumptions Accepted (across paper candidates)",
            "",
        ]
    )
    if assumptions:
        for a in assumptions:
            lines.append(f"- `{_md(a)}`")
    else:
        lines.append("_None._")

    lines.extend(
        [
            "",
            "## 5. CDNA Fill-First Candidates",
            "",
            "| Asset | Threshold | Direction | Net edge after fees | CDNA cap | Candidate action |",
            "|---|---:|---|---:|---:|---|",
        ]
    )
    if not cdna_paper:
        lines.append("| none |  |  |  |  |  |")
    for r in cdna_paper[:25]:
        lines.append(
            "| "
            f"{_md(r.get('asset'))} | {_md(r.get('threshold'))} | "
            f"{_md(r.get('direction'))} | {_md(r.get('net_edge_after_fees'))} | "
            f"{_md(r.get('available_notional_or_size_cap'))} | "
            f"{_md(r.get('candidate_action'))} |"
        )

    lines.extend(
        [
            "",
            "## 6. Unmatched due to target time",
            "",
            "| Asset | Threshold | Kalshi target time | Polymarket target time | Net (pre-block) |",
            "|---|---:|---|---|---:|",
        ]
    )
    unmatched_time = report.get("unmatched_target_time_rows") or []
    if not unmatched_time:
        lines.append("| none |  |  |  |  |")
    for r in unmatched_time[:50]:
        lines.append(
            "| "
            f"{_md(r.get('asset'))} | {_md(r.get('threshold'))} | "
            f"{_md((r.get('leg_1') or {}).get('quote_timestamp') or r.get('target_time'))} | "
            f"{_md((r.get('leg_2') or {}).get('quote_timestamp') or r.get('target_time'))} | "
            f"{_md(r.get('net_edge_after_fees'))} |"
        )

    # Single-venue markets — a market that exists on exactly one venue. Shown so a
    # one-sided universe is never an unexplained zero.
    unmatched_venue = report.get("unmatched_single_venue_rows") or []
    lines.extend(
        [
            "",
            "## 6b. Unmatched single-venue markets",
            "",
            f"_Total: {len(unmatched_venue)} single-venue markets (shown: up to 50)._",
            "",
            "| Asset | Venue | Threshold | Target date |",
            "|---|---|---:|---|",
        ]
    )
    if not unmatched_venue:
        lines.append("| none |  |  |  |")
    for r in unmatched_venue[:50]:
        lines.append(
            "| "
            f"{_md(r.get('asset'))} | {_md(r.get('venue'))} | "
            f"{_md(r.get('threshold'))} | {_md(r.get('target_date'))} |"
        )

    lines.extend(
        [
            "",
            "## 7. Missing CDNA evidence",
            "",
        ]
    )
    cdna_missing_assets = [
        a for a in report.get("asset_reports") or []
        if report.get("include_cdna")
        and not a.get("cdna_evidence")
        and (a.get("status") == "OK" or "cdna_evidence_missing" in (a.get("warnings") or []))
    ]
    if cdna_missing_assets:
        for a in cdna_missing_assets:
            lines.append(
                f"- {_md(a.get('asset'))}: CDNA evidence not found at "
                f"`{_md(a.get('folder'))}` — Kalshi/Polymarket scan continues."
            )
    else:
        lines.append("_None._")

    lines.extend(
        [
            "",
            "## 8. Freshness Diagnostics",
            "",
            f"- max_quote_age_seconds: `{report.get('parameters', {}).get('max_quote_age_seconds')}`",
            f"- generated_at: `{_md(report.get('generated_at'))}`",
            f"- refresh_kalshi_polymarket: `{str(bool(report.get('refresh_kalshi_polymarket'))).lower()}`",
        ]
    )
    refresh = report.get("refresh_summary") or {}
    if refresh:
        lines.append(
            f"- refresh_output_root: `{_md(refresh.get('output_root'))}` "
            f"(assets refreshed: {len(refresh.get('per_asset') or [])})"
        )
        for asset_record in (refresh.get("per_asset") or [])[:20]:
            lines.append(
                f"  - {_md(asset_record.get('asset'))}: "
                f"K={asset_record.get('kalshi_markets_found', 0)} "
                f"P={asset_record.get('polymarket_markets_found', 0)} "
                f"C={asset_record.get('cdna_files_copied', 0)} "
                f"warn={_md(', '.join(asset_record.get('warnings') or []))}"
            )

    lines.extend(
        [
            "",
            "## 9. Depth Diagnostics",
            "",
            f"- allow_top_of_book_depth: `{str(bool(report.get('allow_top_of_book_depth'))).lower()}`",
            f"- operator_size_cap: `{report.get('operator_size_cap', 0)}`",
        ]
    )

    lines.extend(
        [
            "",
            "## 10. Ignored / Blocked Rows",
            "",
            "| Asset | Threshold | Direction | Hard blockers |",
            "|---|---:|---|---|",
        ]
    )
    if not blocked_rows:
        lines.append("| none |  |  |  |")
    for r in blocked_rows[:50]:
        lines.append(
            "| "
            f"{_md(r.get('asset'))} | {_md(r.get('threshold'))} | "
            f"{_md(r.get('direction'))} | {_md(', '.join(r.get('hard_blockers') or []))} |"
        )

    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- diagnostic_only: `true`",
            "- saved_files_only: `true`",
            "- uses_asks_for_entry: `true`",
            "- uses_midpoint: `false`",
            "- orders_or_execution_logic_added: `false`",
            "- auth_or_account_logic_added: `false`",
            "- browser_automation_added: `false`",
            "- strict_exact_arb: `false`",
        ]
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------- #
# Row shaping                                                                  #
# ---------------------------------------------------------------------------- #


def _shape_row(
    row: dict[str, Any],
    *,
    asset: str,
    target_date_filter: str | None,
    operator_risk_mode: str,
    cdna_operator_size_cap: float,
    min_available_notional: float,
    operator_accept_cdna_display_price_risk: bool,
    allow_top_of_book_depth: bool = False,
    operator_size_cap: float = 0.0,
) -> dict[str, Any] | None:
    direction = row.get("direction") or ""
    if direction == "UNMATCHED":
        return None
    if target_date_filter and row.get("target_date") and str(row.get("target_date")) != str(target_date_filter):
        return None

    paper_class = row.get("paper_candidate_class") or CLASS_NONE
    is_cdna_row = paper_class == CLASS_CDNA or direction.startswith("CDNA_")
    blockers = list(row.get("blockers") or [])
    depth_permissive = bool(allow_top_of_book_depth and operator_size_cap and operator_size_cap > 0)
    hard_blockers = collect_hard_blockers(
        blockers,
        ignore_cdna_info=is_cdna_row,
        accepted_basis=operator_risk_mode in {"standard", "aggressive"},
        accepted_top_of_book_size_cap=depth_permissive,
    )

    leg_1, leg_2 = _legs(row, direction=direction, asset=asset, is_cdna=is_cdna_row, cdna_cap=cdna_operator_size_cap)

    net_edge = row.get("net_edge")
    if is_cdna_row and net_edge is None:
        # CDNA rows in the basis-review scout don't compute net post-partner-fee
        # because the CDNA leg is display-only. Use gross minus the partner fee
        # estimate if both legs are present and within the size cap.
        net_edge = row.get("gross_edge")

    available_or_cap = row.get("available_notional")
    if is_cdna_row:
        # CDNA leg defines the size cap; partner leg's notional is also bounded by it.
        cap = cdna_operator_size_cap
        if available_or_cap is None or available_or_cap > cap:
            available_or_cap = cap

    assumptions_accepted = list(row.get("assumptions_accepted") or [])
    if operator_risk_mode in {"standard", "aggressive"} and not is_cdna_row:
        if "source_index_basis_risk_accepted" not in assumptions_accepted and any(
            b in blockers
            for b in ("basis_risk_review_required", "source_index_mismatch", "source_mismatch")
        ):
            assumptions_accepted = sorted(set([*assumptions_accepted, "source_index_basis_risk_accepted"]))

    risk_notes = list(row.get("risk_notes") or [])
    candidate_action = row.get("candidate_action") or (
        "FILL_CDNA_FIRST_THEN_HEDGE_EXACT_FILLED_QUANTITY"
        if is_cdna_row and row.get("paper_candidate")
        else ("PAPER_TEST_OR_MANUAL_MICRO_TEST" if row.get("paper_candidate") else "")
    )

    action = row.get("action")
    if action not in {ACTION_PAPER, ACTION_WATCH, ACTION_IGNORE}:
        action = ACTION_IGNORE if hard_blockers else ACTION_WATCH

    # Depth-permissive promotion. When --allow-top-of-book-depth is on and the
    # operator supplies a size cap, the daily check re-evaluates whether the row
    # could be a paper candidate using top-of-book pricing. All other gates
    # (fresh quote, exact target time/threshold, positive net, asks present,
    # complement present) must still hold.
    is_paper = bool(row.get("paper_candidate"))
    if (
        depth_permissive
        and not is_paper
        and not is_cdna_row
        and operator_risk_mode == "aggressive"
        and not hard_blockers
        and net_edge is not None
        and net_edge > 0
        and row.get("kalshi_ask") is not None
        and row.get("polymarket_ask") is not None
    ):
        is_paper = True
        paper_class = CLASS_OPERATOR
        if "limited_depth_operator_size_cap_applied" not in assumptions_accepted:
            assumptions_accepted = sorted(
                set([*assumptions_accepted, "limited_depth_operator_size_cap_applied"])
            )
        action = ACTION_PAPER
        candidate_action = candidate_action or "PAPER_TEST_OR_MANUAL_MICRO_TEST"
        risk_notes = list(risk_notes)
        if not any("limited top-of-book" in note.lower() for note in risk_notes):
            risk_notes.append(
                "Operator accepted limited top-of-book depth with explicit size cap; "
                "do not exceed the cap."
            )
        capped_notional = operator_size_cap
        if available_or_cap is None or available_or_cap > capped_notional:
            available_or_cap = capped_notional
    if (
        depth_permissive
        and not is_paper
        and is_cdna_row
        and operator_risk_mode == "aggressive"
        and operator_accept_cdna_display_price_risk
        and not hard_blockers
        and net_edge is not None
        and net_edge > 0
    ):
        is_paper = True
        paper_class = CLASS_CDNA
        for required in (
            "cdna_display_price_assumed_fillable_at_operator_cap",
            "cdna_executable_size_unverified_pre_fill",
        ):
            if required not in assumptions_accepted:
                assumptions_accepted = sorted(set([*assumptions_accepted, required]))
        if "limited_depth_operator_size_cap_applied" not in assumptions_accepted:
            assumptions_accepted = sorted(
                set([*assumptions_accepted, "limited_depth_operator_size_cap_applied"])
            )
        action = ACTION_PAPER
        candidate_action = candidate_action or "FILL_CDNA_FIRST_THEN_HEDGE_EXACT_FILLED_QUANTITY"
        capped_notional = min(operator_size_cap, cdna_operator_size_cap)
        if available_or_cap is None or available_or_cap > capped_notional:
            available_or_cap = capped_notional

    shaped = {
        "action": action,
        "paper_candidate": bool(is_paper),
        "paper_candidate_class": paper_class if is_paper else CLASS_NONE,
        "asset": asset,
        "threshold": row.get("threshold"),
        "target_date": row.get("target_date"),
        "target_time": row.get("target_time_kalshi") or row.get("target_time_polymarket") or row.get("target_time_cdna"),
        "timezone": row.get("timezone_kalshi") or row.get("timezone_polymarket"),
        "direction": direction,
        "leg_1": leg_1,
        "leg_2": leg_2,
        "net_edge_after_fees": net_edge,
        "available_notional_or_size_cap": available_or_cap,
        "assumptions_accepted": assumptions_accepted,
        "hard_blockers": hard_blockers,
        "risk_notes": risk_notes,
        "candidate_action": candidate_action,
        "strict_exact_arb": bool(row.get("strict_exact_arb")),
        "mathematical_strict_exact_arb": bool(row.get("mathematical_strict_exact_arb")),
    }
    if shaped["paper_candidate"]:
        # Sanity: any positive paper candidate must have zero hard blockers.
        if hard_blockers:
            shaped["paper_candidate"] = False
            shaped["paper_candidate_class"] = CLASS_NONE
            shaped["action"] = ACTION_IGNORE
            shaped["candidate_action"] = ""
    if not shaped["paper_candidate"] and action == ACTION_PAPER:
        shaped["action"] = ACTION_WATCH if not hard_blockers else ACTION_IGNORE
    return shaped


def _legs(
    row: dict[str, Any],
    *,
    direction: str,
    asset: str,
    is_cdna: bool,
    cdna_cap: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if is_cdna:
        cdna_leg_src = row.get("cdna_leg") or {}
        partner_leg_src = row.get("partner_leg") or {}
        cdna_leg = {
            "venue": "cdna",
            "side": cdna_leg_src.get("side"),
            "contract_id": cdna_leg_src.get("contract_id"),
            "symbol": cdna_leg_src.get("symbol"),
            "display_price": cdna_leg_src.get("display_price"),
            "fee_per_contract": cdna_leg_src.get("fee_per_contract"),
            "all_in_cost_per_contract": cdna_leg_src.get("all_in_cost_per_contract"),
            "depth_status": cdna_leg_src.get("depth_status") or "display_price_only",
            "executable_size_proven": False,
            "operator_size_cap": cdna_cap,
        }
        partner_leg = {
            "venue": partner_leg_src.get("platform"),
            "side": partner_leg_src.get("side"),
            "ask": partner_leg_src.get("ask"),
            "ask_size": partner_leg_src.get("ask_size"),
            "available_notional": partner_leg_src.get("available_notional"),
            "depth_status": partner_leg_src.get("depth_status"),
            "quote_timestamp": partner_leg_src.get("quote_timestamp"),
        }
        return cdna_leg, partner_leg
    # Kalshi/Polymarket basis row uses kalshi_ask/polymarket_ask.
    kalshi_ask = row.get("kalshi_ask")
    poly_ask = row.get("polymarket_ask")
    kalshi_side, poly_side = _kalshi_poly_sides(direction)
    leg_kalshi = {
        "venue": "kalshi",
        "side": kalshi_side,
        "market_ticker": row.get("kalshi_ticker"),
        "ask": kalshi_ask,
        "quote_timestamp": (row.get("quote_timestamps") or {}).get("kalshi"),
    }
    leg_poly = {
        "venue": "polymarket",
        "side": poly_side,
        "platform_market_id": row.get("polymarket_market_id"),
        "condition_id": row.get("polymarket_condition_id"),
        "ask": poly_ask,
        "quote_timestamp": (row.get("quote_timestamps") or {}).get("polymarket"),
    }
    if direction.startswith("KALSHI_YES"):
        return leg_kalshi, leg_poly
    return leg_poly, leg_kalshi


def _kalshi_poly_sides(direction: str) -> tuple[str, str]:
    if direction == "KALSHI_YES_POLYMARKET_NO":
        return "YES", "NO"
    if direction == "POLYMARKET_YES_KALSHI_NO":
        return "NO", "YES"
    return "?", "?"


# ---------------------------------------------------------------------------- #
# Evidence discovery                                                           #
# ---------------------------------------------------------------------------- #


def _discover_asset_evidence(
    asset: str,
    roots: list[Path],
    *,
    include_cdna: bool,
) -> AssetDiscovery:
    asset_l = asset.lower()
    candidates: list[tuple[Path, dict[str, Path], dict[str, str | None]]] = []
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            if asset_l not in child.name.lower():
                continue
            files: dict[str, Path] = {}
            schemas: dict[str, str | None] = {}
            for fname in child.glob("*.json"):
                stem = fname.name.lower()
                payload = _safe_read_json(fname)
                platform = str((payload or {}).get("platform") or "").lower()
                schema = (payload or {}).get("schema_kind")
                if "kalshi" in stem or "kalshi" in platform:
                    files.setdefault("kalshi", fname)
                    schemas.setdefault("kalshi", schema)
                elif "polymarket" in stem or "polymarket" in platform:
                    files.setdefault("polymarket", fname)
                    schemas.setdefault("polymarket", schema)
                elif "cdna" in stem or "cdna" in platform or "crypto.com" in platform:
                    files.setdefault("cdna", fname)
                    schemas.setdefault("cdna", schema)
            if "kalshi" in files and "polymarket" in files:
                candidates.append((child, files, schemas))
    if not candidates:
        return AssetDiscovery(
            asset=asset,
            folder=None,
            kalshi_evidence=None,
            polymarket_evidence=None,
            cdna_evidence=None,
            schemas={},
            warnings=(f"no_evidence_found_for_{asset}",),
        )

    def _rank(item: tuple[Path, dict[str, Path], dict[str, str | None]]) -> tuple[int, int, str]:
        folder, _files, schemas = item
        name = folder.name.lower()
        # Prefer point-in-time / price_threshold folders since the basis-review
        # scout is built for that market_shape. Lower rank tuples sort first.
        shape_rank = 0 if ("point_in_time_threshold" in name or "price_threshold" in name) else (
            1 if "threshold" in name else 2
        )
        polished_rank = 0 if (
            (schemas.get("kalshi") or "") == POLISHED_SCHEMA
            and (schemas.get("polymarket") or "") == POLISHED_SCHEMA
        ) else 1
        return (polished_rank, shape_rank, name)

    candidates.sort(key=_rank)
    chosen = candidates[0]
    folder, files, schemas = chosen
    polished = (
        (schemas.get("kalshi") or "") == POLISHED_SCHEMA
        and (schemas.get("polymarket") or "") == POLISHED_SCHEMA
    )
    warnings: list[str] = []
    if not polished:
        warnings.append("non_polished_schema_used")
    if include_cdna and "cdna" not in files:
        warnings.append("cdna_evidence_missing")
    return AssetDiscovery(
        asset=asset,
        folder=folder,
        kalshi_evidence=files.get("kalshi"),
        polymarket_evidence=files.get("polymarket"),
        cdna_evidence=files.get("cdna") if include_cdna else None,
        schemas=schemas,
        warnings=tuple(warnings),
    )


def _safe_read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


# ---------------------------------------------------------------------------- #
# Summary                                                                      #
# ---------------------------------------------------------------------------- #


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    actions = Counter(r.get("action") for r in rows)
    classes = Counter(r.get("paper_candidate_class") for r in rows if r.get("paper_candidate"))
    hard_counter: Counter = Counter()
    for r in rows:
        hard_counter.update(r.get("hard_blockers") or [])
    return {
        "rows": len(rows),
        "paper_candidate_rows": sum(1 for r in rows if r.get("paper_candidate")),
        "strict_paper_candidate_rows": classes.get(CLASS_STRICT, 0),
        "operator_paper_candidate_rows": classes.get(CLASS_OPERATOR, 0),
        "cdna_fill_first_paper_candidate_rows": classes.get(CLASS_CDNA, 0),
        "total_paper_candidate_rows": sum(1 for r in rows if r.get("paper_candidate")),
        "watch_rows": actions.get(ACTION_WATCH, 0),
        "ignore_blocked_rows": actions.get(ACTION_IGNORE, 0),
        "top_hard_blockers": [
            {"blocker": key, "count": value} for key, value in hard_counter.most_common(15)
        ],
    }


# ---------------------------------------------------------------------------- #
# Matching diagnostics + zero-row reasons                                      #
# ---------------------------------------------------------------------------- #


def _matching_diagnostics(scout_report: dict[str, Any], *, asset_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Explain how the K/P typed-key universe collapsed to (or away from) rows.

    Reports the number of shared (date, threshold) typed keys before exact-time
    matching, why candidates were rejected, and sample per-venue target times so
    a zero exact-time result is never silent.
    """
    k_keys = scout_report.get("kalshi_market_keys") or []
    p_keys = scout_report.get("polymarket_market_keys") or []
    cdna_keys = scout_report.get("cdna_market_keys") or []
    rows = scout_report.get("rows") or []

    def _typed(keys: list[dict[str, Any]]) -> set[tuple[Any, Any]]:
        return {
            (k.get("target_date"), k.get("threshold"))
            for k in keys
            if k.get("target_date") is not None and k.get("threshold") is not None
        }

    k_set = _typed(k_keys)
    p_set = _typed(p_keys)
    shared = k_set & p_set
    k_dates = {d for (d, _t) in k_set}
    p_dates = {d for (d, _t) in p_set}
    only = (k_set - p_set) | (p_set - k_set)
    threshold_mismatch = sum(1 for (d, _th) in only if d in k_dates and d in p_dates)
    date_mismatch = len(only) - threshold_mismatch

    matched_rows = [
        r
        for r in rows
        if (r.get("direction") or "") not in ("", "UNMATCHED")
        and not str(r.get("direction") or "").startswith("CDNA_")
    ]
    time_mismatch_keys: set[tuple[Any, Any]] = set()
    missing_ask_keys: set[tuple[Any, Any]] = set()
    for r in matched_rows:
        key = (r.get("target_date"), r.get("threshold"))
        blk = r.get("blockers") or []
        if "target_time_mismatch" in blk:
            time_mismatch_keys.add(key)
        if "missing_quote" in blk:
            missing_ask_keys.add(key)

    k_comp = scout_report.get("kalshi_comparator")
    p_comp = scout_report.get("polymarket_comparator")
    comparator_mismatch = len(shared) if (k_comp and p_comp and k_comp != p_comp) else 0

    exact_time_rows = sum(
        1 for r in asset_rows if not str(r.get("direction") or "").startswith("CDNA_")
    )
    sample_k_times = sorted({k.get("target_time") for k in k_keys if k.get("target_time")})[:8]
    sample_p_times = sorted({p.get("target_time") for p in p_keys if p.get("target_time")})[:8]

    return {
        "kalshi_market_count": len(k_keys),
        "polymarket_market_count": len(p_keys),
        "cdna_market_count": len(cdna_keys),
        "typed_key_candidates": len(shared),
        "matched_directional_rows": len(matched_rows),
        "exact_time_rows": exact_time_rows,
        "rejected": {
            "threshold_mismatch": threshold_mismatch,
            "date_mismatch": date_mismatch,
            "target_time_mismatch": len(time_mismatch_keys),
            "comparator_mismatch": comparator_mismatch,
            "missing_ask": len(missing_ask_keys),
        },
        "sample_kalshi_target_times": sample_k_times,
        "sample_polymarket_target_times": sample_p_times,
    }


def _zero_row_reasons(
    *,
    combined_rows: list[dict[str, Any]],
    venue_counts: dict[str, int],
    asset_reports: list[dict[str, Any]],
    include_cdna: bool,
    refreshed: bool,
    unmatched_time_rows: list[dict[str, Any]],
) -> dict[str, str | None]:
    total_k = venue_counts.get("kalshi_markets", 0)
    total_p = venue_counts.get("polymarket_markets", 0)
    total_cdna = venue_counts.get("cdna_markets", 0)
    total_keys = venue_counts.get("typed_key_candidates", 0)

    kalshi_zero_reason: str | None = None
    if total_k == 0:
        if refreshed:
            series = sorted({s for a in asset_reports for s in (a.get("kalshi_series_queried") or [])})
            rej = _merge_counters(a.get("kalshi_rejection_reasons") for a in asset_reports)
            kalshi_zero_reason = (
                "kalshi refresh found zero usable daily markets"
                + (f"; series queried: {', '.join(series)}" if series else "")
                + (f"; rejection reasons: {_fmt_counter(rej)}" if rej else "")
            )
        else:
            kalshi_zero_reason = "no kalshi daily crypto evidence loaded from saved roots"

    polymarket_zero_reason: str | None = None
    if total_p == 0:
        if refreshed:
            strategies = sorted({s for a in asset_reports for s in (a.get("polymarket_query_strategies") or [])})
            rej = _merge_counters(a.get("polymarket_rejection_reasons") for a in asset_reports)
            clob_fail = sum(int(a.get("polymarket_clob_fetch_failures", 0) or 0) for a in asset_reports)
            polymarket_zero_reason = (
                "polymarket refresh found zero usable markets"
                + (f"; strategies: {', '.join(strategies)}" if strategies else "")
                + (f"; clob_fetch_failures: {clob_fail}" if clob_fail else "")
                + (f"; rejection reasons: {_fmt_counter(rej)}" if rej else "")
            )
        else:
            polymarket_zero_reason = "no polymarket daily crypto evidence loaded from saved roots"

    if not include_cdna:
        cdna_zero_reason: str | None = "cdna_not_requested"
    elif total_cdna == 0:
        cdna_zero_reason = "cdna_evidence_missing_for_all_requested_assets"
    else:
        cdna_zero_reason = None

    no_cross_venue_rows_reason: str | None = None
    if not combined_rows:
        if total_k == 0 and total_p == 0:
            no_cross_venue_rows_reason = "no_markets_discovered_on_any_venue"
        elif total_k == 0:
            no_cross_venue_rows_reason = f"kalshi_zero: {kalshi_zero_reason}"
        elif total_p == 0:
            no_cross_venue_rows_reason = f"polymarket_zero: {polymarket_zero_reason}"
        elif total_keys == 0:
            no_cross_venue_rows_reason = (
                "no_shared_typed_key: kalshi and polymarket both have markets but share no "
                "(asset, threshold, date) typed key; see matching diagnostics for sample "
                "thresholds and target times"
            )
        elif unmatched_time_rows:
            no_cross_venue_rows_reason = (
                "all_matched_rows_blocked: shared typed keys exist but every matched row is a "
                "target_time_mismatch (different settlement instant)"
            )
        else:
            no_cross_venue_rows_reason = "all_matched_rows_blocked_or_date_filtered"

    return {
        "kalshi_zero_reason": kalshi_zero_reason,
        "polymarket_zero_reason": polymarket_zero_reason,
        "cdna_zero_reason": cdna_zero_reason,
        "no_cross_venue_rows_reason": no_cross_venue_rows_reason,
    }


def _merge_counters(dicts: Any) -> dict[str, int]:
    merged: dict[str, int] = {}
    for d in dicts:
        if not isinstance(d, dict):
            continue
        for k, v in d.items():
            try:
                merged[str(k)] = merged.get(str(k), 0) + int(v)
            except (TypeError, ValueError):
                continue
    return merged


def _fmt_counter(counter: dict[str, int]) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return -1e9


def _venue(report: dict[str, Any], key: str) -> Any:
    return (report.get("venue_market_counts") or {}).get(key, 0)


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")
