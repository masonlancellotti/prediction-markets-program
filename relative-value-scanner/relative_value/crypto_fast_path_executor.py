"""Fast-path crypto micro-execution: split the slow structural scout from the hot
trigger loop.

Three phases:
  1. Discovery pass (slow, infrequent): run the full structural scout and freeze
     the buy-only candidate universe (leg IDs/markets/strikes/target instants/
     payoff vectors) into ``active_crypto_candidate_universe.json``.
  2. Fast quote loop (hot, 250ms-1000ms): watch ONLY the universe's legs, refresh
     + cache their quotes to ``quote_cache.jsonl``. No structural scan here.
  3. Trigger evaluator (hot): recompute net edge from the quote cache; the instant
     edge >= min, build protected limit BUY intents immediately and stamp the
     latencies — NO full report and NO markdown in the hot path. The full report
     and micro-test journal are written AFTER the decision (post-decision).

Hard requirements honored:
  - recognition->order-intent and quote-refresh->order-submit latencies are
    measured and reported.
  - no order if ``decision_age_ms > max_decision_age_ms`` or any leg
    ``quote_age_ms > max_quote_age_ms``.
  - no full structural scan in the hot path; no markdown before the decision.

Safety unchanged: dry-run default, protected LIMIT BUY only, no shorting/market
orders, CDNA manual fill-first, kill-switch checked, no network/keys/browser here
(the quote source is an injected refresher; production wraps the existing public
CLOB/orderbook GETs — WebSocket where an official/public client supports it, else
rate-limited REST polling).
"""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from relative_value.execution_microstructure_plan import build_single_execution_plan
from relative_value.extract_crypto_paper_candidate_audit_pack import _boundary_inclusivity_risk
from relative_value.crypto_micro_test_journal import (
    start_micro_test_from_objects, finalize_crypto_micro_test, record_micro_test_event,
)
from relative_value.live_crypto_execution_adapters import (
    default_adapters, build_order_request, redact, MODE_DRY_RUN, MODE_LIVE,
)
from relative_value.live_crypto_micro_executor import (
    _execute_live_orders, _ordered_plan_legs, LIVE_ENV_VAR, DEFAULT_KILL_SWITCH,
    MAX_TOTAL_NOTIONAL_HARD_CAP, MAX_PLATFORM_NOTIONAL_HARD_CAP, MAX_LEG_NOTIONAL_HARD_CAP, MIN_NET_EDGE_FLOOR,
    _attach_notification_results_to_final_report, _notification_payload, _notify_execution_event,
)
from relative_value.live_trade_notifications import LiveTradeNotifier


UNIVERSE_SCHEMA_KIND = "active_crypto_candidate_universe_v1"
FASTPATH_SCHEMA_KIND = "crypto_fast_path_trigger_v1"


# ---------------------------------------------------------------------------- #
# Phase 1: discovery pass                                                      #
# ---------------------------------------------------------------------------- #


def build_active_candidate_universe(
    *, assets: list[str], operator_risk_mode: str = "aggressive", include_cdna: bool = False,
    operator_accept_cdna_display_price_risk: bool = False,
    cdna_evidence_dir: Path | None = None, cdna_timeseries_dir: Path | None = None,
    max_cdna_snapshot_age_seconds: float = 60.0, require_cdna_fresh_for_cdna_candidates: bool = True,
    allow_top_of_book_depth: bool = True,
    executable_venues: Any = None, scan_venues: Any = None,
    exclude_non_executable_from_live_universe: bool = True,
    include_near_miss_templates: bool = False, near_miss_net_edge_threshold: float = 0.10,
    include_missing_quote_templates: bool = False, min_template_quality: str = "paper_only",
    operator_size_cap: float = 10.0, cdna_operator_size_cap: float = 1.0, max_basket_legs: int = 12,
    source_basis_buffer_bps: float = 0.0, lookahead_hours: float = 8.0, min_net_edge: float = 0.0,
    max_candidates: int = 50, output_path: Path | None = None, generated_at: datetime | None = None,
    report_builder: Callable[..., dict[str, Any]] | None = None, http_get: Any = None,
) -> dict[str, Any]:
    from relative_value.executable_venue_policy import normalize_venues, DEFAULT_EXECUTABLE_VENUES, DEFAULT_SCAN_VENUES
    exec_venues = normalize_venues(executable_venues, default=DEFAULT_EXECUTABLE_VENUES)
    scan_v = normalize_venues(scan_venues, default=DEFAULT_SCAN_VENUES)
    gen = _now(generated_at)
    builder = report_builder or _default_report_builder
    report = builder(
        assets=[a.strip().upper() for a in assets if a.strip()], operator_risk_mode=operator_risk_mode,
        include_cdna=include_cdna, operator_accept_cdna_display_price_risk=bool(operator_accept_cdna_display_price_risk or include_cdna),
        allow_top_of_book_depth=bool(allow_top_of_book_depth), operator_size_cap=operator_size_cap,
        cdna_operator_size_cap=cdna_operator_size_cap, cdna_evidence_dir=cdna_evidence_dir,
        max_basket_legs=int(max_basket_legs), source_basis_buffer_bps=source_basis_buffer_bps, lookahead_hours=lookahead_hours,
        generated_at=gen, refresh_kalshi_polymarket=True, http_get=http_get,
    )
    rows = report.get("rows") or []

    # Executable Kalshi/Polymarket templates — NOT only already-paper rows, so the
    # fast path always has legs to watch. Quality bar + near-miss/missing-quote flags
    # widen the net; CDNA/short/barrier/expired rows are excluded (and counted).
    exec_rows, template_diag = _select_executable_templates(
        rows, executable_venues=exec_venues, min_template_quality=str(min_template_quality),
        min_net_edge=float(min_net_edge), include_near_miss=bool(include_near_miss_templates),
        near_miss_threshold=float(near_miss_net_edge_threshold),
        include_missing_quote=bool(include_missing_quote_templates), now=gen)
    executable_candidates = [_universe_candidate(r) for r in exec_rows[: int(max_candidates)]]

    # CDNA harmonic terminal-threshold candidates from the latest saved snapshot (file
    # only). CDNA is SCAN-ONLY: it never enters the executable live universe — it is
    # surfaced as a non-executable scan candidate and counted, never watched for live.
    cdna_diagnostics: dict[str, Any] = {"cdna_supplied": False, "cdna_missing_reason": "cdna_timeseries_dir_not_provided"}
    cdna_scan_candidates: list[dict[str, Any]] = []
    if cdna_timeseries_dir is not None or cdna_evidence_dir is not None:
        from relative_value.cdna_fast_snapshot import load_latest_cdna_snapshot, build_cdna_fill_first_candidates
        snap = load_latest_cdna_snapshot(timeseries_dir=cdna_timeseries_dir, evidence_dir=cdna_evidence_dir, now=gen)
        gen_out = build_cdna_fill_first_candidates(
            cdna_rows=snap.get("rows") or [], partner_legs=_partner_terminal_legs(rows), now=gen,
            max_age_seconds=float(max_cdna_snapshot_age_seconds), cdna_operator_size_cap=float(cdna_operator_size_cap),
            operator_risk_mode=operator_risk_mode, require_fresh=bool(require_cdna_fresh_for_cdna_candidates),
            min_net_edge=float(min_net_edge))
        cdna_scan_candidates = [
            {**c, "execution_status": "NO_SAFE_ORDER_API", "executable": False,
             "do_not_trade_reason": "cdna_no_safe_automated_order_adapter"}
            for c in gen_out["candidates"]
            if _opt_f(c.get("net_edge_after_fees")) is not None
            and _opt_f(c.get("net_edge_after_fees")) >= float(min_net_edge)
            and not c.get("hard_blockers") and not c.get("requires_short_or_sell")]
        cdna_diagnostics = {k: v for k, v in gen_out.items() if k != "candidates"}
        cdna_diagnostics.update({
            "cdna_supplied": bool(snap.get("cdna_supplied")), "cdna_rows_loaded": snap.get("rows_loaded"),
            "cdna_latest_snapshot_generated_at": snap.get("generated_at"), "cdna_missing_reason": snap.get("missing_reason"),
            "require_cdna_fresh_for_cdna_candidates": bool(require_cdna_fresh_for_cdna_candidates),
            "max_cdna_snapshot_age_seconds": float(max_cdna_snapshot_age_seconds),
        })

    # The live universe is executable-only by default; CDNA + other non-executable
    # scan candidates are surfaced separately and never make the bot wait on a fill.
    non_executable_candidates = cdna_scan_candidates
    if exclude_non_executable_from_live_universe:
        candidates = executable_candidates
    else:
        candidates = executable_candidates + non_executable_candidates
    # CDNA excluded from the executable universe = snapshot fill-first candidates +
    # any scout row carrying a CDNA leg.
    excluded_cdna_count = len(cdna_scan_candidates) + int(
        (template_diag.get("excluded_by_reason") or {}).get("cdna_leg", 0))

    watched: dict[str, dict[str, Any]] = {}
    for c in candidates:  # watch legs of the live universe (executable-only when excluding)
        for leg in c["legs"]:
            watched.setdefault(leg["leg_key"], {k: leg[k] for k in (
                "leg_key", "platform", "market_id_or_ticker", "side", "token_id", "contract_id",
                "condition_id", "reference_ask", "fee", "available_size_or_cap")})
    watched_legs = list(watched.values())
    by_platform, by_side = _watched_breakdown(watched_legs)
    zero_reason = None
    if not executable_candidates:
        zero_reason = _zero_universe_reason(rows, template_diag, exec_venues)

    universe = {
        "schema_kind": UNIVERSE_SCHEMA_KIND, "generated_at": gen.isoformat(),
        "min_net_edge_at_discovery": float(min_net_edge), "assets": [a.strip().upper() for a in assets if a.strip()],
        "candidate_count": len(candidates),
        "executable_universe_candidate_count": len(executable_candidates),
        "non_executable_scan_candidate_count": len(non_executable_candidates),
        "excluded_cdna_candidate_count": excluded_cdna_count,
        "excluded_cdna_reason": ("no_safe_order_api" if excluded_cdna_count else None),
        "watched_leg_count": len(watched_legs),
        "watched_leg_count_by_platform": by_platform,
        "watched_leg_count_by_side": by_side,
        "zero_universe_reason": zero_reason,
        "cdna_candidate_count": sum(1 for c in candidates if _has_cdna_leg(c)),
        "executable_venues": list(exec_venues), "scan_venues": list(scan_v),
        "exclude_non_executable_from_live_universe": bool(exclude_non_executable_from_live_universe),
        "candidates": candidates, "watched_legs": watched_legs,
        "non_executable_candidates": non_executable_candidates,
        "executable_template_diagnostics": template_diag,
        "cdna_scan_only": True, "cdna_executable": False,
        "cdna_diagnostics": cdna_diagnostics,
        "discovery_params": {
            "assets": [a.strip().upper() for a in assets if a.strip()], "operator_risk_mode": operator_risk_mode,
            "include_cdna": bool(include_cdna), "cdna_evidence_dir": str(cdna_evidence_dir) if cdna_evidence_dir else None,
            "cdna_timeseries_dir": str(cdna_timeseries_dir) if cdna_timeseries_dir else None,
            "max_cdna_snapshot_age_seconds": float(max_cdna_snapshot_age_seconds),
            "require_cdna_fresh_for_cdna_candidates": bool(require_cdna_fresh_for_cdna_candidates),
            "executable_venues": list(exec_venues), "scan_venues": list(scan_v),
            "exclude_non_executable_from_live_universe": bool(exclude_non_executable_from_live_universe),
            "include_near_miss_templates": bool(include_near_miss_templates),
            "near_miss_net_edge_threshold": float(near_miss_net_edge_threshold),
            "include_missing_quote_templates": bool(include_missing_quote_templates),
            "min_template_quality": str(min_template_quality),
            "operator_size_cap": float(operator_size_cap), "cdna_operator_size_cap": float(cdna_operator_size_cap),
            "source_basis_buffer_bps": float(source_basis_buffer_bps), "lookahead_hours": float(lookahead_hours),
            "min_net_edge": float(min_net_edge), "max_candidates": int(max_candidates),
        },
        "safety": {"diagnostic_only": True, "discovery_pass_runs_full_scout": True, "network_access_in_hot_path": False,
                   "cdna_excluded_from_executable_universe": True},
    }
    if output_path is not None:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(json.dumps(universe, indent=2, sort_keys=True), encoding="utf-8")
    return universe


