from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.canonical_convention_registry import (
    load_canonical_convention_registry,
    match_canonical_registry_entry,
)
from relative_value.quote_freshness_policy import DEFAULT_STALENESS_SECONDS, quote_freshness_status
from relative_value.settlement_evidence_burden import (
    FAMILY_CRYPTO_PRICE_THRESHOLD,
    FAMILY_FED_FOMC,
    FAMILIES_ELIGIBLE_FOR_EXACT_PAYOFF,
    REPORT_SOURCE as SETTLEMENT_BURDEN_SOURCE,
    TIER_EXACT_PAYOFF_REVIEW_READY,
    TIER_FAMILY_TYPED_REVIEW_READY,
    TIER_SETTLEMENT_SOURCE_REVIEW_READY,
)


SCHEMA_VERSION = 1
REPORT_SOURCE = "family_graduation_plan_v1"

SUPPORTED_FAMILIES = (FAMILY_CRYPTO_PRICE_THRESHOLD, FAMILY_FED_FOMC)
FORBIDDEN_PAPER_LITERAL = "PAPER" + "_CANDIDATE"

# Canonical convention scope kinds. These describe how many rows a single
# reviewed registry entry can plausibly cover. They are diagnostic-only and
# do not by themselves promote any row up a review tier.
SCOPE_FED_MEETING_DATE = "FAMILY_FED_FOMC:venue:event_ticker_prefix:meeting_date"
SCOPE_CRYPTO_ASSET_MEASUREMENT = (
    "FAMILY_CRYPTO_PRICE_THRESHOLD:venue:event_ticker_prefix:asset:measurement_date"
)
SCOPE_PER_ROW_FALLBACK = "per_row_fallback"

# Planted source URLs are unreviewed hints. We label them explicitly so a
# reviewer never confuses the planted URL with venue-confirmed evidence.
SOURCE_URL_STATUS_HINT = "hint_unreviewed_must_validate_against_venue_rules"
SOURCE_URL_STATUS_NONE = "no_url_hint_planted"


