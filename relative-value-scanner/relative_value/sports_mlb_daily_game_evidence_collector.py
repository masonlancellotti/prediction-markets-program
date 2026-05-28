from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date as date_cls
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


SCHEMA_VERSION = 1
SCHEMA_KIND = "sports_mlb_daily_game_evidence_collection_v1"
REPORT_SOURCE = "sports_mlb_daily_game_evidence_collection_v1"

POLYMARKET_GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
POLYMARKET_CLOB_BASE_URL = "https://clob.polymarket.com"
KALSHI_PUBLIC_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
DEFAULT_USER_AGENT = "relative-value-scanner/0.1 public-read-only"

MLBGAME_RULES_URL = "https://kalshi-public-docs.s3.amazonaws.com/contract_terms/MLBGAME.pdf"
KALSHI_SERIES_TICKER = "KXMLBGAME"

HttpGet = Callable[[str, float], Any]

_EXCLUDE_POLYMARKET_RE = re.compile(
    r"\b(spread|run\s*line|total|over/under|over\s+under|player|props?|strikeouts?|hits?|"
    r"home\s+runs?|rbi|inning|innings|futures?|world\s+series|parlay|same\s+game|series\s+winner)\b",
    re.IGNORECASE,
)
_MLB_RE = re.compile(r"\b(mlb|baseball)\b", re.IGNORECASE)
_KALSHI_EVENT_RE = re.compile(r"-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})(?P<hh>\d{2})(?P<mm>\d{2})(?P<teams>[A-Z]+)$")
_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")

_MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}

TEAM_CODE_TO_NAME = {
    "ARI": "Arizona Diamondbacks",
    "ATL": "Atlanta Braves",
    "BAL": "Baltimore Orioles",
    "BOS": "Boston Red Sox",
    "CHC": "Chicago Cubs",
    "CWS": "Chicago White Sox",
    "CIN": "Cincinnati Reds",
    "CLE": "Cleveland Guardians",
    "COL": "Colorado Rockies",
    "DET": "Detroit Tigers",
    "HOU": "Houston Astros",
    "KC": "Kansas City Royals",
    "LAA": "Los Angeles Angels",
    "LAD": "Los Angeles Dodgers",
    "MIA": "Miami Marlins",
    "MIL": "Milwaukee Brewers",
    "MIN": "Minnesota Twins",
    "NYM": "New York Mets",
    "NYY": "New York Yankees",
    "ATH": "Athletics",
    "PHI": "Philadelphia Phillies",
    "PIT": "Pittsburgh Pirates",
    "SD": "San Diego Padres",
    "SEA": "Seattle Mariners",
    "SF": "San Francisco Giants",
    "STL": "St. Louis Cardinals",
    "TB": "Tampa Bay Rays",
    "TEX": "Texas Rangers",
    "TOR": "Toronto Blue Jays",
    "WSH": "Washington Nationals",
}

_TEAM_ALIASES = {
    "arizona diamondbacks": "ARI",
    "diamondbacks": "ARI",
    "ari": "ARI",
    "atlanta braves": "ATL",
    "braves": "ATL",
    "atl": "ATL",
    "baltimore orioles": "BAL",
    "orioles": "BAL",
    "bal": "BAL",
    "boston red sox": "BOS",
    "red sox": "BOS",
    "bos": "BOS",
    "chicago cubs": "CHC",
    "cubs": "CHC",
    "chc": "CHC",
    "chicago white sox": "CWS",
    "white sox": "CWS",
    "cws": "CWS",
    "chw": "CWS",
    "cincinnati reds": "CIN",
    "reds": "CIN",
    "cin": "CIN",
    "cleveland guardians": "CLE",
    "guardians": "CLE",
    "cle": "CLE",
    "colorado rockies": "COL",
    "rockies": "COL",
    "col": "COL",
    "detroit tigers": "DET",
    "tigers": "DET",
    "det": "DET",
    "houston astros": "HOU",
    "astros": "HOU",
    "hou": "HOU",
    "kansas city royals": "KC",
    "royals": "KC",
    "kc": "KC",
    "kcr": "KC",
    "los angeles angels": "LAA",
    "la angels": "LAA",
    "angels": "LAA",
    "laa": "LAA",
    "los angeles dodgers": "LAD",
    "la dodgers": "LAD",
    "dodgers": "LAD",
    "lad": "LAD",
    "miami marlins": "MIA",
    "marlins": "MIA",
    "mia": "MIA",
    "milwaukee brewers": "MIL",
    "brewers": "MIL",
    "mil": "MIL",
    "minnesota twins": "MIN",
    "twins": "MIN",
    "min": "MIN",
    "new york mets": "NYM",
    "mets": "NYM",
    "nym": "NYM",
    "new york yankees": "NYY",
    "yankees": "NYY",
    "nyy": "NYY",
    "athletics": "ATH",
    "ath": "ATH",
    "oak": "ATH",
    "philadelphia phillies": "PHI",
    "phillies": "PHI",
    "phi": "PHI",
    "pittsburgh pirates": "PIT",
    "pirates": "PIT",
    "pit": "PIT",
    "san diego padres": "SD",
    "padres": "SD",
    "sd": "SD",
    "sdp": "SD",
    "seattle mariners": "SEA",
    "mariners": "SEA",
    "sea": "SEA",
    "san francisco giants": "SF",
    "giants": "SF",
    "sf": "SF",
    "sfg": "SF",
    "st louis cardinals": "STL",
    "st. louis cardinals": "STL",
    "cardinals": "STL",
    "stl": "STL",
    "tampa bay rays": "TB",
    "rays": "TB",
    "tb": "TB",
    "tbr": "TB",
    "texas rangers": "TEX",
    "rangers": "TEX",
    "tex": "TEX",
    "toronto blue jays": "TOR",
    "blue jays": "TOR",
    "tor": "TOR",
    "washington nationals": "WSH",
    "nationals": "WSH",
    "wsh": "WSH",
    "was": "WSH",
}


