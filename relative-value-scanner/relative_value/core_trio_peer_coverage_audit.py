from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
SCHEMA_KIND = "core_trio_peer_coverage_audit_v1"
REPORT_SOURCE = "core_trio_peer_coverage_audit_v1"

CORE_FAMILIES = (
    "crypto_price_threshold",
    "fed_fomc",
    "macro_rate",
    "election",
    "sports_futures",
    "company_metric",
    "weather",
    "other",
)

B_NO_KALSHI_PEER = "no_saved_kalshi_peer_family"
B_KALSHI_MISSING_THRESHOLD = "kalshi_peer_missing_threshold"
B_KALSHI_MISSING_COMPARATOR = "kalshi_peer_missing_comparator"
B_KALSHI_MISSING_TARGET_DATE = "kalshi_peer_missing_target_date"
B_KALSHI_MISSING_TARGET_TIME = "kalshi_peer_missing_target_time"
B_KALSHI_MISSING_SETTLEMENT_SOURCE = "kalshi_peer_missing_settlement_source"
B_POLY_MISSING_SETTLEMENT_SOURCE = "polymarket_peer_missing_settlement_source"
B_CDNA_SETTLEMENT_SOURCE_UNVERIFIED = "cdna_peer_settlement_source_unverified"
B_CDNA_BASIS_RISK_ONLY = "cdna_basis_risk_only"
B_TITLE_ONLY_MATCH = "title_only_match_not_equivalence"
B_POINT_TIME_DEADLINE_MISMATCH = "point_in_time_vs_deadline_mismatch"
B_MISSING_QUOTE = "missing_quote"
B_STALE_QUOTE = "stale_quote"

