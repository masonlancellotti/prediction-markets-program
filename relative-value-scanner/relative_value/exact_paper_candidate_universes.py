from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.btc_threshold_pipeline import (
    build_btc_threshold_readiness_report,
    exact_scope_from_btc_report,
)
from relative_value.fed_fomc_range_pipeline import (
    build_fed_fomc_range_readiness_report,
    exact_scope_from_fed_report,
)


SCHEMA_VERSION = 1
READINESS_ORDER = {
    "NO_INVENTORY": 0,
    "INVENTORY_ONLY": 1,
    "SAME_SCOPE_PAIRS_AVAILABLE": 2,
    "TRUSTED_RELATIONSHIPS_AVAILABLE": 3,
    "EXECUTION_DATA_AVAILABLE": 4,
    "PAPER_CANDIDATE_FOUND": 5,
}
BLOCKER_LABELS = {
    "no_kalshi_inventory",
    "no_polymarket_inventory",
    "scope_mismatch",
    "same_payoff_board_blockers",
    "stale_orderbooks",
    "insufficient_depth",
    "fee_adjusted_gap_below_minimum",
    "unit_mismatch",
    "settlement_mismatch",
    "reference_only_source",
}
UNIVERSE_LIVE_READONLY_DIRS = {
    "mlb_world_series_kxmlb": "mlb",
    "nba_champion_kxnba": "nba",
    "nfl_super_bowl_kxnfl": "nfl",
    "nhl_stanley_cup_kxnhl": "nhl",
    "btc_thresholds": "btc",
    "fed_fomc_decisions": "fed",
}
DISCLAIMER = (
    "Saved-file exact same-payoff paper-check universe diagnostics only. "
    "This report does not fetch by default, execute, place orders, access accounts, "
    "or promote subset/superset relationships to same-payoff."
)


@dataclass(frozen=True)
class UniverseSpec:
    universe_id: str
    label: str
    category: str
    polymarket_snapshot: Path | None = None
    kalshi_snapshot: Path | None = None
    pairs: Path | None = None
    board: Path | None = None
    derived_pairs: Path | None = None
    evaluator: Path | None = None
    polymarket_enriched: Path | None = None
    kalshi_enriched: Path | None = None
    overlap_report: Path | None = None
    recommended_fetch_command: str | None = None
    recommended_pair_command: str | None = None
    exact_scope: dict[str, Any] | None = None