def write_mlb_daily_game_evidence_files(
    *,
    target_date: str | None,
    output_dir: Path,
    normalized_output_dir: Path,
    max_games: int = 20,
    timeout_seconds: float = 10.0,
    generated_at: datetime | None = None,
    http_get: HttpGet | None = None,
    polymarket_gamma_base_url: str = POLYMARKET_GAMMA_BASE_URL,
    polymarket_clob_base_url: str = POLYMARKET_CLOB_BASE_URL,
    kalshi_base_url: str = KALSHI_PUBLIC_BASE_URL,
) -> dict[str, Any]:
    report = build_mlb_daily_game_evidence_collection(
        target_date=target_date,
        output_dir=output_dir,
        normalized_output_dir=normalized_output_dir,
        max_games=max_games,
        timeout_seconds=timeout_seconds,
        generated_at=generated_at,
        http_get=http_get,
        polymarket_gamma_base_url=polymarket_gamma_base_url,
        polymarket_clob_base_url=polymarket_clob_base_url,
        kalshi_base_url=kalshi_base_url,
    )
    return report


def build_mlb_daily_game_evidence_collection(
    *,
    target_date: str | None,
    output_dir: Path,
    normalized_output_dir: Path,
    max_games: int = 20,
    timeout_seconds: float = 10.0,
    generated_at: datetime | None = None,
    http_get: HttpGet | None = None,
    polymarket_gamma_base_url: str = POLYMARKET_GAMMA_BASE_URL,
    polymarket_clob_base_url: str = POLYMARKET_CLOB_BASE_URL,
    kalshi_base_url: str = KALSHI_PUBLIC_BASE_URL,
) -> dict[str, Any]:
    if max_games <= 0:
        raise ValueError("max_games must be positive")
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    date_label = target_date or date_cls.today().isoformat()
    parsed_date = _parse_date(date_label)
    timestamp = generated.strftime("%Y%m%d_%H%M%SZ")
    raw_root = output_dir / timestamp
    polymarket_raw_dir = raw_root / "polymarket"
    kalshi_raw_dir = raw_root / "kalshi"
    normalized_output_dir.mkdir(parents=True, exist_ok=True)
    polymarket_raw_dir.mkdir(parents=True, exist_ok=True)
    kalshi_raw_dir.mkdir(parents=True, exist_ok=True)
    getter = http_get or _default_http_get
    warnings: list[dict[str, Any]] = []
    raw_files: list[str] = []

    kalshi_raw_markets = _fetch_kalshi_daily_raw(
        max_games=max_games,
        timeout_seconds=timeout_seconds,
        getter=getter,
        kalshi_base_url=kalshi_base_url,
        raw_dir=kalshi_raw_dir,
        raw_files=raw_files,
        warnings=warnings,
        generated_at=generated,
    )
    kalshi_candidates = parse_kalshi_mlb_daily_markets(kalshi_raw_markets, date_label=date_label, max_games=max_games)

    polymarket_raw_events = _fetch_polymarket_daily_raw(
        date_label=date_label,
        parsed_date=parsed_date,
        kalshi_candidates=kalshi_candidates,
        max_games=max_games,
        timeout_seconds=timeout_seconds,
        getter=getter,
        gamma_base_url=polymarket_gamma_base_url,
        raw_dir=polymarket_raw_dir,
        raw_files=raw_files,
        warnings=warnings,
        generated_at=generated,
    )
    polymarket_candidates = parse_polymarket_mlb_daily_markets(
        polymarket_raw_events,
        date_label=date_label,
        max_games=max_games,
    )
    _fetch_polymarket_books(
        candidates=polymarket_candidates,
        timeout_seconds=timeout_seconds,
        getter=getter,
        clob_base_url=polymarket_clob_base_url,
        raw_dir=polymarket_raw_dir,
        raw_files=raw_files,
        warnings=warnings,
        generated_at=generated,
    )

    _fetch_kalshi_orderbooks(
        candidates=kalshi_candidates,
        timeout_seconds=timeout_seconds,
        getter=getter,
        kalshi_base_url=kalshi_base_url,
        raw_dir=kalshi_raw_dir,
        raw_files=raw_files,
        warnings=warnings,
        generated_at=generated,
    )

    polymarket_evidence = normalize_polymarket_daily_evidence(
        candidates=polymarket_candidates,
        date_label=date_label,
        generated_at=generated,
    )
    kalshi_evidence = normalize_kalshi_daily_evidence(
        candidates=kalshi_candidates,
        date_label=date_label,
        generated_at=generated,
    )
    summary = build_collection_summary(
        date_label=date_label,
        polymarket_evidence=polymarket_evidence,
        kalshi_evidence=kalshi_evidence,
        warnings=warnings,
        raw_files=raw_files,
        raw_root=raw_root,
    )

    polymarket_path = normalized_output_dir / f"sports_polymarket_mlb_daily_games_{date_label}_normalized_evidence.json"
    kalshi_path = normalized_output_dir / f"sports_kalshi_mlb_daily_games_{date_label}_normalized_evidence.json"
    summary_json_path = normalized_output_dir / f"sports_mlb_daily_games_{date_label}_collection_summary.json"
    summary_md_path = normalized_output_dir / f"sports_mlb_daily_games_{date_label}_collection_summary.md"
    _write_json(polymarket_path, polymarket_evidence)
    _write_json(kalshi_path, kalshi_evidence)
    _write_json(summary_json_path, summary)
    summary_md_path.write_text(render_collection_summary_markdown(summary), encoding="utf-8")
    summary["outputs"] = {
        "polymarket_normalized": str(polymarket_path),
        "kalshi_normalized": str(kalshi_path),
        "summary_json": str(summary_json_path),
        "summary_markdown": str(summary_md_path),
        "raw_root": str(raw_root),
    }
    _write_json(summary_json_path, summary)
    summary_md_path.write_text(render_collection_summary_markdown(summary), encoding="utf-8")
    return summary


