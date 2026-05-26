from __future__ import annotations

import http.client
import errno
import json
import re
import socket
import ssl
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


DEFAULT_IBKR_FORECASTEX_BASE_URL = "https://localhost:5000/v1/api"
IBKR_FORECASTEX_ACCESS_DOCTOR_SCHEMA_KIND = "ibkr_forecastex_access_doctor_v1"
IBKR_FORECASTEX_NORMALIZED_DRAFT_SCHEMA_KIND = "ibkr_forecastex_normalized_draft_v1"
IBKR_FORECASTEX_DISCOVERY_CANDIDATES_SCHEMA_KIND = "ibkr_forecastex_discovery_candidates_v1"
IBKR_FORECASTEX_RAW_SHAPE_SUMMARY_SCHEMA_KIND = "ibkr_forecastex_raw_shape_summary_v1"

DEFAULT_IBKR_FORECASTEX_SEARCH_TERMS = (
    "FORECASTX",
    "ForecastEx",
)
DEFAULT_IBKR_FORECASTEX_DOC_SEED_SYMBOLS = (
    "FF",
    "U",
    "BTC",
    "ETH",
    "FOMC",
    "CPI",
    "TEMP",
    "WEATHER",
)
DEFAULT_MAX_CONTRACT_INFO_REQUESTS = 50
DEFAULT_MAX_FOLLOWUP_ERRORS = 5

_NETWORK_REQUEST_EXCEPTIONS = (
    ConnectionRefusedError,
    ConnectionResetError,
    TimeoutError,
    URLError,
    http.client.HTTPException,
    ssl.SSLError,
    socket.timeout,
    OSError,
)
_REFUSED_ERRNOS = {errno.ECONNREFUSED, 10061}
_REFUSED_WINERRORS = {10061}
_TIMEOUT_ERRNOS = {errno.ETIMEDOUT, 10060}
_TIMEOUT_WINERRORS = {10060}
_UNREACHABLE_ERRNOS = {
    errno.ECONNRESET,
    errno.ENETUNREACH,
    errno.EHOSTUNREACH,
    10051,
    10054,
    10065,
}
_UNREACHABLE_WINERRORS = {
    10051,  # WSAENETUNREACH
    10054,  # WSAECONNRESET
    10065,  # WSAEHOSTUNREACH
}

IBKR_FORECASTEX_REQUIRED_BLOCKERS = (
    "ibkr_local_authenticated_session_required",
    "account_permission_review_required",
    "market_data_permission_review_required",
    "no_order_account_portfolio_calls",
    "settlement_rules_need_review",
)

_AUTH_STATUS_PATH = "/iserver/auth/status"
_DISCOVERY_PATHS = (
    ("/iserver/secdef/search", {"symbol": "FORECASTX", "name": "true"}),
    ("/iserver/secdef/search", {"symbol": "FORECASTX", "secType": "FOP", "name": "true"}),
)
_CONTRACT_DETAILS_PATH = "/iserver/secdef/info"
_STRIKES_PATH = "/iserver/secdef/strikes"
_MARKETDATA_SNAPSHOT_PATH = "/iserver/marketdata/snapshot"
_MARKETDATA_FIELDS = "31,84,85,86,88,6004,6509,7059,7636,7051,7057,7058"
_ALLOWED_PATH_PREFIXES = (
    "/iserver/auth/status",
    "/iserver/secdef/search",
    "/iserver/secdef/info",
    "/iserver/secdef/strikes",
    "/iserver/marketdata/snapshot",
)
_FORBIDDEN_PATH_SEGMENTS = (
    "/account",
    "/portfolio",
    "/order",
    "/orders",
    "/trade",
    "/trades",
    "/reply",
    "/pa/",
)


HttpGet = Callable[[str, dict[str, str], float], Any]


class IBKRReadOnlyHTTPError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class IBKRReadOnlyRequestError(RuntimeError):
    def __init__(self, detail: str, blockers: list[str]) -> None:
        super().__init__(detail)
        self.detail = detail
        self.blockers = list(dict.fromkeys(blockers))


