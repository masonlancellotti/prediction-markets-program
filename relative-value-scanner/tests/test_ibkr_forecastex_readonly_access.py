from __future__ import annotations

import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse

import pytest

import relative_value.ibkr_forecastex_readonly_access as ibkr_access
from relative_value.ibkr_forecastex_readonly_access import (
    IBKRReadOnlyRequestError,
    build_ibkr_forecastex_access_doctor,
    build_ibkr_forecastex_raw_shape_summary,
    fetch_ibkr_forecastex_readonly_snapshot,
)


def test_access_doctor_refuses_non_localhost_by_default() -> None:
    def fake_http(url: str, headers: dict[str, str], timeout_seconds: float) -> object:
        raise AssertionError("non-localhost doctor must not call HTTP")

    report = build_ibkr_forecastex_access_doctor(
        base_url="https://example.com/v1/api",
        http_get=fake_http,
    )

    assert report["status"] == "REFUSED_NON_LOCALHOST_BASE_URL"
    assert report["reachable"] is False
    assert report["authenticated"] is False
    assert "non_localhost_base_url_refused" in report["blockers"]


def test_access_doctor_handles_unreachable_gateway_cleanly() -> None:
    def fake_http(url: str, headers: dict[str, str], timeout_seconds: float) -> object:
        raise RuntimeError("connection refused")

    report = build_ibkr_forecastex_access_doctor(http_get=fake_http)

    assert report["status"] == "LOCAL_GATEWAY_UNREACHABLE"
    assert report["reachable"] is False
    assert "ibkr_local_gateway_unreachable" in report["blockers"]
    assert "ibkr_local_authenticated_session_required" in report["blockers"]
    assert "PAPER_CANDIDATE" not in json.dumps(report)


def test_fake_readonly_endpoint_returns_normalized_forecastex_row(tmp_path: Path) -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_http(url: str, headers: dict[str, str], timeout_seconds: float) -> object:
        calls.append((url, headers))
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("/iserver/auth/status"):
            return {"authenticated": True, "connected": True}
        if parsed.path.endswith("/iserver/secdef/search"):
            assert query["symbol"][0] in {"FF", "U", "BTC", "ETH", "FOMC", "CPI", "TEMP", "WEATHER", "FORECASTX", "ForecastEx"}
            return [
                {
                    "conid": "123456",
                    "symbol": "FEXBTC",
                    "localSymbol": "FEXBTC JUN26 C",
                    "description": "Will BTC be above 100k?",
                    "exchange": "FORECASTX",
                    "right": "C",
                    "expiration": "20260630",
                    "lastTradeTime": "20260630:1600",
                }
            ]
        if parsed.path.endswith("/iserver/marketdata/snapshot"):
            assert query["conids"] == ["123456"]
            return [{"conid": "123456", "31": "0.42", "84": "0.41", "85": "0.43", "86": "10", "88": "12"}]
        raise AssertionError(f"unexpected URL {url}")

    report = fetch_ibkr_forecastex_readonly_snapshot(
        output_dir=tmp_path / "snapshots",
        http_get=fake_http,
        now=datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc),
    )

    assert report["status"] == "OK"
    assert report["reachable"] is True
    assert report["authenticated"] is True
    assert report["summary"]["normalized_rows"] == 1
    assert report["summary"]["raw_files_written"] >= 2
    assert report["summary"]["forecastx_candidate_count"] >= 1
    assert report["discovery_report"]["summary"]["normalized_possible_count"] >= 1
    row = report["records"][0]
    assert row["venue"] == "ibkr_forecastex"
    assert row["exchange"] == "FORECASTX"
    assert row["conid"] == "123456"
    assert row["outcome"] == "YES"
    assert row["bid"] == 0.41
    assert row["ask"] == 0.43
    assert row["bid_size"] == 10.0
    assert row["ask_size"] == 12.0
    assert row["last"] == 0.42
    assert row["diagnostic_only"] is True
    assert row["affects_evaluator_gates"] is False
    assert row["can_create_candidate_pair"] is False
    assert row["can_create_paper_candidate"] is False
    assert "settlement_rules_need_review" in row["blockers"]
    assert "market_data_permission_review_required" not in row["blockers"]
    serialized = json.dumps(report)
    assert "PAPER_CANDIDATE" not in serialized

    called_urls = "\n".join(url for url, _headers in calls).lower()
    for forbidden in ("account", "portfolio", "/order", "/orders", "/trade", "/trades"):
        assert forbidden not in called_urls
    for _url, headers in calls:
        lowered = {key.lower(): value for key, value in headers.items()}
        assert "authorization" not in lowered
        assert "cookie" not in lowered