def parse_polymarket_mlb_daily_markets(raw_payloads: list[Any], *, date_label: str, max_games: int) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for payload in raw_payloads:
        for event, market in _polymarket_event_market_pairs(payload):
            if not _is_polymarket_daily_game_market(event, market, date_label=date_label):
                continue
            teams = _polymarket_teams(event, market)
            if len(teams) != 2:
                continue
            away_code, home_code = _away_home_from_polymarket(event, market, teams, date_label=date_label)
            key = make_cross_platform_game_key(date_label, away_code, home_code)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "cross_platform_game_key": key,
                    "away_code": away_code,
                    "home_code": home_code,
                    "away_team": TEAM_CODE_TO_NAME.get(away_code, teams[0]),
                    "home_team": TEAM_CODE_TO_NAME.get(home_code, teams[1]),
                    "event": event,
                    "market": market,
                    "token_ids_by_team": _token_ids_by_team(teams, market),
                    "books_by_token_id": {},
                    "book_files_by_token_id": {},
                }
            )
            if len(candidates) >= max_games:
                return candidates
    return candidates


def parse_kalshi_mlb_daily_markets(raw_payloads: list[Any], *, date_label: str, max_games: int) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for payload in raw_payloads:
        for market in _kalshi_markets(payload):
            event_ticker = _string_or_none(market.get("event_ticker"))
            if not event_ticker:
                continue
            parsed = _parse_kalshi_event_ticker(event_ticker)
            if not parsed or parsed["game_date"] != date_label:
                continue
            grouped[event_ticker].append(market)
    candidates: list[dict[str, Any]] = []
    for event_ticker, markets in sorted(grouped.items()):
        parsed = _parse_kalshi_event_ticker(event_ticker)
        if not parsed:
            continue
        away_code = parsed["away_code"]
        home_code = parsed["home_code"]
        key = make_cross_platform_game_key(date_label, away_code, home_code)
        candidates.append(
            {
                "cross_platform_game_key": key,
                "away_code": away_code,
                "home_code": home_code,
                "away_team": TEAM_CODE_TO_NAME.get(away_code, away_code),
                "home_team": TEAM_CODE_TO_NAME.get(home_code, home_code),
                "event_ticker": event_ticker,
                "series_ticker": KALSHI_SERIES_TICKER,
                "scheduled_start_time": parsed["scheduled_start_time"],
                "timezone": "ET",
                "markets": markets,
                "orderbooks_by_ticker": {},
                "orderbook_files_by_ticker": {},
            }
        )
        if len(candidates) >= max_games:
            break
    return candidates


def convert_kalshi_orderbook(raw_orderbook: Any) -> dict[str, Any]:
    container = raw_orderbook.get("orderbook") if isinstance(raw_orderbook, dict) else None
    if container is None and isinstance(raw_orderbook, dict):
        container = raw_orderbook.get("orderbook_fp") or raw_orderbook
    if not isinstance(container, dict):
        return {
            "partial_book": True,
            "book_blockers": ["missing_orderbook_payload"],
            "yes_bid": None,
            "yes_bid_size": None,
            "yes_ask": None,
            "yes_ask_size": None,
            "no_bid": None,
            "no_bid_size": None,
            "no_ask": None,
            "no_ask_size": None,
        }
    yes_bids = _levels_from_pairs(container.get("yes_dollars") or container.get("yes") or container.get("yes_bids"))
    no_bids = _levels_from_pairs(container.get("no_dollars") or container.get("no") or container.get("no_bids"))
    best_yes = max(yes_bids, key=lambda item: item[0]) if yes_bids else None
    best_no = max(no_bids, key=lambda item: item[0]) if no_bids else None
    blockers: list[str] = []
    if best_yes is None:
        blockers.append("missing_yes_bid_side")
    if best_no is None:
        blockers.append("missing_no_bid_side")
    yes_bid = best_yes[0] if best_yes else None
    yes_bid_size = best_yes[1] if best_yes else None
    no_bid = best_no[0] if best_no else None
    no_bid_size = best_no[1] if best_no else None
    return {
        "partial_book": bool(blockers),
        "book_blockers": blockers,
        "yes_bid": yes_bid,
        "yes_bid_size": yes_bid_size,
        "yes_ask": round(1.0 - no_bid, 10) if no_bid is not None else None,
        "yes_ask_size": no_bid_size,
        "no_bid": no_bid,
        "no_bid_size": no_bid_size,
        "no_ask": round(1.0 - yes_bid, 10) if yes_bid is not None else None,
        "no_ask_size": yes_bid_size,
    }


def make_cross_platform_game_key(date_label: str, away_code: str, home_code: str) -> str:
    return f"MLB-{date_label}-{away_code}-{home_code}"


