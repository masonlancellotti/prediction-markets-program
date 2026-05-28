from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


SCHEMA_VERSION = 1
SCHEMA_KIND = "sports_mlb_world_series_evidence_collection_v1"
REPORT_SOURCE = "sports_mlb_world_series_evidence_collection_v1"

POLYMARKET_GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
POLYMARKET_CLOB_BASE_URL = "https://clob.polymarket.com"
KALSHI_PUBLIC_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
DEFAULT_USER_AGENT = "relative-value-scanner/0.1 public-read-only"

KALSHI_SERIES_TICKER = "KXMLB"
KALSHI_RULES_URL = "https://kalshi-public-docs.s3.amazonaws.com/contract_terms/TITLE.pdf"

HttpGet = Callable[[str, float], Any]

TEAM_CODE_TO_NAME = {
    "ARI": "Arizona Diamondbacks",
    "AZ": "Arizona Diamondbacks",
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
    "OAK": "Athletics",
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
    "WAS": "Washington Nationals",
}

TEAM_CODE_CANONICAL = {
    "AZ": "ARI",
    "OAK": "ATH",
    "WAS": "WSH",
}

_TEAM_ALIASES = {
    "arizona diamondbacks": "ARI",
    "arizona": "ARI",
    "diamondbacks": "ARI",
    "ari": "ARI",
    "az": "ARI",
    "atlanta braves": "ATL",
    "atlanta": "ATL",
    "braves": "ATL",
    "atl": "ATL",
    "baltimore orioles": "BAL",
    "baltimore": "BAL",
    "orioles": "BAL",
    "bal": "BAL",
    "boston red sox": "BOS",
    "boston": "BOS",
    "red sox": "BOS",
    "bos": "BOS",
    "chicago cubs": "CHC",
    "cubs": "CHC",
    "chc": "CHC",
    "chicago white sox": "CWS",
    "white sox": "CWS",
    "chicago w": "CWS",
    "cws": "CWS",
    "chw": "CWS",
    "cincinnati reds": "CIN",
    "cincinnati": "CIN",
    "reds": "CIN",
    "cin": "CIN",
    "cleveland guardians": "CLE",
    "cleveland": "CLE",
    "guardians": "CLE",
    "cle": "CLE",
    "colorado rockies": "COL",
    "colorado": "COL",
    "rockies": "COL",
    "col": "COL",
    "detroit tigers": "DET",
    "detroit": "DET",
    "tigers": "DET",
    "det": "DET",
    "houston astros": "HOU",
    "houston": "HOU",
    "astros": "HOU",
    "hou": "HOU",
    "kansas city royals": "KC",
    "kansas city": "KC",
    "royals": "KC",
    "kc": "KC",
    "kcr": "KC",
    "los angeles angels": "LAA",
    "los angeles a": "LAA",
    "la angels": "LAA",
    "angels": "LAA",
    "laa": "LAA",
    "los angeles dodgers": "LAD",
    "los angeles d": "LAD",
    "la dodgers": "LAD",
    "dodgers": "LAD",
    "lad": "LAD",
    "miami marlins": "MIA",
    "miami": "MIA",
    "marlins": "MIA",
    "mia": "MIA",
    "milwaukee brewers": "MIL",
    "milwaukee": "MIL",
    "brewers": "MIL",
    "mil": "MIL",
    "minnesota twins": "MIN",
    "minnesota": "MIN",
    "twins": "MIN",
    "min": "MIN",
    "new york mets": "NYM",
    "mets": "NYM",
    "nym": "NYM",
    "new york yankees": "NYY",
    "yankees": "NYY",
    "nyy": "NYY",
    "athletics": "ATH",
    "oakland athletics": "ATH",
    "ath": "ATH",
    "oak": "ATH",
    "philadelphia phillies": "PHI",
    "philadelphia": "PHI",
    "phillies": "PHI",
    "phi": "PHI",
    "pittsburgh pirates": "PIT",
    "pittsburgh": "PIT",
    "pirates": "PIT",
    "pit": "PIT",
    "san diego padres": "SD",
    "san diego": "SD",
    "padres": "SD",
    "sd": "SD",
    "sdp": "SD",
    "seattle mariners": "SEA",
    "seattle": "SEA",
    "mariners": "SEA",
    "sea": "SEA",
    "san francisco giants": "SF",
    "san francisco": "SF",
    "giants": "SF",
    "sf": "SF",
    "sfg": "SF",
    "st louis cardinals": "STL",
    "st. louis cardinals": "STL",
    "st louis": "STL",
    "cardinals": "STL",
    "stl": "STL",
    "tampa bay rays": "TB",
    "tampa bay": "TB",
    "rays": "TB",
    "tb": "TB",
    "tbr": "TB",
    "texas rangers": "TEX",
    "texas": "TEX",
    "rangers": "TEX",
    "tex": "TEX",
    "toronto blue jays": "TOR",
    "toronto": "TOR",
    "blue jays": "TOR",
    "tor": "TOR",
    "washington nationals": "WSH",
    "washington": "WSH",
    "nationals": "WSH",
    "wsh": "WSH",
    "was": "WSH",
}

_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")
_WORLD_SERIES_QUESTION_RE = re.compile(
    r"will\s+(?:the\s+)?(?P<team>.+?)\s+win\s+(?:the\s+)?(?P<season>\d{4})\s+world\s+series",
    re.IGNORECASE,
)


def write_mlb_world_series_evidence_files(
    *,
    season: int | str,
    output_dir: Path,
    normalized_output_dir: Path,
    timeout_seconds: float = 10.0,
    generated_at: datetime | None = None,
    http_get: HttpGet | None = None,
    polymarket_gamma_base_url: str = POLYMARKET_GAMMA_BASE_URL,
    polymarket_clob_base_url: str = POLYMARKET_CLOB_BASE_URL,
    kalshi_base_url: str = KALSHI_PUBLIC_BASE_URL,
) -> dict[str, Any]:
    return build_mlb_world_series_evidence_collection(
        season=season,
        output_dir=output_dir,
        normalized_output_dir=normalized_output_dir,
        timeout_seconds=timeout_seconds,
        generated_at=generated_at,
        http_get=http_get,
        polymarket_gamma_base_url=polymarket_gamma_base_url,
        polymarket_clob_base_url=polymarket_clob_base_url,
        kalshi_base_url=kalshi_base_url,
    )


