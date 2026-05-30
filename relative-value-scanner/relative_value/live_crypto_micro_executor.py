"""Guarded real-money crypto micro-test trigger / executor.

Runs the structural scanner; the instant a buy-only PAPER_CANDIDATE appears it
freezes the candidate, refreshes every leg quote, recomputes the post-fee edge
under slippage caps, builds a protected execution plan, starts a forensic
micro-test journal, and then EITHER (default) dry-runs the intended orders or —
only when every live gate passes — places protected limit BUY orders via injected
venue adapters.

ABSOLUTE SAFETY POSTURE:
  - Dry-run is the default. No live order is possible unless ALL live gates pass.
  - Protected LIMIT BUY only — never a market order, never shorting/selling, never
    a midpoint, never an order without a max_limit_price, never chasing price.
  - A kill-switch file is checked before every order, retry, hedge, and cancel.
  - CDNA is manual / fill-first — never auto-placed, never browser-driven.
  - The only environment read is the boolean gate ``LIVE_CRYPTO_MICROTEST_ENABLED``;
    no credentials/.env are read and nothing secret is ever printed (logs redacted).
This is a micro-test harness with hard caps, not an unattended trading bot.
"""
from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from relative_value.execution_microstructure_plan import build_single_execution_plan
from relative_value.extract_crypto_paper_candidate_audit_pack import _boundary_inclusivity_risk
from relative_value.crypto_micro_test_journal import (
    start_micro_test_from_objects, record_micro_test_event, record_crypto_micro_fill,
    finalize_crypto_micro_test,
)
from relative_value.live_crypto_execution_adapters import (
    default_adapters, build_order_request, redact, MODE_DRY_RUN, MODE_LIVE,
)
from relative_value.live_trade_notifications import LiveTradeNotifier


SCHEMA_KIND = "crypto_structural_trigger_v1"
LIVE_ENV_VAR = "LIVE_CRYPTO_MICROTEST_ENABLED"
DEFAULT_KILL_SWITCH = Path("reports/live_crypto_micro_tests/KILL_SWITCH")
MAX_TOTAL_NOTIONAL_HARD_CAP = 30.0
MAX_PLATFORM_NOTIONAL_HARD_CAP = 10.0
MAX_LEG_NOTIONAL_HARD_CAP = 5.0
MIN_NET_EDGE_FLOOR = 0.10
_POLL_MS = 100.0


