"""Immediate phone notifications for guarded crypto micro-test events.

This module formats and sends notification-only messages through the existing
``notification_providers`` abstraction. It does not place, cancel, route, sign,
or otherwise alter orders. Provider secrets are read only by the provider layer
from the environment and provider results are redacted before callers log them.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from relative_value.notification_providers import (
    STATUS_DRY_RUN,
    STATUS_FAILED,
    STATUS_SENT,
    make_provider,
)


DEFAULT_NOTIFY_ON = frozenset({"submitted", "filled", "partial", "canceled", "rejected", "emergency", "finalized"})

_EVENT_LABELS = {
    "submitted": "SUBMIT",
    "filled": "FILL",
    "partial": "PARTIAL",
    "canceled": "CANCELED",
    "rejected": "REJECTED",
    "emergency": "EMERGENCY",
    "finalized": "FINALIZED",
}


def parse_notify_on(value: str | Iterable[str] | None) -> set[str]:
    if value is None:
        return set(DEFAULT_NOTIFY_ON)
    if isinstance(value, str):
        raw = [part.strip().lower() for part in value.split(",")]
    else:
        raw = [str(part).strip().lower() for part in value]
    return {part for part in raw if part}


class LiveTradeNotifier:
    """Small stateful notifier with in-process de-duplication."""

    def __init__(
        self,
        *,
        provider_name: str = "dry_run",
        send: bool = False,
        notify_on: str | Iterable[str] | None = None,
        dedup_seconds: float = 30.0,
        env: Mapping[str, str] | None = None,
        http_post: Any = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.provider_name = str(provider_name or "dry_run")
        self.send = bool(send)
        self.notify_on = parse_notify_on(notify_on)
        self.dedup_seconds = max(0.0, float(dedup_seconds))
        self.provider = make_provider(self.provider_name, env=env, http_post=http_post)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._seen: dict[str, datetime] = {}
        self._redaction_values = _provider_redaction_values(self.provider, env)

    def notify(self, event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        event = _canonical_event(event_type)
        now = _aware(self.clock())
        base = {
            "schema_kind": "crypto_live_trade_notification_attempt_v1",
            "timestamp_utc": now.isoformat(),
            "event": event,
            "dedup_key": _dedup_key(event, payload),
            "provider": self.provider.name,
            "notify_send": self.send,
        }
        if event not in self.notify_on:
            return {**base, "event_log_event_type": "notification_skipped", "reason": "event_not_enabled"}
        if not self.send:
            return {**base, "event_log_event_type": "notification_skipped", "reason": "notify_send_false"}
        prev = self._seen.get(base["dedup_key"])
        if prev is not None and (now - prev).total_seconds() < self.dedup_seconds:
            return {**base, "event_log_event_type": "notification_skipped", "reason": "deduplicated"}
        self._seen[base["dedup_key"]] = now

        message = build_live_notification_message(event, payload)
        result = _redact_values(self.provider.send("", message, allow_send=True), self._redaction_values)
        log_type = "notification_sent"
        reason = None
        if result.get("status") == STATUS_FAILED:
            log_type = "notification_failed"
            reason = result.get("reason") or "send_failed"
        elif result.get("status") == STATUS_DRY_RUN:
            log_type = "notification_skipped"
            reason = "dry_run_provider"
        elif result.get("status") != STATUS_SENT:
            log_type = "notification_skipped"
            reason = str(result.get("status") or "not_sent").lower()
        return {
            **base,
            "event_log_event_type": log_type,
            "reason": reason,
            "message": message,
            "provider_result": result,
        }


def build_live_notification_message(event_type: str, payload: Mapping[str, Any]) -> str:
    event = _canonical_event(event_type)
    dry_run = bool(payload.get("dry_run"))
    candidate = _candidate_text(payload)
    if dry_run:
        return "\n".join([
            "Crypto Arb DRY RUN",
            "Would submit protected limit orders",
            f"Candidate: {candidate}",
            f"Edge: {_fmt(payload.get('expected_edge'))}",
            f"Reason: {_fmt(payload.get('reason') or payload.get('short_status'))}",
        ])

    return "\n".join([
        "Crypto Arb LIVE",
        f"Event: {_EVENT_LABELS.get(event, event.upper())}",
        f"Test: {_fmt(payload.get('test_id'))}",
        f"Candidate: {candidate}",
        f"Leg: {_leg_text(payload)}",
        f"Qty: {_qty_text(payload)}",
        f"Price: {_fmt(payload.get('fill_price'))} limit {_fmt(payload.get('limit_price'))}",
        f"Edge: {_fmt(payload.get('expected_edge'))}",
        f"Residual: {_residual_text(payload.get('residual_exposure'))}",
        f"Status: {_fmt(payload.get('short_status'))}",
    ])


def build_trade_journal_notification_message(event_type: str, payload: Mapping[str, Any]) -> str:
    return "\n".join([
        "Crypto Arb Trade",
        "",
        f"Event: {_fmt(event_type)}",
        f"Platform: {_fmt(payload.get('platform'))}",
        f"Side: {_fmt(payload.get('side'))}",
        f"Qty: {_fmt(payload.get('quantity'))}",
        f"Price: {_fmt(payload.get('price'))}",
        f"P&L: {_fmt(payload.get('pnl'))}",
    ])


def send_trade_journal_notification(
    *,
    event_type: str,
    payload: Mapping[str, Any],
    provider_name: str = "dry_run",
    send: bool = False,
    env: Mapping[str, str] | None = None,
    http_post: Any = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    now_fn = clock or (lambda: datetime.now(timezone.utc))
    now = _aware(now_fn())
    provider = make_provider(provider_name, env=env, http_post=http_post)
    redaction_values = _provider_redaction_values(provider, env)
    message = build_trade_journal_notification_message(event_type, payload)
    base = {
        "schema_kind": "crypto_trade_journal_notification_attempt_v1",
        "timestamp_utc": now.isoformat(),
        "event": str(event_type),
        "provider": provider.name,
        "notify_send": bool(send),
        "message": message,
    }
    if not send:
        return {
            **base,
            "event_log_event_type": "notification_skipped",
            "reason": "notify_send_false",
            "provider_result": {"provider": provider.name, "sent": False, "status": STATUS_DRY_RUN,
                                "redacted_config": provider.redacted_config()},
        }
    result = _redact_values(provider.send("", message, allow_send=True), redaction_values)
    log_type = "notification_sent"
    reason = None
    if result.get("status") == STATUS_FAILED:
        log_type = "notification_failed"
        reason = result.get("reason") or "send_failed"
    elif result.get("status") == STATUS_DRY_RUN:
        log_type = "notification_skipped"
        reason = "dry_run_provider"
    elif result.get("status") != STATUS_SENT:
        log_type = "notification_skipped"
        reason = str(result.get("status") or "not_sent").lower()
    return {**base, "event_log_event_type": log_type, "reason": reason, "provider_result": result}


def _canonical_event(event_type: str) -> str:
    event = str(event_type or "").strip().lower()
    aliases = {
        "submit": "submitted",
        "submitted": "submitted",
        "intent": "submitted",
        "order_intent_created": "submitted",
        "fill": "filled",
        "filled": "filled",
        "partial_fill": "partial",
        "cancelled": "canceled",
        "cancel": "canceled",
        "reject": "rejected",
        "hedge_failed": "emergency",
        "residual_opened": "emergency",
        "emergency_review_required": "emergency",
        "finalize": "finalized",
    }
    return aliases.get(event, event)


def _dedup_key(event: str, payload: Mapping[str, Any]) -> str:
    parts = [
        event,
        _fmt(payload.get("order_id")),
        _fmt(payload.get("fill_id")),
        _fmt(payload.get("test_id")),
        _fmt(payload.get("leg_key")),
    ]
    return "|".join(parts)


def _candidate_text(payload: Mapping[str, Any]) -> str:
    asset = payload.get("asset")
    ctype = payload.get("candidate_type")
    return " ".join(str(x) for x in (asset, ctype) if x not in (None, ""))


def _leg_text(payload: Mapping[str, Any]) -> str:
    return " ".join(str(x) for x in (
        payload.get("platform"),
        payload.get("side"),
        payload.get("market_id"),
    ) if x not in (None, ""))


def _qty_text(payload: Mapping[str, Any]) -> str:
    return f"{_fmt(payload.get('filled_qty'))}/{_fmt(payload.get('intended_qty'))}"


def _residual_text(value: Any) -> str:
    if value in (None, "", [], {}):
        return "none"
    return _fmt(value)


def _fmt(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, float):
        return f"{value:.8g}"
    return str(value)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _redact_values(obj: Any, values: list[str]) -> Any:
    if isinstance(obj, dict):
        return {k: _redact_values(v, values) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_values(v, values) for v in obj]
    if isinstance(obj, str):
        out = obj
        for value in values:
            out = out.replace(value, "***REDACTED***")
        return out
    return obj


def _provider_redaction_values(provider: Any, env: Mapping[str, str] | None) -> list[str]:
    vals = [str(v) for v in (env or {}).values() if v and len(str(v)) >= 4]
    for key in provider.required_env():
        try:
            val = provider._secret(key)
        except Exception:  # noqa: BLE001 (redaction helper must not affect notification flow)
            val = ""
        if val and len(str(val)) >= 4:
            vals.append(str(val))
    return sorted(set(vals), key=len, reverse=True)
