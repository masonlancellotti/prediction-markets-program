from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
REFERENCE_SOURCE = "the_odds_api_saved_reference_v1"
REFERENCE_GLOB = "manual_snapshots/the_odds_api/**/oddsapi_*_odds.json"


def american_to_implied_probability(odds: int | float) -> float:
    if odds == 0:
        raise ValueError("American odds cannot be zero")
    if odds > 0:
        return 100.0 / (float(odds) + 100.0)
    return abs(float(odds)) / (abs(float(odds)) + 100.0)


def no_vig_probabilities(american_odds_by_outcome: Mapping[str, int | float]) -> dict[str, float]:
    if len(american_odds_by_outcome) < 2:
        raise ValueError("At least two outcomes are required for no-vig conversion")
    implied = {
        outcome: american_to_implied_probability(odds)
        for outcome, odds in american_odds_by_outcome.items()
    }
    total = sum(implied.values())
    if total <= 0:
        raise ValueError("Implied probability total must be positive")
    return {outcome: probability / total for outcome, probability in implied.items()}


@dataclass(frozen=True)
class ReferenceOddsRow:
    venue: str
    sport_key: str | None
    league: str | None
    sport: str | None
    event_id: str | None
    commence_time: str | None
    home_team: str | None
    away_team: str | None
    bookmaker: str | None
    bookmaker_key: str | None
    market_type: str | None
    outcome_name: str | None
    price: float | None
    odds: float | None
    implied_probability: float | None
    no_vig_probability: float | None
    point: float | None
    line: float | None
    captured_at: str | None
    raw_source_file: str
    raw_event_index: int | None
    raw_market_index: int | None
    raw_outcome_index: int | None
    blockers: tuple[str, ...] = ()
    reference_only: bool = True
    executable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_saved_reference_odds_rows(input_dir: Path) -> dict[str, Any]:
    """Load saved The Odds API odds snapshots without making network calls."""

    files = sorted(input_dir.glob(REFERENCE_GLOB))
    rows: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    events_read = 0
    for path in files:
        parsed = normalize_saved_reference_odds_file(path)
        warnings.extend(parsed["warnings"])
        events_read += parsed["event_count"]
        rows.extend(parsed["rows"])
    return {
        "source": REFERENCE_SOURCE,
        "input_dir": str(input_dir),
        "files_read": len(files),
        "odds_events_read": events_read,
        "rows": rows,
        "warnings": warnings,
    }


def normalize_saved_reference_odds_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "path": str(path),
            "event_count": 0,
            "rows": [],
            "warnings": [{"source_file": str(path), "blocker": "saved_odds_file_missing"}],
        }
    except json.JSONDecodeError:
        return {
            "path": str(path),
            "event_count": 0,
            "rows": [],
            "warnings": [{"source_file": str(path), "blocker": "saved_odds_file_invalid_json"}],
        }

    events = _events_from_payload(payload)
    captured_at = _captured_at_from_payload(payload) or _file_mtime_iso(path)
    rows: list[dict[str, Any]] = []
    skipped = 0
    for event_index, event in enumerate(events):
        if not isinstance(event, dict):
            skipped += 1
            continue
        event_rows, event_skipped = _rows_from_event(
            event,
            path=path,
            event_index=event_index,
            captured_at=captured_at,
        )
        rows.extend(row.to_dict() for row in event_rows)
        skipped += event_skipped
    warnings = []
    if not events:
        warnings.append({"source_file": str(path), "blocker": "saved_odds_file_no_events"})
    if skipped:
        warnings.append({"source_file": str(path), "blocker": "saved_odds_rows_skipped", "count": skipped})
    return {
        "path": str(path),
        "event_count": len(events),
        "rows": rows,
        "warnings": warnings,
    }


def _events_from_payload(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("raw_response"), list):
        return list(payload["raw_response"])
    if isinstance(payload.get("events"), list):
        return list(payload["events"])
    if isinstance(payload.get("data"), list):
        return list(payload["data"])
    if payload.get("schema_kind") == "reference_snapshot_v1" and isinstance(payload.get("normalized_records"), list):
        return _events_from_reference_records(payload["normalized_records"])
    return []


def _events_from_reference_records(records: Sequence[Any]) -> list[dict[str, Any]]:
    events: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        event_id = str(record.get("event_id") or "unknown")
        event = events.setdefault(
            event_id,
            {
                "id": record.get("event_id"),
                "sport_key": record.get("sport_key"),
                "sport_title": record.get("sport_title"),
                "commence_time": record.get("commence_time"),
                "home_team": None,
                "away_team": None,
                "bookmakers": [],
            },
        )
        bookmaker_key = str(record.get("bookmaker_key") or record.get("bookmaker") or "unknown")
        bookmaker = next((row for row in event["bookmakers"] if row.get("key") == bookmaker_key), None)
        if bookmaker is None:
            bookmaker = {"key": bookmaker_key, "title": record.get("bookmaker"), "markets": []}
            event["bookmakers"].append(bookmaker)
        market_key = str(record.get("market_type") or "unknown")
        market = next((row for row in bookmaker["markets"] if row.get("key") == market_key), None)
        if market is None:
            market = {"key": market_key, "outcomes": []}
            bookmaker["markets"].append(market)
        market["outcomes"].append(
            {
                "name": record.get("outcome_name"),
                "price": record.get("american_odds") or record.get("price") or record.get("odds"),
                "point": record.get("point"),
            }
        )
    return list(events.values())


