from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from relative_value.exhaustive_evidence_trust import exhaustive_evidence_trust_blockers, has_reference_only_flag
from relative_value.fees import FeeModel, KalshiTieredFeeModel, PolymarketConservativeFeeModel
from relative_value.same_payoff_evidence import SAME_PAYOFF_BOARD_SOURCE


BUY_YES = "BUY_YES"
BUY_NO = "BUY_NO"


@dataclass(frozen=True)
class NetEdgeConfig:
    detected_at: datetime | None = None
    max_quote_age_seconds: float = 1800.0
    min_required_edge_cents: float = 1.0
    slippage_budget_cents_per_leg: float = 0.0
    desired_quantity: float = 1.0
    fail_on_unknown_fee_model: bool = True
    conservative_unknown_fee_cents_per_leg: float = 2.0


def calculate_manual_net_edge(
    *,
    legs: list[dict[str, Any]],
    gross_payout_cents: float = 100.0,
    config: NetEdgeConfig | None = None,
    fee_models: dict[str, FeeModel] | None = None,
) -> dict[str, Any]:
    return _calculate(
        legs=legs,
        gross_payout_cents=gross_payout_cents,
        config=config or NetEdgeConfig(),
        fee_models=fee_models or _default_fee_models(),
        precheck_blockers=[],
        evidence={
            "input_type": "manual_test_fixture",
            "trusted_exact_same_payoff": False,
            "trusted_exhaustive_basket": False,
        },
    )


def calculate_exact_group_net_edge(
    *,
    pair_or_group: dict[str, Any],
    legs: list[dict[str, Any]],
    gross_payout_cents: float = 100.0,
    config: NetEdgeConfig | None = None,
    fee_models: dict[str, FeeModel] | None = None,
) -> dict[str, Any]:
    relationship = pair_or_group.get("contract_relationship") or pair_or_group.get("relationship")
    blockers = _trusted_exact_relationship_blockers(relationship)
    return _calculate(
        legs=legs,
        gross_payout_cents=gross_payout_cents,
        config=config or NetEdgeConfig(),
        fee_models=fee_models or _default_fee_models(),
        precheck_blockers=blockers,
        evidence={
            "input_type": "trusted_exact_same_payoff_group",
            "trusted_exact_same_payoff": not blockers,
            "relationship_source": relationship.get("source") if isinstance(relationship, dict) else None,
            "trusted_exhaustive_basket": False,
        },
    )


def calculate_structural_basket_net_edge(
    *,
    exhaustive_evidence: dict[str, Any] | None,
    legs: list[dict[str, Any]],
    gross_payout_cents: float = 100.0,
    config: NetEdgeConfig | None = None,
    fee_models: dict[str, FeeModel] | None = None,
) -> dict[str, Any]:
    blockers = _exhaustive_evidence_blockers(exhaustive_evidence)
    return _calculate(
        legs=legs,
        gross_payout_cents=gross_payout_cents,
        config=config or NetEdgeConfig(),
        fee_models=fee_models or _default_fee_models(),
        precheck_blockers=blockers,
        evidence={
            "input_type": "same_venue_exhaustive_basket",
            "trusted_exact_same_payoff": False,
            "trusted_exhaustive_basket": not blockers,
            "exhaustive_evidence_source": exhaustive_evidence.get("source") if isinstance(exhaustive_evidence, dict) else None,
        },
    )


