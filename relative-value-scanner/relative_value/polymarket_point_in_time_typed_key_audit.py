from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
SCHEMA_KIND = "polymarket_point_in_time_typed_key_audit_v1"
REPORT_SOURCE = "polymarket_point_in_time_typed_key_audit_v1"

SHAPE_POINT_IN_TIME = "point_in_time_threshold"

B_MISSING_ASSET_OR_FAMILY = "missing_asset_or_family"
B_MISSING_THRESHOLD = "missing_threshold"
B_MISSING_COMPARATOR = "missing_comparator"
B_MISSING_TARGET_DATE = "missing_target_date"
B_MISSING_TARGET_TIME = "missing_target_time"
B_MISSING_TIMEZONE = "missing_timezone"
B_MISSING_SETTLEMENT_SOURCE = "missing_settlement_source"
B_MISSING_CONDITION_ID = "missing_condition_id"
B_MISSING_TOKEN_ID = "missing_token_id"
B_MISSING_CLOB_BOOK = "missing_clob_book"
B_STALE_OR_MISSING_QUOTE = "stale_or_missing_quote"
B_TITLE_ONLY_MATCH = "title_only_match_not_equivalence"
B_NO_SAVED_PEER_FAMILY = "no_saved_peer_family"
B_PEER_FAMILY_BASIS_RISK_ONLY = "peer_family_basis_risk_only"

_ALL_TIME_HIGH_BY_DEADLINE_RE = re.compile(
    r"\b(?:all[-\s]?time\s+high|ath)\b.{0,160}\b(?:by|before|at\s+any\s+time\s+before)\b",
    re.IGNORECASE,
)
_DEADLINE_TOUCH_BY_RE = re.compile(
    r"\b(?:hit|hits|reaches|reach|reached|touches|touch|touched)\b.{0,160}\b(?:by|before|at\s+any\s+time\s+before)\b",
    re.IGNORECASE,
)
_BY_DEADLINE_TOUCH_RE = re.compile(
    r"\b(?:by|before|at\s+any\s+time\s+before)\b.{0,160}\b(?:hit|hits|reaches|reach|reached|touches|touch|touched)\b",
    re.IGNORECASE,
)
_INTERVAL_TOUCH_RE = re.compile(
    r"\b(?:hit|hits|reaches|reach|reached|touches|touch|touched)\b.{0,160}\b(?:in|during)\s+"
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
    r"sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|q[1-4]|20\d{2})\b",
    re.IGNORECASE,
)
_FORBIDDEN_POINT_TEXT_RE = re.compile(
    r"\b(?:deadline|range[-\s]?hit|hit\s+by|reach\s+by|touch\s+by|before|at\s+any\s+time\s+before)\b",
    re.IGNORECASE,
)
_TIMEZONE_RE = re.compile(r"\b(?:ET|EST|EDT|UTC|GMT|PT|PST|PDT|CT|CST|CDT)\b", re.IGNORECASE)


def build_polymarket_point_in_time_typed_key_audit_report(
    *,
    taxonomy_json: Path,
    enriched_json: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    warnings: list[dict[str, Any]] = []
    taxonomy_payload = _load_json(taxonomy_json, warnings=warnings, input_name="taxonomy_json")
    enriched_payload = _load_json(enriched_json, warnings=warnings, input_name="enriched_json")
    taxonomy_rows = _rows_from_payload(taxonomy_payload)
    enriched_by_id = {
        str(row.get("row_id")): row
        for row in _rows_from_payload(enriched_payload)
        if row.get("row_id") is not None
    }
    peer_inventory = _saved_peer_inventory(taxonomy_json.parent)

    rows: list[dict[str, Any]] = []
    excluded_fake_point_rows = 0
    point_rows_seen = 0
    for row in taxonomy_rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("market_shape") or "").strip().lower() != SHAPE_POINT_IN_TIME:
            continue
        point_rows_seen += 1
        if _has_deadline_or_range_hit_text(row):
            excluded_fake_point_rows += 1
            continue
        row_id = str(row.get("row_id") or "")
        enriched_row = enriched_by_id.get(row_id, row)
        rows.append(_audit_one_row(row=row, enriched_row=enriched_row, peer_inventory=peer_inventory))

    rows.sort(
        key=lambda r: (
            -float(r.get("typed_key_completeness_score") or 0.0),
            0 if _row_has_peer_hint(r) else 1,
            0 if r.get("targeted_clob_refresh_candidate") else 1,
            str(r.get("row_id") or ""),
        )
    )
    summary = _summary(rows, point_rows_seen=point_rows_seen, excluded_fake_point_rows=excluded_fake_point_rows)
    return {
        "schema_kind": SCHEMA_KIND,
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "taxonomy_json": str(taxonomy_json),
        "enriched_json": str(enriched_json),
        "diagnostic_only": True,
        "saved_files_only": True,
        "summary": summary,
        "rows": rows,
        "warnings": warnings,
        "safety": _safety_block(),
    }


