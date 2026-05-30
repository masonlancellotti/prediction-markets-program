"""Forensic journal for manual real-money crypto-arb micro-tests.

Mason manually places any tiny ($10/account or less) orders himself. This module
is a *data collector and journal only* — it records the frozen candidate, the
intended order plan, observed quote snapshots, manually-entered fills, timestamps,
and post-trade markouts, then computes a forensic finalization + verdict so the
exact cause of any outcome is diagnosable.

HARD SCOPE — this module NEVER:
  - places, submits, cancels, or signs an order
  - connects to an account, reads credentials, or uses API keys / .env
  - touches the network or drives a browser
It only reads its own candidate/plan report inputs and writes local journal files.

Storage: ``<output_root>/<test_id>/`` with test_plan.json, event_log.jsonl,
fills.jsonl, quote_snapshots.jsonl, markouts.jsonl, final_report.json/.md.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.live_trade_notifications import send_trade_journal_notification


SCHEMA_KIND = "crypto_micro_test_journal_v1"
SCHEMA_VERSION = 1
DEFAULT_OUTPUT_ROOT = Path("reports/crypto_micro_tests")

VERDICTS = (
    "CLEAN_COMPLETE", "PARTIAL_FILL_RISK", "HEDGE_FAILED", "PRICE_MOVED_BEFORE_FILL",
    "FEES_KILLED_EDGE", "SOURCE_BASIS_LOSS", "MANUAL_DATA_INCOMPLETE", "CANCELED_NO_TRADE",
)
_FILLED_STATES = {"filled", "partial"}
_NOFILL_STATES = {"not_filled", "canceled", "rejected"}


# ---------------------------------------------------------------------------- #
# Command 1: start                                                             #
# ---------------------------------------------------------------------------- #


def start_crypto_micro_test(
    *,
    candidate_audit_pack: Path,
    candidate_id: str,
    execution_plan: Path | None = None,
    max_total_notional: float = 10.0,
    test_label: str | None = None,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    now: datetime | None = None,
) -> dict[str, Any]:
    ts = _now(now)
    candidates, c_err = _load_list(candidate_audit_pack, ("candidates", "rows", "paper_candidates"))
    candidate = _select(candidates, candidate_id)
    warnings: list[str] = []
    if c_err:
        warnings.append(c_err)
    if candidate is None:
        warnings.append("candidate_not_found")
        candidate = {}

    plan = None
    plan_params: dict[str, Any] = {}
    if execution_plan is not None:
        plans, p_err = _load_list(Path(execution_plan), ("plans",))
        if p_err:
            warnings.append(f"execution_plan:{p_err}")
        plan = _select_plan(plans, candidate, candidate_id)
        if plan is None:
            warnings.append("execution_plan_for_candidate_not_found")
        plan_params = (_read_json(Path(execution_plan)) or {}).get("parameters") or {}

    test_id = _test_id(test_label, ts)
    test_dir = _test_dir(output_root, test_id)
    test_dir.mkdir(parents=True, exist_ok=True)

    intended_legs = _freeze_intended_legs(candidate, plan, float(max_total_notional))
    test_plan = {
        "schema_kind": SCHEMA_KIND,
        "schema_version": SCHEMA_VERSION,
        "test_id": test_id,
        "test_label": test_label,
        "created_at_utc": ts.isoformat(),
        "candidate_audit_pack": str(candidate_audit_pack),
        "execution_plan": str(execution_plan) if execution_plan is not None else None,
        "max_total_notional": float(max_total_notional),
        "candidate_id_arg": str(candidate_id),
        "candidate_snapshot": {
            "candidate_id": candidate.get("dedup_key") or candidate.get("candidate_id"),
            "asset": candidate.get("asset"),
            "candidate_type": candidate.get("candidate_type"),
            "paper_candidate_class": candidate.get("paper_candidate_class"),
            "candidate_verdict": candidate.get("verdict"),
            "target_instant_utc": candidate.get("target_instant_utc"),
            "iteration_timestamp": candidate.get("iteration_timestamp"),
            "payoff_vector": list(candidate.get("payoff_vector") or []),
            "min_payoff": candidate.get("min_payoff"),
            "max_payoff": candidate.get("max_payoff"),
            "expected_net_edge_after_fees": candidate.get("net_edge_after_fees"),
            "adjusted_net_edge_after_fees": candidate.get("adjusted_net_edge_after_fees"),
            "expected_total_cost_after_fees": candidate.get("total_cost_after_fees"),
            "assumptions_accepted": list(candidate.get("assumptions_accepted") or []),
            "source_indexes": list(candidate.get("source_indexes") or []),
        },
        "intended_legs": intended_legs,
        "execution_style": (plan or {}).get("effective_execution_style") or "manual",
        "candidate_action": (plan or {}).get("candidate_action"),
        "risk_limits": {
            "max_total_notional": float(max_total_notional),
            "max_leg_notional": plan_params.get("max_leg_notional"),
            "max_slippage_cents": plan_params.get("max_slippage_cents"),
            "max_quote_age_ms": plan_params.get("max_quote_age_ms"),
            "never_chase_beyond": "intended_limit_price",
            "hedge_quantity_basis": "EXACT_FILLED_QUANTITY",
        },
        "manual_instructions": _manual_instructions(intended_legs, plan),
        "do_not_trade_reasons_from_plan": list((plan or {}).get("do_not_trade_reasons") or []),
        "risk_warnings_from_plan": list((plan or {}).get("risk_warnings") or []),
    }
    _write_json(test_dir / "test_plan.json", test_plan)
    # Touch the journal files so the layout is complete from the start.
    for fname in ("event_log.jsonl", "fills.jsonl", "quote_snapshots.jsonl", "markouts.jsonl"):
        (test_dir / fname).touch()

    # Initial scanner quote snapshot, one record per leg.
    for leg in intended_legs:
        _append_jsonl(test_dir / "quote_snapshots.jsonl", _quote_record(leg, source="scanner", appended_at=ts))

    _log_event(test_dir, "start_crypto_micro_test", "start-crypto-micro-test",
               inputs={"candidate_id": str(candidate_id), "test_label": test_label,
                       "candidate_audit_pack": str(candidate_audit_pack),
                       "execution_plan": str(execution_plan) if execution_plan else None},
               derived={"test_id": test_id, "intended_legs": len(intended_legs)},
               warnings=warnings, now=ts)
    return {
        "status": "OK",
        "test_id": test_id,
        "test_dir": str(test_dir),
        "intended_legs": len(intended_legs),
        "warnings": warnings,
        "test_plan_path": str(test_dir / "test_plan.json"),
    }


def start_micro_test_from_objects(
    *, candidate: dict[str, Any], plan: dict[str, Any] | None = None,
    max_total_notional: float = 10.0, test_label: str | None = None,
    output_root: Path = DEFAULT_OUTPUT_ROOT, now: datetime | None = None,
    test_id: str | None = None, extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Start a micro-test journal from in-memory candidate + plan objects (used by
    the live trigger so no audit-pack/plan files are required)."""
    ts = _now(now)
    tid = test_id or _test_id(test_label, ts)
    test_dir = _test_dir(output_root, tid)
    test_dir.mkdir(parents=True, exist_ok=True)
    plan = plan or {}
    plan_params = plan.get("parameters") or {}
    intended_legs = _freeze_intended_legs(candidate, plan, float(max_total_notional))
    cs = {
        "candidate_id": candidate.get("dedup_key") or candidate.get("candidate_id"),
        "asset": candidate.get("asset"), "candidate_type": candidate.get("candidate_type"),
        "paper_candidate_class": candidate.get("paper_candidate_class"), "candidate_verdict": candidate.get("verdict"),
        "target_instant_utc": candidate.get("target_instant_utc"), "iteration_timestamp": candidate.get("iteration_timestamp"),
        "payoff_vector": list(candidate.get("payoff_vector") or []), "min_payoff": candidate.get("min_payoff"),
        "max_payoff": candidate.get("max_payoff"), "expected_net_edge_after_fees": candidate.get("net_edge_after_fees"),
        "adjusted_net_edge_after_fees": candidate.get("adjusted_net_edge_after_fees"),
        "expected_total_cost_after_fees": candidate.get("total_cost_after_fees"),
        "assumptions_accepted": list(candidate.get("assumptions_accepted") or []),
        "source_indexes": list(candidate.get("source_indexes") or []),
    }
    test_plan = {
        "schema_kind": SCHEMA_KIND, "schema_version": SCHEMA_VERSION, "test_id": tid, "test_label": test_label,
        "created_at_utc": ts.isoformat(), "started_from": "trigger_objects",
        "max_total_notional": float(max_total_notional), "candidate_snapshot": cs, "intended_legs": intended_legs,
        "execution_style": plan.get("effective_execution_style") or "manual",
        "candidate_action": plan.get("candidate_action"),
        "risk_limits": {
            "max_total_notional": float(max_total_notional), "max_leg_notional": plan_params.get("max_leg_notional"),
            "max_slippage_cents": plan_params.get("max_slippage_cents"), "max_quote_age_ms": plan_params.get("max_quote_age_ms"),
            "never_chase_beyond": "intended_limit_price", "hedge_quantity_basis": "EXACT_FILLED_QUANTITY",
        },
        "manual_instructions": _manual_instructions(intended_legs, plan),
        "do_not_trade_reasons_from_plan": list(plan.get("do_not_trade_reasons") or []),
        "risk_warnings_from_plan": list(plan.get("risk_warnings") or []),
        "trigger_context": extra or {},
    }
    _write_json(test_dir / "test_plan.json", test_plan)
    for fname in ("event_log.jsonl", "fills.jsonl", "quote_snapshots.jsonl", "markouts.jsonl"):
        (test_dir / fname).touch()
    for leg in intended_legs:
        _append_jsonl(test_dir / "quote_snapshots.jsonl", _quote_record(leg, source="scanner", appended_at=ts))
    _log_event(test_dir, "start_micro_test_from_objects", "trigger-crypto-structural-arb",
               inputs={"test_label": test_label, "candidate_id": cs.get("candidate_id")},
               derived={"test_id": tid, "intended_legs": len(intended_legs)}, warnings=[], now=ts)
    return {"status": "OK", "test_id": tid, "test_dir": str(test_dir), "intended_legs": len(intended_legs),
            "test_plan_path": str(test_dir / "test_plan.json")}