def _calculate(
    *,
    legs: list[dict[str, Any]],
    gross_payout_cents: float,
    config: NetEdgeConfig,
    fee_models: dict[str, FeeModel],
    precheck_blockers: list[str],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    detected_at = config.detected_at or datetime.now(timezone.utc)
    blockers = list(precheck_blockers)
    depth_blockers: list[str] = []
    freshness_blockers: list[str] = []
    warnings: list[str] = []
    leg_results = []
    gross_cost_cents = 0.0
    taker_fees_cents = 0.0
    max_fillable_quantity: float | None = None
    fee_model_names: dict[str, str] = {}

    if not legs:
        blockers.append("missing_legs")

    for index, leg in enumerate(legs):
        venue = str(leg.get("venue") or "").lower()
        side = leg.get("side") or BUY_YES
        ob = leg.get("orderbook_enrichment") if isinstance(leg.get("orderbook_enrichment"), dict) else leg
        if has_reference_only_flag(leg) or has_reference_only_flag(ob):
            blockers.append(f"leg_{index}_reference_only_source")
        fill = _fillable_ask_cost(ob, side, config.desired_quantity)
        if fill["blockers"]:
            depth_blockers.extend(f"leg_{index}_{blocker}" for blocker in fill["blockers"])
        captured = _parse_datetime_or_none(ob.get("orderbook_captured_at"))
        age = (detected_at - captured).total_seconds() if captured else None
        if age is None:
            freshness_blockers.append(f"leg_{index}_missing_quote_timestamp")
        elif age > config.max_quote_age_seconds:
            freshness_blockers.append(f"leg_{index}_stale_quote")

        model = fee_models.get(venue)
        fee_cents = 0.0
        if model is None:
            if config.fail_on_unknown_fee_model:
                blockers.append(f"unknown_fee_model:{venue or 'missing_venue'}")
            else:
                warnings.append(f"unknown_fee_model_conservative_default:{venue or 'missing_venue'}")
                fee_cents = config.conservative_unknown_fee_cents_per_leg * config.desired_quantity
                fee_model_names[venue or "missing_venue"] = "ConservativeUnknownFee"
        else:
            fee_model_names[venue] = model.__class__.__name__
            if fill["average_price"] is not None:
                fee_cents = model.fee_for_leg(fill["average_price"]) * 100.0 * config.desired_quantity

        if fill["cost_cents"] is not None:
            gross_cost_cents += fill["cost_cents"]
        taker_fees_cents += fee_cents
        if fill["fillable_quantity"] is not None:
            max_fillable_quantity = fill["fillable_quantity"] if max_fillable_quantity is None else min(max_fillable_quantity, fill["fillable_quantity"])
        leg_results.append(
            {
                "venue": venue,
                "side": side,
                "cost_cents": _round(fill["cost_cents"]),
                "average_price": _round(fill["average_price"]),
                "fillable_quantity": _round(fill["fillable_quantity"]),
                "fee_cents": _round(fee_cents),
                "quote_age_seconds": _round(age),
                "uses_midpoint": False,
                "used_l2_ladder": fill["used_l2_ladder"],
            }
        )

    blockers.extend(depth_blockers)
    blockers.extend(freshness_blockers)
    slippage_budget_cents = config.slippage_budget_cents_per_leg * len(legs) * config.desired_quantity
    conservative_net_edge_cents = gross_payout_cents - gross_cost_cents - taker_fees_cents - slippage_budget_cents
    if conservative_net_edge_cents < config.min_required_edge_cents:
        blockers.append("conservative_net_edge_below_minimum")
    status = "NET_EDGE_REVIEW" if not blockers else "BLOCKED"
    return {
        "schema_version": 1,
        "source": "net_edge_calculator_v1",
        "status": status,
        "blockers": sorted(set(blockers)),
        "warnings": warnings,
        "gross_cost_cents": _round(gross_cost_cents),
        "gross_payout_cents": _round(gross_payout_cents),
        "taker_fees_cents": _round(taker_fees_cents),
        "slippage_budget_cents": _round(slippage_budget_cents),
        "conservative_net_edge_cents": _round(conservative_net_edge_cents),
        "conservative_net_edge_bps": _round((conservative_net_edge_cents / gross_payout_cents) * 10000.0 if gross_payout_cents else None),
        "min_required_edge_cents": _round(config.min_required_edge_cents),
        "max_fillable_quantity": _round(max_fillable_quantity),
        "depth_blockers": sorted(set(depth_blockers)),
        "freshness_blockers": sorted(set(freshness_blockers)),
        "fee_model_names": dict(sorted(fee_model_names.items())),
        "legs": leg_results,
        "evidence": evidence,
        "safety": {
            "saved_file_only": True,
            "diagnostic_only": True,
            "paper_candidate_emitted": False,
            "paper_candidate_count": 0,
            "places_orders": False,
            "uses_midpoint": False,
            "uses_executable_asks_only": True,
            "affects_evaluator_gates": False,
        },
    }


def _fillable_ask_cost(ob: dict[str, Any], side: str, desired_quantity: float) -> dict[str, Any]:
    ladder = _ask_ladder(ob, side)
    blockers: list[str] = []
    if ladder:
        remaining = desired_quantity
        cost = 0.0
        fillable = 0.0
        for price, size in ladder:
            if price is None or size is None:
                continue
            take = min(remaining, size)
            if take <= 0:
                continue
            cost += price * take * 100.0
            fillable += take
            remaining -= take
            if remaining <= 1e-12:
                break
        if fillable < desired_quantity:
            blockers.append("insufficient_l2_ask_depth")
        average = (cost / 100.0 / fillable) if fillable else None
        return {
            "cost_cents": cost if fillable else None,
            "average_price": average,
            "fillable_quantity": fillable,
            "blockers": blockers,
            "used_l2_ladder": True,
        }

    ask = _float_or_none(ob.get(_best_ask_key(side)))
    depth = _float_or_none(ob.get(_depth_key(side)))
    if ask is None:
        blockers.append("missing_executable_ask")
    if depth is None:
        blockers.append("missing_ask_depth")
    elif depth < desired_quantity:
        blockers.append("insufficient_top_of_book_depth")
    return {
        "cost_cents": ask * desired_quantity * 100.0 if ask is not None else None,
        "average_price": ask,
        "fillable_quantity": depth,
        "blockers": blockers,
        "used_l2_ladder": False,
    }


def _ask_ladder(ob: dict[str, Any], side: str) -> list[tuple[float | None, float | None]]:
    keys = ("yes_asks", "asks") if side == BUY_YES else ("no_asks",)
    raw = None
    for key in keys:
        if isinstance(ob.get(key), list):
            raw = ob[key]
            break
    if raw is None:
        orderbook = ob.get("orderbook")
        if isinstance(orderbook, dict):
            key = "yes_asks" if side == BUY_YES else "no_asks"
            raw = orderbook.get(key)
    if not isinstance(raw, list):
        return []
    ladder = []
    for item in raw:
        if isinstance(item, dict):
            ladder.append((_float_or_none(item.get("price")), _float_or_none(item.get("size"))))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            ladder.append((_float_or_none(item[0]), _float_or_none(item[1])))
    return sorted(ladder, key=lambda row: 999.0 if row[0] is None else row[0])


def _trusted_exact_relationship_blockers(relationship: Any) -> list[str]:
    if not isinstance(relationship, dict):
        return ["missing_trusted_contract_relationship"]
    blockers = []
    if relationship.get("relationship") != "EQUIVALENT":
        blockers.append("relationship_not_equivalent")
    if relationship.get("same_payoff") is not True:
        blockers.append("relationship_same_payoff_not_true")
    if relationship.get("source") != SAME_PAYOFF_BOARD_SOURCE:
        blockers.append("relationship_source_not_same_payoff_board_v1")
    if relationship.get("blocking_reasons") not in ([], None):
        blockers.append("relationship_has_blocking_reasons")
    evidence = relationship.get("same_payoff_board_evidence")
    if isinstance(evidence, dict) and evidence.get("passed") is False:
        blockers.append("same_payoff_board_evidence_not_passed")
    return blockers


def _exhaustive_evidence_blockers(evidence: dict[str, Any] | None) -> list[str]:
    if not isinstance(evidence, dict):
        return ["missing_exhaustive_evidence"]
    blockers = []
    if has_reference_only_flag(evidence):
        blockers.append("reference_only_source")
    blockers.extend(
        exhaustive_evidence_trust_blockers(
            source=evidence.get("source") or evidence.get("evidence_source"),
            is_exhaustive=(
                evidence.get("is_exhaustive") is True
                or evidence.get("all_outcomes_included") is True
                or evidence.get("exhaustive") is True
            ),
            venue_native=evidence.get("venue_native") is True,
            trusted_local_manifest=evidence.get("trusted_local_manifest") is True,
        )
    )
    return blockers


def _best_ask_key(side: str) -> str:
    return "best_no_ask" if side == BUY_NO else "best_ask"


def _depth_key(side: str) -> str:
    return "depth_at_best_no_ask" if side == BUY_NO else "depth_at_best_ask"


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