_DEADLINE_TEXT_RE = re.compile(
    r"\b(?:hit|hits|reach|reaches|reached|touch|touches|touched)\b.{0,160}"
    r"\b(?:by|before|at\s+any\s+time\s+before)\b|"
    r"\b(?:deadline|range[-\s]?hit|all[-\s]?time\s+high|ath)\b",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
_TIME_RE = re.compile(r"\b(\d{1,2})(?::\d{2})?\s*(?:AM|PM|ET|EST|EDT|UTC|GMT|PT|PST|PDT|CT|CST|CDT)\b", re.IGNORECASE)
_DATE_ISO_RE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_DATE_COMPACT_RE = re.compile(r"\b(\d{2})([A-Z]{3})(\d{2})\b", re.IGNORECASE)
_MONTH_DAY_YEAR_RE = re.compile(
    r"\b("
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
    r")\s+(\d{1,2}),?\s+(20\d{2})\b",
    re.IGNORECASE,
)
_YEAR_MONTH_DAY_RE = re.compile(
    r"\b(20\d{2})\s+("
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
    r")\s+(\d{1,2})\b",
    re.IGNORECASE,
)

_MONTHS = {
    "jan": "01",
    "january": "01",
    "feb": "02",
    "february": "02",
    "mar": "03",
    "march": "03",
    "apr": "04",
    "april": "04",
    "may": "05",
    "jun": "06",
    "june": "06",
    "jul": "07",
    "july": "07",
    "aug": "08",
    "august": "08",
    "sep": "09",
    "september": "09",
    "oct": "10",
    "october": "10",
    "nov": "11",
    "november": "11",
    "dec": "12",
    "december": "12",
}


def build_core_trio_peer_coverage_audit_report(
    *,
    input_dir: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)

    warnings: list[dict[str, Any]] = []
    point_audit = _load_json(input_dir / "polymarket_point_in_time_typed_key_audit.json", warnings, "polymarket_point_in_time_typed_key_audit")
    enriched = _load_json(input_dir / "polymarket_taxonomy_shape_scout_enriched.json", warnings, "polymarket_taxonomy_shape_scout_enriched")
    cdna_scout = _load_json(input_dir / "cdna_crypto_basis_risk_scout.json", warnings, "cdna_crypto_basis_risk_scout")
    cdna_snapshot = _load_json(input_dir / "crypto_com_predict_cdna_research_snapshot.json", warnings, "crypto_com_predict_cdna_research_snapshot")
    normalized = _load_json(input_dir / "normalized_markets_v0.json", warnings, "normalized_markets_v0")
    ops_status = _load_json(input_dir / "relative_value_ops_status.json", warnings, "relative_value_ops_status")

    polymarket_rows = [_normalize_polymarket_row(row) for row in _rows_from_payload(point_audit)]
    polymarket_rows = [row for row in polymarket_rows if row is not None]
    excluded_deadline_rows = _count_deadline_like_enriched_rows(enriched)
    cdna_rows = _load_cdna_rows(cdna_scout=cdna_scout, cdna_snapshot=cdna_snapshot)
    kalshi_rows = _load_kalshi_rows(input_dir=input_dir, normalized_payload=normalized, warnings=warnings)

    families = _build_family_rows(
        polymarket_rows=polymarket_rows,
        cdna_rows=cdna_rows,
        kalshi_rows=kalshi_rows,
    )
    overlap_rows = _build_overlap_rows(
        polymarket_rows=polymarket_rows,
        cdna_rows=cdna_rows,
        kalshi_rows=kalshi_rows,
    )
    _attach_overlap_counts(families, overlap_rows)

    summary = _summary(
        families=families,
        overlap_rows=overlap_rows,
        polymarket_rows=polymarket_rows,
        cdna_rows=cdna_rows,
        kalshi_rows=kalshi_rows,
        excluded_deadline_rows=excluded_deadline_rows,
        input_dir=input_dir,
    )

    return {
        "schema_kind": SCHEMA_KIND,
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "input_dir": str(input_dir),
        "diagnostic_only": True,
        "saved_files_only": True,
        "inputs": {
            "polymarket_point_in_time_typed_key_audit": str(input_dir / "polymarket_point_in_time_typed_key_audit.json"),
            "polymarket_taxonomy_shape_scout_enriched": str(input_dir / "polymarket_taxonomy_shape_scout_enriched.json"),
            "cdna_crypto_basis_risk_scout": str(input_dir / "cdna_crypto_basis_risk_scout.json"),
            "crypto_com_predict_cdna_research_snapshot": str(input_dir / "crypto_com_predict_cdna_research_snapshot.json"),
            "normalized_markets_v0": str(input_dir / "normalized_markets_v0.json"),
            "relative_value_ops_status": str(input_dir / "relative_value_ops_status.json"),
        },
        "summary": summary,
        "families": families,
        "closest_existing_overlaps": overlap_rows[:50],
        "warnings": warnings,
        "safety": _safety_block(),
        "source_ops_status_present": isinstance(ops_status, dict),
    }


def write_core_trio_peer_coverage_audit_files(
    *,
    input_dir: Path,
    json_output: Path,
    markdown_output: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_core_trio_peer_coverage_audit_report(input_dir=input_dir, generated_at=generated_at)
    report["report_path"] = str(json_output)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_core_trio_peer_coverage_audit_markdown(report), encoding="utf-8")
    return report


def render_core_trio_peer_coverage_audit_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    families = report.get("families") or []
    lines = [
        "# Core Trio Peer Coverage Audit",
        "",
        "Saved-file-only diagnostic for Kalshi, Polymarket, and Crypto.com Predict / CDNA peer coverage. It does not create exact relationships, candidate pairs, paper actions, or executable rows.",
        "",
        "## Executive Summary",
        "",
        f"- total_core_trio_rows: `{summary.get('total_core_trio_rows', 0)}`",
        f"- peer_coverage_families: `{summary.get('peer_coverage_families', 0)}`",
        f"- families_with_kalshi_peer_rows: `{summary.get('families_with_kalshi_peer_rows', 0)}`",
        f"- families_without_kalshi_peer_rows: `{summary.get('families_without_kalshi_peer_rows', 0)}`",
        f"- strongest_overlap_family: `{summary.get('strongest_overlap_family') or 'none'}`",
        f"- polymarket_deadline_or_range_hit_rows_excluded: `{summary.get('polymarket_deadline_or_range_hit_rows_excluded', 0)}`",
        f"- exact_ready_rows: `0`",
        f"- paper_candidate_rows: `0`",
        "",
        "## Core Trio Coverage Table",
        "",
        "| Family | Polymarket Rows | PM Typed | PM Quoted | CDNA Rows | CDNA PIT | Kalshi Rows | Kalshi Typed | Overlaps | Quote Coverage | Blockers |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in families:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("family")),
                    str(row.get("polymarket_rows", 0)),
                    str(row.get("polymarket_typed_complete_rows", 0)),
                    str(row.get("polymarket_quoted_rows", 0)),
                    str(row.get("cdna_rows", 0)),
                    str(row.get("cdna_point_in_time_rows", 0)),
                    str(row.get("kalshi_candidate_rows_found", 0)),
                    str(row.get("kalshi_typed_complete_rows_found", 0)),
                    str(row.get("date_threshold_comparator_overlap_count", 0)),
                    str(row.get("quote_coverage_count", 0)),
                    _md(", ".join((row.get("blockers") or [])[:4]) or "none"),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Top 10 Missing Kalshi Peer Opportunities", ""])
    _append_targets_table(lines, summary.get("top_10_next_fetch_targets") or [])
    lines.extend(["", "## Top 10 Closest Existing Overlaps", ""])
    _append_overlap_table(lines, summary.get("top_10_closest_existing_overlaps") or [])
    lines.extend(["", "## Recommended Next Kalshi Fetch/Query Commands", ""])
    commands = summary.get("recommended_next_kalshi_fetch_or_query_commands") or []
    if commands:
        for command in commands:
            lines.append(f"- `{command}`")
    else:
        lines.append("- No command suggestions were generated from saved rows.")
    lines.extend(["", "## Top Blockers", "", "| Blocker | Count |", "|---|---:|"])
    for item in summary.get("top_blockers") or []:
        lines.append(f"| {_md(item.get('blocker'))} | {item.get('count', 0)} |")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- diagnostic_only: `true`",
            "- saved_files_only: `true`",
            "- live_fetch_attempted: `false`",
            "- creates_candidate_pairs: `false`",
            "- creates_exact_relationships: `false`",
            "- exact_ready_rows: `0`",
            "- paper_candidate_rows: `0`",
        ]
    )
    return "\n".join(lines) + "\n"


def _append_targets_table(lines: list[str], rows: list[dict[str, Any]]) -> None:
    if not rows:
        lines.append("_None._")
        return
    lines.extend(
        [
            "| # | Family | Priority | Reason | Suggested Query | Blockers |",
            "|---:|---|---:|---|---|---|",
        ]
    )
    for index, row in enumerate(rows[:10], start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    _md(row.get("family")),
                    f"{float(row.get('priority_score') or 0.0):.1f}",
                    _md(row.get("reason")),
                    _md(row.get("next_fetch_query_suggestion")),
                    _md(", ".join((row.get("blockers") or [])[:4]) or "none"),
                ]
            )
            + " |"
        )