def write_polymarket_point_in_time_typed_key_audit_files(
    *,
    taxonomy_json: Path,
    enriched_json: Path,
    json_output: Path,
    markdown_output: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_polymarket_point_in_time_typed_key_audit_report(
        taxonomy_json=taxonomy_json,
        enriched_json=enriched_json,
        generated_at=generated_at,
    )
    report["report_path"] = str(json_output)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_polymarket_point_in_time_typed_key_audit_markdown(report), encoding="utf-8")
    return report


def render_polymarket_point_in_time_typed_key_audit_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Polymarket Point-In-Time Typed-Key Audit",
        "",
        "Saved-file-only diagnostic for remaining Polymarket point-in-time taxonomy rows. It ranks typed-key completeness and CLOB refresh targets; it does not create candidate pairs or executable claims.",
        "",
        "## Summary",
        "",
        f"- point_in_time_rows_seen: `{summary.get('point_in_time_rows_seen', 0)}`",
        f"- point_in_time_rows_audited: `{summary.get('point_in_time_rows_audited', 0)}`",
        f"- excluded_fake_point_in_time_rows: `{summary.get('excluded_fake_point_in_time_rows', 0)}`",
        f"- typed_complete_rows: `{summary.get('typed_complete_rows', 0)}`",
        f"- targeted_clob_refresh_candidate_rows: `{summary.get('targeted_clob_refresh_candidate_rows', 0)}`",
        f"- rows_with_clob_attached: `{summary.get('rows_with_clob_attached', 0)}`",
        f"- rows_with_bid_ask_size: `{summary.get('rows_with_bid_ask_size', 0)}`",
        f"- exact_ready_rows: `0`",
        f"- paper_candidate_rows: `0`",
        "",
        "## Family Counts",
        "",
        "| Family | Rows |",
        "|---|---:|",
    ]
    for family, count in sorted((summary.get("market_family_counts") or {}).items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {_md(family)} | {count} |")
    lines.extend(["", "## Top Blockers", "", "| Blocker | Count |", "|---|---:|"])
    for item in summary.get("top_blockers") or []:
        lines.append(f"| {_md(item.get('blocker'))} | {item.get('count', 0)} |")
    lines.extend(["", "## Top 20 Candidates", ""])
    _append_candidate_table(lines, summary.get("top_20_candidates") or [])
    lines.extend(["", "## Strong Typed Keys Missing CLOB", ""])
    _append_candidate_table(lines, summary.get("top_targeted_clob_refresh_candidates") or [])
    lines.extend(["", "## Strong Typed Keys With CLOB", ""])
    _append_candidate_table(lines, summary.get("top_typed_with_clob_attached") or [])
    lines.extend(["", "## Missing Target Date Or Source", ""])
    _append_candidate_table(lines, summary.get("top_missing_target_date_or_source") or [])
    lines.extend(
        [
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
        ]
    )
    return "\n".join(lines) + "\n"


def _append_candidate_table(lines: list[str], rows: list[dict[str, Any]]) -> None:
    if not rows:
        lines.append("_None._")
        return
    lines.extend(
        [
            "| # | Score | Family | Peer Hints | CLOB | Row | Top Blockers |",
            "|---:|---:|---|---|---|---|---|",
        ]
    )
    for index, row in enumerate(rows[:20], start=1):
        peer_hints = row.get("peer_lane_hints") or {}
        peer_text = ", ".join(
            str(peer_hints.get(key))
            for key in ("likely_kalshi_peer_family", "likely_cdna_peer_family", "likely_ibkr_forecastex_peer_family")
            if peer_hints.get(key)
        ) or "none"
        quote = row.get("quote") or {}
        clob_text = (
            f"attached={str(bool(row.get('clob_book_attached'))).lower()} "
            f"bid={quote.get('bid')} ask={quote.get('ask')} ts={quote.get('quote_timestamp')}"
        )
        label = row.get("market_slug") or row.get("question") or row.get("row_id")
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    f"{float(row.get('typed_key_completeness_score') or 0.0):.1f}",
                    _md(row.get("market_family")),
                    _md(peer_text),
                    _md(clob_text),
                    _md(str(label)[:96]),
                    _md(", ".join((row.get("blockers") or [])[:4]) or "none"),
                ]
            )
            + " |"
        )


