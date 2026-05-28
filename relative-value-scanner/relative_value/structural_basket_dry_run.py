"""Saved-file-only orchestration for the Kalshi event metadata → structural
basket → paper-fill review pipeline.

This module wires together the existing saved-file diagnostics so a real
saved Kalshi market snapshot plus saved event-metadata JSON files can be
walked through the full review pipeline in a single command WITHOUT any
live API calls, order placement, account/auth/private endpoints, or
midpoint-fill assumptions.

Pipeline (every step is saved-file-only):

1. audit_kalshi_event_metadata        — normalize/audit metadata payloads
2. join_kalshi_event_metadata         — write enriched_snapshot with
                                        normalized_markets carrying trusted
                                        venue-native exhaustive_group evidence
3. build_structural_basket_review_report — apply explicit fee, depth,
                                            freshness, settlement,
                                            exhaustiveness gates
4. simulate_paper_fill_journal        — ONLY when STOP_FOR_REVIEW rows exist
                                        AND the caller opted in; otherwise
                                        paper simulation is skipped and the
                                        reason is recorded

Hard safety invariants enforced here:

* The simulator is invoked only when the upstream detector produced at
  least one STOP_FOR_REVIEW row. Any other condition produces
  ``paper_simulation_skipped`` plus a structured ``paper_simulation_skip_reason``.
* No completeness markers are propagated from this module. Every
  completeness signal comes from the trusted join in
  ``kalshi_event_metadata.join_kalshi_event_metadata``.
* Title similarity, graph hints, market counts, and per-market Yes/No are
  NEVER consulted here.
* STOP_FOR_REVIEW status is treated as review/report-only; the summary
  text never claims an order will be placed, never emits PAPER_CANDIDATE,
  and the safety block reasserts this on every run.
* The metadata importer is also saved-file-only: it reads a JSON file from
  disk, validates structure via the existing normalizer, and reports
  blockers. It NEVER fetches from any URL, opens a socket, or touches
  secrets/auth/credentials/wallets/browsers.
"""

from __future__ import annotations

import json
import shutil
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.kalshi_event_metadata import (
    KALSHI_EVENT_METADATA_EVIDENCE_SOURCE,
    audit_kalshi_event_metadata,
    join_kalshi_event_metadata,
    normalize_kalshi_event_metadata_payload,
)
from relative_value.paper_fill_simulator import simulate_paper_fill_journal
from relative_value.structural_basket_detector import (
    STATUS_STOP_FOR_REVIEW,
    build_structural_basket_review_report,
)


DRY_RUN_SOURCE = "structural_basket_dry_run_v1"
METADATA_IMPORT_SOURCE = "kalshi_event_metadata_saved_file_importer_v1"
PAPER_SIMULATION_SKIP_NO_STOP_FOR_REVIEW = "no_stop_for_review_row"
PAPER_SIMULATION_SKIP_DISABLED_BY_CALLER = "paper_simulation_disabled_by_caller"


