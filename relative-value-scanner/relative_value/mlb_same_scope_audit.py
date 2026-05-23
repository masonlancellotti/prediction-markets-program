from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.paper_candidate_evaluator import PaperCandidateEvaluatorConfig, evaluate_paper_candidate_files
from relative_value.same_payoff_board import build_same_payoff_board_files, render_same_payoff_board_markdown
from relative_value.same_payoff_evidence import attach_same_payoff_evidence_files


SCHEMA_VERSION = 1
AUDIT_SOURCE = "mlb_same_scope_audit_v1"
AUDIT_DISCLAIMER = (
    "Saved-file MLB/KXMLB same-scope audit only. No live fetch, execution, auth, "
    "orders, account access, midpoint fill assumption, or trade instruction."
)
TARGETING_DISCLAIMER = (
    "Saved-file MLB same-scope targeting diagnostics only. This report does not match, "
    "score, enrich, evaluate, execute, or grant trade permission."
)
WORLD_SERIES_PAIR_DISCLAIMER = (
    "Saved-file MLB World Series pair generator only. It reads existing snapshots, "
    "does not call live APIs, does not mutate matcher or evaluator output, and does "
    "not assert same-payoff."
)
COMPETITION_SCOPES = ("WORLD_SERIES", "ALCS", "NLCS", "GAME", "UNKNOWN")

_TEAM_TOKEN_ALIASES = (
    {"tampa", "bay"},
    {"tampa", "bay", "rays"},
    {"kansas", "city"},
    {"kansas", "city", "royals"},
    {"los", "angeles", "dodgers"},
    {"lad"},
    {"los", "angeles", "angels"},
    {"laa"},
    {"st", "louis"},
    {"st", "louis", "cardinals"},
    {"san", "diego"},
    {"san", "diego", "padres"},
    {"san", "francisco"},
    {"san", "francisco", "giants"},
)
_AMBIGUOUS_CITY_ALIASES = ({"los", "angeles"},)
_TEAM_DISCRIMINATING_TOKENS = {
    "angels",
    "astros",
    "athletics",
    "bluejays",
    "blue",
    "braves",
    "brewers",
    "cardinals",
    "cubs",
    "diamondbacks",
    "dodgers",
    "giants",
    "guardians",
    "jays",
    "lad",
    "laa",
    "mariners",
    "marlins",
    "mets",
    "orioles",
    "padres",
    "phillies",
    "pirates",
    "rangers",
    "rays",
    "redsox",
    "red",
    "reds",
    "rockies",
    "royals",
    "sox",
    "tigers",
    "twins",
    "whitesox",
    "yankees",
}
_TOKEN_RE = re.compile(r"[a-z0-9]+")

_MLB_TEAM_ALIASES: dict[str, tuple[str, ...]] = {
    "ARI": ("arizona diamondbacks", "diamondbacks", "arizona"),
    "ATH": ("athletics", "a s"),
    "ATL": ("atlanta braves", "braves", "atlanta"),
    "BAL": ("baltimore orioles", "orioles", "baltimore"),
    "BOS": ("boston red sox", "red sox", "boston"),
    "CHC": ("chicago cubs", "chicago c", "cubs"),
    "CWS": ("chicago white sox", "chicago ws", "chicago w", "white sox"),
    "CIN": ("cincinnati reds", "reds", "cincinnati"),
    "CLE": ("cleveland guardians", "guardians", "cleveland"),
    "COL": ("colorado rockies", "rockies", "colorado"),
    "DET": ("detroit tigers", "tigers", "detroit"),
    "HOU": ("houston astros", "astros", "houston"),
    "KC": ("kansas city royals", "kansas city", "royals"),
    "LAA": ("los angeles angels", "los angeles a", "laa", "angels"),
    "LAD": ("los angeles dodgers", "los angeles d", "lad", "dodgers"),
    "MIA": ("miami marlins", "marlins", "miami"),
    "MIL": ("milwaukee brewers", "brewers", "milwaukee"),
    "MIN": ("minnesota twins", "twins", "minnesota"),
    "NYM": ("new york mets", "new york m", "nym", "mets"),
    "NYY": ("new york yankees", "new york y", "nyy", "yankees"),
    "PHI": ("philadelphia phillies", "phillies", "philadelphia"),
    "PIT": ("pittsburgh pirates", "pirates", "pittsburgh"),
    "SD": ("san diego padres", "san diego", "padres"),
    "SEA": ("seattle mariners", "mariners", "seattle"),
    "SF": ("san francisco giants", "san francisco", "giants"),
    "STL": ("st louis cardinals", "st louis", "cardinals"),
    "TB": ("tampa bay rays", "tampa bay", "rays"),
    "TEX": ("texas rangers", "rangers", "texas"),
    "TOR": ("toronto blue jays", "toronto", "blue jays", "bluejays"),
    "WSH": ("washington nationals", "nationals", "washington"),
}

_GENERIC_TEAM_TOKENS = {
    "a",
    "alcs",
    "american",
    "baseball",
    "champion",
    "championship",
    "d",
    "league",
    "m",
    "mlb",
    "pro",
    "series",
    "w",
    "will",
    "win",
    "world",
    "y",
}