def _audit_one_row(
    *,
    row: dict[str, Any],
    enriched_row: dict[str, Any],
    peer_inventory: dict[str, bool],
) -> dict[str, Any]:
    typed = row.get("typed_keys") if isinstance(row.get("typed_keys"), dict) else {}
    quote = _attached_quote(enriched_row)
    market_family = _market_family(row)
    asset_or_family = _asset_or_family(row, typed, market_family)
    threshold = _first_present(typed, ("threshold_value", "threshold", "strike", "price_level"))
    comparator = _first_present(typed, ("threshold_operator", "comparator", "operator"))
    target_date = _first_present(typed, ("measurement_date", "deadline_or_date", "date", "event_date", "settlement_date"))
    target_time = _first_present(typed, ("measurement_time", "target_time", "time", "settlement_time"))
    timezone_value = _first_present(typed, ("timezone", "target_timezone", "measurement_timezone")) or _timezone_from_text(target_time)
    settlement_source_present = bool(row.get("settlement_source_present"))
    condition_id = row.get("condition_id")
    token_ids = _token_ids(row.get("token_ids"))
    clob_attached = _clob_attached(enriched_row, quote)
    quote_timestamp = quote.get("quote_timestamp")
    blockers = _blockers(
        asset_or_family=asset_or_family,
        threshold=threshold,
        comparator=comparator,
        target_date=target_date,
        target_time=target_time,
        timezone_value=timezone_value,
        settlement_source_present=settlement_source_present,
        condition_id=condition_id,
        token_ids=token_ids,
        clob_attached=clob_attached,
        quote_timestamp=quote_timestamp,
        market_family=market_family,
        peer_hints=_peer_hints(market_family=market_family, asset_or_family=asset_or_family, peer_inventory=peer_inventory),
    )
    peer_hints = _peer_hints(market_family=market_family, asset_or_family=asset_or_family, peer_inventory=peer_inventory)
    score = _typed_key_score(
        asset_or_family=asset_or_family,
        threshold=threshold,
        comparator=comparator,
        target_date=target_date,
        target_time=target_time,
        timezone_value=timezone_value,
        settlement_source_present=settlement_source_present,
        condition_id=condition_id,
        token_ids=token_ids,
        clob_attached=clob_attached,
        quote_timestamp=quote_timestamp,
    )
    typed_complete = not any(
        blocker in blockers
        for blocker in (
            B_MISSING_ASSET_OR_FAMILY,
            B_MISSING_THRESHOLD,
            B_MISSING_COMPARATOR,
            B_MISSING_TARGET_DATE,
            B_MISSING_TARGET_TIME,
            B_MISSING_TIMEZONE,
            B_MISSING_SETTLEMENT_SOURCE,
            B_MISSING_CONDITION_ID,
            B_MISSING_TOKEN_ID,
        )
    )
    targeted_refresh = bool(
        typed_complete
        and not clob_attached
        and score >= 75.0
    )
    return {
        "row_id": row.get("row_id"),
        "market_id": row.get("market_id"),
        "condition_id": condition_id,
        "event_id": row.get("event_id"),
        "event_slug": row.get("event_slug"),
        "market_slug": row.get("market_slug"),
        "venue": row.get("venue") or "polymarket",
        "source_url": row.get("source_url"),
        "raw_source_file": row.get("raw_source_file"),
        "question": row.get("question"),
        "title": row.get("title"),
        "market_shape": SHAPE_POINT_IN_TIME,
        "market_family": market_family,
        "asset_or_family": asset_or_family,
        "threshold": threshold,
        "comparator": comparator,
        "target_date": target_date,
        "target_time": target_time,
        "timezone": timezone_value,
        "settlement_source_present": settlement_source_present,
        "token_ids": token_ids,
        "typed_keys": typed,
        "typed_key_complete_for_review": typed_complete,
        "typed_key_completeness_score": round(score, 2),
        "peer_lane_hints": peer_hints,
        "clob_book_attached": clob_attached,
        "quote": quote,
        "targeted_clob_refresh_candidate": targeted_refresh,
        "blockers": blockers,
        "diagnostic_only": True,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "exact_ready": False,
        "execution_ready": False,
        "paper_candidate": False,
        "affects_evaluator_gates": False,
    }


