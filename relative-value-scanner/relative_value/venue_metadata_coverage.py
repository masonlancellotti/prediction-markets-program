from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1

TIER_INGESTED_ONLY = "INGESTED_ONLY"
TIER_MATCH_CANDIDATE_READY = "MATCH_CANDIDATE_READY"
TIER_RELATIONSHIP_REVIEW_READY = "RELATIONSHIP_REVIEW_READY"
TIER_EXECUTION_EVALUATION_READY = "EXECUTION_EVALUATION_READY"
TIER_BLOCKED_METADATA_INCOMPLETE = "BLOCKED_METADATA_INCOMPLETE"

KNOWN_FEE_MODEL_VENUES = {
    "kalshi": "known_default_fee_model",
    "polymarket": "known_default_fee_model",
}

CSV_FIELDS = [
    "venue",
    "event_id",
    "event_ticker",
    "event_slug",
    "market_id",
    "ticker",
    "token_id",
    "title",
    "category",
    "outcome_count",
    "has_settlement_rules",
    "has_settlement_source",
    "settlement_rules_text",
    "settlement_source_url",
    "settlement_source_kind",
    "close_time",
    "resolution_time",
    "resolution_time_kind",
    "status",
    "has_orderbook",
    "has_depth",
    "has_top_of_book",
    "has_quote_timestamp",
    "fee_model_status",
    "readiness_tier",
    "blockers",
    "settlement_metadata_blockers",
    "source_file",
]

SETTLEMENT_RULE_TEXT_ALIASES = (
    "settlement_rules",
    "rules",
    "rules_primary",
    "rules_secondary",
    "resolution_text",
    "resolution_criteria",
)

SETTLEMENT_ADVISORY_TEXT_ALIASES = (
    "description",
    "descriptionHtml",
    "description_html",
)

SETTLEMENT_SOURCE_URL_ALIASES = (
    "settlement_source_url",
    "settlementSourceUrl",
    "resolution_source_url",
    "resolutionSourceUrl",
    "source_url",
    "sourceUrl",
    "rules_url",
    "rulesUrl",
)

SETTLEMENT_SOURCE_TEXT_ALIASES = (
    "settlement_source",
    "settlementSource",
    "settlement_basis",
    "settlementBasis",
    "resolution_source",
    "resolutionSource",
    "settlement_source_raw_evidence",
)

RESOLUTION_TIME_ACTUAL_ALIASES = (
    "actual_resolution_time",
    "actualResolutionTime",
    "actual_settlement_time",
    "actualSettlementTime",
    "resolved_at",
    "resolvedAt",
    "settled_at",
    "settledAt",
)

RESOLUTION_TIME_EXPECTED_ALIASES = (
    "expected_expiration_time",
    "expectedExpirationTime",
)

RESOLUTION_TIME_DEADLINE_ALIASES = (
    "end_date",
    "endDate",
    "endDateIso",
    "expiration_time",
    "expirationTime",
    "latest_expiration_time",
    "latestExpirationTime",
    "resolution_date",
    "resolutionDate",
)

RESOLUTION_TIME_UNKNOWN_ALIASES = (
    "resolution_time",
    "resolutionTime",
    "settlement_time",
    "settlementTime",
)

SETTLEMENT_SOURCE_BLOCKERS = {
    "missing_settlement_source_url",
    "settlement_rules_text_only",
    "description_only_not_source",
    "source_kind_unknown",
    "source_evidence_missing",
}

URL_RE = re.compile(r"https?://[^\s<>\"')]+")

FIELD_ALIASES = {
    "venue": ("venue",),
    "event_id": ("event_id", "eventId", "event_ticker", "eventTicker", "event_slug", "eventSlug"),
    "event_ticker": ("event_ticker", "eventTicker"),
    "event_slug": ("event_slug", "eventSlug", "slug"),
    "market_id": ("market_id", "marketId", "id", "ticker", "market_ticker", "token_id", "tokenId", "asset_id", "condition_id", "conditionId", "clobTokenId"),
    "ticker": ("ticker", "market_ticker"),
    "token_id": ("token_id", "tokenId", "asset_id", "clob_token_id", "clobTokenId"),
    "title": ("title", "question", "market_title", "name"),
    "category": ("category", "sport", "topic", "series_ticker", "tag"),
    "settlement_rules": ("settlement_rules", "rules", "rules_primary", "rules_secondary", "resolution_text", "resolution_criteria", "description"),
    "settlement_source": (
        "settlement_source",
        "settlementSource",
        "settlement_basis",
        "settlementBasis",
        "resolution_source",
        "resolutionSource",
        "source_url",
        "rules_url",
        "settlement_source_raw_evidence",
    ),
    "close_time": ("close_time", "closeTime"),
    "resolution_time": (
        "resolution_time",
        "resolutionTime",
        "resolution_date",
        "resolutionDate",
        "settlement_time",
        "settlementTime",
        "expiration_time",
        "expirationTime",
        "expected_expiration_time",
        "expectedExpirationTime",
        "latest_expiration_time",
        "latestExpirationTime",
        "end_date",
        "endDate",
    ),
    "status": ("status", "state"),
    # Only fields that track when the bid/ask was sampled count as quote_timestamp.
    # Record-level update timestamps (updated_at/updatedAt/last_update_time/lastUpdateTime/
    # Kalshi's updated_time) reflect metadata edits, not orderbook freshness; crediting
    # them produces fake-edge "fresh quote" claims on stale orderbooks.
    "quote_timestamp": (
        "quote_timestamp",
        "quoteTimestamp",
        "market_data_timestamp",
        "marketDataTimestamp",
        "orderbook_timestamp",
        "orderbookTimestamp",
        "book_timestamp",
        "bookTimestamp",
        "collected_at",
        "collectedAt",
        "snapshot_time",
        "snapshotTime",
        "orderbook_captured_at",
        "orderbookCapturedAt",
    ),
    "fee_model_status": ("fee_model_status", "fee_model", "fee_rate", "fee_assumption_status"),
}

