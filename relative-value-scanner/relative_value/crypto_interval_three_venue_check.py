"""Crypto interval three-venue check.

Matches live/upcoming intraday crypto interval / point-in-time threshold
contracts across Kalshi, Polymarket and (optional, saved-evidence-only) CDNA by
EXACT settlement instant, and emits post-fee paper candidates.

This is the intraday sibling of ``daily_crypto_three_venue_check``. The daily
family cannot produce matched-instant rows because Kalshi daily settles at
00:00/17:00 ET while Polymarket daily settles at 12:00 ET — a permanent
``target_time_mismatch``. Intraday contracts anchor to clock-hour boundaries and
do collide on the same UTC instant (e.g. ``2026-05-30T05:00:00Z`` = 1am ET), so
they can form real typed-key matches.

Hard guarantees:
  - No order placement / execution / auth / account / browser automation.
  - Public read-only GET for Kalshi/Polymarket; CDNA saved-evidence-only.
  - Asks only for entry; NO midpoint. ``net_edge_after_fees`` is the only edge a
    candidate decision uses.
  - Settlement-instant discipline: instant mismatch beyond tolerance, timezone
    mismatch, strike mismatch, missing ask, missing complement, stale quote,
    incompatible shape, and CDNA pre-fill strict-arb claims stay hard blockers.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from relative_value.fees import KalshiTieredFeeModel, PolymarketConservativeFeeModel
from relative_value.operator_paper_candidate_policy import (
    ACTION_IGNORE,
    ACTION_PAPER,
    ACTION_WATCH,
    CLASS_CDNA,
    CLASS_NONE,
    CLASS_OPERATOR,
    collect_hard_blockers,
    normalize_operator_risk_mode,
)


HttpGet = Callable[[str, float], Any]
Sleep = Callable[[float], None]

SCHEMA_KIND = "crypto_interval_three_venue_check_v1"
SCHEMA_VERSION = 1

DEFAULT_ASSETS = ("BTC", "ETH", "SOL", "XRP", "DOGE")
DEFAULT_LOOKAHEAD_HOURS = 8.0
DEFAULT_TARGET_TIME_TOLERANCE_SECONDS = 0.0
DEFAULT_MAX_QUOTE_AGE_SECONDS = 300.0
DEFAULT_MIN_AVAILABLE_NOTIONAL = 1.0
DEFAULT_CDNA_OPERATOR_SIZE_CAP = 1.0
CDNA_FEE_PER_CONTRACT = 0.02

SHAPE_POINT_IN_TIME = "point_in_time_threshold"

_KALSHI_FEE = KalshiTieredFeeModel()
_POLY_FEE = PolymarketConservativeFeeModel()


def _default_refresh_root(*, generated_at: datetime) -> Path:
    return Path("reports") / "manual_evidence" / "crypto_interval_live" / generated_at.strftime("%Y-%m-%d") / generated_at.strftime("%H%M%S")


# ---------------------------------------------------------------------------- #
# Public entry points                                                          #
# ---------------------------------------------------------------------------- #


def write_crypto_interval_three_venue_check_files(
    *,
    json_output: Path,
    markdown_output: Path,
    **kwargs: Any,
) -> dict[str, Any]:
    report = build_crypto_interval_three_venue_check_report(**kwargs)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_crypto_interval_three_venue_check_markdown(report), encoding="utf-8")
    return report


def build_crypto_interval_three_venue_check_report(
    *,
    assets: list[str],
    lookahead_hours: float = DEFAULT_LOOKAHEAD_HOURS,
    target_time_tolerance_seconds: float = DEFAULT_TARGET_TIME_TOLERANCE_SECONDS,
    operator_risk_mode: str = "conservative",
    allow_top_of_book_depth: bool = False,
    operator_size_cap: float = 0.0,
    include_cdna: bool = False,
    operator_accept_cdna_display_price_risk: bool = False,
    cdna_operator_size_cap: float = DEFAULT_CDNA_OPERATOR_SIZE_CAP,
    cdna_evidence_dir: Path | None = None,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
    min_available_notional: float = DEFAULT_MIN_AVAILABLE_NOTIONAL,
    generated_at: datetime | None = None,
    http_get: "HttpGet | None" = None,
    sleep: "Sleep | None" = None,
    refresh_kalshi_polymarket: bool = False,
    write_refreshed_evidence_root: Path | None = None,
    evidence_roots: list[Path] | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    risk_mode = normalize_operator_risk_mode(operator_risk_mode)
    asset_list = [str(a).strip().upper() for a in assets if str(a).strip()]
    depth_permissive = bool(allow_top_of_book_depth and operator_size_cap and operator_size_cap > 0)

    per_asset_rows, refresh_summary = _gather_rows(
        asset_list=asset_list,
        lookahead_hours=lookahead_hours,
        generated=generated,
        include_cdna=include_cdna,
        cdna_evidence_dir=cdna_evidence_dir,
        http_get=http_get,
        sleep=sleep,
        refresh=refresh_kalshi_polymarket,
        write_refreshed_evidence_root=write_refreshed_evidence_root,
        evidence_roots=evidence_roots,
    )

    rows: list[dict[str, Any]] = []
    matched_windows: list[dict[str, Any]] = []
    unmatched_instant: list[dict[str, Any]] = []
    synthetic_rows: list[dict[str, Any]] = []
    synthetic_diag_by_asset: list[dict[str, Any]] = []
    asset_reports: list[dict[str, Any]] = []

    for asset in asset_list:
        rec = per_asset_rows.get(asset) or {}
        k_rows = list(rec.get("kalshi_rows") or [])
        p_rows = list(rec.get("polymarket_rows") or [])
        c_rows = list(rec.get("cdna_rows") or []) if include_cdna else []
        s_rows, s_diag = _synthetic_lane(
            asset=asset,
            kalshi_rows=k_rows,
            polymarket_rows=p_rows,
            generated=generated,
            tolerance_seconds=float(target_time_tolerance_seconds),
            risk_mode=risk_mode,
            depth_permissive=depth_permissive,
            operator_size_cap=float(operator_size_cap or 0.0),
            max_quote_age_seconds=float(max_quote_age_seconds),
            min_available_notional=float(min_available_notional),
        )
        synthetic_rows.extend(s_rows)
        synthetic_diag_by_asset.append({"asset": asset, **s_diag})
        asset_rows, asset_matched, asset_unmatched, matching = _match_asset(
            asset=asset,
            kalshi_rows=k_rows,
            polymarket_rows=p_rows,
            cdna_rows=c_rows,
            generated=generated,
            tolerance_seconds=float(target_time_tolerance_seconds),
            risk_mode=risk_mode,
            depth_permissive=depth_permissive,
            operator_size_cap=float(operator_size_cap or 0.0),
            cdna_operator_size_cap=float(cdna_operator_size_cap),
            operator_accept_cdna=operator_accept_cdna_display_price_risk,
            max_quote_age_seconds=float(max_quote_age_seconds),
            min_available_notional=float(min_available_notional),
        )
        rows.extend(asset_rows)
        matched_windows.extend(asset_matched)
        unmatched_instant.extend(asset_unmatched)
        asset_reports.append(
            {
                "asset": asset,
                "kalshi_market_count": len(k_rows),
                "polymarket_market_count": len(p_rows),
                "cdna_market_count": len(c_rows),
                "kalshi_diagnostics": rec.get("kalshi_diagnostics") or {},
                "polymarket_diagnostics": rec.get("polymarket_diagnostics") or {},
                "cdna_diagnostics": rec.get("cdna_diagnostics") or {},
                "matching_diagnostics": matching,
            }
        )

    rows.sort(
        key=lambda r: (1 if r.get("paper_candidate") else 0, _safe_float(r.get("net_edge_after_fees"))),
        reverse=True,
    )
    synthetic_rows.sort(
        key=lambda r: (1 if r.get("paper_candidate") else 0, _safe_float(r.get("net_edge_after_fees"))),
        reverse=True,
    )
    summary = _summary(rows)
    synthetic_summary = _synthetic_summary(synthetic_rows, synthetic_diag_by_asset)
    summary["synthetic_rows"] = synthetic_summary["synthetic_rows"]
    summary["synthetic_paper_candidate_rows"] = synthetic_summary["synthetic_paper_candidate_rows"]
    summary["synthetic_watch_rows"] = synthetic_summary["synthetic_watch_rows"]
    summary["synthetic_ignore_blocked_rows"] = synthetic_summary["synthetic_ignore_blocked_rows"]
    venue_counts = {
        "kalshi_markets": sum(int(a["kalshi_market_count"]) for a in asset_reports),
        "polymarket_markets": sum(int(a["polymarket_market_count"]) for a in asset_reports),
        "cdna_markets": sum(int(a["cdna_market_count"]) for a in asset_reports),
        "typed_key_candidates": len(matched_windows),
    }
    reasons = _reasons(
        rows=rows, venue_counts=venue_counts, asset_reports=asset_reports,
        include_cdna=include_cdna, refreshed=refresh_kalshi_polymarket, unmatched_instant=unmatched_instant,
    )
    harmonic_alignment = [
        endpoint
        for a in asset_reports
        for endpoint in ((a.get("matching_diagnostics") or {}).get("harmonic_endpoints") or [])
    ]
    harmonic_summary = {
        "endpoints": len(harmonic_alignment),
        "compatible_shared_target_instants": sum(1 for e in harmonic_alignment if e.get("compatible_for_direct_match")),
        "harmonic_point_in_time_matches": sum(
            1 for w in matched_windows
            if w.get("observation_type") == "point_in_time_at_target" and w.get("harmonic_alignment_used")
        ),
        "direct_updown_matches": sum(
            1 for w in matched_windows if w.get("observation_type") == "interval_start_to_end_change"
        ),
        "point_in_time_matches": sum(
            1 for w in matched_windows if w.get("observation_type") == "point_in_time_at_target"
        ),
    }

    return {
        "schema_kind": SCHEMA_KIND,
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated.isoformat(),
        "diagnostic_only": True,
        "public_read_only": True,
        "saved_files_only": not refresh_kalshi_polymarket,
        "strict_exact_arb": False,
        "operator_risk_mode": risk_mode,
        "assets_requested": asset_list,
        "lookahead_hours": float(lookahead_hours),
        "target_time_tolerance_seconds": float(target_time_tolerance_seconds),
        "include_cdna": bool(include_cdna),
        "operator_accept_cdna_display_price_risk": bool(operator_accept_cdna_display_price_risk),
        "allow_top_of_book_depth": bool(allow_top_of_book_depth),
        "operator_size_cap": float(operator_size_cap or 0.0),
        "refresh_kalshi_polymarket": bool(refresh_kalshi_polymarket),
        "refresh_summary_window": (refresh_summary or {}).get("target_window_end_utc"),
        "parameters": {
            "max_quote_age_seconds": float(max_quote_age_seconds),
            "min_available_notional": float(min_available_notional),
            "cdna_operator_size_cap": float(cdna_operator_size_cap),
        },
        "asset_reports": asset_reports,
        "rows": rows,
        "exact_matched_windows": matched_windows,
        "unmatched_by_target_instant": unmatched_instant,
        "harmonic_endpoint_alignment": harmonic_alignment,
        "harmonic_summary": harmonic_summary,
        "synthetic_rows": synthetic_rows,
        "synthetic_diagnostics": synthetic_diag_by_asset,
        "synthetic_summary": synthetic_summary,
        "venue_market_counts": venue_counts,
        "typed_key_candidates": len(matched_windows),
        "no_cross_venue_rows_reason": reasons["no_cross_venue_rows_reason"],
        "kalshi_zero_reason": reasons["kalshi_zero_reason"],
        "polymarket_zero_reason": reasons["polymarket_zero_reason"],
        "cdna_zero_reason": reasons["cdna_zero_reason"],
        "summary_counts": summary,
        "top_blockers": summary["top_blockers"],
        "kalshi_discovery_diagnostics": [
            {"asset": a["asset"], **(a["kalshi_diagnostics"] or {})} for a in asset_reports
        ],
        "polymarket_discovery_diagnostics": [
            {"asset": a["asset"], **(a["polymarket_diagnostics"] or {})} for a in asset_reports
        ],
        "cdna_evidence_diagnostics": [
            {"asset": a["asset"], **(a["cdna_diagnostics"] or {})} for a in asset_reports
        ],
        "safety": {
            "diagnostic_only": True,
            "public_read_only": True,
            "cdna_network_fetch_attempted": False,
            "uses_midpoint": False,
            "uses_asks_for_entry": True,
            "orders_or_execution_logic_added": False,
            "auth_or_account_logic_added": False,
            "browser_automation_added": False,
            "strict_exact_arb": False,
        },
    }


# ---------------------------------------------------------------------------- #
# Row gathering (refresh via collector, or saved snapshots)                    #
# ---------------------------------------------------------------------------- #


def _gather_rows(
    *,
    asset_list: list[str],
    lookahead_hours: float,
    generated: datetime,
    include_cdna: bool,
    cdna_evidence_dir: Path | None,
    http_get: "HttpGet | None",
    sleep: "Sleep | None",
    refresh: bool,
    write_refreshed_evidence_root: Path | None,
    evidence_roots: list[Path] | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None]:
    if refresh:
        from relative_value.crypto_interval_evidence_collector import (  # noqa: WPS433
            write_crypto_interval_live_evidence,
        )

        refresh_root = write_refreshed_evidence_root or _default_refresh_root(generated_at=generated)
        summary = write_crypto_interval_live_evidence(
            assets=asset_list,
            output_root=refresh_root,
            lookahead_hours=lookahead_hours,
            generated_at=generated,
            http_get=http_get,
            cdna_evidence_dir=cdna_evidence_dir if include_cdna else None,
            sleep=sleep,
        )
        return {str(rec.get("asset")).upper(): rec for rec in summary.get("per_asset") or []}, summary

    per_asset: dict[str, dict[str, Any]] = {}
    for root in evidence_roots or []:
        root = Path(root)
        for asset in asset_list:
            snapshot = root / asset.lower() / "interval_typed_keys.json"
            if asset in per_asset or not snapshot.exists():
                continue
            try:
                payload = json.loads(snapshot.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                per_asset[asset] = payload
    return per_asset, None


# ---------------------------------------------------------------------------- #
# Per-asset matching + candidate generation                                    #
# ---------------------------------------------------------------------------- #


def _match_asset(
    *,
    asset: str,
    kalshi_rows: list[dict[str, Any]],
    polymarket_rows: list[dict[str, Any]],
    cdna_rows: list[dict[str, Any]],
    generated: datetime,
    tolerance_seconds: float,
    risk_mode: str,
    depth_permissive: bool,
    operator_size_cap: float,
    cdna_operator_size_cap: float,
    operator_accept_cdna: bool,
    max_quote_age_seconds: float,
    min_available_notional: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    rows_out: list[dict[str, Any]] = []
    matched_windows: list[dict[str, Any]] = []
    unmatched_instant: list[dict[str, Any]] = []

    by_platform = {"kalshi": kalshi_rows, "polymarket": polymarket_rows, "cdna": cdna_rows}
    all_rows = [r for plat_rows in by_platform.values() for r in plat_rows]

    def _obs(row: dict[str, Any]) -> str:
        return str(row.get("payoff_observation_type") or "unknown")

    pit_rows = [
        r for r in all_rows
        if _obs(r) == "point_in_time_at_target" and r.get("threshold_or_strike") is not None and r.get("target_instant_utc")
    ]
    updown_rows = [r for r in all_rows if _obs(r) == "interval_start_to_end_change" and r.get("target_instant_utc")]
    range_rows = [r for r in all_rows if _obs(r) == "range_at_target"]
    touch_rows = [r for r in all_rows if _obs(r) == "touch_before_deadline"]
    unknown_rows = [r for r in all_rows if _obs(r) == "unknown"]

    # Observation types that are never eligible for a *direct* cross-venue match.
    for r in range_rows:
        rows_out.append(_watch_row(asset, r, reason="range_at_target_synthetic_lane_only"))
    for r in touch_rows:
        rows_out.append(_watch_row(asset, r, reason="touch_or_deadline_incompatible_shape", hard="incompatible_shape"))
    for r in unknown_rows:
        rows_out.append(_watch_row(asset, r, reason="unknown_payoff_observation_type"))

    venue_pairs = (("kalshi", "polymarket"), ("kalshi", "cdna"), ("polymarket", "cdna"))
    matched_row_ids: set[int] = set()
    typed_key_pairs = 0
    rejected: Counter = Counter()

    # ---- Lane 1: point-in-time-at-target. Interval length need NOT match. ----
    by_strike: dict[float, list[dict[str, Any]]] = defaultdict(list)
    for r in pit_rows:
        by_strike[float(r["threshold_or_strike"])].append(r)
    for strike, group in by_strike.items():
        plat_rows = {plat: [r for r in group if r["platform"] == plat] for plat in by_platform}
        for a, b in venue_pairs:
            for ra in plat_rows.get(a, []):
                for rb in plat_rows.get(b, []):
                    dt = _instant_delta_seconds(ra, rb)
                    if dt is None:
                        continue
                    if dt > tolerance_seconds:
                        rejected["target_time_mismatch"] += 1
                        unmatched_instant.append(_unmatched_instant_row(asset, ra, rb, strike))
                        continue
                    typed_key_pairs += 1
                    matched_row_ids.add(id(ra))
                    matched_row_ids.add(id(rb))
                    harmonic = _intervals_differ(ra, rb)
                    pair_rows = _candidate_rows_for_pair(
                        asset=asset, strike=strike, ra=ra, rb=rb, generated=generated,
                        risk_mode=risk_mode, depth_permissive=depth_permissive,
                        operator_size_cap=operator_size_cap, cdna_operator_size_cap=cdna_operator_size_cap,
                        operator_accept_cdna=operator_accept_cdna,
                        max_quote_age_seconds=max_quote_age_seconds, min_available_notional=min_available_notional,
                        observation_type="point_in_time_at_target", side_label="ABOVE",
                        harmonic_alignment_used=harmonic,
                        compatibility_reason=(
                            "point_in_time_at_shared_target_instant_different_interval_grids"
                            if harmonic else "point_in_time_at_shared_target_instant"
                        ),
                    )
                    rows_out.extend(pair_rows)
                    best_net = max((r["net_edge_after_fees"] for r in pair_rows if r["net_edge_after_fees"] is not None), default=None)
                    matched_windows.append(
                        {
                            "asset": asset, "threshold_or_strike": strike,
                            "target_instant_utc": ra.get("target_instant_utc"),
                            "observation_type": "point_in_time_at_target",
                            "harmonic_alignment_used": harmonic,
                            "venues": sorted({ra["platform"], rb["platform"]}),
                            "best_net_edge_after_fees": best_net,
                            "has_paper_candidate": any(r.get("paper_candidate") for r in pair_rows),
                        }
                    )

    # ---- Lane 2: interval up/down. Require same reference_start AND target. ----
    for a, b in venue_pairs:
        a_rows = [r for r in updown_rows if r["platform"] == a]
        b_rows = [r for r in updown_rows if r["platform"] == b]
        for ra in a_rows:
            for rb in b_rows:
                dt = _instant_delta_seconds(ra, rb)
                if dt is None:
                    continue
                if dt > tolerance_seconds:
                    rejected["target_time_mismatch"] += 1
                    unmatched_instant.append(_unmatched_instant_row(asset, ra, rb, None))
                    continue
                if not _reference_starts_match(ra, rb, tolerance_seconds):
                    # Endpoints align but the windows differ (e.g. 2h vs 1h up/down) ->
                    # NOT the same payoff. Surface, do not match.
                    rejected["updown_reference_start_mismatch"] += 1
                    rows_out.append(_watch_row(asset, ra, reason="updown_reference_start_mismatch", hard="target_time_mismatch"))
                    continue
                typed_key_pairs += 1
                matched_row_ids.add(id(ra))
                matched_row_ids.add(id(rb))
                pair_rows = _candidate_rows_for_pair(
                    asset=asset, strike=None, ra=ra, rb=rb, generated=generated,
                    risk_mode=risk_mode, depth_permissive=depth_permissive,
                    operator_size_cap=operator_size_cap, cdna_operator_size_cap=cdna_operator_size_cap,
                    operator_accept_cdna=operator_accept_cdna,
                    max_quote_age_seconds=max_quote_age_seconds, min_available_notional=min_available_notional,
                    observation_type="interval_start_to_end_change", side_label="UP",
                    harmonic_alignment_used=False,
                    compatibility_reason="updown_same_reference_start_and_target_instant",
                )
                rows_out.extend(pair_rows)
                best_net = max((r["net_edge_after_fees"] for r in pair_rows if r["net_edge_after_fees"] is not None), default=None)
                matched_windows.append(
                    {
                        "asset": asset, "threshold_or_strike": None,
                        "target_instant_utc": ra.get("target_instant_utc"),
                        "reference_start_utc": ra.get("reference_start_utc"),
                        "observation_type": "interval_start_to_end_change",
                        "harmonic_alignment_used": False,
                        "venues": sorted({ra["platform"], rb["platform"]}),
                        "best_net_edge_after_fees": best_net,
                        "has_paper_candidate": any(r.get("paper_candidate") for r in pair_rows),
                    }
                )

    # Eligible rows that never matched a peer -> WATCH missing_platform_peer.
    for r in pit_rows + updown_rows:
        if id(r) not in matched_row_ids:
            rows_out.append(_watch_row(asset, r, reason="missing_platform_peer"))

    k_instants = {r.get("target_instant_utc") for r in kalshi_rows if r.get("target_instant_utc")}
    p_instants = {r.get("target_instant_utc") for r in polymarket_rows if r.get("target_instant_utc")}
    shared_instants = sorted(k_instants & p_instants)
    matching = {
        "kalshi_market_count": len(kalshi_rows),
        "polymarket_market_count": len(polymarket_rows),
        "cdna_market_count": len(cdna_rows),
        "point_in_time_rows": len(pit_rows),
        "updown_rows": len(updown_rows),
        "range_rows": len(range_rows),
        "touch_rows": len(touch_rows),
        "typed_key_pairs": typed_key_pairs,
        "matched_windows": len(matched_windows),
        "rejected": dict(rejected),
        "kalshi_shapes": dict(Counter(r.get("market_shape") for r in kalshi_rows)),
        "polymarket_shapes": dict(Counter(r.get("market_shape") for r in polymarket_rows)),
        "shared_instants": shared_instants[:8],
        "shared_instant_count": len(shared_instants),
        "harmonic_endpoints": _harmonic_endpoints(asset, all_rows, tolerance_seconds),
        "sample_kalshi_instants": _sample_instants(kalshi_rows),
        "sample_polymarket_instants": _sample_instants(polymarket_rows),
        "sample_cdna_instants": _sample_instants(cdna_rows),
    }
    return rows_out, matched_windows, unmatched_instant, matching


def _candidate_rows_for_pair(
    *,
    asset: str,
    strike: float | None,
    ra: dict[str, Any],
    rb: dict[str, Any],
    generated: datetime,
    risk_mode: str,
    depth_permissive: bool,
    operator_size_cap: float,
    cdna_operator_size_cap: float,
    operator_accept_cdna: bool,
    max_quote_age_seconds: float,
    min_available_notional: float,
    observation_type: str = "point_in_time_at_target",
    side_label: str = "ABOVE",
    harmonic_alignment_used: bool = False,
    compatibility_reason: str = "",
) -> list[dict[str, Any]]:
    a_yes, a_no = _canonical_above(ra)
    b_yes, b_no = _canonical_above(rb)
    out: list[dict[str, Any]] = []
    # Two complete-hedge orientations across the venue pair.
    for leg_yes_src, leg_yes, leg_no_src, leg_no in (
        (ra, a_yes, rb, b_no),
        (rb, b_yes, ra, a_no),
    ):
        out.append(
            _build_candidate_row(
                asset=asset, strike=strike, generated=generated, risk_mode=risk_mode,
                depth_permissive=depth_permissive, operator_size_cap=operator_size_cap,
                cdna_operator_size_cap=cdna_operator_size_cap, operator_accept_cdna=operator_accept_cdna,
                max_quote_age_seconds=max_quote_age_seconds, min_available_notional=min_available_notional,
                yes_row=leg_yes_src, yes_leg=leg_yes, no_row=leg_no_src, no_leg=leg_no,
                observation_type=observation_type, side_label=side_label,
                harmonic_alignment_used=harmonic_alignment_used, compatibility_reason=compatibility_reason,
            )
        )
    return out


def _build_candidate_row(
    *,
    asset: str,
    strike: float | None,
    generated: datetime,
    risk_mode: str,
    depth_permissive: bool,
    operator_size_cap: float,
    cdna_operator_size_cap: float,
    operator_accept_cdna: bool,
    max_quote_age_seconds: float,
    min_available_notional: float,
    yes_row: dict[str, Any],
    yes_leg: dict[str, Any],
    no_row: dict[str, Any],
    no_leg: dict[str, Any],
    observation_type: str = "point_in_time_at_target",
    side_label: str = "ABOVE",
    harmonic_alignment_used: bool = False,
    compatibility_reason: str = "",
) -> dict[str, Any]:
    is_cdna = yes_row["platform"] == "cdna" or no_row["platform"] == "cdna"
    blockers: list[str] = []

    yes_ask = _valid_ask(yes_leg["ask"])
    no_ask = _valid_ask(no_leg["ask"])
    if yes_ask is None or no_ask is None:
        blockers.append("missing_ask")
    # complement availability: the buying side must exist on each venue.
    if yes_leg.get("native_side") is None or no_leg.get("native_side") is None:
        blockers.append("missing_complement_quote")

    if _stale(yes_row, generated, max_quote_age_seconds) or _stale(no_row, generated, max_quote_age_seconds):
        blockers.append("stale_or_missing_quote")

    yes_fee = _leg_fee(yes_row["platform"], yes_ask)
    no_fee = _leg_fee(no_row["platform"], no_ask)
    net = None
    if yes_ask is not None and no_ask is not None and yes_fee is not None and no_fee is not None:
        net = round(1.0 - yes_ask - no_ask - yes_fee - no_fee, 6)
        if net <= 0:
            blockers.append("no_positive_net_edge_after_fees")
    else:
        blockers.append("no_positive_net_edge_after_fees")

    # A CDNA leg has no CLOB size (display-price/fill-first); its operator-accepted
    # size is the CDNA cap, so it does not count as missing CLOB depth. A real CLOB
    # leg with no size genuinely lacks depth.
    yes_eff = _effective_size(yes_row, yes_leg, cdna_operator_size_cap)
    no_eff = _effective_size(no_row, no_leg, cdna_operator_size_cap)
    available_size = None
    if yes_eff is not None and no_eff is not None:
        available_size = min(yes_eff, no_eff)
    if available_size is None:
        blockers.append("missing_quote_depth")
    elif (
        not is_cdna
        and yes_ask is not None
        and no_ask is not None
        and min(yes_eff * yes_ask, no_eff * no_ask) < min_available_notional
    ):
        # The notional floor is a CLOB-depth gate; CDNA fill-first size is the cap.
        blockers.append("insufficient_available_notional")

    if yes_row.get("price_source") and no_row.get("price_source") and yes_row["price_source"] != no_row["price_source"]:
        blockers.append("basis_risk_review_required")
    if is_cdna:
        blockers.append("cdna_display_price_only")
        blockers.append("cdna_executable_size_unverified")

    accepted_basis = risk_mode in {"standard", "aggressive"}
    accepted_cdna = is_cdna and operator_accept_cdna and risk_mode in {"standard", "aggressive"}
    hard_blockers = collect_hard_blockers(
        blockers,
        ignore_cdna_info=accepted_cdna,
        accepted_basis=accepted_basis,
        accepted_top_of_book_size_cap=depth_permissive,
    )

    assumptions: list[str] = []
    if accepted_basis and "basis_risk_review_required" in blockers:
        assumptions.append("source_index_basis_risk_accepted")
    if depth_permissive and ("missing_quote_depth" in blockers or "insufficient_available_notional" in blockers):
        assumptions.append("limited_depth_operator_size_cap_applied")
    if accepted_cdna:
        assumptions.append("cdna_display_price_fill_first_risk_accepted")
        assumptions.append("cdna_executable_size_unverified_pre_fill")
    assumptions = sorted(set(assumptions))

    paper = bool(net is not None and net > 0 and not hard_blockers)
    if paper and is_cdna and not accepted_cdna:
        paper = False
    # Cross-source (BRTI/Binance/CDNA) basis is only operator-accepted in
    # standard/aggressive. Conservative keeps such a row as WATCH, never a candidate.
    if paper and "basis_risk_review_required" in blockers and not accepted_basis:
        paper = False
    paper_class = CLASS_NONE
    action = ACTION_IGNORE if hard_blockers else ACTION_WATCH
    candidate_action = ""
    risk_notes: list[str] = []
    if paper:
        paper_class = CLASS_CDNA if is_cdna else CLASS_OPERATOR
        action = ACTION_PAPER
        candidate_action = (
            "FILL_CDNA_FIRST_THEN_HEDGE_EXACT_FILLED_QUANTITY" if is_cdna else "PAPER_TEST_OR_MANUAL_MICRO_TEST"
        )
        if "limited_depth_operator_size_cap_applied" in assumptions:
            risk_notes.append("Operator accepted limited top-of-book depth with explicit size cap; do not exceed the cap.")
        if is_cdna:
            risk_notes.append("CDNA leg is display-price/fill-first; fill CDNA first, then hedge the exact filled quantity.")

    available_size_or_cap = available_size
    if paper:
        cap = operator_size_cap if operator_size_cap else available_size
        if is_cdna:
            cap = min(x for x in (cap, cdna_operator_size_cap) if x is not None) if cap is not None else cdna_operator_size_cap
        if available_size_or_cap is None or (cap is not None and available_size_or_cap > cap):
            available_size_or_cap = cap

    yes_side = f"{side_label}_YES"
    no_side = f"{side_label}_NO"
    direction = f"{yes_row['platform'].upper()}_{yes_side} + {no_row['platform'].upper()}_{no_side}"
    return {
        "lane": "direct",
        "action": action,
        "paper_candidate": paper,
        "paper_candidate_class": paper_class,
        "asset": asset,
        "market_shape": yes_row.get("market_shape"),
        "payoff_observation_type": observation_type,
        "endpoint_alignment_status": "matched_at_shared_target_instant",
        "harmonic_alignment_used": bool(harmonic_alignment_used),
        "compatibility_reason": compatibility_reason,
        "threshold_or_strike": strike,
        "target_instant_utc": yes_row.get("target_instant_utc"),
        "reference_start_utc": yes_row.get("reference_start_utc"),
        "interval_length_seconds": yes_row.get("interval_length_seconds"),
        "direction": direction,
        "leg_1": _leg_view(yes_row, yes_leg, yes_side, yes_fee),
        "leg_2": _leg_view(no_row, no_leg, no_side, no_fee),
        "net_edge_after_fees": net,
        "available_size_or_cap": available_size_or_cap,
        "assumptions_accepted": assumptions,
        "hard_blockers": hard_blockers,
        "risk_notes": risk_notes,
        "candidate_action": candidate_action,
        "strict_exact_arb": False,
    }


# ---------------------------------------------------------------------------- #
# Synthetic lane: Kalshi bucket basket vs Polymarket cumulative threshold       #
# ---------------------------------------------------------------------------- #

SYNTHETIC_LANE = "synthetic_kalshi_bucket_vs_polymarket_threshold"
_BOUNDARY_TOL = 1.0  # dollars; absorbs Kalshi's ".99" display convention


def _synthetic_lane(
    *,
    asset: str,
    kalshi_rows: list[dict[str, Any]],
    polymarket_rows: list[dict[str, Any]],
    generated: datetime,
    tolerance_seconds: float,
    risk_mode: str,
    depth_permissive: bool,
    operator_size_cap: float,
    max_quote_age_seconds: float,
    min_available_notional: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Synthesize a Kalshi cumulative threshold from its exhaustive bucket family
    and compare to a Polymarket "above X" market at the same instant.

    Synthetic events are built from YES on mutually-exclusive constituent buckets
    only (never NO on many buckets — that would not be a $1 payoff).
    """
    diagnostics: dict[str, Any] = {
        "bucket_families": [],
        "thresholds_tested": 0,
        "synthetic_candidates_generated": 0,
        "rejected": {},
    }
    rejected: Counter = Counter()
    rows: list[dict[str, Any]] = []

    kalshi_by_instant: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in kalshi_rows:
        inst = r.get("target_instant_utc")
        if inst:
            kalshi_by_instant[inst].append(r)
    poly_by_instant: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in polymarket_rows:
        if (
            r.get("market_shape") == SHAPE_POINT_IN_TIME
            and r.get("comparator") == "above"
            and r.get("threshold_or_strike") is not None
            and r.get("target_instant_utc")
        ):
            poly_by_instant[r["target_instant_utc"]].append(r)

    for instant, poly_rows_at in poly_by_instant.items():
        family = _kalshi_family_for_instant(kalshi_by_instant, instant, tolerance_seconds)
        if not family:
            continue
        coverage = _validate_bucket_family(family)
        diagnostics["bucket_families"].append(
            {
                "asset": asset,
                "target_instant_utc": instant,
                "leg_count": len(family),
                "coverage_status": coverage["status"],
                "bucket_range_min": coverage["range_min"],
                "bucket_range_max": coverage["range_max"],
            }
        )
        for poly in poly_rows_at:
            strike = float(poly["threshold_or_strike"])
            diagnostics["thresholds_tested"] += 1
            for basket_type in ("kalshi_bucket_above", "kalshi_bucket_not_above"):
                row = _build_synthetic_row(
                    asset=asset, instant=instant, strike=strike, basket_type=basket_type,
                    coverage=coverage, poly=poly, generated=generated, risk_mode=risk_mode,
                    depth_permissive=depth_permissive, operator_size_cap=operator_size_cap,
                    max_quote_age_seconds=max_quote_age_seconds, min_available_notional=min_available_notional,
                )
                rows.append(row)
                if row.get("paper_candidate"):
                    diagnostics["synthetic_candidates_generated"] += 1
                else:
                    for b in row.get("hard_blockers") or []:
                        rejected[b] += 1
    diagnostics["rejected"] = dict(rejected)
    return rows, diagnostics