def audit_same_scope_mlb_candidate_files(
    *,
    pairs_path: Path,
    polymarket_enriched_path: Path,
    kalshi_enriched_path: Path,
    json_output_path: Path,
    markdown_output_path: Path,
    board_json_output_path: Path,
    board_markdown_output_path: Path,
    derived_pairs_output_path: Path,
    evaluator_output_path: Path,
    accept_unit_mismatch: bool = False,
    max_quote_age_seconds: float = 1800.0,
    max_settlement_delta_seconds: float = 3600.0,
    min_top_of_book_size: float = 1.0,
    min_net_gap: float = 0.01,
    now: datetime | None = None,
) -> dict[str, Any]:
    generated_at = now or datetime.now(timezone.utc)
    _require_tz_aware(generated_at, "now")

    board = build_same_payoff_board_files(
        pairs_path=pairs_path,
        polymarket_enriched_path=polymarket_enriched_path,
        kalshi_enriched_path=kalshi_enriched_path,
        json_output_path=board_json_output_path,
        markdown_output_path=board_markdown_output_path,
        now=generated_at,
        max_quote_age_seconds=max_quote_age_seconds,
    )
    derived_pairs = attach_same_payoff_evidence_files(pairs_path, board_json_output_path, derived_pairs_output_path)
    evaluator = evaluate_paper_candidate_files(
        pairs_path=derived_pairs_output_path,
        polymarket_enriched_path=polymarket_enriched_path,
        kalshi_enriched_path=kalshi_enriched_path,
        output_path=evaluator_output_path,
        now=generated_at,
        config=PaperCandidateEvaluatorConfig(
            accept_unit_mismatch=accept_unit_mismatch,
            max_quote_age_seconds=max_quote_age_seconds,
            max_settlement_delta_seconds=max_settlement_delta_seconds,
            min_top_of_book_size=min_top_of_book_size,
            min_net_gap=min_net_gap,
        ),
    )

    payload = build_mlb_same_scope_audit_report(
        board_payload=board,
        derived_pairs_payload=derived_pairs,
        evaluator_payload=evaluator,
        generated_at=generated_at,
        inputs={
            "pairs": str(pairs_path),
            "polymarket_enriched": str(polymarket_enriched_path),
            "kalshi_enriched": str(kalshi_enriched_path),
        },
        outputs={
            "board_json": str(board_json_output_path),
            "board_markdown": str(board_markdown_output_path),
            "derived_pairs": str(derived_pairs_output_path),
            "evaluator": str(evaluator_output_path),
            "audit_json": str(json_output_path),
            "audit_markdown": str(markdown_output_path),
        },
        parameters={
            "accept_unit_mismatch": accept_unit_mismatch,
            "max_quote_age_seconds": max_quote_age_seconds,
            "max_settlement_delta_seconds": max_settlement_delta_seconds,
            "min_top_of_book_size": min_top_of_book_size,
            "min_net_gap": min_net_gap,
        },
    )
    json_output_path.parent.mkdir(parents=True, exist_ok=True)
    json_output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_output_path.write_text(render_mlb_same_scope_audit_markdown(payload), encoding="utf-8")
    return payload


