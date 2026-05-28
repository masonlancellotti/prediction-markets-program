from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.venue_identity import (
    executable_venue_identity_from_mapping,
    ibkr_prediction_market_row_blockers,
)


SCHEMA_VERSION = 0
REPORT_SOURCE = "normalized_market_contract_v0"
COVERAGE_SOURCE = "normalized_market_contract_v0_coverage"

SKIPPED_SOURCES = {
    REPORT_SOURCE,
    COVERAGE_SOURCE,
    "venue_metadata_coverage_audit_v1",
    "settlement_evidence_burden_v1",
    "cross_platform_opportunity_triage_v1",
    "standardized_family_candidates_v1",
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

CONSERVATIVE_VENUE_FEE_DEFAULTS = {
    "kalshi": "KalshiTieredFeeModel",
    "polymarket": "PolymarketConservativeFeeModel",
}

CSV_FIELDS = [
    "venue",
    "event_id",
    "event_ticker",
    "event_slug",
    "market_id",
    "ticker",
    "token_id",
    "state",
    "accepting_orders",
    "outcome_count",
    "settlement_source_kind",
    "settlement_source_url",
    "resolution_time",
    "resolution_time_kind",
    "quote_captured_at",
    "fee_model_status",
    "fully_identity_ready",
    "settlement_metadata_ready",
    "quote_depth_ready",
    "fee_metadata_ready",
    "evaluator_metadata_ready",
    "blockers",
    "source_file",
]

URL_RE = re.compile(r"https?://[^\s<>\"')]+")


@dataclass(frozen=True)
class NormalizedOutcome:
    name: str | None = None
    token_id: str | None = None
    price: float | None = None
    raw_evidence_paths: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "token_id": self.token_id,
            "price": self.price,
            "raw_evidence_paths": list(self.raw_evidence_paths),
        }


@dataclass(frozen=True)
class NormalizedSettlementMetadata:
    settlement_rules_text: str | None = None
    settlement_source_url: str | None = None
    settlement_source_kind: str = "unknown"
    resolution_time: str | None = None
    resolution_time_kind: str = "unknown"
    close_time: str | None = None
    raw_evidence_paths: tuple[str, ...] = ()
    advisory_only_fields: tuple[dict[str, Any], ...] = ()
    blockers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "settlement_rules_text": self.settlement_rules_text,
            "settlement_source_url": self.settlement_source_url,
            "settlement_source_kind": self.settlement_source_kind,
            "resolution_time": self.resolution_time,
            "resolution_time_kind": self.resolution_time_kind,
            "close_time": self.close_time,
            "raw_evidence_paths": list(self.raw_evidence_paths),
            "advisory_only_fields": list(self.advisory_only_fields),
            "blockers": list(self.blockers),
        }


@dataclass(frozen=True)
class NormalizedQuoteDepth:
    best_yes_bid_price: float | None = None
    best_yes_bid_size: float | None = None
    best_yes_ask_price: float | None = None
    best_yes_ask_size: float | None = None
    best_no_bid_price: float | None = None
    best_no_bid_size: float | None = None
    best_no_ask_price: float | None = None
    best_no_ask_size: float | None = None
    depth_within_1c: dict[str, float | None] = field(default_factory=dict)
    depth_within_3c: dict[str, float | None] = field(default_factory=dict)
    depth_within_5c: dict[str, float | None] = field(default_factory=dict)
    captured_at: str | None = None
    source_endpoint: str | None = None
    raw_evidence_paths: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "best_yes_bid_price": self.best_yes_bid_price,
            "best_yes_bid_size": self.best_yes_bid_size,
            "best_yes_ask_price": self.best_yes_ask_price,
            "best_yes_ask_size": self.best_yes_ask_size,
            "best_no_bid_price": self.best_no_bid_price,
            "best_no_bid_size": self.best_no_bid_size,
            "best_no_ask_price": self.best_no_ask_price,
            "best_no_ask_size": self.best_no_ask_size,
            "depth_within_1c": self.depth_within_1c,
            "depth_within_3c": self.depth_within_3c,
            "depth_within_5c": self.depth_within_5c,
            "captured_at": self.captured_at,
            "source_endpoint": self.source_endpoint,
            "raw_evidence_paths": list(self.raw_evidence_paths),
            "blockers": list(self.blockers),
        }


@dataclass(frozen=True)
class NormalizedFeeMetadata:
    fee_model_status: str = "missing"
    fee_model_name: str | None = None
    fee_rate: float | None = None
    source: str | None = None
    source_kind: str = "unknown"
    review_status: str = "missing"
    raw_evidence_paths: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "fee_model_status": self.fee_model_status,
            "fee_model_name": self.fee_model_name,
            "fee_rate": self.fee_rate,
            "source": self.source,
            "source_kind": self.source_kind,
            "review_status": self.review_status,
            "raw_evidence_paths": list(self.raw_evidence_paths),
            "blockers": list(self.blockers),
        }


