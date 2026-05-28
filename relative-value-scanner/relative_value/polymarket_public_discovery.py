from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SCHEMA_VERSION = 1
REPORT_SOURCE = "polymarket_crypto_public_discovery_v1"
RAW_RESPONSE_SOURCE = "polymarket_crypto_public_discovery_raw_response_v1"
CANDIDATE_SOURCE = "polymarket_crypto_public_discovery_candidate_v1"

GAMMA_API_BASE_URL = "https://gamma-api.polymarket.com"
CLOB_API_BASE_URL = "https://clob.polymarket.com"

DEFAULT_USER_AGENT = "relative-value-scanner/0.1 public-read-only"
PUBLIC_READ_HEADERS = {
    "Accept": "application/json",
    "User-Agent": DEFAULT_USER_AGENT,
}

CRYPTO_ASSET_PATTERN = re.compile(r"\b(bitcoin|btc|ethereum|eth)\b", re.IGNORECASE)
THRESHOLD_TERM_PATTERN = re.compile(
    r"\b(above|below|hit|reach|cross|price|high|low|greater|less|up|down)\b",
    re.IGNORECASE,
)
EXPLICIT_NUMERIC_PATTERN = re.compile(r"(?:\$?\d+(?:\.\d+)?\s*(?:k|m|b)\b|\$\s*\d+(?:\.\d+)?|\d+(?:,\d{3})+)", re.IGNORECASE)
EXCLUDE_PATTERN = re.compile(
    r"\b(microstrategy|el[-\s]?salvador|reserve|gta|album|trump|company|treasury|adoption)\b",
    re.IGNORECASE,
)
HARD_COMPOUND_CONTEXT_PATTERN = re.compile(
    r"\b(gta|album|microstrategy|el[-\s]?salvador|trump|company|treasury|adoption|"
    r"bitcoin\s+knots|bitcoin\s+core|nodes?|new\s+country\s+buy|country\s+buy|"
    r"updown|5m|all[-\s]?time[-\s]?high|"
    r"best\s+performance|percentage\s+change|vs\.?\s+gold|sp[-\s]?500|s&p\s+500)\b",
    re.IGNORECASE,
)
UP_DOWN_PATTERN = re.compile(r"\b(?:up[-\s]?or[-\s]?down|up\s+or\s+down)\b", re.IGNORECASE)
PRICE_RULE_PATTERN = re.compile(
    r"\b(?:btc|bitcoin|eth|ethereum)\b.*\b(?:price|usdt|usd|dollar|candle|index|oracle|spot)\b",
    re.IGNORECASE | re.DOTALL,
)

TARGETED_SEARCH_TERMS = (
    "bitcoin up or down",
    "btc up or down",
    "ethereum up or down",
    "eth up or down",
    "bitcoin above on",
    "ethereum above on",
    "btc above on",
    "eth above on",
    "bitcoin above 1am",
    "bitcoin above 12am",
    "bitcoin hit before",
    "ethereum hit before",
    "what price will bitcoin hit",
    "what price will ethereum hit",
)

SEED_EVENT_URLS = (
    "https://polymarket.com/event/bitcoin-up-or-down-may-26-2026-12am-et",
    "https://polymarket.com/event/bitcoin-above-on-may-26-2026-1am-et",
    "https://polymarket.com/event/bitcoin-above-on-may-26-2026",
    "https://polymarket.com/event/what-price-will-bitcoin-hit-in-may-2026",
    "https://polymarket.com/event/what-price-will-bitcoin-hit-before-2027",
    "https://polymarket.com/event/what-price-will-ethereum-hit-before-2027",
)

HttpGet = Callable[[str], Any]