def build_family_graduation_report(
    *,
    input_dir: Path,
    family: str | None = None,
    registry_path: Path | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    burden_path = input_dir / "settlement_evidence_burden.json"
    burden_payload, burden_warning = _load_json(burden_path)
    warnings: list[dict[str, Any]] = []
    if burden_warning is not None:
        warnings.append(burden_warning)
    burden_rows = _burden_rows(burden_payload)

    selected_family = _select_family(burden_rows, family)
    if selected_family is None:
        selected_family = family or FAMILY_CRYPTO_PRICE_THRESHOLD
        warnings.append(
            {
                "source_file": str(burden_path),
                "blocker": "no_supported_family_typed_ready_rows",
                "message": "No CRYPTO_PRICE_THRESHOLD or FED_FOMC family-typed rows were found.",
            }
        )

    registry_result = load_canonical_convention_registry(registry_path) if registry_path is not None else None
    registry_entries = registry_result.valid_entries if registry_result is not None else []
    if registry_result is not None:
        warnings.extend(registry_result.warnings)

    normalized_rows = _normalized_rows(input_dir / "normalized_markets_v0.json")
    normalized_index = _normalized_index(normalized_rows)
    standardized_payload, standardized_warning = _load_json(input_dir / "standardized_family_candidates.json")
    if standardized_warning is not None:
        warnings.append(standardized_warning)
    standardized_index = _standardized_index(standardized_payload)

    candidate_rows = [
        _graduation_row(
            row,
            normalized_index=normalized_index,
            standardized_index=standardized_index,
            registry_entries=registry_entries,
            now=generated,
            staleness_seconds=DEFAULT_STALENESS_SECONDS,
        )
        for row in burden_rows
        if row.get("family") == selected_family
    ]
    candidate_rows = [row for row in candidate_rows if row is not None]
    registry_proposals = _unique_registry_proposals(candidate_rows)
    registry_proposal_groups = _registry_proposal_groups(candidate_rows)
    registry_loaded = registry_result is not None
    registry_notes = (
        "A reviewed registry path was supplied or resolved from the guarded CLI default. "
        "Existing registry match metrics count only actual matching reviewed registry entries; "
        "registry proposals remain untrusted and still require human review."
        if registry_loaded
        else (
            "When no --registry-path is supplied, this report cannot match any row against a reviewed "
            "registry entry. The existing_registry_match_count metric will be 0 even if a registry exists "
            "elsewhere on disk. Run audit-canonical-registry-coverage for an authoritative coverage view."
        )
    )
    registry_status = {
        "registry_path_supplied": registry_path is not None,
        "registry_loaded": registry_loaded,
        "registry_entry_count": len(registry_entries),
        "match_attempts_against_registry": registry_loaded,
        "registry_proposal_is_trust": False,
        "human_review_required_for_registry": True,
        "notes": registry_notes,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "input_dir": str(input_dir),
        "family": selected_family,
        "family_auto_selected": family is None,
        "registry_path": str(registry_path) if registry_path is not None else None,
        "registry_status": registry_status,
        "summary": _summary(candidate_rows, registry_proposals, registry_proposal_groups, warnings),
        "rows": candidate_rows,
        "registry_proposals": registry_proposals,
        "registry_proposal_groups": registry_proposal_groups,
        "warnings": warnings,
        "docs_note": (
            "Registry proposals are not trust. A human reviewer must validate scope, source, evidence, "
            "limitations, and expiry before any source/registry evidence can support exact-payoff review."
        ),
        "safety": {
            "saved_files_only": True,
            "live_fetch_attempted": False,
            "execution_or_order_logic_added": False,
            "account_or_auth_logic_added": False,
            "paper_candidate_emitted": False,
            "affects_evaluator_gates": False,
            "registry_proposal_is_trust": False,
            "human_review_required_for_registry": True,
            "source_fetching_attempted": False,
            "exact_payoff_rows_created": False,
        },
    }


def write_family_graduation_files(
    *,
    input_dir: Path,
    json_output: Path,
    markdown_output: Path,
    family: str | None = None,
    registry_path: Path | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_family_graduation_report(
        input_dir=input_dir,
        family=family,
        registry_path=registry_path,
        generated_at=generated_at,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_family_graduation_markdown(report), encoding="utf-8")
    return report


def render_family_graduation_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Family Graduation Plan",
        "",
        "Saved-file-only diagnostic. This report proposes manual registry review work; registry proposal is not trust.",
        "Human review is required before source or registry evidence can support exact-payoff review.",
        "",
        "## Summary",
        "",
        f"- family: `{report.get('family')}`",
        f"- family_auto_selected: `{str(report.get('family_auto_selected', False)).lower()}`",
        f"- candidate_rows: `{summary.get('candidate_row_count', 0)}`",
        f"- family_typed_ready_rows: `{summary.get('family_typed_ready_count', 0)}`",
        f"- registry_proposal_count: `{summary.get('registry_proposal_count', 0)}`",
        f"- registry_proposal_group_count: `{summary.get('registry_proposal_group_count', 0)}` (coarse canonical scopes)",
        f"- registry_path_supplied: `{str(bool((report.get('registry_status') or {}).get('registry_path_supplied'))).lower()}`",
        f"- registry_loaded: `{str(bool((report.get('registry_status') or {}).get('registry_loaded'))).lower()}` (entries=`{(report.get('registry_status') or {}).get('registry_entry_count', 0)}`)",
        f"- existing_reviewed_registry_match_count: `{summary.get('existing_reviewed_registry_match_count', 0)}`",
        f"- projected_exact_review_if_registry_reviewed_count: `{summary.get('projected_exact_review_if_registry_reviewed_count', 0)}`",
        f"- projected_exact_review_from_existing_registry_count: `{summary.get('projected_exact_review_from_existing_registry_count', 0)}`",
        f"- projected_execution_ready_count: `{summary.get('projected_execution_ready_count', 0)}`",
        f"- paper_candidate_count: `{summary.get('paper_candidate_count', 0)}`",
        "",
        "## Top Blockers",
        "",
    ]
    blockers = summary.get("top_blockers") or []
    if blockers:
        lines.extend(["| Blocker | Count |", "|---|---:|"])
        for blocker in blockers[:12]:
            lines.append(f"| {_md(blocker.get('blocker'))} | {_md(blocker.get('count'))} |")
    else:
        lines.append("(none)")
    lines.extend(
        [
            "",
            "## Candidate Rows",
            "",
            "| Venue | Market | Current tier | Registry proposal | Existing registry | Projected exact if reviewed | Blockers |",
            "|---|---|---|---|---|---:|---|",
        ]
    )
    for row in (report.get("rows") or [])[:100]:
        projection = row.get("projection") or {}
        proposal = row.get("registry_proposal") or {}
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("venue")),
                    _md(row.get("market_id") or row.get("ticker")),
                    _md(row.get("current_review_tier")),
                    _md(proposal.get("proposal_id")),
                    _md(str(projection.get("existing_reviewed_registry_match")).lower()),
                    _md(str(projection.get("can_upgrade_to_exact_review_if_reviewed")).lower()),
                    _md(",".join(row.get("current_blockers") or []) or ",".join(projection.get("projected_blockers_if_registry_or_source_added") or [])),
                ]
            )
            + " |"
        )
    if not report.get("rows"):
        lines.append("| (none) | (none) | (none) | (none) | false | false | (none) |")
    lines.extend(
        [
            "",
            "## Registry Proposals",
            "",
            "| Proposal | Source kind | Source URL candidate | Reviewer required | Can upgrade if reviewed | Limitations |",
            "|---|---|---|---:|---:|---|",
        ]
    )
    for proposal in report.get("registry_proposals") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(proposal.get("proposal_id")),
                    _md(proposal.get("source_kind")),
                    _md(proposal.get("source_url_candidate")),
                    _md(str(proposal.get("reviewer_required")).lower()),
                    _md(str(proposal.get("can_upgrade_to_exact_review_if_reviewed")).lower()),
                    _md("; ".join(proposal.get("limitations") or [])),
                ]
            )
            + " |"
        )
    if not report.get("registry_proposals"):
        lines.append("| (none) | (none) | (none) | true | false | (none) |")
    lines.extend(
        [
            "",
            "## Registry Proposal Groups (coarse canonical scope)",
            "",
            "Each group is one canonical convention a single reviewed registry entry can plausibly cover. Reviewing one group can graduate many rows; the per-row proposals above remain for traceability.",
            "",
            "| Scope kind | Scope key | Rows | Typed-complete | Eligible if reviewed | Source URL candidate | Source URL status |",
            "|---|---|---:|---:|---:|---|---|",
        ]
    )
    for group in report.get("registry_proposal_groups") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(group.get("scope_kind")),
                    _md(group.get("scope_key")),
                    _md(group.get("row_count")),
                    _md(group.get("rows_with_typed_keys_complete")),
                    _md(group.get("rows_eligible_to_upgrade_to_exact_review_if_reviewed")),
                    _md(group.get("source_url_candidate")),
                    _md(group.get("source_url_candidate_status")),
                ]
            )
            + " |"
        )
    if not report.get("registry_proposal_groups"):
        lines.append("| (none) | (none) | 0 | 0 | 0 | (none) | (none) |")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- saved_files_only: `true`",
            "- live_fetch_attempted: `false`",
            "- paper_candidate_emitted: `false`",
            "- affects_evaluator_gates: `false`",
            "- registry_proposal_is_trust: `false`",
        ]
    )
    return "\n".join(lines) + "\n"