def default_exact_paper_candidate_universe_specs(project_root: Path) -> list[UniverseSpec]:
    reports = project_root / "reports"
    live = reports / "live_readonly"
    live_mlb = live / "mlb"
    live_nba = live / "nba"
    live_nfl = live / "nfl"
    live_nhl = live / "nhl"
    live_btc = live / "btc"
    live_fed = live / "fed"
    return [
        UniverseSpec(
            universe_id="mlb_world_series_kxmlb",
            label="MLB World Series / KXMLB",
            category="sports_championship_outright",
            polymarket_snapshot=reports / "mlb_kxmlb_48h_unitok_after_guardrails_polymarket_snapshot.json",
            kalshi_snapshot=reports / "mlb_kxmlb_48h_unitok_after_guardrails_kalshi_snapshot.json",
            pairs=reports / "mlb_world_series_pairs_fresh.json",
            board=reports / "mlb_world_series_same_payoff_board.json",
            derived_pairs=reports / "mlb_world_series_pairs_with_evidence.json",
            evaluator=reports / "mlb_world_series_evaluator_fresh_trust_settlement.json",
            polymarket_enriched=reports / "mlb_fresh_polymarket_enriched.json",
            kalshi_enriched=reports / "mlb_fresh_kalshi_enriched.json",
            recommended_fetch_command=(
                "python scan.py fetch-live-overlap-universe --category sports --query MLB "
                "--max-markets 1000 --kalshi-max-pages 20 --output-dir reports/live_readonly/mlb --report-dir reports/live_readonly/mlb --label mlb"
            ),
            recommended_pair_command=(
                "python scan.py build-mlb-world-series-pairs --polymarket-snapshot reports/live_readonly/mlb/polymarket_live_readonly_snapshot.json "
                "--kalshi-snapshot reports/live_readonly/mlb/kalshi_live_readonly_snapshot.json "
                "--json-output reports/mlb_world_series_pairs.json --markdown-output reports/mlb_world_series_pairs.md"
            ),
        ),
        UniverseSpec(
            universe_id="nba_champion_kxnba",
            label="NBA Champion / KXNBA",
            category="sports_championship_outright",
            polymarket_snapshot=reports / "nba_kxnba_polymarket_snapshot.json",
            kalshi_snapshot=reports / "nba_kxnba_kalshi_snapshot.json",
            pairs=reports / "nba_kxnba_pairs.json",
            board=reports / "nba_kxnba_same_payoff_board.json",
            derived_pairs=reports / "nba_kxnba_pairs_with_evidence.json",
            evaluator=reports / "nba_kxnba_evaluator.json",
            polymarket_enriched=reports / "nba_kxnba_polymarket_enriched.json",
            kalshi_enriched=reports / "nba_kxnba_kalshi_enriched.json",
            recommended_fetch_command=(
                "python scan.py fetch-live-overlap-universe --category sports --query NBA "
                "--output-dir reports/live_readonly/nba --report-dir reports/live_readonly/nba --label nba"
            ),
        ),
        UniverseSpec(
            universe_id="nfl_super_bowl_kxnfl",
            label="NFL Super Bowl / KXNFL",
            category="sports_championship_outright",
            polymarket_snapshot=live_nfl / "polymarket_live_readonly_snapshot.json",
            kalshi_snapshot=live_nfl / "kalshi_live_readonly_snapshot.json",
            overlap_report=live_nfl / "nfl_live_overlap_universe_report.json",
            recommended_fetch_command=(
                "python scan.py fetch-live-overlap-universe --category sports --query NFL "
                "--output-dir reports/live_readonly/nfl --report-dir reports/live_readonly/nfl --label nfl"
            ),
        ),
        UniverseSpec(
            universe_id="nhl_stanley_cup_kxnhl",
            label="NHL Stanley Cup / KXNHL",
            category="sports_championship_outright",
            polymarket_snapshot=reports / "nhl_kxnhl_polymarket_snapshot.json",
            kalshi_snapshot=reports / "nhl_kxnhl_kalshi_snapshot.json",
            pairs=reports / "nhl_stanley_cup_pairs.json",
            board=reports / "nhl_stanley_cup_same_payoff_board.json",
            derived_pairs=reports / "nhl_stanley_cup_pairs_with_evidence.json",
            evaluator=reports / "nhl_stanley_cup_evaluator.json",
            polymarket_enriched=reports / "nhl_kxnhl_polymarket_enriched.json",
            kalshi_enriched=reports / "nhl_kxnhl_kalshi_enriched.json",
            overlap_report=live_nhl / "nhl_live_overlap_universe_report.json",
            recommended_fetch_command=(
                "python scan.py fetch-live-overlap-universe --category sports --query NHL "
                "--output-dir reports/live_readonly/nhl --report-dir reports/live_readonly/nhl --label nhl"
            ),
            recommended_pair_command="python scan.py build-nhl-stanley-cup-pairs",
        ),
        UniverseSpec(
            universe_id="btc_thresholds",
            label="BTC threshold markets",
            category="threshold_binary",
            polymarket_snapshot=live_btc / "polymarket_live_readonly_snapshot.json",
            kalshi_snapshot=live_btc / "kalshi_live_readonly_snapshot.json",
            overlap_report=live_btc / "btc_live_overlap_universe_report.json",
            recommended_fetch_command=(
                "python scan.py fetch-live-overlap-universe --category crypto --query BTC "
                "--output-dir reports/live_readonly/btc --report-dir reports/live_readonly/btc --label btc"
            ),
            exact_scope={
                "status": "NOT_EXACT_PIPELINE",
                "source_basis": "Polymarket and Kalshi executable venue inventory only; no reference-only leg is executable.",
                "date_or_deadline": "UNRESOLVED_FROM_INVENTORY",
                "fed_meeting_or_fomc_event": "NOT_APPLICABLE",
                "threshold_or_numeric_condition": "UNRESOLVED_FROM_INVENTORY",
                "required_exact_keys_present": False,
                "pipeline_classification": "NOT_EXACT_PIPELINE",
                "unresolved_ambiguity": [
                    "BTC threshold level must be parsed from explicit contract terms, not broad title similarity.",
                    "Deadline, observation window, timezone, and reference price/index must match exactly before same-payoff review.",
                    "Inventory report cannot assert settlement equivalence or emit a paper candidate.",
                ],
                "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
                "title_similarity_settlement_equivalence": False,
                "paper_candidate_emitted": False,
            },
        ),
        UniverseSpec(
            universe_id="fed_fomc_decisions",
            label="Fed / FOMC exact decision markets",
            category="macro_policy_decision",
            polymarket_snapshot=live_fed / "polymarket_live_readonly_snapshot.json",
            kalshi_snapshot=live_fed / "kalshi_live_readonly_snapshot.json",
            overlap_report=live_fed / "fed_live_overlap_universe_report.json",
            recommended_fetch_command=(
                "python scan.py fetch-live-overlap-universe --category macro --query Fed "
                "--output-dir reports/live_readonly/fed --report-dir reports/live_readonly/fed --label fed"
            ),
            exact_scope={
                "status": "NOT_EXACT_PIPELINE",
                "source_basis": "Polymarket and Kalshi executable venue inventory only; no sportsbook/reference source is executable.",
                "date_or_deadline": "UNRESOLVED_FROM_INVENTORY",
                "fed_meeting_or_fomc_event": "UNRESOLVED_FROM_INVENTORY",
                "threshold_or_numeric_condition": "UNRESOLVED_FROM_INVENTORY",
                "required_exact_keys_present": False,
                "pipeline_classification": "NOT_EXACT_PIPELINE",
                "unresolved_ambiguity": [
                    "FOMC meeting identity and decision date must match explicitly before same-payoff review.",
                    "Rate-change direction, target range, basis-point threshold, and settlement wording must be exact.",
                    "Broad Fed/FOMC title overlap is inventory evidence only, not settlement equivalence.",
                ],
                "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
                "title_similarity_settlement_equivalence": False,
                "paper_candidate_emitted": False,
            },
        ),
    ]


