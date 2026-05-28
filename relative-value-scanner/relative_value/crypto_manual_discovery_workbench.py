"""Saved-file-only manual discovery workbench for crypto payoff-calendar gaps.

Reads the saved crypto payoff-calendar audit and emits a per-venue checklist
of the highest-priority *manual* discovery actions Mason needs to take: which
Kalshi daily-5pm / hourly / weekly-Friday markets to record fresh quotes
against, which Polymarket up/down and hit/touch markets need explicit rules
text and token IDs captured, and which CDNA point-in-time ETH/BTC thresholds
need a fixture refresh.

Hard safety constraints respected by this module:
- Saved files only. No live API calls.
- Diagnostic only: every emitted target is a checklist item, never a candidate
  pair or trusted manifest. ``manual_manifest_candidate`` templates carry
  ``approved: false`` and ``can_create_paper_candidate: false``.
- Never claims exact same-payoff equivalence, never treats touch/direction as
  point-in-time, never lowers evaluator/exact gates.
- Never emits PAPER_CANDIDATE.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.crypto_payoff_calendar_audit import (
    CLASS_BASIS_RISK_ONLY,
    CLASS_EXACT_SHAPE_POSSIBLE,
    CLASS_MANUAL_RULES_NEEDED,
    CLASS_NO_CURRENT_PEER,
    SHAPE_DAILY_5PM_PRICE_THRESHOLD,
    SHAPE_DAILY_DIRECTION_UP_DOWN,
    SHAPE_DEADLINE_TOUCH_THRESHOLD,
    SHAPE_HOURLY_POINT_IN_TIME_PRICE,
    SHAPE_INTRADAY_TOUCH_THRESHOLD,
    SHAPE_POINT_IN_TIME_PRICE_THRESHOLD,
    SHAPE_RANGE_BUCKET_AT_TIME,
    SHAPE_WEEKLY_FRIDAY_CLOSE_THRESHOLD,
)


SCHEMA_VERSION = 1
SCHEMA_KIND = "crypto_manual_discovery_workbench_v1"
REPORT_SOURCE = "crypto_manual_discovery_workbench_v1"

AUDIT_INPUT = "crypto_payoff_calendar_audit.json"

# Per-payoff-family discovery groups.
_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("kalshi_daily_5pm", "kalshi", (SHAPE_DAILY_5PM_PRICE_THRESHOLD,)),
    ("kalshi_weekly_friday", "kalshi", (SHAPE_WEEKLY_FRIDAY_CLOSE_THRESHOLD,)),
    ("kalshi_hourly", "kalshi", (SHAPE_HOURLY_POINT_IN_TIME_PRICE,)),
    ("kalshi_range_bucket", "kalshi", (SHAPE_RANGE_BUCKET_AT_TIME,)),
    ("polymarket_up_down", "polymarket", (SHAPE_DAILY_DIRECTION_UP_DOWN,)),
    ("polymarket_hit_touch", "polymarket", (SHAPE_INTRADAY_TOUCH_THRESHOLD, SHAPE_DEADLINE_TOUCH_THRESHOLD)),
    ("polymarket_point_in_time", "polymarket", (
        SHAPE_DAILY_5PM_PRICE_THRESHOLD,
        SHAPE_HOURLY_POINT_IN_TIME_PRICE,
        SHAPE_POINT_IN_TIME_PRICE_THRESHOLD,
    )),
    ("cdna_point_in_time", "cdna", (
        SHAPE_POINT_IN_TIME_PRICE_THRESHOLD,
        SHAPE_DAILY_5PM_PRICE_THRESHOLD,
        SHAPE_HOURLY_POINT_IN_TIME_PRICE,
    )),
)


# Conservative per-venue evidence checklist. Keep these lists small + literal
# so they are easy for a human reviewer to read and check off.
_CHECKLISTS: dict[str, list[str]] = {
    "kalshi": [
        "Kalshi ticker (KX*-* format)",
        "Kalshi event_ticker",
        "Full settlement rules text (CF Benchmarks BRTI / ERTI window)",
        "Settlement close_time in UTC and the matching ET wall-clock time",
        "Settlement source URL or CF Benchmarks index reference",
        "Comparator (strict above vs at-or-above) confirmed from rules text",
        "Orderbook YES/NO best-bid/ask/size + capture timestamp",
        "Saved snapshot path the row was captured from",
    ],
    "polymarket": [
        "Polymarket event URL or slug + market URL",
        "Full rules / 'How will this resolve?' text",
        "Settlement source URL and named price index (Binance / Coinbase / Chainlink)",
        "Whether the market is 'hit any time before T' vs 'price at exact time T'",
        "Whether 'up or down' refers to open-to-close, prev-close-to-close, or last-trade-to-trade",
        "Observation window start + end timestamps (or single observation time)",
        "Comparator: > vs >= confirmed from rules text",
        "clobTokenIds for YES and NO outcomes",
        "Public CLOB top-of-book best_bid/best_ask/size + capture timestamp",
    ],
    "cdna": [
        "Crypto.com Predict / CDNA event ID and slug",
        "Full settlement rules text + the named Nadex/CDNA rule number",
        "Reference price index (Nadex BTC / Nadex ETH / CDNA U-BTC midpoint)",
        "Observation time + timezone (Eastern Time wall clock)",
        "Comparator and threshold confirmed",
        "Settlement source URL (CDNA rules page)",
        "Saved fixture path the row was captured from",
        "Live chance-to-win / depth (if a saved fixture captures it)",
    ],
}


def build_crypto_manual_discovery_workbench_report(
    *,
    input_dir: Path,
    audit_path: Path | None = None,
    generated_at: datetime | None = None,
    max_targets_per_group: int = 20,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None or generated.utcoffset() is None:
        raise ValueError("generated_at must include timezone information")
    warnings: list[dict[str, Any]] = []
    audit_payload = _load_json(audit_path or (input_dir / AUDIT_INPUT), warnings, "crypto_payoff_calendar_audit_input")
    audit_rows: list[dict[str, Any]] = []
    if isinstance(audit_payload, dict):
        rows = audit_payload.get("rows")
        if isinstance(rows, list):
            audit_rows = [row for row in rows if isinstance(row, dict)]

    groups: list[dict[str, Any]] = []
    target_counter: Counter[str] = Counter()
    asset_dates: dict[tuple[str, str], int] = defaultdict(int)
    for group_name, venue, shapes in _GROUPS:
        eligible = [
            row
            for row in audit_rows
            if row.get("venue") == venue and row.get("payoff_shape") in shapes
        ]
        eligible.sort(key=_target_sort_key)
        targets = []
        for row in eligible[: max_targets_per_group]:
            targets.append(_compose_target(row, group_name=group_name, venue=venue))
            target_counter[group_name] += 1
            key = (row.get("asset") or "UNKNOWN", row.get("target_date") or "unknown_date")
            asset_dates[key] += 1
        groups.append(
            {
                "group_name": group_name,
                "venue": venue,
                "payoff_shapes": list(shapes),
                "total_eligible_rows": len(eligible),
                "targets_emitted": len(targets),
                "evidence_checklist": list(_CHECKLISTS.get(venue, [])),
                "targets": targets,
            }
        )

    top_target = _pick_top_target(groups)
    summary = _summary(
        groups=groups,
        audit_payload=audit_payload,
        asset_dates=asset_dates,
        top_target=top_target,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "schema_kind": SCHEMA_KIND,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "input_dir": str(input_dir),
        "audit_path": str(audit_path or (input_dir / AUDIT_INPUT)),
        "diagnostic_only": True,
        "saved_files_only": True,
        "summary": summary,
        "groups": groups,
        "top_target": top_target,
        "warnings": warnings,
        "safety": _safety_block(),
    }


def write_crypto_manual_discovery_workbench_files(
    *,
    input_dir: Path,
    json_output: Path,
    markdown_output: Path,
    audit_path: Path | None = None,
    generated_at: datetime | None = None,
    max_targets_per_group: int = 20,
) -> dict[str, Any]:
    report = build_crypto_manual_discovery_workbench_report(
        input_dir=input_dir,
        audit_path=audit_path,
        generated_at=generated_at,
        max_targets_per_group=max_targets_per_group,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_crypto_manual_discovery_workbench_markdown(report), encoding="utf-8")
    return report


def render_crypto_manual_discovery_workbench_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines: list[str] = [
        "# Crypto Manual Discovery Workbench",
        "",
        "Saved-file-only checklist for the manual evidence Mason needs to collect before any "
        "shape on a saved Kalshi / Polymarket / CDNA crypto row can move past `manual_rules_needed`. "
        "Every target is diagnostic-only — `manual_manifest_candidate` templates carry "
        "`approved: false` and never reach the evaluator.",
        "",
        "## Executive Summary",
        "",
        f"- audit_path: `{report.get('audit_path')}`",
        f"- total_eligible_audit_rows: `{summary.get('total_eligible_audit_rows', 0)}`",
        f"- groups: `{summary.get('group_count', 0)}`",
        f"- targets_emitted: `{summary.get('targets_emitted', 0)}`",
        f"- top_target_group: `{summary.get('top_target_group')}`",
        f"- top_target_venue: `{summary.get('top_target_venue')}`",
        f"- top_target_asset: `{summary.get('top_target_asset')}`",
        f"- top_target_date: `{summary.get('top_target_date')}`",
        f"- top_target_payoff_shape: `{summary.get('top_target_payoff_shape')}`",
        f"- exact_ready_rows: `0`",
        f"- paper_candidate_rows: `0`",
        "",
        "## Discovery Groups",
        "",
    ]
    for group in report.get("groups") or []:
        lines.append(f"### {group['group_name']}  (venue: `{group['venue']}`)")
        lines.append("")
        lines.append(
            f"- payoff_shapes: `{','.join(group.get('payoff_shapes') or [])}`"
        )
        lines.append(f"- total_eligible_rows: `{group.get('total_eligible_rows', 0)}`")
        lines.append(f"- targets_emitted: `{group.get('targets_emitted', 0)}`")
        lines.append("")
        lines.append("**Evidence checklist (collect for each target):**")
        for item in group.get("evidence_checklist") or []:
            lines.append(f"- [ ] {item}")
        lines.append("")
        lines.append("| # | Asset | Date | Time | TZ | Shape | Threshold | Source | Identifiers | Top Blockers |")
        lines.append("|---:|---|---|---|---|---|---:|---|---|---|")
        targets = group.get("targets") or []
        if not targets:
            lines.append("| _none_ | | | | | | | | | |")
        else:
            for i, target in enumerate(targets, start=1):
                ident_parts = []
                for k in ("ticker", "event_ticker", "condition_id", "token_id"):
                    v = target.get(k)
                    if v:
                        ident_parts.append(f"{k}={v[:40]}")
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            str(i),
                            _md(target.get("asset")),
                            _md(target.get("target_date")),
                            _md(target.get("target_time")),
                            _md(target.get("observation_timezone")),
                            _md(target.get("payoff_shape")),
                            _md(_qd(target.get("threshold"))),
                            _md((target.get("settlement_source") or "")[:40]),
                            _md("; ".join(ident_parts)),
                            _md(",".join((target.get("blockers") or [])[:3])),
                        ]
                    )
                    + " |"
                )
        lines.append("")
    lines.extend(
        [
            "## Safety",
            "",
            "- diagnostic_only: `true`",
            "- saved_files_only: `true`",
            "- can_create_candidate_pair: `false`",
            "- can_create_paper_candidate: `false`",
            "- emits_trusted_manifest: `false`",
            "- approves_manual_manifest_candidates: `false`",
            "- exact_ready: `false`",
            "- paper_candidate: `false`",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Target composition
# ---------------------------------------------------------------------------


def _compose_target(row: dict[str, Any], *, group_name: str, venue: str) -> dict[str, Any]:
    identifiers = {
        "venue": row.get("venue"),
        "source_platform": row.get("source_platform"),
        "market_id": row.get("market_id"),
        "ticker": row.get("ticker"),
        "event_ticker": row.get("event_ticker"),
        "condition_id": row.get("condition_id"),
        "token_id": row.get("token_id"),
    }
    return {
        "group_name": group_name,
        "venue": venue,
        "audit_row_id": row.get("row_id"),
        "asset": row.get("asset"),
        "payoff_shape": row.get("payoff_shape"),
        "target_date": row.get("target_date"),
        "target_time": row.get("target_time"),
        "observation_timezone": row.get("observation_timezone"),
        "threshold": row.get("threshold"),
        "comparator": row.get("comparator"),
        "settlement_source": row.get("settlement_source"),
        "settlement_source_url": row.get("settlement_source_url"),
        "title": row.get("title") or row.get("question"),
        "ticker": row.get("ticker"),
        "event_ticker": row.get("event_ticker"),
        "condition_id": row.get("condition_id"),
        "token_id": row.get("token_id"),
        "comparability_class": row.get("comparability_class"),
        "blockers": list(row.get("blockers") or []),
        "rules_text_preview": row.get("rules_text_preview"),
        "manual_manifest_candidate": {
            "approved": False,
            "schema": "manual_manifest_candidate_v1",
            "venue": row.get("venue"),
            "asset": row.get("asset"),
            "target_date": row.get("target_date"),
            "target_time": row.get("target_time"),
            "observation_timezone": row.get("observation_timezone"),
            "payoff_shape": row.get("payoff_shape"),
            "comparator": row.get("comparator"),
            "threshold": row.get("threshold"),
            "settlement_source": row.get("settlement_source"),
            "settlement_source_url": row.get("settlement_source_url"),
            "identifiers": identifiers,
            "can_create_candidate_pair": False,
            "can_create_paper_candidate": False,
            "review_requirements": [
                "fill_all_evidence_checklist_items",
                "verify_settlement_source_url_with_named_index",
                "confirm_observation_time_matches_payoff_shape",
                "confirm_comparator_is_strict_or_inclusive_as_stated",
                "confirm_touch_window_vs_close_time_semantics",
            ],
        },
        "discovery_action_text": _action_text(row=row, venue=venue, group_name=group_name),
        "diagnostic_only": True,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "exact_ready": False,
        "paper_candidate": False,
    }


def _action_text(*, row: dict[str, Any], venue: str, group_name: str) -> str:
    shape = row.get("payoff_shape")
    asset = row.get("asset")
    date_str = row.get("target_date")
    time_str = row.get("target_time")
    tz_str = row.get("observation_timezone")
    if venue == "kalshi":
        return (
            f"Confirm the Kalshi {asset} {shape} market on {date_str} at {time_str} {tz_str or 'UTC'}: "
            "the saved row already has explicit CF Benchmarks BRTI/ERTI rules text; the *manual* "
            "actions are (1) record a fresh public-read-only orderbook snapshot via enrich-kalshi-orderbooks, "
            "(2) verify the threshold and comparator against the rules text, (3) confirm whether this is "
            "the daily 5pm close or an intraday hour. No live trading."
        )
    if venue == "polymarket":
        if shape == SHAPE_DAILY_DIRECTION_UP_DOWN:
            return (
                f"Capture the Polymarket {asset} up/down market for {date_str}: open the public Polymarket page "
                "for the event, copy the full 'How will this resolve?' rules text, identify which open/close "
                "the comparison uses (open-to-close vs prev-close-to-close), record the price index, save the "
                "clobTokenIds, and run a public no-auth CLOB book snapshot. Do not pair against any threshold market."
            )
        if shape in {SHAPE_INTRADAY_TOUCH_THRESHOLD, SHAPE_DEADLINE_TOUCH_THRESHOLD}:
            return (
                f"Capture the Polymarket {asset} hit/touch market for {date_str}: this is *touch-by-deadline*, not "
                "point-in-time. Record full rules text, confirm whether 'hit' means any 1-second tick or a candle close, "
                "save clobTokenIds, and tag the row as basis-risk-only versus any Kalshi daily-close market."
            )
        return (
            f"Capture the Polymarket {asset} point-in-time market for {date_str} at {time_str} {tz_str or 'ET'}: "
            "record full rules text, identify the named price index (Binance / Coinbase / Chainlink), confirm comparator "
            "(strict vs inclusive), save clobTokenIds, and run a public no-auth CLOB book snapshot before any peer review."
        )
    if venue == "cdna":
        return (
            f"Capture a CDNA fixture for the {asset} {shape} contract on {date_str}: save the public Crypto.com Predict "
            "page, record the Nadex/CDNA rule number, the named price index, the observation time in Eastern Time, "
            "the threshold, and the comparator. CDNA settles on a *different* index than Kalshi BRTI/ERTI — any "
            "Kalshi-CDNA pairing is basis-risk only even when the date and threshold align."
        )
    return "Diagnostic-only manual discovery target; collect the evidence checklist before any peer review."


def _target_sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
    # Prioritise exact_shape_possible → basis_risk_only → manual_rules_needed → no_current_peer.
    klass = row.get("comparability_class") or CLASS_NO_CURRENT_PEER
    klass_score = {
        CLASS_EXACT_SHAPE_POSSIBLE: 0,
        CLASS_BASIS_RISK_ONLY: 1,
        CLASS_MANUAL_RULES_NEEDED: 2,
        CLASS_NO_CURRENT_PEER: 3,
    }.get(klass, 9)
    blocker_count = len(row.get("blockers") or [])
    return (klass_score, blocker_count, str(row.get("row_id") or ""))


# ---------------------------------------------------------------------------
# Summary + helpers
# ---------------------------------------------------------------------------


def _pick_top_target(groups: list[dict[str, Any]]) -> dict[str, Any] | None:
    # Prefer the group whose first target carries the most-actionable class.
    best: dict[str, Any] | None = None
    best_score: tuple[int, int] = (99, 99)
    for group in groups:
        targets = group.get("targets") or []
        if not targets:
            continue
        head = targets[0]
        klass = head.get("comparability_class") or CLASS_NO_CURRENT_PEER
        klass_score = {
            CLASS_EXACT_SHAPE_POSSIBLE: 0,
            CLASS_BASIS_RISK_ONLY: 1,
            CLASS_MANUAL_RULES_NEEDED: 2,
            CLASS_NO_CURRENT_PEER: 3,
        }.get(klass, 9)
        # Bias toward Kalshi+ETH point-in-time and Polymarket point-in-time since those are
        # the lanes most likely to actually pair.
        venue_score = 0 if group["venue"] == "kalshi" else 1
        score = (klass_score, venue_score)
        if score < best_score:
            best_score = score
            best = {**head, "group_name": group["group_name"]}
    return best


def _summary(
    *,
    groups: list[dict[str, Any]],
    audit_payload: Any,
    asset_dates: dict[tuple[str, str], int],
    top_target: dict[str, Any] | None,
) -> dict[str, Any]:
    total_eligible = sum(g.get("total_eligible_rows", 0) for g in groups)
    targets_emitted = sum(g.get("targets_emitted", 0) for g in groups)
    audit_summary = (audit_payload or {}).get("summary") or {}
    top_asset_dates = sorted(asset_dates.items(), key=lambda kv: -kv[1])[:10]
    return {
        "total_eligible_audit_rows": total_eligible,
        "group_count": len(groups),
        "targets_emitted": targets_emitted,
        "top_target_group": (top_target or {}).get("group_name"),
        "top_target_venue": (top_target or {}).get("venue"),
        "top_target_asset": (top_target or {}).get("asset"),
        "top_target_date": (top_target or {}).get("target_date"),
        "top_target_payoff_shape": (top_target or {}).get("payoff_shape"),
        "top_target_comparability_class": (top_target or {}).get("comparability_class"),
        "audit_exact_shape_possible_rows": audit_summary.get("exact_shape_possible_rows", 0),
        "audit_basis_risk_only_rows": audit_summary.get("basis_risk_only_rows", 0),
        "audit_manual_rules_needed_rows": audit_summary.get("manual_rules_needed_rows", 0),
        "exact_ready_rows": 0,
        "paper_candidate_rows": 0,
        "top_asset_date_clusters": [
            {"asset": asset, "target_date": date, "count": count}
            for (asset, date), count in top_asset_dates
        ],
    }


def _safety_block() -> dict[str, Any]:
    return {
        "diagnostic_only": True,
        "saved_files_only": True,
        "live_fetch_attempted": False,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "exact_ready": False,
        "execution_ready": False,
        "paper_candidate": False,
        "emits_trusted_manifest": False,
        "approves_manual_manifest_candidates": False,
        "affects_evaluator_gates": False,
    }


def _load_json(path: Path, warnings: list[dict[str, Any]], reason: str) -> Any:
    if not path.exists():
        warnings.append({"source_file": str(path), "reason_code": f"{reason}_missing", "blocker": f"{reason}_missing"})
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(
            {
                "source_file": str(path),
                "reason_code": f"{reason}_unreadable",
                "blocker": f"{reason}_unreadable:{type(exc).__name__}",
            }
        )
        return None


def _md(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "/").replace("\n", " ")


def _qd(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".") or "0"
    return str(value)
