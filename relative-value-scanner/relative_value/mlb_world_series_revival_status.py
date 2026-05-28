from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.paper_candidate_evaluator import (
    ACTION_MANUAL_REVIEW,
    ACTION_PAPER_CANDIDATE,
    ACTION_WATCH,
    PaperCandidateEvaluatorConfig,
    evaluate_paper_candidates,
)
from relative_value.same_payoff_board import (
    DEFAULT_MAX_QUOTE_AGE_SECONDS,
    DEFAULT_SETTLEMENT_TOLERANCE_SECONDS,
    build_same_payoff_board,
)
from relative_value.same_payoff_evidence import attach_same_payoff_evidence


SCHEMA_VERSION = 1
REPORT_SOURCE = "mlb_world_series_revival_status_v1"
EXACT_EQUALITY_CANDIDATE = "EXACT_EQUALITY_CANDIDATE"

DEFAULT_MAX_SETTLEMENT_DELTA_SECONDS = 3600.0
DEFAULT_MIN_TOP_OF_BOOK_SIZE = 1.0
DEFAULT_MIN_NET_GAP = 0.01

PAIR_FILENAMES = (
    "mlb_world_series_pairs_run.json",
    "mlb_world_series_pairs_fresh.json",
    "mlb_world_series_pairs_from_mlb_saved.json",
    "mlb_world_series_pairs.json",
    "mlb_kxmlb_48h_unitok_after_guardrails_pairs.json",
    "mlb_kxmlb_48h_unitok_pairs.json",
    "mlb_kxmlb_pairs.json",
)
POLYMARKET_ENRICHED_FILENAMES = (
    "mlb_fresh_polymarket_enriched.json",
    "mlb_kxmlb_48h_unitok_after_guardrails_polymarket_enriched.json",
    "mlb_kxmlb_48h_unitok_polymarket_enriched.json",
    "mlb_kxmlb_polymarket_enriched.json",
)
KALSHI_ENRICHED_FILENAMES = (
    "mlb_fresh_kalshi_enriched.json",
    "mlb_kxmlb_48h_unitok_after_guardrails_kalshi_enriched.json",
    "mlb_kxmlb_48h_unitok_kalshi_enriched.json",
    "mlb_kxmlb_kalshi_enriched.json",
)
SNAPSHOT_FILENAMES = (
    "live_readonly/mlb/polymarket_live_readonly_snapshot.json",
    "live_readonly/mlb/kalshi_live_readonly_snapshot.json",
    "mlb_kxmlb_48h_unitok_after_guardrails_polymarket_snapshot.json",
    "mlb_kxmlb_48h_unitok_after_guardrails_kalshi_snapshot.json",
    "mlb_kxmlb_48h_unitok_polymarket_snapshot.json",
    "mlb_kxmlb_48h_unitok_kalshi_snapshot.json",
    "mlb_kxmlb_polymarket_snapshot.json",
    "mlb_kxmlb_kalshi_snapshot.json",
)


@dataclass(frozen=True)
class _InputBundle:
    pairs_path: Path | None
    polymarket_enriched_path: Path | None
    kalshi_enriched_path: Path | None
    pairs_payload: dict[str, Any] | None
    polymarket_payload: dict[str, Any] | None
    kalshi_payload: dict[str, Any] | None
    pair_count: int
    polymarket_join_count: int
    kalshi_join_count: int
    both_join_count: int


def write_mlb_world_series_revival_status_files(
    *,
    input_dir: Path,
    json_output: Path,
    markdown_output: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_mlb_world_series_revival_status_report(input_dir=input_dir, generated_at=generated_at)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_mlb_world_series_revival_status_markdown(report), encoding="utf-8")
    return report