def _universe_candidate(row: dict[str, Any]) -> dict[str, Any]:
    legs = []
    for leg in row.get("basket_legs") or []:
        token_ids = leg.get("token_ids") if isinstance(leg.get("token_ids"), dict) else {}
        legs.append({
            "leg_key": _leg_key(leg),
            "platform": leg.get("platform"), "market_id_or_ticker": leg.get("market_id_or_ticker"),
            "side": leg.get("side"), "token_id": leg.get("token_id_yes") or leg.get("token_id_no") or token_ids.get("yes") or token_ids.get("no"),
            "contract_id": leg.get("contract_id"), "condition_id": leg.get("condition_id"),
            "strike": leg.get("threshold_or_strike") or leg.get("strike"),
            "reference_ask": _opt_f(leg.get("ask")), "fee": _opt_f(leg.get("fee")) or 0.0,
            "available_size_or_cap": _opt_f(leg.get("available_size_or_cap")),
        })
    return {
        "candidate_id": row.get("dedup_key") or _candidate_sig(row),
        "asset": row.get("asset"), "candidate_type": row.get("candidate_type"),
        "paper_candidate_class": row.get("paper_candidate_class"),
        "target_instant_utc": row.get("target_instant_utc"), "iteration_timestamp": row.get("iteration_timestamp"),
        "payoff_vector": list(row.get("payoff_vector") or []), "min_payoff": row.get("min_payoff"),
        "expected_net_edge_after_fees": row.get("net_edge_after_fees"),
        "expected_adjusted_net_edge_after_fees": row.get("adjusted_net_edge_after_fees"),
        "assumptions_accepted": list(row.get("assumptions_accepted") or []),
        "source_indexes": list(row.get("source_indexes") or []),
        "requires_short_or_sell": bool(row.get("requires_short_or_sell")),
        "hard_blockers": list(row.get("hard_blockers") or []), "legs": legs,
        "basket_legs": row.get("basket_legs") or [],  # kept for post-decision plan build
    }


_TEMPLATE_QUALITY_RANK = {"paper_only": 0, "priced_only": 1, "compatible_payoff": 2}


