from __future__ import annotations

import re
from typing import Any


_TOKEN_RE = re.compile(r"[a-z0-9]+")

NBA_CHAMPIONSHIP_SCOPE = "NBA_CHAMPIONSHIP"
NBA_CONFERENCE_SCOPE = "NBA_CONFERENCE"

_NBA_TEAM_ALIASES: dict[str, tuple[str, ...]] = {
    "ATL": ("atlanta hawks", "hawks", "atlanta"),
    "BOS": ("boston celtics", "celtics", "boston"),
    "BKN": ("brooklyn nets", "nets", "brooklyn"),
    "CHA": ("charlotte hornets", "hornets", "charlotte"),
    "CHI": ("chicago bulls", "bulls", "chicago"),
    "CLE": ("cleveland cavaliers", "cavaliers", "cleveland"),
    "DAL": ("dallas mavericks", "mavericks", "dallas"),
    "DEN": ("denver nuggets", "nuggets", "denver"),
    "DET": ("detroit pistons", "pistons", "detroit"),
    "GSW": ("golden state warriors", "warriors", "golden state"),
    "HOU": ("houston rockets", "rockets", "houston"),
    "IND": ("indiana pacers", "pacers", "indiana"),
    "LAC": ("los angeles clippers", "la clippers", "clippers"),
    "LAL": ("los angeles lakers", "la lakers", "lakers"),
    "MEM": ("memphis grizzlies", "grizzlies", "memphis"),
    "MIA": ("miami heat", "heat", "miami"),
    "MIL": ("milwaukee bucks", "bucks", "milwaukee"),
    "MIN": ("minnesota timberwolves", "timberwolves", "minnesota"),
    "NOP": ("new orleans pelicans", "pelicans", "new orleans"),
    "NYK": ("new york knicks", "knicks", "new york"),
    "OKC": ("oklahoma city thunder", "thunder", "oklahoma city"),
    "ORL": ("orlando magic", "magic", "orlando"),
    "PHI": ("philadelphia 76ers", "philadelphia sixers", "sixers", "76ers", "philadelphia"),
    "PHX": ("phoenix suns", "suns", "phoenix"),
    "POR": ("portland trail blazers", "trail blazers", "blazers", "portland"),
    "SAC": ("sacramento kings", "kings", "sacramento"),
    "SAS": ("san antonio spurs", "spurs", "san antonio"),
    "TOR": ("toronto raptors", "raptors", "toronto"),
    "UTA": ("utah jazz", "jazz", "utah"),
    "WAS": ("washington wizards", "wizards", "washington"),
}


def classify_nba_competition_scope(market: dict[str, Any]) -> str:
    lower = _market_text(market).lower()
    normalized = _normalize_text(lower)
    if "conference finals" in normalized or "conference title" in normalized or "conference winner" in normalized:
        return NBA_CONFERENCE_SCOPE
    if (
        "nba finals" in normalized
        or "pro basketball finals" in normalized
        or "nba champion" in normalized
        or "nba championship" in normalized
    ):
        return NBA_CHAMPIONSHIP_SCOPE
    return "UNKNOWN"


def extract_nba_team_id(market_or_text: dict[str, Any] | str) -> str | None:
    text = market_or_text if isinstance(market_or_text, str) else _market_text(market_or_text)
    tokens = set(_TOKEN_RE.findall(_normalize_text(text)))
    matches: list[tuple[int, str]] = []
    for team_id, aliases in _NBA_TEAM_ALIASES.items():
        for alias in aliases:
            alias_tokens = tuple(_TOKEN_RE.findall(alias))
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


def nba_championship_profile(market: dict[str, Any]) -> dict[str, Any]:
    scope = classify_nba_competition_scope(market)
    team_id = extract_nba_team_id(market)
    return {
        "is_nba_championship": scope == NBA_CHAMPIONSHIP_SCOPE,
        "scope": scope,
        "team_id": team_id,
        "market_text": _market_text(market),
    }


def same_nba_championship_team(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_profile = nba_championship_profile(left)
    right_profile = nba_championship_profile(right)
    return (
        left_profile["is_nba_championship"]
        and right_profile["is_nba_championship"]
        and bool(left_profile["team_id"])
        and left_profile["team_id"] == right_profile["team_id"]
    )


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
        )
    )


def _normalize_text(value: str) -> str:
    return " ".join(_TOKEN_RE.findall(value.lower()))