BLOCKER_FIELD_MAP = {
    "missing_venue": "venue",
    "missing_event_id": "event_id",
    "missing_market_id": "market_id",
    "missing_title": "title",
    "missing_outcome_list": "outcomes",
    "missing_settlement_rules": "settlement_rules",
    "missing_settlement_source": "settlement_source",
    "missing_resolution_time": "resolution_time",
    "missing_orderbook": "orderbook",
    "missing_depth": "depth",
    "missing_top_of_book": "top_of_book",
    "missing_quote_timestamp": "quote_timestamp",
    "missing_fee_model": "fee_model_status",
    "source_file_unknown": "source_file",
}

FIELD_CHECKED_PATHS = {
    **{
        field: [path for alias in aliases for path in (f"row.{alias}", f"row.raw.{alias}")]
        for field, aliases in FIELD_ALIASES.items()
    },
    "outcomes": [
        "row.outcomes",
        "row.outcome_list",
        "row.outcome_tokens",
        "row.tokens",
        "row.raw.outcomes",
        "row.raw.outcome_list",
        "row.raw.outcomePrices with explicit labels",
        "row.yes_sub_title/no_sub_title",
    ],
    "orderbook": ["row.orderbook_enrichment", "row.orderbook", "row.research_orderbook", "row.best_ask/best_bid"],
    "depth": [
        "row.depth_at_best_ask/depth_at_best_bid",
        "row.orderbook_enrichment.depth_at_best_ask/depth_at_best_bid",
        "row.orderbook.asks/bids",
        "row.orderbook_enrichment.depth_within_1c/3c/5c",
    ],
    "top_of_book": ["row.best_ask/best_bid", "row.orderbook_enrichment.best_ask/best_bid", "row.orderbook.best_ask/best_bid"],
    "source_file": ["source_file"],
}