def _select_family(rows: list[dict[str, Any]], requested: str | None) -> str | None:
    if requested:
        if requested not in SUPPORTED_FAMILIES:
            raise ValueError(f"unsupported family for graduation plan: {requested}")
        return requested
    counts = Counter(
        str(row.get("family"))
        for row in rows
        if row.get("family") in SUPPORTED_FAMILIES
        and row.get("review_readiness_tier") == TIER_FAMILY_TYPED_REVIEW_READY
    )
    if not counts:
        return None
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _graduation_row(
    row: dict[str, Any],
    *,
    normalized_index: dict[tuple[str, str], dict[str, Any]],
    standardized_index: dict[tuple[str, str], dict[str, Any]],
    registry_entries: list[dict[str, Any]],
    now: datetime,
    staleness_seconds: int,
) -> dict[str, Any] | None:
    typed_keys = _typed_keys(row)
    missing_typed_keys = list(row.get("missing_typed_keys") or [])
    normalized = _matching_normalized(row, normalized_index)
    quote_depth = _quote_depth_status(normalized, now=now, staleness_seconds=staleness_seconds)
    fee_metadata = _fee_status(normalized)
    existing_registry = _existing_registry_match(row, typed_keys=typed_keys, registry_entries=registry_entries)
    registry_match = row.get("registry_match") if isinstance(row.get("registry_match"), dict) else None
    reviewed_registry_match = existing_registry or registry_match
    standardized_group = standardized_index.get((str(row.get("family")), _stable_typed_key(typed_keys)))
    proposal = _registry_proposal(row, typed_keys=typed_keys, missing_typed_keys=missing_typed_keys)
    source_missing = not bool(row.get("settlement_source_url_present")) and not bool(reviewed_registry_match)
    projection = _projection(
        row,
        proposal=proposal,
        reviewed_registry_match=reviewed_registry_match,
        typed_keys=typed_keys,
        missing_typed_keys=missing_typed_keys,
        quote_depth=quote_depth,
        fee_metadata=fee_metadata,
        source_missing=source_missing,
    )
    return {
        "family": row.get("family"),
        "venue": row.get("venue"),
        "event_id": row.get("event_id"),
        "event_ticker": row.get("event_ticker"),
        "event_slug": row.get("event_slug"),
        "market_id": row.get("market_id") or row.get("ticker"),
        "ticker": row.get("ticker"),
        "title": row.get("title"),
        "typed_keys": typed_keys,
        "required_typed_keys": list(row.get("required_typed_keys") or []),
        "present_typed_keys": list(row.get("present_typed_keys") or []),
        "missing_typed_keys": missing_typed_keys,
        "current_review_tier": row.get("review_readiness_tier"),
        "current_blockers": list(row.get("blockers") or []),
        "missing_source_or_registry_evidence": {
            "settlement_source_url_present": bool(row.get("settlement_source_url_present")),
            "existing_registry_match_present": bool(reviewed_registry_match),
            "missing": source_missing,
            "blockers": ["missing_source_or_registry_evidence"] if source_missing else [],
        },
        "quote_freshness_status": quote_depth.get("quote_freshness_status"),
        "missing_quote_depth_freshness_evidence": quote_depth,
        "missing_fee_metadata_evidence": fee_metadata,
        "standardized_family_candidate_group": standardized_group,
        "registry_proposal": proposal,
        "projection": projection,
        "source_file": row.get("source_file"),
        "row_index": row.get("row_index"),
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "paper_candidate_emitted": False,
    }