def run_crypto_structural_trigger(
    *,
    assets: list[str],
    watch_once_or_loop: str = "once",
    iterations: int = 300,
    min_net_edge: float = 0.10,
    operator_risk_mode: str = "aggressive",
    burst_mode: bool = False,
    burst_interval_seconds: float = 3.0,
    normal_interval_seconds: float = 20.0,
    boundary_window_seconds: float = 120.0,
    max_quote_age_ms: float = 750.0,
    max_slippage_cents: float = 1.0,
    order_timeout_ms: float = 1500.0,
    max_total_notional: float = 30.0,
    max_platform_notional: float = 10.0,
    max_leg_notional: float = 5.0,
    operator_size_cap: float = 10.0,
    max_daily_notional: float = 30.0,
    max_orders: int = 4,
    max_residual_exposure: float = 5.0,
    include_cdna: bool = False,
    cdna_evidence_dir: Path | None = None,
    operator_accept_cdna_display_price_risk: bool = False,
    cdna_operator_size_cap: float = 1.0,
    source_basis_buffer_bps: float = 0.0,
    output_dir: Path = Path("reports/crypto_structural_trigger"),
    execution_style: str = "manual",
    dry_run: bool = True,
    live: bool = False,
    i_understand_this_places_real_orders: bool = False,
    fail_fast: bool = False,
    lookahead_hours: float = 8.0,
    # ---- injectables (tests / future live client) ---- #
    report_builder: Callable[..., dict[str, Any]] | None = None,
    adapters: dict[str, Any] | None = None,
    quote_refresher: Callable[..., dict[str, Any]] | None = None,
    clock: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] | None = None,
    console: Callable[[str], None] | None = None,
    env: dict[str, str] | None = None,
    kill_switch_path: Path | None = None,
    http_get: Any = None,
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
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    asset_list = [str(a).strip().upper() for a in assets if str(a).strip()]
    builder = report_builder or _default_report_builder
    refresher = quote_refresher or _default_quote_refresher

    from relative_value.executable_venue_policy import (
        normalize_venues, DEFAULT_EXECUTABLE_VENUES, build_adapter_status_report,
    )
    exec_venues = normalize_venues(None, default=DEFAULT_EXECUTABLE_VENUES)  # kalshi/polymarket only
    requested_live = bool(live) and not bool(dry_run)
    mode = MODE_LIVE if requested_live else MODE_DRY_RUN
    used_adapters = adapters if adapters is not None else default_adapters(mode=mode)
    adapter_status = build_adapter_status_report(used_adapters, executable_venues=exec_venues)
    notifier = LiveTradeNotifier(
        provider_name=notify_provider, send=notify_send, notify_on=notify_on,
        dedup_seconds=notify_dedup_seconds, env=env, http_post=notification_http_post, clock=now_fn)

    params = {
        "min_net_edge": float(min_net_edge), "max_quote_age_ms": float(max_quote_age_ms),
        "max_slippage_cents": float(max_slippage_cents), "order_timeout_ms": float(order_timeout_ms),
        "max_total_notional": float(max_total_notional), "max_platform_notional": float(max_platform_notional),
        "max_leg_notional": float(max_leg_notional), "operator_size_cap": float(operator_size_cap),
        "max_orders": int(max_orders), "max_residual_exposure": float(max_residual_exposure),
        "execution_style": str(execution_style), "include_cdna": bool(include_cdna),
        "source_basis_buffer_bps": float(source_basis_buffer_bps),
        "executable_venues": list(exec_venues),
        "all_live_adapters_ready": bool(adapter_status.get("all_live_adapters_ready")),
    }
    live_flags = {
        "requested_dry_run": bool(dry_run), "requested_live": bool(live),
        "i_understand_this_places_real_orders": bool(i_understand_this_places_real_orders),
        "env_live_enabled": str(env.get(LIVE_ENV_VAR, "")).strip().lower() == "true",
        "mode": mode,
    }

    n_iters = 1 if str(watch_once_or_loop).lower() == "once" else max(1, int(iterations))
    iteration_records: list[dict[str, Any]] = []
    triggers: list[dict[str, Any]] = []
    started = now_fn()

    for i in range(n_iters):
        gen = now_fn()
        report = builder(
            assets=asset_list, operator_risk_mode=operator_risk_mode, include_cdna=include_cdna,
            operator_accept_cdna_display_price_risk=operator_accept_cdna_display_price_risk,
            allow_top_of_book_depth=True, operator_size_cap=operator_size_cap,
            cdna_operator_size_cap=cdna_operator_size_cap, cdna_evidence_dir=cdna_evidence_dir,
            max_quote_age_seconds=max(max_quote_age_ms / 1000.0, 1.0), max_basket_legs=12,
            source_basis_buffer_bps=source_basis_buffer_bps, lookahead_hours=lookahead_hours,
            generated_at=gen, refresh_kalshi_polymarket=True, http_get=http_get,
        )
        rows = report.get("rows") or []
        qualifying = [r for r in rows if _qualifies(r, float(min_net_edge))]
        _write_json(output_dir / "latest_scan_iteration.json", {
            "iteration": i, "generated_at": gen.isoformat(),
            "paper_candidate_rows": sum(1 for r in rows if r.get("paper_candidate")),
            "qualifying_candidates": len(qualifying),
            "summary_counts": report.get("summary_counts"),
        })
        for cand in qualifying[: params["max_orders"]]:
            tr = _process_trigger(
                candidate=cand, report=report, params=params, mode=mode, live_flags=live_flags,
                used_adapters=used_adapters, refresher=refresher, output_dir=output_dir,
                detected_at=now_fn(), now_fn=now_fn, sleeper=sleeper, kill_switch=kill_switch,
                operator_accept_cdna=operator_accept_cdna_display_price_risk,
                notifier=notifier,
            )
            triggers.append(tr)
            emit(f"trigger {tr['trigger_id']} | {tr['asset']} | do_trade={tr['do_trade']} | "
                 f"mode={mode} | reasons={tr['do_not_trade_reasons']}")
        iteration_records.append({"iteration": i, "generated_at": gen.isoformat(),
                                  "qualifying_candidates": len(qualifying), "triggers": len(qualifying)})
        if str(watch_once_or_loop).lower() != "once" and i < n_iters - 1:
            interval = burst_interval_seconds if (burst_mode and _near_boundary(gen, boundary_window_seconds)) else normal_interval_seconds
            sleeper(float(interval))

    summary = {
        "schema_kind": SCHEMA_KIND,
        "started_at": started.isoformat(), "updated_at": now_fn().isoformat(),
        "assets": asset_list, "mode": mode, "execution_style": execution_style,
        "iterations_completed": len(iteration_records),
        "triggers_created": len(triggers),
        "triggers_that_would_trade": sum(1 for t in triggers if t["do_trade"]),
        "live_flags": live_flags, "parameters": params,
        "executable_venues": list(exec_venues), "adapter_status": adapter_status,
        "kill_switch_path": str(kill_switch), "kill_switch_present": kill_switch.exists(),
        "triggers": [{"trigger_id": t["trigger_id"], "asset": t["asset"], "do_trade": t["do_trade"],
                      "do_not_trade_reasons": t["do_not_trade_reasons"], "trigger_dir": t["trigger_dir"],
                      "journal_path": t["journal_path"]} for t in triggers],
        "safety": _safety(),
    }
    _write_json(output_dir / "trigger_run_summary.json", summary)
    return summary


# ---------------------------------------------------------------------------- #
# Per-trigger processing                                                       #
# ---------------------------------------------------------------------------- #