def _blockers(
    *,
    asset_or_family: Any,
    threshold: Any,
    comparator: Any,
    target_date: Any,
    target_time: Any,
    timezone_value: Any,
    settlement_source_present: bool,
    condition_id: Any,
    token_ids: list[str],
    clob_attached: bool,
    quote_timestamp: Any,
    market_family: str,
    peer_hints: dict[str, Any],
) -> list[str]:
    blockers: list[str] = []
    if not asset_or_family:
        blockers.append(B_MISSING_ASSET_OR_FAMILY)
    if threshold is None or threshold == "":
        blockers.append(B_MISSING_THRESHOLD)
    if not comparator:
        blockers.append(B_MISSING_COMPARATOR)
    if not target_date:
        blockers.append(B_MISSING_TARGET_DATE)
    if not target_time:
        blockers.append(B_MISSING_TARGET_TIME)
    if not timezone_value:
        blockers.append(B_MISSING_TIMEZONE)
    if not settlement_source_present:
        blockers.append(B_MISSING_SETTLEMENT_SOURCE)
    if not condition_id:
        blockers.append(B_MISSING_CONDITION_ID)
    if not token_ids:
        blockers.append(B_MISSING_TOKEN_ID)
    if not clob_attached:
        blockers.append(B_MISSING_CLOB_BOOK)
    if not quote_timestamp:
        blockers.append(B_STALE_OR_MISSING_QUOTE)
    blockers.append(B_TITLE_ONLY_MATCH)
    if not any(
        peer_hints.get(key)
        for key in ("likely_kalshi_peer_family", "likely_cdna_peer_family", "likely_ibkr_forecastex_peer_family")
    ):
        blockers.append(B_NO_SAVED_PEER_FAMILY)
    if market_family == "crypto_price" and peer_hints.get("likely_cdna_peer_family"):
        blockers.append(B_PEER_FAMILY_BASIS_RISK_ONLY)
    return list(dict.fromkeys(blockers))


def _typed_key_score(
    *,
    asset_or_family: Any,
    threshold: Any,
    comparator: Any,
    target_date: Any,
    target_time: Any,
    timezone_value: Any,
    settlement_source_present: bool,
    condition_id: Any,
    token_ids: list[str],
    clob_attached: bool,
    quote_timestamp: Any,
) -> float:
    score = 0.0
    if asset_or_family:
        score += 12.0
    if threshold is not None and threshold != "":
        score += 15.0
    if comparator:
        score += 12.0
    if target_date:
        score += 15.0
    if target_time:
        score += 10.0
    if timezone_value:
        score += 8.0
    if settlement_source_present:
        score += 12.0
    if condition_id:
        score += 6.0
    if token_ids:
        score += 6.0
    if clob_attached:
        score += 2.0
    if quote_timestamp:
        score += 2.0
    return max(0.0, min(100.0, score))


