"""Tests for the daily phone-summary notifier (reporting only; no network, no trading)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.daily_summary_notifier import (
    build_daily_summary, build_phone_message, render_summary_markdown, write_and_send_daily_summary,
)
from relative_value.notification_providers import (
    DryRunNotificationProvider, PushoverNotificationProvider, TelegramNotificationProvider,
    TwilioSmsNotificationProvider, make_provider, STATUS_DRY_RUN, STATUS_SENT, STATUS_FAILED,
)

NOW = datetime(2026, 5, 30, 23, 0, 0, tzinfo=timezone.utc)
DATE = "2026-05-30"


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _reports_with_data(root: Path) -> None:
    # one finalized micro-test: 2 filled legs, 1 canceled, 1 rejected; unsettled -> expected P&L only.
    _write(root / "crypto_micro_tests" / "t1" / "final_report.json", {
        "finalized_at_utc": f"{DATE}T20:00:00+00:00", "settlement_status": "unsettled", "verdict": "PARTIAL",
        "matched_basket_quantity": 5, "min_payoff": 1.0, "guarantee_holds": True,
        "actual_net_edge_after_fees_if_all_filled": 0.04, "intended_net_edge_after_fees": 0.03,
        "residual_exposure": [],
        "leg_results": [
            {"order_status": "filled", "filled_price": 0.30, "filled_quantity": 5},
            {"order_status": "filled", "filled_price": 0.50, "filled_quantity": 5},
            {"order_status": "canceled"}, {"order_status": "rejected"},
        ],
    })
    _write(root / "crypto_micro_tests" / "t1" / "test_plan.json", {"created_at_utc": f"{DATE}T19:00:00+00:00"})
    _write(root / "crypto_structural_watch_btc" / "watch_summary.json", {
        "updated_at": f"{DATE}T22:30:00+00:00", "include_cdna": True,
        "totals": {
            "paper_candidates_found": 3, "best_net_edge_after_fees": 0.06,
            "top_actionable_buy_only_blockers": [
                {"blocker": "missing_kalshi_no_ask", "count": 7}, {"blocker": "stale_quote", "count": 2}],
            "cdna_participation": {"cdna_supplied": True, "cdna_candidate_types_generated": {"CDNA_FILL_FIRST": 2}},
        },
        "paper_candidates": [
            {"asset": "BTC", "candidate_type": "LONG_ONLY_GUARANTEED_PAYOFF", "net_edge_after_fees": 0.06, "dedup_key": "K1"}],
        "cdna_fill_first_candidates": [{"asset": "BTC"}, {"asset": "ETH"}],
    })


# --------------------------------------------------------------------------- #
# aggregation                                                                 #
# --------------------------------------------------------------------------- #
def test_summary_aggregates_micro_test_final_reports(tmp_path: Path) -> None:
    _reports_with_data(tmp_path)
    s = build_daily_summary(reports_root=tmp_path, date=DATE, now=NOW)
    assert s["micro_tests_finalized"] == 1 and s["micro_tests_started"] == 1
    assert s["fills_count"] == 2 and s["canceled_count"] == 1 and s["rejected_count"] == 1
    assert s["trades_count"] == 1
    assert s["total_notional"] == 4.0  # 0.30*5 + 0.50*5
    assert s["expected_pnl"] == 0.2  # 0.04 * 5, unsettled
    assert s["realized_pnl"] is None  # unsettled -> not realized


def test_summary_aggregates_watcher_candidates_and_blockers(tmp_path: Path) -> None:
    _reports_with_data(tmp_path)
    s = build_daily_summary(reports_root=tmp_path, date=DATE, now=NOW)
    assert s["paper_candidates_found"] == 3
    assert s["best_net_edge_after_fees"] == 0.06
    assert s["top_blockers"][0]["blocker"] == "missing_kalshi_no_ask"
    assert s["top_opportunities"] and s["top_opportunities"][0]["asset"] == "BTC"
    assert s["cdna_status"]["supplied"] is True and s["cdna_status"]["candidates"] == 2


def test_summary_handles_missing_pnl_without_guessing(tmp_path: Path) -> None:
    # finalized test with NO fills and unsettled -> both P&L fields unknown (None), not guessed.
    _write(tmp_path / "crypto_micro_tests" / "t9" / "final_report.json", {
        "finalized_at_utc": f"{DATE}T20:00:00+00:00", "settlement_status": "unsettled",
        "matched_basket_quantity": 0, "leg_results": [{"order_status": "not_filled"}], "residual_exposure": [],
    })
    s = build_daily_summary(reports_root=tmp_path, date=DATE, now=NOW)
    assert s["realized_pnl"] is None and s["expected_pnl"] is None
    assert s["account_wide_pnl"] == "not_available_from_local_reports"
    assert "realized_pnl_not_available_from_local_reports" in s["warnings"]
    msg = build_phone_message(s)
    assert "realized unknown / expected unknown" in msg


def test_empty_reports_root_is_safe(tmp_path: Path) -> None:
    s = build_daily_summary(reports_root=tmp_path / "nope", date=DATE, now=NOW)
    assert s["fills_count"] == 0 and s["paper_candidates_found"] == 0
    assert s["realized_pnl"] is None and s["expected_pnl"] is None
    # markdown/message render without error on an empty day.
    assert "Crypto Arb Daily" in build_phone_message(s)
    assert render_summary_markdown(s).startswith("# Crypto Arb Daily")


# --------------------------------------------------------------------------- #
# message format / truncation                                                 #
# --------------------------------------------------------------------------- #
def test_phone_message_has_title_blank_line_and_only_short_fields(tmp_path: Path) -> None:
    _reports_with_data(tmp_path)
    s = build_daily_summary(reports_root=tmp_path, date=DATE, now=NOW)
    msg = build_phone_message(s, max_message_chars=100000, report_path="reports/x.md")
    lines = msg.splitlines()

    assert lines == [
        f"Crypto Arb Daily {DATE}",
        "",
        "P&L: realized unknown / expected $0.20 / total known $0.20",
        "Trades: 2 fills, 1 canceled, 1 rejected",
        "Best edge: 6.0% / $0.0600 per $1",
    ]
    assert msg.count(f"Crypto Arb Daily {DATE}\n\nP&L:") == 1
    for excluded in (
        "Top blocker", "missing_kalshi_no_ask", "CDNA", "Warnings", "Report:", "reports/x.md",
        "Notional", "Candidates",
    ):
        assert excluded not in msg


def test_max_message_chars_truncates_safely(tmp_path: Path) -> None:
    _reports_with_data(tmp_path)
    s = build_daily_summary(reports_root=tmp_path, date=DATE, now=NOW)
    full = build_phone_message(s, max_message_chars=100000, report_path="reports/x.md")
    assert full.startswith(f"Crypto Arb Daily {DATE}")
    truncated = build_phone_message(s, max_message_chars=40)
    assert len(truncated) <= 40 and truncated.endswith("…")


# --------------------------------------------------------------------------- #
# orchestration: dry-run writes files, does not send                          #
# --------------------------------------------------------------------------- #
def test_dry_run_generates_files_and_does_not_send(tmp_path: Path) -> None:
    _reports_with_data(tmp_path)
    out = tmp_path / "daily_summaries" / DATE
    sent_flag = {"called": False}

    def tripwire_post(url, *, data=None, headers=None, timeout=10.0):  # must never be called in dry-run
        sent_flag["called"] = True
        return 200

    result = write_and_send_daily_summary(
        reports_root=tmp_path, date=DATE, provider_name="dry_run", send=False,
        json_output=out / "daily_summary.json", markdown_output=out / "daily_summary.md",
        message_output=out / "daily_summary_message.txt", now=NOW, http_post=tripwire_post,
    )
    assert (out / "daily_summary.json").exists() and (out / "daily_summary.md").exists()
    assert (out / "daily_summary_message.txt").exists()
    assert result["notification"]["status"] == STATUS_DRY_RUN and result["notification"]["sent"] is False
    assert sent_flag["called"] is False  # nothing delivered
    persisted = json.loads((out / "daily_summary.json").read_text(encoding="utf-8"))
    assert persisted["notification"]["status"] == STATUS_DRY_RUN


def test_provider_default_is_dry_run() -> None:
    assert isinstance(make_provider(None), DryRunNotificationProvider)
    assert isinstance(make_provider("unknown-provider"), DryRunNotificationProvider)


# --------------------------------------------------------------------------- #
# providers: build request from env, redact secrets                           #
# --------------------------------------------------------------------------- #
def test_pushover_builds_request_with_env_and_redacts_secrets() -> None:
    captured: dict[str, Any] = {}

    def fake_post(url, *, data=None, headers=None, timeout=10.0):
        captured.update({"url": url, "data": data, "headers": headers})
        return 200

    p = PushoverNotificationProvider(
        env={"PUSHOVER_APP_TOKEN": "app-SECRET-123", "PUSHOVER_USER_KEY": "user-SECRET-456"}, http_post=fake_post)
    req = p.build_request("Title", "Body")
    assert req["data"]["token"] == "app-SECRET-123" and req["data"]["user"] == "user-SECRET-456"
    cfg = p.redacted_config()
    assert cfg["PUSHOVER_APP_TOKEN"] == "***set***" and cfg["PUSHOVER_USER_KEY"] == "***set***"
    assert "SECRET" not in json.dumps(cfg)
    result = p.send("Title", "Body", allow_send=True)
    assert result["sent"] is True and result["status"] == STATUS_SENT
    assert "SECRET" not in json.dumps(result)  # secrets never surface in the result/log
    assert captured["data"]["token"] == "app-SECRET-123"  # but the real request did carry it


def test_telegram_builds_request_with_env_and_redacts_secrets() -> None:
    p = TelegramNotificationProvider(env={"TELEGRAM_BOT_TOKEN": "bot-SECRET", "TELEGRAM_CHAT_ID": "chat-99"},
                                     http_post=lambda url, **k: 200)
    req = p.build_request("Title", "Body")
    assert "bot-SECRET" in req["url"] and req["data"]["chat_id"] == "chat-99"
    assert "SECRET" not in json.dumps(p.redacted_config())
    result = p.send("Title", "Body", allow_send=True)
    assert result["status"] == STATUS_SENT and "SECRET" not in json.dumps(result)


def test_twilio_builds_request_with_env_and_redacts_secrets() -> None:
    p = TwilioSmsNotificationProvider(env={
        "TWILIO_ACCOUNT_SID": "AC-SECRET", "TWILIO_AUTH_TOKEN": "tok-SECRET",
        "TWILIO_FROM_NUMBER": "+15550001111", "DAILY_SUMMARY_TO_NUMBER": "+15552223333"},
        http_post=lambda url, **k: 201)
    req = p.build_request("Title", "Body")
    assert "Authorization" in req["headers"] and req["headers"]["Authorization"].startswith("Basic ")
    assert req["data"]["From"] == "+15550001111" and req["data"]["To"] == "+15552223333"
    cfg = p.redacted_config()
    assert cfg["TWILIO_AUTH_TOKEN"] == "***set***"
    assert "SECRET" not in json.dumps(cfg)
    result = p.send("Title", "Body", allow_send=True)
    assert result["status"] == STATUS_SENT and "SECRET" not in json.dumps(result)


def test_missing_env_vars_produce_clear_error() -> None:
    p = PushoverNotificationProvider(env={}, http_post=lambda url, **k: 200)
    ok, missing = p.validate()
    assert ok is False and "PUSHOVER_APP_TOKEN" in missing and "PUSHOVER_USER_KEY" in missing
    result = p.send("T", "M", allow_send=True)
    assert result["sent"] is False and result["status"] == STATUS_FAILED
    assert result["reason"] == "missing_env_vars"
    assert "PUSHOVER_APP_TOKEN" in result["error"] and "PUSHOVER_APP_TOKEN" in result["missing_env_vars"]


def test_provider_failure_does_not_crash() -> None:
    def boom(url, **kwargs):
        raise RuntimeError("network down")

    p = PushoverNotificationProvider(env={"PUSHOVER_APP_TOKEN": "a", "PUSHOVER_USER_KEY": "b"}, http_post=boom)
    result = p.send("T", "M", allow_send=True)  # must return, not raise
    assert result["sent"] is False and result["status"] == STATUS_FAILED and result["reason"] == "RuntimeError"


# --------------------------------------------------------------------------- #
# safety: no trading / order / account / browser code                         #
# --------------------------------------------------------------------------- #
def test_no_trading_order_account_or_browser_code() -> None:
    for rel in ("relative_value/notification_providers.py", "relative_value/daily_summary_notifier.py"):
        src = Path(rel).read_text(encoding="utf-8")
        code = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
        code = re.sub(r"(?m)^\s*#.*$", "", code)
        for pat in (r"\bplace_order\b", r"\bsubmit_order\b", r"\bcancel_order\b", r"\bcreate_order\b",
                    r"\bsign_transaction\b", r"\bprivate_key\b", r"\bseed_phrase\b", r"\bwallet\b",
                    r"\bplaywright\b", r"\bselenium\b", r"\bwebdriver\b", r"\bchromium\b"):
            assert re.search(pat, code, re.IGNORECASE) is None, f"forbidden {pat} in {rel}"
