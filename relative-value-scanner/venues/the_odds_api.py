from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from relative_value.models import NormalizedMarket, SourceKind
from relative_value.normalize import parse_datetime
from relative_value.reference_odds import american_to_implied_probability, no_vig_probabilities
from venues.base import ReadOnlyVenueAdapter


THE_ODDS_API_BASE_URL = "https://api.the-odds-api.com/v4"
DEFAULT_STALE_AFTER_SECONDS = 15 * 60


class FixtureTheOddsApiAdapter(ReadOnlyVenueAdapter):
    name = "the_odds_api_fixture"

    def __init__(self, path: Path) -> None:
        self.path = path

    def load_markets(self) -> Sequence[NormalizedMarket]:
        rows = json.loads(self.path.read_text(encoding="utf-8"))
        markets: list[NormalizedMarket] = []
        for row in rows:
            sportsbook = str(row.get("sportsbook", "fixture_book"))
            probabilities = no_vig_probabilities(row["american_odds"])
            for outcome_name, probability in probabilities.items():
                markets.append(
                    NormalizedMarket(
                        venue=sportsbook,
                        market_id=f"{sportsbook}:{row['event_id']}:{outcome_name}",
                        event_name=str(row["event_name"]),
                        outcome_name=str(outcome_name),
                        source_kind=SourceKind.SPORTSBOOK_REFERENCE,
                        yes_reference_probability=probability,
                        liquidity_top_contracts=0.0,
                        volume_24h=0.0,
                        settlement_time=parse_datetime(row.get("settlement_time")),
                        captured_at=parse_datetime(row.get("captured_at")),
                        settlement_rule=str(row.get("settlement_rule", "")),
                        is_executable=False,
                        raw=row,
                    )
                )
        return markets


