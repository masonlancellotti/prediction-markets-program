from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
REGISTRY_VERSION = "canonical_convention_registry_v0"
REPORT_SOURCE = "canonical_convention_registry_audit_v0"

FAMILY_FED_FOMC = "FED_FOMC"
FAMILY_CRYPTO_PRICE_THRESHOLD = "CRYPTO_PRICE_THRESHOLD"
FAMILY_SPORTS_GAME_RESULT = "SPORTS_GAME_RESULT"
FAMILY_SPORTS_FUTURES_CHAMPIONSHIP = "SPORTS_FUTURES_CHAMPIONSHIP"

SUPPORTED_FAMILIES = {
    FAMILY_FED_FOMC,
    FAMILY_CRYPTO_PRICE_THRESHOLD,
    FAMILY_SPORTS_GAME_RESULT,
    FAMILY_SPORTS_FUTURES_CHAMPIONSHIP,
}

REQUIRED_ENTRY_FIELDS = (
    "registry_version",
    "entry_id",
    "family",
    "reviewer",
    "reviewed_at",
    "applies_to_scope",
    "typed_key_requirements",
    "canonical_source_kind",
    "evidence_quote_or_excerpt",
    "limitations",
    "confidence",
)

VALID_SOURCE_KINDS = {
    "official_source_url",
    "official_source_description",
    "federal_reserve_official",
    "crypto_index_official",
    "league_official_source",
    "venue_rules_convention",
    "reviewer_registered_convention",
}

INVALID_EVIDENCE_KINDS = {
    "title",
    "title_only",
    "title_similarity",
    "graph",
    "graph_hint",
    "market_graph",
    "llm",
    "llm_hint",
    "llm_relationship_hypothesis",
    "planted_hint",
    "unreviewed_hint",
}

SCOPE_SPECIFICITY_KEYS = {
    "venue",
    "event_ticker",
    "event_ticker_prefix",
    "ticker_prefix",
    "event_slug_prefix",
    "market_id_prefix",
    "series_ticker",
    "league",
    "sport",
}

FIELD_ALIASES = {
    "review_date": "reviewed_at",
    "scope": "applies_to_scope",
    "registry_entry_id": "entry_id",
}

OPERATOR_ALIASES = {
    ">": "above",
    "greater than": "above",
    "above": "above",
    "<": "below",
    "less than": "below",
    "below": "below",
    ">=": "at_least",
    "at least": "at_least",
    "<=": "at_most",
    "at most": "at_most",
}


@dataclass(frozen=True)
class RegistryLoadResult:
    entries: list[dict[str, Any]]
    valid_entries: list[dict[str, Any]]
    invalid_entries: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    summary: dict[str, Any]