def build_mlb_world_series_revival_status_report(
    *,
    input_dir: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    input_dir = Path(input_dir)

    warnings: list[dict[str, Any]] = []
    blockers: list[str] = []
    inventory = _source_inventory(input_dir)
    payload_cache: dict[Path, dict[str, Any] | None] = {}
    bundle = _select_input_bundle(input_dir, payload_cache, warnings)

    if bundle.pairs_path is None:
        blockers.append("missing_mlb_world_series_pairs_file")
    if bundle.polymarket_enriched_path is None:
        blockers.append("missing_polymarket_orderbook_enrichment_file")
    if bundle.kalshi_enriched_path is None:
        blockers.append("missing_kalshi_orderbook_enrichment_file")
    if bundle.pairs_path and bundle.both_join_count <= 0:
        blockers.append("selected_pairs_do_not_join_selected_enriched_files")

    triage = _triage_status(input_dir=input_dir, generated_at=generated, source_pair_keys=_all_mlb_pair_keys(input_dir, payload_cache, warnings))
    if triage["exact_equality_candidate_rows"] and triage["rows_with_matching_source_rows"] < triage["exact_equality_candidate_rows"]:
        blockers.append("triage_exact_rows_missing_matching_source_rows")

    stale_input_files = _stale_input_files(bundle, generated_at=generated)
    if stale_input_files:
        blockers.append("stale_orderbook_enrichment")

    missing_orderbook = _missing_orderbook_enrichment(bundle, generated_at=generated)
    if missing_orderbook:
        blockers.append("missing_or_stale_orderbook_enrichment")

    board_payload: dict[str, Any] | None = None
    derived_pairs_payload: dict[str, Any] | None = None
    evaluator_payload: dict[str, Any] | None = None
    same_payoff_error: str | None = None
    evaluator_error: str | None = None

    if _bundle_ready_for_strict_rebuild(bundle):
        try:
            board_payload = build_same_payoff_board(
                pairs_payload=bundle.pairs_payload or {},
                polymarket_payload=bundle.polymarket_payload or {},
                kalshi_payload=bundle.kalshi_payload or {},
                inputs=_bundle_input_paths(bundle),
                generated_at=generated,
                settlement_tolerance_seconds=DEFAULT_SETTLEMENT_TOLERANCE_SECONDS,
                max_quote_age_seconds=DEFAULT_MAX_QUOTE_AGE_SECONDS,
            )
            derived_pairs_payload = attach_same_payoff_evidence(
                pairs_payload=bundle.pairs_payload or {},
                board_payload=board_payload,
                inputs={
                    "pairs": str(bundle.pairs_path),
                    "board": "<in-memory strict same-payoff-board rebuild>",
                },
            )
        except (TypeError, ValueError) as exc:
            same_payoff_error = str(exc)
            blockers.append("same_payoff_board_rebuild_failed")
    else:
        blockers.append("same_payoff_board_rebuild_inputs_missing")

    missing_same_payoff = _missing_same_payoff_evidence(board_payload)
    if missing_same_payoff:
        blockers.append("missing_same_payoff_evidence")

    if derived_pairs_payload is not None and _bundle_ready_for_strict_rebuild(bundle):
        try:
            evaluator_payload = evaluate_paper_candidates(
                pairs_payload=derived_pairs_payload,
                polymarket_payload=bundle.polymarket_payload or {},
                kalshi_payload=bundle.kalshi_payload or {},
                inputs=_bundle_input_paths(bundle),
                config=PaperCandidateEvaluatorConfig(
                    max_quote_age_seconds=DEFAULT_MAX_QUOTE_AGE_SECONDS,
                    max_settlement_delta_seconds=DEFAULT_MAX_SETTLEMENT_DELTA_SECONDS,
                    min_top_of_book_size=DEFAULT_MIN_TOP_OF_BOOK_SIZE,
                    min_net_gap=DEFAULT_MIN_NET_GAP,
                    accept_unit_mismatch=False,
                ),
                detected_at=generated,
            )
        except (TypeError, ValueError) as exc:
            evaluator_error = str(exc)
            blockers.append("saved_file_evaluator_run_failed")

    trusted_relationships_attached = int(
        ((derived_pairs_payload or {}).get("same_payoff_evidence_attachment") or {}).get("trusted_relationship_attached_count") or 0
    )
    strict_same_payoff_pass_count = int((board_payload or {}).get("strict_same_payoff_pass_count") or 0)
    evaluator_rows = int((evaluator_payload or {}).get("ledger_count") or 0)
    paper_count = int(((evaluator_payload or {}).get("counts_by_action") or {}).get(ACTION_PAPER_CANDIDATE) or 0)
    positive_action_counts = _positive_evaluator_counts(evaluator_payload)
    diagnostic_only = paper_count == 0

    summary = {
        "pairs_found": bundle.pair_count,
        "strict_same_payoff_pass_count": strict_same_payoff_pass_count,
        "trusted_relationships_attached": trusted_relationships_attached,
        "evaluator_rows": evaluator_rows,
        "paper_candidate_count": paper_count,
        "triage_exact_equality_candidate_rows": triage["exact_equality_candidate_rows"],
        "triage_exact_rows_with_matching_source_rows": triage["rows_with_matching_source_rows"],
        "selected_pairs_joining_both_enriched_files": bundle.both_join_count,
        "missing_orderbook_enrichment_count": len(missing_orderbook),
        "missing_same_payoff_evidence_count": len(missing_same_payoff),
        "stale_input_file_count": len(stale_input_files),
        "blockers": sorted(set(blockers)),
        "top_blockers": _top_blockers(blockers, missing_orderbook, missing_same_payoff),
        "next_operator_command": _next_operator_command(blockers, bundle),
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "input_dir": str(input_dir),
        "diagnostic_only": diagnostic_only,
        "summary": summary,
        "pairs_found": summary["pairs_found"],
        "strict_same_payoff_pass_count": strict_same_payoff_pass_count,
        "trusted_relationships_attached": trusted_relationships_attached,
        "evaluator_rows": evaluator_rows,
        "paper_candidate_count": paper_count,
        "positive_evaluator_action_counts": positive_action_counts,
        "selected_inputs": _selected_inputs(bundle),
        "source_inventory": inventory,
        "triage_exact_equality_candidates": triage,
        "blockers": sorted(set(blockers)),
        "stale_input_files": stale_input_files,
        "missing_orderbook_enrichment": missing_orderbook,
        "missing_same_payoff_evidence": missing_same_payoff,
        "same_payoff_rebuild_error": same_payoff_error,
        "evaluator_error": evaluator_error,
        "next_operator_command": summary["next_operator_command"],
        "next_operator_commands": _next_operator_commands(blockers, bundle),
        "warnings": warnings,
        "safety": {
            "saved_files_only": True,
            "live_fetch_attempted": False,
            "execution_or_order_logic_added": False,
            "account_or_auth_logic_added": False,
            "paper_candidate_rows_created_by_this_report": False,
            "same_payoff_logic_reimplemented": False,
            "evaluator_logic_modified": False,
            "thresholds_or_relationship_gates_lowered": False,
            "affects_evaluator_gates": False,
        },
    }


def render_mlb_world_series_revival_status_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    selected = report.get("selected_inputs") or {}
    positive_counts = report.get("positive_evaluator_action_counts") or {}
    lines = [
        "# MLB World Series Revival Status",
        "",
        "Saved-file-only status report. It rebuilds strict same-payoff evidence in memory and creates no candidates.",
        "",
        "## Summary",
        "",
        f"- pairs_found: `{summary.get('pairs_found', 0)}`",
        f"- strict_same_payoff_pass_count: `{summary.get('strict_same_payoff_pass_count', 0)}`",
        f"- trusted_relationships_attached: `{summary.get('trusted_relationships_attached', 0)}`",
        f"- evaluator_rows: `{summary.get('evaluator_rows', 0)}`",
        f"- paper_candidate_count: `{summary.get('paper_candidate_count', 0)}`",
        f"- triage_exact_equality_candidate_rows: `{summary.get('triage_exact_equality_candidate_rows', 0)}`",
        f"- triage_exact_rows_with_matching_source_rows: `{summary.get('triage_exact_rows_with_matching_source_rows', 0)}`",
        f"- selected_pairs_joining_both_enriched_files: `{summary.get('selected_pairs_joining_both_enriched_files', 0)}`",
        "",
        "## Selected Inputs",
        "",
        f"- pairs: `{selected.get('pairs')}`",
        f"- polymarket_enriched: `{selected.get('polymarket_enriched')}`",
        f"- kalshi_enriched: `{selected.get('kalshi_enriched')}`",
        "",
        "## Positive Evaluator Action Counts",
        "",
    ]
    if positive_counts:
        lines.extend(["| Action | Count |", "|---|---:|"])
        for action, count in sorted(positive_counts.items()):
            lines.append(f"| {_md(action)} | {_md(count)} |")
    else:
        lines.append("(none)")
    lines.extend(["", "## Blockers", ""])
    blockers = report.get("blockers") or []
    if blockers:
        for blocker in blockers:
            lines.append(f"- `{blocker}`")
    else:
        lines.append("- `none`")

    top_blockers = summary.get("top_blockers") or []
    lines.extend(["", "### Top Blocker Details", ""])
    if top_blockers:
        lines.extend(["| Blocker | Count |", "|---|---:|"])
        for row in top_blockers:
            lines.append(f"| {_md(row.get('blocker'))} | {_md(row.get('count'))} |")
    else:
        lines.append("(none)")

    lines.extend(["", "## Stale Input Files", ""])
    stale = report.get("stale_input_files") or []
    if stale:
        lines.extend(["| Source file | Venue | Latest quote | Age seconds | Reason |", "|---|---|---|---:|---|"])
        for row in stale:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md(row.get("source_file")),
                        _md(row.get("venue")),
                        _md(row.get("latest_orderbook_captured_at")),
                        _md(row.get("latest_age_seconds")),
                        _md(row.get("reason")),
                    ]
                )
                + " |"
            )
    else:
        lines.append("(none)")

    lines.extend(["", "## Missing Orderbook Enrichment", ""])
    missing_orderbook = report.get("missing_orderbook_enrichment") or []
    if missing_orderbook:
        lines.extend(["| Candidate | Venue | Reason | Captured at | Age seconds |", "|---|---|---|---|---:|"])
        for row in missing_orderbook[:30]:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md(row.get("candidate_id")),
                        _md(row.get("venue")),
                        _md(row.get("reason")),
                        _md(row.get("orderbook_captured_at")),
                        _md(row.get("age_seconds")),
                    ]
                )
                + " |"
            )
    else:
        lines.append("(none)")

    lines.extend(["", "## Missing Same-Payoff Evidence", ""])
    missing_payoff = report.get("missing_same_payoff_evidence") or []
    if missing_payoff:
        lines.extend(["| Candidate | Blockers | Missing fields |", "|---|---|---|"])
        for row in missing_payoff[:30]:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md(row.get("candidate_id")),
                        _md(", ".join(row.get("blockers") or [])),
                        _md(", ".join(row.get("missing_fields") or [])),
                    ]
                )
                + " |"
            )
    else:
        lines.append("(none)")

    lines.extend(
        [
            "",
            "## Next Operator Command",
            "",
            f"`{report.get('next_operator_command')}`",
            "",
            "## Safety",
            "",
            "- saved_files_only: `true`",
            "- live_fetch_attempted: `false`",
            "- execution_or_order_logic_added: `false`",
            "- paper_candidate_rows_created_by_this_report: `false`",
            "- thresholds_or_relationship_gates_lowered: `false`",
            "- affects_evaluator_gates: `false`",
        ]
    )
    return "\n".join(lines) + "\n"