def build_mlb_world_series_evidence_collection(
    *,
    season: int | str,
    output_dir: Path,
    normalized_output_dir: Path,
    timeout_seconds: float = 10.0,
    generated_at: datetime | None = None,
    http_get: HttpGet | None = None,
    polymarket_gamma_base_url: str = POLYMARKET_GAMMA_BASE_URL,
    polymarket_clob_base_url: str = POLYMARKET_CLOB_BASE_URL,
    kalshi_base_url: str = KALSHI_PUBLIC_BASE_URL,
) -> dict[str, Any]:
    season_label = _season_label(season)
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    timestamp = generated.strftime("%Y%m%d_%H%M%SZ")
    raw_root = output_dir / timestamp
    kalshi_raw_dir = raw_root / "kalshi"
    polymarket_raw_dir = raw_root / "polymarket"
    normalized_output_dir.mkdir(parents=True, exist_ok=True)
    kalshi_raw_dir.mkdir(parents=True, exist_ok=True)
    polymarket_raw_dir.mkdir(parents=True, exist_ok=True)

    getter = http_get or _default_http_get
    warnings: list[dict[str, Any]] = []
    raw_files: list[str] = []

    kalshi_raw = _fetch_kalshi_world_series_raw(
        season=season_label,
        timeout_seconds=timeout_seconds,
        getter=getter,
        kalshi_base_url=kalshi_base_url,
        raw_dir=kalshi_raw_dir,
        raw_files=raw_files,
        warnings=warnings,
        generated_at=generated,
    )
    kalshi_outcomes = parse_kalshi_world_series_markets(kalshi_raw, season=season_label)
    _fetch_kalshi_orderbooks(
        outcomes=kalshi_outcomes,
        timeout_seconds=timeout_seconds,
        getter=getter,
        kalshi_base_url=kalshi_base_url,
        raw_dir=kalshi_raw_dir,
        raw_files=raw_files,
        warnings=warnings,
        generated_at=generated,
    )

    polymarket_raw = _fetch_polymarket_world_series_raw(
        season=season_label,
        timeout_seconds=timeout_seconds,
        getter=getter,
        gamma_base_url=polymarket_gamma_base_url,
        raw_dir=polymarket_raw_dir,
        raw_files=raw_files,
        warnings=warnings,
        generated_at=generated,
    )
    polymarket_event = parse_polymarket_world_series_event(polymarket_raw, season=season_label)
    _fetch_polymarket_books(
        event=polymarket_event,
        timeout_seconds=timeout_seconds,
        getter=getter,
        clob_base_url=polymarket_clob_base_url,
        raw_dir=polymarket_raw_dir,
        raw_files=raw_files,
        warnings=warnings,
        generated_at=generated,
    )

    kalshi_evidence = normalize_kalshi_world_series_evidence(
        outcomes=kalshi_outcomes,
        season=season_label,
        generated_at=generated,
        raw_root=raw_root,
    )
    polymarket_evidence = normalize_polymarket_world_series_evidence(
        event=polymarket_event,
        season=season_label,
        generated_at=generated,
        raw_root=raw_root,
    )
    summary = build_collection_summary(
        season=season_label,
        kalshi_evidence=kalshi_evidence,
        polymarket_evidence=polymarket_evidence,
        warnings=warnings,
        raw_files=raw_files,
        raw_root=raw_root,
    )

    kalshi_path = normalized_output_dir / f"sports_kalshi_mlb_world_series_{season_label}_normalized_evidence.json"
    polymarket_path = normalized_output_dir / f"sports_polymarket_mlb_world_series_{season_label}_normalized_evidence.json"
    summary_json_path = normalized_output_dir / f"sports_mlb_world_series_{season_label}_collection_summary.json"
    summary_md_path = normalized_output_dir / f"sports_mlb_world_series_{season_label}_collection_summary.md"
    _write_json(kalshi_path, kalshi_evidence)
    _write_json(polymarket_path, polymarket_evidence)
    summary["outputs"] = {
        "kalshi_normalized": str(kalshi_path),
        "polymarket_normalized": str(polymarket_path),
        "summary_json": str(summary_json_path),
        "summary_markdown": str(summary_md_path),
        "raw_root": str(raw_root),
    }
    _write_json(summary_json_path, summary)
    summary_md_path.write_text(render_collection_summary_markdown(summary), encoding="utf-8")
    return summary


def parse_kalshi_world_series_markets(raw_payloads: list[Any], *, season: int | str) -> list[dict[str, Any]]:
    season_label = _season_label(season)
    event_ticker = f"{KALSHI_SERIES_TICKER}-{season_label[-2:]}"
    outcomes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for payload in raw_payloads:
        for market in _kalshi_markets(payload):
            ticker = _string_or_none(market.get("ticker"))
            market_event = _string_or_none(market.get("event_ticker")) or (ticker.rsplit("-", 1)[0] if ticker else None)
            if market_event != event_ticker or not ticker or ticker in seen:
                continue
            team_code_raw = ticker.rsplit("-", 1)[-1]
            code = team_code(team_code_raw)
            if not code:
                code = _team_code_from_text(market.get("yes_sub_title") or market.get("title"))
            if not code:
                continue
            seen.add(ticker)
            outcomes.append(
                {
                    "team_code": code,
                    "team_name": TEAM_CODE_TO_NAME.get(code, code),
                    "team_aliases": _team_aliases_for_code(code),
                    "market_ticker": ticker,
                    "event_ticker": event_ticker,
                    "series_ticker": KALSHI_SERIES_TICKER,
                    "market": market,
                    "orderbook": None,
                    "orderbook_file": None,
                }
            )
    outcomes.sort(key=lambda item: (_price_sort_key(item.get("market")), item["team_name"]))
    for index, outcome in enumerate(outcomes, start=1):
        outcome["rank_or_order"] = index
    return outcomes