def build_exact_paper_candidate_universe_report_files(
    *,
    project_root: Path,
    json_output_path: Path,
    markdown_output_path: Path,
    specs: list[UniverseSpec] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    payload = build_exact_paper_candidate_universe_report(
        specs=specs or default_exact_paper_candidate_universe_specs(project_root),
        generated_at=generated_at,
    )
    json_output_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_output_path.parent.mkdir(parents=True, exist_ok=True)
    json_output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output_path.write_text(render_exact_paper_candidate_universe_markdown(payload), encoding="utf-8")
    return payload


def build_exact_paper_candidate_universe_report(
    *,
    specs: list[UniverseSpec],
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    rows = [_universe_row(spec) for spec in specs]
    readiness_counts = Counter(row["readiness"] for row in rows)
    exact_scope_counts = Counter(row["exact_scope"]["status"] for row in rows if row.get("exact_scope"))
    next_by_strict = _next_universe_by_strict_criteria(rows)
    closest = _closest_universe(rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "exact_paper_candidate_universe_discovery_v1",
        "generated_at": generated.isoformat(),
        "summary": {
            "universe_count": len(rows),
            "readiness_counts": {key: readiness_counts.get(key, 0) for key in READINESS_ORDER},
            "closest_universe_id": closest["universe_id"] if closest else None,
            "closest_readiness": closest["readiness"] if closest else None,
            "next_universe_by_strict_criteria": next_by_strict["universe_id"] if next_by_strict else None,
            "next_universe_readiness": next_by_strict["readiness"] if next_by_strict else None,
            "paper_candidate_count": sum(row["evaluator_counts"].get("PAPER_CANDIDATE", 0) for row in rows),
            "exact_scope_status_counts": dict(sorted(exact_scope_counts.items())),
            "exact_scope_unresolved_count": sum(
                1
                for row in rows
                if row.get("exact_scope")
                and row["exact_scope"].get("status") in {"WATCH", "MANUAL_REVIEW", "NOT_EXACT_PIPELINE"}
                and row["exact_scope"].get("unresolved_ambiguity")
            ),
        },
        "universes": rows,
        "recommended_next_commands": _recommended_next_commands(rows),
        "safety": {
            "saved_files_only_by_default": True,
            "live_fetch_attempted": False,
            "original_inputs_mutated": False,
            "thresholds_or_relationship_gates_lowered": False,
            "subset_superset_promoted_to_same_payoff": False,
            "title_similarity_used_as_settlement_equivalence": False,
            "execution_logic_added": False,
        },
        "disclaimer": DISCLAIMER,
    }


def render_exact_paper_candidate_universe_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Exact Same-Payoff Paper-Candidate Universes",
        "",
        payload["disclaimer"],
        "",
        "## Summary",
        "",
        f"- Universes: `{summary['universe_count']}`",
        f"- Closest universe: `{summary.get('closest_universe_id')}` (`{summary.get('closest_readiness')}`)",
        f"- Next universe by strict criteria: `{summary.get('next_universe_by_strict_criteria')}` (`{summary.get('next_universe_readiness')}`)",
        f"- Existing PAPER_CANDIDATE rows: `{summary.get('paper_candidate_count', 0)}`",
        f"- Exact-scope status counts: `{summary.get('exact_scope_status_counts', {})}`",
        f"- Exact-scope unresolved inventories: `{summary.get('exact_scope_unresolved_count', 0)}`",
        "",
        "## Universes",
        "",
        "| Universe | Readiness | Inventory | Universe Paths | Pairs | Strict Passes | Trusted | Fresh OB | Evaluator Ready | Paper | Top fail-closed reasons |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in payload["universes"]:
        counts = row["evaluator_counts"]
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row["label"]),
                    _md(row["readiness"]),
                    _md(str(row["inventory_available"]).lower()),
                    _md(str(row["preflight"]["paths_are_universe_specific"]).lower()),
                    _md(row["same_scope_pair_count"]),
                    _md(row["strict_same_payoff_pass_count"]),
                    _md(row["trusted_relationship_count"]),
                    _md(str(row["fresh_orderbook_enrichment_available"]).lower()),
                    _md(str(row["evaluator_ready"]).lower()),
                    _md(row["paper_candidates_count"]),
                    _md(",".join(row["top_fail_closed_reasons"]) or "none"),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Operator Preflight", ""])
    for row in payload["universes"]:
        preflight = row["preflight"]
        notice = f" `{row['paper_review_notice']}`" if row.get("paper_review_notice") else ""
        warning = preflight.get("generic_live_readonly_warning") or "none"
        lines.extend(
            [
                f"### {row['label']}{notice}",
                "",
                f"- Source snapshots: Polymarket `{preflight['source_snapshot_paths']['polymarket_snapshot']}`, Kalshi `{preflight['source_snapshot_paths']['kalshi_snapshot']}`",
                f"- Universe-specific paths: `{preflight['paths_are_universe_specific']}`",
                f"- Generic live_readonly warning: `{warning}`",
                f"- Legacy top-level report warning: `{preflight.get('legacy_top_level_report_warning') or 'none'}`",
                f"- Authoritative fresh-run hint: `{preflight.get('authoritative_fresh_run_hint') or 'none'}`",
                f"- Pair file evaluated: `{preflight['pair_file_evaluated']}`",
                f"- Same-payoff board/evidence file evaluated: `{preflight['same_payoff_board_evidence_file_evaluated']}`",
                f"- Enriched orderbooks: Polymarket `{preflight['enriched_orderbook_files_evaluated']['polymarket']}`, Kalshi `{preflight['enriched_orderbook_files_evaluated']['kalshi']}`",
                f"- Quote freshness status: `{preflight['quote_freshness_status']['status']}`",
                f"- Fee models: Polymarket `{preflight['fee_model_names']['polymarket']}`, Kalshi `{preflight['fee_model_names']['kalshi']}`",
                f"- Settlement normalization trust: `{preflight['settlement_normalization_trust']['status']}`",
                f"- Depth/top-of-book: Polymarket `{preflight['top_of_book_depth_status']['polymarket']['status']}`, Kalshi `{preflight['top_of_book_depth_status']['kalshi']['status']}`",
                f"- Counts: paper `{preflight['paper_count']}`, watch/manual_review `{preflight['watch_manual_review_count']}`, rejected `{preflight['rejected_count']}`",
                f"- Top blockers: `{','.join(preflight['top_blockers']) or 'none'}`",
                "",
            ]
        )
    exact_scope_rows = [row for row in payload["universes"] if row.get("exact_scope") and row["universe_id"] in {"btc_thresholds", "fed_fomc_decisions"}]
    if exact_scope_rows:
        lines.extend(
            [
                "",
                "## BTC / Fed Exact Scope Inventory",
                "",
                "| Universe | Status | BTC inventory | Typed BTC formulas | BTC exact matches | Fed inventory | Typed Fed formulas | Fed exact matches | Fed overlaps | Missing meeting | Missing range | Not exact | Source basis | Date/deadline | FOMC event | Threshold/condition | Unresolved ambiguity |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|---|---|",
            ]
        )
        for row in exact_scope_rows:
            scope = row["exact_scope"]
            btc_counts = scope.get("btc_exact_threshold_counts") or {}
            fed_counts = scope.get("fed_fomc_exact_range_counts") or {}
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md(row["label"]),
                        _md(scope["status"]),
                        _md(btc_counts.get("btc_inventory_count", "")),
                        _md(btc_counts.get("typed_btc_formula_count", "")),
                        _md(btc_counts.get("exact_key_match_count", "")),
                        _md(fed_counts.get("fed_inventory_count", "")),
                        _md(fed_counts.get("typed_fed_formula_count", "")),
                        _md(fed_counts.get("exact_meeting_range_match_count", "")),
                        _md(fed_counts.get("overlapping_range_count", "")),
                        _md(fed_counts.get("missing_meeting_count", "")),
                        _md(fed_counts.get("missing_range_count", "")),
                        _md(btc_counts.get("not_exact_pipeline_count", fed_counts.get("not_exact_pipeline_count", ""))),
                        _md(scope["source_basis"]),
                        _md(scope["date_or_deadline"]),
                        _md(scope["fed_meeting_or_fomc_event"]),
                        _md(scope["threshold_or_numeric_condition"]),
                        _md("; ".join(scope["unresolved_ambiguity"])),
                    ]
                )
                + " |"
            )
    lines.extend(["", "## Recommended Next Commands", ""])
    for command in payload.get("recommended_next_commands") or []:
        lines.extend(["```powershell", command, "```", ""])
    return "\n".join(lines)


