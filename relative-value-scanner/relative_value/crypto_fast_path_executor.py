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
)


UNIVERSE_SCHEMA_KIND = "active_crypto_candidate_universe_v1"
FASTPATH_SCHEMA_KIND = "crypto_fast_path_trigger_v1"


# ---------------------------------------------------------------------------- #
# Phase 1: discovery pass                                                      #
# ---------------------------------------------------------------------------- #


def build_active_candidate_universe(
    *, assets: list[str], operator_risk_mode: str = "aggressive", include_cdna: bool = False,
    cdna_evidence_dir: Path | None = None, operator_size_cap: float = 10.0, cdna_operator_size_cap: float = 1.0,
    source_basis_buffer_bps: float = 0.0, lookahead_hours: float = 8.0, min_net_edge: float = 0.0,
    max_candidates: int = 50, output_path: Path | None = None, generated_at: datetime | None = None,
    report_builder: Callable[..., dict[str, Any]] | None = None, http_get: Any = None,
) -> dict[str, Any]:
    gen = _now(generated_at)
    builder = report_builder or _default_report_builder
    report = builder(
        assets=[a.strip().upper() for a in assets if a.strip()], operator_risk_mode=operator_risk_mode,
        include_cdna=include_cdna, operator_accept_cdna_display_price_risk=include_cdna,
        allow_top_of_book_depth=True, operator_size_cap=operator_size_cap,
        cdna_operator_size_cap=cdna_operator_size_cap, cdna_evidence_dir=cdna_evidence_dir,
        max_basket_legs=12, source_basis_buffer_bps=source_basis_buffer_bps, lookahead_hours=lookahead_hours,
        generated_at=gen, refresh_kalshi_polymarket=True, http_get=http_get,
    )
    rows = report.get("rows") or []
    buy_only = [
        r for r in rows
        if r.get("tradable_buy_only", True) and not r.get("requires_short_or_sell")
        and _opt_f(r.get("net_edge_after_fees")) is not None
        and _opt_f(r.get("net_edge_after_fees")) >= float(min_net_edge)
    ]
    buy_only.sort(key=lambda r: _opt_f(r.get("net_edge_after_fees")) or -1e9, reverse=True)
    candidates = [_universe_candidate(r) for r in buy_only[: int(max_candidates)]]
    watched: dict[str, dict[str, Any]] = {}
    for c in candidates:
        for leg in c["legs"]:
            watched.setdefault(leg["leg_key"], {k: leg[k] for k in (
                "leg_key", "platform", "market_id_or_ticker", "side", "token_id", "contract_id",
                "condition_id", "reference_ask", "fee", "available_size_or_cap")})
    universe = {
        "schema_kind": UNIVERSE_SCHEMA_KIND, "generated_at": gen.isoformat(),
        "min_net_edge_at_discovery": float(min_net_edge), "assets": [a.strip().upper() for a in assets if a.strip()],
        "candidate_count": len(candidates), "watched_leg_count": len(watched),
        "candidates": candidates, "watched_legs": list(watched.values()),
        "discovery_params": {
            "assets": [a.strip().upper() for a in assets if a.strip()], "operator_risk_mode": operator_risk_mode,
            "include_cdna": bool(include_cdna), "cdna_evidence_dir": str(cdna_evidence_dir) if cdna_evidence_dir else None,
            "operator_size_cap": float(operator_size_cap), "cdna_operator_size_cap": float(cdna_operator_size_cap),
            "source_basis_buffer_bps": float(source_basis_buffer_bps), "lookahead_hours": float(lookahead_hours),
            "min_net_edge": float(min_net_edge), "max_candidates": int(max_candidates),
        },
        "safety": {"diagnostic_only": True, "discovery_pass_runs_full_scout": True, "network_access_in_hot_path": False},
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
    dry_run: bool = True,
    live: bool = False,
    i_understand_this_places_real_orders: bool = False,
    quote_refresher: Callable[..., dict[str, Any]] | None = None,
    discovery_fn: Callable[[], dict[str, Any]] | None = None,
    adapters: dict[str, Any] | None = None,
    clock: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] | None = None,
    console: Callable[[str], None] | None = None,
    env: dict[str, str] | None = None,
    kill_switch_path: Path | None = None,
) -> dict[str, Any]:
    now_fn = clock or (lambda: datetime.now(timezone.utc))
    sleeper = sleep or (lambda _s: None)
    emit = console or print
    env = env if env is not None else os.environ
    kill_switch = Path(kill_switch_path) if kill_switch_path is not None else DEFAULT_KILL_SWITCH
    refresher = quote_refresher or _universe_quote_refresher
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    universe = _read_json(Path(candidate_universe)) or {}
    candidates = universe.get("candidates") or []
    watched = universe.get("watched_legs") or []
    requested_live = bool(live) and not bool(dry_run)
    mode = MODE_LIVE if requested_live else MODE_DRY_RUN
    used_adapters = adapters if adapters is not None else default_adapters(mode=mode)

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
    }
    live_flags = {
        "requested_dry_run": bool(dry_run), "requested_live": bool(live),
        "i_understand_this_places_real_orders": bool(i_understand_this_places_real_orders),
        "env_live_enabled": str(env.get(LIVE_ENV_VAR, "")).strip().lower() == "true", "mode": mode,
    }

    quote_cache: dict[str, dict[str, Any]] = {}
    cache_path = output_dir / "quote_cache.jsonl"
    decisions: list[dict[str, Any]] = []
    recognized_first: dict[str, datetime] = {}
    discovery_runs = 0
    last_discovery_at = now_fn()  # the loaded universe file is the t0 discovery

    for tick in range(max(1, int(iterations))):
        tick_now = now_fn()
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
        for leg in watched:
            q = refresher(leg=leg, now=refresh_started)
            quote_cache[leg["leg_key"]] = q
            _append_jsonl(cache_path, {"tick": tick, "leg_key": leg["leg_key"], **q})
        refresh_completed = now_fn()

        for cand in candidates:
            rec = _recompute_edge_from_cache(cand, quote_cache, params)
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
            )
            decisions.append(decision)
            emit(f"fastpath tick={tick} {cand.get('asset')} {cand.get('candidate_type')} "
                 f"do_trade={decision['do_trade']} rec->intent={decision['latency']['recognition_to_order_intent_ms']}ms "
                 f"qr->submit={decision['latency']['quote_refresh_to_order_submit_ms']}ms reasons={decision['do_not_trade_reasons']}")

        if tick < int(iterations) - 1:
            sleeper(float(quote_loop_interval_ms) / 1000.0)

    summary = {
        "schema_kind": FASTPATH_SCHEMA_KIND, "generated_at": now_fn().isoformat(),
        "candidate_universe": str(candidate_universe), "universe_candidate_count": len(candidates),
        "watched_leg_count": len(watched), "mode": mode, "live_flags": live_flags, "parameters": params,
        "ticks": int(max(1, iterations)), "decisions": len(decisions),
        "discovery_runs_during_loop": discovery_runs,
        "full_scout_runs_per_tick": 0,
        "decisions_that_would_trade": sum(1 for d in decisions if d["do_trade"]),
        "decision_records": [{"trigger_id": d["trigger_id"], "asset": d["asset"], "do_trade": d["do_trade"],
                              "latency": d["latency"], "do_not_trade_reasons": d["do_not_trade_reasons"],
                              "trigger_dir": d["trigger_dir"]} for d in decisions],
        "quote_cache_path": str(cache_path), "kill_switch_present": kill_switch.exists(),
        "safety": _safety(),
    }
    _write_json(output_dir / "fast_path_run_summary.json", summary)
    return summary


