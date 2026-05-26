from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.exhaustive_evidence_trust import has_reference_only_flag


NORMALIZER_SOURCE = "kalshi_event_metadata_normalized_v1"
EVENT_METADATA_AUDIT_SOURCE = "kalshi_event_metadata_audit_v1"
EVENT_METADATA_JOIN_SOURCE = "kalshi_event_metadata_join_v1"
KALSHI_EVENT_METADATA_EVIDENCE_SOURCE = "kalshi_event_metadata"

EVENT_METADATA_BLOCKERS = frozenset(
    {
        "missing_event_ticker",
        "missing_event_id",
        "missing_event_outcome_list",
        "per_market_binary_outcomes_only_at_event_level",
        "event_not_marked_complete",
        "outcome_count_lt_two",
        "title_only_event",
        "reference_only_source",
        "missing_rules_evidence",
        "missing_settlement_source_evidence",
        "outcome_count_vs_market_count_mismatch",
        "mixed_market_rules",
        "mixed_market_times",
        "duplicate_outcome_list_entries",
        "duplicate_market_tickers",
    }
)


@dataclass(frozen=True)
class NormalizedKalshiEventMetadata:
    event_ticker: str | None
    event_id: str | None
    series_ticker: str | None
    title: str | None
    outcome_list: list[str] | None
    complete: bool
    is_exhaustive: bool
    all_outcomes_included: bool
    market_tickers: list[str]
    rules_primary: str | None
    rules_secondary: str | None
    settlement_source_raw_evidence: str | None
    close_time: str | None
    expected_expiration_time: str | None
    expiration_time: str | None
    latest_expiration_time: str | None
    reference_only: bool
    blockers: list[str]
    source: str = NORMALIZER_SOURCE
    source_path: str | None = None
    per_market_binary_outcomes_seen: list[list[str]] = field(default_factory=list)

    def is_trusted_for_completeness(self) -> bool:
        return (
            not self.blockers
            and not self.reference_only
            and self.complete is True
            and self.outcome_list is not None
            and len(self.outcome_list) >= 2
            and bool(self.event_ticker or self.event_id)
        )

    def to_diagnostic(self) -> dict[str, Any]:
        return {
            "event_ticker": self.event_ticker,
            "event_id": self.event_id,
            "series_ticker": self.series_ticker,
            "title": self.title,
            "outcome_list": list(self.outcome_list or []),
            "complete": self.complete,
            "is_exhaustive": self.is_exhaustive,
            "all_outcomes_included": self.all_outcomes_included,
            "market_tickers": list(self.market_tickers),
            "rules_primary": self.rules_primary,
            "rules_secondary": self.rules_secondary,
            "settlement_source_raw_evidence": self.settlement_source_raw_evidence,
            "close_time": self.close_time,
            "expected_expiration_time": self.expected_expiration_time,
            "expiration_time": self.expiration_time,
            "latest_expiration_time": self.latest_expiration_time,
            "reference_only": self.reference_only,
            "blockers": list(self.blockers),
            "source": self.source,
            "source_path": self.source_path,
            "per_market_binary_outcomes_seen": [list(values) for values in self.per_market_binary_outcomes_seen],
            "is_trusted_for_completeness": self.is_trusted_for_completeness(),
        }

    def to_event_block(self) -> dict[str, Any]:
        """Build a snapshot 'events' entry from this normalized metadata.

        Even if the event is not trusted, we still emit the markers we discovered
        so the downstream audit can see them — but trusted booleans are only set
        to True when ``is_trusted_for_completeness`` holds. This means an
        untrusted/blocked event metadata file cannot fabricate completeness in
        the enriched snapshot.
        """
        trusted = self.is_trusted_for_completeness()
        block: dict[str, Any] = {
            "event_ticker": self.event_ticker,
            "event_id": self.event_id,
            "series_ticker": self.series_ticker,
            "title": self.title,
            "rules_primary": self.rules_primary,
            "rules_secondary": self.rules_secondary,
            "settlement_source": self.settlement_source_raw_evidence,
            "settlement_source_raw_evidence": self.settlement_source_raw_evidence,
            "close_time": self.close_time,
            "expected_expiration_time": self.expected_expiration_time,
            "expiration_time": self.expiration_time,
            "latest_expiration_time": self.latest_expiration_time,
            "event_metadata_source": KALSHI_EVENT_METADATA_EVIDENCE_SOURCE,
            "event_metadata_blockers": list(self.blockers),
            "event_metadata_trusted_for_completeness": trusted,
        }
        if trusted:
            block["outcome_list"] = list(self.outcome_list or [])
            block["all_outcomes_included"] = True
            block["complete"] = True
            block["is_exhaustive"] = True
        if self.reference_only:
            block["reference_only"] = True
        return block


def normalize_kalshi_event_metadata_payload(
    payload: dict[str, Any],
    *,
    source_path: str | None = None,
) -> list[NormalizedKalshiEventMetadata]:
    events = _iter_event_objects(payload)
    return [_normalize_event(event, source_path=source_path) for event in events]