def parse_polymarket_world_series_event(raw_payloads: list[Any], *, season: int | str) -> dict[str, Any]:
    season_label = _season_label(season)
    target_slug = f"mlb-world-series-champion-{season_label}"
    best_event: dict[str, Any] | None = None
    candidate_markets: list[dict[str, Any]] = []
    for record in _records_from_payloads(raw_payloads):
        if not isinstance(record, dict):
            continue
        markets = [m for m in record.get("markets") or [] if isinstance(m, dict)]
        slug = _string_or_none(record.get("slug"))
        title = _string_or_none(record.get("title") or record.get("question"))
        if markets and (slug == target_slug or _looks_like_world_series_text(title, season_label)):
            best_event = record
            candidate_markets = markets
            break
        if _looks_like_polymarket_team_market(record, season_label):
            candidate_markets.append(record)
            if best_event is None:
                best_event = record.get("event") if isinstance(record.get("event"), dict) else {}
    if best_event is None:
        best_event = {}

    outcomes: list[dict[str, Any]] = []
    other_outcome: dict[str, Any] | None = None
    seen_market_ids: set[str] = set()
    for market in candidate_markets:
        market_id = _string_or_none(market.get("id") or market.get("market_id"))
        if market_id and market_id in seen_market_ids:
            continue
        if market_id:
            seen_market_ids.add(market_id)
        other = _is_other_polymarket_market(market)
        code = _polymarket_market_team_code(market, season_label)
        tokens = _token_ids_from_market(market)
        entry = {
            "team_code": code,
            "team_name": TEAM_CODE_TO_NAME.get(code, "Other") if code else "Other",
            "team_aliases": _team_aliases_for_code(code) if code else ["Other"],
            "market": market,
            "market_id": market_id,
            "condition_id": _string_or_none(market.get("conditionId") or market.get("condition_id")),
            "token_id_yes": tokens[0] if len(tokens) > 0 else None,
            "token_id_no": tokens[1] if len(tokens) > 1 else None,
            "yes_book": None,
            "no_book": None,
            "yes_book_file": None,
            "no_book_file": None,
        }
        if other or not code:
            other_outcome = entry
        else:
            outcomes.append(entry)
    outcomes.sort(key=lambda item: (_price_sort_key(item.get("market")), item["team_name"]))
    for index, outcome in enumerate(outcomes, start=1):
        outcome["rank_or_order"] = index
    return {
        "event": best_event,
        "event_slug": _string_or_none(best_event.get("slug")) or target_slug,
        "parent_event_id": _string_or_none(best_event.get("id")),
        "outcomes": outcomes,
        "other_outcome": other_outcome,
    }


def convert_kalshi_orderbook(raw_orderbook: Any) -> dict[str, Any]:
    container = raw_orderbook.get("orderbook") if isinstance(raw_orderbook, dict) else None
    if container is None and isinstance(raw_orderbook, dict):
        container = raw_orderbook.get("orderbook_fp") or raw_orderbook
    if not isinstance(container, dict):
        return _empty_kalshi_book(["missing_orderbook_payload"])
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
        "yes_book_levels": len(yes_bids),
        "no_book_levels": len(no_bids),
        "depth_status": "partial_book" if blockers else "full_clob",
    }


def parse_polymarket_clob_book(raw_book: Any) -> dict[str, Any]:
    if isinstance(raw_book, dict) and isinstance(raw_book.get("raw_response"), dict):
        raw_book = raw_book["raw_response"]
    if not isinstance(raw_book, dict):
        return _empty_polymarket_book(["missing_book_payload"])
    bids = _levels_from_dicts(raw_book.get("bids"))
    asks = _levels_from_dicts(raw_book.get("asks"))
    best_bid = max(bids, key=lambda item: item[0]) if bids else None
    best_ask = min(asks, key=lambda item: item[0]) if asks else None
    blockers: list[str] = []
    if best_bid is None:
        blockers.append("missing_bid")
    if best_ask is None:
        blockers.append("missing_ask")
    return {
        "bid": best_bid[0] if best_bid else None,
        "bid_size": best_bid[1] if best_bid else None,
        "ask": best_ask[0] if best_ask else None,
        "ask_size": best_ask[1] if best_ask else None,
        "bids_levels": len(bids),
        "asks_levels": len(asks),
        "quote_timestamp": _string_or_none(raw_book.get("timestamp")),
        "partial_book": bool(blockers),
        "book_blockers": blockers,
    }


