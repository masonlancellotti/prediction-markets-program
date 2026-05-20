from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from relative_value.paper_candidate_evaluator import PaperCandidateEvaluatorConfig


SUPPORTED_SCHEMA_VERSION = 1
MARKOUT_WINDOWS_SECONDS = {
    "t_plus_30s": 30.0,
    "t_plus_5m": 5.0 * 60.0,
    "t_plus_30m": 30.0 * 60.0,
    "t_plus_2h": 2.0 * 60.0 * 60.0,
}
MARKOUT_STATUSES = ("filled", "no_data", "stale", "missing_market", "missing_orderbook")
DISCLAIMER = (
    "Read-only markout replay for research evidence only. A spread closing is not guaranteed profit; "
    "this makes no trading, fill, midpoint, executable-liquidity, or settlement-equivalence claim."
)


@dataclass(frozen=True)
class MarkoutReplayConfig:
    window_tolerance_seconds: float = 60.0
    evaluator_config: PaperCandidateEvaluatorConfig = field(default_factory=PaperCandidateEvaluatorConfig)


def replay_paper_candidate_markout_files(
    *,
    ledger_path: Path,
    polymarket_enriched_later_path: Path,
    kalshi_enriched_later_path: Path,
    output_path: Path | None = None,
    config: MarkoutReplayConfig | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    replayed_at = now or datetime.now(timezone.utc)
    _require_tz_aware(replayed_at, "now")
    payload = replay_paper_candidate_markouts(
        ledger_payload=_load_json_object(ledger_path, "ledger"),
        polymarket_later_payload=_load_json_object(polymarket_enriched_later_path, "polymarket_enriched_later"),
        kalshi_later_payload=_load_json_object(kalshi_enriched_later_path, "kalshi_enriched_later"),
        inputs={
            "ledger": str(ledger_path),
            "polymarket_enriched_later": str(polymarket_enriched_later_path),
            "kalshi_enriched_later": str(kalshi_enriched_later_path),
        },
        config=config,
        replayed_at=replayed_at,
    )
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def replay_paper_candidate_markouts(
    *,
    ledger_payload: dict[str, Any],
    polymarket_later_payload: dict[str, Any],
    kalshi_later_payload: dict[str, Any],
    inputs: dict[str, str] | None = None,
    config: MarkoutReplayConfig | None = None,
    replayed_at: datetime | None = None,
) -> dict[str, Any]:
    cfg = config or MarkoutReplayConfig()
    if cfg.window_tolerance_seconds < 0:
        raise ValueError("window_tolerance_seconds must be non-negative")
    generated_at = replayed_at or datetime.now(timezone.utc)
    _require_tz_aware(generated_at, "replayed_at")

    payload = copy.deepcopy(ledger_payload)
    polymarket_later = copy.deepcopy(polymarket_later_payload)
    kalshi_later = copy.deepcopy(kalshi_later_payload)
    _validate_schema_one("ledger", payload)
    _validate_schema_one("polymarket_enriched_later", polymarket_later)
    _validate_schema_one("kalshi_enriched_later", kalshi_later)

    ledger_rows = payload.get("ledger")
    if not isinstance(ledger_rows, list):
        raise ValueError("ledger input must contain a ledger list")
    polymarket_by_id = {
        _string_or_empty(row.get("market_id")): row
        for row in _market_rows(polymarket_later, "polymarket_enriched_later")
        if _string_or_empty(row.get("market_id"))
    }
    kalshi_by_ticker = {
        _string_or_empty(row.get("ticker") or row.get("market_id")): row
        for row in _market_rows(kalshi_later, "kalshi_enriched_later")
        if _string_or_empty(row.get("ticker") or row.get("market_id"))
    }

    counts = {status: 0 for status in MARKOUT_STATUSES}
    for row in ledger_rows:
        if not isinstance(row, dict):
            continue
        replayed_markouts = _replay_row_markouts(
            row,
            polymarket_by_id,
            kalshi_by_ticker,
            cfg,
        )
        row["markouts"] = replayed_markouts
        row["disclaimer"] = DISCLAIMER
        for markout in replayed_markouts.values():
            status = markout["markout_status"]
            counts[status] += 1

    payload["source"] = "paper_candidate_markout_replay"
    payload["disclaimer"] = DISCLAIMER
    payload["markout_replay"] = {
        "schema_version": 1,
        "source": "paper_candidate_markout_replay",
        "generated_at": generated_at.isoformat(),
        "inputs": inputs
        or {
            "ledger": "<in-memory>",
            "polymarket_enriched_later": "<in-memory>",
            "kalshi_enriched_later": "<in-memory>",
        },
        "window_tolerance_seconds": cfg.window_tolerance_seconds,
        "windows_seconds": MARKOUT_WINDOWS_SECONDS,
        "counts_by_status": counts,
        "disclaimer": DISCLAIMER,
    }
    return payload


def _replay_row_markouts(
    row: dict[str, Any],
    polymarket_by_id: dict[str, dict[str, Any]],
    kalshi_by_ticker: dict[str, dict[str, Any]],
    cfg: MarkoutReplayConfig,
) -> dict[str, dict[str, Any]]:
    detected_at = _parse_datetime_or_none(row.get("detected_at"))
    poly_id = _string_or_empty((row.get("polymarket") or {}).get("market_id"))
    kalshi_ticker = _string_or_empty((row.get("kalshi") or {}).get("ticker"))
    polymarket_later = polymarket_by_id.get(poly_id)
    kalshi_later = kalshi_by_ticker.get(kalshi_ticker)

    if not poly_id or not kalshi_ticker or polymarket_later is None or kalshi_later is None:
        return {window: _empty_markout("missing_market") for window in MARKOUT_WINDOWS_SECONDS}

    poly_enrichment = _enrichment(polymarket_later)
    kalshi_enrichment = _enrichment(kalshi_later)
    orderbook = _later_orderbook(poly_enrichment, kalshi_enrichment)
    if orderbook is None:
        return {window: _empty_markout("missing_orderbook") for window in MARKOUT_WINDOWS_SECONDS}

    direction = _original_direction(row)
    if detected_at is None or direction is None:
        return {window: _empty_markout("no_data") for window in MARKOUT_WINDOWS_SECONDS}

    markouts: dict[str, dict[str, Any]] = {}
    for window, offset_seconds in MARKOUT_WINDOWS_SECONDS.items():
        expected_at = detected_at + timedelta(seconds=offset_seconds)
        status = _window_status(
            expected_at,
            orderbook["polymarket_captured_at"],
            orderbook["kalshi_captured_at"],
            cfg.window_tolerance_seconds,
        )
        if status != "filled":
            markouts[window] = _empty_markout(status)
            continue
        markouts[window] = _filled_markout(row, direction, orderbook, cfg.evaluator_config)
    return markouts


def _window_status(
    expected_at: datetime,
    polymarket_captured_at: datetime,
    kalshi_captured_at: datetime,
    tolerance_seconds: float,
) -> str:
    lower_bound = expected_at - timedelta(seconds=tolerance_seconds)
    upper_bound = expected_at + timedelta(seconds=tolerance_seconds)
    latest_capture = max(polymarket_captured_at, kalshi_captured_at)
    if latest_capture < lower_bound:
        return "no_data"
    if not (lower_bound <= polymarket_captured_at <= upper_bound):
        return "stale"
    if not (lower_bound <= kalshi_captured_at <= upper_bound):
        return "stale"
    return "filled"


def _filled_markout(
    row: dict[str, Any],
    direction: dict[str, str],
    orderbook: dict[str, Any],
    cfg: PaperCandidateEvaluatorConfig,
) -> dict[str, Any]:
    later_prices = _later_direction_prices(direction, orderbook)
    if later_prices is None:
        return _empty_markout("missing_orderbook")
    polymarket_price = later_prices["polymarket_would_enter_price"]
    kalshi_price = later_prices["kalshi_would_enter_price"]
    polymarket_fee = cfg.polymarket_fee_model.fee_for_leg(polymarket_price)
    kalshi_fee = cfg.kalshi_fee_model.fee_for_leg(kalshi_price)
    estimated_net_gap = round(later_prices["gross_gap"] - polymarket_fee - kalshi_fee, 6)
    original_net_gap = _float_or_none((row.get("gap") or {}).get("estimated_net_gap"))
    change_in_estimated_net_gap = (
        round(estimated_net_gap - original_net_gap, 6)
        if original_net_gap is not None
        else None
    )
    return {
        "markout_status": "filled",
        "later_polymarket_quote_captured_at": orderbook["polymarket_captured_at"].isoformat(),
        "later_kalshi_quote_captured_at": orderbook["kalshi_captured_at"].isoformat(),
        "later_polymarket_best_bid": orderbook["polymarket_best_bid"],
        "later_polymarket_best_ask": orderbook["polymarket_best_ask"],
        "later_kalshi_best_bid": orderbook["kalshi_best_bid"],
        "later_kalshi_best_ask": orderbook["kalshi_best_ask"],
        "later_gross_gap": round(later_prices["gross_gap"], 6),
        "later_polymarket_fee": round(polymarket_fee, 6),
        "later_kalshi_fee": round(kalshi_fee, 6),
        "later_estimated_net_gap": estimated_net_gap,
        "change_in_estimated_net_gap": change_in_estimated_net_gap,
        "spread_closed_boolean": estimated_net_gap <= 0.0 if original_net_gap is not None else None,
    }


def _later_direction_prices(direction: dict[str, str], orderbook: dict[str, Any]) -> dict[str, float] | None:
    polymarket_price = _price_for_side(
        direction["polymarket_side"],
        bid=orderbook["polymarket_best_bid"],
        ask=orderbook["polymarket_best_ask"],
    )
    kalshi_price = _price_for_side(
        direction["kalshi_side"],
        bid=orderbook["kalshi_best_bid"],
        ask=orderbook["kalshi_best_ask"],
    )
    if polymarket_price is None or kalshi_price is None:
        return None
    if direction["polymarket_side"] == "SELL_YES" and direction["kalshi_side"] == "BUY_YES":
        gross_gap = polymarket_price - kalshi_price
    elif direction["polymarket_side"] == "BUY_YES" and direction["kalshi_side"] == "SELL_YES":
        gross_gap = kalshi_price - polymarket_price
    else:
        return None
    return {
        "polymarket_would_enter_price": polymarket_price,
        "kalshi_would_enter_price": kalshi_price,
        "gross_gap": gross_gap,
    }


def _price_for_side(side: str, *, bid: float, ask: float) -> float | None:
    if side == "BUY_YES":
        return ask
    if side == "SELL_YES":
        return bid
    return None


def _later_orderbook(poly_enrichment: dict[str, Any], kalshi_enrichment: dict[str, Any]) -> dict[str, Any] | None:
    if poly_enrichment.get("enrichment_status") != "enriched":
        return None
    if kalshi_enrichment.get("enrichment_status") != "enriched":
        return None
    poly_captured = _parse_datetime_or_none(poly_enrichment.get("orderbook_captured_at"))
    kalshi_captured = _parse_datetime_or_none(kalshi_enrichment.get("orderbook_captured_at"))
    prices = {
        "polymarket_best_bid": _float_or_none(poly_enrichment.get("best_bid")),
        "polymarket_best_ask": _float_or_none(poly_enrichment.get("best_ask")),
        "kalshi_best_bid": _float_or_none(kalshi_enrichment.get("best_bid")),
        "kalshi_best_ask": _float_or_none(kalshi_enrichment.get("best_ask")),
    }
    if poly_captured is None or kalshi_captured is None:
        return None
    if any(value is None or not 0.0 <= value <= 1.0 for value in prices.values()):
        return None
    return {
        "polymarket_captured_at": poly_captured,
        "kalshi_captured_at": kalshi_captured,
        **prices,
    }


def _original_direction(row: dict[str, Any]) -> dict[str, str] | None:
    polymarket = row.get("polymarket")
    kalshi = row.get("kalshi")
    if not isinstance(polymarket, dict) or not isinstance(kalshi, dict):
        return None
    poly_side = _string_or_empty(polymarket.get("would_enter_side"))
    kalshi_side = _string_or_empty(kalshi.get("would_enter_side"))
    if {poly_side, kalshi_side} != {"BUY_YES", "SELL_YES"}:
        return None
    return {"polymarket_side": poly_side, "kalshi_side": kalshi_side}


def _empty_markout(status: str) -> dict[str, Any]:
    return {
        "markout_status": status,
        "later_polymarket_quote_captured_at": None,
        "later_kalshi_quote_captured_at": None,
        "later_polymarket_best_bid": None,
        "later_polymarket_best_ask": None,
        "later_kalshi_best_bid": None,
        "later_kalshi_best_ask": None,
        "later_gross_gap": None,
        "later_polymarket_fee": None,
        "later_kalshi_fee": None,
        "later_estimated_net_gap": None,
        "change_in_estimated_net_gap": None,
        "spread_closed_boolean": None,
    }


def _market_rows(payload: dict[str, Any], label: str) -> list[dict[str, Any]]:
    rows = payload.get("normalized_markets")
    if not isinstance(rows, list):
        raise ValueError(f"{label} input must contain normalized_markets list")
    return [row for row in rows if isinstance(row, dict)]


def _validate_schema_one(label: str, payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
        raise ValueError(f"{label} schema_version must be 1")


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"{label} file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} JSON is invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} JSON must be an object")
    return payload


def _enrichment(market: dict[str, Any]) -> dict[str, Any]:
    enrichment = market.get("orderbook_enrichment")
    return enrichment if isinstance(enrichment, dict) else {}


def _parse_datetime_or_none(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _string_or_empty(value: Any) -> str:
    if value is None:
        return ""
    return str(value)