def build_venue_metadata_coverage_report(
    *,
    input_dir: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    market_rows: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if input_dir.exists():
        for path in sorted(input_dir.rglob("*.json")):
            payload, warning = _load_json(path)
            if warning is not None:
                warnings.append(warning)
                continue
            extracted = _extract_market_rows(payload, source_file=path)
            if not extracted and _looks_like_snapshot(payload):
                warnings.append(
                    {
                        "source_file": str(path),
                        "reason_code": "snapshot_contains_no_market_rows",
                        "blocker": "saved_snapshot_missing_markets",
                    }
                )
            market_rows.extend(extracted)
    else:
        warnings.append(
            {
                "source_file": str(input_dir),
                "reason_code": "input_dir_missing",
                "blocker": "saved_input_directory_missing",
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "source": "venue_metadata_coverage_audit_v1",
        "generated_at": generated.isoformat(),
        "diagnostic_only": True,
        "input_dir": str(input_dir),
        "summary": _summary(market_rows, warnings),
        "venues": _venue_summaries(market_rows),
        "blocker_drilldown": _blocker_drilldown(market_rows),
        "next_adapter_fixes": _next_adapter_fixes(market_rows),
        "category_breadth": _category_breadth(market_rows),
        "markets": market_rows,
        "warnings": warnings,
        "safety": {
            "saved_files_only": True,
            "live_fetch_attempted": False,
            "execution_or_order_logic_added": False,
            "account_or_auth_logic_added": False,
            "tradability_claimed": False,
            "paper_candidate_emitted": False,
            "affects_evaluator_gates": False,
        },
    }


def write_venue_metadata_coverage_files(
    *,
    input_dir: Path,
    json_output: Path,
    csv_output: Path,
    markdown_output: Path | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_venue_metadata_coverage_report(input_dir=input_dir, generated_at=generated_at)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    csv_output.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(report["markets"], csv_output)
    if markdown_output is not None:
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(render_venue_metadata_coverage_markdown(report), encoding="utf-8")
    return report


def render_venue_metadata_coverage_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Venue Metadata Coverage Audit",
        "",
        "Saved-file-only diagnostic coverage report. Readiness tiers do not claim tradability or paper-candidate status.",
        "",
        "## Venues",
        "",
        "| Venue | Markets | Active | Orderbooks | Rules text | Source URLs | Expected time | Actual time | Source blocked | Match ready | Evaluator ready | Top blockers |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in report.get("venues") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("venue")),
                    _md(row.get("market_count")),
                    _md(row.get("active_market_count")),
                    _md(row.get("markets_with_orderbooks")),
                    _md(row.get("markets_with_rules_text")),
                    _md(row.get("markets_with_explicit_source_url")),
                    _md(row.get("markets_with_resolution_time_expected")),
                    _md(row.get("markets_with_resolution_time_actual")),
                    _md(row.get("markets_blocked_by_settlement_source")),
                    _md(row.get("match_ready_count")),
                    _md(row.get("evaluator_ready_count")),
                    _md(",".join(item["blocker"] for item in row.get("top_blockers") or []) or "none"),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Readiness Tiers", ""])
    lines.extend(
        [
            f"- `{TIER_INGESTED_ONLY}`: minimally parsed saved row; more metadata required.",
            f"- `{TIER_MATCH_CANDIDATE_READY}`: enough identifiers/text/outcomes for review matching, but not settlement-ready.",
            f"- `{TIER_RELATIONSHIP_REVIEW_READY}`: core settlement metadata present for relationship review.",
            f"- `{TIER_EXECUTION_EVALUATION_READY}`: diagnostic row has orderbook/depth/quote timestamp/fee metadata present; not a trade claim.",
            f"- `{TIER_BLOCKED_METADATA_INCOMPLETE}`: missing basic venue/market/source metadata.",
        ]
    )
    lines.extend(["", "## Adapter Gaps", ""])
    for row in report.get("next_adapter_fixes") or []:
        lines.append(
            f"- `{_md(row.get('venue'))}`: aliases={_md(','.join(row.get('high_impact_missing_aliases') or [])) or 'none'}; "
            f"collection={_md(','.join(row.get('true_missing_data_requires_collection') or [])) or 'none'}; "
            f"before evaluator={_md(','.join(row.get('fields_needed_before_evaluator_readiness_can_improve') or [])) or 'none'}"
        )
    lines.extend(["", "## Category Breadth", ""])
    lines.extend(["| Venue | Category/topic | Markets | Match ready | Evaluator ready |", "|---|---|---:|---:|---:|"])
    for row in report.get("category_breadth") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("venue")),
                    _md(row.get("category")),
                    _md(row.get("market_count")),
                    _md(row.get("match_ready_count")),
                    _md(row.get("evaluator_ready_count")),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _extract_market_rows(payload: Any, *, source_file: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in _market_objects(payload):
        if not isinstance(raw, dict):
            continue
        rows.append(_coverage_row(raw, source_file=source_file, payload=payload))
    return rows


def _market_objects(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload if _list_looks_like_markets(payload) else []
    if not isinstance(payload, dict):
        return []
    if payload.get("source") in {
        "venue_metadata_coverage_audit_v1",
        "normalized_market_contract_v0",
        "normalized_market_contract_v0_coverage",
        "settlement_evidence_burden_v1",
        "standardized_family_candidates_v1",
        "relative_value_ops_status_v1",
        "existing_paper_candidate_audit_v1",
        "mlb_world_series_revival_status_v1",
        "stale_report_archive_plan_v1",
    }:
        return []
    normalized = payload.get("normalized_markets")
    if isinstance(normalized, list):
        return normalized
    for key in ("markets", "records"):
        value = payload.get(key)
        if isinstance(value, list) and _list_looks_like_markets(value):
            return value
    return []


def _coverage_row(raw: dict[str, Any], *, source_file: Path, payload: Any) -> dict[str, Any]:
    raw_nested = raw.get("raw") if isinstance(raw.get("raw"), dict) else {}
    contexts = _contexts(raw, payload)
    field_evidence: dict[str, Any] = {}
    venue, field_evidence["venue"] = _field_string(contexts, "venue")
    if venue is None:
        venue = _venue_from_payload(payload)
        if venue is not None:
            field_evidence["venue"] = {"present": True, "path": "payload.source", "value_preview": _preview(venue)}
    event_id, field_evidence["event_id"] = _field_string(contexts, "event_id")
    event_ticker, field_evidence["event_ticker"] = _field_string(contexts, "event_ticker")
    event_slug, field_evidence["event_slug"] = _field_string(contexts, "event_slug")
    market_id, field_evidence["market_id"] = _field_string(contexts, "market_id")
    ticker, field_evidence["ticker"] = _field_string(contexts, "ticker")
    token_id, field_evidence["token_id"] = _field_string(contexts, "token_id")
    title, field_evidence["title"] = _field_string(contexts, "title")
    category, field_evidence["category"] = _field_string(contexts, "category")
    outcomes, field_evidence["outcomes"] = _outcomes(raw, raw_nested)
    settlement_rules, field_evidence["settlement_rules"] = _field_string(contexts, "settlement_rules")
    settlement_source, field_evidence["settlement_source"] = _field_string(contexts, "settlement_source")
    close_time, field_evidence["close_time"] = _field_string(contexts, "close_time")
    resolution_time, field_evidence["resolution_time"] = _field_string(contexts, "resolution_time")
    settlement_metadata = _settlement_metadata(contexts)
    status, field_evidence["status"] = _field_string(contexts, "status")
    if status is None:
        active = _first(raw, raw_nested, "active")
        closed = _first(raw, raw_nested, "closed")
        if active is True:
            status = "active"
            field_evidence["status"] = {"present": True, "path": "row.active", "value_preview": "true"}
        elif closed is True:
            status = "closed"
            field_evidence["status"] = {"present": True, "path": "row.closed", "value_preview": "true"}
    orderbook = raw.get("orderbook_enrichment") if isinstance(raw.get("orderbook_enrichment"), dict) else {}
    orderbook_alt = raw.get("orderbook") if isinstance(raw.get("orderbook"), dict) else {}
    has_orderbook = bool(orderbook or orderbook_alt or raw.get("research_orderbook") or raw.get("best_ask") is not None or raw.get("best_bid") is not None)
    field_evidence["orderbook"] = _presence_evidence(
        has_orderbook,
        _first_present_path(
            (("row", raw),),
            ("orderbook_enrichment", "orderbook", "research_orderbook", "best_ask", "best_bid"),
        ),
    )
    has_top_of_book = _number_or_none(raw.get("best_ask")) is not None or _number_or_none(raw.get("best_bid")) is not None
    top_path = "row.best_ask/best_bid" if has_top_of_book else None
    if not has_top_of_book:
        has_top_of_book = _number_or_none(orderbook.get("best_ask")) is not None or _number_or_none(orderbook.get("best_bid")) is not None
        top_path = "row.orderbook_enrichment.best_ask/best_bid" if has_top_of_book else None
    field_evidence["top_of_book"] = _presence_evidence(has_top_of_book, top_path)
    has_depth, depth_path = _has_depth(raw, orderbook, orderbook_alt)
    field_evidence["depth"] = _presence_evidence(has_depth, depth_path)
    quote_timestamp, field_evidence["quote_timestamp"] = _quote_timestamp(raw, raw_nested, orderbook, orderbook_alt)
    fee_model_status = _fee_model_status(raw, raw_nested, orderbook, venue)
    explicit_fee, fee_evidence = _field_string(contexts, "fee_model_status")
    if explicit_fee:
        field_evidence["fee_model_status"] = fee_evidence
    elif fee_model_status != "missing":
        field_evidence["fee_model_status"] = {"present": True, "path": "venue_default_fee_model", "value_preview": fee_model_status}
    else:
        field_evidence["fee_model_status"] = fee_evidence
    blockers = _blockers(
        venue=venue,
        event_id=event_id or event_ticker or event_slug,
        market_id=market_id or ticker or token_id,
        title=title,
        outcomes=outcomes,
        settlement_rules=settlement_rules,
        settlement_source=settlement_source,
        resolution_time=resolution_time,
        has_orderbook=has_orderbook,
        has_depth=has_depth,
        has_top_of_book=has_top_of_book,
        quote_timestamp=quote_timestamp,
        fee_model_status=fee_model_status,
        source_file=source_file,
    )
    readiness_tier = _readiness_tier(blockers)
    return {
        "venue": venue,
        "event_id": event_id,
        "event_ticker": event_ticker,
        "event_slug": event_slug,
        "market_id": market_id,
        "ticker": ticker,
        "token_id": token_id,
        "title": title,
        "category": category,
        "outcomes": outcomes,
        "outcome_count": len(outcomes),
        "settlement_rules": settlement_rules,
        "settlement_source": settlement_source,
        "settlement_metadata": settlement_metadata,
        "settlement_rules_text": settlement_metadata["settlement_rules_text"],
        "settlement_source_url": settlement_metadata["settlement_source_url"],
        "settlement_source_kind": settlement_metadata["settlement_source_kind"],
        "close_time": close_time,
        "resolution_time": resolution_time,
        "resolution_time_kind": settlement_metadata["resolution_time_kind"],
        "status": status,
        "has_orderbook": has_orderbook,
        "has_depth": has_depth,
        "has_top_of_book": has_top_of_book,
        "quote_timestamp": quote_timestamp,
        "has_quote_timestamp": quote_timestamp is not None,
        "fee_model_status": fee_model_status,
        "field_evidence": field_evidence,
        "unrecognized_field_hints": _unrecognized_field_hints(raw, raw_nested, orderbook, orderbook_alt),
        "readiness_tier": readiness_tier,
        "blockers": blockers,
        "source_file": str(source_file),
        "diagnostic_only": True,
        "tradability_claimed": False,
        "paper_candidate_emitted": False,
    }


def _settlement_metadata(contexts: tuple[tuple[str, dict[str, Any]], ...]) -> dict[str, Any]:
    rules_hits = _collect_alias_hits(contexts, SETTLEMENT_RULE_TEXT_ALIASES)
    advisory_hits = _collect_alias_hits(contexts, SETTLEMENT_ADVISORY_TEXT_ALIASES)
    source_url_hit = _first_url_hit(contexts, SETTLEMENT_SOURCE_URL_ALIASES + SETTLEMENT_SOURCE_TEXT_ALIASES)
    source_text_hits = _collect_alias_hits(contexts, SETTLEMENT_SOURCE_TEXT_ALIASES)
    actual_time = _first_alias_hit(contexts, RESOLUTION_TIME_ACTUAL_ALIASES)
    expected_time = _first_alias_hit(contexts, RESOLUTION_TIME_EXPECTED_ALIASES)
    deadline_time = _first_alias_hit(contexts, RESOLUTION_TIME_DEADLINE_ALIASES)
    unknown_time = _first_alias_hit(contexts, RESOLUTION_TIME_UNKNOWN_ALIASES)
    close_time = _first_alias_hit(contexts, ("close_time", "closeTime"))

    if actual_time is not None:
        resolution_time = actual_time["value"]
        resolution_time_kind = "actual"
        resolution_path = actual_time["path"]
    elif expected_time is not None:
        resolution_time = expected_time["value"]
        resolution_time_kind = "expected"
        resolution_path = expected_time["path"]
    elif deadline_time is not None:
        resolution_time = deadline_time["value"]
        resolution_time_kind = "deadline"
        resolution_path = deadline_time["path"]
    elif unknown_time is not None:
        resolution_time = unknown_time["value"]
        resolution_time_kind = "unknown"
        resolution_path = unknown_time["path"]
    else:
        resolution_time = None
        resolution_time_kind = "unknown"
        resolution_path = None

    settlement_rules_text = _join_unique(hit["value"] for hit in rules_hits)
    settlement_source_url = source_url_hit["url"] if source_url_hit is not None else None
    if settlement_source_url is not None:
        settlement_source_kind = "external_url"
    elif source_text_hits:
        settlement_source_kind = "text_evidence"
    elif rules_hits:
        settlement_source_kind = "rules_text_only"
    elif advisory_hits:
        settlement_source_kind = "description_only"
    else:
        settlement_source_kind = "unknown"

    advisory_only_fields = [
        {
            "path": hit["path"],
            "reason": "description_is_not_settlement_source",
            "value_preview": _preview(hit["value"]),
        }
        for hit in advisory_hits
    ]
    if settlement_source_url is None:
        advisory_only_fields.extend(
            {
                "path": hit["path"],
                "reason": "source_text_without_url",
                "value_preview": _preview(hit["value"]),
            }
            for hit in source_text_hits
        )

    raw_evidence_paths = _unique_strings(
        [
            *(hit["path"] for hit in rules_hits),
            *(hit["path"] for hit in advisory_hits),
            *(hit["path"] for hit in source_text_hits),
            source_url_hit["path"] if source_url_hit is not None else None,
            resolution_path,
            close_time["path"] if close_time is not None else None,
        ]
    )
    blockers = _settlement_metadata_blockers(
        settlement_rules_text=settlement_rules_text,
        settlement_source_url=settlement_source_url,
        settlement_source_kind=settlement_source_kind,
        resolution_time_kind=resolution_time_kind,
        source_text_hits=source_text_hits,
        advisory_hits=advisory_hits,
    )
    return {
        "settlement_rules_text": settlement_rules_text,
        "settlement_source_url": settlement_source_url,
        "settlement_source_kind": settlement_source_kind,
        "resolution_time": resolution_time,
        "resolution_time_kind": resolution_time_kind,
        "close_time": close_time["value"] if close_time is not None else None,
        "raw_evidence_paths": raw_evidence_paths,
        "advisory_only_fields": advisory_only_fields,
        "blockers": blockers,
    }


def _settlement_metadata_blockers(
    *,
    settlement_rules_text: str | None,
    settlement_source_url: str | None,
    settlement_source_kind: str,
    resolution_time_kind: str,
    source_text_hits: list[dict[str, str]],
    advisory_hits: list[dict[str, str]],
) -> list[str]:
    blockers: list[str] = []
    if settlement_source_url is None:
        blockers.append("missing_settlement_source_url")
    if settlement_rules_text and settlement_source_url is None:
        blockers.append("settlement_rules_text_only")
    if not settlement_rules_text and advisory_hits and settlement_source_url is None:
        blockers.append("description_only_not_source")
    if settlement_source_url is None and source_text_hits:
        blockers.append("source_kind_unknown")
    if settlement_source_url is None and not source_text_hits:
        blockers.append("source_evidence_missing")
    if resolution_time_kind == "expected":
        blockers.append("resolution_time_expected_not_actual")
    if settlement_source_kind == "unknown" and "source_kind_unknown" not in blockers:
        blockers.append("source_kind_unknown")
    return blockers


def _settlement_contexts(contexts: tuple[tuple[str, dict[str, Any]], ...]) -> tuple[tuple[str, dict[str, Any]], ...]:
    return tuple((label, mapping) for label, mapping in contexts if label in {"row", "row.raw"})


def _collect_alias_hits(contexts: tuple[tuple[str, dict[str, Any]], ...], aliases: tuple[str, ...]) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for label, mapping in _settlement_contexts(contexts):
        for alias in aliases:
            if alias not in mapping:
                continue
            text = _string_or_none(mapping.get(alias))
            if text is None:
                continue
            path = f"{label}.{alias}"
            if path in seen_paths:
                continue
            seen_paths.add(path)
            hits.append({"path": path, "alias": alias, "value": text})
    return hits


def _first_alias_hit(contexts: tuple[tuple[str, dict[str, Any]], ...], aliases: tuple[str, ...]) -> dict[str, str] | None:
    hits = _collect_alias_hits(contexts, aliases)
    return hits[0] if hits else None


def _first_url_hit(contexts: tuple[tuple[str, dict[str, Any]], ...], aliases: tuple[str, ...]) -> dict[str, str] | None:
    for hit in _collect_alias_hits(contexts, aliases):
        url = _extract_url(hit["value"])
        if url is not None:
            return {**hit, "url": url}
    return None


def _extract_url(value: str) -> str | None:
    match = URL_RE.search(value)
    if match is None:
        return None
    return match.group(0).rstrip(".,;:")


def _join_unique(values: Any) -> str | None:
    parts = _unique_strings(values)
    return "\n\n".join(parts) if parts else None


def _unique_strings(values: Any) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _string_or_none(value)
        if text is None or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def _blockers(
    *,
    venue: str | None,
    event_id: str | None,
    market_id: str | None,
    title: str | None,
    outcomes: list[dict[str, Any]],
    settlement_rules: str | None,
    settlement_source: str | None,
    resolution_time: str | None,
    has_orderbook: bool,
    has_depth: bool,
    has_top_of_book: bool,
    quote_timestamp: str | None,
    fee_model_status: str,
    source_file: Path | None,
) -> list[str]:
    blockers: list[str] = []
    if not venue:
        blockers.append("missing_venue")
    if not event_id:
        blockers.append("missing_event_id")
    if not market_id:
        blockers.append("missing_market_id")
    if not title:
        blockers.append("missing_title")
    if not outcomes:
        blockers.append("missing_outcome_list")
    if not settlement_rules:
        blockers.append("missing_settlement_rules")
    if not settlement_source:
        blockers.append("missing_settlement_source")
    if not resolution_time:
        blockers.append("missing_resolution_time")
    if not has_orderbook:
        blockers.append("missing_orderbook")
    if not has_depth:
        blockers.append("missing_depth")
    if not has_top_of_book:
        blockers.append("missing_top_of_book")
    if not quote_timestamp:
        blockers.append("missing_quote_timestamp")
    if fee_model_status in {"missing", "unknown"}:
        blockers.append("missing_fee_model")
    if source_file is None:
        blockers.append("source_file_unknown")
    return blockers


def _readiness_tier(blockers: list[str]) -> str:
    blocker_set = set(blockers)
    basic = {"missing_venue", "missing_market_id", "missing_title", "source_file_unknown"}
    if blocker_set & basic:
        return TIER_BLOCKED_METADATA_INCOMPLETE
    match_blockers = {"missing_outcome_list"}
    relationship_blockers = {
        "missing_event_id",
        "missing_settlement_rules",
        "missing_settlement_source",
        "missing_resolution_time",
    }
    execution_blockers = {
        "missing_orderbook",
        "missing_depth",
        "missing_top_of_book",
        "missing_quote_timestamp",
        "missing_fee_model",
    }
    if not (blocker_set & (match_blockers | relationship_blockers | execution_blockers)):
        return TIER_EXECUTION_EVALUATION_READY
    if not (blocker_set & (match_blockers | relationship_blockers)):
        return TIER_RELATIONSHIP_REVIEW_READY
    if not (blocker_set & match_blockers):
        return TIER_MATCH_CANDIDATE_READY
    return TIER_INGESTED_ONLY


def _summary(rows: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    unique_keys: set[tuple[str, str]] = set()
    unique_evaluator_keys: set[tuple[str, str]] = set()
    for row in rows:
        venue = row.get("venue")
        market_id = row.get("market_id") or row.get("ticker") or row.get("token_id")
        if not venue or not market_id:
            continue
        key = (str(venue), str(market_id))
        unique_keys.add(key)
        if row.get("readiness_tier") == TIER_EXECUTION_EVALUATION_READY:
            unique_evaluator_keys.add(key)
    return {
        "market_count": len(rows),
        "market_row_count": len(rows),
        "unique_market_count": len(unique_keys),
        "venue_count": len({row.get("venue") for row in rows if row.get("venue")}),
        "match_ready_count": sum(1 for row in rows if row.get("readiness_tier") in {TIER_MATCH_CANDIDATE_READY, TIER_RELATIONSHIP_REVIEW_READY, TIER_EXECUTION_EVALUATION_READY}),
        "evaluator_ready_count": sum(1 for row in rows if row.get("readiness_tier") == TIER_EXECUTION_EVALUATION_READY),
        "unique_evaluator_ready_market_count": len(unique_evaluator_keys),
        "markets_with_rules_text": sum(1 for row in rows if _settlement_field(row, "settlement_rules_text")),
        "markets_with_explicit_source_url": sum(1 for row in rows if _settlement_field(row, "settlement_source_url")),
        "markets_with_resolution_time_expected": sum(
            1 for row in rows if _settlement_field(row, "resolution_time_kind") == "expected"
        ),
        "markets_with_resolution_time_actual": sum(
            1 for row in rows if _settlement_field(row, "resolution_time_kind") == "actual"
        ),
        "markets_blocked_by_settlement_source": sum(1 for row in rows if _settlement_source_blocked(row)),
        "warning_count": len(warnings),
        "top_blockers": _top_blockers(rows),
    }


def _venue_summaries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("venue") or "unknown")].append(row)
    summaries = []
    for venue, venue_rows in sorted(grouped.items()):
        summaries.append(
            {
                "venue": venue,
                "market_count": len(venue_rows),
                "active_market_count": sum(1 for row in venue_rows if str(row.get("status") or "").lower() in {"active", "open"}),
                "markets_with_orderbooks": sum(1 for row in venue_rows if row.get("has_orderbook") is True),
                "markets_with_settlement_rules": sum(1 for row in venue_rows if row.get("settlement_rules")),
                "markets_with_rules_text": sum(1 for row in venue_rows if _settlement_field(row, "settlement_rules_text")),
                "markets_with_explicit_source_url": sum(
                    1 for row in venue_rows if _settlement_field(row, "settlement_source_url")
                ),
                "markets_with_outcomes": sum(1 for row in venue_rows if row.get("outcome_count", 0) > 0),
                "markets_with_resolution_time": sum(1 for row in venue_rows if row.get("resolution_time")),
                "markets_with_resolution_time_expected": sum(
                    1 for row in venue_rows if _settlement_field(row, "resolution_time_kind") == "expected"
                ),
                "markets_with_resolution_time_actual": sum(
                    1 for row in venue_rows if _settlement_field(row, "resolution_time_kind") == "actual"
                ),
                "markets_blocked_by_settlement_source": sum(1 for row in venue_rows if _settlement_source_blocked(row)),
                "match_ready_count": sum(
                    1
                    for row in venue_rows
                    if row.get("readiness_tier") in {TIER_MATCH_CANDIDATE_READY, TIER_RELATIONSHIP_REVIEW_READY, TIER_EXECUTION_EVALUATION_READY}
                ),
                "evaluator_ready_count": sum(1 for row in venue_rows if row.get("readiness_tier") == TIER_EXECUTION_EVALUATION_READY),
                "top_blockers": _top_blockers(venue_rows),
            }
        )
    return summaries


def _blocker_drilldown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for blocker in row.get("blockers") or []:
            grouped[(str(blocker), str(row.get("venue") or "unknown"), str(row.get("source_file") or ""))].append(row)
    drilldown = []
    for (blocker, venue, source_file), affected in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        field = BLOCKER_FIELD_MAP.get(blocker, blocker.removeprefix("missing_"))
        drilldown.append(
            {
                "blocker": blocker,
                "venue": venue,
                "source_file": source_file,
                "affected_market_count": len(affected),
                "example_market_ids": _example_ids(affected),
                "example_raw_field_paths_checked": FIELD_CHECKED_PATHS.get(field, [field]),
                "field_presence_assessment": _field_presence_assessment(field, affected),
            }
        )
    return drilldown


def _next_adapter_fixes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("venue") or "unknown")].append(row)
    fixes = []
    for venue, venue_rows in sorted(grouped.items()):
        blockers = Counter(blocker for row in venue_rows for blocker in row.get("blockers") or [])
        high_impact_aliases = []
        true_missing = []
        fields_needed = []
        for blocker, count in blockers.most_common(12):
            field = BLOCKER_FIELD_MAP.get(blocker, blocker.removeprefix("missing_"))
            assessment = _field_presence_assessment(field, [row for row in venue_rows if blocker in (row.get("blockers") or [])])
            if assessment == "likely_alias_missing":
                high_impact_aliases.append(f"{field}:{count}")
            elif assessment == "likely_absent":
                true_missing.append(f"{field}:{count}")
            else:
                fields_needed.append(f"{field}:{count}")
        execution_fields = [
            f"{BLOCKER_FIELD_MAP.get(blocker, blocker)}:{count}"
            for blocker, count in blockers.most_common()
            if blocker in {"missing_orderbook", "missing_depth", "missing_top_of_book", "missing_quote_timestamp", "missing_fee_model"}
        ]
        fixes.append(
            {
                "venue": venue,
                "high_impact_missing_aliases": high_impact_aliases[:8],
                "true_missing_data_requires_collection": true_missing[:8],
                "fields_needed_before_evaluator_readiness_can_improve": execution_fields[:8] or fields_needed[:8],
            }
        )
    return fixes


def _category_breadth(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        category = str(row.get("category") or "uncategorized")
        grouped[(str(row.get("venue") or "unknown"), category)].append(row)
    output = []
    for (venue, category), category_rows in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1])):
        output.append(
            {
                "venue": venue,
                "category": category,
                "market_count": len(category_rows),
                "match_ready_count": sum(
                    1
                    for row in category_rows
                    if row.get("readiness_tier") in {TIER_MATCH_CANDIDATE_READY, TIER_RELATIONSHIP_REVIEW_READY, TIER_EXECUTION_EVALUATION_READY}
                ),
                "evaluator_ready_count": sum(1 for row in category_rows if row.get("readiness_tier") == TIER_EXECUTION_EVALUATION_READY),
            }
        )
    return output


