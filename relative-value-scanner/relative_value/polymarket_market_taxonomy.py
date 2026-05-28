from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SCHEMA_VERSION = 1
REPORT_SOURCE = "polymarket_market_taxonomy_v1"
RAW_RESPONSE_SOURCE = "polymarket_market_universe_raw_response_v1"

GAMMA_API_BASE_URL = "https://gamma-api.polymarket.com"
CLOB_API_BASE_URL = "https://clob.polymarket.com"

DEFAULT_USER_AGENT = "relative-value-scanner/0.1 public-read-only"
PUBLIC_READ_HEADERS = {
    "Accept": "application/json",
    "User-Agent": DEFAULT_USER_AGENT,
}

FAMILY_CRYPTO = "CRYPTO"
FAMILY_POLITICS_ELECTION_RESULT = "POLITICS_ELECTION_RESULT"
FAMILY_POLITICS_NEWS_OR_POLICY = "POLITICS_NEWS_OR_POLICY"
FAMILY_MACRO_FED_RATES = "MACRO_FED_RATES"
FAMILY_MACRO_ECONOMIC_RELEASE = "MACRO_ECONOMIC_RELEASE"
FAMILY_TECH_AI = "TECH_AI"
FAMILY_TECH_COMPANY_PRODUCT = "TECH_COMPANY_PRODUCT"
FAMILY_SPORTS_GAME = "SPORTS_GAME"
FAMILY_SPORTS_FUTURES = "SPORTS_FUTURES"
FAMILY_CULTURE_ENTERTAINMENT = "CULTURE_ENTERTAINMENT"
FAMILY_WEATHER = "WEATHER"
FAMILY_OTHER_UNKNOWN = "OTHER_UNKNOWN"

SHAPE_POINT_IN_TIME_THRESHOLD = "POINT_IN_TIME_THRESHOLD"
SHAPE_DEADLINE_HIT_BY_DATE = "DEADLINE_HIT_BY_DATE"
SHAPE_RANGE_BUCKET = "RANGE_BUCKET"
SHAPE_BINARY_EVENT_RESULT = "BINARY_EVENT_RESULT"
SHAPE_ELECTION_WINNER = "ELECTION_WINNER"
SHAPE_NOMINATION_WINNER = "NOMINATION_WINNER"
SHAPE_YES_NO_NEWS_EVENT = "YES_NO_NEWS_EVENT"
SHAPE_SPORTS_MONEYLINE = "SPORTS_MONEYLINE"
SHAPE_SPORTS_SPREAD = "SPORTS_SPREAD"
SHAPE_SPORTS_TOTAL = "SPORTS_TOTAL"
SHAPE_SPORTS_FUTURES_WINNER = "SPORTS_FUTURES_WINNER"
SHAPE_UP_DOWN_INTERVAL = "UP_DOWN_INTERVAL"
SHAPE_MACRO_RATE_TARGET = "MACRO_RATE_TARGET"
SHAPE_ECONOMIC_RELEASE_THRESHOLD = "ECONOMIC_RELEASE_THRESHOLD"
SHAPE_TECH_RELEASE_OR_PRODUCT_EVENT = "TECH_RELEASE_OR_PRODUCT_EVENT"
SHAPE_COMPANY_MARKET_CAP_OR_PRICE_THRESHOLD = "COMPANY_MARKET_CAP_OR_PRICE_THRESHOLD"
SHAPE_UNKNOWN_OR_COMPOUND = "UNKNOWN_OR_COMPOUND"

_CRYPTO_ASSET_PATTERN = re.compile(r"\b(bitcoin|btc|ethereum|eth)\b", re.IGNORECASE)
_THRESHOLD_PATTERN = re.compile(r"\$?\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*([kmb])?\b", re.IGNORECASE)
_DATE_PATTERN = re.compile(
    r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+\d{1,2}(?:st|nd|rd|th)?(?:,\s*\d{4})?\b",
    re.IGNORECASE,
)
_YEAR_PATTERN = re.compile(r"\b(20\d{2})\b")
_TIME_PATTERN = re.compile(
    r"\b(\d{1,2}(?::\d{2})?\s*(?:AM|PM|a\.m\.|p\.m\.)?)\s*"
    r"(EST|EDT|CST|CDT|MST|MDT|PST|PDT|UTC|GMT|ET|CT|MT|PT)\b",
    re.IGNORECASE,
)
_URL_PATTERN = re.compile(r"https?://[^\s),;\"']+")

_SPORT_LEAGUES = {
    "nfl": "NFL",
    "nba": "NBA",
    "mlb": "MLB",
    "nhl": "NHL",
    "wnba": "WNBA",
    "epl": "EPL",
    "champions league": "UEFA_CHAMPIONS_LEAGUE",
    "college football": "NCAAF",
    "college basketball": "NCAAB",
}
_HARD_COMPOUND_PATTERN = re.compile(
    r"\b(gta|album|microstrategy|el[-\s]?salvador|treasury|reserve|adoption|"
    r"company\s+(?:buy|sell)|bitcoin\s+core|bitcoin\s+knots)\b",
    re.IGNORECASE,
)

SEARCH_TERMS = (
    "bitcoin",
    "ethereum",
    "btc above",
    "eth above",
    "presidential election",
    "senate election",
    "nomination",
    "fed rate",
    "fomc",
    "cpi inflation",
    "unemployment",
    "openai",
    "chatgpt",
    "artificial intelligence",
    "nfl",
    "nba",
    "mlb",
    "weather",
)

TAG_SLUGS = (
    "crypto",
    "politics",
    "sports",
    "elections",
    "economy",
    "business",
    "technology",
    "weather",
)

HttpGet = Callable[[str, float], Any]