def _captured_at_from_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("captured_at", "retrieved_at", "snapshot_captured_at", "generated_at"):
        value = _string_or_none(payload.get(key))
        if value:
            return value
    return None


def _rows_from_event(
    event: dict[str, Any],
    *,
    path: Path,
    event_index: int,
    captured_at: str | None,
) -> tuple[list[ReferenceOddsRow], int]:
    bookmakers = event.get("bookmakers")
    if not isinstance(bookmakers, list):
        return [], 1
    rows: list[ReferenceOddsRow] = []
    skipped = 0
    for bookmaker in bookmakers:
        if not isinstance(bookmaker, dict):
            skipped += 1
            continue
        markets = bookmaker.get("markets")
        if not isinstance(markets, list):
            skipped += 1
            continue
        for market_index, market in enumerate(markets):
            if not isinstance(market, dict):
                skipped += 1
                continue
            outcomes = market.get("outcomes")
            if not isinstance(outcomes, list):
                skipped += 1
                continue
            no_vig = _no_vig_by_outcome(outcomes)
            for outcome_index, outcome in enumerate(outcomes):
                if not isinstance(outcome, dict):
                    skipped += 1
                    continue
                row = _row_from_outcome(
                    event=event,
                    bookmaker=bookmaker,
                    market=market,
                    outcome=outcome,
                    no_vig=no_vig,
                    path=path,
                    event_index=event_index,
                    market_index=market_index,
                    outcome_index=outcome_index,
                    captured_at=captured_at,
                )
                if row is None:
                    skipped += 1
                else:
                    rows.append(row)
    return rows, skipped


def _row_from_outcome(
    *,
    event: dict[str, Any],
    bookmaker: dict[str, Any],
    market: dict[str, Any],
    outcome: dict[str, Any],
    no_vig: dict[str, float | None],
    path: Path,
    event_index: int,
    market_index: int,
    outcome_index: int,
    captured_at: str | None,
) -> ReferenceOddsRow | None:
    outcome_name = _string_or_none(outcome.get("name"))
    price = _number_or_none(outcome.get("price"))
    if outcome_name is None or price is None:
        return None
    try:
        implied = round(american_to_implied_probability(price), 6)
    except ValueError:
        return None
    point = _number_or_none(outcome.get("point"))
    sport = _string_or_none(event.get("sport_title")) or _string_or_none(event.get("sport_key"))
    no_vig_probability = no_vig.get(_outcome_key(outcome))
    blockers = ["reference_only_source", "not_executable", "no_same_payoff_claim"]
    if no_vig_probability is None:
        blockers.append("vig_removal_ambiguous")
    return ReferenceOddsRow(
        venue="the_odds_api",
        sport_key=_string_or_none(event.get("sport_key")),
        league=sport,
        sport=sport,
        event_id=_string_or_none(event.get("id")),
        commence_time=_string_or_none(event.get("commence_time")),
        home_team=_string_or_none(event.get("home_team")),
        away_team=_string_or_none(event.get("away_team")),
        bookmaker=_string_or_none(bookmaker.get("title")) or _string_or_none(bookmaker.get("key")),
        bookmaker_key=_string_or_none(bookmaker.get("key")),
        market_type=_string_or_none(market.get("key")),
        outcome_name=outcome_name,
        price=price,
        odds=price,
        implied_probability=implied,
        no_vig_probability=no_vig_probability,
        point=point,
        line=point,
        captured_at=captured_at,
        raw_source_file=str(path),
        raw_event_index=event_index,
        raw_market_index=market_index,
        raw_outcome_index=outcome_index,
        blockers=tuple(blockers),
    )


def _no_vig_by_outcome(outcomes: Sequence[Any]) -> dict[str, float | None]:
    odds_by_outcome: dict[str, int | float] = {}
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        price = _number_or_none(outcome.get("price"))
        name = _string_or_none(outcome.get("name"))
        if price is None or name is None:
            continue
        odds_by_outcome[_outcome_key(outcome)] = price
    if len(odds_by_outcome) != 2:
        return {key: None for key in odds_by_outcome}
    try:
        return {key: round(value, 6) for key, value in no_vig_probabilities(odds_by_outcome).items()}
    except ValueError:
        return {key: None for key in odds_by_outcome}


def _outcome_key(outcome: dict[str, Any]) -> str:
    point = outcome.get("point")
    name = str(outcome.get("name") or "")
    return name if point is None else f"{name}|{point}"


def _file_mtime_iso(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return None


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
