from __future__ import annotations

import re
from typing import Any


_TOKEN_RE = re.compile(r"[a-z0-9]+")

MLB_WORLD_SERIES_SCOPE = "WORLD_SERIES"

_MLB_TEAM_ALIASES: dict[str, tuple[str, ...]] = {
    "ARI": ("arizona diamondbacks", "diamondbacks", "arizona"),
    "ATH": ("athletics", "a s"),
    "ATL": ("atlanta braves", "braves", "atlanta"),
    "BAL": ("baltimore orioles", "orioles", "baltimore"),
    "BOS": ("boston red sox", "red sox", "boston"),
    "CHC": ("chicago cubs", "chicago c", "cubs"),
    "CWS": ("chicago white sox", "chicago ws", "chicago w", "white sox"),
    "CIN": ("cincinnati reds", "reds", "cincinnati"),
    "CLE": ("cleveland guardians", "guardians", "cleveland"),
    "COL": ("colorado rockies", "rockies", "colorado"),
    "DET": ("detroit tigers", "tigers", "detroit"),
    "HOU": ("houston astros", "astros", "houston"),
    "KC": ("kansas city royals", "kansas city", "royals"),
    "LAA": ("los angeles angels", "los angeles a", "laa", "angels"),
    "LAD": ("los angeles dodgers", "los angeles d", "lad", "dodgers"),
    "MIA": ("miami marlins", "marlins", "miami"),
    "MIL": ("milwaukee brewers", "brewers", "milwaukee"),
    "MIN": ("minnesota twins", "twins", "minnesota"),
    "NYM": ("new york mets", "new york m", "nym", "mets"),
    "NYY": ("new york yankees", "new york y", "nyy", "yankees"),
    "PHI": ("philadelphia phillies", "phillies", "philadelphia"),
    "PIT": ("pittsburgh pirates", "pirates", "pittsburgh"),
    "SD": ("san diego padres", "san diego", "padres"),
    "SEA": ("seattle mariners", "mariners", "seattle"),
    "SF": ("san francisco giants", "san francisco", "giants"),
    "STL": ("st louis cardinals", "st louis", "cardinals"),
    "TB": ("tampa bay rays", "tampa bay", "rays"),
    "TEX": ("texas rangers", "rangers", "texas"),
    "TOR": ("toronto blue jays", "toronto", "blue jays", "bluejays"),
    "WSH": ("washington nationals", "nationals", "washington"),
}


def classify_mlb_competition_scope(market: dict[str, Any]) -> str:
    lower = _market_text(market).lower()
    tokens = set(_TOKEN_RE.findall(lower))
    if "american league championship series" in lower or "alcs" in tokens:
        return "ALCS"
    if "national league championship series" in lower or "nlcs" in tokens:
        return "NLCS"
    if "world series" in lower or "pro baseball championship" in lower:
        return MLB_WORLD_SERIES_SCOPE
    if " vs " in lower or " at " in lower or " beat " in lower or " defeat " in lower:
        return "GAME"
    return "UNKNOWN"


def extract_mlb_team_id(market_or_text: dict[str, Any] | str) -> str | None:
    text = market_or_text if isinstance(market_or_text, str) else _market_text(market_or_text)
    tokens = set(_TOKEN_RE.findall(_normalize_text(text)))
    matches: list[tuple[int, str]] = []
    for team_id, aliases in _MLB_TEAM_ALIASES.items():
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


def mlb_world_series_profile(market: dict[str, Any]) -> dict[str, Any]:
    scope = classify_mlb_competition_scope(market)
    team_id = extract_mlb_team_id(market)
    return {
        "is_mlb_world_series": scope == MLB_WORLD_SERIES_SCOPE,
        "scope": scope,
        "team_id": team_id,
        "market_text": _market_text(market),
    }


def same_mlb_world_series_team(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_profile = mlb_world_series_profile(left)
    right_profile = mlb_world_series_profile(right)
    return (
        left_profile["is_mlb_world_series"]
        and right_profile["is_mlb_world_series"]
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
        )
    )


def _normalize_text(value: str) -> str:
    return " ".join(_TOKEN_RE.findall(value.lower()))