def build_polymarket_market_universe_report(
    *,
    output_dir: Path,
    limit: int = 1000,
    include_books: bool = False,
    generated_at: datetime | None = None,
    timeout_seconds: float = 10.0,
    gamma_base_url: str = GAMMA_API_BASE_URL,
    clob_base_url: str = CLOB_API_BASE_URL,
    http_get: HttpGet | None = None,
    max_pages: int = 2,
) -> dict[str, Any]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    if max_pages <= 0:
        raise ValueError("max_pages must be positive")
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    getter = http_get or _default_http_get
    timestamp = generated.strftime("%Y%m%d_%H%M%SZ")
    snapshot_dir = output_dir / timestamp
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    endpoints_attempted: list[dict[str, Any]] = []
    raw_files_written: list[str] = []
    warnings: list[dict[str, Any]] = []
    taxonomy_rows: list[dict[str, Any]] = []
    seen_keys: set[tuple[str | None, str | None, str | None]] = set()
    book_files_attempted: list[dict[str, Any]] = []
    books_saved = 0
    page_limit = min(limit, 500)

    for endpoint in _endpoint_specs():
        for page in range(max_pages):
            offset = page * page_limit
            url = _gamma_url(gamma_base_url, endpoint["path"], endpoint["params"], page_limit, offset)
            attempt = {
                "endpoint_name": endpoint["name"],
                "url": url,
                "query_params": dict(endpoint["params"], limit=str(page_limit), offset=str(offset)),
                "page": page,
                "offset": offset,
                "public_no_auth": True,
            }
            endpoints_attempted.append(attempt)
            try:
                payload = getter(url, timeout_seconds)
            except Exception as exc:  # pragma: no cover - network exception details vary
                warnings.append(
                    {
                        "endpoint_name": endpoint["name"],
                        "url": url,
                        "reason_code": "public_endpoint_request_failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                break

            raw_file = snapshot_dir / f"raw_{endpoint['name']}_offset_{offset}.json"
            _write_json(
                raw_file,
                {
                    "schema_version": SCHEMA_VERSION,
                    "source": RAW_RESPONSE_SOURCE,
                    "endpoint_name": endpoint["name"],
                    "query_params": attempt["query_params"],
                    "url": url,
                    "captured_at": generated.isoformat(),
                    "public_no_auth": True,
                    "raw_response": payload,
                },
            )
            raw_files_written.append(str(raw_file))
            records = _records_from_response(payload, endpoint_kind=endpoint["kind"])
            attempt["record_count"] = len(records)
            if not records:
                warnings.append(
                    {
                        "endpoint_name": endpoint["name"],
                        "url": url,
                        "reason_code": "empty_public_response",
                    }
                )
                break

            for event, market in _event_market_pairs(records, endpoint_kind=endpoint["kind"]):
                key = (
                    _string_or_none((event or {}).get("slug") or (market or {}).get("eventSlug")),
                    _string_or_none((market or {}).get("slug")),
                    _string_or_none((market or {}).get("id") or (market or {}).get("conditionId")),
                )
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                row = _taxonomy_row(
                    event=event,
                    market=market,
                    raw_source_file=str(raw_file),
                    generated_at=generated,
                    row_index=len(taxonomy_rows),
                )

                row_book_files: dict[str, str] = {}
                if include_books:
                    for token_id in row.get("token_ids") or []:
                        book_url = _clob_book_url(clob_base_url, token_id)
                        try:
                            book_payload = getter(book_url, timeout_seconds)
                        except Exception as exc:  # pragma: no cover - network exception details vary
                            warnings.append(
                                {
                                    "endpoint_name": "clob_book",
                                    "url": book_url,
                                    "reason_code": "public_book_request_failed",
                                    "token_id": token_id,
                                    "error": f"{type(exc).__name__}: {exc}",
                                }
                            )
                            book_files_attempted.append(
                                {
                                    "token_id": token_id,
                                    "url": book_url,
                                    "status": "failed",
                                    "taxonomy_row_index": row.get("row_index"),
                                }
                            )
                            continue
                        book_file = snapshot_dir / f"book_{_safe_slug(token_id)}.json"
                        _write_json(
                            book_file,
                            {
                                "schema_version": SCHEMA_VERSION,
                                "source": RAW_RESPONSE_SOURCE,
                                "endpoint_name": "clob_book",
                                "url": book_url,
                                "token_id": token_id,
                                "captured_at": generated.isoformat(),
                                "public_no_auth": True,
                                "raw_response": book_payload,
                            },
                        )
                        raw_files_written.append(str(book_file))
                        row_book_files[token_id] = str(book_file)
                        books_saved += 1
                        book_files_attempted.append(
                            {
                                "token_id": token_id,
                                "url": book_url,
                                "status": "saved",
                                "book_file": str(book_file),
                                "taxonomy_row_index": row.get("row_index"),
                            }
                        )
                row["book_files_by_token_id"] = row_book_files
                taxonomy_rows.append(row)

            if len(records) < page_limit:
                break

    summary = _summary(
        rows=taxonomy_rows,
        endpoints_attempted=endpoints_attempted,
        raw_files_written=raw_files_written,
        warnings=warnings,
        books_saved=books_saved,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "output_dir": str(output_dir),
        "snapshot_dir": str(snapshot_dir),
        "limit": limit,
        "include_books": include_books,
        "summary": summary,
        "endpoints_attempted": endpoints_attempted,
        "taxonomy_rows": taxonomy_rows,
        "unknown_shape_clusters": _unknown_shape_clusters(taxonomy_rows),
        "high_value_parser_targets": _high_value_parser_targets(taxonomy_rows),
        "book_files_attempted": book_files_attempted,
        "raw_files_written": raw_files_written,
        "warnings": warnings,
        "safety": _safety_block(),
    }


def write_polymarket_market_universe_files(
    *,
    output_dir: Path,
    json_output: Path,
    markdown_output: Path,
    limit: int = 1000,
    include_books: bool = False,
    generated_at: datetime | None = None,
    timeout_seconds: float = 10.0,
    max_pages: int = 2,
) -> dict[str, Any]:
    report = build_polymarket_market_universe_report(
        output_dir=output_dir,
        limit=limit,
        include_books=include_books,
        generated_at=generated_at,
        timeout_seconds=timeout_seconds,
        max_pages=max_pages,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.write_text(render_polymarket_market_taxonomy_markdown(report), encoding="utf-8")
    return report


def render_polymarket_market_taxonomy_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Polymarket Market Taxonomy",
        "",
        "Public no-auth Gamma/CLOB read-only universe discovery. This report is taxonomy/review only and does not affect evaluator gates.",
        "",
        "## Summary",
        "",
        f"- total_events: `{summary.get('total_events', 0)}`",
        f"- total_markets: `{summary.get('total_markets', 0)}`",
        f"- typed_key_complete_count: `{summary.get('typed_key_complete_count', 0)}`",
        f"- partial_count: `{summary.get('partial_count', 0)}`",
        f"- unknown_count: `{summary.get('unknown_count', 0)}`",
        f"- books_saved: `{summary.get('books_saved', 0)}`",
        "",
        "## By Family",
        "",
    ]
    lines.extend(_count_table(summary.get("by_family") or {}))
    lines.extend(["", "## By Market Shape", ""])
    lines.extend(_count_table(summary.get("by_market_shape") or {}))
    lines.extend(["", "## High Value Parser Targets", "", "| Target | Priority | Rows | Reason |", "|---|---:|---:|---|"])
    for target in report.get("high_value_parser_targets") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(target.get("target")),
                    _md(target.get("parser_priority")),
                    _md(target.get("row_count")),
                    _md(target.get("reason")),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Unknown Shape Examples", "", "| Pattern | Family | Rows | Example | Reason | Priority |", "|---|---|---:|---|---|---:|"])
    for cluster in (report.get("unknown_shape_clusters") or [])[:50]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(cluster.get("pattern")),
                    _md(cluster.get("suggested_parser_family")),
                    _md(cluster.get("row_count")),
                    _md(cluster.get("examples", [{}])[0].get("slug") if cluster.get("examples") else None),
                    _md(cluster.get("reason_unknown")),
                    _md(cluster.get("parser_priority")),
                ]
            )
            + " |"
        )
    safety = report.get("safety") or {}
    lines.extend(
        [
            "",
            "## Safety",
            "",
            f"- diagnostic_only: `{str(safety.get('diagnostic_only')).lower()}`",
            f"- affects_evaluator_gates: `{str(safety.get('affects_evaluator_gates')).lower()}`",
            f"- live_trading: `{str(safety.get('live_trading')).lower()}`",
            f"- authenticated_endpoints_used: `{str(safety.get('authenticated_endpoints_used')).lower()}`",
            f"- orders_or_cancellations: `{str(safety.get('orders_or_cancellations')).lower()}`",
            f"- paper_candidate_emitted: `{str(safety.get('paper_candidate_emitted')).lower()}`",
        ]
    )
    return "\n".join(lines) + "\n"