def _process_trigger(*, candidate, report, params, mode, live_flags, used_adapters, refresher,
                     output_dir, detected_at, now_fn, sleeper, kill_switch, operator_accept_cdna,
                     notifier: LiveTradeNotifier | None = None) -> dict[str, Any]:
    asset = str(candidate.get("asset") or "UNK")
    trigger_id = f"{detected_at.strftime('%Y%m%dT%H%M%SZ')}_{asset}_{abs(hash(_candidate_sig(candidate))) % 10000:04d}"
    trigger_dir = output_dir / trigger_id
    (trigger_dir / "micro_test_journal").mkdir(parents=True, exist_ok=True)

    _write_json(trigger_dir / "trigger_candidate.json", {"detected_at_utc": detected_at.isoformat(), "candidate": candidate})
    _write_json(trigger_dir / "scan_iteration.json", {
        "generated_at": report.get("generated_at"), "summary_counts": report.get("summary_counts"),
        "candidate_row": candidate,
    })

    # 2-3. Immediately refresh every leg quote + recompute edge.
    quote_refresh_started = now_fn()
    refreshed_candidate = copy.deepcopy(candidate)
    refreshed_legs_meta = []
    for leg in refreshed_candidate.get("basket_legs") or []:
        q = refresher(leg=leg, now=now_fn())
        if q.get("ask") is not None:
            leg["ask"] = q.get("ask")
        leg["quote_timestamp"] = q.get("quote_timestamp")
        meta = {"leg_key": _leg_key(leg), **q}
        refreshed_legs_meta.append(meta)
        _append_jsonl(trigger_dir / "refreshed_quotes.jsonl", meta)
    quote_refresh_completed = now_fn()

    plan = build_single_execution_plan(
        refreshed_candidate, max_total_notional=params["max_total_notional"],
        max_leg_notional=params["max_leg_notional"], max_slippage_cents=params["max_slippage_cents"],
        max_quote_age_ms=params["max_quote_age_ms"], execution_style=params["execution_style"],
        min_net_edge=params["min_net_edge"], generated_at=now_fn(),
    )
    _write_json(trigger_dir / "execution_plan.json", plan)

    boundary_risk, boundary_detail = _boundary_inclusivity_risk(refreshed_candidate.get("basket_legs") or [])
    recomputed = {
        "original_net_edge_after_fees": candidate.get("net_edge_after_fees"),
        "refreshed_net_edge_after_fees": plan.get("expected_net_edge_after_fees"),
        "adjusted_net_edge_after_fees": plan.get("expected_adjusted_net_edge_after_fees"),
        "net_edge_after_fees_at_max_limits": plan.get("net_edge_after_fees_at_max_limits"),
        "min_net_edge_gate": params["min_net_edge"],
        "per_leg_quote_age_ms": {m["leg_key"]: m.get("quote_age_ms") for m in refreshed_legs_meta},
        "max_quote_age_ms": params["max_quote_age_ms"],
        "boundary_inclusivity_risk": boundary_risk, "boundary_detail": boundary_detail,
        "detected_to_refresh_complete_ms": _delta_ms(detected_at, quote_refresh_completed),
    }
    _write_json(trigger_dir / "recomputed_edge.json", recomputed)

    # 5. Start micro-test journal from the frozen + refreshed objects.
    journal_root = trigger_dir / "micro_test_journal"
    jr = start_micro_test_from_objects(
        candidate=refreshed_candidate, plan=plan, max_total_notional=params["max_total_notional"],
        test_label=f"{trigger_id}", output_root=journal_root, now=now_fn(),
        extra={"trigger_id": trigger_id, "detected_at_utc": detected_at.isoformat(), "mode": mode},
    )
    journal_test_id = jr["test_id"]
    notification_results: list[dict[str, Any]] = []

    # 6. Intended orders (always BUY LIMIT, never market). Recorded for dry-run + audit.
    intended_orders = []
    ordered_legs = _ordered_plan_legs(plan)
    for idx, lp in enumerate(ordered_legs):
        req = build_order_request(client_order_id=f"{trigger_id}-{idx}", leg=lp,
                                  max_limit_price=lp.get("max_limit_price"), quantity=lp.get("quantity_cap"),
                                  order_timeout_ms=params["order_timeout_ms"])
        rec = req.to_redacted_dict()
        intended_orders.append(rec)
        _append_jsonl(trigger_dir / "intended_orders.jsonl", rec)
    record_micro_test_event(test_id=journal_test_id, event_type="intended_orders",
                            inputs={"intended_orders": intended_orders}, output_root=journal_root, now=now_fn())

    # 7. Gate evaluation.
    do_not_trade = _evaluate_gates(
        candidate=candidate, plan=plan, params=params, live_flags=live_flags, mode=mode,
        kill_switch=kill_switch, refreshed_legs_meta=refreshed_legs_meta, boundary_risk=boundary_risk,
        operator_accept_cdna=operator_accept_cdna,
    )
    do_trade = (mode == MODE_LIVE) and not do_not_trade
    if mode == MODE_DRY_RUN:
        _notify_execution_event(
            notifier=notifier, event_type="submitted", notifications=notification_results,
            journal_test_id=journal_test_id, journal_root=journal_root, now_fn=now_fn,
            payload=_notification_payload(
                candidate=candidate, test_id=journal_test_id, dry_run=True,
                expected_edge=plan.get("expected_net_edge_after_fees") or candidate.get("net_edge_after_fees"),
                reason=", ".join(do_not_trade) or "dry_run_no_live_orders",
                short_status="dry_run_intended_orders_created",
            ),
        )

    execution_result = {"placed": False, "mode": mode, "fills": [], "cancels": [], "residual_exposure": [],
                        "emergency_review_required": False, "manual_cdna_required": plan.get("has_cdna_leg", False)}
    if do_trade:
        execution_result = _execute_live_orders(
            plan=plan, ordered_legs=ordered_legs, used_adapters=used_adapters, params=params,
            trigger_id=trigger_id, trigger_dir=trigger_dir, journal_test_id=journal_test_id,
            journal_root=journal_root, now_fn=now_fn, sleeper=sleeper, kill_switch=kill_switch,
            notifier=notifier, notifications=notification_results, notification_base={
                "test_id": journal_test_id, "asset": candidate.get("asset"),
                "candidate_type": candidate.get("candidate_type"),
                "expected_edge": plan.get("expected_net_edge_after_fees") or candidate.get("net_edge_after_fees"),
            },
        )
    else:
        record_micro_test_event(test_id=journal_test_id, event_type=("dry_run_no_orders" if mode == MODE_DRY_RUN else "live_gates_failed_no_orders"),
                                inputs={"mode": mode, "do_not_trade_reasons": do_not_trade},
                                output_root=journal_root, now=now_fn())

    _write_json(trigger_dir / "residual_exposure.json",
                {"residual_exposure": execution_result.get("residual_exposure", []),
                 "emergency_review_required": execution_result.get("emergency_review_required", False),
                 "max_residual_exposure": params["max_residual_exposure"]})

    # 8. Finalize journal even if no order placed.
    final = finalize_crypto_micro_test(test_id=journal_test_id, output_root=journal_root, now=now_fn(),
                                       manual_notes=f"trigger={trigger_id} mode={mode} do_trade={do_trade}")
    _notify_execution_event(
        notifier=notifier, event_type="finalized", notifications=notification_results,
        journal_test_id=journal_test_id, journal_root=journal_root, now_fn=now_fn,
        payload=_notification_payload(
            candidate=candidate, test_id=journal_test_id,
            expected_edge=final.get("actual_net_edge_after_fees_if_all_filled")
            or final.get("intended_net_edge_after_fees"),
            residual_exposure=final.get("residual_exposure"),
            short_status=str(final.get("verdict") or "finalized"),
        ),
    )
    execution_result["notification_results"] = notification_results
    _attach_notification_results_to_final_report(journal_root, journal_test_id, notification_results)

    latency = {
        "detected_at": detected_at.isoformat(),
        "quote_refresh_started_at": quote_refresh_started.isoformat(),
        "quote_refresh_completed_at": quote_refresh_completed.isoformat(),
        "detected_to_refresh_complete_ms": _delta_ms(detected_at, quote_refresh_completed),
        "max_allowed_time_to_complete_basket_ms": params["order_timeout_ms"] * max(1, len(ordered_legs)),
    }
    trigger_report = {
        "schema_kind": "crypto_structural_trigger_report_v1",
        "trigger_id": trigger_id, "asset": asset, "mode": mode,
        "detected_at_utc": detected_at.isoformat(), "do_trade": do_trade,
        "do_not_trade_reasons": do_not_trade, "execution_style": plan.get("effective_execution_style"),
        "original_net_edge_after_fees": candidate.get("net_edge_after_fees"),
        "refreshed_net_edge_after_fees": plan.get("expected_net_edge_after_fees"),
        "adjusted_net_edge_after_fees": plan.get("expected_adjusted_net_edge_after_fees"),
        "net_edge_after_fees_at_max_limits": plan.get("net_edge_after_fees_at_max_limits"),
        "min_net_edge": params["min_net_edge"], "max_slippage_cents": params["max_slippage_cents"],
        "basket_quantity_cap": plan.get("basket_quantity_cap"),
        "latency": latency, "recomputed_edge": recomputed, "intended_orders": intended_orders,
        "execution_result": execution_result, "plan": plan,
        "notification_results": notification_results,
        "journal_path": str(journal_root / journal_test_id),
        "trigger_dir": str(trigger_dir), "safety": _safety(),
    }
    _write_json(trigger_dir / "trigger_report.json", trigger_report)
    _write_text(trigger_dir / "trigger_report.md", _render_trigger_md(trigger_report))
    return trigger_report