def _select_executable_templates(
    rows: list[dict[str, Any]], *, executable_venues, min_template_quality: str, min_net_edge: float,
    include_near_miss: bool, near_miss_threshold: float, include_missing_quote: bool, now: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Pick plausible buy-only Kalshi/Polymarket templates (not only already-paper rows)
    so the fast path always has executable legs to watch. CDNA/short/barrier/expired
    rows are excluded and counted by reason for ``zero_universe_reason``."""
    exec_set = {str(v).lower() for v in executable_venues}
    max_rank = _TEMPLATE_QUALITY_RANK.get(str(min_template_quality), 0)
    included: list[tuple[str, dict[str, Any]]] = []
    exclusion: Counter = Counter()
    tier_counts: Counter = Counter()
    kp_legs_present = 0
    for r in rows:
        for leg in r.get("basket_legs") or []:
            if str(leg.get("platform") or "").lower() in exec_set:
                kp_legs_present += 1
        include, tier, reason = _template_decision(
            r, exec_set=exec_set, max_rank=max_rank, min_net_edge=min_net_edge,
            include_near_miss=include_near_miss, near_miss_threshold=near_miss_threshold,
            include_missing_quote=include_missing_quote, now=now)
        if include:
            included.append((tier, r))
            tier_counts[tier] += 1
        elif reason:
            exclusion[reason] += 1
    included.sort(key=lambda it: (1 if it[1].get("paper_candidate") else 0,
                                  _opt_f(it[1].get("net_edge_after_fees")) if _opt_f(it[1].get("net_edge_after_fees")) is not None else -1e9),
                  reverse=True)
    diag = {"included_by_tier": dict(tier_counts), "excluded_by_reason": dict(exclusion),
            "kp_legs_present": kp_legs_present, "rows_scanned": len(rows), "min_template_quality": str(min_template_quality)}
    return [r for _t, r in included], diag


def _template_decision(r, *, exec_set, max_rank, min_net_edge, include_near_miss, near_miss_threshold,
                       include_missing_quote, now) -> tuple[bool, str | None, str | None]:
    if not r.get("tradable_buy_only", False) or r.get("requires_short_or_sell"):
        return False, None, "short_or_not_buy_only"
    legs = r.get("basket_legs") or []
    if not legs:
        return False, None, "no_legs"
    plats = [str(l.get("platform") or "").lower() for l in legs]
    if any(p == "cdna" for p in plats):
        return False, None, "cdna_leg"
    if any(p not in exec_set for p in plats):
        return False, None, "non_executable_venue"
    ctype = str(r.get("candidate_type") or "").lower()
    fam = str(r.get("contract_family") or "").lower()
    if "barrier" in ctype or "barrier" in fam or r.get("lane") == "barrier" \
            or any("barrier" in str(b) for b in (r.get("hard_blockers") or [])):
        return False, None, "barrier_incompatible"
    if _template_expired(r, now):
        return False, None, "expired_or_stale"
    if r.get("min_payoff") is None or not r.get("payoff_vector"):
        return False, None, "no_payoff_structure"
    net = _opt_f(r.get("net_edge_after_fees"))
    if r.get("paper_candidate"):
        tier, include, reason = "paper_only", True, None
    elif net is not None:
        tier = "priced_only"
        if net >= min_net_edge:
            include, reason = True, None
        elif include_near_miss and net >= -abs(near_miss_threshold):
            include, reason = True, None
        else:
            include, reason = False, "priced_below_threshold_and_not_near_miss"
    else:
        tier = "compatible_payoff"
        include = bool(include_missing_quote)
        reason = None if include else "missing_quote_not_included"
    if include and _TEMPLATE_QUALITY_RANK.get(tier, 0) > max_rank:
        return False, tier, "below_min_template_quality"
    return include, tier, reason


def _template_expired(r: dict[str, Any], now: datetime) -> bool:
    ts = r.get("target_instant_utc")
    if not isinstance(ts, str) or not ts.strip():
        return False
    try:
        dt = datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt <= now


def _zero_universe_reason(rows, diag, exec_venues) -> str:
    if not rows or int(diag.get("kp_legs_present", 0)) == 0:
        return "no_kalshi_polymarket_legs_available"
    excl = dict(diag.get("excluded_by_reason") or {})
    if not excl:
        return "no_compatible_kp_payoff_templates"
    mapping = {
        "cdna_leg": "all_templates_require_non_executable_venue",
        "non_executable_venue": "all_templates_require_non_executable_venue",
        "short_or_not_buy_only": "all_templates_require_shorting",
        "expired_or_stale": "all_templates_expired_or_stale",
        "priced_below_threshold_and_not_near_miss": "filters_too_strict_for_min_template_quality",
        "below_min_template_quality": "filters_too_strict_for_min_template_quality",
        "missing_quote_not_included": "filters_too_strict_for_min_template_quality",
    }
    dominant = max(excl.items(), key=lambda kv: kv[1])[0]
    return mapping.get(dominant, "no_compatible_kp_payoff_templates")


def _watched_breakdown(watched_legs: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, int]]:
    by_platform: Counter = Counter()
    by_side: Counter = Counter()
    for leg in watched_legs:
        by_platform[str(leg.get("platform") or "").lower()] += 1
        by_side[str(leg.get("side") or "").upper()] += 1
    return dict(by_platform), dict(by_side)


def _narrow_score(c: dict[str, Any], prefer_priced: bool, prefer_near_miss: bool) -> tuple:
    edge = _opt_f(c.get("expected_net_edge_after_fees"))
    priced = edge is not None
    near = edge is not None and edge > -0.10
    return ((1 if (prefer_priced and priced) else 0),
            (1 if (prefer_near_miss and near) else 0),
            edge if edge is not None else -1e9)


def _leg_to_watched(leg: dict[str, Any] | None) -> dict[str, Any] | None:
    if not leg:
        return None
    return {k: leg.get(k) for k in ("leg_key", "platform", "market_id_or_ticker", "side", "token_id",
                                    "contract_id", "condition_id", "reference_ask", "fee", "available_size_or_cap")}


def _narrow_watch_universe(candidates, watched, *, max_watched_candidates, max_watched_legs,
                           prefer_priced, prefer_near_miss) -> tuple[list, list]:
    """Keep the highest-value candidates (priced/near-miss first) and cap watched legs
    so the hot loop never refreshes hundreds of stale/low-value legs."""
    if not candidates:
        return candidates, watched
    ordered = sorted(candidates, key=lambda c: _narrow_score(c, prefer_priced, prefer_near_miss), reverse=True)
    if max_watched_candidates and int(max_watched_candidates) > 0:
        ordered = ordered[: int(max_watched_candidates)]
    watched_by_key = {l.get("leg_key"): l for l in (watched or [])}
    cand_leg_by_key: dict[str, Any] = {}
    for c in candidates:
        for leg in c.get("legs") or []:
            cand_leg_by_key.setdefault(leg.get("leg_key"), leg)
    kept, seen = [], set()
    for c in ordered:
        for leg in c.get("legs") or []:
            lk = leg.get("leg_key")
            if lk and lk not in seen:
                seen.add(lk)
                kept.append(lk)
    if max_watched_legs and int(max_watched_legs) > 0:
        kept = kept[: int(max_watched_legs)]
    kept_set = set(kept)
    new_watched = [w for lk in kept for w in (watched_by_key.get(lk) or _leg_to_watched(cand_leg_by_key.get(lk)),) if w]
    # Drop candidates whose legs were entirely cut by the leg cap (can't be priced).
    ordered = [c for c in ordered if all((leg.get("leg_key") in kept_set) for leg in (c.get("legs") or []))] or ordered
    return ordered, new_watched


def _priority_leg_keys(candidates: list[dict[str, Any]]) -> list[str]:
    """Refresh order: near-positive candidates' legs first, then priced, then
    LONG_ONLY_GUARANTEED_PAYOFF legs, then the rest."""
    def _key(c):
        edge = _opt_f(c.get("expected_net_edge_after_fees"))
        return (1 if (edge is not None and edge > -0.05) else 0, 1 if edge is not None else 0,
                1 if "LONG_ONLY" in str(c.get("candidate_type") or "").upper() else 0,
                edge if edge is not None else -1e9)
    keys, seen = [], set()
    for c in sorted(candidates, key=_key, reverse=True):
        for leg in c.get("legs") or []:
            lk = leg.get("leg_key")
            if lk and lk not in seen:
                seen.add(lk)
                keys.append(lk)
    return keys


def _partner_terminal_legs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Kalshi/Polymarket terminal-threshold legs from scout rows, as CDNA match partners.

    Interval length is deliberately NOT part of the identity — CDNA matches partners by
    asset + target_instant_utc + strike (terminal-threshold payoff grammar)."""
    out: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for r in rows or []:
        asset = r.get("asset")
        row_instant = r.get("target_instant_utc")
        for leg in r.get("basket_legs") or []:
            plat = str(leg.get("platform") or "").lower()
            if plat not in ("kalshi", "polymarket"):
                continue
            shape = str(leg.get("market_shape") or "").lower()
            fam = str(leg.get("contract_family") or "").lower()
            if shape != "point_in_time_threshold" and fam != "terminal_threshold":
                continue
            instant = leg.get("target_instant_utc") or row_instant
            strike = leg.get("threshold_or_strike") if leg.get("threshold_or_strike") is not None else leg.get("strike")
            key = (plat, leg.get("market_id_or_ticker"), str(leg.get("side") or "").upper(), instant, strike)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "platform": plat, "asset": asset, "target_instant_utc": instant, "threshold_or_strike": strike,
                "comparator": leg.get("comparator") or "above", "market_shape": "point_in_time_threshold",
                "contract_family": "terminal_threshold", "reference_start_utc": leg.get("reference_start_utc"),
                "interval_length_seconds": leg.get("interval_length_seconds"),
                "ask": leg.get("ask"), "fee": leg.get("fee"), "market_id_or_ticker": leg.get("market_id_or_ticker"),
                "token_id_yes": leg.get("token_id_yes"), "token_id_no": leg.get("token_id_no"),
                "token_id": leg.get("token_id"), "source_index": leg.get("source_index"),
                "available_size_or_cap": leg.get("available_size_or_cap"),
            })
    return out