def build_polymarket_crypto_discovery_report(
    *,
    output_dir: Path,
    limit: int = 200,
    include_books: bool = False,
    generated_at: datetime | None = None,
    timeout_seconds: float = 10.0,
    gamma_base_url: str = GAMMA_API_BASE_URL,
    clob_base_url: str = CLOB_API_BASE_URL,
    http_get: Callable[[str, float], Any] | None = None,
    max_pages: int = 3,
    targeted_query: str | None = None,
    targeted_queries: list[str] | None = None,
    targeted_asset: str | None = None,
    targeted_target_date: str | None = None,
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
    targeted_filter_active = bool(
        targeted_query or targeted_queries or targeted_asset or targeted_target_date
    )

    endpoints_attempted: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    raw_files_written: list[str] = []
    candidate_rows: list[dict[str, Any]] = []
    excluded_reasons: Counter[str] = Counter()
    seen_candidate_keys: set[tuple[str | None, str | None, str | None]] = set()
    books_saved = 0
    book_files_attempted: list[dict[str, Any]] = []

    for seed_url in SEED_EVENT_URLS:
        slug = seed_url.rstrip("/").rsplit("/", 1)[-1]
        row = _seed_candidate_row(seed_url=seed_url, event_slug=slug, discovered_at=generated, snapshot_dir=snapshot_dir, index=len(candidate_rows))
        key = (row.get("event_slug"), row.get("market_slug"), row.get("market_id"))
        if key in seen_candidate_keys:
            continue
        seen_candidate_keys.add(key)
        candidate_file = snapshot_dir / f"candidate_{len(candidate_rows):04d}_{_safe_slug(slug)}.json"
        _write_json(
            candidate_file,
            {
                "schema_version": SCHEMA_VERSION,
                "source": CANDIDATE_SOURCE,
                "discovered_at": generated.isoformat(),
                "candidate": row,
                "raw_event": {"slug": slug, "source_url": seed_url, "seed_only": True},
                "raw_market": None,
            },
        )
        row["candidate_file"] = str(candidate_file)
        candidate_rows.append(row)

    endpoint_specs = (
        _targeted_endpoint_specs(
            query=targeted_query,
            queries=targeted_queries,
            asset=targeted_asset,
            target_date=targeted_target_date,
        )
        if targeted_filter_active
        else _endpoint_specs()
    )
    for endpoint in endpoint_specs:
        page_limit = limit
        for page in range(max_pages):
            offset = page * page_limit
            url = _gamma_url(gamma_base_url, endpoint["path"], endpoint["params"], page_limit, offset)
            endpoint_attempt = {
                "endpoint_name": endpoint["name"],
                "url": url,
                "page": page,
                "offset": offset,
                "public_no_auth": True,
            }
            endpoints_attempted.append(endpoint_attempt)
            try:
                payload = getter(url, timeout_seconds)
            except Exception as exc:  # pragma: no cover - exact network exceptions vary by platform
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
                    "url": url,
                    "captured_at": generated.isoformat(),
                    "public_no_auth": True,
                    "raw_response": payload,
                },
            )
            raw_files_written.append(str(raw_file))

            records = _records_from_response(payload, endpoint_kind=endpoint["kind"])
            endpoint_attempt["record_count"] = len(records)
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
                decision = _candidate_decision(event, market)
                if not decision["candidate"]:
                    excluded_reasons.update(decision["reasons"] or ["not_crypto_threshold_like"])
                    continue
                if targeted_filter_active and not _targeted_client_side_filter_passes(
                    event=event,
                    market=market,
                    asset=targeted_asset,
                    target_date=targeted_target_date,
                ):
                    excluded_reasons["targeted_client_side_filter_excluded"] += 1
                    continue
                key = (
                    _string_or_none(event.get("slug") if event else None),
                    _string_or_none(market.get("slug") if market else None),
                    _string_or_none(market.get("id") if market else None),
                )
                if key in seen_candidate_keys:
                    continue
                seen_candidate_keys.add(key)
                row = _candidate_row(
                    event=event,
                    market=market,
                    discovered_at=generated,
                    snapshot_dir=snapshot_dir,
                    index=len(candidate_rows),
                    reasons=decision["reasons"],
                )
                candidate_file = snapshot_dir / f"candidate_{len(candidate_rows):04d}_{_safe_slug(row.get('market_slug') or row.get('event_slug') or 'candidate')}.json"
                _write_json(
                    candidate_file,
                    {
                        "schema_version": SCHEMA_VERSION,
                        "source": CANDIDATE_SOURCE,
                        "discovered_at": generated.isoformat(),
                        "candidate": row,
                        "raw_event": event,
                        "raw_market": market,
                    },
                )
                row["candidate_file"] = str(candidate_file)
                candidate_rows.append(row)

                row_book_files: dict[str, str] = {}
                if include_books:
                    for token_id in row.get("token_ids") or []:
                        book_url = _clob_book_url(clob_base_url, token_id)
                        try:
                            book_payload = getter(book_url, timeout_seconds)
                        except Exception as exc:  # pragma: no cover - exact network exceptions vary by platform
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
                                    "candidate_row_index": row.get("row_index"),
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
                        books_saved += 1
                        raw_files_written.append(str(book_file))
                        row_book_files[token_id] = str(book_file)
                        book_files_attempted.append(
                            {
                                "token_id": token_id,
                                "url": book_url,
                                "status": "saved",
                                "book_file": str(book_file),
                                "candidate_row_index": row.get("row_index"),
                            }
                        )
                # Always include the diagnostic key — empty dict when --include-books is off
                # or when every CLOB book request failed — so downstream readers can rely
                # on the key being present.
                row["book_files_by_token_id"] = row_book_files

            if len(records) < page_limit:
                break

    summary = _summary(
        endpoints_attempted=endpoints_attempted,
        raw_files_written=raw_files_written,
        candidates=candidate_rows,
        excluded_reasons=excluded_reasons,
        books_saved=books_saved,
        warnings=warnings,
        book_files_attempted=book_files_attempted,
    )
    targeted_block = _targeted_block(
        active=targeted_filter_active,
        query=targeted_query,
        queries=targeted_queries,
        asset=targeted_asset,
        target_date=targeted_target_date,
        candidates=candidate_rows,
    )
    summary["targeted_filter_active"] = targeted_filter_active
    summary["targeted_query"] = targeted_query
    summary["targeted_queries"] = list(targeted_queries) if targeted_queries else None
    summary["targeted_asset"] = targeted_asset
    summary["targeted_target_date"] = targeted_target_date
    summary["targeted_filter_mode"] = targeted_block["targeted_filter_mode"]
    summary["targeted_rows_found"] = targeted_block["rows_found"]
    summary["targeted_point_in_time_rows"] = targeted_block["point_in_time_rows"]
    summary["targeted_deadline_or_range_hit_rows"] = targeted_block["deadline_or_range_hit_rows"]
    summary["targeted_typed_rows"] = targeted_block["typed_rows"]
    summary["targeted_rows_with_token_ids"] = targeted_block["rows_with_token_ids"]
    summary["targeted_rows_with_condition_ids"] = targeted_block["rows_with_condition_ids"]
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
        "candidates": candidate_rows,
        "excluded_candidates_by_reason": dict(sorted(excluded_reasons.items())),
        "raw_files_written": raw_files_written,
        "book_files_attempted": book_files_attempted,
        "warnings": warnings,
        "targeted_filter": targeted_block,
        "safety": {
            "public_no_auth_only": True,
            "live_trading": False,
            "authenticated_endpoints_used": False,
            "orders_or_cancellations": False,
            "account_or_wallet_or_signing_code": False,
            "candidate_pair_creation": False,
            "paper_candidate_emitted": False,
            "affects_evaluator_gates": False,
            "treats_title_similarity_as_settlement_equivalence": False,
            "treats_deadline_or_range_hit_as_point_in_time": False,
        },
    }


