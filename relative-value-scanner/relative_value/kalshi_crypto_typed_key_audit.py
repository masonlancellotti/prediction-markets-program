"""Saved-file-only audit of Kalshi crypto price-threshold typed keys.

Reads saved normalized Kalshi rows, extracts explicit typed keys (asset,
threshold, comparator, target date / time / timezone, settlement source +
URL), classifies contract shape (point-in-time threshold vs deadline /
range-hit / range-bucket / ambiguous), tags per-row blockers, and emits
diagnostic peer hints against saved CDNA and Polymarket point-in-time
crypto rows.

Hard safety constraints respected by this module:
- Saved files only; no live API calls, no auth, no orders, no fills,
  no cancels, no account/balance/positions, no wallet/signing/private
  keys, no browser automation, no geolocation/proxy/VPN/Tor/Cloudflare
  bypass.
- Never infers target time, settlement source, threshold, comparator,
  asset, or quote from missing data; absence is reported as a blocker.
- Never treats title similarity as settlement equivalence.
- Never treats deadline / range-hit / range-bucket rows as point-in-time.
- Never claims exact same-payoff equivalence with a CDNA or Polymarket
  peer; peer-hint fields are diagnostic only.
- Never emits a paper-candidate or exact-ready row.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
SCHEMA_KIND = "kalshi_crypto_typed_key_audit_v1"
REPORT_SOURCE = "kalshi_crypto_typed_key_audit_v1"

NORMALIZED_INPUT_FILE = "normalized_markets_v0.json"
CDNA_INPUT_FILE = "crypto_com_predict_cdna_research_snapshot.json"
POLYMARKET_PIT_INPUT_FILE = "polymarket_point_in_time_typed_key_audit.json"
KALSHI_FRESH_CRYPTO_INPUT_FILE = Path("live_readonly") / "crypto" / "kalshi_live_readonly_snapshot.json"
KALSHI_ORDERBOOK_ENRICHED_GLOBS = (
    "*kalshi*orderbook*enriched*.json",
    "*kalshi_orderbook_enriched*.json",
)

# Shape vocabulary.
SHAPE_POINT_IN_TIME = "point_in_time_threshold"
SHAPE_DEADLINE_TOUCH = "deadline_threshold_touch"
SHAPE_RANGE_HIT = "range_hit"
SHAPE_RANGE_BUCKET = "range_bucket"
SHAPE_AMBIGUOUS = "ambiguous"

# Peer hint vocabulary.
HINT_POSSIBLE_CDNA_PEER = "possible_cdna_peer"
HINT_POSSIBLE_POLYMARKET_PEER = "possible_polymarket_peer"
HINT_NO_SAVED_PEER = "no_saved_peer"

# Blocker vocabulary.
B_MISSING_ASSET = "kalshi_crypto_missing_asset"
B_MISSING_THRESHOLD = "kalshi_crypto_missing_threshold"
B_MISSING_COMPARATOR = "kalshi_crypto_missing_comparator"
B_MISSING_TARGET_DATE = "kalshi_crypto_missing_target_date"
B_MISSING_TARGET_TIME = "kalshi_crypto_missing_target_time"
B_MISSING_TIMEZONE = "kalshi_crypto_missing_timezone"
B_MISSING_SETTLEMENT_SOURCE = "kalshi_crypto_missing_settlement_source"
B_AMBIGUOUS_SHAPE = "kalshi_crypto_ambiguous_contract_shape"
B_DEADLINE_NOT_POINT_IN_TIME = "kalshi_crypto_deadline_not_point_in_time"
B_STALE_OR_MISSING_QUOTE = "stale_or_missing_quote"
B_MISSING_QUOTE = "missing_quote"
B_STALE_TOP_OF_BOOK = "stale_top_of_book"
B_FULL_ORDERBOOK_MISSING = "full_orderbook_missing"
B_KALSHI_LIVE_ORDERBOOK_FETCH_NOT_ENABLED_OR_MISSING = "kalshi_live_orderbook_fetch_not_enabled_or_missing"

# Ticker prefixes that strongly identify a saved Kalshi crypto market.
_CRYPTO_TICKER_PREFIXES: tuple[tuple[str, str], ...] = (
    ("KXBTCD", "BTC"),
    ("KXBTC", "BTC"),
    ("KXETHD", "ETH"),
    ("KXETH", "ETH"),
    ("KXSOLD", "SOL"),
    ("KXSOL", "SOL"),
)

# Title keywords that fall back when the ticker prefix is non-standard.
_CRYPTO_TITLE_TOKENS: tuple[tuple[str, str], ...] = (
    ("bitcoin", "BTC"),
    ("ethereum", "ETH"),
    ("solana", "SOL"),
)

# Known settlement-source markers in Kalshi crypto rules text. Detection is
# explicit; absence is reported as a blocker rather than guessed.
_SETTLEMENT_SOURCE_MARKERS: tuple[tuple[str, str], ...] = (
    ("cf benchmarks' bitcoin real-time index (brti)", "CF Benchmarks BRTI"),
    ("cf benchmarks' bitcoin real-time index", "CF Benchmarks BRTI"),
    ("cf benchmarks brti", "CF Benchmarks BRTI"),
    ("cf benchmarks' ethereum real-time index (erti)", "CF Benchmarks ERTI"),
    ("cf benchmarks' ethereum real-time index", "CF Benchmarks ERTI"),
    ("cf benchmarks erti", "CF Benchmarks ERTI"),
    ("cf benchmarks' solana real-time index", "CF Benchmarks SOL RTI"),
    ("coinbase real-time index", "Coinbase Real-Time Index"),
    ("binance real-time index", "Binance Real-Time Index"),
)

_COMPARATOR_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bat or above\b", re.IGNORECASE), "at_or_above"),
    (re.compile(r"\bat or below\b", re.IGNORECASE), "at_or_below"),
    (re.compile(r"\bgreater than or equal to\b", re.IGNORECASE), "at_or_above"),
    (re.compile(r"\bless than or equal to\b", re.IGNORECASE), "at_or_below"),
    (re.compile(r"\bgreater than\b", re.IGNORECASE), "greater_than"),
    (re.compile(r"\bless than\b", re.IGNORECASE), "less_than"),
    (re.compile(r"\babove\b", re.IGNORECASE), "above"),
    (re.compile(r"\bbelow\b", re.IGNORECASE), "below"),
)

# Range and deadline shape detection. Conservative: only fire on explicit
# wording in the rules text, never on titles alone.
_RANGE_BUCKET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bis between\b\s+[\$\d.,kKmMbB-]+", re.IGNORECASE),
    re.compile(r"\bin the range\b", re.IGNORECASE),
    re.compile(r"\bends in the range\b", re.IGNORECASE),
)
_RANGE_HIT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bends in\b", re.IGNORECASE),
    re.compile(r"\bcloses in\b", re.IGNORECASE),
)
_DEADLINE_TOUCH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:touch|touches|hits|reaches|reach)\b.*\bbefore\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bany time\b.*\bbefore\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bby (?:the )?end of\b", re.IGNORECASE),
)

# Point-in-time confirmation: explicit "at HH (AM|PM) on DATE" or "at <time> on <date>"
_POINT_IN_TIME_PATTERN = re.compile(
    r"\bat\b\s+\d{1,2}\s*(?:am|pm|:\d{2})?\s*\w*\s+on\s+\b",
    re.IGNORECASE,
)

_TIME_OF_DAY_PATTERN = re.compile(
    r"\b(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>am|pm)\s*(?P<tz>e[ds]t|et|utc|gmt|pt|p[ds]t|ct|c[ds]t)?\b",
    re.IGNORECASE,
)
_TIMEZONE_PATTERN = re.compile(
    r"\b(?P<tz>e[ds]t|et|utc|gmt|p[ds]t|pt|c[ds]t|ct)\b",
    re.IGNORECASE,
)


def build_kalshi_crypto_typed_key_audit_report(
    *,
    input_dir: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    warnings: list[dict[str, Any]] = []

    normalized = _load_json(input_dir / NORMALIZED_INPUT_FILE, warnings, "normalized_markets_v0_input")
    fresh_crypto = _load_optional_json(
        input_dir / KALSHI_FRESH_CRYPTO_INPUT_FILE,
        warnings,
        "kalshi_fresh_crypto_snapshot_input",
    )
    cdna_payload = _load_json(input_dir / CDNA_INPUT_FILE, warnings, "cdna_research_snapshot_input")
    pm_audit_payload = _load_json(input_dir / POLYMARKET_PIT_INPUT_FILE, warnings, "polymarket_pit_audit_input")
    orderbook_index = _load_kalshi_orderbook_enrichment_index(input_dir=input_dir, warnings=warnings)

    cdna_index = _build_cdna_index(cdna_payload)
    polymarket_index = _build_polymarket_index(pm_audit_payload)

    fresh_candidates = _kalshi_crypto_candidates(fresh_crypto)
    candidates = _merge_prefer_fresh_crypto_candidates(
        fresh_candidates=fresh_candidates,
        normalized_candidates=_kalshi_crypto_candidates(normalized),
    )
    rows: list[dict[str, Any]] = []
    overlap_rows: list[dict[str, Any]] = []
    for raw_row in candidates:
        raw_row = _overlay_orderbook_enrichment(raw_row, orderbook_index)
        audit_row = _audit_row(
            raw=raw_row,
            cdna_index=cdna_index,
            polymarket_index=polymarket_index,
        )
        rows.append(audit_row)
        if audit_row.get("date_threshold_comparator_overlap_present"):
            overlap_rows.append(audit_row)

    rows.sort(key=_row_sort_key)
    summary = _summary(rows=rows, overlap_rows=overlap_rows, orderbook_diagnostics=orderbook_index["diagnostics"])
    summary["fresh_crypto_snapshot_present"] = bool(fresh_candidates)
    summary["fresh_crypto_snapshot_rows_loaded"] = len(fresh_candidates)
    return {
        "schema_version": SCHEMA_VERSION,
        "schema_kind": SCHEMA_KIND,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "input_dir": str(input_dir),
        "diagnostic_only": True,
        "saved_files_only": True,
        "inputs": {
            "normalized_markets_v0": str(input_dir / NORMALIZED_INPUT_FILE),
            "kalshi_fresh_crypto_snapshot": str(input_dir / KALSHI_FRESH_CRYPTO_INPUT_FILE),
            "cdna_snapshot": str(input_dir / CDNA_INPUT_FILE),
            "polymarket_pit_audit": str(input_dir / POLYMARKET_PIT_INPUT_FILE),
            "kalshi_orderbook_enrichment_files": orderbook_index["diagnostics"].get("enriched_file_paths", []),
        },
        "summary": summary,
        "rows": rows,
        "warnings": warnings,
        "safety": _safety_block(),
    }


def write_kalshi_crypto_typed_key_audit_files(
    *,
    input_dir: Path,
    json_output: Path,
    markdown_output: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_kalshi_crypto_typed_key_audit_report(
        input_dir=input_dir,
        generated_at=generated_at,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_kalshi_crypto_typed_key_audit_markdown(report), encoding="utf-8")
    return report


def render_kalshi_crypto_typed_key_audit_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    rows = report.get("rows") or []
    lines: list[str] = [
        "# Kalshi Crypto Typed-Key Audit",
        "",
        "Saved-file-only diagnostic for Kalshi crypto price-threshold rows. Extracts explicit "
        "typed keys (asset / threshold / comparator / target date+time / timezone / settlement "
        "source) from saved normalized Kalshi rows, classifies contract shape, tags blockers, "
        "and emits diagnostic peer hints against saved CDNA and Polymarket point-in-time crypto "
        "rows. Never creates candidate pairs; never claims exact same-payoff equivalence; never "
        "treats deadline / range-hit / range-bucket as point-in-time; never emits paper actions.",
        "",
        "## Executive Summary",
        "",
        f"- kalshi_crypto_rows: `{summary.get('kalshi_crypto_rows', 0)}`",
        f"- typed_complete_rows: `{summary.get('typed_complete_rows', 0)}`",
        f"- point_in_time_rows: `{summary.get('point_in_time_rows', 0)}`",
        f"- deadline_or_range_hit_rows: `{summary.get('deadline_or_range_hit_rows', 0)}`",
        f"- ambiguous_rows: `{summary.get('ambiguous_rows', 0)}`",
        f"- rows_with_asset: `{summary.get('rows_with_asset', 0)}`",
        f"- rows_with_threshold: `{summary.get('rows_with_threshold', 0)}`",
        f"- rows_with_comparator: `{summary.get('rows_with_comparator', 0)}`",
        f"- rows_with_target_date: `{summary.get('rows_with_target_date', 0)}`",
        f"- rows_with_target_time: `{summary.get('rows_with_target_time', 0)}`",
        f"- rows_with_timezone: `{summary.get('rows_with_timezone', 0)}`",
        f"- rows_with_settlement_source: `{summary.get('rows_with_settlement_source', 0)}`",
        f"- rows_with_settlement_source_url: `{summary.get('rows_with_settlement_source_url', 0)}`",
        f"- rows_with_quote: `{summary.get('rows_with_quote', 0)}`",
        f"- fresh_crypto_snapshot_present: `{str(bool(summary.get('fresh_crypto_snapshot_present'))).lower()}`",
        f"- fresh_crypto_snapshot_rows_loaded: `{summary.get('fresh_crypto_snapshot_rows_loaded', 0)}`",
        f"- enriched_files_read: `{summary.get('enriched_files_read', 0)}`",
        f"- rows_with_existing_top_of_book: `{summary.get('rows_with_existing_top_of_book', 0)}`",
        f"- rows_with_fresh_orderbook: `{summary.get('rows_with_fresh_orderbook', 0)}`",
        f"- rows_with_stale_top_of_book: `{summary.get('rows_with_stale_top_of_book', 0)}`",
        f"- rows_with_full_orderbook_missing: `{summary.get('rows_with_full_orderbook_missing', 0)}`",
        f"- rows_with_bid_ask_size_timestamp: `{summary.get('rows_with_bid_ask_size_timestamp', 0)}`",
        f"- kalshi_live_orderbook_fetch_supported: `{str(bool(summary.get('kalshi_live_orderbook_fetch_supported'))).lower()}`",
        f"- kalshi_live_orderbook_fetch_not_enabled_or_missing_count: `{summary.get('kalshi_live_orderbook_fetch_not_enabled_or_missing_count', 0)}`",
        f"- possible_cdna_peer_rows: `{summary.get('possible_cdna_peer_rows', 0)}`",
        f"- possible_polymarket_peer_rows: `{summary.get('possible_polymarket_peer_rows', 0)}`",
        f"- no_saved_peer_rows: `{summary.get('no_saved_peer_rows', 0)}`",
        f"- date_threshold_comparator_overlap_rows: `{summary.get('date_threshold_comparator_overlap_rows', 0)}`",
        f"- exact_ready_rows: `0`",
        f"- paper_candidate_rows: `0`",
        "",
        "## Counts By Shape",
        "",
        "| Shape | Rows |",
        "|---|---:|",
    ]
    for shape, count in (summary.get("shape_counts") or {}).items():
        lines.append(f"| {shape} | {count} |")
    lines.extend(
        [
            "",
            "## Counts By Asset",
            "",
            "| Asset | Rows |",
            "|---|---:|",
        ]
    )
    for asset, count in (summary.get("asset_counts") or {}).items():
        lines.append(f"| {asset or 'UNKNOWN'} | {count} |")

    lines.extend(
        [
            "",
            "## Top 20 Kalshi Crypto Rows By Typed-Key Completeness",
            "",
            "| # | Ticker | Asset | Shape | Threshold | Comp | Date | Time | TZ | Source | Peer Hints |",
            "|---:|---|---|---|---:|---|---|---|---|---|---|",
        ]
    )
    top20 = summary.get("top_20_by_completeness") or []
    if not top20:
        lines.append("| _none_ | | | | | | | | | | |")
    else:
        for i, row in enumerate(top20, start=1):
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(i),
                        _md_cell(row.get("ticker")),
                        _md_cell(row.get("asset")),
                        _md_cell(row.get("market_shape")),
                        _md_cell(_quote_display(row.get("threshold"))),
                        _md_cell(row.get("comparator")),
                        _md_cell(row.get("target_date")),
                        _md_cell(row.get("target_time")),
                        _md_cell(row.get("timezone")),
                        _md_cell((row.get("settlement_source") or "")[:60]),
                        _md_cell(",".join(row.get("peer_hints") or [])),
                    ]
                )
                + " |"
            )

    lines.extend(
        [
            "",
            "## Missing Typed-Key Breakdown",
            "",
            "| Blocker | Count |",
            "|---|---:|",
        ]
    )
    for item in summary.get("top_blockers") or []:
        lines.append(f"| {item.get('blocker')} | {item.get('count')} |")

    lines.extend(
        [
            "",
            "## Potential CDNA / Polymarket Peer Hints (Diagnostic Only)",
            "",
            "| # | Ticker | Asset | Shape | Threshold | Date | CDNA Peer Hint | Polymarket Peer Hint |",
            "|---:|---|---|---|---:|---|:---:|:---:|",
        ]
    )
    top_peers = summary.get("top_10_peer_hint_rows") or []
    if not top_peers:
        lines.append("| _none_ | | | | | | | |")
    else:
        for i, row in enumerate(top_peers, start=1):
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(i),
                        _md_cell(row.get("ticker")),
                        _md_cell(row.get("asset")),
                        _md_cell(row.get("market_shape")),
                        _md_cell(_quote_display(row.get("threshold"))),
                        _md_cell(row.get("target_date")),
                        "yes" if HINT_POSSIBLE_CDNA_PEER in (row.get("peer_hints") or []) else "no",
                        "yes" if HINT_POSSIBLE_POLYMARKET_PEER in (row.get("peer_hints") or []) else "no",
                    ]
                )
                + " |"
            )

    lines.extend(
        [
            "",
            "## Recommended Next Normalization / Fetch Action",
            "",
            f"- next_action: `{summary.get('next_action')}`",
            f"- next_action_reason: `{summary.get('next_action_reason')}`",
            "",
            "## Safety",
            "",
            "- diagnostic_only: `true`",
            "- saved_files_only: `true`",
            "- live_fetch_attempted: `false`",
            "- can_create_candidate_pair: `false`",
            "- can_create_paper_candidate: `false`",
            "- exact_ready: `false`",
            "- execution_ready: `false`",
            "- paper_candidate: `false`",
            "- treats_title_similarity_as_settlement_equivalence: `false`",
            "- treats_deadline_or_range_hit_as_point_in_time: `false`",
            "- infers_threshold_or_comparator_from_midpoint_or_complement: `false`",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Candidate selection + typed key extraction
# ---------------------------------------------------------------------------


def _kalshi_crypto_candidates(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("normalized_markets")
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("venue") != "kalshi":
            continue
        if _looks_crypto(row):
            out.append(row)
    return out


def _merge_prefer_fresh_crypto_candidates(
    *,
    fresh_candidates: list[dict[str, Any]],
    normalized_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in fresh_candidates:
        ticker = _string_or_none(row.get("ticker") or row.get("market_id"))
        if ticker:
            seen.add(ticker)
        cloned = dict(row)
        cloned["raw_source_file"] = str(Path("reports") / KALSHI_FRESH_CRYPTO_INPUT_FILE).replace("\\", "/")
        cloned["fresh_crypto_snapshot_preferred"] = True
        merged.append(cloned)
    for row in normalized_candidates:
        ticker = _string_or_none(row.get("ticker") or row.get("market_id"))
        if ticker and ticker in seen:
            continue
        merged.append(row)
    return merged


def _load_kalshi_orderbook_enrichment_index(
    *,
    input_dir: Path,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    by_ticker: dict[str, dict[str, Any]] = {}
    by_ticker_source: dict[tuple[str, str], dict[str, Any]] = {}
    seen_paths: set[Path] = set()
    file_paths: list[str] = []
    summary_counts = Counter()

    for pattern in KALSHI_ORDERBOOK_ENRICHED_GLOBS:
        for path in sorted(input_dir.glob(pattern)):
            resolved = path.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            payload = _load_json(path, warnings, "kalshi_orderbook_enrichment_input")
            if not _is_kalshi_orderbook_enrichment_payload(payload):
                continue
            file_paths.append(str(path))
            summary = payload.get("orderbook_enrichment") if isinstance(payload.get("orderbook_enrichment"), dict) else {}
            for key in (
                "market_count",
                "enriched_count",
                "fresh_orderbook_fetch_enriched_count",
                "existing_top_of_book_present_count",
                "full_orderbook_missing_count",
                "fetch_failed_count",
                "stale_existing_top_of_book_count",
            ):
                summary_counts[key] += _int(summary.get(key))
            source_snapshot = _string_or_none(summary.get("source_snapshot_path"))
            source_snapshot_rel = _repo_relative_path(source_snapshot) if source_snapshot else None
            rows = payload.get("normalized_markets") if isinstance(payload.get("normalized_markets"), list) else []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                ticker = _string_or_none(row.get("ticker") or row.get("market_id"))
                if not ticker:
                    continue
                indexed = dict(row)
                indexed["_orderbook_enrichment_report_path"] = str(path)
                indexed["_orderbook_enrichment_source_snapshot_path"] = source_snapshot
                if source_snapshot_rel:
                    by_ticker_source[(ticker, source_snapshot_rel)] = _prefer_orderbook_row(
                        by_ticker_source.get((ticker, source_snapshot_rel)),
                        indexed,
                    )
                by_ticker[ticker] = _prefer_orderbook_row(by_ticker.get(ticker), indexed)

    diagnostics = {
        "enriched_files_read": len(file_paths),
        "enriched_file_paths": file_paths,
        "orderbook_enrichment_market_rows": summary_counts["market_count"],
        "orderbook_enrichment_enriched_rows": summary_counts["enriched_count"],
        "orderbook_enrichment_fresh_orderbook_fetch_enriched_rows": summary_counts[
            "fresh_orderbook_fetch_enriched_count"
        ],
        "orderbook_enrichment_existing_top_of_book_rows": summary_counts["existing_top_of_book_present_count"],
        "orderbook_enrichment_full_orderbook_missing_rows": summary_counts["full_orderbook_missing_count"],
        "orderbook_enrichment_fetch_failed_rows": summary_counts["fetch_failed_count"],
        "orderbook_enrichment_stale_existing_top_of_book_rows": summary_counts["stale_existing_top_of_book_count"],
        "kalshi_live_orderbook_fetch_supported": True,
    }
    return {
        "by_ticker": by_ticker,
        "by_ticker_source": by_ticker_source,
        "diagnostics": diagnostics,
    }


def _is_kalshi_orderbook_enrichment_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    summary = payload.get("orderbook_enrichment")
    if not isinstance(summary, dict):
        return False
    if summary.get("source") != "read_only_orderbook_enrichment":
        return False
    if str(summary.get("venue") or "").lower() != "kalshi":
        return False
    return isinstance(payload.get("normalized_markets"), list)


def _prefer_orderbook_row(current: dict[str, Any] | None, candidate: dict[str, Any]) -> dict[str, Any]:
    if current is None:
        return candidate
    current_enrichment = current.get("orderbook_enrichment") if isinstance(current.get("orderbook_enrichment"), dict) else {}
    candidate_enrichment = (
        candidate.get("orderbook_enrichment") if isinstance(candidate.get("orderbook_enrichment"), dict) else {}
    )
    if candidate_enrichment.get("enrichment_status") == "enriched" and current_enrichment.get("enrichment_status") != "enriched":
        return candidate
    return current


def _overlay_orderbook_enrichment(
    raw_row: dict[str, Any],
    orderbook_index: dict[str, Any],
) -> dict[str, Any]:
    ticker = _string_or_none(raw_row.get("ticker") or raw_row.get("market_id"))
    if not ticker:
        return raw_row
    source_file = _string_or_none(raw_row.get("raw_source_file") or raw_row.get("source_file") or raw_row.get("source_path"))
    source_file_rel = _repo_relative_path(source_file) if source_file else None
    by_ticker_source = orderbook_index.get("by_ticker_source") if isinstance(orderbook_index.get("by_ticker_source"), dict) else {}
    by_ticker = orderbook_index.get("by_ticker") if isinstance(orderbook_index.get("by_ticker"), dict) else {}
    enriched_row = None
    if source_file_rel:
        enriched_row = by_ticker_source.get((ticker, source_file_rel))
    if enriched_row is None:
        enriched_row = by_ticker.get(ticker)
    if not isinstance(enriched_row, dict):
        return raw_row

    merged = dict(raw_row)
    enrichment = enriched_row.get("orderbook_enrichment")
    if isinstance(enrichment, dict):
        merged["orderbook_enrichment"] = enrichment
    merged["orderbook_enrichment_report_path"] = enriched_row.get("_orderbook_enrichment_report_path")
    merged["orderbook_enrichment_source_snapshot_path"] = enriched_row.get("_orderbook_enrichment_source_snapshot_path")
    merged["orderbook_enrichment_row_top_of_book"] = {
        "best_bid": enriched_row.get("best_bid"),
        "best_ask": enriched_row.get("best_ask"),
    }
    return merged


def _looks_crypto(row: dict[str, Any]) -> bool:
    event_ticker = (row.get("event_ticker") or "").upper()
    ticker = (row.get("ticker") or "").upper()
    title = (row.get("title") or "").lower()
    for prefix, _ in _CRYPTO_TICKER_PREFIXES:
        if event_ticker.startswith(prefix) or ticker.startswith(prefix):
            return True
    for token, _ in _CRYPTO_TITLE_TOKENS:
        if token in title:
            return True
    return False


def _audit_row(
    *,
    raw: dict[str, Any],
    cdna_index: dict[str, list[dict[str, Any]]],
    polymarket_index: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    ticker = _string_or_none(raw.get("ticker"))
    event_ticker = _string_or_none(raw.get("event_ticker"))
    title = _string_or_none(raw.get("title"))
    settlement = raw.get("settlement") if isinstance(raw.get("settlement"), dict) else {}
    raw_payload = raw.get("raw") if isinstance(raw.get("raw"), dict) else {}
    raw_rules_text = "\n\n".join(
        str(value)
        for value in (raw_payload.get("rules_primary"), raw_payload.get("rules_secondary"))
        if value is not None and str(value).strip()
    )
    rules_text = _string_or_none(settlement.get("settlement_rules_text")) or raw_rules_text or ""
    close_time_iso = _string_or_none(settlement.get("close_time") or raw.get("close_time") or raw_payload.get("close_time"))
    resolution_time_iso = _string_or_none(
        settlement.get("resolution_time")
        or raw_payload.get("expected_expiration_time")
        or raw_payload.get("expiration_time")
        or raw.get("end_date")
    )
    settlement_source_url = _string_or_none(settlement.get("settlement_source_url") or raw.get("settlement_source_url"))
    settlement_source_kind = _string_or_none(settlement.get("settlement_source_kind") or raw.get("settlement_source_kind"))

    asset = _string_or_none(raw.get("asset")) or _extract_asset(ticker=ticker, event_ticker=event_ticker, title=title)
    threshold, secondary_threshold = _extract_threshold(ticker=ticker)
    threshold = _float_or_none(raw.get("threshold")) if raw.get("threshold") is not None else threshold
    secondary_threshold = (
        _float_or_none(raw.get("threshold_lower")) if raw.get("threshold_lower") is not None else secondary_threshold
    )
    shape, comparator_from_shape = _classify_shape(rules_text=rules_text, ticker=ticker, has_secondary=bool(secondary_threshold))
    if shape == SHAPE_AMBIGUOUS and _string_or_none(raw.get("market_shape")):
        shape = _string_or_none(raw.get("market_shape")) or shape
    comparator = _string_or_none(raw.get("comparator")) or _extract_comparator(
        rules_text=rules_text,
        fallback=comparator_from_shape,
        shape=shape,
    )
    target_date, target_time, timezone_label = _extract_target_datetime(
        rules_text=rules_text,
        close_time_iso=close_time_iso,
        resolution_time_iso=resolution_time_iso,
    )
    target_date = _string_or_none(raw.get("target_date")) or target_date
    target_time = _string_or_none(raw.get("target_time")) or target_time
    timezone_label = _string_or_none(raw.get("timezone")) or timezone_label
    settlement_source = _string_or_none(raw.get("settlement_source")) or _extract_settlement_source(rules_text=rules_text)

    quote = _extract_quote(raw)

    blockers = _compute_blockers(
        asset=asset,
        threshold=threshold,
        comparator=comparator,
        target_date=target_date,
        target_time=target_time,
        timezone_label=timezone_label,
        settlement_source=settlement_source,
        shape=shape,
        quote=quote,
    )

    typed_complete = not any(
        b in blockers
        for b in (
            B_MISSING_ASSET,
            B_MISSING_THRESHOLD,
            B_MISSING_COMPARATOR,
            B_MISSING_TARGET_DATE,
            B_MISSING_TARGET_TIME,
            B_MISSING_TIMEZONE,
            B_MISSING_SETTLEMENT_SOURCE,
            B_AMBIGUOUS_SHAPE,
            B_DEADLINE_NOT_POINT_IN_TIME,
        )
    )

    peer_hints, peer_evidence = _peer_hints(
        asset=asset,
        threshold=threshold,
        comparator=comparator,
        target_date=target_date,
        shape=shape,
        cdna_index=cdna_index,
        polymarket_index=polymarket_index,
    )
    overlap_present = (
        shape == SHAPE_POINT_IN_TIME
        and bool(target_date)
        and threshold is not None
        and comparator is not None
        and (
            any(hint in peer_hints for hint in (HINT_POSSIBLE_CDNA_PEER, HINT_POSSIBLE_POLYMARKET_PEER))
        )
    )

    typed_completeness_score = sum(
        1
        for value in (asset, threshold, comparator, target_date, target_time, timezone_label, settlement_source)
        if value not in (None, "")
    )

    return {
        "row_id": f"kalshi_crypto::{ticker or event_ticker or ''}",
        "ticker": ticker,
        "event_ticker": event_ticker,
        "market_id": _string_or_none(raw.get("market_id")),
        "title": title,
        "venue": "kalshi",
        "asset": asset,
        "threshold": threshold,
        "threshold_lower": secondary_threshold if shape in {SHAPE_RANGE_BUCKET, SHAPE_RANGE_HIT} else None,
        "comparator": comparator,
        "target_date": target_date,
        "target_time": target_time,
        "timezone": timezone_label,
        "settlement_source": settlement_source,
        "settlement_source_url": settlement_source_url,
        "settlement_source_kind": settlement_source_kind,
        "settlement_close_time": close_time_iso,
        "settlement_resolution_time": resolution_time_iso,
        "yes_no_side": "yes",
        "market_shape": shape,
        "quote": quote,
        "blockers": blockers,
        "peer_hints": peer_hints,
        "peer_evidence": peer_evidence,
        "typed_complete": typed_complete,
        "typed_completeness_score": typed_completeness_score,
        "date_threshold_comparator_overlap_present": overlap_present,
        "raw_source_file": _string_or_none(
            raw.get("raw_source_file") or raw.get("source_file") or raw.get("source_path")
        ),
        "fresh_crypto_snapshot_preferred": bool(raw.get("fresh_crypto_snapshot_preferred")),
        "raw_row_index": raw.get("row_index"),
        "settlement_rules_text_preview": (rules_text or "")[:240],
        "diagnostic_only": True,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "exact_ready": False,
        "execution_ready": False,
        "paper_candidate": False,
        "affects_evaluator_gates": False,
        "treats_title_similarity_as_settlement_equivalence": False,
        "treats_deadline_or_range_hit_as_point_in_time": False,
    }


def _extract_asset(*, ticker: str | None, event_ticker: str | None, title: str | None) -> str | None:
    for source in (event_ticker, ticker):
        if not source:
            continue
        upper = source.upper()
        for prefix, asset in _CRYPTO_TICKER_PREFIXES:
            if upper.startswith(prefix):
                return asset
    if title:
        lowered = title.lower()
        for token, asset in _CRYPTO_TITLE_TOKENS:
            if token in lowered:
                return asset
    return None


def _extract_threshold(*, ticker: str | None) -> tuple[float | None, float | None]:
    """Extract the explicit ticker strike value(s).

    The Kalshi ticker tail format observed is ``-T<value>`` (above strike) or
    ``-B<value>`` (below / lower-band strike). A pair like ``-T...-B...`` only
    appears in range markets; that case is handled here by returning both
    values, but the audit still treats the higher strike as ``threshold`` and
    the lower strike as ``threshold_lower`` to keep the contract shown
    explicitly.
    """
    if not ticker:
        return None, None
    matches = re.findall(r"-([TB])([0-9]+(?:\.[0-9]+)?)", ticker.upper())
    if not matches:
        return None, None
    if len(matches) == 1:
        try:
            return float(matches[0][1]), None
        except ValueError:
            return None, None
    try:
        values = [float(value) for _, value in matches]
    except ValueError:
        return None, None
    values.sort()
    return values[-1], values[0]


def _classify_shape(
    *,
    rules_text: str,
    ticker: str | None,
    has_secondary: bool,
) -> tuple[str, str | None]:
    text = (rules_text or "").strip()
    if not text:
        return SHAPE_AMBIGUOUS, None
    if any(p.search(text) for p in _DEADLINE_TOUCH_PATTERNS):
        return SHAPE_DEADLINE_TOUCH, "touches_before"
    if any(p.search(text) for p in _RANGE_BUCKET_PATTERNS) or has_secondary:
        return SHAPE_RANGE_BUCKET, "between"
    if any(p.search(text) for p in _RANGE_HIT_PATTERNS):
        return SHAPE_RANGE_HIT, "ends_in_range"
    if _POINT_IN_TIME_PATTERN.search(text):
        return SHAPE_POINT_IN_TIME, None
    if " above " in text.lower() or " below " in text.lower():
        # An "above/below ... at TIME on DATE" phrasing — keep as point-in-time
        # only if the rules text says so, otherwise it stays ambiguous.
        if re.search(r"\b(?:above|below|at or above|at or below)\b.*\bon\b", text, re.IGNORECASE | re.DOTALL):
            return SHAPE_POINT_IN_TIME, None
    return SHAPE_AMBIGUOUS, None


def _extract_comparator(*, rules_text: str, fallback: str | None, shape: str) -> str | None:
    if shape == SHAPE_RANGE_BUCKET:
        return "between"
    if shape == SHAPE_RANGE_HIT:
        return "ends_in_range"
    if shape == SHAPE_DEADLINE_TOUCH:
        return fallback or "touches_before"
    text = rules_text or ""
    for pattern, label in _COMPARATOR_PATTERNS:
        if pattern.search(text):
            return label
    return fallback


def _extract_target_datetime(
    *,
    rules_text: str,
    close_time_iso: str | None,
    resolution_time_iso: str | None,
) -> tuple[str | None, str | None, str | None]:
    target_date: str | None = None
    target_time: str | None = None
    timezone_label: str | None = None

    parsed = _parse_iso(close_time_iso) or _parse_iso(resolution_time_iso)
    if parsed is not None:
        target_date = parsed.strftime("%Y-%m-%d")
        target_time = parsed.strftime("%H:%M")
        if parsed.tzinfo is not None:
            offset = parsed.utcoffset()
            if offset == timezone.utc.utcoffset(parsed):
                timezone_label = "UTC"

    text = rules_text or ""
    if not target_time or not timezone_label:
        match = _TIME_OF_DAY_PATTERN.search(text)
        if match:
            if not target_time:
                hour = match.group("hour")
                minute = match.group("minute") or "00"
                meridiem = (match.group("meridiem") or "").lower()
                if hour and meridiem:
                    h = int(hour)
                    if meridiem == "pm" and h != 12:
                        h += 12
                    if meridiem == "am" and h == 12:
                        h = 0
                    target_time = f"{h:02d}:{minute}"
            if not timezone_label:
                tz_label = match.group("tz")
                if tz_label:
                    timezone_label = tz_label.upper()
        if not timezone_label:
            tz_match = _TIMEZONE_PATTERN.search(text)
            if tz_match:
                timezone_label = tz_match.group("tz").upper()

    if not target_date and parsed is None:
        date_match = re.search(
            r"\bon\s+([A-Z][a-z]+\s+\d{1,2},\s*\d{4})\b",
            text,
        )
        if date_match:
            target_date = date_match.group(1).strip()

    return target_date, target_time, timezone_label


def _extract_settlement_source(*, rules_text: str) -> str | None:
    text = (rules_text or "").lower()
    if not text:
        return None
    for marker, label in _SETTLEMENT_SOURCE_MARKERS:
        if marker in text:
            return label
    return None


def _extract_quote(raw: dict[str, Any]) -> dict[str, Any]:
    quote: dict[str, Any] = {
        "bid": None,
        "ask": None,
        "bid_size": None,
        "ask_size": None,
        "observed_at": None,
        "quote_timestamp": None,
        "source": None,
        "raw_source_file": _string_or_none(raw.get("orderbook_enrichment_report_path")),
        "present": False,
        "bid_ask_size_timestamp_present": False,
        "fresh_orderbook": False,
        "existing_top_of_book_present": False,
        "existing_top_of_book_bid": None,
        "existing_top_of_book_ask": None,
        "stale_top_of_book": False,
        "full_orderbook_missing": False,
        "kalshi_live_orderbook_fetch_not_enabled_or_missing": False,
        "orderbook_enrichment_status": None,
        "orderbook_enrichment_warnings": [],
        "orderbook_failure_reason": None,
        "market_settled": False,
        "orderbook_source_snapshot_path": _string_or_none(raw.get("orderbook_enrichment_source_snapshot_path")),
    }

    top_of_book = raw.get("orderbook_enrichment_row_top_of_book")
    top_of_book = top_of_book if isinstance(top_of_book, dict) else raw
    existing_bid = _float_or_none(_first_present(top_of_book, ("best_bid", "yes_best_bid", "bid")))
    existing_ask = _float_or_none(_first_present(top_of_book, ("best_ask", "yes_best_ask", "ask")))
    if existing_bid is not None or existing_ask is not None:
        quote["existing_top_of_book_present"] = True
        quote["existing_top_of_book_bid"] = existing_bid
        quote["existing_top_of_book_ask"] = existing_ask

    quote_depth = raw.get("quote_depth") if isinstance(raw.get("quote_depth"), dict) else {}
    if quote_depth:
        candidate = _quote_candidate_from_mapping(
            quote_depth,
            source="kalshi_quote_depth",
            raw_source_file=_string_or_none(raw.get("raw_source_file") or raw.get("source_file")),
        )
        if candidate["present"]:
            quote.update(candidate)
            return quote
        quote["source"] = candidate["source"]
        quote["bid"] = candidate["bid"]
        quote["ask"] = candidate["ask"]
        quote["bid_size"] = candidate["bid_size"]
        quote["ask_size"] = candidate["ask_size"]
        quote["observed_at"] = candidate["observed_at"]
        quote["quote_timestamp"] = candidate["quote_timestamp"]
        quote["raw_source_file"] = candidate["raw_source_file"]

    enrichment = raw.get("orderbook_enrichment") if isinstance(raw.get("orderbook_enrichment"), dict) else {}
    if enrichment:
        warnings = [
            str(item)
            for item in (enrichment.get("enrichment_warnings") or [])
            if str(item or "").strip()
        ]
        status = _string_or_none(enrichment.get("enrichment_status"))
        quote["orderbook_enrichment_status"] = status
        quote["orderbook_enrichment_warnings"] = warnings
        quote["orderbook_failure_reason"] = _string_or_none(enrichment.get("failure_reason"))
        quote["market_settled"] = bool(enrichment.get("market_settled"))
        if status == "enriched":
            candidate = _quote_candidate_from_mapping(
                enrichment,
                source="kalshi_orderbook_enrichment",
                raw_source_file=_string_or_none(raw.get("orderbook_enrichment_report_path")),
            )
            if candidate["present"]:
                quote.update(candidate)
                quote["fresh_orderbook"] = True
                quote["orderbook_enrichment_status"] = status
                quote["orderbook_enrichment_warnings"] = warnings
                return quote
            quote.update(
                {
                    "source": candidate["source"],
                    "bid": candidate["bid"],
                    "ask": candidate["ask"],
                    "bid_size": candidate["bid_size"],
                    "ask_size": candidate["ask_size"],
                    "observed_at": candidate["observed_at"],
                    "quote_timestamp": candidate["quote_timestamp"],
                    "raw_source_file": candidate["raw_source_file"],
                }
            )
        else:
            quote["source"] = quote["source"] or "kalshi_orderbook_enrichment_unenriched"
        if status != "enriched":
            quote["full_orderbook_missing"] = True
        if "stale_snapshot" in warnings or (quote["existing_top_of_book_present"] and status != "enriched"):
            quote["stale_top_of_book"] = True
        if "stale_snapshot" in warnings:
            quote["kalshi_live_orderbook_fetch_not_enabled_or_missing"] = True

    return quote


def _quote_candidate_from_mapping(
    mapping: dict[str, Any],
    *,
    source: str,
    raw_source_file: str | None,
) -> dict[str, Any]:
    bid = _float_or_none(_first_present(mapping, ("best_bid", "yes_best_bid", "bid")))
    ask = _float_or_none(_first_present(mapping, ("best_ask", "yes_best_ask", "ask")))
    bid_size = _float_or_none(_first_present(mapping, ("depth_at_best_bid", "best_bid_size", "yes_best_bid_size", "bid_size")))
    ask_size = _float_or_none(_first_present(mapping, ("depth_at_best_ask", "best_ask_size", "yes_best_ask_size", "ask_size")))
    captured_at = _first_present(mapping, ("orderbook_captured_at", "captured_at", "snapshot_captured_at", "observed_at"))
    observed_at = captured_at if isinstance(captured_at, str) and captured_at else None
    complete = all(value is not None for value in (bid, ask, bid_size, ask_size)) and observed_at is not None
    return {
        "bid": bid,
        "ask": ask,
        "bid_size": bid_size,
        "ask_size": ask_size,
        "observed_at": observed_at,
        "quote_timestamp": observed_at,
        "source": source,
        "raw_source_file": raw_source_file,
        "present": complete,
        "bid_ask_size_timestamp_present": complete,
    }


def _compute_blockers(
    *,
    asset: str | None,
    threshold: float | None,
    comparator: str | None,
    target_date: str | None,
    target_time: str | None,
    timezone_label: str | None,
    settlement_source: str | None,
    shape: str,
    quote: dict[str, Any],
) -> list[str]:
    blockers: list[str] = []
    if not asset:
        blockers.append(B_MISSING_ASSET)
    if threshold is None and shape not in {SHAPE_RANGE_BUCKET, SHAPE_RANGE_HIT}:
        blockers.append(B_MISSING_THRESHOLD)
    if not comparator:
        blockers.append(B_MISSING_COMPARATOR)
    if not target_date:
        blockers.append(B_MISSING_TARGET_DATE)
    if not target_time:
        blockers.append(B_MISSING_TARGET_TIME)
    if not timezone_label:
        blockers.append(B_MISSING_TIMEZONE)
    if not settlement_source:
        blockers.append(B_MISSING_SETTLEMENT_SOURCE)
    if shape == SHAPE_AMBIGUOUS:
        blockers.append(B_AMBIGUOUS_SHAPE)
    if shape in {SHAPE_DEADLINE_TOUCH, SHAPE_RANGE_HIT, SHAPE_RANGE_BUCKET}:
        blockers.append(B_DEADLINE_NOT_POINT_IN_TIME)
    if not quote.get("present"):
        blockers.append(B_MISSING_QUOTE)
        blockers.append(B_STALE_OR_MISSING_QUOTE)
    if quote.get("stale_top_of_book"):
        blockers.append(B_STALE_TOP_OF_BOOK)
    if quote.get("full_orderbook_missing"):
        blockers.append(B_FULL_ORDERBOOK_MISSING)
    if quote.get("kalshi_live_orderbook_fetch_not_enabled_or_missing"):
        blockers.append(B_KALSHI_LIVE_ORDERBOOK_FETCH_NOT_ENABLED_OR_MISSING)
    return blockers


# ---------------------------------------------------------------------------
# Peer hint indexes
# ---------------------------------------------------------------------------


def _build_cdna_index(payload: Any) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(payload, dict):
        return index
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return index
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("market_type") or "").lower() != "point_in_time_threshold":
            continue
        asset = str(row.get("asset") or "").upper().strip()
        if asset not in {"BTC", "ETH", "SOL"}:
            continue
        threshold = _float_or_none(row.get("threshold_value") or row.get("upper"))
        if threshold is None:
            continue
        target_date = _string_or_none(row.get("target_date"))
        if not target_date:
            continue
        entry = {
            "asset": asset,
            "threshold": threshold,
            "target_date": _normalize_date_token(target_date),
            "comparator": str(row.get("comparator") or "").strip() or None,
            "title": row.get("title"),
            "source_url": row.get("source_url"),
        }
        index.setdefault(asset, []).append(entry)
    return index


def _build_polymarket_index(payload: Any) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(payload, dict):
        return index
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return index
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("market_shape") or "").lower() != "point_in_time_threshold":
            continue
        asset = str(row.get("asset_or_family") or "").upper().strip()
        if asset not in {"BTC", "ETH", "SOL"}:
            continue
        threshold = _float_or_none(row.get("threshold"))
        if threshold is None:
            continue
        target_date = _string_or_none(row.get("target_date"))
        if not target_date:
            continue
        entry = {
            "asset": asset,
            "threshold": threshold,
            "target_date": _normalize_date_token(target_date),
            "comparator": str(row.get("comparator") or "").strip() or None,
            "question": row.get("question"),
            "row_id": row.get("row_id"),
        }
        index.setdefault(asset, []).append(entry)
    return index


def _peer_hints(
    *,
    asset: str | None,
    threshold: float | None,
    comparator: str | None,
    target_date: str | None,
    shape: str,
    cdna_index: dict[str, list[dict[str, Any]]],
    polymarket_index: dict[str, list[dict[str, Any]]],
) -> tuple[list[str], dict[str, Any]]:
    hints: list[str] = []
    evidence: dict[str, Any] = {"cdna_candidates": [], "polymarket_candidates": []}
    if asset is None or shape != SHAPE_POINT_IN_TIME:
        hints.append(HINT_NO_SAVED_PEER)
        return hints, evidence

    norm_date = _normalize_date_token(target_date) if target_date else None
    if asset in cdna_index:
        for entry in cdna_index[asset]:
            if _entries_compatible(
                threshold=threshold,
                comparator=comparator,
                target_date=norm_date,
                peer_entry=entry,
            ):
                evidence["cdna_candidates"].append(entry)
        if evidence["cdna_candidates"]:
            hints.append(HINT_POSSIBLE_CDNA_PEER)
    if asset in polymarket_index:
        for entry in polymarket_index[asset]:
            if _entries_compatible(
                threshold=threshold,
                comparator=comparator,
                target_date=norm_date,
                peer_entry=entry,
            ):
                evidence["polymarket_candidates"].append(entry)
        if evidence["polymarket_candidates"]:
            hints.append(HINT_POSSIBLE_POLYMARKET_PEER)
    if not hints:
        hints.append(HINT_NO_SAVED_PEER)
    return hints, evidence


def _entries_compatible(
    *,
    threshold: float | None,
    comparator: str | None,
    target_date: str | None,
    peer_entry: dict[str, Any],
) -> bool:
    if target_date and peer_entry.get("target_date") and peer_entry["target_date"] != target_date:
        return False
    if threshold is not None and peer_entry.get("threshold") is not None:
        if peer_entry["threshold"] <= 0:
            return False
        relative = abs(threshold - peer_entry["threshold"]) / max(abs(peer_entry["threshold"]), 1.0)
        if relative > 0.01:
            return False
    if comparator and peer_entry.get("comparator"):
        if not _comparator_family_compatible(comparator, peer_entry["comparator"]):
            return False
    return True


def _comparator_family_compatible(a: str, b: str) -> bool:
    above = {"above", ">", "greater_than", "at_or_above", ">=", "greater_than_or_equal_to"}
    below = {"below", "<", "less_than", "at_or_below", "<=", "less_than_or_equal_to"}
    al = a.strip().lower()
    bl = b.strip().lower()
    if al in above and bl in above:
        return True
    if al in below and bl in below:
        return True
    return al == bl


def _normalize_date_token(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = text.strip().rstrip(",")
    iso = _parse_iso(cleaned)
    if iso is not None:
        return iso.strftime("%Y-%m-%d")
    months = {
        "january": "01", "february": "02", "march": "03", "april": "04",
        "may": "05", "june": "06", "july": "07", "august": "08",
        "september": "09", "october": "10", "november": "11", "december": "12",
    }
    match = re.match(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", cleaned)
    if match:
        month_word = match.group(1).lower()
        if month_word in months:
            return f"{match.group(3)}-{months[month_word]}-{int(match.group(2)):02d}"
    match2 = re.match(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", cleaned)
    if match2:
        month_word = match2.group(2).lower()
        if month_word in months:
            return f"{match2.group(3)}-{months[month_word]}-{int(match2.group(1)):02d}"
    return cleaned


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _summary(
    *,
    rows: list[dict[str, Any]],
    overlap_rows: list[dict[str, Any]],
    orderbook_diagnostics: dict[str, Any],
) -> dict[str, Any]:
    asset_counts: Counter[str] = Counter()
    shape_counts: Counter[str] = Counter()
    blocker_counts: Counter[str] = Counter()
    typed_complete = 0
    point_in_time = 0
    deadline_or_range = 0
    ambiguous = 0
    with_asset = 0
    with_threshold = 0
    with_comparator = 0
    with_target_date = 0
    with_target_time = 0
    with_timezone = 0
    with_settlement_source = 0
    with_settlement_source_url = 0
    with_quote = 0
    with_existing_top_of_book = 0
    with_fresh_orderbook = 0
    with_stale_top_of_book = 0
    with_full_orderbook_missing = 0
    with_bid_ask_size_timestamp = 0
    cdna_hint_rows = 0
    polymarket_hint_rows = 0
    no_peer_rows = 0
    for row in rows:
        asset_counts[(row.get("asset") or "UNKNOWN").upper()] += 1
        shape = row.get("market_shape") or SHAPE_AMBIGUOUS
        shape_counts[shape] += 1
        if shape == SHAPE_POINT_IN_TIME:
            point_in_time += 1
        elif shape in {SHAPE_DEADLINE_TOUCH, SHAPE_RANGE_HIT, SHAPE_RANGE_BUCKET}:
            deadline_or_range += 1
        else:
            ambiguous += 1
        if row.get("typed_complete"):
            typed_complete += 1
        if row.get("asset"):
            with_asset += 1
        if row.get("threshold") is not None:
            with_threshold += 1
        if row.get("comparator"):
            with_comparator += 1
        if row.get("target_date"):
            with_target_date += 1
        if row.get("target_time"):
            with_target_time += 1
        if row.get("timezone"):
            with_timezone += 1
        if row.get("settlement_source"):
            with_settlement_source += 1
        if row.get("settlement_source_url"):
            with_settlement_source_url += 1
        quote = row.get("quote") or {}
        if quote.get("present"):
            with_quote += 1
        if quote.get("existing_top_of_book_present"):
            with_existing_top_of_book += 1
        if quote.get("fresh_orderbook"):
            with_fresh_orderbook += 1
        if quote.get("stale_top_of_book"):
            with_stale_top_of_book += 1
        if quote.get("full_orderbook_missing"):
            with_full_orderbook_missing += 1
        if quote.get("bid_ask_size_timestamp_present"):
            with_bid_ask_size_timestamp += 1
        peer_hints = row.get("peer_hints") or []
        if HINT_POSSIBLE_CDNA_PEER in peer_hints:
            cdna_hint_rows += 1
        if HINT_POSSIBLE_POLYMARKET_PEER in peer_hints:
            polymarket_hint_rows += 1
        if HINT_NO_SAVED_PEER in peer_hints:
            no_peer_rows += 1
        for blocker in row.get("blockers") or []:
            blocker_counts[blocker] += 1

    top_blockers = [
        {"blocker": blocker, "count": count} for blocker, count in blocker_counts.most_common(20)
    ]
    top_20 = [
        {
            "ticker": row.get("ticker"),
            "asset": row.get("asset"),
            "market_shape": row.get("market_shape"),
            "threshold": row.get("threshold"),
            "comparator": row.get("comparator"),
            "target_date": row.get("target_date"),
            "target_time": row.get("target_time"),
            "timezone": row.get("timezone"),
            "settlement_source": row.get("settlement_source"),
            "peer_hints": row.get("peer_hints"),
            "typed_completeness_score": row.get("typed_completeness_score"),
        }
        for row in rows[:20]
    ]
    top_peers = [
        {
            "ticker": row.get("ticker"),
            "asset": row.get("asset"),
            "market_shape": row.get("market_shape"),
            "threshold": row.get("threshold"),
            "target_date": row.get("target_date"),
            "peer_hints": row.get("peer_hints"),
        }
        for row in overlap_rows[:10]
    ]
    next_action, next_action_reason = _next_action(
        point_in_time=point_in_time,
        typed_complete=typed_complete,
        deadline_or_range=deadline_or_range,
        cdna_hint_rows=cdna_hint_rows,
        polymarket_hint_rows=polymarket_hint_rows,
        overlap_count=len(overlap_rows),
        kalshi_total=len(rows),
    )
    return {
        "kalshi_crypto_rows": len(rows),
        "typed_complete_rows": typed_complete,
        "point_in_time_rows": point_in_time,
        "deadline_or_range_hit_rows": deadline_or_range,
        "ambiguous_rows": ambiguous,
        "rows_with_asset": with_asset,
        "rows_with_threshold": with_threshold,
        "rows_with_comparator": with_comparator,
        "rows_with_target_date": with_target_date,
        "rows_with_target_time": with_target_time,
        "rows_with_timezone": with_timezone,
        "rows_with_settlement_source": with_settlement_source,
        "rows_with_settlement_source_url": with_settlement_source_url,
        "rows_with_quote": with_quote,
        "enriched_files_read": _int(orderbook_diagnostics.get("enriched_files_read")),
        "enriched_file_paths": list(orderbook_diagnostics.get("enriched_file_paths") or []),
        "rows_with_existing_top_of_book": with_existing_top_of_book,
        "rows_with_fresh_orderbook": with_fresh_orderbook,
        "rows_with_stale_top_of_book": with_stale_top_of_book,
        "rows_with_full_orderbook_missing": with_full_orderbook_missing,
        "rows_with_bid_ask_size_timestamp": with_bid_ask_size_timestamp,
        "orderbook_enrichment_market_rows": _int(orderbook_diagnostics.get("orderbook_enrichment_market_rows")),
        "orderbook_enrichment_enriched_rows": _int(orderbook_diagnostics.get("orderbook_enrichment_enriched_rows")),
        "orderbook_enrichment_fresh_orderbook_fetch_enriched_rows": _int(
            orderbook_diagnostics.get("orderbook_enrichment_fresh_orderbook_fetch_enriched_rows")
        ),
        "orderbook_enrichment_existing_top_of_book_rows": _int(
            orderbook_diagnostics.get("orderbook_enrichment_existing_top_of_book_rows")
        ),
        "orderbook_enrichment_full_orderbook_missing_rows": _int(
            orderbook_diagnostics.get("orderbook_enrichment_full_orderbook_missing_rows")
        ),
        "orderbook_enrichment_stale_existing_top_of_book_rows": _int(
            orderbook_diagnostics.get("orderbook_enrichment_stale_existing_top_of_book_rows")
        ),
        "kalshi_live_orderbook_fetch_supported": bool(orderbook_diagnostics.get("kalshi_live_orderbook_fetch_supported")),
        "kalshi_live_orderbook_fetch_not_enabled_or_missing_count": blocker_counts[
            B_KALSHI_LIVE_ORDERBOOK_FETCH_NOT_ENABLED_OR_MISSING
        ],
        "possible_cdna_peer_rows": cdna_hint_rows,
        "possible_polymarket_peer_rows": polymarket_hint_rows,
        "no_saved_peer_rows": no_peer_rows,
        "date_threshold_comparator_overlap_rows": len(overlap_rows),
        "exact_ready_rows": 0,
        "paper_candidate_rows": 0,
        "execution_ready_rows": 0,
        "asset_counts": dict(asset_counts),
        "shape_counts": dict(shape_counts),
        "top_blockers": top_blockers,
        "top_20_by_completeness": top_20,
        "top_10_peer_hint_rows": top_peers,
        "next_action": next_action,
        "next_action_reason": next_action_reason,
    }


def _next_action(
    *,
    point_in_time: int,
    typed_complete: int,
    deadline_or_range: int,
    cdna_hint_rows: int,
    polymarket_hint_rows: int,
    overlap_count: int,
    kalshi_total: int,
) -> tuple[str, str]:
    if kalshi_total == 0:
        return ("FETCH_SAVED_KALSHI_CRYPTO", "no saved kalshi crypto rows found in normalized_markets_v0.json")
    if overlap_count > 0:
        return (
            "MANUAL_REVIEW_DATE_THRESHOLD_COMPARATOR_OVERLAPS",
            f"{overlap_count} kalshi rows have loose (asset, target_date, threshold, comparator) overlap with CDNA or Polymarket peers — manual review of settlement source / window / payoff scope before any pairing claim",
        )
    if point_in_time > 0 and cdna_hint_rows == 0 and polymarket_hint_rows == 0:
        return (
            "REFRESH_OR_NORMALIZE_PEER_LANE",
            "kalshi has point-in-time crypto rows but no CDNA or Polymarket peer with matching asset/date/threshold — refresh those peer lanes",
        )
    if typed_complete == 0 and point_in_time > 0:
        return (
            "IMPROVE_TYPED_KEY_EXTRACTION",
            "no kalshi crypto row passed typed-key completeness — extend the rules-text parser for missing field (likely settlement source or timezone)",
        )
    return (
        "WATCH",
        "no immediate next-action signal; keep diagnostic loop running",
    )


def _row_sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
    return (
        -(row.get("typed_completeness_score") or 0),
        0 if row.get("market_shape") == SHAPE_POINT_IN_TIME else 1,
        str(row.get("ticker") or ""),
    )


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def _safety_block() -> dict[str, bool]:
    return {
        "diagnostic_only": True,
        "saved_files_only": True,
        "live_fetch_attempted": False,
        "execution_or_order_logic_added": False,
        "account_or_auth_logic_added": False,
        "wallet_or_signing_or_account_logic_added": False,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "exact_ready": False,
        "execution_ready": False,
        "paper_candidate": False,
        "affects_evaluator_gates": False,
        "source_registry_unchanged": True,
        "source_exact_payoff_compatible_with_kalshi": False,
        "treats_title_similarity_as_settlement_equivalence": False,
        "treats_deadline_or_range_hit_as_point_in_time": False,
        "infers_threshold_or_comparator_from_midpoint_or_complement": False,
    }


def _load_json(path: Path, warnings: list[dict[str, Any]], reason: str) -> Any:
    if not path.exists():
        warnings.append({"source_file": str(path), "reason_code": f"{reason}_missing", "blocker": f"{reason}_missing"})
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(
            {
                "source_file": str(path),
                "reason_code": f"{reason}_unreadable",
                "blocker": f"{reason}_unreadable:{type(exc).__name__}",
            }
        )
        return None


def _load_optional_json(path: Path, warnings: list[dict[str, Any]], reason: str) -> Any:
    if not path.exists():
        return None
    return _load_json(path, warnings, reason)


def _first_present(container: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in container and container[key] is not None:
            return container[key]
    return None


def _parse_iso(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _repo_relative_path(path_value: str | None) -> str:
    if not path_value:
        return ""
    path_text = str(path_value).replace("\\", "/")
    marker = "relative-value-scanner/"
    if marker in path_text:
        path_text = path_text.split(marker, 1)[1]
    return path_text


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
