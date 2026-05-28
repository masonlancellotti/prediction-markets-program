from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
SCHEMA_KIND = "polymarket_taxonomy_shape_scout_v1"
REPORT_SOURCE = "polymarket_taxonomy_shape_scout_v1"


# Allowed diagnostic actions only. No PAPER_CANDIDATE, no exact-equality.
ACTION_WATCH = "WATCH"
ACTION_MANUAL_REVIEW = "MANUAL_REVIEW"
ACTION_SOURCE_REVIEW = "SOURCE_REVIEW"
ACTION_BASIS_RISK_REVIEW = "BASIS_RISK_REVIEW"
ALLOWED_ACTIONS = (ACTION_WATCH, ACTION_MANUAL_REVIEW, ACTION_SOURCE_REVIEW, ACTION_BASIS_RISK_REVIEW)


# Spec-required shape vocabulary.
SHAPE_POINT_IN_TIME = "point_in_time_threshold"
SHAPE_DEADLINE = "deadline_threshold_touch"
SHAPE_RANGE_HIT = "range_hit"
SHAPE_RANGE_BUCKET = "range_bucket"
SHAPE_EVENT_WINNER = "event_winner"
SHAPE_ELECTION_CANDIDATE = "election_candidate"
SHAPE_MACRO_RATE_MEETING = "macro_rate_meeting"
SHAPE_SPORTS_FUTURES = "sports_futures"
SHAPE_CRYPTO_DEADLINE_RANGE_HIT = "crypto_deadline_range_hit"
SHAPE_AMBIGUOUS = "ambiguous"
SHAPE_SPORTS_GAME = "sports_game"
SHAPE_TECH_RELEASE = "tech_release"
SHAPE_COMPANY_THRESHOLD = "company_threshold"
SHAPE_MACRO_RELEASE = "macro_economic_release"
SHAPE_ALL_TIME_HIGH_BY_DATE = "all_time_high_by_date"

ALLOWED_SHAPES = (
    SHAPE_POINT_IN_TIME,
    SHAPE_DEADLINE,
    SHAPE_RANGE_HIT,
    SHAPE_RANGE_BUCKET,
    SHAPE_EVENT_WINNER,
    SHAPE_ELECTION_CANDIDATE,
    SHAPE_MACRO_RATE_MEETING,
    SHAPE_SPORTS_FUTURES,
    SHAPE_CRYPTO_DEADLINE_RANGE_HIT,
    SHAPE_SPORTS_GAME,
    SHAPE_TECH_RELEASE,
    SHAPE_COMPANY_THRESHOLD,
    SHAPE_MACRO_RELEASE,
    SHAPE_ALL_TIME_HIGH_BY_DATE,
    SHAPE_AMBIGUOUS,
)


# Mapping from existing polymarket_market_taxonomy market_shape values.
_TAXONOMY_SHAPE_MAP = {
    "POINT_IN_TIME_THRESHOLD": SHAPE_POINT_IN_TIME,
    "DEADLINE_HIT_BY_DATE": SHAPE_DEADLINE,
    "RANGE_BUCKET": SHAPE_RANGE_BUCKET,
    "UP_DOWN_INTERVAL": SHAPE_RANGE_HIT,
    "NOMINATION_WINNER": SHAPE_ELECTION_CANDIDATE,
    "ELECTION_WINNER": SHAPE_EVENT_WINNER,
    "BINARY_EVENT_RESULT": SHAPE_EVENT_WINNER,
    "SPORTS_FUTURES_WINNER": SHAPE_SPORTS_FUTURES,
    "SPORTS_MONEYLINE": SHAPE_SPORTS_GAME,
    "SPORTS_TOTAL": SHAPE_SPORTS_GAME,
    "MACRO_RATE_TARGET": SHAPE_MACRO_RATE_MEETING,
    "ECONOMIC_RELEASE_THRESHOLD": SHAPE_MACRO_RELEASE,
    "COMPANY_MARKET_CAP_OR_PRICE_THRESHOLD": SHAPE_COMPANY_THRESHOLD,
    "TECH_RELEASE_OR_PRODUCT_EVENT": SHAPE_TECH_RELEASE,
    "UNKNOWN_OR_COMPOUND": SHAPE_AMBIGUOUS,
}

_DEADLINE_OR_RANGE_HIT_SHAPES = {
    SHAPE_DEADLINE,
    SHAPE_RANGE_HIT,
    SHAPE_RANGE_BUCKET,
    SHAPE_CRYPTO_DEADLINE_RANGE_HIT,
    SHAPE_ALL_TIME_HIGH_BY_DATE,
}