def build_mlb_same_scope_audit_report(
    *,
    board_payload: dict[str, Any],
    derived_pairs_payload: dict[str, Any],
    evaluator_payload: dict[str, Any],
    generated_at: datetime,
    inputs: dict[str, str] | None = None,
    outputs: dict[str, str] | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _require_tz_aware(generated_at, "generated_at")
    board_rows = [row for row in board_payload.get("rows") or [] if isinstance(row, dict)]
    evaluator_rows = {
        _pair_id_from_ledger(row): row
        for row in evaluator_payload.get("ledger") or []
        if isinstance(row, dict) and _pair_id_from_ledger(row)
    }
    derived_pairs = {
        _pair_id_from_pair(pair): pair
        for pair in derived_pairs_payload.get("pairs") or []
        if isinstance(pair, dict) and _pair_id_from_pair(pair)
    }

    rows = []
    for row in board_rows:
        pair_id = _pair_id_from_board_row(row)
        evaluator_row = evaluator_rows.get(pair_id, {})
        derived_pair = derived_pairs.get(pair_id, {})
        scope = classify_mlb_scope(row)
        trusted = _has_trusted_relationship(derived_pair)
        eval_action = str(evaluator_row.get("action") or "")
        blockers = _row_blockers(row, evaluator_row, trusted)
        rows.append(
            {
                "pair_id": pair_id,
                "polymarket": row.get("polymarket") or {},
                "kalshi": row.get("kalshi") or {},
                "scope_classification": scope,
                "same_scope_candidate": scope["classification"] == "exact_same_competition_scope",
                "board_same_payoff": row.get("same_payoff") is True,
                "strict_pass_count": row.get("strict_pass_count"),
                "strict_comparator_count": row.get("strict_comparator_count"),
                "trusted_same_payoff_evidence": trusted,
                "candidate_action_reached": eval_action == "PAPER_CANDIDATE",
                "evaluator_action": eval_action,
                "missed_fill_reason": evaluator_row.get("missed_fill_reason"),
                "blockers": blockers,
            }
        )

    scope_counts = Counter(row["scope_classification"]["classification"] for row in rows)
    trusted_count = sum(1 for row in rows if row["trusted_same_payoff_evidence"])
    candidate_action_count = sum(1 for row in rows if row["candidate_action_reached"])
    same_scope_count = sum(1 for row in rows if row["same_scope_candidate"])
    return {
        "schema_version": SCHEMA_VERSION,
        "source": AUDIT_SOURCE,
        "generated_at": generated_at.isoformat(),
        "inputs": inputs or {},
        "outputs": outputs or {},
        "parameters": parameters or {},
        "summary": {
            "row_count": len(rows),
            "same_scope_candidate_count": same_scope_count,
            "trusted_same_payoff_evidence_count": trusted_count,
            "candidate_action_count": candidate_action_count,
            "scope_classification_counts": dict(sorted(scope_counts.items())),
            "board_strict_same_payoff_pass_count": board_payload.get("strict_same_payoff_pass_count"),
            "attach_trusted_relationship_count": (derived_pairs_payload.get("same_payoff_evidence_attachment") or {}).get(
                "trusted_relationship_attached_count"
            ),
        },
        "rows": rows,
        "recommended_next_commands": _recommended_next_commands() if same_scope_count == 0 else [],
        "safety": {
            "saved_file_only": True,
            "live_fetch_attempted": False,
            "thresholds_or_relationship_gates_lowered": False,
            "reference_only_sources_used_as_executable": False,
            "execution_or_trading_logic_added": False,
        },
        "disclaimer": AUDIT_DISCLAIMER,
    }


def diagnose_mlb_same_scope_targeting_files(
    *,
    polymarket_snapshot_path: Path,
    kalshi_snapshot_path: Path,
    json_output_path: Path,
    markdown_output_path: Path,
    pairs_path: Path | None = None,
    audit_path: Path | None = None,
    scope: str = "all",
    now: datetime | None = None,
) -> dict[str, Any]:
    generated_at = now or datetime.now(timezone.utc)
    _require_tz_aware(generated_at, "now")
    polymarket = _load_json_object(polymarket_snapshot_path, "polymarket_snapshot")
    kalshi = _load_json_object(kalshi_snapshot_path, "kalshi_snapshot")
    pairs = _load_json_object(pairs_path, "pairs") if pairs_path is not None else None
    audit = _load_json_object(audit_path, "mlb_same_scope_audit") if audit_path is not None else None
    payload = build_mlb_same_scope_targeting_report(
        polymarket_snapshot=polymarket,
        kalshi_snapshot=kalshi,
        pairs_payload=pairs,
        audit_payload=audit,
        generated_at=generated_at,
        scope=scope,
        inputs={
            "polymarket_snapshot": str(polymarket_snapshot_path),
            "kalshi_snapshot": str(kalshi_snapshot_path),
            "pairs": str(pairs_path) if pairs_path is not None else None,
            "audit": str(audit_path) if audit_path is not None else None,
        },
        outputs={"json": str(json_output_path), "markdown": str(markdown_output_path)},
    )
    json_output_path.parent.mkdir(parents=True, exist_ok=True)
    json_output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_output_path.write_text(render_mlb_same_scope_targeting_markdown(payload), encoding="utf-8")
    return payload


def build_mlb_world_series_pairs_files(
    *,
    polymarket_snapshot_path: Path,
    kalshi_snapshot_path: Path,
    json_output_path: Path,
    markdown_output_path: Path,
    match_report_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    generated_at = now or datetime.now(timezone.utc)
    _require_tz_aware(generated_at, "now")
    polymarket = _load_json_object(polymarket_snapshot_path, "polymarket_snapshot")
    kalshi = _load_json_object(kalshi_snapshot_path, "kalshi_snapshot")
    match_report = _load_json_object(match_report_path, "match_report") if match_report_path is not None and match_report_path.exists() else None
    payload = build_mlb_world_series_pairs_report(
        polymarket_snapshot=polymarket,
        kalshi_snapshot=kalshi,
        match_report_payload=match_report,
        generated_at=generated_at,
        inputs={
            "polymarket_snapshot": str(polymarket_snapshot_path),
            "kalshi_snapshot": str(kalshi_snapshot_path),
            "match_report": str(match_report_path) if match_report_path is not None else None,
        },
        outputs={"json": str(json_output_path), "markdown": str(markdown_output_path)},
    )
    json_output_path.parent.mkdir(parents=True, exist_ok=True)
    json_output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_output_path.write_text(render_mlb_world_series_pairs_markdown(payload), encoding="utf-8")
    return payload


def build_mlb_world_series_pairs_report(
    *,
    polymarket_snapshot: dict[str, Any],
    kalshi_snapshot: dict[str, Any],
    match_report_payload: dict[str, Any] | None = None,
    generated_at: datetime | None = None,
    inputs: dict[str, str | None] | None = None,
    outputs: dict[str, str] | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    poly_rows = _snapshot_market_rows(polymarket_snapshot, "polymarket_snapshot")
    kalshi_rows = _snapshot_market_rows(kalshi_snapshot, "kalshi_snapshot")
    poly_prepared = [_prepared_market(row, "polymarket") for row in poly_rows]
    kalshi_prepared = [_prepared_market(row, "kalshi") for row in kalshi_rows]
    source_counts = {
        "polymarket": _scope_count_map([_scope_count_row(row) for row in poly_prepared]),
        "kalshi": _scope_count_map([_scope_count_row(row) for row in kalshi_prepared]),
    }
    input_provenance = {
        "polymarket": _snapshot_input_provenance(polymarket_snapshot, "polymarket", len(poly_rows)),
        "kalshi": _snapshot_input_provenance(kalshi_snapshot, "kalshi", len(kalshi_rows)),
    }
    warnings = _world_series_pair_warnings(
        source_counts=source_counts,
        input_provenance=input_provenance,
        generated_at=generated,
    )

    pairs: list[dict[str, Any]] = []
    rejected_rows: list[dict[str, Any]] = []
    for row in poly_prepared:
        if row["competition_scope"] != "WORLD_SERIES":
            rejected_rows.append(_rejected_market_row(row, f"polymarket_scope_{row['competition_scope'].lower()}_rejected"))
    for row in kalshi_prepared:
        if row["competition_scope"] != "WORLD_SERIES":
            rejected_rows.append(_rejected_market_row(row, f"kalshi_scope_{row['competition_scope'].lower()}_rejected"))

    poly_ws = [row for row in poly_prepared if row["competition_scope"] == "WORLD_SERIES"]
    kalshi_ws = [row for row in kalshi_prepared if row["competition_scope"] == "WORLD_SERIES"]
    rejected_candidate_pairs: list[dict[str, Any]] = []
    for poly in poly_ws:
        for kalshi in kalshi_ws:
            decision = _world_series_pair_decision(poly, kalshi)
            if decision["accepted"]:
                pairs.append(_world_series_pair(poly, kalshi, decision))
            elif decision["record_rejection"]:
                rejected_candidate_pairs.append(_rejected_candidate_pair(poly, kalshi, decision["reason"]))

    pairs.sort(key=lambda pair: ((pair.get("matched_team") or {}).get("team_id") or "", _pair_polymarket_id(pair), _pair_kalshi_ticker(pair)))
    rejected_rows.sort(key=lambda row: (str(row.get("source")), str(row.get("reason")), str(row.get("market_id"))))
    rejected_candidate_pairs.sort(key=lambda row: (str(row.get("reason")), str((row.get("polymarket") or {}).get("market_id")), str((row.get("kalshi") or {}).get("ticker"))))
    rejection_counts = Counter(row["reason"] for row in rejected_rows)
    rejection_counts.update(row["reason"] for row in rejected_candidate_pairs)
    report_inputs = inputs or {}
    report_outputs = outputs or {}
    next_commands = _world_series_pair_next_commands(report_outputs, report_inputs, len(pairs))
    if not pairs and source_counts["polymarket"].get("WORLD_SERIES", 0) > 0 and source_counts["kalshi"].get("WORLD_SERIES", 0) > 0:
        warnings.append("no_ws_ws_pairs_found_despite_world_series_inventory")
        warnings = sorted(set(warnings))

    return {
        "schema_version": SCHEMA_VERSION,
        "source": "mlb_world_series_saved_pair_generator_v1",
        "generated_at": generated.isoformat(),
        "diagnostic_only": True,
        "pair_count": len(pairs),
        "generated_ws_ws_pair_count": len(pairs),
        "pairs": pairs,
        "summary": {
            "generated_ws_ws_pair_count": len(pairs),
            "source_counts_by_scope": source_counts,
            "matched_team_entity_pairs": [pair["matched_team"] for pair in pairs],
            "rejected_row_count": len(rejected_rows),
            "rejected_candidate_pair_count": len(rejected_candidate_pairs),
            "rejected_reasons": dict(sorted(rejection_counts.items())),
            "warnings": warnings,
        },
        "input_provenance": input_provenance,
        "rejected_rows": rejected_rows,
        "rejected_candidate_pairs": rejected_candidate_pairs,
        "old_matcher_issue_explanation": _old_matcher_issue_explanation(match_report_payload),
        "recommended_next_commands": next_commands,
        "inputs": report_inputs,
        "outputs": report_outputs,
        "safety": {
            "saved_file_only": True,
            "live_fetch_attempted": False,
            "thresholds_or_relationship_gates_lowered": False,
            "same_payoff_asserted": False,
            "promotes_subset_superset_to_same_payoff": False,
            "original_matcher_outputs_mutated": False,
            "execution_or_trading_logic_added": False,
        },
        "disclaimer": WORLD_SERIES_PAIR_DISCLAIMER,
    }


def build_mlb_same_scope_targeting_report(
    *,
    polymarket_snapshot: dict[str, Any],
    kalshi_snapshot: dict[str, Any],
    pairs_payload: dict[str, Any] | None = None,
    audit_payload: dict[str, Any] | None = None,
    generated_at: datetime | None = None,
    scope: str = "all",
    inputs: dict[str, str | None] | None = None,
    outputs: dict[str, str] | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    scope_filter = _normalize_scope_filter(scope)
    poly_rows = _scope_rows(polymarket_snapshot, "polymarket", scope_filter)
    kalshi_rows = _scope_rows(kalshi_snapshot, "kalshi", scope_filter)
    source_counts = {
        "polymarket": _scope_count_map(poly_rows),
        "kalshi": _scope_count_map(kalshi_rows),
    }
    missing_scope = {
        "polymarket": [row for row in poly_rows if row["competition_scope"] == "UNKNOWN"][:20],
        "kalshi": [row for row in kalshi_rows if row["competition_scope"] == "UNKNOWN"][:20],
    }
    overlap_scopes = [
        scope_name
        for scope_name in COMPETITION_SCOPES
        if scope_name != "UNKNOWN" and source_counts["polymarket"].get(scope_name, 0) > 0 and source_counts["kalshi"].get(scope_name, 0) > 0
    ]
    pair_mismatches = _pair_scope_mismatches(pairs_payload) if pairs_payload is not None else []
    audit_mismatches = _audit_scope_mismatches(audit_payload) if audit_payload is not None else []
    all_mismatches = _dedupe_scope_mismatches(pair_mismatches + audit_mismatches)
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "mlb_same_scope_targeting_v1",
        "generated_at": generated.isoformat(),
        "diagnostic_only": True,
        "scope_filter": scope_filter,
        "inputs": inputs or {},
        "outputs": outputs or {},
        "summary": {
            "polymarket_rows": len(poly_rows),
            "kalshi_rows": len(kalshi_rows),
            "source_counts_by_scope": source_counts,
            "source_missing_scope_counts": {
                "polymarket": len([row for row in poly_rows if row["competition_scope"] == "UNKNOWN"]),
                "kalshi": len([row for row in kalshi_rows if row["competition_scope"] == "UNKNOWN"]),
            },
            "overlapping_same_scope_inventory": bool(overlap_scopes),
            "overlap_scopes": overlap_scopes,
            "world_series_vs_league_championship_mismatch_count": len(
                [row for row in all_mismatches if row.get("reason") == "world_series_vs_league_championship_mismatch"]
            ),
        },
        "source_missing_scope_samples": missing_scope,
        "sample_rows_by_source": {
            "polymarket": poly_rows[:20],
            "kalshi": kalshi_rows[:20],
        },
        "scope_pair_mismatches": all_mismatches[:50],
        "audit_scope_mismatches": [],
        "recommended_fetch_commands": _same_scope_targeting_recommended_commands(overlap_scopes),
        "safety": {
            "saved_file_only": True,
            "live_fetch_attempted": False,
            "thresholds_or_relationship_gates_lowered": False,
            "forces_matches": False,
            "promotes_subset_superset_to_same_payoff": False,
            "execution_or_trading_logic_added": False,
        },
        "disclaimer": TARGETING_DISCLAIMER,
    }


def classify_mlb_scope(row_or_pair: dict[str, Any]) -> dict[str, Any]:
    polymarket = row_or_pair.get("polymarket") if isinstance(row_or_pair.get("polymarket"), dict) else {}
    kalshi = row_or_pair.get("kalshi") if isinstance(row_or_pair.get("kalshi"), dict) else {}
    poly_text = _side_text(polymarket)
    kalshi_text = _side_text(kalshi)
    poly_scope = _scope(poly_text)
    kalshi_scope = _scope(kalshi_text)
    entity_match = _entity_match(poly_text, kalshi_text)

    if "game" in {poly_scope, kalshi_scope} and poly_scope != kalshi_scope:
        classification = "game_winner_vs_series_or_outright_mismatch"
    elif {poly_scope, kalshi_scope} in ({"world_series", "alcs"}, {"world_series", "nlcs"}):
        classification = "world_series_vs_league_championship_subset_superset"
    elif poly_scope != "unknown" and poly_scope == kalshi_scope and entity_match:
        classification = "exact_same_competition_scope"
    elif not entity_match:
        classification = "team_entity_mismatch"
    else:
        classification = "unknown_or_missing_scope"
    return {
        "classification": classification,
        "polymarket_scope": poly_scope,
        "kalshi_scope": kalshi_scope,
        "team_entity_match": entity_match,
    }


def classify_mlb_competition_scope(market: dict[str, Any]) -> str:
    return _scope(_market_scope_text(market)).upper()


def render_mlb_same_scope_audit_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    lines = [
        "# MLB Same-Scope Audit",
        "",
        AUDIT_DISCLAIMER,
        "",
        f"Rows: {summary.get('row_count', 0)}",
        f"Same-scope candidates: {summary.get('same_scope_candidate_count', 0)}",
        f"Trusted same-payoff evidence rows: {summary.get('trusted_same_payoff_evidence_count', 0)}",
        f"Strict candidate-action rows: {summary.get('candidate_action_count', 0)}",
        "",
        "| Pair | Scope classification | Strict checks | Trusted evidence | Candidate action | Blockers |",
        "| --- | --- | ---: | --- | --- | --- |",
    ]
    for row in payload.get("rows") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("pair_id")),
                    _md((row.get("scope_classification") or {}).get("classification")),
                    _md(f"{row.get('strict_pass_count')}/{row.get('strict_comparator_count')}"),
                    _md(str(row.get("trusted_same_payoff_evidence")).lower()),
                    _md(str(row.get("candidate_action_reached")).lower()),
                    _md(", ".join(row.get("blockers") or []) or "none"),
                ]
            )
            + " |"
        )
    commands = payload.get("recommended_next_commands") or []
    if commands:
        lines.extend(["", "## Suggested Saved-Snapshot Refresh Commands", ""])
        lines.extend(f"- `{command}`" for command in commands)
    lines.append("")
    return "\n".join(lines)