def normalize_polymarket_daily_evidence(
    *,
    candidates: list[dict[str, Any]],
    date_label: str,
    generated_at: datetime,
) -> dict[str, Any]:
    games: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        event = candidate["event"]
        market = candidate["market"]
        teams = _polymarket_teams(event, market)
        token_ids = candidate.get("token_ids_by_team") or {}
        outcomes = []
        for team in teams:
            token_id = token_ids.get(team)
            book = candidate.get("books_by_token_id", {}).get(token_id) if token_id else None
            metrics = _polymarket_book_metrics(book)
            outcomes.append(
                {
                    "team": team,
                    "bid": _as_price_string(metrics.get("bid")),
                    "ask": _as_price_string(metrics.get("ask")),
                    "bid_size": _as_price_string(metrics.get("bid_size")),
                    "ask_size": _as_price_string(metrics.get("ask_size")),
                    "bids_levels": metrics.get("bids_levels"),
                    "asks_levels": metrics.get("asks_levels"),
                }
            )
        rules_text = _polymarket_rules_text(event, market)
        suspended = _sentence_for_terms(rules_text, ("suspended", "shortened"))
        extras = _sentence_for_terms(rules_text, ("extra innings", "innings"))
        blockers = []
        if not suspended:
            blockers.append("missing_suspended_or_shortened_game_rules")
        if not extras:
            blockers.append("missing_extra_innings_rules")
        if any((outcome.get("bid") is None or outcome.get("ask") is None) for outcome in outcomes):
            blockers.append("partial_book")
        games.append(
            {
                "platform": "Polymarket",
                "game_number": index,
                "cross_platform_game_key": candidate["cross_platform_game_key"],
                "league": "MLB",
                "game_date": date_label,
                "teams": f"{candidate['away_team']} vs. {candidate['home_team']}",
                "home_team": candidate["home_team"],
                "away_team": candidate["away_team"],
                "scheduled_start_time": _scheduled_time_from_market(event, market),
                "timezone": "ET",
                "market_type": "game_winner",
                "ids": {
                    "market_id": _string_or_none(market.get("id")),
                    "condition_id": _string_or_none(market.get("conditionId") or market.get("condition_id")),
                    "event_id": _string_or_none(event.get("id")),
                    "slug": _string_or_none(market.get("slug") or event.get("slug")),
                    "token_ids": token_ids,
                },
                "rules_text": rules_text,
                "settlement_source": _settlement_source(event, market),
                "postponement_rules": _sentence_for_terms(rules_text, ("postponed",)) or "Not explicitly stated in rules text.",
                "cancellation_rules": _sentence_for_terms(rules_text, ("canceled", "cancelled", "make-up", "tie")) or "Not explicitly stated in rules text.",
                "suspended_or_shortened_game_rules": suspended or "Not explicitly stated in rules text.",
                "extra_innings_rules": extras or "Not explicitly stated in rules text.",
                "quotes": {
                    "market_status_at_fetch": _market_status(market),
                    "quote_timestamp_iso": generated_at.isoformat(),
                    "fetch_time_utc": generated_at.isoformat(),
                    "outcomes": outcomes,
                },
                "blockers_remaining": blockers,
                "diagnostic_only": True,
                "exact_ready": False,
                "paper_candidate": False,
            }
        )
    return _evidence_payload(platform="Polymarket", date_label=date_label, games=games)


def normalize_kalshi_daily_evidence(
    *,
    candidates: list[dict[str, Any]],
    date_label: str,
    generated_at: datetime,
) -> dict[str, Any]:
    games: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        market_tickers: dict[str, str] = {}
        outcomes = []
        blockers: list[str] = []
        for market in candidate.get("markets") or []:
            team = _kalshi_market_team(market, candidate)
            ticker = _string_or_none(market.get("ticker"))
            if team and ticker:
                market_tickers[team] = ticker
            raw_book = candidate.get("orderbooks_by_ticker", {}).get(ticker) if ticker else None
            metrics = convert_kalshi_orderbook(raw_book or {})
            if metrics["partial_book"]:
                blockers.append("partial_book")
            outcomes.append(
                {
                    "team": team,
                    "market_ticker": ticker,
                    "yes_bid": metrics.get("yes_bid"),
                    "yes_ask": metrics.get("yes_ask"),
                    "no_bid": metrics.get("no_bid"),
                    "no_ask": metrics.get("no_ask"),
                    "yes_bid_size": metrics.get("yes_bid_size"),
                    "yes_ask_size": metrics.get("yes_ask_size"),
                    "no_bid_size": metrics.get("no_bid_size"),
                    "no_ask_size": metrics.get("no_ask_size"),
                    "partial_book": metrics.get("partial_book"),
                    "book_blockers": metrics.get("book_blockers"),
                }
            )
        games.append(
            {
                "platform": "Kalshi",
                "game_number": index,
                "cross_platform_game_key": candidate["cross_platform_game_key"],
                "league": "MLB",
                "game_date": date_label,
                "teams": f"{candidate['away_team']} vs. {candidate['home_team']}",
                "home_team": candidate["home_team"],
                "away_team": candidate["away_team"],
                "scheduled_start_time": candidate.get("scheduled_start_time"),
                "timezone": candidate.get("timezone") or "ET",
                "market_type": "game_winner",
                "ids": {
                    "series_ticker": KALSHI_SERIES_TICKER,
                    "event_ticker": candidate.get("event_ticker"),
                    "market_tickers": market_tickers,
                },
                "rules_text": "Binary per-team Yes/No. Each team market resolves Yes if that team wins. Full game includes regulation and extra innings; shared public MLBGAME rules reference.",
                "settlement_source": "https://www.mlb.com/scores (primary); see shared MLBGAME rules reference",
                "postponement_rules": "If rescheduled and started within 48h of original start, market remains open and resolves on rescheduled result. If not started within 48h, resolves at last fair market price.",
                "cancellation_rules": "If cancelled and not started within 48h, resolves at last fair market price.",
                "suspended_or_shortened_game_rules": "If shortened/called and governing body declares game official, resolves on official final result. If suspended and not resumed within 48h, resolves at last fair market price.",
                "extra_innings_rules": "All extra innings and tie-breaking procedures included; resolves on final result after all extra innings concluded.",
                "full_rules_pdf": MLBGAME_RULES_URL,
                "quotes": {
                    "game_status_at_fetch": _kalshi_game_status(candidate),
                    "quote_timestamp_utc": generated_at.isoformat(),
                    "fetch_time_utc": generated_at.isoformat(),
                    "outcomes": outcomes,
                    "size_unit_note": "Sizes are explicit orderbook level sizes returned by the public Kalshi endpoint.",
                },
                "blockers_remaining": sorted(set(blockers)),
                "diagnostic_only": True,
                "exact_ready": False,
                "paper_candidate": False,
            }
        )
    return _evidence_payload(platform="Kalshi", date_label=date_label, games=games)