# Blockers.
B_DEADLINE_VS_POINT = "deadline_vs_point_in_time_mismatch"
B_RANGE_VS_CLOSE = "range_hit_vs_close_price_mismatch"
B_AMBIGUOUS_SHAPE = "ambiguous_contract_shape"
B_AMBIGUOUS_DATE = "ambiguous_date_or_deadline"
B_AMBIGUOUS_SOURCE = "ambiguous_settlement_source"
B_MISSING_CLOB_BOOK = "missing_clob_book"
B_STALE_OR_MISSING_QUOTE = "stale_or_missing_quote"
B_TITLE_ONLY_MATCH = "title_only_match_not_equivalence"
B_MULTI_CONDITION = "multi_condition_market_not_single_payoff"
B_MISSING_TYPED_KEY = "missing_typed_key"
B_SETTLEMENT_SOURCE_MISSING = "settlement_source_missing"
B_SETTLEMENT_RULES_MISSING = "settlement_rules_missing"
B_POLYMARKET_REGISTRY_BLOCKS_PAIR_CREATION = "polymarket_registry_blocks_pair_creation_until_review"
B_HIT_BY_DEADLINE_NOT_POINT_IN_TIME = "hit_by_deadline_not_point_in_time"
B_SETTLEMENT_WINDOW_MISMATCH = "settlement_window_mismatch"
B_EXACT_PAYOFF_NOT_PROVEN = "exact_payoff_not_proven"


_STALE_QUOTE_DAYS = 5
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
_AT_ANY_TIME_BEFORE_RE = re.compile(r"\bat\s+any\s+time\s+before\b", re.IGNORECASE)
_BEFORE_DEADLINE_RE = re.compile(r"\bbefore\b", re.IGNORECASE)


