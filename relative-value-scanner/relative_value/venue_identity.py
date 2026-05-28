from __future__ import annotations

import re
from typing import Any, Mapping


IBKR_KALSHI_FAKE_EDGE_BLOCKERS = (
    "ibkr_kalshi_is_same_exchange_as_direct_kalshi",
    "broker_route_not_independent_venue",
    "do_not_cross_compare_as_independent_arb",
)

_KNOWN_DIRECT_EXCHANGES = {
    "CME",
    "FORECASTX",
    "KALSHI",
    "POLYMARKET",
}

_ALIASES = {
    "FORECASTEX": "FORECASTX",
    "IBKR_FORECASTEX": "IBKR_FORECASTX",
}

_IBKR_ROUTED_VENUES = {
    "IBKR_CME": "CME",
    "IBKR_FORECASTEX": "FORECASTX",
    "IBKR_FORECASTX": "FORECASTX",
    "IBKR_KALSHI": "KALSHI",
}


def canonical_venue_token(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    token = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").upper()
    return _ALIASES.get(token, token)


def executable_venue_identity_from_fields(
    *,
    venue: Any = None,
    exchange_venue: Any = None,
    executable_venue: Any = None,
    exchange: Any = None,
    access_platform: Any = None,
    source_platform: Any = None,
) -> str | None:
    explicit = canonical_venue_token(executable_venue)
    if explicit:
        return _ALIASES.get(explicit, explicit)

    exchange_token = canonical_venue_token(exchange_venue) or canonical_venue_token(exchange)
    if exchange_token in _KNOWN_DIRECT_EXCHANGES:
        return exchange_token

    venue_token = canonical_venue_token(venue)
    if venue_token in _IBKR_ROUTED_VENUES:
        return _IBKR_ROUTED_VENUES[venue_token]
    if venue_token in _KNOWN_DIRECT_EXCHANGES:
        return venue_token

    platform_tokens = {
        canonical_venue_token(access_platform),
        canonical_venue_token(source_platform),
    }
    if "IBKR" in platform_tokens and venue_token and venue_token.startswith("IBKR_"):
        routed = _IBKR_ROUTED_VENUES.get(venue_token)
        if routed:
            return routed
    return venue_token


def executable_venue_identity_from_mapping(row: Mapping[str, Any] | None) -> str | None:
    if not row:
        return None
    raw = row.get("raw") if isinstance(row.get("raw"), Mapping) else {}
    return executable_venue_identity_from_fields(
        venue=row.get("venue") or raw.get("venue"),
        exchange_venue=row.get("exchange_venue") or raw.get("exchange_venue"),
        executable_venue=row.get("executable_venue") or raw.get("executable_venue"),
        exchange=row.get("exchange") or raw.get("exchange"),
        access_platform=row.get("access_platform") or raw.get("access_platform"),
        source_platform=row.get("source_platform") or raw.get("source_platform"),
    )


def executable_venue_identity_from_market(market: Any) -> str | None:
    raw = getattr(market, "raw", None)
    raw = raw if isinstance(raw, Mapping) else {}
    return executable_venue_identity_from_fields(
        venue=getattr(market, "venue", None) or raw.get("venue"),
        exchange_venue=getattr(market, "exchange_venue", None) or raw.get("exchange_venue"),
        executable_venue=getattr(market, "executable_venue", None) or raw.get("executable_venue"),
        exchange=raw.get("exchange"),
        access_platform=getattr(market, "access_platform", None) or raw.get("access_platform"),
        source_platform=getattr(market, "source_platform", None) or raw.get("source_platform"),
    )


def is_ibkr_kalshi_route(row: Mapping[str, Any] | None) -> bool:
    if not row:
        return False
    identity = executable_venue_identity_from_mapping(row)
    if identity != "KALSHI":
        return False
    raw = row.get("raw") if isinstance(row.get("raw"), Mapping) else {}
    platform_tokens = {
        canonical_venue_token(row.get("access_platform")),
        canonical_venue_token(row.get("source_platform")),
        canonical_venue_token(raw.get("access_platform")),
        canonical_venue_token(raw.get("source_platform")),
    }
    venue_token = canonical_venue_token(row.get("venue") or raw.get("venue"))
    return "IBKR" in platform_tokens or venue_token == "IBKR_KALSHI"


def ibkr_prediction_market_row_blockers(row: Mapping[str, Any] | None) -> list[str]:
    return list(IBKR_KALSHI_FAKE_EDGE_BLOCKERS) if is_ibkr_kalshi_route(row) else []


def broker_route_fake_edge_blockers(left: Any, right: Any) -> list[str]:
    left_mapping = _as_mapping(left)
    right_mapping = _as_mapping(right)
    left_identity = (
        executable_venue_identity_from_mapping(left_mapping)
        if left_mapping is not None
        else executable_venue_identity_from_market(left)
    )
    right_identity = (
        executable_venue_identity_from_mapping(right_mapping)
        if right_mapping is not None
        else executable_venue_identity_from_market(right)
    )
    if not left_identity or left_identity != right_identity:
        return []
    if left_identity != "KALSHI":
        return []
    if _is_ibkr_market(left) or _is_ibkr_market(right):
        return list(IBKR_KALSHI_FAKE_EDGE_BLOCKERS)
    return []


def same_executable_venue(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_identity = executable_venue_identity_from_mapping(left)
    right_identity = executable_venue_identity_from_mapping(right)
    return bool(left_identity and right_identity and left_identity == right_identity)


def _is_ibkr_market(value: Any) -> bool:
    mapping = _as_mapping(value)
    if mapping is None:
        raw = getattr(value, "raw", None)
        mapping = raw if isinstance(raw, Mapping) else {}
        platform_tokens = {
            canonical_venue_token(getattr(value, "access_platform", None)),
            canonical_venue_token(getattr(value, "source_platform", None)),
            canonical_venue_token(mapping.get("access_platform")),
            canonical_venue_token(mapping.get("source_platform")),
        }
        venue_token = canonical_venue_token(getattr(value, "venue", None) or mapping.get("venue"))
    else:
        raw = mapping.get("raw") if isinstance(mapping.get("raw"), Mapping) else {}
        platform_tokens = {
            canonical_venue_token(mapping.get("access_platform")),
            canonical_venue_token(mapping.get("source_platform")),
            canonical_venue_token(raw.get("access_platform")),
            canonical_venue_token(raw.get("source_platform")),
        }
        venue_token = canonical_venue_token(mapping.get("venue") or raw.get("venue"))
    return "IBKR" in platform_tokens or bool(venue_token and venue_token.startswith("IBKR_"))


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None