def _projection(
    row: dict[str, Any],
    *,
    proposal: dict[str, Any],
    reviewed_registry_match: dict[str, Any] | None,
    typed_keys: dict[str, Any],
    missing_typed_keys: list[str],
    quote_depth: dict[str, Any],
    fee_metadata: dict[str, Any],
    source_missing: bool,
) -> dict[str, Any]:
    family = str(row.get("family") or "")
    typed_complete = bool(row.get("required_typed_keys")) and not missing_typed_keys
    family_exact_eligible = family in FAMILIES_ELIGIBLE_FOR_EXACT_PAYOFF
    existing_source = bool(row.get("settlement_source_url_present")) or bool(reviewed_registry_match)
    quote_freshness = quote_depth.get("quote_freshness_status") if isinstance(quote_depth.get("quote_freshness_status"), dict) else {}
    quote_freshness_blocker = _string_or_none(quote_freshness.get("blocker"))
    quote_is_fresh = quote_freshness.get("is_fresh") is True
    can_upgrade_if_reviewed = bool(
        typed_complete and family_exact_eligible and (proposal.get("source_url_candidate") or proposal.get("official_source_description"))
        and quote_is_fresh
    )
    existing_projected_tier = row.get("review_readiness_tier")
    if typed_complete and family_exact_eligible and existing_source and quote_is_fresh:
        existing_projected_tier = TIER_EXACT_PAYOFF_REVIEW_READY
    elif typed_complete and existing_source:
        existing_projected_tier = TIER_SETTLEMENT_SOURCE_REVIEW_READY

    reviewed_projected_tier = row.get("review_readiness_tier")
    if can_upgrade_if_reviewed:
        reviewed_projected_tier = TIER_EXACT_PAYOFF_REVIEW_READY
    elif typed_complete and (proposal.get("source_url_candidate") or proposal.get("official_source_description")):
        reviewed_projected_tier = TIER_SETTLEMENT_SOURCE_REVIEW_READY

    blockers: list[str] = []
    if missing_typed_keys:
        blockers.append("missing_required_typed_keys")
        blockers.extend(f"missing_typed_key:{key}" for key in missing_typed_keys)
    if source_missing and not reviewed_registry_match:
        blockers.append("registry_or_source_evidence_requires_human_review")
    if quote_freshness_blocker:
        blockers.append(quote_freshness_blocker)
    if quote_depth.get("missing"):
        blockers.append("missing_quote_depth_or_freshness")
    if fee_metadata.get("missing"):
        blockers.append("missing_fee_metadata")
    blockers.append("pair_review_not_performed")
    return {
        "existing_reviewed_registry_match": bool(reviewed_registry_match),
        "existing_registry_match": reviewed_registry_match,
        "current_tier_preserved": row.get("review_readiness_tier"),
        "projected_tier_from_existing_registry_or_source": existing_projected_tier,
        "projected_tier_if_registry_reviewed": reviewed_projected_tier,
        "can_upgrade_to_exact_review_if_reviewed": can_upgrade_if_reviewed,
        "can_upgrade_to_execution_evaluation_if_reviewed": False,
        "projected_execution_ready": False,
        "projected_blockers_if_registry_or_source_added": _unique_strings(blockers),
    }