def _source_inventory(input_dir: Path) -> dict[str, Any]:
    return {
        "triage_report_present": (input_dir / "cross_platform_opportunity_triage.json").exists(),
        "candidate_pairs_files": [str(path) for path in _existing_named_paths(input_dir, PAIR_FILENAMES)],
        "candidate_polymarket_enriched_files": [str(path) for path in _existing_named_paths(input_dir, POLYMARKET_ENRICHED_FILENAMES)],
        "candidate_kalshi_enriched_files": [str(path) for path in _existing_named_paths(input_dir, KALSHI_ENRICHED_FILENAMES)],
        "candidate_snapshot_files": [str(path) for path in _existing_named_paths(input_dir, SNAPSHOT_FILENAMES)],
    }


def _select_input_bundle(
    input_dir: Path,
    payload_cache: dict[Path, dict[str, Any] | None],
    warnings: list[dict[str, Any]],
) -> _InputBundle:
    pair_paths = _existing_named_paths(input_dir, PAIR_FILENAMES)
    polymarket_paths = _existing_named_paths(input_dir, POLYMARKET_ENRICHED_FILENAMES)
    kalshi_paths = _existing_named_paths(input_dir, KALSHI_ENRICHED_FILENAMES)
    candidates: list[_InputBundle] = []
    for pair_path in pair_paths or [None]:
        pairs_payload = _load_payload_cached(pair_path, payload_cache, warnings) if pair_path else None
        for polymarket_path in polymarket_paths or [None]:
            polymarket_payload = _load_payload_cached(polymarket_path, payload_cache, warnings) if polymarket_path else None
            for kalshi_path in kalshi_paths or [None]:
                kalshi_payload = _load_payload_cached(kalshi_path, payload_cache, warnings) if kalshi_path else None
                candidates.append(
                    _bundle_from_payloads(
                        pairs_path=pair_path,
                        polymarket_enriched_path=polymarket_path,
                        kalshi_enriched_path=kalshi_path,
                        pairs_payload=pairs_payload,
                        polymarket_payload=polymarket_payload,
                        kalshi_payload=kalshi_payload,
                    )
                )
    if not candidates:
        return _InputBundle(None, None, None, None, None, None, 0, 0, 0, 0)
    return max(candidates, key=_bundle_score)


