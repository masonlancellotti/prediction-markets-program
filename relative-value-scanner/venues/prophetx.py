from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROPHETX_RESEARCH_SCHEMA_KIND = "prophetx_research_snapshot_v1"

PROPHETX_REQUIRED_BLOCKERS = (
    "live_transport_not_implemented",
    "api_access_not_verified",
    "account_permission_not_verified",
    "market_mapping_not_reviewed",
    "settlement_wording_not_normalized",
    "fee_commission_model_not_reviewed",
    "depth_units_not_normalized",
    "quote_freshness_not_reviewed",
    "venue_restrictions_not_reviewed",
    "not_integrated_with_matcher_or_evaluator",
)


def load_prophetx_research_fixtures(
    *,
    markets_path: Path,
    orderbook_path: Path,
    settlement_path: Path,
    fee_path: Path,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    markets_payload = _load_fixture_object(markets_path)
    orderbook_payload = _load_fixture_object(orderbook_path)
    settlement_payload = _load_fixture_object(settlement_path)
    fee_payload = _load_fixture_object(fee_path)
    return build_prophetx_research_snapshot(
        markets_payload=markets_payload,
        orderbook_payload=orderbook_payload,
        settlement_payload=settlement_payload,
        fee_payload=fee_payload,
        captured_at=captured_at,
    )


def build_prophetx_research_snapshot(
    *,
    markets_payload: dict[str, Any],
    orderbook_payload: dict[str, Any],
    settlement_payload: dict[str, Any],
    fee_payload: dict[str, Any],
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    timestamp = captured_at or datetime.now(timezone.utc)
    markets = _dict_rows(markets_payload.get("markets"))
    orderbooks_by_id = _rows_by_market_id(orderbook_payload.get("orderbooks"))
    settlements_by_id = _rows_by_market_id(settlement_payload.get("settlements"))
    fees_by_id = _rows_by_market_id(fee_payload.get("fees"))
    research_markets = [
        _research_market(
            market,
            orderbook=orderbooks_by_id.get(str(market.get("market_id") or "")),
            settlement=settlements_by_id.get(str(market.get("market_id") or "")),
            fee=fees_by_id.get(str(market.get("market_id") or "")),
        )
        for market in markets
    ]
    return {
        "schema_version": 1,
        "schema_kind": PROPHETX_RESEARCH_SCHEMA_KIND,
        "source": "prophetx_research",
        "source_id": "prophetx",
        "source_type": "EXECUTABLE_VENUE",
        "implementation_status": "PLANNED_NOT_IMPLEMENTED",
        "permission": "FIXTURE_RESEARCH_ONLY",
        "captured_at": timestamp.isoformat(),
        "live_fetch_attempted": False,
        "live_fetch_succeeded": False,
        "is_executable": False,
        "execution_allowed_in_project_now": False,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "market_count": len(markets),
        "orderbook_count": len(orderbooks_by_id),
        "settlement_count": len(settlements_by_id),
        "fee_count": len(fees_by_id),
        "research_market_count": len(research_markets),
        "unresolved_blockers": list(PROPHETX_REQUIRED_BLOCKERS),
        "research_markets": research_markets,
        "notes": (
            "Fixture-backed ProphetX research snapshot only. It is not executable schema-v1, "
            "not matcher/evaluator integrated, and not paper-candidate eligible."
        ),
    }


def _research_market(
    market: dict[str, Any],
    *,
    orderbook: dict[str, Any] | None,
    settlement: dict[str, Any] | None,
    fee: dict[str, Any] | None,
) -> dict[str, Any]:
    orderbook = orderbook or {}
    settlement = settlement or {}
    fee = fee or {}
    bid_depth = _number_or_none(orderbook.get("bid_depth"))
    ask_depth = _number_or_none(orderbook.get("ask_depth"))
    displayed_depth = None
    if bid_depth is not None or ask_depth is not None:
        displayed_depth = {
            "bid_depth": bid_depth,
            "ask_depth": ask_depth,
            "unit": orderbook.get("depth_unit") or "fixture_displayed_contracts_unreviewed",
        }
    return {
        "source_id": "prophetx",
        "market_id": market.get("market_id"),
        "event_id": market.get("event_id"),
        "title": market.get("title"),
        "question": market.get("question") or market.get("title"),
        "event_category": market.get("event_category"),
        "sport": market.get("sport"),
        "league": market.get("league"),
        "market_type": market.get("market_type"),
        "outcome_names": _list_or_empty(market.get("outcome_names")),
        "status": market.get("status"),
        "start_time": market.get("start_time"),
        "close_time": market.get("close_time"),
        "settlement_time": settlement.get("settlement_time"),
        "settlement_wording": settlement.get("settlement_wording"),
        "settlement_source": settlement.get("settlement_source"),
        "event_window": settlement.get("event_window"),
        "outcome_terms": settlement.get("outcome_terms"),
        "void_or_cancellation_rules": settlement.get("void_or_cancellation_rules"),
        "best_bid": _number_or_none(orderbook.get("best_bid")),
        "best_ask": _number_or_none(orderbook.get("best_ask")),
        "displayed_depth": displayed_depth,
        "market_data_timestamp": orderbook.get("market_data_timestamp") or orderbook.get("quote_timestamp"),
        "quote_timestamp": orderbook.get("quote_timestamp") or orderbook.get("market_data_timestamp"),
        "quote_timestamp_alias_of": "market_data_timestamp",
        "delayed_or_realtime_status": orderbook.get("delayed_or_realtime_status"),
        "fee_commission_status": fee.get("fee_commission_status") or "missing_or_unreviewed",
        "fee_schedule_version": fee.get("fee_schedule_version"),
        "maker_fee": _number_or_none(fee.get("maker_fee")),
        "taker_fee": _number_or_none(fee.get("taker_fee")),
        "other_venue_fees": fee.get("other_venue_fees"),
        "venue_restriction_notes": market.get("venue_restriction_notes"),
        "is_executable": False,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "execution_allowed_in_project_now": False,
        "unresolved_blockers": list(PROPHETX_REQUIRED_BLOCKERS),
    }


def _load_fixture_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"fixture must contain a JSON object: {path}")
    return payload


def _dict_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _rows_by_market_id(value: Any) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("market_id")): row
        for row in _dict_rows(value)
        if str(row.get("market_id") or "").strip()
    }


def _list_or_empty(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _number_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
