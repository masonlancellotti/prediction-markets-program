"""Execution-microstructure PLANNING layer for crypto structural-arb candidates.

Converts audited buy-only PAPER_CANDIDATE rows into *protected execution intents*
— order plans a human (or, later, a guarded executor) could act on — while
modelling the real microstructure risks of a tiny real-money test:

  - bid/ask drift between detection and placement (slippage caps + freshness TTL)
  - partial fills / one leg fills and the hedge moves (residual-exposure model)
  - stale quote snapshots (quote-age gate)
  - thin short-dated Kalshi books / Polymarket CLOB matching latency (timing budget)
  - CDNA display-price/fill-first uncertainty (fill-worst-leg-first, no orderbook)

HARD SCOPE — this module DESIGNS and SIMULATES only:
  - It NEVER places, submits, cancels, or signs an order.
  - No API keys, no .env, no auth/session, no network, no browser automation.
  - Output is ``order_intent`` records + a manual micro-test checklist. Any future
    live execution is a separate, explicitly-authorized task.

Documented venue microstructure encoded below is conservative and public:
  - Kalshi: binary YES/NO contracts settle to $1; limit orders rest on a CLOB;
    a NO buy is the complement of a YES sell. Short-dated hourly books are thin.
  - Polymarket: CLOB supports limit / marketable-limit (FOK/FAK/GTC) orders with
    matching latency; price can move between read and match.
  - Crypto.com / CDNA (Nadex/CDNA Rule 14.x): display-price/fill-first; no public
    server-side orderbook depth — fill the CDNA leg first, then hedge the exact
    filled quantity. Flat per-contract fee.
These are planning placeholders, not execution guarantees.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_KIND = "crypto_execution_plan_v1"
SCHEMA_VERSION = 1

STYLE_PARALLEL = "parallel_protected_limit"
STYLE_WORST_FIRST = "fill_worst_leg_first"
STYLE_LEAST_LIQUID_FIRST = "least_liquid_first"  # trigger alias for fill_worst_leg_first
STYLE_MANUAL = "manual"
VALID_STYLES = (STYLE_PARALLEL, STYLE_WORST_FIRST, STYLE_LEAST_LIQUID_FIRST, STYLE_MANUAL)
_STYLE_ALIASES = {STYLE_LEAST_LIQUID_FIRST: STYLE_WORST_FIRST}

CDNA_CANDIDATE_ACTION = "FILL_CDNA_FIRST_THEN_HEDGE_EXACT_FILLED_QUANTITY"

# Conservative latency placeholders (ms). Not measured; for budgeting only.
_SUBMIT_LATENCY_MS = {"kalshi": 300, "polymarket": 450, "cdna": 0}
_FILL_LATENCY_MS = {"kalshi": 600, "polymarket": 900, "cdna": 0}
_DEFAULT_SUBMIT_LATENCY_MS = 400
_DEFAULT_FILL_LATENCY_MS = 800

_FLOAT_TOL = 1e-9

_INVALID_VERDICTS = {"INVALID_DUPLICATE", "INVALID_RECOMPUTE_FAIL"}


# ---------------------------------------------------------------------------- #
# Public entry points                                                          #
# ---------------------------------------------------------------------------- #


def write_execution_plan_files(
    *, candidate_report: Path, json_output: Path, markdown_output: Path, **kwargs: Any
) -> dict[str, Any]:
    report = build_execution_plan_report(candidate_report=candidate_report, **kwargs)
    Path(json_output).parent.mkdir(parents=True, exist_ok=True)
    Path(markdown_output).parent.mkdir(parents=True, exist_ok=True)
    Path(json_output).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    Path(markdown_output).write_text(render_execution_plan_markdown(report), encoding="utf-8")
    return report


def build_execution_plan_report(
    *,
    candidate_report: Path,
    candidate_id: str | None = None,
    max_total_notional: float = 10.0,
    max_leg_notional: float = 5.0,
    max_slippage_cents: float = 1.0,
    max_quote_age_ms: float = 750.0,
    execution_style: str = STYLE_MANUAL,
    min_net_edge: float | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    requested_style = str(execution_style).strip().lower()
    if requested_style not in VALID_STYLES:
        raise ValueError(f"execution_style must be one of {VALID_STYLES}, got {execution_style!r}")
    requested_style = _STYLE_ALIASES.get(requested_style, requested_style)

    params = {
        "max_total_notional": float(max_total_notional),
        "max_leg_notional": float(max_leg_notional),
        "max_slippage_cents": float(max_slippage_cents),
        "max_slippage_price": round(float(max_slippage_cents) / 100.0, 8),
        "max_quote_age_ms": float(max_quote_age_ms),
        "min_net_edge": (None if min_net_edge is None else float(min_net_edge)),
        "requested_execution_style": requested_style,
        "generated_at": generated.isoformat(),
    }

    candidate_report = Path(candidate_report)
    candidates, source_kind, load_error = _load_candidates(candidate_report)
    if candidate_id:
        candidates = [c for c in candidates if _matches_id(c, candidate_id)]

    plans = [_plan_for_candidate(c, params, generated) for c in candidates]
    executable = [p for p in plans if p["executable_intent"]]
    return {
        "schema_kind": SCHEMA_KIND,
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated.isoformat(),
        "candidate_report": str(candidate_report),
        "candidate_report_exists": candidate_report.exists(),
        "candidate_source_kind": source_kind,
        "load_error": load_error,
        "candidate_id_filter": candidate_id,
        "parameters": params,
        "candidate_plans_total": len(plans),
        "executable_intent_plans": len(executable),
        "do_not_trade_plans": len(plans) - len(executable),
        "plans": plans,
        "venue_microstructure_notes": _venue_notes(),
        "safety": {
            "diagnostic_only": True,
            "produces_order_intents_only": True,
            "live_order_placement": False,
            "order_submit_or_cancel": False,
            "network_access": False,
            "uses_api_keys_or_env": False,
            "auth_or_session_logic_added": False,
            "browser_automation_added": False,
            "uses_midpoint": False,
        },
    }


def build_single_execution_plan(
    candidate: dict[str, Any],
    *,
    max_total_notional: float = 10.0,
    max_leg_notional: float = 5.0,
    max_slippage_cents: float = 1.0,
    max_quote_age_ms: float = 750.0,
    execution_style: str = STYLE_MANUAL,
    min_net_edge: float | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build ONE execution plan from an in-memory (already quote-refreshed)
    candidate. Used by the live trigger so planning logic is never duplicated."""
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    style = str(execution_style).strip().lower()
    if style not in VALID_STYLES:
        raise ValueError(f"execution_style must be one of {VALID_STYLES}, got {execution_style!r}")
    style = _STYLE_ALIASES.get(style, style)
    params = {
        "max_total_notional": float(max_total_notional),
        "max_leg_notional": float(max_leg_notional),
        "max_slippage_cents": float(max_slippage_cents),
        "max_slippage_price": round(float(max_slippage_cents) / 100.0, 8),
        "max_quote_age_ms": float(max_quote_age_ms),
        "min_net_edge": (None if min_net_edge is None else float(min_net_edge)),
        "requested_execution_style": style,
        "generated_at": generated.isoformat(),
    }
    return _plan_for_candidate(candidate, params, generated)