def _bundle_from_payloads(
    *,
    pairs_path: Path | None,
    polymarket_enriched_path: Path | None,
    kalshi_enriched_path: Path | None,
    pairs_payload: dict[str, Any] | None,
    polymarket_payload: dict[str, Any] | None,
    kalshi_payload: dict[str, Any] | None,
) -> _InputBundle:
    pairs = _pairs(pairs_payload)
    polymarket_ids = _market_ids(polymarket_payload, venue="polymarket")
    kalshi_ids = _market_ids(kalshi_payload, venue="kalshi")
    polymarket_join = 0
    kalshi_join = 0
    both_join = 0
    for pair in pairs:
        poly_id, kalshi_id = _pair_identity(pair)
        has_poly = bool(poly_id and poly_id in polymarket_ids)
        has_kalshi = bool(kalshi_id and kalshi_id in kalshi_ids)
        polymarket_join += int(has_poly)
        kalshi_join += int(has_kalshi)
        both_join += int(has_poly and has_kalshi)
    return _InputBundle(
        pairs_path=pairs_path,
        polymarket_enriched_path=polymarket_enriched_path,
        kalshi_enriched_path=kalshi_enriched_path,
        pairs_payload=pairs_payload,
        polymarket_payload=polymarket_payload,
        kalshi_payload=kalshi_payload,
        pair_count=len(pairs),
        polymarket_join_count=polymarket_join,
        kalshi_join_count=kalshi_join,
        both_join_count=both_join,
    )