def build_collection_summary(
    *,
    date_label: str,
    polymarket_evidence: dict[str, Any],
    kalshi_evidence: dict[str, Any],
    warnings: list[dict[str, Any]],
    raw_files: list[str],
    raw_root: Path,
) -> dict[str, Any]:
    poly_games = polymarket_evidence.get("games") or []
    kalshi_games = kalshi_evidence.get("games") or []
    poly_keys = {game.get("cross_platform_game_key") for game in poly_games}
    kalshi_keys = {game.get("cross_platform_game_key") for game in kalshi_games}
    matched = sorted(key for key in poly_keys & kalshi_keys if key)
    unmatched_poly = sorted(key for key in poly_keys - kalshi_keys if key)
    unmatched_kalshi = sorted(key for key in kalshi_keys - poly_keys if key)
    blockers = Counter()
    for _key in unmatched_poly:
        blockers["missing_kalshi_peer"] += 1
    for _key in unmatched_kalshi:
        blockers["missing_polymarket_peer"] += 1
    for game in poly_games + kalshi_games:
        blockers.update(game.get("blockers_remaining") or [])
    summary_counts = {
        "polymarket_games": len(poly_games),
        "kalshi_games": len(kalshi_games),
        "matched_games": len(matched),
        "missing_kalshi_peer": len(unmatched_poly),
        "missing_polymarket_peer": len(unmatched_kalshi),
        "raw_files_written": len(raw_files),
        "warnings": len(warnings),
        "exact_ready_rows": 0,
        "paper_candidate_rows": 0,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "schema_kind": SCHEMA_KIND,
        "source": REPORT_SOURCE,
        "date": date_label,
        "diagnostic_only": True,
        "public_no_auth_only": True,
        "raw_root": str(raw_root),
        "games_loaded": {"polymarket": len(poly_games), "kalshi": len(kalshi_games)},
        "matched_games": matched,
        "unmatched": {
            "missing_kalshi_peer": unmatched_poly,
            "missing_polymarket_peer": unmatched_kalshi,
        },
        "summary_counts": summary_counts,
        "top_blockers": [{"blocker": key, "count": value} for key, value in blockers.most_common(15)],
        "warnings": warnings,
        "raw_files_written": raw_files,
        "safety": {
            "diagnostic_only": True,
            "public_no_auth_only": True,
            "authenticated_endpoints_used": False,
            "orders_or_cancellations": False,
            "account_balance_position_or_session_endpoints": False,
            "browser_automation": False,
            "candidate_pair_creation": False,
            "exact_ready": False,
            "paper_candidate_emitted": False,
        },
    }


def render_collection_summary_markdown(summary: dict[str, Any]) -> str:
    counts = summary.get("summary_counts") or {}
    lines = [
        "# MLB Daily Game Evidence Collection",
        "",
        "Public read-only evidence collector. It writes raw snapshots and normalized evidence only; it does not create candidates or affect evaluator gates.",
        "",
        "## Summary",
        "",
        f"- date: `{_md(summary.get('date'))}`",
        f"- polymarket_games: `{counts.get('polymarket_games', 0)}`",
        f"- kalshi_games: `{counts.get('kalshi_games', 0)}`",
        f"- matched_games: `{counts.get('matched_games', 0)}`",
        f"- missing_kalshi_peer: `{counts.get('missing_kalshi_peer', 0)}`",
        f"- missing_polymarket_peer: `{counts.get('missing_polymarket_peer', 0)}`",
        f"- raw_files_written: `{counts.get('raw_files_written', 0)}`",
        f"- exact_ready_rows: `0`",
        f"- paper_candidate_rows: `0`",
        "",
        "## Matched Games",
        "",
    ]
    matched = summary.get("matched_games") or []
    if matched:
        for key in matched:
            lines.append(f"- `{_md(key)}`")
    else:
        lines.append("_None._")
    lines.extend(["", "## Missing Peers", ""])
    unmatched = summary.get("unmatched") or {}
    for label in ("missing_kalshi_peer", "missing_polymarket_peer"):
        values = unmatched.get(label) or []
        lines.append(f"- {label}: `{', '.join(values) or 'none'}`")
    lines.extend(["", "## Top Blockers", "", "| Blocker | Count |", "|---|---:|"])
    blockers = summary.get("top_blockers") or []
    if blockers:
        for item in blockers:
            lines.append(f"| {_md(item.get('blocker'))} | {_md(item.get('count'))} |")
    else:
        lines.append("| none | 0 |")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- diagnostic_only: `true`",
            "- public_no_auth_only: `true`",
            "- authenticated_endpoints_used: `false`",
            "- orders_or_cancellations: `false`",
            "- account_balance_position_or_session_endpoints: `false`",
            "- browser_automation: `false`",
            "- exact_ready: `false`",
            "- paper_candidate_emitted: `false`",
        ]
    )
    return "\n".join(lines) + "\n"


def _polymarket_slug_seeds_from_kalshi(candidates: list[dict[str, Any]], *, date_label: str) -> list[str]:
    slugs: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        away = _string_or_none(candidate.get("away_code")) or team_code(candidate.get("away_team"))
        home = _string_or_none(candidate.get("home_code")) or team_code(candidate.get("home_team"))
        if not away or not home:
            continue
        slug = f"mlb-{away.lower()}-{home.lower()}-{date_label}"
        if slug in seen:
            continue
        seen.add(slug)
        slugs.append(slug)
    return slugs