def build_ibkr_forecastex_access_doctor(
    *,
    base_url: str = DEFAULT_IBKR_FORECASTEX_BASE_URL,
    timeout_seconds: float = 5.0,
    allow_non_localhost: bool = False,
    http_get: HttpGet | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    timestamp = _utc_now(now)
    safety = _safety_block()
    local_check = _validate_local_base_url(base_url, allow_non_localhost=allow_non_localhost)
    if not local_check["allowed"]:
        return {
            "schema_kind": IBKR_FORECASTEX_ACCESS_DOCTOR_SCHEMA_KIND,
            "generated_at": timestamp.isoformat(),
            "base_url": _redacted_base_url(base_url),
            "status": "REFUSED_NON_LOCALHOST_BASE_URL",
            "reachable": False,
            "authenticated": False,
            "auth_status_endpoint": _AUTH_STATUS_PATH,
            "blockers": ["non_localhost_base_url_refused", "ibkr_local_authenticated_session_required"],
            "warnings": [local_check["reason"]],
            "operator_instructions": _operator_instructions(),
            "safety": safety,
        }

    getter = http_get or _default_http_get
    url = _build_url(base_url, _AUTH_STATUS_PATH)
    try:
        payload = getter(url, _safe_headers(), float(timeout_seconds))
    except IBKRReadOnlyHTTPError as exc:
        if exc.status_code in {401, 403}:
            return {
                "schema_kind": IBKR_FORECASTEX_ACCESS_DOCTOR_SCHEMA_KIND,
                "generated_at": timestamp.isoformat(),
                "base_url": _redacted_base_url(base_url),
                "status": "LOCAL_GATEWAY_REACHABLE_SESSION_NOT_AUTHENTICATED",
                "reachable": True,
                "authenticated": False,
                "auth_status_endpoint": _AUTH_STATUS_PATH,
                "auth_status_summary": {
                    "authenticated": False,
                    "http_status": exc.status_code,
                    "message": exc.detail[:200],
                },
                "blockers": list(IBKR_FORECASTEX_REQUIRED_BLOCKERS),
                "warnings": [f"HTTP {exc.status_code}: auth status requires a manual Client Portal login session."],
                "operator_instructions": _operator_instructions(),
                "safety": safety,
            }
        raise
    except IBKRReadOnlyRequestError as exc:
        return {
            "schema_kind": IBKR_FORECASTEX_ACCESS_DOCTOR_SCHEMA_KIND,
            "generated_at": timestamp.isoformat(),
            "base_url": _redacted_base_url(base_url),
            "status": "LOCAL_GATEWAY_UNREACHABLE",
            "reachable": False,
            "authenticated": False,
            "auth_status_endpoint": _AUTH_STATUS_PATH,
            "blockers": list(dict.fromkeys([*exc.blockers, "ibkr_local_gateway_unreachable", "ibkr_local_authenticated_session_required"])),
            "warnings": [f"{type(exc).__name__}: {_safe_exception_message(exc)}"],
            "operator_instructions": _operator_instructions(),
            "safety": safety,
        }
    except Exception as exc:  # noqa: BLE001 - report-only command should fail closed.
        return {
            "schema_kind": IBKR_FORECASTEX_ACCESS_DOCTOR_SCHEMA_KIND,
            "generated_at": timestamp.isoformat(),
            "base_url": _redacted_base_url(base_url),
            "status": "LOCAL_GATEWAY_UNREACHABLE",
            "reachable": False,
            "authenticated": False,
            "auth_status_endpoint": _AUTH_STATUS_PATH,
            "blockers": ["ibkr_local_gateway_unreachable", "ibkr_local_authenticated_session_required"],
            "warnings": [f"{type(exc).__name__}: {_safe_exception_message(exc)}"],
            "operator_instructions": _operator_instructions(),
            "safety": safety,
        }

    auth = _auth_status_from_payload(payload)
    blockers = list(IBKR_FORECASTEX_REQUIRED_BLOCKERS)
    if auth["authenticated"]:
        blockers = [blocker for blocker in blockers if blocker != "ibkr_local_authenticated_session_required"]
    return {
        "schema_kind": IBKR_FORECASTEX_ACCESS_DOCTOR_SCHEMA_KIND,
        "generated_at": timestamp.isoformat(),
        "base_url": _redacted_base_url(base_url),
        "status": "OK" if auth["authenticated"] else "LOCAL_GATEWAY_REACHABLE_SESSION_NOT_AUTHENTICATED",
        "reachable": True,
        "authenticated": auth["authenticated"],
        "auth_status_endpoint": _AUTH_STATUS_PATH,
        "auth_status_summary": auth,
        "blockers": blockers,
        "warnings": [],
        "operator_instructions": _operator_instructions(),
        "safety": safety,
    }


def fetch_ibkr_forecastex_readonly_snapshot(
    *,
    output_dir: Path,
    base_url: str = DEFAULT_IBKR_FORECASTEX_BASE_URL,
    timeout_seconds: float = 8.0,
    max_contracts: int = 100,
    max_contract_info_requests: int = DEFAULT_MAX_CONTRACT_INFO_REQUESTS,
    max_followup_errors: int = DEFAULT_MAX_FOLLOWUP_ERRORS,
    search_terms: str | None = None,
    forecastx_doc_seed: bool = True,
    seed_conids_path: Path | None = None,
    allow_non_localhost: bool = False,
    http_get: HttpGet | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    timestamp = _utc_now(now)
    captured_at = timestamp.isoformat()
    safety = _safety_block()
    local_check = _validate_local_base_url(base_url, allow_non_localhost=allow_non_localhost)
    snapshot_dir = output_dir / _timestamp_slug(timestamp)
    raw_files_written: list[str] = []
    warnings: list[str] = []
    blockers = list(IBKR_FORECASTEX_REQUIRED_BLOCKERS)
    records: list[dict[str, Any]] = []
    discovery_candidates: list[dict[str, Any]] = []
    discovery_raw_response_count = 0
    marketdata_permission_missing = False
    seed_conids = _read_seed_conids(seed_conids_path)
    followup_stats: Counter[str] = Counter(
        {
            "strikes_requests_attempted": 0,
            "strikes_rows_found": 0,
            "contract_info_requests_attempted": 0,
            "missing_secdef_parameter_count": 0,
        }
    )

    if not local_check["allowed"]:
        warnings.append(local_check["reason"])
        return _fetch_report(
            captured_at=captured_at,
            base_url=base_url,
            status="REFUSED_NON_LOCALHOST_BASE_URL",
            reachable=False,
            authenticated=False,
            raw_files_written=raw_files_written,
            records=records,
            blockers=["non_localhost_base_url_refused", *blockers],
            warnings=warnings,
            output_dir=output_dir,
            snapshot_dir=None,
            endpoints_attempted=[],
            search_terms=_prepare_search_terms(search_terms, forecastx_doc_seed=forecastx_doc_seed, seed_conids=seed_conids),
            seed_conids=seed_conids,
            discovery_candidates=discovery_candidates,
            discovery_raw_response_count=discovery_raw_response_count,
            discovery_statuses=["REFUSED_NON_LOCALHOST_BASE_URL"],
            safety=safety,
            followup_stats=followup_stats,
        )

    getter = http_get or _default_http_get
    endpoints_attempted: list[str] = []

    auth_report = build_ibkr_forecastex_access_doctor(
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        allow_non_localhost=allow_non_localhost,
        http_get=getter,
        now=timestamp,
    )
    endpoints_attempted.append(_AUTH_STATUS_PATH)
    authenticated = bool(auth_report.get("authenticated"))
    reachable = bool(auth_report.get("reachable"))
    if not reachable:
        return _fetch_report(
            captured_at=captured_at,
            base_url=base_url,
            status="LOCAL_GATEWAY_UNREACHABLE",
            reachable=False,
            authenticated=False,
            raw_files_written=raw_files_written,
            records=records,
            blockers=list(dict.fromkeys(auth_report.get("blockers", blockers))),
            warnings=list(auth_report.get("warnings", [])),
            output_dir=output_dir,
            snapshot_dir=None,
            endpoints_attempted=endpoints_attempted,
            search_terms=_prepare_search_terms(search_terms, forecastx_doc_seed=forecastx_doc_seed, seed_conids=seed_conids),
            seed_conids=seed_conids,
            discovery_candidates=discovery_candidates,
            discovery_raw_response_count=discovery_raw_response_count,
            discovery_statuses=["LOCAL_GATEWAY_UNREACHABLE"],
            safety=safety,
            followup_stats=followup_stats,
        )
    if not authenticated:
        return _fetch_report(
            captured_at=captured_at,
            base_url=base_url,
            status="LOCAL_GATEWAY_REACHABLE_SESSION_NOT_AUTHENTICATED",
            reachable=True,
            authenticated=False,
            raw_files_written=raw_files_written,
            records=records,
            blockers=list(dict.fromkeys(auth_report.get("blockers", blockers))),
            warnings=list(auth_report.get("warnings", [])),
            output_dir=output_dir,
            snapshot_dir=None,
            endpoints_attempted=endpoints_attempted,
            search_terms=_prepare_search_terms(search_terms, forecastx_doc_seed=forecastx_doc_seed, seed_conids=seed_conids),
            seed_conids=seed_conids,
            discovery_candidates=discovery_candidates,
            discovery_raw_response_count=discovery_raw_response_count,
            discovery_statuses=["ACCOUNT_PERMISSION_REVIEW_REQUIRED"],
            safety=safety,
            followup_stats=followup_stats,
        )
    if authenticated:
        blockers = [blocker for blocker in blockers if blocker != "ibkr_local_authenticated_session_required"]

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    discovery_rows: list[dict[str, Any]] = []
    terms = _prepare_search_terms(search_terms, forecastx_doc_seed=forecastx_doc_seed, seed_conids=seed_conids)
    search_plan = _search_endpoint_plan(terms)
    stop_discovery = False
    for index, (term, path, params) in enumerate(search_plan):
        if stop_discovery:
            break
        endpoint_name = f"secdef_search_{index + 1}_{_safe_filename_token(term)}"
        endpoints_attempted.append(path)
        followup_stats["search_requests_attempted"] += 1
        try:
            payload = _safe_get_json(
                getter=getter,
                base_url=base_url,
                path=path,
                params=params,
                timeout_seconds=timeout_seconds,
            )
            raw_path = _write_raw_payload(
                snapshot_dir=snapshot_dir,
                endpoint_name=endpoint_name,
                path=path,
                params=params,
                payload=payload,
                captured_at=captured_at,
            )
            raw_files_written.append(str(raw_path))
            discovery_raw_response_count += 1
            rows = _rows_from_payload(payload)
            forecastx_rows = _forecastx_contract_rows(payload)
            discovery_candidates.extend(
                _candidate_rows(
                    rows,
                    search_term=term,
                    endpoint=path,
                    raw_candidate_count=len(rows),
                    source="search",
                )
            )
            discovery_rows.extend(forecastx_rows)
            for underlier_index, underlier in enumerate(row for row in forecastx_rows if _is_underlier_candidate(row)):
                underlier_conid = str(underlier.get("conid") or "").strip()
                if not underlier_conid:
                    continue
                followup_term = f"underlier:{underlier.get('symbol') or underlier_conid}"
                months = _candidate_months(underlier)
                if not months:
                    missing_row = _missing_secdef_parameter_row(
                        underlier,
                        underlier_conid=underlier_conid,
                        missing=("month", "strike", "right"),
                        stage="UNDERLIER_FOUND",
                    )
                    followup_stats["missing_secdef_parameter_count"] += 1
                    discovery_candidates.extend(
                        _candidate_rows(
                            [missing_row],
                            search_term=followup_term,
                            endpoint=_CONTRACT_DETAILS_PATH,
                            raw_candidate_count=1,
                            source="underlier_followup",
                        )
                    )
                    discovery_rows.append(missing_row)

                for month in months:
                    followup_name = f"secdef_strikes_{index + 1}_{underlier_index + 1}_{_safe_filename_token(underlier_conid)}_{_safe_filename_token(month)}"
                    followup_params = {"conid": underlier_conid, "secType": "EC", "month": month, "exchange": "FORECASTX"}
                    endpoints_attempted.append(_STRIKES_PATH)
                    followup_stats["followup_requests_attempted"] += 1
                    followup_stats["strikes_requests_attempted"] += 1
                    try:
                        strikes_payload = _safe_get_json(
                            getter=getter,
                            base_url=base_url,
                            path=_STRIKES_PATH,
                            params=followup_params,
                            timeout_seconds=timeout_seconds,
                        )
                        raw_path = _write_raw_payload(
                            snapshot_dir=snapshot_dir,
                            endpoint_name=followup_name,
                            path=_STRIKES_PATH,
                            params=followup_params,
                            payload=strikes_payload,
                            captured_at=captured_at,
                        )
                        raw_files_written.append(str(raw_path))
                        discovery_raw_response_count += 1
                        combinations, incomplete_rows, strike_count = _contract_info_combinations_from_strikes_payload(
                            strikes_payload,
                            underlier=underlier,
                            underlier_conid=underlier_conid,
                            month=month,
                        )
                        followup_stats["strikes_rows_found"] += strike_count
                        if incomplete_rows:
                            followup_stats["missing_secdef_parameter_count"] += len(incomplete_rows)
                            discovery_candidates.extend(
                                _candidate_rows(
                                    incomplete_rows,
                                    search_term=followup_term,
                                    endpoint=_STRIKES_PATH,
                                    raw_candidate_count=len(incomplete_rows),
                                    source="underlier_followup",
                                )
                            )
                            discovery_rows.extend(incomplete_rows)
                        for combo in combinations:
                            if followup_stats["contract_info_requests_attempted"] >= max(0, int(max_contract_info_requests)):
                                if "contract_info_request_cap_reached" not in warnings:
                                    warnings.append("contract_info_request_cap_reached")
                                break
                            info_params = {
                                "conid": underlier_conid,
                                "secType": combo["secType"],
                                "month": combo["month"],
                                "strike": combo["strike"],
                                "right": combo["right"],
                                "exchange": combo["exchange"],
                            }
                            info_name = (
                                f"secdef_info_contract_{index + 1}_{underlier_index + 1}_"
                                f"{_safe_filename_token(underlier_conid)}_{_safe_filename_token(combo['month'])}_"
                                f"{_safe_filename_token(str(combo['strike']))}_{_safe_filename_token(combo['right'])}"
                            )
                            endpoints_attempted.append(_CONTRACT_DETAILS_PATH)
                            followup_stats["followup_requests_attempted"] += 1
                            followup_stats["contract_info_requests_attempted"] += 1
                            try:
                                info_payload = _safe_get_json(
                                    getter=getter,
                                    base_url=base_url,
                                    path=_CONTRACT_DETAILS_PATH,
                                    params=info_params,
                                    timeout_seconds=timeout_seconds,
                                )
                                raw_path = _write_raw_payload(
                                    snapshot_dir=snapshot_dir,
                                    endpoint_name=info_name,
                                    path=_CONTRACT_DETAILS_PATH,
                                    params=info_params,
                                    payload=info_payload,
                                    captured_at=captured_at,
                                )
                                raw_files_written.append(str(raw_path))
                                discovery_raw_response_count += 1
                                contract_rows = _rows_from_payload(info_payload)
                                if not contract_rows:
                                    contract_rows = [
                                        _contract_info_empty_row(
                                            underlier,
                                            underlier_conid=underlier_conid,
                                            combo=combo,
                                        )
                                    ]
                                for row in contract_rows:
                                    _apply_contract_info_context(row, underlier=underlier, underlier_conid=underlier_conid, combo=combo)
                                discovery_candidates.extend(
                                    _candidate_rows(
                                        contract_rows,
                                        search_term=followup_term,
                                        endpoint=_CONTRACT_DETAILS_PATH,
                                        raw_candidate_count=len(contract_rows),
                                        source="underlier_followup",
                                    )
                                )
                                discovery_rows.extend([row for row in contract_rows if _is_forecastx_like(row)])
                            except IBKRReadOnlyRequestError as exc:
                                _record_readonly_request_error(
                                    exc,
                                    endpoint_name=info_name,
                                    endpoint=_CONTRACT_DETAILS_PATH,
                                    search_term=followup_term,
                                    source="underlier_followup",
                                    blockers=blockers,
                                    warnings=warnings,
                                    discovery_candidates=discovery_candidates,
                                    followup_stats=followup_stats,
                                    conid=underlier_conid,
                                )
                                if _followup_error_limit_reached(followup_stats, max_followup_errors):
                                    stop_discovery = True
                                    break
                            except Exception as exc:  # noqa: BLE001
                                warnings.append(f"{info_name}: {type(exc).__name__}: {_safe_exception_message(exc)}")
                        if stop_discovery:
                            break
                    except IBKRReadOnlyRequestError as exc:
                        _record_readonly_request_error(
                            exc,
                            endpoint_name=followup_name,
                            endpoint=_STRIKES_PATH,
                            search_term=followup_term,
                            source="underlier_followup",
                            blockers=blockers,
                            warnings=warnings,
                            discovery_candidates=discovery_candidates,
                            followup_stats=followup_stats,
                            conid=underlier_conid,
                        )
                        if _followup_error_limit_reached(followup_stats, max_followup_errors):
                            stop_discovery = True
                            break
                    except Exception as exc:  # noqa: BLE001
                        warnings.append(f"{followup_name}: {type(exc).__name__}: {_safe_exception_message(exc)}")
                if stop_discovery:
                    break

                followup_name = f"secdef_info_underlier_{index + 1}_{underlier_index + 1}_{_safe_filename_token(underlier_conid)}"
                followup_params = {"conid": underlier_conid}
                endpoints_attempted.append(_CONTRACT_DETAILS_PATH)
                followup_stats["followup_requests_attempted"] += 1
                try:
                    followup_payload = _safe_get_json(
                        getter=getter,
                        base_url=base_url,
                        path=_CONTRACT_DETAILS_PATH,
                        params=followup_params,
                        timeout_seconds=timeout_seconds,
                    )
                    raw_path = _write_raw_payload(
                        snapshot_dir=snapshot_dir,
                        endpoint_name=followup_name,
                        path=_CONTRACT_DETAILS_PATH,
                        params=followup_params,
                        payload=followup_payload,
                        captured_at=captured_at,
                    )
                    raw_files_written.append(str(raw_path))
                    discovery_raw_response_count += 1
                    followup_rows = _rows_from_payload(followup_payload)
                    if not followup_rows:
                        followup_rows = [
                            {
                                "conid": underlier_conid,
                                "symbol": underlier.get("symbol"),
                                "companyHeader": underlier.get("companyHeader"),
                                "description": underlier.get("description"),
                                "exchange": "FORECASTX",
                                "_forecastx_underlier_candidate": True,
                                "_underlier_conid": underlier_conid,
                                "_discovery_stage": "CONTRACT_INFO_FOUND",
                                "_extra_blockers": ["final_tradable_forecastex_contract_not_found"],
                            }
                        ]
                    for row in followup_rows:
                        row.setdefault("_underlier_conid", underlier_conid)
                        row.setdefault("symbol", underlier.get("symbol"))
                        row.setdefault("_discovery_stage", "CONTRACT_INFO_FOUND")
                        row.setdefault("_extra_blockers", [])
                        if not _is_tradable_contract_candidate(row):
                            row.setdefault("_forecastx_underlier_candidate", True)
                            row["_extra_blockers"].append("final_tradable_forecastex_contract_not_found")
                    discovery_candidates.extend(
                        _candidate_rows(
                            followup_rows,
                            search_term=followup_term,
                            endpoint=_CONTRACT_DETAILS_PATH,
                            raw_candidate_count=len(followup_rows),
                            source="underlier_followup",
                        )
                    )
                    discovery_rows.extend([row for row in followup_rows if _is_forecastx_like(row)])
                except IBKRReadOnlyRequestError as exc:
                    _record_readonly_request_error(
                        exc,
                        endpoint_name=followup_name,
                        endpoint=_CONTRACT_DETAILS_PATH,
                        search_term=followup_term,
                        source="underlier_followup",
                        blockers=blockers,
                        warnings=warnings,
                        discovery_candidates=discovery_candidates,
                        followup_stats=followup_stats,
                        conid=underlier_conid,
                    )
                    if _followup_error_limit_reached(followup_stats, max_followup_errors):
                        stop_discovery = True
                        break
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"{followup_name}: {type(exc).__name__}: {_safe_exception_message(exc)}")
        except IBKRReadOnlyRequestError as exc:
            _record_readonly_request_error(
                exc,
                endpoint_name=endpoint_name,
                endpoint=path,
                search_term=term,
                source="search",
                blockers=blockers,
                warnings=warnings,
                discovery_candidates=discovery_candidates,
                followup_stats=followup_stats,
            )
            if _followup_error_limit_reached(followup_stats, max_followup_errors):
                stop_discovery = True
        except Exception as exc:  # noqa: BLE001 - fail closed and continue.
            warnings.append(f"{endpoint_name}: {type(exc).__name__}: {_safe_exception_message(exc)}")

    seed_rows: list[dict[str, Any]] = []
    for seed_index, conid in enumerate(seed_conids):
        if stop_discovery:
            break
        endpoint_name = f"secdef_info_seed_{seed_index + 1}_{_safe_filename_token(conid)}"
        endpoints_attempted.append(_CONTRACT_DETAILS_PATH)
        followup_stats["followup_requests_attempted"] += 1
        try:
            payload = _safe_get_json(
                getter=getter,
                base_url=base_url,
                path=_CONTRACT_DETAILS_PATH,
                params={"conid": conid},
                timeout_seconds=timeout_seconds,
            )
            raw_path = _write_raw_payload(
                snapshot_dir=snapshot_dir,
                endpoint_name=endpoint_name,
                path=_CONTRACT_DETAILS_PATH,
                params={"conid": conid},
                payload=payload,
                captured_at=captured_at,
            )
            raw_files_written.append(str(raw_path))
            discovery_raw_response_count += 1
            rows = _rows_from_payload(payload)
            if not rows:
                rows = [
                    {
                        "conid": conid,
                        "exchange": "FORECASTX",
                        "_discovery_stage": "CONTRACT_INFO_FOUND",
                        "_extra_blockers": ["seed_conid_contract_details_missing"],
                    }
                ]
            for row in rows:
                row.setdefault("conid", conid)
                row.setdefault("_discovery_stage", "CONTRACT_INFO_FOUND")
                row.setdefault("_extra_blockers", [])
                if not _is_forecastx_like(row):
                    row["_extra_blockers"].append("operator_seed_conid_requires_manual_forecastex_confirmation")
            discovery_candidates.extend(
                _candidate_rows(
                    rows,
                    search_term=f"seed_conid:{conid}",
                    endpoint=_CONTRACT_DETAILS_PATH,
                    raw_candidate_count=len(rows),
                    source="seed_conid",
                    seed_conid=conid,
                )
            )
            seed_rows.extend(rows)
        except IBKRReadOnlyRequestError as exc:
            _record_readonly_request_error(
                exc,
                endpoint_name=endpoint_name,
                endpoint=_CONTRACT_DETAILS_PATH,
                search_term=f"seed_conid:{conid}",
                source="seed_conid",
                blockers=blockers,
                warnings=warnings,
                discovery_candidates=discovery_candidates,
                followup_stats=followup_stats,
                conid=conid,
            )
            if _followup_error_limit_reached(followup_stats, max_followup_errors):
                stop_discovery = True
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{endpoint_name}: {type(exc).__name__}: {_safe_exception_message(exc)}")
            discovery_candidates.append(
                {
                    "source": "seed_conid",
                    "search_term": f"seed_conid:{conid}",
                    "endpoint": _CONTRACT_DETAILS_PATH,
                    "raw_candidate_count": 0,
                    "conid": conid,
                    "symbol": None,
                    "localSymbol": None,
                    "description": None,
                    "secType": None,
                    "right": None,
                    "exchange": None,
                    "normalized_possible": False,
                    "blockers": ["seed_conid_contract_details_unavailable"],
                }
            )

    discovery_rows.extend(seed_rows)
    contracts = _dedupe_contracts(discovery_rows)[: max(0, int(max_contracts))]
    marketdata_by_conid: dict[str, dict[str, Any]] = {}
    marketdata_contracts = [row for row in contracts if _is_tradable_contract_candidate(row)]
    if marketdata_contracts:
        conids = [str(row.get("conid")) for row in marketdata_contracts if str(row.get("conid") or "").strip()]
        if conids:
            endpoints_attempted.append(_MARKETDATA_SNAPSHOT_PATH)
            followup_stats["followup_requests_attempted"] += 1
            try:
                payload = _safe_get_json(
                    getter=getter,
                    base_url=base_url,
                    path=_MARKETDATA_SNAPSHOT_PATH,
                    params={"conids": ",".join(conids), "fields": _MARKETDATA_FIELDS},
                    timeout_seconds=timeout_seconds,
                )
                raw_path = _write_raw_payload(
                    snapshot_dir=snapshot_dir,
                    endpoint_name="marketdata_snapshot",
                    path=_MARKETDATA_SNAPSHOT_PATH,
                    params={"conids": ",".join(conids), "fields": _MARKETDATA_FIELDS},
                    payload=payload,
                    captured_at=captured_at,
                )
                raw_files_written.append(str(raw_path))
                marketdata_by_conid = _marketdata_by_conid(payload)
            except IBKRReadOnlyRequestError as exc:
                _record_readonly_request_error(
                    exc,
                    endpoint_name="marketdata_snapshot",
                    endpoint=_MARKETDATA_SNAPSHOT_PATH,
                    search_term="marketdata_snapshot",
                    source="marketdata",
                    blockers=blockers,
                    warnings=warnings,
                    discovery_candidates=discovery_candidates,
                    followup_stats=followup_stats,
                )
                marketdata_permission_missing = True
                if "market_data_permission_review_required" not in blockers:
                    blockers.append("market_data_permission_review_required")
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"marketdata_snapshot: {type(exc).__name__}: {_safe_exception_message(exc)}")
                marketdata_permission_missing = True
                if "market_data_permission_review_required" not in blockers:
                    blockers.append("market_data_permission_review_required")

    for contract in contracts:
        conid = str(contract.get("conid") or "").strip()
        records.append(
            _normalize_forecastex_record(
                contract,
                marketdata_by_conid.get(conid, {}),
                captured_at=captured_at,
                raw_source_files=raw_files_written,
                authenticated=authenticated,
            )
        )

    if not contracts:
        warnings.append("No ForecastEx/FORECASTX contracts found from read-only secdef discovery.")
    gateway_failure_status = _gateway_failure_status(followup_stats)
    discovery_statuses = _discovery_statuses(
        authenticated=authenticated,
        candidates=discovery_candidates,
        contracts=contracts,
        seed_conids=seed_conids,
        marketdata_permission_missing=marketdata_permission_missing,
        marketdata_row_count=len(marketdata_by_conid),
        gateway_failure_status=gateway_failure_status,
    )
    report_blockers = _summary_blockers(records, blockers)
    return _fetch_report(
        captured_at=captured_at,
        base_url=base_url,
        status=gateway_failure_status or "OK",
        reachable=reachable,
        authenticated=authenticated,
        raw_files_written=raw_files_written,
        records=records,
        blockers=report_blockers,
        warnings=warnings,
        output_dir=output_dir,
        snapshot_dir=snapshot_dir,
        endpoints_attempted=endpoints_attempted,
        search_terms=terms,
        seed_conids=seed_conids,
        discovery_candidates=discovery_candidates,
        discovery_raw_response_count=discovery_raw_response_count,
        discovery_statuses=discovery_statuses,
        safety=safety,
        followup_stats=followup_stats,
    )


