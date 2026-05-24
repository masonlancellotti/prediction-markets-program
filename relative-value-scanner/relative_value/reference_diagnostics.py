from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.live_snapshot_matcher import load_reference_snapshot


DEFAULT_REFERENCE_MATCH_MIN_SCORE = 0.35
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "at",
    "be",
    "by",
    "for",
    "in",
    "market",
    "no",
    "of",
    "on",
    "or",
    "the",
    "to",
    "will",
    "yes",
}


def explain_reference_context_files(
    *,
    snapshot_path: Path,
    reference_snapshot_path: Path,
    now: datetime | None = None,
    min_similarity: float = DEFAULT_REFERENCE_MATCH_MIN_SCORE,
) -> dict[str, Any]:
    generated_at = now or datetime.now(timezone.utc)
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("now must include timezone information")
    executable_snapshot = _load_executable_snapshot(snapshot_path)
    reference_snapshot = load_reference_snapshot(reference_snapshot_path)
    if reference_snapshot.issues:
        raise ValueError(f"reference snapshot invalid: {','.join(reference_snapshot.issues)}")

    executable_markets = executable_snapshot.get("normalized_markets", [])
    reference_records = reference_snapshot.payload.get("normalized_records", [])
    diagnostic_rows: list[dict[str, Any]] = []
    skipped_reference_record_count = 0
    stale_reference_record_count = 0
    malformed_reference_record_count = 0
    for record in reference_records:
        if not isinstance(record, dict):
            skipped_reference_record_count += 1
            malformed_reference_record_count += 1
            continue
        issues = _reference_record_issues(record, generated_at)
        if "malformed_reference_record" in issues:
            skipped_reference_record_count += 1
            malformed_reference_record_count += 1
            continue
        if "stale_reference_record" in issues:
            stale_reference_record_count += 1
        for market in executable_markets:
            if not isinstance(market, dict):
                continue
            score = _reference_match_score(market, record)
            if score < min_similarity:
                continue
            diagnostic_rows.append(_diagnostic_row(market, record, score, issues))

    diagnostic_rows.sort(key=lambda row: (row["match_score"], row["executable_market_title"]), reverse=True)
    return {
        "schema_version": 1,
        "source": "reference_context_diagnostics",
        "generated_at": generated_at.isoformat(),
        "inputs": {
            "snapshot": str(snapshot_path),
            "reference_snapshot": str(reference_snapshot_path),
        },
        "reference_source_id": reference_snapshot.payload.get("source_id"),
        "reference_source_type": reference_snapshot.payload.get("source_type"),
        "executable_market_count": len(executable_markets),
        "reference_record_count": len(reference_records),
        "diagnostic_match_count": len(diagnostic_rows),
        "stale_reference_record_count": stale_reference_record_count,
        "malformed_reference_record_count": malformed_reference_record_count,
        "skipped_reference_record_count": skipped_reference_record_count,
        "diagnostic_rows": diagnostic_rows,
        "disclaimer": (
            "Reference-only diagnostics. Sportsbook odds are not executable prices; "
            "title similarity is not settlement equivalence; no action promotion is performed."
        ),
    }


def _load_executable_snapshot(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"snapshot file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("snapshot JSON is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("snapshot JSON must be an object")
    if payload.get("schema_version") != 1:
        raise ValueError("snapshot schema_version must be 1")
    if payload.get("schema_kind") == "reference_snapshot_v1":
        raise ValueError("snapshot must be an executable venue snapshot, not reference_snapshot_v1")
    if not isinstance(payload.get("normalized_markets"), list):
        raise ValueError("snapshot normalized_markets must be a list")
    return payload


def _diagnostic_row(market: dict[str, Any], record: dict[str, Any], score: float, reference_issues: list[str]) -> dict[str, Any]:
    stale = "stale_reference_record" in reference_issues
    return {
        "action": "REFERENCE_ONLY_DIAGNOSTIC",
        "executable_market_id": market.get("market_id") or market.get("ticker"),
        "executable_market_title": _market_title(market),
        "executable_venue": market.get("venue") or market.get("source"),
        "reference_event_title": record.get("event_title"),
        "bookmaker": record.get("bookmaker"),
        "market_type": record.get("market_type"),
        "reference_outcome_name": record.get("outcome_name"),
        "no_vig_probability": record.get("no_vig_probability"),
        "implied_probability": record.get("implied_probability"),
        "retrieved_at": record.get("retrieved_at"),
        "stale_after": record.get("stale_after"),
        "reference_status": "stale" if stale else "fresh",
        "reference_diagnostics": sorted(set(reference_issues)),
        "match_score": round(score, 6),
        "match_reason": "title_entity_similarity_only",
        "notes": "Diagnostic only; no sportsbook execution, payoff equivalence, gap, fee, depth, or action claim.",
    }


def _reference_record_issues(record: dict[str, Any], generated_at: datetime) -> list[str]:
    issues: list[str] = []
    if record.get("source_type") != "REFERENCE_ONLY" or record.get("permission") != "REFERENCE_ONLY":
        issues.append("reference_record_not_reference_only")
    if record.get("is_executable") is not False:
        issues.append("reference_record_not_non_executable")
    if record.get("usable_for_trade_decision") is not False:
        issues.append("reference_record_trade_decision_not_disabled")
    if not (record.get("event_title") and record.get("bookmaker") and record.get("market_type")):
        issues.append("malformed_reference_record")
    stale_after = _parse_datetime_or_none(str(record.get("stale_after") or ""))
    if stale_after is None:
        issues.append("missing_reference_stale_after")
    elif generated_at > stale_after:
        issues.append("stale_reference_record")
    return issues


def _reference_match_score(market: dict[str, Any], record: dict[str, Any]) -> float:
    market_tokens = _meaningful_tokens(_market_text(market))
    reference_tokens = _meaningful_tokens(_reference_text(record))
    if not market_tokens or not reference_tokens:
        return 0.0
    overlap = len(market_tokens & reference_tokens)
    return (2.0 * overlap) / (len(market_tokens) + len(reference_tokens))


def _market_text(market: dict[str, Any]) -> str:
    return " ".join(str(value or "") for value in (market.get("event_title"), market.get("question"), market.get("title")))


def _reference_text(record: dict[str, Any]) -> str:
    return " ".join(
        str(value or "")
        for value in (
            record.get("event_title"),
            record.get("outcome_name"),
            record.get("market_type"),
        )
    )


def _market_title(market: dict[str, Any]) -> str:
    return str(market.get("question") or market.get("title") or market.get("event_title") or market.get("market_id") or "")


def _meaningful_tokens(value: str) -> set[str]:
    return {token for token in _TOKEN_RE.findall(value.lower()) if token not in _STOPWORDS}


def _parse_datetime_or_none(value: str) -> datetime | None:
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed
