"""Polymarket public CLOB refresh-and-attach for top taxonomy-shape candidates.

Reads the saved polymarket_taxonomy_shape_scout JSON, selects top candidates by
shape priority + review-priority score, fetches public no-auth CLOB books via the
existing PolymarketOrderbookClient, saves raw responses, and attaches *explicit*
top-of-book quote fields (bid, ask, bid_size, ask_size, token_id, condition_id,
quote_timestamp) back to a per-row enriched payload.

Hard safety constraints respected by this module:
- Public-no-auth CLOB book endpoint only (PolymarketOrderbookClient.fetch_orderbook).
- Read-only: never imports or uses any write- or account-scoped endpoint.
- Never reads, requests, or persists secrets, keys, or seeds.
- No midpoint / last / probability / complement / title inference of bid or ask.
- Preserves the title-only-match-not-equivalence and registry-blocks-pair-creation
  guard blockers verbatim.
- Removes the missing-CLOB-book blocker only when the public endpoint returned a
  real response; removes the stale-or-missing-quote blocker only when we have a
  fresh observed_at timestamp AND at least one usable bid or ask.
- Diagnostic-only output. Never emits a paper-candidate or exact-payoff
  equivalence claim.
"""

from __future__ import annotations

import copy
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from venues.orderbooks import OrderbookClientError, PolymarketOrderbookClient


SCHEMA_VERSION = 1
SCHEMA_KIND = "polymarket_clob_taxonomy_refresh_v1"
REPORT_SOURCE = "polymarket_clob_taxonomy_refresh_v1"
ENRICHED_SCHEMA_KIND = "polymarket_taxonomy_shape_scout_enriched_v1"
ENRICHED_SOURCE = "polymarket_taxonomy_shape_scout_enriched_v1"
RAW_BOOK_SOURCE = "polymarket_clob_taxonomy_refresh_raw_book_v1"

SHAPE_POINT_IN_TIME = "point_in_time_threshold"
SHAPE_MACRO_RATE_MEETING = "macro_rate_meeting"
SHAPE_EVENT_WINNER = "event_winner"
SHAPE_SPORTS_FUTURES = "sports_futures"
DEFAULT_SHAPE_PRIORITY: tuple[str, ...] = (
    SHAPE_POINT_IN_TIME,
    SHAPE_MACRO_RATE_MEETING,
    SHAPE_EVENT_WINNER,
    SHAPE_SPORTS_FUTURES,
)
DEADLINE_RANGE_SHAPES = frozenset(
    {
        "deadline_threshold_touch",
        "range_hit",
        "range_bucket",
        "crypto_deadline_range_hit",
        "all_time_high_by_date",
    }
)

QUOTE_FRESH_DAYS = 5

# Blockers added per row when fields are absent after refresh.
B_PUBLIC_FETCH_FAILED = "polymarket_clob_public_fetch_failed"
B_EMPTY_BOOK = "polymarket_clob_empty_book"
B_MISSING_TOKEN_ID = "polymarket_missing_token_id"
B_MISSING_CONDITION_ID = "polymarket_missing_condition_id"
B_MISSING_BID = "polymarket_missing_bid"
B_MISSING_ASK = "polymarket_missing_ask"
B_MISSING_BID_SIZE = "polymarket_missing_bid_size"
B_MISSING_ASK_SIZE = "polymarket_missing_ask_size"
B_MISSING_QUOTE_TIMESTAMP = "polymarket_missing_quote_timestamp"

# Blockers we may downgrade when an actual book is attached.
B_MISSING_CLOB_BOOK = "missing_clob_book"
B_STALE_OR_MISSING_QUOTE = "stale_or_missing_quote"

# Blockers that must persist regardless of book attachment.
PRESERVED_BLOCKERS = frozenset(
    {
        "title_only_match_not_equivalence",
        "polymarket_registry_blocks_pair_creation_until_review",
    }
)


HttpBookFetcher = Callable[[str], Any]