def write_ibkr_forecastex_access_doctor_file(
    *,
    json_output: Path,
    base_url: str = DEFAULT_IBKR_FORECASTEX_BASE_URL,
    timeout_seconds: float = 5.0,
    allow_non_localhost: bool = False,
) -> dict[str, Any]:
    report = build_ibkr_forecastex_access_doctor(
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        allow_non_localhost=allow_non_localhost,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def write_ibkr_forecastex_readonly_snapshot_file(
    *,
    output_dir: Path,
    json_output: Path,
    discovery_json_output: Path,
    discovery_markdown_output: Path,
    base_url: str = DEFAULT_IBKR_FORECASTEX_BASE_URL,
    timeout_seconds: float = 8.0,
    max_contracts: int = 100,
    max_contract_info_requests: int = DEFAULT_MAX_CONTRACT_INFO_REQUESTS,
    max_followup_errors: int = DEFAULT_MAX_FOLLOWUP_ERRORS,
    search_terms: str | None = None,
    forecastx_doc_seed: bool = True,
    seed_conids_path: Path | None = None,
    allow_non_localhost: bool = False,
) -> dict[str, Any]:
    report = fetch_ibkr_forecastex_readonly_snapshot(
        output_dir=output_dir,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        max_contracts=max_contracts,
        max_contract_info_requests=max_contract_info_requests,
        max_followup_errors=max_followup_errors,
        search_terms=search_terms,
        forecastx_doc_seed=forecastx_doc_seed,
        seed_conids_path=seed_conids_path,
        allow_non_localhost=allow_non_localhost,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    discovery_report = report.get("discovery_report") if isinstance(report.get("discovery_report"), dict) else {}
    discovery_json_output.parent.mkdir(parents=True, exist_ok=True)
    discovery_markdown_output.parent.mkdir(parents=True, exist_ok=True)
    discovery_json_output.write_text(json.dumps(discovery_report, indent=2, sort_keys=True), encoding="utf-8")
    discovery_markdown_output.write_text(_discovery_markdown(discovery_report), encoding="utf-8")
    raw_shape_summary = build_ibkr_forecastex_raw_shape_summary(
        raw_files=[Path(path) for path in report.get("raw_files_written", [])],
        generated_at=str(report.get("generated_at") or ""),
        snapshot_dir=Path(str(report["snapshot_dir"])) if report.get("snapshot_dir") else None,
    )
    raw_shape_json = json_output.parent / "ibkr_forecastex_raw_shape_summary.json"
    raw_shape_markdown = json_output.parent / "ibkr_forecastex_raw_shape_summary.md"
    raw_shape_json.write_text(json.dumps(raw_shape_summary, indent=2, sort_keys=True), encoding="utf-8")
    raw_shape_markdown.write_text(_raw_shape_summary_markdown(raw_shape_summary), encoding="utf-8")
    return report


def build_ibkr_forecastex_raw_shape_summary(
    *,
    raw_files: list[Path],
    generated_at: str,
    snapshot_dir: Path | None = None,
) -> dict[str, Any]:
    files = [_raw_shape_file_summary(path) for path in raw_files]
    blockers = Counter(blocker for row in files for blocker in row.get("blockers", []))
    top_level_types = Counter(str(row.get("payload_top_level_type")) for row in files)
    endpoint_counts = Counter(str(row.get("path")) for row in files)
    forecastx_files = [row for row in files if row.get("forecastx_identifier_found")]
    final_tradable_files = [row for row in files if row.get("final_tradable_contract_fields_present")]
    return {
        "schema_kind": IBKR_FORECASTEX_RAW_SHAPE_SUMMARY_SCHEMA_KIND,
        "generated_at": generated_at,
        "snapshot_dir": str(snapshot_dir) if snapshot_dir else None,
        "permission": "saved_raw_shape_diagnostic_only",
        "raw_files_read": len(files),
        "files": files,
        "summary": {
            "raw_files_read": len(files),
            "forecastx_identifier_files": len(forecastx_files),
            "final_tradable_contract_field_files": len(final_tradable_files),
            "call_put_right_files": sum(1 for row in files if row.get("has_call_put_right")),
            "binary_yes_no_files": sum(1 for row in files if row.get("has_binary_yes_no")),
            "strike_field_files": sum(1 for row in files if row.get("has_strike_or_threshold")),
            "expiry_or_month_files": sum(1 for row in files if row.get("has_expiry_or_month")),
            "event_contract_field_files": sum(1 for row in files if row.get("has_event_contract_fields")),
            "payload_top_level_types": dict(sorted(top_level_types.items())),
            "endpoint_counts": dict(sorted(endpoint_counts.items())),
            "blockers_by_count": dict(sorted(blockers.items())),
            "final_tradable_contract_fields_present": bool(final_tradable_files),
            "final_tradable_contract_blockers": sorted(blockers),
        },
        "safety": _safety_block(),
    }


def _fetch_report(
    *,
    captured_at: str,
    base_url: str,
    status: str,
    reachable: bool,
    authenticated: bool,
    raw_files_written: list[str],
    records: list[dict[str, Any]],
    blockers: list[str],
    warnings: list[str],
    output_dir: Path,
    snapshot_dir: Path | None,
    endpoints_attempted: list[str],
    search_terms: list[str],
    seed_conids: list[str],
    discovery_candidates: list[dict[str, Any]],
    discovery_raw_response_count: int,
    discovery_statuses: list[str],
    safety: dict[str, bool],
    followup_stats: dict[str, int] | Counter[str] | None = None,
) -> dict[str, Any]:
    blockers_by_count = Counter(blocker for record in records for blocker in record.get("blockers", []))
    blockers_by_count.update(blockers)
    followup_stats = Counter(followup_stats or {})
    discovery_report = _discovery_report(
        captured_at=captured_at,
        base_url=base_url,
        output_dir=output_dir,
        snapshot_dir=snapshot_dir,
        endpoints_attempted=endpoints_attempted,
        raw_files_written=raw_files_written,
        search_terms=search_terms,
        seed_conids=seed_conids,
        candidates=discovery_candidates,
        records=records,
        raw_response_count=discovery_raw_response_count,
        statuses=discovery_statuses,
        warnings=warnings,
        safety=safety,
        followup_stats=followup_stats,
    )
    return {
        "schema_kind": IBKR_FORECASTEX_NORMALIZED_DRAFT_SCHEMA_KIND,
        "generated_at": captured_at,
        "base_url": _redacted_base_url(base_url),
        "status": status,
        "reachable": reachable,
        "authenticated": authenticated,
        "discovery_status": discovery_statuses[0] if discovery_statuses else None,
        "discovery_statuses": discovery_statuses,
        "permission": "local_gateway_read_only_diagnostic",
        "output_dir": str(output_dir),
        "snapshot_dir": str(snapshot_dir) if snapshot_dir else None,
        "endpoints_attempted": list(dict.fromkeys(endpoints_attempted)),
        "search_terms": search_terms,
        "seed_conids_count": len(seed_conids),
        "raw_files_written": raw_files_written,
        "records": records,
        "normalized_records": records,
        "discovery_report": discovery_report,
        "summary": {
            "raw_files_written": len(raw_files_written),
            "normalized_rows": len(records),
            "forecastx_rows": len(records),
            "documented_seed_ff_attempted": discovery_report.get("summary", {}).get("documented_seed_ff_attempted"),
            "ff_underlier_found": discovery_report.get("summary", {}).get("ff_underlier_found"),
            "forecastx_underlier_candidates": discovery_report.get("summary", {}).get("forecastx_underlier_candidates", 0),
            "forecastx_tradable_contract_candidates": discovery_report.get("summary", {}).get("forecastx_tradable_contract_candidates", 0),
            "forecastx_marketdata_rows": discovery_report.get("summary", {}).get("forecastx_marketdata_rows", 0),
            "strikes_requests_attempted": discovery_report.get("summary", {}).get("strikes_requests_attempted", 0),
            "strikes_rows_found": discovery_report.get("summary", {}).get("strikes_rows_found", 0),
            "contract_info_requests_attempted": discovery_report.get("summary", {}).get("contract_info_requests_attempted", 0),
            "missing_secdef_parameter_count": discovery_report.get("summary", {}).get("missing_secdef_parameter_count", 0),
            "search_requests_attempted": discovery_report.get("summary", {}).get("search_requests_attempted", 0),
            "followup_requests_attempted": discovery_report.get("summary", {}).get("followup_requests_attempted", 0),
            "followup_errors": discovery_report.get("summary", {}).get("followup_errors", 0),
            "candidates_so_far": len(discovery_candidates),
            "auth_status": discovery_report.get("summary", {}).get("auth_status"),
            "discovery_candidate_count": len(discovery_candidates),
            "forecastx_candidate_count": _count_forecastx_candidates(discovery_candidates),
            "normalized_possible_candidate_count": sum(1 for row in discovery_candidates if row.get("normalized_possible")),
            "seed_conids_count": len(seed_conids),
            "discovery_status": discovery_statuses[0] if discovery_statuses else None,
            "discovery_statuses": discovery_statuses,
            "blockers_by_count": dict(sorted(blockers_by_count.items())),
            "warnings": warnings,
        },
        "blockers": list(dict.fromkeys(blockers)),
        "warnings": warnings,
        "operator_instructions": _operator_instructions(),
        "safety": safety,
    }


def _normalize_forecastex_record(
    contract: dict[str, Any],
    quote: dict[str, Any],
    *,
    captured_at: str,
    raw_source_files: list[str],
    authenticated: bool,
) -> dict[str, Any]:
    right = _text(contract.get("right") or contract.get("putCall") or contract.get("side")).upper()
    outcome = "YES" if right == "C" else "NO" if right == "P" else None
    tradable = _is_tradable_contract_candidate(contract)
    underlier = _is_underlier_candidate(contract)
    discovery_stage = contract.get("_discovery_stage")
    if quote and tradable:
        discovery_stage = "MARKETDATA_FOUND"
    elif not discovery_stage:
        discovery_stage = "CONTRACT_INFO_FOUND" if tradable else "UNDERLIER_FOUND" if underlier else "DISCOVERY_ONLY"
    blockers = list(IBKR_FORECASTEX_REQUIRED_BLOCKERS)
    if authenticated:
        blockers = [blocker for blocker in blockers if blocker != "ibkr_local_authenticated_session_required"]
    if _price(quote, "bid", "84") is not None or _price(quote, "ask", "85") is not None:
        blockers = [blocker for blocker in blockers if blocker != "market_data_permission_review_required"]
    if not tradable:
        blockers.extend(_missing_final_tradable_contract_blockers(contract))
    blockers.extend(str(blocker) for blocker in contract.get("_extra_blockers", []) if blocker)
    blockers = list(dict.fromkeys(blockers))
    return {
        "venue": "ibkr_forecastex",
        "exchange": contract.get("exchange") or contract.get("listingExchange") or contract.get("exchangeCode") or "FORECASTX",
        "discovery_stage": discovery_stage,
        "forecastx_underlier_candidate": underlier,
        "underlier_conid": contract.get("_underlier_conid") or contract.get("underlier_conid") or contract.get("conid") if underlier else contract.get("_underlier_conid") or contract.get("underlier_conid"),
        "tradable_contract_candidate": tradable,
        "normalized_possible": tradable,
        "conid": contract.get("conid"),
        "symbol": contract.get("symbol"),
        "localSymbol": contract.get("localSymbol") or contract.get("local_symbol"),
        "companyHeader": contract.get("companyHeader"),
        "description": contract.get("description") or contract.get("companyName") or contract.get("contract_title"),
        "secType": contract.get("secType") or contract.get("assetClass"),
        "sections": contract.get("sections"),
        "right": right or None,
        "outcome": outcome,
        "outcome_mapping": {"C": "YES", "P": "NO"},
        "strike": contract.get("strike") or contract.get("_strike"),
        "event_threshold": contract.get("event_threshold") or contract.get("strike") or contract.get("_strike"),
        "bid": _price(quote, "bid", "84"),
        "ask": _price(quote, "ask", "85"),
        "bid_size": _price(quote, "bid_size", "86"),
        "ask_size": _price(quote, "ask_size", "88"),
        "last": _price(quote, "last", "31"),
        "maturity": contract.get("maturity") or contract.get("lastTradeDateOrContractMonth") or contract.get("_month"),
        "lastTradeDateOrContractMonth": contract.get("lastTradeDateOrContractMonth") or contract.get("_month"),
        "expiration": contract.get("expiration") or contract.get("maturityDate") or contract.get("lastTradingDay") or contract.get("lastTradeDateOrContractMonth"),
        "last_trade_time": contract.get("last_trade_time") or contract.get("lastTradeTime") or contract.get("lastTradingDate"),
        "settlement_rules_text": contract.get("settlement_rules_text") or contract.get("contract_terms_url_or_text"),
        "settlement_source": contract.get("settlement_source"),
        "settlement_source_url": contract.get("settlement_source_url"),
        "captured_at": captured_at,
        "quote_captured_at": captured_at if quote else None,
        "raw_source_files": raw_source_files,
        "missing_secdef_parameters": contract.get("_missing_secdef_parameters") or contract.get("missing_secdef_parameters") or [],
        "blockers": blockers,
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "paper_candidate_emitted": False,
    }


def _discovery_report(
    *,
    captured_at: str,
    base_url: str,
    output_dir: Path,
    snapshot_dir: Path | None,
    endpoints_attempted: list[str],
    raw_files_written: list[str],
    search_terms: list[str],
    seed_conids: list[str],
    candidates: list[dict[str, Any]],
    records: list[dict[str, Any]],
    raw_response_count: int,
    statuses: list[str],
    warnings: list[str],
    safety: dict[str, bool],
    followup_stats: Counter[str],
) -> dict[str, Any]:
    blockers = Counter(blocker for row in candidates for blocker in row.get("blockers", []))
    documented_seed_ff_attempted = "FF" in search_terms
    ff_underlier_found = any(
        row.get("search_term") == "FF" and row.get("forecastx_underlier_candidate") for row in candidates
    )
    underlier_count = sum(1 for row in candidates if row.get("forecastx_underlier_candidate"))
    tradable_count = sum(1 for row in candidates if row.get("tradable_contract_candidate"))
    marketdata_rows = sum(1 for row in records if row.get("quote_captured_at"))
    return {
        "schema_kind": IBKR_FORECASTEX_DISCOVERY_CANDIDATES_SCHEMA_KIND,
        "source": "ibkr_forecastex_discovery_candidates_v1",
        "generated_at": captured_at,
        "base_url": _redacted_base_url(base_url),
        "permission": "local_gateway_read_only_diagnostic",
        "output_dir": str(output_dir),
        "snapshot_dir": str(snapshot_dir) if snapshot_dir else None,
        "endpoints_attempted": list(dict.fromkeys(endpoints_attempted)),
        "search_terms": search_terms,
        "seed_conids_count": len(seed_conids),
        "documented_seed_ff_attempted": documented_seed_ff_attempted,
        "ff_underlier_found": ff_underlier_found,
        "raw_files_written": raw_files_written,
        "candidates": candidates,
        "summary": {
            "discovery_status": statuses[0] if statuses else None,
            "discovery_statuses": statuses,
            "documented_seed_ff_attempted": documented_seed_ff_attempted,
            "ff_underlier_found": ff_underlier_found,
            "forecastx_underlier_candidates": underlier_count,
            "forecastx_tradable_contract_candidates": tradable_count,
            "forecastx_marketdata_rows": marketdata_rows,
            "strikes_requests_attempted": int(followup_stats.get("strikes_requests_attempted", 0)),
            "strikes_rows_found": int(followup_stats.get("strikes_rows_found", 0)),
            "contract_info_requests_attempted": int(followup_stats.get("contract_info_requests_attempted", 0)),
            "missing_secdef_parameter_count": int(followup_stats.get("missing_secdef_parameter_count", 0)),
            "search_requests_attempted": int(followup_stats.get("search_requests_attempted", 0)),
            "followup_requests_attempted": int(followup_stats.get("followup_requests_attempted", 0)),
            "followup_errors": int(followup_stats.get("followup_errors", 0)),
            "candidates_so_far": len(candidates),
            "final_tradable_forecastex_contract_not_found": tradable_count == 0,
            "auth_status": _discovery_auth_status(statuses),
            "raw_response_count": raw_response_count,
            "raw_files_written": len(raw_files_written),
            "candidate_count": len(candidates),
            "forecastx_candidate_count": _count_forecastx_candidates(candidates),
            "normalized_possible_count": sum(1 for row in candidates if row.get("normalized_possible")),
            "seed_candidate_count": sum(1 for row in candidates if row.get("source") == "seed_conid"),
            "blockers_by_count": dict(sorted(blockers.items())),
            "blockers": sorted(blockers),
            "warnings": warnings,
        },
        "warnings": warnings,
        "operator_instructions": _operator_instructions(),
        "safety": safety,
    }


def _discovery_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# IBKR / ForecastEx Discovery Candidates",
        "",
        "Local Client Portal Gateway read-only diagnostic. No login, account, portfolio, position, order, wallet, or signing endpoints are used.",
        "",
        "## Summary",
        "",
        f"- discovery_status: `{summary.get('discovery_status')}`",
        f"- discovery_statuses: `{', '.join(summary.get('discovery_statuses') or [])}`",
        f"- documented_seed_ff_attempted: `{str(bool(summary.get('documented_seed_ff_attempted'))).lower()}`",
        f"- ff_underlier_found: `{str(bool(summary.get('ff_underlier_found'))).lower()}`",
        f"- forecastx_underlier_candidates: `{summary.get('forecastx_underlier_candidates', 0)}`",
        f"- forecastx_tradable_contract_candidates: `{summary.get('forecastx_tradable_contract_candidates', 0)}`",
        f"- forecastx_marketdata_rows: `{summary.get('forecastx_marketdata_rows', 0)}`",
        f"- strikes_requests_attempted: `{summary.get('strikes_requests_attempted', 0)}`",
        f"- strikes_rows_found: `{summary.get('strikes_rows_found', 0)}`",
        f"- contract_info_requests_attempted: `{summary.get('contract_info_requests_attempted', 0)}`",
        f"- missing_secdef_parameter_count: `{summary.get('missing_secdef_parameter_count', 0)}`",
        f"- search_requests_attempted: `{summary.get('search_requests_attempted', 0)}`",
        f"- followup_requests_attempted: `{summary.get('followup_requests_attempted', 0)}`",
        f"- followup_errors: `{summary.get('followup_errors', 0)}`",
        f"- candidate_count: `{summary.get('candidate_count', 0)}`",
        f"- forecastx_candidate_count: `{summary.get('forecastx_candidate_count', 0)}`",
        f"- normalized_possible_count: `{summary.get('normalized_possible_count', 0)}`",
        f"- seed_candidate_count: `{summary.get('seed_candidate_count', 0)}`",
        f"- raw_files_written: `{summary.get('raw_files_written', 0)}`",
        "",
        "## Candidate Examples",
        "",
    ]
    candidates = report.get("candidates") if isinstance(report.get("candidates"), list) else []
    if candidates:
        lines.extend(["| Source | Search Term | Conid | Symbol | Local Symbol | Exchange | Normalized Possible | Blockers |", "|---|---|---|---|---|---|---:|---|"])
        for row in candidates[:25]:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _markdown_cell(row.get("source")),
                        _markdown_cell(row.get("search_term")),
                        _markdown_cell(row.get("conid")),
                        _markdown_cell(row.get("symbol")),
                        _markdown_cell(row.get("localSymbol")),
                        _markdown_cell(row.get("exchange")),
                        _markdown_cell(str(bool(row.get("normalized_possible"))).lower()),
                        _markdown_cell(", ".join(row.get("blockers") or [])),
                    ]
                )
                + " |"
            )
    else:
        lines.append("(none)")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- live_trading: `false`",
            "- login_automation: `false`",
            "- credentials_sent: `false`",
            "- account_balance_position_portfolio_endpoints_called: `false`",
            "- order_endpoints_called: `false`",
            "- paper_candidate_emitted: `false`",
        ]
    )
    return "\n".join(lines) + "\n"


