from __future__ import annotations

import json
from json import JSONDecodeError
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SX_BET_RESEARCH_SCHEMA_KIND = "sx_bet_research_snapshot_v1"
SX_BET_PERCENTAGE_ODDS_SCALE = 10**20
SX_BET_USDC_SCALE = 10**6
SX_BET_DEFAULT_BASE_URL = "https://api.sx.bet"
SX_BET_DEFAULT_USER_AGENT = "relative-value-scanner/0.x (read-only research; no execution, no auth)"
SX_BET_REDACTED_VALUE = "[REDACTED]"
SX_BET_RAW_REDACTION_FIELDS = {
    "authorization",
    "authToken",
    "token",
    "signature",
    "privateKey",
    "wallet",
    "maker",
    "taker",
    "session",
    "executor",
    "salt",
    "nonce",
    "affiliateAddress",
    "eip712Signature",
    "relayer",
}


class SXBetReadOnlyFetchError(RuntimeError):
    def __init__(self, message: str, *, error_category: str = "READ_ONLY_FETCH_FAILED") -> None:
        super().__init__(message)
        self.error_category = error_category


class SXBetReadOnlyClient:
    def __init__(
        self,
        base_url: str = SX_BET_DEFAULT_BASE_URL,
        timeout_seconds: float = 10.0,
        user_agent: str = SX_BET_DEFAULT_USER_AGENT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent

    def fetch_research_snapshot(
        self,
        *,
        max_markets: int = 25,
        captured_at: datetime | None = None,
        sport: str | None = None,
        league: str | None = None,
        query: str | None = None,
    ) -> dict[str, Any]:
        if max_markets <= 0:
            raise ValueError("max_markets must be positive")
        timestamp = captured_at or datetime.now(timezone.utc)
        markets_response = self._fetch_active_markets(max_markets=max_markets)
        markets = _sx_bet_markets_from_response(markets_response)
        retained_markets, targeting_diagnostics = _filter_sx_bet_markets(
            markets,
            sport=sport,
            league=league,
            query=query,
        )
        market_hashes = [_string_or_none(market.get("marketHash")) for market in retained_markets if isinstance(market, dict)]
        market_hashes = [market_hash for market_hash in market_hashes if market_hash]
        orders_response = self._fetch_orders(market_hashes=market_hashes)
        orders = _sx_bet_orders_from_response(orders_response)
        payload = {
            "markets": retained_markets,
            "orders": orders,
        }
        snapshot = build_sx_bet_research_snapshot(_redact_sx_bet_raw_payload(payload), captured_at=timestamp)
        snapshot["live_fetch_attempted"] = True
        snapshot["live_fetch_succeeded"] = True
        snapshot["execution_allowed_in_project_now"] = False
        snapshot["can_create_candidate_pair"] = False
        snapshot["can_create_paper_candidate"] = False
        snapshot["endpoint_metadata"] = {
            "base_url": self.base_url,
            "markets_endpoint": "/markets/active",
            "orders_endpoint": "/orders",
            "market_count_requested": max_markets,
            "orders_requested_for_market_hash_count": len(market_hashes),
            "auth_used": False,
            "wallet_or_signing_used": False,
            "targeting_method": targeting_diagnostics["targeting_method"],
        }
        snapshot["targeting"] = targeting_diagnostics
        snapshot["sx_bet_fetched_count"] = targeting_diagnostics["sx_bet_fetched_count"]
        snapshot["sx_bet_retained_count"] = targeting_diagnostics["sx_bet_retained_count"]
        snapshot["unresolved_blockers"] = [
            "sx_bet_registry_status_planned_not_implemented",
            "not_executable_schema_v1",
            "not_integrated_with_matcher_or_evaluator",
            "fee_model_not_reviewed",
            "depth_units_not_normalized",
            "settlement_wording_not_normalized",
            "venue_restrictions_not_reviewed",
        ]
        snapshot["raw_redaction_policy"] = {
            "allow_raw_network_echo": False,
            "redacted_fields": sorted(SX_BET_RAW_REDACTION_FIELDS),
        }
        return snapshot

    def _fetch_active_markets(self, *, max_markets: int) -> dict[str, Any]:
        params = {
            "pageSize": str(min(max_markets, 100)),
            "onlyMainLine": "true",
        }
        return self._get_json("/markets/active", params)

    def _fetch_orders(self, *, market_hashes: list[str]) -> dict[str, Any]:
        if not market_hashes:
            return {"status": "success", "data": []}
        params = {
            "marketHashes": ",".join(market_hashes),
            "perPage": "1000",
        }
        return self._get_json("/orders", params)

    def _get_json(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        query = urlencode(params)
        url = f"{self.base_url}{path}" + (f"?{query}" if query else "")
        request = Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 403:
                raise SXBetReadOnlyFetchError(
                    f"SX Bet public read-only fetch blocked with HTTP 403 for {path}",
                    error_category="READ_ONLY_FETCH_BLOCKED",
                ) from exc
            raise SXBetReadOnlyFetchError(
                f"SX Bet API returned HTTP {exc.code} for {path}",
                error_category="HTTP_ERROR",
            ) from exc
        except URLError as exc:
            raise SXBetReadOnlyFetchError(
                f"SX Bet API request failed for {path}: {exc.reason}",
                error_category="NETWORK_ERROR",
            ) from exc
        except TimeoutError as exc:
            raise SXBetReadOnlyFetchError(
                f"SX Bet API request timed out for {path}",
                error_category="TIMEOUT",
            ) from exc
        except JSONDecodeError as exc:
            raise SXBetReadOnlyFetchError(
                f"SX Bet API returned invalid JSON for {path}",
                error_category="MALFORMED_JSON",
            ) from exc


def build_sx_bet_research_snapshot(
    raw_payload: dict[str, Any],
    *,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    timestamp = captured_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("captured_at must include timezone information")
    markets = _list_or_empty(raw_payload.get("markets"))
    orders = _list_or_empty(raw_payload.get("orders"))
    orders_by_market = _orders_by_market_hash(orders)
    research_markets: list[dict[str, Any]] = []
    skipped_market_count = 0
    for market in markets:
        if not isinstance(market, dict):
            skipped_market_count += 1
            continue
        market_hash = _string_or_none(market.get("marketHash"))
        if market_hash is None:
            skipped_market_count += 1
            continue
        research_markets.append(
            _research_market(
                market,
                orders_by_market.get(market_hash, []),
                captured_at=timestamp,
            )
        )
    return {
        "schema_version": 1,
        "schema_kind": SX_BET_RESEARCH_SCHEMA_KIND,
        "source": "sx_bet_research",
        "source_id": "sx_bet",
        "source_type": "EXECUTABLE_VENUE",
        "implementation_status": "PLANNED_NOT_IMPLEMENTED",
        "permission": "READ_ONLY_RESEARCH",
        "is_executable": False,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "execution_allowed_in_project_now": False,
        "live_fetch_attempted": False,
        "live_fetch_succeeded": False,
        "captured_at": timestamp.isoformat(),
        "market_count": len(markets),
        "research_market_count": len(research_markets),
        "skipped_market_count": skipped_market_count,
        "order_count": len(orders),
        "research_markets": research_markets,
        "readiness_requirements": [
            "implemented_read_only_adapter",
            "real_bid_ask_depth_confirmed",
            "quote_freshness_policy",
            "fee_model",
            "settlement_wording_normalization",
            "strict_same_payoff_relationship_classification",
            "venue_restrictions_review",
            "no_wallet_private_key_signing_or_execution_logic",
        ],
        "disclaimer": (
            "SX Bet feasibility snapshot only. Not executable schema-v1, not a scanner input, "
            "and not eligible for paper-candidate or candidate-pair creation."
        ),
    }


def load_sx_bet_research_fixture(path: Path, *, captured_at: datetime | None = None) -> dict[str, Any]:
    raw_payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_payload, dict):
        raise ValueError("SX Bet research fixture must be a JSON object")
    return build_sx_bet_research_snapshot(raw_payload, captured_at=captured_at)


def build_sx_bet_failure_snapshot(
    *,
    error_category: str,
    error_message: str,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    snapshot = build_sx_bet_research_snapshot({"markets": [], "orders": []}, captured_at=captured_at)
    snapshot["live_fetch_attempted"] = True
    snapshot["live_fetch_succeeded"] = False
    snapshot["error_category"] = error_category
    snapshot["error_message"] = error_message
    snapshot["is_executable"] = False
    snapshot["execution_allowed_in_project_now"] = False
    snapshot["can_create_candidate_pair"] = False
    snapshot["can_create_paper_candidate"] = False
    snapshot["unresolved_blockers"] = [
        "sx_bet_public_readonly_fetch_unavailable",
        "sx_bet_registry_status_planned_not_implemented",
        "not_executable_schema_v1",
        "not_integrated_with_matcher_or_evaluator",
    ]
    snapshot["raw_redaction_policy"] = {
        "allow_raw_network_echo": False,
        "redacted_fields": sorted(SX_BET_RAW_REDACTION_FIELDS),
    }
    return snapshot


def _research_market(
    market: dict[str, Any],
    orders: Sequence[dict[str, Any]],
    *,
    captured_at: datetime,
) -> dict[str, Any]:
    outcome_one = _string_or_none(market.get("outcomeOneName"))
    outcome_two = _string_or_none(market.get("outcomeTwoName"))
    outcome_void = _string_or_none(market.get("outcomeVoidName"))
    orderbook = _research_orderbook(orders)
    return {
        "market_hash": _string_or_none(market.get("marketHash")),
        "event_title": _string_or_none(
            market.get("eventName")
            or market.get("gameLabel")
            or market.get("eventLabel")
            or _sx_bet_derived_event_title(market.get("teamOneName"), market.get("teamTwoName"))
        ),
        "league": _string_or_none(market.get("leagueLabel") or market.get("league")),
        "sport": _string_or_none(market.get("sportLabel") or market.get("sport")),
        "market_type": market.get("type"),
        "line": _number_or_none(market.get("line")),
        "main_line": bool(market.get("mainLine")) if market.get("mainLine") is not None else None,
        "status": _string_or_none(market.get("status")),
        "starts_at": _string_or_none(market.get("gameTime") or market.get("startTime") or market.get("startsAt")),
        "outcome_one_name": outcome_one,
        "outcome_two_name": outcome_two,
        "outcome_void_name": outcome_void,
        "settlement_metadata": {
            "settlement_source": _string_or_none(market.get("settlementSource")),
            "settlement_rule": _string_or_none(market.get("settlementRule")),
            "void_rule": outcome_void,
            "raw_status": market.get("status"),
        },
        "fee_metadata": {
            "fee_model_status": "not_normalized",
            "source_note": "SX Bet docs describe 0% single-bet fees and parlay fees, but this project has no reviewed SX Bet fee model.",
        },
        "restrictions": {
            "requires_wallet_or_private_key_for_execution": True,
            "execution_allowed_in_project_now": False,
            "candidate_pair_allowed": False,
        },
        "quote_captured_at": captured_at.isoformat(),
        "research_orderbook": orderbook,
        "raw": market,
    }


def _sx_bet_derived_event_title(team_one: Any, team_two: Any) -> str | None:
    team_one_text = _string_or_none(team_one)
    team_two_text = _string_or_none(team_two)
    if team_one_text and team_two_text:
        return f"{team_one_text} vs {team_two_text}"
    return team_one_text or team_two_text


def _filter_sx_bet_markets(
    markets: list[Any],
    *,
    sport: str | None,
    league: str | None,
    query: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    filters = {
        "sport": _string_or_none(sport),
        "league": _string_or_none(league),
        "query": _string_or_none(query),
    }
    active_filters = {key: value for key, value in filters.items() if value}
    retained: list[dict[str, Any]] = []
    rejected_samples: list[dict[str, Any]] = []
    rejected_counts: dict[str, int] = {}
    for market in markets:
        if not isinstance(market, dict):
            rejected_counts["non_object_market"] = rejected_counts.get("non_object_market", 0) + 1
            if len(rejected_samples) < 5:
                rejected_samples.append({"event_title": None, "rejection_reasons": ["non_object_market"]})
            continue
        reasons = _sx_bet_filter_rejection_reasons(market, **filters)
        if reasons:
            for reason in reasons:
                rejected_counts[reason] = rejected_counts.get(reason, 0) + 1
            if len(rejected_samples) < 5:
                rejected_samples.append(_sx_bet_filter_sample(market, rejection_reasons=reasons))
            continue
        retained.append(market)
    return retained, {
        "targeting_method": "local" if active_filters else "none",
        "api_side_filtering_used": False,
        "local_filtering_used": bool(active_filters),
        "requested_sport": filters["sport"],
        "requested_league": filters["league"],
        "requested_query": filters["query"],
        "sx_bet_fetched_count": len(markets),
        "sx_bet_retained_count": len(retained),
        "retained_sport_counts": _sx_bet_value_counts(retained, ("sportLabel", "sport")),
        "retained_league_counts": _sx_bet_value_counts(retained, ("leagueLabel", "league")),
        "rejected_count_by_reason": rejected_counts,
        "sample_retained_events": [_sx_bet_filter_sample(market) for market in retained[:5]],
        "sample_rejected_events": rejected_samples,
        "compatible_universe_note": (
            "Run fetch-live-overlap-universe for Kalshi/Polymarket with the same sport/league/query before compare-sx-bet-reference."
            if active_filters
            else "No SX Bet universe targeting requested."
        ),
    }


def _sx_bet_filter_sample(market: dict[str, Any], *, rejection_reasons: list[str] | None = None) -> dict[str, Any]:
    return {
        "market_hash": _string_or_none(market.get("marketHash")),
        "event_title": _string_or_none(
            market.get("eventName")
            or market.get("gameLabel")
            or market.get("eventLabel")
            or _sx_bet_derived_event_title(market.get("teamOneName"), market.get("teamTwoName"))
        ),
        "sport": _string_or_none(market.get("sportLabel") or market.get("sport")),
        "league": _string_or_none(market.get("leagueLabel") or market.get("league")),
        "outcome_one_name": _string_or_none(market.get("outcomeOneName")),
        "outcome_two_name": _string_or_none(market.get("outcomeTwoName")),
        "rejection_reasons": rejection_reasons or [],
    }


def _sx_bet_filter_rejection_reasons(
    market: dict[str, Any],
    *,
    sport: str | None,
    league: str | None,
    query: str | None,
) -> list[str]:
    reasons: list[str] = []
    if sport and not _sx_bet_filter_matches(market, sport, scope="sport"):
        reasons.append("sport_mismatch")
    if league and not _sx_bet_filter_matches(market, league, scope="league"):
        reasons.append("league_mismatch")
    if query and not _sx_bet_filter_matches(market, query, scope="query"):
        reasons.append("query_mismatch")
    return reasons


def _sx_bet_filter_matches(market: dict[str, Any], value: str, *, scope: str) -> bool:
    normalized_value = value.strip().lower()
    if not normalized_value:
        return True
    aliases = _sx_bet_filter_aliases(normalized_value)
    text = _sx_bet_filter_text(market, scope=scope)
    return any(alias in text for alias in aliases)


def _sx_bet_filter_aliases(value: str) -> tuple[str, ...]:
    aliases = {
        "mlb": ("mlb", "baseball", "major league baseball"),
        "baseball": ("baseball", "mlb", "major league baseball"),
        "nba": ("nba", "basketball", "pro basketball"),
        "basketball": ("basketball", "nba", "pro basketball"),
        "nfl": ("nfl", "football", "pro football"),
        "football": ("football", "nfl", "pro football"),
        "nhl": ("nhl", "hockey", "ice hockey"),
        "hockey": ("hockey", "nhl", "ice hockey"),
        "soccer": ("soccer", "fifa", "premier league"),
    }
    return aliases.get(value, (value,))


def _sx_bet_filter_text(market: dict[str, Any], *, scope: str) -> str:
    if scope == "sport":
        keys = ("sportLabel", "sport", "leagueLabel", "league")
    elif scope == "league":
        keys = ("leagueLabel", "league", "sportLabel", "sport")
    else:
        keys = (
            "eventName",
            "gameLabel",
            "eventLabel",
            "teamOneName",
            "teamTwoName",
            "outcomeOneName",
            "outcomeTwoName",
            "leagueLabel",
            "league",
            "sportLabel",
            "sport",
        )
    return " ".join(str(market.get(key) or "").lower() for key in keys)


def _sx_bet_value_counts(markets: list[dict[str, Any]], keys: tuple[str, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for market in markets:
        value = None
        for key in keys:
            value = _string_or_none(market.get(key))
            if value:
                break
        if not value:
            value = "unknown"
        counts[value] = counts.get(value, 0) + 1
    return counts


def _research_orderbook(orders: Sequence[dict[str, Any]]) -> dict[str, Any]:
    outcome_one_levels: list[dict[str, Any]] = []
    outcome_two_levels: list[dict[str, Any]] = []
    skipped_order_count = 0
    for order in orders:
        if not isinstance(order, dict):
            skipped_order_count += 1
            continue
        parsed = _research_order_level(order)
        if parsed is None:
            skipped_order_count += 1
            continue
        if order.get("isMakerBettingOutcomeOne") is False:
            outcome_one_levels.append(parsed)
        elif order.get("isMakerBettingOutcomeOne") is True:
            outcome_two_levels.append(parsed)
        else:
            skipped_order_count += 1
    outcome_one_levels.sort(key=lambda row: row["taker_price"])
    outcome_two_levels.sort(key=lambda row: row["taker_price"])
    return {
        "order_count": len(orders),
        "skipped_order_count": skipped_order_count,
        "outcome_one_taker_levels": outcome_one_levels,
        "outcome_two_taker_levels": outcome_two_levels,
        "best_taker_price_outcome_one": _best_price(outcome_one_levels),
        "best_taker_price_outcome_two": _best_price(outcome_two_levels),
        "depth_usdc_at_best_outcome_one": _depth_at_best(outcome_one_levels),
        "depth_usdc_at_best_outcome_two": _depth_at_best(outcome_two_levels),
        "unit_warning": "Depth is maker stake in USDC, not normalized prediction-market contracts.",
    }


def _research_order_level(order: dict[str, Any]) -> dict[str, Any] | None:
    maker_odds = _scaled_int_to_float(order.get("percentageOdds"), SX_BET_PERCENTAGE_ODDS_SCALE)
    total_size = _scaled_int_to_float(order.get("totalBetSize"), SX_BET_USDC_SCALE)
    fill_amount = _scaled_int_to_float(order.get("fillAmount") or 0, SX_BET_USDC_SCALE)
    if maker_odds is None or total_size is None or fill_amount is None:
        return None
    available_size = max(0.0, total_size - fill_amount)
    taker_price = 1.0 - maker_odds
    if taker_price < 0.0 or taker_price > 1.0:
        return None
    return {
        "order_hash": _string_or_none(order.get("orderHash")),
        "maker_odds": round(maker_odds, 6),
        "taker_price": round(taker_price, 6),
        "available_maker_stake_usdc": round(available_size, 6),
        "is_maker_betting_outcome_one": order.get("isMakerBettingOutcomeOne"),
        "raw": order,
    }


def _orders_by_market_hash(orders: Sequence[Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for order in orders:
        if not isinstance(order, dict):
            continue
        market_hash = _string_or_none(order.get("marketHash"))
        if market_hash is None:
            continue
        result.setdefault(market_hash, []).append(order)
    return result


def _best_price(levels: Sequence[dict[str, Any]]) -> float | None:
    if not levels:
        return None
    return levels[0]["taker_price"]


def _depth_at_best(levels: Sequence[dict[str, Any]]) -> float | None:
    if not levels:
        return None
    best = levels[0]["taker_price"]
    return round(sum(level["available_maker_stake_usdc"] for level in levels if level["taker_price"] == best), 6)


def _list_or_empty(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:
        return None
    return parsed


def _scaled_int_to_float(value: Any, scale: int) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed / scale


def _sx_bet_orders_from_response(response: dict[str, Any]) -> list[Any]:
    data = response.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("orders", "data", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def _sx_bet_markets_from_response(response: dict[str, Any]) -> list[Any]:
    data = response.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("markets", "data", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def _redact_sx_bet_raw_payload(payload: Any) -> Any:
    if isinstance(payload, list):
        return [_redact_sx_bet_raw_payload(item) for item in payload]
    if isinstance(payload, dict):
        redacted = {}
        for key, value in payload.items():
            if key in SX_BET_RAW_REDACTION_FIELDS:
                redacted[key] = SX_BET_REDACTED_VALUE
            else:
                redacted[key] = _redact_sx_bet_raw_payload(value)
        return redacted
    return payload