# ---------------------------------------------------------------------------- #
# Gates + protected order execution                                            #
# ---------------------------------------------------------------------------- #


def _qualifies(row: dict[str, Any], min_net_edge: float) -> bool:
    if not row.get("paper_candidate"):
        return False
    if not row.get("tradable_buy_only", True):
        return False
    if row.get("requires_short_or_sell"):
        return False
    if row.get("hard_blockers"):
        return False
    net = _opt_f(row.get("net_edge_after_fees"))
    adj = _opt_f(row.get("adjusted_net_edge_after_fees"))
    adj = adj if adj is not None else net
    return net is not None and net >= min_net_edge and adj is not None and adj >= min_net_edge


def _evaluate_gates(*, candidate, plan, params, live_flags, mode, kill_switch,
                    refreshed_legs_meta, boundary_risk, operator_accept_cdna) -> list[str]:
    reasons: list[str] = []
    if mode == MODE_DRY_RUN:
        reasons.append("dry_run_default_no_live_orders")
    if not live_flags["env_live_enabled"]:
        reasons.append("env_LIVE_CRYPTO_MICROTEST_ENABLED_not_true")
    if not live_flags["requested_live"]:
        reasons.append("missing_flag_--live")
    if not live_flags["i_understand_this_places_real_orders"]:
        reasons.append("missing_flag_--i-understand-this-places-real-orders")
    if live_flags["requested_dry_run"]:
        reasons.append("dry_run_flag_present")
    if kill_switch.exists():
        reasons.append("kill_switch_present")
    if params["max_total_notional"] > MAX_TOTAL_NOTIONAL_HARD_CAP:
        reasons.append("max_total_notional_exceeds_cap_30")
    if params["max_platform_notional"] > MAX_PLATFORM_NOTIONAL_HARD_CAP:
        reasons.append("max_platform_notional_exceeds_cap_10")
    if params["max_leg_notional"] > MAX_LEG_NOTIONAL_HARD_CAP:
        reasons.append("max_leg_notional_exceeds_cap_5")
    if params["min_net_edge"] < MIN_NET_EDGE_FLOOR:
        reasons.append("min_net_edge_below_required_floor_0.10")
    # Plan-derived gates (stale, edge, hard blockers, qty, etc.).
    for r in plan.get("do_not_trade_reasons") or []:
        reasons.append(f"plan:{r}")
    if not plan.get("executable_intent"):
        reasons.append("execution_plan_not_executable_intent")
    # Manual style never auto-places — it prints the plan for the operator.
    if plan.get("effective_execution_style") == "manual":
        reasons.append("manual_execution_style_no_automated_orders")
    # Refreshed-quote freshness.
    for m in refreshed_legs_meta:
        age = _opt_f(m.get("quote_age_ms"))
        if m.get("ask") is None or m.get("quote_timestamp") is None:
            reasons.append("refreshed_quote_missing")
            break
        if age is None or age > params["max_quote_age_ms"]:
            reasons.append("refreshed_quote_stale")
            break
    if candidate.get("requires_short_or_sell"):
        reasons.append("short_or_sell_required")
    if candidate.get("hard_blockers"):
        reasons.append("candidate_has_hard_blockers")
    if boundary_risk:
        reasons.append("boundary_inclusivity_unvalidated")
    if plan.get("has_cdna_leg"):
        reasons.append("cdna_requires_manual_fill_first_no_confirmed_fill")
        reasons.append("cdna_no_safe_automated_order_adapter")
    # Executable-venue policy: only Kalshi/Polymarket are auto-executable.
    exec_set = {str(v).lower() for v in (params.get("executable_venues") or ("kalshi", "polymarket"))}
    plan_platforms = [str(lp.get("platform") or "").lower() for lp in (plan.get("legs") or plan.get("ordered_legs") or [])]
    if any(p and p not in exec_set for p in plan_platforms):
        reasons.append("non_executable_venue_leg")
    # Live adapters must be real (client injected + preflight ok); a stub fails closed.
    if mode == MODE_LIVE and not params.get("all_live_adapters_ready"):
        reasons.append("live_adapter_not_implemented")
    return sorted(set(reasons))