def write_polymarket_crypto_discovery_files(
    *,
    output_dir: Path,
    json_output: Path,
    markdown_output: Path,
    limit: int = 200,
    include_books: bool = False,
    generated_at: datetime | None = None,
    timeout_seconds: float = 10.0,
    max_pages: int = 3,
    targeted_query: str | None = None,
    targeted_queries: list[str] | None = None,
    targeted_asset: str | None = None,
    targeted_target_date: str | None = None,
) -> dict[str, Any]:
    report = build_polymarket_crypto_discovery_report(
        output_dir=output_dir,
        limit=limit,
        include_books=include_books,
        generated_at=generated_at,
        timeout_seconds=timeout_seconds,
        max_pages=max_pages,
        targeted_query=targeted_query,
        targeted_queries=targeted_queries,
        targeted_asset=targeted_asset,
        targeted_target_date=targeted_target_date,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.write_text(render_polymarket_crypto_discovery_markdown(report), encoding="utf-8")
    return report


def render_polymarket_crypto_discovery_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Polymarket Crypto Public Discovery",
        "",
        "Public no-auth Gamma/CLOB read-only discovery. This report does not create candidate pairs and does not affect evaluator gates.",
        "",
        "## Summary",
        "",
        f"- endpoints_attempted: `{summary.get('endpoints_attempted', 0)}`",
        f"- raw_files_written: `{summary.get('raw_files_written', 0)}`",
        f"- candidate_events: `{summary.get('candidate_events', 0)}`",
        f"- candidate_markets: `{summary.get('candidate_markets', 0)}`",
        f"- threshold_like_candidates: `{summary.get('threshold_like_candidates', 0)}`",
        f"- token_ids_available: `{summary.get('token_ids_available', 0)}`",
        f"- books_saved: `{summary.get('books_saved', 0)}`",
        f"- warnings: `{summary.get('warning_count', 0)}`",
        "",
        "## Candidates",
        "",
        "| Slug | Title/question | Token IDs | Source URL | Reasons |",
        "|---|---|---:|---|---|",
    ]
    for row in report.get("candidates") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("market_slug") or row.get("event_slug")),
                    _md(row.get("title") or row.get("question")),
                    _md(len(row.get("token_ids") or [])),
                    _md(row.get("source_url")),
                    _md(",".join(row.get("candidate_reasons") or [])),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Safety",
            "",
            f"- live_trading: `{str((report.get('safety') or {}).get('live_trading')).lower()}`",
            f"- authenticated_endpoints_used: `{str((report.get('safety') or {}).get('authenticated_endpoints_used')).lower()}`",
            f"- orders_or_cancellations: `{str((report.get('safety') or {}).get('orders_or_cancellations')).lower()}`",
            f"- candidate_pair_creation: `{str((report.get('safety') or {}).get('candidate_pair_creation')).lower()}`",
            f"- paper_candidate_emitted: `{str((report.get('safety') or {}).get('paper_candidate_emitted')).lower()}`",
        ]
    )
    return "\n".join(lines) + "\n"