def audit_kalshi_event_metadata(
    metadata_payloads: list[dict[str, Any]],
    *,
    generated_at: datetime | None = None,
    source_paths: list[str] | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    paths = source_paths or [None] * len(metadata_payloads)
    if len(paths) != len(metadata_payloads):
        paths = list(paths) + [None] * (len(metadata_payloads) - len(paths))
    normalized: list[NormalizedKalshiEventMetadata] = []
    for payload, source_path in zip(metadata_payloads, paths):
        if isinstance(payload, dict):
            normalized.extend(normalize_kalshi_event_metadata_payload(payload, source_path=source_path))
    blocker_counts: Counter[str] = Counter()
    for event in normalized:
        for blocker in event.blockers:
            blocker_counts[blocker] += 1
    trusted_count = sum(1 for event in normalized if event.is_trusted_for_completeness())
    return {
        "schema_version": 1,
        "source": EVENT_METADATA_AUDIT_SOURCE,
        "generated_at": generated.isoformat(),
        "summary": {
            "metadata_files": len(metadata_payloads),
            "events_discovered": len(normalized),
            "events_trusted_for_completeness": trusted_count,
            "events_blocked": sum(1 for event in normalized if event.blockers),
            "events_reference_only": sum(1 for event in normalized if event.reference_only),
            "blocker_counts": dict(sorted(blocker_counts.items())),
            "stop_for_review_count": 0,
            "paper_candidate_count": 0,
        },
        "events": [event.to_diagnostic() for event in normalized],
        "safety": {
            "saved_file_only": True,
            "diagnostic_only": True,
            "live_fetch_attempted": False,
            "places_orders": False,
            "paper_candidate_emitted": False,
            "stop_for_review_emitted": False,
            "uses_title_similarity_for_exhaustiveness": False,
            "uses_graph_hints_for_exhaustiveness": False,
            "uses_count_only_evidence": False,
            "affects_evaluator_gates": False,
            "allowed_evidence_source": KALSHI_EVENT_METADATA_EVIDENCE_SOURCE,
            "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        },
    }


def audit_kalshi_event_metadata_files(
    *,
    metadata_paths: list[Path],
    json_output: Path,
    markdown_output: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    payloads = []
    paths = []
    for path in metadata_paths:
        payloads.append(json.loads(Path(path).read_text(encoding="utf-8")))
        paths.append(str(path))
    report = audit_kalshi_event_metadata(payloads, generated_at=generated_at, source_paths=paths)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_kalshi_event_metadata_audit_markdown(report), encoding="utf-8")
    return report


def render_kalshi_event_metadata_audit_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Kalshi Event Metadata Audit",
        "",
        "Saved-file-only diagnostic. No live API calls. Completeness is detected only from explicit "
        "event-level outcome_list/outcomes plus complete/is_exhaustive/all_outcomes_included markers.",
        "",
        f"- metadata_files: {summary.get('metadata_files', 0)}",
        f"- events_discovered: {summary.get('events_discovered', 0)}",
        f"- events_trusted_for_completeness: {summary.get('events_trusted_for_completeness', 0)}",
        f"- events_blocked: {summary.get('events_blocked', 0)}",
        f"- events_reference_only: {summary.get('events_reference_only', 0)}",
        f"- stop_for_review_count: {summary.get('stop_for_review_count', 0)}",
        f"- paper_candidate_count: {summary.get('paper_candidate_count', 0)}",
        "",
        "| Event ticker | Event id | Outcomes | Complete | Markets | Trusted | Top blockers |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for event in report.get("events", []) or []:
        blockers = "; ".join((event.get("blockers") or [])[:4]).replace("|", "/")
        lines.append(
            "| {ticker} | {event_id} | {outcomes} | {complete} | {markets} | {trusted} | {blockers} |".format(
                ticker=str(event.get("event_ticker") or "").replace("|", "/"),
                event_id=str(event.get("event_id") or "").replace("|", "/"),
                outcomes=len(event.get("outcome_list") or []),
                complete=str(bool(event.get("complete"))).lower(),
                markets=len(event.get("market_tickers") or []),
                trusted=str(bool(event.get("is_trusted_for_completeness"))).lower(),
                blockers=blockers,
            )
        )
    return "\n".join(lines) + "\n"


def join_kalshi_event_metadata(
    *,
    snapshot_payload: dict[str, Any],
    metadata_payloads: list[dict[str, Any]],
    generated_at: datetime | None = None,
    snapshot_path: str | None = None,
    source_paths: list[str] | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    paths = source_paths or [None] * len(metadata_payloads)
    if len(paths) != len(metadata_payloads):
        paths = list(paths) + [None] * (len(metadata_payloads) - len(paths))
    normalized: list[NormalizedKalshiEventMetadata] = []
    for payload, source_path in zip(metadata_payloads, paths):
        if isinstance(payload, dict):
            normalized.extend(normalize_kalshi_event_metadata_payload(payload, source_path=source_path))
    snapshot_tickers_by_event = _snapshot_event_market_tickers(snapshot_payload)
    snapshot_event_keys = set(snapshot_tickers_by_event.keys())
    snapshot_event_lookup = _snapshot_event_lookup(snapshot_payload)
    original_events = [dict(event) for event in (snapshot_payload.get("events") or []) if isinstance(event, dict)]
    event_index_by_key: dict[str, int] = {}
    for index, event in enumerate(original_events):
        for key in ("event_ticker", "event_id", "id", "venue_native_event_id"):
            value = _string_or_none(event.get(key))
            if value and value not in event_index_by_key:
                event_index_by_key[value] = index
    enriched_events: list[dict[str, Any]] = list(original_events)
    join_rows: list[dict[str, Any]] = []
    enriched_normalized_market_rows: list[dict[str, Any]] = []
    trusted_join_count = 0
    for event in normalized:
        keys = [key for key in (event.event_ticker, event.event_id) if key]
        snapshot_markets: list[str] = []
        matched_key: str | None = None
        for key in keys:
            if key in snapshot_tickers_by_event:
                snapshot_markets = list(snapshot_tickers_by_event[key])
                matched_key = key
                break
        matched_to_snapshot = matched_key is not None
        manifest_tickers = list(event.market_tickers)
        snapshot_set = set(snapshot_markets)
        manifest_set = set(manifest_tickers)
        missing_in_snapshot = sorted(manifest_set - snapshot_set)
        extra_in_snapshot = sorted(snapshot_set - manifest_set)
        snapshot_event = snapshot_event_lookup.get(matched_key) if matched_key else None
        snapshot_event_markets = []
        if isinstance(snapshot_event, dict):
            raw_markets = snapshot_event.get("markets")
            if isinstance(raw_markets, list):
                snapshot_event_markets = [m for m in raw_markets if isinstance(m, dict)]
        snapshot_reference_only_markets = _snapshot_reference_only_market_tickers(
            snapshot_event_markets,
            manifest_set,
        )
        snapshot_reference_only = (
            has_reference_only_flag(snapshot_event)
            or bool(snapshot_reference_only_markets)
        )
        block = event.to_event_block()
        join_blockers = list(event.blockers)
        if not matched_to_snapshot:
            join_blockers.append("no_matching_event_in_snapshot")
        if matched_to_snapshot and manifest_tickers and missing_in_snapshot:
            join_blockers.append("manifest_market_tickers_absent_from_snapshot")
        if matched_to_snapshot and manifest_tickers and extra_in_snapshot:
            join_blockers.append("snapshot_has_markets_outside_metadata")
        if matched_to_snapshot and snapshot_reference_only:
            join_blockers.append("snapshot_reference_only_source")
        join_blockers = sorted(set(join_blockers))
        joined_trusted = (
            event.is_trusted_for_completeness()
            and matched_to_snapshot
            and not missing_in_snapshot
            and not extra_in_snapshot
            and not snapshot_reference_only
        )
        if not joined_trusted:
            # Strip any completeness markers that could fabricate exhaustiveness when
            # the join itself failed (snapshot mismatch, reference-only, etc.).
            for stripped in ("outcome_list", "all_outcomes_included", "complete", "is_exhaustive"):
                block.pop(stripped, None)
            block["event_metadata_trusted_for_completeness"] = False
        if matched_to_snapshot:
            block["matched_snapshot_event_key"] = matched_key
        # Merge into the matched existing snapshot event when possible so the
        # downstream audit attributes completeness to the same markets. The
        # original event's markets list is preserved; we only add metadata
        # fields that are missing AND only when the join is trusted.
        target_index: int | None = None
        for key in keys:
            if key in event_index_by_key:
                target_index = event_index_by_key[key]
                break
        if target_index is not None:
            target = enriched_events[target_index]
            for field_name in (
                "series_ticker",
                "title",
                "rules_primary",
                "rules_secondary",
                "settlement_source",
                "settlement_source_raw_evidence",
                "close_time",
                "expected_expiration_time",
                "expiration_time",
                "latest_expiration_time",
            ):
                if field_name in block and not target.get(field_name):
                    target[field_name] = block[field_name]
            target["event_metadata_source"] = block.get("event_metadata_source")
            target["event_metadata_blockers"] = block.get("event_metadata_blockers")
            target["event_metadata_trusted_for_completeness"] = block.get(
                "event_metadata_trusted_for_completeness"
            )
            if "matched_snapshot_event_key" in block:
                target["matched_snapshot_event_key"] = block["matched_snapshot_event_key"]
            if joined_trusted:
                target["outcome_list"] = list(event.outcome_list or [])
                target["all_outcomes_included"] = True
                target["complete"] = True
                target["is_exhaustive"] = True
            if event.reference_only or snapshot_reference_only:
                target["reference_only"] = True
        else:
            if snapshot_reference_only:
                block["reference_only"] = True
            enriched_events.append(block)
        if joined_trusted:
            trusted_join_count += 1
            enriched_normalized_market_rows.extend(
                _build_enriched_normalized_markets(
                    trusted_event=event,
                    snapshot_event_markets=snapshot_event_markets,
                    matched_event_key=matched_key,
                )
            )
        join_rows.append(
            {
                "event_ticker": event.event_ticker,
                "event_id": event.event_id,
                "matched_to_snapshot": matched_to_snapshot,
                "matched_snapshot_event_key": matched_key,
                "snapshot_market_count": len(snapshot_markets),
                "metadata_market_count": len(manifest_tickers),
                "missing_in_snapshot": missing_in_snapshot,
                "extra_in_snapshot": extra_in_snapshot,
                "metadata_blockers": list(event.blockers),
                "join_blockers": join_blockers,
                "trusted_for_completeness_after_join": joined_trusted,
                "reference_only": event.reference_only,
                "snapshot_reference_only": snapshot_reference_only,
                "snapshot_reference_only_markets": snapshot_reference_only_markets,
                "metadata_outcome_count": len(event.outcome_list or []),
                "metadata_source_path": event.source_path,
            }
        )
    enriched_snapshot: dict[str, Any] = dict(snapshot_payload)
    enriched_snapshot["events"] = enriched_events
    # Build the normalized_markets list that detect-structural-baskets consumes
    # directly. We preserve any pre-existing normalized_markets in the snapshot
    # and only ADD trusted-join rows (deduped by market_ticker against existing
    # entries). This is the only place trusted venue-native exhaustive_group
    # evidence is materialized; untrusted joins never reach this list.
    existing_normalized: list[dict[str, Any]] = []
    seen_normalized_tickers: set[str] = set()
    raw_existing = snapshot_payload.get("normalized_markets")
    if isinstance(raw_existing, list):
        for market in raw_existing:
            if isinstance(market, dict):
                existing_normalized.append(market)
                ticker = _string_or_none(market.get("market_ticker") or market.get("ticker"))
                if ticker:
                    seen_normalized_tickers.add(ticker)
    new_trusted_rows: list[dict[str, Any]] = []
    for row in enriched_normalized_market_rows:
        ticker = _string_or_none(row.get("market_ticker"))
        if ticker and ticker in seen_normalized_tickers:
            continue
        if ticker:
            seen_normalized_tickers.add(ticker)
        new_trusted_rows.append(row)
    enriched_snapshot["normalized_markets"] = existing_normalized + new_trusted_rows
    enriched_snapshot.setdefault("event_metadata_join", {})["join_source"] = EVENT_METADATA_JOIN_SOURCE
    enriched_snapshot["event_metadata_join"]["generated_at"] = generated.isoformat()
    enriched_snapshot["event_metadata_join"]["snapshot_path"] = snapshot_path
    enriched_snapshot["event_metadata_join"]["trusted_join_count"] = trusted_join_count
    enriched_snapshot["event_metadata_join"]["enriched_normalized_market_row_count"] = len(new_trusted_rows)
    enriched_snapshot["event_metadata_join"]["safety"] = {
        "saved_file_only": True,
        "places_orders": False,
        "paper_candidate_emitted": False,
        "stop_for_review_emitted": False,
        "affects_evaluator_gates": False,
        "uses_title_similarity_for_exhaustiveness": False,
        "uses_graph_hints_for_exhaustiveness": False,
        "uses_count_only_evidence": False,
    }
    report = {
        "schema_version": 1,
        "source": EVENT_METADATA_JOIN_SOURCE,
        "generated_at": generated.isoformat(),
        "summary": {
            "metadata_files": len(metadata_payloads),
            "events_discovered": len(normalized),
            "events_matched_to_snapshot": sum(1 for row in join_rows if row["matched_to_snapshot"]),
            "events_trusted_after_join": sum(1 for row in join_rows if row["trusted_for_completeness_after_join"]),
            "events_blocked_after_join": sum(1 for row in join_rows if row["join_blockers"]),
            "snapshot_event_keys": sorted(snapshot_event_keys),
            "enriched_normalized_market_row_count": len(new_trusted_rows),
            "stop_for_review_count": 0,
            "paper_candidate_count": 0,
        },
        "rows": join_rows,
        "safety": {
            "saved_file_only": True,
            "diagnostic_only": True,
            "live_fetch_attempted": False,
            "places_orders": False,
            "paper_candidate_emitted": False,
            "stop_for_review_emitted": False,
            "uses_title_similarity_for_exhaustiveness": False,
            "uses_graph_hints_for_exhaustiveness": False,
            "uses_count_only_evidence": False,
            "affects_evaluator_gates": False,
            "allowed_evidence_source": KALSHI_EVENT_METADATA_EVIDENCE_SOURCE,
            "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        },
    }
    return {
        "enriched_snapshot": enriched_snapshot,
        "report": report,
        "normalized_events": [event.to_diagnostic() for event in normalized],
    }


def join_kalshi_event_metadata_files(
    *,
    snapshot_path: Path,
    metadata_paths: list[Path],
    json_output: Path,
    markdown_output: Path,
    enriched_snapshot_output: Path | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    snapshot_payload = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    metadata_payloads = []
    string_paths = []
    for path in metadata_paths:
        metadata_payloads.append(json.loads(Path(path).read_text(encoding="utf-8")))
        string_paths.append(str(path))
    result = join_kalshi_event_metadata(
        snapshot_payload=snapshot_payload,
        metadata_payloads=metadata_payloads,
        generated_at=generated_at,
        snapshot_path=str(snapshot_path),
        source_paths=string_paths,
    )
    report = result["report"]
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_kalshi_event_metadata_join_markdown(report), encoding="utf-8")
    if enriched_snapshot_output is not None:
        enriched_snapshot_output.parent.mkdir(parents=True, exist_ok=True)
        enriched_snapshot_output.write_text(
            json.dumps(result["enriched_snapshot"], indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return result


def render_kalshi_event_metadata_join_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Kalshi Event Metadata Join Report",
        "",
        "Saved-file-only diagnostic. Trust requires (1) normalized event metadata passing all blockers,",
        "(2) a matching event in the snapshot, and (3) market_tickers consistent between the two.",
        "",
        f"- metadata_files: {summary.get('metadata_files', 0)}",
        f"- events_discovered: {summary.get('events_discovered', 0)}",
        f"- events_matched_to_snapshot: {summary.get('events_matched_to_snapshot', 0)}",
        f"- events_trusted_after_join: {summary.get('events_trusted_after_join', 0)}",
        f"- events_blocked_after_join: {summary.get('events_blocked_after_join', 0)}",
        f"- enriched_normalized_market_row_count: {summary.get('enriched_normalized_market_row_count', 0)}",
        f"- stop_for_review_count: {summary.get('stop_for_review_count', 0)}",
        f"- paper_candidate_count: {summary.get('paper_candidate_count', 0)}",
        "",
        "| Event ticker | Matched | Snapshot markets | Metadata markets | Missing in snapshot | Extra in snapshot | Trusted | Top blockers |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in report.get("rows", []) or []:
        blockers = "; ".join((row.get("join_blockers") or [])[:4]).replace("|", "/")
        lines.append(
            "| {ticker} | {matched} | {snap} | {meta} | {missing} | {extra} | {trusted} | {blockers} |".format(
                ticker=str(row.get("event_ticker") or "").replace("|", "/"),
                matched=str(bool(row.get("matched_to_snapshot"))).lower(),
                snap=row.get("snapshot_market_count") or 0,
                meta=row.get("metadata_market_count") or 0,
                missing=len(row.get("missing_in_snapshot") or []),
                extra=len(row.get("extra_in_snapshot") or []),
                trusted=str(bool(row.get("trusted_for_completeness_after_join"))).lower(),
                blockers=blockers,
            )
        )
    return "\n".join(lines) + "\n"


def _iter_event_objects(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    direct_events = payload.get("events")
    if isinstance(direct_events, list):
        return [event for event in direct_events if isinstance(event, dict)]
    if isinstance(payload.get("event"), dict):
        return [payload["event"]]
    # Treat the payload itself as an event if it carries event-like keys.
    if any(key in payload for key in ("event_ticker", "event_id", "series_ticker", "markets")):
        return [payload]
    return []


def _normalize_event(event: dict[str, Any], *, source_path: str | None = None) -> NormalizedKalshiEventMetadata:
    blockers: list[str] = []
    event_ticker = _string_or_none(
        event.get("event_ticker") or event.get("ticker") or event.get("venue_native_event_ticker")
    )
    event_id = _string_or_none(event.get("event_id") or event.get("id") or event.get("venue_native_event_id"))
    series_ticker = _string_or_none(event.get("series_ticker") or event.get("series"))
    title = _string_or_none(event.get("title") or event.get("name") or event.get("event_title"))
    rules_primary = _string_or_none(event.get("rules_primary") or event.get("rules") or event.get("resolution_text"))
    rules_secondary = _string_or_none(event.get("rules_secondary") or event.get("settlement_rules"))
    settlement_source = _string_or_none(
        event.get("settlement_source_raw_evidence")
        or event.get("settlement_source_evidence")
        or event.get("settlement_source")
        or event.get("resolution_source")
        or event.get("settlement_basis")
    )
    close_time = _string_or_none(event.get("close_time"))
    expected_expiration_time = _string_or_none(event.get("expected_expiration_time"))
    expiration_time = _string_or_none(event.get("expiration_time"))
    latest_expiration_time = _string_or_none(event.get("latest_expiration_time"))
    reference_only = has_reference_only_flag(event)
    outcome_list = _event_outcome_list(event)
    per_market_binary_seen = _collect_per_market_binary_outcomes(event)
    market_tickers = _event_market_tickers(event)
    complete_marker = event.get("complete") is True
    is_exhaustive_marker = event.get("is_exhaustive") is True
    all_outcomes_marker = event.get("all_outcomes_included") is True
    complete = complete_marker or is_exhaustive_marker or all_outcomes_marker
    if reference_only:
        blockers.append("reference_only_source")
    if not event_ticker:
        blockers.append("missing_event_ticker")
    if not event_id:
        blockers.append("missing_event_id")
    if not event_ticker and not event_id and title:
        blockers.append("title_only_event")
    if outcome_list is None:
        blockers.append("missing_event_outcome_list")
        if per_market_binary_seen:
            blockers.append("per_market_binary_outcomes_only_at_event_level")
    else:
        if len(outcome_list) < 2:
            blockers.append("outcome_count_lt_two")
        if len(set(outcome_list)) != len(outcome_list):
            blockers.append("duplicate_outcome_list_entries")
        if market_tickers and len(market_tickers) != len(outcome_list):
            blockers.append("outcome_count_vs_market_count_mismatch")
    if market_tickers and len(set(market_tickers)) != len(market_tickers):
        blockers.append("duplicate_market_tickers")
    if not complete:
        blockers.append("event_not_marked_complete")
    if not rules_primary and not rules_secondary:
        blockers.append("missing_rules_evidence")
    if not settlement_source:
        blockers.append("missing_settlement_source_evidence")
    if _markets_have_mixed_rules(event):
        blockers.append("mixed_market_rules")
    if _markets_have_mixed_times(event):
        blockers.append("mixed_market_times")
    blockers = sorted(set(blockers))
    return NormalizedKalshiEventMetadata(
        event_ticker=event_ticker,
        event_id=event_id,
        series_ticker=series_ticker,
        title=title,
        outcome_list=outcome_list,
        complete=complete,
        is_exhaustive=is_exhaustive_marker,
        all_outcomes_included=all_outcomes_marker,
        market_tickers=market_tickers,
        rules_primary=rules_primary,
        rules_secondary=rules_secondary,
        settlement_source_raw_evidence=settlement_source,
        close_time=close_time,
        expected_expiration_time=expected_expiration_time,
        expiration_time=expiration_time,
        latest_expiration_time=latest_expiration_time,
        reference_only=reference_only,
        blockers=blockers,
        source_path=source_path,
        per_market_binary_outcomes_seen=per_market_binary_seen,
    )


def _event_outcome_list(event: dict[str, Any]) -> list[str] | None:
    for key in ("outcome_list", "outcomes", "event_outcome_list", "complete_outcome_list"):
        value = event.get(key)
        parsed = _list_from(value)
        if parsed and not _is_binary_yes_no(parsed):
            return parsed
    return None


def _collect_per_market_binary_outcomes(event: dict[str, Any]) -> list[list[str]]:
    results: list[list[str]] = []
    markets = event.get("markets")
    if not isinstance(markets, list):
        return results
    for market in markets:
        if not isinstance(market, dict):
            continue
        for key in ("outcomes", "outcome_list"):
            parsed = _list_from(market.get(key))
            if parsed and _is_binary_yes_no(parsed):
                results.append(parsed)
                break
    return results


def _event_market_tickers(event: dict[str, Any]) -> list[str]:
    tickers: list[str] = []
    explicit = _string_list(
        event.get("market_tickers") or event.get("exact_market_tickers") or event.get("market_ids")
    )
    if explicit:
        tickers.extend(explicit)
    markets = event.get("markets")
    if isinstance(markets, list):
        for market in markets:
            if not isinstance(market, dict):
                continue
            ticker = market.get("market_ticker") or market.get("ticker") or market.get("id")
            if isinstance(ticker, str) and ticker.strip():
                tickers.append(ticker.strip())
    seen: set[str] = set()
    deduped: list[str] = []
    for ticker in tickers:
        if ticker not in seen:
            deduped.append(ticker)
            seen.add(ticker)
    return deduped


def _markets_have_mixed_rules(event: dict[str, Any]) -> bool:
    markets = event.get("markets")
    if not isinstance(markets, list):
        return False
    rules_keys: set[str] = set()
    for market in markets:
        if not isinstance(market, dict):
            continue
        rules_value = market.get("rules_primary") or market.get("rules") or market.get("resolution_text")
        key = _normalize_text(rules_value)
        if key:
            rules_keys.add(key)
    return len(rules_keys) > 1


def _markets_have_mixed_times(event: dict[str, Any]) -> bool:
    markets = event.get("markets")
    if not isinstance(markets, list):
        return False
    time_keys: set[tuple[str, ...]] = set()
    for market in markets:
        if not isinstance(market, dict):
            continue
        key = tuple(
            _normalize_text(market.get(name)) or ""
            for name in (
                "close_time",
                "expected_expiration_time",
                "expiration_time",
                "latest_expiration_time",
            )
        )
        if any(key):
            time_keys.add(key)
    return len(time_keys) > 1


def _snapshot_event_lookup(snapshot_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map every event-identifying key (event_ticker/event_id/etc.) found in the
    snapshot to the underlying snapshot event dict. Used by the join to find the
    matched snapshot event's markets when building enriched normalized_market
    rows. Returns the FIRST event seen for each key — duplicates are ignored.
    """
    lookup: dict[str, dict[str, Any]] = {}
    if not isinstance(snapshot_payload, dict):
        return lookup
    events = snapshot_payload.get("events")
    if not isinstance(events, list):
        return lookup
    for event in events:
        if not isinstance(event, dict):
            continue
        for key in ("event_ticker", "event_id", "id", "venue_native_event_id"):
            value = _string_or_none(event.get(key))
            if value and value not in lookup:
                lookup[value] = event
    return lookup


def _build_enriched_normalized_markets(
    *,
    trusted_event: NormalizedKalshiEventMetadata,
    snapshot_event_markets: list[dict[str, Any]],
    matched_event_key: str | None,
) -> list[dict[str, Any]]:
    """Build per-market rows that detect-structural-baskets can consume directly.

    Each row carries (a) identifiers from the snapshot market, (b) event-level
    rules/settlement_source/timing from the trusted event metadata, (c) the
    per-market orderbook_enrichment from the snapshot, and (d) a venue-native
    ``exhaustive_group`` evidence block sourced from the trusted event metadata.

    Safety invariants:

    * Only called when ``trusted_event.is_trusted_for_completeness()`` is True
      AND the join has confirmed the snapshot event matches and no metadata
      tickers are missing/extra. The caller MUST gate on these.
    * Outcome list and completeness markers are taken ONLY from the trusted
      event metadata. Per-market Yes/No, title, ticker counts, and graph hints
      are never used.
    * Orderbook fields are read AS-IS from the snapshot market — never
      synthesized. Missing/stale orderbooks remain visible to downstream
      depth/freshness gates and will block in detect-structural-baskets.
    * No STOP_FOR_REVIEW or PAPER_CANDIDATE state is emitted by this builder.
      It only prepares input rows for the downstream detector, which still must
      pass fee/depth/freshness/settlement gates independently.
    """
    if not trusted_event.is_trusted_for_completeness():
        return []
    outcome_list = list(trusted_event.outcome_list or [])
    manifest_tickers = list(trusted_event.market_tickers)
    if not manifest_tickers:
        return []
    group_key = matched_event_key or trusted_event.event_ticker or trusted_event.event_id
    if not group_key:
        return []
    event_rules_primary = trusted_event.rules_primary
    event_rules_secondary = trusted_event.rules_secondary
    event_settlement_source = trusted_event.settlement_source_raw_evidence
    event_close_time = trusted_event.close_time
    event_expected_expiration_time = trusted_event.expected_expiration_time
    event_expiration_time = trusted_event.expiration_time
    event_latest_expiration_time = trusted_event.latest_expiration_time
    event_settlement_time = event_expiration_time or event_expected_expiration_time
    event_resolution_date = _date_prefix(
        event_expected_expiration_time or event_expiration_time or event_latest_expiration_time
    )

    snapshot_by_ticker: dict[str, dict[str, Any]] = {}
    for market in snapshot_event_markets:
        ticker = _string_or_none(market.get("market_ticker") or market.get("ticker"))
        if ticker and ticker not in snapshot_by_ticker:
            snapshot_by_ticker[ticker] = market

    rows: list[dict[str, Any]] = []
    for ticker in manifest_tickers:
        snapshot_market = snapshot_by_ticker.get(ticker)
        if not snapshot_market:
            # Should not happen — caller guarantees no missing tickers — but
            # fail closed and skip rather than fabricate.
            continue
        rules_primary = _string_or_none(snapshot_market.get("rules_primary")) or event_rules_primary
        rules_secondary = _string_or_none(snapshot_market.get("rules_secondary")) or event_rules_secondary
        rules_combined = _join_rules_text(rules_primary, rules_secondary)
        settlement_source = (
            _string_or_none(snapshot_market.get("settlement_source_raw_evidence"))
            or _string_or_none(snapshot_market.get("settlement_source"))
            or _string_or_none(snapshot_market.get("resolution_source"))
            or event_settlement_source
        )
        close_time = _string_or_none(snapshot_market.get("close_time")) or event_close_time
        expected_expiration_time = (
            _string_or_none(snapshot_market.get("expected_expiration_time"))
            or event_expected_expiration_time
        )
        expiration_time = _string_or_none(snapshot_market.get("expiration_time")) or event_expiration_time
        latest_expiration_time = (
            _string_or_none(snapshot_market.get("latest_expiration_time"))
            or event_latest_expiration_time
        )
        settlement_time = (
            _string_or_none(snapshot_market.get("settlement_time"))
            or expiration_time
            or expected_expiration_time
            or event_settlement_time
        )
        resolution_date = (
            _date_prefix(_string_or_none(snapshot_market.get("resolution_date")))
            or _date_prefix(expected_expiration_time or expiration_time or latest_expiration_time)
            or event_resolution_date
        )
        outcome = _string_or_none(
            snapshot_market.get("yes_sub_title")
            or snapshot_market.get("outcome")
            or snapshot_market.get("outcome_label")
        )
        title = (
            _string_or_none(snapshot_market.get("title"))
            or _string_or_none(snapshot_market.get("question"))
            or ticker
        )
        orderbook = snapshot_market.get("orderbook_enrichment")
        if not isinstance(orderbook, dict):
            orderbook = None
        row: dict[str, Any] = {
            "venue": "kalshi",
            "market_id": ticker,
            "ticker": ticker,
            "market_ticker": ticker,
            "event_id": group_key,
            "event_ticker": trusted_event.event_ticker,
            "group_id": group_key,
            "venue_native_event_id": group_key,
            "venue_native_group_id": group_key,
            "series_ticker": trusted_event.series_ticker,
            "title": title,
            "question": title,
            "outcome": outcome,
            "rules": rules_combined,
            "rules_primary": rules_primary,
            "rules_secondary": rules_secondary,
            "resolution_criteria": rules_combined,
            "settlement_source": settlement_source,
            "settlement_source_raw_evidence": settlement_source,
            "settlement_source_status": "explicit" if settlement_source else "missing",
            "close_time": close_time,
            "expected_expiration_time": expected_expiration_time,
            "expiration_time": expiration_time,
            "latest_expiration_time": latest_expiration_time,
            "settlement_time": settlement_time,
            "resolution_date": resolution_date,
            "orderbook_enrichment": orderbook,
            "outcome_list": list(outcome_list),
            "all_outcomes_included": True,
            "is_exhaustive": True,
            "complete": True,
            "reference_only": has_reference_only_flag(snapshot_market),
            "event_metadata_join_source": EVENT_METADATA_JOIN_SOURCE,
            "event_metadata_trusted_for_completeness": True,
            "exhaustive_group": {
                "source": KALSHI_EVENT_METADATA_EVIDENCE_SOURCE,
                "venue_native": True,
                "all_outcomes_included": True,
                "is_exhaustive": True,
                "group_id": group_key,
                "event_id": group_key,
                "outcome_market_ids": list(manifest_tickers),
                "expected_outcome_count": len(outcome_list),
                "outcome_list": list(outcome_list),
                "evidence": (
                    "saved Kalshi event metadata trusted join: explicit event-level outcome_list, "
                    "complete marker, rules and settlement_source — and matching snapshot tickers"
                ),
            },
        }
        rows.append(row)
    return rows


def _snapshot_reference_only_market_tickers(
    snapshot_event_markets: list[dict[str, Any]],
    manifest_tickers: set[str],
) -> list[str]:
    tickers: list[str] = []
    for market in snapshot_event_markets:
        ticker = _string_or_none(market.get("market_ticker") or market.get("ticker"))
        if manifest_tickers and ticker not in manifest_tickers:
            continue
        if has_reference_only_flag(market):
            tickers.append(ticker or "<unknown>")
    return sorted(set(tickers))


def _join_rules_text(primary: str | None, secondary: str | None) -> str | None:
    parts = [part for part in (primary, secondary) if isinstance(part, str) and part.strip()]
    if not parts:
        return None
    return "\n".join(parts)


def _date_prefix(value: str | None) -> str | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    return value[:10]


def _snapshot_event_market_tickers(snapshot_payload: dict[str, Any]) -> dict[str, list[str]]:
    by_event: dict[str, list[str]] = {}
    if not isinstance(snapshot_payload, dict):
        return by_event
    events = snapshot_payload.get("events") or []
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict):
                continue
            event_keys = [
                _string_or_none(event.get(key))
                for key in ("event_ticker", "event_id", "id", "venue_native_event_id")
            ]
            tickers: list[str] = []
            for market in event.get("markets", []) or []:
                if isinstance(market, dict):
                    ticker = market.get("market_ticker") or market.get("ticker") or market.get("id")
                    if isinstance(ticker, str) and ticker.strip():
                        tickers.append(ticker.strip())
            for key in event_keys:
                if key:
                    by_event.setdefault(key, []).extend(tickers)
    for source in (snapshot_payload.get("normalized_markets"), snapshot_payload.get("markets")):
        if isinstance(source, list):
            for market in source:
                if not isinstance(market, dict):
                    continue
                raw = market.get("raw") if isinstance(market.get("raw"), dict) else {}
                event_key = (
                    _string_or_none(market.get("event_ticker"))
                    or _string_or_none(market.get("event_id"))
                    or _string_or_none(raw.get("event_ticker"))
                    or _string_or_none(raw.get("event_id"))
                )
                ticker = (
                    market.get("market_ticker")
                    or market.get("ticker")
                    or (raw.get("market_ticker") if isinstance(raw, dict) else None)
                    or (raw.get("ticker") if isinstance(raw, dict) else None)
                )
                if event_key and isinstance(ticker, str) and ticker.strip():
                    by_event.setdefault(event_key, []).append(ticker.strip())
    deduped: dict[str, list[str]] = {}
    for key, tickers in by_event.items():
        seen: set[str] = set()
        ordered: list[str] = []
        for ticker in tickers:
            if ticker not in seen:
                ordered.append(ticker)
                seen.add(ticker)
        deduped[key] = ordered
    return deduped


def _list_from(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    parsed: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            parsed.append(item.strip())
        elif isinstance(item, dict):
            label = item.get("label") or item.get("name") or item.get("outcome") or item.get("yes_sub_title")
            if isinstance(label, str) and label.strip():
                parsed.append(label.strip())
    return parsed or None


def _is_binary_yes_no(values: list[str]) -> bool:
    normalized = {value.strip().lower() for value in values}
    return normalized == {"yes", "no"}


def _string_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _normalize_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return " ".join(value.lower().split())
