"""Saved-file-only structural basket hunting workflow.

Sweep saved Kalshi market/orderbook snapshots, saved Kalshi event metadata
files, and (optionally) ``local_manifest_v1`` manifests, then tell Mason
exactly which groups are closest to a credible paper review.

Every step is saved-file-only:

* No live API call is made — every payload is read from disk.
* No order placement, no auth, no private endpoints, no secrets, no
  browser automation, no wallets.
* No ``PAPER_CANDIDATE`` is ever promoted.
* ``STOP_FOR_REVIEW`` is review/report-only; it never authorizes execution.
* The hunter NEVER infers exhaustiveness from title, ticker, market count,
  or graph hints.
* Per-market Yes/No is NOT an event-level ``outcome_list``.
* Reference-only sources (``reference_only`` / ``source_kind`` /
  ``venue_type`` reference flags) fail closed.
* No midpoint-fill assumptions: fees, ask-side, depth, and freshness gates
  in the existing detector and paper-fill simulator are reused verbatim.

The manifest template generator emits incomplete-by-default
``local_manifest_v1`` files. They contain explicit ``null`` placeholders
and ``trusted_local_manifest: false`` so :func:`validate_local_manifest_v1_group`
returns multiple blockers. They CANNOT feed ``STOP_FOR_REVIEW`` until a
human reviewer fills the placeholders and re-validates.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.exhaustive_evidence_trust import has_reference_only_flag
from relative_value.kalshi_event_metadata import (
    KALSHI_EVENT_METADATA_EVIDENCE_SOURCE,
    audit_kalshi_event_metadata,
    join_kalshi_event_metadata,
    normalize_kalshi_event_metadata_payload,
)
from relative_value.kalshi_native_groups import audit_kalshi_native_groups
from relative_value.local_manifest_v1 import (
    LOCAL_MANIFEST_SOURCE,
    MANIFEST_BLOCKERS,
    validate_local_manifest_v1_group,
)
from relative_value.paper_fill_simulator import simulate_paper_fill_journal
from relative_value.structural_basket_detector import (
    STATUS_STOP_FOR_REVIEW,
    build_structural_basket_review_report,
)

HUNTER_SOURCE = "structural_basket_hunter_v1"
MANIFEST_TEMPLATE_SOURCE = "structural_basket_hunter_template_v1"
PAPER_SIMULATION_SKIP_NO_STOP_FOR_REVIEW = "no_stop_for_review_row"
PAPER_SIMULATION_SKIP_DISABLED_BY_CALLER = "paper_simulation_disabled_by_caller"

DO_NOT_PAPER_SIMULATE_WARNING = (
    "DO NOT paper simulate yet — this row is not STOP_FOR_REVIEW. Resolve the blockers "
    "above first; the paper-fill simulator must only be invoked on rows that already "
    "cleared the structural detector's fee/depth/freshness/settlement/exhaustive-evidence gates."
)
REVIEW_ONLY_WARNING = (
    "STOP_FOR_REVIEW is review/report-only. Running the suggested saved-file paper-fill "
    "command never places orders, never authenticates, and never emits PAPER_CANDIDATE."
)

# Profit-readiness ladder — ordered from "go now" to "dead end". Operators
# should walk down this list to find the first credible saved-file paper
# review candidate without changing any trust or economic gate.
LADDER_READY_STOP_FOR_REVIEW = "READY_STOP_FOR_REVIEW"
LADDER_NEEDS_VALID_MANIFEST = "NEEDS_VALID_MANIFEST"
LADDER_NEEDS_EVENT_METADATA = "NEEDS_EVENT_METADATA"
LADDER_NEEDS_FRESH_QUOTES = "NEEDS_FRESH_QUOTES"
LADDER_NEEDS_DEPTH = "NEEDS_DEPTH"
LADDER_FEES_KILL = "FEES_KILL"
LADDER_REFERENCE_ONLY_BLOCKED = "REFERENCE_ONLY_BLOCKED"
LADDER_NOT_EXHAUSTIVE_EVIDENCE = "NOT_EXHAUSTIVE_EVIDENCE"

LADDER_ORDER: tuple[str, ...] = (
    LADDER_READY_STOP_FOR_REVIEW,
    LADDER_NEEDS_VALID_MANIFEST,
    LADDER_NEEDS_EVENT_METADATA,
    LADDER_NEEDS_FRESH_QUOTES,
    LADDER_NEEDS_DEPTH,
    LADDER_FEES_KILL,
    LADDER_REFERENCE_ONLY_BLOCKED,
    LADDER_NOT_EXHAUSTIVE_EVIDENCE,
)
_LADDER_RANK_INDEX = {status: index for index, status in enumerate(LADDER_ORDER)}

# Ordered ranking of normalized blocker categories: highest-impact first.
BLOCKER_RANK_ORDER: tuple[str, ...] = (
    "reference_only_source",
    "no_metadata_match",
    "missing_outcome_list",
    "missing_completeness_evidence",
    "missing_settlement_source",
    "missing_resolution_metadata",
    "mixed_resolution_criteria",
    "stale_orderbook",
    "insufficient_depth",
    "fees_kill",
    "manifest_required",
    "other",
)
_BLOCKER_RANK_INDEX = {category: index for index, category in enumerate(BLOCKER_RANK_ORDER)}

# Map raw blockers (from metadata audit / join / structural detector / paper
# fill / kalshi_native_groups audit) to the normalized hunter categories.
_BLOCKER_CATEGORY_MAP: dict[str, str] = {
    # Reference-only fail-closed
    "reference_only_source": "reference_only_source",
    "snapshot_reference_only_source": "reference_only_source",
    "leg_0_reference_only_source": "reference_only_source",
    "leg_1_reference_only_source": "reference_only_source",
    "leg_2_reference_only_source": "reference_only_source",
    # No metadata join
    "no_matching_event_in_snapshot": "no_metadata_match",
    "manifest_market_tickers_absent_from_snapshot": "no_metadata_match",
    "snapshot_has_markets_outside_metadata": "no_metadata_match",
    # Missing event-level outcome_list (binary Yes/No at per-market level is
    # NOT event-level outcomes)
    "missing_event_outcome_list": "missing_outcome_list",
    "per_market_binary_outcomes_only_at_event_level": "missing_outcome_list",
    "missing_outcome_list": "missing_outcome_list",
    "per_market_binary_outcomes_not_event_outcome_list": "missing_outcome_list",
    # Missing/insufficient completeness evidence
    "event_not_marked_complete": "missing_completeness_evidence",
    "missing_completeness_evidence": "missing_completeness_evidence",
    "title_only_event": "missing_completeness_evidence",
    "outcome_count_lt_two": "missing_completeness_evidence",
    "outcome_count_vs_market_count_mismatch": "missing_completeness_evidence",
    "duplicate_outcome_list_entries": "missing_completeness_evidence",
    "duplicate_market_tickers": "missing_completeness_evidence",
    "missing_event_ticker": "missing_completeness_evidence",
    "missing_event_id": "missing_completeness_evidence",
    "missing_venue_native_event_id": "missing_completeness_evidence",
    "missing_venue_native_group_id": "missing_completeness_evidence",
    "partial_event_metadata": "missing_completeness_evidence",
    "title_only_group_not_trusted": "missing_completeness_evidence",
    "range_ladder_not_exhaustive": "missing_completeness_evidence",
    "threshold_ladder_not_exhaustive": "missing_completeness_evidence",
    "not_explicitly_exhaustive": "missing_completeness_evidence",
    "explicit_exhaustive_group_incomplete": "missing_completeness_evidence",
    "explicit_exhaustive_group_member_mismatch": "missing_completeness_evidence",
    # Settlement source
    "missing_settlement_source_evidence": "missing_settlement_source",
    "missing_settlement_source": "missing_settlement_source",
    # Resolution / rules
    "missing_rules_evidence": "missing_resolution_metadata",
    "missing_resolution_metadata": "missing_resolution_metadata",
    # Mixed criteria
    "mixed_market_rules": "mixed_resolution_criteria",
    "mixed_market_times": "mixed_resolution_criteria",
    "mixed_resolution_criteria": "mixed_resolution_criteria",
    "mixed_resolution_timing": "mixed_resolution_criteria",
    "mixed_settlement_source": "mixed_resolution_criteria",
    "mixed_event_group_metadata": "mixed_resolution_criteria",
    "mixed_time_metadata": "mixed_resolution_criteria",
    # Freshness
    "stale_orderbook": "stale_orderbook",
    "stale_quote": "stale_orderbook",
    "missing_quote_timestamp": "stale_orderbook",
    # Depth
    "missing_ask_depth": "insufficient_depth",
    "insufficient_ask_depth": "insufficient_depth",
    "missing_orderbook_enrichment": "insufficient_depth",
    "missing_executable_ask": "insufficient_depth",
    "missing_depth": "insufficient_depth",
    "insufficient_depth": "insufficient_depth",
    "missing_orderbook_ask": "insufficient_depth",
    "missing_executable_depth": "insufficient_depth",
    "insufficient_executable_depth": "insufficient_depth",
    # Fees
    "fees_kill_or_no_positive_basket_gap": "fees_kill",
    "missing_fee_model": "fees_kill",
    "conservative_net_edge_not_positive": "fees_kill",
    "conservative_net_edge_below_minimum": "fees_kill",
    # Manifest gate
    "missing_explicit_exhaustive_evidence": "manifest_required",
    "exhaustive_evidence_source_not_trusted": "manifest_required",
    "venue_native_exhaustive_evidence_required": "manifest_required",
    "trusted_local_manifest_required": "manifest_required",
    "missing_exhaustive_evidence_source": "manifest_required",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def hunt_structural_basket_candidates(
    *,
    snapshot_paths: list[Path],
    metadata_paths: list[Path],
    manifest_paths: list[Path] | None = None,
    generated_at: datetime | None = None,
    max_quote_age_seconds: float = 1800.0,
    min_depth: float = 1.0,
    desired_quantity: float = 1.0,
    slippage_budget_cents_per_leg: float = 0.0,
    simulate_paper_fills_on_stop_for_review: bool = True,
    manifest_template_output_dir: Path | None = None,
    write_templates: bool = True,
    top_closest_n: int = 10,
) -> dict[str, Any]:
    """Run the saved-file hunt and return a complete report.

    Inputs are explicit file lists so the function is easy to drive from
    tests. The public CLI walks directories and forwards the discovered
    file lists here via :func:`hunt_structural_basket_candidates_files`.

    The hunter never reaches the network and never opens a private/auth
    endpoint. It also never lowers any fee/depth/freshness/settlement gate;
    it is a pure orchestration layer over the existing diagnostics.
    """
    generated = generated_at or datetime.now(timezone.utc)

    snapshot_infos = [_classify(path) for path in snapshot_paths]
    metadata_infos = [_classify(path) for path in metadata_paths]
    manifest_infos = [_classify(path) for path in (manifest_paths or [])]

    snapshot_files = [info for info in snapshot_infos if "snapshot" in info["classifications"]]
    metadata_files = [info for info in metadata_infos if "metadata" in info["classifications"]]
    manifest_files = [info for info in manifest_infos if "manifest" in info["classifications"]]
    enriched_files = [info for info in snapshot_files if "enriched_snapshot" in info["classifications"]]
    unparseable = sorted({str(info["path"]) for info in (*snapshot_infos, *metadata_infos, *manifest_infos) if info["error"]})
    unclassified = sorted(
        {
            str(info["path"])
            for info in (*snapshot_infos, *metadata_infos, *manifest_infos)
            if not info["error"] and not info["classifications"]
        }
    )

    pairings: list[dict[str, Any]] = []
    for snap in snapshot_files:
        snap_keys = _snapshot_event_keys(snap["payload"])
        # Only pair metadata/manifest files whose event/group keys intersect
        # the snapshot's event keys. If the snapshot has no identifiable
        # event keys we cannot pair anything, so we run the snapshot alone.
        matched_meta = (
            [
                meta
                for meta in metadata_files
                if _metadata_event_keys(meta["payload"]) & snap_keys
            ]
            if snap_keys
            else []
        )
        matched_manifests = (
            [
                manifest
                for manifest in manifest_files
                if _manifest_group_keys(manifest["payload"]) & snap_keys
            ]
            if snap_keys
            else []
        )
        pairings.append(
            _process_pairing(
                snapshot_info=snap,
                metadata_infos=matched_meta,
                manifest_infos=matched_manifests,
                generated_at=generated,
                max_quote_age_seconds=max_quote_age_seconds,
                min_depth=min_depth,
                desired_quantity=desired_quantity,
                slippage_budget_cents_per_leg=slippage_budget_cents_per_leg,
                simulate_paper_fills_on_stop_for_review=simulate_paper_fills_on_stop_for_review,
            )
        )

    # Templates are computed BEFORE closest_groups so each closest group can
    # carry its associated template path + current validation state. The
    # template generator itself never marks a template as trusted.
    template_suggestions: list[dict[str, Any]] = []
    if manifest_template_output_dir is not None:
        manifest_template_output_dir.mkdir(parents=True, exist_ok=True)
        template_suggestions = _maybe_write_templates(
            pairings=pairings,
            output_dir=manifest_template_output_dir,
            write_templates=write_templates,
        )

    all_entries = _collect_closest_groups(
        pairings,
        top_closest_n=None,
        template_suggestions=template_suggestions,
        max_quote_age_seconds=max_quote_age_seconds,
        min_depth=min_depth,
    )
    closest_groups = all_entries[:top_closest_n]
    missing_metadata_requirements = _collect_missing_metadata_requirements(pairings)
    raw_blocker_counts = _aggregate_raw_blocker_counts(pairings)
    top_blockers = _aggregate_normalized_blocker_categories(raw_blocker_counts)

    # Ladder counts reflect every entry across every pairing so the operator
    # can see the true totals even when top_closest_n is small. The per-rung
    # detail tables (in markdown) still draw from the top-N to stay compact.
    profit_readiness_ladder = _build_profit_readiness_ladder(
        all_entries=all_entries,
        top_entries=closest_groups,
    )
    # next_5_actions and shortest_blocker_chain walk EVERY entry so that the
    # operator's recommendation isn't accidentally truncated by --top-closest-n.
    next_5_actions = _build_next_5_actions(
        closest_groups=all_entries,
        template_suggestions=template_suggestions,
        max_quote_age_seconds=max_quote_age_seconds,
        min_depth=min_depth,
    )
    shortest_chain = _shortest_blocker_chain_to_stop_for_review(all_entries)

    next_commands = _suggest_next_commands(
        pairings=pairings,
        closest_groups=closest_groups,
        template_suggestions=template_suggestions,
    )

    ladder_counts = {status: 0 for status in LADDER_ORDER}
    for group in all_entries:
        status = group.get("profit_readiness") or LADDER_NOT_EXHAUSTIVE_EVIDENCE
        ladder_counts[status] = ladder_counts.get(status, 0) + 1

    summary = {
        "files_considered": len(snapshot_infos) + len(metadata_infos) + len(manifest_infos),
        "snapshots_considered": len(snapshot_files),
        "metadata_files_considered": len(metadata_files),
        "manifests_considered": len(manifest_files),
        "enriched_snapshots_seen": len(enriched_files),
        "unparseable_files": len(unparseable),
        "unclassified_files": len(unclassified),
        "joined_snapshots_created": sum(1 for p in pairings if p["join_summary"]["events_matched_to_snapshot"] > 0),
        "structural_groups_evaluated": sum(p["structural_summary"]["evaluated_group_count"] for p in pairings),
        "stop_for_review_count": sum(p["stop_for_review_count"] for p in pairings),
        "paper_fill_rows": sum(p["paper_fill_summary"]["input_row_count"] for p in pairings),
        "paper_fill_simulated_count": sum(p["paper_fill_summary"]["simulated_fill_count"] for p in pairings),
        "paper_fill_blocked_count": sum(p["paper_fill_summary"]["blocked_count"] for p in pairings),
        "paper_simulation_skipped_count": sum(1 for p in pairings if p["paper_simulation_skipped"]),
        "paper_candidate_count": 0,
        "manifest_templates_written": sum(1 for t in template_suggestions if t.get("written")),
        "manifest_template_suggestion_count": len(template_suggestions),
        "manifest_templates_still_invalid": sum(
            1 for t in template_suggestions if t.get("currently_invalid", True)
        ),
        "top_blocker_categories": [entry["category"] for entry in top_blockers[:5]],
        "profit_readiness_counts": ladder_counts,
        "ready_stop_for_review_count": ladder_counts.get(LADDER_READY_STOP_FOR_REVIEW, 0),
    }

    return {
        "schema_version": 1,
        "source": HUNTER_SOURCE,
        "generated_at": generated.isoformat(),
        "diagnostic_only": True,
        "review_only": True,
        "config": {
            "max_quote_age_seconds": max_quote_age_seconds,
            "min_depth": min_depth,
            "desired_quantity": desired_quantity,
            "slippage_budget_cents_per_leg": slippage_budget_cents_per_leg,
            "simulate_paper_fills_on_stop_for_review": simulate_paper_fills_on_stop_for_review,
            "manifest_template_output_dir": str(manifest_template_output_dir) if manifest_template_output_dir else None,
            "write_templates": write_templates,
            "top_closest_n": top_closest_n,
        },
        "summary": summary,
        "top_blockers": top_blockers,
        "raw_blocker_counts": [
            {"stage": stage, "blocker": blocker, "count": count}
            for (stage, blocker), count in sorted(raw_blocker_counts.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1]))
        ],
        "profit_readiness_ladder": profit_readiness_ladder,
        "next_5_actions": next_5_actions,
        "shortest_blocker_chain_to_stop_for_review": shortest_chain,
        "closest_groups_to_review": closest_groups,
        "missing_metadata_requirements": missing_metadata_requirements,
        "manifest_template_suggestions": template_suggestions,
        "next_commands": next_commands,
        "pairings": pairings,
        "files": {
            "snapshots": [_file_summary(info) for info in snapshot_files],
            "metadata": [_file_summary(info) for info in metadata_files],
            "manifests": [_file_summary(info) for info in manifest_files],
            "enriched_snapshots": [_file_summary(info) for info in enriched_files],
            "unparseable": unparseable,
            "unclassified": unclassified,
        },
        "safety": _safety_block(),
    }


def hunt_structural_basket_candidates_files(
    *,
    snapshots_dir: Path | None,
    metadata_dir: Path | None,
    manifest_dir: Path | None = None,
    json_output: Path,
    markdown_output: Path,
    manifest_template_output_dir: Path | None = None,
    write_templates: bool = True,
    generated_at: datetime | None = None,
    max_quote_age_seconds: float = 1800.0,
    min_depth: float = 1.0,
    desired_quantity: float = 1.0,
    slippage_budget_cents_per_leg: float = 0.0,
    simulate_paper_fills_on_stop_for_review: bool = True,
    top_closest_n: int = 10,
) -> dict[str, Any]:
    """File-level wrapper: walk the given directories, run the hunt, and
    write the JSON and Markdown reports.

    Both ``snapshots_dir`` and ``metadata_dir`` may point to the same
    directory; classification is done per file so a directory that mixes
    snapshots and metadata is handled correctly.
    """
    snapshot_paths = _discover_json_files(snapshots_dir)
    metadata_paths = _discover_json_files(metadata_dir) if metadata_dir != snapshots_dir else list(snapshot_paths)
    manifest_paths = _discover_json_files(manifest_dir)

    report = hunt_structural_basket_candidates(
        snapshot_paths=snapshot_paths,
        metadata_paths=metadata_paths,
        manifest_paths=manifest_paths,
        generated_at=generated_at,
        max_quote_age_seconds=max_quote_age_seconds,
        min_depth=min_depth,
        desired_quantity=desired_quantity,
        slippage_budget_cents_per_leg=slippage_budget_cents_per_leg,
        simulate_paper_fills_on_stop_for_review=simulate_paper_fills_on_stop_for_review,
        manifest_template_output_dir=manifest_template_output_dir,
        write_templates=write_templates,
        top_closest_n=top_closest_n,
    )

    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_hunter_markdown(report), encoding="utf-8")
    return report


def render_hunter_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    config = report.get("config") or {}
    lines = [
        "# Structural Basket Hunt",
        "",
        "Saved-file-only diagnostic. The hunter sweeps saved snapshots, Kalshi event "
        "metadata, and local_manifest_v1 files; runs audit + join + structural detector "
        "+ optional paper-fill simulation only on STOP_FOR_REVIEW rows. No live API, no "
        "orders, no auth, no secrets, no PAPER_CANDIDATE. STOP_FOR_REVIEW is "
        "review/report-only. Templates are INVALID by default — they will fail "
        "validate_local_manifest_v1_group until a reviewer completes them.",
        "",
        f"- generated_at: {report.get('generated_at', '')}",
        f"- max_quote_age_seconds: {config.get('max_quote_age_seconds', '')}",
        f"- min_depth: {config.get('min_depth', '')}",
        f"- desired_quantity: {config.get('desired_quantity', '')}",
        f"- slippage_budget_cents_per_leg: {config.get('slippage_budget_cents_per_leg', '')}",
        f"- manifest_template_output_dir: {config.get('manifest_template_output_dir') or '(none)'}",
        "",
        "## Counts",
        "",
        f"- files_considered: {summary.get('files_considered', 0)}",
        f"- snapshots_considered: {summary.get('snapshots_considered', 0)}",
        f"- metadata_files_considered: {summary.get('metadata_files_considered', 0)}",
        f"- manifests_considered: {summary.get('manifests_considered', 0)}",
        f"- enriched_snapshots_seen: {summary.get('enriched_snapshots_seen', 0)}",
        f"- joined_snapshots_created: {summary.get('joined_snapshots_created', 0)}",
        f"- structural_groups_evaluated: {summary.get('structural_groups_evaluated', 0)}",
        f"- stop_for_review_count: {summary.get('stop_for_review_count', 0)}",
        f"- paper_fill_rows: {summary.get('paper_fill_rows', 0)}",
        f"- paper_fill_simulated_count: {summary.get('paper_fill_simulated_count', 0)}",
        f"- paper_fill_blocked_count: {summary.get('paper_fill_blocked_count', 0)}",
        f"- paper_candidate_count: {summary.get('paper_candidate_count', 0)}",
        f"- manifest_template_suggestions: {summary.get('manifest_template_suggestion_count', 0)}",
        f"- manifest_templates_written: {summary.get('manifest_templates_written', 0)}",
        f"- manifest_templates_still_invalid: {summary.get('manifest_templates_still_invalid', 0)}",
        f"- ready_stop_for_review_count: {summary.get('ready_stop_for_review_count', 0)}",
        "",
        "## Profit-readiness ladder",
        "",
        "Rungs are ordered from \"go now\" to \"dead end\". Walk down the ladder to find the first credible "
        "STOP_FOR_REVIEW row. Rows that are NOT already STOP_FOR_REVIEW must NOT be paper-simulated.",
        "",
        "| Rank | Status | Count |",
        "|---:|---|---:|",
    ]
    for entry in report.get("profit_readiness_ladder") or []:
        lines.append(
            "| {rank} | {status} | {count} |".format(
                rank=entry.get("rank", ""),
                status=entry.get("status", ""),
                count=entry.get("count", 0),
            )
        )

    # Per-rung detail tables — only render rungs that have groups so the
    # report stays compact when only a couple of rungs are populated.
    for ladder_entry in report.get("profit_readiness_ladder") or []:
        groups = ladder_entry.get("groups") or []
        if not groups:
            continue
        status = ladder_entry.get("status", "")
        lines.extend(
            [
                "",
                f"### {status}",
                "",
                "| Group | Snapshot | Why not STOP_FOR_REVIEW | Best next action | Manifest template | Still invalid? |",
                "|---|---|---|---|---|---:|",
            ]
        )
        for group in groups:
            template_path = group.get("manifest_template_path") or "(none)"
            still_invalid = group.get("manifest_template_still_invalid")
            still_invalid_text = "true" if still_invalid else ("false" if still_invalid is not None else "(none)")
            lines.append(
                "| {grp} | {snap} | {why} | {action} | {tpl} | {still_invalid} |".format(
                    grp=str(group.get("group_id") or "")[:40].replace("|", "/"),
                    snap=_shorten(str(group.get("snapshot_path") or "")).replace("|", "/"),
                    why=str(group.get("why_not_stop_for_review") or "")[:120].replace("|", "/"),
                    action=str(group.get("best_next_action") or "")[:140].replace("|", "/"),
                    tpl=_shorten(str(template_path)).replace("|", "/"),
                    still_invalid=still_invalid_text,
                )
            )
            if group.get("do_not_paper_simulate_yet"):
                lines.append(
                    "| ⚠️ DO NOT PAPER SIMULATE YET | — | — | — | — | — |"
                )
            if status == LADDER_READY_STOP_FOR_REVIEW and group.get("paper_simulate_command"):
                lines.append(
                    "| ✅ STOP_FOR_REVIEW — exact saved-file paper-fill commands | — | — | — | — | — |"
                )
                lines.append("")
                lines.append("```bash")
                for command in group["paper_simulate_command"].get("command_lines") or []:
                    lines.append(command)
                lines.append("```")
                lines.append(group["paper_simulate_command"].get("review_only_warning") or REVIEW_ONLY_WARNING)

    chain = report.get("shortest_blocker_chain_to_stop_for_review")
    if chain:
        lines.extend(
            [
                "",
                "## Shortest blocker chain to first STOP_FOR_REVIEW",
                "",
                f"- snapshot_path: {chain.get('snapshot_path') or ''}",
                f"- group_id: {chain.get('group_id') or ''}",
                f"- profit_readiness: {chain.get('profit_readiness') or ''}",
                "",
            ]
        )
        for index, step in enumerate(chain.get("chain") or [], start=1):
            lines.append(f"{index}. {step}")
        lines.append("")
        lines.append(chain.get("warning") or DO_NOT_PAPER_SIMULATE_WARNING)
    elif summary.get("ready_stop_for_review_count", 0) == 0:
        lines.extend(
            [
                "",
                "## Shortest blocker chain to first STOP_FOR_REVIEW",
                "",
                "- (no actionable rung — all candidates are reference-only / not-exhaustive)",
                "",
                DO_NOT_PAPER_SIMULATE_WARNING,
            ]
        )

    next_actions = report.get("next_5_actions") or []
    lines.extend(
        [
            "",
            "## Next 5 actions",
            "",
        ]
    )
    if next_actions:
        for index, action in enumerate(next_actions, start=1):
            lines.append(f"{index}. **{action.get('action', '')}** — {action.get('label', '')}")
            for command in action.get("command_lines") or []:
                lines.append(f"    `{command}`")
    else:
        lines.append(
            "- (no actionable steps. Provide saved Kalshi event metadata or local_manifest_v1 manifests, "
            "or refresh stale snapshots externally and re-run.)"
        )

    if summary.get("ready_stop_for_review_count", 0) == 0:
        lines.extend(
            [
                "",
                f"> ⚠️ {DO_NOT_PAPER_SIMULATE_WARNING}",
            ]
        )

    lines.extend(
        [
            "",
            "## Top blockers (normalized)",
            "",
            "| Rank | Category | Count | Sample raw blockers |",
            "|---:|---|---:|---|",
        ]
    )
    for index, entry in enumerate(report.get("top_blockers") or [], start=1):
        samples = "; ".join(entry.get("sample_raw_blockers") or []).replace("|", "/")
        lines.append(
            f"| {index} | {entry.get('category', '')} | {entry.get('count', 0)} | {samples} |"
        )
    if not report.get("top_blockers"):
        lines.append("| (none) | (none) | 0 | (none) |")

    lines.extend(
        [
            "",
            "## Closest groups to review",
            "",
            "| Rank | Snapshot | Group | Profit readiness | Detector status | Blockers | Sum asks | Fees | Total cost | Gap | Min depth | Top blocker category |",
            "|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for index, group in enumerate(report.get("closest_groups_to_review") or [], start=1):
        snapshot = _shorten(group.get("snapshot_path") or "")
        group_id = str(group.get("group_id") or "")[:40]
        status = str(group.get("status") or "")
        readiness = str(group.get("profit_readiness") or "")
        blockers = group.get("num_blockers", 0)
        sum_asks = float(group.get("sum_asks") or 0.0)
        fees = float(group.get("conservative_fees") or 0.0)
        total = float(group.get("total_cost_after_fees") or 0.0)
        depth = float(group.get("min_posted_depth") or 0.0)
        gap_value = group.get("conservative_gap")
        gap = float(gap_value) if gap_value is not None else 0.0
        top_cat = group.get("top_blocker_category") or "(none)"
        lines.append(
            "| {idx} | {snap} | {grp} | {rd} | {st} | {b} | {sa:.4f} | {f:.4f} | {tc:.4f} | {gap:.4f} | {d:.4f} | {tc2} |".format(
                idx=index,
                snap=snapshot.replace("|", "/"),
                grp=group_id.replace("|", "/"),
                rd=readiness.replace("|", "/"),
                st=status.replace("|", "/"),
                b=blockers,
                sa=sum_asks,
                f=fees,
                tc=total,
                gap=gap,
                d=depth,
                tc2=str(top_cat).replace("|", "/"),
            )
        )
    if not report.get("closest_groups_to_review"):
        lines.append("| (none) | (none) | (none) | (none) | (none) | 0 | 0 | 0 | 0 | 0 | 0 | (none) |")

    lines.extend(
        [
            "",
            "## Missing metadata requirements",
            "",
            "| Snapshot | Group | Top missing requirement | Required next |",
            "|---|---|---|---|",
        ]
    )
    for req in report.get("missing_metadata_requirements") or []:
        lines.append(
            "| {snap} | {grp} | {req} | {nxt} |".format(
                snap=_shorten(str(req.get("snapshot_path") or "")).replace("|", "/"),
                grp=str(req.get("group_id") or "").replace("|", "/"),
                req=str(req.get("top_required_category") or "").replace("|", "/"),
                nxt=str(req.get("required_next") or "").replace("|", "/"),
            )
        )
    if not report.get("missing_metadata_requirements"):
        lines.append("| (none) | (none) | (none) | (none) |")

    lines.extend(
        [
            "",
            "## Manifest template suggestions",
            "",
            "| Group | Template path | Written | Validation blockers expected |",
            "|---|---|---:|---|",
        ]
    )
    for suggestion in report.get("manifest_template_suggestions") or []:
        blockers = "; ".join((suggestion.get("validation_blockers_expected") or [])[:6]).replace("|", "/")
        lines.append(
            "| {grp} | {path} | {wr} | {bl} |".format(
                grp=str(suggestion.get("group_id") or "").replace("|", "/"),
                path=_shorten(str(suggestion.get("template_path") or "")).replace("|", "/"),
                wr=str(bool(suggestion.get("written"))).lower(),
                bl=blockers,
            )
        )
    if not report.get("manifest_template_suggestions"):
        lines.append("| (none) | (none) | false | (none) |")

    lines.extend(["", "## Suggested next commands", "", "```bash"])
    for command in report.get("next_commands") or []:
        lines.append(command)
    if not report.get("next_commands"):
        lines.append("# (no suggested next commands)")
    lines.append("```")

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
            "- browser_automation_used: false",
            "- wallet_used: false",
            "- paper_candidate_emitted: false",
            "- stop_for_review_means_review_only: true",
            "- uses_midpoint: false",
            "- uses_title_similarity_for_exhaustiveness: false",
            "- uses_graph_hints_for_exhaustiveness: false",
            "- uses_count_only_evidence: false",
            "- infers_exhaustiveness_from_title: false",
            "- infers_exhaustiveness_from_ticker: false",
            "- infers_exhaustiveness_from_market_count: false",
            "- templates_are_valid_by_default: false",
            "- allowed_actions: WATCH, MANUAL_REVIEW, MANIFEST_REVIEW",
        ]
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Pairing and aggregation helpers
# ---------------------------------------------------------------------------


def _process_pairing(
    *,
    snapshot_info: dict[str, Any],
    metadata_infos: list[dict[str, Any]],
    manifest_infos: list[dict[str, Any]],
    generated_at: datetime,
    max_quote_age_seconds: float,
    min_depth: float,
    desired_quantity: float,
    slippage_budget_cents_per_leg: float,
    simulate_paper_fills_on_stop_for_review: bool,
) -> dict[str, Any]:
    snapshot_path = str(snapshot_info["path"])
    snapshot_payload = snapshot_info["payload"]
    metadata_payloads = [info["payload"] for info in metadata_infos]
    metadata_source_paths = [str(info["path"]) for info in metadata_infos]
    manifest_source_paths = [str(info["path"]) for info in manifest_infos]

    audit_report = audit_kalshi_event_metadata(
        metadata_payloads,
        generated_at=generated_at,
        source_paths=metadata_source_paths,
    )
    join_result = join_kalshi_event_metadata(
        snapshot_payload=snapshot_payload,
        metadata_payloads=metadata_payloads,
        generated_at=generated_at,
        snapshot_path=snapshot_path,
        source_paths=metadata_source_paths,
    )
    enriched_snapshot = join_result["enriched_snapshot"]
    join_report = join_result["report"]

    manifest_payload = _merge_manifest_payloads(manifest_infos)
    structural_report = build_structural_basket_review_report(
        snapshot_payloads=[enriched_snapshot],
        manifest_payload=manifest_payload,
        detected_at=generated_at,
        max_quote_age_seconds=max_quote_age_seconds,
        min_depth=min_depth,
    )

    structural_rows = list(structural_report.get("rows") or [])
    stop_for_review_rows = [row for row in structural_rows if row.get("status") == STATUS_STOP_FOR_REVIEW]
    paper_fill_journal: dict[str, Any] | None = None
    skip_reason: str | None = None
    if not stop_for_review_rows:
        skip_reason = PAPER_SIMULATION_SKIP_NO_STOP_FOR_REVIEW
    elif not simulate_paper_fills_on_stop_for_review:
        skip_reason = PAPER_SIMULATION_SKIP_DISABLED_BY_CALLER
    else:
        paper_fill_journal = simulate_paper_fill_journal(
            input_payload={"rows": stop_for_review_rows},
            generated_at=generated_at,
            desired_quantity=desired_quantity,
            max_quote_age_seconds=max_quote_age_seconds,
            slippage_budget_cents_per_leg=slippage_budget_cents_per_leg,
        )

    # Run the native group audit on the ORIGINAL snapshot so we can show
    # candidate groups that the detector couldn't evaluate (no metadata
    # match, no native completeness markers, no manifest). The detector ran
    # on the ENRICHED snapshot; we don't want to double-count those.
    native_audit = audit_kalshi_native_groups(snapshot_payload, generated_at=generated_at)
    detector_group_ids = {
        str(row.get("group_id"))
        for row in structural_rows
        if row.get("group_id") is not None
    }
    native_candidates: list[dict[str, Any]] = []
    for group in native_audit.get("groups") or []:
        group_id = group.get("venue_native_group_id")
        if not group_id or str(group_id) in detector_group_ids:
            continue
        if group.get("status") == "COMPLETE_EXHAUSTIVE_GROUP":
            continue
        # Ignore title-only buckets — they are not credible groups.
        if isinstance(group_id, str) and group_id.startswith("title_only:"):
            continue
        native_candidates.append(group)

    return {
        "snapshot_path": snapshot_path,
        "metadata_paths": metadata_source_paths,
        "manifest_paths": manifest_source_paths,
        "audit_summary": dict(audit_report.get("summary") or {}),
        "join_summary": dict(join_report.get("summary") or {}),
        "structural_summary": dict(structural_report.get("summary") or {}),
        "structural_rows": structural_rows,
        "native_candidates": native_candidates,
        "paper_fill_summary": dict(paper_fill_journal["summary"]) if paper_fill_journal else _zero_paper_fill_summary(),
        "paper_fill_journal": paper_fill_journal,
        "paper_simulation_skipped": paper_fill_journal is None,
        "paper_simulation_skip_reason": skip_reason,
        "stop_for_review_count": len(stop_for_review_rows),
        "audit_report_blocker_counts": dict(audit_report.get("summary", {}).get("blocker_counts") or {}),
        "join_rows": list(join_report.get("rows") or []),
    }


def _zero_paper_fill_summary() -> dict[str, Any]:
    return {
        "input_row_count": 0,
        "simulated_fill_count": 0,
        "blocked_count": 0,
        "paper_candidate_count_created": 0,
        "status_counts": {},
    }


def _merge_manifest_payloads(manifest_infos: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not manifest_infos:
        return None
    groups: list[dict[str, Any]] = []
    for info in manifest_infos:
        payload = info.get("payload") or {}
        for key in ("exhaustive_groups", "trusted_exhaustive_groups", "groups"):
            raw = payload.get(key)
            if isinstance(raw, list):
                for group in raw:
                    if isinstance(group, dict):
                        groups.append(group)
    if not groups:
        return None
    return {"exhaustive_groups": groups}


def _collect_closest_groups(
    pairings: list[dict[str, Any]],
    *,
    top_closest_n: int | None,
    template_suggestions: list[dict[str, Any]] | None = None,
    max_quote_age_seconds: float = 1800.0,
    min_depth: float = 1.0,
) -> list[dict[str, Any]]:
    template_lookup = _build_template_lookup(template_suggestions or [])
    entries: list[dict[str, Any]] = []
    for pairing in pairings:
        snapshot_path = pairing["snapshot_path"]
        for row in pairing.get("structural_rows") or []:
            blockers = list(row.get("blockers") or [])
            top_category = _top_blocker_category(blockers)
            market_tickers = [
                outcome.get("ticker") or outcome.get("market_id")
                for outcome in (row.get("outcomes") or [])
                if isinstance(outcome, dict)
            ]
            entries.append(
                {
                    "kind": "detector_row",
                    "snapshot_path": snapshot_path,
                    "metadata_paths": list(pairing.get("metadata_paths") or []),
                    "manifest_paths": list(pairing.get("manifest_paths") or []),
                    "venue": row.get("venue"),
                    "group_id": row.get("group_id"),
                    "market_tickers": [t for t in market_tickers if t],
                    "status": row.get("status"),
                    "blockers": blockers,
                    "num_blockers": len(blockers),
                    "sum_asks": row.get("sum_asks"),
                    "conservative_fees": row.get("conservative_fees"),
                    "total_cost_after_fees": row.get("total_cost_after_fees"),
                    "min_posted_depth": row.get("min_posted_depth"),
                    "max_quote_age_seconds": row.get("max_quote_age_seconds"),
                    "evidence_source": (row.get("evidence") or {}).get("source"),
                    "venue_native": bool((row.get("evidence") or {}).get("venue_native")),
                    "stop_for_review": row.get("status") == STATUS_STOP_FOR_REVIEW,
                    "top_blocker_category": top_category,
                }
            )
        for group in pairing.get("native_candidates") or []:
            blockers = list(group.get("blockers") or [])
            top_category = _top_blocker_category(blockers + ["manifest_required"])
            entries.append(
                {
                    "kind": "needs_manifest_or_metadata",
                    "snapshot_path": snapshot_path,
                    "metadata_paths": list(pairing.get("metadata_paths") or []),
                    "manifest_paths": list(pairing.get("manifest_paths") or []),
                    "venue": group.get("venue"),
                    "group_id": group.get("venue_native_group_id"),
                    "market_tickers": [
                        market.get("market_ticker")
                        for market in (group.get("markets") or [])
                        if isinstance(market, dict) and market.get("market_ticker")
                    ],
                    "status": group.get("status") or "NEEDS_METADATA",
                    "blockers": blockers,
                    "num_blockers": len(blockers),
                    "market_count": group.get("market_count"),
                    "shared_rules": group.get("shared_rules"),
                    "shared_times": group.get("shared_times"),
                    "outcome_list_source": group.get("outcome_list_source"),
                    "trusted_local_manifest_complete": group.get("trusted_local_manifest_complete"),
                    "sum_asks": None,
                    "conservative_fees": None,
                    "total_cost_after_fees": None,
                    "min_posted_depth": None,
                    "max_quote_age_seconds": None,
                    "evidence_source": None,
                    "venue_native": False,
                    "stop_for_review": False,
                    "top_blocker_category": top_category,
                }
            )
    for entry in entries:
        _enrich_entry_with_readiness(
            entry,
            template_lookup=template_lookup,
            max_quote_age_seconds=max_quote_age_seconds,
            min_depth=min_depth,
        )
    entries.sort(key=_closeness_key)
    if top_closest_n is None:
        return entries
    return entries[:top_closest_n]


def _closeness_key(entry: dict[str, Any]) -> tuple[Any, ...]:
    ladder_rank = _LADDER_RANK_INDEX.get(
        entry.get("profit_readiness") or LADDER_NOT_EXHAUSTIVE_EVIDENCE,
        len(LADDER_ORDER),
    )
    detector_rank = 0 if entry.get("kind") == "detector_row" else 1
    blocker_count = int(entry.get("num_blockers") or 0)
    total_cost = float(entry.get("total_cost_after_fees") or 1.0)
    min_depth = float(entry.get("min_posted_depth") or 0.0)
    quote_age = float(entry.get("max_quote_age_seconds") or 999999.0)
    group_id = str(entry.get("group_id") or "")
    return (ladder_rank, detector_rank, blocker_count, total_cost, -min_depth, quote_age, group_id)


def _build_template_lookup(suggestions: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for suggestion in suggestions:
        key = (
            str(suggestion.get("snapshot_path") or ""),
            str(suggestion.get("group_id") or ""),
        )
        lookup[key] = suggestion
    return lookup


def _enrich_entry_with_readiness(
    entry: dict[str, Any],
    *,
    template_lookup: dict[tuple[str, str], dict[str, Any]],
    max_quote_age_seconds: float,
    min_depth: float,
) -> None:
    """Attach profit_readiness ladder status + supporting operator fields.

    This is reporting-only. It does NOT change any economic, trust, or
    freshness gate; it only translates the detector's existing output into
    an operator checklist.
    """
    template_key = (
        str(entry.get("snapshot_path") or ""),
        str(entry.get("group_id") or ""),
    )
    template = template_lookup.get(template_key)
    entry["manifest_template_exists"] = bool(template and template.get("template_exists"))
    entry["manifest_template_path"] = template.get("template_path") if template else None
    entry["manifest_template_still_invalid"] = bool(template and template.get("currently_invalid", True))
    entry["required_template_fields"] = list(template.get("required_template_fields") or []) if template else []

    entry["quote_freshness_summary"] = _quote_freshness_summary(entry, max_quote_age_seconds)
    entry["depth_summary"] = _depth_summary(entry, min_depth)
    entry["conservative_gap"] = _conservative_gap(entry)

    profit_readiness = _compute_profit_readiness(entry, template_exists=entry["manifest_template_exists"])
    entry["profit_readiness"] = profit_readiness
    entry["profit_readiness_rank"] = _LADDER_RANK_INDEX.get(profit_readiness, len(LADDER_ORDER))

    entry["why_not_stop_for_review"] = _why_not_stop_for_review(entry, profit_readiness)
    entry["best_next_action"] = _best_next_action(entry, profit_readiness)
    entry["do_not_paper_simulate_yet"] = profit_readiness != LADDER_READY_STOP_FOR_REVIEW
    entry["do_not_paper_simulate_yet_reason"] = (
        DO_NOT_PAPER_SIMULATE_WARNING if profit_readiness != LADDER_READY_STOP_FOR_REVIEW else None
    )
    entry["paper_simulate_command"] = _paper_simulate_command(entry) if profit_readiness == LADDER_READY_STOP_FOR_REVIEW else None
    entry["dry_run_command"] = _dry_run_command(entry)


def _compute_profit_readiness(entry: dict[str, Any], *, template_exists: bool) -> str:
    """Map a closest-group entry to one of the 8 ladder rungs.

    Reference-only flags take precedence (fail closed). STOP_FOR_REVIEW is
    next (READY). Then explicit detector statuses for stale/depth/fees.
    Manifest blockers prefer NEEDS_VALID_MANIFEST when a template has been
    written; otherwise NEEDS_EVENT_METADATA so the operator either acquires
    metadata or completes a manifest. NOT_EXHAUSTIVE_EVIDENCE is the
    fallback when nothing more specific fits.
    """
    blockers = list(entry.get("blockers") or [])
    normalized = {_normalize_blocker(blocker) for blocker in blockers}

    if "reference_only_source" in normalized:
        return LADDER_REFERENCE_ONLY_BLOCKED
    if entry.get("stop_for_review"):
        return LADDER_READY_STOP_FOR_REVIEW

    status = str(entry.get("status") or "").upper()
    if status == "STALE_ORDERBOOK" or "stale_orderbook" in normalized:
        return LADDER_NEEDS_FRESH_QUOTES
    if status in {"INSUFFICIENT_DEPTH", "MISSING_ORDERBOOK"} or "insufficient_depth" in normalized:
        return LADDER_NEEDS_DEPTH
    if status == "FEES_KILL" or "fees_kill" in normalized:
        return LADDER_FEES_KILL

    if "manifest_required" in normalized:
        return LADDER_NEEDS_VALID_MANIFEST

    kind = entry.get("kind")
    if kind == "needs_manifest_or_metadata":
        # Template already staged → completing it is the shortest path; otherwise
        # importing event metadata is the lighter-trust path.
        return LADDER_NEEDS_VALID_MANIFEST if template_exists else LADDER_NEEDS_EVENT_METADATA

    if status == "NOT_EXHAUSTIVE_EVIDENCE":
        return LADDER_NEEDS_VALID_MANIFEST if template_exists else LADDER_NEEDS_EVENT_METADATA

    return LADDER_NOT_EXHAUSTIVE_EVIDENCE


def _why_not_stop_for_review(entry: dict[str, Any], profit_readiness: str) -> str | None:
    if profit_readiness == LADDER_READY_STOP_FOR_REVIEW:
        return None
    return {
        LADDER_REFERENCE_ONLY_BLOCKED: (
            "Reference-only source (reference_only / source_kind=reference / venue_type=reference_only) — "
            "fail closed; not a tradable leg."
        ),
        LADDER_NEEDS_FRESH_QUOTES: (
            "Orderbook quotes are stale (or missing a captured_at). The detector requires every leg's "
            "quote_age to stay under max_quote_age_seconds."
        ),
        LADDER_NEEDS_DEPTH: (
            "Posted top-of-book depth is below min_depth on at least one leg. Refresh the snapshot when "
            "the book is deeper."
        ),
        LADDER_FEES_KILL: (
            "Sum of asks plus conservative Kalshi fees reaches or exceeds 1.0 — no positive structural "
            "basket gap is available right now."
        ),
        LADDER_NEEDS_VALID_MANIFEST: (
            "Group lacks trusted exhaustive evidence. A local_manifest_v1 manifest must be completed "
            "(reviewer-validated, every placeholder replaced) before the detector can promote it."
        ),
        LADDER_NEEDS_EVENT_METADATA: (
            "Group lacks trusted exhaustive evidence and no manifest template has been staged. Acquire a "
            "saved Kalshi event metadata JSON and re-run import-kalshi-event-metadata."
        ),
        LADDER_NOT_EXHAUSTIVE_EVIDENCE: (
            "Structural detector reported a generic NOT_EXHAUSTIVE_EVIDENCE blocker that does not map to "
            "a single ladder rung. Inspect the per-pairing report."
        ),
    }.get(profit_readiness, "Not STOP_FOR_REVIEW. Inspect the per-pairing report for blockers.")


def _best_next_action(entry: dict[str, Any], profit_readiness: str) -> str:
    template_path = entry.get("manifest_template_path")
    snapshot_path = entry.get("snapshot_path") or ""
    metadata_paths = entry.get("metadata_paths") or []
    if profit_readiness == LADDER_READY_STOP_FOR_REVIEW:
        return (
            "Run the saved-file paper-fill simulator on this row (review-only — no orders are placed)."
        )
    if profit_readiness == LADDER_REFERENCE_ONLY_BLOCKED:
        return "Skip — reference-only sources are not executable legs; fail closed."
    if profit_readiness == LADDER_NEEDS_FRESH_QUOTES:
        return f"Refresh the saved snapshot at {snapshot_path} so every leg's quote_age < max_quote_age_seconds, then re-run hunt-structural-basket-candidates."
    if profit_readiness == LADDER_NEEDS_DEPTH:
        return f"Wait for deeper top-of-book on {snapshot_path}; re-run hunt-structural-basket-candidates after refreshing the snapshot."
    if profit_readiness == LADDER_FEES_KILL:
        return "Wait for a wider basket gap (or skip this group); conservative Kalshi fees absorb the current gap."
    if profit_readiness == LADDER_NEEDS_VALID_MANIFEST:
        if template_path:
            return (
                f"Complete the staged manifest template at {template_path}: fill reviewer, reviewed_at, "
                "evidence_text, settlement_source_evidence, rules_evidence, outcome_list; set complete=true "
                "and trusted_local_manifest=true; then re-run hunt-structural-basket-candidates with --manifest-dir."
            )
        return (
            "Write a local_manifest_v1 manifest for this group (reviewer-validated, explicit evidence) and "
            "re-run hunt-structural-basket-candidates with --manifest-dir."
        )
    if profit_readiness == LADDER_NEEDS_EVENT_METADATA:
        meta_paths = ", ".join(metadata_paths) or "(none paired yet)"
        return (
            f"Import explicit Kalshi event metadata for {entry.get('group_id') or '<group>'} (paired metadata: {meta_paths}) "
            "via `python scan.py import-kalshi-event-metadata --source <saved_event_metadata.json> --destination-dir reports/kalshi_event_metadata`, "
            "then re-run hunt-structural-basket-candidates."
        )
    return "Inspect the per-pairing report for the underlying blockers."


def _quote_freshness_summary(entry: dict[str, Any], max_quote_age_seconds: float) -> dict[str, Any]:
    age = entry.get("max_quote_age_seconds")
    return {
        "max_quote_age_seconds": age,
        "max_quote_age_threshold": max_quote_age_seconds,
        "fresh": (age is not None and age <= max_quote_age_seconds),
    }


def _depth_summary(entry: dict[str, Any], min_depth: float) -> dict[str, Any]:
    depth = entry.get("min_posted_depth")
    return {
        "min_posted_depth": depth,
        "min_depth_threshold": min_depth,
        "sufficient": (depth is not None and depth >= min_depth),
    }


def _conservative_gap(entry: dict[str, Any]) -> float | None:
    total = entry.get("total_cost_after_fees")
    if total is None:
        return None
    try:
        return round(max(0.0, 1.0 - float(total)), 6)
    except (TypeError, ValueError):
        return None


def _dry_run_command(entry: dict[str, Any]) -> str:
    snap = _shell_quote(entry.get("snapshot_path") or "")
    metas = entry.get("metadata_paths") or []
    if metas:
        meta_args = " ".join(f"--metadata {_shell_quote(p)}" for p in metas)
    else:
        meta_args = "--metadata <saved_event_metadata.json>"
    return (
        f"python scan.py run-structural-basket-dry-run --snapshot {snap} {meta_args}".strip()
    )


def _paper_simulate_command(entry: dict[str, Any]) -> dict[str, Any]:
    """Build the literal saved-file simulate-paper-fills command pair.

    The first command writes the structural sub-report; the second runs
    simulate-paper-fills against that file. Both commands are saved-file
    diagnostics. Neither places orders, neither authenticates, neither
    emits PAPER_CANDIDATE — the safety block + the simulator's gating logic
    enforces this.
    """
    snap = _shell_quote(entry.get("snapshot_path") or "")
    metas = entry.get("metadata_paths") or []
    meta_args = " ".join(f"--metadata {_shell_quote(p)}" for p in metas) or "--metadata <saved_event_metadata.json>"
    safe = _safe_command_token(entry)
    structural_json = f"reports/{safe}_structural.json"
    structural_md = f"reports/{safe}_structural.md"
    paper_json = f"reports/{safe}_paper_fill_journal.json"
    paper_md = f"reports/{safe}_paper_fill_journal.md"
    return {
        "review_only_warning": REVIEW_ONLY_WARNING,
        "command_lines": [
            (
                f"python scan.py run-structural-basket-dry-run --snapshot {snap} {meta_args} "
                f"--structural-json-output {structural_json} --structural-markdown-output {structural_md}"
            ),
            (
                f"python scan.py simulate-paper-fills --input {structural_json} "
                f"--json-output {paper_json} --markdown-output {paper_md}"
            ),
        ],
        "saved_file_only": True,
        "places_orders": False,
        "paper_candidate_emitted": False,
    }


def _safe_command_token(entry: dict[str, Any]) -> str:
    snap_stem = Path(entry.get("snapshot_path") or "snapshot").stem
    group = str(entry.get("group_id") or "group")
    raw = f"{snap_stem}__{group}"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("_").lower()
    if not safe:
        safe = "structural_basket"
    if len(safe) > 80:
        safe = safe[:80].rstrip("_")
    return safe


def _build_profit_readiness_ladder(
    *,
    all_entries: list[dict[str, Any]],
    top_entries: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build the per-rung ladder.

    ``count`` reflects EVERY entry across all pairings, so the operator sees
    true totals even when ``top_closest_n`` is small. ``groups`` (the
    per-rung detail rows) is drawn from ``top_entries`` (the visible
    closest-groups list) so the report stays compact.
    """
    top_entries = top_entries if top_entries is not None else all_entries
    full_counts: dict[str, int] = {status: 0 for status in LADDER_ORDER}
    for entry in all_entries:
        status = entry.get("profit_readiness") or LADDER_NOT_EXHAUSTIVE_EVIDENCE
        full_counts[status] = full_counts.get(status, 0) + 1
    grouped: dict[str, list[dict[str, Any]]] = {status: [] for status in LADDER_ORDER}
    for entry in top_entries:
        status = entry.get("profit_readiness") or LADDER_NOT_EXHAUSTIVE_EVIDENCE
        grouped.setdefault(status, []).append(entry)
    ladder: list[dict[str, Any]] = []
    for status in LADDER_ORDER:
        rows = grouped.get(status) or []
        ladder.append(
            {
                "status": status,
                "rank": _LADDER_RANK_INDEX[status],
                "count": full_counts.get(status, 0),
                "visible_count": len(rows),
                "groups": [
                    {
                        "snapshot_path": row.get("snapshot_path"),
                        "group_id": row.get("group_id"),
                        "market_tickers": list(row.get("market_tickers") or []),
                        "current_status": row.get("status"),
                        "why_not_stop_for_review": row.get("why_not_stop_for_review"),
                        "best_next_action": row.get("best_next_action"),
                        "blockers": list(row.get("blockers") or []),
                        "manifest_template_path": row.get("manifest_template_path"),
                        "manifest_template_exists": row.get("manifest_template_exists"),
                        "manifest_template_still_invalid": row.get("manifest_template_still_invalid"),
                        "required_template_fields": list(row.get("required_template_fields") or []),
                        "quote_freshness_summary": row.get("quote_freshness_summary"),
                        "depth_summary": row.get("depth_summary"),
                        "sum_asks": row.get("sum_asks"),
                        "conservative_fees": row.get("conservative_fees"),
                        "total_cost_after_fees": row.get("total_cost_after_fees"),
                        "conservative_gap": row.get("conservative_gap"),
                        "do_not_paper_simulate_yet": row.get("do_not_paper_simulate_yet"),
                        "do_not_paper_simulate_yet_reason": row.get("do_not_paper_simulate_yet_reason"),
                        "paper_simulate_command": row.get("paper_simulate_command"),
                    }
                    for row in rows
                ],
            }
        )
    return ladder