def _execute_live_orders(*, plan, ordered_legs, used_adapters, params, trigger_id, trigger_dir,
                         journal_test_id, journal_root, now_fn, sleeper, kill_switch,
                         notifier: LiveTradeNotifier | None = None,
                         notifications: list[dict[str, Any]] | None = None,
                         notification_base: dict[str, Any] | None = None) -> dict[str, Any]:
    fills_out: list[dict[str, Any]] = []
    cancels_out: list[dict[str, Any]] = []
    residual: list[dict[str, Any]] = []
    emergency = False
    orders_placed = 0
    hedge_qty: float | None = None  # exact filled qty propagated to hedge legs
    first_leg_filled = 0.0

    for idx, lp in enumerate(ordered_legs):
        if _kill(kill_switch):
            emergency = True
            _journal(journal_test_id, journal_root, "kill_switch_abort", {"leg": idx}, now_fn)
            _notify_execution_event(
                notifier=notifier, event_type="emergency", notifications=notifications,
                journal_test_id=journal_test_id, journal_root=journal_root, now_fn=now_fn,
                payload=_notification_payload(base=notification_base, leg=lp, request=req if "req" in locals() else None,
                                              short_status="kill_switch_abort"),
            )
            break
        if orders_placed >= params["max_orders"]:
            break
        platform = str(lp.get("platform") or "").lower()
        adapter = used_adapters.get(platform)
        qty = hedge_qty if (hedge_qty is not None and idx > 0) else lp.get("quantity_cap")
        req = build_order_request(client_order_id=f"{trigger_id}-{idx}", leg=lp,
                                  max_limit_price=lp.get("max_limit_price"), quantity=qty,
                                  order_timeout_ms=params["order_timeout_ms"])
        ok, vreason = req.validate()
        _append_jsonl(trigger_dir / "order_requests_redacted.jsonl", req.to_redacted_dict())
        if not ok or adapter is None:
            cancels_out.append({"leg": idx, "reason": vreason if not ok else "no_adapter"})
            break
        if _kill(kill_switch):
            emergency = True
            break
        resp = adapter.place_limit_buy(req)
        orders_placed += 1
        _append_jsonl(trigger_dir / "order_responses_redacted.jsonl", redact(resp))
        status = str(resp.get("status"))
        if status in {"MANUAL_REQUIRED", "REJECTED", "DRY_RUN_NOT_PLACED"}:
            _notify_execution_event(
                notifier=notifier, event_type=("rejected" if status == "REJECTED" else "submitted"),
                notifications=notifications, journal_test_id=journal_test_id, journal_root=journal_root, now_fn=now_fn,
                payload=_notification_payload(base=notification_base, leg=lp, request=req, order_id=resp.get("order_id"),
                                              short_status=status, reason=resp.get("reason")),
            )
            _journal(journal_test_id, journal_root, "order_not_placed", {"leg": idx, "status": status, "reason": resp.get("reason")}, now_fn)
            break
        order_id = resp.get("order_id")
        _notify_execution_event(
            notifier=notifier, event_type="submitted", notifications=notifications,
            journal_test_id=journal_test_id, journal_root=journal_root, now_fn=now_fn,
            payload=_notification_payload(base=notification_base, leg=lp, request=req, order_id=order_id,
                                          short_status=status),
        )
        filled_qty, avg_px = _poll_until_fill(adapter, order_id, qty, params["order_timeout_ms"], now_fn, sleeper, kill_switch, trigger_dir)
        if (filled_qty or 0) < (qty or 0):
            cancel = adapter.cancel_order(order_id)
            cancels_out.append({"leg": idx, "order_id": order_id, "cancel": redact(cancel)})
            _append_jsonl(trigger_dir / "cancels.jsonl", {"leg": idx, "order_id": order_id, "cancel": redact(cancel)})
            _notify_execution_event(
                notifier=notifier, event_type="canceled", notifications=notifications,
                journal_test_id=journal_test_id, journal_root=journal_root, now_fn=now_fn,
                payload=_notification_payload(base=notification_base, leg=lp, request=req, order_id=order_id,
                                              filled_qty=filled_qty, intended_qty=qty, fill_price=avg_px,
                                              short_status=str((cancel or {}).get("status") or "canceled")),
            )
        leg_fills = adapter.get_fills(order_id) or [{"price": avg_px, "quantity": filled_qty}]
        for f in leg_fills:
            _append_jsonl(trigger_dir / "fills.jsonl", redact({"leg": idx, "order_id": order_id, **f}))
        fills_out.append({"leg": idx, "filled_quantity": filled_qty, "avg_fill_price": avg_px})
        if filled_qty:
            _notify_execution_event(
                notifier=notifier, event_type=("filled" if filled_qty >= (qty or 0) else "partial"),
                notifications=notifications, journal_test_id=journal_test_id, journal_root=journal_root, now_fn=now_fn,
                payload=_notification_payload(base=notification_base, leg=lp, request=req, order_id=order_id,
                                              fill_id=f"{order_id}:{idx}:{filled_qty}", filled_qty=filled_qty,
                                              intended_qty=qty, fill_price=avg_px,
                                              short_status=("filled" if filled_qty >= (qty or 0) else "partial")),
            )
        record_crypto_micro_fill(
            test_id=journal_test_id, platform=lp.get("platform"), market_id_or_ticker=lp.get("market_id_or_ticker"),
            side=("YES" if str(lp.get("side", "")).upper().endswith("YES") else "NO"),
            filled_price=avg_px, filled_quantity=filled_qty, fees=None,
            order_status=("filled" if (filled_qty or 0) >= (qty or 0) and filled_qty else ("partial" if filled_qty else "not_filled")),
            output_root=journal_root, now=now_fn(),
        )
        _append_jsonl(trigger_dir / "order_status_updates.jsonl",
                      {"leg": idx, "order_id": order_id, "filled_quantity": filled_qty, "avg_fill_price": avg_px})

        if idx == 0:
            first_leg_filled = filled_qty or 0.0
            if first_leg_filled <= 0:
                _journal(journal_test_id, journal_root, "first_leg_no_fill_abort", {"leg": idx}, now_fn)
                break
            hedge_qty = first_leg_filled  # hedge EXACT filled quantity only
        else:
            # Hedge leg: residual is the part of the first leg left unhedged.
            unhedged = round(max(0.0, first_leg_filled - (filled_qty or 0.0)), 8)
            if unhedged > 0:
                emergency = True
                residual.append({
                    "unhedged_quantity": unhedged,
                    "worst_case_loss_if_settles_zero": round((lp.get("all_in_max_cost") or 0.0) * unhedged, 6),
                    "reason": "hedge_partial_or_failed_stop_all_further_orders",
                })
                _journal(journal_test_id, journal_root, "hedge_failed_emergency_review_required",
                         {"unhedged_quantity": unhedged}, now_fn)
                _notify_execution_event(
                    notifier=notifier, event_type="emergency", notifications=notifications,
                    journal_test_id=journal_test_id, journal_root=journal_root, now_fn=now_fn,
                    payload=_notification_payload(base=notification_base, leg=lp, request=req, order_id=order_id,
                                                  filled_qty=filled_qty, intended_qty=qty, fill_price=avg_px,
                                                  residual_exposure=residual,
                                                  short_status="hedge_failed_residual_opened"),
                )
                break
    return {"placed": orders_placed > 0, "mode": MODE_LIVE, "orders_placed": orders_placed,
            "fills": fills_out, "cancels": cancels_out, "residual_exposure": residual,
            "emergency_review_required": emergency, "manual_cdna_required": False}