def record_micro_test_event(
    *, test_id: str, event_type: str, inputs: dict[str, Any] | None = None,
    derived: dict[str, Any] | None = None, warnings: list[str] | None = None,
    command: str = "trigger-crypto-structural-arb", output_root: Path = DEFAULT_OUTPUT_ROOT,
    now: datetime | None = None,
) -> None:
    """Append an order/dry-run/live event to a micro-test's event log."""
    _log_event(_test_dir(output_root, test_id), event_type, command,
               inputs=inputs or {}, derived=derived or {}, warnings=warnings or [], now=_now(now))


def _freeze_intended_legs(candidate: dict[str, Any], plan: dict[str, Any] | None, max_total_notional: float) -> list[dict[str, Any]]:
    cand_legs = candidate.get("basket_legs") or []
    plan_legs = {(_leg_key(l.get("platform"), l.get("market_id_or_ticker"), l.get("side"))): l for l in (plan or {}).get("legs") or []}
    plan_basket_qty = (plan or {}).get("basket_quantity_cap")
    out: list[dict[str, Any]] = []
    # cost per unit (intended) for fallback sizing if no plan.
    for leg in cand_legs:
        key = _leg_key(leg.get("platform"), leg.get("market_id_or_ticker"), leg.get("side"))
        pl = plan_legs.get(key) or {}
        token_ids = leg.get("token_ids") if isinstance(leg.get("token_ids"), dict) else {}
        scanner_ask = _opt_f(leg.get("ask"))
        fee = _opt_f(pl.get("expected_fee"))
        if fee is None:
            fee = _opt_f(leg.get("fee")) or 0.0
        intended_limit = _opt_f(pl.get("max_limit_price"))
        if intended_limit is None:
            intended_limit = scanner_ask
        all_in_max = _opt_f(pl.get("all_in_max_cost"))
        if all_in_max is None and intended_limit is not None:
            all_in_max = round(intended_limit + fee, 8)
        intended_qty = pl.get("quantity_cap")
        if intended_qty in (None, 0):
            intended_qty = plan_basket_qty
        if intended_qty in (None, 0) and all_in_max:
            import math
            intended_qty = math.floor(max_total_notional / all_in_max) if all_in_max > 0 else 0
        out.append({
            "platform": leg.get("platform"),
            "market_id_or_ticker": leg.get("market_id_or_ticker"),
            "side": leg.get("side"),
            "token_id": leg.get("token_id_yes") or leg.get("token_id_no") or token_ids.get("yes") or token_ids.get("no"),
            "condition_id": leg.get("condition_id"),
            "contract_id": leg.get("contract_id"),
            "scanner_quote_ask": scanner_ask,
            "scanner_quote_bid": _opt_f(leg.get("bid")),
            "scanner_quote_timestamp": leg.get("quote_timestamp"),
            "depth_status": leg.get("depth_status"),
            "source_index": leg.get("source_index"),
            "intended_limit_price": intended_limit,
            "intended_quantity": int(intended_qty) if intended_qty not in (None, "") else None,
            "expected_fee": round(fee, 8) if fee is not None else None,
            "all_in_max_cost": all_in_max,
            "per_leg_urgency": pl.get("per_leg_urgency"),
        })
    return out


