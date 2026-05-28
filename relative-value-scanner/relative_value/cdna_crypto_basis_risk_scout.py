from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.crypto_com_predict_cdna_saved_page_parser import (
    SHAPE_ALL_TIME_HIGH_BY_DATE,
    SHAPE_AMBIGUOUS,
    SHAPE_DEADLINE_HIT_BY_DATE,
    SHAPE_EARLIEST_TIMEFRAME_THRESHOLD_TOUCH,
    SHAPE_POINT_IN_TIME_THRESHOLD,
    SHAPE_YEAR_END_RANGE_BUCKET,
    _parse_json_fixture,
)


SCHEMA_VERSION = 1
SCHEMA_KIND = "cdna_crypto_basis_risk_scout_v1"
REPORT_SOURCE = "cdna_crypto_basis_risk_scout_v1"


ACTION_BASIS_RISK_REVIEW = "BASIS_RISK_REVIEW"
ACTION_MANUAL_REVIEW = "MANUAL_REVIEW"
ACTION_WATCH = "WATCH"
ALLOWED_ACTIONS = (ACTION_BASIS_RISK_REVIEW, ACTION_MANUAL_REVIEW, ACTION_WATCH)


# Blockers used by the scout. The scout never claims exact equality.
B_CDNA_FIXTURE_ONLY = "cdna_saved_fixture_only"
B_SETTLEMENT_SOURCE_UNVERIFIED = "settlement_source_unverified"
B_PRICE_SOURCE_UNVERIFIED = "price_source_unverified"
B_DEADLINE_VS_POINT = "deadline_vs_point_in_time_mismatch"
B_RANGE_VS_CLOSE = "range_hit_vs_close_price_mismatch"
B_ATH_METHODOLOGY = "all_time_high_methodology_unverified"
B_AMBIGUOUS_SHAPE = "ambiguous_contract_shape"
B_MISSING_THRESHOLD = "missing_threshold"
B_MISSING_TARGET_DATE = "missing_target_date"
B_MISSING_PRICE_SOURCE = "missing_price_source"
B_MISSING_SETTLEMENT_RULES = "missing_settlement_rules"
B_NO_KALSHI_PEER = "no_saved_kalshi_or_polymarket_crypto_peer"
B_ASSET_MISMATCH = "asset_mismatch"
B_THRESHOLD_DISTANCE_LARGE = "threshold_distance_large_relative_to_peer"
B_DATE_DISTANCE_LARGE = "settlement_date_distance_large"


# Default companion saved-data inputs we look at when scoring against Kalshi/Polymarket.
DEFAULT_KALSHI_PEER_FILES = (
    "normalized_markets_v0.json",
    "standardized_family_candidates.json",
)