def render_mlb_same_scope_targeting_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    source_counts = summary.get("source_counts_by_scope") or {}
    lines = [
        "# MLB Same-Scope Targeting",
        "",
        TARGETING_DISCLAIMER,
        "",
        f"Scope filter: {payload.get('scope_filter')}",
        f"Overlapping same-scope inventory: {str(summary.get('overlapping_same_scope_inventory')).lower()}",
        f"Overlap scopes: {', '.join(summary.get('overlap_scopes') or []) or 'none'}",
        "",
        "| Source | WORLD_SERIES | ALCS | NLCS | GAME | UNKNOWN |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for source in ("polymarket", "kalshi"):
        counts = source_counts.get(source) or {}
        lines.append(
            "| "
            + " | ".join([source] + [str(counts.get(scope, 0)) for scope in COMPETITION_SCOPES])
            + " |"
        )
    lines.extend(
        [
            "",
            "## Scope Mismatches",
            "",
            "| Pair | Polymarket scope | Kalshi scope | Reason |",
            "| --- | --- | --- | --- |",
        ]
    )
    mismatches = list(payload.get("scope_pair_mismatches") or []) + list(payload.get("audit_scope_mismatches") or [])
    if not mismatches:
        lines.append("| none |  |  |  |")
    for row in mismatches:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("pair_id")),
                    _md(row.get("polymarket_scope")),
                    _md(row.get("kalshi_scope")),
                    _md(row.get("reason")),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Recommended Commands", ""])
    for command in payload.get("recommended_fetch_commands") or []:
        lines.append(f"- `{command}`")
    lines.append("")
    return "\n".join(lines)