def normalize_kalshi_world_series_evidence(
    *,
    outcomes: list[dict[str, Any]],
    season: str,
    generated_at: datetime,
    raw_root: Path,
) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    blockers = Counter()
    quote_time_ms = str(int(generated_at.timestamp() * 1000))
    for index, outcome in enumerate(outcomes, start=1):
        market = outcome.get("market") or {}
        metrics = convert_kalshi_orderbook(outcome.get("orderbook") or {})
        missing = _missing_required_quote_fields(
            metrics,
            ("yes_bid", "yes_ask", "yes_bid_size", "yes_ask_size", "no_bid", "no_ask", "no_bid_size", "no_ask_size"),
        )
        row_blockers = list(metrics.get("book_blockers") or [])
        if missing:
            row_blockers.append("missing_required_quote_fields")
        blockers.update(row_blockers)
        normalized.append(
            {
                "rank_or_order": outcome.get("rank_or_order") or index,
                "team_name": outcome["team_name"],
                "team_aliases": outcome.get("team_aliases") or [],
                "market_ticker": outcome["market_ticker"],
                "outcome_status": _string_or_none(market.get("status") or market.get("state")) or "unknown",
                "source_slices": {
                    "outcome_ticker_slice": "kalshi_public_markets_api",
                    "quote_slice": "kalshi_public_orderbook_api" if outcome.get("orderbook_file") else None,
                },
                "quote_status": "present_full_clob" if not missing and not metrics.get("partial_book") else "partial_book",
                "quote": {
                    "yes_bid": _as_price_string(metrics.get("yes_bid")),
                    "yes_ask": _as_price_string(metrics.get("yes_ask")),
                    "yes_bid_size": _as_size_string(metrics.get("yes_bid_size")),
                    "yes_ask_size": _as_size_string(metrics.get("yes_ask_size")),
                    "no_bid": _as_price_string(metrics.get("no_bid")),
                    "no_ask": _as_price_string(metrics.get("no_ask")),
                    "no_bid_size": _as_size_string(metrics.get("no_bid_size")),
                    "no_ask_size": _as_size_string(metrics.get("no_ask_size")),
                    "last_price": _string_or_none(market.get("last_price_dollars") or market.get("last_price")),
                    "quote_source": f"Kalshi public CLOB API - {KALSHI_PUBLIC_BASE_URL}/markets/{outcome['market_ticker']}/orderbook",
                    "quote_timestamp": quote_time_ms,
                    "quote_timestamp_type": "fetch_time_unix_ms",
                    "fetch_time_utc": generated_at.isoformat(),
                    "depth_status": metrics.get("depth_status"),
                    "yes_book_levels": f"{metrics.get('yes_book_levels', 0)} bid levels / {metrics.get('no_book_levels', 0)} contra NO bid levels",
                    "source_file": outcome.get("orderbook_file"),
                    "required_quote_fields_present": not missing,
                    "missing_required_quote_fields": missing,
                    "execution_depth_ok": False,
                },
                "volume_or_open_interest": {
                    "volume_fp": _string_or_none(market.get("volume_fp")),
                    "volume_24h_fp": _string_or_none(market.get("volume_24h_fp")),
                    "open_interest_fp": _string_or_none(market.get("open_interest_fp")),
                    "units": "contracts, as returned by public Kalshi market metadata",
                },
                "blockers_remaining": sorted(set(row_blockers)),
            }
        )
    return {
        "schema_kind": "kalshi_championship_futures_normalized_evidence_v1",
        "diagnostic_only": True,
        "no_trade_or_arbitrage_claims": True,
        "paper_candidate_emitted": False,
        "platform": "Kalshi",
        "batch": "championship_futures",
        "league": "MLB",
        "season": season,
        "market": {
            "platform": "Kalshi",
            "batch": "championship_futures",
            "league": "MLB",
            "season": season,
            "market_title": f"Pro Baseball Champion {season}",
            "url": f"https://kalshi.com/markets/kxmlb/world-series/kxmlb-{season[-2:]}",
            "series_ticker": KALSHI_SERIES_TICKER,
            "event_ticker": f"{KALSHI_SERIES_TICKER}-{season[-2:]}",
            "timezone": "ET",
        },
        "rules": {
            "rules_text": _kalshi_rules_text(outcomes),
            "settlement_source": "League/association governing Pro Baseball Champion plus published Kalshi TITLE rule source agencies; manual review still required.",
            "resolution_timing": "Fetched from public market metadata where available; rules text should be reviewed before any exact comparison.",
            "void_cancellation_rules": "TITLE rules include cancellation/no-contest proportional settlement language; manual review still required.",
            "other_or_no_champion_rule": "No separate active Other outcome is expected on Kalshi team-strike board; cancellation/no-contest treatment remains rules-based.",
            "blockers_remaining": ["settlement_rules_need_manual_review"],
        },
        "fee_structure": {
            "fee_review_required": True,
            "source": "not_collected_by_public_evidence_collector",
        },
        "quote_collection_notes": {
            "api_endpoint": f"{KALSHI_PUBLIC_BASE_URL}/markets/{{ticker}}/orderbook",
            "orderbook_structure": "YES and NO resting bids are read explicitly. YES ask is derived only from the explicit best NO resting bid; sizes come from that same explicit resting level.",
            "timestamp_note": "Kalshi orderbook endpoint did not provide a server timestamp; fetch time is preserved as quote_timestamp.",
            "raw_root": str(raw_root),
        },
        "validation": {
            "team_outcomes_observed": len(normalized),
            "unique_market_tickers_observed": len({row.get("market_ticker") for row in normalized}),
            "quote_outcomes_observed": sum(1 for row in normalized if row.get("quote", {}).get("source_file")),
            "quote_outcomes_with_required_fields_present": sum(1 for row in normalized if row.get("quote", {}).get("required_quote_fields_present")),
            "other_outcome_present": False,
            "arb_or_exact_same_payoff_claim": False,
            "paper_candidate_emitted": False,
            "gates_cleared": False,
            "exact_ready": False,
            "all_outcomes_full_clob_depth": all(not row.get("blockers_remaining") for row in normalized) if normalized else False,
        },
        "outcomes": normalized,
        "summary_counts": {
            "outcomes": len(normalized),
            "tickers": len({row.get("market_ticker") for row in normalized}),
            "books_with_required_fields": sum(1 for row in normalized if row.get("quote", {}).get("required_quote_fields_present")),
            "top_blockers": [{"blocker": key, "count": value} for key, value in blockers.most_common(10)],
            "exact_ready_rows": 0,
            "paper_candidate_rows": 0,
        },
        "blockers_summary": [{"blocker": key, "count": value} for key, value in blockers.most_common(10)],
        "gates_cleared": False,
        "exact_ready": False,
    }