# ---------------------------------------------------------------------------- #
# Candidate loading                                                            #
# ---------------------------------------------------------------------------- #


def _load_candidates(path: Path) -> tuple[list[dict[str, Any]], str, str | None]:
    if not path.exists():
        return [], "missing", "candidate_report_not_found"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:  # noqa: BLE001
        return [], "unreadable", f"could_not_parse:{type(exc).__name__}"
    if not isinstance(payload, dict):
        return [], "unrecognized", "top_level_not_object"
    # Preferred: audit-pack canonical candidates.
    if isinstance(payload.get("candidates"), list):
        return [c for c in payload["candidates"] if isinstance(c, dict)], "audit_pack_candidates", None
    # Fallback: a raw scout report (rows) or watch summary (paper_candidates).
    if isinstance(payload.get("rows"), list):
        return [r for r in payload["rows"] if isinstance(r, dict) and r.get("paper_candidate")], "scout_rows", None
    if isinstance(payload.get("paper_candidates"), list):
        return [r for r in payload["paper_candidates"] if isinstance(r, dict)], "watch_summary_paper_candidates", None
    return [], "unrecognized", "no_candidates_rows_or_paper_candidates_field"


def _candidate_id(c: dict[str, Any]) -> str:
    if c.get("dedup_key"):
        return str(c["dedup_key"])
    legs = c.get("basket_legs") or []
    leg_sig = "|".join(sorted(f"{l.get('platform')}:{l.get('side')}:{l.get('market_id_or_ticker')}" for l in legs)) or "no_legs"
    return f"{c.get('iteration_timestamp')}::{c.get('candidate_type')}::{c.get('asset')}::{c.get('target_instant_utc')}::{leg_sig}"


def _matches_id(c: dict[str, Any], wanted: str) -> bool:
    cid = _candidate_id(c)
    return wanted == cid or wanted in cid or wanted == str(c.get("iteration_timestamp"))


# ---------------------------------------------------------------------------- #
# Per-candidate plan                                                           #
# ---------------------------------------------------------------------------- #


