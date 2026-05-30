from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_KIND = "cdna_manual_fill_log_v1"
FILL_RECORD_SCHEMA_KIND = "cdna_manual_fill_record_v1"
UPDATE_SCHEMA_KIND = "cdna_manual_fill_log_update_v1"

FORBIDDEN_FIELD_KEYS = {
    "fiat_balance",
    "open_position",
    "account",
    "account_id",
    "balance",
    "position",
    "order_history",
    "cookie",
    "cookies",
    "session",
    "token",
    "auth",
    "authorization",
    "api_key",
    "secret",
    "private_key",
    "credential",
}


def build_cdna_fill_record(
    *,
    event_key: str,
    market_family: str,
    team: str,
    side: str,
    contract_id: str,
    symbol: str,
    requested_quantity: float,
    filled_quantity: float,
    filled_price: float,
    fee_per_contract: float,
    filled_at: str,
    source_note: str = "",
    time_to_fill_seconds: float | None = None,
) -> dict[str, Any]:
    side_label = str(side).strip().upper()
    requested = _float_or_none(requested_quantity)
    filled = _float_or_none(filled_quantity)
    price = _float_or_none(filled_price)
    fee = _float_or_none(fee_per_contract)
    if requested is None or filled is None or price is None or fee is None:
        raise ValueError("requested_quantity, filled_quantity, filled_price, and fee_per_contract must be numeric")
    if side_label not in {"YES", "NO"}:
        raise ValueError("side must be YES or NO")
    if requested < 0 or filled < 0:
        raise ValueError("quantities must be non-negative")
    if filled > requested:
        raise ValueError("filled_quantity cannot exceed requested_quantity")
    if not 0.0 <= price <= 1.0:
        raise ValueError("filled_price must be in [0, 1]")
    if fee < 0:
        raise ValueError("fee_per_contract must be non-negative")
    residual = round(requested - filled, 8)
    return {
        "schema_kind": FILL_RECORD_SCHEMA_KIND,
        "recorded_by_operator_manually": True,
        "event_key": str(event_key),
        "market_family": str(market_family),
        "team": str(team),
        "side": side_label,
        "contract_id": str(contract_id),
        "symbol": str(symbol),
        "requested_quantity": requested,
        "filled_quantity": filled,
        "partial": filled < requested,
        "filled_price_per_contract": price,
        "fill_fee_per_contract": fee,
        "all_in_filled_cost": round(filled * (price + fee), 8),
        "time_to_fill_seconds": _float_or_none(time_to_fill_seconds),
        "filled_at": str(filled_at),
        "source_note": str(source_note),
        "residual_unhedged_cdna_quantity": residual,
    }


def append_cdna_fill_record(fill_log: Path, record: dict[str, Any]) -> dict[str, Any]:
    errors = validate_cdna_fill_record(record)
    if errors:
        return {
            "schema_kind": UPDATE_SCHEMA_KIND,
            "record_written": False,
            "validation_errors": errors,
            "fill_log": str(fill_log),
            "records_count": _existing_record_count(fill_log),
            "safety": _safety_block(),
        }

    payload = load_cdna_fill_log(fill_log)
    records = payload.setdefault("records", [])
    records.append(record)
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    fill_log.parent.mkdir(parents=True, exist_ok=True)
    fill_log.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "schema_kind": UPDATE_SCHEMA_KIND,
        "record_written": True,
        "validation_errors": [],
        "fill_log": str(fill_log),
        "records_count": len(records),
        "record": record,
        "safety": _safety_block(),
    }


def record_cdna_fill_file(
    *,
    fill_log: Path,
    event_key: str,
    market_family: str,
    team: str,
    side: str,
    contract_id: str,
    symbol: str,
    requested_quantity: float,
    filled_quantity: float,
    filled_price: float,
    fee_per_contract: float,
    filled_at: str,
    source_note: str = "",
    time_to_fill_seconds: float | None = None,
) -> dict[str, Any]:
    record = build_cdna_fill_record(
        event_key=event_key,
        market_family=market_family,
        team=team,
        side=side,
        contract_id=contract_id,
        symbol=symbol,
        requested_quantity=requested_quantity,
        filled_quantity=filled_quantity,
        filled_price=filled_price,
        fee_per_contract=fee_per_contract,
        filled_at=filled_at,
        source_note=source_note,
        time_to_fill_seconds=time_to_fill_seconds,
    )
    return append_cdna_fill_record(fill_log, record)


def load_cdna_fill_log(fill_log: Path | None) -> dict[str, Any]:
    if fill_log is None or not fill_log.exists():
        return {
            "schema_kind": SCHEMA_KIND,
            "diagnostic_only": True,
            "manual_records_only": True,
            "records": [],
        }
    payload = json.loads(fill_log.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return {
            "schema_kind": SCHEMA_KIND,
            "diagnostic_only": True,
            "manual_records_only": True,
            "records": payload,
        }
    if isinstance(payload, dict):
        records = payload.get("records")
        if isinstance(records, list):
            return payload
        if payload.get("schema_kind") == FILL_RECORD_SCHEMA_KIND:
            return {
                "schema_kind": SCHEMA_KIND,
                "diagnostic_only": True,
                "manual_records_only": True,
                "records": [payload],
            }
    return {
        "schema_kind": SCHEMA_KIND,
        "diagnostic_only": True,
        "manual_records_only": True,
        "records": [],
        "warnings": [{"reason": "invalid_fill_log_shape", "path": str(fill_log)}],
    }


def validate_cdna_fill_record(record: dict[str, Any]) -> list[str]:
    forbidden = _forbidden_keys(record)
    if forbidden:
        return [f"forbidden_field_present:{key}" for key in sorted(forbidden)]
    required = [
        "event_key",
        "market_family",
        "team",
        "side",
        "contract_id",
        "symbol",
        "requested_quantity",
        "filled_quantity",
        "filled_price_per_contract",
        "fill_fee_per_contract",
        "filled_at",
    ]
    missing = [key for key in required if _blank(record.get(key))]
    errors = [f"missing_required_field:{key}" for key in missing]
    side = str(record.get("side") or "").upper()
    if side and side not in {"YES", "NO"}:
        errors.append("invalid_side")
    requested = _float_or_none(record.get("requested_quantity"))
    filled = _float_or_none(record.get("filled_quantity"))
    price = _float_or_none(record.get("filled_price_per_contract"))
    fee = _float_or_none(record.get("fill_fee_per_contract"))
    if requested is None or requested < 0:
        errors.append("invalid_requested_quantity")
    if filled is None or filled < 0:
        errors.append("invalid_filled_quantity")
    if requested is not None and filled is not None and filled > requested:
        errors.append("filled_quantity_exceeds_requested_quantity")
    if price is None or not 0.0 <= price <= 1.0:
        errors.append("invalid_filled_price")
    if fee is None or fee < 0:
        errors.append("invalid_fill_fee")
    return list(dict.fromkeys(errors))


def _forbidden_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_FIELD_KEYS:
                found.add(normalized)
            found.update(_forbidden_keys(nested))
    elif isinstance(value, list):
        for item in value:
            found.update(_forbidden_keys(item))
    return found


def _existing_record_count(fill_log: Path) -> int:
    try:
        payload = load_cdna_fill_log(fill_log)
    except Exception:  # noqa: BLE001
        return 0
    return len(payload.get("records") or [])


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def _safety_block() -> dict[str, Any]:
    return {
        "diagnostic_only": True,
        "saved_files_only": True,
        "orders_or_execution_logic_added": False,
        "auth_or_account_logic_added": False,
        "forbidden_sensitive_fields_rejected": True,
    }
