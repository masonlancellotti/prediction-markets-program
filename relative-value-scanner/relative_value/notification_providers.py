"""Notification provider abstraction for the daily phone summary.

Reporting / notification ONLY. These providers deliver a short text summary to
Mason's phone via public provider APIs (Pushover / Telegram / Twilio SMS). They
contain NO trading, order, account, or browser-automation logic.

Safety contract for every provider:
  * the default provider is :class:`DryRunNotificationProvider` (never sends);
  * nothing is ever sent unless the caller passes ``allow_send=True`` (wired to
    the ``--send`` CLI flag);
  * secrets are read ONLY from environment variables, at send time;
  * tokens / keys are NEVER returned in logs or result dicts — only a redacted
    ``"***set***" / "***missing***"`` config is exposed;
  * a provider failure returns a structured ``SEND_FAILED`` result and never
    raises, so report generation cannot be crashed by a delivery problem.
"""
from __future__ import annotations

import base64
from typing import Any, Callable, Mapping

# Result status constants (stable strings for logs/tests).
STATUS_DRY_RUN = "DRY_RUN_NOT_SENT"
STATUS_SENT = "SENT"
STATUS_FAILED = "SEND_FAILED"

HttpPost = Callable[..., Any]


def _default_http_post(url: str, *, data: Mapping[str, Any] | None = None,
                       headers: Mapping[str, str] | None = None, timeout: float = 10.0) -> int:
    """Real outbound POST (stdlib only). Only ever called when ``allow_send`` is set."""
    import urllib.parse
    import urllib.request

    body = urllib.parse.urlencode(dict(data or {})).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=dict(headers or {}), method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310 (https provider API)
        return int(getattr(resp, "status", 0) or 0)


class NotificationProvider:
    """Base provider. Subclasses define ``name``, ``required_env`` and ``build_request``."""

    name = "base"

    def __init__(self, *, env: Mapping[str, str] | None = None, http_post: HttpPost | None = None) -> None:
        import os
        self._env: Mapping[str, str] = env if env is not None else os.environ
        self._http_post = http_post or _default_http_post

    # -- configuration ------------------------------------------------------- #
    def required_env(self) -> list[str]:
        return []

    def _secret(self, key: str) -> str:
        return str(self._env.get(key) or "").strip()

    def validate(self) -> tuple[bool, list[str]]:
        """Return ``(ok, missing_env_vars)`` without revealing any values."""
        missing = [k for k in self.required_env() if not self._secret(k)]
        return (not missing, missing)

    def redacted_config(self) -> dict[str, Any]:
        """Config safe to log: presence only, never the secret value."""
        cfg: dict[str, Any] = {"provider": self.name}
        for key in self.required_env():
            cfg[key] = "***set***" if self._secret(key) else "***missing***"
        return cfg

    # -- request building (may contain secrets; never logged) ---------------- #
    def build_request(self, title: str, message: str) -> dict[str, Any]:
        raise NotImplementedError

    # -- send ---------------------------------------------------------------- #
    def send(self, title: str, message: str, *, allow_send: bool = False) -> dict[str, Any]:
        if not allow_send:
            return {"provider": self.name, "sent": False, "status": STATUS_DRY_RUN,
                    "redacted_config": self.redacted_config()}
        ok, missing = self.validate()
        if not ok:
            return {"provider": self.name, "sent": False, "status": STATUS_FAILED,
                    "reason": "missing_env_vars", "missing_env_vars": missing,
                    "error": f"missing required environment variables: {', '.join(missing)}",
                    "redacted_config": self.redacted_config()}
        try:
            req = self.build_request(title, message)
            http_status = self._http_post(req["url"], data=req.get("data"),
                                          headers=req.get("headers"), timeout=10.0)
            return {"provider": self.name, "sent": True, "status": STATUS_SENT,
                    "http_status": http_status, "redacted_config": self.redacted_config()}
        except Exception as exc:  # noqa: BLE001 (delivery failure must not crash report generation)
            return {"provider": self.name, "sent": False, "status": STATUS_FAILED,
                    "reason": type(exc).__name__, "error": str(exc),
                    "redacted_config": self.redacted_config()}


class DryRunNotificationProvider(NotificationProvider):
    """Builds nothing, sends nothing. The safe default."""

    name = "dry_run"

    def build_request(self, title: str, message: str) -> dict[str, Any]:
        return {"url": None, "data": None, "headers": None}

    def send(self, title: str, message: str, *, allow_send: bool = False) -> dict[str, Any]:
        return {"provider": self.name, "sent": False, "status": STATUS_DRY_RUN,
                "redacted_config": self.redacted_config(),
                "note": "dry_run provider never delivers; use --provider with --send to deliver"}


class PushoverNotificationProvider(NotificationProvider):
    name = "pushover"
    API_URL = "https://api.pushover.net/1/messages.json"

    def required_env(self) -> list[str]:
        return ["PUSHOVER_APP_TOKEN", "PUSHOVER_USER_KEY"]

    def build_request(self, title: str, message: str) -> dict[str, Any]:
        return {"url": self.API_URL, "headers": {},
                "data": {"token": self._secret("PUSHOVER_APP_TOKEN"),
                         "user": self._secret("PUSHOVER_USER_KEY"),
                         "title": title, "message": message}}


class TelegramNotificationProvider(NotificationProvider):
    name = "telegram"

    def required_env(self) -> list[str]:
        return ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]

    def build_request(self, title: str, message: str) -> dict[str, Any]:
        token = self._secret("TELEGRAM_BOT_TOKEN")
        return {"url": f"https://api.telegram.org/bot{token}/sendMessage", "headers": {},
                "data": {"chat_id": self._secret("TELEGRAM_CHAT_ID"),
                         "text": f"{title}\n{message}" if title else message,
                         "disable_web_page_preview": "true"}}


class TwilioSmsNotificationProvider(NotificationProvider):
    name = "twilio"

    def required_env(self) -> list[str]:
        return ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "DAILY_SUMMARY_TO_NUMBER"]

    def build_request(self, title: str, message: str) -> dict[str, Any]:
        sid = self._secret("TWILIO_ACCOUNT_SID")
        token = self._secret("TWILIO_AUTH_TOKEN")
        basic = base64.b64encode(f"{sid}:{token}".encode("utf-8")).decode("ascii")
        body = f"{title}\n{message}" if title else message
        return {"url": f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                "headers": {"Authorization": f"Basic {basic}"},
                "data": {"From": self._secret("TWILIO_FROM_NUMBER"),
                         "To": self._secret("DAILY_SUMMARY_TO_NUMBER"), "Body": body}}


_PROVIDERS: dict[str, type[NotificationProvider]] = {
    "dry_run": DryRunNotificationProvider,
    "pushover": PushoverNotificationProvider,
    "telegram": TelegramNotificationProvider,
    "twilio": TwilioSmsNotificationProvider,
}

PROVIDER_NAMES = tuple(_PROVIDERS.keys())


def make_provider(name: str | None, *, env: Mapping[str, str] | None = None,
                  http_post: HttpPost | None = None) -> NotificationProvider:
    """Construct a provider by name; unknown / empty names fall back to dry_run."""
    cls = _PROVIDERS.get(str(name or "dry_run").strip().lower(), DryRunNotificationProvider)
    return cls(env=env, http_post=http_post)
