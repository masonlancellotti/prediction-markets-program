from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.contract_relationship import classify_contract_relationship


SUPPORTED_SCHEMA_VERSION = 1
DEFAULT_MAX_SNAPSHOT_AGE_HOURS = 24.0
DEFAULT_MIN_SIMILARITY = 0.68
DEFAULT_SETTLEMENT_BONUS_WINDOW_SECONDS = 6 * 60 * 60
SETTLEMENT_TIME_BONUS = 0.08
EVENT_KEYWORD_BONUS = 0.06
MIN_QUESTION_SIMILARITY_FOR_BONUS = 0.45

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_EVENT_KEYWORDS = {
    "basketball": "NBA",
    "bitcoin": "Bitcoin",
    "btc": "BTC",
    "cpi": "CPI",
    "election": "election",
    "eth": "ETH",
    "ethereum": "Ethereum",
    "fed": "Fed",
    "football": "NFL",
    "house": "House",
    "ipo": "IPO",
    "mlb": "MLB",
    "mls": "MLS",
    "nba": "NBA",
    "nfl": "NFL",
    "nhl": "NHL",
    "president": "President",
    "rates": "rates",
    "senate": "Senate",
    "uefa": "UEFA",
}
_COMPACT_EVENT_KEYWORDS = {"btc", "cpi", "eth", "fed", "ipo", "mlb", "mls", "nba", "nfl", "nhl", "uefa"}
_STOPWORDS = {
    "a",
    "an",
    "and",
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
_LEAGUE_STAGE_SCOPE_PHRASES = (
    "american league championship series",
    "american league championship",
    "national league championship series",
    "national league championship",
    "league championship series",
    "league championship",
    "conference championship",
    "afc championship",
    "nfc championship",
    "conference finals",
    "conference final",
    "division series",
    "semifinal",
    "wild card",
    "wildcard",
    "alcs",
    "alds",
    "nlcs",
    "nlds",
    "champions league group stage",
    "champions league round of 16",
    "copa america group stage",
)
_OVERALL_FINAL_SCOPE_PHRASES = (
    "world series",
    "pro baseball championship",
    "super bowl",
    "stanley cup",
    "world cup",
    "mls cup",
    "nba finals",
    "nhl finals",
    "premier league title",
    "la liga title",
    "bundesliga title",
    "serie a title",
    "champions league title",
    "copa america",
    "euro championship",
)


@dataclass(frozen=True)
class LoadedSnapshot:
    venue: str
    path: Path
    payload: dict[str, Any]
    issues: tuple[str, ...]


@dataclass(frozen=True)
class LoadedReferenceSnapshot:
    path: Path
    payload: dict[str, Any]
    issues: tuple[str, ...]


def match_snapshot_files(
    polymarket_path: Path,
    kalshi_path: Path,
    output_path: Path | None = None,
    now: datetime | None = None,
    max_snapshot_age_hours: float = DEFAULT_MAX_SNAPSHOT_AGE_HOURS,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
    reference_snapshot_paths: list[Path] | None = None,
) -> dict[str, Any]:
    generated_at = now or datetime.now(timezone.utc)
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("now must include timezone information")
    polymarket = load_snapshot(polymarket_path, venue="polymarket")
    kalshi = load_snapshot(kalshi_path, venue="kalshi")
    reference_snapshots = [load_reference_snapshot(path) for path in reference_snapshot_paths or []]
    payload = match_snapshots(
        polymarket,
        kalshi,
        generated_at=generated_at,
        max_snapshot_age_hours=max_snapshot_age_hours,
        min_similarity=min_similarity,
        reference_snapshots=reference_snapshots,
    )
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def load_snapshot(path: Path, venue: str) -> LoadedSnapshot:
    issues: list[str] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return LoadedSnapshot(venue=venue, path=path, payload={}, issues=("missing_snapshot_file",))
    except json.JSONDecodeError:
        return LoadedSnapshot(venue=venue, path=path, payload={}, issues=("invalid_snapshot_json",))
    if not isinstance(payload, dict):
        return LoadedSnapshot(venue=venue, path=path, payload={}, issues=("invalid_snapshot_shape",))
    version = payload.get("schema_version")
    if version is None:
        issues.append("missing_schema_version")
    elif version != SUPPORTED_SCHEMA_VERSION:
        issues.append("unsupported_schema_version")
    schema_kind = payload.get("schema_kind")
    if schema_kind is not None and schema_kind != "live_snapshot_v1":
        issues.append("unsupported_schema_kind")
    if "normalized_markets" not in payload or not isinstance(payload.get("normalized_markets"), list):
        issues.append("missing_normalized_markets")
    return LoadedSnapshot(venue=venue, path=path, payload=payload, issues=tuple(issues))


def load_reference_snapshot(path: Path) -> LoadedReferenceSnapshot:
    issues: list[str] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return LoadedReferenceSnapshot(path=path, payload={}, issues=("missing_reference_snapshot_file",))
    except json.JSONDecodeError:
        return LoadedReferenceSnapshot(path=path, payload={}, issues=("invalid_reference_snapshot_json",))
    if not isinstance(payload, dict):
        return LoadedReferenceSnapshot(path=path, payload={}, issues=("invalid_reference_snapshot_shape",))
    if payload.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
        issues.append("unsupported_reference_schema_version")
    if payload.get("schema_kind") != "reference_snapshot_v1":
        issues.append("unsupported_reference_schema_kind")
    if payload.get("source_type") != "REFERENCE_ONLY":
        issues.append("reference_snapshot_not_reference_only")
    if not isinstance(payload.get("normalized_records"), list):
        issues.append("missing_reference_normalized_records")
    return LoadedReferenceSnapshot(path=path, payload=payload, issues=tuple(issues))


def match_snapshots(
    polymarket: LoadedSnapshot,
    kalshi: LoadedSnapshot,
    generated_at: datetime,
    max_snapshot_age_hours: float = DEFAULT_MAX_SNAPSHOT_AGE_HOURS,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
    reference_snapshots: list[LoadedReferenceSnapshot] | None = None,
) -> dict[str, Any]:
    snapshot_issues = {
        "polymarket": list(polymarket.issues) + _snapshot_freshness_reasons(polymarket.payload, generated_at, max_snapshot_age_hours),
        "kalshi": list(kalshi.issues) + _snapshot_freshness_reasons(kalshi.payload, generated_at, max_snapshot_age_hours),
    }
    reference_context = _reference_context_summary(reference_snapshots or [], generated_at)
    pairs: list[dict[str, Any]] = []
    if polymarket.issues or kalshi.issues:
        return _output_payload(polymarket, kalshi, generated_at, snapshot_issues, pairs, reference_context)

    polymarket_markets = polymarket.payload.get("normalized_markets", [])
    kalshi_markets = kalshi.payload.get("normalized_markets", [])
    for poly_market in polymarket_markets:
        if not isinstance(poly_market, dict):
            continue
        for kalshi_market in kalshi_markets:
            if not isinstance(kalshi_market, dict):
                continue
            candidate = _pair_candidate(poly_market, kalshi_market, snapshot_issues, min_similarity)
            if candidate is not None:
                pairs.append(candidate)
    pairs.sort(key=lambda item: (item["similarity_score"], item["action"]), reverse=True)
    return _output_payload(polymarket, kalshi, generated_at, snapshot_issues, pairs, reference_context)


def _pair_candidate(
    polymarket: dict[str, Any],
    kalshi: dict[str, Any],
    snapshot_issues: dict[str, list[str]],
    min_similarity: float,
) -> dict[str, Any] | None:
    poly_question = _market_question(polymarket)
    kalshi_question = _market_question(kalshi)
    poly_event = str(polymarket.get("event_title") or "")
    kalshi_event = str(kalshi.get("event_title") or "")
    question_score = _text_similarity(poly_question, kalshi_question)
    event_score = _text_similarity(poly_event, kalshi_event) if poly_event and kalshi_event else None
    base_similarity = min(question_score, event_score) if event_score is not None else question_score
    settlement_time_delta = _settlement_time_delta_seconds(polymarket, kalshi)
    settlement_time_bonus = _settlement_time_bonus(question_score, settlement_time_delta)
    poly_event_tokens = _event_keyword_tokens(polymarket)
    kalshi_event_tokens = _event_keyword_tokens(kalshi)
    shared_event_tokens = sorted(poly_event_tokens & kalshi_event_tokens)
    event_keyword_bonus = _event_keyword_bonus(question_score, shared_event_tokens)
    similarity = min(1.0, base_similarity + settlement_time_bonus + event_keyword_bonus)
    if similarity < min_similarity:
        return None

    reasons: list[str] = []
    reasons.extend(f"polymarket_snapshot_{reason}" for reason in snapshot_issues["polymarket"])
    reasons.extend(f"kalshi_snapshot_{reason}" for reason in snapshot_issues["kalshi"])
    reasons.extend(_market_ineligibility_reasons("polymarket", polymarket))
    reasons.extend(_market_ineligibility_reasons("kalshi", kalshi))
    relationship_reasons = _sports_equivalence_reasons(polymarket, kalshi)
    reasons.extend(relationship_reasons)
    if _numeric_tokens(poly_question) != _numeric_tokens(kalshi_question):
        relationship_reasons.append("ambiguous_wording")
        reasons.append("ambiguous_wording")
    contract_relationship = classify_contract_relationship(relationship_reasons)
    action = "WATCH" if reasons else "MANUAL_REVIEW"
    return {
        "action": action,
        "polymarket": {
            "market_id": polymarket.get("market_id"),
            "question": poly_question,
            "event_title": polymarket.get("event_title"),
        },
        "kalshi": {
            "ticker": kalshi.get("ticker") or kalshi.get("market_id"),
            "question": kalshi_question,
            "event_title": kalshi.get("event_title"),
        },
        "similarity_score": round(similarity, 4),
        "matched_fields": {
            "question_similarity": round(question_score, 4),
            "event_title_similarity": None if event_score is None else round(event_score, 4),
            "settlement_time_delta_seconds": settlement_time_delta,
            "settlement_time_bonus": round(settlement_time_bonus, 4),
            "settlement_time_warning": _settlement_time_warning(polymarket, kalshi, settlement_time_delta),
            "shared_event_tokens": shared_event_tokens,
            "event_keyword_bonus": round(event_keyword_bonus, 4),
            "final_similarity_score": round(similarity, 4),
            "polymarket_end_date": polymarket.get("end_date") or polymarket.get("close_time"),
            "kalshi_close_time": kalshi.get("close_time") or kalshi.get("end_date"),
        },
        "ineligibility_reasons": sorted(set(reasons)),
        "contract_relationship": contract_relationship.to_report_dict(),
        "notes": "Manual review only. This prototype makes no arb, profit, executable-liquidity, or trading claim.",
    }


def _market_question(market: dict[str, Any]) -> str:
    return str(market.get("question") or market.get("title") or market.get("market_id") or market.get("ticker") or "")


def _market_ineligibility_reasons(prefix: str, market: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if market.get("active") is not True:
        reasons.append(f"{prefix}_closed_inactive_market")
    if market.get("closed") is True:
        reasons.append(f"{prefix}_closed_inactive_market")
    if not (market.get("end_date") or market.get("close_time")):
        reasons.append(f"{prefix}_missing_close_end_time")
    if market.get("liquidity") is None:
        reasons.append(f"{prefix}_missing_liquidity_units")
    return reasons


def _sports_equivalence_reasons(polymarket: dict[str, Any], kalshi: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    poly_text = _sports_guardrail_text(polymarket)
    kalshi_text = _sports_guardrail_text(kalshi)
    poly_scope = _sports_competition_scope(poly_text)
    kalshi_scope = _sports_competition_scope(kalshi_text)
    if {poly_scope, kalshi_scope} == {"league_championship", "overall_championship"}:
        reasons.append("sports_competition_scope_mismatch")
    poly_team = _los_angeles_baseball_team(poly_text)
    kalshi_team = _los_angeles_baseball_team(kalshi_text)
    if poly_team is not None and kalshi_team is not None and poly_team != kalshi_team:
        reasons.append("sports_team_alias_mismatch")
    return reasons


def _sports_guardrail_text(market: dict[str, Any]) -> str:
    raw = market.get("raw")
    raw = raw if isinstance(raw, dict) else {}
    fields = [
        market.get("question"),
        market.get("title"),
        market.get("event_title"),
        market.get("market_id"),
        market.get("ticker"),
        raw.get("event_slug"),
        raw.get("series_ticker"),
        raw.get("event_ticker"),
    ]
    return " ".join(str(field or "") for field in fields).lower()


def _sports_competition_scope(text: str) -> str | None:
    if _has_any_phrase(text, _LEAGUE_STAGE_SCOPE_PHRASES):
        return "league_championship"
    if _has_any_phrase(text, _OVERALL_FINAL_SCOPE_PHRASES):
        return "overall_championship"
    if "championship" in _TOKEN_RE.findall(text):
        return "overall_championship"
    return None


def _los_angeles_baseball_team(text: str) -> str | None:
    tokens = set(_TOKEN_RE.findall(text))
    if _has_any_phrase(text, ("los angeles dodgers",)) or "dodgers" in tokens or "lad" in tokens:
        return "dodgers"
    if (
        _has_any_phrase(text, ("los angeles angels", "los angeles a"))
        or "angels" in tokens
        or "laa" in tokens
    ):
        return "angels"
    return None


def _has_any_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    normalized = " ".join(_TOKEN_RE.findall(text))
    return any(phrase in normalized for phrase in phrases)


def _snapshot_freshness_reasons(
    payload: dict[str, Any],
    generated_at: datetime,
    max_snapshot_age_hours: float,
) -> list[str]:
    captured_at = payload.get("captured_at")
    if not captured_at:
        return ["missing_captured_at"]
    parsed = _parse_datetime_or_none(str(captured_at))
    if parsed is None:
        return ["missing_captured_at"]
    age_hours = (generated_at - parsed).total_seconds() / 3600.0
    if age_hours > max_snapshot_age_hours:
        return ["stale_captured_at"]
    return []


def _text_similarity(left: str, right: str) -> float:
    left_tokens = _meaningful_tokens(left)
    right_tokens = _meaningful_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    return (2.0 * overlap) / (len(left_tokens) + len(right_tokens))


def _settlement_time_delta_seconds(polymarket: dict[str, Any], kalshi: dict[str, Any]) -> float | None:
    poly_time = _parse_datetime_or_none(str(polymarket.get("end_date") or polymarket.get("close_time") or ""))
    kalshi_time = _parse_datetime_or_none(str(kalshi.get("close_time") or kalshi.get("end_date") or ""))
    if poly_time is None or kalshi_time is None:
        return None
    return abs((poly_time - kalshi_time).total_seconds())


def _settlement_time_bonus(question_score: float, delta_seconds: float | None) -> float:
    if question_score < MIN_QUESTION_SIMILARITY_FOR_BONUS:
        return 0.0
    if delta_seconds is None:
        return 0.0
    if delta_seconds <= DEFAULT_SETTLEMENT_BONUS_WINDOW_SECONDS:
        return SETTLEMENT_TIME_BONUS
    return 0.0


def _settlement_time_warning(polymarket: dict[str, Any], kalshi: dict[str, Any], delta_seconds: float | None) -> str | None:
    if delta_seconds is not None:
        return None
    poly_has_time = bool(polymarket.get("end_date") or polymarket.get("close_time"))
    kalshi_has_time = bool(kalshi.get("close_time") or kalshi.get("end_date"))
    if not poly_has_time or not kalshi_has_time:
        return "missing_settlement_time"
    return "unparseable_or_naive_settlement_time"


def _event_keyword_bonus(question_score: float, shared_event_tokens: list[str]) -> float:
    if question_score < MIN_QUESTION_SIMILARITY_FOR_BONUS:
        return 0.0
    if not shared_event_tokens:
        return 0.0
    return EVENT_KEYWORD_BONUS


def _event_keyword_tokens(market: dict[str, Any]) -> set[str]:
    raw = market.get("raw")
    raw = raw if isinstance(raw, dict) else {}
    text_fields = " ".join(
        str(value or "")
        for value in (
            market.get("event_title"),
            raw.get("event_slug"),
        )
    ).lower()
    tokens = set(_TOKEN_RE.findall(text_fields))
    matches: set[str] = set()
    for keyword, label in _EVENT_KEYWORDS.items():
        if keyword in tokens:
            matches.add(label)

    ticker_fields = " ".join(str(raw.get(key) or "") for key in ("series_ticker", "event_ticker")).lower()
    ticker_tokens = set(_TOKEN_RE.findall(ticker_fields))
    ticker_compact = "".join(ticker_tokens)
    for keyword, label in _EVENT_KEYWORDS.items():
        if keyword in ticker_tokens or (keyword in _COMPACT_EVENT_KEYWORDS and keyword in ticker_compact):
            matches.add(label)
    return matches


def _meaningful_tokens(value: str) -> set[str]:
    return {token for token in _TOKEN_RE.findall(value.lower()) if token not in _STOPWORDS}


def _numeric_tokens(value: str) -> tuple[str, ...]:
    return tuple(sorted(_NUMBER_RE.findall(value.lower())))


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


def _reference_context_summary(
    reference_snapshots: list[LoadedReferenceSnapshot],
    generated_at: datetime,
) -> dict[str, Any]:
    snapshots: list[dict[str, Any]] = []
    total_records = 0
    valid_records = 0
    stale_records = 0
    malformed_records = 0
    diagnostics: list[str] = []
    for snapshot in reference_snapshots:
        payload = snapshot.payload
        records = payload.get("normalized_records", [])
        records = records if isinstance(records, list) else []
        snapshot_total = len(records)
        snapshot_valid = 0
        snapshot_stale = 0
        snapshot_malformed = 0
        snapshot_diagnostics = list(snapshot.issues)
        for record in records:
            if not isinstance(record, dict):
                snapshot_malformed += 1
                continue
            record_issues = _reference_record_issues(record, generated_at)
            if record_issues:
                snapshot_diagnostics.extend(record_issues)
            if "malformed_reference_record" in record_issues:
                snapshot_malformed += 1
                continue
            if "stale_reference_record" in record_issues:
                snapshot_stale += 1
            snapshot_valid += 1
        total_records += snapshot_total
        valid_records += snapshot_valid
        stale_records += snapshot_stale
        malformed_records += snapshot_malformed
        diagnostics.extend(snapshot_diagnostics)
        snapshots.append(
            {
                "path": str(snapshot.path),
                "source_id": payload.get("source_id"),
                "source_type": payload.get("source_type"),
                "schema_kind": payload.get("schema_kind"),
                "record_count": payload.get("record_count", snapshot_total),
                "normalized_count": payload.get("normalized_count", snapshot_total),
                "valid_record_count": snapshot_valid,
                "stale_record_count": snapshot_stale,
                "malformed_record_count": snapshot_malformed,
                "retrieved_at": payload.get("retrieved_at"),
                "stale_after": payload.get("stale_after"),
                "diagnostics": sorted(set(snapshot_diagnostics)),
            }
        )
    return {
        "source": "reference_snapshot_observability",
        "snapshot_count": len(reference_snapshots),
        "record_count": total_records,
        "valid_record_count": valid_records,
        "stale_record_count": stale_records,
        "malformed_record_count": malformed_records,
        "diagnostics": sorted(set(diagnostics)),
        "snapshots": snapshots,
        "notes": "Reference-only sportsbook rows are observability metadata only; they are never candidate legs.",
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


def _output_payload(
    polymarket: LoadedSnapshot,
    kalshi: LoadedSnapshot,
    generated_at: datetime,
    snapshot_issues: dict[str, list[str]],
    pairs: list[dict[str, Any]],
    reference_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source": "live_snapshot_matcher",
        "generated_at": generated_at.isoformat(),
        "inputs": {
            "polymarket": str(polymarket.path),
            "kalshi": str(kalshi.path),
            "reference_snapshots": [snapshot["path"] for snapshot in (reference_context or {}).get("snapshots", [])],
        },
        "snapshot_issues": snapshot_issues,
        "reference_context": reference_context or _reference_context_summary([], generated_at),
        "pair_count": len(pairs),
        "pairs": pairs,
        "disclaimer": "Read-only prototype. Pairs are WATCH/MANUAL_REVIEW only; no scoring, arb, profit, or trading claim.",
    }