def _kalshi_family_for_instant(
    kalshi_by_instant: dict[str, list[dict[str, Any]]],
    instant: str,
    tolerance_seconds: float,
) -> list[dict[str, Any]]:
    target = _parse_dt(instant)
    family: list[dict[str, Any]] = []
    for inst, rows in kalshi_by_instant.items():
        other = _parse_dt(inst)
        if target is None or other is None:
            continue
        if abs((target - other).total_seconds()) <= tolerance_seconds:
            family.extend(rows)
    # Keep only legs that expose a usable [floor, cap] range (buckets + tails).
    return [r for r in family if r.get("bucket_floor") is not None or r.get("bucket_cap") is not None]


def _validate_bucket_family(family: list[dict[str, Any]]) -> dict[str, Any]:
    legs = sorted(family, key=lambda r: (-1e18 if r.get("bucket_floor") is None else float(r["bucket_floor"])))
    blockers: list[str] = []
    has_bottom = any(r.get("bucket_floor") is None for r in legs)
    has_top = any(r.get("bucket_cap") is None for r in legs)
    if not has_bottom or not has_top:
        blockers.append("bucket_family_not_exhaustive")
    prev_cap = None
    gap = False
    for leg in legs:
        floor = leg.get("bucket_floor")
        cap = leg.get("bucket_cap")
        if prev_cap is not None and floor is not None and abs(float(floor) - prev_cap) > _BOUNDARY_TOL:
            gap = True
        if cap is not None:
            prev_cap = float(cap)
    if gap:
        blockers.append("synthetic_bucket_coverage_incomplete")
    finite_floors = [float(r["bucket_floor"]) for r in legs if r.get("bucket_floor") is not None]
    finite_caps = [float(r["bucket_cap"]) for r in legs if r.get("bucket_cap") is not None]
    status = "exhaustive" if not blockers else ("gaps" if gap else "not_exhaustive")
    return {
        "status": status,
        "blockers": blockers,
        "legs": legs,
        "range_min": min(finite_caps) if finite_caps else None,
        "range_max": max(finite_floors) if finite_floors else None,
    }