def build_canonical_convention_registry_audit(
    *,
    registry_path: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    loaded = load_canonical_convention_registry(registry_path)
    return {
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "registry_version": REGISTRY_VERSION,
        "generated_at": generated.isoformat(),
        "registry_path": str(registry_path),
        "summary": loaded.summary,
        "entries": loaded.entries,
        "valid_entries": loaded.valid_entries,
        "invalid_entries": loaded.invalid_entries,
        "warnings": loaded.warnings,
        "safety": {
            "saved_files_only": True,
            "live_fetch_attempted": False,
            "execution_or_order_logic_added": False,
            "account_or_auth_logic_added": False,
            "paper_candidate_emitted": False,
            "affects_evaluator_gates": False,
            "registry_can_bypass_typed_key_matching": False,
            "registry_can_bypass_quote_depth_freshness_or_fee_gates": False,
            "title_only_evidence_accepted": False,
            "graph_or_llm_evidence_accepted": False,
        },
    }


def audit_registry_review_until_status(
    entry: dict[str, Any],
    *,
    now: datetime,
    expiring_soon_days: int = 7,
) -> dict[str, Any]:
    _require_tz_aware(now, "now")
    blockers: list[str] = []
    review_until_raw = entry.get("review_until")
    review_until = _parse_review_until(review_until_raw)
    if _is_blank(review_until_raw):
        blockers.append("missing_review_until")
    elif review_until is None:
        blockers.append("invalid_review_until")

    review_expired = bool(review_until is not None and review_until < now)
    seconds_until_expiry = int((review_until - now).total_seconds()) if review_until is not None else None
    review_expiring_soon = bool(
        review_until is not None
        and not review_expired
        and seconds_until_expiry is not None
        and seconds_until_expiry <= expiring_soon_days * 24 * 60 * 60
    )
    valid_current_review = bool(review_until is not None and not review_expired and not blockers)
    if review_expired:
        blockers.append("review_expired")
    return {
        "entry_id": entry.get("entry_id"),
        "review_until": review_until_raw,
        "review_until_parsed": review_until.isoformat() if review_until is not None else None,
        "valid_current_review": valid_current_review,
        "review_expiring_soon": review_expiring_soon,
        "review_expired": review_expired,
        "seconds_until_expiry": seconds_until_expiry,
        "blockers": sorted(set(blockers)),
    }


def load_canonical_convention_registry(path: Path | None) -> RegistryLoadResult:
    if path is None:
        return RegistryLoadResult([], [], [], [], _summary([], [], []))
    if not path.exists():
        warning = {
            "source_file": str(path),
            "reason_code": "registry_file_missing",
            "blocker": "saved_registry_missing",
        }
        return RegistryLoadResult([], [], [], [warning], _summary([], [], [warning]))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        warning = {
            "source_file": str(path),
            "reason_code": "registry_invalid_json",
            "blocker": "saved_registry_invalid",
        }
        return RegistryLoadResult([], [], [], [warning], _summary([], [], [warning]))
    except OSError as exc:
        warning = {
            "source_file": str(path),
            "reason_code": "registry_read_error",
            "blocker": f"saved_registry_read_error:{type(exc).__name__}",
        }
        return RegistryLoadResult([], [], [], [warning], _summary([], [], [warning]))

    entries = payload.get("entries") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        warning = {
            "source_file": str(path),
            "reason_code": "registry_no_entries",
            "blocker": "saved_registry_no_entries",
        }
        return RegistryLoadResult([], [], [], [warning], _summary([], [], [warning]))

    top_version = payload.get("registry_version") if isinstance(payload, dict) else None
    audited: list[dict[str, Any]] = []
    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for index, raw_entry in enumerate(entries):
        entry = _canonicalize_entry(raw_entry, top_version=top_version)
        audit_row = _audit_entry(entry, index=index)
        audited.append(audit_row)
        if audit_row["valid"]:
            valid.append(entry)
        else:
            invalid.append(audit_row)
    return RegistryLoadResult(audited, valid, invalid, [], _summary(audited, invalid, []))


def match_canonical_registry_entry(
    entries: list[dict[str, Any]],
    *,
    venue: str,
    family: str,
    event_ticker: str | None,
    ticker: str | None,
    event_slug: str | None,
    typed_keys: dict[str, Any],
) -> dict[str, Any] | None:
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("family") != family:
            continue
        scope = entry.get("applies_to_scope") if isinstance(entry.get("applies_to_scope"), dict) else {}
        if not _scope_matches(
            scope,
            venue=venue,
            event_ticker=event_ticker,
            ticker=ticker,
            event_slug=event_slug,
        ):
            continue
        typed_match = _typed_key_requirements_match(
            entry.get("typed_key_requirements"),
            typed_keys=typed_keys,
        )
        if not typed_match["matched"]:
            continue
        return {
            "matched": True,
            "registry_version": entry.get("registry_version"),
            "entry_id": entry.get("entry_id"),
            "registry_entry_id": entry.get("entry_id"),
            "family": entry.get("family"),
            "reviewer": entry.get("reviewer"),
            "reviewed_at": entry.get("reviewed_at"),
            "review_until": entry.get("review_until"),
            "expires_at": entry.get("expires_at"),
            "applies_to_scope": scope,
            "typed_key_requirements": entry.get("typed_key_requirements"),
            "typed_keys_matched": typed_match["matched_keys"],
            "canonical_source_kind": entry.get("canonical_source_kind"),
            "canonical_source_url": entry.get("canonical_source_url"),
            "official_source_description": entry.get("official_source_description"),
            "evidence_quote_or_excerpt": entry.get("evidence_quote_or_excerpt"),
            "limitations": entry.get("limitations"),
            "confidence": entry.get("confidence"),
            "diagnostic_only": True,
            "affects_evaluator_gates": False,
        }
    return None


def _canonicalize_entry(raw_entry: Any, *, top_version: Any) -> dict[str, Any]:
    if not isinstance(raw_entry, dict):
        return {"_raw_type": type(raw_entry).__name__}
    entry = dict(raw_entry)
    for old, new in FIELD_ALIASES.items():
        if new not in entry and old in entry:
            entry[new] = entry[old]
    if "registry_version" not in entry and top_version is not None:
        entry["registry_version"] = top_version
    return entry


def _audit_entry(entry: dict[str, Any], *, index: int) -> dict[str, Any]:
    blockers: list[str] = []
    if "_raw_type" in entry:
        blockers.append("entry_not_object")
        return _audit_row(entry, index=index, blockers=blockers)

    for field in REQUIRED_ENTRY_FIELDS:
        if _is_blank(entry.get(field)):
            blockers.append(f"missing_{field}")
    if _is_blank(entry.get("canonical_source_url")) and _is_blank(entry.get("official_source_description")):
        blockers.append("missing_canonical_source")
    if _is_blank(entry.get("expires_at")) and _is_blank(entry.get("review_until")):
        blockers.append("missing_expires_at_or_review_until")

    if entry.get("registry_version") != REGISTRY_VERSION:
        blockers.append("unsupported_registry_version")
    if entry.get("family") not in SUPPORTED_FAMILIES:
        blockers.append("unsupported_family")
    if not _valid_isoish_datetime(entry.get("reviewed_at")):
        blockers.append("invalid_reviewed_at")
    expiry_value = entry.get("expires_at") or entry.get("review_until")
    if not _valid_isoish_datetime(expiry_value):
        blockers.append("invalid_expires_at_or_review_until")

    source_kind = _normalized_string(entry.get("canonical_source_kind"))
    if not source_kind or source_kind not in VALID_SOURCE_KINDS:
        blockers.append("source_kind_unknown")
    source_url = entry.get("canonical_source_url")
    if not _is_blank(source_url) and not _looks_like_url(source_url):
        blockers.append("invalid_canonical_source_url")

    scope = entry.get("applies_to_scope")
    scope_blockers = _scope_blockers(scope)
    blockers.extend(scope_blockers)

    typed_requirements = entry.get("typed_key_requirements")
    blockers.extend(_typed_key_requirement_blockers(typed_requirements, entry.get("family")))

    evidence_blockers = _evidence_blockers(entry.get("evidence_quote_or_excerpt"))
    blockers.extend(evidence_blockers)

    if _is_blank(entry.get("limitations")):
        blockers.append("missing_limitations")
    if _is_blank(entry.get("confidence")):
        blockers.append("missing_confidence")

    return _audit_row(entry, index=index, blockers=sorted(set(blockers)))


def _audit_row(entry: dict[str, Any], *, index: int, blockers: list[str]) -> dict[str, Any]:
    return {
        "index": index,
        "entry_id": entry.get("entry_id"),
        "family": entry.get("family"),
        "reviewer": entry.get("reviewer"),
        "reviewed_at": entry.get("reviewed_at"),
        "valid": not blockers,
        "blockers": blockers,
        "applies_to_scope": entry.get("applies_to_scope"),
        "typed_key_requirements": entry.get("typed_key_requirements"),
        "canonical_source_kind": entry.get("canonical_source_kind"),
        "has_canonical_source_url": bool(entry.get("canonical_source_url")),
        "has_official_source_description": bool(entry.get("official_source_description")),
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
    }


def _scope_blockers(scope: Any) -> list[str]:
    blockers: list[str] = []
    if not isinstance(scope, dict) or not scope:
        return ["missing_scope"]
    if _is_broad_value(scope.get("scope")) or _is_broad_value(scope.get("applies_to")):
        blockers.append("overbroad_scope")
    specific_keys = [key for key in SCOPE_SPECIFICITY_KEYS if not _is_blank(scope.get(key))]
    if not specific_keys:
        blockers.append("overbroad_scope")
    if "venue" not in specific_keys:
        blockers.append("missing_scope_venue")
    for key, value in scope.items():
        if _is_broad_value(value):
            blockers.append("overbroad_scope")
            break
    return sorted(set(blockers))


def _typed_key_requirement_blockers(requirements: Any, family: Any) -> list[str]:
    if not isinstance(requirements, dict) or not requirements:
        return ["missing_typed_key_requirements"]
    blockers: list[str] = []
    required = requirements.get("required")
    if not isinstance(required, list) or not required or any(not isinstance(key, str) or not key.strip() for key in required):
        blockers.append("missing_typed_key_requirements")
    match = requirements.get("match")
    if match is not None and (not isinstance(match, dict) or any(_is_blank(key) or _is_blank(value) for key, value in match.items())):
        blockers.append("invalid_typed_key_match_requirements")
    if family == FAMILY_CRYPTO_PRICE_THRESHOLD and isinstance(match, dict):
        required_exact = {"asset", "threshold_value", "threshold_operator", "measurement_date", "price_source_index"}
        required_source_convention = {"asset", "measurement_date", "price_source_index"}
        if not (required_exact.issubset(set(match)) or required_source_convention.issubset(set(match))):
            blockers.append("crypto_registry_requires_exact_asset_date_source_threshold_operator")
    return blockers


def _evidence_blockers(evidence: Any) -> list[str]:
    blockers: list[str] = []
    if _is_blank(evidence):
        return ["missing_evidence"]
    kind = ""
    text = ""
    if isinstance(evidence, dict):
        kind = _normalized_string(evidence.get("kind") or evidence.get("source_kind") or "")
        text = str(evidence.get("text") or evidence.get("quote") or evidence.get("excerpt") or "")
    else:
        text = str(evidence)
    if not text.strip():
        blockers.append("missing_evidence")
    if kind in INVALID_EVIDENCE_KINDS:
        if "title" in kind:
            blockers.append("title_only_evidence")
        else:
            blockers.append("graph_or_llm_evidence")
    text_lower = text.lower()
    if re.search(r"\btitle[-_\s]*(only|similarity|match)\b", text_lower):
        blockers.append("title_only_evidence")
    if re.search(r"\b(graph[_\s-]*hint|market[_\s-]*graph|llm|language model)\b", text_lower):
        blockers.append("graph_or_llm_evidence")
    if re.search(r"\b(planted[_\s-]*hint|unreviewed[_\s-]*hint|hint_unreviewed_must_validate_against_venue_rules)\b", text_lower):
        blockers.append("planted_hint_evidence")
    return sorted(set(blockers))


def _typed_key_requirements_match(requirements: Any, *, typed_keys: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(requirements, dict):
        return {"matched": False, "matched_keys": [], "blockers": ["missing_typed_key_requirements"]}
    evidence = typed_keys.get("evidence") if isinstance(typed_keys.get("evidence"), dict) else {}
    missing = set(typed_keys.get("missing") or [])
    required = [key for key in requirements.get("required") or [] if isinstance(key, str)]
    matched_keys: list[str] = []
    blockers: list[str] = []
    for key in required:
        if key in missing or key not in evidence:
            blockers.append(f"missing_typed_key:{key}")
        else:
            matched_keys.append(key)
    match = requirements.get("match") if isinstance(requirements.get("match"), dict) else {}
    for key, expected in match.items():
        if key in missing or key not in evidence:
            blockers.append(f"missing_typed_key:{key}")
            continue
        actual = evidence.get(key)
        actual_value = actual.get("value") if isinstance(actual, dict) else actual
        if not _values_match(actual_value, expected):
            blockers.append(f"mismatched_typed_key:{key}")
        elif key not in matched_keys:
            matched_keys.append(key)
    return {
        "matched": not blockers and bool(required),
        "matched_keys": sorted(set(matched_keys)),
        "blockers": blockers,
    }


def _scope_matches(
    scope: dict[str, Any],
    *,
    venue: str,
    event_ticker: str | None,
    ticker: str | None,
    event_slug: str | None,
) -> bool:
    scope_venue = scope.get("venue")
    if scope_venue and not _value_in_scope(venue.lower(), scope_venue, lower=True):
        return False
    event_source = event_ticker or ""
    ticker_source = ticker or ""
    slug_source = event_slug or ""
    market_id_source = ticker or event_ticker or event_slug or ""
    if scope.get("event_ticker") and not _value_in_scope(event_source.upper(), scope.get("event_ticker"), lower=False):
        return False
    if scope.get("event_ticker_prefix") and not _has_prefix(event_source, scope.get("event_ticker_prefix")):
        return False
    if scope.get("ticker_prefix") and not _has_prefix(ticker_source, scope.get("ticker_prefix")):
        return False
    if scope.get("event_slug_prefix") and not _has_prefix(slug_source, scope.get("event_slug_prefix"), lower=True):
        return False
    if scope.get("market_id_prefix") and not _has_prefix(market_id_source, scope.get("market_id_prefix")):
        return False
    return True


def _values_match(actual: Any, expected: Any) -> bool:
    actual_number = _number_or_none(actual)
    expected_number = _number_or_none(expected)
    if actual_number is not None and expected_number is not None:
        return abs(actual_number - expected_number) < 1e-9
    return _normalize_match_value(actual) == _normalize_match_value(expected)


def _normalize_match_value(value: Any) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "").strip().lower())
    return OPERATOR_ALIASES.get(normalized, normalized)