@dataclass(frozen=True)
class NormalizedMarket:
    venue: str | None
    source_platform: str | None = None
    access_platform: str | None = None
    exchange_venue: str | None = None
    executable_venue: str | None = None
    event_id: str | None = None
    event_ticker: str | None = None
    event_slug: str | None = None
    market_id: str | None = None
    ticker: str | None = None
    token_id: str | None = None
    title: str | None = None
    outcomes: tuple[NormalizedOutcome, ...] = ()
    settlement: NormalizedSettlementMetadata = field(default_factory=NormalizedSettlementMetadata)
    state: str | None = None
    accepting_orders: bool | None = None
    quote_depth: NormalizedQuoteDepth = field(default_factory=NormalizedQuoteDepth)
    fee_metadata: NormalizedFeeMetadata = field(default_factory=NormalizedFeeMetadata)
    source_file: str | None = None
    source_payload: str | None = None
    row_index: int | None = None
    field_evidence: dict[str, Any] = field(default_factory=dict)
    readiness: dict[str, bool] = field(default_factory=dict)
    blockers: tuple[str, ...] = ()
    diagnostic_only: bool = True
    tradability_claimed: bool = False
    settlement_equivalence_asserted: bool = False
    exact_payoff_asserted: bool = False
    evaluator_integration_enabled: bool = False
    paper_candidate_emitted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "venue": self.venue,
            "source_platform": self.source_platform,
            "access_platform": self.access_platform,
            "exchange_venue": self.exchange_venue,
            "executable_venue": self.executable_venue,
            "event_id": self.event_id,
            "event_ticker": self.event_ticker,
            "event_slug": self.event_slug,
            "market_id": self.market_id,
            "ticker": self.ticker,
            "token_id": self.token_id,
            "title": self.title,
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
            "settlement": self.settlement.to_dict(),
            "state": self.state,
            "accepting_orders": self.accepting_orders,
            "quote_depth": self.quote_depth.to_dict(),
            "fee_metadata": self.fee_metadata.to_dict(),
            "source_file": self.source_file,
            "source_payload": self.source_payload,
            "row_index": self.row_index,
            "field_evidence": self.field_evidence,
            "readiness": self.readiness,
            "blockers": list(self.blockers),
            "diagnostic_only": self.diagnostic_only,
            "tradability_claimed": self.tradability_claimed,
            "settlement_equivalence_asserted": self.settlement_equivalence_asserted,
            "exact_payoff_asserted": self.exact_payoff_asserted,
            "evaluator_integration_enabled": self.evaluator_integration_enabled,
            "paper_candidate_emitted": self.paper_candidate_emitted,
        }