def _universe_row(spec: UniverseSpec) -> dict[str, Any]:
    polymarket_snapshot = _load_optional_json(spec.polymarket_snapshot)
    kalshi_snapshot = _load_optional_json(spec.kalshi_snapshot)
    pairs_payload = _load_optional_json(spec.pairs)
    board_payload = _load_optional_json(spec.board)
    derived_payload = _load_optional_json(spec.derived_pairs)
    evaluator_payload = _load_optional_json(spec.evaluator)
    polymarket_enriched = _load_optional_json(spec.polymarket_enriched)
    kalshi_enriched = _load_optional_json(spec.kalshi_enriched)
    overlap_report = _load_optional_json(spec.overlap_report)

    polymarket_count = _market_count(polymarket_snapshot)
    kalshi_count = _market_count(kalshi_snapshot)
    overlap_counts = _overlap_retained_counts(overlap_report)
    if polymarket_count <= 0:
        polymarket_count = overlap_counts.get("polymarket", 0)
    if kalshi_count <= 0:
        kalshi_count = overlap_counts.get("kalshi", 0)
    pair_count = _pair_count(pairs_payload)
    strict_pass_count = int(board_payload.get("strict_same_payoff_pass_count") or 0) if board_payload else 0
    trusted_count = _trusted_relationship_count(derived_payload)
    evaluator_counts = _evaluator_counts(evaluator_payload)
    execution_rows = _execution_data_row_count(polymarket_enriched, kalshi_enriched, evaluator_payload)
    fresh_orderbook_available = _fresh_orderbook_enrichment_available(polymarket_enriched, kalshi_enriched)
    blockers = _blockers(
        polymarket_count=polymarket_count,
        kalshi_count=kalshi_count,
        pair_count=pair_count,
        board_payload=board_payload,
        derived_payload=derived_payload,
        evaluator_payload=evaluator_payload,
        trusted_count=trusted_count,
        polymarket_enriched=polymarket_enriched,
        kalshi_enriched=kalshi_enriched,
    )
    readiness = _readiness(
        polymarket_count=polymarket_count,
        kalshi_count=kalshi_count,
        pair_count=pair_count,
        trusted_count=trusted_count,
        execution_rows=execution_rows,
        paper_count=evaluator_counts.get("PAPER_CANDIDATE", 0),
    )
    commands = _commands_for_row(spec, readiness, trusted_count, blockers)
    inventory_available = polymarket_count > 0 and kalshi_count > 0
    same_scope_pairs_available = pair_count > 0
    evaluator_ready = trusted_count > 0 and execution_rows > 0
    paper_candidates_count = evaluator_counts.get("PAPER_CANDIDATE", 0)
    source_paths = {
        "polymarket_snapshot": spec.polymarket_snapshot,
        "kalshi_snapshot": spec.kalshi_snapshot,
    }
    evaluated_files = {
        "pairs": spec.pairs,
        "same_payoff_board": spec.board,
        "same_payoff_evidence_pairs": spec.derived_pairs,
        "polymarket_enriched_orderbook": spec.polymarket_enriched,
        "kalshi_enriched_orderbook": spec.kalshi_enriched,
        "evaluator": spec.evaluator,
    }
    path_safety = _path_safety(spec.universe_id, {**source_paths, **evaluated_files})
    quote_status = _quote_freshness_status(evaluator_payload)
    depth_status = _top_of_book_depth_status(polymarket_enriched, kalshi_enriched)
    exact_scope = spec.exact_scope or _empty_exact_scope()
    btc_threshold_report = None
    fed_range_report = None
    if spec.universe_id == "btc_thresholds":
        btc_threshold_report = build_btc_threshold_readiness_report(
            polymarket_snapshot=polymarket_snapshot,
            kalshi_snapshot=kalshi_snapshot,
        )
        exact_scope = exact_scope_from_btc_report(exact_scope, btc_threshold_report)
    if spec.universe_id == "fed_fomc_decisions":
        fed_range_report = build_fed_fomc_range_readiness_report(
            polymarket_snapshot=polymarket_snapshot,
            kalshi_snapshot=kalshi_snapshot,
        )
        exact_scope = exact_scope_from_fed_report(exact_scope, fed_range_report)
    return {
        "universe_id": spec.universe_id,
        "label": spec.label,
        "category": spec.category,
        "readiness": readiness,
        "inventory_available": inventory_available,
        "same_scope_pairs_available": same_scope_pairs_available,
        "strict_same_payoff_passes": strict_pass_count,
        "trusted_relationships_attached": trusted_count,
        "fresh_orderbook_enrichment_available": fresh_orderbook_available,
        "evaluator_ready": evaluator_ready,
        "paper_candidates_count": paper_candidates_count,
        "top_fail_closed_reasons": blockers[:5],
        "paper_review_notice": "STOP_FOR_REVIEW" if paper_candidates_count > 0 else None,
        "preflight": {
            "universe": spec.universe_id,
            "source_snapshot_paths": {key: str(path) if path else None for key, path in source_paths.items()},
            "paths_are_universe_specific": path_safety["all_universe_specific_or_not_live_readonly"],
            "generic_live_readonly_warning": path_safety["generic_live_readonly_warning"],
            "legacy_top_level_report_warning": path_safety["legacy_top_level_report_warning"],
            "authoritative_fresh_run_hint": _authoritative_fresh_run_hint(spec.universe_id, path_safety),
            "path_details": path_safety["path_details"],
            "report_source_paths": _report_source_paths({**source_paths, **evaluated_files}),
            "pair_file_evaluated": str(spec.pairs) if spec.pairs else None,
            "same_payoff_board_evidence_file_evaluated": str(spec.board) if spec.board else None,
            "same_payoff_evidence_pairs_file_evaluated": str(spec.derived_pairs) if spec.derived_pairs else None,
            "enriched_orderbook_files_evaluated": {
                "polymarket": str(spec.polymarket_enriched) if spec.polymarket_enriched else None,
                "kalshi": str(spec.kalshi_enriched) if spec.kalshi_enriched else None,
            },
            "quote_freshness_status": quote_status,
            "fee_model_names": _fee_model_names(),
            "settlement_normalization_trust": {
                "requested": [],
                "status": "absent",
                "mlb_only_allowed_value": "mlb_world_series_timezone_convention_drift",
            },
            "top_of_book_depth_status": depth_status,
            "paper_count": paper_candidates_count,
            "watch_manual_review_count": evaluator_counts.get("WATCH", 0) + evaluator_counts.get("MANUAL_REVIEW", 0),
            "rejected_count": evaluator_counts.get("WATCH", 0),
            "top_blockers": blockers[:5],
        },
        "inventory": {
            "polymarket_count": polymarket_count,
            "kalshi_count": kalshi_count,
            "polymarket_snapshot": str(spec.polymarket_snapshot) if spec.polymarket_snapshot else None,
            "kalshi_snapshot": str(spec.kalshi_snapshot) if spec.kalshi_snapshot else None,
            "overlap_report": str(spec.overlap_report) if spec.overlap_report else None,
        },
        "same_scope_pair_count": pair_count,
        "strict_same_payoff_pass_count": strict_pass_count,
        "trusted_relationship_count": trusted_count,
        "execution_data_row_count": execution_rows,
        "evaluator_counts": evaluator_counts,
        "blockers": blockers,
        "dominant_blocker": blockers[0] if blockers else None,
        "inputs": {
            "pairs": str(spec.pairs) if spec.pairs else None,
            "board": str(spec.board) if spec.board else None,
            "derived_pairs": str(spec.derived_pairs) if spec.derived_pairs else None,
            "evaluator": str(spec.evaluator) if spec.evaluator else None,
            "polymarket_enriched": str(spec.polymarket_enriched) if spec.polymarket_enriched else None,
            "kalshi_enriched": str(spec.kalshi_enriched) if spec.kalshi_enriched else None,
            "overlap_report": str(spec.overlap_report) if spec.overlap_report else None,
        },
        "exact_scope": exact_scope,
        "btc_exact_threshold_readiness": btc_threshold_report,
        "fed_fomc_exact_range_readiness": fed_range_report,
        "recommended_next_commands": commands,
    }