def _plan_for_candidate(c: dict[str, Any], params: dict[str, Any], generated: datetime) -> dict[str, Any]:
    raw_legs = c.get("basket_legs") or []
    has_cdna = any(str(l.get("platform") or "").lower() == "cdna" for l in raw_legs)
    requested = params["requested_execution_style"]
    if has_cdna and requested != STYLE_MANUAL:
        effective_style = STYLE_WORST_FIRST
        style_override_reason = "cdna_leg_requires_fill_first"
    else:
        effective_style = requested
        style_override_reason = None
    candidate_action = CDNA_CANDIDATE_ACTION if has_cdna else (
        "PLACE_PROTECTED_LIMIT_LEGS_NEAR_SIMULTANEOUS" if effective_style == STYLE_PARALLEL
        else "FILL_WORST_LEG_FIRST_THEN_HEDGE" if effective_style == STYLE_WORST_FIRST
        else "MANUAL_PLACE_LEGS_IN_RECOMMENDED_ORDER"
    )

    min_payoff = _opt_f(c.get("min_payoff"))
    timing = _timing_budget(c, raw_legs, params, generated, effective_style)

    # Size the basket: one contract = $1 payoff; cost per unit = sum of leg all-in caps.
    leg_plans = [_leg_plan(leg, params) for leg in raw_legs]
    total_all_in_per_unit = round(sum(lp["all_in_max_cost"] for lp in leg_plans), 8) if leg_plans else None
    qty_caps = []
    for lp in leg_plans:
        per_leg = math.floor(params["max_leg_notional"] / lp["all_in_max_cost"]) if lp["all_in_max_cost"] and lp["all_in_max_cost"] > 0 else 0
        by_size = math.floor(lp["available_size_or_cap"]) if lp["available_size_or_cap"] is not None else per_leg
        qty_caps.append(min(per_leg, by_size))
    by_total = math.floor(params["max_total_notional"] / total_all_in_per_unit) if total_all_in_per_unit and total_all_in_per_unit > 0 else 0
    basket_qty = min([by_total] + qty_caps) if qty_caps else 0
    basket_qty = max(0, int(basket_qty))
    for lp in leg_plans:
        lp["quantity_cap"] = basket_qty

    # Worst-case edge if every leg fills at its slippage cap.
    net_edge_after_fees = _opt_f(c.get("net_edge_after_fees"))
    net_edge_at_max_limits = (
        round(min_payoff - total_all_in_per_unit, 8)
        if (min_payoff is not None and total_all_in_per_unit is not None) else None
    )

    order = _order_recommendation(leg_plans, effective_style, has_cdna)
    partial = _partial_fill_scenarios(leg_plans, basket_qty, min_payoff)

    do_not_trade: list[str] = []
    warnings: list[str] = []

    verdict = str(c.get("verdict") or "")
    if verdict in _INVALID_VERDICTS:
        do_not_trade.append(f"candidate_verdict_{verdict}")
    elif verdict == "NEEDS_BOUNDARY_REVIEW":
        warnings.append("candidate_needs_boundary_review_confirm_inclusivity_before_trading")
    if c.get("hard_blockers"):
        do_not_trade.append("candidate_has_hard_blockers")
    if c.get("requires_short_or_sell") or (c.get("candidate_execution_type") not in (None, "BUY_ONLY") and c.get("candidate_execution_type")):
        do_not_trade.append("not_buy_only")
    if timing["stale_leg_count"] > 0:
        do_not_trade.append("stale_quote_at_detection")
    if timing["opportunity_stale"]:
        do_not_trade.append("opportunity_stale_refresh_quotes_before_trading")
    if net_edge_at_max_limits is not None and net_edge_at_max_limits <= 0:
        do_not_trade.append("edge_non_positive_after_max_slippage")
    # Edge-preserving / min-edge gate: even at the worst-case slippage cap the
    # basket must clear the operator's minimum net edge.
    min_edge = params.get("min_net_edge")
    if min_edge is not None:
        if net_edge_after_fees is not None and net_edge_after_fees < min_edge:
            do_not_trade.append("net_edge_below_min")
        if net_edge_at_max_limits is not None and net_edge_at_max_limits < min_edge:
            do_not_trade.append("edge_below_min_after_max_slippage")
        adj = _opt_f(c.get("adjusted_net_edge_after_fees"))
        if adj is not None and adj < min_edge:
            do_not_trade.append("adjusted_net_edge_below_min")
    if basket_qty <= 0:
        do_not_trade.append("quantity_cap_zero_within_notional_limits")
    if not leg_plans:
        do_not_trade.append("no_legs_to_execute")

    if any(lp["quote_timestamp"] is None for lp in leg_plans):
        warnings.append("leg_quote_timestamp_unavailable_refresh_required")
    if has_cdna:
        warnings.append("cdna_display_price_fill_first_no_orderbook_depth")
    if effective_style == STYLE_MANUAL and timing["estimated_total_latency_ms"] is not None:
        warnings.append("manual_execution_cannot_meet_ms_freshness_window_use_tiny_size")
    if timing["latency_exceeds_freshness_window"]:
        warnings.append("estimated_latency_exceeds_quote_freshness_window")
    if partial["any_residual_risk"]:
        warnings.append("partial_fill_residual_exposure_possible")

    return {
        "candidate_id": _candidate_id(c),
        "asset": c.get("asset"),
        "candidate_type": c.get("candidate_type"),
        "paper_candidate_class": c.get("paper_candidate_class"),
        "candidate_verdict": verdict or None,
        "target_instant_utc": c.get("target_instant_utc"),
        "iteration_timestamp": c.get("iteration_timestamp"),
        "requested_execution_style": requested,
        "effective_execution_style": effective_style,
        "execution_style_override_reason": style_override_reason,
        "candidate_action": candidate_action,
        "has_cdna_leg": has_cdna,
        "basket_quantity_cap": basket_qty,
        "expected_min_payoff": min_payoff,
        "expected_total_cost_after_fees": c.get("total_cost_after_fees"),
        "expected_net_edge_after_fees": net_edge_after_fees,
        "expected_adjusted_net_edge_after_fees": _opt_f(c.get("adjusted_net_edge_after_fees")),
        "total_all_in_cost_per_unit_at_caps": total_all_in_per_unit,
        "net_edge_after_fees_at_max_limits": net_edge_at_max_limits,
        "min_net_edge_gate": params.get("min_net_edge"),
        "legs": leg_plans,
        "leg_order_recommendation": order,
        "timing_budget": timing,
        "partial_fill_plan": partial,
        "cancel_plan": _cancel_plan(effective_style),
        "hedge_plan": _hedge_plan(effective_style, has_cdna),
        "residual_exposure_plan": partial["residual_exposure_plan"],
        "risk_warnings": sorted(set(warnings)),
        "do_not_trade_reasons": sorted(set(do_not_trade)),
        "executable_intent": not do_not_trade,
    }