def _top_blockers(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counter = Counter(blocker for row in rows for blocker in row.get("blockers") or [])
    return [{"blocker": blocker, "count": count} for blocker, count in counter.most_common(10)]


def _settlement_field(row: dict[str, Any], field: str) -> Any:
    metadata = row.get("settlement_metadata") if isinstance(row.get("settlement_metadata"), dict) else {}
    return metadata.get(field)


def _settlement_source_blocked(row: dict[str, Any]) -> bool:
    metadata = row.get("settlement_metadata") if isinstance(row.get("settlement_metadata"), dict) else {}
    blockers = set(metadata.get("blockers") or [])
    return bool(blockers & SETTLEMENT_SOURCE_BLOCKERS)


def _example_ids(rows: list[dict[str, Any]], limit: int = 5) -> list[str]:
    examples = []
    for row in rows:
        value = row.get("market_id") or row.get("ticker") or row.get("token_id") or row.get("title")
        if value is None:
            continue
        text = str(value)
        if text not in examples:
            examples.append(text)
        if len(examples) >= limit:
            break
    return examples


def _field_presence_assessment(field: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "unknown"
    evidence_hits = 0
    alias_hints = 0
    for row in rows[:25]:
        evidence = row.get("field_evidence") if isinstance(row.get("field_evidence"), dict) else {}
        field_evidence = evidence.get(field) if isinstance(evidence.get(field), dict) else {}
        if field_evidence.get("present") is True:
            evidence_hits += 1
        if _row_has_near_alias(row, field):
            alias_hints += 1
    if evidence_hits:
        return "likely_alias_missing"
    if alias_hints:
        return "likely_alias_missing"
    if len(rows) >= 1:
        return "likely_absent"
    return "unknown"


def _write_csv(rows: list[dict[str, Any]], csv_output: Path) -> None:
    with csv_output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "venue": row.get("venue"),
                    "event_id": row.get("event_id"),
                    "event_ticker": row.get("event_ticker"),
                    "event_slug": row.get("event_slug"),
                    "market_id": row.get("market_id"),
                    "ticker": row.get("ticker"),
                    "token_id": row.get("token_id"),
                    "title": row.get("title"),
                    "category": row.get("category"),
                    "outcome_count": row.get("outcome_count"),
                    "has_settlement_rules": bool(row.get("settlement_rules")),
                    "has_settlement_source": bool(row.get("settlement_source")),
                    "settlement_rules_text": _settlement_field(row, "settlement_rules_text"),
                    "settlement_source_url": _settlement_field(row, "settlement_source_url"),
                    "settlement_source_kind": _settlement_field(row, "settlement_source_kind"),
                    "close_time": row.get("close_time"),
                    "resolution_time": row.get("resolution_time"),
                    "resolution_time_kind": _settlement_field(row, "resolution_time_kind"),
                    "status": row.get("status"),
                    "has_orderbook": row.get("has_orderbook"),
                    "has_depth": row.get("has_depth"),
                    "has_top_of_book": row.get("has_top_of_book"),
                    "has_quote_timestamp": row.get("has_quote_timestamp"),
                    "fee_model_status": row.get("fee_model_status"),
                    "readiness_tier": row.get("readiness_tier"),
                    "blockers": ";".join(row.get("blockers") or []),
                    "settlement_metadata_blockers": ";".join(_settlement_field(row, "blockers") or []),
                    "source_file": row.get("source_file"),
                }
            )


