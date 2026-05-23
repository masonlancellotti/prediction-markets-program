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