def _summary(rows: list[dict[str, Any]], *, point_rows_seen: int, excluded_fake_point_rows: int) -> dict[str, Any]:
    blockers: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()
    typed_complete = 0
    targeted = 0
    clob_attached = 0
    bid_ask_size = 0
    target_missing = 0
    peer_counts: Counter[str] = Counter()
    for row in rows:
        family_counts[str(row.get("market_family") or "other")] += 1
        if row.get("typed_key_complete_for_review"):
            typed_complete += 1
        if row.get("targeted_clob_refresh_candidate"):
            targeted += 1
        if row.get("clob_book_attached"):
            clob_attached += 1
        quote = row.get("quote") or {}
        if (
            quote.get("bid") is not None
            and quote.get("ask") is not None
            and quote.get("bid_size") is not None
            and quote.get("ask_size") is not None
        ):
            bid_ask_size += 1
        if B_MISSING_TARGET_DATE in (row.get("blockers") or []) or B_MISSING_SETTLEMENT_SOURCE in (row.get("blockers") or []):
            target_missing += 1
        for blocker in row.get("blockers") or []:
            blockers[str(blocker)] += 1
        hints = row.get("peer_lane_hints") or {}
        for key in ("likely_kalshi_peer_family", "likely_cdna_peer_family", "likely_ibkr_forecastex_peer_family"):
            value = hints.get(key)
            if value:
                peer_counts[str(value)] += 1
    top_rows = [_summary_row(row) for row in rows[:20]]
    return {
        "point_in_time_rows_seen": point_rows_seen,
        "point_in_time_rows_audited": len(rows),
        "excluded_fake_point_in_time_rows": excluded_fake_point_rows,
        "typed_complete_rows": typed_complete,
        "targeted_clob_refresh_candidate_rows": targeted,
        "rows_with_clob_attached": clob_attached,
        "rows_with_bid_ask_size": bid_ask_size,
        "rows_missing_target_date_or_source": target_missing,
        "exact_ready_rows": 0,
        "paper_candidate_rows": 0,
        "execution_ready_rows": 0,
        "market_family_counts": dict(family_counts),
        "peer_lane_hint_counts": dict(peer_counts),
        "top_blockers": [{"blocker": b, "count": c} for b, c in blockers.most_common(15)],
        "top_20_candidates": top_rows,
        "top_targeted_clob_refresh_candidates": [
            _summary_row(row) for row in rows if row.get("targeted_clob_refresh_candidate")
        ][:20],
        "top_typed_with_clob_attached": [
            _summary_row(row)
            for row in rows
            if row.get("typed_key_complete_for_review") and row.get("clob_book_attached")
        ][:20],
        "top_missing_target_date_or_source": [
            _summary_row(row)
            for row in rows
            if B_MISSING_TARGET_DATE in (row.get("blockers") or [])
            or B_MISSING_SETTLEMENT_SOURCE in (row.get("blockers") or [])
        ][:20],
    }


def _summary_row(row: dict[str, Any]) -> dict[str, Any]:
    quote = row.get("quote") or {}
    return {
        "row_id": row.get("row_id"),
        "market_id": row.get("market_id"),
        "condition_id": row.get("condition_id"),
        "market_slug": row.get("market_slug"),
        "question": row.get("question"),
        "market_family": row.get("market_family"),
        "asset_or_family": row.get("asset_or_family"),
        "threshold": row.get("threshold"),
        "comparator": row.get("comparator"),
        "target_date": row.get("target_date"),
        "target_time": row.get("target_time"),
        "timezone": row.get("timezone"),
        "typed_key_completeness_score": row.get("typed_key_completeness_score"),
        "typed_key_complete_for_review": row.get("typed_key_complete_for_review"),
        "targeted_clob_refresh_candidate": row.get("targeted_clob_refresh_candidate"),
        "clob_book_attached": row.get("clob_book_attached"),
        "bid": quote.get("bid"),
        "ask": quote.get("ask"),
        "bid_size": quote.get("bid_size"),
        "ask_size": quote.get("ask_size"),
        "quote_timestamp": quote.get("quote_timestamp"),
        "peer_lane_hints": row.get("peer_lane_hints"),
        "blockers": list(row.get("blockers") or [])[:8],
    }


def _row_has_peer_hint(row: dict[str, Any]) -> bool:
    hints = row.get("peer_lane_hints") or {}
    return any(
        hints.get(key)
        for key in ("likely_kalshi_peer_family", "likely_cdna_peer_family", "likely_ibkr_forecastex_peer_family")
    )