def _build_next_5_actions(
    *,
    closest_groups: list[dict[str, Any]],
    template_suggestions: list[dict[str, Any]],
    max_quote_age_seconds: float,
    min_depth: float,
) -> list[dict[str, Any]]:
    """Pick up to 5 concrete operator actions, balancing diversity with
    closeness.

    Pass 1: walk the ladder in rank order and emit AT MOST 2 actions per
    rung so different action types (complete manifest, import metadata,
    refresh snapshot, skip dead group) all surface even when one rung
    dominates the top-N list.

    Pass 2: backfill remaining slots from the highest-rank rung's leftover
    entries so the list always has 5 actions when there's enough work to do.
    """
    actions: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    per_rung_emitted: dict[str, int] = {}
    pass_1_cap = 2

    def _make_action(entry: dict[str, Any]) -> dict[str, Any] | None:
        readiness = entry.get("profit_readiness")
        if readiness == LADDER_READY_STOP_FOR_REVIEW:
            return {
                "action": "run_paper_simulate",
                "label": "Paper-simulate this STOP_FOR_REVIEW row (saved-file, no orders)",
                "target": entry.get("group_id") or "",
                "snapshot_path": entry.get("snapshot_path"),
                "command_lines": (entry.get("paper_simulate_command") or {}).get("command_lines") or [],
                "review_only_warning": REVIEW_ONLY_WARNING,
            }
        if readiness == LADDER_NEEDS_VALID_MANIFEST:
            template_path = entry.get("manifest_template_path") or "<template.json>"
            template_dir = Path(entry.get("manifest_template_path") or "reports/manifest_templates").parent
            return {
                "action": "complete_manifest_template",
                "label": f"Complete manifest template for {entry.get('group_id') or '<group>'}",
                "target": entry.get("manifest_template_path") or entry.get("group_id") or "",
                "snapshot_path": entry.get("snapshot_path"),
                "manifest_template_path": entry.get("manifest_template_path"),
                "required_template_fields": list(entry.get("required_template_fields") or []),
                "command_lines": [
                    f"# Edit {template_path} — fill placeholders, set complete=true and trusted_local_manifest=true",
                    f"python scan.py hunt-structural-basket-candidates --snapshots-dir reports --metadata-dir reports --manifest-dir {template_dir}",
                ],
            }
        if readiness == LADDER_NEEDS_EVENT_METADATA:
            return {
                "action": "import_event_metadata",
                "label": f"Import Kalshi event metadata for {entry.get('group_id') or '<group>'}",
                "target": entry.get("group_id") or "",
                "snapshot_path": entry.get("snapshot_path"),
                "command_lines": [
                    "python scan.py import-kalshi-event-metadata --source <saved_event_metadata.json> --destination-dir reports/kalshi_event_metadata",
                    "python scan.py hunt-structural-basket-candidates --snapshots-dir reports --metadata-dir reports/kalshi_event_metadata",
                ],
            }
        if readiness == LADDER_NEEDS_FRESH_QUOTES:
            return {
                "action": "refresh_saved_snapshot",
                "label": f"Refresh saved snapshot {entry.get('snapshot_path') or ''} (operator runs fetch outside hunt-structural-basket-candidates)",
                "target": entry.get("snapshot_path") or "",
                "snapshot_path": entry.get("snapshot_path"),
                "command_lines": [
                    "# Refresh the saved snapshot externally — hunt-structural-basket-candidates is saved-file-only and never re-fetches.",
                    "python scan.py hunt-structural-basket-candidates --snapshots-dir reports --metadata-dir reports",
                ],
            }
        if readiness == LADDER_NEEDS_DEPTH:
            return {
                "action": "wait_for_depth",
                "label": f"Wait for deeper top-of-book on {entry.get('snapshot_path') or ''}; re-run hunter after refresh",
                "target": entry.get("snapshot_path") or "",
                "snapshot_path": entry.get("snapshot_path"),
                "command_lines": [
                    "# Wait until min depth >= min_depth on every leg, then refresh the saved snapshot externally.",
                    "python scan.py hunt-structural-basket-candidates --snapshots-dir reports --metadata-dir reports",
                ],
            }
        if readiness == LADDER_FEES_KILL:
            return {
                "action": "skip_fees_kill_group",
                "label": f"Skip {entry.get('group_id') or '<group>'} — conservative fees kill the current gap",
                "target": entry.get("group_id") or "",
                "snapshot_path": entry.get("snapshot_path"),
                "command_lines": [
                    "# Skip this group for now; re-run hunter when basket asks tighten.",
                ],
            }
        if readiness == LADDER_REFERENCE_ONLY_BLOCKED:
            return {
                "action": "skip_reference_only_group",
                "label": f"Skip {entry.get('group_id') or '<group>'} — reference-only source fails closed",
                "target": entry.get("group_id") or "",
                "snapshot_path": entry.get("snapshot_path"),
                "command_lines": [
                    "# Skip permanently — reference-only sources are not executable legs.",
                ],
            }
        return None

    def _push(action: dict[str, Any], readiness: str) -> bool:
        key = (action.get("action") or "", action.get("target") or "")
        if key in seen or len(actions) >= 5:
            return False
        seen.add(key)
        actions.append(action)
        per_rung_emitted[readiness] = per_rung_emitted.get(readiness, 0) + 1
        return True

    # Pass 1: at most pass_1_cap actions per rung, in ladder order.
    for status in LADDER_ORDER:
        if len(actions) >= 5:
            break
        for entry in closest_groups:
            if len(actions) >= 5:
                break
            if entry.get("profit_readiness") != status:
                continue
            if per_rung_emitted.get(status, 0) >= pass_1_cap:
                break
            action = _make_action(entry)
            if action:
                _push(action, status)

    # Pass 2: fill remaining slots from the highest-rank rung first.
    for entry in closest_groups:
        if len(actions) >= 5:
            break
        readiness = entry.get("profit_readiness") or ""
        action = _make_action(entry)
        if action:
            _push(action, readiness)

    return actions