def _manual_instructions(legs: list[dict[str, Any]], plan: dict[str, Any] | None) -> list[str]:
    order = ((plan or {}).get("leg_order_recommendation") or {}).get("sequence") or [l.get("market_id_or_ticker") for l in legs]
    steps = [
        "Forensic journal only — you place every order manually; this tool records, it does not trade.",
        f"Recommended leg order: {' -> '.join(str(x) for x in order)}.",
        "For each leg: place a LIMIT BUY at intended_limit_price for intended_quantity; never exceed the limit.",
        "After the first (worst/CDNA) leg fills, read the EXACT filled quantity and hedge that exact quantity.",
        "If any leg will not fill at its limit, STOP — cancel it yourself, do not chase, and record order_status accordingly.",
        "Record each leg with `record-crypto-micro-fill` (observed prices/quantities/timestamps), then `finalize-crypto-micro-test`.",
    ]
    return steps


# ---------------------------------------------------------------------------- #
# Command 2: record fill                                                       #
# ---------------------------------------------------------------------------- #


def record_crypto_micro_fill(
    *,
    test_id: str,
    platform: str,
    market_id_or_ticker: str,
    side: str,
    intended_limit_price: float | None = None,
    filled_price: float | None = None,
    filled_quantity: float | None = None,
    fees: float | None = None,
    order_start_time_utc: str | None = None,
    order_submit_time_utc: str | None = None,
    first_fill_time_utc: str | None = None,
    final_fill_time_utc: str | None = None,
    order_status: str = "filled",
    notes: str | None = None,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    now: datetime | None = None,
    notify_provider: str = "dry_run",
    notify_send: bool = False,
    notification_http_post: Any = None,
) -> dict[str, Any]:
    ts = _now(now)
    test_dir = _test_dir(output_root, test_id)
    plan = _read_json(test_dir / "test_plan.json") or {}
    warnings: list[str] = []
    leg = _find_intended_leg(plan, platform, market_id_or_ticker, side)
    if leg is None:
        warnings.append("intended_leg_not_found_in_test_plan")
        leg = {}

    scanner_ask = _opt_f(leg.get("scanner_quote_ask"))
    intended_limit = _opt_f(intended_limit_price)
    if intended_limit is None:
        intended_limit = _opt_f(leg.get("intended_limit_price"))
    intended_qty = leg.get("intended_quantity")
    expected_fee = _opt_f(leg.get("expected_fee"))
    fp = _opt_f(filled_price)
    fq = _opt_f(filled_quantity)
    fee_actual = _opt_f(fees)

    t_start = _parse_ts(order_start_time_utc)
    t_submit = _parse_ts(order_submit_time_utc)
    t_first = _parse_ts(first_fill_time_utc)
    t_final = _parse_ts(final_fill_time_utc)

    derived = {
        "time_to_submit_ms": _delta_ms(t_start, t_submit),
        "time_to_first_fill_ms": _delta_ms(t_submit, t_first),
        "time_to_final_fill_ms": _delta_ms(t_submit, t_final),
        "slippage_vs_scanner_quote": _round(_sub(fp, scanner_ask)),
        "slippage_vs_limit": _round(_sub(fp, intended_limit)),
        "fee_actual_vs_expected": _round(_sub(fee_actual, expected_fee)),
        "quantity_filled_vs_intended": _round(_sub(fq, _opt_f(intended_qty))),
        "residual_unhedged_quantity": _round(max(0.0, (_opt_f(intended_qty) or 0.0) - (fq or 0.0))) if intended_qty is not None else None,
        "fill_ratio": _round((fq / _opt_f(intended_qty)) if (fq is not None and _opt_f(intended_qty)) else None),
    }
    if order_status in _FILLED_STATES and (fp is None or fq is None):
        warnings.append("filled_status_but_price_or_quantity_missing")
    if order_status not in (_FILLED_STATES | _NOFILL_STATES):
        warnings.append(f"unrecognized_order_status:{order_status}")

    record = {
        "recorded_at_utc": ts.isoformat(),
        "platform": platform, "market_id_or_ticker": market_id_or_ticker, "side": side,
        "leg_key": _leg_key(platform, market_id_or_ticker, side),
        "token_id": leg.get("token_id"), "condition_id": leg.get("condition_id"), "contract_id": leg.get("contract_id"),
        "scanner_quote_ask": scanner_ask, "scanner_quote_timestamp": leg.get("scanner_quote_timestamp"),
        "intended_limit_price": intended_limit, "intended_quantity": intended_qty, "expected_fee": expected_fee,
        "filled_price": fp, "filled_quantity": fq, "fees": fee_actual, "order_status": order_status,
        "order_start_time_utc": order_start_time_utc, "order_submit_time_utc": order_submit_time_utc,
        "first_fill_time_utc": first_fill_time_utc, "final_fill_time_utc": final_fill_time_utc,
        "notes": notes, "derived": derived,
    }
    _append_jsonl(test_dir / "fills.jsonl", record)
    _log_event(test_dir, "record_crypto_micro_fill", "record-crypto-micro-fill",
               inputs={"platform": platform, "market_id_or_ticker": market_id_or_ticker, "side": side,
                       "order_status": order_status, "filled_price": fp, "filled_quantity": fq, "fees": fee_actual},
               derived=derived, warnings=warnings, now=ts)
    notification = None
    if str(order_status).lower() in {"filled", "partial", "canceled", "rejected", "not_filled"}:
        notification = send_trade_journal_notification(
            event_type=str(order_status).lower(),
            payload={"platform": platform, "side": side, "quantity": fq, "price": fp, "pnl": None},
            provider_name=notify_provider, send=notify_send, http_post=notification_http_post, clock=lambda: ts,
        )
        _log_event(test_dir, notification.get("event_log_event_type") or "notification_skipped",
                   "record-crypto-micro-fill", inputs={"notification": notification},
                   derived={}, warnings=[], now=ts)
    return {"status": "OK", "test_id": test_id, "leg_key": record["leg_key"], "derived": derived,
            "warnings": warnings, "notification": notification}