def _attached_quote(row: dict[str, Any]) -> dict[str, Any]:
    refresh = row.get("clob_refresh") if isinstance(row.get("clob_refresh"), dict) else {}
    quote = refresh.get("attached_quote") if isinstance(refresh.get("attached_quote"), dict) else None
    if quote is not None:
        return {
            "bid": quote.get("bid"),
            "ask": quote.get("ask"),
            "bid_size": quote.get("bid_size"),
            "ask_size": quote.get("ask_size"),
            "quote_timestamp": quote.get("quote_timestamp") or quote.get("observed_at") or quote.get("raw_book_source_timestamp"),
            "observed_at": quote.get("observed_at"),
            "raw_book_file": quote.get("raw_book_file"),
            "token_id": quote.get("token_id"),
            "condition_id": quote.get("condition_id") or row.get("condition_id"),
            "attached": bool(quote.get("attached")),
            "inferred_from_midpoint_or_complement": bool(quote.get("inferred_from_midpoint_or_complement")),
        }
    book = row.get("clob_book") if isinstance(row.get("clob_book"), dict) else {}
    return {
        "bid": book.get("best_bid"),
        "ask": book.get("best_ask"),
        "bid_size": book.get("depth_at_best_bid"),
        "ask_size": book.get("depth_at_best_ask"),
        "quote_timestamp": book.get("orderbook_captured_at"),
        "observed_at": book.get("orderbook_captured_at"),
        "raw_book_file": None,
        "token_id": None,
        "condition_id": row.get("condition_id"),
        "attached": bool(book),
        "inferred_from_midpoint_or_complement": False,
    }


def _clob_attached(row: dict[str, Any], quote: dict[str, Any]) -> bool:
    return bool(
        row.get("clob_book_attached")
        or (
            quote.get("attached")
            and quote.get("bid") is not None
            and quote.get("ask") is not None
        )
    )


def _market_family(row: dict[str, Any]) -> str:
    family = str(row.get("family") or "").upper()
    text = _combined_text(row).lower()
    if "best performance" in text:
        return "other"
    if family in {"TECH_COMPANY_PRODUCT", "TECH_AI"} or any(
        token in text
        for token in ("market cap", "ipo", "fdv", "stock", "public sale", "committed to", "valuation")
    ):
        return "company_metric"
    if any(token in text for token in ("bitcoin", "ethereum", " btc", " eth", "crypto", "solana", "xrp", "doge")):
        return "crypto_price"
    if family == "CRYPTO":
        return "crypto_price"
    if family == "MACRO_FED_RATES" or "fomc" in text or "fed " in text or "fed's" in text:
        return "macro_rate"
    if family.startswith("SPORTS"):
        return "sports"
    if family == "POLITICS_ELECTION_RESULT" or "election" in text:
        return "election"
    if family == "WEATHER" or "weather" in text or "temperature" in text:
        return "weather"
    return "other"


def _asset_or_family(row: dict[str, Any], typed: dict[str, Any], market_family: str) -> str | None:
    value = _first_present(typed, ("asset", "entity", "underlying", "indicator"))
    if value:
        return str(value)
    text = _combined_text(row).upper()
    if "BITCOIN" in text or " BTC" in f" {text}":
        return "BTC"
    if "ETHEREUM" in text or " ETH" in f" {text}":
        return "ETH"
    family = row.get("family")
    if family:
        return str(family)
    return market_family if market_family != "other" else None


def _peer_hints(*, market_family: str, asset_or_family: Any, peer_inventory: dict[str, bool]) -> dict[str, Any]:
    asset = str(asset_or_family or "").upper()
    hints = {
        "likely_kalshi_peer_family": None,
        "likely_cdna_peer_family": None,
        "likely_ibkr_forecastex_peer_family": None,
        "likely_no_current_peer": False,
    }
    if market_family == "crypto_price":
        if asset in {"BTC", "ETH", "BITCOIN", "ETHEREUM"} and peer_inventory.get("kalshi_crypto"):
            hints["likely_kalshi_peer_family"] = "KALSHI_CRYPTO_PRICE_THRESHOLD"
        if asset in {"BTC", "ETH", "BITCOIN", "ETHEREUM"} and peer_inventory.get("cdna_crypto"):
            hints["likely_cdna_peer_family"] = "CDNA_CRYPTO_POINT_IN_TIME_BASIS_RISK_REVIEW"
    elif market_family == "macro_rate":
        if peer_inventory.get("kalshi_fed"):
            hints["likely_kalshi_peer_family"] = "KALSHI_FED_FOMC"
        if peer_inventory.get("ibkr_forecastex_fed"):
            hints["likely_ibkr_forecastex_peer_family"] = "IBKR_FORECASTX_FED_FOMC"
    elif market_family == "weather" and peer_inventory.get("kalshi_weather"):
        hints["likely_kalshi_peer_family"] = "KALSHI_WEATHER"
    elif market_family == "election" and peer_inventory.get("kalshi_election"):
        hints["likely_kalshi_peer_family"] = "KALSHI_ELECTION"
    hints["likely_no_current_peer"] = not any(
        hints.get(key)
        for key in ("likely_kalshi_peer_family", "likely_cdna_peer_family", "likely_ibkr_forecastex_peer_family")
    )
    return hints


