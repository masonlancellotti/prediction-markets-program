"""Tests for the executable-venue policy (Part A): scan vs executable, adapter status."""
from __future__ import annotations

import re
from pathlib import Path

from relative_value.executable_venue_policy import (
    EXECUTION_STATUS_EXECUTABLE, EXECUTION_STATUS_NO_SAFE_ORDER_API, EXECUTION_STATUS_SCAN_ONLY,
    EXECUTION_STATUS_STUB_FAIL_CLOSED, CDNA_NO_SAFE_ORDER_ADAPTER_REASON,
    adapter_execution_status, build_adapter_status_report, candidate_execution_status,
    is_executable_candidate, normalize_venues, venue_execution_status,
)
from relative_value.live_crypto_execution_adapters import KalshiLiveAdapter, default_adapters


def test_normalize_venues() -> None:
    assert normalize_venues("kalshi,polymarket,cdna") == ("kalshi", "polymarket", "cdna")
    assert normalize_venues(["Kalshi", "Polymarket"]) == ("kalshi", "polymarket")
    assert normalize_venues(None) == ("kalshi", "polymarket")


def test_venue_execution_status() -> None:
    assert venue_execution_status("kalshi") == EXECUTION_STATUS_EXECUTABLE
    assert venue_execution_status("polymarket") == EXECUTION_STATUS_EXECUTABLE
    assert venue_execution_status("cdna") == EXECUTION_STATUS_NO_SAFE_ORDER_API  # never executable
    assert venue_execution_status("kalshi", adapter_ready=False) == EXECUTION_STATUS_STUB_FAIL_CLOSED
    assert venue_execution_status("sx") == EXECUTION_STATUS_SCAN_ONLY


def test_candidate_execution_status_kp_executable() -> None:
    c = {"legs": [{"platform": "kalshi"}, {"platform": "polymarket"}]}
    s = candidate_execution_status(c)
    assert s["execution_status"] == EXECUTION_STATUS_EXECUTABLE and s["executable"] is True
    assert s["do_not_trade_reasons"] == [] and is_executable_candidate(c) is True


def test_candidate_execution_status_cdna_not_executable() -> None:
    c = {"legs": [{"platform": "cdna"}, {"platform": "kalshi"}]}
    s = candidate_execution_status(c)
    assert s["execution_status"] == EXECUTION_STATUS_NO_SAFE_ORDER_API and s["executable"] is False
    assert CDNA_NO_SAFE_ORDER_ADAPTER_REASON in s["do_not_trade_reasons"]
    assert s["has_cdna_leg"] is True and is_executable_candidate(c) is False


def test_adapter_status_stub_vs_ready() -> None:
    # default (stub) adapters -> not ready.
    rep = build_adapter_status_report(default_adapters(mode="live"))
    assert rep["kalshi_adapter_status"] == EXECUTION_STATUS_STUB_FAIL_CLOSED
    assert rep["polymarket_adapter_status"] == EXECUTION_STATUS_STUB_FAIL_CLOSED
    assert rep["cdna_adapter_status"] == EXECUTION_STATUS_NO_SAFE_ORDER_API
    assert rep["all_live_adapters_ready"] is False

    class FakeClient:
        def place_limit_buy(self, req): return {"status": "ACCEPTED"}

    ready = {"kalshi": KalshiLiveAdapter(mode="live", client=FakeClient()),
             "polymarket": KalshiLiveAdapter(mode="live", client=FakeClient())}
    rep2 = build_adapter_status_report(ready)
    assert adapter_execution_status(ready["kalshi"]) == EXECUTION_STATUS_EXECUTABLE
    assert rep2["all_live_adapters_ready"] is True


def test_cdna_adapter_never_executable() -> None:
    assert adapter_execution_status(default_adapters(mode="live")["cdna"]) == EXECUTION_STATUS_NO_SAFE_ORDER_API


def test_no_trading_network_or_browser_code() -> None:
    src = Path("relative_value/executable_venue_policy.py").read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    code = re.sub(r"(?m)^\s*#.*$", "", code)
    for pat in (r"\bplace_order\b", r"\bsubmit_order\b", r"\burllib\b", r"\brequests\b", r"\burlopen\b",
                r"\bplaywright\b", r"\bselenium\b", r"\bwebdriver\b", r"\bgetenv\b", r"\.env\b"):
        assert re.search(pat, code, re.IGNORECASE) is None, f"forbidden {pat}"