# ---------------------------------------------------------------------------- #
# Phases 2+3: fast quote loop + trigger evaluator                              #
# ---------------------------------------------------------------------------- #


def run_crypto_fast_path_trigger(
    *,
    candidate_universe: Path,
    quote_loop_interval_ms: float = 500.0,
    iterations: int = 1,
    min_net_edge: float = 0.10,
    max_decision_age_ms: float = 500.0,
    max_quote_age_ms: float = 750.0,
    refresh_universe_every_seconds: float = 60.0,
    source_basis_buffer_bps: float = 0.0,
    max_slippage_cents: float = 1.0,
    order_timeout_ms: float = 1500.0,
    max_total_notional: float = 30.0,
    max_platform_notional: float = 10.0,
    max_leg_notional: float = 5.0,
    operator_size_cap: float = 10.0,
    max_orders: int = 4,
    max_residual_exposure: float = 5.0,
    execution_style: str = "manual",
    output_dir: Path = Path("reports/crypto_fast_path_trigger"),
    quote_source: str = "reference",
    quote_refresh_workers: int = 8,
    quote_request_timeout_ms: float = 750.0,
    max_quote_refresh_latency_ms: float = 1500.0,
    max_watched_candidates: int | None = None,
    max_watched_legs: int | None = None,
    prefer_priced_templates: bool = False,
    prefer_near_miss_templates: bool = False,
    dry_run: bool = True,
    live: bool = False,
    i_understand_this_places_real_orders: bool = False,
    quote_refresher: Callable[..., dict[str, Any]] | None = None,
    http_get: Any = None,
    cdna_timeseries_dir: Path | None = None,
    cdna_evidence_dir: Path | None = None,
    max_cdna_snapshot_age_seconds: float = 60.0,
    require_cdna_fresh_for_cdna_candidates: bool = True,
    cdna_operator_size_cap: float = 1.0,
    executable_venues: Any = None,
    discovery_fn: Callable[[], dict[str, Any]] | None = None,
    adapters: dict[str, Any] | None = None,
    clock: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] | None = None,
    console: Callable[[str], None] | None = None,
    env: dict[str, str] | None = None,
    kill_switch_path: Path | None = None,
    notify_provider: str = "dry_run",
    notify_send: bool = False,
    notify_on: str | list[str] | None = None,
    notify_dedup_seconds: float = 30.0,
    notification_http_post: Any = None,
) -> dict[str, Any]:
    now_fn = clock or (lambda: datetime.now(timezone.utc))
    sleeper = sleep or (lambda _s: None)
    emit = console or print
    env = env if env is not None else os.environ
    kill_switch = Path(kill_switch_path) if kill_switch_path is not None else DEFAULT_KILL_SWITCH
    if quote_refresher is not None:
        base_refresher = quote_refresher
    elif str(quote_source).lower() == "public_live":
        from relative_value.crypto_fast_quote_refresher import make_public_live_refresher
        base_refresher = make_public_live_refresher(
            http_get=http_get, workers=int(quote_refresh_workers),
            timeout_seconds=float(quote_request_timeout_ms) / 1000.0,
            max_latency_ms=float(max_quote_refresh_latency_ms))
    else:
        base_refresher = _universe_quote_refresher

    # CDNA: file-only, reload-on-change snapshot source (display-price/fill-first).
    # CDNA legs are served from the latest saved snapshot with strict freshness gates;
    # never from the network. Non-CDNA legs keep using the base refresher untouched.
    cdna_source = None
    if cdna_timeseries_dir is not None or cdna_evidence_dir is not None:
        from relative_value.cdna_fast_snapshot import CdnaFastQuoteSource
        cdna_source = CdnaFastQuoteSource(
            timeseries_dir=cdna_timeseries_dir, evidence_dir=cdna_evidence_dir,
            max_age_seconds=float(max_cdna_snapshot_age_seconds), clock=now_fn)

    def refresher(*, leg: dict[str, Any], now: datetime) -> dict[str, Any]:
        if cdna_source is not None and str(leg.get("platform") or "").lower() == "cdna":
            return cdna_source.quote(leg=leg, now=now)
        return base_refresher(leg=leg, now=now)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    from relative_value.executable_venue_policy import (
        normalize_venues, DEFAULT_EXECUTABLE_VENUES, build_adapter_status_report,
    )
    universe = _read_json(Path(candidate_universe)) or {}
    candidates = universe.get("candidates") or []
    watched = universe.get("watched_legs") or []
    # Narrow the watch set so the hot loop doesn't refresh hundreds of stale/low-value
    # legs: keep the highest-value candidates (priced / near-miss first), cap legs.
    narrowed_diag = {"watched_candidates_before": len(candidates), "watched_legs_before": len(watched)}
    candidates, watched = _narrow_watch_universe(
        candidates, watched, max_watched_candidates=max_watched_candidates, max_watched_legs=max_watched_legs,
        prefer_priced=prefer_priced_templates, prefer_near_miss=prefer_near_miss_templates)
    narrowed_diag.update({"watched_candidates_after": len(candidates), "watched_legs_after": len(watched)})
    # Refresh priority: candidate-critical legs first (near-positive edge, LONG_ONLY
    # guaranteed, then priced) so the freshest quotes go to the best candidates.
    priority_keys = _priority_leg_keys(candidates)
    exec_venues = normalize_venues(
        executable_venues if executable_venues is not None else universe.get("executable_venues"),
        default=DEFAULT_EXECUTABLE_VENUES)
    requested_live = bool(live) and not bool(dry_run)
    mode = MODE_LIVE if requested_live else MODE_DRY_RUN
    used_adapters = adapters if adapters is not None else default_adapters(mode=mode)
    adapter_status = build_adapter_status_report(used_adapters, executable_venues=exec_venues)
    notifier = LiveTradeNotifier(
        provider_name=notify_provider, send=notify_send, notify_on=notify_on,
        dedup_seconds=notify_dedup_seconds, env=env, http_post=notification_http_post, clock=now_fn)

    params = {
        "min_net_edge": float(min_net_edge), "max_decision_age_ms": float(max_decision_age_ms),
        "max_quote_age_ms": float(max_quote_age_ms), "max_slippage_cents": float(max_slippage_cents),
        "max_slippage_price": round(float(max_slippage_cents) / 100.0, 8), "order_timeout_ms": float(order_timeout_ms),
        "max_total_notional": float(max_total_notional), "max_platform_notional": float(max_platform_notional),
        "max_leg_notional": float(max_leg_notional), "operator_size_cap": float(operator_size_cap),
        "max_orders": int(max_orders), "max_residual_exposure": float(max_residual_exposure),
        "execution_style": str(execution_style),
        "source_basis_buffer_bps": float(source_basis_buffer_bps),
        "source_basis_buffer_edge": round(float(source_basis_buffer_bps) / 10000.0, 8),
        "refresh_universe_every_seconds": float(refresh_universe_every_seconds),
        "executable_venues": list(exec_venues),
        "all_live_adapters_ready": bool(adapter_status.get("all_live_adapters_ready")),
    }
    live_flags = {
        "requested_dry_run": bool(dry_run), "requested_live": bool(live),
        "i_understand_this_places_real_orders": bool(i_understand_this_places_real_orders),
        "env_live_enabled": str(env.get(LIVE_ENV_VAR, "")).strip().lower() == "true", "mode": mode,
    }

    quote_cache: dict[str, dict[str, Any]] = {}
    cache_path = output_dir / "quote_cache.jsonl"
    refresh_log_path = output_dir / "quote_refresh_log.jsonl"
    decisions: list[dict[str, Any]] = []
    tick_metrics: list[dict[str, Any]] = []
    recognized_first: dict[str, datetime] = {}
    discovery_runs = 0
    best_watched_edge: dict[str, float | None] = {"value": None}
    cdna_excluded_reasons: dict[str, int] = {}
    cdna_excluded_ids: set[str] = set()
    last_discovery_at = now_fn()  # the loaded universe file is the t0 discovery

    for tick in range(max(1, int(iterations))):
        tick_now = now_fn()
        # CDNA snapshot is file-only; reload it ONLY when the latest file changed
        # (never per leg, never over the network).
        if cdna_source is not None:
            cdna_source.reload_if_changed(tick_now)
        # Periodic re-discovery on a SLOW cadence — the full scout runs here, never
        # per quote tick. Bounded by refresh_universe_every_seconds.
        if discovery_fn is not None and (tick_now - last_discovery_at).total_seconds() >= params["refresh_universe_every_seconds"]:
            fresh = discovery_fn() or {}
            if fresh.get("candidates") is not None:
                candidates = fresh.get("candidates") or []
                watched = fresh.get("watched_legs") or []
                if Path(candidate_universe):
                    _write_json(Path(candidate_universe), fresh)
            discovery_runs += 1
            last_discovery_at = tick_now
            recognized_first.clear()

        refresh_started = now_fn()
        batch_diag: dict[str, Any] = {}
        # Bounded-concurrency batch refresh when the source supports it (public_live);
        # CDNA legs are still served from the file snapshot, never fetched here.
        batch_legs = [l for l in watched if str(l.get("platform") or "").lower() != "cdna"] \
            if cdna_source is not None else watched
        batch = getattr(base_refresher, "refresh_all", None)
        if callable(batch) and batch_legs:
            quotes, batch_diag = batch(batch_legs, now=refresh_started, priority_keys=priority_keys)
            for lk, q in quotes.items():
                quote_cache[lk] = q
                _append_jsonl(cache_path, {"tick": tick, "leg_key": lk, **q})
            for leg in watched:  # CDNA legs (file snapshot) — not in the parallel batch
                if str(leg.get("platform") or "").lower() == "cdna" and cdna_source is not None:
                    q = cdna_source.quote(leg=leg, now=refresh_started)
                    quote_cache[leg["leg_key"]] = q
                    _append_jsonl(cache_path, {"tick": tick, "leg_key": leg["leg_key"], **q})
        else:
            for leg in watched:
                q = refresher(leg=leg, now=refresh_started)
                quote_cache[leg["leg_key"]] = q
                _append_jsonl(cache_path, {"tick": tick, "leg_key": leg["leg_key"], **q})
        refresh_completed = now_fn()

        legs_requested = len(watched)
        legs_refreshed = sum(1 for l in watched if _opt_f((quote_cache.get(l["leg_key"]) or {}).get("ask")) is not None)
        tick_metric = {
            "tick": tick, "quote_source": str(quote_source),
            "quote_refresh_started_at": refresh_started.isoformat(),
            "quote_refresh_completed_at": refresh_completed.isoformat(),
            "quote_refresh_latency_ms": batch_diag.get("quote_refresh_latency_ms", _delta_ms(refresh_started, refresh_completed)),
            "legs_requested": legs_requested, "legs_refreshed": legs_refreshed,
            "legs_missing_quote": legs_requested - legs_refreshed,
            "quote_refresh_workers": batch_diag.get("quote_refresh_workers"),
            "unique_kalshi_fetches": batch_diag.get("unique_kalshi_fetches"),
            "unique_polymarket_fetches": batch_diag.get("unique_polymarket_fetches"),
            "unique_gamma_fetches": batch_diag.get("unique_gamma_fetches"),
            "per_platform_latency_ms": batch_diag.get("per_platform_latency_ms"),
            "rate_limit_or_timeout_errors": batch_diag.get("rate_limit_or_timeout_errors"),
            "quote_refresh_latency_exceeds_max": batch_diag.get("quote_refresh_latency_exceeds_max"),
        }
        tick_metrics.append(tick_metric)
        _append_jsonl(refresh_log_path, tick_metric)
        emit(f"fastpath tick={tick} quote_source={quote_source} legs_refreshed={legs_refreshed}/{legs_requested} "
             f"quote_refresh_latency_ms={tick_metric['quote_refresh_latency_ms']}")

        for cand in candidates:
            rec = _recompute_edge_from_cache(cand, quote_cache, params)
            # Track the best executable (K/P-only) watched edge for the run summary.
            edge = rec.get("net_edge_after_fees")
            if edge is not None and not _has_cdna_leg(cand) and (
                    best_watched_edge["value"] is None or edge > best_watched_edge["value"]):
                best_watched_edge["value"] = edge
            # CDNA-involved candidate blocked by a stale/missing snapshot: record the
            # reason and exclude it. Kalshi/Polymarket-only candidates are untouched.
            if _has_cdna_leg(cand):
                blocked = _cdna_block_reason(cand, quote_cache)
                if blocked:
                    cdna_excluded_ids.add(cand["candidate_id"])
                    cdna_excluded_reasons[blocked] = cdna_excluded_reasons.get(blocked, 0) + 1
            if not (rec["all_quotes_present"] and rec["net_edge_after_fees"] is not None
                    and rec["net_edge_after_fees"] >= params["min_net_edge"]
                    and rec["net_edge_after_fees_at_max_limits"] is not None
                    and rec["net_edge_after_fees_at_max_limits"] >= params["min_net_edge"]):
                continue
            recognized_at = recognized_first.setdefault(cand["candidate_id"], refresh_completed)
            decision = _decide_and_intent(
                cand=cand, rec=rec, params=params, live_flags=live_flags, mode=mode, used_adapters=used_adapters,
                recognized_at=recognized_at, refresh_started=refresh_started, refresh_completed=refresh_completed,
                output_dir=output_dir, now_fn=now_fn, sleeper=sleeper, kill_switch=kill_switch, tick=tick,
                notifier=notifier,
            )
            decisions.append(decision)
            emit(f"fastpath tick={tick} {cand.get('asset')} {cand.get('candidate_type')} "
                 f"do_trade={decision['do_trade']} rec->intent={decision['latency']['recognition_to_order_intent_ms']}ms "
                 f"qr->submit={decision['latency']['quote_refresh_to_order_submit_ms']}ms reasons={decision['do_not_trade_reasons']}")

        if tick < int(iterations) - 1:
            sleeper(float(quote_loop_interval_ms) / 1000.0)

    executable_universe = [c for c in candidates if not _has_cdna_leg(c)
                           and all(str(l.get("platform") or "").lower() in {v.lower() for v in exec_venues}
                                   for l in c.get("legs") or [])]
    zero_universe_reason = universe.get("zero_universe_reason")
    if not executable_universe and zero_universe_reason is None:
        zero_universe_reason = "no_executable_kp_candidates_in_universe"
    summary = {
        "schema_kind": FASTPATH_SCHEMA_KIND, "generated_at": now_fn().isoformat(),
        "candidate_universe": str(candidate_universe), "universe_candidate_count": len(candidates),
        "executable_universe_candidate_count": len(executable_universe),
        "non_executable_scan_candidate_count": len(universe.get("non_executable_candidates") or []),
        "watched_leg_count": len(watched), "mode": mode, "quote_source": str(quote_source),
        "executable_venues": list(exec_venues), "adapter_status": adapter_status,
        "watch_narrowing": narrowed_diag,
        "zero_universe_reason": zero_universe_reason, "best_watched_edge": best_watched_edge["value"],
        "live_flags": live_flags, "parameters": params,
        "ticks": int(max(1, iterations)), "decisions": len(decisions),
        "discovery_runs_during_loop": discovery_runs,
        "full_scout_runs_per_tick": 0,
        "quote_refresh_metrics": _summarize_refresh(tick_metrics),
        "tick_metrics": tick_metrics[:50],
        "decisions_that_would_trade": sum(1 for d in decisions if d["do_trade"]),
        "decision_records": [{"trigger_id": d["trigger_id"], "asset": d["asset"], "do_trade": d["do_trade"],
                              "latency": d["latency"], "do_not_trade_reasons": d["do_not_trade_reasons"],
                              "trigger_dir": d["trigger_dir"]} for d in decisions],
        "quote_cache_path": str(cache_path), "kill_switch_present": kill_switch.exists(),
        "cdna": _cdna_summary(cdna_source, candidates, universe, cdna_excluded_ids, cdna_excluded_reasons,
                              require_cdna_fresh_for_cdna_candidates, now_fn()),
        "safety": _safety(),
    }
    _write_json(output_dir / "fast_path_run_summary.json", summary)
    return summary