def _taxonomy_row(
    *,
    event: dict[str, Any] | None,
    market: dict[str, Any] | None,
    raw_source_file: str,
    generated_at: datetime,
    row_index: int,
) -> dict[str, Any]:
    event = event or {}
    market = market or {}
    text = _combined_text(event, market)
    rules_text = _rules_text(event, market)
    family = _family(text)
    shape = _market_shape(family=family, text=text, rules_text=rules_text)
    typed_keys, typed_key_sources = _typed_keys(family=family, shape=shape, text=text, rules_text=rules_text, event=event, market=market)
    blockers = _blockers(family=family, shape=shape, typed_keys=typed_keys, rules_text=rules_text)
    typed_key_complete = _typed_key_complete(family=family, shape=shape, typed_keys=typed_keys, blockers=blockers)
    source_url = _source_url(
        event_slug=_string_or_none(event.get("slug") or market.get("eventSlug") or market.get("event_slug")),
        market_slug=_string_or_none(market.get("slug")),
    )
    if explicit_url := _explicit_source_url(rules_text):
        source_url = explicit_url
    return {
        "row_index": row_index,
        "venue": "polymarket",
        "event_id": _string_or_none(event.get("id") or market.get("eventId") or market.get("event_id")),
        "market_id": _string_or_none(market.get("id") or market.get("conditionId") or market.get("condition_id")),
        "condition_id": _string_or_none(market.get("conditionId") or market.get("condition_id")),
        "event_slug": _string_or_none(event.get("slug") or market.get("eventSlug") or market.get("event_slug")),
        "market_slug": _string_or_none(market.get("slug")),
        "title": _string_or_none(event.get("title") or event.get("name") or market.get("title")),
        "question": _string_or_none(market.get("question") or event.get("question")),
        "family": family,
        "market_shape": shape,
        "typed_keys": typed_keys,
        "typed_key_evidence_sources": typed_key_sources,
        "typed_key_complete": typed_key_complete,
        "settlement_rules_text_present": bool(rules_text),
        "settlement_source_present": bool(_settlement_source_from_rules(rules_text)),
        "source_url": source_url,
        "token_ids": _token_ids(event, market),
        "book_files_by_token_id": {},
        "raw_source_file": raw_source_file,
        "captured_at": generated_at.isoformat(),
        "blockers": blockers,
        "status": "typed_key_complete" if typed_key_complete else ("partial" if typed_keys else "discovery_only"),
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
    }


def _endpoint_specs() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = [
        {"name": "gamma_markets_active", "kind": "markets", "path": "/markets", "params": {"active": "true", "closed": "false"}},
        {"name": "gamma_events_active", "kind": "events", "path": "/events", "params": {"active": "true", "closed": "false"}},
        {
            "name": "gamma_markets_recent",
            "kind": "markets",
            "path": "/markets",
            "params": {"active": "true", "closed": "false", "order": "volume", "ascending": "false"},
        },
        {
            "name": "gamma_events_recent",
            "kind": "events",
            "path": "/events",
            "params": {"active": "true", "closed": "false", "order": "volume", "ascending": "false"},
        },
    ]
    for tag in TAG_SLUGS:
        safe = _safe_slug(tag).replace("-", "_")
        specs.append(
            {
                "name": f"gamma_events_tag_{safe}",
                "kind": "events",
                "path": "/events",
                "params": {"active": "true", "closed": "false", "tag_slug": tag},
            }
        )
        specs.append(
            {
                "name": f"gamma_markets_tag_{safe}",
                "kind": "markets",
                "path": "/markets",
                "params": {"active": "true", "closed": "false", "tag_slug": tag},
            }
        )
    for index, term in enumerate(SEARCH_TERMS):
        safe = _safe_slug(term).replace("-", "_")
        specs.append(
            {
                "name": f"gamma_events_search_{index:02d}_{safe}",
                "kind": "events",
                "path": "/events",
                "params": {"active": "true", "closed": "false", "search": term},
            }
        )
        specs.append(
            {
                "name": f"gamma_markets_search_{index:02d}_{safe}",
                "kind": "markets",
                "path": "/markets",
                "params": {"active": "true", "closed": "false", "search": term},
            }
        )
    return specs