def _fetch_polymarket_daily_raw(
    *,
    date_label: str,
    parsed_date: date_cls,
    kalshi_candidates: list[dict[str, Any]],
    max_games: int,
    timeout_seconds: float,
    getter: HttpGet,
    gamma_base_url: str,
    raw_dir: Path,
    raw_files: list[str],
    warnings: list[dict[str, Any]],
    generated_at: datetime,
) -> list[Any]:
    terms = [
        f"MLB {date_label}",
        f"MLB {_human_date(parsed_date)}",
        f"baseball {_human_date(parsed_date)}",
    ]
    payloads: list[Any] = []
    limit = max(max_games * 4, 50)
    for index, term in enumerate(terms):
        for path in ("/events", "/markets"):
            url = f"{gamma_base_url.rstrip('/')}{path}?{urlencode({'active': 'true', 'closed': 'false', 'limit': str(limit), 'search': term})}"
            try:
                payload = getter(url, timeout_seconds)
            except Exception as exc:  # pragma: no cover - exact network exception text varies
                warnings.append({"venue": "polymarket", "url": url, "reason": "public_request_failed", "error": f"{type(exc).__name__}: {exc}"})
                continue
            filename = raw_dir / f"gamma_{path.strip('/')}_search_{index}.json"
            _write_json(filename, {"source": "polymarket_gamma_raw", "url": url, "captured_at": generated_at.isoformat(), "raw_response": payload})
            raw_files.append(str(filename))
            payloads.append(payload)
    for index, slug in enumerate(_polymarket_slug_seeds_from_kalshi(kalshi_candidates, date_label=date_label)):
        url = f"{gamma_base_url.rstrip('/')}/markets?{urlencode({'active': 'true', 'closed': 'false', 'limit': '5', 'slug': slug})}"
        try:
            payload = getter(url, timeout_seconds)
        except Exception as exc:  # pragma: no cover - exact network exception text varies
            warnings.append({"venue": "polymarket", "url": url, "reason": "public_slug_request_failed", "error": f"{type(exc).__name__}: {exc}"})
            continue
        filename = raw_dir / f"gamma_markets_slug_{index}_{_safe_slug(slug)}.json"
        _write_json(filename, {"source": "polymarket_gamma_raw", "url": url, "captured_at": generated_at.isoformat(), "raw_response": payload})
        raw_files.append(str(filename))
        payloads.append(payload)
    return payloads


def _fetch_polymarket_books(
    *,
    candidates: list[dict[str, Any]],
    timeout_seconds: float,
    getter: HttpGet,
    clob_base_url: str,
    raw_dir: Path,
    raw_files: list[str],
    warnings: list[dict[str, Any]],
    generated_at: datetime,
) -> None:
    for candidate in candidates:
        for token_id in (candidate.get("token_ids_by_team") or {}).values():
            if not token_id:
                continue
            url = f"{clob_base_url.rstrip('/')}/book?{urlencode({'token_id': token_id})}"
            try:
                payload = getter(url, timeout_seconds)
            except Exception as exc:  # pragma: no cover
                warnings.append({"venue": "polymarket", "url": url, "token_id": token_id, "reason": "public_book_request_failed", "error": f"{type(exc).__name__}: {exc}"})
                continue
            filename = raw_dir / f"book_{_safe_slug(str(token_id))}.json"
            _write_json(filename, {"source": "polymarket_clob_book_raw", "url": url, "token_id": token_id, "captured_at": generated_at.isoformat(), "raw_response": payload})
            raw_files.append(str(filename))
            candidate.setdefault("books_by_token_id", {})[token_id] = payload
            candidate.setdefault("book_files_by_token_id", {})[token_id] = str(filename)


def _fetch_kalshi_daily_raw(
    *,
    max_games: int,
    timeout_seconds: float,
    getter: HttpGet,
    kalshi_base_url: str,
    raw_dir: Path,
    raw_files: list[str],
    warnings: list[dict[str, Any]],
    generated_at: datetime,
) -> list[Any]:
    limit = max(max_games * 4, 100)
    url = f"{kalshi_base_url.rstrip('/')}/markets?{urlencode({'status': 'open', 'limit': str(limit), 'series_ticker': KALSHI_SERIES_TICKER})}"
    try:
        payload = getter(url, timeout_seconds)
    except Exception as exc:  # pragma: no cover
        warnings.append({"venue": "kalshi", "url": url, "reason": "public_request_failed", "error": f"{type(exc).__name__}: {exc}"})
        return []
    filename = raw_dir / "markets_kxmlbgame.json"
    _write_json(filename, {"source": "kalshi_markets_raw", "url": url, "captured_at": generated_at.isoformat(), "raw_response": payload})
    raw_files.append(str(filename))
    return [payload]


def _fetch_kalshi_orderbooks(
    *,
    candidates: list[dict[str, Any]],
    timeout_seconds: float,
    getter: HttpGet,
    kalshi_base_url: str,
    raw_dir: Path,
    raw_files: list[str],
    warnings: list[dict[str, Any]],
    generated_at: datetime,
) -> None:
    for candidate in candidates:
        for market in candidate.get("markets") or []:
            ticker = _string_or_none(market.get("ticker"))
            if not ticker:
                continue
            url = f"{kalshi_base_url.rstrip('/')}/markets/{quote(ticker, safe='')}/orderbook"
            try:
                payload = getter(url, timeout_seconds)
            except Exception as exc:  # pragma: no cover
                warnings.append({"venue": "kalshi", "url": url, "ticker": ticker, "reason": "public_orderbook_request_failed", "error": f"{type(exc).__name__}: {exc}"})
                continue
            filename = raw_dir / f"orderbook_{_safe_slug(ticker)}.json"
            _write_json(filename, {"source": "kalshi_orderbook_raw", "url": url, "ticker": ticker, "captured_at": generated_at.isoformat(), "raw_response": payload})
            raw_files.append(str(filename))
            candidate.setdefault("orderbooks_by_ticker", {})[ticker] = payload
            candidate.setdefault("orderbook_files_by_ticker", {})[ticker] = str(filename)