def _outcomes(raw: dict[str, Any], raw_nested: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    for key in ("outcomes", "outcome_list", "outcome_tokens", "tokens"):
        value = raw.get(key)
        path = f"row.{key}"
        if value is None:
            value = raw_nested.get(key)
            path = f"row.raw.{key}"
        parsed = _maybe_json(value)
        if isinstance(parsed, list):
            rows = []
            for item in parsed:
                if isinstance(item, dict):
                    name = _string_or_none(item.get("name") or item.get("label") or item.get("outcome") or item.get("token_id"))
                    rows.append({"name": name, "raw": item})
                elif item is not None:
                    rows.append({"name": str(item), "raw": item})
            return rows, _presence_evidence(bool(rows), path if rows else None)
    raw_outcomes = _maybe_json(raw_nested.get("outcomes"))
    raw_prices = _maybe_json(raw_nested.get("outcomePrices"))
    if isinstance(raw_outcomes, list) and raw_outcomes and isinstance(raw_prices, list):
        rows = [{"name": str(item), "raw": item} for item in raw_outcomes if item is not None]
        return rows, _presence_evidence(bool(rows), "row.raw.outcomes + row.raw.outcomePrices" if rows else None)
    yes = _string_or_none(_first(raw, raw_nested, "yes_sub_title"))
    no = _string_or_none(_first(raw, raw_nested, "no_sub_title"))
    if yes or no:
        return [{"name": yes or "Yes"}, {"name": no or "No"}], _presence_evidence(True, "row.yes_sub_title/no_sub_title")
    return [], _presence_evidence(False, None)


def _has_depth(raw: dict[str, Any], orderbook: dict[str, Any], orderbook_alt: dict[str, Any]) -> tuple[bool, str | None]:
    for label, mapping in (("row", raw), ("row.orderbook_enrichment", orderbook), ("row.orderbook", orderbook_alt)):
        for key in ("depth_at_best_ask", "depth_at_best_bid", "ask_depth", "bid_depth", "depth"):
            if _number_or_none(mapping.get(key)) is not None:
                return True, f"{label}.{key}"
        for side in ("asks", "bids", "yes", "no"):
            value = mapping.get(side)
            if isinstance(value, list) and value:
                return True, f"{label}.{side}"
        for key in ("depth_within_1c", "depth_within_3c", "depth_within_5c"):
            value = mapping.get(key)
            if isinstance(value, dict) and any(_number_or_none(item) is not None for item in value.values()):
                return True, f"{label}.{key}"
    return False, None


def _fee_model_status(raw: dict[str, Any], raw_nested: dict[str, Any], orderbook: dict[str, Any], venue: str | None) -> str:
    explicit = _string_or_none(_first_dicts((raw, raw_nested, orderbook), "fee_model_status", "fee_model", "fee_rate", "fee_assumption_status"))
    if explicit:
        return explicit
    normalized_venue = str(venue or "").strip().lower()
    return KNOWN_FEE_MODEL_VENUES.get(normalized_venue, "missing")


def _market_id_like(row: dict[str, Any]) -> bool:
    return any(row.get(key) is not None for key in ("market_id", "id", "ticker", "market_ticker", "conditionId", "token_id", "asset_id"))


def _list_looks_like_markets(rows: list[Any]) -> bool:
    dict_rows = [row for row in rows[:20] if isinstance(row, dict)]
    return bool(dict_rows) and any(_market_id_like(row) or row.get("venue") is not None for row in dict_rows)


def _looks_like_snapshot(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and payload.get("source") not in {
            "venue_metadata_coverage_audit_v1",
            "normalized_market_contract_v0",
            "normalized_market_contract_v0_coverage",
            "settlement_evidence_burden_v1",
            "standardized_family_candidates_v1",
            "cross_platform_opportunity_triage_v1",
            "relative_value_ops_status_v1",
            "existing_paper_candidate_audit_v1",
            "platform_api_expansion_audit_v1",
            "sx_bet_normalized_draft_v1",
            "sx_bet_normalized_draft_coverage_v1",
            "sx_bet_sports_typed_keys_v1",
            "sx_bet_sports_overlap_v1",
            "mlb_world_series_revival_status_v1",
            "stale_report_archive_plan_v1",
        }
        and any(key in payload for key in ("normalized_markets", "markets", "records"))
    )


def _venue_from_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    source = _string_or_none(payload.get("venue"))
    if source:
        return source
    source = _string_or_none(payload.get("source"))
    if source == "kalshi_markets":
        return "kalshi"
    if source == "polymarket_gamma":
        return "polymarket"
    return source


def _load_json(path: Path) -> tuple[Any, dict[str, Any] | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, {"source_file": str(path), "reason_code": "json_file_missing", "blocker": "saved_json_file_missing"}
    except json.JSONDecodeError:
        return None, {"source_file": str(path), "reason_code": "invalid_json", "blocker": "saved_json_invalid"}
    except OSError as exc:
        return None, {"source_file": str(path), "reason_code": "json_read_error", "blocker": f"saved_json_read_error:{type(exc).__name__}"}


def _contexts(raw: dict[str, Any], payload: Any) -> tuple[tuple[str, dict[str, Any]], ...]:
    raw_nested = raw.get("raw") if isinstance(raw.get("raw"), dict) else {}
    orderbook = raw.get("orderbook_enrichment") if isinstance(raw.get("orderbook_enrichment"), dict) else {}
    orderbook_alt = raw.get("orderbook") if isinstance(raw.get("orderbook"), dict) else {}
    contexts: list[tuple[str, dict[str, Any]]] = [("row", raw), ("row.raw", raw_nested), ("row.orderbook_enrichment", orderbook), ("row.orderbook", orderbook_alt)]
    # Only use top-level payload aliases that are not quote/orderbook freshness proxies.
    if isinstance(payload, dict):
        contexts.append(("payload", payload))
    return tuple(contexts)


def _field_string(contexts: tuple[tuple[str, dict[str, Any]], ...], field: str) -> tuple[str | None, dict[str, Any]]:
    aliases = FIELD_ALIASES.get(field, (field,))
    for label, mapping in contexts:
        if not mapping:
            continue
        # Top-level payload timestamps are snapshot provenance, not per-market quote freshness.
        if label == "payload" and field in {"quote_timestamp", "market_id", "event_id", "title", "outcomes"}:
            continue
        for alias in aliases:
            if alias in mapping and mapping.get(alias) is not None:
                text = _string_or_none(mapping.get(alias))
                if text is not None:
                    return text, {"present": True, "path": f"{label}.{alias}", "value_preview": _preview(text)}
    return None, {"present": False, "paths_checked": FIELD_CHECKED_PATHS.get(field, [field])}


def _quote_timestamp(
    raw: dict[str, Any],
    raw_nested: dict[str, Any],
    orderbook: dict[str, Any],
    orderbook_alt: dict[str, Any],
) -> tuple[str | None, dict[str, Any]]:
    for label, mapping in (("row", raw), ("row.raw", raw_nested)):
        for alias in ("quote_timestamp", "quoteTimestamp", "market_data_timestamp", "marketDataTimestamp", "orderbook_timestamp", "orderbookTimestamp", "book_timestamp", "bookTimestamp", "collected_at", "collectedAt", "snapshot_time", "snapshotTime"):
            if alias in mapping and mapping.get(alias) is not None:
                text = _string_or_none(mapping.get(alias))
                if text is not None:
                    return text, {"present": True, "path": f"{label}.{alias}", "value_preview": _preview(text)}
    status = str(orderbook.get("enrichment_status") or "").lower()
    if status == "enriched" or (not status and (_number_or_none(orderbook.get("best_ask")) is not None or _number_or_none(orderbook.get("best_bid")) is not None)):
        text = _string_or_none(orderbook.get("orderbook_captured_at") or orderbook.get("orderbookCapturedAt"))
        if text is not None:
            return text, {"present": True, "path": "row.orderbook_enrichment.orderbook_captured_at", "value_preview": _preview(text)}
    text = _string_or_none(orderbook_alt.get("orderbook_captured_at") or orderbook_alt.get("orderbookCapturedAt"))
    if text is not None:
        return text, {"present": True, "path": "row.orderbook.orderbook_captured_at", "value_preview": _preview(text)}
    return None, {"present": False, "paths_checked": FIELD_CHECKED_PATHS.get("quote_timestamp", ["quote_timestamp"])}


def _presence_evidence(present: bool, path: str | None) -> dict[str, Any]:
    if present:
        return {"present": True, "path": path}
    return {"present": False}


def _first_present_path(contexts: tuple[tuple[str, dict[str, Any]], ...], keys: tuple[str, ...]) -> str | None:
    for label, mapping in contexts:
        for key in keys:
            if key in mapping and mapping.get(key) is not None:
                return f"{label}.{key}"
    return None


def _row_has_near_alias(row: dict[str, Any], field: str) -> bool:
    hints = row.get("unrecognized_field_hints") if isinstance(row.get("unrecognized_field_hints"), dict) else {}
    return bool(hints.get(field))


def _unrecognized_field_hints(*mappings: dict[str, Any]) -> dict[str, list[str]]:
    known_aliases = {alias for aliases in FIELD_ALIASES.values() for alias in aliases}
    hints: dict[str, list[str]] = defaultdict(list)
    # quote_timestamp near-alias terms must only point to *quote/orderbook* freshness fields;
    # record-update timestamps ("updated", "last_update") are intentionally excluded to avoid
    # operators re-adding them as quote_timestamp aliases (fake-edge risk).
    terms_by_field = {
        "settlement_source": ("settlement_source", "settlementsource", "settlement_basis", "resolution_source", "source_url", "rules_url"),
        "quote_timestamp": ("orderbook_timestamp", "book_timestamp", "collected_at", "snapshot_time", "captured_at"),
        "depth": ("depth", "asks", "bids"),
        "outcomes": ("outcome", "token"),
        "resolution_time": ("expiration", "resolution_time", "resolutiondate", "settlement_time", "enddate"),
    }
    for mapping in mappings:
        for key in mapping.keys():
            if key in known_aliases:
                continue
            normalized = str(key).replace("-", "_").lower()
            for field, terms in terms_by_field.items():
                if any(term in normalized for term in terms):
                    hints[field].append(str(key))
    return {field: sorted(set(values))[:8] for field, values in hints.items()}


def _first(primary: dict[str, Any], secondary: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in primary and primary.get(key) is not None:
            return primary.get(key)
        if key in secondary and secondary.get(key) is not None:
            return secondary.get(key)
    return None


def _first_dicts(mappings: tuple[dict[str, Any], ...], *keys: str) -> Any:
    for mapping in mappings:
        for key in keys:
            if key in mapping and mapping.get(key) is not None:
                return mapping.get(key)
    return None


def _maybe_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return []
    if stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _preview(value: Any, limit: int = 120) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