def _has_cdna_leg(cand: dict[str, Any]) -> bool:
    return any(str(l.get("platform") or "").lower() == "cdna" for l in cand.get("legs") or [])


def _cdna_block_reason(cand: dict[str, Any], cache: dict[str, dict[str, Any]]) -> str | None:
    """Return the CDNA freshness blocker for a CDNA leg of this candidate, if any."""
    for leg in cand.get("legs") or []:
        if str(leg.get("platform") or "").lower() != "cdna":
            continue
        q = cache.get(leg["leg_key"]) or {}
        blockers = q.get("hard_blockers") or []
        for reason in ("cdna_snapshot_stale", "cdna_target_expired", "missing_cdna_snapshot_row",
                       "missing_cdna_quote_timestamp", "missing_cdna_display_yes", "missing_cdna_display_no"):
            if reason in blockers:
                return reason
        if _opt_f(q.get("ask")) is None:
            return "cdna_quote_unavailable"
    return None


def _cdna_summary(cdna_source, candidates, universe, excluded_ids, excluded_reasons, require_fresh, now) -> dict[str, Any]:
    cdna_cands = [c for c in candidates if _has_cdna_leg(c)]
    if cdna_source is None:
        base = {"cdna_supplied": False, "cdna_rows_loaded": 0, "cdna_latest_snapshot_age_seconds": None,
                "cdna_snapshot_loaded_at": None, "cdna_stale_rows": 0, "cdna_fresh_rows": 0,
                "cdna_missing_reason": "cdna_timeseries_dir_not_provided", "cdna_reloads_during_loop": 0,
                "cdna_top_of_hour_rows": 0, "cdna_20m_top_of_hour_rows": 0, "cdna_2h_rows": 0}
    else:
        base = cdna_source.diagnostics(now=now)
    base.update({
        "require_cdna_fresh_for_cdna_candidates": bool(require_fresh),
        "cdna_candidates_considered": len(cdna_cands),
        "cdna_fill_first_candidates": sum(1 for c in cdna_cands
                                          if c.get("paper_candidate_class") == "CDNA_FILL_FIRST") or len(cdna_cands),
        "cdna_excluded_stale_candidate_count": len(excluded_ids),
        "cdna_excluded_stale_candidate_ids": sorted(excluded_ids)[:50],
        "cdna_excluded_reasons": dict(excluded_reasons),
        "cdna_universe_diagnostics": (universe.get("cdna_diagnostics") if isinstance(universe, dict) else None),
    })
    return base