def _is_polymarket_daily_game_market(event: dict[str, Any], market: dict[str, Any], *, date_label: str) -> bool:
    text = _combined_text(event, market)
    if not _MLB_RE.search(text):
        return False
    if not any(variant.lower() in text.lower() for variant in _date_variants(date_label)):
        return False
    if _EXCLUDE_POLYMARKET_RE.search(text):
        return False
    outcomes = _polymarket_teams(event, market)
    if len(outcomes) != 2:
        return False
    if {outcome.lower() for outcome in outcomes} <= {"yes", "no"}:
        return False
    return True


def _polymarket_event_market_pairs(payload: Any) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    records = _records_from_response(payload)
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        markets = record.get("markets")
        if isinstance(markets, list):
            for market in markets:
                if isinstance(market, dict):
                    pairs.append((record, market))
        elif "question" in record or "conditionId" in record or "outcomes" in record:
            event = record.get("event") if isinstance(record.get("event"), dict) else {}
            pairs.append((event, record))
    return pairs


def _records_from_response(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        raw = payload.get("raw_response") if isinstance(payload.get("raw_response"), (list, dict)) else payload
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict):
            for key in ("events", "markets", "data", "results"):
                if isinstance(raw.get(key), list):
                    return raw[key]
    return []


def _kalshi_markets(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("raw_response"), dict):
        payload = payload["raw_response"]
    if isinstance(payload, dict) and isinstance(payload.get("markets"), list):
        return [row for row in payload["markets"] if isinstance(row, dict)]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return []


def _away_home_from_polymarket(event: dict[str, Any], market: dict[str, Any], teams: list[str], *, date_label: str) -> tuple[str, str]:
    slug_text = " ".join(str(value or "") for value in (market.get("slug"), event.get("slug")))
    parsed = _away_home_from_slug(slug_text, date_label=date_label)
    if parsed:
        return parsed
    home_code = team_code(market.get("home_team") or event.get("home_team"))
    away_code = team_code(market.get("away_team") or event.get("away_team"))
    if home_code and away_code:
        return away_code, home_code
    codes = [team_code(team) for team in teams]
    if len(codes) == 2 and codes[0] and codes[1]:
        return codes[0], codes[1]
    return "UNK", "UNK"


def _away_home_from_slug(text: str, *, date_label: str) -> tuple[str, str] | None:
    lower = text.lower().replace("_", "-")
    date_variants = [date_label, date_label.replace("-", "")]
    parts = [part for part in re.split(r"[^a-z0-9]+", lower) if part]
    for idx, part in enumerate(parts):
        if part != "mlb":
            continue
        if idx + 2 >= len(parts):
            continue
        away = team_code(parts[idx + 1])
        home = team_code(parts[idx + 2])
        if away and home and (any(variant in lower for variant in date_variants) or len(parts) >= idx + 5):
            return away, home
    return None


def _parse_kalshi_event_ticker(event_ticker: str) -> dict[str, Any] | None:
    match = _KALSHI_EVENT_RE.search(event_ticker)
    if not match:
        return None
    month = _MONTHS.get(match.group("mon"))
    if not month:
        return None
    year = 2000 + int(match.group("yy"))
    day = int(match.group("dd"))
    hour = int(match.group("hh"))
    minute = int(match.group("mm"))
    split = _split_team_code_pair(match.group("teams"))
    if not split:
        return None
    away_code, home_code = split
    return {
        "game_date": f"{year:04d}-{month:02d}-{day:02d}",
        "scheduled_start_time": f"{hour:02d}:{minute:02d}",
        "away_code": away_code,
        "home_code": home_code,
    }


def _split_team_code_pair(value: str) -> tuple[str, str] | None:
    codes = set(TEAM_CODE_TO_NAME)
    for index in range(2, len(value) - 1):
        left = value[:index]
        right = value[index:]
        if left in codes and right in codes:
            return left, right
    return None


def _kalshi_market_team(market: dict[str, Any], candidate: dict[str, Any]) -> str | None:
    ticker = _string_or_none(market.get("ticker"))
    if ticker:
        suffix = ticker.rsplit("-", 1)[-1]
        code = team_code(suffix)
        if code:
            return TEAM_CODE_TO_NAME.get(code, code)
    text = " ".join(str(market.get(key) or "") for key in ("title", "subtitle", "yes_sub_title"))
    for code, name in TEAM_CODE_TO_NAME.items():
        if name.lower() in text.lower() or re.search(rf"\b{re.escape(code)}\b", text, re.IGNORECASE):
            return name
    return None


def _kalshi_game_status(candidate: dict[str, Any]) -> str:
    states = sorted({_string_or_none(market.get("status") or market.get("state")) or "open" for market in candidate.get("markets") or []})
    return ",".join(states) or "open"


def _polymarket_teams(event: dict[str, Any], market: dict[str, Any]) -> list[str]:
    outcomes = _maybe_json_array(market.get("outcomes") or market.get("outcome_names") or market.get("outcomeNames"))
    out: list[str] = []
    for outcome in outcomes:
        text = _string_or_none(outcome)
        if text:
            out.append(text)
    return out


def _token_ids_by_team(teams: list[str], market: dict[str, Any]) -> dict[str, str]:
    tokens = _maybe_json_array(
        market.get("clobTokenIds")
        or market.get("clob_token_ids")
        or market.get("tokenIds")
        or market.get("token_ids")
    )
    result: dict[str, str] = {}
    for index, team in enumerate(teams):
        if index < len(tokens) and tokens[index] is not None:
            result[team] = str(tokens[index])
    return result


def _polymarket_rules_text(event: dict[str, Any], market: dict[str, Any]) -> str:
    values = [
        market.get("rules"),
        market.get("resolutionSource"),
        market.get("description"),
        market.get("question"),
        event.get("rules"),
        event.get("description"),
    ]
    return " ".join(str(value).strip() for value in values if value)