def refresh_polymarket_clob_for_taxonomy_candidates(
    *,
    taxonomy_json: Path,
    output_dir: Path,
    max_candidates: int = 200,
    shape_filter: str | None = SHAPE_POINT_IN_TIME,
    min_score: float = 30.0,
    include_deadline_range: bool = False,
    timeout_seconds: float = 10.0,
    generated_at: datetime | None = None,
    fetch_book: HttpBookFetcher | None = None,
    book_client: PolymarketOrderbookClient | None = None,
) -> dict[str, Any]:
    """Refresh public-no-auth CLOB books for the top taxonomy candidates.

    Returns a dict with two payloads:
      - ``report``: the refresh report (counts, per-row attachments, blockers).
      - ``enriched``: a copy of the scout JSON whose rows have refreshed
        CLOB quote fields and recomputed blockers merged in.
    Both payloads are diagnostic-only; neither emits paper candidates or exact
    equivalences.
    """
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    if max_candidates <= 0:
        raise ValueError("max_candidates must be positive")
    if min_score < 0:
        raise ValueError("min_score must be non-negative")

    scout_payload = _load_scout(taxonomy_json)
    rows_in: list[dict[str, Any]] = (
        scout_payload.get("rows") if isinstance(scout_payload.get("rows"), list) else []
    )
    selected, excluded_by_reason = _select_candidates(
        rows_in,
        max_candidates=max_candidates,
        shape_filter=shape_filter,
        min_score=min_score,
        include_deadline_range=include_deadline_range,
    )

    client = book_client or PolymarketOrderbookClient(timeout_seconds=timeout_seconds)
    fetcher = fetch_book if fetch_book is not None else (lambda token_id: client.fetch_orderbook(token_id))

    timestamp = generated.strftime("%Y%m%d_%H%M%SZ")
    snapshot_dir = output_dir / timestamp
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    refresh_rows: list[dict[str, Any]] = []
    enriched_by_row_id: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, Any]] = []
    books_requested = 0
    books_saved = 0

    for tax_row in selected:
        outcome = _refresh_one_row(
            tax_row=tax_row,
            snapshot_dir=snapshot_dir,
            generated=generated,
            fetch_book=fetcher,
            client=client,
            warnings=warnings,
        )
        refresh_rows.append(outcome["refresh_row"])
        enriched_by_row_id[outcome["row_id"]] = outcome["enriched_row"]
        books_requested += outcome["books_requested"]
        books_saved += outcome["books_saved"]

    summary = _summary(
        refresh_rows=refresh_rows,
        selected_count=len(selected),
        books_requested=books_requested,
        books_saved=books_saved,
    )
    enriched_payload = _enriched_payload(
        scout_payload=scout_payload,
        enriched_by_row_id=enriched_by_row_id,
        generated=generated,
        taxonomy_json=taxonomy_json,
        refresh_summary=summary,
    )

    report = {
        "schema_kind": SCHEMA_KIND,
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "taxonomy_json": str(taxonomy_json),
        "output_dir": str(output_dir),
        "snapshot_dir": str(snapshot_dir),
        "max_candidates": int(max_candidates),
        "shape_filter": shape_filter,
        "min_score": float(min_score),
        "include_deadline_range": bool(include_deadline_range),
        "diagnostic_only": True,
        "summary": summary,
        "rows": refresh_rows,
        "excluded_by_reason": dict(sorted(excluded_by_reason.items())),
        "warnings": warnings,
        "safety": _safety_block(),
    }
    return {"report": report, "enriched": enriched_payload}