def _poll_until_fill(adapter, order_id, qty, timeout_ms, now_fn, sleeper, kill_switch, trigger_dir):
    elapsed = 0.0
    filled_qty = 0.0
    avg_px = None
    while True:
        if _kill(kill_switch):
            break
        st = adapter.get_order_status(order_id)
        filled_qty = _opt_f(st.get("filled_quantity")) or 0.0
        avg_px = st.get("avg_fill_price")
        if filled_qty >= (qty or 0):
            break
        if elapsed >= timeout_ms:
            break
        sleeper(_POLL_MS / 1000.0)
        elapsed += _POLL_MS
    return filled_qty, avg_px


def _notify_execution_event(*, notifier, event_type, notifications, journal_test_id, journal_root, now_fn, payload) -> dict[str, Any] | None:
    if notifier is None:
        return None
    result = notifier.notify(event_type, payload)
    if notifications is not None:
        notifications.append(result)
    record_micro_test_event(
        test_id=journal_test_id,
        event_type=result.get("event_log_event_type") or "notification_skipped",
        inputs={"notification": result},
        output_root=journal_root,
        now=now_fn(),
    )
    return result


def _notification_payload(*, candidate=None, base=None, test_id=None, leg=None, request=None,
                          order_id=None, fill_id=None, filled_qty=None, intended_qty=None,
                          fill_price=None, expected_edge=None, residual_exposure=None,
                          dry_run=False, reason=None, short_status=None) -> dict[str, Any]:
    base = dict(base or {})
    candidate = candidate or {}
    req = request
    market = (leg or {}).get("market_id_or_ticker")
    side = getattr(req, "side", None) or (leg or {}).get("side")
    limit_price = getattr(req, "max_limit_price", None)
    qty = intended_qty if intended_qty is not None else getattr(req, "quantity", None)
    platform = getattr(req, "platform", None) or (leg or {}).get("platform")
    return {
        "test_id": test_id or base.get("test_id"),
        "asset": base.get("asset") or candidate.get("asset"),
        "candidate_type": base.get("candidate_type") or candidate.get("candidate_type"),
        "platform": platform,
        "side": side,
        "market_id": market or getattr(req, "market_id_or_ticker", None),
        "leg_key": _leg_key(leg or {}) if leg else None,
        "order_id": order_id,
        "fill_id": fill_id,
        "filled_qty": filled_qty,
        "intended_qty": qty,
        "fill_price": fill_price,
        "limit_price": limit_price,
        "expected_edge": expected_edge if expected_edge is not None else base.get("expected_edge"),
        "residual_exposure": residual_exposure,
        "dry_run": bool(dry_run),
        "reason": reason,
        "short_status": short_status,
    }