def normalize_polymarket_world_series_evidence(
    *,
    event: dict[str, Any],
    season: str,
    generated_at: datetime,
    raw_root: Path,
) -> dict[str, Any]:
    raw_event = event.get("event") or {}
    outcomes = event.get("outcomes") or []
    normalized: list[dict[str, Any]] = []
    blockers = Counter()
    for index, outcome in enumerate(outcomes, start=1):
        yes_metrics = parse_polymarket_clob_book(outcome.get("yes_book") or {})
        no_metrics = parse_polymarket_clob_book(outcome.get("no_book") or {})
        missing = _missing_required_quote_fields(
            {
                "yes_bid": yes_metrics.get("bid"),
                "yes_ask": yes_metrics.get("ask"),
                "yes_bid_size": yes_metrics.get("bid_size"),
                "yes_ask_size": yes_metrics.get("ask_size"),
                "no_bid": no_metrics.get("bid"),
                "no_ask": no_metrics.get("ask"),
                "no_bid_size": no_metrics.get("bid_size"),
                "no_ask_size": no_metrics.get("ask_size"),
            },
            ("yes_bid", "yes_ask", "yes_bid_size", "yes_ask_size", "no_bid", "no_ask", "no_bid_size", "no_ask_size"),
        )
        row_blockers = []
        if missing:
            row_blockers.append("missing_required_quote_fields")
        blockers.update(row_blockers)
        quote_timestamp = yes_metrics.get("quote_timestamp") or no_metrics.get("quote_timestamp") or generated_at.isoformat()
        normalized.append(
            {
                "rank_or_order": outcome.get("rank_or_order") or index,
                "team_name": outcome["team_name"],
                "outcome_name": outcome["team_name"],
                "team_aliases": outcome.get("team_aliases") or [],
                "market_id": outcome.get("market_id"),
                "condition_id": outcome.get("condition_id"),
                "token_id_yes": outcome.get("token_id_yes"),
                "token_id_no": outcome.get("token_id_no"),
                "token_id_status": "full" if outcome.get("token_id_yes") and outcome.get("token_id_no") else "partial",
                "outcome_status": _polymarket_market_status(outcome.get("market") or {}),
                "outcome_volume_or_open_interest": _string_or_none((outcome.get("market") or {}).get("volume")),
                "liquidity": _string_or_none((outcome.get("market") or {}).get("liquidity")),
                "quote_status": "present" if not missing else "partial_book",
                "quote": {
                    "yes_bid": _as_price_string(yes_metrics.get("bid")),
                    "yes_ask": _as_price_string(yes_metrics.get("ask")),
                    "yes_bid_size": _as_size_string(yes_metrics.get("bid_size")),
                    "yes_ask_size": _as_size_string(yes_metrics.get("ask_size")),
                    "no_bid": _as_price_string(no_metrics.get("bid")),
                    "no_ask": _as_price_string(no_metrics.get("ask")),
                    "no_bid_size": _as_size_string(no_metrics.get("bid_size")),
                    "no_ask_size": _as_size_string(no_metrics.get("ask_size")),
                    "last_price": _string_or_none((outcome.get("market") or {}).get("lastTradePrice")),
                    "quote_source": "Polymarket public CLOB",
                    "quote_timestamp": quote_timestamp,
                    "quote_timestamp_type": "unix_ms" if str(quote_timestamp).isdigit() else "fetch_or_iso",
                    "depth_status": "full_clob" if not missing else "partial_book",
                    "yes_book_levels": f"{yes_metrics.get('bids_levels', 0)} bid levels / {yes_metrics.get('asks_levels', 0)} ask levels",
                    "no_book_levels": f"{no_metrics.get('bids_levels', 0)} bid levels / {no_metrics.get('asks_levels', 0)} ask levels",
                    "quote_blockers_remaining": row_blockers,
                    "yes_book_source_file": outcome.get("yes_book_file"),
                    "no_book_source_file": outcome.get("no_book_file"),
                    "required_quote_fields_present": not missing,
                    "missing_required_quote_fields": missing,
                },
                "source_slices": {
                    "outcome_ids": "polymarket_gamma_event",
                    "quote": "polymarket_public_clob" if outcome.get("yes_book_file") or outcome.get("no_book_file") else None,
                },
                "data_quality_warnings": [],
                "blockers_remaining": row_blockers,
            }
        )
    other = _normalize_polymarket_other_outcome(event.get("other_outcome"), generated_at=generated_at)
    other_exists = bool(other) or _rules_mentions_other(raw_event, outcomes)
    other_ids = bool(other and other.get("token_id_yes"))
    other_quote = bool(other and other.get("quote_status") == "present")
    rules_text = _polymarket_rules_text(raw_event, outcomes)
    return {
        "schema_kind": f"polymarket_mlb_world_series_{season}_normalized_evidence_v1",
        "diagnostic_only": True,
        "no_trade_or_arbitrage_claims": True,
        "paper_candidate_emitted": False,
        "platform": "Polymarket",
        "batch": "championship_futures",
        "league": "MLB",
        "season": season,
        "market_title": _string_or_none(raw_event.get("title")) or f"MLB World Series Champion {season}",
        "url": f"https://polymarket.com/event/{event.get('event_slug')}",
        "event_slug": event.get("event_slug"),
        "parent_event_id": event.get("parent_event_id"),
        "source_slices": {
            "rules": {"status": "present" if rules_text else "missing", "source": "polymarket_gamma_event"},
            "outcome_ids": {"status": "present" if outcomes else "missing", "source": "polymarket_gamma_event"},
            "quotes": {"status": "present" if any(row.get("quote_status") == "present" for row in normalized) else "missing", "source": "polymarket_public_clob"},
            "quotes_other": {"status": "present" if other_quote else "missing", "source": "polymarket_public_clob" if other_quote else None},
        },
        "rules": {
            "rules_text": rules_text,
            "settlement_source": _string_or_none(raw_event.get("resolutionSource")) or _resolution_source_from_rules(rules_text),
            "resolution_timing": _string_or_none(raw_event.get("endDate") or raw_event.get("endDateIso")),
            "void_cancellation_rules": _sentence_for_terms(rules_text, ("cancelled", "canceled", "postponed", "other")),
            "team_resolves_no_on_elimination": _sentence_for_terms(rules_text, ("eliminated", "impossible")),
            "other_outcome_exists": bool(other_exists),
            "other_resolution_rule": _sentence_for_terms(rules_text, ("other",)) or ("Other mentioned in market structure." if other_exists else None),
            "timezone": "ET/UTC as returned by Gamma; manual review still required.",
            "neg_risk": _string_or_none(raw_event.get("negRisk") or raw_event.get("enableNegRisk") or raw_event.get("negRiskMarketID")),
            "resolver": _string_or_none(raw_event.get("resolvedBy")),
            "fee_structure": {
                "makerBaseFee_bps": _string_or_none((outcomes[0].get("market") or {}).get("makerBaseFee")) if outcomes else None,
                "takerBaseFee_bps": _string_or_none((outcomes[0].get("market") or {}).get("takerBaseFee")) if outcomes else None,
                "fee_review_required": True,
            },
            "blockers_remaining": ["settlement_rules_need_manual_review"],
        },
        "market_structure": {
            "listed_team_count": len(normalized),
            "other_outcome_exists": bool(other_exists),
            "other_outcome_ids_provided": bool(other_ids),
            "other_quote_provided": bool(other_quote),
            "neg_risk": _string_or_none(raw_event.get("negRisk") or raw_event.get("enableNegRisk") or raw_event.get("negRiskMarketID")),
        },
        "quote_collection": {
            "quote_source": "Polymarket public CLOB",
            "depth_status_observed": _depth_status_summary(normalized),
            "fetch_time_utc": generated_at.isoformat(),
            "raw_root": str(raw_root),
        },
        "outcomes": normalized,
        "other_outcome": other,
        "validation": {
            "team_outcomes_observed": len(normalized),
            "unique_market_ids_observed": len({row.get("market_id") for row in normalized if row.get("market_id")}),
            "token_pairs_observed": sum(1 for row in normalized if row.get("token_id_yes") and row.get("token_id_no")),
            "quote_outcomes_observed": sum(1 for row in normalized if row.get("quote_status") == "present"),
            "quote_outcomes_with_required_fields_present": sum(1 for row in normalized if row.get("quote", {}).get("required_quote_fields_present")),
            "other_outcome_present": bool(other_exists),
            "other_outcome_ids_provided": bool(other_ids),
            "other_quote_provided": bool(other_quote),
            "arb_or_exact_same_payoff_claim": False,
            "paper_candidate_emitted": False,
            "gates_cleared": False,
            "exact_ready": False,
        },
        "summary_counts": {
            "outcomes": len(normalized),
            "token_ids": sum(1 for row in normalized for key in ("token_id_yes", "token_id_no") if row.get(key)),
            "books_with_required_fields": sum(1 for row in normalized if row.get("quote", {}).get("required_quote_fields_present")),
            "top_blockers": [{"blocker": key, "count": value} for key, value in blockers.most_common(10)],
            "exact_ready_rows": 0,
            "paper_candidate_rows": 0,
        },
        "blockers_remaining": ["settlement_rules_need_manual_review"],
        "gates_cleared": False,
        "exact_ready": False,
    }