def _decide_and_intent(*, cand, rec, params, live_flags, mode, used_adapters, recognized_at,
                       refresh_started, refresh_completed, output_dir, now_fn, sleeper, kill_switch, tick) -> dict[str, Any]:
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

    # --- POST-DECISION (not in measured hot path): live execution if armed, then full report + journal + markdown. ---
    execution_result = {"placed": False, "mode": mode, "fills": [], "cancels": [], "residual_exposure": [],
                        "emergency_review_required": False}
    if do_trade:
        plan = _post_decision_plan(cand, params, now_fn)
        execution_result = _execute_with_journal(
            cand, plan, params, used_adapters, trigger_id, trigger_dir, now_fn, sleeper, kill_switch)
    _write_post_decision_report(cand, params, decision_record, execution_result, trigger_dir, now_fn, mode)
    decision_record["trigger_dir"] = str(trigger_dir)
    decision_record["execution_result"] = execution_result
    return decision_record


def _execute_with_journal(cand, plan, params, used_adapters, trigger_id, trigger_dir, now_fn, sleeper, kill_switch):
    jr = start_micro_test_from_objects(candidate={**cand, "basket_legs": cand.get("basket_legs")}, plan=plan,
                                       max_total_notional=params["max_total_notional"],
                                       test_label=trigger_id, output_root=trigger_dir / "micro_test_journal", now=now_fn())
    res = _execute_live_orders(
        plan=plan, ordered_legs=_ordered_plan_legs(plan), used_adapters=used_adapters, params=params,
        trigger_id=trigger_id, trigger_dir=trigger_dir, journal_test_id=jr["test_id"],
        journal_root=trigger_dir / "micro_test_journal", now_fn=now_fn, sleeper=sleeper, kill_switch=kill_switch,
    )
    finalize_crypto_micro_test(test_id=jr["test_id"], output_root=trigger_dir / "micro_test_journal", now=now_fn())
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
    if any(str(l.get("platform") or "").lower() == "cdna" for l in cand.get("legs") or []):
        reasons.append("cdna_requires_manual_fill_first_no_confirmed_fill")
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


def _write_post_decision_report(cand, params, decision_record, execution_result, trigger_dir, now_fn, mode) -> None:
    report = {
        "schema_kind": "crypto_fast_path_trigger_report_v1", "trigger_id": decision_record["trigger_id"],
        "asset": cand.get("asset"), "candidate_type": cand.get("candidate_type"), "mode": mode,
        "do_trade": decision_record["do_trade"], "do_not_trade_reasons": decision_record["do_not_trade_reasons"],
        "latency": decision_record["latency"], "recomputed_edge": decision_record["recomputed_edge"],
        "intended_orders": decision_record["intended_orders"], "execution_result": execution_result,
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
