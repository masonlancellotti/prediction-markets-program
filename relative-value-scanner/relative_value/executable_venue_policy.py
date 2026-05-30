"""Executable-venue policy for automated live micro-tests.

Separates two distinct ideas:

  * ``scan_venue`` — a venue allowed in discovery / research / coverage reports.
  * ``executable_venue`` — a venue whose orders can be placed by an automated,
    protected-limit adapter that has a confirmed-safe official order API.

For this project right now:
  * Kalshi / Polymarket: executable IF a real live adapter (client) is configured
    and its preflight passes — otherwise the live path fails CLOSED.
  * CDNA / Crypto.com Predict: SCAN-ONLY. There is NO confirmed-safe official order
    API, so CDNA never enters the automated live order pipeline (no browser clicks,
    no cookie/session replay, no private-endpoint guessing). CDNA stays in scanning,
    research and coverage, and never blocks Kalshi/Polymarket live execution.

This module is pure policy — it places no orders, opens no network, reads no
secrets, and drives no browser.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

# Per-venue execution status.
EXECUTION_STATUS_EXECUTABLE = "EXECUTABLE"
EXECUTION_STATUS_STUB_FAIL_CLOSED = "STUB_FAIL_CLOSED"
EXECUTION_STATUS_NO_SAFE_ORDER_API = "NO_SAFE_ORDER_API"
EXECUTION_STATUS_SCAN_ONLY = "SCAN_ONLY"

DEFAULT_EXECUTABLE_VENUES = ("kalshi", "polymarket")
DEFAULT_SCAN_VENUES = ("kalshi", "polymarket", "cdna")
# Venues that have no confirmed-safe automated order API (never executable).
NON_EXECUTABLE_VENUES = ("cdna",)

CDNA_NO_SAFE_ORDER_ADAPTER_REASON = "cdna_no_safe_automated_order_adapter"
NON_EXECUTABLE_VENUE_REASON = "non_executable_venue_leg"
LIVE_ADAPTER_NOT_IMPLEMENTED_REASON = "live_adapter_not_implemented"


def normalize_venues(value: Any, *, default: Iterable[str] = DEFAULT_EXECUTABLE_VENUES) -> tuple[str, ...]:
    """Parse ``"kalshi,polymarket"`` / a list into a normalized lowercase tuple."""
    if value is None:
        return tuple(v.lower() for v in default)
    if isinstance(value, str):
        items = [p.strip().lower() for p in value.split(",")]
    else:
        items = [str(p).strip().lower() for p in value]
    out: list[str] = []
    for it in items:
        if it and it not in out:
            out.append(it)
    return tuple(out)


def venue_is_non_executable(platform: str) -> bool:
    return str(platform or "").lower() in NON_EXECUTABLE_VENUES


def venue_is_executable(platform: str, *, executable_venues: Iterable[str] = DEFAULT_EXECUTABLE_VENUES) -> bool:
    p = str(platform or "").lower()
    if venue_is_non_executable(p):
        return False
    return p in {str(v).lower() for v in executable_venues}


def venue_execution_status(platform: str, *, executable_venues: Iterable[str] = DEFAULT_EXECUTABLE_VENUES,
                           adapter_ready: bool | None = None) -> str:
    """Status for a single venue. CDNA is always NO_SAFE_ORDER_API."""
    p = str(platform or "").lower()
    if venue_is_non_executable(p):
        return EXECUTION_STATUS_NO_SAFE_ORDER_API
    if p not in {str(v).lower() for v in executable_venues}:
        return EXECUTION_STATUS_SCAN_ONLY
    if adapter_ready is False:
        return EXECUTION_STATUS_STUB_FAIL_CLOSED
    return EXECUTION_STATUS_EXECUTABLE


def _candidate_platforms(candidate: Mapping[str, Any]) -> list[str]:
    legs = candidate.get("legs") or candidate.get("basket_legs") or []
    return [str(l.get("platform") or "").lower() for l in legs if isinstance(l, dict)]


def candidate_execution_status(candidate: Mapping[str, Any], *,
                               executable_venues: Iterable[str] = DEFAULT_EXECUTABLE_VENUES) -> dict[str, Any]:
    """Classify a candidate as executable / scan-only and give do-not-trade reasons.

    A candidate is EXECUTABLE only if it has legs and *every* leg is on an executable
    venue. Any CDNA leg makes it NO_SAFE_ORDER_API; any other off-list venue makes it
    SCAN_ONLY. CDNA never enters the live order pipeline."""
    platforms = _candidate_platforms(candidate)
    exec_set = {str(v).lower() for v in executable_venues}
    cdna_legs = [p for p in platforms if venue_is_non_executable(p)]
    non_exec = [p for p in platforms if p not in exec_set or venue_is_non_executable(p)]
    reasons: list[str] = []
    if not platforms:
        status, executable = EXECUTION_STATUS_SCAN_ONLY, False
        reasons.append("no_legs")
    elif cdna_legs:
        status, executable = EXECUTION_STATUS_NO_SAFE_ORDER_API, False
        reasons.append(CDNA_NO_SAFE_ORDER_ADAPTER_REASON)
    elif non_exec:
        status, executable = EXECUTION_STATUS_SCAN_ONLY, False
        reasons.append(NON_EXECUTABLE_VENUE_REASON)
    else:
        status, executable = EXECUTION_STATUS_EXECUTABLE, True
    return {
        "execution_status": status,
        "executable": executable,
        "do_not_trade_reasons": reasons,
        "platforms": platforms,
        "non_executable_platforms": sorted(set(non_exec)),
        "has_cdna_leg": bool(cdna_legs),
    }


def is_executable_candidate(candidate: Mapping[str, Any], *,
                            executable_venues: Iterable[str] = DEFAULT_EXECUTABLE_VENUES) -> bool:
    return candidate_execution_status(candidate, executable_venues=executable_venues)["executable"]


# --------------------------------------------------------------------------- #
# adapter readiness                                                           #
# --------------------------------------------------------------------------- #
def adapter_execution_status(adapter: Any) -> str:
    """EXECUTABLE only when a live client is configured AND preflight passes.

    A stub adapter (no credentialed client) is STUB_FAIL_CLOSED — the live path must
    refuse to place. The CDNA adapter is always NO_SAFE_ORDER_API."""
    if adapter is None:
        return EXECUTION_STATUS_STUB_FAIL_CLOSED
    if str(getattr(adapter, "platform", "")).lower() in NON_EXECUTABLE_VENUES:
        return EXECUTION_STATUS_NO_SAFE_ORDER_API
    try:
        pf = adapter.preflight()
    except Exception:  # noqa: BLE001 (a broken adapter is treated as not ready)
        return EXECUTION_STATUS_STUB_FAIL_CLOSED
    if bool(pf.get("ok")) and bool(pf.get("live_client_configured")):
        return EXECUTION_STATUS_EXECUTABLE
    return EXECUTION_STATUS_STUB_FAIL_CLOSED


def build_adapter_status_report(adapters: Mapping[str, Any] | None, *,
                                executable_venues: Iterable[str] = DEFAULT_EXECUTABLE_VENUES) -> dict[str, Any]:
    """Per-venue adapter status + ``all_live_adapters_ready`` (only the executable,
    non-CDNA venues need to be EXECUTABLE)."""
    adapters = adapters or {}
    statuses = {
        "kalshi_adapter_status": adapter_execution_status(adapters.get("kalshi")),
        "polymarket_adapter_status": adapter_execution_status(adapters.get("polymarket")),
        "cdna_adapter_status": EXECUTION_STATUS_NO_SAFE_ORDER_API,
    }
    required = [str(v).lower() for v in executable_venues if str(v).lower() not in NON_EXECUTABLE_VENUES]
    all_ready = bool(required) and all(
        statuses.get(f"{v}_adapter_status") == EXECUTION_STATUS_EXECUTABLE for v in required)
    statuses["all_live_adapters_ready"] = all_ready
    statuses["executable_venues"] = list(required)
    return statuses