def _decide_and_intent(*, cand, rec, params, live_flags, mode, used_adapters, recognized_at,
                       refresh_started, refresh_completed, output_dir, now_fn, sleeper, kill_switch, tick,
                       notifier: LiveTradeNotifier | None = None) -> dict[str, Any]:
    # --- HOT PATH: build protected limit intents immediately. No plan/markdown here. ---
    decision_started_at = now_fn()
    intents = []
    for leg in cand["legs"]:
        cached = rec["leg_detail"].get(leg["leg_key"], {})
        max_limit = cached.get("max_limit_price")
        qty = rec["quantity_cap"]
        req = build_order_request(
            client_order_id=f"fp-{cand['candidate_id'][:24]}-{tick}-{len(intents)}",
            leg={"platform": leg["platform"], "market_id_or_ticker": leg["market_id_or_ticker"],
                 "side": leg["side"], "token_id": leg["token_id"], "contract_id": leg["contract_id"],
                 "condition_id": leg["condition_id"]},
            max_limit_price=max_limit, quantity=qty, order_timeout_ms=params["order_timeout_ms"],
        )
        intents.append(req)
    decision_completed_at = now_fn()       # intents built
    order_intent_created_at = decision_completed_at
    order_submitted_at = now_fn()          # dry-run: the moment we would submit

    decision_age_ms = _delta_ms(refresh_completed, order_submitted_at)
    max_leg_quote_age = max([d.get("quote_age_ms") or 0.0 for d in rec["leg_detail"].values()] or [0.0])
    latency = {
        "candidate_recognized_at": recognized_at.isoformat(),
        "quote_refresh_started_at": refresh_started.isoformat(),
        "quote_refresh_completed_at": refresh_completed.isoformat(),
        "decision_started_at": decision_started_at.isoformat(),
        "decision_completed_at": decision_completed_at.isoformat(),
        "order_intent_created_at": order_intent_created_at.isoformat(),
        "order_submitted_at": order_submitted_at.isoformat(),
        "decision_latency_ms": _delta_ms(decision_started_at, decision_completed_at),
        "recognition_to_order_intent_ms": _delta_ms(recognized_at, order_intent_created_at),
        "quote_refresh_to_order_submit_ms": _delta_ms(refresh_completed, order_submitted_at),
        "decision_age_ms": decision_age_ms,
        "quote_age_ms": max_leg_quote_age,
        "max_leg_quote_age_ms": max_leg_quote_age,
        "max_decision_age_ms": params["max_decision_age_ms"], "max_quote_age_ms": params["max_quote_age_ms"],
    }

    do_not_trade = _fast_gates(cand, rec, params, live_flags, mode, kill_switch, decision_age_ms, max_leg_quote_age)
    do_trade = (mode == MODE_LIVE) and not do_not_trade

    trigger_id = f"{order_intent_created_at.strftime('%Y%m%dT%H%M%SZ')}_{cand.get('asset')}_t{tick}_{abs(hash(cand['candidate_id'])) % 10000:04d}"
    trigger_dir = output_dir / trigger_id
    (trigger_dir / "micro_test_journal").mkdir(parents=True, exist_ok=True)
    notification_results: list[dict[str, Any]] = []

    intended_orders = [req.to_redacted_dict() for req in intents]
    for rec_o in intended_orders:
        _append_jsonl(trigger_dir / "intended_orders.jsonl", rec_o)
    decision_record = {
        "trigger_id": trigger_id, "candidate_id": cand["candidate_id"], "asset": cand.get("asset"),
        "candidate_type": cand.get("candidate_type"), "mode": mode, "do_trade": do_trade,
        "do_not_trade_reasons": do_not_trade, "recomputed_edge": {k: rec[k] for k in
            ("net_edge_after_fees", "net_edge_after_fees_at_max_limits", "min_payoff", "total_all_in_cost", "quantity_cap")},
        "latency": latency, "intended_orders": intended_orders,
        "hot_path_no_full_scan": True, "hot_path_no_markdown": True,
    }
    _write_json(trigger_dir / "decision.json", decision_record)  # hot-path artifact (no markdown)
    if mode == MODE_DRY_RUN:
        result = notifier.notify("submitted", {
            "test_id": trigger_id,
            "asset": cand.get("asset"),
            "candidate_type": cand.get("candidate_type"),
            "expected_edge": rec.get("net_edge_after_fees"),
            "dry_run": True,
            "reason": ", ".join(do_not_trade) or "dry_run_no_live_orders",
            "short_status": "dry_run_intended_orders_created",
        }) if notifier is not None else None
        if result is not None:
            notification_results.append(result)
            _append_jsonl(trigger_dir / "event_log.jsonl", {
                "event_type": result.get("event_log_event_type") or "notification_skipped",
                "timestamp_utc": now_fn().isoformat(),
                "command": "trigger-crypto-fast-path",
                "inputs": {"notification": result},
                "derived_values": {},
                "warnings": [],
            })

    # --- POST-DECISION (not in measured hot path): live execution if armed, then full report + journal + markdown. ---
    execution_result = {"placed": False, "mode": mode, "fills": [], "cancels": [], "residual_exposure": [],
                        "emergency_review_required": False}
    if do_trade:
        plan = _post_decision_plan(cand, params, now_fn)
        execution_result = _execute_with_journal(
            cand, plan, params, used_adapters, trigger_id, trigger_dir, now_fn, sleeper, kill_switch,
            notifier, notification_results)
    execution_result["notification_results"] = notification_results
    _write_post_decision_report(cand, params, decision_record, execution_result, trigger_dir, now_fn, mode,
                                notification_results)
    decision_record["trigger_dir"] = str(trigger_dir)
    decision_record["execution_result"] = execution_result
    decision_record["notification_results"] = notification_results
    return decision_record