def _endpoint_specs() -> list[dict[str, Any]]:
    specs = [
        {
            "name": "gamma_markets_active",
            "kind": "markets",
            "path": "/markets",
            "params": {"active": "true", "closed": "false"},
        },
        {
            "name": "gamma_events_active",
            "kind": "events",
            "path": "/events",
            "params": {"active": "true", "closed": "false"},
        },
        {
            "name": "gamma_events_crypto_tag",
            "kind": "events",
            "path": "/events",
            "params": {"active": "true", "closed": "false", "tag_slug": "crypto"},
        },
        {
            "name": "gamma_markets_crypto_tag",
            "kind": "markets",
            "path": "/markets",
            "params": {"active": "true", "closed": "false", "tag_slug": "crypto"},
        },
    ]
    for index, term in enumerate(TARGETED_SEARCH_TERMS):
        safe_name = _safe_slug(term).replace("-", "_")
        specs.append(
            {
                "name": f"gamma_events_search_{index:02d}_{safe_name}",
                "kind": "events",
                "path": "/events",
                "params": {"active": "true", "closed": "false", "search": term},
            }
        )
        specs.append(
            {
                "name": f"gamma_markets_search_{index:02d}_{safe_name}",
                "kind": "markets",
                "path": "/markets",
                "params": {"active": "true", "closed": "false", "search": term},
            }
        )
    return specs