def build_normalized_markets_v0_report(
    *,
    input_dir: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    records: list[NormalizedMarket] = []
    warnings: list[dict[str, Any]] = []

    if input_dir.exists():
        for path in sorted(input_dir.rglob("*.json")):
            payload, warning = _load_json(path)
            if warning is not None:
                warnings.append(warning)
                continue
            if _skip_payload(payload):
                continue
            rows = _market_objects(payload)
            if not rows and _looks_like_snapshot(payload):
                warnings.append(
                    {
                        "source_file": str(path),
                        "reason_code": "snapshot_contains_no_market_rows",
                        "blocker": "saved_snapshot_missing_markets",
                    }
                )
            for index, raw in enumerate(rows):
                if isinstance(raw, dict):
                    records.append(_normalize_market(raw, payload=payload, source_file=path, row_index=index))
    else:
        warnings.append(
            {
                "source_file": str(input_dir),
                "reason_code": "input_dir_missing",
                "blocker": "saved_input_directory_missing",
            }
        )

    market_dicts = [record.to_dict() for record in records]
    return {
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "input_dir": str(input_dir),
        "normalized_markets": market_dicts,
        "coverage": _coverage(market_dicts, warnings),
        "warnings": warnings,
        "safety": _safety_block(),
    }


def build_normalized_markets_v0_coverage(report: dict[str, Any]) -> dict[str, Any]:
    rows = report.get("normalized_markets") if isinstance(report.get("normalized_markets"), list) else []
    warnings = report.get("warnings") if isinstance(report.get("warnings"), list) else []
    return {
        "schema_version": SCHEMA_VERSION,
        "source": COVERAGE_SOURCE,
        "generated_at": report.get("generated_at"),
        "input_dir": report.get("input_dir"),
        **_coverage(rows, warnings),
        "warnings": warnings,
        "safety": _safety_block(),
    }


def write_normalized_markets_v0_files(
    *,
    input_dir: Path,
    json_output: Path,
    coverage_output: Path,
    csv_output: Path | None = None,
    markdown_output: Path | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_normalized_markets_v0_report(input_dir=input_dir, generated_at=generated_at)
    coverage = build_normalized_markets_v0_coverage(report)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    coverage_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    coverage_output.write_text(json.dumps(coverage, indent=2, sort_keys=True), encoding="utf-8")
    if csv_output is not None:
        csv_output.parent.mkdir(parents=True, exist_ok=True)
        _write_csv(report["normalized_markets"], csv_output)
    if markdown_output is not None:
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(render_normalized_markets_v0_markdown(coverage), encoding="utf-8")
    return {"report": report, "coverage": coverage}


def render_normalized_markets_v0_markdown(coverage: dict[str, Any]) -> str:
    lines = [
        "# Normalized Markets v0 Coverage",
        "",
        "Saved-file-only contract coverage. This supplements the venue metadata coverage audit and does not feed evaluator logic.",
        "",
        "## Venues",
        "",
        "| Venue | Normalized | Identity | Settlement | Quote/depth | Fee | Evaluator metadata | Top blockers |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in coverage.get("venues") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("venue")),
                    _md(row.get("normalized_count")),
                    _md(row.get("fully_identity_ready")),
                    _md(row.get("settlement_metadata_ready")),
                    _md(row.get("quote_depth_ready")),
                    _md(row.get("fee_metadata_ready")),
                    _md(row.get("evaluator_metadata_ready")),
                    _md(",".join(item["blocker"] for item in row.get("top_blockers") or []) or "none"),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _normalize_market(raw: dict[str, Any], *, payload: Any, source_file: Path, row_index: int) -> NormalizedMarket:
    contexts = _contexts(raw, payload)
    field_evidence: dict[str, Any] = {}
    venue, field_evidence["venue"] = _field_string(contexts, ("venue",))
    if venue is None:
        venue = _venue_from_payload(payload)
        if venue is not None:
            field_evidence["venue"] = {"present": True, "path": "payload.source", "value_preview": _preview(venue)}
    source_platform, field_evidence["source_platform"] = _field_string(contexts, ("source_platform",))
    access_platform, field_evidence["access_platform"] = _field_string(contexts, ("access_platform",))
    exchange_venue, field_evidence["exchange_venue"] = _field_string(contexts, ("exchange_venue",))
    executable_venue, field_evidence["executable_venue"] = _field_string(contexts, ("executable_venue",))
    executable_venue = executable_venue or executable_venue_identity_from_mapping(raw)
    event_id, field_evidence["event_id"] = _field_string(
        contexts, ("event_id", "eventId", "event_ticker", "eventTicker", "event_slug", "eventSlug")
    )
    event_ticker, field_evidence["event_ticker"] = _field_string(contexts, ("event_ticker", "eventTicker", "series_ticker"))
    event_slug, field_evidence["event_slug"] = _field_string(contexts, ("event_slug", "eventSlug", "slug"))
    market_id, field_evidence["market_id"] = _field_string(
        contexts,
        (
            "market_id",
            "marketId",
            "id",
            "ticker",
            "market_ticker",
            "condition_id",
            "conditionId",
            "token_id",
            "tokenId",
            "asset_id",
            "clobTokenId",
        ),
    )
    ticker, field_evidence["ticker"] = _field_string(contexts, ("ticker", "market_ticker"))
    token_id, token_evidence = _token_id(contexts)
    field_evidence["token_id"] = token_evidence
    title, field_evidence["title"] = _field_string(contexts, ("title", "question", "market_title", "name"))
    outcomes, outcome_evidence = _outcomes(raw)
    field_evidence["outcomes"] = outcome_evidence
    settlement = _settlement_metadata(contexts, outcomes)
    state, field_evidence["state"] = _state(raw)
    accepting_orders, field_evidence["accepting_orders"] = _accepting_orders(contexts)
    quote_depth = _quote_depth(raw)
    fee_metadata = _fee_metadata(raw, venue)
    readiness = _readiness(
        venue=venue,
        event_identity=event_id or event_ticker or event_slug,
        market_identity=market_id or ticker or token_id,
        outcomes=outcomes,
        settlement=settlement,
        quote_depth=quote_depth,
        fee_metadata=fee_metadata,
    )
    blockers = _unique_strings(
        [
            *_identity_blockers(venue, event_id or event_ticker or event_slug, market_id or ticker or token_id),
            *settlement.blockers,
            *quote_depth.blockers,
            *fee_metadata.blockers,
            *ibkr_prediction_market_row_blockers(raw),
        ]
    )
    return NormalizedMarket(
        venue=venue,
        source_platform=source_platform,
        access_platform=access_platform,
        exchange_venue=exchange_venue,
        executable_venue=executable_venue,
        event_id=event_id,
        event_ticker=event_ticker,
        event_slug=event_slug,
        market_id=market_id,
        ticker=ticker,
        token_id=token_id,
        title=title,
        outcomes=tuple(outcomes),
        settlement=settlement,
        state=state,
        accepting_orders=accepting_orders,
        quote_depth=quote_depth,
        fee_metadata=fee_metadata,
        source_file=str(source_file),
        source_payload=_string_or_none(payload.get("source")) if isinstance(payload, dict) else None,
        row_index=row_index,
        field_evidence=field_evidence,
        readiness=readiness,
        blockers=tuple(blockers),
    )


def _readiness(
    *,
    venue: str | None,
    event_identity: str | None,
    market_identity: str | None,
    outcomes: list[NormalizedOutcome],
    settlement: NormalizedSettlementMetadata,
    quote_depth: NormalizedQuoteDepth,
    fee_metadata: NormalizedFeeMetadata,
) -> dict[str, bool]:
    fully_identity_ready = bool(venue and event_identity and market_identity)
    settlement_metadata_ready = bool(
        outcomes and settlement.settlement_rules_text and settlement.settlement_source_url and settlement.resolution_time
    )
    quote_depth_ready = not quote_depth.blockers
    fee_metadata_ready = not fee_metadata.blockers
    evaluator_metadata_ready = (
        fully_identity_ready and settlement_metadata_ready and quote_depth_ready and fee_metadata_ready
    )
    return {
        "fully_identity_ready": fully_identity_ready,
        "settlement_metadata_ready": settlement_metadata_ready,
        "quote_depth_ready": quote_depth_ready,
        "fee_metadata_ready": fee_metadata_ready,
        "evaluator_metadata_ready": evaluator_metadata_ready,
    }


def _identity_blockers(venue: str | None, event_identity: str | None, market_identity: str | None) -> list[str]:
    blockers = []
    if not venue:
        blockers.append("missing_venue")
    if not event_identity:
        blockers.append("missing_event_identity")
    if not market_identity:
        blockers.append("missing_market_identity")
    return blockers


def _settlement_metadata(
    contexts: tuple[tuple[str, dict[str, Any]], ...], outcomes: list[NormalizedOutcome]
) -> NormalizedSettlementMetadata:
    rules_hits = _collect_alias_hits(
        contexts,
        ("settlement_rules", "rules", "rules_primary", "rules_secondary", "resolution_text", "resolution_criteria"),
    )
    advisory_hits = _collect_alias_hits(contexts, ("description", "descriptionHtml", "description_html"))
    source_url_hit = _first_url_hit(
        contexts,
        (
            "settlement_source_url",
            "settlementSourceUrl",
            "resolution_source_url",
            "resolutionSourceUrl",
            "source_url",
            "sourceUrl",
            "rules_url",
            "rulesUrl",
            "resolutionSource",
            "resolution_source",
            "settlement_source",
            "settlementSource",
            "settlement_source_raw_evidence",
        ),
    )
    source_text_hits = _collect_alias_hits(
        contexts,
        (
            "settlement_source",
            "settlementSource",
            "settlement_basis",
            "settlementBasis",
            "resolution_source",
            "resolutionSource",
            "settlement_source_raw_evidence",
        ),
    )
    actual_time = _first_alias_hit(
        contexts,
        (
            "actual_resolution_time",
            "actualResolutionTime",
            "actual_settlement_time",
            "actualSettlementTime",
            "resolved_at",
            "resolvedAt",
            "settled_at",
            "settledAt",
        ),
    )
    expected_time = _first_alias_hit(contexts, ("expected_expiration_time", "expectedExpirationTime"))
    deadline_time = _first_alias_hit(
        contexts,
        (
            "end_date",
            "endDate",
            "endDateIso",
            "expiration_time",
            "expirationTime",
            "latest_expiration_time",
            "latestExpirationTime",
            "resolution_date",
            "resolutionDate",
        ),
    )
    unknown_time = _first_alias_hit(contexts, ("resolution_time", "resolutionTime", "settlement_time", "settlementTime"))
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

    rules_text = _join_unique(hit["value"] for hit in rules_hits)
    source_url = source_url_hit["url"] if source_url_hit else None
    if source_url:
        source_kind = "external_url"
    elif source_text_hits:
        source_kind = "text_evidence"
    elif rules_hits:
        source_kind = "rules_text_only"
    elif advisory_hits:
        source_kind = "description_only"
    else:
        source_kind = "unknown"

    advisory_only_fields = [
        {"path": hit["path"], "reason": "description_is_not_settlement_source", "value_preview": _preview(hit["value"])}
        for hit in advisory_hits
    ]
    if not source_url:
        advisory_only_fields.extend(
            {"path": hit["path"], "reason": "source_text_without_url", "value_preview": _preview(hit["value"])}
            for hit in source_text_hits
        )

    blockers: list[str] = []
    if not outcomes:
        blockers.append("missing_outcomes")
    if not rules_text:
        blockers.append("missing_settlement_rules_text")
    if not source_url:
        blockers.append("missing_settlement_source_url")
    if not resolution_time:
        blockers.append("missing_resolution_time")
    if rules_text and not source_url:
        blockers.append("settlement_rules_text_only")
    if advisory_hits and not rules_text and not source_url:
        blockers.append("description_only_not_source")
    if source_text_hits and not source_url:
        blockers.append("source_kind_unknown")
    if not source_url and not source_text_hits:
        blockers.append("source_evidence_missing")
    if resolution_time_kind == "expected":
        blockers.append("resolution_time_expected_not_actual")

    return NormalizedSettlementMetadata(
        settlement_rules_text=rules_text,
        settlement_source_url=source_url,
        settlement_source_kind=source_kind,
        resolution_time=resolution_time,
        resolution_time_kind=resolution_time_kind,
        close_time=close_time["value"] if close_time else None,
        raw_evidence_paths=tuple(
            _unique_strings(
                [
                    *(hit["path"] for hit in rules_hits),
                    *(hit["path"] for hit in advisory_hits),
                    *(hit["path"] for hit in source_text_hits),
                    source_url_hit["path"] if source_url_hit else None,
                    resolution_path,
                    close_time["path"] if close_time else None,
                ]
            )
        ),
        advisory_only_fields=tuple(advisory_only_fields),
        blockers=tuple(_unique_strings(blockers)),
    )


def _quote_depth(raw: dict[str, Any]) -> NormalizedQuoteDepth:
    raw_nested = raw.get("raw") if isinstance(raw.get("raw"), dict) else {}
    enrichment = raw.get("orderbook_enrichment") if isinstance(raw.get("orderbook_enrichment"), dict) else {}
    orderbook = raw.get("orderbook") if isinstance(raw.get("orderbook"), dict) else {}
    contexts = (("row.orderbook_enrichment", enrichment), ("row.orderbook", orderbook), ("row", raw), ("row.raw", raw_nested))
    yes_bid, yes_bid_path = _first_number(contexts, ("best_bid", "yes_bid", "yesBid", "yes_bid_dollars", "yesBidDollars"))
    yes_ask, yes_ask_path = _first_number(contexts, ("best_ask", "yes_ask", "yesAsk", "yes_ask_dollars", "yesAskDollars"))
    yes_bid_size, yes_bid_size_path = _first_number(
        contexts, ("depth_at_best_bid", "yes_bid_size", "yesBidSize", "yes_bid_size_fp", "yesBidSizeFp")
    )
    yes_ask_size, yes_ask_size_path = _first_number(
        contexts, ("depth_at_best_ask", "yes_ask_size", "yesAskSize", "yes_ask_size_fp", "yesAskSizeFp")
    )
    no_bid, no_bid_path = _first_number(contexts, ("best_no_bid", "no_bid", "noBid", "no_bid_dollars", "noBidDollars"))
    no_ask, no_ask_path = _first_number(contexts, ("best_no_ask", "no_ask", "noAsk", "no_ask_dollars", "noAskDollars"))
    no_bid_size, no_bid_size_path = _first_number(contexts, ("no_bid_size", "noBidSize", "no_bid_size_fp", "noBidSizeFp"))
    no_ask_size, no_ask_size_path = _first_number(contexts, ("no_ask_size", "noAskSize", "no_ask_size_fp", "noAskSizeFp"))
    captured_at, captured_path = _quote_captured_at(raw, raw_nested, enrichment, orderbook)
    source_endpoint, source_path = _field_string((("row.orderbook_enrichment", enrichment), ("row.orderbook", orderbook)), ("source_endpoint", "sourceEndpoint"))
    blockers: list[str] = []
    if yes_bid is None:
        blockers.append("missing_best_yes_bid_price")
    if yes_ask is None:
        blockers.append("missing_best_yes_ask_price")
    if yes_bid_size is None:
        blockers.append("missing_best_yes_bid_size")
    if yes_ask_size is None:
        blockers.append("missing_best_yes_ask_size")
    if not captured_at:
        blockers.append("missing_quote_captured_at")
    return NormalizedQuoteDepth(
        best_yes_bid_price=yes_bid,
        best_yes_bid_size=yes_bid_size,
        best_yes_ask_price=yes_ask,
        best_yes_ask_size=yes_ask_size,
        best_no_bid_price=no_bid,
        best_no_bid_size=no_bid_size,
        best_no_ask_price=no_ask,
        best_no_ask_size=no_ask_size,
        depth_within_1c=_band_depth(enrichment.get("depth_within_1c")),
        depth_within_3c=_band_depth(enrichment.get("depth_within_3c")),
        depth_within_5c=_band_depth(enrichment.get("depth_within_5c")),
        captured_at=captured_at,
        source_endpoint=source_endpoint,
        raw_evidence_paths=tuple(
            _unique_strings(
                [
                    yes_bid_path,
                    yes_ask_path,
                    yes_bid_size_path,
                    yes_ask_size_path,
                    no_bid_path,
                    no_ask_path,
                    no_bid_size_path,
                    no_ask_size_path,
                    captured_path,
                    source_path,
                ]
            )
        ),
        blockers=tuple(blockers),
    )


def _fee_metadata(raw: dict[str, Any], venue: str | None) -> NormalizedFeeMetadata:
    raw_nested = raw.get("raw") if isinstance(raw.get("raw"), dict) else {}
    enrichment = raw.get("orderbook_enrichment") if isinstance(raw.get("orderbook_enrichment"), dict) else {}
    embedded = raw.get("fee_metadata") if isinstance(raw.get("fee_metadata"), dict) else {}
    contexts = (
        ("row.fee_metadata", embedded),
        ("row", raw),
        ("row.raw", raw_nested),
        ("row.orderbook_enrichment", enrichment),
    )
    status, status_evidence = _field_string(
        contexts,
        ("fee_model_status", "feeModelStatus", "fee_model", "feeModel", "fee_assumption_status", "feeAssumptionStatus"),
    )
    model, model_evidence = _field_string(contexts, ("fee_model_name", "feeModelName", "fee_model", "feeModel"))
    source, source_evidence = _field_string(contexts, ("source", "fee_source", "feeSource"))
    source_kind, source_kind_evidence = _field_string(contexts, ("source_kind", "sourceKind", "fee_source_kind", "feeSourceKind"))
    review_status, review_evidence = _field_string(
        contexts, ("review_status", "reviewStatus", "fee_review_status", "feeReviewStatus")
    )
    fee_rate, fee_rate_path = _first_number(contexts, ("fee_rate", "feeRate", "makerBaseFee", "takerBaseFee"))
    paths = [status_evidence.get("path"), model_evidence.get("path"), source_evidence.get("path"), source_kind_evidence.get("path"), review_evidence.get("path"), fee_rate_path]
    normalized_venue = str(venue or "").strip().lower()
    if not status and normalized_venue in CONSERVATIVE_VENUE_FEE_DEFAULTS:
        return NormalizedFeeMetadata(
            fee_model_status="conservative_venue_default",
            fee_model_name=CONSERVATIVE_VENUE_FEE_DEFAULTS[normalized_venue],
            source="repo_conservative_venue_default",
            source_kind="conservative_default",
            review_status="conservative_default",
            raw_evidence_paths=("repo.conservative_venue_default_fee_model",),
            blockers=(),
        )

    blockers: list[str] = []
    if not status:
        blockers.append("missing_fee_model_status")
    if not model:
        blockers.append("missing_fee_model_name")
    if not source:
        blockers.append("missing_fee_source")
    if review_status not in {"reviewed", "explicit_reviewed", "conservative_default", "known_default_fee_model"}:
        blockers.append("missing_fee_reviewed_source")

    return NormalizedFeeMetadata(
        fee_model_status=status or "missing",
        fee_model_name=model,
        fee_rate=fee_rate,
        source=source,
        source_kind=source_kind or "unknown",
        review_status=review_status or "missing",
        raw_evidence_paths=tuple(_unique_strings(paths)),
        blockers=tuple(blockers),
    )


def _coverage(rows: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "summary": {
            "normalized_count": len(rows),
            "venue_count": len({row.get("venue") for row in rows if row.get("venue")}),
            "fully_identity_ready": _readiness_count(rows, "fully_identity_ready"),
            "settlement_metadata_ready": _readiness_count(rows, "settlement_metadata_ready"),
            "quote_depth_ready": _readiness_count(rows, "quote_depth_ready"),
            "fee_metadata_ready": _readiness_count(rows, "fee_metadata_ready"),
            "evaluator_metadata_ready": _readiness_count(rows, "evaluator_metadata_ready"),
            "warning_count": len(warnings),
            "top_blockers": _top_blockers(rows),
        },
        "venues": _venue_coverage(rows),
    }


def _venue_coverage(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("venue") or "unknown")].append(row)
    output = []
    for venue, venue_rows in sorted(grouped.items()):
        output.append(
            {
                "venue": venue,
                "normalized_count": len(venue_rows),
                "fully_identity_ready": _readiness_count(venue_rows, "fully_identity_ready"),
                "settlement_metadata_ready": _readiness_count(venue_rows, "settlement_metadata_ready"),
                "quote_depth_ready": _readiness_count(venue_rows, "quote_depth_ready"),
                "fee_metadata_ready": _readiness_count(venue_rows, "fee_metadata_ready"),
                "evaluator_metadata_ready": _readiness_count(venue_rows, "evaluator_metadata_ready"),
                "top_blockers": _top_blockers(venue_rows),
            }
        )
    return output


def _readiness_count(rows: list[dict[str, Any]], key: str) -> int:
    return sum(1 for row in rows if (row.get("readiness") or {}).get(key) is True)


def _top_blockers(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counter = Counter(blocker for row in rows for blocker in row.get("blockers") or [])
    return [{"blocker": blocker, "count": count} for blocker, count in counter.most_common(10)]


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            readiness = row.get("readiness") or {}
            settlement = row.get("settlement") or {}
            quote = row.get("quote_depth") or {}
            fee = row.get("fee_metadata") or {}
            writer.writerow(
                {
                    "venue": row.get("venue"),
                    "event_id": row.get("event_id"),
                    "event_ticker": row.get("event_ticker"),
                    "event_slug": row.get("event_slug"),
                    "market_id": row.get("market_id"),
                    "ticker": row.get("ticker"),
                    "token_id": row.get("token_id"),
                    "state": row.get("state"),
                    "accepting_orders": row.get("accepting_orders"),
                    "outcome_count": len(row.get("outcomes") or []),
                    "settlement_source_kind": settlement.get("settlement_source_kind"),
                    "settlement_source_url": settlement.get("settlement_source_url"),
                    "resolution_time": settlement.get("resolution_time"),
                    "resolution_time_kind": settlement.get("resolution_time_kind"),
                    "quote_captured_at": quote.get("captured_at"),
                    "fee_model_status": fee.get("fee_model_status"),
                    "fully_identity_ready": readiness.get("fully_identity_ready"),
                    "settlement_metadata_ready": readiness.get("settlement_metadata_ready"),
                    "quote_depth_ready": readiness.get("quote_depth_ready"),
                    "fee_metadata_ready": readiness.get("fee_metadata_ready"),
                    "evaluator_metadata_ready": readiness.get("evaluator_metadata_ready"),
                    "blockers": ";".join(row.get("blockers") or []),
                    "source_file": row.get("source_file"),
                }
            )


def _market_objects(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload if _list_looks_like_markets(payload) else []
    if not isinstance(payload, dict):
        return []
    normalized = payload.get("normalized_markets")
    if isinstance(normalized, list) and _list_looks_like_markets(normalized):
        return normalized
    for key in ("markets", "records"):
        value = payload.get(key)
        if isinstance(value, list) and _list_looks_like_markets(value):
            return value
    return []


def _list_looks_like_markets(rows: list[Any]) -> bool:
    dict_rows = [row for row in rows[:20] if isinstance(row, dict)]
    return bool(dict_rows) and any(
        row.get("venue") is not None
        or any(row.get(key) is not None for key in ("market_id", "id", "ticker", "market_ticker", "conditionId", "token_id", "asset_id"))
        for row in dict_rows
    )


def _looks_like_snapshot(payload: Any) -> bool:
    return isinstance(payload, dict) and any(key in payload for key in ("normalized_markets", "markets", "records"))


def _skip_payload(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("source") in SKIPPED_SOURCES


def _contexts(raw: dict[str, Any], payload: Any) -> tuple[tuple[str, dict[str, Any]], ...]:
    raw_nested = raw.get("raw") if isinstance(raw.get("raw"), dict) else {}
    enrichment = raw.get("orderbook_enrichment") if isinstance(raw.get("orderbook_enrichment"), dict) else {}
    orderbook = raw.get("orderbook") if isinstance(raw.get("orderbook"), dict) else {}
    contexts: list[tuple[str, dict[str, Any]]] = [("row", raw), ("row.raw", raw_nested), ("row.orderbook_enrichment", enrichment), ("row.orderbook", orderbook)]
    if isinstance(payload, dict):
        contexts.append(("payload", payload))
    return tuple(contexts)


def _field_string(contexts: tuple[tuple[str, dict[str, Any]], ...], aliases: tuple[str, ...]) -> tuple[str | None, dict[str, Any]]:
    for label, mapping in contexts:
        if not mapping:
            continue
        for alias in aliases:
            if alias in mapping and mapping.get(alias) is not None:
                text = _string_or_none(mapping.get(alias))
                if text is not None:
                    return text, {"present": True, "path": f"{label}.{alias}", "value_preview": _preview(text)}
    return None, {"present": False, "paths_checked": [f"row.{alias}" for alias in aliases]}


def _collect_alias_hits(contexts: tuple[tuple[str, dict[str, Any]], ...], aliases: tuple[str, ...]) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    seen: set[str] = set()
    for label, mapping in contexts:
        if label == "payload":
            continue
        for alias in aliases:
            if alias not in mapping:
                continue
            text = _string_or_none(mapping.get(alias))
            if text is None:
                continue
            path = f"{label}.{alias}"
            if path in seen:
                continue
            seen.add(path)
            hits.append({"path": path, "alias": alias, "value": text})
    return hits


def _first_alias_hit(contexts: tuple[tuple[str, dict[str, Any]], ...], aliases: tuple[str, ...]) -> dict[str, str] | None:
    hits = _collect_alias_hits(contexts, aliases)
    return hits[0] if hits else None


def _first_url_hit(contexts: tuple[tuple[str, dict[str, Any]], ...], aliases: tuple[str, ...]) -> dict[str, str] | None:
    for hit in _collect_alias_hits(contexts, aliases):
        url = _extract_url(hit["value"])
        if url:
            return {**hit, "url": url}
    return None


def _outcomes(raw: dict[str, Any]) -> tuple[list[NormalizedOutcome], dict[str, Any]]:
    raw_nested = raw.get("raw") if isinstance(raw.get("raw"), dict) else {}
    token_ids = _maybe_json(_first_value((("row", raw), ("row.raw", raw_nested)), ("clobTokenIds", "clob_token_ids", "tokenIds")))
    if not isinstance(token_ids, list):
        token_ids = []
    for label, mapping in (("row", raw), ("row.raw", raw_nested)):
        for key in ("outcomes", "outcome_list", "outcome_tokens", "tokens"):
            parsed = _maybe_json(mapping.get(key))
            if isinstance(parsed, list):
                prices = _maybe_json(mapping.get("outcomePrices"))
                rows = []
                for index, item in enumerate(parsed):
                    price = _number_or_none(prices[index]) if isinstance(prices, list) and index < len(prices) else None
                    token_id = _string_or_none(token_ids[index]) if index < len(token_ids) else None
                    if isinstance(item, dict):
                        name = _string_or_none(item.get("name") or item.get("label") or item.get("outcome") or item.get("token_id"))
                        token_id = _string_or_none(item.get("token_id") or item.get("tokenId") or item.get("asset_id")) or token_id
                        price = _number_or_none(item.get("outcome_yes_token_price") or item.get("price")) if price is None else price
                    else:
                        name = _string_or_none(item)
                    rows.append(
                        NormalizedOutcome(
                            name=name,
                            token_id=token_id,
                            price=price,
                            raw_evidence_paths=tuple(_unique_strings([f"{label}.{key}", f"{label}.outcomePrices" if price is not None else None])),
                        )
                    )
                return rows, {"present": bool(rows), "path": f"{label}.{key}" if rows else None}
    yes = _string_or_none(raw.get("yes_sub_title") or raw_nested.get("yes_sub_title"))
    no = _string_or_none(raw.get("no_sub_title") or raw_nested.get("no_sub_title"))
    if yes or no:
        return (
            [
                NormalizedOutcome(name=yes or "Yes", raw_evidence_paths=("row.yes_sub_title/no_sub_title",)),
                NormalizedOutcome(name=no or "No", raw_evidence_paths=("row.yes_sub_title/no_sub_title",)),
            ],
            {"present": True, "path": "row.yes_sub_title/no_sub_title"},
        )
    return [], {"present": False, "paths_checked": ["row.outcomes", "row.raw.outcomes", "row.yes_sub_title/no_sub_title"]}


def _state(raw: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    raw_nested = raw.get("raw") if isinstance(raw.get("raw"), dict) else {}
    state = _string_or_none(raw.get("state") or raw.get("status") or raw_nested.get("state") or raw_nested.get("status"))
    if state:
        return state, {"present": True, "path": "row.state/status", "value_preview": _preview(state)}
    active = raw.get("active", raw_nested.get("active"))
    closed = raw.get("closed", raw_nested.get("closed"))
    if closed is True:
        return "closed", {"present": True, "path": "row.closed", "value_preview": "true"}
    if active is True:
        return "active", {"present": True, "path": "row.active", "value_preview": "true"}
    return None, {"present": False, "paths_checked": ["row.state", "row.status", "row.active", "row.closed"]}


def _accepting_orders(contexts: tuple[tuple[str, dict[str, Any]], ...]) -> tuple[bool | None, dict[str, Any]]:
    for label, mapping in contexts:
        for key in ("accepting_orders", "acceptingOrders"):
            if key in mapping and isinstance(mapping.get(key), bool):
                return mapping.get(key), {"present": True, "path": f"{label}.{key}", "value_preview": str(mapping.get(key)).lower()}
    return None, {"present": False, "paths_checked": ["row.accepting_orders", "row.raw.acceptingOrders"]}


def _token_id(contexts: tuple[tuple[str, dict[str, Any]], ...]) -> tuple[str | None, dict[str, Any]]:
    value, evidence = _field_string(contexts, ("token_id", "tokenId", "asset_id", "clobTokenId"))
    if value:
        return value, evidence
    for label, mapping in contexts:
        parsed = _maybe_json(mapping.get("clobTokenIds") or mapping.get("clob_token_ids"))
        if isinstance(parsed, list) and parsed:
            text = _string_or_none(parsed[0])
            if text:
                return text, {"present": True, "path": f"{label}.clobTokenIds[0]", "value_preview": _preview(text)}
    return None, {"present": False, "paths_checked": ["row.token_id", "row.raw.clobTokenIds[0]"]}


def _quote_captured_at(
    raw: dict[str, Any], raw_nested: dict[str, Any], enrichment: dict[str, Any], orderbook: dict[str, Any]
) -> tuple[str | None, str | None]:
    for label, mapping in (("row.orderbook_enrichment", enrichment), ("row.orderbook", orderbook), ("row", raw), ("row.raw", raw_nested)):
        for key in (
            "orderbook_captured_at",
            "orderbookCapturedAt",
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
        ):
            text = _string_or_none(mapping.get(key))
            if text:
                return text, f"{label}.{key}"
    return None, None


def _first_number(contexts: tuple[tuple[str, dict[str, Any]], ...], aliases: tuple[str, ...]) -> tuple[float | None, str | None]:
    for label, mapping in contexts:
        if not mapping:
            continue
        for alias in aliases:
            if alias in mapping:
                number = _number_or_none(mapping.get(alias))
                if number is not None:
                    return number, f"{label}.{alias}"
    return None, None


def _band_depth(value: Any) -> dict[str, float | None]:
    if not isinstance(value, dict):
        return {}
    return {str(key): _number_or_none(item) for key, item in value.items()}


def _first_value(contexts: tuple[tuple[str, dict[str, Any]], ...], aliases: tuple[str, ...]) -> Any:
    for _, mapping in contexts:
        for alias in aliases:
            if alias in mapping and mapping.get(alias) is not None:
                return mapping.get(alias)
    return None


def _venue_from_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    venue = _string_or_none(payload.get("venue"))
    if venue:
        return venue
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


def _extract_url(value: str) -> str | None:
    match = URL_RE.search(value)
    if not match:
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


def _safety_block() -> dict[str, Any]:
    return {
        "saved_files_only": True,
        "live_fetch_attempted": False,
        "execution_or_order_logic_added": False,
        "account_or_auth_logic_added": False,
        "tradability_claimed": False,
        "settlement_equivalence_asserted": False,
        "exact_payoff_asserted": False,
        "paper_candidate_emitted": False,
        "feeds_evaluator_by_default": False,
    }


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