def _build_synthetic_row(
    *,
    asset: str,
    instant: str,
    strike: float,
    basket_type: str,
    coverage: dict[str, Any],
    poly: dict[str, Any],
    generated: datetime,
    risk_mode: str,
    depth_permissive: bool,
    operator_size_cap: float,
    max_quote_age_seconds: float,
    min_available_notional: float,
) -> dict[str, Any]:
    legs_sorted = coverage["legs"]
    blockers: list[str] = list(coverage["blockers"])

    above, not_above, straddle = _classify_legs(legs_sorted, strike)
    if straddle:
        # Polymarket strike falls strictly inside a Kalshi bucket -> cannot classify.
        blockers.append("threshold_grid_mismatch")

    basket_legs = above if basket_type == "kalshi_bucket_above" else not_above
    poly_q = poly.get("quote") or {}
    if basket_type == "kalshi_bucket_above":
        # Synthetic ABOVE (pays iff price > X) hedged against Polymarket NO.
        poly_ask = _valid_ask(poly_q.get("no_ask"))
        poly_size = _to_float(poly_q.get("no_ask_size"))
        poly_side = "NO"
        direction = "KALSHI_BUCKET_ABOVE_YES_POLYMARKET_NO"
    else:
        # Polymarket YES (pays iff price > X) hedged against synthetic NOT-ABOVE.
        poly_ask = _valid_ask(poly_q.get("yes_ask"))
        poly_size = _to_float(poly_q.get("yes_ask_size"))
        poly_side = "YES"
        direction = "POLYMARKET_YES_KALSHI_BUCKET_NOT_ABOVE"

    if poly_ask is None:
        blockers.append("missing_polymarket_complement_ask")

    # Build the YES-only bucket legs (mutually exclusive constituents).
    leg_views: list[dict[str, Any]] = []
    kalshi_cost = 0.0
    kalshi_fee_total = 0.0
    min_leg_size: float | None = None
    any_leg_stale = False
    missing_leg_ask = False
    for leg in basket_legs:
        q = leg.get("quote") or {}
        ask = _valid_ask(q.get("yes_ask"))
        size = _to_float(q.get("yes_ask_size"))
        fee = _leg_fee("kalshi", ask)
        if ask is None or fee is None:
            missing_leg_ask = True
        else:
            kalshi_cost += ask
            kalshi_fee_total += fee
        if _stale(leg, generated, max_quote_age_seconds):
            any_leg_stale = True
        eff = size if size is not None else (operator_size_cap if depth_permissive and operator_size_cap else None)
        if eff is not None:
            min_leg_size = eff if min_leg_size is None else min(min_leg_size, eff)
        leg_views.append(
            {
                "market_id_or_ticker": leg.get("market_id_or_ticker"),
                "bucket_floor": leg.get("bucket_floor"),
                "bucket_cap": leg.get("bucket_cap"),
                "yes_ask": ask,
                "yes_ask_size": size,
                "kalshi_fee": fee,
            }
        )
    if not basket_legs:
        blockers.append("synthetic_bucket_coverage_incomplete")
    if missing_leg_ask:
        blockers.append("missing_bucket_leg_ask")
    if any_leg_stale or _stale(poly, generated, max_quote_age_seconds):
        blockers.append("stale_or_missing_quote")

    poly_fee = _leg_fee("polymarket", poly_ask)
    total_fee = round(kalshi_fee_total + (poly_fee or 0.0), 6)

    net = None
    if not missing_leg_ask and poly_ask is not None and poly_fee is not None and basket_legs:
        net = round(1.0 - kalshi_cost - kalshi_fee_total - poly_ask - poly_fee, 6)
        if net <= 0:
            blockers.append("no_positive_net_edge_after_fees")
    else:
        blockers.append("no_positive_net_edge_after_fees")

    # Depth: the basket can only be filled to the MIN executable leg quantity.
    poly_eff = poly_size if poly_size is not None else (operator_size_cap if depth_permissive and operator_size_cap else None)
    available_size = None
    if min_leg_size is not None and poly_eff is not None:
        available_size = min(min_leg_size, poly_eff)
    if available_size is None:
        blockers.append("missing_quote_depth")

    if poly.get("price_source"):
        blockers.append("basis_risk_review_required")

    accepted_basis = risk_mode in {"standard", "aggressive"}
    hard_blockers = collect_hard_blockers(
        blockers, accepted_basis=accepted_basis, accepted_top_of_book_size_cap=depth_permissive
    )

    assumptions: list[str] = []
    if accepted_basis and "basis_risk_review_required" in blockers:
        assumptions.append("source_index_basis_risk_accepted")
    if depth_permissive and "missing_quote_depth" in blockers:
        assumptions.append("limited_depth_operator_size_cap_applied")
    if coverage["status"] == "exhaustive" and "threshold_grid_mismatch" not in blockers:
        assumptions.append("synthetic_bucket_exhaustiveness_accepted")
    assumptions = sorted(set(assumptions))

    # Synthetic candidates are only graduated in aggressive mode.
    paper = bool(
        risk_mode == "aggressive"
        and net is not None
        and net > 0
        and not hard_blockers
    )
    action = ACTION_IGNORE if hard_blockers else ACTION_WATCH
    paper_class = CLASS_NONE
    candidate_action = ""
    risk_notes: list[str] = []
    if paper:
        paper_class = CLASS_OPERATOR
        action = ACTION_PAPER
        candidate_action = "PAPER_TEST_OR_MANUAL_MICRO_TEST"
        risk_notes.append(
            f"Synthetic Kalshi basket of {len(basket_legs)} YES legs; fill all legs + the "
            "Polymarket complement, do not exceed the min executable basket quantity."
        )

    available_size_or_cap = available_size
    if paper and operator_size_cap and (available_size_or_cap is None or available_size_or_cap > operator_size_cap):
        available_size_or_cap = operator_size_cap

    return {
        "lane": SYNTHETIC_LANE,
        "synthetic_basket": True,
        "synthetic_basket_type": basket_type,
        "payoff_observation_type": "range_at_target",
        "endpoint_alignment_status": "matched_at_shared_target_instant",
        "harmonic_alignment_used": False,
        "compatibility_reason": "kalshi_bucket_basket_synthesizes_cumulative_threshold_at_target",
        "action": action,
        "paper_candidate": paper,
        "paper_candidate_class": paper_class,
        "asset": asset,
        "market_shape": SHAPE_POINT_IN_TIME,
        "direction": direction,
        "threshold": strike,
        "target_instant_utc": instant,
        "kalshi_bucket_leg_count": len(basket_legs),
        "kalshi_synthetic_leg_count": len(basket_legs),
        "kalshi_bucket_legs": leg_views,
        "kalshi_synthetic_fee_total": round(kalshi_fee_total, 6),
        "polymarket_leg": {"side": poly_side, "ask": poly_ask, "ask_size": poly_size, "fee_estimate": poly_fee, "condition_id": poly.get("condition_id"), "market_id_or_ticker": poly.get("market_id_or_ticker")},
        "polymarket_fee": poly_fee,
        "total_fee_estimate": total_fee,
        "bucket_coverage_status": coverage["status"],
        "bucket_range_min": coverage["range_min"],
        "bucket_range_max": coverage["range_max"],
        "net_edge_after_fees": net,
        "available_size_or_cap": available_size_or_cap,
        "assumptions_accepted": assumptions,
        "hard_blockers": hard_blockers,
        "risk_notes": risk_notes,
        "candidate_action": candidate_action,
        "strict_exact_arb": False,
    }