def _leg_plan(leg: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    platform = str(leg.get("platform") or "").lower()
    is_cdna = platform == "cdna"
    ask = _opt_f(leg.get("ask"))
    fee = _opt_f(leg.get("fee")) or 0.0
    slip = params["max_slippage_price"]
    token_ids = leg.get("token_ids") if isinstance(leg.get("token_ids"), dict) else {}
    token_id = leg.get("token_id_yes") or leg.get("token_id_no") or token_ids.get("yes") or token_ids.get("no")

    if ask is None:
        max_limit_price = None
        all_in_max = None
    elif is_cdna:
        # CDNA: display-price/fill-first, no price improvement, no slippage budget.
        max_limit_price = round(ask, 8)
        all_in_max = round(ask + fee, 8)
    else:
        max_limit_price = round(min(ask + slip, 1.0), 8)
        all_in_max = round(max_limit_price + fee, 8)

    return {
        "platform": leg.get("platform"),
        "side": leg.get("side"),
        "market_id_or_ticker": leg.get("market_id_or_ticker"),
        "condition_id": leg.get("condition_id"),
        "token_id": token_id,
        "contract_id": leg.get("contract_id"),
        "quoted_ask": ask,
        "max_slippage_cents": params["max_slippage_cents"],
        "max_limit_price": max_limit_price,
        "expected_fee": round(fee, 8),
        "all_in_max_cost": all_in_max,
        "available_size_or_cap": _opt_f(leg.get("available_size_or_cap")),
        "quantity_cap": 0,  # filled in by caller after basket sizing
        "quote_timestamp": leg.get("quote_timestamp"),
        "depth_status": leg.get("depth_status"),
        "complement_used": bool(leg.get("complement_used")),
        "complement_source": leg.get("complement_source"),
        "order_type": "DISPLAY_PRICE_FILL_FIRST" if is_cdna else "PROTECTED_LIMIT_BUY",
        "price_chasing": "never_auto_chase_beyond_max_limit_price",
        "per_leg_urgency": _leg_urgency(leg, is_cdna),
    }


def _leg_urgency(leg: dict[str, Any], is_cdna: bool) -> str:
    if is_cdna:
        return "HIGHEST_FILL_FIRST"
    size = _opt_f(leg.get("available_size_or_cap"))
    if leg.get("complement_used"):
        return "HIGH_COMPLEMENT_DERIVED_LIMITED_DEPTH"
    if size is not None and size < 10:
        return "HIGH_THIN_DEPTH"
    if str(leg.get("platform") or "").lower() == "kalshi":
        return "MEDIUM_SHORT_DATED_BOOK"
    return "LOW"


def _order_recommendation(leg_plans: list[dict[str, Any]], style: str, has_cdna: bool) -> dict[str, Any]:
    rank = {"HIGHEST_FILL_FIRST": 0, "HIGH_COMPLEMENT_DERIVED_LIMITED_DEPTH": 1, "HIGH_THIN_DEPTH": 1, "MEDIUM_SHORT_DATED_BOOK": 2, "LOW": 3}
    worst_first = sorted(leg_plans, key=lambda lp: rank.get(lp["per_leg_urgency"], 9))
    if style == STYLE_PARALLEL:
        sequence = [lp["market_id_or_ticker"] for lp in leg_plans]
        note = "Submit all legs near-simultaneously as protected limit orders; cancel any unfilled leg at TTL and evaluate residual."
        return {"mode": "parallel", "sequence": sequence, "note": note}
    sequence = [lp["market_id_or_ticker"] for lp in worst_first]
    note = (
        "Fill the worst/least-liquid (CDNA first) leg, confirm fill quantity, then hedge the EXACT filled quantity on the remaining leg(s)."
        if has_cdna or style == STYLE_WORST_FIRST else
        "Place legs manually in this order; do not place the hedge leg until the first leg's fill quantity is known."
    )
    return {"mode": ("worst_leg_first" if style != STYLE_MANUAL else "manual_worst_leg_first"), "sequence": sequence, "note": note}


def _timing_budget(c: dict[str, Any], legs: list[dict[str, Any]], params: dict[str, Any], generated: datetime, style: str) -> dict[str, Any]:
    detection = _parse_ts(c.get("iteration_timestamp")) or _parse_ts(c.get("target_instant_utc"))
    max_age = params["max_quote_age_ms"]
    per_leg = []
    stale = 0
    for leg in legs:
        qts = _parse_ts(leg.get("quote_timestamp"))
        if qts is not None and detection is not None:
            age = (detection - qts).total_seconds() * 1000.0
        else:
            age = None
        is_stale = age is not None and age > max_age
        if is_stale:
            stale += 1
        per_leg.append({
            "market_id_or_ticker": leg.get("market_id_or_ticker"),
            "quote_timestamp": leg.get("quote_timestamp"),
            "quote_age_at_detection_ms": None if age is None else round(age, 1),
            "stale": is_stale,
        })
    opp_age = round((generated - detection).total_seconds() * 1000.0, 1) if detection is not None else None

    submit = [_SUBMIT_LATENCY_MS.get(str(l.get("platform") or "").lower(), _DEFAULT_SUBMIT_LATENCY_MS) for l in legs]
    fillc = [_FILL_LATENCY_MS.get(str(l.get("platform") or "").lower(), _DEFAULT_FILL_LATENCY_MS) for l in legs]
    per_leg_total = [s + f for s, f in zip(submit, fillc)]
    if style == STYLE_PARALLEL:
        est_total = max(per_leg_total) if per_leg_total else None
    else:  # worst-leg-first / manual are serial
        est_total = sum(per_leg_total) if per_leg_total else None

    return {
        "detection_timestamp": detection.isoformat() if detection else None,
        "opportunity_age_ms": opp_age,
        "max_allowed_quote_age_ms": max_age,
        "max_allowed_time_to_complete_basket_ms": max_age,
        "estimated_api_submit_latency_ms": submit,
        "estimated_fill_latency_ms": fillc,
        "estimated_total_latency_ms": est_total,
        "latency_model_serial_or_parallel": "parallel" if style == STYLE_PARALLEL else "serial",
        "latency_exceeds_freshness_window": bool(est_total is not None and est_total > max_age),
        "per_leg_quote_age": per_leg,
        "stale_leg_count": stale,
        "opportunity_stale": bool(opp_age is not None and opp_age > max_age),
    }


def _partial_fill_scenarios(leg_plans: list[dict[str, Any]], basket_qty: int, min_payoff: float | None) -> dict[str, Any]:
    scenarios = []
    any_residual = False
    for i, filled in enumerate(leg_plans):
        missing = [lp for j, lp in enumerate(leg_plans) if j != i]
        paid = _opt_f(filled.get("all_in_max_cost"))
        worst_case_loss_per_contract = paid  # a single bought leg can settle to 0 -> lose what you paid
        worst_case_loss_total = None if (paid is None) else round(paid * basket_qty, 6)
        scenarios.append({
            "filled_leg": filled.get("market_id_or_ticker"),
            "filled_side": filled.get("side"),
            "unfilled_legs": [lp.get("market_id_or_ticker") for lp in missing],
            "residual_is_unhedged_directional": True,
            "worst_case_loss_per_contract_if_unhedged": worst_case_loss_per_contract,
            "worst_case_loss_total_if_unhedged": worst_case_loss_total,
            "immediate_hedge_action": "buy_exact_filled_quantity_of_unfilled_legs_at_their_max_limit_price",
            "hedge_quantity_basis": "EXACT_FILLED_QUANTITY_NOT_INTENDED_QUANTITY",
            "residual_risk_if_hedge_unfillable": True,
        })
        any_residual = True
    residual_plan = {
        "rule": "Never leave a leg unhedged to settlement. If the hedge leg will not fill at its max_limit_price, "
                "do NOT chase; flatten the filled leg if exitable, else hold as a flagged manual residual.",
        "hedge_quantity_basis": "EXACT_FILLED_QUANTITY",
        "guaranteed_only_when_all_legs_filled": True,
        "min_payoff_all_legs": min_payoff,
    }
    return {
        "scenarios": scenarios,
        "any_residual_risk": any_residual and len(leg_plans) > 1,
        "residual_exposure_plan": residual_plan,
    }


def _cancel_plan(style: str) -> dict[str, Any]:
    if style == STYLE_PARALLEL:
        ttl = "Cancel any leg unfilled within the freshness TTL (max_allowed_time_to_complete_basket_ms); then evaluate residual."
    elif style == STYLE_WORST_FIRST:
        ttl = "If the first (worst) leg does not fill at its cap, cancel and abort — do not place the hedge leg."
    else:
        ttl = "Manual: if a leg does not fill at its max_limit_price, cancel it yourself and stop; do not chase price."
    return {
        "automated_cancel": False,
        "rule": ttl,
        "never_chase_beyond": "max_limit_price",
        "note": "This task does not submit or cancel orders; this is the intended cancel discipline for a future guarded executor or a human.",
    }


def _hedge_plan(style: str, has_cdna: bool) -> dict[str, Any]:
    return {
        "hedge_quantity_basis": "EXACT_FILLED_QUANTITY",
        "fill_first_leg": "cdna_or_least_liquid" if (has_cdna or style == STYLE_WORST_FIRST) else "n/a_parallel",
        "rule": (
            "Fill the CDNA/least-liquid leg first, read the exact filled quantity, then buy that exact quantity on the "
            "hedge leg at its max_limit_price." if (has_cdna or style == STYLE_WORST_FIRST) else
            "Place legs as near-simultaneous protected limits; if only one side fills, immediately hedge the exact filled "
            "quantity at the other leg's max_limit_price or cancel/flatten."
        ),
        "hedge_price_can_move_after_first_fill": True,
    }


# ---------------------------------------------------------------------------- #
# Markdown                                                                     #
# ---------------------------------------------------------------------------- #


def render_execution_plan_markdown(report: dict[str, Any]) -> str:
    plans = report.get("plans") or []
    p = report.get("parameters") or {}
    lines = [
        "# Crypto Execution-Microstructure Plan (intents only — no live orders)",
        "",
        "Protected execution **intents** for audited buy-only paper candidates. This plan never "
        "places, submits, cancels, or signs an order; it models slippage, freshness, latency, "
        "partial-fill, and residual-exposure risk so a tiny manual micro-test can be done safely.",
        "",
        "## Executive Summary",
        "",
        f"- candidate_report: `{_md(report.get('candidate_report'))}` (exists: `{report.get('candidate_report_exists')}`, "
        f"source: `{report.get('candidate_source_kind')}`)",
        f"- requested execution style: `{_md(p.get('requested_execution_style'))}`",
        f"- caps: max_total_notional=`{p.get('max_total_notional')}`  max_leg_notional=`{p.get('max_leg_notional')}`  "
        f"max_slippage_cents=`{p.get('max_slippage_cents')}`  max_quote_age_ms=`{p.get('max_quote_age_ms')}`",
        f"- candidate plans: `{report.get('candidate_plans_total', 0)}`  "
        f"executable intents: `{report.get('executable_intent_plans', 0)}`  "
        f"do-not-trade: `{report.get('do_not_trade_plans', 0)}`",
    ]
    if report.get("load_error"):
        lines.append(f"- load_error: `{_md(report.get('load_error'))}`")
    if not plans:
        lines += ["", "_No candidate plans (no paper candidates loaded)._"]

    # Candidate execution plans.
    lines += [
        "",
        "## Candidate Execution Plans",
        "",
        "| # | Asset | Type | Verdict | Eff. style | Qty cap | Exp net | Net @caps | Executable | Do-not-trade |",
        "|---:|---|---|---|---|---:|---:|---:|---|---|",
    ]
    for i, pl in enumerate(plans, 1):
        lines.append(
            f"| {i} | {_md(pl.get('asset'))} | {_md(pl.get('candidate_type'))} | {_md(pl.get('candidate_verdict'))} | "
            f"{_md(pl.get('effective_execution_style'))} | {_md(pl.get('basket_quantity_cap'))} | "
            f"{_md(pl.get('expected_net_edge_after_fees'))} | {_md(pl.get('net_edge_after_fees_at_max_limits'))} | "
            f"{'yes' if pl.get('executable_intent') else 'NO'} | {_md(', '.join(pl.get('do_not_trade_reasons') or []) or 'none')} |"
        )

    for i, pl in enumerate(plans, 1):
        lines += [
            "",
            f"### Plan {i}: {_md(pl.get('asset'))} {_md(pl.get('candidate_type'))} @ {_md(pl.get('target_instant_utc'))}",
            "",
            f"- candidate_id: `{_md(pl.get('candidate_id'))}`",
            f"- effective style: `{_md(pl.get('effective_execution_style'))}` "
            f"(requested `{_md(pl.get('requested_execution_style'))}`"
            + (f", override: {_md(pl.get('execution_style_override_reason'))}" if pl.get('execution_style_override_reason') else "")
            + f")  candidate_action: `{_md(pl.get('candidate_action'))}`",
            f"- executable_intent: `{pl.get('executable_intent')}`  "
            f"do_not_trade_reasons: `{_md(', '.join(pl.get('do_not_trade_reasons') or []) or 'none')}`",
            f"- risk_warnings: `{_md(', '.join(pl.get('risk_warnings') or []) or 'none')}`",
            "",
            "#### Recommended Leg Order",
            "",
            f"- mode: `{_md((pl.get('leg_order_recommendation') or {}).get('mode'))}`  "
            f"sequence: `{_md(' -> '.join((pl.get('leg_order_recommendation') or {}).get('sequence') or []))}`",
            f"- {_md((pl.get('leg_order_recommendation') or {}).get('note'))}",
            "",
            "#### Limit Prices / Caps",
            "",
            "| Platform | Side | Market id/ticker | token_id | contract_id | Quoted ask | Max limit | Fee | All-in max | Qty cap | Urgency | Order type |",
            "|---|---|---|---|---|---:|---:|---:|---:|---:|---|---|",
        ]
        for leg in pl.get("legs") or []:
            lines.append(
                f"| {_md(leg.get('platform'))} | {_md(leg.get('side'))} | {_md(leg.get('market_id_or_ticker'))} | "
                f"{_md(leg.get('token_id'))} | {_md(leg.get('contract_id'))} | {_md(leg.get('quoted_ask'))} | "
                f"{_md(leg.get('max_limit_price'))} | {_md(leg.get('expected_fee'))} | {_md(leg.get('all_in_max_cost'))} | "
                f"{_md(leg.get('quantity_cap'))} | {_md(leg.get('per_leg_urgency'))} | {_md(leg.get('order_type'))} |"
            )
        tb = pl.get("timing_budget") or {}
        lines += [
            "",
            "#### Timing Budget",
            "",
            f"- detection_timestamp: `{_md(tb.get('detection_timestamp'))}`  opportunity_age_ms: `{_md(tb.get('opportunity_age_ms'))}`",
            f"- max_allowed_quote_age_ms: `{_md(tb.get('max_allowed_quote_age_ms'))}`  "
            f"max_allowed_time_to_complete_basket_ms: `{_md(tb.get('max_allowed_time_to_complete_basket_ms'))}`",
            f"- estimated_total_latency_ms: `{_md(tb.get('estimated_total_latency_ms'))}` "
            f"({_md(tb.get('latency_model_serial_or_parallel'))})  "
            f"latency_exceeds_freshness_window: `{tb.get('latency_exceeds_freshness_window')}`",
            f"- stale_leg_count: `{tb.get('stale_leg_count')}`  opportunity_stale: `{tb.get('opportunity_stale')}`",
            "",
            "#### Partial-Fill Scenarios",
            "",
            "| Filled leg | Unfilled | Worst-case loss/contract if unhedged | Hedge action |",
            "|---|---|---:|---|",
        ]
        for s in (pl.get("partial_fill_plan") or {}).get("scenarios") or []:
            lines.append(
                f"| {_md(s.get('filled_leg'))} | {_md(', '.join(s.get('unfilled_legs') or []))} | "
                f"{_md(s.get('worst_case_loss_per_contract_if_unhedged'))} | {_md(s.get('immediate_hedge_action'))} |"
            )

    # Residual exposure table (cross-candidate).
    lines += [
        "",
        "## Residual Exposure Table",
        "",
        "| # | Asset | Qty cap | Hedge basis | Guaranteed only if all legs fill | Residual risk |",
        "|---:|---|---:|---|---|---|",
    ]
    for i, pl in enumerate(plans, 1):
        rp = pl.get("residual_exposure_plan") or {}
        lines.append(
            f"| {i} | {_md(pl.get('asset'))} | {_md(pl.get('basket_quantity_cap'))} | {_md(rp.get('hedge_quantity_basis'))} | "
            f"{_md(rp.get('guaranteed_only_when_all_legs_filled'))} | {_md((pl.get('partial_fill_plan') or {}).get('any_residual_risk'))} |"
        )

    lines += [
        "",
        "## What Could Go Wrong",
        "",
        "- **Bid/ask drift before placement** — mitigated by per-leg `max_limit_price` (ask + max_slippage) and a quote-age gate; never chase beyond the cap.",
        "- **Partial fills / one leg fills, hedge moves** — hedge the EXACT filled quantity; if the hedge will not fill at its cap, do not chase (residual flagged).",
        "- **Stale quote snapshot** — `stale_quote_at_detection` / `opportunity_stale` gate the plan to do-not-trade until quotes are refreshed.",
        "- **Thin short-dated Kalshi books** — small `available_size_or_cap` raises per-leg urgency and shrinks the quantity cap.",
        "- **Polymarket CLOB matching latency** — included in the timing budget; if estimated latency exceeds the freshness window the plan is flagged.",
        "- **CDNA display-price/fill-first uncertainty** — CDNA forces fill-worst-leg-first; hedge only the exact CDNA filled quantity; no orderbook depth assumed.",
        "",
        "## Manual Micro-Test Instructions",
        "",
        "Use the smallest size that satisfies the caps (often 1 contract). For each executable plan:",
        "",
    ]
    manual = [pl for pl in plans if pl.get("executable_intent")]
    if not manual:
        lines.append("- No executable-intent plans right now — every candidate is do-not-trade (most likely `opportunity_stale_refresh_quotes_before_trading`). Re-run the watcher to refresh quotes, re-audit, then re-plan.")
    for i, pl in enumerate(manual, 1):
        seq = (pl.get("leg_order_recommendation") or {}).get("sequence") or []
        lines += [
            f"{i}. **{_md(pl.get('asset'))} {_md(pl.get('candidate_type'))}** — place in order: {_md(' -> '.join(seq))}.",
            f"   - For each leg, place a LIMIT BUY at its `max_limit_price` for `{pl.get('basket_quantity_cap')}` contract(s). Do not exceed the limit.",
            "   - After the first (worst/CDNA) leg fills, read the EXACT filled quantity and hedge that exact quantity on the remaining leg(s).",
            "   - If any leg will not fill at its cap, STOP and review — do not chase price. Cancel the resting leg yourself.",
        ]

    lines += [
        "",
        "## Safety Notes",
        "",
        "- produces_order_intents_only: `true`  live_order_placement: `false`  order_submit_or_cancel: `false`",
        "- network_access: `false`  uses_api_keys_or_env: `false`  auth_or_session_logic_added: `false`  browser_automation_added: `false`",
        "- uses_midpoint: `false`. CDNA is display-price/fill-first, saved-evidence only.",
        "- Any future live execution is a separate, explicitly-authorized task with its own guardrails.",
    ]
    return "\n".join(lines) + "\n"


def _venue_notes() -> dict[str, str]:
    return {
        "kalshi": "Binary YES/NO settle to $1; limit orders rest on a CLOB; NO buy is the complement of a YES sell; short-dated hourly books are thin. Use protected limit; expect maker/taker fees.",
        "polymarket": "CLOB supports limit / marketable-limit (FOK/FAK/GTC); price can move between read and match; budget for matching latency.",
        "cdna": "Crypto.com/CDNA (Nadex/CDNA Rule 14.x): display-price/fill-first, no public server-side orderbook depth; fill the CDNA leg first then hedge the exact filled quantity; flat per-contract fee.",
        "disclaimer": "Conservative public-doc placeholders for PLANNING only; not execution guarantees; latency values are not measured.",
    }


# ---------------------------------------------------------------------------- #
# Small helpers                                                                #
# ---------------------------------------------------------------------------- #


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z", text)
    if m:
        y, mo, d, h, mi, s = (int(x) for x in m.groups())
        return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _opt_f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")
