"""Dry-run-safe venue adapter interfaces for the guarded crypto micro-test executor.

NOTHING here places a real order unless the adapter is explicitly constructed in
``mode="live"`` AND a real venue ``client`` is injected by the caller. By default
every adapter is dry-run / stub-safe:

  - ``place_limit_buy`` never touches the network, never reads credentials, and
    returns a non-binding simulated response. It only ever accepts a BUY,
    PROTECTED_LIMIT request that carries a ``max_limit_price`` (no market orders,
    no shorting/selling, no midpoint, no order without a price cap).
  - CDNA is manual / fill-first only — it NEVER auto-places and NEVER drives a
    browser. A live CDNA order returns ``MANUAL_REQUIRED``.
  - Requests/responses are redacted before logging (no secrets are ever stored).

This module reads no ``.env``, holds no API keys, and opens no sockets. A future
credentialed live placement is intentionally NOT implemented here; the live path
returns a safe rejection unless a real client is injected (e.g. a test fake).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable


ORDER_SIDE_BUY = "BUY"
ORDER_TYPE_PROTECTED_LIMIT = "PROTECTED_LIMIT"
TIF_GTD = "GTD"  # good-till-time (cancel at order_timeout); never GTC market-chasing

MODE_DRY_RUN = "dry_run"
MODE_LIVE = "live"

CDNA_CANDIDATE_ACTION = "FILL_CDNA_FIRST_THEN_HEDGE_EXACT_FILLED_QUANTITY"

_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|secret|password|passwd|authoriz|bearer|token(?!_id)|cookie|"
    r"private|credential|signature|session)",
    re.IGNORECASE,
)


def redact(obj: Any) -> Any:
    """Deep-copy ``obj`` replacing any sensitive value with a redaction marker."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if _SECRET_KEY_RE.search(str(k)) and not str(k).lower().endswith(("token_id", "token_id_yes", "token_id_no")):
                out[k] = "***REDACTED***"
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact(v) for v in obj]
    return obj


@dataclass
class OrderRequest:
    client_order_id: str
    platform: str
    market_id_or_ticker: str
    side: str = ORDER_SIDE_BUY
    order_type: str = ORDER_TYPE_PROTECTED_LIMIT
    max_limit_price: float | None = None
    quantity: float | None = None
    time_in_force: str = TIF_GTD
    order_timeout_ms: float | None = None
    token_id: str | None = None
    contract_id: str | None = None
    condition_id: str | None = None

    def validate(self) -> tuple[bool, str]:
        if self.side != ORDER_SIDE_BUY:
            return False, "only_buy_orders_allowed_no_shorting"
        if self.order_type != ORDER_TYPE_PROTECTED_LIMIT:
            return False, "only_protected_limit_orders_no_market_orders"
        if self.max_limit_price is None or self.max_limit_price <= 0:
            return False, "missing_or_nonpositive_max_limit_price"
        if self.quantity is None or self.quantity <= 0:
            return False, "missing_or_nonpositive_quantity"
        return True, "ok"

    def to_redacted_dict(self) -> dict[str, Any]:
        return redact({
            "client_order_id": self.client_order_id, "platform": self.platform,
            "market_id_or_ticker": self.market_id_or_ticker, "side": self.side,
            "order_type": self.order_type, "max_limit_price": self.max_limit_price,
            "quantity": self.quantity, "time_in_force": self.time_in_force,
            "order_timeout_ms": self.order_timeout_ms, "token_id": self.token_id,
            "contract_id": self.contract_id, "condition_id": self.condition_id,
        })