def build_collection_summary(
    *,
    season: str,
    kalshi_evidence: dict[str, Any],
    polymarket_evidence: dict[str, Any],
    warnings: list[dict[str, Any]],
    raw_files: list[str],
    raw_root: Path,
) -> dict[str, Any]:
    kalshi_outcomes = kalshi_evidence.get("outcomes") or []
    poly_outcomes = polymarket_evidence.get("outcomes") or []
    blockers = Counter()
    for row in kalshi_outcomes + poly_outcomes:
        blockers.update(row.get("blockers_remaining") or [])
    for blocker in kalshi_evidence.get("rules", {}).get("blockers_remaining") or []:
        blockers[blocker] += 1
    for blocker in polymarket_evidence.get("rules", {}).get("blockers_remaining") or []:
        blockers[blocker] += 1
    kalshi_books = sum(1 for row in kalshi_outcomes if row.get("quote", {}).get("source_file"))
    poly_yes_no_tokens = sum(1 for row in poly_outcomes for key in ("token_id_yes", "token_id_no") if row.get(key))
    poly_books = sum(
        1
        for row in poly_outcomes
        for key in ("yes_book_source_file", "no_book_source_file")
        if row.get("quote", {}).get(key)
    )
    summary_counts = {
        "kalshi_team_outcomes": len(kalshi_outcomes),
        "kalshi_tickers": len({row.get("market_ticker") for row in kalshi_outcomes if row.get("market_ticker")}),
        "kalshi_orderbooks_requested": kalshi_books,
        "kalshi_books_with_full_depth": sum(1 for row in kalshi_outcomes if row.get("quote", {}).get("required_quote_fields_present")),
        "polymarket_team_outcomes": len(poly_outcomes),
        "polymarket_token_ids": poly_yes_no_tokens,
        "polymarket_books_requested": poly_books,
        "polymarket_books_with_full_depth": sum(1 for row in poly_outcomes if row.get("quote", {}).get("required_quote_fields_present")),
        "raw_files_written": len(raw_files),
        "warnings": len(warnings),
        "exact_ready_rows": 0,
        "paper_candidate_rows": 0,
    }
    missing = {
        "kalshi_missing_tickers": max(0, 30 - summary_counts["kalshi_tickers"]),
        "kalshi_missing_orderbooks": max(0, summary_counts["kalshi_tickers"] - summary_counts["kalshi_orderbooks_requested"]),
        "polymarket_missing_team_outcomes": max(0, 30 - summary_counts["polymarket_team_outcomes"]),
        "polymarket_missing_token_ids": max(0, summary_counts["polymarket_team_outcomes"] * 2 - summary_counts["polymarket_token_ids"]),
        "polymarket_missing_books": max(0, summary_counts["polymarket_token_ids"] - summary_counts["polymarket_books_requested"]),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "schema_kind": SCHEMA_KIND,
        "source": REPORT_SOURCE,
        "season": season,
        "diagnostic_only": True,
        "public_no_auth_only": True,
        "raw_root": str(raw_root),
        "summary_counts": summary_counts,
        "missing_fields_or_blockers": missing,
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
            "evaluator_invoked": False,
            "exact_ready": False,
            "paper_candidate_emitted": False,
        },
    }


def render_collection_summary_markdown(summary: dict[str, Any]) -> str:
    counts = summary.get("summary_counts") or {}
    missing = summary.get("missing_fields_or_blockers") or {}
    lines = [
        "# MLB World Series Evidence Collection",
        "",
        "Public read-only championship-futures evidence collector. It writes raw snapshots and normalized evidence only; it does not create candidates or affect evaluator gates.",
        "",
        "## Summary",
        "",
        f"- season: `{_md(summary.get('season'))}`",
        f"- kalshi_team_outcomes: `{counts.get('kalshi_team_outcomes', 0)}`",
        f"- kalshi_tickers: `{counts.get('kalshi_tickers', 0)}`",
        f"- kalshi_orderbooks_requested: `{counts.get('kalshi_orderbooks_requested', 0)}`",
        f"- kalshi_books_with_full_depth: `{counts.get('kalshi_books_with_full_depth', 0)}`",
        f"- polymarket_team_outcomes: `{counts.get('polymarket_team_outcomes', 0)}`",
        f"- polymarket_token_ids: `{counts.get('polymarket_token_ids', 0)}`",
        f"- polymarket_books_requested: `{counts.get('polymarket_books_requested', 0)}`",
        f"- polymarket_books_with_full_depth: `{counts.get('polymarket_books_with_full_depth', 0)}`",
        f"- raw_files_written: `{counts.get('raw_files_written', 0)}`",
        f"- exact_ready_rows: `0`",
        f"- paper_candidate_rows: `0`",
        "",
        "## Missing Fields",
        "",
        "| Field | Count |",
        "|---|---:|",
    ]
    for key, value in missing.items():
        lines.append(f"| {_md(key)} | {_md(value)} |")
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


def _fetch_kalshi_world_series_raw(
    *,
    season: str,
    timeout_seconds: float,
    getter: HttpGet,
    kalshi_base_url: str,
    raw_dir: Path,
    raw_files: list[str],
    warnings: list[dict[str, Any]],
    generated_at: datetime,
) -> list[Any]:
    event_ticker = f"{KALSHI_SERIES_TICKER}-{season[-2:]}"
    url = f"{kalshi_base_url.rstrip('/')}/markets?{urlencode({'status': 'open', 'limit': '200', 'series_ticker': KALSHI_SERIES_TICKER, 'event_ticker': event_ticker})}"
    try:
        payload = getter(url, timeout_seconds)
    except Exception as exc:  # pragma: no cover - network details vary
        warnings.append({"venue": "kalshi", "url": url, "reason": "public_request_failed", "error": f"{type(exc).__name__}: {exc}"})
        return []
    filename = raw_dir / f"markets_{_safe_slug(event_ticker)}.json"
    _write_json(filename, {"source": "kalshi_markets_raw", "url": url, "captured_at": generated_at.isoformat(), "raw_response": payload})
    raw_files.append(str(filename))
    return [payload]


def _fetch_kalshi_orderbooks(
    *,
    outcomes: list[dict[str, Any]],
    timeout_seconds: float,
    getter: HttpGet,
    kalshi_base_url: str,
    raw_dir: Path,
    raw_files: list[str],
    warnings: list[dict[str, Any]],
    generated_at: datetime,
) -> None:
    for outcome in outcomes:
        ticker = _string_or_none(outcome.get("market_ticker"))
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
        outcome["orderbook"] = payload
        outcome["orderbook_file"] = str(filename)


