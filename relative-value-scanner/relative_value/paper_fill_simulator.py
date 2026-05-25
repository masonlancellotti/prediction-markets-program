from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.exhaustive_evidence_trust import has_reference_only_flag
from relative_value.fees import FeeModel, KalshiTieredFeeModel, PolymarketConservativeFeeModel


GATED_STRUCTURAL_STATUSES = {"STRUCTURAL_BASKET_REVIEW", "STOP_FOR_REVIEW"}
GATED_EXACT_ACTIONS = {"PAPER_CANDIDATE"}
BUY_YES = "BUY_YES"
BUY_NO = "BUY_NO"
SELL_YES = "SELL_YES"
SELL_NO = "SELL_NO"


def simulate_paper_fill_journal(
    *,
    input_payload: dict[str, Any],
    generated_at: datetime | None = None,
    desired_quantity: float = 1.0,
    max_quote_age_seconds: float = 1800.0,
    slippage_budget_cents_per_leg: float = 0.0,
    fee_models: dict[str, FeeModel] | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    models = fee_models or _default_fee_models()
    rows = []
    for row in _candidate_rows(input_payload):
        rows.append(
            _simulate_row(
                row=row,
                generated_at=generated,
                desired_quantity=desired_quantity,
                max_quote_age_seconds=max_quote_age_seconds,
                slippage_budget_cents_per_leg=slippage_budget_cents_per_leg,
                fee_models=models,
            )
        )
    status_counts = Counter(row["status"] for row in rows)
    return {
        "schema_version": 1,
        "source": "saved_file_paper_fill_simulator_v1",
        "generated_at": generated.isoformat(),
        "summary": {
            "input_row_count": len(rows),
            "simulated_fill_count": sum(1 for row in rows if row["status"] == "paper_simulation"),
            "blocked_count": sum(1 for row in rows if row["status"] == "blocked"),
            "paper_candidate_count_created": 0,
            "status_counts": dict(sorted(status_counts.items())),
        },
        "journal": rows,
        "safety": {
            "saved_file_only": True,
            "review_only": True,
            "paper_candidate_created": False,
            "places_real_trades": False,
            "uses_midpoint": False,
            "uses_executable_depth_only": True,
            "saved_reports_only": True,
            "affects_evaluator_gates": False,
        },
    }


def simulate_paper_fill_journal_files(
    *,
    input_path: Path,
    json_output: Path,
    markdown_output: Path,
    desired_quantity: float = 1.0,
    max_quote_age_seconds: float = 1800.0,
    slippage_budget_cents_per_leg: float = 0.0,
) -> dict[str, Any]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    journal = simulate_paper_fill_journal(
        input_payload=payload,
        desired_quantity=desired_quantity,
        max_quote_age_seconds=max_quote_age_seconds,
        slippage_budget_cents_per_leg=slippage_budget_cents_per_leg,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(journal, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_paper_fill_journal_markdown(journal), encoding="utf-8")
    return journal


def render_paper_fill_journal_markdown(journal: dict[str, Any]) -> str:
    lines = [
        "# Paper Fill Journal",
        "",
        "Saved-file paper_simulation for upstream-gated review rows only. No live action is performed.",
        "",
        "| Source candidate | Type | Status | Quantity | Fees cents | Net edge cents | Blockers |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for row in journal.get("journal", []):
        lines.append(
            "| {source} | {kind} | {status} | {qty:.4f} | {fees:.4f} | {edge:.4f} | {blockers} |".format(
                source=str(row.get("source_candidate_id") or "").replace("|", "/"),
                kind=str(row.get("candidate_type") or "").replace("|", "/"),
                status=row.get("status") or "",
                qty=float(row.get("simulated_quantity") or 0.0),
                fees=float(row.get("conservative_fee_cents") or 0.0),
                edge=float(row.get("conservative_net_edge_cents") or 0.0),
                blockers="; ".join(row.get("blockers") or []).replace("|", "/"),
            )
        )
    return "\n".join(lines) + "\n"


def _simulate_row(
    *,
    row: dict[str, Any],
    generated_at: datetime,
    desired_quantity: float,
    max_quote_age_seconds: float,
    slippage_budget_cents_per_leg: float,
    fee_models: dict[str, FeeModel],
) -> dict[str, Any]:
    candidate_type = _candidate_type(row)
    blockers = _gating_blockers(row, candidate_type)
    fill_prices = []
    depth_used = []
    replay_ids = []
    fee_names = {}
    gross_cost_cents = 0.0
    fees_cents = 0.0
    min_quantity: float | None = None
    legs = _legs(row, candidate_type)
    if not legs:
        blockers.append("missing_simulation_legs")
    for index, leg in enumerate(legs):
        venue = str(leg.get("venue") or row.get("venue") or "").lower()
        side = leg.get("side") or BUY_YES
        ob = leg.get("orderbook_enrichment") if isinstance(leg.get("orderbook_enrichment"), dict) else leg
        if has_reference_only_flag(leg) or has_reference_only_flag(ob):
            blockers.append(f"leg_{index}_reference_only_source")
        fill = _walk_book(ob, side, desired_quantity)
        if fill["blockers"]:
            blockers.extend(f"leg_{index}_{blocker}" for blocker in fill["blockers"])
        captured = _parse_datetime_or_none(ob.get("orderbook_captured_at"))
        age = (generated_at - captured).total_seconds() if captured else None
        if age is None:
            blockers.append(f"leg_{index}_missing_quote_timestamp")
        elif age > max_quote_age_seconds:
            blockers.append(f"leg_{index}_stale_quote")
        model = fee_models.get(venue)
        if model is None:
            blockers.append(f"leg_{index}_missing_fee_model")
        elif fill["average_price"] is not None:
            fee_names[venue] = model.__class__.__name__
            fees_cents += model.fee_for_leg(fill["average_price"]) * 100.0 * fill["quantity"]
        gross_cost_cents += fill["cost_cents"]
        min_quantity = fill["quantity"] if min_quantity is None else min(min_quantity, fill["quantity"])
        fill_prices.append(
            {
                "venue": venue,
                "side": side,
                "average_price": _round(fill["average_price"]),
                "levels": fill["levels"],
                "uses_midpoint": False,
            }
        )
        depth_used.append({"venue": venue, "side": side, "quantity": _round(fill["quantity"])})
        replay_ids.append(ob.get("snapshot_id") or ob.get("orderbook_snapshot_id") or leg.get("market_id") or leg.get("ticker"))
    simulated_quantity = min(desired_quantity, min_quantity or 0.0)
    slippage = slippage_budget_cents_per_leg * len(legs) * simulated_quantity
    payout = float(row.get("gross_payout_cents") or 100.0) * simulated_quantity
    net_edge = payout - gross_cost_cents - fees_cents - slippage
    if net_edge <= 0:
        blockers.append("conservative_net_edge_not_positive")
    unique_blockers = sorted({blocker for blocker in blockers if blocker})
    return {
        "source_candidate_id": row.get("source_candidate_id") or row.get("candidate_id") or row.get("group_id") or row.get("id"),
        "candidate_type": candidate_type,
        "status": "paper_simulation" if not unique_blockers else "blocked",
        "simulated_quantity": _round(simulated_quantity),
        "fill_prices": fill_prices,
        "cumulative_depth_used": depth_used,
        "fee_model_names": dict(sorted(fee_names.items())),
        "conservative_fee_cents": _round(fees_cents),
        "gross_cost_cents": _round(gross_cost_cents),
        "slippage_budget_cents": _round(slippage),
        "conservative_net_edge_cents": _round(net_edge),
        "blockers": unique_blockers,
        "replay_snapshot_ids": [value for value in replay_ids if value],
        "generated_at": generated_at.isoformat(),
        "paper_candidate_created": False,
        "uses_midpoint": False,
        "review_only": True,
    }


def _candidate_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("journal", "rows", "ledger", "candidates"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    if isinstance(payload.get("candidate"), dict):
        return [payload["candidate"]]
    return []


def _candidate_type(row: dict[str, Any]) -> str:
    if row.get("candidate_type"):
        return str(row["candidate_type"])
    if row.get("outcomes") is not None or row.get("evidence", {}).get("venue_native") is not None:
        return "structural_basket"
    return "exact_same_payoff"


def _gating_blockers(row: dict[str, Any], candidate_type: str) -> list[str]:
    blockers = []
    if has_reference_only_flag(row):
        blockers.append("reference_only_source")
    if candidate_type == "structural_basket":
        if row.get("status") not in GATED_STRUCTURAL_STATUSES:
            blockers.append("ungated_structural_basket_row")
        return blockers
    if row.get("action") not in GATED_EXACT_ACTIONS and row.get("status") not in GATED_EXACT_ACTIONS:
        blockers.append("ungated_exact_same_payoff_row")
    return blockers


def _legs(row: dict[str, Any], candidate_type: str) -> list[dict[str, Any]]:
    if isinstance(row.get("legs"), list):
        return [leg for leg in row["legs"] if isinstance(leg, dict)]
    if candidate_type == "structural_basket":
        return [
            {
                "venue": row.get("venue"),
                "market_id": outcome.get("market_id"),
                "side": BUY_YES,
                "orderbook_enrichment": _outcome_orderbook(outcome),
            }
            for outcome in row.get("outcomes", [])
            if isinstance(outcome, dict)
        ]
    return []


def _outcome_orderbook(outcome: dict[str, Any]) -> dict[str, Any]:
    if isinstance(outcome.get("orderbook_enrichment"), dict):
        return outcome["orderbook_enrichment"]
    return {
        "best_ask": outcome.get("best_ask"),
        "depth_at_best_ask": outcome.get("depth_at_best_ask"),
        "orderbook_captured_at": outcome.get("orderbook_captured_at"),
        "snapshot_id": outcome.get("snapshot_id"),
    }


def _walk_book(ob: dict[str, Any], side: str, desired_quantity: float) -> dict[str, Any]:
    ladder = _ladder(ob, side)
    blockers = []
    levels = []
    remaining = desired_quantity
    cost = 0.0
    quantity = 0.0
    for price, size in ladder:
        if price is None or size is None:
            continue
        take = min(remaining, size)
        if take <= 0:
            continue
        levels.append({"price": _round(price), "quantity": _round(take)})
        cost += price * take * 100.0
        quantity += take
        remaining -= take
        if remaining <= 1e-12:
            break
    if not ladder:
        blockers.append("missing_executable_depth")
    elif quantity < desired_quantity:
        blockers.append("insufficient_executable_depth")
    return {
        "cost_cents": cost,
        "quantity": quantity,
        "average_price": cost / 100.0 / quantity if quantity else None,
        "levels": levels,
        "blockers": blockers,
    }


def _ladder(ob: dict[str, Any], side: str) -> list[tuple[float | None, float | None]]:
    key_options = {
        BUY_YES: ("yes_asks", "asks"),
        BUY_NO: ("no_asks",),
        SELL_YES: ("yes_bids", "bids"),
        SELL_NO: ("no_bids",),
    }.get(side, ("yes_asks", "asks"))
    raw = None
    for key in key_options:
        if isinstance(ob.get(key), list):
            raw = ob[key]
            break
    if raw is None and side in {BUY_YES, BUY_NO}:
        price_key = "best_no_ask" if side == BUY_NO else "best_ask"
        depth_key = "depth_at_best_no_ask" if side == BUY_NO else "depth_at_best_ask"
        if ob.get(price_key) is not None and ob.get(depth_key) is not None:
            raw = [[ob.get(price_key), ob.get(depth_key)]]
    if raw is None and side in {SELL_YES, SELL_NO}:
        price_key = "best_no_bid" if side == SELL_NO else "best_bid"
        depth_key = "depth_at_best_no_bid" if side == SELL_NO else "depth_at_best_bid"
        if ob.get(price_key) is not None and ob.get(depth_key) is not None:
            raw = [[ob.get(price_key), ob.get(depth_key)]]
    if not isinstance(raw, list):
        return []
    ladder = []
    for item in raw:
        if isinstance(item, dict):
            ladder.append((_float_or_none(item.get("price")), _float_or_none(item.get("size"))))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            ladder.append((_float_or_none(item[0]), _float_or_none(item[1])))
    reverse = side in {SELL_YES, SELL_NO}
    return sorted(ladder, key=lambda item: -1.0 if item[0] is None else item[0], reverse=reverse)


def _parse_datetime_or_none(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 6)


def _default_fee_models() -> dict[str, FeeModel]:
    return {
        "kalshi": KalshiTieredFeeModel(),
        "polymarket": PolymarketConservativeFeeModel(),
    }