def _family(text: str) -> str:
    lowered = text.lower()
    if re.search(r"\b(ipo|market\s+cap|stock\s+price)\b", lowered) and re.search(r"\b(kraken|coinbase|apple|tesla|nvidia|microsoft|google|meta|amazon)\b", lowered):
        return FAMILY_TECH_COMPANY_PRODUCT
    if re.search(r"\b(bitcoin|btc|ethereum|eth|crypto|solana|xrp|doge)\b", lowered):
        return FAMILY_CRYPTO
    if re.search(r"\b(fed|fomc|interest\s+rate|rate\s+(?:cut|hike|target|range)|bps|basis\s+points)\b", lowered):
        return FAMILY_MACRO_FED_RATES
    if re.search(r"\b(cpi|inflation|unemployment|payrolls?|jobs\s+report|gdp|recession)\b", lowered):
        return FAMILY_MACRO_ECONOMIC_RELEASE
    if re.search(r"\b(nfl|nba|mlb|nhl|wnba|epl|fifa|world\s+cup|super\s+bowl|world\s+series|stanley\s+cup|championship|soccer|football|baseball|basketball|hockey)\b", lowered):
        if re.search(r"\b(championship|winner|futures?)\b", lowered) or re.search(
            r"\bwin\b.*\b(super\s+bowl|world\s+series|world\s+cup|stanley\s+cup|nba\s+finals)\b",
            lowered,
        ):
            return FAMILY_SPORTS_FUTURES
        return FAMILY_SPORTS_GAME
    if re.search(r"\b(election|president|senate|house|governor|mayor|nominee|nomination|primary\s+election|electoral)\b", lowered):
        if re.search(r"\b(win|winner|nominee|nomination|elected|electoral|certified)\b", lowered):
            return FAMILY_POLITICS_ELECTION_RESULT
        return FAMILY_POLITICS_NEWS_OR_POLICY
    if re.search(r"\b(openai|chatgpt|gpt-?[0-9]|anthropic|claude|gemini|llm|artificial\s+intelligence|\bai\b)\b", lowered):
        return FAMILY_TECH_AI
    if re.search(r"\b(apple|iphone|tesla|spacex|nvidia|microsoft|google|meta|amazon|product|release|market\s+cap|stock\s+price)\b", lowered):
        return FAMILY_TECH_COMPANY_PRODUCT
    if re.search(r"\b(hurricane|temperature|rain|snow|weather|tornado|storm)\b", lowered):
        return FAMILY_WEATHER
    if re.search(r"\b(oscar|grammy|movie|album|song|box\s+office|celebrity|emmy)\b", lowered):
        return FAMILY_CULTURE_ENTERTAINMENT
    if re.search(r"\b(congress|supreme\s+court|policy|bill|law|tariff|war|ceasefire|government)\b", lowered):
        return FAMILY_POLITICS_NEWS_OR_POLICY
    return FAMILY_OTHER_UNKNOWN


def _market_shape(*, family: str, text: str, rules_text: str) -> str:
    lowered = text.lower()
    rules_lower = rules_text.lower()
    if _HARD_COMPOUND_PATTERN.search(text):
        return SHAPE_UNKNOWN_OR_COMPOUND
    if family in {FAMILY_SPORTS_GAME, FAMILY_SPORTS_FUTURES}:
        if re.search(r"\b(spread|handicap)\b", lowered):
            return SHAPE_SPORTS_SPREAD
        if re.search(r"\b(total|over/under|over\s+\d|under\s+\d)\b", lowered):
            return SHAPE_SPORTS_TOTAL
        if family == FAMILY_SPORTS_FUTURES:
            return SHAPE_SPORTS_FUTURES_WINNER
        if re.search(r"\b(moneyline|to\s+win|winner| vs | v\.? )\b", lowered):
            return SHAPE_SPORTS_MONEYLINE
        return SHAPE_UNKNOWN_OR_COMPOUND
    if family == FAMILY_POLITICS_ELECTION_RESULT:
        if re.search(r"\b(nominee|nomination|primary)\b", lowered):
            return SHAPE_NOMINATION_WINNER
        return SHAPE_ELECTION_WINNER
    if family == FAMILY_MACRO_FED_RATES:
        return SHAPE_MACRO_RATE_TARGET
    if family == FAMILY_MACRO_ECONOMIC_RELEASE:
        if _threshold(text) is not None:
            return SHAPE_ECONOMIC_RELEASE_THRESHOLD
        return SHAPE_BINARY_EVENT_RESULT
    if family in {FAMILY_TECH_AI, FAMILY_TECH_COMPANY_PRODUCT}:
        if re.search(r"\b(market\s+cap|stock\s+price|above|below|\$)\b", lowered):
            return SHAPE_COMPANY_MARKET_CAP_OR_PRICE_THRESHOLD
        return SHAPE_TECH_RELEASE_OR_PRODUCT_EVENT
    if re.search(r"\b(up[-\s]?or[-\s]?down|close\s*>=?\s*open|close\s+above\s+open|close\s+below\s+open)\b", lowered + " " + rules_lower):
        return SHAPE_UP_DOWN_INTERVAL
    if re.search(r"\bbetween\b|\brange\b|what\s+price\s+will\b", lowered):
        return SHAPE_RANGE_BUCKET
    if _threshold(text) is not None and _TIME_PATTERN.search(text) and (_DATE_PATTERN.search(text) or _YEAR_PATTERN.search(text)):
        return SHAPE_POINT_IN_TIME_THRESHOLD
    if _threshold(text) is not None and re.search(r"\b(by|before|deadline|end\s+of)\b", lowered):
        return SHAPE_DEADLINE_HIT_BY_DATE
    if _threshold(text) is not None and re.search(r"\b(above|below|greater|less|over|under)\b", lowered):
        return SHAPE_POINT_IN_TIME_THRESHOLD
    if re.search(r"\bwill\b", lowered) and re.search(r"\b(resolve|resolves|source|according)\b", rules_lower):
        return SHAPE_BINARY_EVENT_RESULT
    if family == FAMILY_POLITICS_NEWS_OR_POLICY:
        return SHAPE_YES_NO_NEWS_EVENT
    return SHAPE_UNKNOWN_OR_COMPOUND