def write_polymarket_clob_taxonomy_refresh_files(
    *,
    taxonomy_json: Path,
    output_dir: Path,
    json_output: Path,
    enriched_output: Path,
    markdown_output: Path,
    max_candidates: int = 200,
    shape_filter: str | None = SHAPE_POINT_IN_TIME,
    min_score: float = 30.0,
    include_deadline_range: bool = False,
    timeout_seconds: float = 10.0,
    generated_at: datetime | None = None,
    fetch_book: HttpBookFetcher | None = None,
    book_client: PolymarketOrderbookClient | None = None,
) -> dict[str, Any]:
    bundle = refresh_polymarket_clob_for_taxonomy_candidates(
        taxonomy_json=taxonomy_json,
        output_dir=output_dir,
        max_candidates=max_candidates,
        shape_filter=shape_filter,
        min_score=min_score,
        include_deadline_range=include_deadline_range,
        timeout_seconds=timeout_seconds,
        generated_at=generated_at,
        fetch_book=fetch_book,
        book_client=book_client,
    )
    report = bundle["report"]
    enriched_payload = bundle["enriched"]
    json_output.parent.mkdir(parents=True, exist_ok=True)
    enriched_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    enriched_output.write_text(json.dumps(enriched_payload, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_polymarket_clob_taxonomy_refresh_markdown(report), encoding="utf-8")
    return bundle


def render_polymarket_clob_taxonomy_refresh_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    rows = report.get("rows") or []
    lines: list[str] = [
        "# Polymarket CLOB Refresh For Taxonomy Candidates",
        "",
        "Public no-auth Polymarket CLOB book refresh for top taxonomy-shape candidates. "
        "Diagnostic-only: no candidate-pair creation, no paper actions, no exact-payoff equivalence. "
        "Bid/ask/sizes are read directly from each token's `bids`/`asks` arrays — never inferred "
        "from midpoint, last, probability, complement math, or title similarity.",
        "",
        "## Summary",
        "",
        f"- taxonomy_json: `{report.get('taxonomy_json')}`",
        f"- snapshot_dir: `{report.get('snapshot_dir')}`",
        f"- shape_filter: `{report.get('shape_filter')}`",
        f"- min_score: `{report.get('min_score')}`",
        f"- max_candidates: `{report.get('max_candidates')}`",
        f"- include_deadline_range: `{str(bool(report.get('include_deadline_range'))).lower()}`",
        f"- candidates_selected: `{summary.get('candidates_selected', 0)}`",
        f"- books_requested: `{summary.get('books_requested', 0)}`",
        f"- books_saved: `{summary.get('books_saved', 0)}`",
        f"- rows_enriched: `{summary.get('rows_enriched', 0)}`",
        f"- rows_with_bid: `{summary.get('rows_with_bid', 0)}`",
        f"- rows_with_ask: `{summary.get('rows_with_ask', 0)}`",
        f"- rows_with_bid_ask: `{summary.get('rows_with_bid_ask', 0)}`",
        f"- rows_with_bid_ask_size: `{summary.get('rows_with_bid_ask_size', 0)}`",
        f"- rows_with_timestamp: `{summary.get('rows_with_timestamp', 0)}`",
        f"- still_missing_clob: `{summary.get('still_missing_clob', 0)}`",
        f"- still_stale_or_missing_quote: `{summary.get('still_stale_or_missing_quote', 0)}`",
        f"- exact_ready_rows: `0`",
        f"- paper_candidate_rows: `0`",
        "",
        "## Top 10 Enriched Candidates",
        "",
        "| # | Score | Shape | Family | Bid | Ask | BidSize | AskSize | ObservedAt | Row ID | Question |",
        "|---:|---:|---|---|---:|---:|---:|---:|---|---|---|",
    ]
    enriched_sorted = [r for r in rows if r.get("clob_book_attached_now")]
    enriched_sorted.sort(key=lambda r: -(_float_or_zero(r.get("review_priority_score"))))
    if not enriched_sorted:
        lines.append("| _none_ |  |  |  |  |  |  |  |  |  |  |")
    else:
        for i, row in enumerate(enriched_sorted[:10], start=1):
            quote = row.get("attached_quote") or {}
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(i),
                        f"{_float_or_zero(row.get('review_priority_score')):.1f}",
                        _md_cell(row.get("market_shape")),
                        _md_cell(row.get("family")),
                        _md_cell(_quote_display(quote.get("bid"))),
                        _md_cell(_quote_display(quote.get("ask"))),
                        _md_cell(_quote_display(quote.get("bid_size"))),
                        _md_cell(_quote_display(quote.get("ask_size"))),
                        _md_cell(quote.get("observed_at") or ""),
                        _md_cell(row.get("row_id")),
                        _md_cell((row.get("question") or "")[:80]),
                    ]
                )
                + " |"
            )
    lines.extend(["", "## Top Remaining Blockers", "", "| Blocker | Count |", "|---|---:|"])
    for item in summary.get("top_remaining_blockers") or []:
        lines.append(f"| {item.get('blocker')} | {item.get('count')} |")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- diagnostic_only: `true`",
            "- public_no_auth_only: `true`",
            "- private_or_auth_endpoint_used: `false`",
            "- order_or_cancel_or_fill_logic_added: `false`",
            "- wallet_or_signing_or_account_logic_added: `false`",
            "- can_create_candidate_pair: `false`",
            "- can_create_paper_candidate: `false`",
            "- exact_ready: `false`",
            "- inferred_bid_or_ask_from_midpoint_or_complement: `false`",
            "- preserved_blockers: `title_only_match_not_equivalence,polymarket_registry_blocks_pair_creation_until_review`",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Loading + selection
# ---------------------------------------------------------------------------