def _targeted_endpoint_specs(
    *,
    query: str | None,
    queries: list[str] | None,
    asset: str | None,
    target_date: str | None,
) -> list[dict[str, Any]]:
    terms: list[str] = []
    if query:
        terms.append(query.strip())
    if queries:
        for q in queries:
            if isinstance(q, str) and q.strip():
                terms.append(q.strip())
    if not terms:
        # Synthesize a search term from asset + target_date if provided explicitly.
        if asset and target_date:
            asset_word = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}.get(
                asset.upper(), asset.lower()
            )
            terms.append(f"{asset_word} {target_date}")
        elif asset:
            asset_word = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}.get(
                asset.upper(), asset.lower()
            )
            terms.append(asset_word)
    if not terms:
        # No usable targeted term; fall back to a single broad crypto-tag query.
        terms.append("crypto")
    specs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, term in enumerate(terms):
        if term in seen:
            continue
        seen.add(term)
        safe_name = _safe_slug(term).replace("-", "_")
        specs.append(
            {
                "name": f"gamma_events_targeted_{index:02d}_{safe_name}",
                "kind": "events",
                "path": "/events",
                "params": {"active": "true", "closed": "false", "search": term},
            }
        )
        specs.append(
            {
                "name": f"gamma_markets_targeted_{index:02d}_{safe_name}",
                "kind": "markets",
                "path": "/markets",
                "params": {"active": "true", "closed": "false", "search": term},
            }
        )
    return specs


_TARGETED_ASSET_TOKENS: dict[str, tuple[str, ...]] = {
    "BTC": ("bitcoin", "btc"),
    "ETH": ("ethereum", "eth"),
    "SOL": ("solana", "sol"),
}


def _targeted_client_side_filter_passes(
    *,
    event: dict[str, Any] | None,
    market: dict[str, Any] | None,
    asset: str | None,
    target_date: str | None,
) -> bool:
    text = _combined_text(event or {}, market or {}).lower()
    if asset:
        tokens = _TARGETED_ASSET_TOKENS.get(asset.upper(), (asset.lower(),))
        if not any(token in text for token in tokens):
            return False
    if target_date:
        for variant in _date_variants(target_date):
            if variant.lower() in text:
                return True
        return False
    return True


def _date_variants(target_date: str) -> list[str]:
    """Return a small list of free-form date renderings used to filter PM titles.

    Only variants explicitly derivable from the input are returned; nothing is
    inferred or guessed.
    """
    variants: set[str] = set()
    text = (target_date or "").strip()
    if not text:
        return []
    variants.add(text)
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        parsed = None
    if parsed is not None:
        variants.add(parsed.strftime("%B %d, %Y"))
        variants.add(parsed.strftime("%b %d, %Y"))
        variants.add(parsed.strftime("%d %B %Y"))
        variants.add(parsed.strftime("%m/%d/%Y"))
        # Cross-platform "no leading zeros" rendering (strftime("%-m/%-d/%Y") is POSIX-only).
        variants.add(f"{parsed.month}/{parsed.day}/{parsed.year}")
        # Common alternates: "May 29" + "May 29 2026" (no comma) for slug-style titles.
        variants.add(parsed.strftime("%B %d"))
        variants.add(parsed.strftime("%B %d %Y"))
    return list(variants)