def _attach_notification_results_to_final_report(journal_root: Path, test_id: str, notifications: list[dict[str, Any]]) -> None:
    path = Path(journal_root) / str(test_id) / "final_report.json"
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(payload, dict):
        return
    payload["notification_results"] = list(notifications or [])
    _write_json(path, payload)


# ---------------------------------------------------------------------------- #
# Defaults + helpers                                                           #
# ---------------------------------------------------------------------------- #


def _default_report_builder(**kwargs: Any) -> dict[str, Any]:
    from relative_value.crypto_structural_payoff_arb_scout import (  # local import: keeps import graph light
        build_crypto_structural_payoff_arb_scout_report,
    )
    return build_crypto_structural_payoff_arb_scout_report(**kwargs)


def _default_quote_refresher(*, leg: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Acting immediately on a live scan, the scanner's just-read quote IS the
    refreshed quote. Re-stamp the age relative to now (no extra network fetch)."""
    qts = _parse_ts(leg.get("quote_timestamp"))
    age = _delta_ms(qts, now) if qts is not None else None
    return {
        "platform": leg.get("platform"), "market_id_or_ticker": leg.get("market_id_or_ticker"),
        "side": leg.get("side"), "ask": _opt_f(leg.get("ask")), "bid": _opt_f(leg.get("bid")),
        "ask_size": _opt_f(leg.get("available_size_or_cap")), "bid_size": None,
        "quote_timestamp": leg.get("quote_timestamp"), "quote_age_ms": age,
        "depth_status": leg.get("depth_status"), "source": "live_scan_immediate",
    }


def _ordered_plan_legs(plan: dict[str, Any]) -> list[dict[str, Any]]:
    seq = (plan.get("leg_order_recommendation") or {}).get("sequence") or []
    legs = plan.get("legs") or []
    by_id = {l.get("market_id_or_ticker"): l for l in legs}
    ordered = [by_id[m] for m in seq if m in by_id]
    return ordered or legs


def _candidate_sig(c: dict[str, Any]) -> str:
    legs = c.get("basket_legs") or []
    return f"{c.get('asset')}|{c.get('candidate_type')}|{c.get('target_instant_utc')}|" + "|".join(
        sorted(f"{l.get('platform')}:{l.get('side')}:{l.get('market_id_or_ticker')}" for l in legs))


def _leg_key(leg: dict[str, Any]) -> str:
    return f"{str(leg.get('platform') or '').lower()}::{leg.get('market_id_or_ticker') or ''}::{str(leg.get('side') or '').upper()}"


def _near_boundary(now: datetime, window_seconds: float) -> bool:
    epoch = now.timestamp()
    for p in (300, 900, 1200, 3600, 7200, 14400):
        r = epoch % p
        if min(r, p - r) <= window_seconds:
            return True
    return False


def _kill(kill_switch: Path) -> bool:
    return Path(kill_switch).exists()


def _journal(test_id, journal_root, event_type, inputs, now_fn) -> None:
    record_micro_test_event(test_id=test_id, event_type=event_type, inputs=inputs,
                            output_root=journal_root, now=now_fn())


def _render_trigger_md(tr: dict[str, Any]) -> str:
    er = tr.get("execution_result") or {}
    lat = tr.get("latency") or {}
    plan = tr.get("plan") or {}
    lines = [
        "# Crypto Structural Arb — Live Trigger Report",
        "",
        "Guarded micro-test trigger. Dry-run by default; protected LIMIT BUY only; no shorting; "
        "no market orders; CDNA manual fill-first; kill-switch protected; logs redacted.",
        "",
        "## Decision",
        "",
        f"- trigger_id: `{tr.get('trigger_id')}`  asset: `{tr.get('asset')}`  mode: **{tr.get('mode')}**",
        f"- **do_trade: {tr.get('do_trade')}**",
        f"- do_not_trade_reasons: `{', '.join(tr.get('do_not_trade_reasons') or []) or 'none'}`",
        f"- execution_style: `{tr.get('execution_style')}`  candidate_action: `{plan.get('candidate_action')}`",
        "",
        "## Edge",
        "",
        f"- original net edge: `{tr.get('original_net_edge_after_fees')}`  refreshed: `{tr.get('refreshed_net_edge_after_fees')}`  "
        f"adjusted: `{tr.get('adjusted_net_edge_after_fees')}`",
        f"- net edge at max slippage caps: `{tr.get('net_edge_after_fees_at_max_limits')}`  min_net_edge: `{tr.get('min_net_edge')}`",
        f"- detected -> refresh complete: `{lat.get('detected_to_refresh_complete_ms')}` ms",
        "",
        "## Intended Protected Orders (BUY LIMIT only)",
        "",
        "| Platform | Side | Market id/ticker | Max limit | Qty | Type |",
        "|---|---|---|---:|---:|---|",
    ]
    for o in tr.get("intended_orders") or []:
        lines.append(
            f"| {o.get('platform')} | {o.get('side')} | {o.get('market_id_or_ticker')} | "
            f"{o.get('max_limit_price')} | {o.get('quantity')} | {o.get('order_type')} |"
        )
    lines += [
        "",
        "## Execution Result",
        "",
        f"- placed: `{er.get('placed')}`  orders_placed: `{er.get('orders_placed', 0)}`  "
        f"emergency_review_required: `{er.get('emergency_review_required')}`",
        f"- manual_cdna_required: `{er.get('manual_cdna_required')}`",
        f"- residual_exposure: `{er.get('residual_exposure')}`",
        "",
        "## Why No Order (if applicable)",
        "",
        ("- Orders placed." if tr.get("do_trade") else
         "- " + "\n- ".join(tr.get("do_not_trade_reasons") or ["dry-run / gates not satisfied"])),
        "",
        f"- micro-test journal: `{tr.get('journal_path')}`",
        "",
        "## Safety",
        "",
        "- dry_run_default: `true`  protected_limit_buy_only: `true`  market_orders: `false`  shorting: `false`",
        "- browser_automation_added: `false`  reads_credentials: `false`  prints_secrets: `false`  logs_redacted: `true`",
    ]
    return "\n".join(lines) + "\n"


def _safety() -> dict[str, Any]:
    return {
        "dry_run_default": True, "protected_limit_buy_only": True, "market_orders_disabled": True,
        "shorting_disabled": True, "browser_automation_added": False, "reads_credentials": False,
        "prints_secrets": False, "logs_redacted": True, "kill_switch_checked": True,
        "cdna_manual_fill_first_only": True,
    }


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


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    import re
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


def _opt_f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