def build_cdna_crypto_basis_risk_scout_report(
    *,
    input_fixture: Path,
    peer_input_dir: Path | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    warnings: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    cdna_rows: list[dict[str, Any]] = []
    if not input_fixture.exists():
        warnings.append(
            {
                "source_file": str(input_fixture),
                "reason_code": "input_missing",
                "blocker": "input_fixture_missing",
            }
        )
    else:
        try:
            cdna_rows = _parse_json_fixture(input_fixture, generated_at=generated)
        except Exception as exc:  # noqa: BLE001
            warnings.append(
                {
                    "source_file": str(input_fixture),
                    "reason_code": "cdna_parser_error",
                    "blocker": f"cdna_parser_error:{type(exc).__name__}",
                }
            )

    peer_rows_kalshi: list[dict[str, Any]] = []
    peer_rows_polymarket: list[dict[str, Any]] = []
    if peer_input_dir is not None and peer_input_dir.exists():
        peer_rows_kalshi, peer_rows_polymarket, peer_warnings = _load_peers(peer_input_dir)
        warnings.extend(peer_warnings)

    for cdna_row in cdna_rows:
        if not isinstance(cdna_row, dict):
            continue
        peer = _best_peer(cdna_row, peer_rows_kalshi, peer_rows_polymarket)
        rows.append(_compose_row(cdna_row=cdna_row, peer=peer, input_fixture=input_fixture))

    rows.sort(key=_sort_key)
    summary = _summary(rows, cdna_rows)
    return {
        "schema_kind": SCHEMA_KIND,
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "input_fixture": str(input_fixture),
        "peer_input_dir": str(peer_input_dir) if peer_input_dir is not None else None,
        "diagnostic_only": True,
        "summary": summary,
        "rows": rows,
        "warnings": warnings,
        "safety": _safety_block(),
    }


def write_cdna_crypto_basis_risk_scout_files(
    *,
    input_fixture: Path,
    json_output: Path,
    markdown_output: Path,
    peer_input_dir: Path | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_cdna_crypto_basis_risk_scout_report(
        input_fixture=input_fixture,
        peer_input_dir=peer_input_dir,
        generated_at=generated_at,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_cdna_crypto_basis_risk_scout_markdown(report), encoding="utf-8")
    return report


def render_cdna_crypto_basis_risk_scout_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    rows = report.get("rows") or []
    lines = [
        "# CDNA / Crypto.com Predict Crypto Basis-Risk Scout",
        "",
        "Saved-file-only diagnostic. CDNA / Crypto.com Predict rows are compared to saved Kalshi/Polymarket crypto rows where available. This report never emits exact-equality, paper-candidate, or executable rows. CDNA settlement source, methodology, and contract shape remain unverified; basis risk is the strongest claim this scout will make.",
        "",
        "## Executive Summary",
        "",
        f"- cdna_rows: `{summary.get('cdna_rows', 0)}`",
        f"- cdna_btc_rows: `{summary.get('cdna_btc_rows', 0)}`",
        f"- cdna_eth_rows: `{summary.get('cdna_eth_rows', 0)}`",
        f"- point_in_time_rows: `{summary.get('point_in_time_rows', 0)}`",
        f"- deadline_or_range_hit_rows: `{summary.get('deadline_or_range_hit_rows', 0)}`",
        f"- ambiguous_rows: `{summary.get('ambiguous_rows', 0)}`",
        f"- basis_risk_review_rows: `{summary.get('action_counts', {}).get('BASIS_RISK_REVIEW', 0)}`",
        f"- watch_rows: `{summary.get('action_counts', {}).get('WATCH', 0)}`",
        f"- manual_review_rows: `{summary.get('action_counts', {}).get('MANUAL_REVIEW', 0)}`",
        f"- exact_ready_rows: `0`",
        f"- paper_candidate_rows: `0`",
        f"- execution_ready_rows: `0`",
        "",
        "## Rows By Shape",
        "",
        "| Shape | Rows |",
        "|---|---:|",
    ]
    shape_counts = summary.get("shape_counts") or {}
    for shape, count in shape_counts.items():
        lines.append(f"| {shape} | {count} |")
    lines.extend(["", "## Top Blockers", "", "| Blocker | Count |", "|---|---:|"])
    for item in summary.get("top_blockers") or []:
        lines.append(f"| {item.get('blocker')} | {item.get('count')} |")
    lines.extend(
        [
            "",
            "## Promising ETH Point-In-Time Targets",
            "",
            "| CDNA Selection | Threshold | Comparator | Target Date | CDNA Outcome | Peer Venue | Peer Ticker | Action | Blockers |",
            "|---|---:|---|---|---|---|---|---|---|",
        ]
    )
    promising = [
        row
        for row in rows
        if row.get("shape_class") == SHAPE_POINT_IN_TIME_THRESHOLD
        and row.get("cdna", {}).get("asset") == "ETH"
    ][:25]
    if not promising:
        lines.append("| _no ETH point-in-time rows surfaced_ |  |  |  |  |  |  |  |  |")
    else:
        for row in promising:
            cdna = row.get("cdna", {})
            peer = row.get("peer") or {}
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md_cell(cdna.get("selection_label")),
                        _md_cell(cdna.get("threshold_value")),
                        _md_cell(cdna.get("comparator")),
                        _md_cell(cdna.get("target_date")),
                        _md_cell(cdna.get("outcome_label")),
                        _md_cell(peer.get("venue")),
                        _md_cell(peer.get("ticker_or_event")),
                        _md_cell(row.get("allowed_next_action")),
                        _md_cell(", ".join((row.get("blockers") or [])[:3]) or "none"),
                    ]
                )
                + " |"
            )
    lines.extend(["", "## All Scout Rows", "", "| Score | Action | Shape | CDNA Asset | CDNA Selection | Threshold | Target Date | Peer Venue | Peer Ticker | Blockers |", "|---:|---|---|---|---|---:|---|---|---|---|"])
    for row in rows[:200]:
        cdna = row.get("cdna", {})
        peer = row.get("peer") or {}
        lines.append(
            "| "
            + " | ".join(
                [
                    f"{row.get('basis_risk_priority_score', 0):.1f}",
                    _md_cell(row.get("allowed_next_action")),
                    _md_cell(row.get("shape_class")),
                    _md_cell(cdna.get("asset")),
                    _md_cell(cdna.get("selection_label")),
                    _md_cell(cdna.get("threshold_value")),
                    _md_cell(cdna.get("target_date")),
                    _md_cell(peer.get("venue")),
                    _md_cell(peer.get("ticker_or_event")),
                    _md_cell(", ".join((row.get("blockers") or [])[:4]) or "none"),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- diagnostic_only: `true`",
            "- can_create_candidate_pair: `false`",
            "- can_create_paper_candidate: `false`",
            "- exact_ready: `false`",
            "- execution_ready: `false`",
            "- paper_candidate: `false`",
            "- source_registry_unchanged: `true`",
            "- source_exact_payoff_compatible_with_kalshi: `false`",
        ]
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Peer loading
# ---------------------------------------------------------------------------


def _load_peers(input_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    kalshi: list[dict[str, Any]] = []
    polymarket: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    normalized_path = input_dir / "normalized_markets_v0.json"
    if normalized_path.exists():
        try:
            payload = json.loads(normalized_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(
                {
                    "source_file": str(normalized_path),
                    "reason_code": "peer_input_unreadable",
                    "blocker": f"peer_input_unreadable:{type(exc).__name__}",
                }
            )
        else:
            for row in payload.get("normalized_markets") or []:
                if not isinstance(row, dict):
                    continue
                venue = row.get("venue") or ""
                title = (row.get("title") or "").lower()
                if not _is_crypto_title(title):
                    continue
                if venue == "kalshi":
                    kalshi.append(row)
                elif venue == "polymarket":
                    polymarket.append(row)
    return kalshi, polymarket, warnings


def _is_crypto_title(title: str) -> bool:
    text = (title or "").lower()
    crypto_tokens = ("btc", "bitcoin", "eth", "ethereum", "solana", "sol", "doge", "ada", "xrp", "shib")
    return any(token in text for token in crypto_tokens)


def _best_peer(cdna_row: dict[str, Any], kalshi: list[dict[str, Any]], polymarket: list[dict[str, Any]]) -> dict[str, Any] | None:
    asset = (cdna_row.get("asset") or "").upper()
    if not asset:
        return None
    target_date = cdna_row.get("target_date") or cdna_row.get("measurement_date")
    threshold = cdna_row.get("threshold_value") or cdna_row.get("strike")
    candidates = kalshi + polymarket
    if not candidates:
        return None
    asset_token = "BTC" if asset == "BTC" else "ETH" if asset == "ETH" else asset
    asset_lc = asset_token.lower()
    matches: list[dict[str, Any]] = []
    for row in candidates:
        title = (row.get("title") or "").lower()
        ticker = (row.get("ticker") or "").lower()
        text = f"{title} {ticker}"
        if asset_token == "BTC" and ("btc" not in text and "bitcoin" not in text):
            continue
        if asset_token == "ETH" and ("eth" not in text and "ethereum" not in text):
            continue
        matches.append(row)
    if not matches:
        return None
    # Closest by threshold (if both available) else by date.
    target_threshold = _to_float(threshold)
    target_date_dt = _parse_iso_date_loose(target_date) if target_date else None
    best: tuple[float, dict[str, Any]] | None = None
    for row in matches:
        peer_threshold = _peer_threshold_from_row(row)
        peer_date = _parse_iso_date_loose(row.get("settlement", {}).get("close_time") or row.get("settlement", {}).get("resolution_time"))
        distance = 0.0
        if target_threshold is not None and peer_threshold is not None:
            distance += abs(target_threshold - peer_threshold) / max(1.0, target_threshold)
        else:
            distance += 1.0  # missing threshold information adds penalty
        if target_date_dt is not None and peer_date is not None:
            distance += abs((target_date_dt - peer_date).days) / 365.0
        else:
            distance += 0.5
        if best is None or distance < best[0]:
            best = (distance, row)
    return best[1] if best else None


def _peer_threshold_from_row(row: dict[str, Any]) -> float | None:
    ticker = row.get("ticker") or ""
    match = re.search(r"T(\d+(?:\.\d+)?)$", str(ticker))
    if match:
        return _to_float(match.group(1))
    return None


# ---------------------------------------------------------------------------
# Row composition
# ---------------------------------------------------------------------------


def _compose_row(
    *,
    cdna_row: dict[str, Any],
    peer: dict[str, Any] | None,
    input_fixture: Path,
) -> dict[str, Any]:
    shape = (cdna_row.get("market_shape_conservative") or "").lower()
    shape_class_constant = _shape_class_constant(shape)
    asset = cdna_row.get("asset")
    threshold = cdna_row.get("threshold_value") or cdna_row.get("strike")
    cdna_summary = {
        "asset": asset,
        "title": cdna_row.get("title"),
        "selection_label": cdna_row.get("selection_label") or cdna_row.get("outcome_label"),
        "outcome_label": cdna_row.get("outcome_label"),
        "shape_class": shape_class_constant,
        "market_shape_conservative": cdna_row.get("market_shape_conservative"),
        "market_type": cdna_row.get("market_type"),
        "threshold_value": threshold,
        "comparator": cdna_row.get("comparator") or cdna_row.get("threshold_operator"),
        "target_date": cdna_row.get("target_date") or cdna_row.get("measurement_date"),
        "lower": cdna_row.get("lower"),
        "upper": cdna_row.get("upper"),
        "source_url": cdna_row.get("source_url"),
        "settlement_source": cdna_row.get("settlement_source"),
        "settlement_source_url": cdna_row.get("settlement_source_url"),
        "price_source_index": cdna_row.get("price_source_index"),
        "raw_source_file": cdna_row.get("raw_source_file"),
        "outcome": cdna_row.get("outcome_label"),
    }
    peer_summary: dict[str, Any] | None = None
    if peer is not None:
        peer_summary = {
            "venue": peer.get("venue"),
            "ticker_or_event": peer.get("ticker") or peer.get("event_ticker") or peer.get("event_slug"),
            "title": peer.get("title"),
            "settlement_close_time": (peer.get("settlement") or {}).get("close_time"),
            "settlement_resolution_time": (peer.get("settlement") or {}).get("resolution_time"),
            "peer_threshold": _peer_threshold_from_row(peer),
        }
    blockers = _row_blockers(cdna_row=cdna_row, peer=peer_summary, shape_class=shape_class_constant)
    action = _next_action(blockers=blockers, shape_class=shape_class_constant, peer=peer_summary)
    score = _basis_risk_priority_score(
        cdna=cdna_summary, peer=peer_summary, shape_class=shape_class_constant, blockers=blockers
    )
    next_action_text = _next_action_text(action=action, blockers=blockers, shape_class=shape_class_constant)
    evidence_summary = _evidence_summary(cdna=cdna_summary, peer=peer_summary, shape_class=shape_class_constant)
    return {
        "row_id": _row_id(cdna_row=cdna_row, peer=peer_summary),
        "shape_class": shape_class_constant,
        "cdna": cdna_summary,
        "peer": peer_summary,
        "blockers": blockers,
        "allowed_next_action": action,
        "next_action_text": next_action_text,
        "basis_risk_priority_score": round(score, 2),
        "evidence_summary": evidence_summary,
        "source_files": [str(input_fixture), cdna_row.get("raw_source_file")],
        "diagnostic_only": True,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "exact_ready": False,
        "execution_ready": False,
        "paper_candidate": False,
        "affects_evaluator_gates": False,
        "source_exact_payoff_compatible_with_kalshi": False,
    }


def _row_blockers(
    *,
    cdna_row: dict[str, Any],
    peer: dict[str, Any] | None,
    shape_class: str | None,
) -> list[str]:
    blockers: list[str] = [B_CDNA_FIXTURE_ONLY, B_SETTLEMENT_SOURCE_UNVERIFIED]
    if not cdna_row.get("price_source_index"):
        blockers.append(B_PRICE_SOURCE_UNVERIFIED)
        blockers.append(B_MISSING_PRICE_SOURCE)
    if not cdna_row.get("settlement_rule_text") and not cdna_row.get("settlement_rules_methodology_text"):
        blockers.append(B_MISSING_SETTLEMENT_RULES)
    if shape_class in {SHAPE_DEADLINE_HIT_BY_DATE, SHAPE_EARLIEST_TIMEFRAME_THRESHOLD_TOUCH}:
        blockers.append(B_DEADLINE_VS_POINT)
    if shape_class == SHAPE_YEAR_END_RANGE_BUCKET:
        blockers.append(B_RANGE_VS_CLOSE)
    if shape_class == SHAPE_ALL_TIME_HIGH_BY_DATE:
        blockers.append(B_ATH_METHODOLOGY)
    if shape_class == SHAPE_AMBIGUOUS:
        blockers.append(B_AMBIGUOUS_SHAPE)
    threshold = cdna_row.get("threshold_value") or cdna_row.get("strike")
    if threshold is None and shape_class not in {SHAPE_YEAR_END_RANGE_BUCKET, SHAPE_ALL_TIME_HIGH_BY_DATE}:
        blockers.append(B_MISSING_THRESHOLD)
    if not (cdna_row.get("target_date") or cdna_row.get("measurement_date")):
        blockers.append(B_MISSING_TARGET_DATE)
    if peer is None:
        blockers.append(B_NO_KALSHI_PEER)
    else:
        peer_threshold = peer.get("peer_threshold")
        target_threshold = _to_float(threshold)
        if peer_threshold is not None and target_threshold is not None:
            rel_distance = abs(peer_threshold - target_threshold) / max(1.0, abs(target_threshold))
            if rel_distance > 0.20:
                blockers.append(B_THRESHOLD_DISTANCE_LARGE)
        peer_date = _parse_iso_date_loose(peer.get("settlement_close_time") or peer.get("settlement_resolution_time"))
        cdna_date = _parse_iso_date_loose(cdna_row.get("target_date") or cdna_row.get("measurement_date"))
        if peer_date is not None and cdna_date is not None:
            day_distance = abs((peer_date - cdna_date).days)
            if day_distance > 60:
                blockers.append(B_DATE_DISTANCE_LARGE)
    return list(dict.fromkeys(blockers))


def _next_action(*, blockers: list[str], shape_class: str | None, peer: dict[str, Any] | None) -> str:
    blocker_set = set(blockers)
    if shape_class in {SHAPE_DEADLINE_HIT_BY_DATE, SHAPE_EARLIEST_TIMEFRAME_THRESHOLD_TOUCH, SHAPE_YEAR_END_RANGE_BUCKET}:
        return ACTION_BASIS_RISK_REVIEW
    if shape_class == SHAPE_ALL_TIME_HIGH_BY_DATE:
        return ACTION_BASIS_RISK_REVIEW
    if shape_class == SHAPE_AMBIGUOUS:
        return ACTION_WATCH
    if shape_class == SHAPE_POINT_IN_TIME_THRESHOLD:
        if peer is None:
            return ACTION_WATCH
        if B_MISSING_THRESHOLD in blocker_set or B_MISSING_TARGET_DATE in blocker_set:
            return ACTION_MANUAL_REVIEW
        return ACTION_MANUAL_REVIEW
    return ACTION_WATCH


def _next_action_text(*, action: str, blockers: list[str], shape_class: str | None) -> str:
    if action == ACTION_BASIS_RISK_REVIEW:
        if shape_class in {SHAPE_DEADLINE_HIT_BY_DATE, SHAPE_EARLIEST_TIMEFRAME_THRESHOLD_TOUCH}:
            return "Deadline / earliest-timeframe touch contract — basis-risk review only. Cannot be exact-paired with a Kalshi point-in-time close."
        if shape_class == SHAPE_YEAR_END_RANGE_BUCKET:
            return "Range-bucket close-price contract — basis-risk review only. Cannot be exact-paired with a Kalshi above-threshold contract."
        if shape_class == SHAPE_ALL_TIME_HIGH_BY_DATE:
            return "All-time-high methodology unverified; basis-risk review only. Capture the CDNA Source Agency methodology before any further pairing."
        return "Basis-risk review only."
    if action == ACTION_MANUAL_REVIEW:
        return "Point-in-time threshold candidate with peer present — manual review of settlement source, price index, and ticker mapping required before any exact comparison."
    return "Watch-only diagnostic; no peer or insufficient evidence."


def _evidence_summary(*, cdna: dict[str, Any], peer: dict[str, Any] | None, shape_class: str | None) -> str:
    parts = [
        f"cdna={cdna.get('asset')}:{cdna.get('selection_label')}",
        f"shape={shape_class}",
        f"threshold={cdna.get('threshold_value')}",
        f"target_date={cdna.get('target_date')}",
    ]
    if peer is not None:
        parts.append(f"peer={peer.get('venue')}:{peer.get('ticker_or_event')}")
    else:
        parts.append("peer=none")
    return " | ".join(str(part) for part in parts if part)


def _basis_risk_priority_score(
    *,
    cdna: dict[str, Any],
    peer: dict[str, Any] | None,
    shape_class: str | None,
    blockers: list[str],
) -> float:
    score = 0.0
    if shape_class == SHAPE_POINT_IN_TIME_THRESHOLD:
        score += 12.0
    elif shape_class == SHAPE_DEADLINE_HIT_BY_DATE or shape_class == SHAPE_EARLIEST_TIMEFRAME_THRESHOLD_TOUCH:
        score += 6.0
    elif shape_class == SHAPE_YEAR_END_RANGE_BUCKET:
        score += 4.0
    elif shape_class == SHAPE_ALL_TIME_HIGH_BY_DATE:
        score += 3.0
    else:
        score += 1.0
    if peer is not None:
        score += 4.0
    if cdna.get("price_source_index"):
        score += 3.0
    if cdna.get("threshold_value") is not None:
        score += 2.0
    if cdna.get("target_date"):
        score += 2.0
    penalties = {
        B_NO_KALSHI_PEER: -2.0,
        B_THRESHOLD_DISTANCE_LARGE: -3.0,
        B_DATE_DISTANCE_LARGE: -3.0,
        B_AMBIGUOUS_SHAPE: -5.0,
        B_RANGE_VS_CLOSE: -3.0,
        B_DEADLINE_VS_POINT: -3.0,
        B_ATH_METHODOLOGY: -2.0,
        B_MISSING_THRESHOLD: -3.0,
        B_MISSING_TARGET_DATE: -2.0,
        B_PRICE_SOURCE_UNVERIFIED: -2.0,
        B_MISSING_SETTLEMENT_RULES: -2.0,
    }
    for blocker in set(blockers):
        score += penalties.get(blocker, 0.0)
    return max(0.0, min(100.0, score))


def _sort_key(row: dict[str, Any]) -> tuple[float, int, str]:
    score = -float(row.get("basis_risk_priority_score") or 0.0)
    has_peer_rank = 0 if row.get("peer") else 1
    return (score, has_peer_rank, str(row.get("row_id") or ""))


def _summary(rows: list[dict[str, Any]], cdna_rows: list[dict[str, Any]]) -> dict[str, Any]:
    btc_rows = sum(1 for r in cdna_rows if (r.get("asset") or "").upper() == "BTC")
    eth_rows = sum(1 for r in cdna_rows if (r.get("asset") or "").upper() == "ETH")
    point_in_time = sum(1 for r in cdna_rows if r.get("market_shape_conservative") == "point_in_time_threshold")
    deadline_or_range = sum(
        1
        for r in cdna_rows
        if r.get("market_shape_conservative")
        in {"deadline_threshold_touch", "earliest_timeframe_threshold_touch", "year_end_range_bucket"}
    )
    ambiguous = sum(1 for r in cdna_rows if r.get("market_shape_conservative") in {"ambiguous", "unknown"} or not r.get("market_shape_conservative"))
    shape_counts = Counter(r.get("market_shape_conservative") or "unknown" for r in cdna_rows)
    blocker_counts: Counter[str] = Counter()
    action_counts: Counter[str] = Counter()
    for row in rows:
        for blocker in row.get("blockers") or []:
            blocker_counts[blocker] += 1
        action_counts[row.get("allowed_next_action") or ACTION_WATCH] += 1
    return {
        "cdna_rows": len(cdna_rows),
        "cdna_btc_rows": btc_rows,
        "cdna_eth_rows": eth_rows,
        "point_in_time_rows": point_in_time,
        "deadline_or_range_hit_rows": deadline_or_range,
        "ambiguous_rows": ambiguous,
        "shape_counts": dict(shape_counts),
        "top_blockers": [
            {"blocker": b, "count": c} for b, c in blocker_counts.most_common(15)
        ],
        "action_counts": dict(action_counts),
        "exact_ready_rows": 0,
        "paper_candidate_rows": 0,
        "execution_ready_rows": 0,
        "scout_row_count": len(rows),
    }


def _safety_block() -> dict[str, bool]:
    return {
        "diagnostic_only": True,
        "saved_files_only": True,
        "live_fetch_attempted": False,
        "execution_or_order_logic_added": False,
        "account_or_auth_logic_added": False,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "exact_ready": False,
        "execution_ready": False,
        "paper_candidate": False,
        "affects_evaluator_gates": False,
        "source_registry_unchanged": True,
        "source_exact_payoff_compatible_with_kalshi": False,
    }


def _row_id(*, cdna_row: dict[str, Any], peer: dict[str, Any] | None) -> str:
    parts = [
        str(cdna_row.get("asset") or "ASSET"),
        str(cdna_row.get("market_type") or cdna_row.get("market_shape_conservative") or "shape"),
        str(cdna_row.get("selection_label") or cdna_row.get("outcome_label") or "selection"),
    ]
    if peer is not None:
        parts.append(str(peer.get("ticker_or_event") or peer.get("venue") or "peer"))
    return "::".join(_safe_token(part) for part in parts)


def _safe_token(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.\-]+", "_", str(value or "")).strip("_")
    return text[:64] or "x"


def _shape_class_constant(market_shape_conservative: str | None) -> str | None:
    if not market_shape_conservative:
        return None
    mapping = {
        "year_end_range_bucket": SHAPE_YEAR_END_RANGE_BUCKET,
        "deadline_threshold_touch": SHAPE_DEADLINE_HIT_BY_DATE,
        "earliest_timeframe_threshold_touch": SHAPE_EARLIEST_TIMEFRAME_THRESHOLD_TOUCH,
        "all_time_high_by_date": SHAPE_ALL_TIME_HIGH_BY_DATE,
        "point_in_time_threshold": SHAPE_POINT_IN_TIME_THRESHOLD,
        "ambiguous": SHAPE_AMBIGUOUS,
        "unknown": SHAPE_AMBIGUOUS,
    }
    return mapping.get(market_shape_conservative.lower(), market_shape_conservative.upper())


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None


def _parse_iso_date_loose(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    # Try ISO first.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    # Try month-name format like "December 31, 2026".
    for fmt in ("%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _md_cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "/").replace("\n", " ")