def test_unreachable_fetch_writes_fail_closed_report(tmp_path: Path) -> None:
    def fake_http(url: str, headers: dict[str, str], timeout_seconds: float) -> object:
        raise RuntimeError("gateway unavailable")

    report = fetch_ibkr_forecastex_readonly_snapshot(
        output_dir=tmp_path / "snapshots",
        http_get=fake_http,
    )

    assert report["reachable"] is False
    assert report["summary"]["normalized_rows"] == 0
    assert "ibkr_local_authenticated_session_required" in report["blockers"]
    assert report["safety"]["order_endpoints_called"] is False
    assert "PAPER_CANDIDATE" not in json.dumps(report)


def test_search_terms_produce_discovery_candidate_rows(tmp_path: Path) -> None:
    def fake_http(url: str, headers: dict[str, str], timeout_seconds: float) -> object:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("/iserver/auth/status"):
            return {"authenticated": True, "connected": True}
        if parsed.path.endswith("/iserver/secdef/search"):
            if query.get("symbol") == ["event contract"]:
                return [
                    {
                        "conid": "999",
                        "symbol": "FEXWEATHER",
                        "localSymbol": "FEXWEATHER MAY26 C",
                        "description": "ForecastEx weather event contract",
                        "exchange": "FORECASTX",
                        "right": "C",
                    }
                ]
            return []
        if parsed.path.endswith("/iserver/marketdata/snapshot"):
            return [{"conid": "999", "84": "0.12", "85": "0.15"}]
        raise AssertionError(f"unexpected URL {url}")

    report = fetch_ibkr_forecastex_readonly_snapshot(
        output_dir=tmp_path / "snapshots",
        search_terms="event contract",
        http_get=fake_http,
    )

    discovery = report["discovery_report"]
    assert discovery["summary"]["candidate_count"] >= 1
    assert discovery["summary"]["forecastx_candidate_count"] >= 1
    assert discovery["candidates"][0]["search_term"] == "event contract"
    assert report["summary"]["normalized_rows"] == 1
    assert "FORECASTX_CANDIDATES_FOUND" in report["discovery_statuses"]
    assert "PAPER_CANDIDATE" not in json.dumps(report)


def test_explicit_ff_search_terms_do_not_expand_to_other_doc_seeds(tmp_path: Path) -> None:
    symbols_seen: list[str] = []

    def fake_http(url: str, headers: dict[str, str], timeout_seconds: float) -> object:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("/iserver/auth/status"):
            return {"authenticated": True, "connected": True}
        if parsed.path.endswith("/iserver/secdef/search"):
            symbols_seen.extend(query.get("symbol", []))
            return []
        raise AssertionError(f"unexpected URL {url}")

    report = fetch_ibkr_forecastex_readonly_snapshot(
        output_dir=tmp_path / "snapshots",
        search_terms="FF",
        http_get=fake_http,
    )

    assert set(symbols_seen) == {"FF"}
    assert report["search_terms"] == ["FF"]
    assert "PAPER_CANDIDATE" not in json.dumps(report)


def test_seed_conid_file_produces_normalized_row(tmp_path: Path) -> None:
    seed_path = tmp_path / "conids.txt"
    seed_path.write_text("777777\n", encoding="utf-8")
    calls: list[str] = []

    def fake_http(url: str, headers: dict[str, str], timeout_seconds: float) -> object:
        calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("/iserver/auth/status"):
            return {"authenticated": True, "connected": True}
        if parsed.path.endswith("/iserver/secdef/search"):
            return []
        if parsed.path.endswith("/iserver/secdef/info"):
            assert query["conid"] == ["777777"]
            return {
                "conid": "777777",
                "symbol": "FEXFED",
                "localSymbol": "FEXFED JUL26 P",
                "description": "ForecastEx Fed event contract",
                "exchange": "FORECASTX",
                "right": "P",
            }
        if parsed.path.endswith("/iserver/marketdata/snapshot"):
            assert query["conids"] == ["777777"]
            return [{"conid": "777777", "31": "0.58", "84": "0.56", "85": "0.6"}]
        raise AssertionError(f"unexpected URL {url}")

    report = fetch_ibkr_forecastex_readonly_snapshot(
        output_dir=tmp_path / "snapshots",
        seed_conids_path=seed_path,
        http_get=fake_http,
    )

    assert report["summary"]["normalized_rows"] == 1
    row = report["records"][0]
    assert row["conid"] == "777777"
    assert row["outcome"] == "NO"
    assert row["bid"] == 0.56
    assert row["ask"] == 0.6
    assert report["discovery_report"]["summary"]["seed_candidate_count"] == 1
    assert "FORECASTX_CANDIDATES_FOUND" in report["discovery_statuses"]
    called = "\n".join(calls).lower()
    for forbidden in ("account", "portfolio", "/order", "/orders", "/trade", "/trades"):
        assert forbidden not in called


