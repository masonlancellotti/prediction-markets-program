from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


IBKR_FORECASTEX_RESEARCH_SCHEMA_KIND = "ibkr_forecastex_research_snapshot_v1"

IBKR_FORECASTEX_REQUIRED_BLOCKERS = (
    "live_transport_not_implemented",
    "account_permission_not_verified",
    "instrument_mapping_not_reviewed",
    "settlement_wording_not_normalized",
    "fee_commission_model_not_reviewed",
    "quote_freshness_not_reviewed",
    "not_integrated_with_matcher_or_evaluator",
)


def load_ibkr_forecastex_research_fixtures(
    *,
    instruments_path: Path,
    quotes_path: Path,
    settlement_path: Path,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    instruments_payload = _load_fixture_object(instruments_path)
    quotes_payload = _load_fixture_object(quotes_path)
    settlement_payload = _load_fixture_object(settlement_path)
    return build_ibkr_forecastex_research_snapshot(
        instruments_payload=instruments_payload,
        quotes_payload=quotes_payload,
        settlement_payload=settlement_payload,
        captured_at=captured_at,
    )


def build_ibkr_forecastex_research_snapshot(
    *,
    instruments_payload: dict[str, Any],
    quotes_payload: dict[str, Any],
    settlement_payload: dict[str, Any],
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    timestamp = captured_at or datetime.now(timezone.utc)
    instruments = _dict_rows(instruments_payload.get("instruments"))
    quotes_by_id = _rows_by_instrument_id(quotes_payload.get("quotes"))
    settlements_by_id = _rows_by_instrument_id(settlement_payload.get("settlements"))
    research_markets = [
        _research_market(
            instrument,
            quote=quotes_by_id.get(str(instrument.get("instrument_id") or "")),
            settlement=settlements_by_id.get(str(instrument.get("instrument_id") or "")),
        )
        for instrument in instruments
    ]
    return {
        "schema_version": 1,
        "schema_kind": IBKR_FORECASTEX_RESEARCH_SCHEMA_KIND,
        "source": "ibkr_forecastex_research",
        "source_id": "forecastex_ibkr",
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
        "instrument_count": len(instruments),
        "quote_count": len(quotes_by_id),
        "settlement_count": len(settlements_by_id),
        "research_market_count": len(research_markets),
        "unresolved_blockers": list(IBKR_FORECASTEX_REQUIRED_BLOCKERS),
        "research_markets": research_markets,
        "notes": (
            "Fixture-backed IBKR / ForecastEx research snapshot only. It is not executable schema-v1, "
            "not matcher/evaluator integrated, and not paper-candidate eligible."
        ),
    }


def _research_market(
    instrument: dict[str, Any],
    *,
    quote: dict[str, Any] | None,
    settlement: dict[str, Any] | None,
) -> dict[str, Any]:
    quote = quote or {}
    settlement = settlement or {}
    bid_size = _number_or_none(quote.get("bid_size"))
    ask_size = _number_or_none(quote.get("ask_size"))
    displayed_depth = None
    if bid_size is not None or ask_size is not None:
        displayed_depth = {
            "bid_size": bid_size,
            "ask_size": ask_size,
            "unit": "fixture_displayed_contracts_unreviewed",
        }
    return {
        "source_id": "forecastex_ibkr",
        "instrument_id": instrument.get("instrument_id"),
        "conid": instrument.get("conid"),
        "symbol": instrument.get("symbol"),
        "exchange": instrument.get("exchange"),
        "trading_class": instrument.get("trading_class"),
        "currency": instrument.get("currency"),
        "contract_title": instrument.get("contract_title"),
        "question": instrument.get("question") or instrument.get("contract_title"),
        "event_category": instrument.get("event_category"),
        "expiration": instrument.get("expiration"),
        "settlement_wording": settlement.get("settlement_wording"),
        "settlement_source": settlement.get("settlement_source"),
        "close_time": settlement.get("close_time") or instrument.get("expiration"),
        "settlement_time": settlement.get("settlement_time"),
        "contract_multiplier": settlement.get("contract_multiplier"),
        "best_bid": _number_or_none(quote.get("bid")),
        "best_ask": _number_or_none(quote.get("ask")),
        "displayed_depth": displayed_depth,
        "quote_timestamp": quote.get("quote_timestamp"),
        "delayed_or_realtime_status": quote.get("delayed_or_realtime_status"),
        "fee_commission_status": settlement.get("fee_commission_status") or "missing_or_unreviewed",
        "commission_schedule_version": settlement.get("commission_schedule_version"),
        "is_executable": False,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "execution_allowed_in_project_now": False,
        "unresolved_blockers": list(IBKR_FORECASTEX_REQUIRED_BLOCKERS),
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


def _rows_by_instrument_id(value: Any) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("instrument_id")): row
        for row in _dict_rows(value)
        if str(row.get("instrument_id") or "").strip()
    }


def _number_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