class TheOddsApiReadOnlyClient:
    """Small read-only client for The Odds API odds endpoint."""

    def __init__(
        self,
        api_key: str,
        base_url: str = THE_ODDS_API_BASE_URL,
        timeout_seconds: float = 10.0,
        user_agent: str = "relative-value-scanner/0.1 read-only",
    ) -> None:
        if not api_key:
            raise ValueError("The Odds API key is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent

    def fetch_odds(
        self,
        *,
        sport_key: str,
        regions: str = "us",
        markets: str = "h2h,spreads,totals",
        odds_format: str = "american",
    ) -> Any:
        if not sport_key:
            raise ValueError("sport_key is required")
        params = {
            "apiKey": self.api_key,
            "regions": regions,
            "markets": markets,
            "oddsFormat": odds_format,
        }
        request = Request(
            f"{self.base_url}/sports/{sport_key}/odds?{urlencode(params)}",
            headers={
                "Accept": "application/json",
                "User-Agent": self.user_agent,
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = response.read().decode("utf-8")
        except HTTPError as exc:
            raise RuntimeError(f"The Odds API returned HTTP {exc.code} for /sports/{sport_key}/odds") from exc
        except URLError as exc:
            raise RuntimeError(f"The Odds API request failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RuntimeError("The Odds API request timed out") from exc
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RuntimeError("The Odds API returned invalid JSON") from exc

    def fetch_reference_snapshot(
        self,
        *,
        sport_key: str,
        regions: str = "us",
        markets: str = "h2h,spreads,totals",
        odds_format: str = "american",
        stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
    ) -> dict[str, Any]:
        raw_response = self.fetch_odds(
            sport_key=sport_key,
            regions=regions,
            markets=markets,
            odds_format=odds_format,
        )
        return build_the_odds_api_reference_snapshot(
            raw_response,
            sport_key=sport_key,
            regions=regions,
            markets=markets,
            odds_format=odds_format,
            stale_after_seconds=stale_after_seconds,
        )


def build_the_odds_api_reference_snapshot(
    raw_response: Any,
    *,
    sport_key: str,
    regions: str,
    markets: str,
    odds_format: str,
    retrieved_at: datetime | None = None,
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
) -> dict[str, Any]:
    captured_at = retrieved_at or datetime.now(timezone.utc)
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise ValueError("retrieved_at must include timezone information")
    if stale_after_seconds <= 0:
        raise ValueError("stale_after_seconds must be positive")
    events = parse_the_odds_api_response(raw_response)
    stale_after = captured_at + timedelta(seconds=stale_after_seconds)
    records, skipped_count = extract_reference_records(
        events,
        retrieved_at=captured_at,
        stale_after=stale_after,
        odds_format=odds_format,
    )
    return {
        "schema_version": 1,
        "schema_kind": "reference_snapshot_v1",
        "source": "the_odds_api_reference",
        "source_id": "the_odds_api",
        "source_type": "REFERENCE_ONLY",
        "permission": "REFERENCE_ONLY",
        "retrieved_at": captured_at.isoformat(),
        "stale_after": stale_after.isoformat(),
        "request": {
            "sport_key": sport_key,
            "regions": regions,
            "markets": markets,
            "odds_format": odds_format,
            "endpoint": f"/sports/{sport_key}/odds",
        },
        "raw_response": raw_response,
        "event_count": len(events),
        "record_count": sum(_outcome_count(event) for event in events),
        "normalized_count": len(records),
        "skipped_count": skipped_count,
        "normalized_records": records,
    }


def parse_the_odds_api_response(raw_response: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_response, list):
        raise ValueError("The Odds API response must be a list")
    return [event for event in raw_response if isinstance(event, dict)]


def extract_reference_records(
    events: Sequence[dict[str, Any]],
    *,
    retrieved_at: datetime,
    stale_after: datetime,
    odds_format: str,
) -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    skipped_count = 0
    for event in events:
        event_id = _string_or_none(event.get("id"))
        event_title = _event_title(event)
        bookmakers = event.get("bookmakers")
        if event_id is None or event_title is None or not isinstance(bookmakers, list):
            skipped_count += max(1, _outcome_count(event))
            continue
        for bookmaker in bookmakers:
            if not isinstance(bookmaker, dict):
                skipped_count += 1
                continue
            bookmaker_key = _string_or_none(bookmaker.get("key"))
            bookmaker_title = _string_or_none(bookmaker.get("title")) or bookmaker_key
            markets = bookmaker.get("markets")
            if bookmaker_key is None or not isinstance(markets, list):
                skipped_count += 1
                continue
            for market in markets:
                if not isinstance(market, dict):
                    skipped_count += 1
                    continue
                market_key = _string_or_none(market.get("key"))
                outcomes = market.get("outcomes")
                if market_key is None or not isinstance(outcomes, list):
                    skipped_count += 1
                    continue
                no_vig_by_outcome = _no_vig_by_outcome(outcomes)
                for outcome in outcomes:
                    if not isinstance(outcome, dict):
                        skipped_count += 1
                        continue
                    outcome_name = _string_or_none(outcome.get("name"))
                    price = _number_or_none(outcome.get("price"))
                    if outcome_name is None or price is None:
                        skipped_count += 1
                        continue
                    try:
                        implied_probability = american_to_implied_probability(price)
                    except ValueError:
                        skipped_count += 1
                        continue
                    point = _number_or_none(outcome.get("point"))
                    records.append(
                        {
                            "source_id": "the_odds_api",
                            "source_type": "REFERENCE_ONLY",
                            "permission": "REFERENCE_ONLY",
                            "event_id": event_id,
                            "event_title": event_title,
                            "sport_key": event.get("sport_key"),
                            "sport_title": event.get("sport_title"),
                            "commence_time": event.get("commence_time"),
                            "bookmaker_key": bookmaker_key,
                            "bookmaker": bookmaker_title,
                            "market_type": market_key,
                            "outcome_name": outcome_name,
                            "point": point,
                            "odds_format": odds_format,
                            "american_odds": price,
                            "implied_probability": round(implied_probability, 6),
                            "no_vig_probability": no_vig_by_outcome.get(_outcome_key(outcome)),
                            "retrieved_at": retrieved_at.isoformat(),
                            "stale_after": stale_after.isoformat(),
                            "provenance": {
                                "event_id": event_id,
                                "bookmaker_key": bookmaker_key,
                                "market_key": market_key,
                                "bookmaker_last_update": bookmaker.get("last_update"),
                                "market_last_update": market.get("last_update"),
                            },
                            "is_executable": False,
                            "usable_for_trade_decision": False,
                            "raw": outcome,
                        }
                    )
    return records, skipped_count


def write_the_odds_api_reference_snapshot(snapshot: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")


def _no_vig_by_outcome(outcomes: Sequence[Any]) -> dict[str, float]:
    odds_by_outcome: dict[str, int | float] = {}
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        name = _string_or_none(outcome.get("name"))
        price = _number_or_none(outcome.get("price"))
        if name is None or price is None:
            continue
        key = _outcome_key(outcome)
        odds_by_outcome[key] = price
    if len(odds_by_outcome) < 2:
        return {}
    try:
        return {outcome: round(probability, 6) for outcome, probability in no_vig_probabilities(odds_by_outcome).items()}
    except ValueError:
        return {}


def _outcome_key(outcome: dict[str, Any]) -> str:
    name = str(outcome.get("name") or "")
    point = outcome.get("point")
    return name if point is None else f"{name}|{point}"


def _event_title(event: dict[str, Any]) -> str | None:
    home = _string_or_none(event.get("home_team"))
    away = _string_or_none(event.get("away_team"))
    if home and away:
        return f"{away} at {home}"
    return _string_or_none(event.get("sport_title"))


def _outcome_count(event: dict[str, Any]) -> int:
    total = 0
    bookmakers = event.get("bookmakers")
    if not isinstance(bookmakers, list):
        return 0
    for bookmaker in bookmakers:
        if not isinstance(bookmaker, dict):
            continue
        markets = bookmaker.get("markets")
        if not isinstance(markets, list):
            continue
        for market in markets:
            if not isinstance(market, dict):
                continue
            outcomes = market.get("outcomes")
            if isinstance(outcomes, list):
                total += len(outcomes)
    return total


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