def _saved_peer_inventory(input_dir: Path) -> dict[str, bool]:
    inventory = {
        "kalshi_crypto": False,
        "kalshi_fed": False,
        "kalshi_weather": False,
        "kalshi_election": False,
        "cdna_crypto": False,
        "ibkr_forecastex_fed": False,
    }
    normalized = _read_json_if_exists(input_dir / "normalized_markets_v0.json")
    for row in _rows_from_key(normalized, "normalized_markets"):
        if not isinstance(row, dict) or str(row.get("venue") or "").lower() != "kalshi":
            continue
        text = " ".join(str(row.get(key) or "") for key in ("event_ticker", "ticker", "market_id", "title")).upper()
        if "KXBTC" in text or "KXETH" in text or "BITCOIN" in text or "ETHEREUM" in text:
            inventory["kalshi_crypto"] = True
        if "KXFED" in text or "FOMC" in text or "FEDERAL FUNDS" in text:
            inventory["kalshi_fed"] = True
        if "WEATHER" in text or "TEMP" in text or "HIGH" in text and "TEMPERATURE" in text:
            inventory["kalshi_weather"] = True
        if "ELECTION" in text or "PRESIDENT" in text:
            inventory["kalshi_election"] = True
    cdna = _read_json_if_exists(input_dir / "crypto_com_predict_cdna_research_snapshot.json")
    for row in _rows_from_key(cdna, "rows"):
        if isinstance(row, dict) and str(row.get("asset") or "").upper() in {"BTC", "ETH"}:
            inventory["cdna_crypto"] = True
    ibkr = _read_json_if_exists(input_dir / "ibkr_forecastex_quote_diagnostics.json")
    summary = ibkr.get("summary") if isinstance(ibkr, dict) and isinstance(ibkr.get("summary"), dict) else {}
    if int(summary.get("final_contract_rows") or 0) > 0:
        inventory["ibkr_forecastex_fed"] = True
    return inventory


def _has_deadline_or_range_hit_text(row: dict[str, Any]) -> bool:
    text = _combined_text(row)
    return bool(
        _ALL_TIME_HIGH_BY_DEADLINE_RE.search(text)
        or _DEADLINE_TOUCH_BY_RE.search(text)
        or _BY_DEADLINE_TOUCH_RE.search(text)
        or _INTERVAL_TOUCH_RE.search(text)
        or _FORBIDDEN_POINT_TEXT_RE.search(text)
    )


def _combined_text(row: dict[str, Any]) -> str:
    return " ".join(
        str(row.get(key) or "")
        for key in ("question", "title", "market_slug", "event_slug")
        if row.get(key)
    )


def _timezone_from_text(value: Any) -> str | None:
    if not value:
        return None
    match = _TIMEZONE_RE.search(str(value))
    return match.group(0).upper() if match else None


def _first_present(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None and value != "":
            return value
    return None


def _token_ids(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "").strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _load_json(path: Path, *, warnings: list[dict[str, Any]], input_name: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        warnings.append({"input_name": input_name, "source_file": str(path), "blocker": "saved_input_missing"})
        return {}
    except json.JSONDecodeError as exc:
        warnings.append(
            {
                "input_name": input_name,
                "source_file": str(path),
                "blocker": "saved_input_unreadable",
                "detail": f"{type(exc).__name__}",
            }
        )
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_json_if_exists(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _rows_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("rows") if isinstance(payload, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _rows_from_key(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = payload.get(key) if isinstance(payload, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _safety_block() -> dict[str, bool]:
    return {
        "diagnostic_only": True,
        "saved_files_only": True,
        "live_fetch_attempted": False,
        "execution_or_order_logic_added": False,
        "account_or_auth_logic_added": False,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "exact_ready": False,
        "execution_ready": False,
        "paper_candidate": False,
        "affects_evaluator_gates": False,
        "source_registry_unchanged": True,
    }


def _md(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "/").replace("\n", " ")