def _typed_keys(
    *,
    family: str,
    shape: str,
    text: str,
    rules_text: str,
    event: dict[str, Any],
    market: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    keys: dict[str, Any] = {}
    sources: dict[str, str] = {}
    if family == FAMILY_CRYPTO:
        if asset := _crypto_asset(text):
            keys["asset"] = asset
            sources["asset"] = "slug_or_question"
        if threshold := _threshold(text):
            keys["threshold_value"] = threshold
            sources["threshold_value"] = "slug_or_question"
        if operator := _operator(text):
            keys["threshold_operator"] = operator
            sources["threshold_operator"] = "slug_or_question"
        if date := _date(text):
            keys["measurement_date"] = date
            sources["measurement_date"] = "slug_or_question"
        if time_text := _time(text):
            keys["measurement_time"] = time_text
            sources["measurement_time"] = "slug_or_question"
        if source := _settlement_source_from_rules(rules_text):
            keys["price_source_index"] = source
            sources["price_source_index"] = "rules_or_resolution_source"
        return keys, sources
    if family == FAMILY_POLITICS_ELECTION_RESULT:
        if office := _office(text):
            keys["office_or_contest"] = office
            sources["office_or_contest"] = "slug_or_question"
        if candidate := _candidate(text):
            keys["candidate_or_party"] = candidate
            sources["candidate_or_party"] = "slug_or_question"
        if date := _date(text) or _year(text):
            keys["election_date_or_cycle"] = date
            sources["election_date_or_cycle"] = "slug_or_question"
        if result_basis := _result_basis(rules_text):
            keys["result_basis"] = result_basis
            sources["result_basis"] = "rules_or_resolution_source"
        return keys, sources
    if family == FAMILY_MACRO_FED_RATES:
        if date := _date(text):
            keys["meeting_date"] = date
            sources["meeting_date"] = "slug_or_question"
        if threshold := _rate_threshold(text):
            keys["rate_bound"] = threshold
            sources["rate_bound"] = "slug_or_question"
        if operator := _operator(text):
            keys["operator"] = operator
            sources["operator"] = "slug_or_question"
        if source := _fed_source_convention(rules_text):
            keys["source_convention"] = source
            sources["source_convention"] = "rules_or_resolution_source"
        return keys, sources
    if family == FAMILY_SPORTS_GAME:
        if league := _league(text):
            keys["league"] = league
            sources["league"] = "slug_or_question"
        teams = _participants(event, market, text)
        if teams:
            keys["participants"] = teams
            sources["participants"] = "structured_fields_or_question"
        if date := _date(text):
            keys["game_date"] = date
            sources["game_date"] = "slug_or_question"
        if shape in {SHAPE_SPORTS_SPREAD, SHAPE_SPORTS_TOTAL}:
            if line := _line(text):
                keys["line"] = line
                sources["line"] = "slug_or_question"
        if re.search(r"\b(overtime|void|push|cancel)\b", rules_text, re.IGNORECASE):
            keys["void_or_overtime_rules_present"] = True
            sources["void_or_overtime_rules_present"] = "rules_or_resolution_source"
        keys["market_type"] = shape
        sources["market_type"] = "shape_classifier"
        return keys, sources
    if family in {FAMILY_TECH_AI, FAMILY_TECH_COMPANY_PRODUCT}:
        if entity := _tech_entity(text):
            keys["entity"] = entity
            sources["entity"] = "slug_or_question"
        if date := _date(text) or _year(text):
            keys["deadline_or_date"] = date
            sources["deadline_or_date"] = "slug_or_question"
        if rules_text:
            keys["rules_text_present"] = True
            sources["rules_text_present"] = "rules_or_resolution_source"
        return keys, sources
    return keys, sources


def _typed_key_complete(*, family: str, shape: str, typed_keys: dict[str, Any], blockers: list[str]) -> bool:
    if shape == SHAPE_UNKNOWN_OR_COMPOUND:
        return False
    if "unknown_source_or_rules" in blockers:
        return False
    required_by_family = {
        FAMILY_CRYPTO: {"asset", "threshold_value", "threshold_operator", "price_source_index"},
        FAMILY_POLITICS_ELECTION_RESULT: {"office_or_contest", "candidate_or_party", "result_basis"},
        FAMILY_MACRO_FED_RATES: {"meeting_date", "rate_bound", "operator", "source_convention"},
        FAMILY_SPORTS_GAME: {"league", "participants", "market_type"},
        FAMILY_TECH_AI: {"entity", "deadline_or_date", "rules_text_present"},
        FAMILY_TECH_COMPANY_PRODUCT: {"entity", "deadline_or_date", "rules_text_present"},
    }
    required = required_by_family.get(family)
    if not required:
        return False
    if shape in {SHAPE_POINT_IN_TIME_THRESHOLD, SHAPE_DEADLINE_HIT_BY_DATE} and family == FAMILY_CRYPTO:
        required = required | {"measurement_date"}
    if shape in {SHAPE_SPORTS_SPREAD, SHAPE_SPORTS_TOTAL}:
        required = required | {"line"}
    return required.issubset(typed_keys.keys())


def _blockers(*, family: str, shape: str, typed_keys: dict[str, Any], rules_text: str) -> list[str]:
    blockers: list[str] = []
    if not rules_text:
        blockers.append("missing_settlement_rules_text")
    if family == FAMILY_OTHER_UNKNOWN:
        blockers.append("unknown_family")
    if shape == SHAPE_UNKNOWN_OR_COMPOUND:
        blockers.append("unknown_or_compound_market_shape")
    if family in {FAMILY_CRYPTO, FAMILY_MACRO_FED_RATES, FAMILY_POLITICS_ELECTION_RESULT} and not _settlement_source_from_rules(rules_text):
        blockers.append("unknown_source_or_rules")
    if family == FAMILY_POLITICS_NEWS_OR_POLICY:
        blockers.append("vague_news_or_policy_discovery_only")
    if family in {FAMILY_CULTURE_ENTERTAINMENT, FAMILY_WEATHER}:
        blockers.append("reference_or_discovery_only_family")
    if family == FAMILY_SPORTS_GAME and "void_or_overtime_rules_present" not in typed_keys:
        blockers.append("missing_void_or_overtime_rules")
    if not typed_keys:
        blockers.append("typed_keys_missing")
    return sorted(set(blockers))


def _summary(
    *,
    rows: list[dict[str, Any]],
    endpoints_attempted: list[dict[str, Any]],
    raw_files_written: list[str],
    warnings: list[dict[str, Any]],
    books_saved: int,
) -> dict[str, Any]:
    by_family = Counter(str(row.get("family") or FAMILY_OTHER_UNKNOWN) for row in rows)
    by_shape = Counter(str(row.get("market_shape") or SHAPE_UNKNOWN_OR_COMPOUND) for row in rows)
    blockers = Counter(blocker for row in rows for blocker in row.get("blockers") or [])
    event_ids = {row.get("event_id") or row.get("event_slug") for row in rows if row.get("event_id") or row.get("event_slug")}
    market_ids = {row.get("market_id") or row.get("market_slug") or row.get("question") for row in rows if row.get("market_id") or row.get("market_slug") or row.get("question")}
    typed_complete = sum(1 for row in rows if row.get("typed_key_complete") is True)
    unknown_count = sum(1 for row in rows if row.get("family") == FAMILY_OTHER_UNKNOWN or row.get("market_shape") == SHAPE_UNKNOWN_OR_COMPOUND)
    return {
        "total_events": len(event_ids),
        "total_markets": len(market_ids),
        "taxonomy_rows": len(rows),
        "endpoints_attempted": len(endpoints_attempted),
        "raw_files_written": len(raw_files_written),
        "by_family": dict(sorted(by_family.items())),
        "by_market_shape": dict(sorted(by_shape.items())),
        "typed_key_complete_count": typed_complete,
        "partial_count": sum(1 for row in rows if row.get("typed_keys") and not row.get("typed_key_complete")),
        "unknown_count": unknown_count,
        "candidate_exact_review_families": sorted({row["family"] for row in rows if row.get("typed_key_complete") and row.get("family") in {FAMILY_CRYPTO, FAMILY_MACRO_FED_RATES, FAMILY_POLITICS_ELECTION_RESULT}}),
        "candidate_basis_risk_families": sorted({row["family"] for row in rows if row.get("family") in {FAMILY_CRYPTO, FAMILY_MACRO_FED_RATES} and row.get("typed_keys")}),
        "candidate_fv_watch_families": sorted({row["family"] for row in rows if row.get("family") in {FAMILY_CRYPTO, FAMILY_SPORTS_GAME, FAMILY_SPORTS_FUTURES} and row.get("typed_keys")}),
        "blockers_by_count": dict(sorted(blockers.items(), key=lambda item: (-item[1], item[0]))),
        "books_saved": books_saved,
        "warning_count": len(warnings),
        "paper_candidate_count": 0,
        "safe_to_normalize_count": 0,
    }


def _unknown_shape_clusters(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("market_shape") != SHAPE_UNKNOWN_OR_COMPOUND and row.get("family") != FAMILY_OTHER_UNKNOWN:
            continue
        pattern = _slug_pattern(row.get("market_slug") or row.get("event_slug") or row.get("question") or "unknown")
        clusters[pattern].append(row)
    results: list[dict[str, Any]] = []
    for pattern, cluster_rows in clusters.items():
        family_counts = Counter(str(row.get("family") or FAMILY_OTHER_UNKNOWN) for row in cluster_rows)
        family = family_counts.most_common(1)[0][0]
        examples = [
            {
                "slug": row.get("market_slug") or row.get("event_slug"),
                "question": row.get("question") or row.get("title"),
                "raw_source_file": row.get("raw_source_file"),
            }
            for row in cluster_rows[:3]
        ]
        results.append(
            {
                "pattern": pattern,
                "row_count": len(cluster_rows),
                "reason_unknown": ",".join(sorted({blocker for row in cluster_rows for blocker in row.get("blockers") or []})) or "unknown",
                "suggested_parser_family": family,
                "parser_priority": _parser_priority(family=family, row_count=len(cluster_rows), rules_present=any(row.get("settlement_rules_text_present") for row in cluster_rows)),
                "examples": examples,
            }
        )
    return sorted(results, key=lambda item: (-int(item["row_count"]), str(item["pattern"])))[:50]


def _high_value_parser_targets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("typed_key_complete"):
            continue
        grouped[(str(row.get("family") or FAMILY_OTHER_UNKNOWN), str(row.get("market_shape") or SHAPE_UNKNOWN_OR_COMPOUND))].append(row)
    results: list[dict[str, Any]] = []
    for (family, shape), group_rows in grouped.items():
        rules_present = any(row.get("settlement_rules_text_present") for row in group_rows)
        priority = _parser_priority(family=family, row_count=len(group_rows), rules_present=rules_present)
        usefulness = _family_usefulness(family)
        results.append(
            {
                "target": f"{family}:{shape}",
                "family": family,
                "market_shape": shape,
                "row_count": len(group_rows),
                "parser_priority": priority,
                "rules_present": rules_present,
                "family_usefulness_to_relative_value": usefulness,
                "likely_comparable_venues": _likely_comparable_venues(family),
                "fake_edge_risk": _fake_edge_risk(family, shape),
                "reason": f"{len(group_rows)} rows, {usefulness} usefulness, rules_present={str(rules_present).lower()}",
            }
        )
    priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    return sorted(results, key=lambda item: (priority_order.get(str(item["parser_priority"]), 9), -int(item["row_count"]), str(item["target"])))[:25]


def _records_from_response(payload: Any, *, endpoint_kind: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in (endpoint_kind, "data", "results", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    if endpoint_kind == "events" and isinstance(payload.get("event"), dict):
        return [payload["event"]]
    if endpoint_kind == "markets" and isinstance(payload.get("market"), dict):
        return [payload["market"]]
    if any(key in payload for key in ("id", "slug", "question", "title", "markets")):
        return [payload]
    return []


def _event_market_pairs(records: list[dict[str, Any]], *, endpoint_kind: str) -> list[tuple[dict[str, Any] | None, dict[str, Any] | None]]:
    pairs: list[tuple[dict[str, Any] | None, dict[str, Any] | None]] = []
    for record in records:
        markets = record.get("markets")
        if endpoint_kind == "events" and isinstance(markets, list) and markets:
            for market in markets:
                if isinstance(market, dict):
                    pairs.append((record, market))
            continue
        if endpoint_kind == "events":
            pairs.append((record, None))
            continue
        event = record.get("event") if isinstance(record.get("event"), dict) else None
        pairs.append((event, record))
    return pairs


def _combined_text(event: dict[str, Any], market: dict[str, Any]) -> str:
    values: list[str] = []
    for row in (event, market):
        for key in (
            "slug",
            "eventSlug",
            "event_slug",
            "title",
            "name",
            "question",
            "rules",
            "description",
            "resolutionSource",
            "resolution_source",
            "category",
            "tags",
            "outcomes",
        ):
            value = row.get(key)
            if isinstance(value, list):
                values.extend(str(item) for item in value if item is not None)
            elif value is not None:
                text = str(value).strip()
                if text:
                    values.append(text)
    return " ".join(values)


def _rules_text(event: dict[str, Any], market: dict[str, Any]) -> str:
    values: list[str] = []
    for row in (event, market):
        for key in ("rules", "description", "resolutionSource", "resolution_source"):
            value = _string_or_none(row.get(key))
            if value:
                values.append(value)
    return " ".join(values)


def _crypto_asset(text: str) -> str | None:
    match = _CRYPTO_ASSET_PATTERN.search(text)
    if not match:
        return None
    token = match.group(1).lower()
    if token in {"bitcoin", "btc"}:
        return "BTC"
    if token in {"ethereum", "eth"}:
        return "ETH"
    return token.upper()


def _threshold(text: str) -> float | None:
    for match in _THRESHOLD_PATTERN.finditer(text):
        raw = match.group(0)
        number_text = match.group(1).replace(",", "")
        try:
            number = float(number_text)
        except ValueError:
            continue
        suffix = (match.group(2) or "").lower()
        has_price_hint = "$" in raw or "," in raw or bool(suffix)
        if not has_price_hint and 1900 <= number <= 2100:
            continue
        if suffix == "k":
            number *= 1_000
        elif suffix == "m":
            number *= 1_000_000
        elif suffix == "b":
            number *= 1_000_000_000
        if number >= 0:
            return number
    return None


def _operator(text: str) -> str | None:
    lowered = text.lower()
    if re.search(r"\b(below|less\s+than|under|low)\b", lowered):
        return "<="
    if re.search(r"\b(above|greater\s+than|over|hit|reach|cross|high)\b", lowered):
        return ">="
    return None


def _date(text: str) -> str | None:
    match = _DATE_PATTERN.search(text)
    return match.group(0) if match else None


def _time(text: str) -> str | None:
    match = _TIME_PATTERN.search(text)
    if not match:
        return None
    return " ".join(part.strip().upper().replace(".", "") for part in match.groups())


def _year(text: str) -> str | None:
    match = _YEAR_PATTERN.search(text)
    return match.group(1) if match else None


def _settlement_source_from_rules(rules_text: str) -> str | None:
    lowered = rules_text.lower()
    if "binance" in lowered:
        return "Binance"
    if "chainlink" in lowered:
        return "Chainlink"
    if "cf benchmarks" in lowered or "brti" in lowered:
        return "CF Benchmarks / BRTI"
    if "associated press" in lowered or "ap " in lowered:
        return "Associated Press"
    if "federal reserve" in lowered or "fomc" in lowered:
        return "Federal Reserve / FOMC"
    if "official" in lowered and ("certif" in lowered or "source" in lowered):
        return "Official source described in rules"
    return None


def _explicit_source_url(rules_text: str) -> str | None:
    match = _URL_PATTERN.search(rules_text)
    return match.group(0) if match else None


def _office(text: str) -> str | None:
    lowered = text.lower()
    for value in ("president", "senate", "house", "governor", "mayor"):
        if value in lowered:
            return value.upper()
    return None


def _candidate(text: str) -> str | None:
    match = re.search(r"\b(?:will|can)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\s+(?:win|become|receive)\b", text)
    if match:
        return match.group(1)
    for name in ("Trump", "Biden", "Harris", "DeSantis"):
        if re.search(rf"\b{name}\b", text):
            return name
    return None


def _result_basis(rules_text: str) -> str | None:
    lowered = rules_text.lower()
    if "certified" in lowered:
        return "certified_result"
    if "associated press" in lowered or "ap" in lowered:
        return "ap_projection"
    if "inaugurat" in lowered:
        return "inauguration"
    if "nomination" in lowered:
        return "nomination_result"
    return None


def _rate_threshold(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if not match:
        return None
    return float(match.group(1))


def _fed_source_convention(rules_text: str) -> str | None:
    lowered = rules_text.lower()
    if "federal reserve" in lowered or "fomc" in lowered:
        return "Federal Reserve FOMC target range"
    return None


def _league(text: str) -> str | None:
    lowered = text.lower()
    for token, league in _SPORT_LEAGUES.items():
        if re.search(rf"\b{re.escape(token)}\b", lowered):
            return league
    return None


def _participants(event: dict[str, Any], market: dict[str, Any], text: str) -> list[str]:
    structured: list[str] = []
    for row in (event, market):
        for key in ("home_team", "away_team", "teamOneName", "teamTwoName"):
            value = _string_or_none(row.get(key))
            if value:
                structured.append(value)
    if structured:
        return structured
    match = re.search(r"\b([A-Z][A-Za-z .]+?)\s+(?:vs\.?|v\.?|at)\s+([A-Z][A-Za-z .]+?)\b", text)
    if match:
        return [match.group(1).strip(), match.group(2).strip()]
    return []


def _line(text: str) -> float | None:
    match = re.search(r"([+-]?\d+(?:\.\d+)?)", text)
    if not match:
        return None
    return float(match.group(1))


def _tech_entity(text: str) -> str | None:
    for entity in ("OpenAI", "ChatGPT", "GPT-5", "Anthropic", "Claude", "Apple", "Tesla", "Nvidia", "Microsoft", "Google", "Meta"):
        if re.search(rf"\b{re.escape(entity)}\b", text, re.IGNORECASE):
            return entity
    return None


def _token_ids(event: dict[str, Any], market: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for row in (market, event):
        for key in ("clobTokenIds", "clob_token_ids", "tokenIds", "token_ids", "tokens"):
            values.extend(_as_string_list(row.get(key)))
        for key in ("clobTokenId", "clob_token_id", "tokenId", "token_id"):
            value = _string_or_none(row.get(key))
            if value:
                values.append(value)
    unique: list[str] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return unique


def _as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            try:
                decoded = json.loads(stripped)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, list):
                return [_string_or_none(item) for item in decoded if _string_or_none(item)]
        return [stripped]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if isinstance(item, dict):
                for key in ("token_id", "tokenId", "id"):
                    value_text = _string_or_none(item.get(key))
                    if value_text:
                        result.append(value_text)
                        break
            elif _string_or_none(item):
                result.append(str(item).strip())
        return result
    return []


def _source_url(*, event_slug: str | None, market_slug: str | None) -> str | None:
    if event_slug:
        return f"https://polymarket.com/event/{event_slug}"
    if market_slug:
        return f"https://polymarket.com/market/{market_slug}"
    return None


def _parser_priority(*, family: str, row_count: int, rules_present: bool) -> str:
    if family in {FAMILY_CRYPTO, FAMILY_POLITICS_ELECTION_RESULT, FAMILY_SPORTS_GAME, FAMILY_MACRO_FED_RATES} and row_count >= 1:
        return "HIGH" if rules_present or family == FAMILY_SPORTS_GAME else "MEDIUM"
    if family in {FAMILY_TECH_AI, FAMILY_MACRO_ECONOMIC_RELEASE, FAMILY_SPORTS_FUTURES} and row_count >= 3:
        return "MEDIUM"
    if row_count >= 10:
        return "MEDIUM"
    return "LOW"


def _family_usefulness(family: str) -> str:
    if family in {FAMILY_CRYPTO, FAMILY_POLITICS_ELECTION_RESULT, FAMILY_SPORTS_GAME, FAMILY_MACRO_FED_RATES}:
        return "high"
    if family in {FAMILY_MACRO_ECONOMIC_RELEASE, FAMILY_SPORTS_FUTURES, FAMILY_TECH_AI}:
        return "medium"
    return "low"


def _likely_comparable_venues(family: str) -> list[str]:
    if family == FAMILY_CRYPTO:
        return ["kalshi", "crypto_com_predict_cdna"]
    if family in {FAMILY_SPORTS_GAME, FAMILY_SPORTS_FUTURES}:
        return ["kalshi", "sx_bet", "reference_odds"]
    if family == FAMILY_MACRO_FED_RATES:
        return ["kalshi"]
    if family == FAMILY_POLITICS_ELECTION_RESULT:
        return ["kalshi"]
    return []


def _fake_edge_risk(family: str, shape: str) -> str:
    if shape in {SHAPE_YES_NO_NEWS_EVENT, SHAPE_UNKNOWN_OR_COMPOUND}:
        return "high"
    if family in {FAMILY_CRYPTO, FAMILY_MACRO_FED_RATES, FAMILY_POLITICS_ELECTION_RESULT, FAMILY_SPORTS_GAME}:
        return "medium"
    return "high"


def _slug_pattern(value: str) -> str:
    text = value.lower()
    text = re.sub(r"\$?\d+(?:,\d{3})*(?:\.\d+)?[kmb]?", "<num>", text)
    text = re.sub(r"20\d{2}", "<year>", text)
    text = re.sub(r"[^a-z0-9<>]+", "-", text)
    return text.strip("-")[:96] or "unknown"


def _safety_block() -> dict[str, bool]:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "live_trading": False,
        "authenticated_endpoints_used": False,
        "orders_or_cancellations": False,
        "account_or_wallet_or_signing_code": False,
        "candidate_pair_creation": False,
        "paper_candidate_emitted": False,
    }


def _gamma_url(base_url: str, path: str, params: dict[str, str], limit: int, offset: int) -> str:
    query = dict(params)
    query["limit"] = str(limit)
    query["offset"] = str(offset)
    return f"{base_url.rstrip('/')}{path}?{urlencode(query)}"


def _clob_book_url(base_url: str, token_id: str) -> str:
    return f"{base_url.rstrip('/')}/book?{urlencode({'token_id': token_id})}"


def _default_http_get(url: str, timeout_seconds: float) -> Any:
    request = Request(url, headers=PUBLIC_READ_HEADERS, method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        raise RuntimeError(f"public Polymarket endpoint returned HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"public Polymarket endpoint failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("public Polymarket endpoint timed out") from exc
    return json.loads(payload)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _safe_slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-._")[:96] or "row"


def _count_table(counts: dict[str, Any]) -> list[str]:
    lines = ["| Key | Count |", "|---|---:|"]
    for key, count in sorted(counts.items(), key=lambda item: (-int(item[1]), str(item[0]))):
        lines.append(f"| {_md(key)} | {_md(count)} |")
    return lines


def _md(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "/")


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
