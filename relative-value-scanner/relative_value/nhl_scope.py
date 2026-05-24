from __future__ import annotations

import re
import unicodedata
from typing import Any


_TOKEN_RE = re.compile(r"[a-z0-9]+")

NHL_STANLEY_CUP_SCOPE = "STANLEY_CUP"
NHL_CONFERENCE_WINNER_SCOPE = "CONFERENCE_WINNER"
NHL_DIVISION_WINNER_SCOPE = "DIVISION_WINNER"
NHL_GAME_SCOPE = "GAME"
NHL_UNKNOWN_SCOPE = "UNKNOWN"

_NHL_TEAM_ALIASES: dict[str, tuple[str, ...]] = {
    "CAR": ("carolina hurricanes", "hurricanes"),
    "COL": ("colorado avalanche", "avalanche"),
    "MTL": ("montreal canadiens", "canadiens"),
    "VGK": ("vegas golden knights", "golden knights"),
}


def classify_nhl_competition_scope(market: dict[str, Any]) -> str:
    text = _market_text(market)
    normalized = _normalize_text(text)
    tokens = set(_TOKEN_RE.findall(normalized))
    if " vs " in text.lower() or " matchup" in normalized or "game" in tokens:
        return NHL_GAME_SCOPE
    if "division" in normalized:
        return NHL_DIVISION_WINNER_SCOPE
    if "conference" in normalized:
        return NHL_CONFERENCE_WINNER_SCOPE
    if "stanley cup" in normalized or "pro hockey championship" in normalized:
        return NHL_STANLEY_CUP_SCOPE
    return NHL_UNKNOWN_SCOPE


def extract_nhl_team_id(market_or_text: dict[str, Any] | str) -> str | None:
    text = market_or_text if isinstance(market_or_text, str) else _market_text(market_or_text)
    tokens = set(_TOKEN_RE.findall(_normalize_text(text)))
    matches: list[tuple[int, str]] = []
    for team_id, aliases in _NHL_TEAM_ALIASES.items():
        for alias in aliases:
            alias_tokens = tuple(_TOKEN_RE.findall(_normalize_text(alias)))
            if alias_tokens and set(alias_tokens) <= tokens:
                matches.append((len(alias_tokens), team_id))
    if not matches:
        return None
    matches.sort(reverse=True)
    top_len = matches[0][0]
    top_ids = sorted({team_id for length, team_id in matches if length == top_len})
    if len(top_ids) == 1:
        return top_ids[0]
    return None


def nhl_stanley_cup_profile(market: dict[str, Any]) -> dict[str, Any]:
    scope = classify_nhl_competition_scope(market)
    team_id = extract_nhl_team_id(market)
    return {
        "is_nhl_stanley_cup": scope == NHL_STANLEY_CUP_SCOPE,
        "scope": scope,
        "team_id": team_id,
        "championship_year": _championship_year(market),
        "market_text": _market_text(market),
    }


def same_nhl_stanley_cup_team(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_profile = nhl_stanley_cup_profile(left)
    right_profile = nhl_stanley_cup_profile(right)
    return (
        left_profile["is_nhl_stanley_cup"]
        and right_profile["is_nhl_stanley_cup"]
        and bool(left_profile["team_id"])
        and left_profile["team_id"] == right_profile["team_id"]
        and bool(left_profile["championship_year"])
        and left_profile["championship_year"] == right_profile["championship_year"]
    )


def _championship_year(market: dict[str, Any]) -> int | None:
    text = _normalize_text(_market_text(market))
    short_season = re.search(r"\b(20\d{2})\s+(\d{2})\b", text)
    if short_season:
        start_year = int(short_season.group(1))
        end_two_digits = int(short_season.group(2))
        end_year = (start_year // 100) * 100 + end_two_digits
        if end_year == start_year + 1:
            return end_year
    long_season = re.search(r"\b(20\d{2})\s+(20\d{2})\b", text)
    if long_season:
        start_year = int(long_season.group(1))
        end_year = int(long_season.group(2))
        if end_year == start_year + 1:
            return end_year
    years = [int(value) for value in re.findall(r"\b20\d{2}\b", text)]
    if len(set(years)) == 1:
        return years[0]
    return None


def _market_text(market: dict[str, Any]) -> str:
    raw = market.get("raw") if isinstance(market.get("raw"), dict) else {}
    return " ".join(
        str(value or "")
        for value in (
            market.get("question"),
            market.get("title"),
            market.get("event_title"),
            market.get("market_id"),
            market.get("ticker"),
            raw.get("event_slug"),
            raw.get("series_ticker"),
            raw.get("event_ticker"),
            raw.get("yes_sub_title"),
            raw.get("no_sub_title"),
            raw.get("rules_primary"),
            raw.get("description"),
        )
    )


def _normalize_text(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value.lower()).encode("ascii", "ignore").decode("ascii")
    return " ".join(_TOKEN_RE.findall(ascii_value))