def _fetch_polymarket_world_series_raw(
    *,
    season: str,
    timeout_seconds: float,
    getter: HttpGet,
    gamma_base_url: str,
    raw_dir: Path,
    raw_files: list[str],
    warnings: list[dict[str, Any]],
    generated_at: datetime,
) -> list[Any]:
    slug = f"mlb-world-series-champion-{season}"
    queries = [
        ("/events", {"slug": slug}),
        ("/events", {"search": f"MLB World Series Champion {season}", "limit": "20"}),
    ]
    payloads: list[Any] = []
    for index, (path, params) in enumerate(queries):
        url = f"{gamma_base_url.rstrip('/')}{path}?{urlencode(params)}"
        try:
            payload = getter(url, timeout_seconds)
        except Exception as exc:  # pragma: no cover
            warnings.append({"venue": "polymarket", "url": url, "reason": "public_request_failed", "error": f"{type(exc).__name__}: {exc}"})
            continue
        filename = raw_dir / f"gamma_{path.strip('/')}_{index}.json"
        _write_json(filename, {"source": "polymarket_gamma_raw", "url": url, "captured_at": generated_at.isoformat(), "raw_response": payload})
        raw_files.append(str(filename))
        payloads.append(payload)
    return payloads


def _fetch_polymarket_books(
    *,
    event: dict[str, Any],
    timeout_seconds: float,
    getter: HttpGet,
    clob_base_url: str,
    raw_dir: Path,
    raw_files: list[str],
    warnings: list[dict[str, Any]],
    generated_at: datetime,
) -> None:
    targets: list[tuple[dict[str, Any], str, str]] = []
    for outcome in event.get("outcomes") or []:
        for side, key in (("yes", "token_id_yes"), ("no", "token_id_no")):
            token_id = _string_or_none(outcome.get(key))
            if token_id:
                targets.append((outcome, side, token_id))
    other = event.get("other_outcome")
    if other:
        for side, key in (("yes", "token_id_yes"), ("no", "token_id_no")):
            token_id = _string_or_none(other.get(key))
            if token_id:
                targets.append((other, side, token_id))
    for outcome, side, token_id in targets:
        url = f"{clob_base_url.rstrip('/')}/book?{urlencode({'token_id': token_id})}"
        try:
            payload = getter(url, timeout_seconds)
        except Exception as exc:  # pragma: no cover
            warnings.append({"venue": "polymarket", "url": url, "token_id": token_id, "reason": "public_book_request_failed", "error": f"{type(exc).__name__}: {exc}"})
            continue
        filename = raw_dir / f"book_{_safe_slug(token_id)}.json"
        _write_json(filename, {"source": "polymarket_clob_book_raw", "url": url, "token_id": token_id, "captured_at": generated_at.isoformat(), "raw_response": payload})
        raw_files.append(str(filename))
        outcome[f"{side}_book"] = payload
        outcome[f"{side}_book_file"] = str(filename)


def _normalize_polymarket_other_outcome(other: dict[str, Any] | None, *, generated_at: datetime) -> dict[str, Any] | None:
    if not other:
        return None
    yes_metrics = parse_polymarket_clob_book(other.get("yes_book") or {})
    no_metrics = parse_polymarket_clob_book(other.get("no_book") or {})
    has_ids = bool(other.get("token_id_yes") or other.get("token_id_no"))
    has_quote = bool(other.get("yes_book_file") or other.get("no_book_file"))
    return {
        "outcome_name": "Other",
        "market_id": other.get("market_id"),
        "condition_id": other.get("condition_id"),
        "token_id_yes": other.get("token_id_yes"),
        "token_id_no": other.get("token_id_no"),
        "other_outcome_ids_provided": has_ids,
        "other_quote_provided": has_quote,
        "quote_status": "present" if has_quote else "missing",
        "quote": {
            "yes_bid": _as_price_string(yes_metrics.get("bid")),
            "yes_ask": _as_price_string(yes_metrics.get("ask")),
            "yes_bid_size": _as_size_string(yes_metrics.get("bid_size")),
            "yes_ask_size": _as_size_string(yes_metrics.get("ask_size")),
            "no_bid": _as_price_string(no_metrics.get("bid")),
            "no_ask": _as_price_string(no_metrics.get("ask")),
            "no_bid_size": _as_size_string(no_metrics.get("bid_size")),
            "no_ask_size": _as_size_string(no_metrics.get("ask_size")),
            "quote_timestamp": yes_metrics.get("quote_timestamp") or no_metrics.get("quote_timestamp") or generated_at.isoformat(),
        },
        "blockers_remaining": [] if has_quote else ["other_quote_missing"],
    }


def _kalshi_markets(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("raw_response"), dict):
        payload = payload["raw_response"]
    if isinstance(payload, dict) and isinstance(payload.get("markets"), list):
        return [row for row in payload["markets"] if isinstance(row, dict)]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return []


def _records_from_payloads(payloads: list[Any]) -> list[Any]:
    records: list[Any] = []
    for payload in payloads:
        if isinstance(payload, dict) and isinstance(payload.get("raw_response"), (list, dict)):
            payload = payload["raw_response"]
        if isinstance(payload, list):
            records.extend(payload)
        elif isinstance(payload, dict):
            if isinstance(payload.get("markets"), list) and (payload.get("slug") or payload.get("title") or payload.get("id")):
                records.append(payload)
                continue
            for key in ("events", "markets", "data", "results"):
                value = payload.get(key)
                if isinstance(value, list):
                    records.extend(value)
                    break
            else:
                records.append(payload)
    return records


def _token_ids_from_market(market: dict[str, Any]) -> list[str]:
    tokens = _maybe_json_array(
        market.get("clobTokenIds")
        or market.get("clob_token_ids")
        or market.get("tokenIds")
        or market.get("token_ids")
    )
    return [str(token) for token in tokens if token is not None]


def _looks_like_world_series_text(text: Any, season: str) -> bool:
    value = str(text or "").lower()
    return season in value and "world series" in value and ("champion" in value or "winner" in value)


def _looks_like_polymarket_team_market(market: dict[str, Any], season: str) -> bool:
    return _polymarket_market_team_code(market, season) is not None or _is_other_polymarket_market(market)