def _bundle_score(bundle: _InputBundle) -> tuple[int, int, int, int, int, str, str, str]:
    paths_present = int(bundle.pairs_path is not None) + int(bundle.polymarket_enriched_path is not None) + int(bundle.kalshi_enriched_path is not None)
    return (
        bundle.both_join_count,
        min(bundle.polymarket_join_count, bundle.kalshi_join_count),
        bundle.pair_count,
        paths_present,
        _latest_mtime(bundle),
        str(bundle.pairs_path or ""),
        str(bundle.polymarket_enriched_path or ""),
        str(bundle.kalshi_enriched_path or ""),
    )


def _latest_mtime(bundle: _InputBundle) -> int:
    mtimes = []
    for path in (bundle.pairs_path, bundle.polymarket_enriched_path, bundle.kalshi_enriched_path):
        if path is not None and path.exists():
            mtimes.append(int(path.stat().st_mtime))
    return max(mtimes) if mtimes else 0


def _bundle_ready_for_strict_rebuild(bundle: _InputBundle) -> bool:
    return bool(bundle.pairs_payload and bundle.polymarket_payload and bundle.kalshi_payload)


def _bundle_input_paths(bundle: _InputBundle) -> dict[str, str]:
    return {
        "pairs": str(bundle.pairs_path) if bundle.pairs_path is not None else "<missing>",
        "polymarket_enriched": str(bundle.polymarket_enriched_path) if bundle.polymarket_enriched_path is not None else "<missing>",
        "kalshi_enriched": str(bundle.kalshi_enriched_path) if bundle.kalshi_enriched_path is not None else "<missing>",
    }


def _selected_inputs(bundle: _InputBundle) -> dict[str, Any]:
    return {
        "pairs": str(bundle.pairs_path) if bundle.pairs_path else None,
        "polymarket_enriched": str(bundle.polymarket_enriched_path) if bundle.polymarket_enriched_path else None,
        "kalshi_enriched": str(bundle.kalshi_enriched_path) if bundle.kalshi_enriched_path else None,
        "pair_count": bundle.pair_count,
        "polymarket_join_count": bundle.polymarket_join_count,
        "kalshi_join_count": bundle.kalshi_join_count,
        "both_join_count": bundle.both_join_count,
    }


def _triage_status(
    *,
    input_dir: Path,
    generated_at: datetime,
    source_pair_keys: dict[tuple[str, str], set[str]],
) -> dict[str, Any]:
    triage_path = input_dir / "cross_platform_opportunity_triage.json"
    payload = _load_json_optional(triage_path)
    if not isinstance(payload, dict):
        return {
            "source_file": str(triage_path),
            "present": False,
            "exact_equality_candidate_rows": 0,
            "rows_with_matching_source_rows": 0,
            "missing_matching_source_rows": [],
        }
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    exact_rows = [row for row in rows if isinstance(row, dict) and row.get("relationship_class") == EXACT_EQUALITY_CANDIDATE and _is_mlb_world_series_triage_row(row)]
    missing: list[dict[str, Any]] = []
    matched = 0
    for row in exact_rows:
        identity = _triage_identity(row)
        if identity is not None and identity in source_pair_keys:
            matched += 1
        else:
            missing.append(
                {
                    "row_id": row.get("row_id") or row.get("rank"),
                    "polymarket_market_id": identity[0] if identity else None,
                    "kalshi_ticker": identity[1] if identity else None,
                    "reason": "matching_mlb_pair_source_row_missing",
                }
            )
    return {
        "source_file": str(triage_path),
        "present": True,
        "generated_at": payload.get("generated_at"),
        "as_of": generated_at.isoformat(),
        "exact_equality_candidate_rows": len(exact_rows),
        "rows_with_matching_source_rows": matched,
        "missing_matching_source_rows": missing[:50],
    }


def _all_mlb_pair_keys(
    input_dir: Path,
    payload_cache: dict[Path, dict[str, Any] | None],
    warnings: list[dict[str, Any]],
) -> dict[tuple[str, str], set[str]]:
    keys: dict[tuple[str, str], set[str]] = {}
    if not input_dir.exists():
        return keys
    for path in sorted(input_dir.rglob("*.json")):
        name = path.name.lower()
        if "mlb" not in name or "pair" not in name:
            continue
        payload = _load_payload_cached(path, payload_cache, warnings)
        pairs = _pairs(payload)
        if not pairs:
            continue
        for pair in pairs:
            identity = _pair_identity(pair)
            if identity[0] and identity[1]:
                keys.setdefault(identity, set()).add(str(path))
    return keys


def _missing_orderbook_enrichment(bundle: _InputBundle, *, generated_at: datetime) -> list[dict[str, Any]]:
    if not bundle.pairs_payload:
        return []
    polymarket_by_id = _markets_by_id(bundle.polymarket_payload, venue="polymarket")
    kalshi_by_id = _markets_by_id(bundle.kalshi_payload, venue="kalshi")
    missing: list[dict[str, Any]] = []
    for pair in _pairs(bundle.pairs_payload):
        poly_id, kalshi_id = _pair_identity(pair)
        candidate_id = _candidate_id(poly_id, kalshi_id)
        missing.extend(_orderbook_blockers(candidate_id, "polymarket", poly_id, polymarket_by_id.get(poly_id or ""), generated_at))
        missing.extend(_orderbook_blockers(candidate_id, "kalshi", kalshi_id, kalshi_by_id.get(kalshi_id or ""), generated_at))
    return missing