def _raw_shape_file_summary(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "raw_file_path": str(path),
            "readable": False,
            "blockers": ["raw_shape_file_unreadable"],
            "warnings": [f"{type(exc).__name__}: {_safe_exception_message(exc)}"],
        }
    wrapper = raw if isinstance(raw, dict) else {}
    payload = wrapper.get("payload") if isinstance(wrapper, dict) and "payload" in wrapper else raw
    dicts = list(_walk_dicts(payload))
    payload_keys = sorted(payload.keys()) if isinstance(payload, dict) else []
    wrapper_keys = sorted(wrapper.keys()) if isinstance(wrapper, dict) else []
    conids = _field_values(dicts, ("conid", "underConid", "underlyingConid", "underlier_conid"))
    sec_types = _field_values(dicts, ("secType", "assetClass"))
    symbols = _field_values(dicts, ("symbol", "localSymbol", "local_symbol", "ticker"))
    exchanges = _field_values(dicts, ("exchange", "listingExchange", "exchangeCode", "validExchanges"))
    currencies = _field_values(dicts, ("currency",))
    forecastx_found = _payload_contains_text(payload, ("FORECASTX", "ForecastEx"))
    ff_found = _payload_contains_text(payload, ("FF", "FFDEC"))
    has_call_put = any(_has_call_put_right(row) for row in dicts)
    has_binary = any(_has_binary_yes_no(row) for row in dicts)
    has_strike_field = any(_has_strike_or_threshold_field(row) for row in dicts)
    has_nonzero_strike = any(_has_nonzero_strike_or_threshold(row) for row in dicts)
    has_expiry = any(_has_expiry_or_month(row) for row in dicts)
    has_event_contract = any(_has_event_contract_fields(row) for row in dicts)
    final_rows = [row for row in dicts if _raw_row_has_final_tradable_contract_fields(row)]
    blockers: list[str] = []
    if forecastx_found and not final_rows:
        blockers.extend(["final_tradable_forecastex_contract_not_found"])
        if not has_call_put:
            blockers.append("missing_call_put_right")
        if not has_expiry:
            blockers.append("missing_expiry_or_month")
        if not has_nonzero_strike:
            blockers.append("missing_strike_or_event_threshold")
        if any(_raw_row_is_underlier_only(row) for row in dicts):
            blockers.append("underlier_only_no_tradable_contract_fields")
    return {
        "raw_file_path": str(path),
        "readable": True,
        "endpoint_name": wrapper.get("endpoint_name") if isinstance(wrapper, dict) else None,
        "path": wrapper.get("path") if isinstance(wrapper, dict) else None,
        "query_params": _redact_payload(wrapper.get("query_params")) if isinstance(wrapper, dict) else None,
        "captured_at": wrapper.get("captured_at") if isinstance(wrapper, dict) else None,
        "payload_top_level_type": type(payload).__name__,
        "payload_top_level_keys": payload_keys[:50],
        "wrapper_top_level_keys": wrapper_keys[:50],
        "important_nested_key_paths": _interesting_key_paths(payload)[:80],
        "conids_found": conids[:50],
        "secTypes_found": sec_types[:25],
        "symbols_found": symbols[:50],
        "exchanges_found": exchanges[:25],
        "currencies_found": currencies[:10],
        "forecastx_identifier_found": forecastx_found,
        "ff_identifier_found": ff_found,
        "has_binary_yes_no": has_binary,
        "has_call_put_right": has_call_put,
        "has_strike_or_threshold": has_strike_field,
        "has_nonzero_strike_or_threshold": has_nonzero_strike,
        "has_expiry_or_month": has_expiry,
        "has_event_contract_fields": has_event_contract,
        "final_tradable_contract_fields_present": bool(final_rows),
        "final_tradable_contract_evidence_count": len(final_rows),
        "blockers": list(dict.fromkeys(blockers)),
    }