def _polymarket_market_team_code(market: dict[str, Any], season: str) -> str | None:
    for key in ("groupItemTitle", "group_item_title", "team", "outcome"):
        code = team_code(market.get(key))
        if code:
            return code
    question = _string_or_none(market.get("question") or market.get("title")) or ""
    match = _WORLD_SERIES_QUESTION_RE.search(question)
    if match and match.group("season") == season:
        return team_code(match.group("team"))
    slug = _string_or_none(market.get("slug")) or ""
    slug_parts = [part for part in re.split(r"[-_]+", slug.lower()) if part]
    for size in range(min(4, len(slug_parts)), 0, -1):
        for index in range(0, len(slug_parts) - size + 1):
            code = team_code(" ".join(slug_parts[index : index + size]))
            if code:
                return code
    return None


def _is_other_polymarket_market(market: dict[str, Any]) -> bool:
    text = " ".join(str(market.get(key) or "") for key in ("question", "title", "slug", "groupItemTitle")).lower()
    return "another team" in text or re.search(r"\bother\b", text) is not None


def _polymarket_market_status(market: dict[str, Any]) -> str:
    if market.get("closed") is True:
        return "closed"
    if market.get("active") is True:
        return "active"
    return _string_or_none(market.get("status")) or "unknown"


def _polymarket_rules_text(event: dict[str, Any], outcomes: list[dict[str, Any]]) -> str:
    values = [
        event.get("description"),
        event.get("resolutionSource"),
        event.get("rules"),
    ]
    for outcome in outcomes[:3]:
        market = outcome.get("market") or {}
        values.extend([market.get("description"), market.get("rules")])
    return "\n\n".join(str(value).strip() for value in values if value)


def _rules_mentions_other(event: dict[str, Any], outcomes: list[dict[str, Any]]) -> bool:
    text = _polymarket_rules_text(event, outcomes).lower()
    return " other" in f" {text}" or "another team" in text


def _resolution_source_from_rules(text: str) -> str | None:
    if "mlb.com" in text.lower():
        return "https://www.mlb.com/"
    return None


def _sentence_for_terms(text: str, terms: tuple[str, ...]) -> str | None:
    if not text:
        return None
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for sentence in sentences:
        lowered = sentence.lower()
        if any(term in lowered for term in terms):
            return sentence.strip()
    return None


def _kalshi_rules_text(outcomes: list[dict[str, Any]]) -> str:
    for outcome in outcomes:
        market = outcome.get("market") or {}
        values = [market.get("rules_primary"), market.get("rules_secondary")]
        text = "\n\n".join(str(value).strip() for value in values if value)
        if text:
            return text
    return f"Public Kalshi TITLE rules should be reviewed for {KALSHI_SERIES_TICKER}; no full rules text was returned in the captured market rows. Reference: {KALSHI_RULES_URL}"


def _depth_status_summary(rows: list[dict[str, Any]]) -> str:
    full = sum(1 for row in rows if row.get("quote", {}).get("required_quote_fields_present"))
    total = len(rows)
    if total and full == total:
        return f"full_clob for all {total} listed team outcomes"
    return f"full_clob for {full}/{total} listed team outcomes"


def team_code(value: Any) -> str | None:
    text = _string_or_none(value)
    if not text:
        return None
    upper = text.upper()
    if upper in TEAM_CODE_TO_NAME:
        return TEAM_CODE_CANONICAL.get(upper, upper)
    normalized = _normalize_team_text(text)
    code = _TEAM_ALIASES.get(normalized)
    return TEAM_CODE_CANONICAL.get(code, code) if code else None


def _team_code_from_text(value: Any) -> str | None:
    text = _string_or_none(value)
    if not text:
        return None
    lowered = _normalize_team_text(text)
    candidates = sorted(_TEAM_ALIASES.items(), key=lambda item: len(item[0]), reverse=True)
    for alias, code in candidates:
        if re.search(rf"\b{re.escape(alias)}\b", lowered):
            return TEAM_CODE_CANONICAL.get(code, code)
    return None


def _team_aliases_for_code(code: str | None) -> list[str]:
    if not code:
        return []
    canonical = TEAM_CODE_CANONICAL.get(code, code)
    name = TEAM_CODE_TO_NAME.get(canonical, TEAM_CODE_TO_NAME.get(code, code))
    nickname = name.split()[-1] if name else canonical
    aliases = [canonical, nickname]
    if canonical == "ARI":
        aliases.append("AZ")
    if canonical == "ATH":
        aliases.append("OAK")
    if canonical == "WSH":
        aliases.append("WAS")
    return list(dict.fromkeys(aliases))


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


def _missing_required_quote_fields(row: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    return [key for key in keys if row.get(key) is None]


def _empty_kalshi_book(blockers: list[str]) -> dict[str, Any]:
    return {
        "partial_book": True,
        "book_blockers": blockers,
        "yes_bid": None,
        "yes_bid_size": None,
        "yes_ask": None,
        "yes_ask_size": None,
        "no_bid": None,
        "no_bid_size": None,
        "no_ask": None,
        "no_ask_size": None,
        "yes_book_levels": 0,
        "no_book_levels": 0,
        "depth_status": "missing_book",
    }


def _empty_polymarket_book(blockers: list[str]) -> dict[str, Any]:
    return {
        "bid": None,
        "bid_size": None,
        "ask": None,
        "ask_size": None,
        "bids_levels": 0,
        "asks_levels": 0,
        "quote_timestamp": None,
        "partial_book": True,
        "book_blockers": blockers,
    }


def _price_sort_key(market: Any) -> float:
    if not isinstance(market, dict):
        return 999.0
    for key in ("last_price_dollars", "lastTradePrice", "bestBid", "outcomePrices"):
        value = market.get(key)
        if key == "outcomePrices":
            values = _maybe_json_array(value)
            if values:
                price = _price_or_none(values[0])
                if price is not None:
                    return -price
        price = _price_or_none(value)
        if price is not None:
            return -price
    return 999.0


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


def _as_price_string(value: Any) -> str | None:
    number = _float_or_none(value)
    if number is None:
        return None
    return f"{number:.10f}".rstrip("0").rstrip(".")


def _as_size_string(value: Any) -> str | None:
    number = _float_or_none(value)
    if number is None:
        return None
    return f"{number:.10f}".rstrip("0").rstrip(".")


def _season_label(season: int | str) -> str:
    text = str(season).strip()
    if not re.fullmatch(r"\d{4}", text):
        raise ValueError("season must be a four-digit year")
    return text


def _safe_slug(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    return safe[:120] or "item"


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