def _orderbook_blockers(
    candidate_id: str,
    venue: str,
    market_id: str,
    market: dict[str, Any] | None,
    generated_at: datetime,
) -> list[dict[str, Any]]:
    if market is None:
        return [{"candidate_id": candidate_id, "venue": venue, "market_id": market_id, "reason": f"{venue}_enriched_market_missing"}]
    enrichment = market.get("orderbook_enrichment") if isinstance(market.get("orderbook_enrichment"), dict) else {}
    rows: list[dict[str, Any]] = []
    if enrichment.get("enrichment_status") != "enriched":
        rows.append(_orderbook_blocker(candidate_id, venue, market_id, "orderbook_not_enriched", enrichment, generated_at))
    for field in ("best_bid", "best_ask", "depth_at_best_bid", "depth_at_best_ask"):
        if enrichment.get(field) is None:
            rows.append(_orderbook_blocker(candidate_id, venue, market_id, f"missing_{field}", enrichment, generated_at))
    captured_at = _parse_datetime_or_none(enrichment.get("orderbook_captured_at"))
    if captured_at is None:
        rows.append(_orderbook_blocker(candidate_id, venue, market_id, "missing_orderbook_captured_at", enrichment, generated_at))
    else:
        age = (generated_at - captured_at).total_seconds()
        if age < 0 or age > DEFAULT_MAX_QUOTE_AGE_SECONDS:
            rows.append(_orderbook_blocker(candidate_id, venue, market_id, "stale_orderbook_captured_at", enrichment, generated_at))
    return rows


def _orderbook_blocker(
    candidate_id: str,
    venue: str,
    market_id: str,
    reason: str,
    enrichment: dict[str, Any],
    generated_at: datetime,
) -> dict[str, Any]:
    captured = _parse_datetime_or_none(enrichment.get("orderbook_captured_at"))
    age = (generated_at - captured).total_seconds() if captured is not None else None
    return {
        "candidate_id": candidate_id,
        "venue": venue,
        "market_id": market_id,
        "reason": reason,
        "orderbook_captured_at": enrichment.get("orderbook_captured_at"),
        "age_seconds": round(age, 3) if age is not None else None,
        "max_quote_age_seconds": DEFAULT_MAX_QUOTE_AGE_SECONDS,
    }


def _stale_input_files(bundle: _InputBundle, *, generated_at: datetime) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for venue, path, payload in (
        ("polymarket", bundle.polymarket_enriched_path, bundle.polymarket_payload),
        ("kalshi", bundle.kalshi_enriched_path, bundle.kalshi_payload),
    ):
        if path is None or payload is None:
            continue
        quote_times = [
            dt
            for market in _market_rows(payload)
            for dt in [_parse_datetime_or_none((_enrichment(market)).get("orderbook_captured_at"))]
            if dt is not None
        ]
        if not quote_times:
            rows.append(
                {
                    "source_file": str(path),
                    "venue": venue,
                    "latest_orderbook_captured_at": None,
                    "latest_age_seconds": None,
                    "max_quote_age_seconds": DEFAULT_MAX_QUOTE_AGE_SECONDS,
                    "reason": "orderbook_captured_at_missing",
                }
            )
            continue
        latest = max(quote_times)
        age = (generated_at - latest).total_seconds()
        if age < 0 or age > DEFAULT_MAX_QUOTE_AGE_SECONDS:
            rows.append(
                {
                    "source_file": str(path),
                    "venue": venue,
                    "latest_orderbook_captured_at": latest.isoformat(),
                    "latest_age_seconds": round(age, 3),
                    "max_quote_age_seconds": DEFAULT_MAX_QUOTE_AGE_SECONDS,
                    "reason": "latest_orderbook_older_than_max_quote_age" if age >= 0 else "latest_orderbook_time_in_future",
                }
            )
    return rows