# ---------------------------------------------------------------------------- #
# Command: append quote snapshot                                               #
# ---------------------------------------------------------------------------- #


def append_crypto_micro_quote_snapshot(
    *, test_id: str, source: str = "manual", json_file: Path | None = None,
    output_root: Path = DEFAULT_OUTPUT_ROOT, now: datetime | None = None,
) -> dict[str, Any]:
    ts = _now(now)
    test_dir = _test_dir(output_root, test_id)
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    if json_file is not None:
        payload = _read_json(Path(json_file))
        rows = _coerce_quote_rows(payload)
        if not rows:
            warnings.append("no_quote_rows_parsed_from_json_file")
    else:
        warnings.append("no_json_file_supplied")
    appended = 0
    for raw in rows:
        _append_jsonl(test_dir / "quote_snapshots.jsonl", _quote_record(raw, source=source, appended_at=ts))
        appended += 1
    _log_event(test_dir, "append_crypto_micro_quote_snapshot", "append-crypto-micro-quote-snapshot",
               inputs={"source": source, "json_file": str(json_file) if json_file else None},
               derived={"appended": appended}, warnings=warnings, now=ts)
    return {"status": "OK", "test_id": test_id, "appended": appended, "warnings": warnings}


# ---------------------------------------------------------------------------- #
# Command 3: finalize                                                          #
# ---------------------------------------------------------------------------- #


def finalize_crypto_micro_test(
    *, test_id: str, settlement_status: str | None = None, manual_notes: str | None = None,
    output_root: Path = DEFAULT_OUTPUT_ROOT, now: datetime | None = None,
    notify_provider: str = "dry_run", notify_send: bool = False, notification_http_post: Any = None,
) -> dict[str, Any]:
    ts = _now(now)
    test_dir = _test_dir(output_root, test_id)
    plan = _read_json(test_dir / "test_plan.json") or {}
    fills = _read_jsonl(test_dir / "fills.jsonl")
    legs = plan.get("intended_legs") or []
    min_payoff = _opt_f(plan.get("candidate_snapshot", {}).get("min_payoff"))
    cross_source = len(set(plan.get("candidate_snapshot", {}).get("source_indexes") or [])) > 1
    warnings: list[str] = []

    # Latest fill per leg key.
    fill_by_leg: dict[str, dict[str, Any]] = {}
    for f in fills:
        fill_by_leg[f.get("leg_key")] = f

    leg_results = []
    intended_cost_per_unit = 0.0
    actual_cost_per_unit = 0.0
    all_have_fill = bool(legs)
    filled_legs = 0
    nofill_legs = 0
    incomplete_data = False
    filled_quantities: list[float] = []
    for leg in legs:
        key = _leg_key(leg.get("platform"), leg.get("market_id_or_ticker"), leg.get("side"))
        f = fill_by_leg.get(key)
        intended_all_in = _opt_f(leg.get("all_in_max_cost"))
        if intended_all_in is not None:
            intended_cost_per_unit += intended_all_in
        status = (f or {}).get("order_status")
        fp = _opt_f((f or {}).get("filled_price"))
        fq = _opt_f((f or {}).get("filled_quantity"))
        fee = _opt_f((f or {}).get("fees"))
        per_contract_fee = (fee / fq) if (fee is not None and fq) else None
        leg_actual_per_unit = (fp + (per_contract_fee or 0.0)) if fp is not None else None
        if f is None:
            all_have_fill = False
        elif status in _FILLED_STATES and fq and fp is not None:
            filled_legs += 1
            filled_quantities.append(fq)
            actual_cost_per_unit += leg_actual_per_unit if leg_actual_per_unit is not None else 0.0
        elif status in _NOFILL_STATES or not fq:
            nofill_legs += 1
        if f is not None and status in _FILLED_STATES and (fp is None or fq is None):
            incomplete_data = True
        leg_results.append({
            "leg_key": key, "platform": leg.get("platform"), "side": leg.get("side"),
            "market_id_or_ticker": leg.get("market_id_or_ticker"),
            "intended_limit_price": leg.get("intended_limit_price"), "intended_quantity": leg.get("intended_quantity"),
            "order_status": status, "filled_price": fp, "filled_quantity": fq, "fees": fee,
            "per_contract_fee": _round(per_contract_fee), "leg_actual_cost_per_unit": _round(leg_actual_per_unit),
            "scanner_quote_ask": leg.get("scanner_quote_ask"),
            "quote_drift_vs_scanner": _round(_sub(fp, _opt_f(leg.get("scanner_quote_ask")))),
            "fee_actual_vs_expected": _round(_sub(fee, _opt_f(leg.get("expected_fee")))),
            "has_fill_record": f is not None,
        })

    matched_qty = min(filled_quantities) if (filled_quantities and len(filled_quantities) == len(legs)) else 0
    residuals = []
    for lr in leg_results:
        fq = _opt_f(lr.get("filled_quantity")) or 0.0
        resid = round(fq - matched_qty, 8) if fq > matched_qty else 0.0
        if resid > 0:
            residuals.append({
                "leg_key": lr["leg_key"], "residual_quantity": resid,
                "worst_case_loss_if_settles_zero": _round((lr.get("leg_actual_cost_per_unit") or 0.0) * resid),
            })

    intended_net_edge = _round(_sub(min_payoff, intended_cost_per_unit)) if min_payoff is not None else None
    all_filled_full = (filled_legs == len(legs) and len(legs) > 0 and nofill_legs == 0
                       and len(set(filled_quantities)) == 1 and not incomplete_data)
    actual_net_edge = _round(_sub(min_payoff, actual_cost_per_unit)) if (all_filled_full and min_payoff is not None) else None
    guarantee_holds = bool(all_filled_full and (min_payoff or 0) >= 1.0 and (actual_net_edge or 0) > 0)

    fee_drag = round(sum((_sub(_opt_f(lr.get("fees")), _opt_f((_find_intended_leg(plan, lr["platform"], lr["market_id_or_ticker"], lr["side"]) or {}).get("expected_fee"))) or 0.0) for lr in leg_results), 8)
    slip_drag = round(sum((lr.get("quote_drift_vs_scanner") or 0.0) for lr in leg_results), 8)

    failure_legs = [lr["leg_key"] for lr in leg_results if (lr.get("order_status") in _NOFILL_STATES) or (not lr.get("filled_quantity")) or (_opt_f(lr.get("filled_quantity")) or 0) < (_opt_f(lr.get("intended_quantity")) or 0)]

    timeline = _execution_timeline(fills)
    fill_sequence = _fill_sequence_risk(fills)
    markouts = _markouts(test_dir, leg_results)
    _write_jsonl(test_dir / "markouts.jsonl", markouts)

    verdict, verdict_reason = _verdict(
        legs=legs, fills=fills, all_have_fill=all_have_fill, filled_legs=filled_legs, nofill_legs=nofill_legs,
        all_filled_full=all_filled_full, incomplete_data=incomplete_data, actual_net_edge=actual_net_edge,
        fee_drag=fee_drag, slip_drag=slip_drag, cross_source=cross_source, settlement_status=settlement_status,
        residuals=residuals,
    )

    final = {
        "schema_kind": "crypto_micro_test_final_report_v1",
        "test_id": test_id,
        "finalized_at_utc": ts.isoformat(),
        "settlement_status": settlement_status,
        "manual_notes": manual_notes,
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "legs_total": len(legs),
        "filled_legs": filled_legs,
        "nofill_legs": nofill_legs,
        "matched_basket_quantity": matched_qty,
        "min_payoff": min_payoff,
        "guarantee_holds": guarantee_holds,
        "guarantee_requires_all_legs_filled_equal_qty": True,
        "intended_basket_cost_per_unit": _round(intended_cost_per_unit),
        "actual_basket_cost_per_unit": _round(actual_cost_per_unit) if all_filled_full else None,
        "intended_net_edge_after_fees": intended_net_edge,
        "actual_net_edge_after_fees_if_all_filled": actual_net_edge,
        "fee_drag_actual_vs_expected": fee_drag,
        "slippage_drag_vs_scanner": slip_drag,
        "failure_legs": failure_legs,
        "residual_exposure": residuals,
        "leg_results": leg_results,
        "execution_time_decomposition": timeline,
        "fill_sequence_risk": fill_sequence,
        "candidate_snapshot": plan.get("candidate_snapshot"),
        "execution_style": plan.get("execution_style"),
        "warnings": warnings,
        "safety": _safety(),
    }
    _write_json(test_dir / "final_report.json", final)
    _write_text(test_dir / "final_report.md", render_micro_test_report_markdown(final, plan))
    _log_event(test_dir, "finalize_crypto_micro_test", "finalize-crypto-micro-test",
               inputs={"settlement_status": settlement_status, "manual_notes": manual_notes},
               derived={"verdict": verdict, "guarantee_holds": guarantee_holds,
                        "actual_net_edge_after_fees_if_all_filled": actual_net_edge}, warnings=warnings, now=ts)
    notification = send_trade_journal_notification(
        event_type="finalized",
        payload={"platform": "all", "side": "n/a", "quantity": "n/a", "price": "n/a",
                 "pnl": actual_net_edge},
        provider_name=notify_provider, send=notify_send, http_post=notification_http_post, clock=lambda: ts,
    )
    final["notification"] = notification
    _write_json(test_dir / "final_report.json", final)
    _log_event(test_dir, notification.get("event_log_event_type") or "notification_skipped",
               "finalize-crypto-micro-test", inputs={"notification": notification},
               derived={}, warnings=[], now=ts)
    return final