def _targeted_block(
    *,
    active: bool,
    query: str | None,
    queries: list[str] | None,
    asset: str | None,
    target_date: str | None,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    rows_with_token_ids = 0
    rows_with_condition_ids = 0
    point_in_time_rows = 0
    deadline_or_range_hit_rows = 0
    typed_rows = 0
    for row in candidates:
        token_ids = row.get("token_ids") if isinstance(row.get("token_ids"), list) else []
        if token_ids:
            rows_with_token_ids += 1
        condition_or_market = row.get("condition_id") or row.get("market_id")
        if condition_or_market:
            rows_with_condition_ids += 1
        reasons = set(row.get("candidate_reasons") or [])
        if "up_down_shape" in reasons or _looks_deadline_or_range_in_text(row):
            deadline_or_range_hit_rows += 1
        elif "explicit_price_threshold_rules" in reasons or "numeric_threshold" in reasons:
            point_in_time_rows += 1
        if "explicit_price_threshold_rules" in reasons:
            typed_rows += 1
    return {
        "targeted_filter_mode": (
            "client_side_public_discovery_plus_server_search"
            if active
            else "off"
        ),
        "active": active,
        "query": query,
        "queries": list(queries) if queries else None,
        "asset": asset,
        "target_date": target_date,
        "rows_found": len(candidates),
        "rows_with_token_ids": rows_with_token_ids,
        "rows_with_condition_ids": rows_with_condition_ids,
        "point_in_time_rows": point_in_time_rows,
        "deadline_or_range_hit_rows": deadline_or_range_hit_rows,
        "typed_rows": typed_rows,
        "deadline_or_range_hit_treated_as_point_in_time": False,
    }


def _looks_deadline_or_range_in_text(row: dict[str, Any]) -> bool:
    text = " ".join(
        str(row.get(key) or "")
        for key in ("title", "question", "rules", "description")
    ).lower()
    if not text:
        return False
    return any(
        token in text
        for token in (
            "touch",
            "touches",
            "hit before",
            "any time before",
            "by the end of",
            "ends in",
            "between $",
            "between ",
            "range",
        )
    )


def _gamma_url(base_url: str, path: str, params: dict[str, str], limit: int, offset: int) -> str:
    query = dict(params)
    query["limit"] = str(limit)
    query["offset"] = str(offset)
    return f"{base_url.rstrip('/')}{path}?{urlencode(query)}"


def _clob_book_url(base_url: str, token_id: str) -> str:
    return f"{base_url.rstrip('/')}/book?{urlencode({'token_id': token_id})}"


def _default_http_get(url: str, timeout_seconds: float) -> Any:
    request = Request(
        url,
        headers=PUBLIC_READ_HEADERS,
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        raise RuntimeError(f"public Polymarket endpoint returned HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"public Polymarket endpoint failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("public Polymarket endpoint timed out") from exc
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("public Polymarket endpoint returned invalid JSON") from exc


def _records_from_response(payload: Any, *, endpoint_kind: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    preferred = ("markets",) if endpoint_kind == "markets" else ("events",)
    for key in (*preferred, "data", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    if endpoint_kind == "markets" and _looks_like_market(payload):
        return [payload]
    if endpoint_kind == "events" and _looks_like_event(payload):
        return [payload]
    return []


def _event_market_pairs(records: list[dict[str, Any]], *, endpoint_kind: str) -> list[tuple[dict[str, Any] | None, dict[str, Any] | None]]:
    pairs: list[tuple[dict[str, Any] | None, dict[str, Any] | None]] = []
    if endpoint_kind == "markets":
        for market in records:
            event = market.get("event") if isinstance(market.get("event"), dict) else None
            pairs.append((event, market))
        return pairs
    for event in records:
        markets = event.get("markets")
        if isinstance(markets, list) and markets:
            for market in markets:
                if isinstance(market, dict):
                    pairs.append((event, market))
        else:
            pairs.append((event, None))
    return pairs


def _candidate_decision(event: dict[str, Any] | None, market: dict[str, Any] | None) -> dict[str, Any]:
    event = event or {}
    market = market or {}
    text = _combined_text(event, market)
    rules_text = _rules_text(event, market)
    reasons: list[str] = []
    if not CRYPTO_ASSET_PATTERN.search(text):
        return {"candidate": False, "reasons": ["missing_crypto_asset_term"]}
    reasons.append("crypto_asset_term")
    if not THRESHOLD_TERM_PATTERN.search(text):
        return {"candidate": False, "reasons": ["missing_threshold_term"]}
    reasons.append("threshold_term")
    up_down = bool(UP_DOWN_PATTERN.search(text))
    if not EXPLICIT_NUMERIC_PATTERN.search(text) and not up_down:
        return {"candidate": False, "reasons": ["missing_numeric_threshold"]}
    reasons.append("up_down_shape" if up_down else "numeric_threshold")
    if HARD_COMPOUND_CONTEXT_PATTERN.search(text):
        return {"candidate": False, "reasons": ["excluded_compound_or_non_price_market"]}
    if EXCLUDE_PATTERN.search(text) and not _explicit_price_threshold_rules(rules_text):
        return {"candidate": False, "reasons": ["excluded_compound_or_non_price_market"]}
    if _explicit_price_threshold_rules(rules_text):
        reasons.append("explicit_price_threshold_rules")
    return {"candidate": True, "reasons": reasons}


def _seed_candidate_row(
    *,
    seed_url: str,
    event_slug: str,
    discovered_at: datetime,
    snapshot_dir: Path,
    index: int,
) -> dict[str, Any]:
    title = event_slug.replace("-", " ")
    return {
        "row_index": index,
        "venue": "polymarket",
        "event_id": event_slug,
        "market_id": None,
        "condition_id": None,
        "event_slug": event_slug,
        "market_slug": event_slug,
        "title": title,
        "question": title,
        "rules": None,
        "description": None,
        "resolution_source": None,
        "token_ids": [],
        "token_ids_available": False,
        "source_url": seed_url,
        "discovered_at": discovered_at.isoformat(),
        "raw_snapshot_dir": str(snapshot_dir),
        "candidate_reasons": ["seed_event_url", "requires_public_gamma_or_saved_page_rules_for_settlement_source"],
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
    }


def _candidate_row(
    *,
    event: dict[str, Any] | None,
    market: dict[str, Any] | None,
    discovered_at: datetime,
    snapshot_dir: Path,
    index: int,
    reasons: list[str],
) -> dict[str, Any]:
    event = event or {}
    market = market or {}
    event_slug = _string_or_none(event.get("slug") or market.get("eventSlug") or market.get("event_slug"))
    market_slug = _string_or_none(market.get("slug"))
    title = _string_or_none(event.get("title") or event.get("name") or market.get("title"))
    question = _string_or_none(market.get("question") or event.get("question"))
    token_ids = _token_ids(event, market)
    source_url = _source_url(event_slug=event_slug, market_slug=market_slug)
    return {
        "row_index": index,
        "venue": "polymarket",
        "event_id": _string_or_none(event.get("id") or market.get("eventId")),
        "market_id": _string_or_none(market.get("id") or market.get("conditionId")),
        "condition_id": _string_or_none(market.get("conditionId")),
        "event_slug": event_slug,
        "market_slug": market_slug,
        "title": title,
        "question": question,
        "rules": _string_or_none(market.get("rules") or event.get("rules")),
        "description": _string_or_none(market.get("description") or event.get("description")),
        "resolution_source": _string_or_none(
            market.get("resolutionSource")
            or market.get("resolution_source")
            or event.get("resolutionSource")
            or event.get("resolution_source")
        ),
        "token_ids": token_ids,
        "token_ids_available": bool(token_ids),
        "source_url": source_url,
        "discovered_at": discovered_at.isoformat(),
        "raw_snapshot_dir": str(snapshot_dir),
        "candidate_reasons": reasons,
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
    }


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
        ):
            value = _string_or_none(row.get(key))
            if value:
                values.append(value)
        outcomes = row.get("outcomes")
        if isinstance(outcomes, str):
            values.append(outcomes)
        elif isinstance(outcomes, list):
            values.extend(str(item) for item in outcomes if item is not None)
    return " ".join(values)


def _rules_text(event: dict[str, Any], market: dict[str, Any]) -> str:
    values = []
    for row in (event, market):
        for key in ("rules", "description", "resolutionSource", "resolution_source"):
            value = _string_or_none(row.get(key))
            if value:
                values.append(value)
    return " ".join(values)


def _explicit_price_threshold_rules(rules_text: str) -> bool:
    return bool(
        rules_text
        and CRYPTO_ASSET_PATTERN.search(rules_text)
        and THRESHOLD_TERM_PATTERN.search(rules_text)
        and EXPLICIT_NUMERIC_PATTERN.search(rules_text)
        and PRICE_RULE_PATTERN.search(rules_text)
    )


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
        return [_string_or_none(item) for item in value if _string_or_none(item)]
    return []


def _source_url(*, event_slug: str | None, market_slug: str | None) -> str | None:
    if event_slug:
        return f"https://polymarket.com/event/{event_slug}"
    if market_slug:
        return f"https://polymarket.com/market/{market_slug}"
    return None


def _summary(
    *,
    endpoints_attempted: list[dict[str, Any]],
    raw_files_written: list[str],
    candidates: list[dict[str, Any]],
    excluded_reasons: Counter[str],
    books_saved: int,
    warnings: list[dict[str, Any]],
    book_files_attempted: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    event_slugs = {row.get("event_slug") for row in candidates if row.get("event_slug")}
    market_ids = {
        row.get("market_id") or row.get("market_slug") or row.get("question")
        for row in candidates
        if row.get("market_id") or row.get("market_slug") or row.get("question")
    }
    book_files_attempted = book_files_attempted or []
    book_token_ids_saved = {
        item.get("token_id") for item in book_files_attempted if item.get("status") == "saved"
    }
    book_token_ids_failed = {
        item.get("token_id") for item in book_files_attempted if item.get("status") == "failed"
    }
    candidates_with_any_book = sum(
        1 for row in candidates if any((row.get("book_files_by_token_id") or {}).values())
    )
    candidates_with_all_books = sum(
        1
        for row in candidates
        if (row.get("token_ids") or [])
        and all(
            token_id in (row.get("book_files_by_token_id") or {})
            for token_id in row.get("token_ids") or []
        )
    )
    return {
        "endpoints_attempted": len(endpoints_attempted),
        "raw_files_written": len(raw_files_written),
        "candidate_events": len(event_slugs),
        "candidate_markets": len(market_ids),
        "threshold_like_candidates": len(candidates),
        "excluded_candidates_by_reason": dict(sorted(excluded_reasons.items())),
        "token_ids_available": sum(1 for row in candidates if row.get("token_ids")),
        "books_saved": books_saved,
        "seed_url_candidates": sum(1 for row in candidates if "seed_event_url" in (row.get("candidate_reasons") or [])),
        "targeted_query_count": len(TARGETED_SEARCH_TERMS),
        "book_token_ids_saved_count": len(book_token_ids_saved),
        "book_token_ids_failed_count": len(book_token_ids_failed),
        "candidates_with_any_book_attached_count": candidates_with_any_book,
        "candidates_with_all_books_attached_count": candidates_with_all_books,
        "warning_count": len(warnings),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _safe_slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-._")[:64] or "row"


def _md(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "/")


def _looks_like_market(row: dict[str, Any]) -> bool:
    return any(key in row for key in ("conditionId", "question", "clobTokenIds", "outcomes"))


def _looks_like_event(row: dict[str, Any]) -> bool:
    return any(key in row for key in ("slug", "title", "markets"))


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