def _shortest_blocker_chain_to_stop_for_review(
    closest_groups: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Pick the highest-rank non-READY entry and translate its readiness
    status into a 1-3 step chain to reach STOP_FOR_REVIEW.

    Returns ``None`` when at least one READY entry already exists (the
    operator should paper-simulate that row first).
    """
    if any(entry.get("profit_readiness") == LADDER_READY_STOP_FOR_REVIEW for entry in closest_groups):
        return None
    for entry in closest_groups:
        readiness = entry.get("profit_readiness")
        if not readiness or readiness in {LADDER_REFERENCE_ONLY_BLOCKED, LADDER_NOT_EXHAUSTIVE_EVIDENCE}:
            continue
        chain = _chain_for_readiness(readiness, entry)
        if chain:
            return {
                "snapshot_path": entry.get("snapshot_path"),
                "group_id": entry.get("group_id"),
                "profit_readiness": readiness,
                "chain": chain,
                "warning": DO_NOT_PAPER_SIMULATE_WARNING,
            }
    return None


def _chain_for_readiness(readiness: str, entry: dict[str, Any]) -> list[str]:
    if readiness == LADDER_NEEDS_VALID_MANIFEST:
        return [
            f"Complete manifest template {entry.get('manifest_template_path') or '<template.json>'} (reviewer-validated).",
            "Re-run hunt-structural-basket-candidates with --manifest-dir pointing at the completed template's parent directory.",
            "Confirm the structural detector promotes the group to STOP_FOR_REVIEW (review-only).",
        ]
    if readiness == LADDER_NEEDS_EVENT_METADATA:
        return [
            f"Acquire saved Kalshi event metadata JSON for group {entry.get('group_id') or '<group>'}.",
            "Run `python scan.py import-kalshi-event-metadata --source <saved.json> --destination-dir reports/kalshi_event_metadata`.",
            "Re-run hunt-structural-basket-candidates with --metadata-dir reports/kalshi_event_metadata.",
        ]
    if readiness == LADDER_NEEDS_FRESH_QUOTES:
        return [
            f"Refresh the saved snapshot at {entry.get('snapshot_path') or '<snapshot>'} (operator-run fetch outside this command).",
            "Re-run hunt-structural-basket-candidates against the refreshed snapshot.",
            "Confirm STOP_FOR_REVIEW once quote_age < max_quote_age_seconds for every leg.",
        ]
    if readiness == LADDER_NEEDS_DEPTH:
        return [
            f"Wait for deeper top-of-book on {entry.get('snapshot_path') or '<snapshot>'} (min depth on every leg).",
            "Refresh the saved snapshot externally, then re-run hunt-structural-basket-candidates.",
            "Confirm STOP_FOR_REVIEW once min_posted_depth >= min_depth on every leg.",
        ]
    if readiness == LADDER_FEES_KILL:
        return [
            f"Skip {entry.get('group_id') or '<group>'} until basket asks tighten enough to beat conservative fees.",
            "Re-run hunt-structural-basket-candidates after the next snapshot refresh.",
        ]
    return []


def _collect_missing_metadata_requirements(pairings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []
    for pairing in pairings:
        snapshot_path = pairing["snapshot_path"]
        for group in pairing.get("native_candidates") or []:
            blockers = list(group.get("blockers") or [])
            top_category = _top_blocker_category(blockers + ["manifest_required"])
            requirements.append(
                {
                    "snapshot_path": snapshot_path,
                    "group_id": group.get("venue_native_group_id"),
                    "blockers": blockers,
                    "top_required_category": top_category,
                    "required_next": _required_next_text(top_category),
                }
            )
        for join_row in pairing.get("join_rows") or []:
            if join_row.get("trusted_for_completeness_after_join"):
                continue
            blockers = list(join_row.get("join_blockers") or [])
            top_category = _top_blocker_category(blockers)
            requirements.append(
                {
                    "snapshot_path": snapshot_path,
                    "group_id": join_row.get("event_ticker") or join_row.get("event_id"),
                    "blockers": blockers,
                    "top_required_category": top_category,
                    "required_next": _required_next_text(top_category),
                }
            )
    # Dedupe by (snapshot_path, group_id, top_required_category)
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for req in requirements:
        key = (
            str(req.get("snapshot_path") or ""),
            str(req.get("group_id") or ""),
            str(req.get("top_required_category") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(req)
    return deduped


def _required_next_text(category: str) -> str:
    return {
        "reference_only_source": "Source is reference-only; do not promote — fail closed.",
        "no_metadata_match": "Acquire and validate Kalshi event metadata that matches the snapshot event keys.",
        "missing_outcome_list": "Provide an explicit event-level outcome_list (per-market Yes/No is not enough).",
        "missing_completeness_evidence": "Provide explicit complete / is_exhaustive / all_outcomes_included markers.",
        "missing_settlement_source": "Provide settlement_source_raw_evidence in the metadata.",
        "missing_resolution_metadata": "Provide rules_primary or rules_secondary in the metadata.",
        "mixed_resolution_criteria": "Resolve mixed rules/times across markets in the snapshot or metadata.",
        "stale_orderbook": "Refresh the saved snapshot so quote_age < max_quote_age_seconds.",
        "insufficient_depth": "Refresh the snapshot when depth >= min_depth on every leg.",
        "fees_kill": "Wait for a wider basket — the current basket cannot beat conservative fees.",
        "manifest_required": "Either supply a trusted local_manifest_v1 or import Kalshi event metadata.",
        "other": "Inspect the per-pairing report for the underlying blockers.",
    }.get(category, "Inspect the per-pairing report for the underlying blockers.")


def _aggregate_raw_blocker_counts(pairings: list[dict[str, Any]]) -> Counter:
    counter: Counter = Counter()
    for pairing in pairings:
        for blocker, count in (pairing.get("audit_report_blocker_counts") or {}).items():
            counter[("metadata_audit", blocker)] += int(count or 0)
        for join_row in pairing.get("join_rows") or []:
            for blocker in join_row.get("join_blockers") or []:
                counter[("metadata_join", blocker)] += 1
        for row in pairing.get("structural_rows") or []:
            for blocker in row.get("blockers") or []:
                counter[("structural_basket", blocker)] += 1
        for group in pairing.get("native_candidates") or []:
            for blocker in group.get("blockers") or []:
                counter[("kalshi_native_groups", blocker)] += 1
        journal = pairing.get("paper_fill_journal")
        if isinstance(journal, dict):
            for row in journal.get("journal") or []:
                for blocker in row.get("blockers") or []:
                    counter[("paper_fill", blocker)] += 1
    return counter


def _aggregate_normalized_blocker_categories(raw_counts: Counter) -> list[dict[str, Any]]:
    by_category: dict[str, dict[str, Any]] = {}
    for (stage, blocker), count in raw_counts.items():
        category = _normalize_blocker(blocker)
        entry = by_category.setdefault(
            category,
            {"category": category, "count": 0, "raw_blockers": set(), "stages": set()},
        )
        entry["count"] += count
        entry["raw_blockers"].add(blocker)
        entry["stages"].add(stage)
    output: list[dict[str, Any]] = []
    for entry in by_category.values():
        raw = sorted(entry["raw_blockers"])
        stages = sorted(entry["stages"])
        output.append(
            {
                "category": entry["category"],
                "count": entry["count"],
                "rank": _BLOCKER_RANK_INDEX.get(entry["category"], len(BLOCKER_RANK_ORDER)),
                "sample_raw_blockers": raw[:6],
                "stages": stages,
            }
        )
    output.sort(key=lambda item: (item["rank"], -item["count"], item["category"]))
    return output


def _top_blocker_category(blockers: list[str]) -> str | None:
    if not blockers:
        return None
    best: str | None = None
    best_rank = len(BLOCKER_RANK_ORDER) + 1
    for blocker in blockers:
        category = _normalize_blocker(blocker)
        rank = _BLOCKER_RANK_INDEX.get(category, len(BLOCKER_RANK_ORDER))
        if rank < best_rank:
            best = category
            best_rank = rank
    return best


def _normalize_blocker(blocker: str) -> str:
    if not isinstance(blocker, str):
        return "other"
    if blocker in _BLOCKER_CATEGORY_MAP:
        return _BLOCKER_CATEGORY_MAP[blocker]
    if blocker in MANIFEST_BLOCKERS:
        return "manifest_required"
    # Stripped-leg prefixes from paper_fill_simulator (leg_N_...) — also map
    # by suffix lookup.
    stripped = re.sub(r"^leg_\d+_", "", blocker)
    if stripped != blocker and stripped in _BLOCKER_CATEGORY_MAP:
        return _BLOCKER_CATEGORY_MAP[stripped]
    return "other"


# ---------------------------------------------------------------------------
# Manifest template generator (INVALID by default)
# ---------------------------------------------------------------------------


def _maybe_write_templates(
    *,
    pairings: list[dict[str, Any]],
    output_dir: Path,
    write_templates: bool,
) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    for pairing in pairings:
        snapshot_path = pairing["snapshot_path"]
        snapshot_stem = Path(snapshot_path).stem
        for group in pairing.get("native_candidates") or []:
            group_id = group.get("venue_native_group_id")
            if not isinstance(group_id, str) or not group_id.strip():
                continue
            template = build_manifest_template(
                group_id=group_id,
                venue=str(group.get("venue") or "kalshi"),
                snapshot_path=snapshot_path,
                snapshot_market_tickers=[
                    market.get("market_ticker")
                    for market in group.get("markets") or []
                    if isinstance(market, dict) and market.get("market_ticker")
                ],
            )
            target_name = _safe_template_filename(snapshot_stem=snapshot_stem, group_id=group_id)
            target = output_dir / target_name
            written = False
            if write_templates and not target.exists():
                target.write_text(json.dumps(template, indent=2, sort_keys=True), encoding="utf-8")
                written = True
            validation_blockers_default = validate_local_manifest_v1_group(
                template["exhaustive_groups"][0]
            )
            # If the file is on disk, re-read it and re-validate. This lets
            # the hunter detect when a reviewer has finished a template
            # without the hunter itself ever silently promoting it.
            current_blockers: list[str] = list(validation_blockers_default)
            currently_invalid = True
            if target.exists():
                current_blockers, currently_invalid = _re_validate_template_on_disk(target)
            required_fields = sorted(
                blocker.removeprefix("missing_manifest_").replace("manifest_", "")
                for blocker in current_blockers
                if blocker.startswith("missing_manifest_") or blocker in {"manifest_not_marked_complete", "trusted_local_manifest_required"}
            )
            suggestions.append(
                {
                    "snapshot_path": snapshot_path,
                    "group_id": group_id,
                    "template_path": str(target),
                    "written": written,
                    "template_exists": target.exists(),
                    "validation_blockers_expected": validation_blockers_default,
                    "validation_blockers_current": current_blockers,
                    "currently_invalid": currently_invalid,
                    "required_template_fields": required_fields,
                    "valid_by_default": False,
                }
            )
    return suggestions


def _re_validate_template_on_disk(target: Path) -> tuple[list[str], bool]:
    """Re-validate the manifest template currently on disk.

    Returns (current_blockers, currently_invalid). If the file is unreadable
    or malformed, we treat it as still-invalid and report the parse error
    instead of silently accepting it.
    """
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ([f"template_unreadable:{exc}"], True)
    if not isinstance(payload, dict):
        return (["template_not_an_object"], True)
    groups = payload.get("exhaustive_groups")
    if not isinstance(groups, list) or not groups or not isinstance(groups[0], dict):
        return (["template_missing_exhaustive_groups"], True)
    blockers = validate_local_manifest_v1_group(groups[0])
    return (blockers, bool(blockers))


def build_manifest_template(
    *,
    group_id: str,
    venue: str,
    snapshot_path: str | None,
    snapshot_market_tickers: list[Any],
) -> dict[str, Any]:
    """Build an incomplete-by-default ``local_manifest_v1`` payload.

    The returned payload's group MUST fail
    :func:`validate_local_manifest_v1_group` until a reviewer fills the
    placeholders. The function is deterministic so tests can assert the
    exact failure set without time-dependent fields.
    """
    cleaned_tickers = [
        str(ticker).strip()
        for ticker in (snapshot_market_tickers or [])
        if isinstance(ticker, str) and ticker.strip()
    ]
    template_group: dict[str, Any] = {
        "venue": venue,
        "source": LOCAL_MANIFEST_SOURCE,
        # Explicitly NOT trusted by default.
        "trusted_local_manifest": False,
        "group_id": group_id,
        "venue_native_event_id": group_id,
        "venue_native_group_id": group_id,
        # Completeness markers are explicitly false so manifest_marked_complete
        # returns False.
        "complete": False,
        "is_exhaustive": False,
        # All editable fields are explicit nulls so the validator surfaces
        # missing_manifest_* blockers rather than silently accepting TODO text.
        "reviewer": None,
        "reviewed_at": None,
        "evidence_text": None,
        "evidence_notes": None,
        "settlement_source_evidence": None,
        "rules_evidence": None,
        # Outcome list intentionally empty — the validator will mark it as
        # missing rather than guess at outcomes.
        "outcome_list": [],
        "outcomes": [],
        "market_tickers": cleaned_tickers,
        "expected_outcome_count": None,
        # Template metadata + reviewer hints.
        "manifest_template": True,
        "manifest_template_source": MANIFEST_TEMPLATE_SOURCE,
        "manifest_template_warning": (
            "This template is intentionally invalid. validate_local_manifest_v1_group "
            "MUST return blockers until a reviewer (1) sets trusted_local_manifest=true, "
            "(2) fills reviewer/reviewed_at with a real ISO timestamp, (3) provides "
            "evidence_text + settlement_source_evidence + rules_evidence, (4) fills "
            "outcome_list with the explicit event outcomes, and (5) marks complete=true."
        ),
        "placeholders": {
            "reviewer": "TODO: reviewer email or handle",
            "reviewed_at": "TODO: ISO-8601 timestamp, e.g. 2026-05-25T12:00:00Z",
            "evidence_text": "TODO: paste evidence text citing saved Kalshi event page",
            "settlement_source_evidence": "TODO: cite the settlement source verbatim",
            "rules_evidence": "TODO: cite rules / resolution text verbatim",
            "outcome_list": "TODO: list explicit outcome labels matching market_tickers",
            "trusted_local_manifest": "TODO: set to true ONLY after reviewer signs off",
        },
    }
    return {
        "schema_version": 1,
        "source": LOCAL_MANIFEST_SOURCE,
        "manifest_template_source": MANIFEST_TEMPLATE_SOURCE,
        "snapshot_path": snapshot_path,
        "warning": (
            "STRUCTURAL BASKET HUNTER TEMPLATE — NOT exhaustive evidence. This file is "
            "intentionally invalid and will fail validate_local_manifest_v1_group until "
            "a reviewer completes every placeholder."
        ),
        "exhaustive_groups": [template_group],
        "safety": {
            "is_template": True,
            "valid_for_stop_for_review": False,
            "do_not_load_until_edited": True,
            "saved_file_only": True,
            "live_fetch_attempted": False,
            "places_orders": False,
            "auth_used": False,
            "secrets_read": False,
            "browser_automation_used": False,
            "wallet_used": False,
        },
    }


def _safe_template_filename(*, snapshot_stem: str, group_id: str) -> str:
    raw = f"{snapshot_stem}__{group_id}"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("_").lower()
    if not safe:
        safe = "structural_basket_template"
    if len(safe) > 96:
        safe = safe[:96].rstrip("_")
    return f"{safe}.template.json"


# ---------------------------------------------------------------------------
# Next-command suggester
# ---------------------------------------------------------------------------


def _suggest_next_commands(
    *,
    pairings: list[dict[str, Any]],
    closest_groups: list[dict[str, Any]],
    template_suggestions: list[dict[str, Any]],
) -> list[str]:
    commands: list[str] = []
    seen: set[str] = set()

    def _add(cmd: str) -> None:
        if cmd not in seen:
            seen.add(cmd)
            commands.append(cmd)

    for pairing in pairings:
        if pairing.get("stop_for_review_count"):
            snapshot = _shell_quote(pairing["snapshot_path"])
            metadata = " ".join(f"--metadata {_shell_quote(p)}" for p in (pairing.get("metadata_paths") or []))
            _add(
                f"python scan.py run-structural-basket-dry-run --snapshot {snapshot} {metadata}".strip()
            )
    for group in closest_groups:
        if group.get("stop_for_review"):
            continue
        if group.get("kind") == "detector_row":
            snapshot = _shell_quote(group["snapshot_path"])
            metadata = " ".join(f"--metadata {_shell_quote(p)}" for p in (group.get("metadata_paths") or []))
            _add(
                f"python scan.py run-structural-basket-dry-run --snapshot {snapshot} {metadata}".strip()
            )
        elif group.get("kind") == "needs_manifest_or_metadata":
            snapshot = _shell_quote(group["snapshot_path"])
            _add(f"python scan.py audit-kalshi-native-groups --snapshot {snapshot}")
    if template_suggestions:
        _add(
            "# Edit the placeholders in reports/manifest_templates/*.template.json, then re-run hunt-structural-basket-candidates."
        )
    if not commands:
        commands.append(
            "# No actionable next command. Provide saved Kalshi event metadata or local_manifest_v1 files."
        )
    return commands


def _shell_quote(value: str) -> str:
    if not value:
        return '""'
    if all(ch.isalnum() or ch in ("/", "_", "-", ".", ":") for ch in value):
        return value
    escaped = value.replace('"', '\\"')
    return f'"{escaped}"'


# ---------------------------------------------------------------------------
# File classification helpers
# ---------------------------------------------------------------------------


def _classify(path: Path) -> dict[str, Any]:
    try:
        raw_text = Path(path).read_text(encoding="utf-8")
        payload = json.loads(raw_text)
    except (FileNotFoundError, IsADirectoryError, OSError) as exc:
        return {"path": path, "classifications": set(), "payload": None, "error": f"io_error:{exc}"}
    except json.JSONDecodeError as exc:
        return {"path": path, "classifications": set(), "payload": None, "error": f"invalid_json:{exc.msg}"}
    classifications: set[str] = set()
    if isinstance(payload, dict):
        if _looks_like_manifest(payload):
            classifications.add("manifest")
        if _looks_like_snapshot(payload):
            classifications.add("snapshot")
            if isinstance(payload.get("event_metadata_join"), dict):
                classifications.add("enriched_snapshot")
        if "snapshot" not in classifications and _looks_like_metadata(payload):
            classifications.add("metadata")
    return {"path": path, "classifications": classifications, "payload": payload, "error": None}


def _looks_like_manifest(payload: dict[str, Any]) -> bool:
    if payload.get("source") == LOCAL_MANIFEST_SOURCE or payload.get("evidence_source") == LOCAL_MANIFEST_SOURCE:
        return True
    for key in ("exhaustive_groups", "trusted_exhaustive_groups", "groups"):
        groups = payload.get(key)
        if isinstance(groups, list):
            for group in groups:
                if isinstance(group, dict) and (
                    group.get("source") == LOCAL_MANIFEST_SOURCE
                    or group.get("evidence_source") == LOCAL_MANIFEST_SOURCE
                ):
                    return True
    return False


def _looks_like_snapshot(payload: dict[str, Any]) -> bool:
    if isinstance(payload.get("event_metadata_join"), dict):
        return True
    events = payload.get("events")
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict):
                continue
            markets = event.get("markets")
            if isinstance(markets, list) and markets:
                for market in markets:
                    if isinstance(market, dict) and "orderbook_enrichment" in market:
                        return True
    normalized = payload.get("normalized_markets")
    if isinstance(normalized, list) and normalized:
        for market in normalized:
            if isinstance(market, dict) and (
                "orderbook_enrichment" in market or "best_ask" in market
            ):
                return True
    return False


def _looks_like_metadata(payload: dict[str, Any]) -> bool:
    keys = ("event_ticker", "event_id", "outcome_list", "outcomes")
    if any(key in payload for key in keys):
        return True
    if isinstance(payload.get("event"), dict):
        return True
    events = payload.get("events")
    if isinstance(events, list) and events:
        for event in events:
            if isinstance(event, dict) and any(key in event for key in keys):
                return True
    return False


def _snapshot_event_keys(payload: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    events = payload.get("events") or []
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict):
                continue
            for key_name in ("event_ticker", "event_id", "id", "venue_native_event_id"):
                value = event.get(key_name)
                if isinstance(value, str) and value.strip():
                    keys.add(value.strip())
    return keys


def _metadata_event_keys(payload: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    try:
        normalized = normalize_kalshi_event_metadata_payload(payload, source_path=None)
    except Exception:
        return keys
    for event in normalized:
        for value in (event.event_ticker, event.event_id):
            if value:
                keys.add(value)
    return keys


def _manifest_group_keys(payload: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for source_key in ("exhaustive_groups", "trusted_exhaustive_groups", "groups"):
        groups = payload.get(source_key)
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            for key_name in (
                "group_id",
                "event_id",
                "event_ticker",
                "venue_native_event_id",
                "venue_native_group_id",
            ):
                value = group.get(key_name)
                if isinstance(value, str) and value.strip():
                    keys.add(value.strip())
    return keys


def _discover_json_files(directory: Path | None) -> list[Path]:
    if directory is None:
        return []
    if not directory.exists():
        return []
    return sorted(
        path
        for path in directory.rglob("*.json")
        if path.is_file()
    )


def _file_summary(info: dict[str, Any]) -> dict[str, Any]:
    payload = info.get("payload") or {}
    summary: dict[str, Any] = {
        "path": str(info["path"]),
        "classifications": sorted(info["classifications"]),
    }
    if isinstance(payload, dict):
        if "events" in payload and isinstance(payload["events"], list):
            summary["event_count"] = len(payload["events"])
        if "normalized_markets" in payload and isinstance(payload["normalized_markets"], list):
            summary["normalized_market_count"] = len(payload["normalized_markets"])
        if "exhaustive_groups" in payload and isinstance(payload["exhaustive_groups"], list):
            summary["exhaustive_group_count"] = len(payload["exhaustive_groups"])
        if isinstance(payload.get("event_metadata_join"), dict):
            summary["event_metadata_join_present"] = True
        if has_reference_only_flag(payload):
            summary["reference_only"] = True
    return summary


def _shorten(value: str, *, max_length: int = 80) -> str:
    if len(value) <= max_length:
        return value
    return f"...{value[-(max_length - 3):]}"


# ---------------------------------------------------------------------------
# Safety block
# ---------------------------------------------------------------------------


def _safety_block() -> dict[str, Any]:
    return {
        "saved_file_only": True,
        "diagnostic_only": True,
        "review_only": True,
        "live_fetch_attempted": False,
        "places_orders": False,
        "auth_used": False,
        "private_endpoints_used": False,
        "secrets_read": False,
        "browser_automation_used": False,
        "wallet_used": False,
        "paper_candidate_emitted": False,
        "paper_candidate_count": 0,
        "stop_for_review_means_review_only": True,
        "uses_midpoint": False,
        "uses_title_similarity_for_exhaustiveness": False,
        "uses_graph_hints_for_exhaustiveness": False,
        "uses_count_only_evidence": False,
        "infers_exhaustiveness_from_title": False,
        "infers_exhaustiveness_from_ticker": False,
        "infers_exhaustiveness_from_market_count": False,
        "infers_exhaustiveness_from_graph_hints": False,
        "templates_are_valid_by_default": False,
        "affects_evaluator_gates": False,
        "allowed_evidence_source": KALSHI_EVENT_METADATA_EVIDENCE_SOURCE,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW", "MANIFEST_REVIEW"],
    }


__all__ = [
    "HUNTER_SOURCE",
    "MANIFEST_TEMPLATE_SOURCE",
    "BLOCKER_RANK_ORDER",
    "LADDER_ORDER",
    "LADDER_READY_STOP_FOR_REVIEW",
    "LADDER_NEEDS_VALID_MANIFEST",
    "LADDER_NEEDS_EVENT_METADATA",
    "LADDER_NEEDS_FRESH_QUOTES",
    "LADDER_NEEDS_DEPTH",
    "LADDER_FEES_KILL",
    "LADDER_REFERENCE_ONLY_BLOCKED",
    "LADDER_NOT_EXHAUSTIVE_EVIDENCE",
    "DO_NOT_PAPER_SIMULATE_WARNING",
    "REVIEW_ONLY_WARNING",
    "PAPER_SIMULATION_SKIP_NO_STOP_FOR_REVIEW",
    "PAPER_SIMULATION_SKIP_DISABLED_BY_CALLER",
    "hunt_structural_basket_candidates",
    "hunt_structural_basket_candidates_files",
    "render_hunter_markdown",
    "build_manifest_template",
]