def run_structural_basket_dry_run(
    *,
    snapshot_payload: dict[str, Any],
    metadata_payloads: list[dict[str, Any]],
    snapshot_path: str | None = None,
    metadata_source_paths: list[str] | None = None,
    generated_at: datetime | None = None,
    max_quote_age_seconds: float = 1800.0,
    min_depth: float = 1.0,
    desired_quantity: float = 1.0,
    slippage_budget_cents_per_leg: float = 0.0,
    simulate_paper_fills_on_stop_for_review: bool = True,
) -> dict[str, Any]:
    """Run the full saved-file dry-run pipeline and return a summary report.

    The returned dict embeds the audit, join, and detector reports verbatim,
    plus the paper-fill journal when (and only when) the upstream detector
    surfaced at least one STOP_FOR_REVIEW row AND the caller opted in.
    """
    generated = generated_at or datetime.now(timezone.utc)

    audit_report = audit_kalshi_event_metadata(
        metadata_payloads,
        generated_at=generated,
        source_paths=metadata_source_paths,
    )
    join_result = join_kalshi_event_metadata(
        snapshot_payload=snapshot_payload,
        metadata_payloads=metadata_payloads,
        generated_at=generated,
        snapshot_path=snapshot_path,
        source_paths=metadata_source_paths,
    )
    enriched_snapshot = join_result["enriched_snapshot"]
    join_report = join_result["report"]

    structural_report = build_structural_basket_review_report(
        snapshot_payloads=[enriched_snapshot],
        manifest_payload=None,
        detected_at=generated,
        max_quote_age_seconds=max_quote_age_seconds,
        min_depth=min_depth,
    )

    stop_for_review_rows = [
        row for row in structural_report.get("rows", []) if row.get("status") == STATUS_STOP_FOR_REVIEW
    ]
    paper_fill_journal: dict[str, Any] | None = None
    skip_reason: str | None = None
    if not stop_for_review_rows:
        skip_reason = PAPER_SIMULATION_SKIP_NO_STOP_FOR_REVIEW
    elif not simulate_paper_fills_on_stop_for_review:
        skip_reason = PAPER_SIMULATION_SKIP_DISABLED_BY_CALLER
    else:
        paper_fill_journal = simulate_paper_fill_journal(
            input_payload={"rows": stop_for_review_rows},
            generated_at=generated,
            desired_quantity=desired_quantity,
            max_quote_age_seconds=max_quote_age_seconds,
            slippage_budget_cents_per_leg=slippage_budget_cents_per_leg,
        )

    top_blockers = _aggregate_top_blockers(
        audit_report=audit_report,
        join_report=join_report,
        structural_report=structural_report,
        paper_fill_journal=paper_fill_journal,
    )
    summary = {
        "metadata_files": audit_report["summary"].get("metadata_files", 0),
        "metadata_events": audit_report["summary"].get("events_discovered", 0),
        "trusted_metadata_events": audit_report["summary"].get("events_trusted_for_completeness", 0),
        "blocked_metadata_events": audit_report["summary"].get("events_blocked", 0),
        "reference_only_metadata_events": audit_report["summary"].get("events_reference_only", 0),
        "matched_events": join_report["summary"].get("events_matched_to_snapshot", 0),
        "trusted_after_join_events": join_report["summary"].get("events_trusted_after_join", 0),
        "blocked_after_join_events": join_report["summary"].get("events_blocked_after_join", 0),
        "enriched_normalized_market_rows": join_report["summary"].get("enriched_normalized_market_row_count", 0),
        "structural_groups_evaluated": structural_report["summary"].get("evaluated_group_count", 0),
        "structural_review_count": structural_report["summary"].get("review_count", 0),
        "stop_for_review_count": structural_report["summary"].get("stop_for_review_count", 0),
        "structural_status_counts": dict(structural_report["summary"].get("status_counts") or {}),
        "paper_fill_rows": (paper_fill_journal["summary"].get("input_row_count", 0) if paper_fill_journal else 0),
        "paper_fill_simulated_count": (
            paper_fill_journal["summary"].get("simulated_fill_count", 0) if paper_fill_journal else 0
        ),
        "paper_fill_blocked_count": (
            paper_fill_journal["summary"].get("blocked_count", 0) if paper_fill_journal else 0
        ),
        "paper_simulation_skipped": paper_fill_journal is None,
        "paper_simulation_skip_reason": skip_reason,
        "top_blockers": top_blockers,
        "paper_candidate_count": 0,
    }
    return {
        "schema_version": 1,
        "source": DRY_RUN_SOURCE,
        "generated_at": generated.isoformat(),
        "config": {
            "snapshot_path": snapshot_path,
            "metadata_source_paths": list(metadata_source_paths or []),
            "max_quote_age_seconds": max_quote_age_seconds,
            "min_depth": min_depth,
            "desired_quantity": desired_quantity,
            "slippage_budget_cents_per_leg": slippage_budget_cents_per_leg,
            "simulate_paper_fills_on_stop_for_review": simulate_paper_fills_on_stop_for_review,
        },
        "summary": summary,
        "audit_report": audit_report,
        "join_report": join_report,
        "enriched_snapshot": enriched_snapshot,
        "structural_basket_report": structural_report,
        "paper_fill_journal": paper_fill_journal,
        "safety": {
            "saved_file_only": True,
            "diagnostic_only": True,
            "live_fetch_attempted": False,
            "places_orders": False,
            "auth_used": False,
            "private_endpoints_used": False,
            "secrets_read": False,
            "browser_automation_used": False,
            "wallet_used": False,
            "paper_candidate_emitted": False,
            "stop_for_review_means_review_only": True,
            "uses_midpoint": False,
            "uses_title_similarity_for_exhaustiveness": False,
            "uses_graph_hints_for_exhaustiveness": False,
            "uses_count_only_evidence": False,
            "infers_exhaustiveness_from_ticker": False,
            "infers_exhaustiveness_from_market_count": False,
            "affects_evaluator_gates": False,
            "allowed_evidence_source": KALSHI_EVENT_METADATA_EVIDENCE_SOURCE,
            "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        },
    }


def run_structural_basket_dry_run_files(
    *,
    snapshot_path: Path,
    metadata_paths: list[Path],
    summary_json_output: Path,
    summary_markdown_output: Path,
    audit_json_output: Path | None = None,
    audit_markdown_output: Path | None = None,
    join_json_output: Path | None = None,
    join_markdown_output: Path | None = None,
    enriched_snapshot_output: Path | None = None,
    structural_json_output: Path | None = None,
    structural_markdown_output: Path | None = None,
    paper_fill_json_output: Path | None = None,
    paper_fill_markdown_output: Path | None = None,
    generated_at: datetime | None = None,
    max_quote_age_seconds: float = 1800.0,
    min_depth: float = 1.0,
    desired_quantity: float = 1.0,
    slippage_budget_cents_per_leg: float = 0.0,
    simulate_paper_fills_on_stop_for_review: bool = True,
) -> dict[str, Any]:
    """File-level helper that reads inputs from disk, runs the dry-run, and
    writes the summary plus every sub-report it can.

    The summary JSON/MD are always written. The per-stage sub-reports are
    only written when their output paths are provided; this keeps the
    default invocation lightweight while letting callers opt into the full
    audit trail.
    """
    snapshot_payload = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    metadata_payloads: list[dict[str, Any]] = []
    string_paths: list[str] = []
    for path in metadata_paths:
        metadata_payloads.append(json.loads(Path(path).read_text(encoding="utf-8")))
        string_paths.append(str(path))

    report = run_structural_basket_dry_run(
        snapshot_payload=snapshot_payload,
        metadata_payloads=metadata_payloads,
        snapshot_path=str(snapshot_path),
        metadata_source_paths=string_paths,
        generated_at=generated_at,
        max_quote_age_seconds=max_quote_age_seconds,
        min_depth=min_depth,
        desired_quantity=desired_quantity,
        slippage_budget_cents_per_leg=slippage_budget_cents_per_leg,
        simulate_paper_fills_on_stop_for_review=simulate_paper_fills_on_stop_for_review,
    )

    summary_json_output.parent.mkdir(parents=True, exist_ok=True)
    summary_markdown_output.parent.mkdir(parents=True, exist_ok=True)
    summary_json_output.write_text(
        json.dumps(_summary_only(report), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    summary_markdown_output.write_text(render_dry_run_summary_markdown(report), encoding="utf-8")

    if audit_json_output is not None:
        audit_json_output.parent.mkdir(parents=True, exist_ok=True)
        audit_json_output.write_text(
            json.dumps(report["audit_report"], indent=2, sort_keys=True),
            encoding="utf-8",
        )
    if audit_markdown_output is not None:
        from relative_value.kalshi_event_metadata import render_kalshi_event_metadata_audit_markdown

        audit_markdown_output.parent.mkdir(parents=True, exist_ok=True)
        audit_markdown_output.write_text(
            render_kalshi_event_metadata_audit_markdown(report["audit_report"]),
            encoding="utf-8",
        )
    if join_json_output is not None:
        join_json_output.parent.mkdir(parents=True, exist_ok=True)
        join_json_output.write_text(
            json.dumps(report["join_report"], indent=2, sort_keys=True),
            encoding="utf-8",
        )
    if join_markdown_output is not None:
        from relative_value.kalshi_event_metadata import render_kalshi_event_metadata_join_markdown

        join_markdown_output.parent.mkdir(parents=True, exist_ok=True)
        join_markdown_output.write_text(
            render_kalshi_event_metadata_join_markdown(report["join_report"]),
            encoding="utf-8",
        )
    if enriched_snapshot_output is not None:
        enriched_snapshot_output.parent.mkdir(parents=True, exist_ok=True)
        enriched_snapshot_output.write_text(
            json.dumps(report["enriched_snapshot"], indent=2, sort_keys=True),
            encoding="utf-8",
        )
    if structural_json_output is not None:
        structural_json_output.parent.mkdir(parents=True, exist_ok=True)
        structural_json_output.write_text(
            json.dumps(report["structural_basket_report"], indent=2, sort_keys=True),
            encoding="utf-8",
        )
    if structural_markdown_output is not None:
        from relative_value.structural_basket_detector import render_structural_basket_review_markdown

        structural_markdown_output.parent.mkdir(parents=True, exist_ok=True)
        structural_markdown_output.write_text(
            render_structural_basket_review_markdown(report["structural_basket_report"]),
            encoding="utf-8",
        )
    if paper_fill_json_output is not None and report.get("paper_fill_journal") is not None:
        paper_fill_json_output.parent.mkdir(parents=True, exist_ok=True)
        paper_fill_json_output.write_text(
            json.dumps(report["paper_fill_journal"], indent=2, sort_keys=True),
            encoding="utf-8",
        )
    if paper_fill_markdown_output is not None and report.get("paper_fill_journal") is not None:
        from relative_value.paper_fill_simulator import render_paper_fill_journal_markdown

        paper_fill_markdown_output.parent.mkdir(parents=True, exist_ok=True)
        paper_fill_markdown_output.write_text(
            render_paper_fill_journal_markdown(report["paper_fill_journal"]),
            encoding="utf-8",
        )

    return report


def render_dry_run_summary_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    config = report.get("config") or {}
    lines = [
        "# Structural Basket Dry Run Summary",
        "",
        "Saved-file-only pipeline: audit Kalshi event metadata → join into snapshot → "
        "build structural basket review → optionally simulate paper fills only when the "
        "review surfaces STOP_FOR_REVIEW. No live API calls. No orders are placed. "
        "STOP_FOR_REVIEW is review/report-only; it never authorizes execution.",
        "",
        f"- generated_at: {report.get('generated_at', '')}",
        f"- snapshot_path: {config.get('snapshot_path', '')}",
        f"- metadata_source_paths: {', '.join(config.get('metadata_source_paths') or []) or '(none)'}",
        f"- max_quote_age_seconds: {config.get('max_quote_age_seconds', '')}",
        f"- min_depth: {config.get('min_depth', '')}",
        f"- desired_quantity: {config.get('desired_quantity', '')}",
        f"- slippage_budget_cents_per_leg: {config.get('slippage_budget_cents_per_leg', '')}",
        "",
        "## Counts",
        "",
        f"- metadata_events: {summary.get('metadata_events', 0)}",
        f"- trusted_metadata_events: {summary.get('trusted_metadata_events', 0)}",
        f"- blocked_metadata_events: {summary.get('blocked_metadata_events', 0)}",
        f"- reference_only_metadata_events: {summary.get('reference_only_metadata_events', 0)}",
        f"- matched_events: {summary.get('matched_events', 0)}",
        f"- trusted_after_join_events: {summary.get('trusted_after_join_events', 0)}",
        f"- enriched_normalized_market_rows: {summary.get('enriched_normalized_market_rows', 0)}",
        f"- structural_groups_evaluated: {summary.get('structural_groups_evaluated', 0)}",
        f"- structural_review_count: {summary.get('structural_review_count', 0)}",
        f"- stop_for_review_count: {summary.get('stop_for_review_count', 0)}",
        f"- paper_fill_rows: {summary.get('paper_fill_rows', 0)}",
        f"- paper_fill_simulated_count: {summary.get('paper_fill_simulated_count', 0)}",
        f"- paper_fill_blocked_count: {summary.get('paper_fill_blocked_count', 0)}",
        f"- paper_candidate_count: {summary.get('paper_candidate_count', 0)}",
        "",
        "## Paper simulation",
        "",
        (
            f"- paper_simulation_skipped: {bool(summary.get('paper_simulation_skipped'))}"
        ),
        (
            f"- paper_simulation_skip_reason: {summary.get('paper_simulation_skip_reason') or '(none)'}"
        ),
        "",
        "## Top blockers",
        "",
        "| Stage | Blocker | Count |",
        "|---|---|---:|",
    ]
    for entry in summary.get("top_blockers") or []:
        stage = str(entry.get("stage") or "").replace("|", "/")
        blocker = str(entry.get("blocker") or "").replace("|", "/")
        count = entry.get("count") or 0
        lines.append(f"| {stage} | {blocker} | {count} |")
    if not summary.get("top_blockers"):
        lines.append("| (none) | (none) | 0 |")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- saved_file_only: true",
            "- live_fetch_attempted: false",
            "- places_orders: false",
            "- auth_used: false",
            "- private_endpoints_used: false",
            "- secrets_read: false",
            "- paper_candidate_emitted: false",
            "- stop_for_review_means_review_only: true",
            "- uses_midpoint: false",
            "- uses_title_similarity_for_exhaustiveness: false",
            "- uses_graph_hints_for_exhaustiveness: false",
            "- uses_count_only_evidence: false",
            "- allowed_actions: WATCH, MANUAL_REVIEW",
        ]
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Saved-file Kalshi event metadata importer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImportedKalshiEventMetadata:
    source_path: str
    destination_path: str | None
    accepted: bool
    blockers: list[str]
    trusted_for_completeness: bool
    event_tickers: list[str]


def import_kalshi_event_metadata_file(
    *,
    source: Path,
    destination_dir: Path | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Saved-file metadata acquisition: read a Kalshi event metadata JSON
    payload from ``source``, validate that it parses through the normalizer,
    and (optionally) copy it into ``destination_dir`` so the dry-run can
    pick it up.

    This helper performs ZERO network I/O. It does not authenticate, does
    not read secrets, does not query any URL, and does not touch any
    venue's private endpoints. The "acquisition" step is meant to be
    performed manually by Mason (e.g. saving a JSON response from the
    Kalshi public web view to disk and pointing this importer at it). The
    importer's job is to validate structure and copy into the metadata
    directory.

    The function returns a report dict listing every event the file
    contained, the blockers raised by the normalizer for each event, and
    whether the destination file was written.
    """
    raw_text = source.read_text(encoding="utf-8")
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return _import_report(
            imported=[
                ImportedKalshiEventMetadata(
                    source_path=str(source),
                    destination_path=None,
                    accepted=False,
                    blockers=[f"invalid_json:{exc.msg}"],
                    trusted_for_completeness=False,
                    event_tickers=[],
                )
            ],
        )

    normalized_events = normalize_kalshi_event_metadata_payload(payload, source_path=str(source))
    blockers: list[str] = []
    event_tickers: list[str] = []
    trusted = False
    for event in normalized_events:
        blockers.extend(event.blockers)
        if event.event_ticker:
            event_tickers.append(event.event_ticker)
        if event.is_trusted_for_completeness():
            trusted = True
    blockers = sorted(set(blockers))

    destination_path: str | None = None
    written = False
    if destination_dir is not None:
        destination_dir.mkdir(parents=True, exist_ok=True)
        target = destination_dir / source.name
        if target.exists() and not overwrite:
            destination_path = str(target)
            written = False
        else:
            # Copy via shutil so the on-disk file is byte-identical to the
            # source we just validated — we never rewrite the JSON, which
            # avoids accidentally normalizing fields that a downstream tool
            # might later add new blockers for.
            shutil.copyfile(str(source), str(target))
            destination_path = str(target)
            written = True

    imported = ImportedKalshiEventMetadata(
        source_path=str(source),
        destination_path=destination_path,
        accepted=written or (destination_dir is None),
        blockers=blockers,
        trusted_for_completeness=trusted,
        event_tickers=event_tickers,
    )
    return _import_report(imported=[imported])


def import_kalshi_event_metadata_files(
    *,
    sources: list[Path],
    destination_dir: Path | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Same as :func:`import_kalshi_event_metadata_file` but for many files."""
    imported: list[ImportedKalshiEventMetadata] = []
    for source in sources:
        single = import_kalshi_event_metadata_file(
            source=source,
            destination_dir=destination_dir,
            overwrite=overwrite,
        )
        imported.extend(_imported_from_report(single))
    return _import_report(imported=imported)


def render_metadata_importer_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Kalshi Event Metadata Importer",
        "",
        "Saved-file-only acquisition. No live API. No auth. No secrets. "
        "Files are validated through the same normalizer the audit uses; "
        "blockers are reported but the importer never strips them.",
        "",
        f"- files_seen: {summary.get('files_seen', 0)}",
        f"- files_written: {summary.get('files_written', 0)}",
        f"- files_skipped_existing: {summary.get('files_skipped_existing', 0)}",
        f"- trusted_event_count: {summary.get('trusted_event_count', 0)}",
        f"- blocked_event_count: {summary.get('blocked_event_count', 0)}",
        "",
        "| Source | Destination | Accepted | Trusted | Top blockers | Event tickers |",
        "|---|---|---:|---:|---|---|",
    ]
    for row in report.get("rows", []) or []:
        blockers = "; ".join((row.get("blockers") or [])[:4]).replace("|", "/")
        tickers = ", ".join(row.get("event_tickers") or []).replace("|", "/")
        lines.append(
            "| {source} | {dest} | {accepted} | {trusted} | {blockers} | {tickers} |".format(
                source=str(row.get("source_path") or "").replace("|", "/"),
                dest=str(row.get("destination_path") or "").replace("|", "/"),
                accepted=str(bool(row.get("accepted"))).lower(),
                trusted=str(bool(row.get("trusted_for_completeness"))).lower(),
                blockers=blockers,
                tickers=tickers,
            )
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _summary_only(report: dict[str, Any]) -> dict[str, Any]:
    """Strip the embedded sub-reports out of the summary JSON to keep it
    small. Sub-reports are still written when their dedicated outputs are
    supplied. The summary file should be cheap to grep and diff."""
    return {
        "schema_version": report.get("schema_version"),
        "source": report.get("source"),
        "generated_at": report.get("generated_at"),
        "config": report.get("config"),
        "summary": report.get("summary"),
        "safety": report.get("safety"),
    }


def _aggregate_top_blockers(
    *,
    audit_report: dict[str, Any],
    join_report: dict[str, Any],
    structural_report: dict[str, Any],
    paper_fill_journal: dict[str, Any] | None,
    top_n: int = 12,
) -> list[dict[str, Any]]:
    counter: Counter[tuple[str, str]] = Counter()
    for blocker, count in (audit_report.get("summary", {}).get("blocker_counts") or {}).items():
        counter[("metadata_audit", blocker)] += int(count or 0)
    for row in join_report.get("rows") or []:
        for blocker in row.get("join_blockers") or []:
            counter[("metadata_join", blocker)] += 1
    for row in structural_report.get("rows") or []:
        for blocker in row.get("blockers") or []:
            counter[("structural_basket", blocker)] += 1
    if paper_fill_journal is not None:
        for row in paper_fill_journal.get("journal") or []:
            for blocker in row.get("blockers") or []:
                counter[("paper_fill", blocker)] += 1
    return [
        {"stage": stage, "blocker": blocker, "count": count}
        for (stage, blocker), count in counter.most_common(top_n)
    ]


def _import_report(*, imported: list[ImportedKalshiEventMetadata]) -> dict[str, Any]:
    rows = [
        {
            "source_path": item.source_path,
            "destination_path": item.destination_path,
            "accepted": item.accepted,
            "blockers": list(item.blockers),
            "trusted_for_completeness": item.trusted_for_completeness,
            "event_tickers": list(item.event_tickers),
        }
        for item in imported
    ]
    written = sum(1 for item in imported if item.destination_path and item.accepted)
    skipped = sum(
        1
        for item in imported
        if item.destination_path is not None and not item.accepted and not item.blockers
    )
    return {
        "schema_version": 1,
        "source": METADATA_IMPORT_SOURCE,
        "summary": {
            "files_seen": len(imported),
            "files_written": written,
            "files_skipped_existing": skipped,
            "trusted_event_count": sum(
                len(item.event_tickers) for item in imported if item.trusted_for_completeness
            ),
            "blocked_event_count": sum(1 for item in imported if item.blockers),
        },
        "rows": rows,
        "safety": {
            "saved_file_only": True,
            "live_fetch_attempted": False,
            "auth_used": False,
            "private_endpoints_used": False,
            "secrets_read": False,
            "places_orders": False,
            "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        },
    }


def _imported_from_report(report: dict[str, Any]) -> list[ImportedKalshiEventMetadata]:
    out: list[ImportedKalshiEventMetadata] = []
    for row in report.get("rows") or []:
        out.append(
            ImportedKalshiEventMetadata(
                source_path=row.get("source_path") or "",
                destination_path=row.get("destination_path"),
                accepted=bool(row.get("accepted")),
                blockers=list(row.get("blockers") or []),
                trusted_for_completeness=bool(row.get("trusted_for_completeness")),
                event_tickers=list(row.get("event_tickers") or []),
            )
        )
    return out


__all__ = [
    "DRY_RUN_SOURCE",
    "METADATA_IMPORT_SOURCE",
    "PAPER_SIMULATION_SKIP_NO_STOP_FOR_REVIEW",
    "PAPER_SIMULATION_SKIP_DISABLED_BY_CALLER",
    "ImportedKalshiEventMetadata",
    "import_kalshi_event_metadata_file",
    "import_kalshi_event_metadata_files",
    "render_dry_run_summary_markdown",
    "render_metadata_importer_markdown",
    "run_structural_basket_dry_run",
    "run_structural_basket_dry_run_files",
]