def _registry_proposal(row: dict[str, Any], *, typed_keys: dict[str, Any], missing_typed_keys: list[str]) -> dict[str, Any]:
    family = str(row.get("family") or "")
    source_url, source_kind, description = _source_candidate(family, typed_keys)
    typed_key_scope = {
        "family": family,
        "venue": row.get("venue"),
        "event_ticker": row.get("event_ticker"),
        "ticker": row.get("ticker"),
        "event_slug": row.get("event_slug"),
        "typed_key_match": typed_keys,
    }
    can_upgrade = bool(
        not missing_typed_keys
        and family in FAMILIES_ELIGIBLE_FOR_EXACT_PAYOFF
        and (source_url or description)
    )
    recommended_scope = _recommended_registry_scope(family, row=row, typed_keys=typed_keys)
    return {
        "proposal_id": f"{family}:{row.get('venue')}:{row.get('ticker') or row.get('market_id')}",
        "source_url_candidate": source_url,
        "source_url_candidate_status": SOURCE_URL_STATUS_HINT if source_url else SOURCE_URL_STATUS_NONE,
        "source_kind": source_kind,
        "official_source_description": description,
        "typed_key_scope": typed_key_scope,
        "typed_key_requirements": {
            "required": list(row.get("required_typed_keys") or []),
            "match": typed_keys,
        },
        "reviewer_required": True,
        "evidence_required": [
            "human reviewer identity",
            "reviewed_at timestamp",
            "canonical source URL or official source description",
            "short evidence quote or excerpt from official rules/source",
            "scope limitations and expiry or review_until date",
        ],
        "limitations": [
            "Proposal is not trusted evidence.",
            "Do not use title similarity, graph hints, or LLM hints to fill typed keys.",
            "Registry entries cannot bypass quote, depth, freshness, fee, relationship, or evaluator gates.",
            "source_url_candidate is a planted hint and must be validated against the venue's published rules before being trusted.",
        ],
        "can_upgrade_to_exact_review_if_reviewed": can_upgrade,
        "recommended_registry_scope": recommended_scope,
    }


def _recommended_registry_scope(family: str, *, row: dict[str, Any], typed_keys: dict[str, Any]) -> dict[str, Any]:
    venue = _string_or_none(row.get("venue")) or ""
    event_ticker = _string_or_none(row.get("event_ticker")) or _string_or_none(row.get("ticker")) or ""
    event_ticker_prefix = _event_ticker_prefix(event_ticker)
    if family == FAMILY_FED_FOMC:
        meeting_date = _string_or_none(typed_keys.get("meeting_date"))
        scope_key = f"{family}|{venue}|{event_ticker_prefix}|{meeting_date or 'unknown'}"
        return {
            "scope_kind": SCOPE_FED_MEETING_DATE,
            "scope_key": scope_key,
            "scope_fields": {
                "family": family,
                "venue": venue,
                "event_ticker_prefix": event_ticker_prefix,
                "meeting_date": meeting_date,
            },
            "notes": "One reviewed registry entry should cover every threshold/rate-bound contract for a single FOMC meeting on the same venue event_ticker prefix.",
        }
    if family == FAMILY_CRYPTO_PRICE_THRESHOLD:
        asset = _string_or_none(typed_keys.get("asset"))
        measurement_date = _string_or_none(typed_keys.get("measurement_date"))
        scope_key = f"{family}|{venue}|{event_ticker_prefix}|{asset or 'unknown'}|{measurement_date or 'unknown'}"
        return {
            "scope_kind": SCOPE_CRYPTO_ASSET_MEASUREMENT,
            "scope_key": scope_key,
            "scope_fields": {
                "family": family,
                "venue": venue,
                "event_ticker_prefix": event_ticker_prefix,
                "asset": asset,
                "measurement_date": measurement_date,
            },
            "notes": "One reviewed registry entry should cover every threshold contract for the same asset + measurement timestamp on the same venue event_ticker prefix.",
        }
    return {
        "scope_kind": SCOPE_PER_ROW_FALLBACK,
        "scope_key": f"{family}|{venue}|{row.get('ticker') or row.get('market_id')}",
        "scope_fields": {
            "family": family,
            "venue": venue,
            "ticker": row.get("ticker"),
            "market_id": row.get("market_id"),
        },
        "notes": "No coarser canonical scope is defined for this family; one entry per row is the fallback.",
    }