def _append_overlap_table(lines: list[str], rows: list[dict[str, Any]]) -> None:
    if not rows:
        lines.append("_None._")
        return
    lines.extend(
        [
            "| # | Lane | Family | Score | Key | PM | CDNA | Kalshi | Blockers |",
            "|---:|---|---|---:|---|---|---|---|---|",
        ]
    )
    for index, row in enumerate(rows[:10], start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    _md(row.get("lane")),
                    _md(row.get("family")),
                    f"{float(row.get('overlap_score') or 0.0):.1f}",
                    _md(row.get("typed_key")),
                    _md(row.get("polymarket_label")),
                    _md(row.get("cdna_label")),
                    _md(row.get("kalshi_label")),
                    _md(", ".join((row.get("blockers") or [])[:4]) or "none"),
                ]
            )
            + " |"
        )


def _build_family_rows(
    *,
    polymarket_rows: list[dict[str, Any]],
    cdna_rows: list[dict[str, Any]],
    kalshi_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_family: dict[str, dict[str, Any]] = {
        family: {
            "family": family,
            "polymarket_rows": 0,
            "polymarket_typed_complete_rows": 0,
            "polymarket_quoted_rows": 0,
            "cdna_rows": 0,
            "cdna_point_in_time_rows": 0,
            "kalshi_candidate_rows_found": 0,
            "kalshi_typed_complete_rows_found": 0,
            "date_threshold_comparator_overlap_count": 0,
            "polymarket_kalshi_overlap_count": 0,
            "cdna_kalshi_overlap_count": 0,
            "polymarket_cdna_overlap_count": 0,
            "quote_coverage_count": 0,
            "missing_typed_keys": [],
            "next_fetch_query_suggestion": _fetch_suggestion_for_family(family),
            "blockers": [],
        }
        for family in CORE_FAMILIES
    }

    for row in polymarket_rows:
        family = _known_family(row.get("family"))
        target = by_family[family]
        target["polymarket_rows"] += 1
        if row.get("typed_complete"):
            target["polymarket_typed_complete_rows"] += 1
        if row.get("quote_complete"):
            target["polymarket_quoted_rows"] += 1
            target["quote_coverage_count"] += 1

    for row in cdna_rows:
        family = _known_family(row.get("family"))
        target = by_family[family]
        target["cdna_rows"] += 1
        if row.get("shape") == "point_in_time_threshold":
            target["cdna_point_in_time_rows"] += 1

    for row in kalshi_rows:
        family = _known_family(row.get("family"))
        target = by_family[family]
        target["kalshi_candidate_rows_found"] += 1
        if row.get("typed_complete"):
            target["kalshi_typed_complete_rows_found"] += 1
        if row.get("quote_complete"):
            target["quote_coverage_count"] += 1
        for missing_key in row.get("missing_typed_keys") or []:
            target["missing_typed_keys"].append(missing_key)

    for row in by_family.values():
        row["missing_typed_keys"] = sorted(set(row["missing_typed_keys"]))
        blockers = _family_blockers(row)
        row["blockers"] = blockers
        row["diagnostic_only"] = True
        row["exact_ready_rows"] = 0
        row["paper_candidate_rows"] = 0
    return [row for row in by_family.values() if _family_has_content(row)]


def _family_blockers(row: dict[str, Any]) -> list[str]:
    blockers: list[str] = [B_TITLE_ONLY_MATCH]
    if (row.get("polymarket_rows") or row.get("cdna_rows")) and not row.get("kalshi_candidate_rows_found"):
        blockers.append(B_NO_KALSHI_PEER)
    missing = set(row.get("missing_typed_keys") or [])
    blocker_map = {
        "threshold": B_KALSHI_MISSING_THRESHOLD,
        "comparator": B_KALSHI_MISSING_COMPARATOR,
        "target_date": B_KALSHI_MISSING_TARGET_DATE,
        "target_time": B_KALSHI_MISSING_TARGET_TIME,
        "settlement_source": B_KALSHI_MISSING_SETTLEMENT_SOURCE,
    }
    for key, blocker in blocker_map.items():
        if key in missing:
            blockers.append(blocker)
    if row.get("polymarket_rows") and (row.get("polymarket_typed_complete_rows") or 0) < (row.get("polymarket_rows") or 0):
        blockers.append(B_POLY_MISSING_SETTLEMENT_SOURCE)
    if row.get("cdna_rows"):
        blockers.append(B_CDNA_SETTLEMENT_SOURCE_UNVERIFIED)
        blockers.append(B_CDNA_BASIS_RISK_ONLY)
    if row.get("quote_coverage_count", 0) == 0:
        blockers.append(B_MISSING_QUOTE)
        blockers.append(B_STALE_QUOTE)
    return list(dict.fromkeys(blockers))


def _family_has_content(row: dict[str, Any]) -> bool:
    return bool(
        row.get("polymarket_rows")
        or row.get("cdna_rows")
        or row.get("kalshi_candidate_rows_found")
    )


def _build_overlap_rows(
    *,
    polymarket_rows: list[dict[str, Any]],
    cdna_rows: list[dict[str, Any]],
    kalshi_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    overlaps: list[dict[str, Any]] = []
    overlaps.extend(
        _overlap_between(
            lane="polymarket_vs_kalshi",
            left_rows=polymarket_rows,
            right_rows=kalshi_rows,
            left_name="polymarket",
            right_name="kalshi",
        )
    )
    overlaps.extend(
        _overlap_between(
            lane="cdna_vs_kalshi",
            left_rows=[row for row in cdna_rows if row.get("shape") == "point_in_time_threshold"],
            right_rows=kalshi_rows,
            left_name="cdna",
            right_name="kalshi",
        )
    )
    overlaps.extend(
        _overlap_between(
            lane="polymarket_vs_cdna",
            left_rows=polymarket_rows,
            right_rows=[row for row in cdna_rows if row.get("shape") == "point_in_time_threshold"],
            left_name="polymarket",
            right_name="cdna",
        )
    )
    overlaps.sort(key=lambda row: (-float(row.get("overlap_score") or 0.0), str(row.get("lane")), str(row.get("typed_key"))))
    return overlaps


def _overlap_between(
    *,
    lane: str,
    left_rows: list[dict[str, Any]],
    right_rows: list[dict[str, Any]],
    left_name: str,
    right_name: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    right_by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in right_rows:
        right_by_family[_known_family(row.get("family"))].append(row)
    for left in left_rows:
        if not left.get("typed_complete"):
            continue
        family = _known_family(left.get("family"))
        for right in right_by_family.get(family, []):
            if not right.get("typed_complete"):
                continue
            key_score = _typed_key_overlap_score(left, right)
            if key_score <= 0:
                continue
            blockers = [B_TITLE_ONLY_MATCH, "exact_payoff_not_proven"]
            if left.get("shape") != "point_in_time_threshold" or right.get("shape") != "point_in_time_threshold":
                blockers.append(B_POINT_TIME_DEADLINE_MISMATCH)
            if left_name == "cdna" or right_name == "cdna":
                blockers.append(B_CDNA_SETTLEMENT_SOURCE_UNVERIFIED)
                blockers.append(B_CDNA_BASIS_RISK_ONLY)
            if not left.get("quote_complete") or not right.get("quote_complete"):
                blockers.append(B_MISSING_QUOTE)
            rows.append(
                {
                    "lane": lane,
                    "family": family,
                    "overlap_score": key_score,
                    "typed_key": _typed_key_text(left),
                    "left_source": left_name,
                    "right_source": right_name,
                    "polymarket_label": _label(left) if left_name == "polymarket" else _label(right) if right_name == "polymarket" else None,
                    "cdna_label": _label(left) if left_name == "cdna" else _label(right) if right_name == "cdna" else None,
                    "kalshi_label": _label(left) if left_name == "kalshi" else _label(right) if right_name == "kalshi" else None,
                    "left_id": left.get("id"),
                    "right_id": right.get("id"),
                    "blockers": list(dict.fromkeys(blockers)),
                    "diagnostic_only": True,
                    "exact_ready": False,
                    "paper_candidate": False,
                }
            )
    return rows


def _typed_key_overlap_score(left: dict[str, Any], right: dict[str, Any]) -> float:
    score = 0.0
    if _asset_key(left) and _asset_key(left) == _asset_key(right):
        score += 25.0
    elif _known_family(left.get("family")) == _known_family(right.get("family")):
        score += 10.0
    if _threshold_key(left) is not None and _threshold_key(left) == _threshold_key(right):
        score += 25.0
    if _comparator_key(left) and _comparator_key(left) == _comparator_key(right):
        score += 20.0
    if _date_key(left) and _date_key(left) == _date_key(right):
        score += 20.0
    if score < 70.0:
        return 0.0
    if _time_key(left) and _time_key(right) and _time_key(left) == _time_key(right):
        score += 10.0
    return min(100.0, score)


def _attach_overlap_counts(families: list[dict[str, Any]], overlaps: list[dict[str, Any]]) -> None:
    by_family = {row["family"]: row for row in families}
    for overlap in overlaps:
        family = _known_family(overlap.get("family"))
        target = by_family.get(family)
        if target is None:
            continue
        target["date_threshold_comparator_overlap_count"] += 1
        lane = overlap.get("lane")
        if lane == "polymarket_vs_kalshi":
            target["polymarket_kalshi_overlap_count"] += 1
        elif lane == "cdna_vs_kalshi":
            target["cdna_kalshi_overlap_count"] += 1
        elif lane == "polymarket_vs_cdna":
            target["polymarket_cdna_overlap_count"] += 1


def _summary(
    *,
    families: list[dict[str, Any]],
    overlap_rows: list[dict[str, Any]],
    polymarket_rows: list[dict[str, Any]],
    cdna_rows: list[dict[str, Any]],
    kalshi_rows: list[dict[str, Any]],
    excluded_deadline_rows: int,
    input_dir: Path,
) -> dict[str, Any]:
    blockers: Counter[str] = Counter()
    for family in families:
        blockers.update(family.get("blockers") or [])
    for overlap in overlap_rows:
        blockers.update(overlap.get("blockers") or [])

    families_with_kalshi = [row["family"] for row in families if row.get("kalshi_candidate_rows_found")]
    families_without_kalshi = [
        row["family"]
        for row in families
        if (row.get("polymarket_rows") or row.get("cdna_rows")) and not row.get("kalshi_candidate_rows_found")
    ]
    strongest = max(
        families,
        key=lambda row: (
            int(row.get("date_threshold_comparator_overlap_count") or 0),
            int(row.get("quote_coverage_count") or 0),
            int(row.get("polymarket_typed_complete_rows") or 0) + int(row.get("cdna_point_in_time_rows") or 0),
        ),
        default={},
    )
    strongest_overlap_count = int(strongest.get("date_threshold_comparator_overlap_count") or 0)
    strongest_overlap_family = strongest.get("family") if strongest_overlap_count > 0 else None
    top_targets = _next_fetch_targets(families)
    return {
        "total_core_trio_rows": len(polymarket_rows) + len(cdna_rows) + len(kalshi_rows),
        "peer_coverage_families": len(families),
        "families_with_kalshi_peer_rows": len(families_with_kalshi),
        "families_without_kalshi_peer_rows": len(families_without_kalshi),
        "families_with_kalshi_peer_row_names": families_with_kalshi,
        "families_without_kalshi_peer_row_names": families_without_kalshi,
        "strongest_overlap_family": strongest_overlap_family,
        "strongest_overlap_score": strongest_overlap_count,
        "polymarket_rows": len(polymarket_rows),
        "polymarket_typed_complete_rows": sum(1 for row in polymarket_rows if row.get("typed_complete")),
        "polymarket_quoted_rows": sum(1 for row in polymarket_rows if row.get("quote_complete")),
        "polymarket_deadline_or_range_hit_rows_excluded": excluded_deadline_rows,
        "cdna_rows": len(cdna_rows),
        "cdna_point_in_time_rows": sum(1 for row in cdna_rows if row.get("shape") == "point_in_time_threshold"),
        "kalshi_candidate_rows_found": len(kalshi_rows),
        "kalshi_typed_complete_rows_found": sum(1 for row in kalshi_rows if row.get("typed_complete")),
        "date_threshold_comparator_overlap_count": len(overlap_rows),
        "quote_coverage_count": sum(1 for row in polymarket_rows + kalshi_rows if row.get("quote_complete")),
        "top_10_next_fetch_targets": top_targets[:10],
        "top_10_closest_existing_overlaps": overlap_rows[:10],
        "recommended_next_kalshi_fetch_or_query_commands": _recommended_commands(top_targets[:5], input_dir=input_dir),
        "top_blockers": [{"blocker": key, "count": value} for key, value in blockers.most_common(15)],
        "exact_ready_rows": 0,
        "paper_candidate_rows": 0,
        "execution_ready_rows": 0,
    }


def _next_fetch_targets(families: list[dict[str, Any]]) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for family in families:
        missing_kalshi = not family.get("kalshi_candidate_rows_found")
        pm_typed = int(family.get("polymarket_typed_complete_rows") or 0)
        cdna_pit = int(family.get("cdna_point_in_time_rows") or 0)
        quote_coverage = int(family.get("quote_coverage_count") or 0)
        blockers = list(family.get("blockers") or [])
        score = pm_typed * 4.0 + cdna_pit * 3.0 + quote_coverage
        if missing_kalshi:
            score += 8.0
        elif int(family.get("kalshi_typed_complete_rows_found") or 0) == 0:
            score += 4.0
        if score <= 0:
            continue
        reason = "missing saved Kalshi peer family" if missing_kalshi else "saved Kalshi peer rows need typed-key or quote completion"
        targets.append(
            {
                "family": family.get("family"),
                "priority_score": round(score, 2),
                "reason": reason,
                "next_fetch_query_suggestion": family.get("next_fetch_query_suggestion"),
                "polymarket_typed_complete_rows": pm_typed,
                "cdna_point_in_time_rows": cdna_pit,
                "kalshi_candidate_rows_found": family.get("kalshi_candidate_rows_found", 0),
                "blockers": blockers,
                "diagnostic_only": True,
            }
        )
    targets.sort(key=lambda row: (-float(row.get("priority_score") or 0.0), str(row.get("family"))))
    return targets


def _recommended_commands(targets: list[dict[str, Any]], *, input_dir: Path) -> list[str]:
    commands: list[str] = []
    for target in targets:
        family = str(target.get("family") or "")
        if family == "crypto_price_threshold":
            commands.append("python scan.py fetch-kalshi --series-ticker KXBTC --limit 200 --output reports/kalshi_crypto_price_threshold_snapshot.json")
            commands.append("python scan.py fetch-kalshi --series-ticker KXETH --limit 200 --output reports/kalshi_crypto_price_threshold_eth_snapshot.json")
        elif family in {"fed_fomc", "macro_rate"}:
            commands.append("python scan.py fetch-kalshi --series-ticker KXFED --limit 200 --output reports/kalshi_fed_fomc_snapshot.json")
        elif family == "company_metric":
            commands.append("python scan.py fetch-kalshi --series-ticker <COMPANY_METRIC_SERIES> --limit 200 --output reports/kalshi_company_metric_snapshot.json")
        elif family == "weather":
            commands.append("python scan.py fetch-kalshi --series-ticker <WEATHER_SERIES> --limit 200 --output reports/kalshi_weather_snapshot.json")
        elif family == "election":
            commands.append("python scan.py fetch-kalshi --series-ticker <ELECTION_SERIES> --limit 200 --output reports/kalshi_election_snapshot.json")
        elif family == "sports_futures":
            commands.append("python scan.py fetch-kalshi --series-ticker <SPORTS_SERIES> --limit 200 --output reports/kalshi_sports_futures_snapshot.json")
    commands.append(f"python scan.py normalize-market-snapshots --input-dir {input_dir} --json-output reports/normalized_markets_v0.json --coverage-output reports/normalized_markets_v0_coverage.json")
    return list(dict.fromkeys(commands))[:10]


def _normalize_polymarket_row(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    question = str(row.get("question") or row.get("title") or "")
    if _DEADLINE_TEXT_RE.search(question):
        return None
    family = _family_from_polymarket(row)
    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    return {
        "source": "polymarket",
        "id": row.get("row_id") or row.get("market_id") or row.get("condition_id"),
        "label": row.get("market_slug") or question,
        "family": family,
        "asset": _clean_asset(row.get("asset_or_family")),
        "threshold": _normalize_threshold(row.get("threshold")),
        "comparator": _normalize_comparator(row.get("comparator")),
        "target_date": _normalize_date(row.get("target_date")),
        "target_time": _normalize_time(row.get("target_time")),
        "timezone": row.get("timezone"),
        "settlement_source_present": bool(row.get("settlement_source_present")),
        "condition_id": row.get("condition_id"),
        "token_ids": list(row.get("token_ids") or []),
        "typed_complete": bool(row.get("typed_key_complete_for_review")),
        "quote_complete": _quote_complete(quote),
        "quote_timestamp": quote.get("quote_timestamp"),
        "shape": "point_in_time_threshold",
        "blockers": list(row.get("blockers") or []),
    }


def _load_cdna_rows(*, cdna_scout: Any, cdna_snapshot: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    scout_rows = _rows_from_payload(cdna_scout)
    if scout_rows:
        for row in scout_rows:
            if not isinstance(row, dict):
                continue
            cdna = row.get("cdna") if isinstance(row.get("cdna"), dict) else row
            rows.append(_normalize_cdna_row(row=row, cdna=cdna))
        return rows
    for row in _rows_from_payload(cdna_snapshot):
        if isinstance(row, dict):
            rows.append(_normalize_cdna_row(row=row, cdna=row))
    return rows


def _normalize_cdna_row(*, row: dict[str, Any], cdna: dict[str, Any]) -> dict[str, Any]:
    shape = str(row.get("shape_class") or cdna.get("market_shape_conservative") or cdna.get("market_shape_normalized") or "").strip().lower()
    title = cdna.get("selection_label") or cdna.get("title") or cdna.get("outcome_label")
    return {
        "source": "cdna",
        "id": row.get("row_id") or cdna.get("id") or title,
        "label": title,
        "family": "crypto_price_threshold",
        "asset": _clean_asset(cdna.get("asset")),
        "threshold": _normalize_threshold(cdna.get("threshold_value") or cdna.get("strike")),
        "comparator": _normalize_comparator(cdna.get("comparator") or cdna.get("threshold_operator")),
        "target_date": _normalize_date(cdna.get("target_date") or cdna.get("measurement_date")),
        "target_time": _normalize_time(cdna.get("measurement_time") or cdna.get("target_time")),
        "timezone": cdna.get("timezone"),
        "settlement_source_present": bool(cdna.get("settlement_source") or cdna.get("settlement_source_url")),
        "typed_complete": bool(
            shape == "point_in_time_threshold"
            and _clean_asset(cdna.get("asset"))
            and _normalize_threshold(cdna.get("threshold_value") or cdna.get("strike")) is not None
            and _normalize_comparator(cdna.get("comparator") or cdna.get("threshold_operator"))
            and _normalize_date(cdna.get("target_date") or cdna.get("measurement_date"))
        ),
        "quote_complete": False,
        "shape": shape or "unknown",
        "blockers": list(row.get("blockers") or []) + [B_CDNA_SETTLEMENT_SOURCE_UNVERIFIED, B_CDNA_BASIS_RISK_ONLY],
    }


def _load_kalshi_rows(
    *,
    input_dir: Path,
    normalized_payload: Any,
    warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = [
        _normalize_kalshi_row(row, source_file=str(input_dir / "normalized_markets_v0.json"))
        for row in _rows_from_payload(normalized_payload)
        if _is_kalshi_row(row)
    ]
    rows = [row for row in rows if row is not None]
    if rows:
        return rows

    fallback_rows: list[dict[str, Any]] = []
    skip_names = {
        "core_trio_peer_coverage_audit.json",
        "relative_value_ops_status.json",
        "cross_venue_opportunity_scout.json",
    }
    for path in sorted(input_dir.rglob("*kalshi*.json"))[:200]:
        if path.name in skip_names:
            continue
        payload = _load_json(path, warnings, f"kalshi_fallback:{path.name}", missing_ok=True)
        for row in _rows_from_payload(payload):
            if _is_kalshi_row(row) or "kalshi" in str(path.name).lower():
                normalized = _normalize_kalshi_row(row, source_file=str(path))
                if normalized is not None:
                    fallback_rows.append(normalized)
    unique: dict[str, dict[str, Any]] = {}
    for row in fallback_rows:
        unique[str(row.get("id") or len(unique))] = row
    return list(unique.values())


def _normalize_kalshi_row(row: Any, *, source_file: str) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    title = _row_text(row)
    family = _family_from_kalshi(row)
    if family not in CORE_FAMILIES:
        family = "other"
    settlement = row.get("settlement") if isinstance(row.get("settlement"), dict) else {}
    quote_depth = row.get("quote_depth") if isinstance(row.get("quote_depth"), dict) else {}
    ticker = str(row.get("ticker") or row.get("market_ticker") or row.get("id") or "")
    threshold = _kalshi_threshold(row)
    comparator = _kalshi_comparator(row)
    target_dt_raw = (
        settlement.get("resolution_time")
        or settlement.get("close_time")
        or row.get("close_time")
        or row.get("expiration_time")
        or row.get("expiration_datetime")
        or row.get("expected_expiration_time")
    )
    date = _normalize_date(target_dt_raw) or _normalize_date(title) or _date_from_kalshi_ticker(ticker)
    time_value = _normalize_time(target_dt_raw) or _normalize_time(title)
    settlement_source = bool(
        settlement.get("settlement_source_url")
        or settlement.get("settlement_rules_text")
        or settlement.get("source_kind")
        or row.get("rules_primary")
        or row.get("settlement_rules")
    )
    missing: list[str] = []
    if threshold is None:
        missing.append("threshold")
    if not comparator:
        missing.append("comparator")
    if not date:
        missing.append("target_date")
    if not time_value:
        missing.append("target_time")
    if not settlement_source:
        missing.append("settlement_source")
    return {
        "source": "kalshi",
        "id": row.get("ticker") or row.get("market_id") or row.get("id") or title,
        "label": row.get("ticker") or row.get("market_id") or title,
        "ticker": ticker,
        "family": family,
        "asset": _asset_from_text(title + " " + ticker),
        "threshold": threshold,
        "comparator": comparator,
        "target_date": date,
        "target_time": time_value,
        "timezone": "ET" if time_value and "T" in str(target_dt_raw) else None,
        "settlement_source_present": settlement_source,
        "typed_complete": not missing,
        "missing_typed_keys": missing,
        "quote_complete": _kalshi_quote_complete(quote_depth),
        "quote_timestamp": quote_depth.get("captured_at") or quote_depth.get("quote_timestamp"),
        "shape": "point_in_time_threshold",
        "source_file": source_file,
    }


def _is_kalshi_row(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    return str(row.get("venue") or row.get("source") or row.get("platform") or "").strip().lower() == "kalshi"


def _rows_from_payload(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("rows", "normalized_markets", "records", "markets", "data", "contracts"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _load_json(path: Path, warnings: list[dict[str, Any]], input_name: str, *, missing_ok: bool = False) -> Any:
    if not path.exists():
        if not missing_ok:
            warnings.append({"input": input_name, "path": str(path), "blocker": "saved_report_missing"})
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        warnings.append(
            {
                "input": input_name,
                "path": str(path),
                "blocker": "saved_report_parse_error",
                "error_type": type(exc).__name__,
            }
        )
        return None


def _family_from_polymarket(row: dict[str, Any]) -> str:
    raw = str(row.get("market_family") or row.get("family") or "").strip().lower()
    if raw in {"crypto_price", "crypto", "crypto_price_threshold"}:
        return "crypto_price_threshold"
    if raw in {"macro_rate", "fed_fomc", "fed", "fomc"}:
        return "fed_fomc"
    if raw in {"sports", "sports_futures"}:
        return "sports_futures"
    if raw in {"company_metric", "company", "equity"}:
        return "company_metric"
    if raw in {"election", "weather"}:
        return raw
    return _family_from_text(_row_text(row))


def _family_from_kalshi(row: dict[str, Any]) -> str:
    return _family_from_text(_row_text(row) + " " + str(row.get("event_ticker") or row.get("ticker") or ""))


def _family_from_text(text: str) -> str:
    text_l = text.lower()
    if any(token in text_l for token in ("bitcoin", "btc", "ethereum", "eth", "crypto")):
        return "crypto_price_threshold"
    if any(token in text_l for token in ("fed", "fomc", "federal funds", "interest rate", "rate cut", "rate hike")):
        return "fed_fomc"
    if any(token in text_l for token in ("election", "president", "senate", "governor", "mayor")):
        return "election"
    if any(token in text_l for token in ("world series", "super bowl", "nba", "nfl", "mlb", "nhl", "stanley cup", "championship")):
        return "sports_futures"
    if any(token in text_l for token in ("earnings", "revenue", "market cap", "stock", "shares", "tesla", "apple", "nvidia")):
        return "company_metric"
    if any(token in text_l for token in ("temperature", "weather", "hurricane", "rain", "snow")):
        return "weather"
    return "other"


def _known_family(value: Any) -> str:
    value_str = str(value or "other").strip()
    return value_str if value_str in CORE_FAMILIES else "other"


def _row_text(row: dict[str, Any]) -> str:
    parts = [
        row.get("title"),
        row.get("question"),
        row.get("event_title"),
        row.get("market_slug"),
        row.get("event_slug"),
        row.get("selection_label"),
    ]
    return " ".join(str(part or "") for part in parts)


def _kalshi_threshold(row: dict[str, Any]) -> float | None:
    for key in ("threshold", "strike", "strike_price", "cap_strike"):
        value = _normalize_threshold(row.get(key))
        if value is not None:
            return value
    ticker = str(row.get("ticker") or row.get("market_ticker") or "")
    match = re.search(r"(?:-|_)(?:T|B)(\d+(?:\.\d+)?)\b", ticker, re.IGNORECASE)
    if match:
        return _normalize_threshold(match.group(1))
    return _first_number(_row_text(row))


def _kalshi_comparator(row: dict[str, Any]) -> str | None:
    for key in ("comparator", "threshold_operator", "operator"):
        comparator = _normalize_comparator(row.get(key))
        if comparator:
            return comparator
    ticker = str(row.get("ticker") or row.get("market_ticker") or "")
    if re.search(r"(?:-|_)T\d", ticker, re.IGNORECASE):
        return "above"
    if re.search(r"(?:-|_)B\d", ticker, re.IGNORECASE):
        return "below"
    text_l = _row_text(row).lower()
    if any(token in text_l for token in (" above ", " greater than ", " over ", " at or above ")):
        return "above"
    if any(token in text_l for token in (" below ", " less than ", " under ", " at or below ")):
        return "below"
    return None


def _kalshi_quote_complete(quote_depth: dict[str, Any]) -> bool:
    if not isinstance(quote_depth, dict):
        return False
    return all(
        quote_depth.get(key) is not None
        for key in ("best_yes_bid", "best_yes_ask", "best_yes_bid_size", "best_yes_ask_size")
    ) and bool(quote_depth.get("captured_at") or quote_depth.get("quote_timestamp"))


def _quote_complete(quote: dict[str, Any]) -> bool:
    return all(quote.get(key) is not None for key in ("bid", "ask", "bid_size", "ask_size")) and bool(
        quote.get("quote_timestamp")
    )


def _asset_from_text(text: str) -> str | None:
    text_l = text.lower()
    if "bitcoin" in text_l or re.search(r"\bbtc\b", text_l):
        return "BTC"
    if "ethereum" in text_l or re.search(r"\beth\b", text_l):
        return "ETH"
    return None


def _clean_asset(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    if text in {"BTC", "BITCOIN"}:
        return "BTC"
    if text in {"ETH", "ETHEREUM"}:
        return "ETH"
    if text:
        return text
    return None


def _normalize_threshold(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    match = _NUMBER_RE.search(text.replace("$", ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _first_number(text: str) -> float | None:
    return _normalize_threshold(text)


def _normalize_comparator(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in {">", ">=", "above", "over", "greater_than", "greater than", "at_or_above", "at or above"}:
        return "above"
    if text in {"<", "<=", "below", "under", "less_than", "less than", "at_or_below", "at or below"}:
        return "below"
    if "above" in text or "greater" in text or "over" in text:
        return "above"
    if "below" in text or "less" in text or "under" in text:
        return "below"
    return text


def _normalize_date(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)
    iso_match = _DATE_ISO_RE.search(text)
    if iso_match:
        return "-".join(iso_match.groups())
    compact = _DATE_COMPACT_RE.search(text)
    if compact:
        day, month, yy = compact.groups()
        month_num = _MONTHS.get(month.lower())
        if month_num:
            return f"20{yy}-{month_num}-{int(day):02d}"
    month_day_year = _MONTH_DAY_YEAR_RE.search(text)
    if month_day_year:
        month, day, year = month_day_year.groups()
        month_num = _MONTHS.get(month.lower())
        if month_num:
            return f"{year}-{month_num}-{int(day):02d}"
    year_month_day = _YEAR_MONTH_DAY_RE.search(text)
    if year_month_day:
        year, month, day = year_month_day.groups()
        month_num = _MONTHS.get(month.lower())
        if month_num:
            return f"{year}-{month_num}-{int(day):02d}"
    return None


def _date_from_kalshi_ticker(ticker: str) -> str | None:
    match = _DATE_COMPACT_RE.search(ticker)
    if not match:
        return None
    day, month, yy = match.groups()
    month_num = _MONTHS.get(month.lower())
    if not month_num:
        return None
    return f"20{yy}-{month_num}-{int(day):02d}"


def _normalize_time(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)
    if "T" in text:
        match = re.search(r"T(\d{2}:\d{2})", text)
        if match:
            return match.group(1)
    match = _TIME_RE.search(text)
    if match:
        return match.group(0).upper()
    return None


def _count_deadline_like_enriched_rows(payload: Any) -> int:
    count = 0
    for row in _rows_from_payload(payload):
        if not isinstance(row, dict):
            continue
        shape = str(row.get("market_shape") or "").lower()
        text = _row_text(row)
        if shape in {"deadline_threshold_touch", "crypto_deadline_range_hit", "range_hit", "all_time_high_by_date"}:
            count += 1
        elif _DEADLINE_TEXT_RE.search(text):
            count += 1
    return count


def _asset_key(row: dict[str, Any]) -> str | None:
    return _clean_asset(row.get("asset"))


def _threshold_key(row: dict[str, Any]) -> float | None:
    value = row.get("threshold")
    if value is None:
        return None
    return round(float(value), 6)


def _comparator_key(row: dict[str, Any]) -> str | None:
    return _normalize_comparator(row.get("comparator"))


def _date_key(row: dict[str, Any]) -> str | None:
    return _normalize_date(row.get("target_date"))


def _time_key(row: dict[str, Any]) -> str | None:
    return _normalize_time(row.get("target_time"))


def _typed_key_text(row: dict[str, Any]) -> str:
    return "|".join(
        str(part or "")
        for part in (
            _known_family(row.get("family")),
            _asset_key(row),
            _threshold_key(row),
            _comparator_key(row),
            _date_key(row),
        )
    )


def _label(row: dict[str, Any]) -> str | None:
    value = row.get("label") or row.get("id")
    return str(value) if value is not None else None


def _fetch_suggestion_for_family(family: str) -> str:
    suggestions = {
        "crypto_price_threshold": "Fetch/normalize Kalshi BTC and ETH threshold series for matching date, threshold, comparator, time, and settlement-source fields.",
        "fed_fomc": "Fetch/normalize Kalshi Fed/FOMC rate series and capture target-rate semantics before any cross-venue review.",
        "macro_rate": "Fetch/normalize Kalshi macro-rate series with explicit target date, threshold, comparator, and settlement-source fields.",
        "election": "Fetch/normalize Kalshi election series keyed by office, candidate/party, jurisdiction, and final certification source.",
        "sports_futures": "Fetch/normalize Kalshi sports futures by league, season, team/selection, and championship scope.",
        "company_metric": "Search Kalshi company-metric series and normalize metric name, threshold, reporting period, and source filing/provider.",
        "weather": "Fetch/normalize Kalshi weather series with station/location, measurement window, threshold, and data-source fields.",
        "other": "Inventory saved Kalshi series for this family before attempting any peer comparison.",
    }
    return suggestions.get(family, suggestions["other"])


def _md(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _safety_block() -> dict[str, bool]:
    return {
        "diagnostic_only": True,
        "saved_files_only": True,
        "live_fetch_attempted": False,
        "creates_candidate_pairs": False,
        "creates_exact_relationships": False,
        "emits_paper_candidate": False,
        "affects_evaluator_gates": False,
        "exact_ready": False,
        "execution_ready": False,
        "paper_candidate": False,
    }