def _classify_legs(
    legs_sorted: list[dict[str, Any]], strike: float
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    above: list[dict[str, Any]] = []
    not_above: list[dict[str, Any]] = []
    straddle: list[dict[str, Any]] = []
    for leg in legs_sorted:
        floor = leg.get("bucket_floor")
        cap = leg.get("bucket_cap")
        if floor is not None and float(floor) >= strike - _BOUNDARY_TOL:
            above.append(leg)
        elif cap is not None and float(cap) <= strike + _BOUNDARY_TOL:
            not_above.append(leg)
        else:
            straddle.append(leg)
    return above, not_above, straddle


def _synthetic_summary(rows: list[dict[str, Any]], diag_by_asset: list[dict[str, Any]]) -> dict[str, Any]:
    actions = Counter(r.get("action") for r in rows)
    fee_drag = sorted(
        (
            {
                "asset": r.get("asset"),
                "threshold": r.get("threshold"),
                "basket_type": r.get("synthetic_basket_type"),
                "leg_count": r.get("kalshi_bucket_leg_count"),
                "total_fee_estimate": r.get("total_fee_estimate"),
                "net_edge_after_fees": r.get("net_edge_after_fees"),
            }
            for r in rows
        ),
        key=lambda d: _safe_float(d.get("total_fee_estimate")),
        reverse=True,
    )
    return {
        "synthetic_rows": len(rows),
        "synthetic_paper_candidate_rows": sum(1 for r in rows if r.get("paper_candidate")),
        "synthetic_watch_rows": actions.get(ACTION_WATCH, 0),
        "synthetic_ignore_blocked_rows": actions.get(ACTION_IGNORE, 0),
        "bucket_families_discovered": sum(len(d.get("bucket_families") or []) for d in diag_by_asset),
        "thresholds_tested": sum(int(d.get("thresholds_tested") or 0) for d in diag_by_asset),
        "synthetic_candidates_generated": sum(int(d.get("synthetic_candidates_generated") or 0) for d in diag_by_asset),
        "top_fee_drag_rows": fee_drag[:10],
    }


def _canonical_above(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return canonical (above_yes, above_no) legs regardless of native comparator.

    above_yes pays when price > strike; above_no pays when price <= strike.
    """
    q = row.get("quote") or {}
    yes = {"ask": q.get("yes_ask"), "size": q.get("yes_ask_size"), "native_side": "YES"}
    no = {"ask": q.get("no_ask"), "size": q.get("no_ask_size"), "native_side": "NO"}
    comparator = row.get("comparator")
    # For up/down (interval_start_to_end_change), "up" plays the role of the
    # positive/YES side and "down" the complement — same canonicalization as
    # above/below so a single hedge builder serves both lanes.
    if comparator in ("above", "up"):
        return yes, no
    if comparator in ("below", "down"):
        return no, yes
    empty = {"ask": None, "size": None, "native_side": None}
    return dict(empty), dict(empty)


def _leg_view(row: dict[str, Any], leg: dict[str, Any], canonical_side: str, fee: float | None) -> dict[str, Any]:
    return {
        "venue": row.get("platform"),
        "canonical_side": canonical_side,
        "native_side": leg.get("native_side"),
        "native_comparator": row.get("comparator"),
        "ask": _valid_ask(leg.get("ask")),
        "ask_size": _to_float(leg.get("size")),
        "fee_estimate": fee,
        "market_id_or_ticker": row.get("market_id_or_ticker"),
        "condition_id": row.get("condition_id"),
        "contract_id": row.get("contract_id"),
        "target_instant_utc": row.get("target_instant_utc"),
        "quote_timestamp": (row.get("quote") or {}).get("quote_timestamp"),
        "price_source": row.get("price_source"),
        "depth_status": (row.get("quote") or {}).get("depth_status"),
    }


def _watch_row(asset: str, row: dict[str, Any], *, reason: str, hard: str | None = None) -> dict[str, Any]:
    hard_blockers = [hard] if hard else ([reason] if reason in {"incompatible_shape"} else [])
    return {
        "lane": "direct",
        "action": ACTION_IGNORE if hard_blockers else ACTION_WATCH,
        "paper_candidate": False,
        "paper_candidate_class": CLASS_NONE,
        "asset": asset,
        "market_shape": row.get("market_shape"),
        "payoff_observation_type": row.get("payoff_observation_type"),
        "endpoint_alignment_status": row.get("endpoint_alignment_status"),
        "harmonic_alignment_used": False,
        "compatibility_reason": reason,
        "threshold_or_strike": row.get("threshold_or_strike"),
        "target_instant_utc": row.get("target_instant_utc"),
        "reference_start_utc": row.get("reference_start_utc"),
        "interval_length_seconds": row.get("interval_length_seconds"),
        "direction": f"{str(row.get('platform') or '').upper()}_SINGLE_VENUE",
        "leg_1": {
            "venue": row.get("platform"),
            "comparator": row.get("comparator"),
            "market_id_or_ticker": row.get("market_id_or_ticker"),
            "quote": row.get("quote"),
        },
        "leg_2": {},
        "net_edge_after_fees": None,
        "available_size_or_cap": None,
        "assumptions_accepted": [],
        "hard_blockers": hard_blockers,
        "risk_notes": [reason],
        "candidate_action": "",
        "strict_exact_arb": False,
    }


def _intervals_differ(ra: dict[str, Any], rb: dict[str, Any]) -> bool:
    """True when the two point-in-time legs were aligned on the same target instant
    despite different (or differently-known) interval grids — i.e. harmonic."""
    a = ra.get("interval_length_seconds")
    b = rb.get("interval_length_seconds")
    if a is None and b is None:
        return False
    return a != b


def _reference_starts_match(ra: dict[str, Any], rb: dict[str, Any], tolerance_seconds: float) -> bool:
    a = _parse_dt(ra.get("reference_start_utc"))
    b = _parse_dt(rb.get("reference_start_utc"))
    if a is None or b is None:
        return False
    return abs((a - b).total_seconds()) <= tolerance_seconds


def _harmonic_endpoints(asset: str, all_rows: list[dict[str, Any]], tolerance_seconds: float) -> list[dict[str, Any]]:
    by_instant: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in all_rows:
        inst = r.get("target_instant_utc")
        if inst:
            by_instant[inst].append(r)
    out: list[dict[str, Any]] = []
    for instant, rows in sorted(by_instant.items()):
        platforms = sorted({r["platform"] for r in rows})
        intervals = sorted({r.get("interval_length_seconds") for r in rows if r.get("interval_length_seconds") is not None})
        obs_types = sorted({str(r.get("payoff_observation_type") or "unknown") for r in rows})
        obs_by_platform: dict[str, set[str]] = defaultdict(set)
        for r in rows:
            obs_by_platform[r["platform"]].add(str(r.get("payoff_observation_type") or "unknown"))
        pit_platforms = {p for p, o in obs_by_platform.items() if "point_in_time_at_target" in o}
        ud_platforms = {p for p, o in obs_by_platform.items() if "interval_start_to_end_change" in o}
        compatible = False
        reason = "single_platform_or_incompatible_types"
        if len(pit_platforms) >= 2:
            compatible = True
            reason = "point_in_time_threshold_compatible_across_interval_lengths"
        elif len(ud_platforms) >= 2:
            ud_rows = [r for r in rows if str(r.get("payoff_observation_type")) == "interval_start_to_end_change"]
            starts = {r.get("reference_start_utc") for r in ud_rows}
            if len(starts) == 1 and None not in starts:
                compatible = True
                reason = "updown_same_reference_start"
            else:
                reason = "updown_endpoint_aligned_but_reference_start_differs"
        elif any("range_at_target" in o for o in obs_by_platform.values()) and pit_platforms:
            reason = "range_vs_threshold_synthetic_lane_only"
        elif any("touch_before_deadline" in o for o in obs_by_platform.values()):
            reason = "touch_or_deadline_not_matchable"
        out.append(
            {
                "asset": asset,
                "target_instant_utc": instant,
                "platforms_present": platforms,
                "interval_lengths_present": intervals,
                "observation_types_present": obs_types,
                "compatible_for_direct_match": compatible,
                "reason": reason,
            }
        )
    return out


def _unmatched_instant_row(asset: str, ra: dict[str, Any], rb: dict[str, Any], strike: float | None) -> dict[str, Any]:
    return {
        "asset": asset,
        "threshold_or_strike": strike,
        "venue_a": ra.get("platform"),
        "venue_a_instant": ra.get("target_instant_utc"),
        "venue_b": rb.get("platform"),
        "venue_b_instant": rb.get("target_instant_utc"),
        "blocker": "target_time_mismatch",
    }


# ---------------------------------------------------------------------------- #
# Numeric / fee / freshness helpers                                            #
# ---------------------------------------------------------------------------- #


def _leg_fee(platform: str, ask: float | None) -> float | None:
    if ask is None:
        return None
    if platform == "kalshi":
        return round(_KALSHI_FEE.fee_for_leg(ask), 6)
    if platform == "polymarket":
        return round(_POLY_FEE.fee_for_leg_for_category(ask, category="crypto"), 6)
    if platform == "cdna":
        return CDNA_FEE_PER_CONTRACT
    return None


def _effective_size(row: dict[str, Any], leg: dict[str, Any], cdna_cap: float) -> float | None:
    size = _to_float(leg.get("size"))
    if size is not None:
        return size
    if row.get("platform") == "cdna":
        return cdna_cap
    return None


def _valid_ask(value: Any) -> float | None:
    ask = _to_float(value)
    if ask is None or not 0.0 <= ask <= 1.0:
        return None
    return ask


def _instant_delta_seconds(ra: dict[str, Any], rb: dict[str, Any]) -> float | None:
    a = _parse_dt(ra.get("target_instant_utc"))
    b = _parse_dt(rb.get("target_instant_utc"))
    if a is None or b is None:
        return None
    return abs((a - b).total_seconds())


def _stale(row: dict[str, Any], generated: datetime, max_age: float) -> bool:
    ts = (row.get("quote") or {}).get("quote_timestamp")
    parsed = _parse_dt(ts)
    if parsed is None:
        return True
    return (generated - parsed).total_seconds() > max_age


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sample_instants(rows: list[dict[str, Any]]) -> list[str]:
    return sorted({str(r.get("target_instant_utc")) for r in rows if r.get("target_instant_utc")})[:8]


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return -1e9


# ---------------------------------------------------------------------------- #
# Summary + reasons                                                            #
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
        "operator_paper_candidate_rows": classes.get(CLASS_OPERATOR, 0),
        "cdna_fill_first_paper_candidate_rows": classes.get(CLASS_CDNA, 0),
        "watch_rows": actions.get(ACTION_WATCH, 0),
        "ignore_blocked_rows": actions.get(ACTION_IGNORE, 0),
        "top_blockers": [{"blocker": k, "count": v} for k, v in hard_counter.most_common(15)],
    }


def _reasons(
    *,
    rows: list[dict[str, Any]],
    venue_counts: dict[str, int],
    asset_reports: list[dict[str, Any]],
    include_cdna: bool,
    refreshed: bool,
    unmatched_instant: list[dict[str, Any]],
) -> dict[str, str | None]:
    total_k = venue_counts.get("kalshi_markets", 0)
    total_p = venue_counts.get("polymarket_markets", 0)
    total_c = venue_counts.get("cdna_markets", 0)
    total_keys = venue_counts.get("typed_key_candidates", 0)
    has_candidate = any(r.get("paper_candidate") for r in rows)

    kalshi_zero_reason = None
    if total_k == 0:
        series = sorted({s for a in asset_reports for s in ((a.get("kalshi_diagnostics") or {}).get("series_queried") or [])})
        kalshi_zero_reason = (
            "kalshi interval discovery found zero in-window markets"
            + (f"; series queried: {', '.join(series)}" if series else "")
            if refreshed
            else "no kalshi interval evidence loaded"
        )
    polymarket_zero_reason = None
    if total_p == 0:
        polymarket_zero_reason = (
            "polymarket interval discovery found zero in-window markets (hourly slug + keyword)"
            if refreshed
            else "no polymarket interval evidence loaded"
        )
    if not include_cdna:
        cdna_zero_reason: str | None = "cdna_not_requested"
    elif total_c == 0:
        cdna_zero_reason = "cdna_evidence_missing_or_out_of_window"
    else:
        cdna_zero_reason = None

    no_cross = None
    if not has_candidate:
        if total_k == 0 and total_p == 0 and total_c == 0:
            no_cross = "no_markets_discovered_on_any_venue"
        elif total_k == 0:
            no_cross = f"kalshi_zero: {kalshi_zero_reason}"
        elif total_p == 0:
            no_cross = f"polymarket_zero: {polymarket_zero_reason}"
        elif total_keys == 0:
            shared_instant = any(
                (a.get("matching_diagnostics") or {}).get("shared_instant_count") for a in asset_reports
            )
            kalshi_has_buckets = any(
                ((a.get("matching_diagnostics") or {}).get("kalshi_shapes") or {}).get("range_bucket") for a in asset_reports
            )
            if shared_instant and kalshi_has_buckets:
                no_cross = (
                    "shape_mismatch_at_shared_instant: venues settle at the SAME instant but "
                    "Kalshi hourly is range-bucket shaped while Polymarket is cumulative-threshold; "
                    "no single-market typed key overlaps. A Kalshi cumulative threshold must be "
                    "synthesized from buckets (multi-leg) to compare to Polymarket."
                )
            elif shared_instant:
                no_cross = (
                    "shared_instant_but_no_strike_overlap: venues settle at the same instant but "
                    "their strike grids do not coincide; see unmatched_by_target_instant"
                )
            else:
                no_cross = (
                    "no_shared_instant_and_strike: venues have in-window markets but share no "
                    "(strike, settlement instant) typed key; see unmatched_by_target_instant"
                )
        elif unmatched_instant:
            no_cross = "matched_windows_exist_but_no_positive_post_fee_edge_or_blocked"
        else:
            no_cross = "matched_windows_blocked_or_no_positive_edge"
    return {
        "kalshi_zero_reason": kalshi_zero_reason,
        "polymarket_zero_reason": polymarket_zero_reason,
        "cdna_zero_reason": cdna_zero_reason,
        "no_cross_venue_rows_reason": no_cross,
    }


# ---------------------------------------------------------------------------- #
# Markdown                                                                     #
# ---------------------------------------------------------------------------- #


def render_crypto_interval_three_venue_check_markdown(report: dict[str, Any]) -> str:
    counts = report.get("summary_counts") or {}
    rows = report.get("rows") or []
    paper = [r for r in rows if r.get("paper_candidate")]
    watch = [r for r in rows if r.get("action") == ACTION_WATCH]
    vc = report.get("venue_market_counts") or {}
    lines = [
        "# Crypto Interval Three-Venue Check",
        "",
        "Live/upcoming intraday crypto interval & point-in-time threshold scan across "
        "Kalshi (public GET), Polymarket (public GET), and optional saved CDNA evidence. "
        "Matched by EXACT UTC settlement instant. Asks only; no midpoint. Source/index "
        "basis is operator-accepted; settlement-instant discipline is not.",
        "",
        "## 1. Summary",
        "",
        f"- operator_risk_mode: `{_md(report.get('operator_risk_mode'))}`",
        f"- assets_requested: `{', '.join(report.get('assets_requested') or [])}`",
        f"- lookahead_hours: `{report.get('lookahead_hours')}`  target_time_tolerance_seconds: `{report.get('target_time_tolerance_seconds')}`",
        f"- venue_market_counts: `kalshi={vc.get('kalshi_markets', 0)} polymarket={vc.get('polymarket_markets', 0)} cdna={vc.get('cdna_markets', 0)}`",
        f"- exact_matched_windows (typed_key_candidates): `{report.get('typed_key_candidates', 0)}`",
        f"- rows: `{counts.get('rows', 0)}`  paper_candidate_rows: `{counts.get('paper_candidate_rows', 0)}` "
        f"(operator: `{counts.get('operator_paper_candidate_rows', 0)}`, cdna_fill_first: `{counts.get('cdna_fill_first_paper_candidate_rows', 0)}`)",
        f"- watch_rows: `{counts.get('watch_rows', 0)}`  ignore_blocked_rows: `{counts.get('ignore_blocked_rows', 0)}`",
        f"- no_cross_venue_rows_reason: `{_md(report.get('no_cross_venue_rows_reason') or 'rows present')}`",
        f"- kalshi_zero_reason: `{_md(report.get('kalshi_zero_reason') or 'kalshi markets present')}`",
        f"- polymarket_zero_reason: `{_md(report.get('polymarket_zero_reason') or 'polymarket markets present')}`",
        f"- cdna_zero_reason: `{_md(report.get('cdna_zero_reason') or 'cdna markets present')}`",
        "",
        "## 2. Paper Candidates (sorted by net edge after fees)",
        "",
        "| Class | Asset | Strike | Instant (UTC) | Direction | Net edge after fees | Size/cap | Assumptions | Candidate action |",
        "|---|---|---:|---|---|---:|---:|---|---|",
    ]
    if not paper:
        lines.append("| none |  |  |  |  |  |  |  |  |")
    for r in paper[:50]:
        lines.append(
            "| "
            f"{_md(r.get('paper_candidate_class'))} | {_md(r.get('asset'))} | {_md(r.get('threshold_or_strike'))} | "
            f"{_md(r.get('target_instant_utc'))} | {_md(r.get('direction'))} | {_md(r.get('net_edge_after_fees'))} | "
            f"{_md(r.get('available_size_or_cap'))} | {_md(', '.join(r.get('assumptions_accepted') or []))} | "
            f"{_md(r.get('candidate_action'))} |"
        )

    lines.extend(
        [
            "",
            "## 3. Upcoming matched windows",
            "",
            "| Asset | Strike | Instant (UTC) | Venues | Best net edge | Has candidate |",
            "|---|---:|---|---|---:|---|",
        ]
    )
    matched = report.get("exact_matched_windows") or []
    if not matched:
        lines.append("| none |  |  |  |  |  |")
    for w in matched[:60]:
        lines.append(
            "| "
            f"{_md(w.get('asset'))} | {_md(w.get('threshold_or_strike'))} | {_md(w.get('target_instant_utc'))} | "
            f"{_md(', '.join(w.get('venues') or []))} | {_md(w.get('best_net_edge_after_fees'))} | "
            f"{'yes' if w.get('has_paper_candidate') else 'no'} |"
        )

    # Harmonic endpoint alignment — which shared target instants are compatible for
    # a direct match (point-in-time across interval grids; up/down only if same start).
    hs = report.get("harmonic_summary") or {}
    lines.extend(
        [
            "",
            "## 3b. Harmonic Endpoint Alignment",
            "",
            f"- compatible_shared_target_instants: `{hs.get('compatible_shared_target_instants', 0)}` "
            f"of `{hs.get('endpoints', 0)}` endpoints",
            f"- point_in_time_matches: `{hs.get('point_in_time_matches', 0)}` "
            f"(harmonic / cross-interval: `{hs.get('harmonic_point_in_time_matches', 0)}`)  "
            f"direct_updown_matches: `{hs.get('direct_updown_matches', 0)}`",
            "",
            "| Asset | Target instant (UTC) | Platforms | Interval lengths (s) | Observation types | Compatible | Reason |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    harmonic = report.get("harmonic_endpoint_alignment") or []
    if not harmonic:
        lines.append("| none |  |  |  |  |  |  |")
    for e in harmonic[:80]:
        lines.append(
            "| "
            f"{_md(e.get('asset'))} | {_md(e.get('target_instant_utc'))} | "
            f"{_md(', '.join(e.get('platforms_present') or []))} | "
            f"{_md(', '.join(str(x) for x in e.get('interval_lengths_present') or []))} | "
            f"{_md(', '.join(e.get('observation_types_present') or []))} | "
            f"{'yes' if e.get('compatible_for_direct_match') else 'no'} | {_md(e.get('reason'))} |"
        )

    lines.extend(["", "## 4. Watch rows", "", "| Asset | Shape | Strike | Instant (UTC) | Direction | Risk notes |", "|---|---|---:|---|---|---|"])
    if not watch:
        lines.append("| none |  |  |  |  |  |")
    for r in watch[:50]:
        lines.append(
            "| "
            f"{_md(r.get('asset'))} | {_md(r.get('market_shape'))} | {_md(r.get('threshold_or_strike'))} | "
            f"{_md(r.get('target_instant_utc'))} | {_md(r.get('direction'))} | {_md(', '.join(r.get('risk_notes') or []))} |"
        )

    lines.extend(
        [
            "",
            "## 5. Unmatched by target instant",
            "",
            "| Asset | Strike | Venue A | Instant A | Venue B | Instant B |",
            "|---|---:|---|---|---|---|",
        ]
    )
    unmatched = report.get("unmatched_by_target_instant") or []
    if not unmatched:
        lines.append("| none |  |  |  |  |  |")
    for u in unmatched[:50]:
        lines.append(
            "| "
            f"{_md(u.get('asset'))} | {_md(u.get('threshold_or_strike'))} | {_md(u.get('venue_a'))} | "
            f"{_md(u.get('venue_a_instant'))} | {_md(u.get('venue_b'))} | {_md(u.get('venue_b_instant'))} |"
        )

    # Synthetic lane.
    syn_summary = report.get("synthetic_summary") or {}
    syn_rows = report.get("synthetic_rows") or []
    syn_paper = [r for r in syn_rows if r.get("paper_candidate")]
    lines.extend(
        [
            "",
            "## 5c. Synthetic Bucket Candidates",
            "",
            "Synthetic = Kalshi cumulative threshold built from YES on mutually-exclusive "
            "constituent buckets, compared to a Polymarket cumulative threshold at the same "
            "instant. Per-leg Kalshi fees mean many-leg baskets often net negative.",
            "",
            f"- bucket_families_discovered: `{syn_summary.get('bucket_families_discovered', 0)}` "
            f"thresholds_tested: `{syn_summary.get('thresholds_tested', 0)}` "
            f"synthetic_candidates_generated: `{syn_summary.get('synthetic_candidates_generated', 0)}`",
            f"- synthetic_rows: `{syn_summary.get('synthetic_rows', 0)}` "
            f"(paper: `{syn_summary.get('synthetic_paper_candidate_rows', 0)}`, "
            f"watch: `{syn_summary.get('synthetic_watch_rows', 0)}`, "
            f"ignore_blocked: `{syn_summary.get('synthetic_ignore_blocked_rows', 0)}`)",
            "",
            "| Action | Asset | Threshold | Instant (UTC) | Direction | Bucket legs | Net edge after fees | Total fee | Size/cap | Coverage | Assumptions | Blockers |",
            "|---|---|---:|---|---|---:|---:|---:|---:|---|---|---|",
        ]
    )
    syn_display = (syn_paper or [])[:50] if syn_paper else syn_rows[:50]
    if not syn_display:
        lines.append("| none |  |  |  |  |  |  |  |  |  |  |  |")
    for r in syn_display:
        lines.append(
            "| "
            f"{_md(r.get('action'))} | {_md(r.get('asset'))} | {_md(r.get('threshold'))} | "
            f"{_md(r.get('target_instant_utc'))} | {_md(r.get('direction'))} | {_md(r.get('kalshi_bucket_leg_count'))} | "
            f"{_md(r.get('net_edge_after_fees'))} | {_md(r.get('total_fee_estimate'))} | {_md(r.get('available_size_or_cap'))} | "
            f"{_md(r.get('bucket_coverage_status'))} | {_md(', '.join(r.get('assumptions_accepted') or []))} | "
            f"{_md(', '.join(r.get('hard_blockers') or []))} |"
        )
    # Synthetic diagnostics: rejection reasons + top fee-drag rows.
    syn_rej: Counter = Counter()
    for d in report.get("synthetic_diagnostics") or []:
        for k, v in (d.get("rejected") or {}).items():
            syn_rej[k] += v
    if syn_rej:
        lines.append("")
        lines.append(f"- rejected_synthetic_by_reason: `{_md(_fmt_counter(dict(syn_rej)))}`")
    if syn_summary.get("top_fee_drag_rows"):
        lines.append("- top fee-drag rows:")
        for d in syn_summary["top_fee_drag_rows"][:5]:
            lines.append(
                f"  - {_md(d.get('asset'))} {_md(d.get('basket_type'))} X={_md(d.get('threshold'))} "
                f"legs={_md(d.get('leg_count'))} fee={_md(d.get('total_fee_estimate'))} net={_md(d.get('net_edge_after_fees'))}"
            )

    lines.extend(["", "## 6. Kalshi discovery diagnostics", "", "| Asset | Series | Events | Markets | In window | Rows kept | Rejections |", "|---|---|---:|---:|---:|---:|---|"])
    for d in report.get("kalshi_discovery_diagnostics") or []:
        lines.append(
            "| "
            f"{_md(d.get('asset'))} | {_md(', '.join(d.get('series_queried') or []))} | {_md(d.get('events_found', 0))} | "
            f"{_md(d.get('markets_found', 0))} | {_md(d.get('markets_in_window', 0))} | {_md(d.get('rows_kept', 0))} | "
            f"{_md(_fmt_counter(d.get('rejection_reasons') or {}))} |"
        )

    lines.extend(
        [
            "",
            "## 7. Polymarket discovery diagnostics",
            "",
            "| Asset | Queries | Events | Markets | In window | Rows kept | CLOB fails | Gamma fallback | Missing ask | Rejections |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for d in report.get("polymarket_discovery_diagnostics") or []:
        lines.append(
            "| "
            f"{_md(d.get('asset'))} | {_md(d.get('queries_attempted', 0))} | {_md(d.get('events_found', 0))} | "
            f"{_md(d.get('markets_found', 0))} | {_md(d.get('markets_in_window', 0))} | {_md(d.get('rows_kept', 0))} | "
            f"{_md(d.get('clob_fetch_failures', 0))} | {_md(d.get('gamma_fallback_used', 0))} | {_md(d.get('missing_ask_outcomes', 0))} | "
            f"{_md(_fmt_counter(d.get('rejection_reasons') or {}))} |"
        )

    lines.extend(["", "## 8. CDNA evidence diagnostics", "", "| Asset | Supplied | Rows loaded | In window | Rows kept | Warnings |", "|---|---|---:|---:|---:|---|"])
    for d in report.get("cdna_evidence_diagnostics") or []:
        lines.append(
            "| "
            f"{_md(d.get('asset'))} | {'yes' if d.get('supplied') else 'no'} | {_md(d.get('rows_loaded', 0))} | "
            f"{_md(d.get('rows_in_window', 0))} | {_md(d.get('rows_kept', 0))} | {_md(', '.join(d.get('warnings') or []))} |"
        )

    lines.extend(["", "## 9. Hard blockers (top across rows)", "", "| Blocker | Count |", "|---|---:|"])
    if not report.get("top_blockers"):
        lines.append("| none | 0 |")
    for item in report.get("top_blockers") or []:
        lines.append(f"| {_md(item.get('blocker'))} | {_md(item.get('count'))} |")

    lines.extend(
        [
            "",
            "## 10. Safety",
            "",
            "- diagnostic_only: `true`",
            "- public_read_only: `true`",
            "- cdna_network_fetch_attempted: `false`",
            "- uses_asks_for_entry: `true`",
            "- uses_midpoint: `false`",
            "- orders_or_execution_logic_added: `false`",
            "- auth_or_account_logic_added: `false`",
            "- browser_automation_added: `false`",
            "- strict_exact_arb: `false`",
        ]
    )
    return "\n".join(lines) + "\n"


def _fmt_counter(counter: dict[str, int]) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")