def _verdict(*, legs, fills, all_have_fill, filled_legs, nofill_legs, all_filled_full, incomplete_data,
            actual_net_edge, fee_drag, slip_drag, cross_source, settlement_status, residuals) -> tuple[str, str]:
    if settlement_status and "basis" in str(settlement_status).lower():
        return "SOURCE_BASIS_LOSS", f"settlement_status={settlement_status}"
    if not legs:
        return "MANUAL_DATA_INCOMPLETE", "no intended legs in test plan"
    if not fills:
        return "MANUAL_DATA_INCOMPLETE", "no fill records entered"
    # All recorded fills are no-fill/cancel and nothing filled -> canceled, no trade.
    if filled_legs == 0 and nofill_legs >= 1 and all_have_fill:
        return "CANCELED_NO_TRADE", "no leg filled; orders canceled/not filled"
    if not all_have_fill:
        return "MANUAL_DATA_INCOMPLETE", "one or more legs has no fill record"
    if incomplete_data:
        return "MANUAL_DATA_INCOMPLETE", "a filled leg is missing price/quantity"
    if filled_legs >= 1 and nofill_legs >= 1:
        return "HEDGE_FAILED", "at least one leg filled while another did not -> unhedged directional residual"
    if residuals:  # all legs filled but quantities differ
        return "PARTIAL_FILL_RISK", "legs filled with mismatched quantities -> unhedged residual"
    if all_filled_full:
        if (actual_net_edge or 0) > 0:
            return "CLEAN_COMPLETE", "all legs filled in balanced quantity with positive post-fee edge"
        if cross_source and (fee_drag <= slip_drag):
            return "SOURCE_BASIS_LOSS", "cross-source basket lost edge; drift dominated fees"
        if fee_drag >= slip_drag and fee_drag > 0:
            return "FEES_KILLED_EDGE", f"fee drag {fee_drag} dominated the lost edge"
        if slip_drag > 0:
            return "PRICE_MOVED_BEFORE_FILL", f"quote drift {slip_drag} eroded the edge before fill"
        return "FEES_KILLED_EDGE", "post-fee edge non-positive after fills"
    return "MANUAL_DATA_INCOMPLETE", "could not classify from recorded data"


# ---------------------------------------------------------------------------- #
# Command 4: report                                                            #
# ---------------------------------------------------------------------------- #


