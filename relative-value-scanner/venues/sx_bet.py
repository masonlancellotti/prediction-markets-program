from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


SX_BET_RESEARCH_SCHEMA_KIND = "sx_bet_research_snapshot_v1"
SX_BET_PERCENTAGE_ODDS_SCALE = 10**20
SX_BET_USDC_SCALE = 10**6


def build_sx_bet_research_snapshot(
    raw_payload: dict[str, Any],
    *,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    timestamp = captured_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("captured_at must include timezone information")
    markets = _list_or_empty(raw_payload.get("markets"))
    orders = _list_or_empty(raw_payload.get("orders"))
    orders_by_market = _orders_by_market_hash(orders)
    research_markets: list[dict[str, Any]] = []
    skipped_market_count = 0
    for market in markets:
        if not isinstance(market, dict):
            skipped_market_count += 1
            continue
        market_hash = _string_or_none(market.get("marketHash"))
        if market_hash is None:
            skipped_market_count += 1
            continue
        research_markets.append(
            _research_market(
                market,
                orders_by_market.get(market_hash, []),
                captured_at=timestamp,
            )
        )
    return {
        "schema_version": 1,
        "schema_kind": SX_BET_RESEARCH_SCHEMA_KIND,
        "source": "sx_bet_research",
        "source_id": "sx_bet",
        "source_type": "EXECUTABLE_VENUE",
        "implementation_status": "PLANNED_NOT_IMPLEMENTED",
        "permission": "READ_ONLY_RESEARCH",
        "is_executable": False,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "captured_at": timestamp.isoformat(),
        "market_count": len(markets),
        "research_market_count": len(research_markets),
        "skipped_market_count": skipped_market_count,
        "order_count": len(orders),
        "research_markets": research_markets,
        "readiness_requirements": [
            "implemented_read_only_adapter",
            "real_bid_ask_depth_confirmed",
            "quote_freshness_policy",
            "fee_model",
            "settlement_wording_normalization",
            "strict_same_payoff_relationship_classification",
            "venue_restrictions_review",
            "no_wallet_private_key_signing_or_execution_logic",
        ],
        "disclaimer": (
            "SX Bet feasibility snapshot only. Not executable schema-v1, not a scanner input, "
            "and not eligible for PAPER_CANDIDATE or candidate-pair creation."
        ),
    }


def load_sx_bet_research_fixture(path: Path, *, captured_at: datetime | None = None) -> dict[str, Any]:
    raw_payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_payload, dict):
        raise ValueError("SX Bet research fixture must be a JSON object")
    return build_sx_bet_research_snapshot(raw_payload, captured_at=captured_at)


def _research_market(
    market: dict[str, Any],
    orders: Sequence[dict[str, Any]],
    *,
    captured_at: datetime,
) -> dict[str, Any]:
    outcome_one = _string_or_none(market.get("outcomeOneName"))
    outcome_two = _string_or_none(market.get("outcomeTwoName"))
    outcome_void = _string_or_none(market.get("outcomeVoidName"))
    orderbook = _research_orderbook(orders)
    return {
        "market_hash": _string_or_none(market.get("marketHash")),
        "event_title": _string_or_none(market.get("eventName") or market.get("gameLabel") or market.get("eventLabel")),
        "league": _string_or_none(market.get("leagueLabel") or market.get("league")),
        "sport": _string_or_none(market.get("sportLabel") or market.get("sport")),
        "market_type": market.get("type"),
        "line": _number_or_none(market.get("line")),
        "main_line": bool(market.get("mainLine")) if market.get("mainLine") is not None else None,
        "status": _string_or_none(market.get("status")),
        "starts_at": _string_or_none(market.get("gameTime") or market.get("startTime") or market.get("startsAt")),
        "outcome_one_name": outcome_one,
        "outcome_two_name": outcome_two,
        "outcome_void_name": outcome_void,
        "settlement_metadata": {
            "settlement_source": _string_or_none(market.get("settlementSource")),
            "settlement_rule": _string_or_none(market.get("settlementRule")),
            "void_rule": outcome_void,
            "raw_status": market.get("status"),
        },
        "fee_metadata": {
            "fee_model_status": "not_normalized",
            "source_note": "SX Bet docs describe 0% single-bet fees and parlay fees, but this project has no reviewed SX Bet fee model.",
        },
        "restrictions": {
            "requires_wallet_or_private_key_for_execution": True,
            "execution_allowed_in_project_now": False,
            "candidate_pair_allowed": False,
        },
        "quote_captured_at": captured_at.isoformat(),
        "research_orderbook": orderbook,
        "raw": market,
    }