def _execute_with_journal(cand, plan, params, used_adapters, trigger_id, trigger_dir, now_fn, sleeper, kill_switch,
                          notifier=None, notification_results=None):
    jr = start_micro_test_from_objects(candidate={**cand, "basket_legs": cand.get("basket_legs")}, plan=plan,
                                       max_total_notional=params["max_total_notional"],
                                       test_label=trigger_id, output_root=trigger_dir / "micro_test_journal", now=now_fn())
    res = _execute_live_orders(
        plan=plan, ordered_legs=_ordered_plan_legs(plan), used_adapters=used_adapters, params=params,
        trigger_id=trigger_id, trigger_dir=trigger_dir, journal_test_id=jr["test_id"],
        journal_root=trigger_dir / "micro_test_journal", now_fn=now_fn, sleeper=sleeper, kill_switch=kill_switch,
        notifier=notifier, notifications=notification_results, notification_base={
            "test_id": jr["test_id"], "asset": cand.get("asset"), "candidate_type": cand.get("candidate_type"),
            "expected_edge": plan.get("expected_net_edge_after_fees") or cand.get("expected_net_edge_after_fees"),
        },
    )
    final = finalize_crypto_micro_test(test_id=jr["test_id"], output_root=trigger_dir / "micro_test_journal", now=now_fn())
    _notify_execution_event(
        notifier=notifier, event_type="finalized", notifications=notification_results,
        journal_test_id=jr["test_id"], journal_root=trigger_dir / "micro_test_journal", now_fn=now_fn,
        payload=_notification_payload(
            candidate=cand, test_id=jr["test_id"],
            expected_edge=final.get("actual_net_edge_after_fees_if_all_filled")
            or final.get("intended_net_edge_after_fees"),
            residual_exposure=final.get("residual_exposure"),
            short_status=str(final.get("verdict") or "finalized"),
        ),
    )
    _attach_notification_results_to_final_report(trigger_dir / "micro_test_journal", jr["test_id"],
                                                 notification_results or [])
    return res


def _fast_gates(cand, rec, params, live_flags, mode, kill_switch, decision_age_ms, max_leg_quote_age) -> list[str]:
    reasons: list[str] = []
    if mode == MODE_DRY_RUN:
        reasons.append("dry_run_default_no_live_orders")
    if not live_flags["env_live_enabled"]:
        reasons.append("env_LIVE_CRYPTO_MICROTEST_ENABLED_not_true")
    if not live_flags["requested_live"]:
        reasons.append("missing_flag_--live")
    if not live_flags["i_understand_this_places_real_orders"]:
        reasons.append("missing_flag_--i-understand-this-places-real-orders")
    if kill_switch.exists():
        reasons.append("kill_switch_present")
    # Latency / freshness HARD gates (the point of the fast path).
    if decision_age_ms is None or decision_age_ms > params["max_decision_age_ms"]:
        reasons.append("decision_age_exceeds_max")
    if max_leg_quote_age is None or max_leg_quote_age > params["max_quote_age_ms"]:
        reasons.append("quote_age_exceeds_max")
    # Edge / structural gates.
    if rec["net_edge_after_fees"] is None or rec["net_edge_after_fees"] < params["min_net_edge"]:
        reasons.append("net_edge_below_min")
    if rec["net_edge_after_fees_at_max_limits"] is None or rec["net_edge_after_fees_at_max_limits"] < params["min_net_edge"]:
        reasons.append("edge_below_min_after_max_slippage")
    adj = rec.get("adjusted_net_edge_after_fees")
    if adj is not None and adj < params["min_net_edge"]:
        reasons.append("adjusted_net_edge_below_min_after_basis_buffer")
    if cand.get("hard_blockers"):
        reasons.append("candidate_has_hard_blockers")
    if cand.get("requires_short_or_sell"):
        reasons.append("short_or_sell_required")
    if rec["quantity_cap"] <= 0:
        reasons.append("quantity_cap_zero")
    boundary_risk, _ = _boundary_inclusivity_risk(cand.get("basket_legs") or [])
    if boundary_risk:
        reasons.append("boundary_inclusivity_unvalidated")
    # Executable-venue policy: only Kalshi/Polymarket can be auto-executed. A CDNA leg
    # has no safe automated order API; any other off-list venue is scan-only. CDNA/non-
    # executable legs block ONLY their own candidate — never Kalshi/Polymarket ones.
    exec_set = {str(v).lower() for v in (params.get("executable_venues") or ("kalshi", "polymarket"))}
    leg_platforms = [str(l.get("platform") or "").lower() for l in cand.get("legs") or []]
    if any(p == "cdna" for p in leg_platforms):
        reasons.append("cdna_requires_manual_fill_first_no_confirmed_fill")
        reasons.append("cdna_no_safe_automated_order_adapter")
    if any(p not in exec_set for p in leg_platforms):
        reasons.append("non_executable_venue_leg")
    # Live adapters must be real (a client injected + preflight ok). A stub fails closed.
    if mode == MODE_LIVE and not params.get("all_live_adapters_ready"):
        reasons.append("live_adapter_not_implemented")
    # Hard live caps.
    if params["max_total_notional"] > MAX_TOTAL_NOTIONAL_HARD_CAP:
        reasons.append("max_total_notional_exceeds_cap_30")
    if params["max_platform_notional"] > MAX_PLATFORM_NOTIONAL_HARD_CAP:
        reasons.append("max_platform_notional_exceeds_cap_10")
    if params["max_leg_notional"] > MAX_LEG_NOTIONAL_HARD_CAP:
        reasons.append("max_leg_notional_exceeds_cap_5")
    if params["min_net_edge"] < MIN_NET_EDGE_FLOOR:
        reasons.append("min_net_edge_below_required_floor_0.10")
    if str(params["execution_style"]).lower() == "manual":
        reasons.append("manual_execution_style_no_automated_orders")
    return sorted(set(reasons))


def _recompute_edge_from_cache(cand: dict[str, Any], cache: dict[str, dict[str, Any]], params: dict[str, Any]) -> dict[str, Any]:
    import math
    slip = params["max_slippage_price"]
    min_payoff = _opt_f(cand.get("min_payoff"))
    leg_detail: dict[str, Any] = {}
    total_all_in = 0.0
    total_all_in_caps = 0.0
    all_present = bool(cand["legs"])
    per_leg_caps = []
    for leg in cand["legs"]:
        q = cache.get(leg["leg_key"]) or {}
        ask = _opt_f(q.get("ask"))
        fee = _opt_f(leg.get("fee")) or 0.0
        if ask is None:
            all_present = False
            leg_detail[leg["leg_key"]] = {"ask": None, "quote_age_ms": q.get("quote_age_ms"), "max_limit_price": None}
            continue
        is_cdna = str(leg.get("platform") or "").lower() == "cdna"
        max_limit = round(ask if is_cdna else min(ask + slip, 1.0), 8)
        all_in = round(ask + fee, 8)
        all_in_cap = round(max_limit + fee, 8)
        total_all_in += all_in
        total_all_in_caps += all_in_cap
        leg_detail[leg["leg_key"]] = {"ask": ask, "fee": fee, "max_limit_price": max_limit,
                                      "all_in_cost": all_in, "all_in_max_cost": all_in_cap,
                                      "quote_age_ms": _opt_f(q.get("quote_age_ms"))}
        cap_size = leg.get("available_size_or_cap")
        per_leg_caps.append(math.floor(params["max_leg_notional"] / all_in_cap) if all_in_cap > 0 else 0)
        if cap_size is not None:
            per_leg_caps[-1] = min(per_leg_caps[-1], math.floor(cap_size))
    net = round(min_payoff - total_all_in, 8) if (all_present and min_payoff is not None) else None
    net_caps = round(min_payoff - total_all_in_caps, 8) if (all_present and min_payoff is not None) else None
    # Source/index basis buffer: haircut the edge for cross-source baskets.
    sources = {str(s) for s in (cand.get("source_indexes") or [])}
    cross_source = len(sources) > 1
    basis_edge = params.get("source_basis_buffer_edge", 0.0) if cross_source else 0.0
    adjusted = round(net - basis_edge, 8) if net is not None else None
    adjusted_caps = round(net_caps - basis_edge, 8) if net_caps is not None else None
    by_total = math.floor(params["max_total_notional"] / total_all_in_caps) if (all_present and total_all_in_caps > 0) else 0
    qty = max(0, min([by_total] + per_leg_caps)) if (all_present and per_leg_caps) else 0
    return {
        "all_quotes_present": all_present, "min_payoff": min_payoff,
        "total_all_in_cost": round(total_all_in, 8) if all_present else None,
        "net_edge_after_fees": net, "net_edge_after_fees_at_max_limits": net_caps,
        "adjusted_net_edge_after_fees": adjusted, "adjusted_net_edge_after_fees_at_max_limits": adjusted_caps,
        "cross_source": cross_source, "source_basis_buffer_edge": basis_edge,
        "quantity_cap": int(qty), "leg_detail": leg_detail,
    }