def _has_prefix(value: str, prefix: Any, *, lower: bool = False) -> bool:
    values = [str(prefix_item) for prefix_item in prefix] if isinstance(prefix, list) else [str(prefix)]
    source = value.lower() if lower else value.upper()
    for item in values:
        candidate = item.lower() if lower else item.upper()
        if source.startswith(candidate):
            return True
    return False


def _value_in_scope(value: str, scope_value: Any, *, lower: bool = False) -> bool:
    values = scope_value if isinstance(scope_value, list) else [scope_value]
    source = value.lower() if lower else value
    for item in values:
        candidate = str(item).lower() if lower else str(item)
        if source == candidate:
            return True
    return False


def _summary(entries: list[dict[str, Any]], invalid_entries: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    valid_entries = [entry for entry in entries if entry.get("valid")]
    blocker_counts: Counter[str] = Counter()
    by_family: dict[str, dict[str, int]] = defaultdict(lambda: {"valid": 0, "invalid": 0})
    for entry in entries:
        family = str(entry.get("family") or "unknown")
        if entry.get("valid"):
            by_family[family]["valid"] += 1
        else:
            by_family[family]["invalid"] += 1
        for blocker in entry.get("blockers") or []:
            blocker_counts[str(blocker)] += 1
    for warning in warnings:
        blocker_counts[str(warning.get("blocker") or warning.get("reason_code") or "warning")] += 1
    return {
        "registry_entry_count": len(entries),
        "valid_entry_count": len(valid_entries),
        "invalid_entry_count": len(invalid_entries),
        "warning_count": len(warnings),
        "by_family": {family: dict(counts) for family, counts in sorted(by_family.items())},
        "top_blockers": dict(blocker_counts.most_common(10)),
    }


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict, tuple, set)):
        return not value
    return False


def _is_broad_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, list):
        return any(_is_broad_value(item) for item in value)
    normalized = _normalized_string(value)
    return normalized in {"*", "all", "any", "global", "all_markets", "family_only"}


def _normalized_string(value: Any) -> str:
    return re.sub(r"\s+", "_", str(value or "").strip().lower())


def _looks_like_url(value: Any) -> bool:
    return isinstance(value, str) and bool(re.match(r"^https?://", value.strip(), re.IGNORECASE))


def _valid_isoish_datetime(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    candidate = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate):
        return True
    try:
        datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _parse_review_until(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate):
        try:
            return datetime.combine(date.fromisoformat(candidate), time.max, tzinfo=timezone.utc)
        except ValueError:
            return None
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _number_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{name} must be timezone-aware")