class LiveVenueAdapter:
    """Base adapter. Dry-run / stub-safe by default."""

    platform = "base"
    supports_client_order_id = True

    def __init__(self, *, mode: str = MODE_DRY_RUN, client: Any = None,
                 preflight_ok: bool = True, preflight_reason: str = "dry_run_stub"):
        self.mode = mode if mode in (MODE_DRY_RUN, MODE_LIVE) else MODE_DRY_RUN
        self._client = client
        self._preflight_ok = preflight_ok
        self._preflight_reason = preflight_reason

    # -- read-only -------------------------------------------------------------- #
    def preflight(self) -> dict[str, Any]:
        return {"platform": self.platform, "mode": self.mode, "ok": bool(self._preflight_ok),
                "reason": self._preflight_reason, "live_client_configured": self._client is not None}

    def get_market_quote(self, *, market_id_or_ticker: str, side: str, token_id: str | None = None) -> dict[str, Any]:
        # Dry-run stub: no network. The executor uses an injected quote_refresher
        # for real freshness; this is a safe placeholder.
        return {"platform": self.platform, "market_id_or_ticker": market_id_or_ticker, "side": side,
                "ask": None, "bid": None, "ask_size": None, "bid_size": None,
                "quote_timestamp": None, "depth_status": "dry_run_stub_no_network", "source": "dry_run_stub"}

    # -- order lifecycle (guarded) --------------------------------------------- #
    def place_limit_buy(self, request: OrderRequest) -> dict[str, Any]:
        ok, reason = request.validate()
        if not ok:
            return self.normalize_order_response({"status": "REJECTED", "reason": reason,
                                                  "client_order_id": request.client_order_id})
        if self.mode != MODE_LIVE:
            return self.normalize_order_response({
                "status": "DRY_RUN_NOT_PLACED", "reason": "dry_run_mode_no_live_order",
                "client_order_id": request.client_order_id, "order_id": None,
                "filled_quantity": 0.0, "avg_fill_price": None,
            })
        if self._client is None:
            return self.normalize_order_response({
                "status": "REJECTED", "reason": "live_client_not_configured_no_credentialed_placement",
                "client_order_id": request.client_order_id,
            })
        # A real credentialed placement is intentionally NOT implemented in this
        # build. An injected client (e.g. a test fake) drives the live path.
        return self.normalize_order_response(self._client.place_limit_buy(request))

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        if self.mode != MODE_LIVE or self._client is None:
            return {"status": "DRY_RUN_CANCEL", "order_id": order_id, "ok": True}
        return self.normalize_order_response(self._client.cancel_order(order_id))

    def get_order_status(self, order_id: str) -> dict[str, Any]:
        if self.mode != MODE_LIVE or self._client is None:
            return {"status": "DRY_RUN_NO_STATUS", "order_id": order_id, "filled_quantity": 0.0, "avg_fill_price": None}
        return self.normalize_order_response(self._client.get_order_status(order_id))

    def get_fills(self, order_id: str) -> list[dict[str, Any]]:
        if self.mode != MODE_LIVE or self._client is None:
            return []
        return [redact(f) for f in (self._client.get_fills(order_id) or [])]

    def normalize_order_response(self, raw: dict[str, Any]) -> dict[str, Any]:
        raw = raw or {}
        return {
            "platform": self.platform,
            "status": str(raw.get("status") or "UNKNOWN"),
            "reason": raw.get("reason"),
            "order_id": raw.get("order_id"),
            "client_order_id": raw.get("client_order_id"),
            "filled_quantity": float(raw.get("filled_quantity") or 0.0),
            "avg_fill_price": raw.get("avg_fill_price"),
            "raw_redacted": redact({k: v for k, v in raw.items() if k not in {"status", "order_id"}}),
        }


class KalshiLiveAdapter(LiveVenueAdapter):
    platform = "kalshi"


class PolymarketLiveAdapter(LiveVenueAdapter):
    platform = "polymarket"


class CdnaManualFillFirstAdapter(LiveVenueAdapter):
    """CDNA is display-price/fill-first with no automated order path. It never
    places an order and never drives a browser; it instructs the operator."""

    platform = "cdna"
    supports_client_order_id = False

    def place_limit_buy(self, request: OrderRequest) -> dict[str, Any]:
        ok, reason = request.validate()
        status_reason = reason if not ok else "cdna_manual_fill_first_no_automated_order"
        return self.normalize_order_response({
            "status": "MANUAL_REQUIRED",
            "reason": status_reason,
            "client_order_id": request.client_order_id,
            "candidate_action": CDNA_CANDIDATE_ACTION,
            "instruction": "Fill the CDNA leg manually at the capped size, record the confirmed fill, "
                           "then hedge the exact filled quantity.",
        })

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        return {"status": "MANUAL_REQUIRED", "order_id": order_id, "ok": False,
                "reason": "cdna_orders_are_manual_no_automated_cancel"}


def default_adapters(*, mode: str = MODE_DRY_RUN) -> dict[str, LiveVenueAdapter]:
    """Dry-run-safe adapter set keyed by platform. Live mode still refuses to
    place unless a real client is injected per adapter by the caller."""
    return {
        "kalshi": KalshiLiveAdapter(mode=mode),
        "polymarket": PolymarketLiveAdapter(mode=mode),
        "cdna": CdnaManualFillFirstAdapter(mode=mode),
    }


def build_order_request(*, client_order_id: str, leg: dict[str, Any], max_limit_price: float | None,
                        quantity: float | None, order_timeout_ms: float | None) -> OrderRequest:
    """Always a BUY, PROTECTED_LIMIT request with a price cap (never a market order)."""
    return OrderRequest(
        client_order_id=client_order_id,
        platform=str(leg.get("platform") or ""),
        market_id_or_ticker=str(leg.get("market_id_or_ticker") or ""),
        side=ORDER_SIDE_BUY,
        order_type=ORDER_TYPE_PROTECTED_LIMIT,
        max_limit_price=max_limit_price,
        quantity=quantity,
        time_in_force=TIF_GTD,
        order_timeout_ms=order_timeout_ms,
        token_id=leg.get("token_id"),
        contract_id=leg.get("contract_id"),
        condition_id=leg.get("condition_id"),
    )