def _research_orderbook(orders: Sequence[dict[str, Any]]) -> dict[str, Any]:
    outcome_one_levels: list[dict[str, Any]] = []
    outcome_two_levels: list[dict[str, Any]] = []
    skipped_order_count = 0
    for order in orders:
        if not isinstance(order, dict):
            skipped_order_count += 1
            continue
        parsed = _research_order_level(order)
        if parsed is None:
            skipped_order_count += 1
            continue
        if order.get("isMakerBettingOutcomeOne") is False:
            outcome_one_levels.append(parsed)
        elif order.get("isMakerBettingOutcomeOne") is True:
            outcome_two_levels.append(parsed)
        else:
            skipped_order_count += 1
    outcome_one_levels.sort(key=lambda row: row["taker_price"])
    outcome_two_levels.sort(key=lambda row: row["taker_price"])
    return {
        "order_count": len(orders),
        "skipped_order_count": skipped_order_count,
        "outcome_one_taker_levels": outcome_one_levels,
        "outcome_two_taker_levels": outcome_two_levels,
        "best_taker_price_outcome_one": _best_price(outcome_one_levels),
        "best_taker_price_outcome_two": _best_price(outcome_two_levels),
        "depth_usdc_at_best_outcome_one": _depth_at_best(outcome_one_levels),
        "depth_usdc_at_best_outcome_two": _depth_at_best(outcome_two_levels),
        "unit_warning": "Depth is maker stake in USDC, not normalized prediction-market contracts.",
    }


def _research_order_level(order: dict[str, Any]) -> dict[str, Any] | None:
    maker_odds = _scaled_int_to_float(order.get("percentageOdds"), SX_BET_PERCENTAGE_ODDS_SCALE)
    total_size = _scaled_int_to_float(order.get("totalBetSize"), SX_BET_USDC_SCALE)
    fill_amount = _scaled_int_to_float(order.get("fillAmount") or 0, SX_BET_USDC_SCALE)
    if maker_odds is None or total_size is None or fill_amount is None:
        return None
    available_size = max(0.0, total_size - fill_amount)
    taker_price = 1.0 - maker_odds
    if taker_price < 0.0 or taker_price > 1.0:
        return None
    return {
        "order_hash": _string_or_none(order.get("orderHash")),
        "maker_odds": round(maker_odds, 6),
        "taker_price": round(taker_price, 6),
        "available_maker_stake_usdc": round(available_size, 6),
        "is_maker_betting_outcome_one": order.get("isMakerBettingOutcomeOne"),
        "raw": order,
    }


def _orders_by_market_hash(orders: Sequence[Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for order in orders:
        if not isinstance(order, dict):
            continue
        market_hash = _string_or_none(order.get("marketHash"))
        if market_hash is None:
            continue
        result.setdefault(market_hash, []).append(order)
    return result


def _best_price(levels: Sequence[dict[str, Any]]) -> float | None:
    if not levels:
        return None
    return levels[0]["taker_price"]


def _depth_at_best(levels: Sequence[dict[str, Any]]) -> float | None:
    if not levels:
        return None
    best = levels[0]["taker_price"]
    return round(sum(level["available_maker_stake_usdc"] for level in levels if level["taker_price"] == best), 6)


def _list_or_empty(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:
        return None
    return parsed


def _scaled_int_to_float(value: Any, scale: int) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed / scale