def _raw_shape_summary_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# IBKR / ForecastEx Raw Shape Summary",
        "",
        "Saved raw-response diagnostic only. No account, portfolio, position, order, wallet, signing, or login endpoints are used.",
        "",
        "## Summary",
        "",
        f"- raw_files_read: `{summary.get('raw_files_read', 0)}`",
        f"- forecastx_identifier_files: `{summary.get('forecastx_identifier_files', 0)}`",
        f"- final_tradable_contract_field_files: `{summary.get('final_tradable_contract_field_files', 0)}`",
        f"- call_put_right_files: `{summary.get('call_put_right_files', 0)}`",
        f"- expiry_or_month_files: `{summary.get('expiry_or_month_files', 0)}`",
        f"- strike_field_files: `{summary.get('strike_field_files', 0)}`",
        f"- final_tradable_contract_fields_present: `{str(bool(summary.get('final_tradable_contract_fields_present'))).lower()}`",
        "",
        "## Blockers",
        "",
    ]
    blockers = summary.get("blockers_by_count") if isinstance(summary.get("blockers_by_count"), dict) else {}
    if blockers:
        for blocker, count in sorted(blockers.items()):
            lines.append(f"- `{blocker}`: `{count}`")
    else:
        lines.append("(none)")
    lines.extend(
        [
            "",
            "## Raw File Shapes",
            "",
            "| Raw File | Endpoint | Payload Type | ForecastX | C/P | Expiry/Month | Strike/Threshold | Final Tradable Fields | Blockers |",
            "|---|---|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    files = report.get("files") if isinstance(report.get("files"), list) else []
    for row in files[:100]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(row.get("raw_file_path")),
                    _markdown_cell(row.get("path")),
                    _markdown_cell(row.get("payload_top_level_type")),
                    _markdown_cell(str(bool(row.get("forecastx_identifier_found"))).lower()),
                    _markdown_cell(str(bool(row.get("has_call_put_right"))).lower()),
                    _markdown_cell(str(bool(row.get("has_expiry_or_month"))).lower()),
                    _markdown_cell(str(bool(row.get("has_strike_or_threshold"))).lower()),
                    _markdown_cell(str(bool(row.get("final_tradable_contract_fields_present"))).lower()),
                    _markdown_cell(", ".join(row.get("blockers") or [])),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- live_trading: `false`",
            "- credentials_sent: `false`",
            "- account_balance_position_portfolio_endpoints_called: `false`",
            "- order_endpoints_called: `false`",
            "- paper_candidate_emitted: `false`",
        ]
    )
    return "\n".join(lines) + "\n"