def _settlement_source(event: dict[str, Any], market: dict[str, Any]) -> str | None:
    for value in (market.get("resolutionSource"), market.get("resolution_source"), event.get("resolutionSource")):
        text = _string_or_none(value)
        if text:
            return text
    text = _polymarket_rules_text(event, market)
    if "mlb.com" in text.lower():
        return "https://www.mlb.com/"
    return None


def _scheduled_time_from_market(event: dict[str, Any], market: dict[str, Any]) -> str | None:
    for key in ("startTime", "start_time", "game_start_time", "scheduled_start_time"):
        text = _string_or_none(market.get(key) or event.get(key))
        if text:
            return text
    return None


def _market_status(market: dict[str, Any]) -> str:
    if market.get("closed") is True:
        return "Closed"
    if market.get("active") is True:
        return "Open"
    return "Unknown"


def _sentence_for_terms(text: str, terms: tuple[str, ...]) -> str | None:
    if not text:
        return None
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for sentence in sentences:
        lowered = sentence.lower()
        if any(term in lowered for term in terms):
            return sentence.strip()
    return None


def _polymarket_book_metrics(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"bid": None, "ask": None, "bid_size": None, "ask_size": None, "bids_levels": 0, "asks_levels": 0}
    bids = _levels_from_dicts(raw.get("bids"))
    asks = _levels_from_dicts(raw.get("asks"))
    best_bid = max(bids, key=lambda item: item[0]) if bids else None
    best_ask = min(asks, key=lambda item: item[0]) if asks else None
    return {
        "bid": best_bid[0] if best_bid else None,
        "bid_size": best_bid[1] if best_bid else None,
        "ask": best_ask[0] if best_ask else None,
        "ask_size": best_ask[1] if best_ask else None,
        "bids_levels": len(bids),
        "asks_levels": len(asks),
    }


def _evidence_payload(*, platform: str, date_label: str, games: list[dict[str, Any]]) -> dict[str, Any]:
    blockers = Counter()
    for game in games:
        blockers.update(game.get("blockers_remaining") or [])
    return {
        "platform": platform,
        "league": "MLB",
        "date_label": date_label,
        "diagnostic_only": True,
        "paper_candidate_emitted": False,
        "gates_cleared": False,
        "exact_ready": False,
        "games": games,
        "summary_counts": {
            "games": len(games),
            "games_with_blockers": sum(1 for game in games if game.get("blockers_remaining")),
            "top_blockers": [{"blocker": key, "count": value} for key, value in blockers.most_common(10)],
            "exact_ready_rows": 0,
            "paper_candidate_rows": 0,
        },
    }


def team_code(value: Any) -> str | None:
    text = _string_or_none(value)
    if not text:
        return None
    if text.upper() in TEAM_CODE_TO_NAME:
        return text.upper()
    normalized = _normalize_team_text(text)
    return _TEAM_ALIASES.get(normalized)


def _normalize_team_text(value: str) -> str:
    lowered = value.strip().lower().replace("&", "and").replace(".", "")
    lowered = _TOKEN_SPLIT_RE.sub(" ", lowered)
    return " ".join(lowered.split())


def _levels_from_pairs(value: Any) -> list[tuple[float, float]]:
    levels: list[tuple[float, float]] = []
    if not isinstance(value, list):
        return levels
    for level in value:
        if not isinstance(level, (list, tuple)) or len(level) < 2:
            continue
        price = _price_or_none(level[0])
        size = _float_or_none(level[1])
        if price is not None and size is not None:
            levels.append((price, size))
    return levels


def _levels_from_dicts(value: Any) -> list[tuple[float, float]]:
    levels: list[tuple[float, float]] = []
    if not isinstance(value, list):
        return levels
    for level in value:
        if not isinstance(level, dict):
            continue
        price = _price_or_none(level.get("price"))
        size = _float_or_none(level.get("size"))
        if price is not None and size is not None:
            levels.append((price, size))
    return levels


def _price_or_none(value: Any) -> float | None:
    number = _float_or_none(value)
    if number is None:
        return None
    if number > 1.0:
        number = number / 100.0
    return round(number, 10)


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _maybe_json_array(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return [part.strip() for part in stripped.split(",") if part.strip()]
        return parsed if isinstance(parsed, list) else []
    return []


def _combined_text(event: dict[str, Any], market: dict[str, Any]) -> str:
    parts: list[str] = []
    for source in (event, market):
        for key in ("title", "question", "slug", "description", "rules", "resolutionSource"):
            value = source.get(key)
            if value:
                parts.append(str(value))
    parts.extend(str(value) for value in _maybe_json_array(market.get("outcomes")) if value)
    return " ".join(parts)


def _date_variants(date_label: str) -> list[str]:
    parsed = _parse_date(date_label)
    month_name = parsed.strftime("%B")
    month_short = parsed.strftime("%b")
    day = parsed.day
    return [
        date_label,
        date_label.replace("-", "/"),
        f"{month_name} {day}, {parsed.year}",
        f"{month_name} {day}",
        f"{month_short} {day}, {parsed.year}",
        f"{month_short} {day}",
    ]


def _human_date(parsed: date_cls) -> str:
    return f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}"


def _parse_date(value: str) -> date_cls:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _safe_slug(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    return safe[:120] or "item"


def _as_price_string(value: Any) -> str | None:
    number = _float_or_none(value)
    if number is None:
        return None
    return f"{number:.10f}".rstrip("0").rstrip(".")


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _md(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _default_http_get(url: str, timeout_seconds: float) -> Any:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": DEFAULT_USER_AGENT}, method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        raise RuntimeError(f"public read-only endpoint returned HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"public read-only request failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("public read-only request timed out") from exc
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("public read-only endpoint returned invalid JSON") from exc