# ---------------------------------------------------------------------------- #
# Post-decision report (async; never in the measured hot path)                 #
# ---------------------------------------------------------------------------- #


def _post_decision_plan(cand: dict[str, Any], params: dict[str, Any], now_fn) -> dict[str, Any]:
    plan_candidate = {**cand, "basket_legs": cand.get("basket_legs") or []}
    return build_single_execution_plan(
        plan_candidate, max_total_notional=params["max_total_notional"], max_leg_notional=params["max_leg_notional"],
        max_slippage_cents=params["max_slippage_cents"], max_quote_age_ms=params["max_quote_age_ms"],
        execution_style=params["execution_style"], min_net_edge=params["min_net_edge"], generated_at=now_fn(),
    )


def _write_post_decision_report(cand, params, decision_record, execution_result, trigger_dir, now_fn, mode,
                                notification_results=None) -> None:
    report = {
        "schema_kind": "crypto_fast_path_trigger_report_v1", "trigger_id": decision_record["trigger_id"],
        "asset": cand.get("asset"), "candidate_type": cand.get("candidate_type"), "mode": mode,
        "do_trade": decision_record["do_trade"], "do_not_trade_reasons": decision_record["do_not_trade_reasons"],
        "latency": decision_record["latency"], "recomputed_edge": decision_record["recomputed_edge"],
        "intended_orders": decision_record["intended_orders"], "execution_result": execution_result,
        "notification_results": list(notification_results or []),
        "written_after_decision_not_in_hot_path": True, "safety": _safety(),
    }
    _write_json(trigger_dir / "trigger_report.json", report)
    _write_text(trigger_dir / "trigger_report.md", _render_md(report))


def _render_md(r: dict[str, Any]) -> str:
    lat = r.get("latency") or {}
    e = r.get("recomputed_edge") or {}
    lines = [
        "# Crypto Fast-Path Trigger Report", "",
        "Fast-path decision (post-decision render — NOT in the hot path). Dry-run by default; "
        "protected LIMIT BUY only; no shorting; no market orders; CDNA manual fill-first.", "",
        "## Decision", "",
        f"- trigger_id: `{r.get('trigger_id')}`  asset: `{r.get('asset')}`  mode: **{r.get('mode')}**  do_trade: **{r.get('do_trade')}**",
        f"- do_not_trade_reasons: `{', '.join(r.get('do_not_trade_reasons') or []) or 'none'}`",
        "", "## Latency (measured in the hot path)", "",
        f"- recognition_to_order_intent_ms: `{lat.get('recognition_to_order_intent_ms')}`",
        f"- quote_refresh_to_order_submit_ms: `{lat.get('quote_refresh_to_order_submit_ms')}`",
        f"- decision_age_ms: `{lat.get('decision_age_ms')}` (max `{lat.get('max_decision_age_ms')}`)  "
        f"max_leg_quote_age_ms: `{lat.get('max_leg_quote_age_ms')}` (max `{lat.get('max_quote_age_ms')}`)",
        "", "## Recomputed Edge (from quote cache, no full scan)", "",
        f"- net_edge_after_fees: `{e.get('net_edge_after_fees')}`  at_max_limits: `{e.get('net_edge_after_fees_at_max_limits')}`  "
        f"min_payoff: `{e.get('min_payoff')}`  quantity_cap: `{e.get('quantity_cap')}`",
        "", "## Intended Protected Orders (BUY LIMIT only)", "",
        "| Platform | Side | Market | Max limit | Qty | Type |", "|---|---|---|---:|---:|---|",
    ]
    for o in r.get("intended_orders") or []:
        lines.append(f"| {o.get('platform')} | {o.get('side')} | {o.get('market_id_or_ticker')} | "
                     f"{o.get('max_limit_price')} | {o.get('quantity')} | {o.get('order_type')} |")
    lines += ["", "## Safety", "",
              "- dry_run_default: `true`  protected_limit_buy_only: `true`  market_orders: `false`  shorting: `false`",
              "- hot_path_no_full_scan: `true`  hot_path_no_markdown: `true`  browser_automation_added: `false`"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------- #
# Defaults + helpers                                                           #
# ---------------------------------------------------------------------------- #


def _default_report_builder(**kwargs: Any) -> dict[str, Any]:
    from relative_value.crypto_structural_payoff_arb_scout import build_crypto_structural_payoff_arb_scout_report
    return build_crypto_structural_payoff_arb_scout_report(**kwargs)


def _universe_quote_refresher(*, leg: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Offline/dry-run placeholder: re-emit the universe reference ask stamped now.
    Production injects a real fast source (public CLOB WebSocket where supported,
    else rate-limited REST polling)."""
    return {"platform": leg.get("platform"), "market_id_or_ticker": leg.get("market_id_or_ticker"),
            "side": leg.get("side"), "ask": _opt_f(leg.get("reference_ask")), "bid": None,
            "quote_timestamp": now.isoformat(), "quote_age_ms": 0.0, "depth_status": "universe_reference_dry_run",
            "source": "universe_reference_dry_run"}


def _safety() -> dict[str, Any]:
    return {"dry_run_default": True, "protected_limit_buy_only": True, "market_orders_disabled": True,
            "shorting_disabled": True, "hot_path_no_full_scan": True, "hot_path_no_markdown_before_decision": True,
            "browser_automation_added": False, "reads_credentials": False, "prints_secrets": False, "logs_redacted": True}


def _summarize_refresh(tick_metrics: list[dict[str, Any]]) -> dict[str, Any]:
    lats = [m["quote_refresh_latency_ms"] for m in tick_metrics if m.get("quote_refresh_latency_ms") is not None]
    last = tick_metrics[-1] if tick_metrics else {}
    return {
        "ticks": len(tick_metrics),
        "legs_requested_last": last.get("legs_requested", 0),
        "legs_refreshed_last": last.get("legs_refreshed", 0),
        "legs_missing_quote_last": last.get("legs_missing_quote", 0),
        "quote_refresh_latency_ms_last": last.get("quote_refresh_latency_ms"),
        "quote_refresh_latency_ms_avg": round(sum(lats) / len(lats), 3) if lats else None,
        "quote_refresh_latency_ms_max": max(lats) if lats else None,
        "quote_refresh_workers": last.get("quote_refresh_workers"),
        "unique_kalshi_fetches_last": last.get("unique_kalshi_fetches"),
        "unique_polymarket_fetches_last": last.get("unique_polymarket_fetches"),
        "unique_gamma_fetches_last": last.get("unique_gamma_fetches"),
        "per_platform_latency_ms_last": last.get("per_platform_latency_ms"),
        "rate_limit_or_timeout_errors_last": last.get("rate_limit_or_timeout_errors"),
        "quote_refresh_latency_exceeds_max_last": last.get("quote_refresh_latency_exceeds_max"),
    }


def _leg_key(leg: dict[str, Any]) -> str:
    return f"{str(leg.get('platform') or '').lower()}::{leg.get('market_id_or_ticker') or ''}::{str(leg.get('side') or '').upper()}"


def _candidate_sig(c: dict[str, Any]) -> str:
    legs = c.get("basket_legs") or []
    return f"{c.get('asset')}|{c.get('candidate_type')}|{c.get('target_instant_utc')}|" + "|".join(
        sorted(f"{l.get('platform')}:{l.get('side')}:{l.get('market_id_or_ticker')}" for l in legs))


def _now(now: datetime | None) -> datetime:
    ts = now or datetime.now(timezone.utc)
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True))
        fh.write("\n")


def _delta_ms(a: datetime | None, b: datetime | None) -> float | None:
    if a is None or b is None:
        return None
    return round((b - a).total_seconds() * 1000.0, 3)


def _opt_f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