def crypto_micro_test_report(
    *, test_id: str, markdown_output: Path | None = None,
    output_root: Path = DEFAULT_OUTPUT_ROOT, now: datetime | None = None,
) -> dict[str, Any]:
    ts = _now(now)
    test_dir = _test_dir(output_root, test_id)
    plan = _read_json(test_dir / "test_plan.json") or {}
    final = _read_json(test_dir / "final_report.json")
    warnings: list[str] = []
    if final is None:
        final = {"test_id": test_id, "verdict": "NOT_FINALIZED",
                 "verdict_reason": "finalize-crypto-micro-test has not been run",
                 "leg_results": [], "candidate_snapshot": plan.get("candidate_snapshot"),
                 "residual_exposure": [], "execution_time_decomposition": {}, "fill_sequence_risk": {},
                 "warnings": ["not_finalized"], "safety": _safety()}
        warnings.append("not_finalized")
    md = render_micro_test_report_markdown(final, plan)
    out_path = Path(markdown_output) if markdown_output else (test_dir / "final_report.md")
    _write_text(out_path, md)
    _log_event(test_dir, "crypto_micro_test_report", "crypto-micro-test-report",
               inputs={"markdown_output": str(out_path)}, derived={"verdict": final.get("verdict")},
               warnings=warnings, now=ts)
    return {"status": "OK", "test_id": test_id, "verdict": final.get("verdict"), "markdown_path": str(out_path), "warnings": warnings}


# ---------------------------------------------------------------------------- #
# Markdown                                                                      #
# ---------------------------------------------------------------------------- #