def test_market_data_permission_missing_is_blocker_not_failure(tmp_path: Path) -> None:
    def fake_http(url: str, headers: dict[str, str], timeout_seconds: float) -> object:
        parsed = urlparse(url)
        if parsed.path.endswith("/iserver/auth/status"):
            return {"authenticated": True, "connected": True}
        if parsed.path.endswith("/iserver/secdef/search"):
            return [
                {
                    "conid": "321",
                    "symbol": "FEXBTC",
                    "description": "ForecastEx BTC event contract",
                    "exchange": "FORECASTX",
                    "right": "C",
                }
            ]
        if parsed.path.endswith("/iserver/marketdata/snapshot"):
            raise RuntimeError("market data permission denied")
        raise AssertionError(f"unexpected URL {url}")

    report = fetch_ibkr_forecastex_readonly_snapshot(
        output_dir=tmp_path / "snapshots",
        search_terms="FORECASTX",
        http_get=fake_http,
    )

    assert report["status"] == "OK"
    assert report["summary"]["normalized_rows"] == 1
    assert "MARKET_DATA_PERMISSION_MISSING" in report["discovery_statuses"]
    assert "market_data_permission_review_required" in report["records"][0]["blockers"]
    assert "PAPER_CANDIDATE" not in json.dumps(report)