def _event_ticker_prefix(event_ticker: str) -> str:
    if not event_ticker:
        return ""
    head = event_ticker.split("-", 1)[0]
    return head.strip().upper()


def _source_candidate(family: str, typed_keys: dict[str, Any]) -> tuple[str | None, str, str | None]:
    if family == FAMILY_FED_FOMC:
        return (
            "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
            "federal_reserve_official",
            "Federal Reserve/FOMC official policy decision and implementation materials for the meeting date.",
        )
    if family == FAMILY_CRYPTO_PRICE_THRESHOLD:
        asset = str(typed_keys.get("asset") or "").upper()
        index = str(typed_keys.get("price_source_index") or "").lower()
        if asset == "BTC" or "brti" in index or "bitcoin" in index:
            url = "https://www.cfbenchmarks.com/data/indices/BRTI"
        elif asset == "ETH" or "ethereum" in index:
            url = "https://www.cfbenchmarks.com/data/indices/ETHUSD_RTI"
        else:
            url = None
        return (
            url,
            "crypto_index_official",
            "Official crypto index source matching the venue rules price_source_index and measurement timestamp.",
        )
    return None, "reviewer_registered_convention", None


def _existing_registry_match(
    row: dict[str, Any],
    *,
    typed_keys: dict[str, Any],
    registry_entries: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not registry_entries:
        return None
    typed = {
        "required": list(row.get("required_typed_keys") or []),
        "present": list(row.get("present_typed_keys") or []),
        "missing": list(row.get("missing_typed_keys") or []),
        "evidence": {
            key: {"value": value, "source": "family_graduation:typed_key"}
            for key, value in typed_keys.items()
            if value is not None
        },
    }
    return match_canonical_registry_entry(
        registry_entries,
        venue=str(row.get("venue") or ""),
        family=str(row.get("family") or ""),
        event_ticker=_string_or_none(row.get("event_ticker")),
        ticker=_string_or_none(row.get("ticker")),
        event_slug=_string_or_none(row.get("event_slug")),
        typed_keys=typed,
    )


def _typed_keys(row: dict[str, Any]) -> dict[str, Any]:
    evidence = row.get("typed_key_evidence") if isinstance(row.get("typed_key_evidence"), dict) else {}
    typed: dict[str, Any] = {}
    for key in row.get("required_typed_keys") or []:
        value = evidence.get(key)
        if isinstance(value, dict):
            typed[key] = value.get("value")
        elif value is not None:
            typed[key] = value
    for key, value in evidence.items():
        if key not in typed:
            typed[key] = value.get("value") if isinstance(value, dict) else value
    return typed


def _quote_depth_status(
    normalized: dict[str, Any] | None,
    *,
    now: datetime,
    staleness_seconds: int,
) -> dict[str, Any]:
    if not normalized:
        freshness = quote_freshness_status(None, now=now, staleness_seconds=staleness_seconds)
        return {
            "quote_depth_ready": False,
            "captured_at": None,
            "quote_freshness_status": {
                **freshness,
                "source": "normalized_market_missing",
                "staleness_seconds": staleness_seconds,
            },
            "missing": True,
            "source": "normalized_market_missing",
            "blockers": ["missing_quote_depth_or_freshness_evidence"],
        }
    readiness = normalized.get("readiness") if isinstance(normalized.get("readiness"), dict) else {}
    quote = normalized.get("quote_depth") if isinstance(normalized.get("quote_depth"), dict) else {}
    ready = readiness.get("quote_depth_ready") is True
    freshness = quote_freshness_status(
        _string_or_none(quote.get("captured_at")),
        now=now,
        staleness_seconds=staleness_seconds,
    )
    blockers = [] if ready else list(quote.get("blockers") or ["missing_quote_depth_or_freshness_evidence"])
    if freshness.get("blocker"):
        blockers.append(str(freshness["blocker"]))
    return {
        "quote_depth_ready": ready,
        "captured_at": quote.get("captured_at"),
        "quote_freshness_status": {
            **freshness,
            "source": "normalized_markets_v0.quote_depth.captured_at",
            "staleness_seconds": staleness_seconds,
        },
        "missing": not ready or freshness.get("is_fresh") is not True,
        "source": "normalized_markets_v0",
        "blockers": _unique_strings(blockers),
    }


def _fee_status(normalized: dict[str, Any] | None) -> dict[str, Any]:
    if not normalized:
        return {
            "fee_metadata_ready": False,
            "missing": True,
            "source": "normalized_market_missing",
            "blockers": ["missing_fee_metadata"],
        }
    readiness = normalized.get("readiness") if isinstance(normalized.get("readiness"), dict) else {}
    fee = normalized.get("fee_metadata") if isinstance(normalized.get("fee_metadata"), dict) else {}
    ready = readiness.get("fee_metadata_ready") is True
    return {
        "fee_metadata_ready": ready,
        "fee_model_status": fee.get("fee_model_status"),
        "review_status": fee.get("review_status"),
        "missing": not ready,
        "source": "normalized_markets_v0",
        "blockers": [] if ready else list(fee.get("blockers") or ["missing_fee_metadata"]),
    }


def _matching_normalized(row: dict[str, Any], normalized_index: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any] | None:
    venue = str(row.get("venue") or "")
    for value in (row.get("ticker"), row.get("market_id"), row.get("event_id"), row.get("event_ticker"), row.get("event_slug")):
        text = _string_or_none(value)
        if text and (venue, text) in normalized_index:
            return normalized_index[(venue, text)]
    return None


def _normalized_index(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        venue = str(row.get("venue") or "")
        for key in ("ticker", "market_id", "event_id", "event_ticker", "event_slug"):
            value = _string_or_none(row.get(key))
            if venue and value:
                index[(venue, value)] = row
    return index


def _standardized_index(payload: Any) -> dict[tuple[str, str], dict[str, Any]]:
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {}
    return {
        (str(row.get("family")), _stable_typed_key(row.get("typed_key") if isinstance(row.get("typed_key"), dict) else {})): {
            "row_id": row.get("row_id"),
            "venues_involved": row.get("venues_involved"),
            "market_count": row.get("market_count"),
            "cross_venue": row.get("cross_venue"),
            "allowed_next_action": row.get("allowed_next_action"),
            "blockers": row.get("blockers"),
        }
        for row in rows
        if isinstance(row, dict)
    }


def _unique_registry_proposals(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    proposals: dict[str, dict[str, Any]] = {}
    for row in rows:
        proposal = row.get("registry_proposal")
        if isinstance(proposal, dict):
            proposals.setdefault(str(proposal.get("proposal_id")), proposal)
    return [proposals[key] for key in sorted(proposals)]


def _registry_proposal_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse per-row registry proposals by recommended_registry_scope.

    Each group represents one canonical convention scope that a single
    reviewed registry entry would plausibly cover. Per-row proposals stay
    available for traceability via `registry_proposals` and the row's own
    `registry_proposal.recommended_registry_scope.scope_key`.
    """
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        proposal = row.get("registry_proposal")
        if not isinstance(proposal, dict):
            continue
        scope = proposal.get("recommended_registry_scope") if isinstance(proposal.get("recommended_registry_scope"), dict) else {}
        scope_key = str(scope.get("scope_key") or proposal.get("proposal_id") or "")
        bucket = groups.get(scope_key)
        if bucket is None:
            bucket = {
                "scope_key": scope_key,
                "scope_kind": scope.get("scope_kind"),
                "scope_fields": scope.get("scope_fields") or {},
                "notes": scope.get("notes"),
                "source_url_candidate": proposal.get("source_url_candidate"),
                "source_url_candidate_status": proposal.get("source_url_candidate_status"),
                "source_kind": proposal.get("source_kind"),
                "official_source_description": proposal.get("official_source_description"),
                "row_count": 0,
                "rows_with_typed_keys_complete": 0,
                "rows_eligible_to_upgrade_to_exact_review_if_reviewed": 0,
                "example_proposal_ids": [],
                "reviewer_required": True,
                "limitations": list(proposal.get("limitations") or []),
            }
            groups[scope_key] = bucket
        bucket["row_count"] += 1
        if not row.get("missing_typed_keys"):
            bucket["rows_with_typed_keys_complete"] += 1
        if proposal.get("can_upgrade_to_exact_review_if_reviewed"):
            bucket["rows_eligible_to_upgrade_to_exact_review_if_reviewed"] += 1
        examples = bucket["example_proposal_ids"]
        if len(examples) < 5:
            pid = str(proposal.get("proposal_id"))
            if pid and pid not in examples:
                examples.append(pid)
    return [groups[key] for key in sorted(groups)]


def _summary(
    rows: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
    proposal_groups: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    blockers = Counter()
    current_tiers = Counter()
    projected_tiers = Counter()
    for row in rows:
        current_tiers[str(row.get("current_review_tier") or "UNKNOWN")] += 1
        projection = row.get("projection") or {}
        projected_tiers[str(projection.get("projected_tier_if_registry_reviewed") or "UNKNOWN")] += 1
        blockers.update(row.get("current_blockers") or [])
        blockers.update((projection.get("projected_blockers_if_registry_or_source_added") or []))
        blockers.update((row.get("missing_source_or_registry_evidence") or {}).get("blockers") or [])
    for warning in warnings:
        blockers[str(warning.get("blocker") or warning.get("reason_code") or "warning")] += 1
    return {
        "candidate_row_count": len(rows),
        "family_typed_ready_count": sum(1 for row in rows if row.get("current_review_tier") == TIER_FAMILY_TYPED_REVIEW_READY),
        "missing_typed_key_count": sum(1 for row in rows if row.get("missing_typed_keys")),
        "registry_proposal_count": len(proposals),
        "registry_proposal_group_count": len(proposal_groups),
        "existing_reviewed_registry_match_count": sum(1 for row in rows if (row.get("projection") or {}).get("existing_reviewed_registry_match")),
        "projected_exact_review_if_registry_reviewed_count": sum(
            1
            for row in rows
            if (row.get("projection") or {}).get("projected_tier_if_registry_reviewed") == TIER_EXACT_PAYOFF_REVIEW_READY
        ),
        "projected_exact_review_from_existing_registry_count": sum(
            1
            for row in rows
            if (row.get("projection") or {}).get("projected_tier_from_existing_registry_or_source") == TIER_EXACT_PAYOFF_REVIEW_READY
        ),
        "projected_execution_ready_count": 0,
        "ready_for_human_registry_review_count": sum(
            1
            for row in rows
            if (row.get("registry_proposal") or {}).get("can_upgrade_to_exact_review_if_reviewed")
        ),
        "current_review_tier_counts": dict(sorted(current_tiers.items())),
        "projected_review_tier_if_registry_reviewed_counts": dict(sorted(projected_tiers.items())),
        "top_blockers": [{"blocker": key, "count": count} for key, count in blockers.most_common(12)],
        "paper_candidate_count": 0,
        "warning_count": len(warnings),
    }


def _burden_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("source") != SETTLEMENT_BURDEN_SOURCE:
        return []
    rows = payload.get("markets")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _normalized_rows(path: Path) -> list[dict[str, Any]]:
    payload, warning = _load_json(path)
    if warning is not None or not isinstance(payload, dict):
        return []
    rows = payload.get("normalized_markets")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _load_json(path: Path) -> tuple[Any, dict[str, Any] | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, {"source_file": str(path), "reason_code": "json_file_missing", "blocker": "saved_json_file_missing"}
    except json.JSONDecodeError:
        return None, {"source_file": str(path), "reason_code": "invalid_json", "blocker": "saved_json_invalid"}
    except OSError as exc:
        return None, {"source_file": str(path), "reason_code": "json_read_error", "blocker": f"saved_json_read_error:{type(exc).__name__}"}


def _stable_typed_key(typed_key: dict[str, Any]) -> str:
    return json.dumps(typed_key, sort_keys=True, separators=(",", ":"))


def _unique_strings(values: list[str]) -> list[str]:
    return sorted({str(value) for value in values if str(value)})


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _md(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