def _path_safety(universe_id: str, paths: dict[str, Path | None]) -> dict[str, Any]:
    details = {}
    generic = []
    wrong_specific = []
    expected_dir = UNIVERSE_LIVE_READONLY_DIRS.get(universe_id)
    for key, path in paths.items():
        path_text = str(path) if path else None
        info = {
            "path": path_text,
            "uses_live_readonly": False,
            "universe_specific_live_readonly": False,
            "generic_live_readonly": False,
        }
        if path is not None:
            normalized = path.as_posix().lower()
            marker = "reports/live_readonly"
            info["uses_live_readonly"] = marker in normalized
            if marker in normalized:
                parts = [part.lower() for part in path.parts]
                try:
                    index = parts.index("live_readonly")
                    child = parts[index + 1] if index + 1 < len(parts) else ""
                except ValueError:
                    child = ""
                info["generic_live_readonly"] = child == "" or child.endswith(".json") or child.endswith(".md")
                info["universe_specific_live_readonly"] = bool(expected_dir and child == expected_dir)
                if info["generic_live_readonly"]:
                    generic.append(key)
                elif expected_dir and not info["universe_specific_live_readonly"]:
                    wrong_specific.append(key)
        details[key] = info
    return {
        "all_universe_specific_or_not_live_readonly": not generic and not wrong_specific,
        "generic_live_readonly_warning": (
            "GENERIC_LIVE_READONLY_PATH_USED:" + ",".join(generic)
            if generic
            else None
        ),
        "legacy_top_level_report_warning": _legacy_top_level_warning(universe_id, paths),
        "wrong_universe_live_readonly_paths": wrong_specific,
        "path_details": details,
    }