def _missing_final_tradable_contract_blockers(row: dict[str, Any]) -> list[str]:
    blockers = ["final_tradable_forecastex_contract_not_found"]
    if not _has_call_put_right(row):
        blockers.append("missing_call_put_right")
    if not _has_expiry_or_month(row):
        blockers.append("missing_expiry_or_month")
    if not _has_nonzero_strike_or_threshold(row):
        blockers.append("missing_strike_or_event_threshold")
    if _is_underlier_candidate(row):
        blockers.append("underlier_only_no_tradable_contract_fields")
    return blockers


def _walk_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for child in value.values():
            found.extend(_walk_dicts(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk_dicts(child))
    return found


def _field_values(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[Any]:
    lowered = {key.lower() for key in keys}
    values: list[Any] = []
    for row in rows:
        for key, value in row.items():
            if key.lower() in lowered and value not in (None, ""):
                if isinstance(value, (str, int, float, bool)):
                    values.append(value)
                elif isinstance(value, list):
                    values.extend(item for item in value if isinstance(item, (str, int, float, bool)))
    return list(dict.fromkeys(values))


def _interesting_key_paths(value: Any, prefix: str = "", depth: int = 0) -> list[str]:
    if depth > 5:
        return []
    tokens = (
        "contract",
        "instrument",
        "section",
        "strike",
        "right",
        "month",
        "maturity",
        "expiry",
        "expiration",
        "conid",
        "sectype",
        "symbol",
        "exchange",
        "currency",
        "forecast",
        "event",
    )
    paths: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if any(token in str(key).lower() for token in tokens):
                paths.append(path)
            paths.extend(_interesting_key_paths(child, path, depth + 1))
    elif isinstance(value, list):
        for index, child in enumerate(value[:10]):
            paths.extend(_interesting_key_paths(child, f"{prefix}[{index}]" if prefix else f"[{index}]", depth + 1))
    return list(dict.fromkeys(paths))


def _payload_contains_text(value: Any, needles: tuple[str, ...]) -> bool:
    text = json.dumps(value, sort_keys=True, default=str).upper()
    return any(needle.upper() in text for needle in needles)


def _has_call_put_right(row: dict[str, Any]) -> bool:
    right = _text(row.get("right") or row.get("putCall") or row.get("side")).upper().strip()
    if right in {"C", "P"}:
        return True
    local_symbol = _text(row.get("localSymbol") or row.get("local_symbol")).upper().strip()
    return local_symbol.endswith((" C", " P"))


def _has_binary_yes_no(row: dict[str, Any]) -> bool:
    values = " ".join(_text(value).upper() for value in row.values() if isinstance(value, (str, int, float, bool)))
    return any(token in {"YES", "NO"} for token in re.findall(r"[A-Z]+", values))


def _has_strike_or_threshold_field(row: dict[str, Any]) -> bool:
    return any(key in row and row.get(key) is not None for key in ("strike", "_strike", "event_threshold", "threshold"))


def _has_nonzero_strike_or_threshold(row: dict[str, Any]) -> bool:
    for key in ("strike", "_strike", "event_threshold", "threshold"):
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            if float(str(value).replace(",", "")) != 0.0:
                return True
        except ValueError:
            return True
    return False


def _has_expiry_or_month(row: dict[str, Any]) -> bool:
    if _candidate_months(row):
        return True
    return any(
        row.get(key) not in (None, "")
        for key in ("maturity", "lastTradeDateOrContractMonth", "expiration", "maturityDate", "lastTradingDay", "lastTradeTime", "_month")
    )


def _has_event_contract_fields(row: dict[str, Any]) -> bool:
    if _text(row.get("secType") or row.get("assetClass")).upper().strip() == "EC":
        return True
    sections = row.get("sections")
    if isinstance(sections, list):
        return any(isinstance(section, dict) and _text(section.get("secType")).upper().strip() == "EC" for section in sections)
    return False


def _raw_row_has_final_tradable_contract_fields(row: dict[str, Any]) -> bool:
    return bool(row.get("conid")) and _has_call_put_right(row) and (_is_forecastx_like(row) or _raw_row_exchange_is_forecastx(row))


def _raw_row_is_underlier_only(row: dict[str, Any]) -> bool:
    return _raw_row_exchange_is_forecastx(row) and not _has_call_put_right(row)


def _raw_row_exchange_is_forecastx(row: dict[str, Any]) -> bool:
    exchange_text = " ".join(
        _text(row.get(key)).upper()
        for key in ("exchange", "listingExchange", "exchangeCode", "validExchanges", "description", "companyHeader")
    )
    return "FORECASTX" in exchange_text or "FORECASTEX" in exchange_text


def _discovery_auth_status(statuses: list[str]) -> str:
    if "REFUSED_NON_LOCALHOST_BASE_URL" in statuses:
        return "refused_non_localhost_base_url"
    if "LOCAL_GATEWAY_UNREACHABLE" in statuses:
        return "unreachable"
    if "LOCAL_GATEWAY_SESSION_DROPPED" in statuses:
        return "session_dropped_or_unreachable"
    if "ACCOUNT_PERMISSION_REVIEW_REQUIRED" in statuses:
        return "not_authenticated_or_permission_required"
    return "authenticated"


def _candidate_months(row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("month", "months", "maturity", "lastTradeDateOrContractMonth", "expiration", "maturityDate"):
        values.extend(_split_month_values(row.get(key)))
    sections = row.get("sections")
    if isinstance(sections, list):
        for section in sections:
            if isinstance(section, dict):
                for key in ("month", "months", "maturity", "lastTradeDateOrContractMonth", "expiration", "maturityDate"):
                    values.extend(_split_month_values(section.get(key)))
    return list(dict.fromkeys(value for value in values if value))


def _split_month_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        values: list[str] = []
        for item in value:
            values.extend(_split_month_values(item))
        return values
    if isinstance(value, dict):
        values = []
        for item in value.values():
            values.extend(_split_month_values(item))
        return values
    text = str(value).strip()
    if not text:
        return []
    return [part.strip() for part in text.replace(",", ";").split(";") if part.strip()]


def _contract_info_combinations_from_strikes_payload(
    payload: Any,
    *,
    underlier: dict[str, Any],
    underlier_conid: str,
    month: str,
) -> tuple[list[dict[str, str]], list[dict[str, Any]], int]:
    strikes_by_right = _strikes_by_right(payload)
    generic_strikes = _generic_strikes(payload)
    rights = _rights_from_payload(payload)
    combinations: list[dict[str, str]] = []
    strikes_found = 0
    for right, strikes in strikes_by_right.items():
        strikes_found += len(strikes)
        for strike in strikes:
            combinations.append(
                {
                    "conid": underlier_conid,
                    "secType": "EC",
                    "month": month,
                    "strike": strike,
                    "right": right,
                    "exchange": "FORECASTX",
                }
            )
    if not combinations and generic_strikes and rights:
        strikes_found += len(generic_strikes)
        for right in rights:
            for strike in generic_strikes:
                combinations.append(
                    {
                        "conid": underlier_conid,
                        "secType": "EC",
                        "month": month,
                        "strike": strike,
                        "right": right,
                        "exchange": "FORECASTX",
                    }
                )
    missing: list[str] = []
    if not month:
        missing.append("month")
    if not (generic_strikes or any(strikes_by_right.values())):
        missing.append("strike")
    if not (rights or any(strikes_by_right.values())):
        missing.append("right")
    incomplete_rows = []
    if missing:
        incomplete_rows.append(
            _missing_secdef_parameter_row(
                underlier,
                underlier_conid=underlier_conid,
                missing=missing,
                stage="STRIKES_FOUND",
                month=month,
            )
        )
    return combinations, incomplete_rows, strikes_found


def _strikes_by_right(payload: Any) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    if not isinstance(payload, dict):
        return result
    for key, right in (("call", "C"), ("calls", "C"), ("C", "C"), ("CALL", "C"), ("put", "P"), ("puts", "P"), ("P", "P"), ("PUT", "P")):
        values = _scalar_values(payload.get(key))
        strikes = [_normalize_strike(value) for value in values]
        strikes = [strike for strike in strikes if strike is not None]
        if strikes:
            result.setdefault(right, [])
            result[right].extend(strike for strike in strikes if strike not in result[right])
    strikes = payload.get("strikes")
    if isinstance(strikes, dict):
        nested = _strikes_by_right(strikes)
        for right, values in nested.items():
            result.setdefault(right, [])
            result[right].extend(value for value in values if value not in result[right])
    return result


def _generic_strikes(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    values: list[Any] = []
    for key in ("strikes", "strike"):
        value = payload.get(key)
        if isinstance(value, dict):
            continue
        values.extend(_scalar_values(value))
    strikes = [_normalize_strike(value) for value in values]
    return list(dict.fromkeys(strike for strike in strikes if strike is not None))


def _rights_from_payload(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    values: list[str] = []
    for key in ("rights", "right", "putCall", "side"):
        for value in _scalar_values(payload.get(key)):
            text = str(value).strip().upper()
            if text in {"C", "CALL", "YES"}:
                values.append("C")
            if text in {"P", "PUT", "NO"}:
                values.append("P")
    return list(dict.fromkeys(values))


def _scalar_values(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, dict):
        values: list[Any] = []
        for item in value.values():
            values.extend(_scalar_values(item))
        return values
    if isinstance(value, (list, tuple, set)):
        values = []
        for item in value:
            values.extend(_scalar_values(item))
        return values
    return [value]


def _normalize_strike(value: Any) -> str | None:
    text = str(value).strip()
    if not text:
        return None
    return text.replace(",", "")


def _missing_secdef_parameter_row(
    underlier: dict[str, Any],
    *,
    underlier_conid: str,
    missing: tuple[str, ...] | list[str],
    stage: str,
    month: str | None = None,
) -> dict[str, Any]:
    return {
        "conid": underlier_conid,
        "symbol": underlier.get("symbol"),
        "companyHeader": underlier.get("companyHeader"),
        "description": underlier.get("description"),
        "exchange": "FORECASTX",
        "secType": underlier.get("secType") or "IND",
        "_forecastx_underlier_candidate": True,
        "_underlier_conid": underlier_conid,
        "_month": month,
        "_discovery_stage": stage,
        "_missing_secdef_parameters": list(dict.fromkeys(str(item) for item in missing if item)),
        "_extra_blockers": [
            "missing_secdef_parameters_for_contract_info",
            "final_tradable_forecastex_contract_not_found",
        ],
    }


def _contract_info_empty_row(
    underlier: dict[str, Any],
    *,
    underlier_conid: str,
    combo: dict[str, str],
) -> dict[str, Any]:
    row = _missing_secdef_parameter_row(
        underlier,
        underlier_conid=underlier_conid,
        missing=(),
        stage="CONTRACT_INFO_FOUND",
        month=combo.get("month"),
    )
    row["_extra_blockers"] = ["contract_info_empty_for_strike_combo", "final_tradable_forecastex_contract_not_found"]
    row["_strike"] = combo.get("strike")
    row["right"] = combo.get("right")
    row["secType"] = combo.get("secType")
    row["_contract_info_empty"] = True
    return row


def _apply_contract_info_context(
    row: dict[str, Any],
    *,
    underlier: dict[str, Any],
    underlier_conid: str,
    combo: dict[str, str],
) -> None:
    row.setdefault("_underlier_conid", underlier_conid)
    row.setdefault("symbol", underlier.get("symbol"))
    row.setdefault("exchange", "FORECASTX")
    row.setdefault("secType", combo.get("secType"))
    row.setdefault("right", combo.get("right"))
    row.setdefault("strike", combo.get("strike"))
    row.setdefault("lastTradeDateOrContractMonth", combo.get("month"))
    row.setdefault("_month", combo.get("month"))
    row.setdefault("_strike", combo.get("strike"))
    row.setdefault("_discovery_stage", "CONTRACT_INFO_FOUND")
    row.setdefault("_extra_blockers", [])
    if not _is_tradable_contract_candidate(row):
        row.setdefault("_forecastx_underlier_candidate", True)
        row["_extra_blockers"].append("final_tradable_forecastex_contract_not_found")


def _candidate_rows(
    rows: list[dict[str, Any]],
    *,
    search_term: str,
    endpoint: str,
    raw_candidate_count: int,
    source: str,
    seed_conid: str | None = None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        conid = row.get("conid") or seed_conid
        underlier = _is_underlier_candidate(row)
        tradable = _is_tradable_contract_candidate(row)
        blockers: list[str] = []
        if not conid:
            blockers.append("missing_conid")
        if source != "seed_conid" and not _is_forecastx_like(row):
            blockers.append("not_forecastx_exchange")
        if underlier and not tradable:
            blockers.extend(_missing_final_tradable_contract_blockers(row))
        blockers.extend(str(blocker) for blocker in row.get("_extra_blockers", []) if blocker)
        candidates.append(
            {
                "source": source,
                "search_term": search_term,
                "endpoint": endpoint,
                "raw_candidate_count": raw_candidate_count,
                "conid": conid,
                "symbol": row.get("symbol"),
                "localSymbol": row.get("localSymbol") or row.get("local_symbol"),
                "description": row.get("description") or row.get("companyName") or row.get("contract_title"),
                "companyHeader": row.get("companyHeader"),
                "secType": row.get("secType") or row.get("assetClass"),
                "right": row.get("right") or row.get("putCall") or row.get("side"),
                "strike": row.get("strike") or row.get("_strike"),
                "maturity": row.get("maturity") or row.get("lastTradeDateOrContractMonth") or row.get("_month"),
                "lastTradeDateOrContractMonth": row.get("lastTradeDateOrContractMonth") or row.get("_month"),
                "exchange": row.get("exchange") or row.get("listingExchange") or row.get("exchangeCode"),
                "sections": row.get("sections"),
                "missing_secdef_parameters": row.get("_missing_secdef_parameters") or row.get("missing_secdef_parameters") or [],
                "forecastx_underlier_candidate": underlier,
                "underlier_conid": row.get("_underlier_conid") or conid if underlier else row.get("_underlier_conid"),
                "tradable_contract_candidate": tradable,
                "discovery_stage": row.get("_discovery_stage") or ("CONTRACT_INFO_FOUND" if tradable else "UNDERLIER_FOUND" if underlier else "DISCOVERY_ONLY"),
                "normalized_possible": bool(conid) and tradable,
                "blockers": list(dict.fromkeys(blockers)),
            }
        )
    return candidates


def _discovery_statuses(
    *,
    authenticated: bool,
    candidates: list[dict[str, Any]],
    contracts: list[dict[str, Any]],
    seed_conids: list[str],
    marketdata_permission_missing: bool,
    marketdata_row_count: int = 0,
    gateway_failure_status: str | None = None,
) -> list[str]:
    statuses: list[str] = []
    underlier_found = any(row.get("forecastx_underlier_candidate") for row in candidates)
    tradable_found = any(row.get("tradable_contract_candidate") for row in candidates)
    if gateway_failure_status:
        statuses.append(gateway_failure_status)
    if marketdata_permission_missing:
        statuses.append("MARKET_DATA_PERMISSION_MISSING")
    if tradable_found and marketdata_row_count == 0:
        statuses.append("FORECASTX_CONTRACT_INFO_FOUND_NEEDS_MARKETDATA_PERMISSION")
    elif underlier_found and not tradable_found:
        statuses.append("FORECASTX_UNDERLIER_FOUND_NEEDS_STRIKES_OR_CONTRACT_INFO")
    elif contracts:
        statuses.append("FORECASTX_CANDIDATES_FOUND")
    elif authenticated:
        statuses.append("LOCAL_SESSION_OK_BUT_NO_FORECASTX_FOUND")
        if not seed_conids:
            statuses.append("SEED_CONIDS_REQUIRED")
    else:
        statuses.append("ACCOUNT_PERMISSION_REVIEW_REQUIRED")
    if candidates and not contracts:
        statuses.append("SEED_CONIDS_REQUIRED")
    return list(dict.fromkeys(statuses))


def _count_forecastx_candidates(candidates: list[dict[str, Any]]) -> int:
    return sum(
        1
        for row in candidates
        if row.get("forecastx_underlier_candidate") or row.get("tradable_contract_candidate") or row.get("normalized_possible")
    )


def _forecastx_contract_rows(payload: Any) -> list[dict[str, Any]]:
    rows = _rows_from_payload(payload)
    forecastx_rows = []
    for row in rows:
        if _is_forecastx_like(row):
            row.setdefault("_discovery_stage", "CONTRACT_INFO_FOUND" if _is_tradable_contract_candidate(row) else "UNDERLIER_FOUND" if _is_underlier_candidate(row) else "DISCOVERY_ONLY")
            if _is_underlier_candidate(row):
                row.setdefault("_forecastx_underlier_candidate", True)
                row.setdefault("_underlier_conid", row.get("conid"))
            forecastx_rows.append(row)
    return forecastx_rows


def _is_tradable_contract_candidate(row: dict[str, Any]) -> bool:
    if row.get("_contract_info_empty"):
        return False
    right = _text(row.get("right") or row.get("putCall") or row.get("side")).upper()
    if right in {"C", "P"} and str(row.get("conid") or "").strip():
        return True
    local_symbol = _text(row.get("localSymbol") or row.get("local_symbol")).upper().strip()
    return bool(str(row.get("conid") or "").strip() and local_symbol.endswith((" C", " P")))


def _is_underlier_candidate(row: dict[str, Any]) -> bool:
    if bool(row.get("_forecastx_underlier_candidate")):
        return True
    if _is_tradable_contract_candidate(row):
        return False
    sections = row.get("sections")
    if isinstance(sections, list):
        for section in sections:
            if not isinstance(section, dict):
                continue
            sec_type = _text(section.get("secType")).upper().strip()
            exchange = _text(section.get("exchange")).upper()
            if sec_type in {"IND", "EC"} and "FORECASTX" in exchange:
                return True
            if sec_type == "EC":
                return True
    sec_type = _text(row.get("secType") or row.get("assetClass")).upper().strip()
    return sec_type in {"IND", "EC"} and _is_forecastx_like(row)


def _is_forecastx_like(row: dict[str, Any]) -> bool:
    haystack = _forecastx_haystack(row)
    return any(
        needle in haystack
        for needle in (
            "FORECASTX",
            "FORECASTEX",
            "FORECAST CONTRACT",
            "EVENT CONTRACT",
            "PREDICTION MARKET",
            "SECTYPE EC",
        )
    )


def _forecastx_haystack(row: dict[str, Any]) -> str:
    parts = [
        _text(row.get(key))
        for key in (
            "exchange",
            "listingExchange",
            "exchangeCode",
            "symbol",
            "localSymbol",
            "description",
            "companyName",
            "companyHeader",
            "secType",
            "assetClass",
        )
    ]
    sections = row.get("sections")
    if isinstance(sections, list):
        for section in sections:
            if isinstance(section, dict):
                parts.extend(f"{key} {value}" for key, value in section.items())
    return " ".join(parts).upper()


def _marketdata_by_conid(payload: Any) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("conid")): row
        for row in _rows_from_payload(payload)
        if str(row.get("conid") or "").strip()
    }


def _rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("data", "results", "contracts", "instruments", "rows", "markets"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        if any(key in payload for key in ("conid", "symbol", "exchange", "listingExchange")):
            return [payload]
    return []


def _dedupe_contracts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows:
        key = str(row.get("conid") or row.get("localSymbol") or row.get("symbol") or json.dumps(row, sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _parse_search_terms(value: str | None) -> list[str]:
    if value is None:
        return list(DEFAULT_IBKR_FORECASTEX_SEARCH_TERMS)
    terms = [term.strip() for term in value.split(",") if term.strip()]
    return terms or list(DEFAULT_IBKR_FORECASTEX_SEARCH_TERMS)


def _prepare_search_terms(value: str | None, *, forecastx_doc_seed: bool, seed_conids: list[str]) -> list[str]:
    if value is None and seed_conids and not forecastx_doc_seed:
        return []
    terms = _parse_search_terms(value)
    if forecastx_doc_seed:
        doc_seeds = DEFAULT_IBKR_FORECASTEX_DOC_SEED_SYMBOLS if value is None else ("FF",)
        terms = [*doc_seeds, *terms]
    return list(dict.fromkeys(terms))


def _search_endpoint_plan(terms: list[str]) -> list[tuple[str, str, dict[str, str]]]:
    plan: list[tuple[str, str, dict[str, str]]] = []
    seen: set[tuple[str, str, tuple[tuple[str, str], ...]]] = set()
    for term in terms:
        variants = [
            {"symbol": term},
            {"symbol": term, "name": "true"},
            {"symbol": term, "name": "true", "exchange": "FORECASTX"},
            {"symbol": term, "secType": "IND", "name": "true"},
            {"symbol": term, "secType": "EC", "name": "true"},
            {"symbol": term, "secType": "EC", "name": "true", "exchange": "FORECASTX"},
            {"symbol": term, "secType": "FOP", "name": "true"},
            {"symbol": term, "secType": "FOP", "name": "true", "exchange": "FORECASTX"},
            {"symbol": term, "secType": "OPT", "name": "true"},
            {"symbol": term, "secType": "OPT", "name": "true", "exchange": "FORECASTX"},
        ]
        for params in variants:
            key = (term, "/iserver/secdef/search", tuple(sorted(params.items())))
            if key in seen:
                continue
            seen.add(key)
            plan.append((term, "/iserver/secdef/search", params))
    return plan


def _read_seed_conids(seed_conids_path: Path | None) -> list[str]:
    if seed_conids_path is None:
        return []
    if not seed_conids_path.exists():
        raise FileNotFoundError(str(seed_conids_path))
    conids: list[str] = []
    for raw_line in seed_conids_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        for value in line.replace(",", " ").split():
            if value.strip():
                conids.append(value.strip())
    return list(dict.fromkeys(conids))


def _summary_blockers(records: list[dict[str, Any]], fallback: list[str]) -> list[str]:
    blockers = list(fallback)
    for record in records:
        blockers.extend(str(blocker) for blocker in record.get("blockers", []))
    return list(dict.fromkeys(blockers))


def _record_readonly_request_error(
    exc: IBKRReadOnlyRequestError,
    *,
    endpoint_name: str,
    endpoint: str,
    search_term: str,
    source: str,
    blockers: list[str],
    warnings: list[str],
    discovery_candidates: list[dict[str, Any]],
    followup_stats: Counter[str],
    conid: str | None = None,
) -> None:
    warnings.append(f"{endpoint_name}: {type(exc).__name__}: {_safe_exception_message(exc)}")
    blockers.extend(exc.blockers)
    followup_stats["followup_errors"] += 1
    followup_stats["readonly_request_errors"] += 1
    if "ibkr_gateway_session_dropped_or_unreachable" in exc.blockers:
        followup_stats["session_drop_errors"] += 1
    if "ibkr_gateway_connection_refused" in exc.blockers:
        followup_stats["connection_refused_errors"] += 1
    if "ibkr_gateway_request_timeout" in exc.blockers:
        followup_stats["timeout_errors"] += 1
    discovery_candidates.append(
        {
            "source": source,
            "search_term": search_term,
            "endpoint": endpoint,
            "raw_candidate_count": 0,
            "conid": conid,
            "symbol": None,
            "localSymbol": None,
            "description": None,
            "secType": None,
            "right": None,
            "exchange": None,
            "normalized_possible": False,
            "blockers": list(dict.fromkeys(exc.blockers)),
        }
    )


def _followup_error_limit_reached(followup_stats: Counter[str], max_followup_errors: int) -> bool:
    return int(followup_stats.get("followup_errors", 0)) >= max(0, int(max_followup_errors))


def _gateway_failure_status(followup_stats: Counter[str]) -> str | None:
    if int(followup_stats.get("session_drop_errors", 0)) > 0:
        return "LOCAL_GATEWAY_SESSION_DROPPED"
    if int(followup_stats.get("readonly_request_errors", 0)) > 0:
        return "PARTIAL_GATEWAY_FAILURE"
    return None


def _safe_get_json(
    *,
    getter: HttpGet,
    base_url: str,
    path: str,
    params: dict[str, str],
    timeout_seconds: float,
) -> Any:
    _validate_readonly_path(path)
    url = _build_url(base_url, path, params=params)
    try:
        return getter(url, _safe_headers(), float(timeout_seconds))
    except (KeyboardInterrupt, SystemExit):
        raise
    except IBKRReadOnlyRequestError:
        raise
    except IBKRReadOnlyHTTPError as exc:
        if exc.status_code in {401, 403}:
            raise IBKRReadOnlyRequestError(
                f"Gateway session dropped or endpoint became unreachable: HTTP {exc.status_code}: {exc.detail[:200]}",
                ["ibkr_gateway_session_dropped_or_unreachable", "ibkr_readonly_endpoint_failed"],
            ) from exc
        raise
    except _NETWORK_REQUEST_EXCEPTIONS as exc:
        raise _request_error_from_exception(exc) from exc


def _default_http_get(url: str, headers: dict[str, str], timeout_seconds: float) -> Any:
    request = Request(url, headers=headers, method="GET")
    parsed = urlparse(url)
    context = ssl._create_unverified_context() if parsed.hostname in {"localhost", "127.0.0.1", "::1"} else None
    try:
        with urlopen(request, timeout=timeout_seconds, context=context) as response:  # noqa: S310 - local explicit read-only command.
            payload = response.read()
    except (KeyboardInterrupt, SystemExit):
        raise
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise IBKRReadOnlyHTTPError(exc.code, detail) from exc
    except _NETWORK_REQUEST_EXCEPTIONS as exc:
        raise _request_error_from_exception(exc) from exc
    text = payload.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw_text": text[:2000]}


def _request_error_from_exception(exc: BaseException) -> IBKRReadOnlyRequestError:
    blockers = ["ibkr_readonly_endpoint_failed"]
    reason = getattr(exc, "reason", None)
    message = _safe_exception_message(exc)
    reason_message = _safe_exception_message(reason) if isinstance(reason, BaseException) else str(reason or "")
    combined = f"{message} {reason_message}".lower()
    if _is_connection_refused(exc) or _is_connection_refused(reason):
        blockers = ["ibkr_gateway_connection_refused", "ibkr_gateway_session_dropped_or_unreachable", *blockers]
    elif _is_timeout_error(exc) or _is_timeout_error(reason) or "timed out" in combined or "timeout" in combined:
        blockers = ["ibkr_gateway_request_timeout", *blockers]
    elif _is_unreachable_error(exc) or _is_unreachable_error(reason):
        blockers = ["ibkr_gateway_session_dropped_or_unreachable", *blockers]
    else:
        blockers = ["ibkr_gateway_session_dropped_or_unreachable", *blockers]
    return IBKRReadOnlyRequestError(
        f"{type(exc).__name__}: {_safe_exception_message(exc)}",
        list(dict.fromkeys(blockers)),
    )


def _is_connection_refused(exc: object) -> bool:
    if isinstance(exc, ConnectionRefusedError):
        return True
    if not isinstance(exc, BaseException):
        return False
    message = _safe_exception_message(exc).lower()
    return (
        _exception_errno(exc) in _REFUSED_ERRNOS
        or _exception_winerror(exc) in _REFUSED_WINERRORS
        or "connection refused" in message
        or "actively refused" in message
        or "target machine actively refused" in message
    )


def _is_timeout_error(exc: object) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    if not isinstance(exc, BaseException):
        return False
    message = _safe_exception_message(exc).lower()
    return (
        _exception_errno(exc) in _TIMEOUT_ERRNOS
        or _exception_winerror(exc) in _TIMEOUT_WINERRORS
        or "timed out" in message
        or "timeout" in message
    )


def _is_unreachable_error(exc: object) -> bool:
    if isinstance(exc, ConnectionResetError):
        return True
    if not isinstance(exc, BaseException):
        return False
    message = _safe_exception_message(exc).lower()
    return (
        _exception_errno(exc) in _UNREACHABLE_ERRNOS
        or _exception_winerror(exc) in _UNREACHABLE_WINERRORS
        or "connection reset" in message
        or "network is unreachable" in message
        or "host is unreachable" in message
    )


def _exception_errno(exc: BaseException) -> int | None:
    value = getattr(exc, "errno", None)
    if isinstance(value, int):
        return value
    if exc.args and isinstance(exc.args[0], int):
        return exc.args[0]
    return None


def _exception_winerror(exc: BaseException) -> int | None:
    value = getattr(exc, "winerror", None)
    if isinstance(value, int):
        return value
    return None


def _write_raw_payload(
    *,
    snapshot_dir: Path,
    endpoint_name: str,
    path: str,
    params: dict[str, str],
    payload: Any,
    captured_at: str,
) -> Path:
    raw = {
        "endpoint_name": endpoint_name,
        "path": path,
        "query_params": params,
        "captured_at": captured_at,
        "payload": _redact_payload(payload),
    }
    raw_path = snapshot_dir / f"ibkr_forecastex_{endpoint_name}.json"
    raw_path.write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")
    return raw_path


def _redact_payload(payload: Any) -> Any:
    if isinstance(payload, list):
        return [_redact_payload(value) for value in payload]
    if isinstance(payload, dict):
        redacted: dict[str, Any] = {}
        for key, value in payload.items():
            lowered = str(key).lower()
            if any(
                token in lowered
                for token in (
                    "credential",
                    "secret",
                    "token",
                    "authorization",
                    "cookie",
                    "account",
                    "position",
                    "balance",
                    "user",
                )
            ):
                redacted[key] = "<redacted>"
            else:
                redacted[key] = _redact_payload(value)
        return redacted
    return payload


def _validate_local_base_url(base_url: str, *, allow_non_localhost: bool) -> dict[str, Any]:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"}:
        return {"allowed": False, "reason": "IBKR Client Portal base URL must use http or https."}
    if allow_non_localhost:
        return {"allowed": True, "reason": None}
    if host in {"localhost", "127.0.0.1", "::1"}:
        return {"allowed": True, "reason": None}
    return {"allowed": False, "reason": f"Refused non-local IBKR base URL host: {host or '<missing>'}"}


def _validate_readonly_path(path: str) -> None:
    lowered = path.lower()
    if not any(lowered.startswith(prefix) for prefix in _ALLOWED_PATH_PREFIXES):
        raise ValueError(f"IBKR endpoint is not in the read-only allowlist: {path}")
    if any(segment in lowered for segment in _FORBIDDEN_PATH_SEGMENTS):
        raise ValueError(f"IBKR endpoint is forbidden by account/order/portfolio guard: {path}")


def _build_url(base_url: str, path: str, *, params: dict[str, str] | None = None) -> str:
    root = base_url.rstrip("/")
    suffix = path if path.startswith("/") else f"/{path}"
    query = ""
    if params:
        query = "?" + urlencode(params)
    return f"{root}{suffix}{query}"


def _safe_headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "User-Agent": "relative-value-scanner-ibkr-forecastex-readonly/1.0",
    }


def _auth_status_from_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"authenticated": False, "raw_status_shape": type(payload).__name__}
    authenticated = bool(
        payload.get("authenticated")
        or payload.get("authenticatedStatus")
        or payload.get("connected")
        or payload.get("isAuthenticated")
    )
    return {
        "authenticated": authenticated,
        "connected": bool(payload.get("connected")) if "connected" in payload else None,
        "competing": bool(payload.get("competing")) if "competing" in payload else None,
        "message": payload.get("message") or payload.get("status"),
    }


def _operator_instructions() -> list[str]:
    return [
        "Start IBKR Client Portal Gateway locally and complete login manually outside this tool.",
        "Re-run the doctor after the local gateway reports an authenticated session.",
        "Fetcher uses only localhost read-only discovery/market-data endpoints and will not call account, order, portfolio, or login endpoints.",
    ]


def _safety_block() -> dict[str, bool]:
    return {
        "live_trading": False,
        "login_automation": False,
        "credentials_sent": False,
        "order_endpoints_called": False,
        "account_balance_position_portfolio_endpoints_called": False,
        "orders_or_cancellations": False,
        "candidate_pair_creation": False,
        "paper_candidate_emitted": False,
        "affects_evaluator_gates": False,
    }


def _price(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        try:
            return float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            continue
    return None


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _safe_exception_message(exc: BaseException) -> str:
    return str(exc).replace("\n", " ")[:500]


def _markdown_cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "/").replace("\n", " ")


def _redacted_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    netloc = parsed.hostname or ""
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return parsed._replace(netloc=netloc).geturl()


def _utc_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _timestamp_slug(timestamp: datetime) -> str:
    return timestamp.strftime("%Y%m%dT%H%M%SZ")


def _safe_filename_token(value: str) -> str:
    token = "".join(char.lower() if char.isalnum() else "_" for char in value)
    token = "_".join(part for part in token.split("_") if part)
    return token[:48] or "term"