def _load_scout(taxonomy_json: Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(taxonomy_json).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"taxonomy scout JSON not found: {taxonomy_json}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"taxonomy scout JSON invalid: {taxonomy_json}") from exc
    if not isinstance(payload, dict):
        raise ValueError("taxonomy scout JSON must be an object")
    return payload


def _select_candidates(
    rows: list[dict[str, Any]],
    *,
    max_candidates: int,
    shape_filter: str | None,
    min_score: float,
    include_deadline_range: bool,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    excluded: Counter[str] = Counter()
    eligible: list[dict[str, Any]] = []
    normalized_filter = (shape_filter or "").strip().lower()
    if normalized_filter in {"", "all", "any"}:
        normalized_filter = ""
    for row in rows:
        if not isinstance(row, dict):
            excluded["non_dict_row"] += 1
            continue
        shape = (row.get("market_shape") or "").strip().lower()
        score = _float_or_zero(row.get("exact_matchability_score"))
        if shape in DEADLINE_RANGE_SHAPES and not include_deadline_range:
            excluded["deadline_or_range_hit_or_bucket_excluded_by_default"] += 1
            continue
        if normalized_filter and shape != normalized_filter:
            excluded[f"shape_filter_excluded:{shape or 'unknown'}"] += 1
            continue
        if score < min_score:
            excluded["below_min_score"] += 1
            continue
        eligible.append(row)
    eligible.sort(
        key=lambda r: (
            _shape_priority(r.get("market_shape")),
            -_float_or_zero(r.get("exact_matchability_score")),
            str(r.get("row_id") or ""),
        )
    )
    return eligible[: int(max_candidates)], excluded


def _shape_priority(shape: Any) -> int:
    text = (shape or "").strip().lower()
    try:
        return DEFAULT_SHAPE_PRIORITY.index(text)
    except ValueError:
        return len(DEFAULT_SHAPE_PRIORITY) + 1


# ---------------------------------------------------------------------------
# Per-row refresh
# ---------------------------------------------------------------------------


def _refresh_one_row(
    *,
    tax_row: dict[str, Any],
    snapshot_dir: Path,
    generated: datetime,
    fetch_book: HttpBookFetcher,
    client: PolymarketOrderbookClient,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    row_id = str(tax_row.get("row_id") or "")
    token_ids = _token_id_list(tax_row.get("token_ids"))
    condition_id = _string_or_none(tax_row.get("condition_id"))
    market_id = _string_or_none(tax_row.get("market_id"))

    raw_books: dict[str, dict[str, Any]] = {}
    raw_book_files: dict[str, str] = {}
    book_fetch_failures: list[dict[str, Any]] = []
    books_requested = 0
    books_saved = 0

    if not token_ids:
        added_blockers = [B_MISSING_TOKEN_ID]
    else:
        for token_id in token_ids:
            books_requested += 1
            url = client.endpoint_for(token_id)
            try:
                raw = fetch_book(token_id)
            except OrderbookClientError as exc:
                book_fetch_failures.append(
                    {
                        "token_id": token_id,
                        "url": url,
                        "error_class": type(exc).__name__,
                        "error_message": str(exc),
                    }
                )
                warnings.append(
                    {
                        "row_id": row_id,
                        "token_id": token_id,
                        "url": url,
                        "reason_code": "public_clob_book_request_failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            except (TypeError, ValueError) as exc:
                book_fetch_failures.append(
                    {
                        "token_id": token_id,
                        "url": url,
                        "error_class": type(exc).__name__,
                        "error_message": str(exc),
                    }
                )
                warnings.append(
                    {
                        "row_id": row_id,
                        "token_id": token_id,
                        "url": url,
                        "reason_code": "public_clob_book_parse_failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            raw_books[token_id] = raw if isinstance(raw, dict) else {"_payload": raw}
            book_file = snapshot_dir / f"book_{_safe_slug(token_id)}.json"
            book_file.write_text(
                json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "source": RAW_BOOK_SOURCE,
                        "endpoint_name": "clob_book",
                        "url": url,
                        "token_id": token_id,
                        "row_id": row_id,
                        "market_id": market_id,
                        "condition_id": condition_id,
                        "captured_at": generated.isoformat(),
                        "public_no_auth": True,
                        "raw_response": raw,
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            books_saved += 1
            raw_book_files[token_id] = str(book_file)
        added_blockers = []

    primary_token_id = token_ids[0] if token_ids else None
    primary_book = raw_books.get(primary_token_id) if primary_token_id else None
    primary_book_file = raw_book_files.get(primary_token_id) if primary_token_id else None
    attached_quote = _attach_explicit_quote(
        raw_book=primary_book,
        token_id=primary_token_id,
        condition_id=condition_id,
        observed_at=generated,
        raw_book_file=primary_book_file,
    )

    blockers_in = list(tax_row.get("blockers") or [])
    blockers_out = _recompute_blockers(
        blockers_in=blockers_in,
        attached_quote=attached_quote,
        added_blockers=added_blockers,
        token_ids=token_ids,
        condition_id=condition_id,
        book_fetch_failures=book_fetch_failures,
        primary_book=primary_book,
        now=generated,
    )

    enriched_row = copy.deepcopy(tax_row)
    clob_refresh_block = {
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "refreshed_at": generated.isoformat(),
        "primary_token_id": primary_token_id,
        "token_ids_requested": list(token_ids),
        "books_requested": books_requested,
        "books_saved": books_saved,
        "raw_book_files_by_token_id": raw_book_files,
        "book_fetch_failures": book_fetch_failures,
        "attached_quote": attached_quote,
        "preserved_blockers": sorted(b for b in blockers_out if b in PRESERVED_BLOCKERS),
    }
    enriched_row["clob_refresh"] = clob_refresh_block
    enriched_row["blockers"] = blockers_out
    has_usable_quote = (
        attached_quote.get("bid") is not None or attached_quote.get("ask") is not None
    )
    enriched_row["clob_book_attached"] = (
        attached_quote.get("attached") is True and has_usable_quote
    )
    enriched_row["clob_book_fresh"] = (
        attached_quote.get("attached") is True
        and has_usable_quote
        and attached_quote.get("observed_at") is not None
    )
    # Diagnostic safety flags must remain false even after enrichment.
    for safety_key in (
        "can_create_candidate_pair",
        "can_create_paper_candidate",
        "exact_ready",
        "execution_ready",
        "paper_candidate",
        "affects_evaluator_gates",
        "source_exact_payoff_compatible_with_kalshi",
    ):
        enriched_row[safety_key] = False

    refresh_row = {
        "row_id": row_id or None,
        "market_id": market_id,
        "condition_id": condition_id,
        "question": tax_row.get("question"),
        "title": tax_row.get("title"),
        "family": tax_row.get("family"),
        "market_shape": tax_row.get("market_shape"),
        "review_priority_score": _float_or_zero(tax_row.get("exact_matchability_score")),
        "token_ids": list(token_ids),
        "primary_token_id": primary_token_id,
        "books_requested": books_requested,
        "books_saved": books_saved,
        "raw_book_files_by_token_id": raw_book_files,
        "book_fetch_failures": book_fetch_failures,
        "attached_quote": attached_quote,
        "book_response_received": bool(attached_quote.get("attached")),
        "clob_book_attached_now": bool(
            attached_quote.get("attached") and has_usable_quote
        ),
        "blockers_before": blockers_in,
        "blockers_after": blockers_out,
        "preserved_blockers": sorted(b for b in blockers_out if b in PRESERVED_BLOCKERS),
        "downgraded_blockers": sorted(set(blockers_in) - set(blockers_out)),
        "new_blockers": sorted(set(blockers_out) - set(blockers_in)),
        "diagnostic_only": True,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "exact_ready": False,
        "execution_ready": False,
        "paper_candidate": False,
    }
    return {
        "row_id": row_id,
        "refresh_row": refresh_row,
        "enriched_row": enriched_row,
        "books_requested": books_requested,
        "books_saved": books_saved,
    }


def _attach_explicit_quote(
    *,
    raw_book: dict[str, Any] | None,
    token_id: str | None,
    condition_id: str | None,
    observed_at: datetime,
    raw_book_file: str | None,
) -> dict[str, Any]:
    """Read explicit top-of-book fields directly from raw_book; never infer."""
    quote: dict[str, Any] = {
        "attached": False,
        "token_id": token_id,
        "condition_id": condition_id,
        "bid": None,
        "ask": None,
        "bid_size": None,
        "ask_size": None,
        "observed_at": None,
        "quote_timestamp": None,
        "raw_book_file": raw_book_file,
        "raw_book_source_timestamp": None,
        "empty_book": False,
        "missing_book": raw_book is None,
        "inferred_from_midpoint_or_complement": False,
    }
    if raw_book is None or not isinstance(raw_book, dict):
        return quote
    quote["attached"] = True
    bid_level = _top_level(raw_book.get("bids"), is_bid=True)
    ask_level = _top_level(raw_book.get("asks"), is_bid=False)
    if bid_level is None and ask_level is None:
        quote["empty_book"] = True
    if bid_level is not None:
        quote["bid"] = bid_level["price"]
        quote["bid_size"] = bid_level["size"]
    if ask_level is not None:
        quote["ask"] = ask_level["price"]
        quote["ask_size"] = ask_level["size"]
    quote["observed_at"] = observed_at.isoformat()
    quote["quote_timestamp"] = observed_at.isoformat()
    raw_ts = raw_book.get("timestamp") or raw_book.get("captured_at")
    if isinstance(raw_ts, str) and raw_ts.strip():
        quote["raw_book_source_timestamp"] = raw_ts.strip()
    return quote


def _top_level(levels: Any, *, is_bid: bool) -> dict[str, Any] | None:
    """Return the explicit best level from a Polymarket CLOB ``bids``/``asks`` array.

    Picks the explicit price/size on the level with the best price (max for bids,
    min for asks). Never derives a level from midpoint, last, or complement.
    Returns ``None`` if no valid level is present.
    """
    if not isinstance(levels, list):
        return None
    parsed: list[dict[str, float]] = []
    for level in levels:
        if not isinstance(level, dict):
            continue
        price = _float_or_none(level.get("price"))
        size = _float_or_none(level.get("size"))
        if price is None or size is None:
            continue
        if price < 0 or size < 0:
            continue
        parsed.append({"price": float(price), "size": float(size)})
    if not parsed:
        return None
    parsed.sort(key=lambda lv: lv["price"], reverse=is_bid)
    return parsed[0]


def _recompute_blockers(
    *,
    blockers_in: list[Any],
    attached_quote: dict[str, Any],
    added_blockers: list[str],
    token_ids: list[str],
    condition_id: str | None,
    book_fetch_failures: list[dict[str, Any]],
    primary_book: dict[str, Any] | None,
    now: datetime,
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    # Always preserve required guard blockers, and bring forward all other input blockers.
    for blocker in blockers_in:
        if not isinstance(blocker, str):
            continue
        if blocker in {B_MISSING_CLOB_BOOK, B_STALE_OR_MISSING_QUOTE}:
            # Carry through; we may remove these below if conditions are met.
            if blocker not in seen:
                out.append(blocker)
                seen.add(blocker)
            continue
        if blocker not in seen:
            out.append(blocker)
            seen.add(blocker)
    for blocker in added_blockers:
        if blocker not in seen:
            out.append(blocker)
            seen.add(blocker)

    # missing_clob_book may be downgraded once the public endpoint returned a real response
    # (even an empty book — that case is reported via the polymarket_clob_empty_book blocker).
    book_response_received = bool(attached_quote.get("attached"))
    if book_response_received and B_MISSING_CLOB_BOOK in seen:
        out = [b for b in out if b != B_MISSING_CLOB_BOOK]
        seen.discard(B_MISSING_CLOB_BOOK)

    # stale_or_missing_quote may only be downgraded when we have a fresh observed_at AND
    # a usable bid or ask (anything else is not a usable quote).
    observed_at = attached_quote.get("observed_at")
    fresh = _quote_is_fresh(observed_at, now=now)
    has_usable_quote = (
        attached_quote.get("bid") is not None or attached_quote.get("ask") is not None
    )
    if book_response_received and fresh and has_usable_quote and B_STALE_OR_MISSING_QUOTE in seen:
        out = [b for b in out if b != B_STALE_OR_MISSING_QUOTE]
        seen.discard(B_STALE_OR_MISSING_QUOTE)

    # New per-row blockers added based on what was actually attached.
    def _add(blocker: str) -> None:
        if blocker not in seen:
            out.append(blocker)
            seen.add(blocker)

    if book_fetch_failures and (primary_book is None):
        _add(B_PUBLIC_FETCH_FAILED)
    if primary_book is not None and attached_quote.get("empty_book"):
        _add(B_EMPTY_BOOK)
    if not token_ids:
        _add(B_MISSING_TOKEN_ID)
    if not condition_id:
        _add(B_MISSING_CONDITION_ID)
    if attached_quote.get("bid") is None:
        _add(B_MISSING_BID)
    if attached_quote.get("ask") is None:
        _add(B_MISSING_ASK)
    if attached_quote.get("bid_size") is None:
        _add(B_MISSING_BID_SIZE)
    if attached_quote.get("ask_size") is None:
        _add(B_MISSING_ASK_SIZE)
    if not observed_at:
        _add(B_MISSING_QUOTE_TIMESTAMP)

    # If no response was received at all, re-assert missing_clob_book + stale_or_missing_quote.
    if not book_response_received:
        _add(B_MISSING_CLOB_BOOK)
        _add(B_STALE_OR_MISSING_QUOTE)
    elif not has_usable_quote and B_STALE_OR_MISSING_QUOTE not in seen:
        # Got a response but no usable quote: the stale-or-missing-quote blocker still applies.
        _add(B_STALE_OR_MISSING_QUOTE)

    # Belt-and-suspenders: ensure preserved blockers are present when they were before.
    for preserved in PRESERVED_BLOCKERS:
        if preserved in blockers_in and preserved not in seen:
            _add(preserved)
    return out


def _quote_is_fresh(observed_at: Any, *, now: datetime) -> bool:
    if not isinstance(observed_at, str) or not observed_at.strip():
        return False
    try:
        parsed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (now - parsed) <= timedelta(days=QUOTE_FRESH_DAYS)


# ---------------------------------------------------------------------------
# Enriched payload
# ---------------------------------------------------------------------------


def _enriched_payload(
    *,
    scout_payload: dict[str, Any],
    enriched_by_row_id: dict[str, dict[str, Any]],
    generated: datetime,
    taxonomy_json: Path,
    refresh_summary: dict[str, Any],
) -> dict[str, Any]:
    enriched = copy.deepcopy(scout_payload)
    enriched_rows: list[dict[str, Any]] = []
    rows = enriched.get("rows") if isinstance(enriched.get("rows"), list) else []
    upgraded_clob_attached = 0
    upgraded_clob_fresh = 0
    for row in rows:
        row_id = str(row.get("row_id") or "")
        if row_id in enriched_by_row_id:
            new_row = enriched_by_row_id[row_id]
            if new_row.get("clob_book_attached"):
                upgraded_clob_attached += 1
            if new_row.get("clob_book_fresh"):
                upgraded_clob_fresh += 1
            enriched_rows.append(new_row)
        else:
            enriched_rows.append(row)
    enriched["rows"] = enriched_rows
    enriched["schema_kind"] = ENRICHED_SCHEMA_KIND
    enriched["source"] = ENRICHED_SOURCE
    enriched["enriched_generated_at"] = generated.isoformat()
    enriched["taxonomy_json"] = str(taxonomy_json)
    enriched["clob_refresh"] = {
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "candidates_refreshed": refresh_summary.get("candidates_selected", 0),
        "rows_enriched": refresh_summary.get("rows_enriched", 0),
        "rows_with_bid": refresh_summary.get("rows_with_bid", 0),
        "rows_with_ask": refresh_summary.get("rows_with_ask", 0),
        "rows_with_bid_ask": refresh_summary.get("rows_with_bid_ask", 0),
        "rows_with_bid_ask_size": refresh_summary.get("rows_with_bid_ask_size", 0),
        "rows_with_timestamp": refresh_summary.get("rows_with_timestamp", 0),
        "books_requested": refresh_summary.get("books_requested", 0),
        "books_saved": refresh_summary.get("books_saved", 0),
        "upgraded_clob_attached_count": upgraded_clob_attached,
        "upgraded_clob_fresh_count": upgraded_clob_fresh,
    }
    # Recompute summary headline counters from enriched rows where possible.
    summary = enriched.get("summary") if isinstance(enriched.get("summary"), dict) else {}
    summary["clob_book_attached"] = sum(
        1 for r in enriched_rows if isinstance(r, dict) and r.get("clob_book_attached")
    )
    summary["clob_book_fresh"] = sum(
        1 for r in enriched_rows if isinstance(r, dict) and r.get("clob_book_fresh")
    )
    summary["exact_ready_rows"] = 0
    summary["paper_candidate_rows"] = 0
    summary["execution_ready_rows"] = 0
    enriched["summary"] = summary
    # Re-affirm safety block.
    enriched_safety = enriched.get("safety") if isinstance(enriched.get("safety"), dict) else {}
    enriched_safety.update(
        {
            "diagnostic_only": True,
            "saved_files_only": False,
            "live_fetch_attempted": True,
            "live_fetch_scope": "polymarket_public_clob_book_only_no_auth",
            "execution_or_order_logic_added": False,
            "account_or_auth_logic_added": False,
            "can_create_candidate_pair": False,
            "can_create_paper_candidate": False,
            "exact_ready": False,
            "execution_ready": False,
            "paper_candidate": False,
            "affects_evaluator_gates": False,
            "source_registry_unchanged": True,
            "source_exact_payoff_compatible_with_kalshi": False,
            "inferred_bid_or_ask_from_midpoint_or_complement": False,
        }
    )
    enriched["safety"] = enriched_safety
    return enriched


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _summary(
    *,
    refresh_rows: list[dict[str, Any]],
    selected_count: int,
    books_requested: int,
    books_saved: int,
) -> dict[str, Any]:
    rows_enriched = 0
    rows_with_bid = 0
    rows_with_ask = 0
    rows_with_bid_ask = 0
    rows_with_bid_ask_size = 0
    rows_with_timestamp = 0
    still_missing_clob = 0
    still_stale = 0
    remaining_blockers: Counter[str] = Counter()
    for row in refresh_rows:
        quote = row.get("attached_quote") or {}
        attached = bool(row.get("clob_book_attached_now"))
        if attached:
            rows_enriched += 1
        if quote.get("bid") is not None:
            rows_with_bid += 1
        if quote.get("ask") is not None:
            rows_with_ask += 1
        if quote.get("bid") is not None and quote.get("ask") is not None:
            rows_with_bid_ask += 1
        if (
            quote.get("bid") is not None
            and quote.get("ask") is not None
            and quote.get("bid_size") is not None
            and quote.get("ask_size") is not None
        ):
            rows_with_bid_ask_size += 1
        if quote.get("observed_at"):
            rows_with_timestamp += 1
        blockers_after = list(row.get("blockers_after") or [])
        if B_MISSING_CLOB_BOOK in blockers_after:
            still_missing_clob += 1
        if B_STALE_OR_MISSING_QUOTE in blockers_after:
            still_stale += 1
        for blocker in blockers_after:
            if isinstance(blocker, str) and blocker.strip():
                remaining_blockers[blocker] += 1
    top_remaining = [
        {"blocker": blocker, "count": count}
        for blocker, count in remaining_blockers.most_common(15)
    ]
    return {
        "candidates_selected": int(selected_count),
        "books_requested": int(books_requested),
        "books_saved": int(books_saved),
        "rows_enriched": int(rows_enriched),
        "rows_with_bid": int(rows_with_bid),
        "rows_with_ask": int(rows_with_ask),
        "rows_with_bid_ask": int(rows_with_bid_ask),
        "rows_with_bid_ask_size": int(rows_with_bid_ask_size),
        "rows_with_timestamp": int(rows_with_timestamp),
        "still_missing_clob": int(still_missing_clob),
        "still_stale_or_missing_quote": int(still_stale),
        "exact_ready_rows": 0,
        "paper_candidate_rows": 0,
        "execution_ready_rows": 0,
        "top_remaining_blockers": top_remaining,
    }


def _safety_block() -> dict[str, Any]:
    return {
        "diagnostic_only": True,
        "public_no_auth_only": True,
        "private_or_auth_endpoint_used": False,
        "order_or_cancel_or_fill_logic_added": False,
        "wallet_or_signing_or_account_logic_added": False,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "exact_ready": False,
        "execution_ready": False,
        "paper_candidate": False,
        "affects_evaluator_gates": False,
        "source_registry_unchanged": True,
        "source_exact_payoff_compatible_with_kalshi": False,
        "inferred_bid_or_ask_from_midpoint_or_complement": False,
        "preserves_title_only_match_not_equivalence": True,
        "preserves_polymarket_registry_pair_creation_blocker": True,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _token_id_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = _string_or_none(item)
        if text:
            out.append(text)
    return out


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _float_or_zero(value: Any) -> float:
    parsed = _float_or_none(value)
    return parsed if parsed is not None else 0.0


def _safe_slug(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in str(value))
    cleaned = cleaned.strip("-._")
    return cleaned[:64] or "token"


def _md_cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "/").replace("\n", " ")


def _quote_display(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".") or "0"
    return str(value)


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