def _legacy_top_level_warning(universe_id: str, paths: dict[str, Path | None]) -> str | None:
    prefix = _legacy_report_prefix(universe_id)
    if not prefix:
        return None
    legacy = []
    for key, path in paths.items():
        if path is None:
            continue
        parts = [part.lower() for part in path.parts]
        if len(parts) >= 2 and parts[-2] == "reports" and path.name.lower().startswith(prefix):
            legacy.append(key)
    if not legacy:
        return None
    return "LEGACY_TOP_LEVEL_REPORT_PATHS:" + ",".join(legacy)


def _legacy_report_prefix(universe_id: str) -> str | None:
    if universe_id == "mlb_world_series_kxmlb":
        return "mlb_"
    if universe_id == "nba_champion_kxnba":
        return "nba_"
    if universe_id == "nhl_stanley_cup_kxnhl":
        return "nhl_"
    return None


def _authoritative_fresh_run_hint(universe_id: str, path_safety: dict[str, Any]) -> str | None:
    if universe_id != "mlb_world_series_kxmlb":
        return None
    if path_safety.get("legacy_top_level_report_warning"):
        return (
            "discover_exact_readiness_is_reading_legacy_top_level_reports; "
            "compare against the latest run-mlb-world-series-paper-check summary generated from reports/live_readonly/mlb"
        )
    return "current_paths_are_universe_specific_or_non_live_readonly"


def _report_source_paths(paths: dict[str, Path | None]) -> dict[str, Any]:
    result = {}
    for key, path in paths.items():
        if path is None:
            result[key] = {"path": None, "exists": False, "modified_at": None}
            continue
        exists = path.exists()
        result[key] = {
            "path": str(path),
            "exists": exists,
            "modified_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat() if exists else None,
        }
    return result


def _fee_model_names() -> dict[str, str]:
    return {
        "polymarket": "PolymarketConservativeFeeModel",
        "kalshi": "KalshiTieredFeeModel",
    }