def render_micro_test_report_markdown(final: dict[str, Any], plan: dict[str, Any] | None = None) -> str:
    plan = plan or {}
    cs = final.get("candidate_snapshot") or plan.get("candidate_snapshot") or {}
    legs = final.get("leg_results") or []
    lines = [
        "# Crypto Micro-Test Forensic Report",
        "",
        "Manual real-money micro-test journal. This tool never places, submits, or cancels orders; "
        "Mason entered all fills manually. Forensic record + verdict only.",
        "",
        "## 1. Test Summary",
        "",
        f"- test_id: `{_md(final.get('test_id'))}`  verdict: **{_md(final.get('verdict'))}**",
        f"- reason: {_md(final.get('verdict_reason'))}",
        f"- legs: `{final.get('legs_total', len(legs))}`  filled: `{final.get('filled_legs', 0)}`  "
        f"no-fill: `{final.get('nofill_legs', 0)}`  matched basket qty: `{final.get('matched_basket_quantity', 0)}`",
        f"- guarantee_holds: `{final.get('guarantee_holds')}`  settlement_status: `{_md(final.get('settlement_status'))}`",
        f"- manual_notes: {_md(final.get('manual_notes'))}",
        "",
        "## 2. Candidate Snapshot",
        "",
        f"- asset: `{_md(cs.get('asset'))}`  type: `{_md(cs.get('candidate_type'))}`  class: `{_md(cs.get('paper_candidate_class'))}`",
        f"- target_instant_utc: `{_md(cs.get('target_instant_utc'))}`  candidate_verdict: `{_md(cs.get('candidate_verdict'))}`",
        f"- min_payoff: `{_md(cs.get('min_payoff'))}`  expected_net_edge_after_fees: `{_md(cs.get('expected_net_edge_after_fees'))}`",
        f"- assumptions_accepted: `{_md(', '.join(cs.get('assumptions_accepted') or []) or 'none')}`  "
        f"source_indexes: `{_md(', '.join(cs.get('source_indexes') or []) or 'single')}`",
        "",
        "## 3. Intended Execution Plan",
        "",
        "| Platform | Side | Market id/ticker | Intended limit | Intended qty | Expected fee | All-in max |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for leg in plan.get("intended_legs") or []:
        lines.append(
            f"| {_md(leg.get('platform'))} | {_md(leg.get('side'))} | {_md(leg.get('market_id_or_ticker'))} | "
            f"{_md(leg.get('intended_limit_price'))} | {_md(leg.get('intended_quantity'))} | "
            f"{_md(leg.get('expected_fee'))} | {_md(leg.get('all_in_max_cost'))} |"
        )
    if not (plan.get("intended_legs")):
        lines.append("| (no plan) |  |  |  |  |  |  |")

    lines += [
        "",
        "## 4. Actual Fill Table",
        "",
        "| Platform | Side | Market id/ticker | Status | Filled px | Filled qty | Fees | vs scanner | vs limit |",
        "|---|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for lr in legs:
        lines.append(
            f"| {_md(lr.get('platform'))} | {_md(lr.get('side'))} | {_md(lr.get('market_id_or_ticker'))} | "
            f"{_md(lr.get('order_status'))} | {_md(lr.get('filled_price'))} | {_md(lr.get('filled_quantity'))} | "
            f"{_md(lr.get('fees'))} | {_md(lr.get('quote_drift_vs_scanner'))} | "
            f"{_md(_round(_sub(_opt_f(lr.get('filled_price')), _opt_f(lr.get('intended_limit_price')))))} |"
        )
    if not legs:
        lines.append("| (no fills) |  |  |  |  |  |  |  |  |")

    tl = final.get("execution_time_decomposition") or {}
    lines += [
        "",
        "## 5. Timeline",
        "",
        f"- earliest order start: `{_md(tl.get('earliest_order_start'))}`  latest final fill: `{_md(tl.get('latest_final_fill'))}`",
        f"- total basket span ms: `{_md(tl.get('total_basket_span_ms'))}`",
        "",
        "| Leg | submit ms | first-fill ms | final-fill ms |",
        "|---|---:|---:|---:|",
    ]
    for row in tl.get("per_leg") or []:
        lines.append(
            f"| {_md(row.get('leg_key'))} | {_md(row.get('time_to_submit_ms'))} | "
            f"{_md(row.get('time_to_first_fill_ms'))} | {_md(row.get('time_to_final_fill_ms'))} |"
        )

    lines += ["", "## 6. Quote Drift Table", "", "| Leg | Scanner ask | Filled px | Drift |", "|---|---:|---:|---:|"]
    for lr in legs:
        lines.append(
            f"| {_md(lr.get('leg_key'))} | {_md(lr.get('scanner_quote_ask'))} | {_md(lr.get('filled_price'))} | "
            f"{_md(lr.get('quote_drift_vs_scanner'))} |"
        )

    lines += ["", "## 7. Slippage Table", "", "| Leg | vs scanner quote | vs intended limit |", "|---|---:|---:|"]
    for lr in legs:
        lines.append(
            f"| {_md(lr.get('leg_key'))} | {_md(lr.get('quote_drift_vs_scanner'))} | "
            f"{_md(_round(_sub(_opt_f(lr.get('filled_price')), _opt_f(lr.get('intended_limit_price')))))} |"
        )

    lines += ["", "## 8. Fee Comparison", "",
              f"- fee drag (actual - expected): `{_md(final.get('fee_drag_actual_vs_expected'))}`",
              "", "| Leg | Fees actual | vs expected |", "|---|---:|---:|"]
    for lr in legs:
        lines.append(f"| {_md(lr.get('leg_key'))} | {_md(lr.get('fees'))} | {_md(lr.get('fee_actual_vs_expected'))} |")

    lines += ["", "## 9. Residual Exposure", ""]
    resid = final.get("residual_exposure") or []
    if not resid:
        lines.append("- none — no unhedged residual quantity.")
    else:
        lines += ["| Leg | Residual qty | Worst-case loss if settles 0 |", "|---|---:|---:|"]
        for r in resid:
            lines.append(f"| {_md(r.get('leg_key'))} | {_md(r.get('residual_quantity'))} | {_md(r.get('worst_case_loss_if_settles_zero'))} |")

    lines += [
        "",
        "## 10. Did the Guarantee Survive?",
        "",
        f"- guarantee_holds: **{final.get('guarantee_holds')}**",
        f"- intended net edge after fees: `{_md(final.get('intended_net_edge_after_fees'))}`",
        f"- actual net edge after fees (if all legs filled): `{_md(final.get('actual_net_edge_after_fees_if_all_filled'))}`",
        f"- failure legs: `{_md(', '.join(final.get('failure_legs') or []) or 'none')}`",
        "",
        "## 11. Root Cause If Bad",
        "",
    ]
    if final.get("verdict") == "CLEAN_COMPLETE":
        lines.append("- none — clean complete fill with positive post-fee edge.")
    else:
        fsr = final.get("fill_sequence_risk") or {}
        lines += [
            f"- verdict: **{_md(final.get('verdict'))}** — {_md(final.get('verdict_reason'))}",
            f"- fee drag: `{_md(final.get('fee_drag_actual_vs_expected'))}`  slippage drag: `{_md(final.get('slippage_drag_vs_scanner'))}`",
            f"- fill-sequence exposure window ms: `{_md(fsr.get('exposure_window_ms'))}` "
            f"(first leg on at `{_md(fsr.get('first_leg_filled_at'))}`, hedge first-fill `{_md(fsr.get('hedge_first_fill_at'))}`)",
        ]

    lines += [
        "",
        "## 12. Lessons For Next Scanner / Execution Changes",
        "",
    ]
    for lesson in _lessons(final):
        lines.append(f"- {lesson}")

    lines += [
        "",
        "## Safety",
        "",
        "- forensic_journal_only: `true`  live_order_placement: `false`  order_submit_or_cancel: `false`",
        "- account_connection: `false`  reads_credentials: `false`  network_access: `false`  browser_automation_added: `false`",
    ]
    return "\n".join(lines) + "\n"


def _lessons(final: dict[str, Any]) -> list[str]:
    v = final.get("verdict")
    base = {
        "CLEAN_COMPLETE": ["Clean — record the realized edge and consider repeating at the same tiny size."],
        "PARTIAL_FILL_RISK": ["Size each leg to the same integer; hedge the EXACT filled quantity; consider all-or-none where supported."],
        "HEDGE_FAILED": ["Fill the least-liquid/CDNA leg first and only then hedge; never place the hedge before the first fill is known.",
                          "Add a hard rule: if the first leg fills and the hedge cannot fill at its limit, flatten immediately."],
        "PRICE_MOVED_BEFORE_FILL": ["Tighten the freshness window / re-quote immediately before placing; widen slippage cap only if edge survives.",
                                     "Prefer near-simultaneous protected limits over serial manual placement for tight-edge baskets."],
        "FEES_KILLED_EDGE": ["Re-check the fee model per leg; only trade candidates whose edge survives worst-case fees + 1c slippage."],
        "SOURCE_BASIS_LOSS": ["Treat cross-source (BRTI vs Binance) baskets as basis trades; require a larger edge buffer before testing."],
        "MANUAL_DATA_INCOMPLETE": ["Record every leg (including not_filled/canceled) with timestamps so the journal can diagnose outcomes."],
        "CANCELED_NO_TRADE": ["No trade — note why the limits did not fill (book too thin / price away) for scanner depth tuning."],
        "NOT_FINALIZED": ["Run finalize-crypto-micro-test once fills are entered."],
    }
    return base.get(v, ["Review the recorded data and refine the scanner/execution rules accordingly."])


# ---------------------------------------------------------------------------- #
# Derivations                                                                  #
# ---------------------------------------------------------------------------- #


def _execution_timeline(fills: list[dict[str, Any]]) -> dict[str, Any]:
    per_leg = []
    starts, finals = [], []
    for f in fills:
        d = f.get("derived") or {}
        per_leg.append({
            "leg_key": f.get("leg_key"),
            "time_to_submit_ms": d.get("time_to_submit_ms"),
            "time_to_first_fill_ms": d.get("time_to_first_fill_ms"),
            "time_to_final_fill_ms": d.get("time_to_final_fill_ms"),
        })
        st = _parse_ts(f.get("order_start_time_utc"))
        fn = _parse_ts(f.get("final_fill_time_utc"))
        if st:
            starts.append(st)
        if fn:
            finals.append(fn)
    span = _delta_ms(min(starts), max(finals)) if (starts and finals) else None
    return {
        "earliest_order_start": min(starts).isoformat() if starts else None,
        "latest_final_fill": max(finals).isoformat() if finals else None,
        "total_basket_span_ms": span,
        "per_leg": per_leg,
    }


def _fill_sequence_risk(fills: list[dict[str, Any]]) -> dict[str, Any]:
    filled = [f for f in fills if (f.get("order_status") in _FILLED_STATES) and _parse_ts(f.get("final_fill_time_utc"))]
    if len(filled) < 2:
        return {"exposure_window_ms": None, "first_leg_filled_at": None, "hedge_first_fill_at": None,
                "note": "need >=2 filled legs with timestamps to measure sequence risk"}
    by_final = sorted(filled, key=lambda f: _parse_ts(f.get("final_fill_time_utc")))
    first_final = _parse_ts(by_final[0].get("final_fill_time_utc"))
    hedge_first = _parse_ts(by_final[1].get("first_fill_time_utc")) or _parse_ts(by_final[1].get("final_fill_time_utc"))
    return {
        "exposure_window_ms": _delta_ms(first_final, hedge_first),
        "first_leg_filled_at": first_final.isoformat() if first_final else None,
        "hedge_first_fill_at": hedge_first.isoformat() if hedge_first else None,
        "note": "time the first leg was filled-and-unhedged before the hedge leg began filling",
    }


def _markouts(test_dir: Path, leg_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    snapshots = _read_jsonl(test_dir / "quote_snapshots.jsonl")
    latest_by_leg: dict[str, dict[str, Any]] = {}
    for s in snapshots:
        key = _leg_key(s.get("platform"), s.get("market_id_or_ticker"), s.get("side"))
        prev = latest_by_leg.get(key)
        if prev is None or str(s.get("appended_at_utc") or "") >= str(prev.get("appended_at_utc") or ""):
            latest_by_leg[key] = s
    out = []
    for lr in leg_results:
        snap = latest_by_leg.get(lr.get("leg_key"))
        if not snap or _opt_f(lr.get("filled_price")) is None:
            continue
        mark = _opt_f(snap.get("ask"))
        if mark is None:
            mark = _opt_f(snap.get("bid"))
        out.append({
            "leg_key": lr.get("leg_key"),
            "filled_price": lr.get("filled_price"),
            "latest_quote_source": snap.get("snapshot_source"),
            "latest_quote_ask": snap.get("ask"),
            "markout_vs_filled": _round(_sub(mark, _opt_f(lr.get("filled_price")))),
        })
    return out


# ---------------------------------------------------------------------------- #
# Loading / selection helpers                                                  #
# ---------------------------------------------------------------------------- #


def _load_list(path: Path, keys: tuple[str, ...]) -> tuple[list[dict[str, Any]], str | None]:
    path = Path(path)
    if not path.exists():
        return [], "file_not_found"
    payload = _read_json(path)
    if not isinstance(payload, dict):
        return [], "top_level_not_object"
    for k in keys:
        if isinstance(payload.get(k), list):
            return [x for x in payload[k] if isinstance(x, dict)], None
    return [], "no_expected_list_field"


def _select(items: list[dict[str, Any]], candidate_id: str) -> dict[str, Any] | None:
    if not items:
        return None
    cid = str(candidate_id).strip()
    if cid.isdigit():
        idx = int(cid) - 1
        if 0 <= idx < len(items):
            return items[idx]
    for it in items:
        ident = str(it.get("dedup_key") or it.get("candidate_id") or "")
        if cid == ident or (cid and cid in ident) or cid == str(it.get("iteration_timestamp")):
            return it
    return None


def _select_plan(plans: list[dict[str, Any]], candidate: dict[str, Any], candidate_id: str) -> dict[str, Any] | None:
    if not plans:
        return None
    target = str(candidate.get("dedup_key") or candidate.get("candidate_id") or "")
    for p in plans:
        if target and str(p.get("candidate_id")) == target:
            return p
    return _select(plans, candidate_id)


def _coerce_quote_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for k in ("quotes", "rows", "legs", "markets", "snapshots"):
            if isinstance(payload.get(k), list):
                return [x for x in payload[k] if isinstance(x, dict)]
        return [payload]
    return []


def _find_intended_leg(plan: dict[str, Any], platform: Any, market: Any, side: Any) -> dict[str, Any] | None:
    want = _leg_key(platform, market, side)
    for leg in plan.get("intended_legs") or []:
        if _leg_key(leg.get("platform"), leg.get("market_id_or_ticker"), leg.get("side")) == want:
            return leg
    return None


def _quote_record(src: dict[str, Any], *, source: str, appended_at: datetime) -> dict[str, Any]:
    token_ids = src.get("token_ids") if isinstance(src.get("token_ids"), dict) else {}
    return {
        "snapshot_source": source,
        "appended_at_utc": appended_at.isoformat(),
        "platform": src.get("platform"),
        "market_id_or_ticker": src.get("market_id_or_ticker"),
        "side": src.get("side"),
        "token_id": src.get("token_id") or src.get("token_id_yes") or src.get("token_id_no") or token_ids.get("yes") or token_ids.get("no"),
        "condition_id": src.get("condition_id"),
        "contract_id": src.get("contract_id"),
        "bid": _opt_f(src.get("bid") if src.get("bid") is not None else src.get("scanner_quote_bid")),
        "ask": _opt_f(src.get("ask") if src.get("ask") is not None else src.get("scanner_quote_ask")),
        "bid_size": _opt_f(src.get("bid_size")),
        "ask_size": _opt_f(src.get("ask_size") if src.get("ask_size") is not None else src.get("available_size_or_cap")),
        "quote_timestamp": src.get("quote_timestamp") or src.get("scanner_quote_timestamp"),
        "depth_status": src.get("depth_status"),
        "source_index": src.get("source_index"),
        "scanner_timestamp": src.get("scanner_quote_timestamp"),
    }


# ---------------------------------------------------------------------------- #
# Low-level IO + math                                                          #
# ---------------------------------------------------------------------------- #


def _now(now: datetime | None) -> datetime:
    ts = now or datetime.now(timezone.utc)
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _test_id(label: str | None, now: datetime) -> str:
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    slug = _slug(label) if label else "microtest"
    return f"{stamp}_{slug}"


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", str(text).strip().lower()).strip("_")
    return s[:48] or "microtest"


def _test_dir(output_root: Path, test_id: str) -> Path:
    return Path(output_root) / str(test_id)


def _leg_key(platform: Any, market: Any, side: Any) -> str:
    return f"{str(platform or '').lower()}::{market or ''}::{str(side or '').upper()}"


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True))
        fh.write("\n")


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, sort_keys=True))
            fh.write("\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _read_json(path: Path) -> Any:
    path = Path(path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _log_event(test_dir: Path, event_type: str, command: str, *, inputs: dict[str, Any],
               derived: dict[str, Any], warnings: list[str], now: datetime) -> None:
    _append_jsonl(test_dir / "event_log.jsonl", {
        "event_type": event_type,
        "timestamp_utc": now.isoformat(),
        "command": command,
        "inputs": inputs,
        "derived_values": derived,
        "warnings": list(warnings or []),
    })


def _safety() -> dict[str, Any]:
    return {
        "forensic_journal_only": True,
        "live_order_placement": False,
        "order_submit_or_cancel": False,
        "account_connection": False,
        "reads_credentials": False,
        "network_access": False,
        "browser_automation_added": False,
    }


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


def _delta_ms(a: datetime | None, b: datetime | None) -> float | None:
    if a is None or b is None:
        return None
    return round((b - a).total_seconds() * 1000.0, 1)


def _sub(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return a - b


def _round(value: float | None, places: int = 8) -> float | None:
    return None if value is None else round(value, places)


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