def test_ff_doc_seed_response_is_forecastx_underlier_and_triggers_followup(tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_http(url: str, headers: dict[str, str], timeout_seconds: float) -> object:
        calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("/iserver/auth/status"):
            return {"authenticated": True, "connected": True}
        if parsed.path.endswith("/iserver/secdef/search"):
            if query.get("symbol") == ["FF"] and "secType" not in query and "name" not in query:
                return [
                    {
                        "conid": 658663572,
                        "companyHeader": "US Fed Funds Target Rate - FORECASTX",
                        "symbol": "FF",
                        "description": "FORECASTX",
                        "sections": [
                            {"secType": "IND", "exchange": "FORECASTX;"},
                            {"secType": "EC", "months": "JUN26"},
                        ],
                    }
                ]
            return []
        if parsed.path.endswith("/iserver/secdef/strikes"):
            assert query["conid"] == ["658663572"]
            assert query["month"] == ["JUN26"]
            return {"months": ["JUN26"], "strikes": [425, 450]}
        if parsed.path.endswith("/iserver/secdef/info"):
            assert query["conid"] == ["658663572"]
            return {
                "conid": 658663572,
                "companyHeader": "US Fed Funds Target Rate - FORECASTX",
                "symbol": "FF",
                "description": "FORECASTX",
                "sections": [{"secType": "EC"}],
            }
        raise AssertionError(f"unexpected URL {url}")

    report = fetch_ibkr_forecastex_readonly_snapshot(
        output_dir=tmp_path / "snapshots",
        search_terms="ForecastEx",
        http_get=fake_http,
    )

    discovery = report["discovery_report"]
    assert discovery["summary"]["documented_seed_ff_attempted"] is True
    assert discovery["summary"]["ff_underlier_found"] is True
    assert discovery["summary"]["forecastx_underlier_candidates"] >= 1
    assert discovery["summary"]["forecastx_tradable_contract_candidates"] == 0
    assert "FORECASTX_UNDERLIER_FOUND_NEEDS_STRIKES_OR_CONTRACT_INFO" in report["discovery_statuses"]
    row = report["records"][0]
    assert row["venue"] == "ibkr_forecastex"
    assert row["forecastx_underlier_candidate"] is True
    assert row["underlier_conid"] == 658663572
    assert row["symbol"] == "FF"
    assert row["normalized_possible"] is False
    assert row["can_create_candidate_pair"] is False
    called = "\n".join(calls).lower()
    assert "/iserver/secdef/strikes" in called
    assert "/iserver/secdef/info" in called
    for forbidden in ("account", "portfolio", "/order", "/orders", "/trade", "/trades"):
        assert forbidden not in called
    assert "PAPER_CANDIDATE" not in json.dumps(report)


def test_forecastx_final_cp_contract_normalizes_to_yes_no(tmp_path: Path) -> None:
    def fake_http(url: str, headers: dict[str, str], timeout_seconds: float) -> object:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("/iserver/auth/status"):
            return {"authenticated": True, "connected": True}
        if parsed.path.endswith("/iserver/secdef/search"):
            if query.get("symbol") == ["FF"] and "secType" not in query and "name" not in query:
                return [
                    {
                        "conid": "658663572",
                        "companyHeader": "US Fed Funds Target Rate - FORECASTX",
                        "symbol": "FF",
                    "sections": [{"secType": "IND", "exchange": "FORECASTX;"}, {"secType": "EC", "months": "JUN26"}],
                }
            ]
            return []
        if parsed.path.endswith("/iserver/secdef/strikes"):
            return {"months": ["JUN26"], "strikes": [450], "rights": ["C"]}
        if parsed.path.endswith("/iserver/secdef/info"):
            if "strike" not in query:
                return {
                    "conid": "658663572",
                    "companyName": "US Fed Funds Target Rate",
                    "exchange": "FORECASTX",
                    "secType": "IND",
                    "right": "?",
                }
            return [
                {
                    "conid": "777001",
                    "symbol": "FF",
                    "localSymbol": "FF JUN26 450 C",
                    "description": "US Fed Funds Target Rate - FORECASTX",
                    "exchange": "FORECASTX",
                    "right": "C",
                    "expiration": "20260630",
                    "lastTradeTime": "20260630:1600",
                }
            ]
        if parsed.path.endswith("/iserver/marketdata/snapshot"):
            assert query["conids"] == ["777001"]
            return [{"conid": "777001", "31": "0.51", "84": "0.49", "85": "0.53", "86": "8", "88": "9"}]
        raise AssertionError(f"unexpected URL {url}")

    report = fetch_ibkr_forecastex_readonly_snapshot(
        output_dir=tmp_path / "snapshots",
        http_get=fake_http,
    )

    tradable_rows = [row for row in report["records"] if row["tradable_contract_candidate"]]
    assert len(tradable_rows) == 1
    row = tradable_rows[0]
    assert row["conid"] == "777001"
    assert row["outcome"] == "YES"
    assert row["discovery_stage"] == "MARKETDATA_FOUND"
    assert row["bid"] == 0.49
    assert row["ask"] == 0.53
    assert report["discovery_report"]["summary"]["forecastx_tradable_contract_candidates"] >= 1
    assert report["discovery_report"]["summary"]["forecastx_marketdata_rows"] >= 1
    assert "PAPER_CANDIDATE" not in json.dumps(report)


def test_missing_secdef_parameters_are_blockers_not_exceptions(tmp_path: Path) -> None:
    def fake_http(url: str, headers: dict[str, str], timeout_seconds: float) -> object:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("/iserver/auth/status"):
            return {"authenticated": True, "connected": True}
        if parsed.path.endswith("/iserver/secdef/search"):
            if query.get("symbol") == ["FF"] and "secType" not in query and "name" not in query:
                return [
                    {
                        "conid": "658663572",
                        "companyHeader": "US Fed Funds Target Rate - FORECASTX",
                        "symbol": "FF",
                        "sections": [{"secType": "IND", "exchange": "FORECASTX;"}, {"secType": "EC"}],
                    }
                ]
            return []
        if parsed.path.endswith("/iserver/secdef/info"):
            return {
                "conid": "658663572",
                "companyName": "US Fed Funds Target Rate",
                "exchange": "FORECASTX",
                "secType": "IND",
                "right": "?",
            }
        if parsed.path.endswith("/iserver/secdef/strikes"):
            raise AssertionError("strikes should not be called without a month")
        raise AssertionError(f"unexpected URL {url}")

    report = fetch_ibkr_forecastex_readonly_snapshot(
        output_dir=tmp_path / "snapshots",
        search_terms="FF",
        http_get=fake_http,
    )

    summary = report["discovery_report"]["summary"]
    assert summary["missing_secdef_parameter_count"] >= 1
    assert summary["strikes_requests_attempted"] == 0
    assert "FORECASTX_UNDERLIER_FOUND_NEEDS_STRIKES_OR_CONTRACT_INFO" in report["discovery_statuses"]
    serialized = json.dumps(report)
    assert "missing_secdef_parameters_for_contract_info" in serialized
    assert "PAPER_CANDIDATE" not in serialized


def test_contract_info_request_cap_is_enforced(tmp_path: Path) -> None:
    info_requests: list[str] = []

    def fake_http(url: str, headers: dict[str, str], timeout_seconds: float) -> object:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("/iserver/auth/status"):
            return {"authenticated": True, "connected": True}
        if parsed.path.endswith("/iserver/secdef/search"):
            if query.get("symbol") == ["FF"] and "secType" not in query and "name" not in query:
                return [
                    {
                        "conid": "658663572",
                        "companyHeader": "US Fed Funds Target Rate - FORECASTX",
                        "symbol": "FF",
                        "sections": [{"secType": "IND", "exchange": "FORECASTX;"}, {"secType": "EC", "months": "JUN26"}],
                    }
                ]
            return []
        if parsed.path.endswith("/iserver/secdef/strikes"):
            return {"strikes": [425, 450, 475], "rights": ["C", "P"]}
        if parsed.path.endswith("/iserver/secdef/info"):
            if "strike" not in query:
                return {"conid": "658663572", "exchange": "FORECASTX", "secType": "IND", "right": "?"}
            info_requests.append(url)
            return []
        raise AssertionError(f"unexpected URL {url}")

    report = fetch_ibkr_forecastex_readonly_snapshot(
        output_dir=tmp_path / "snapshots",
        search_terms="FF",
        max_contract_info_requests=2,
        http_get=fake_http,
    )

    summary = report["discovery_report"]["summary"]
    assert summary["strikes_requests_attempted"] == 1
    assert summary["contract_info_requests_attempted"] == 2
    assert len(info_requests) == 2
    assert "contract_info_request_cap_reached" in report["warnings"]
    assert "PAPER_CANDIDATE" not in json.dumps(report)


def test_raw_shape_inventory_reports_missing_final_contract_fields(tmp_path: Path) -> None:
    raw_path = tmp_path / "ibkr_forecastex_secdef_search_ff.json"
    raw_path.write_text(
        json.dumps(
            {
                "endpoint_name": "secdef_search_ff",
                "path": "/iserver/secdef/search",
                "query_params": {"symbol": "FF"},
                "captured_at": "2026-05-26T12:00:00+00:00",
                "payload": [
                    {
                        "conid": "658663572",
                        "symbol": "FF",
                        "description": "FORECASTX",
                        "companyHeader": "US Fed Funds Target Rate - FORECASTX",
                        "sections": [{"secType": "EC"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_ibkr_forecastex_raw_shape_summary(
        raw_files=[raw_path],
        generated_at="2026-05-26T12:00:00+00:00",
        snapshot_dir=tmp_path,
    )

    summary = report["summary"]
    assert summary["raw_files_read"] == 1
    assert summary["forecastx_identifier_files"] == 1
    assert summary["binary_yes_no_files"] == 0
    assert summary["final_tradable_contract_fields_present"] is False
    assert summary["blockers_by_count"]["final_tradable_forecastex_contract_not_found"] == 1
    assert summary["blockers_by_count"]["missing_call_put_right"] == 1
    assert summary["blockers_by_count"]["missing_expiry_or_month"] == 1
    assert "PAPER_CANDIDATE" not in json.dumps(report)


def test_raw_shape_inventory_detects_final_tradable_contract_fields(tmp_path: Path) -> None:
    raw_path = tmp_path / "ibkr_forecastex_secdef_info_contract.json"
    raw_path.write_text(
        json.dumps(
            {
                "endpoint_name": "secdef_info_contract",
                "path": "/iserver/secdef/info",
                "query_params": {
                    "conid": "658663572",
                    "secType": "EC",
                    "month": "JUN26",
                    "strike": "450",
                    "right": "C",
                    "exchange": "FORECASTX",
                },
                "captured_at": "2026-05-26T12:00:00+00:00",
                "payload": [
                    {
                        "conid": "777001",
                        "symbol": "FF",
                        "localSymbol": "FF JUN26 450 C",
                        "description": "US Fed Funds Target Rate - FORECASTX",
                        "exchange": "FORECASTX",
                        "secType": "EC",
                        "right": "C",
                        "strike": 450,
                        "lastTradeDateOrContractMonth": "202606",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_ibkr_forecastex_raw_shape_summary(
        raw_files=[raw_path],
        generated_at="2026-05-26T12:00:00+00:00",
        snapshot_dir=tmp_path,
    )

    summary = report["summary"]
    assert summary["final_tradable_contract_fields_present"] is True
    assert summary["final_tradable_contract_field_files"] == 1
    assert summary["call_put_right_files"] == 1
    assert summary["expiry_or_month_files"] == 1
    assert summary["blockers_by_count"] == {}
    assert "PAPER_CANDIDATE" not in json.dumps(report)


def test_non_tradable_forecastex_underlier_gets_specific_blockers(tmp_path: Path) -> None:
    def fake_http(url: str, headers: dict[str, str], timeout_seconds: float) -> object:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("/iserver/auth/status"):
            return {"authenticated": True, "connected": True}
        if parsed.path.endswith("/iserver/secdef/search"):
            if query.get("symbol") == ["FF"] and "secType" not in query and "name" not in query:
                return [
                    {
                        "conid": "658663572",
                        "companyHeader": "US Fed Funds Target Rate - FORECASTX",
                        "symbol": "FF",
                        "description": "FORECASTX",
                        "sections": [{"secType": "EC"}],
                    }
                ]
            return []
        if parsed.path.endswith("/iserver/secdef/info"):
            return {
                "conid": "658663572",
                "companyName": "US Fed Funds Target Rate",
                "exchange": "FORECASTX",
                "secType": "IND",
                "right": "?",
                "strike": 0.0,
            }
        raise AssertionError(f"unexpected URL {url}")

    report = fetch_ibkr_forecastex_readonly_snapshot(
        output_dir=tmp_path / "snapshots",
        search_terms="FF",
        http_get=fake_http,
    )

    serialized = json.dumps(report)
    assert "final_tradable_forecastex_contract_not_found" in serialized
    assert "missing_call_put_right" in serialized
    assert "missing_expiry_or_month" in serialized
    assert "missing_strike_or_event_threshold" in serialized
    assert report["summary"]["normalized_possible_candidate_count"] == 0
    assert "PAPER_CANDIDATE" not in serialized


def test_contract_info_empty_row_does_not_become_fake_tradable(tmp_path: Path) -> None:
    def fake_http(url: str, headers: dict[str, str], timeout_seconds: float) -> object:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("/iserver/auth/status"):
            return {"authenticated": True, "connected": True}
        if parsed.path.endswith("/iserver/secdef/search"):
            if query.get("symbol") == ["FF"] and "secType" not in query and "name" not in query:
                return [
                    {
                        "conid": "658663572",
                        "companyHeader": "US Fed Funds Target Rate - FORECASTX",
                        "symbol": "FF",
                        "sections": [{"secType": "IND", "exchange": "FORECASTX;"}, {"secType": "EC", "months": "JUN26"}],
                    }
                ]
            return []
        if parsed.path.endswith("/iserver/secdef/strikes"):
            return {"strikes": [450], "rights": ["C"], "months": ["JUN26"]}
        if parsed.path.endswith("/iserver/secdef/info"):
            if "strike" in query:
                return []
            return {"conid": "658663572", "exchange": "FORECASTX", "secType": "IND", "right": "?"}
        raise AssertionError(f"unexpected URL {url}")

    report = fetch_ibkr_forecastex_readonly_snapshot(
        output_dir=tmp_path / "snapshots",
        search_terms="FF",
        http_get=fake_http,
    )

    assert report["summary"]["normalized_possible_candidate_count"] == 0
    assert report["summary"]["forecastx_tradable_contract_candidates"] == 0
    assert "contract_info_empty_for_strike_combo" in json.dumps(report)
    assert "PAPER_CANDIDATE" not in json.dumps(report)


def test_connection_refused_on_followup_produces_partial_report(tmp_path: Path) -> None:
    def fake_http(url: str, headers: dict[str, str], timeout_seconds: float) -> object:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("/iserver/auth/status"):
            return {"authenticated": True, "connected": True}
        if parsed.path.endswith("/iserver/secdef/search"):
            if query.get("symbol") == ["FF"] and "secType" not in query and "name" not in query:
                return [
                    {
                        "conid": "658663572",
                        "companyHeader": "US Fed Funds Target Rate - FORECASTX",
                        "symbol": "FF",
                        "sections": [{"secType": "IND", "exchange": "FORECASTX;"}, {"secType": "EC", "months": "JUN26"}],
                    }
                ]
            return []
        if parsed.path.endswith("/iserver/secdef/strikes"):
            raise ConnectionRefusedError("local gateway refused connection")
        if parsed.path.endswith("/iserver/secdef/info"):
            return {"conid": "658663572", "exchange": "FORECASTX", "secType": "IND", "right": "?"}
        raise AssertionError(f"unexpected URL {url}")

    report = fetch_ibkr_forecastex_readonly_snapshot(
        output_dir=tmp_path / "snapshots",
        search_terms="FF",
        http_get=fake_http,
    )

    assert report["status"] == "LOCAL_GATEWAY_SESSION_DROPPED"
    assert report["summary"]["raw_files_written"] >= 1
    assert report["summary"]["forecastx_underlier_candidates"] >= 1
    assert report["summary"]["followup_errors"] >= 1
    assert "ibkr_gateway_connection_refused" in report["blockers"]
    assert "ibkr_gateway_session_dropped_or_unreachable" in report["blockers"]
    assert "ibkr_readonly_endpoint_failed" in report["blockers"]
    assert "PAPER_CANDIDATE" not in json.dumps(report)


def test_timeout_on_followup_produces_timeout_blocker(tmp_path: Path) -> None:
    def fake_http(url: str, headers: dict[str, str], timeout_seconds: float) -> object:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("/iserver/auth/status"):
            return {"authenticated": True, "connected": True}
        if parsed.path.endswith("/iserver/secdef/search"):
            if query.get("symbol") == ["FF"] and "secType" not in query and "name" not in query:
                return [
                    {
                        "conid": "658663572",
                        "companyHeader": "US Fed Funds Target Rate - FORECASTX",
                        "symbol": "FF",
                        "sections": [{"secType": "IND", "exchange": "FORECASTX;"}, {"secType": "EC", "months": "JUN26"}],
                    }
                ]
            return []
        if parsed.path.endswith("/iserver/secdef/strikes"):
            raise TimeoutError("gateway request timed out")
        if parsed.path.endswith("/iserver/secdef/info"):
            return {"conid": "658663572", "exchange": "FORECASTX", "secType": "IND", "right": "?"}
        raise AssertionError(f"unexpected URL {url}")

    report = fetch_ibkr_forecastex_readonly_snapshot(
        output_dir=tmp_path / "snapshots",
        search_terms="FF",
        http_get=fake_http,
    )

    assert report["status"] == "PARTIAL_GATEWAY_FAILURE"
    assert "ibkr_gateway_request_timeout" in report["blockers"]
    assert "ibkr_readonly_endpoint_failed" in report["blockers"]
    assert report["summary"]["followup_errors"] >= 1
    assert "PAPER_CANDIDATE" not in json.dumps(report)


def test_repeated_followup_failures_stop_at_max_followup_errors(tmp_path: Path) -> None:
    def fake_http(url: str, headers: dict[str, str], timeout_seconds: float) -> object:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("/iserver/auth/status"):
            return {"authenticated": True, "connected": True}
        if parsed.path.endswith("/iserver/secdef/search"):
            if query.get("symbol") == ["FF"] and "secType" not in query and "name" not in query:
                return [
                    {
                        "conid": "658663572",
                        "companyHeader": "US Fed Funds Target Rate - FORECASTX",
                        "symbol": "FF",
                        "sections": [{"secType": "IND", "exchange": "FORECASTX;"}, {"secType": "EC", "months": "JUN26;JUL26;AUG26"}],
                    }
                ]
            return []
        if parsed.path.endswith("/iserver/secdef/strikes"):
            raise ConnectionRefusedError("local gateway refused connection")
        raise AssertionError(f"unexpected URL {url}")

    report = fetch_ibkr_forecastex_readonly_snapshot(
        output_dir=tmp_path / "snapshots",
        search_terms="FF",
        max_followup_errors=2,
        http_get=fake_http,
    )

    assert report["status"] == "LOCAL_GATEWAY_SESSION_DROPPED"
    assert report["summary"]["followup_errors"] == 2
    assert report["summary"]["strikes_requests_attempted"] == 2
    assert report["summary"]["forecastx_underlier_candidates"] >= 1
    assert "PAPER_CANDIDATE" not in json.dumps(report)


def test_default_http_get_connection_refused_from_urlopen_is_controlled(monkeypatch: pytest.MonkeyPatch) -> None:
    def refused_urlopen(*args: object, **kwargs: object) -> object:
        raise ConnectionRefusedError(10061, "No connection could be made because the target machine actively refused it")

    monkeypatch.setattr(ibkr_access, "urlopen", refused_urlopen)

    with pytest.raises(IBKRReadOnlyRequestError) as exc_info:
        ibkr_access._default_http_get(
            "https://localhost:5000/v1/api/iserver/secdef/search?symbol=FF",
            {},
            0.1,
        )

    assert "ibkr_gateway_connection_refused" in exc_info.value.blockers
    assert "ibkr_gateway_session_dropped_or_unreachable" in exc_info.value.blockers
    assert "ibkr_readonly_endpoint_failed" in exc_info.value.blockers


def test_default_http_get_urlerror_connection_refused_reason_is_controlled(monkeypatch: pytest.MonkeyPatch) -> None:
    def refused_urlopen(*args: object, **kwargs: object) -> object:
        raise URLError(ConnectionRefusedError(10061, "No connection could be made because the target machine actively refused it"))

    monkeypatch.setattr(ibkr_access, "urlopen", refused_urlopen)

    with pytest.raises(IBKRReadOnlyRequestError) as exc_info:
        ibkr_access._default_http_get(
            "https://localhost:5000/v1/api/iserver/secdef/strikes?conid=658663572",
            {},
            0.1,
        )

    assert "ibkr_gateway_connection_refused" in exc_info.value.blockers
    assert "ibkr_gateway_session_dropped_or_unreachable" in exc_info.value.blockers
    assert "ibkr_readonly_endpoint_failed" in exc_info.value.blockers


def test_default_http_get_generic_winerror_oserror_is_controlled(monkeypatch: pytest.MonkeyPatch) -> None:
    def refused_urlopen(*args: object, **kwargs: object) -> object:
        raise OSError(10061, "No connection could be made because the target machine actively refused it")

    monkeypatch.setattr(ibkr_access, "urlopen", refused_urlopen)

    with pytest.raises(IBKRReadOnlyRequestError) as exc_info:
        ibkr_access._default_http_get(
            "https://localhost:5000/v1/api/iserver/secdef/strikes?conid=658663572",
            {},
            0.1,
        )

    assert "ibkr_gateway_connection_refused" in exc_info.value.blockers
    assert "ibkr_gateway_session_dropped_or_unreachable" in exc_info.value.blockers
    assert "ibkr_readonly_endpoint_failed" in exc_info.value.blockers


def test_default_http_get_socket_timeout_is_controlled(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout_urlopen(*args: object, **kwargs: object) -> object:
        raise socket.timeout("timed out")

    monkeypatch.setattr(ibkr_access, "urlopen", timeout_urlopen)

    with pytest.raises(IBKRReadOnlyRequestError) as exc_info:
        ibkr_access._default_http_get(
            "https://localhost:5000/v1/api/iserver/secdef/strikes?conid=658663572",
            {},
            0.1,
        )

    assert "ibkr_gateway_request_timeout" in exc_info.value.blockers
    assert "ibkr_readonly_endpoint_failed" in exc_info.value.blockers


def test_keyboard_interrupt_is_not_swallowed(tmp_path: Path) -> None:
    def fake_http(url: str, headers: dict[str, str], timeout_seconds: float) -> object:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("/iserver/auth/status"):
            return {"authenticated": True, "connected": True}
        if parsed.path.endswith("/iserver/secdef/search"):
            if query.get("symbol") == ["FF"] and "secType" not in query and "name" not in query:
                return [
                    {
                        "conid": "658663572",
                        "companyHeader": "US Fed Funds Target Rate - FORECASTX",
                        "symbol": "FF",
                        "sections": [{"secType": "IND", "exchange": "FORECASTX;"}, {"secType": "EC", "months": "JUN26"}],
                    }
                ]
            return []
        if parsed.path.endswith("/iserver/secdef/strikes"):
            raise KeyboardInterrupt()
        raise AssertionError(f"unexpected URL {url}")

    with pytest.raises(KeyboardInterrupt):
        fetch_ibkr_forecastex_readonly_snapshot(
            output_dir=tmp_path / "snapshots",
            search_terms="FF",
            http_get=fake_http,
        )
