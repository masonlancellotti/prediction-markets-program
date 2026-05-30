"""Tests for immediate guarded live crypto notifications (no network, no trading changes)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import scan
from relative_value.live_crypto_execution_adapters import (
    CdnaManualFillFirstAdapter,
    KalshiLiveAdapter,
    OrderRequest,
    PolymarketLiveAdapter,
)
from relative_value.crypto_micro_test_journal import (
    finalize_crypto_micro_test,
    record_crypto_micro_fill,
    start_micro_test_from_objects,
)
from relative_value.live_crypto_micro_executor import run_crypto_structural_trigger
from relative_value.live_trade_notifications import LiveTradeNotifier, build_trade_journal_notification_message
from relative_value.notification_providers import STATUS_DRY_RUN, STATUS_FAILED, STATUS_SENT


CLOCK = datetime(2026, 5, 30, 16, 0, 0, tzinfo=timezone.utc)
QTS = "2026-05-30T16:00:00Z"
ARMED_ENV = {"LIVE_CRYPTO_MICROTEST_ENABLED": "true"}
NOTIFY_ENV = {
    "PUSHOVER_APP_TOKEN": "app-SECRET-123",
    "PUSHOVER_USER_KEY": "user-SECRET-456",
    **ARMED_ENV,
}


def _leg(platform, side, mid, ask, fee=0.01, size=75.0):
    return {"platform": platform, "side": side, "market_id_or_ticker": mid, "market_shape": "point_in_time_threshold",
            "ask": ask, "fee": fee, "all_in_cost": round(ask + fee, 8), "available_size_or_cap": size,
            "source_index": "brti", "quote_timestamp": QTS, "depth_status": "top", "condition_id": None,
            "token_id_yes": None, "token_id_no": None, "contract_id": None, "complement_used": False,
            "complement_source": None}


def _candidate(legs=None):
    legs = legs if legs is not None else [_leg("kalshi", "NO", "K-A", 0.30), _leg("polymarket", "NO", "K-B", 0.50)]
    total = round(sum(l["all_in_cost"] for l in legs), 8)
    return {"asset": "BTC", "candidate_type": "LONG_ONLY_GUARANTEED_PAYOFF", "paper_candidate": True,
            "paper_candidate_class": "OPERATOR_ACCEPTED_RISK", "tradable_buy_only": True,
            "requires_short_or_sell": False, "candidate_execution_type": "BUY_ONLY", "hard_blockers": [],
            "min_payoff": 1.0, "payoff_vector": [1, 1, 1], "net_edge_after_fees": round(1.0 - total, 8),
            "adjusted_net_edge_after_fees": round(1.0 - total, 8), "total_cost_after_fees": total,
            "assumptions_accepted": ["source_index_mismatch"], "source_indexes": ["brti"],
            "target_instant_utc": "2026-05-30T17:00:00+00:00", "iteration_timestamp": "20260530T160000Z",
            "verdict": "VALID_FOR_PAPER_REVIEW", "basket_legs": legs}


def _fresh(*, leg, now):
    return {"platform": leg["platform"], "market_id_or_ticker": leg["market_id_or_ticker"], "side": leg["side"],
            "ask": leg["ask"], "bid": None, "ask_size": leg["available_size_or_cap"], "bid_size": None,
            "quote_timestamp": QTS, "quote_age_ms": 0.0, "depth_status": "top", "source": "test"}


class FakeClient:
    def __init__(self, *, fill_qty=0.0, avg_px=0.41, order_id="O1"):
        self.fill_qty = fill_qty
        self.avg_px = avg_px
        self.order_id = order_id
        self.placed: list[OrderRequest] = []
        self.canceled = False

    def place_limit_buy(self, req: OrderRequest):
        self.placed.append(req)
        return {"status": "resting", "order_id": self.order_id, "client_order_id": req.client_order_id}

    def get_order_status(self, order_id):
        return {"status": "filled" if self.fill_qty > 0 else "resting",
                "filled_quantity": self.fill_qty, "avg_fill_price": self.avg_px}

    def get_fills(self, order_id):
        return [{"price": self.avg_px, "quantity": self.fill_qty, "fee": 0.0}] if self.fill_qty > 0 else []

    def cancel_order(self, order_id):
        self.canceled = True
        return {"status": "canceled", "order_id": order_id, "ok": True}


def _armed(k, p):
    return {"kalshi": KalshiLiveAdapter(mode="live", client=k),
            "polymarket": PolymarketLiveAdapter(mode="live", client=p),
            "cdna": CdnaManualFillFirstAdapter(mode="live")}


def _journal(tmp_path: Path) -> tuple[str, Path]:
    root = tmp_path / "journal"
    cand = _candidate(legs=[_leg("kalshi", "YES", "K-A", 0.40), _leg("kalshi", "NO", "K-B", 0.50)])
    plan = {
        "effective_execution_style": "manual",
        "candidate_action": "MANUAL_PLACE_LEGS_IN_RECOMMENDED_ORDER",
        "basket_quantity_cap": 6,
        "legs": [
            {"platform": "kalshi", "side": "YES", "market_id_or_ticker": "K-A", "max_limit_price": 0.41,
             "expected_fee": 0.01, "all_in_max_cost": 0.42, "quantity_cap": 6},
            {"platform": "kalshi", "side": "NO", "market_id_or_ticker": "K-B", "max_limit_price": 0.51,
             "expected_fee": 0.01, "all_in_max_cost": 0.52, "quantity_cap": 6},
        ],
    }
    res = start_micro_test_from_objects(candidate=cand, plan=plan, test_label="notify", output_root=root, now=CLOCK)
    return res["test_id"], root


def _events(root: Path, tid: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (root / tid / "event_log.jsonl").read_text(encoding="utf-8").splitlines()]


def _set_provider_env(monkeypatch) -> None:
    monkeypatch.setenv("PUSHOVER_APP_TOKEN", "app-SECRET-123")
    monkeypatch.setenv("PUSHOVER_USER_KEY", "user-SECRET-456")


def _run(tmp_path: Path, *, notify_provider="dry_run", notify_send=False, notify_on=None,
         http_post=None, dry_run=True, live=False, env=None, k=None, p=None):
    out = tmp_path / "trig"
    summary = run_crypto_structural_trigger(
        assets=["BTC"], watch_once_or_loop="once", min_net_edge=0.10, execution_style="least_liquid_first",
        dry_run=dry_run, live=live, i_understand_this_places_real_orders=live,
        output_dir=out, report_builder=lambda **kw: {"generated_at": QTS, "rows": [_candidate()], "summary_counts": {}},
        quote_refresher=_fresh, clock=lambda: CLOCK, sleep=lambda _s: None, console=lambda _m: None,
        env=env or {}, kill_switch_path=tmp_path / "KILL", adapters=_armed(k or FakeClient(), p or FakeClient()),
        order_timeout_ms=100.0, notify_provider=notify_provider, notify_send=notify_send,
        notify_on=notify_on, notification_http_post=http_post,
    )
    tr = json.loads(sorted(out.glob("*/trigger_report.json"))[0].read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (Path(tr["journal_path"]) / "event_log.jsonl").read_text(encoding="utf-8").splitlines()]
    return summary, tr, events


def test_dry_run_provider_records_notification_without_sending(tmp_path: Path) -> None:
    _summary, tr, events = _run(tmp_path, notify_send=True, notify_on="submitted", dry_run=True)
    attempts = [r for r in tr["notification_results"] if r["event"] == "submitted"]
    assert attempts and attempts[0]["provider_result"]["status"] == STATUS_DRY_RUN
    assert attempts[0]["event_log_event_type"] == "notification_skipped"
    assert any(e["event_type"] == "notification_skipped" for e in events)


def test_record_crypto_micro_fill_help_includes_notify_flags(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        scan.main(["record-crypto-micro-fill", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--notify-provider" in out
    assert "--notify-send" in out


def test_finalize_crypto_micro_test_help_includes_notify_flags(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        scan.main(["finalize-crypto-micro-test", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--notify-provider" in out
    assert "--notify-send" in out


def test_journal_fill_event_with_notify_send_calls_provider(tmp_path: Path, monkeypatch) -> None:
    _set_provider_env(monkeypatch)
    sent: list[dict[str, Any]] = []

    def fake_post(url, **kwargs):
        sent.append({"url": url, **kwargs})
        return 200

    tid, root = _journal(tmp_path)
    result = record_crypto_micro_fill(
        test_id=tid, platform="kalshi", market_id_or_ticker="K-A", side="YES",
        filled_price=0.41, filled_quantity=6, fees=0.06, order_status="filled",
        output_root=root, now=CLOCK, notify_provider="pushover", notify_send=True,
        notification_http_post=fake_post,
    )
    assert result["notification"]["event_log_event_type"] == "notification_sent"
    assert len(sent) == 1
    assert "Crypto Arb Trade\n\nEvent: filled" in result["notification"]["message"]


def test_journal_fill_event_without_notify_send_skips_provider(tmp_path: Path, monkeypatch) -> None:
    _set_provider_env(monkeypatch)
    calls = {"count": 0}
    tid, root = _journal(tmp_path)
    result = record_crypto_micro_fill(
        test_id=tid, platform="kalshi", market_id_or_ticker="K-A", side="YES",
        filled_price=0.41, filled_quantity=6, fees=0.06, order_status="filled",
        output_root=root, now=CLOCK, notify_provider="pushover", notify_send=False,
        notification_http_post=lambda url, **kwargs: calls.__setitem__("count", calls["count"] + 1) or 200,
    )
    assert result["notification"]["event_log_event_type"] == "notification_skipped"
    assert result["notification"]["reason"] == "notify_send_false"
    assert calls["count"] == 0


def test_journal_finalize_with_notify_send_calls_provider(tmp_path: Path, monkeypatch) -> None:
    _set_provider_env(monkeypatch)
    sent: list[dict[str, Any]] = []
    tid, root = _journal(tmp_path)
    record_crypto_micro_fill(test_id=tid, platform="kalshi", market_id_or_ticker="K-A", side="YES",
                             filled_price=0.41, filled_quantity=6, fees=0.06, output_root=root, now=CLOCK)
    record_crypto_micro_fill(test_id=tid, platform="kalshi", market_id_or_ticker="K-B", side="NO",
                             filled_price=0.51, filled_quantity=6, fees=0.06, output_root=root, now=CLOCK)
    final = finalize_crypto_micro_test(
        test_id=tid, output_root=root, now=CLOCK, notify_provider="pushover", notify_send=True,
        notification_http_post=lambda url, **kwargs: sent.append({"url": url, **kwargs}) or 200,
    )
    assert final["notification"]["event_log_event_type"] == "notification_sent"
    assert len(sent) == 1
    assert "Event: finalized" in final["notification"]["message"]


def test_journal_provider_failure_logs_failure_and_command_succeeds(tmp_path: Path, monkeypatch) -> None:
    _set_provider_env(monkeypatch)
    tid, root = _journal(tmp_path)

    def failing_post(url, **kwargs):
        raise RuntimeError("delivery failed app-SECRET-123")

    result = record_crypto_micro_fill(
        test_id=tid, platform="kalshi", market_id_or_ticker="K-A", side="YES",
        filled_price=0.41, filled_quantity=6, fees=0.06, order_status="filled",
        output_root=root, now=CLOCK, notify_provider="pushover", notify_send=True,
        notification_http_post=failing_post,
    )
    assert result["status"] == "OK"
    assert result["notification"]["event_log_event_type"] == "notification_failed"
    assert any(e["event_type"] == "notification_failed" for e in _events(root, tid))


def test_journal_notification_secrets_redacted(tmp_path: Path, monkeypatch) -> None:
    _set_provider_env(monkeypatch)
    tid, root = _journal(tmp_path)

    def failing_post(url, **kwargs):
        raise RuntimeError("delivery failed app-SECRET-123 user-SECRET-456")

    result = record_crypto_micro_fill(
        test_id=tid, platform="kalshi", market_id_or_ticker="K-A", side="YES",
        filled_price=0.41, filled_quantity=6, fees=0.06, order_status="filled",
        output_root=root, now=CLOCK, notify_provider="pushover", notify_send=True,
        notification_http_post=failing_post,
    )
    text = json.dumps(result) + (root / tid / "event_log.jsonl").read_text(encoding="utf-8")
    assert "app-SECRET-123" not in text
    assert "user-SECRET-456" not in text
    assert "***set***" in text


def test_no_notification_provider_call_unless_notify_send(tmp_path: Path) -> None:
    calls = {"count": 0}

    def fake_post(url, **kwargs):
        calls["count"] += 1
        return 200

    _run(tmp_path, notify_provider="pushover", notify_send=False, notify_on="submitted",
         http_post=fake_post, dry_run=True, env=NOTIFY_ENV)
    assert calls["count"] == 0


def test_fill_event_sends_one_notification(tmp_path: Path) -> None:
    sent: list[dict[str, Any]] = []

    def fake_post(url, **kwargs):
        sent.append({"url": url, **kwargs})
        return 200

    k, p = FakeClient(fill_qty=9, avg_px=0.31, order_id="K1"), FakeClient(fill_qty=0, order_id="P1")
    _summary, tr, _events = _run(tmp_path, notify_provider="pushover", notify_send=True, notify_on="filled",
                                 http_post=fake_post, dry_run=False, live=True, env=NOTIFY_ENV, k=k, p=p)
    fill_attempts = [r for r in tr["notification_results"] if r["event"] == "filled"]
    assert len(fill_attempts) == 1
    assert fill_attempts[0]["provider_result"]["status"] == STATUS_SENT
    assert len(sent) == 1
    assert "Crypto Arb LIVE\nEvent: FILL" in fill_attempts[0]["message"]


def test_duplicate_fill_event_within_dedup_window_is_skipped() -> None:
    calls = {"count": 0}
    notifier = LiveTradeNotifier(
        provider_name="pushover", send=True, notify_on="filled", dedup_seconds=30,
        env=NOTIFY_ENV, http_post=lambda url, **kwargs: calls.__setitem__("count", calls["count"] + 1) or 200,
        clock=lambda: CLOCK,
    )
    payload = {"test_id": "T1", "order_id": "O1", "fill_id": "F1", "asset": "BTC",
               "candidate_type": "LONG_ONLY_GUARANTEED_PAYOFF", "short_status": "filled"}
    first = notifier.notify("filled", payload)
    second = notifier.notify("filled", payload)
    assert first["event_log_event_type"] == "notification_sent"
    assert second["event_log_event_type"] == "notification_skipped"
    assert second["reason"] == "deduplicated"
    assert calls["count"] == 1


def test_provider_failure_logs_send_failed_and_does_not_crash(tmp_path: Path) -> None:
    def failing_post(url, **kwargs):
        raise RuntimeError("delivery down app-SECRET-123")

    _summary, tr, events = _run(tmp_path, notify_provider="pushover", notify_send=True, notify_on="filled",
                                http_post=failing_post, dry_run=False, live=True, env=NOTIFY_ENV,
                                k=FakeClient(fill_qty=9, order_id="K1"), p=FakeClient(fill_qty=0, order_id="P1"))
    failed = [r for r in tr["notification_results"] if r.get("event_log_event_type") == "notification_failed"]
    assert failed and failed[0]["provider_result"]["status"] == STATUS_FAILED
    assert any(e["event_type"] == "notification_failed" for e in events)


def test_no_secrets_printed_or_persisted(tmp_path: Path) -> None:
    def failing_post(url, **kwargs):
        raise RuntimeError("delivery down app-SECRET-123 user-SECRET-456")

    _summary, tr, _events = _run(tmp_path, notify_provider="pushover", notify_send=True, notify_on="filled",
                                 http_post=failing_post, dry_run=False, live=True, env=NOTIFY_ENV,
                                 k=FakeClient(fill_qty=9, order_id="K1"), p=FakeClient(fill_qty=0, order_id="P1"))
    text = json.dumps(tr)
    assert "app-SECRET-123" not in text
    assert "user-SECRET-456" not in text
    assert "***set***" in text


def test_no_trading_behavior_changed_when_notifications_disabled(tmp_path: Path) -> None:
    k, p = FakeClient(fill_qty=9, order_id="K1"), FakeClient(fill_qty=0, order_id="P1")
    _run(tmp_path, notify_send=False, notify_on="filled", dry_run=False, live=True, env=ARMED_ENV, k=k, p=p)
    assert len(k.placed) == 1
    assert len(p.placed) == 1
    assert p.canceled is True


def test_no_browser_automation_added() -> None:
    for rel in (
        "relative_value/live_trade_notifications.py",
        "relative_value/live_crypto_micro_executor.py",
        "relative_value/crypto_fast_path_executor.py",
    ):
        src = Path(rel).read_text(encoding="utf-8")
        code = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
        code = re.sub(r"(?m)^\\s*#.*$", "", code)
        for pat in (r"\bplaywright\b", r"\bselenium\b", r"\bwebdriver\b", r"\bchromium\b"):
            assert re.search(pat, code, re.IGNORECASE) is None, f"forbidden {pat} in {rel}"