def build_polymarket_taxonomy_shape_scout_report(
    *,
    input_dir: Path,
    generated_at: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    moment = now or generated
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    warnings: list[dict[str, Any]] = []
    taxonomy_rows = _load_taxonomy(input_dir, warnings)
    clob_index = _load_clob_index(input_dir, warnings)

    rows: list[dict[str, Any]] = []
    for tax_row in taxonomy_rows:
        if not isinstance(tax_row, dict):
            continue
        rows.append(_compose_row(tax_row=tax_row, clob_index=clob_index, now=moment))

    rows.sort(key=lambda r: (-(r.get("exact_matchability_score") or 0.0), r.get("row_id") or ""))

    summary = _summary(rows)
    return {
        "schema_kind": SCHEMA_KIND,
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "input_dir": str(input_dir),
        "diagnostic_only": True,
        "summary": summary,
        "rows": rows,
        "warnings": warnings,
        "safety": _safety_block(),
    }


def write_polymarket_taxonomy_shape_scout_files(
    *,
    input_dir: Path,
    json_output: Path,
    markdown_output: Path,
    generated_at: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    report = build_polymarket_taxonomy_shape_scout_report(
        input_dir=input_dir,
        generated_at=generated_at,
        now=now,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_polymarket_taxonomy_shape_scout_markdown(report), encoding="utf-8")
    return report


def render_polymarket_taxonomy_shape_scout_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    rows = report.get("rows") or []
    lines = [
        "# Polymarket Taxonomy + Shape Scout",
        "",
        "Saved-file-only diagnostic. Ranks Polymarket markets by exact-matchability likelihood against Kalshi/IBKR/CDNA based on shape, typed-key completeness, and CLOB book attachment. Deadline/range-hit/range-bucket shapes are never claimed as exact point-in-time matches. No paper-candidate, no executable claim.",
        "",
        "## Executive Summary",
        "",
        f"- total_rows: `{summary.get('total_rows', 0)}`",
        f"- point_in_time_candidates: `{summary.get('point_in_time_candidates', 0)}`",
        f"- deadline_or_range_hit_blocked: `{summary.get('deadline_or_range_hit_blocked', 0)}`",
        f"- deadline_touch_phrase_rows: `{summary.get('deadline_touch_phrase_rows', 0)}`",
        f"- deadline_touch_phrase_reclassified_rows: `{summary.get('deadline_touch_phrase_reclassified_rows', 0)}`",
        f"- clob_book_attached: `{summary.get('clob_book_attached', 0)}`",
        f"- typed_key_complete: `{summary.get('typed_key_complete', 0)}`",
        f"- exact_ready_rows: `0`",
        f"- paper_candidate_rows: `0`",
        f"- execution_ready_rows: `0`",
        "",
        "## Counts By Category (family)",
        "",
        "| Family | Rows |",
        "|---|---:|",
    ]
    cat = summary.get("category_counts") or {}
    for family, count in sorted(cat.items(), key=lambda kv: -kv[1])[:25]:
        lines.append(f"| {family} | {count} |")
    lines.extend(["", "## Counts By Market Shape", "", "| Shape | Rows |", "|---|---:|"])
    sc = summary.get("shape_counts") or {}
    for shape in ALLOWED_SHAPES:
        if shape in sc:
            lines.append(f"| {shape} | {sc[shape]} |")
    lines.extend(["", "## Recommended Next Platform Pair", ""])
    pair = summary.get("recommended_next_platform_pair") or {}
    lines.extend(
        [
            f"- Polymarket_vs_Kalshi: `{pair.get('Polymarket_vs_Kalshi', 0)}`",
            f"- Polymarket_vs_IBKR_ForecastEx: `{pair.get('Polymarket_vs_IBKR_ForecastEx', 0)}`",
            f"- Polymarket_vs_CDNA: `{pair.get('Polymarket_vs_CDNA', 0)}`",
            f"- Polymarket_vs_Odds_API_reference_only: `{pair.get('Polymarket_vs_Odds_API_reference_only', 0)}`",
        ]
    )
    lines.extend(["", "## Top 25 Exact-Matchable Candidates", ""])
    top25 = summary.get("top_25_candidates") or []
    if not top25:
        lines.append("_None yet._")
    else:
        lines.extend(
            [
                "| # | Score | Shape | Family | Suggested Pair | Question | CLOB | Blockers |",
                "|---:|---:|---|---|---|---|:---:|---|",
            ]
        )
        for i, row in enumerate(top25, start=1):
            question = (row.get("question") or "")[:80]
            blockers = ", ".join((row.get("blockers") or [])[:3])
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(i),
                        f"{row.get('exact_matchability_score', 0):.1f}",
                        _md_cell(row.get("market_shape")),
                        _md_cell(row.get("family")),
                        _md_cell(row.get("recommended_pair")),
                        _md_cell(question),
                        "yes" if row.get("clob_book_attached") else "no",
                        _md_cell(blockers or "none"),
                    ]
                )
                + " |"
            )
    lines.extend(["", "## Top Blockers", "", "| Blocker | Count |", "|---|---:|"])
    for item in summary.get("top_blockers") or []:
        lines.append(f"| {item.get('blocker')} | {item.get('count')} |")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- diagnostic_only: `true`",
            "- can_create_candidate_pair: `false`",
            "- can_create_paper_candidate: `false`",
            "- exact_ready: `false`",
            "- execution_ready: `false`",
            "- paper_candidate: `false`",
            "- source_registry_unchanged: `true`",
        ]
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _load_taxonomy(input_dir: Path, warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    path = input_dir / "polymarket_market_taxonomy.json"
    if not path.exists():
        warnings.append({"source_file": str(path), "reason_code": "input_missing", "blocker": "taxonomy_input_missing"})
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append({"source_file": str(path), "reason_code": "input_unreadable", "blocker": f"taxonomy_input_unreadable:{type(exc).__name__}"})
        return []
    rows = payload.get("taxonomy_rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _load_clob_index(input_dir: Path, warnings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build an index of CLOB book entries keyed by condition_id and market_id."""
    index: dict[str, dict[str, Any]] = {}
    path = input_dir / "polymarket_orderbook_enriched_snapshot.json"
    if not path.exists():
        return index
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(
            {
                "source_file": str(path),
                "reason_code": "clob_input_unreadable",
                "blocker": f"clob_input_unreadable:{type(exc).__name__}",
            }
        )
        return index
    markets = payload.get("normalized_markets") if isinstance(payload, dict) else None
    if not isinstance(markets, list):
        return index
    for market in markets:
        if not isinstance(market, dict):
            continue
        book = market.get("orderbook_enrichment")
        if not isinstance(book, dict) or book.get("enrichment_status") != "enriched":
            continue
        entry = {
            "best_bid": book.get("best_bid"),
            "best_ask": book.get("best_ask"),
            "depth_at_best_bid": book.get("depth_at_best_bid"),
            "depth_at_best_ask": book.get("depth_at_best_ask"),
            "spread": book.get("spread"),
            "orderbook_captured_at": book.get("orderbook_captured_at"),
            "source_endpoint": book.get("source_endpoint"),
            "depth_within_1c": book.get("depth_within_1c"),
            "depth_within_3c": book.get("depth_within_3c"),
            "depth_within_5c": book.get("depth_within_5c"),
        }
        for key_field in ("condition_id", "market_id"):
            key = market.get(key_field)
            if key:
                index[str(key)] = entry
    return index


# ---------------------------------------------------------------------------
# Row composition
# ---------------------------------------------------------------------------


def _compose_row(*, tax_row: dict[str, Any], clob_index: dict[str, dict[str, Any]], now: datetime) -> dict[str, Any]:
    raw_shape = (tax_row.get("market_shape") or "").upper()
    family = (tax_row.get("family") or "").upper()
    typed_keys = tax_row.get("typed_keys") if isinstance(tax_row.get("typed_keys"), dict) else {}
    shape_result = _classify_shape(raw_shape=raw_shape, family=family, tax_row=tax_row)
    market_shape = shape_result["market_shape"]
    typed_key_complete = bool(tax_row.get("typed_key_complete"))
    settlement_source_present = bool(tax_row.get("settlement_source_present"))
    settlement_rules_text_present = bool(tax_row.get("settlement_rules_text_present"))
    book_entry = _attach_clob_book(tax_row=tax_row, clob_index=clob_index)
    clob_attached = book_entry is not None
    quote_fresh = _quote_is_fresh(book_entry=book_entry, now=now) if book_entry else False
    blockers = _row_blockers(
        market_shape=market_shape,
        raw_shape=raw_shape,
        typed_keys=typed_keys,
        typed_key_complete=typed_key_complete,
        settlement_source_present=settlement_source_present,
        settlement_rules_text_present=settlement_rules_text_present,
        clob_attached=clob_attached,
        quote_fresh=quote_fresh,
        token_ids=tax_row.get("token_ids"),
        existing_blockers=tax_row.get("blockers"),
        deadline_touch_phrase_detected=bool(shape_result["deadline_touch_phrase_detected"]),
    )
    score = _exact_matchability_score(
        market_shape=market_shape,
        family=family,
        typed_keys=typed_keys,
        typed_key_complete=typed_key_complete,
        settlement_source_present=settlement_source_present,
        clob_attached=clob_attached,
        quote_fresh=quote_fresh,
        blockers=blockers,
    )
    action = _allowed_next_action(market_shape=market_shape, blockers=blockers)
    recommended_pair = _recommended_pair(family=family, market_shape=market_shape, typed_keys=typed_keys)
    next_action_text = _next_action_text(action=action, market_shape=market_shape, blockers=blockers)
    return {
        "row_id": _row_id(tax_row=tax_row),
        "market_id": tax_row.get("market_id"),
        "condition_id": tax_row.get("condition_id"),
        "event_id": tax_row.get("event_id"),
        "event_slug": tax_row.get("event_slug"),
        "market_slug": tax_row.get("market_slug"),
        "venue": tax_row.get("venue") or "polymarket",
        "source_url": tax_row.get("source_url"),
        "raw_source_file": tax_row.get("raw_source_file"),
        "captured_at": tax_row.get("captured_at"),
        "question": tax_row.get("question"),
        "title": tax_row.get("title"),
        "family": family or "UNKNOWN",
        "raw_taxonomy_shape": raw_shape,
        "market_shape": market_shape,
        "conservative_shape_override": bool(shape_result["conservative_shape_override"]),
        "shape_override_reason": shape_result["shape_override_reason"],
        "deadline_touch_phrase_detected": bool(shape_result["deadline_touch_phrase_detected"]),
        "typed_keys": typed_keys,
        "typed_key_complete": typed_key_complete,
        "settlement_source_present": settlement_source_present,
        "settlement_rules_text_present": settlement_rules_text_present,
        "token_ids": tax_row.get("token_ids"),
        "clob_book_attached": clob_attached,
        "clob_book": book_entry,
        "clob_book_fresh": quote_fresh,
        "blockers": blockers,
        "exact_matchability_score": round(score, 2),
        "allowed_next_action": action,
        "next_action_text": next_action_text,
        "recommended_pair": recommended_pair,
        "diagnostic_only": True,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "exact_ready": False,
        "execution_ready": False,
        "paper_candidate": False,
        "affects_evaluator_gates": False,
        "source_exact_payoff_compatible_with_kalshi": False,
    }


def _classify_shape(*, raw_shape: str, family: str, tax_row: dict[str, Any]) -> dict[str, Any]:
    mapped = _TAXONOMY_SHAPE_MAP.get(raw_shape, SHAPE_AMBIGUOUS)
    phrase_kind = _deadline_touch_phrase_kind(_combined_market_text(tax_row))
    market_shape = mapped
    if family == "CRYPTO" and mapped in {SHAPE_DEADLINE, SHAPE_RANGE_BUCKET, SHAPE_RANGE_HIT}:
        market_shape = SHAPE_CRYPTO_DEADLINE_RANGE_HIT
    if phrase_kind == "all_time_high_by_date":
        market_shape = SHAPE_ALL_TIME_HIGH_BY_DATE
    elif mapped == SHAPE_POINT_IN_TIME and phrase_kind == "deadline_threshold_touch":
        market_shape = SHAPE_CRYPTO_DEADLINE_RANGE_HIT if family == "CRYPTO" else SHAPE_DEADLINE
    elif mapped == SHAPE_POINT_IN_TIME and phrase_kind == "before_deadline":
        market_shape = SHAPE_CRYPTO_DEADLINE_RANGE_HIT if family == "CRYPTO" else SHAPE_AMBIGUOUS
    return {
        "market_shape": market_shape,
        "conservative_shape_override": market_shape != mapped,
        "shape_override_reason": phrase_kind if market_shape != mapped else None,
        "deadline_touch_phrase_detected": phrase_kind is not None,
    }


def _combined_market_text(tax_row: dict[str, Any]) -> str:
    fields = (
        tax_row.get("question"),
        tax_row.get("title"),
        tax_row.get("market_slug"),
        tax_row.get("event_slug"),
    )
    return " ".join(str(field) for field in fields if field)


def _deadline_touch_phrase_kind(text: str) -> str | None:
    if not text:
        return None
    if _ALL_TIME_HIGH_BY_DEADLINE_RE.search(text):
        return "all_time_high_by_date"
    if _DEADLINE_TOUCH_BY_RE.search(text) or _BY_DEADLINE_TOUCH_RE.search(text) or _INTERVAL_TOUCH_RE.search(text):
        return "deadline_threshold_touch"
    if _AT_ANY_TIME_BEFORE_RE.search(text):
        return "deadline_threshold_touch"
    if _BEFORE_DEADLINE_RE.search(text):
        return "before_deadline"
    return None


def _attach_clob_book(*, tax_row: dict[str, Any], clob_index: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if not clob_index:
        return None
    for key in (tax_row.get("condition_id"), tax_row.get("market_id")):
        if key is None:
            continue
        book = clob_index.get(str(key))
        if book is not None:
            return book
    return None


def _quote_is_fresh(*, book_entry: dict[str, Any], now: datetime) -> bool:
    captured = book_entry.get("orderbook_captured_at")
    if not captured:
        return False
    try:
        captured_dt = datetime.fromisoformat(str(captured).replace("Z", "+00:00"))
    except ValueError:
        return False
    if captured_dt.tzinfo is None:
        captured_dt = captured_dt.replace(tzinfo=timezone.utc)
    return (now - captured_dt) <= timedelta(days=_STALE_QUOTE_DAYS)


def _row_blockers(
    *,
    market_shape: str,
    raw_shape: str,
    typed_keys: dict[str, Any],
    typed_key_complete: bool,
    settlement_source_present: bool,
    settlement_rules_text_present: bool,
    clob_attached: bool,
    quote_fresh: bool,
    token_ids: Any,
    existing_blockers: Any,
    deadline_touch_phrase_detected: bool,
) -> list[str]:
    blockers: list[str] = [B_POLYMARKET_REGISTRY_BLOCKS_PAIR_CREATION]
    if market_shape in _DEADLINE_OR_RANGE_HIT_SHAPES:
        if market_shape in {SHAPE_RANGE_BUCKET, SHAPE_RANGE_HIT} or raw_shape in {"RANGE_BUCKET", "UP_DOWN_INTERVAL"}:
            blockers.append(B_RANGE_VS_CLOSE)
        else:
            blockers.append(B_DEADLINE_VS_POINT)
        blockers.append(B_SETTLEMENT_WINDOW_MISMATCH)
        blockers.append(B_EXACT_PAYOFF_NOT_PROVEN)
    if deadline_touch_phrase_detected:
        blockers.append(B_HIT_BY_DEADLINE_NOT_POINT_IN_TIME)
    if market_shape == SHAPE_AMBIGUOUS:
        blockers.append(B_AMBIGUOUS_SHAPE)
    if not (
        typed_keys.get("deadline_or_date")
        or typed_keys.get("date")
        or typed_keys.get("measurement_date")
        or typed_keys.get("event_date")
        or typed_keys.get("settlement_date")
    ):
        blockers.append(B_AMBIGUOUS_DATE)
    if not settlement_source_present:
        blockers.append(B_AMBIGUOUS_SOURCE)
        blockers.append(B_SETTLEMENT_SOURCE_MISSING)
    if not settlement_rules_text_present:
        blockers.append(B_SETTLEMENT_RULES_MISSING)
    if not clob_attached:
        blockers.append(B_MISSING_CLOB_BOOK)
        blockers.append(B_STALE_OR_MISSING_QUOTE)
    elif not quote_fresh:
        blockers.append(B_STALE_OR_MISSING_QUOTE)
    if not typed_key_complete:
        blockers.append(B_MISSING_TYPED_KEY)
    # Multi-condition heuristic: many token_ids on a single market suggests an outcome bucket
    # rather than a single binary payoff. We mark anything > 2 token ids as multi-condition.
    if isinstance(token_ids, list) and len(token_ids) > 2:
        blockers.append(B_MULTI_CONDITION)
    # Always include the title-similarity guard.
    blockers.append(B_TITLE_ONLY_MATCH)
    # Carry forward upstream taxonomy blockers if not already present.
    if isinstance(existing_blockers, list):
        for blocker in existing_blockers:
            if isinstance(blocker, str) and blocker not in blockers:
                blockers.append(blocker)
    return list(dict.fromkeys(blockers))


def _exact_matchability_score(
    *,
    market_shape: str,
    family: str,
    typed_keys: dict[str, Any],
    typed_key_complete: bool,
    settlement_source_present: bool,
    clob_attached: bool,
    quote_fresh: bool,
    blockers: list[str],
) -> float:
    score = 0.0
    # Shape bonus
    if market_shape == SHAPE_POINT_IN_TIME:
        score += 18.0
    elif market_shape == SHAPE_MACRO_RATE_MEETING:
        score += 16.0
    elif market_shape == SHAPE_EVENT_WINNER:
        score += 12.0
    elif market_shape == SHAPE_ELECTION_CANDIDATE:
        score += 11.0
    elif market_shape == SHAPE_SPORTS_FUTURES:
        score += 10.0
    elif market_shape == SHAPE_SPORTS_GAME:
        score += 9.0
    elif market_shape == SHAPE_MACRO_RELEASE:
        score += 9.0
    elif market_shape == SHAPE_COMPANY_THRESHOLD:
        score += 7.0
    elif market_shape == SHAPE_TECH_RELEASE:
        score += 5.0
    elif market_shape == SHAPE_CRYPTO_DEADLINE_RANGE_HIT:
        score += 3.0
    elif market_shape == SHAPE_ALL_TIME_HIGH_BY_DATE:
        score += 2.0
    elif market_shape == SHAPE_DEADLINE:
        score += 4.0
    elif market_shape == SHAPE_RANGE_HIT or market_shape == SHAPE_RANGE_BUCKET:
        score += 3.0
    else:
        score += 0.0
    # Typed key bonuses
    has_date = any(
        typed_keys.get(key)
        for key in ("deadline_or_date", "date", "measurement_date", "event_date", "settlement_date")
    )
    has_threshold = (
        typed_keys.get("threshold") is not None
        or typed_keys.get("threshold_value") is not None
    )
    has_comparator = bool(
        typed_keys.get("comparator") or typed_keys.get("threshold_operator")
    )
    if has_date:
        score += 6.0
    if has_threshold:
        score += 4.0
    if has_comparator:
        score += 3.0
    if typed_keys.get("entity") or typed_keys.get("asset"):
        score += 2.0
    if typed_keys.get("price_source_index"):
        score += 2.0
    if typed_key_complete:
        score += 6.0
    if settlement_source_present:
        score += 5.0
    # Family-level priors aligned with which Kalshi/IBKR/CDNA lanes exist.
    if family in {"MACRO_FED_RATES"}:
        score += 6.0
    elif family in {"MACRO_ECONOMIC_RELEASE"}:
        score += 4.0
    elif family in {"POLITICS_ELECTION_RESULT"}:
        score += 4.0
    elif family in {"SPORTS_FUTURES"}:
        score += 3.0
    elif family == "CRYPTO":
        score += 3.0 if market_shape == SHAPE_POINT_IN_TIME else 0.0
    elif family in {"OTHER_UNKNOWN"}:
        score -= 4.0
    # CLOB attachment
    if clob_attached:
        score += 6.0
        if quote_fresh:
            score += 4.0
    # Penalties from blockers (each blocker contributes a small penalty; multi-condition + ambiguous are heavier)
    penalty_lookup = {
        B_DEADLINE_VS_POINT: -8.0,
        B_RANGE_VS_CLOSE: -8.0,
        B_AMBIGUOUS_SHAPE: -10.0,
        B_AMBIGUOUS_DATE: -4.0,
        B_AMBIGUOUS_SOURCE: -3.0,
        B_MISSING_CLOB_BOOK: -3.0,
        B_STALE_OR_MISSING_QUOTE: -2.0,
        B_MULTI_CONDITION: -6.0,
        B_MISSING_TYPED_KEY: -3.0,
        B_TITLE_ONLY_MATCH: -1.0,
        B_SETTLEMENT_SOURCE_MISSING: -2.0,
        B_SETTLEMENT_RULES_MISSING: -3.0,
        B_HIT_BY_DEADLINE_NOT_POINT_IN_TIME: -5.0,
        B_SETTLEMENT_WINDOW_MISMATCH: -4.0,
        B_EXACT_PAYOFF_NOT_PROVEN: -5.0,
    }
    for blocker in set(blockers):
        score += penalty_lookup.get(blocker, 0.0)
    return max(0.0, min(100.0, score))


def _allowed_next_action(*, market_shape: str, blockers: list[str]) -> str:
    blocker_set = set(blockers)
    if market_shape in _DEADLINE_OR_RANGE_HIT_SHAPES:
        return ACTION_BASIS_RISK_REVIEW
    if market_shape == SHAPE_AMBIGUOUS:
        return ACTION_WATCH
    if (
        B_AMBIGUOUS_SOURCE in blocker_set
        or B_SETTLEMENT_SOURCE_MISSING in blocker_set
        or B_SETTLEMENT_RULES_MISSING in blocker_set
    ):
        return ACTION_SOURCE_REVIEW
    if (
        B_MISSING_TYPED_KEY in blocker_set
        or B_AMBIGUOUS_DATE in blocker_set
        or B_MULTI_CONDITION in blocker_set
    ):
        return ACTION_MANUAL_REVIEW
    if B_MISSING_CLOB_BOOK in blocker_set or B_STALE_OR_MISSING_QUOTE in blocker_set:
        return ACTION_WATCH
    return ACTION_MANUAL_REVIEW


def _next_action_text(*, action: str, market_shape: str, blockers: list[str]) -> str:
    if action == ACTION_BASIS_RISK_REVIEW:
        return "Deadline / range-hit / range-bucket shape — basis-risk review only. Cannot be paired as exact point-in-time."
    if action == ACTION_SOURCE_REVIEW:
        return "Settlement source or rules text missing — capture explicit source URL and rules before any further pairing."
    if action == ACTION_MANUAL_REVIEW:
        return "Typed-key or single-payoff evidence incomplete — manual review of date/threshold/source/payoff scope required before any exact pairing."
    return "Watch-only diagnostic; insufficient evidence to advance."


def _recommended_pair(*, family: str, market_shape: str, typed_keys: dict[str, Any]) -> str:
    if family in {"MACRO_FED_RATES"} or market_shape == SHAPE_MACRO_RATE_MEETING:
        return "Polymarket_vs_Kalshi_or_IBKR_ForecastEx"
    if family == "MACRO_ECONOMIC_RELEASE":
        return "Polymarket_vs_Kalshi"
    if family == "POLITICS_ELECTION_RESULT":
        return "Polymarket_vs_Kalshi"
    if family == "SPORTS_FUTURES":
        return "Polymarket_vs_Kalshi"
    if family == "SPORTS_GAME":
        return "Polymarket_vs_Odds_API_reference_only"
    if family == "CRYPTO":
        if market_shape == SHAPE_POINT_IN_TIME:
            return "Polymarket_vs_Kalshi_or_CDNA"
        return "Polymarket_vs_CDNA"
    if family in {"TECH_COMPANY_PRODUCT", "TECH_AI"}:
        return "Polymarket_only_watch"
    if family in {"OTHER_UNKNOWN"}:
        return "Polymarket_only_watch"
    return "Polymarket_only_watch"


def _row_id(tax_row: dict[str, Any]) -> str:
    market_id = tax_row.get("market_id") or tax_row.get("condition_id") or tax_row.get("market_slug") or "x"
    return f"poly_{market_id}"


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    shape_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    blocker_counts: Counter[str] = Counter()
    action_counts: Counter[str] = Counter()
    pair_counts: Counter[str] = Counter()
    clob_count = 0
    typed_complete = 0
    point_in_time_count = 0
    deadline_or_range_count = 0
    deadline_touch_phrase_rows = 0
    deadline_touch_phrase_reclassified_rows = 0
    for row in rows:
        shape = row.get("market_shape") or SHAPE_AMBIGUOUS
        family = row.get("family") or "UNKNOWN"
        shape_counts[shape] += 1
        category_counts[family] += 1
        for blocker in row.get("blockers") or []:
            blocker_counts[blocker] += 1
        action_counts[row.get("allowed_next_action") or ACTION_WATCH] += 1
        pair_counts[row.get("recommended_pair") or "unknown"] += 1
        if row.get("clob_book_attached"):
            clob_count += 1
        if row.get("typed_key_complete"):
            typed_complete += 1
        if shape == SHAPE_POINT_IN_TIME:
            point_in_time_count += 1
        if shape in _DEADLINE_OR_RANGE_HIT_SHAPES:
            deadline_or_range_count += 1
        if row.get("deadline_touch_phrase_detected"):
            deadline_touch_phrase_rows += 1
        if row.get("conservative_shape_override"):
            deadline_touch_phrase_reclassified_rows += 1
    top_25 = [
        {
            "row_id": r.get("row_id"),
            "exact_matchability_score": r.get("exact_matchability_score"),
            "market_shape": r.get("market_shape"),
            "family": r.get("family"),
            "recommended_pair": r.get("recommended_pair"),
            "question": r.get("question"),
            "blockers": r.get("blockers"),
            "clob_book_attached": r.get("clob_book_attached"),
            "settlement_source_present": r.get("settlement_source_present"),
            "typed_key_complete": r.get("typed_key_complete"),
            "allowed_next_action": r.get("allowed_next_action"),
        }
        for r in rows[:25]
    ]
    top_blockers = [
        {"blocker": b, "count": c} for b, c in blocker_counts.most_common(15)
    ]
    recommended_pair_normalized = {
        "Polymarket_vs_Kalshi": pair_counts.get("Polymarket_vs_Kalshi", 0)
        + pair_counts.get("Polymarket_vs_Kalshi_or_IBKR_ForecastEx", 0)
        + pair_counts.get("Polymarket_vs_Kalshi_or_CDNA", 0),
        "Polymarket_vs_IBKR_ForecastEx": pair_counts.get("Polymarket_vs_Kalshi_or_IBKR_ForecastEx", 0),
        "Polymarket_vs_CDNA": pair_counts.get("Polymarket_vs_CDNA", 0)
        + pair_counts.get("Polymarket_vs_Kalshi_or_CDNA", 0),
        "Polymarket_vs_Odds_API_reference_only": pair_counts.get("Polymarket_vs_Odds_API_reference_only", 0),
    }
    return {
        "total_rows": len(rows),
        "point_in_time_candidates": point_in_time_count,
        "deadline_or_range_hit_blocked": deadline_or_range_count,
        "deadline_touch_phrase_rows": deadline_touch_phrase_rows,
        "deadline_touch_phrase_reclassified_rows": deadline_touch_phrase_reclassified_rows,
        "clob_book_attached": clob_count,
        "typed_key_complete": typed_complete,
        "exact_ready_rows": 0,
        "paper_candidate_rows": 0,
        "execution_ready_rows": 0,
        "category_counts": dict(category_counts),
        "shape_counts": dict(shape_counts),
        "action_counts": dict(action_counts),
        "recommended_pair_counts": dict(pair_counts),
        "recommended_next_platform_pair": recommended_pair_normalized,
        "top_blockers": top_blockers,
        "top_25_candidates": top_25,
    }


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
        "source_exact_payoff_compatible_with_kalshi": False,
    }


def _md_cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "/").replace("\n", " ")