def render_mlb_world_series_pairs_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    source_counts = summary.get("source_counts_by_scope") or {}
    warnings = summary.get("warnings") or []
    input_provenance = payload.get("input_provenance") if isinstance(payload.get("input_provenance"), dict) else {}
    lines = [
        "# MLB World Series Saved Pairs",
        "",
        WORLD_SERIES_PAIR_DISCLAIMER,
        "",
        f"Generated WS/WS pairs: {summary.get('generated_ws_ws_pair_count', 0)}",
        "",
        "## Warnings",
        "",
    ]
    if warnings:
        lines.extend(f"- `{warning}`" for warning in warnings)
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Input Provenance",
            "",
            "| Source | Captured at | Schema source | Schema version | Normalized count | Query |",
            "| --- | --- | --- | --- | ---: | --- |",
        ]
    )
    for source in ("polymarket", "kalshi"):
        info = input_provenance.get(source) if isinstance(input_provenance.get(source), dict) else {}
        lines.append(
            "| "
            + " | ".join(
                [
                    source,
                    _md(info.get("captured_at")),
                    _md(info.get("schema_source")),
                    _md(info.get("schema_version")),
                    _md(info.get("normalized_count")),
                    _md(info.get("overlap_universe_query")),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Source Counts",
            "",
        "| Source | WORLD_SERIES | ALCS | NLCS | GAME | UNKNOWN |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for source in ("polymarket", "kalshi"):
        counts = source_counts.get(source) or {}
        lines.append("| " + " | ".join([source] + [str(counts.get(scope, 0)) for scope in COMPETITION_SCOPES]) + " |")
    lines.extend(["", "## Matched Team Pairs", "", "| Team | Polymarket | Kalshi |", "| --- | --- | --- |"])
    if not payload.get("pairs"):
        lines.append("| none |  |  |")
    for pair in payload.get("pairs") or []:
        team = pair.get("matched_team") if isinstance(pair.get("matched_team"), dict) else {}
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(team.get("team_id")),
                    _md(_venue_label(pair.get("polymarket"), "market_id")),
                    _md(_venue_label(pair.get("kalshi"), "ticker")),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Rejected Reasons", "", "| Reason | Count |", "| --- | ---: |"])
    rejected_reasons = summary.get("rejected_reasons") or {}
    if not rejected_reasons:
        lines.append("| none | 0 |")
    for reason, count in rejected_reasons.items():
        lines.append(f"| {_md(reason)} | {_md(count)} |")
    issue = payload.get("old_matcher_issue_explanation") or {}
    if issue.get("available"):
        lines.extend(
            [
                "",
                "## Prior Matcher Diagnostic",
                "",
                f"Saved prior pairs reviewed: {issue.get('pair_count', 0)}",
                f"Scope mismatches: {issue.get('scope_mismatch_count', 0)}",
                f"Likely cause: {_md(issue.get('likely_cause'))}",
            ]
        )
    lines.extend(["", "## Next Commands", ""])
    for command in payload.get("recommended_next_commands") or []:
        lines.append(f"- `{command}`")
    lines.append("")
    return "\n".join(lines)


def _snapshot_market_rows(snapshot: dict[str, Any], label: str) -> list[dict[str, Any]]:
    rows = snapshot.get("normalized_markets")
    if not isinstance(rows, list):
        raise ValueError(f"{label} must contain normalized_markets list")
    return [row for row in rows if isinstance(row, dict)]


def _snapshot_input_provenance(snapshot: dict[str, Any], source: str, fallback_count: int) -> dict[str, Any]:
    provenance = snapshot.get("provenance") if isinstance(snapshot.get("provenance"), dict) else {}
    overlap = snapshot.get("overlap_universe") if isinstance(snapshot.get("overlap_universe"), dict) else {}
    normalized_count = snapshot.get("normalized_count")
    try:
        normalized_count = int(normalized_count)
    except (TypeError, ValueError):
        normalized_count = fallback_count
    return {
        "source": source,
        "captured_at": _string_or_none(provenance.get("captured_at") or snapshot.get("captured_at")),
        "provenance_captured_at": _string_or_none(provenance.get("captured_at")),
        "schema_source": _string_or_none(snapshot.get("source") or snapshot.get("source_id")),
        "schema_version": snapshot.get("schema_version"),
        "normalized_count": normalized_count,
        "overlap_universe_query": _string_or_none(overlap.get("query")),
    }


def _parse_datetime_or_none(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
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


def _world_series_pair_warnings(
    *,
    source_counts: dict[str, dict[str, int]],
    input_provenance: dict[str, dict[str, Any]],
    generated_at: datetime,
) -> list[str]:
    warnings: list[str] = []
    for source in ("polymarket", "kalshi"):
        info = input_provenance.get(source) or {}
        normalized_count = int(info.get("normalized_count") or 0)
        if normalized_count == 0:
            warnings.append(f"{source}_snapshot_empty_likely_wrong_file_or_failed_fetch")
        if source_counts.get(source, {}).get("WORLD_SERIES", 0) == 0:
            warnings.append(f"{source}_snapshot_has_zero_world_series_rows")
        query = info.get("overlap_universe_query")
        if query and "mlb" not in str(query).lower():
            warnings.append(f"snapshot_targeted_at_different_sport:{source}={query}")
        captured_at = _parse_datetime_or_none(info.get("provenance_captured_at"))
        if captured_at is not None:
            age_hours = (generated_at - captured_at).total_seconds() / 3600.0
            if age_hours > 24:
                warnings.append(f"snapshot_stale:{source}={age_hours:.1f}h")
    return sorted(set(warnings))


def _prepared_market(row: dict[str, Any], source: str) -> dict[str, Any]:
    text = _market_scope_text(row)
    team_id = _extract_mlb_team_id(text)
    return {
        "source": source,
        "market": row,
        "market_id": str(row.get("market_id") or row.get("ticker") or ""),
        "ticker": _string_or_none(row.get("ticker")),
        "question": str(row.get("question") or row.get("title") or ""),
        "event_title": _string_or_none(row.get("event_title")),
        "competition_scope": classify_mlb_competition_scope(row),
        "team_id": team_id,
        "team_aliases": list(_MLB_TEAM_ALIASES.get(team_id or "", ())),
        "tokens": set(_TOKEN_RE.findall(text.lower())),
    }


def _scope_count_row(row: dict[str, Any]) -> dict[str, Any]:
    return {"competition_scope": row["competition_scope"]}


def _rejected_market_row(row: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "source": row["source"],
        "market_id": row["market_id"],
        "ticker": row["ticker"],
        "question": row["question"],
        "event_title": row["event_title"],
        "competition_scope": row["competition_scope"],
        "team_id": row["team_id"],
        "reason": reason,
    }


def _world_series_pair_decision(poly: dict[str, Any], kalshi: dict[str, Any]) -> dict[str, Any]:
    if poly["competition_scope"] != "WORLD_SERIES" or kalshi["competition_scope"] != "WORLD_SERIES":
        return {"accepted": False, "record_rejection": True, "reason": "non_world_series_scope_rejected"}
    if not poly["team_id"]:
        return {"accepted": False, "record_rejection": True, "reason": "polymarket_team_entity_unknown"}
    if not kalshi["team_id"]:
        return {"accepted": False, "record_rejection": True, "reason": "kalshi_team_entity_unknown"}
    if poly["team_id"] == kalshi["team_id"]:
        return {"accepted": True, "record_rejection": False, "reason": None}
    reason = _team_mismatch_reason(poly, kalshi)
    return {"accepted": False, "record_rejection": reason is not None, "reason": reason or "team_entity_mismatch"}


def _world_series_pair(poly: dict[str, Any], kalshi: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    pair = {
        "action": "WATCH",
        "polymarket": _pair_market_side(poly["market"], "polymarket"),
        "kalshi": _pair_market_side(kalshi["market"], "kalshi"),
        "matched_team": {
            "team_id": poly["team_id"],
            "polymarket_team_id": poly["team_id"],
            "kalshi_team_id": kalshi["team_id"],
            "match_method": "canonical_mlb_team_alias",
        },
        "competition_scope": {
            "polymarket": poly["competition_scope"],
            "kalshi": kalshi["competition_scope"],
        },
        "ineligibility_reasons": [],
        "research_only": True,
        "same_payoff_asserted": False,
        "readiness_promotion": "none",
    }
    return pair


def _pair_market_side(market: dict[str, Any], source: str) -> dict[str, Any]:
    side = {
        "question": market.get("question") or market.get("title"),
        "event_title": market.get("event_title"),
    }
    if source == "polymarket":
        side["market_id"] = market.get("market_id")
        if market.get("condition_id"):
            side["condition_id"] = market.get("condition_id")
    else:
        side["ticker"] = market.get("ticker") or market.get("market_id")
        side["market_id"] = market.get("market_id")
    return {key: value for key, value in side.items() if value is not None}


def _rejected_candidate_pair(poly: dict[str, Any], kalshi: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "polymarket": _pair_market_side(poly["market"], "polymarket"),
        "kalshi": _pair_market_side(kalshi["market"], "kalshi"),
        "polymarket_team_id": poly["team_id"],
        "kalshi_team_id": kalshi["team_id"],
        "polymarket_scope": poly["competition_scope"],
        "kalshi_scope": kalshi["competition_scope"],
        "reason": reason,
    }


def _extract_mlb_team_id(text: str) -> str | None:
    normalized = _normalize_text(text)
    tokens = set(_TOKEN_RE.findall(normalized))
    matches: list[tuple[int, str]] = []
    for team_id, aliases in _MLB_TEAM_ALIASES.items():
        for alias in aliases:
            alias_tokens = tuple(_TOKEN_RE.findall(alias))
            if not alias_tokens:
                continue
            if set(alias_tokens) <= tokens:
                matches.append((len(alias_tokens), team_id))
    if not matches:
        return None
    matches.sort(reverse=True)
    top_len = matches[0][0]
    top_ids = sorted({team_id for length, team_id in matches if length == top_len})
    if len(top_ids) == 1:
        return top_ids[0]
    return None


def _team_mismatch_reason(poly: dict[str, Any], kalshi: dict[str, Any]) -> str | None:
    combined_ids = {poly["team_id"], kalshi["team_id"]}
    if combined_ids == {"LAD", "LAA"}:
        return "team_entity_mismatch_dodgers_vs_angels_laa"
    if combined_ids == {"BOS", "CWS"}:
        return "team_entity_mismatch_red_sox_vs_white_sox"
    if _shared_ambiguous_city_tokens(poly["tokens"], kalshi["tokens"]):
        return "team_entity_mismatch_shared_city_only"
    shared = (poly["tokens"] & kalshi["tokens"]) - _GENERIC_TEAM_TOKENS
    if shared:
        return "team_entity_mismatch"
    return None


def _shared_ambiguous_city_tokens(left: set[str], right: set[str]) -> bool:
    ambiguous = ({"los", "angeles"}, {"new", "york"}, {"chicago"})
    return any(tokens <= left and tokens <= right for tokens in ambiguous)


def _world_series_pair_next_commands(outputs: dict[str, Any], inputs: dict[str, Any], pair_count: int) -> list[str]:
    if pair_count == 0:
        return ["no pairs generated — fix snapshot inputs and rerun build-mlb-world-series-pairs"]
    pairs_path = outputs.get("json") or "reports\\mlb_world_series_pairs.json"
    polymarket_path = _preferred_enriched_path(inputs.get("polymarket_snapshot") or "reports\\live_readonly\\polymarket_live_readonly_snapshot.json")
    kalshi_path = _preferred_enriched_path(inputs.get("kalshi_snapshot") or "reports\\live_readonly\\kalshi_live_readonly_snapshot.json")
    return [
        f"python scan.py same-payoff-board --pairs {pairs_path} --polymarket-enriched {polymarket_path} --kalshi-enriched {kalshi_path} --json-output reports\\mlb_world_series_same_payoff_board.json --markdown-output reports\\mlb_world_series_same_payoff_board.md",
        f"python scan.py attach-same-payoff-evidence --pairs {pairs_path} --board reports\\mlb_world_series_same_payoff_board.json --output reports\\mlb_world_series_pairs_with_evidence.json",
        f"python scan.py evaluate-paper-candidates --pairs reports\\mlb_world_series_pairs_with_evidence.json --polymarket-enriched {polymarket_path} --kalshi-enriched {kalshi_path} --output reports\\mlb_world_series_evaluator.json",
    ]


def _preferred_enriched_path(path_value: Any) -> str:
    path = Path(str(path_value))
    text = str(path)
    if text.endswith("_snapshot.json"):
        enriched = Path(f"{text[:-len('_snapshot.json')]}_enriched.json")
        if enriched.exists():
            return str(enriched)
    return text


def _old_matcher_issue_explanation(match_report_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not match_report_payload:
        return {"available": False, "reason": "match_report_not_provided_or_missing"}
    pairs = match_report_payload.get("pairs")
    if not isinstance(pairs, list):
        return {"available": False, "reason": "match_report_missing_pairs"}
    mismatches = []
    score_rows = []
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        scope = classify_mlb_scope(pair)
        matched_fields = pair.get("matched_fields") if isinstance(pair.get("matched_fields"), dict) else {}
        score_rows.append(
            {
                "pair_id": _pair_id_from_pair(pair),
                "polymarket_scope": str(scope.get("polymarket_scope") or "").upper(),
                "kalshi_scope": str(scope.get("kalshi_scope") or "").upper(),
                "question_similarity": matched_fields.get("question_similarity"),
                "event_keyword_bonus": matched_fields.get("event_keyword_bonus"),
                "final_similarity_score": matched_fields.get("final_similarity_score"),
                "shared_event_tokens": matched_fields.get("shared_event_tokens") or [],
            }
        )
        if scope.get("classification") == "world_series_vs_league_championship_subset_superset":
            mismatches.append(_pair_id_from_pair(pair))
    return {
        "available": True,
        "pair_count": len([pair for pair in pairs if isinstance(pair, dict)]),
        "scope_mismatch_count": len(mismatches),
        "mismatched_pair_ids": mismatches,
        "score_rows": score_rows,
        "likely_cause": "matcher ranked broad title/event token overlap without a scope-aware World Series preference, so earlier ALCS/NLCS rows could beat available WS rows",
    }


def _venue_label(payload: Any, id_key: str) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get(id_key) or payload.get("market_id") or "")


def _pair_polymarket_id(pair: dict[str, Any]) -> str:
    polymarket = pair.get("polymarket") if isinstance(pair.get("polymarket"), dict) else {}
    return str(polymarket.get("market_id") or polymarket.get("condition_id") or "")


def _pair_kalshi_ticker(pair: dict[str, Any]) -> str:
    kalshi = pair.get("kalshi") if isinstance(pair.get("kalshi"), dict) else {}
    return str(kalshi.get("ticker") or kalshi.get("market_id") or "")


def _normalize_text(value: str) -> str:
    return " ".join(_TOKEN_RE.findall(value.lower()))


def _row_blockers(row: dict[str, Any], evaluator_row: dict[str, Any], trusted: bool) -> list[str]:
    blockers = []
    blockers.extend(str(value) for value in row.get("blockers") or [])
    blockers.extend(f"missing:{value}" for value in row.get("missing_fields") or [])
    for reason in evaluator_row.get("ineligibility_reasons") or []:
        blockers.append(f"evaluator:{reason}")
    missed = evaluator_row.get("missed_fill_reason")
    if missed:
        blockers.append(f"missed_fill:{missed}")
    if not trusted:
        blockers.append("trusted_same_payoff_board_evidence_missing")
    return sorted(set(blockers))


def _has_trusted_relationship(pair: dict[str, Any]) -> bool:
    relationship = pair.get("contract_relationship") if isinstance(pair, dict) else None
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


def _scope(text: str) -> str:
    lower = text.lower()
    if "american league championship series" in lower or _has_token(lower, "alcs"):
        return "alcs"
    if "national league championship series" in lower or _has_token(lower, "nlcs"):
        return "nlcs"
    # Kalshi's "Pro Baseball Championship" is its World Series-equivalent wording.
    if "world series" in lower or "pro baseball championship" in lower:
        return "world_series"
    if " vs " in lower or " at " in lower or " beat " in lower or " defeat " in lower:
        return "game"
    return "unknown"


def _scope_rows(snapshot: dict[str, Any], source_id: str, scope_filter: str) -> list[dict[str, Any]]:
    rows = snapshot.get("normalized_markets")
    if not isinstance(rows, list):
        raise ValueError(f"{source_id} snapshot must contain normalized_markets list")
    scoped = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        scope = classify_mlb_competition_scope(row)
        if scope_filter != "ALL" and scope != scope_filter:
            continue
        scoped.append(
            {
                "source": source_id,
                "market_id": str(row.get("market_id") or row.get("ticker") or ""),
                "ticker": _string_or_none(row.get("ticker")),
                "question": str(row.get("question") or row.get("title") or ""),
                "event_title": _string_or_none(row.get("event_title")),
                "competition_scope": scope,
                "end_date": _string_or_none(row.get("end_date")),
                "close_time": _string_or_none(row.get("close_time")),
            }
        )
    return scoped


def _scope_count_map(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(row["competition_scope"] for row in rows)
    return {scope: counts.get(scope, 0) for scope in COMPETITION_SCOPES}


def _pair_scope_mismatches(pairs_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not pairs_payload:
        return []
    pairs = pairs_payload.get("pairs")
    if not isinstance(pairs, list):
        raise ValueError("pairs input must contain pairs list")
    rows = []
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        poly = pair.get("polymarket") if isinstance(pair.get("polymarket"), dict) else {}
        kalshi = pair.get("kalshi") if isinstance(pair.get("kalshi"), dict) else {}
        poly_scope = classify_mlb_competition_scope(poly)
        kalshi_scope = classify_mlb_competition_scope(kalshi)
        reason = _scope_mismatch_reason(poly_scope, kalshi_scope)
        if reason:
            rows.append(
                {
                    "pair_id": f"{poly.get('market_id') or ''}__{kalshi.get('ticker') or kalshi.get('market_id') or ''}",
                    "polymarket_scope": poly_scope,
                    "kalshi_scope": kalshi_scope,
                    "reason": reason,
                }
            )
    return rows


def _audit_scope_mismatches(audit_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not audit_payload:
        return []
    rows = audit_payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError("audit input must contain rows list")
    mismatches = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        scope = row.get("scope_classification") if isinstance(row.get("scope_classification"), dict) else {}
        poly_scope = str(scope.get("polymarket_scope") or "unknown").upper()
        kalshi_scope = str(scope.get("kalshi_scope") or "unknown").upper()
        reason = _scope_mismatch_reason(poly_scope, kalshi_scope) or str(scope.get("classification") or "")
        if reason and reason != "exact_same_competition_scope":
            mismatches.append(
                {
                    "pair_id": row.get("pair_id"),
                    "polymarket_scope": poly_scope,
                    "kalshi_scope": kalshi_scope,
                    "reason": reason,
                }
            )
    return mismatches


def _dedupe_scope_mismatches(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    deduped = []
    for row in rows:
        key = (row.get("pair_id"), row.get("polymarket_scope"), row.get("kalshi_scope"), row.get("reason"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _scope_mismatch_reason(poly_scope: str, kalshi_scope: str) -> str | None:
    poly_scope = poly_scope.upper()
    kalshi_scope = kalshi_scope.upper()
    if poly_scope == kalshi_scope:
        return None
    if {poly_scope, kalshi_scope} in ({"WORLD_SERIES", "ALCS"}, {"WORLD_SERIES", "NLCS"}):
        return "world_series_vs_league_championship_mismatch"
    if "GAME" in {poly_scope, kalshi_scope}:
        return "game_vs_series_or_outright_mismatch"
    if "UNKNOWN" in {poly_scope, kalshi_scope}:
        return "missing_or_unknown_scope"
    return "competition_scope_mismatch"


def _normalize_scope_filter(scope: str) -> str:
    normalized = str(scope or "all").strip().upper()
    aliases = {
        "ALL": "ALL",
        "WORLD_SERIES": "WORLD_SERIES",
        "WORLDSERIES": "WORLD_SERIES",
        "ALCS": "ALCS",
        "NLCS": "NLCS",
    }
    if normalized not in aliases:
        raise ValueError("scope must be one of: all, world_series, alcs, nlcs")
    return aliases[normalized]


def _same_scope_targeting_recommended_commands(overlap_scopes: list[str]) -> list[str]:
    commands = [
        "python scan.py fetch-live-overlap-universe --category sports --query MLB --max-markets 500 --output-dir reports\\live_readonly --report-dir reports --label mlb_same_scope",
        "python scan.py diagnose-mlb-same-scope-targeting --polymarket-snapshot reports\\live_readonly\\polymarket_live_readonly_snapshot.json --kalshi-snapshot reports\\live_readonly\\kalshi_live_readonly_snapshot.json --scope world_series",
    ]
    if "WORLD_SERIES" in overlap_scopes:
        commands.append(
            "python scan.py match-live-readonly-snapshots --snapshot-dir reports\\live_readonly --json-output reports\\live_readonly_match_report.json --markdown-output reports\\live_readonly_match_report.md"
        )
    else:
        commands.append("rerun inventory discovery and confirm which venue is missing WORLD_SERIES inventory before matching")
    return commands


def _market_scope_text(market: dict[str, Any]) -> str:
    raw = market.get("raw") if isinstance(market.get("raw"), dict) else {}
    return " ".join(
        str(value or "")
        for value in (
            market.get("question"),
            market.get("title"),
            market.get("event_title"),
            market.get("market_id"),
            market.get("ticker"),
            raw.get("event_slug"),
            raw.get("series_ticker"),
            raw.get("event_ticker"),
        )
    )


def _entity_match(poly_text: str, kalshi_text: str) -> bool:
    poly_tokens = set(_TOKEN_RE.findall(poly_text.lower()))
    kalshi_tokens = set(_TOKEN_RE.findall(kalshi_text.lower()))
    for tokens in _TEAM_TOKEN_ALIASES:
        if _alias_matches(tokens, poly_tokens) and _alias_matches(tokens, kalshi_tokens):
            return True
    shared = (poly_tokens & kalshi_tokens) & _TEAM_DISCRIMINATING_TOKENS
    return bool(shared)


def _alias_matches(alias_tokens: set[str], market_tokens: set[str]) -> bool:
    if any(alias_tokens == ambiguous for ambiguous in _AMBIGUOUS_CITY_ALIASES):
        return False
    if alias_tokens <= market_tokens:
        return True
    discriminating = alias_tokens & _TEAM_DISCRIMINATING_TOKENS
    return bool(discriminating & market_tokens)


def _has_token(text: str, token: str) -> bool:
    return token in set(_TOKEN_RE.findall(text))


def _side_text(side: dict[str, Any]) -> str:
    return _market_scope_text(side)


def _pair_id_from_board_row(row: dict[str, Any]) -> str:
    poly = row.get("polymarket") if isinstance(row.get("polymarket"), dict) else {}
    kalshi = row.get("kalshi") if isinstance(row.get("kalshi"), dict) else {}
    return f"{poly.get('market_id') or ''}__{kalshi.get('ticker') or kalshi.get('market_id') or ''}"


def _pair_id_from_pair(pair: dict[str, Any]) -> str:
    poly = pair.get("polymarket") if isinstance(pair.get("polymarket"), dict) else {}
    kalshi = pair.get("kalshi") if isinstance(pair.get("kalshi"), dict) else {}
    return f"{poly.get('market_id') or ''}__{kalshi.get('ticker') or kalshi.get('market_id') or ''}"


def _pair_id_from_ledger(row: dict[str, Any]) -> str:
    poly = row.get("polymarket") if isinstance(row.get("polymarket"), dict) else {}
    kalshi = row.get("kalshi") if isinstance(row.get("kalshi"), dict) else {}
    return f"{poly.get('market_id') or ''}__{kalshi.get('ticker') or kalshi.get('market_id') or ''}"


def _recommended_next_commands() -> list[str]:
    return [
        "python scan.py fetch-live-overlap-universe --category sports --query MLB --max-markets 500 --output-dir reports\\live_readonly --report-dir reports --label mlb_same_scope",
        "python scan.py match-live-readonly-snapshots --snapshot-dir reports\\live_readonly --json-output reports\\live_readonly_match_report.json --markdown-output reports\\live_readonly_match_report.md",
        "python scan.py enrich-live-match-candidates --match-report reports\\live_readonly_match_report.json --snapshot-dir reports\\live_readonly --json-output reports\\live_match_candidate_enrichment.json --markdown-output reports\\live_match_candidate_enrichment.md",
    ]


def _md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"{label} file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} JSON is invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} JSON must be an object")
    return payload


def _string_or_none(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