def _quote_freshness_status(evaluator_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not evaluator_payload:
        return {"status": "missing", "stale_or_missing_rows": 0}
    stale_or_missing = 0
    ledger = evaluator_payload.get("ledger") if isinstance(evaluator_payload.get("ledger"), list) else []
    for row in ledger:
        if not isinstance(row, dict):
            continue
        missed = str(row.get("missed_fill_reason") or "")
        reasons = row.get("ineligibility_reasons") if isinstance(row.get("ineligibility_reasons"), list) else []
        if "stale" in missed or "quote" in missed or any("stale" in str(reason) or "quote" in str(reason) for reason in reasons):
            stale_or_missing += 1
    if stale_or_missing:
        return {"status": "stale_or_missing", "stale_or_missing_rows": stale_or_missing}
    return {"status": "available", "stale_or_missing_rows": 0}


def _top_of_book_depth_status(polymarket: dict[str, Any] | None, kalshi: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "polymarket": _venue_depth_status(polymarket),
        "kalshi": _venue_depth_status(kalshi),
    }


def _venue_depth_status(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {"status": "missing", "rows_with_top_of_book_depth": 0}
    rows = payload.get("normalized_markets") if isinstance(payload.get("normalized_markets"), list) else []
    row_count = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        enrichment = row.get("orderbook_enrichment") if isinstance(row.get("orderbook_enrichment"), dict) else row
        if enrichment.get("depth_at_best_bid") is not None and enrichment.get("depth_at_best_ask") is not None:
            row_count += 1
    if row_count:
        return {"status": "available", "rows_with_top_of_book_depth": row_count}
    summary = payload.get("orderbook_enrichment") if isinstance(payload.get("orderbook_enrichment"), dict) else {}
    if int(summary.get("enriched_count") or 0) > 0:
        return {"status": "enriched_summary_available", "rows_with_top_of_book_depth": 0}
    return {"status": "missing", "rows_with_top_of_book_depth": 0}


def _empty_exact_scope() -> dict[str, Any]:
    return {
        "status": "WATCH",
        "source_basis": None,
        "date_or_deadline": None,
        "fed_meeting_or_fomc_event": None,
        "threshold_or_numeric_condition": None,
        "unresolved_ambiguity": [],
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "title_similarity_settlement_equivalence": False,
        "paper_candidate_emitted": False,
        "required_exact_keys_present": False,
        "pipeline_classification": "NOT_REVIEWED",
    }


def _blockers(
    *,
    polymarket_count: int,
    kalshi_count: int,
    pair_count: int,
    board_payload: dict[str, Any] | None,
    derived_payload: dict[str, Any] | None,
    evaluator_payload: dict[str, Any] | None,
    trusted_count: int,
    polymarket_enriched: dict[str, Any] | None,
    kalshi_enriched: dict[str, Any] | None,
) -> list[str]:
    blockers: set[str] = set()
    if polymarket_count <= 0:
        blockers.add("no_polymarket_inventory")
    if kalshi_count <= 0:
        blockers.add("no_kalshi_inventory")
    if polymarket_count > 0 and kalshi_count > 0 and pair_count <= 0:
        blockers.add("scope_mismatch")
    if board_payload:
        for item in board_payload.get("top_blockers") or []:
            blocker = str(item.get("blocker") or "")
            mapped = _map_board_blocker(blocker)
            if mapped:
                blockers.add(mapped)
    if pair_count > 0 and trusted_count <= 0:
        blockers.add("same_payoff_board_blockers")
    if _stale_or_unenriched(polymarket_enriched) or _stale_or_unenriched(kalshi_enriched):
        blockers.add("stale_orderbooks")
    if evaluator_payload:
        for row in evaluator_payload.get("ledger") or []:
            if not isinstance(row, dict):
                continue
            reasons = [str(reason) for reason in row.get("ineligibility_reasons") or []]
            missed = str(row.get("missed_fill_reason") or "")
            for reason in [missed, *reasons]:
                mapped = _map_evaluator_blocker(reason)
                if mapped:
                    blockers.add(mapped)
    return sorted(blockers, key=lambda value: (0 if value in BLOCKER_LABELS else 1, value))


def _map_board_blocker(blocker: str) -> str | None:
    if not blocker:
        return None
    if "not_executable" in blocker:
        return "reference_only_source"
    if "orderbook" in blocker or "quote" in blocker:
        return "stale_orderbooks"
    if "subset_or_superset" in blocker or "scope" in blocker:
        return "scope_mismatch"
    if "settlement" in blocker:
        return "settlement_mismatch"
    return "same_payoff_board_blockers"


def _map_evaluator_blocker(reason: str) -> str | None:
    if not reason:
        return None
    if "stale_quote" in reason or reason == "stale_or_missing_quote_time":
        return "stale_orderbooks"
    if "depth" in reason:
        return "insufficient_depth"
    if "estimated_net_gap_below_minimum" in reason:
        return "fee_adjusted_gap_below_minimum"
    if "unit_mismatch" in reason:
        return "unit_mismatch"
    if "settlement" in reason:
        return "settlement_mismatch"
    if "reference" in reason or "sportsbook" in reason:
        return "reference_only_source"
    return None


def _readiness(
    *,
    polymarket_count: int,
    kalshi_count: int,
    pair_count: int,
    trusted_count: int,
    execution_rows: int,
    paper_count: int,
) -> str:
    if paper_count > 0:
        return "PAPER_CANDIDATE_FOUND"
    if execution_rows > 0 and trusted_count > 0:
        return "EXECUTION_DATA_AVAILABLE"
    if trusted_count > 0:
        return "TRUSTED_RELATIONSHIPS_AVAILABLE"
    if pair_count > 0:
        return "SAME_SCOPE_PAIRS_AVAILABLE"
    if polymarket_count > 0 or kalshi_count > 0:
        return "INVENTORY_ONLY"
    return "NO_INVENTORY"


def _market_count(payload: dict[str, Any] | None) -> int:
    if not payload:
        return 0
    rows = payload.get("normalized_markets")
    if isinstance(rows, list):
        return len([row for row in rows if isinstance(row, dict)])
    return int(payload.get("normalized_count") or payload.get("market_count") or 0)


def _overlap_retained_counts(payload: dict[str, Any] | None) -> dict[str, int]:
    if not payload:
        return {"kalshi": 0, "polymarket": 0}
    summary = payload.get("summary")
    retained = summary.get("retained_by_source") if isinstance(summary, dict) else None
    if not isinstance(retained, dict):
        return {"kalshi": 0, "polymarket": 0}
    return {
        "kalshi": int(retained.get("kalshi") or 0),
        "polymarket": int(retained.get("polymarket") or 0),
    }


def _pair_count(payload: dict[str, Any] | None) -> int:
    if not payload:
        return 0
    pairs = payload.get("pairs")
    if isinstance(pairs, list):
        return len([pair for pair in pairs if isinstance(pair, dict)])
    return int(payload.get("pair_count") or 0)


def _trusted_relationship_count(payload: dict[str, Any] | None) -> int:
    if not payload:
        return 0
    attachment = payload.get("same_payoff_evidence_attachment")
    if isinstance(attachment, dict):
        return int(attachment.get("trusted_relationship_attached_count") or 0)
    pairs = payload.get("pairs")
    if not isinstance(pairs, list):
        return 0
    return sum(1 for pair in pairs if _has_trusted_relationship(pair))


def _execution_data_row_count(
    polymarket: dict[str, Any] | None,
    kalshi: dict[str, Any] | None,
    evaluator: dict[str, Any] | None,
) -> int:
    if evaluator:
        ledger = evaluator.get("ledger")
        if isinstance(ledger, list):
            return len([row for row in ledger if isinstance(row, dict)])
    return min(_enriched_count(polymarket), _enriched_count(kalshi))


def _enriched_count(payload: dict[str, Any] | None) -> int:
    if not payload:
        return 0
    summary = payload.get("orderbook_enrichment")
    if isinstance(summary, dict):
        return int(summary.get("enriched_count") or 0)
    rows = payload.get("normalized_markets")
    if not isinstance(rows, list):
        return 0
    return sum(1 for row in rows if isinstance(row, dict) and (row.get("orderbook_enrichment") or {}).get("enrichment_status") == "enriched")


def _stale_or_unenriched(payload: dict[str, Any] | None) -> bool:
    if not payload:
        return False
    summary = payload.get("orderbook_enrichment")
    if isinstance(summary, dict):
        warnings = summary.get("snapshot_warnings") if isinstance(summary.get("snapshot_warnings"), list) else []
        if "stale_snapshot" in warnings:
            return True
        if int(summary.get("market_count") or 0) and int(summary.get("enriched_count") or 0) <= 0:
            return True
    return False


def _fresh_orderbook_enrichment_available(polymarket: dict[str, Any] | None, kalshi: dict[str, Any] | None) -> bool:
    return (
        _enriched_count(polymarket) > 0
        and _enriched_count(kalshi) > 0
        and not _stale_or_unenriched(polymarket)
        and not _stale_or_unenriched(kalshi)
    )


def _evaluator_counts(payload: dict[str, Any] | None) -> dict[str, int]:
    counts = {"PAPER_CANDIDATE": 0, "MANUAL_REVIEW": 0, "WATCH": 0}
    if not payload:
        return counts
    raw = payload.get("counts_by_action")
    if isinstance(raw, dict):
        for key in counts:
            counts[key] = int(raw.get(key) or 0)
    return counts


def _has_trusted_relationship(pair: Any) -> bool:
    if not isinstance(pair, dict):
        return False
    relationship = pair.get("contract_relationship")
    if not isinstance(relationship, dict):
        return False
    evidence = relationship.get("same_payoff_board_evidence")
    return (
        relationship.get("relationship") == "EQUIVALENT"
        and relationship.get("same_payoff") is True
        and relationship.get("source") == "same_payoff_board_v1"
        and isinstance(evidence, dict)
        and evidence.get("classifier_version") == "same-payoff-board-v1"
        and evidence.get("strict_pass_count") == evidence.get("strict_comparator_count")
    )


def _closest_universe(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return max(
        rows,
        key=lambda row: (
            READINESS_ORDER.get(row["readiness"], -1),
            row["trusted_relationship_count"],
            row["same_scope_pair_count"],
            -len(row["blockers"]),
            row["universe_id"],
        ),
    )


def _next_universe_by_strict_criteria(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    strict_rows = [
        row
        for row in rows
        if row.get("inventory_available")
        and row.get("same_scope_pairs_available")
        and int(row.get("strict_same_payoff_passes") or 0) > 0
        and int(row.get("trusted_relationships_attached") or 0) > 0
    ]
    if not strict_rows:
        return None
    return max(
        strict_rows,
        key=lambda row: (
            READINESS_ORDER.get(row["readiness"], -1),
            row.get("fresh_orderbook_enrichment_available") is True,
            int(row.get("trusted_relationships_attached") or 0),
            int(row.get("strict_same_payoff_passes") or 0),
            -len(row.get("top_fail_closed_reasons") or []),
            row["universe_id"],
        ),
    )


def _commands_for_row(spec: UniverseSpec, readiness: str, trusted_count: int, blockers: list[str]) -> list[str]:
    commands: list[str] = []
    if readiness in {"NO_INVENTORY", "INVENTORY_ONLY"} and spec.recommended_fetch_command:
        commands.append(spec.recommended_fetch_command)
    if readiness == "INVENTORY_ONLY" and spec.recommended_pair_command:
        commands.append(spec.recommended_pair_command)
    if (
        readiness == "SAME_SCOPE_PAIRS_AVAILABLE"
        and "same_payoff_board_blockers" in blockers
        and spec.pairs
        and spec.polymarket_enriched
        and spec.kalshi_enriched
    ):
        commands.append(
            "python scan.py same-payoff-board "
            f"--pairs {spec.pairs} --polymarket-enriched {spec.polymarket_enriched} --kalshi-enriched {spec.kalshi_enriched}"
        )
    if spec.universe_id == "mlb_world_series_kxmlb" and readiness == "EXECUTION_DATA_AVAILABLE" and "stale_orderbooks" in blockers:
        commands.append(
            "python scan.py run-mlb-world-series-paper-check "
            "--polymarket-snapshot reports/live_readonly/mlb/polymarket_live_readonly_snapshot.json "
            "--kalshi-snapshot reports/live_readonly/mlb/kalshi_live_readonly_snapshot.json "
            "--rebuild-pairs-from-snapshots "
            "--accept-unit-mismatch --trust-settlement-normalization mlb_world_series_timezone_convention_drift"
        )
    if readiness == "TRUSTED_RELATIONSHIPS_AVAILABLE" and trusted_count > 0 and spec.derived_pairs and spec.polymarket_enriched and spec.kalshi_enriched:
        commands.append(
            "python scan.py evaluate-paper-candidates "
            f"--pairs {spec.derived_pairs} --polymarket-enriched {spec.polymarket_enriched} --kalshi-enriched {spec.kalshi_enriched} "
            "--accept-unit-mismatch"
        )
    return commands


def _recommended_next_commands(rows: list[dict[str, Any]]) -> list[str]:
    commands: list[str] = []
    for row in sorted(rows, key=lambda item: -READINESS_ORDER.get(item["readiness"], 0)):
        for command in row.get("recommended_next_commands") or []:
            if command not in commands:
                commands.append(command)
    return commands


def _load_optional_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"JSON must be an object: {path}")
    return payload


def _md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