def _missing_same_payoff_evidence(board_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(board_payload, dict):
        return []
    missing: list[dict[str, Any]] = []
    for row in board_payload.get("rows") or []:
        if not isinstance(row, dict):
            continue
        strict_blockers = _string_list(row.get("strict_blockers") if "strict_blockers" in row else row.get("blockers"))
        strict_missing = _string_list(row.get("strict_missing_fields") if "strict_missing_fields" in row else row.get("missing_fields"))
        if row.get("same_payoff") is True and not strict_blockers and not strict_missing:
            continue
        poly = row.get("polymarket") if isinstance(row.get("polymarket"), dict) else {}
        kalshi = row.get("kalshi") if isinstance(row.get("kalshi"), dict) else {}
        poly_id = _string_or_empty(poly.get("market_id") or poly.get("condition_id"))
        kalshi_id = _string_or_empty(kalshi.get("ticker") or kalshi.get("market_id"))
        missing.append(
            {
                "candidate_id": _candidate_id(poly_id, kalshi_id),
                "polymarket_market_id": poly_id,
                "kalshi_ticker": kalshi_id,
                "same_payoff": row.get("same_payoff"),
                "strict_pass_count": row.get("strict_pass_count"),
                "strict_comparator_count": row.get("strict_comparator_count"),
                "blockers": sorted(set(strict_blockers)),
                "missing_fields": sorted(set(strict_missing)),
                "recommended_next_action": row.get("recommended_next_action"),
            }
        )
    return missing


def _positive_evaluator_counts(evaluator_payload: dict[str, Any] | None) -> dict[str, int]:
    if not isinstance(evaluator_payload, dict):
        return {}
    counts = evaluator_payload.get("counts_by_action") if isinstance(evaluator_payload.get("counts_by_action"), dict) else {}
    allowed = {ACTION_WATCH, ACTION_MANUAL_REVIEW, ACTION_PAPER_CANDIDATE}
    return {str(action): int(count) for action, count in counts.items() if action in allowed and int(count or 0) > 0}


def _top_blockers(
    blockers: list[str],
    missing_orderbook: list[dict[str, Any]],
    missing_same_payoff: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter(blockers)
    for row in missing_orderbook:
        reason = _string_or_empty(row.get("reason"))
        venue = _string_or_empty(row.get("venue"))
        if reason:
            counts[f"{venue}:{reason}" if venue else reason] += 1
    for row in missing_same_payoff:
        for blocker in _string_list(row.get("blockers")):
            counts[f"same_payoff:{blocker}"] += 1
        for field in _string_list(row.get("missing_fields")):
            counts[f"same_payoff_missing:{field}"] += 1
    return [{"blocker": blocker, "count": count} for blocker, count in counts.most_common(15)]


def _next_operator_command(blockers: list[str], bundle: _InputBundle) -> str:
    blocker_set = set(blockers)
    if "missing_mlb_world_series_pairs_file" in blocker_set:
        return (
            "python scan.py build-mlb-world-series-pairs "
            "--polymarket-snapshot reports/live_readonly/mlb/polymarket_live_readonly_snapshot.json "
            "--kalshi-snapshot reports/live_readonly/mlb/kalshi_live_readonly_snapshot.json "
            "--json-output reports/mlb_world_series_pairs_run.json "
            "--markdown-output reports/mlb_world_series_pairs_run.md"
        )
    if "missing_polymarket_orderbook_enrichment_file" in blocker_set or "missing_kalshi_orderbook_enrichment_file" in blocker_set:
        return (
            "python scan.py run-mlb-world-series-paper-check "
            "--polymarket-snapshot reports/live_readonly/mlb/polymarket_live_readonly_snapshot.json "
            "--kalshi-snapshot reports/live_readonly/mlb/kalshi_live_readonly_snapshot.json "
            "--rebuild-pairs-from-snapshots"
        )
    if "missing_same_payoff_evidence" in blocker_set and bundle.pairs_path and bundle.polymarket_enriched_path and bundle.kalshi_enriched_path:
        return (
            "python scan.py same-payoff-board "
            f"--pairs {bundle.pairs_path} "
            f"--polymarket-enriched {bundle.polymarket_enriched_path} "
            f"--kalshi-enriched {bundle.kalshi_enriched_path} "
            "--json-output reports/mlb_world_series_same_payoff_board_fresh.json "
            "--markdown-output reports/mlb_world_series_same_payoff_board_fresh.md"
        )
    if "stale_orderbook_enrichment" in blocker_set or "missing_or_stale_orderbook_enrichment" in blocker_set:
        return (
            "python scan.py run-mlb-world-series-paper-check "
            "--polymarket-snapshot reports/live_readonly/mlb/polymarket_live_readonly_snapshot.json "
            "--kalshi-snapshot reports/live_readonly/mlb/kalshi_live_readonly_snapshot.json "
            "--rebuild-pairs-from-snapshots"
        )
    return (
        "python scan.py run-mlb-world-series-revival-status "
        "--input-dir reports "
        "--json-output reports/mlb_world_series_revival_status.json "
        "--markdown-output reports/mlb_world_series_revival_status.md"
    )


def _next_operator_commands(blockers: list[str], bundle: _InputBundle) -> list[dict[str, Any]]:
    commands = [
        {
            "label": "rerun_saved_file_status",
            "saved_file_only": True,
            "command": (
                "python scan.py run-mlb-world-series-revival-status "
                "--input-dir reports "
                "--json-output reports/mlb_world_series_revival_status.json "
                "--markdown-output reports/mlb_world_series_revival_status.md"
            ),
        }
    ]
    primary = _next_operator_command(blockers, bundle)
    if primary != commands[0]["command"]:
        commands.insert(
            0,
            {
                "label": "primary_next_step",
                "saved_file_only": not primary.startswith("python scan.py run-mlb-world-series-paper-check"),
                "requires_explicit_operator_approval_for_live_readonly_refresh": primary.startswith("python scan.py run-mlb-world-series-paper-check"),
                "command": primary,
            },
        )
    if "stale_orderbook_enrichment" in set(blockers) or "missing_or_stale_orderbook_enrichment" in set(blockers):
        refresh_command = (
            "python scan.py run-mlb-world-series-paper-check "
            "--polymarket-snapshot reports/live_readonly/mlb/polymarket_live_readonly_snapshot.json "
            "--kalshi-snapshot reports/live_readonly/mlb/kalshi_live_readonly_snapshot.json "
            "--rebuild-pairs-from-snapshots"
        )
        if all(row.get("command") != refresh_command for row in commands):
            commands.append(
                {
                    "label": "conditional_refresh_orderbooks_after_same_payoff_pass",
                    "saved_file_only": False,
                    "requires_explicit_operator_approval_for_live_readonly_refresh": True,
                    "command": refresh_command,
                }
            )
    return commands


def _existing_named_paths(input_dir: Path, relative_names: tuple[str, ...]) -> list[Path]:
    return [input_dir / name for name in relative_names if (input_dir / name).exists()]


def _load_payload_cached(
    path: Path | None,
    cache: dict[Path, dict[str, Any] | None],
    warnings: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if path is None:
        return None
    if path in cache:
        return cache[path]
    payload, warning = _load_json(path)
    if warning is not None:
        warnings.append(warning)
    cache[path] = payload
    return payload


def _load_json(path: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, {"source_file": str(path), "reason_code": "json_file_missing", "blocker": "saved_json_file_missing"}
    except json.JSONDecodeError:
        return None, {"source_file": str(path), "reason_code": "invalid_json", "blocker": "saved_json_invalid"}
    if not isinstance(payload, dict):
        return None, {"source_file": str(path), "reason_code": "json_not_object", "blocker": "saved_json_not_object"}
    return payload, None


def _load_json_optional(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _pairs(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    pairs = payload.get("pairs")
    return [pair for pair in pairs if isinstance(pair, dict)] if isinstance(pairs, list) else []


def _market_rows(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("normalized_markets")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _market_ids(payload: dict[str, Any] | None, *, venue: str) -> set[str]:
    return set(_markets_by_id(payload, venue=venue).keys())


def _markets_by_id(payload: dict[str, Any] | None, *, venue: str) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for market in _market_rows(payload):
        if venue == "polymarket":
            key = _string_or_empty(market.get("market_id") or market.get("condition_id"))
        else:
            key = _string_or_empty(market.get("ticker") or market.get("market_id"))
        if key and key not in rows:
            rows[key] = market
    return rows


def _pair_identity(pair: dict[str, Any]) -> tuple[str, str]:
    polymarket = pair.get("polymarket") if isinstance(pair.get("polymarket"), dict) else {}
    kalshi = pair.get("kalshi") if isinstance(pair.get("kalshi"), dict) else {}
    poly_id = _string_or_empty(polymarket.get("market_id") or polymarket.get("condition_id"))
    kalshi_id = _string_or_empty(kalshi.get("ticker") or kalshi.get("market_id"))
    return poly_id, kalshi_id


def _triage_identity(row: dict[str, Any]) -> tuple[str, str] | None:
    poly_id = ""
    kalshi_id = ""
    for suffix in ("a", "b"):
        venue = _string_or_empty(row.get(f"venue_{suffix}")).lower()
        market_id = _string_or_empty(row.get(f"market_id_{suffix}"))
        ticker = _string_or_empty(row.get(f"ticker_{suffix}"))
        if venue == "polymarket":
            poly_id = market_id or ticker
        elif venue == "kalshi":
            kalshi_id = ticker or market_id
    if poly_id and kalshi_id:
        return poly_id, kalshi_id
    return None


def _is_mlb_world_series_triage_row(row: dict[str, Any]) -> bool:
    text = json.dumps(row, sort_keys=True).lower()
    return "kxmlb" in text or ("mlb" in text and "world series" in text)


def _candidate_id(poly_id: str | None, kalshi_id: str | None) -> str:
    return f"{poly_id or 'missing-polymarket'}__{kalshi_id or 'missing-kalshi'}"


def _enrichment(market: dict[str, Any]) -> dict[str, Any]:
    enrichment = market.get("orderbook_enrichment")
    return enrichment if isinstance(enrichment, dict) else {}


def _parse_datetime_or_none(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _string_or_empty(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _require_tz_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _md(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")
