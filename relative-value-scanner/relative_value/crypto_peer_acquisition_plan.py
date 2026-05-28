"""Saved-file-only crypto peer acquisition planner.

Loads the typed-complete Kalshi crypto point-in-time grid plus saved
Polymarket point-in-time audit, Polymarket enriched taxonomy, CDNA snapshot
and core-trio peer-coverage audit, and turns the Kalshi (asset, target_date,
target_time, threshold, comparator, settlement source) grid into precise,
diagnostic-only acquisition targets for:

- Polymarket public discovery queries (no live calls performed here)
- Polymarket public CLOB book refresh candidates if token IDs already exist
  in the enriched taxonomy
- CDNA manual / public fixture targets
- Kalshi orderbook refresh targets

Hard safety constraints:
- Saved files only. No live API calls, no auth, no orders, no fills, no
  cancels, no account/balance/positions, no wallet/signing/private keys,
  no browser automation, no geolocation / proxy / VPN / Tor / Cloudflare
  bypass.
- Recommends only existing safe repo commands. When no safe command is
  known, emits the ``no_safe_fetch_command_found`` blocker and the literal
  marker ``needs command discovery`` rather than inventing a command name.
- Never claims exact same-payoff equivalence with a peer; peer-hint fields
  are diagnostic only and never create candidate pairs.
- Never emits a paper-candidate or exact-ready row. Evaluator/exact gates
  are unaffected.
- Never treats deadline / range-hit / range-bucket rows as point-in-time;
  these are excluded from the acquisition grid.
"""

from __future__ import annotations

import json
import shlex
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
SCHEMA_KIND = "crypto_peer_acquisition_plan_v1"
REPORT_SOURCE = "crypto_peer_acquisition_plan_v1"

KALSHI_AUDIT_INPUT = "kalshi_crypto_typed_key_audit.json"
POLYMARKET_PIT_AUDIT_INPUT = "polymarket_point_in_time_typed_key_audit.json"
POLYMARKET_ENRICHED_INPUT = "polymarket_taxonomy_shape_scout_enriched.json"
CDNA_INPUT = "cdna_crypto_basis_risk_scout.json"
CDNA_SNAPSHOT_INPUT = "crypto_com_predict_cdna_research_snapshot.json"
CORE_TRIO_INPUT = "core_trio_peer_coverage_audit.json"

# Asset priority — higher liquidity / coverage first.
_ASSET_PRIORITY: tuple[tuple[str, int], ...] = (
    ("BTC", 100),
    ("ETH", 80),
    ("SOL", 40),
)

# Blockers introduced by this planner.
B_MISSING_POLYMARKET_PEER = "missing_polymarket_peer_for_kalshi_grid"
B_MISSING_CDNA_PEER = "missing_cdna_peer_for_kalshi_grid"
B_MISSING_KALSHI_ORDERBOOK_QUOTE = "missing_kalshi_orderbook_quote"
B_MISSING_POLYMARKET_CLOB_QUOTE = "missing_polymarket_clob_quote"
B_MISSING_CDNA_QUOTE = "missing_cdna_quote_or_price"
B_SETTLEMENT_SOURCE_UNVERIFIED = "settlement_source_unverified"
B_PEER_DATE_THRESHOLD_GAP = "peer_date_threshold_gap"
B_NO_SAFE_FETCH_COMMAND_FOUND = "no_safe_fetch_command_found"
B_POLYMARKET_TARGETED_QUERY_COMMAND_MISSING = "polymarket_targeted_query_command_missing"
B_KALSHI_ORDERBOOK_INPUT_SNAPSHOT_MISSING = "kalshi_orderbook_input_snapshot_missing_for_crypto_grid"
B_KALSHI_FRESH_CRYPTO_SNAPSHOT_REQUIRED = "kalshi_fresh_crypto_snapshot_required"
B_UNSUPPORTED_COMMAND_FLAG = "unsupported_command_flag"

NEEDS_COMMAND_DISCOVERY = "needs command discovery"
KALSHI_ORDERBOOK_STALE_SNAPSHOT_AGE_OVERRIDE_HOURS = 100000

# Existing safe repo commands referenced by the planner. Keep this table in
# sync with scan.py; never invent a command name.
_SAFE_COMMANDS: dict[str, dict[str, Any]] = {
    "discover-polymarket-crypto-markets": {
        "scope": "polymarket_public_discovery",
        "description": "Public no-auth Polymarket Gamma/CLOB read-only crypto discovery.",
        "live_calls": "public_no_auth_only",
        "capability": "broad_or_targeted_discovery",
        "targeted_query_supported": True,
        "allowed_flags": [
            "--output-dir",
            "--limit",
            "--timeout-seconds",
            "--max-pages",
            "--include-books",
            "--json-output",
            "--markdown-output",
            "--query",
            "--queries-file",
            "--asset",
            "--target-date",
        ],
    },
    "refresh-polymarket-clob-for-taxonomy-candidates": {
        "scope": "polymarket_clob_refresh",
        "description": "Public no-auth Polymarket CLOB book refresh for taxonomy candidates.",
        "live_calls": "public_no_auth_only",
        "allowed_flags": [
            "--taxonomy-json",
            "--output-dir",
            "--json-output",
            "--enriched-output",
            "--markdown-output",
            "--max-candidates",
            "--shape",
            "--min-score",
            "--include-deadline-range",
            "--timeout-seconds",
        ],
    },
    "enrich-kalshi-orderbooks": {
        "scope": "kalshi_orderbook_refresh",
        "description": "Explicit read-only Kalshi orderbook depth enrichment for a saved snapshot.",
        "live_calls": "read_only_no_auth",
        "allowed_flags": [
            "--snapshot",
            "--output",
            "--timeout-seconds",
            "--max-snapshot-age-hours",
            "--preserve-raw-orderbook",
        ],
    },
    "fetch-kalshi-crypto-readonly": {
        "scope": "kalshi_crypto_readonly_refresh",
        "description": "Explicit public read-only Kalshi BTC/ETH crypto market snapshot with optional orderbook fetch.",
        "live_calls": "read_only_no_auth",
        "allowed_flags": [
            "--asset",
            "--output",
            "--limit",
            "--max-pages",
            "--timeout-seconds",
            "--max-orderbooks",
            "--include-orderbooks",
        ],
    },
    "parse-crypto-com-predict-cdna-fixtures": {
        "scope": "cdna_fixture_parse",
        "description": "Parse saved Crypto.com Predict / CDNA fixtures into a research snapshot.",
        "live_calls": "saved_files_only",
        "allowed_flags": [
            "--fixture-dir",
            "--json-output",
        ],
    },
}


def build_crypto_peer_acquisition_plan_report(
    *,
    input_dir: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    today = generated.date()
    warnings: list[dict[str, Any]] = []

    kalshi_payload = _load_json(input_dir / KALSHI_AUDIT_INPUT, warnings, "kalshi_crypto_typed_key_audit_input")
    polymarket_audit = _load_json(input_dir / POLYMARKET_PIT_AUDIT_INPUT, warnings, "polymarket_pit_audit_input")
    polymarket_enriched = _load_json(input_dir / POLYMARKET_ENRICHED_INPUT, warnings, "polymarket_enriched_input")
    cdna_basis = _load_json(input_dir / CDNA_INPUT, warnings, "cdna_basis_risk_input")
    cdna_snapshot = _load_json(input_dir / CDNA_SNAPSHOT_INPUT, warnings, "cdna_snapshot_input")
    core_trio = _load_json(input_dir / CORE_TRIO_INPUT, warnings, "core_trio_input")

    kalshi_rows = _typed_complete_kalshi_point_in_time_rows(kalshi_payload)
    polymarket_index = _polymarket_pit_index(polymarket_audit)
    polymarket_token_index = _polymarket_token_index(polymarket_enriched)
    cdna_index = _cdna_index(cdna_snapshot)
    cdna_assets_with_quote = _cdna_assets_with_quote_or_price(cdna_basis, cdna_snapshot)

    targets: list[dict[str, Any]] = []
    asset_date_density: Counter[tuple[str, str]] = Counter()
    for row in kalshi_rows:
        asset = (row.get("asset") or "").upper()
        date_key = row.get("target_date")
        if asset and date_key:
            asset_date_density[(asset, date_key)] += 1
    for row in kalshi_rows:
        target = _build_target(
            row=row,
            polymarket_index=polymarket_index,
            polymarket_token_index=polymarket_token_index,
            cdna_index=cdna_index,
            cdna_assets_with_quote=cdna_assets_with_quote,
            today=today,
            density_lookup=asset_date_density,
        )
        targets.append(target)
    targets.sort(key=_target_sort_key)

    polymarket_queries = _polymarket_query_plan(targets=targets)
    cdna_targets = _cdna_target_plan(targets=targets)
    kalshi_orderbook_targets = _kalshi_orderbook_plan(
        targets=targets,
        kalshi_payload=kalshi_payload,
        input_dir=input_dir,
    )
    polymarket_clob_targets = _polymarket_clob_target_plan(targets=targets)
    command_validation_errors = _validate_plan_commands(
        polymarket_queries,
        cdna_targets,
        kalshi_orderbook_targets,
        polymarket_clob_targets,
    )

    summary = _summary(
        targets=targets,
        polymarket_queries=polymarket_queries,
        cdna_targets=cdna_targets,
        kalshi_orderbook_targets=kalshi_orderbook_targets,
        polymarket_clob_targets=polymarket_clob_targets,
        kalshi_payload=kalshi_payload,
        core_trio=core_trio,
        command_validation_errors=command_validation_errors,
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "schema_kind": SCHEMA_KIND,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "input_dir": str(input_dir),
        "diagnostic_only": True,
        "saved_files_only": True,
        "inputs": {
            "kalshi_crypto_typed_key_audit": str(input_dir / KALSHI_AUDIT_INPUT),
            "polymarket_point_in_time_typed_key_audit": str(input_dir / POLYMARKET_PIT_AUDIT_INPUT),
            "polymarket_taxonomy_shape_scout_enriched": str(input_dir / POLYMARKET_ENRICHED_INPUT),
            "cdna_crypto_basis_risk_scout": str(input_dir / CDNA_INPUT),
            "crypto_com_predict_cdna_research_snapshot": str(input_dir / CDNA_SNAPSHOT_INPUT),
            "core_trio_peer_coverage_audit": str(input_dir / CORE_TRIO_INPUT),
        },
        "safe_repo_commands": _SAFE_COMMANDS,
        "summary": summary,
        "command_validation_errors": command_validation_errors,
        "targets": targets,
        "polymarket_queries_recommended": polymarket_queries,
        "polymarket_clob_refresh_recommended": polymarket_clob_targets,
        "cdna_targets_recommended": cdna_targets,
        "kalshi_orderbook_targets_recommended": kalshi_orderbook_targets,
        "warnings": warnings,
        "safety": _safety_block(),
    }


def write_crypto_peer_acquisition_plan_files(
    *,
    input_dir: Path,
    json_output: Path,
    markdown_output: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_crypto_peer_acquisition_plan_report(
        input_dir=input_dir,
        generated_at=generated_at,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_crypto_peer_acquisition_plan_markdown(report), encoding="utf-8")
    return report


def render_crypto_peer_acquisition_plan_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    targets = report.get("targets") or []
    queries = report.get("polymarket_queries_recommended") or []
    cdna_targets = report.get("cdna_targets_recommended") or []
    orderbook_targets = report.get("kalshi_orderbook_targets_recommended") or []
    clob_targets = report.get("polymarket_clob_refresh_recommended") or []
    polymarket_targeted_missing = bool(summary.get("polymarket_targeted_query_command_missing"))

    lines: list[str] = [
        "# Crypto Peer Acquisition Plan",
        "",
        "Saved-file-only diagnostic that converts the typed-complete Kalshi crypto point-in-time "
        "grid into precise acquisition targets for Polymarket discovery, Polymarket CLOB refresh, "
        "CDNA manual fixtures, and Kalshi orderbook refresh. No live calls are made by this command; "
        "recommended commands are only emitted if a matching safe repo command exists. Never creates "
        "candidate pairs, claims exact same-payoff equivalence, or emits paper actions.",
        "",
        "## Executive Summary",
        "",
        f"- kalshi_typed_complete_grid_rows: `{summary.get('kalshi_typed_complete_grid_rows', 0)}`",
        f"- unique_assets: `{summary.get('unique_assets', 0)}`",
        f"- unique_dates: `{summary.get('unique_dates', 0)}`",
        f"- unique_thresholds: `{summary.get('unique_thresholds', 0)}`",
        f"- top_target_assets: `{','.join(item.get('asset') for item in (summary.get('top_target_assets') or []))}`",
        f"- top_target_dates: `{','.join(item.get('target_date') for item in (summary.get('top_target_dates') or []))}`",
        f"- polymarket_queries_recommended: `{summary.get('polymarket_queries_recommended', 0)}`",
        f"- polymarket_clob_refresh_recommended: `{summary.get('polymarket_clob_refresh_recommended', 0)}`",
        f"- cdna_targets_recommended: `{summary.get('cdna_targets_recommended', 0)}`",
        f"- kalshi_orderbook_targets_recommended: `{summary.get('kalshi_orderbook_targets_recommended', 0)}`",
        f"- kalshi_fresh_crypto_snapshot_recommended: `{str(bool(summary.get('kalshi_fresh_crypto_snapshot_recommended'))).lower()}`",
        f"- kalshi_orderbook_targets_requiring_snapshot_age_override: `{summary.get('kalshi_orderbook_targets_requiring_snapshot_age_override', 0)}`",
        f"- safe_commands_referenced: `{','.join(sorted(summary.get('safe_commands_referenced') or []))}`",
        f"- safe_commands_missing: `{','.join(sorted(summary.get('safe_commands_missing') or []))}`",
        f"- command_validation_error_count: `{summary.get('command_validation_error_count', 0)}`",
        f"- polymarket_targeted_query_command_missing: `{str(bool(summary.get('polymarket_targeted_query_command_missing'))).lower()}`",
        f"- kalshi_orderbook_input_snapshot_missing_for_crypto_grid: `{str(bool(summary.get('kalshi_orderbook_input_snapshot_missing_for_crypto_grid'))).lower()}`",
        f"- exact_ready_rows: `0`",
        f"- paper_candidate_rows: `0`",
        "",
        "## Top 20 Acquisition Targets",
        "",
        "| # | Score | Asset | Date | Time | TZ | Threshold | Comparator | Density | CDNA? | PM? | KOB? | Top Blocker | Kalshi Ticker |",
        "|---:|---:|---|---|---|---|---:|---|---:|:---:|:---:|:---:|---|---|",
    ]
    if not targets:
        lines.append("| _none_ | | | | | | | | | | | | | |")
    else:
        for i, target in enumerate(targets[:20], start=1):
            top_blocker = (target.get("blockers") or ["none"])[0]
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(i),
                        f"{target.get('priority_score', 0):.1f}",
                        _md_cell(target.get("asset")),
                        _md_cell(target.get("target_date")),
                        _md_cell(target.get("target_time")),
                        _md_cell(target.get("timezone")),
                        _md_cell(_quote_display(target.get("threshold"))),
                        _md_cell(target.get("comparator")),
                        str(target.get("asset_date_threshold_density", 0)),
                        "yes" if target.get("has_cdna_peer") else "no",
                        "yes" if target.get("has_polymarket_peer") else "no",
                        "yes" if target.get("has_kalshi_orderbook_quote") else "no",
                        _md_cell(top_blocker),
                        _md_cell(target.get("kalshi_ticker")),
                    ]
                )
                + " |"
            )

    lines.extend(
        [
            "",
            "## Target Grid By Asset / Date",
            "",
            "| Asset | Target Date | Markets | Min Threshold | Max Threshold | Has CDNA Peer | Has Polymarket Peer |",
            "|---|---|---:|---:|---:|:---:|:---:|",
        ]
    )
    for entry in (summary.get("grid_asset_date_summary") or [])[:30]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md_cell(entry.get("asset")),
                    _md_cell(entry.get("target_date")),
                    str(entry.get("market_count", 0)),
                    _md_cell(_quote_display(entry.get("min_threshold"))),
                    _md_cell(_quote_display(entry.get("max_threshold"))),
                    "yes" if entry.get("any_cdna_peer") else "no",
                    "yes" if entry.get("any_polymarket_peer") else "no",
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Recommended Polymarket Discovery Queries",
            "",
            (
                "Diagnostic-only suggested search terms. Existing `discover-polymarket-crypto-markets` "
                "is broad discovery only; targeted query command is missing, so no `--query` or `--output` "
                "flags are emitted for this command."
                if polymarket_targeted_missing
                else "Diagnostic-only suggested search terms. Existing `discover-polymarket-crypto-markets` "
                "supports targeted `--query`, `--asset`, and `--target-date` filters. The planner uses "
                "`--json-output` / `--markdown-output` and does not emit the unsupported legacy `--output` flag."
            ),
            "",
            "| # | Asset | Target Date | Suggested Search Term | Existing Safe Command | Next Action |",
            "|---:|---|---|---|---|---|",
        ]
    )
    if not queries:
        lines.append("| _none_ | | | | | |")
    else:
        for i, query in enumerate(queries, start=1):
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(i),
                        _md_cell(query.get("asset")),
                        _md_cell(query.get("target_date")),
                        _md_cell(query.get("search_term")),
                        _md_cell(query.get("safe_command") or NEEDS_COMMAND_DISCOVERY),
                        _md_cell(query.get("next_action")),
                    ]
                )
                + " |"
            )

    lines.extend(_exact_next_command_blocks(report))

    lines.extend(
        [
            "",
            "## Recommended CDNA Manual Fixture Targets",
            "",
            "| # | Asset | Target Date | Threshold Range | Saved CDNA Match? | Safe Command |",
            "|---:|---|---|---|:---:|---|",
        ]
    )
    if not cdna_targets:
        lines.append("| _none_ | | | | | |")
    else:
        for i, ct in enumerate(cdna_targets, start=1):
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(i),
                        _md_cell(ct.get("asset")),
                        _md_cell(ct.get("target_date")),
                        _md_cell(ct.get("threshold_range")),
                        "yes" if ct.get("saved_cdna_row_present") else "no",
                        _md_cell(ct.get("safe_command") or NEEDS_COMMAND_DISCOVERY),
                    ]
                )
                + " |"
            )

    lines.extend(
        [
            "",
            "## Recommended Kalshi Orderbook Refresh Targets",
            "",
            "When the source snapshot is stale, include the explicit `--max-snapshot-age-hours` override shown "
            "in the command so the saved file is used as a ticker list for public read-only orderbook fetch.",
            "",
            "| # | Snapshot Path | Markets | Top Event | Top Ticker | Safe Command | Stale Override? | Blockers |",
            "|---:|---|---:|---|---|---|:---:|---|",
        ]
    )
    if not orderbook_targets:
        lines.append("| _none_ | | | | | |")
    else:
        for i, ob in enumerate(orderbook_targets[:20], start=1):
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(i),
                        _md_cell(ob.get("snapshot_input_path")),
                        str(ob.get("kalshi_market_count", 0)),
                        _md_cell(ob.get("top_event_ticker") or ob.get("event_ticker")),
                        _md_cell(ob.get("top_kalshi_ticker")),
                        _md_cell(ob.get("safe_command") or NEEDS_COMMAND_DISCOVERY),
                        "yes" if ob.get("requires_stale_snapshot_age_override") else "no",
                        _md_cell(", ".join(ob.get("blockers") or []) or "none"),
                    ]
                )
                + " |"
            )
            invocation = _string_or_none(ob.get("safe_command_invocation"))
            if invocation:
                lines.append(f"`{invocation}`")

    lines.extend(
        [
            "",
            "## Recommended Polymarket CLOB Refresh Candidates",
            "",
            "| # | Asset | Target Date | Polymarket Row ID | Token IDs | Safe Command |",
            "|---:|---|---|---|---|---|",
        ]
    )
    if not clob_targets:
        lines.append("| _none_ | | | | | |")
    else:
        for i, ct in enumerate(clob_targets[:20], start=1):
            token_ids = ct.get("token_ids") or []
            token_repr = ",".join(token_ids[:2]) + ("..." if len(token_ids) > 2 else "")
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(i),
                        _md_cell(ct.get("asset")),
                        _md_cell(ct.get("target_date")),
                        _md_cell(ct.get("polymarket_row_id")),
                        _md_cell(token_repr),
                        _md_cell(ct.get("safe_command") or NEEDS_COMMAND_DISCOVERY),
                    ]
                )
                + " |"
            )

    lines.extend(
        [
            "",
            "## Top Blockers",
            "",
            "| Blocker | Count |",
            "|---|---:|",
        ]
    )
    for item in summary.get("top_blockers") or []:
        lines.append(f"| {item.get('blocker')} | {item.get('count')} |")

    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- diagnostic_only: `true`",
            "- saved_files_only: `true`",
            "- live_fetch_attempted: `false`",
            "- can_create_candidate_pair: `false`",
            "- can_create_paper_candidate: `false`",
            "- exact_ready: `false`",
            "- execution_ready: `false`",
            "- paper_candidate: `false`",
            "- treats_title_similarity_as_settlement_equivalence: `false`",
            "- treats_deadline_or_range_hit_as_point_in_time: `false`",
            "- infers_bid_or_ask_from_midpoint_or_complement: `false`",
            "- invents_command_names_when_unknown: `false`",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def _exact_next_command_blocks(report: dict[str, Any]) -> list[str]:
    queries = report.get("polymarket_queries_recommended") or []
    cdna_targets = report.get("cdna_targets_recommended") or []
    orderbook_targets = report.get("kalshi_orderbook_targets_recommended") or []
    lines: list[str] = [
        "",
        "## Exact Next Commands",
        "",
        "Copy-paste-safe PowerShell command blocks for the active core trio targets. These are diagnostic-only "
        "read-only public or saved-file commands registered by `scan.py`.",
    ]

    query_index: dict[tuple[str, str], str] = {}
    for query in queries:
        if not isinstance(query, dict):
            continue
        asset = str(query.get("asset") or "").upper()
        target_date = str(query.get("target_date") or "")
        invocation = _string_or_none(query.get("safe_command_invocation"))
        if asset and target_date and invocation:
            query_index.setdefault((asset, target_date), invocation)

    for label, asset, target_date in (
        ("targeted Polymarket BTC 2026-05-27", "BTC", "2026-05-27"),
        ("targeted Polymarket BTC 2026-05-29", "BTC", "2026-05-29"),
        ("targeted Polymarket ETH 2026-05-27", "ETH", "2026-05-27"),
    ):
        invocation = query_index.get((asset, target_date))
        if invocation:
            lines.extend(["", f"### {label}", "", "```powershell", invocation, "```"])

    for target in orderbook_targets:
        if not isinstance(target, dict) or target.get("safe_command") != "fetch-kalshi-crypto-readonly":
            continue
        invocation = _string_or_none(target.get("safe_command_invocation"))
        if invocation:
            lines.extend(["", "### fresh Kalshi crypto fetch", "", "```powershell", invocation, "```"])
            break

    for target in cdna_targets:
        if not isinstance(target, dict) or target.get("safe_command") != "parse-crypto-com-predict-cdna-fixtures":
            continue
        invocation = _string_or_none(target.get("safe_command_invocation"))
        if invocation:
            lines.extend(["", "### CDNA fixture parse", "", "```powershell", invocation, "```"])
            break

    if len(lines) == 4:
        lines.extend(["", "_No exact next command blocks were available from the current saved inputs._"])
    return lines


# ---------------------------------------------------------------------------
# Input filtering
# ---------------------------------------------------------------------------


def _typed_complete_kalshi_point_in_time_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("market_shape") != "point_in_time_threshold":
            continue
        if not row.get("typed_complete"):
            continue
        if not row.get("asset"):
            continue
        if row.get("threshold") is None:
            continue
        if not row.get("target_date"):
            continue
        out.append(row)
    return out


def _polymarket_pit_index(payload: Any) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not isinstance(payload, dict):
        return index
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return index
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("market_shape") or "").lower() != "point_in_time_threshold":
            continue
        asset = str(row.get("asset_or_family") or "").upper().strip()
        if asset not in {"BTC", "ETH", "SOL"}:
            continue
        target_date = _normalize_date_token(row.get("target_date"))
        if not target_date:
            continue
        index[asset].append(
            {
                "asset": asset,
                "target_date": target_date,
                "threshold": _float_or_none(row.get("threshold")),
                "comparator": _string_or_none(row.get("comparator")),
                "row_id": _string_or_none(row.get("row_id")),
                "question": _string_or_none(row.get("question")),
            }
        )
    return index


def _polymarket_token_index(payload: Any) -> dict[str, dict[str, Any]]:
    """row_id -> {token_ids, condition_id, market_id} from saved enriched payload."""
    index: dict[str, dict[str, Any]] = {}
    if not isinstance(payload, dict):
        return index
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return index
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_id = _string_or_none(row.get("row_id"))
        if not row_id:
            continue
        token_ids = row.get("token_ids") if isinstance(row.get("token_ids"), list) else []
        index[row_id] = {
            "token_ids": [t for t in token_ids if isinstance(t, str)],
            "condition_id": _string_or_none(row.get("condition_id")),
            "market_id": _string_or_none(row.get("market_id")),
            "clob_book_attached": bool(row.get("clob_book_attached")),
        }
    return index


def _cdna_index(payload: Any) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not isinstance(payload, dict):
        return index
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return index
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("market_type") or "").lower() != "point_in_time_threshold":
            continue
        asset = str(row.get("asset") or "").upper().strip()
        if asset not in {"BTC", "ETH", "SOL"}:
            continue
        target_date = _normalize_date_token(row.get("target_date"))
        if not target_date:
            continue
        index[asset].append(
            {
                "asset": asset,
                "target_date": target_date,
                "threshold": _float_or_none(row.get("threshold_value") or row.get("upper")),
                "comparator": _string_or_none(row.get("comparator")),
                "title": _string_or_none(row.get("title")),
                "source_url": _string_or_none(row.get("source_url")),
            }
        )
    return index


def _cdna_assets_with_quote_or_price(basis: Any, snapshot: Any) -> set[str]:
    assets: set[str] = set()
    if isinstance(snapshot, dict):
        rows = snapshot.get("rows")
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                asset = str(row.get("asset") or "").upper().strip()
                if not asset:
                    continue
                chance = row.get("chance_to_win_display")
                if isinstance(chance, str) and chance.strip():
                    assets.add(asset)
    return assets


# ---------------------------------------------------------------------------
# Target construction + ranking
# ---------------------------------------------------------------------------


def _build_target(
    *,
    row: dict[str, Any],
    polymarket_index: dict[str, list[dict[str, Any]]],
    polymarket_token_index: dict[str, dict[str, Any]],
    cdna_index: dict[str, list[dict[str, Any]]],
    cdna_assets_with_quote: set[str],
    today: date,
    density_lookup: Counter[tuple[str, str]],
) -> dict[str, Any]:
    asset = (row.get("asset") or "").upper()
    target_date = _string_or_none(row.get("target_date"))
    target_time = _string_or_none(row.get("target_time"))
    timezone_label = _string_or_none(row.get("timezone"))
    threshold = _float_or_none(row.get("threshold"))
    comparator = _string_or_none(row.get("comparator"))
    settlement_source = _string_or_none(row.get("settlement_source"))
    settlement_source_url = _string_or_none(row.get("settlement_source_url"))
    kalshi_ticker = _string_or_none(row.get("ticker"))
    event_ticker = _string_or_none(row.get("event_ticker"))
    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}

    parsed_date = _parse_date_only(target_date)
    days_until = (parsed_date - today).days if parsed_date is not None else None

    cdna_matches = _peer_matches(
        asset=asset,
        target_date=target_date,
        threshold=threshold,
        comparator=comparator,
        index=cdna_index,
    )
    polymarket_matches = _peer_matches(
        asset=asset,
        target_date=target_date,
        threshold=threshold,
        comparator=comparator,
        index=polymarket_index,
    )

    blockers: list[str] = []
    if not polymarket_matches:
        blockers.append(B_MISSING_POLYMARKET_PEER)
    if not cdna_matches:
        blockers.append(B_MISSING_CDNA_PEER)
    if not quote.get("present"):
        blockers.append(B_MISSING_KALSHI_ORDERBOOK_QUOTE)
    if not any(
        polymarket_token_index.get(match.get("row_id") or "", {}).get("clob_book_attached")
        for match in polymarket_matches
    ):
        if polymarket_matches:
            blockers.append(B_MISSING_POLYMARKET_CLOB_QUOTE)
    if asset and asset not in cdna_assets_with_quote:
        blockers.append(B_MISSING_CDNA_QUOTE)
    if not settlement_source_url:
        blockers.append(B_SETTLEMENT_SOURCE_UNVERIFIED)
    if not cdna_matches and not polymarket_matches:
        blockers.append(B_PEER_DATE_THRESHOLD_GAP)

    density = density_lookup.get((asset, target_date or ""), 0)
    priority = _priority_score(
        asset=asset,
        days_until=days_until,
        density=density,
        has_cdna_peer=bool(cdna_matches),
        has_polymarket_peer=bool(polymarket_matches),
        has_quote=bool(quote.get("present")),
    )

    polymarket_token_evidence = [
        polymarket_token_index.get(match.get("row_id") or "", {})
        for match in polymarket_matches
    ]
    return {
        "kalshi_row_id": _string_or_none(row.get("row_id")),
        "kalshi_ticker": kalshi_ticker,
        "event_ticker": event_ticker,
        "kalshi_raw_source_file": _string_or_none(row.get("raw_source_file") or row.get("source_file") or row.get("source_path")),
        "asset": asset or None,
        "target_date": target_date,
        "target_time": target_time,
        "timezone": timezone_label,
        "threshold": threshold,
        "comparator": comparator,
        "settlement_source": settlement_source,
        "settlement_source_url": settlement_source_url,
        "kalshi_quote": quote if quote else None,
        "has_kalshi_orderbook_quote": bool(quote.get("present")),
        "has_polymarket_peer": bool(polymarket_matches),
        "has_cdna_peer": bool(cdna_matches),
        "polymarket_peer_matches": polymarket_matches,
        "cdna_peer_matches": cdna_matches,
        "polymarket_token_evidence": polymarket_token_evidence,
        "asset_date_threshold_density": density,
        "days_until_target": days_until,
        "priority_score": round(priority, 2),
        "why_this_matters": _why_this_matters(
            asset=asset,
            density=density,
            days_until=days_until,
            cdna=bool(cdna_matches),
            polymarket=bool(polymarket_matches),
        ),
        "missing_platform_data": _missing_platform_data(
            has_cdna_peer=bool(cdna_matches),
            has_polymarket_peer=bool(polymarket_matches),
            has_quote=bool(quote.get("present")),
        ),
        "recommended_next_action": _recommended_next_action(
            asset=asset,
            cdna=bool(cdna_matches),
            polymarket=bool(polymarket_matches),
            polymarket_token_present=any(token.get("token_ids") for token in polymarket_token_evidence),
            has_quote=bool(quote.get("present")),
        ),
        "blockers": blockers,
        "diagnostic_only": True,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "exact_ready": False,
        "execution_ready": False,
        "paper_candidate": False,
        "affects_evaluator_gates": False,
    }


def _peer_matches(
    *,
    asset: str,
    target_date: str | None,
    threshold: float | None,
    comparator: str | None,
    index: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    if not asset or asset not in index:
        return []
    norm_target = _normalize_date_token(target_date) if target_date else None
    matches: list[dict[str, Any]] = []
    for entry in index[asset]:
        if norm_target and entry.get("target_date") and entry["target_date"] != norm_target:
            continue
        peer_threshold = _float_or_none(entry.get("threshold"))
        if threshold is not None and peer_threshold is not None:
            if peer_threshold <= 0:
                continue
            relative = abs(threshold - peer_threshold) / max(abs(peer_threshold), 1.0)
            if relative > 0.01:
                continue
        if comparator and entry.get("comparator"):
            if not _comparator_family_compatible(comparator, entry["comparator"]):
                continue
        matches.append(entry)
    return matches


def _comparator_family_compatible(a: str, b: str) -> bool:
    above = {"above", ">", "greater_than", "at_or_above", ">=", "greater_than_or_equal_to"}
    below = {"below", "<", "less_than", "at_or_below", "<=", "less_than_or_equal_to"}
    al = a.strip().lower()
    bl = b.strip().lower()
    if al in above and bl in above:
        return True
    if al in below and bl in below:
        return True
    return al == bl


def _priority_score(
    *,
    asset: str,
    days_until: int | None,
    density: int,
    has_cdna_peer: bool,
    has_polymarket_peer: bool,
    has_quote: bool,
) -> float:
    score = 0.0
    asset_score = dict(_ASSET_PRIORITY).get(asset.upper() if asset else "", 10)
    score += asset_score
    if days_until is not None:
        if days_until >= 0:
            if days_until <= 7:
                score += 30.0
            elif days_until <= 30:
                score += 18.0
            elif days_until <= 90:
                score += 8.0
            else:
                score += 2.0
        else:
            # Past dates still indicate where the grid lives but are deprioritized.
            score += 0.5
    score += min(density * 1.5, 25.0)
    if has_cdna_peer:
        score += 12.0
    if has_polymarket_peer:
        score += 18.0
    if has_quote:
        score += 6.0
    return score


def _target_sort_key(target: dict[str, Any]) -> tuple[float, int, str]:
    return (
        -float(target.get("priority_score") or 0.0),
        target.get("days_until_target") if target.get("days_until_target") is not None else 10_000,
        str(target.get("kalshi_ticker") or ""),
    )


def _why_this_matters(
    *,
    asset: str,
    density: int,
    days_until: int | None,
    cdna: bool,
    polymarket: bool,
) -> str:
    parts: list[str] = []
    if asset.upper() in {"BTC", "ETH"}:
        parts.append(f"{asset.upper()} is core-trio liquidity priority")
    if days_until is not None:
        if days_until < 0:
            parts.append(f"target date {abs(days_until)}d in the past (historical reference)")
        elif days_until <= 7:
            parts.append(f"target date {days_until}d away — near-term")
        elif days_until <= 30:
            parts.append(f"target date {days_until}d away — short-term")
    if density >= 5:
        parts.append(f"{density} adjacent Kalshi strikes on this date")
    if cdna:
        parts.append("CDNA peer present")
    if polymarket:
        parts.append("Polymarket peer present")
    if not parts:
        parts.append("baseline grid coverage")
    return "; ".join(parts)


def _missing_platform_data(
    *,
    has_cdna_peer: bool,
    has_polymarket_peer: bool,
    has_quote: bool,
) -> list[str]:
    missing: list[str] = []
    if not has_polymarket_peer:
        missing.append("polymarket_peer_on_same_asset_date_threshold")
    if not has_cdna_peer:
        missing.append("cdna_peer_on_same_asset_date_threshold")
    if not has_quote:
        missing.append("kalshi_orderbook_quote_for_market")
    return missing


def _recommended_next_action(
    *,
    asset: str,
    cdna: bool,
    polymarket: bool,
    polymarket_token_present: bool,
    has_quote: bool,
) -> dict[str, Any]:
    if not polymarket:
        return {
            "action": "DISCOVER_POLYMARKET_FOR_ASSET_DATE",
            "safe_command": "discover-polymarket-crypto-markets",
            "reason": "no saved Polymarket point-in-time peer for this (asset, target_date, threshold) tuple",
        }
    if polymarket and polymarket_token_present:
        return {
            "action": "REFRESH_POLYMARKET_CLOB_FOR_PEER",
            "safe_command": "refresh-polymarket-clob-for-taxonomy-candidates",
            "reason": "Polymarket peer with token IDs exists but CLOB book not attached",
        }
    if not cdna:
        return {
            "action": "ACQUIRE_CDNA_FIXTURE_FOR_ASSET_DATE",
            "safe_command": "parse-crypto-com-predict-cdna-fixtures",
            "reason": "no saved CDNA point-in-time peer for this (asset, target_date) — parse a manually captured fixture",
        }
    if not has_quote:
        return {
            "action": "REFRESH_KALSHI_ORDERBOOK",
            "safe_command": "enrich-kalshi-orderbooks",
            "reason": "no saved Kalshi orderbook quote for this market — refresh depth via the read-only enrichment",
        }
    return {
        "action": "MANUAL_REVIEW_OVERLAP",
        "safe_command": None,
        "reason": "all three peer lanes have data — manual review of settlement source, window, payoff scope before any exact pairing",
    }


# ---------------------------------------------------------------------------
# Acquisition plan slices
# ---------------------------------------------------------------------------


def _polymarket_query_plan(*, targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    plan: list[dict[str, Any]] = []
    targeted_available = bool(
        _SAFE_COMMANDS.get("discover-polymarket-crypto-markets", {}).get("targeted_query_supported")
    )
    allowed_flags = set(
        _SAFE_COMMANDS.get("discover-polymarket-crypto-markets", {}).get("allowed_flags") or []
    )
    for target in targets:
        if target.get("has_polymarket_peer"):
            continue
        asset = (target.get("asset") or "").upper()
        target_date = target.get("target_date")
        if not asset or not target_date:
            continue
        key = (asset, target_date)
        if key in seen:
            continue
        seen.add(key)
        readable = _readable_date(target_date) or target_date
        asset_word = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}.get(asset, asset.lower())
        search_term = f"{asset_word} {readable}"
        safe_date_slug = target_date.replace("-", "")
        json_output_path = f"reports/polymarket_crypto_discovery_{asset.lower()}_{safe_date_slug}.json"
        markdown_output_path = f"reports/polymarket_crypto_discovery_{asset.lower()}_{safe_date_slug}.md"
        if targeted_available and {"--query", "--asset", "--target-date"} <= allowed_flags:
            invocation = (
                "python scan.py discover-polymarket-crypto-markets "
                f"--query {shlex.quote(search_term)} "
                f"--asset {asset} "
                f"--target-date {target_date} "
                "--output-dir reports/manual_snapshots/polymarket_crypto "
                "--limit 200 "
                "--timeout-seconds 10 "
                "--max-pages 3 "
                f"--json-output {json_output_path} "
                f"--markdown-output {markdown_output_path}"
            )
            next_action_text = (
                "Targeted public-no-auth Polymarket discovery using the new --query / --asset / "
                "--target-date flags. Runs a server-side Gamma search and applies client-side "
                "asset/date filtering."
            )
            unsupported_flags_removed: list[str] = []
            row_blockers = [B_MISSING_POLYMARKET_PEER]
            command_scope = "targeted_or_broad_discovery"
        else:
            invocation = (
                "python scan.py discover-polymarket-crypto-markets "
                "--output-dir reports/manual_snapshots/polymarket_crypto "
                "--limit 200 "
                "--timeout-seconds 10 "
                "--max-pages 3 "
                "--json-output reports/polymarket_crypto_discovery.json "
                "--markdown-output reports/polymarket_crypto_discovery.md"
            )
            next_action_text = (
                "Existing discover-polymarket-crypto-markets is broad discovery only; "
                "targeted query command is missing."
            )
            unsupported_flags_removed = ["--query", "--output"]
            row_blockers = [B_MISSING_POLYMARKET_PEER, B_POLYMARKET_TARGETED_QUERY_COMMAND_MISSING]
            command_scope = "broad_discovery_only"
        plan.append(
            {
                "asset": asset,
                "target_date": target_date,
                "search_term": search_term,
                "safe_command": "discover-polymarket-crypto-markets",
                "safe_command_scope": command_scope,
                "targeted_query_command_available": targeted_available,
                "targeted_query_safe_command": (
                    "discover-polymarket-crypto-markets" if targeted_available else None
                ),
                "safe_command_invocation": invocation,
                "next_action": next_action_text,
                "unsupported_flags_removed": unsupported_flags_removed,
                "blockers": row_blockers,
                "diagnostic_only": True,
            }
        )
    return plan


def _polymarket_clob_target_plan(*, targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    plan: list[dict[str, Any]] = []
    for target in targets:
        for match, token in zip(
            target.get("polymarket_peer_matches") or [],
            target.get("polymarket_token_evidence") or [],
        ):
            row_id = match.get("row_id") if isinstance(match, dict) else None
            if not row_id or row_id in seen:
                continue
            token_ids = (token or {}).get("token_ids") or []
            if not token_ids:
                continue
            if (token or {}).get("clob_book_attached"):
                continue
            seen.add(row_id)
            plan.append(
                {
                    "asset": target.get("asset"),
                    "target_date": target.get("target_date"),
                    "polymarket_row_id": row_id,
                    "token_ids": list(token_ids)[:4],
                    "safe_command": "refresh-polymarket-clob-for-taxonomy-candidates",
                    "safe_command_invocation": (
                        "python scan.py refresh-polymarket-clob-for-taxonomy-candidates "
                        "--taxonomy-json reports/polymarket_taxonomy_shape_scout.json "
                        "--output-dir reports/manual_snapshots/polymarket_clob_taxonomy "
                        "--json-output reports/polymarket_clob_taxonomy_refresh.json "
                        "--enriched-output reports/polymarket_taxonomy_shape_scout_enriched.json"
                    ),
                    "blockers": [B_MISSING_POLYMARKET_CLOB_QUOTE],
                    "diagnostic_only": True,
                }
            )
    return plan


def _cdna_target_plan(*, targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    plan: list[dict[str, Any]] = []
    for target in targets:
        if target.get("has_cdna_peer"):
            continue
        asset = (target.get("asset") or "").upper()
        target_date = target.get("target_date")
        if not asset or not target_date:
            continue
        key = (asset, target_date)
        if key in seen:
            continue
        seen.add(key)
        density = target.get("asset_date_threshold_density") or 0
        threshold_range = ""
        if target.get("threshold") is not None:
            threshold_range = f"~ ±1% of {_quote_display(target['threshold'])}"
        plan.append(
            {
                "asset": asset,
                "target_date": target_date,
                "threshold_range": threshold_range,
                "saved_cdna_row_present": False,
                "kalshi_strike_count_on_date": density,
                "safe_command": "parse-crypto-com-predict-cdna-fixtures",
                "safe_command_invocation": (
                    "python scan.py parse-crypto-com-predict-cdna-fixtures "
                    "--json-output reports/crypto_com_predict_cdna_research_snapshot.json"
                ),
                "manual_action_hint": (
                    f"Capture a public Crypto.com Predict / CDNA event page snapshot for {asset} on "
                    f"{target_date} (point-in-time threshold) into reports/manual_snapshots/cdna/ "
                    "and then re-parse fixtures."
                ),
                "blockers": [B_MISSING_CDNA_PEER],
                "diagnostic_only": True,
            }
        )
    return plan


def _kalshi_orderbook_plan(
    *,
    targets: list[dict[str, Any]],
    kalshi_payload: Any,
    input_dir: Path,
) -> list[dict[str, Any]]:
    source_files = _kalshi_crypto_grid_source_files(kalshi_payload)
    by_source: dict[str, dict[str, Any]] = {}
    missing_source_bucket: dict[str, Any] | None = None
    fresh_crypto_bucket: dict[str, Any] | None = None
    for target in targets:
        if target.get("has_kalshi_orderbook_quote"):
            continue
        event_ticker = target.get("event_ticker") or target.get("kalshi_ticker")
        source_path = _string_or_none(target.get("kalshi_raw_source_file"))
        if source_path:
            source_path = _repo_relative_path(source_path)
        ticker = _string_or_none(target.get("kalshi_ticker"))
        if _target_needs_fresh_kalshi_crypto_snapshot(target):
            if fresh_crypto_bucket is None:
                fresh_crypto_bucket = {
                    "snapshot_input_path": None,
                    "snapshot_output_path": "reports/live_readonly/crypto/kalshi_live_readonly_snapshot.json",
                    "source_files_where_crypto_rows_came_from": source_files,
                    "kalshi_market_count": 0,
                    "top_event_ticker": event_ticker,
                    "top_kalshi_ticker": ticker,
                    "asset": target.get("asset"),
                    "target_date": target.get("target_date"),
                    "safe_command": "fetch-kalshi-crypto-readonly",
                    "safe_command_invocation": (
                        "python scan.py fetch-kalshi-crypto-readonly "
                        "--asset BTC,ETH "
                        "--output reports/live_readonly/crypto/kalshi_live_readonly_snapshot.json "
                        "--max-pages 20 "
                        "--max-orderbooks 200 "
                        "--include-orderbooks"
                    ),
                    "requires_stale_snapshot_age_override": False,
                    "fresh_fetch_guidance": (
                        "Existing saved Kalshi crypto snapshots are stale or contain settled empty books. "
                        "Refresh the active/current BTC/ETH crypto ticker list first, then rerun the typed-key audit."
                    ),
                    "blockers": [B_MISSING_KALSHI_ORDERBOOK_QUOTE, B_KALSHI_FRESH_CRYPTO_SNAPSHOT_REQUIRED],
                    "diagnostic_only": True,
                    "top_priority_score": target.get("priority_score") or 0,
                }
            fresh_crypto_bucket["kalshi_market_count"] += 1
            score = target.get("priority_score") or 0
            if score > (fresh_crypto_bucket.get("top_priority_score") or 0):
                fresh_crypto_bucket["top_priority_score"] = score
                fresh_crypto_bucket["top_event_ticker"] = event_ticker
                fresh_crypto_bucket["top_kalshi_ticker"] = ticker
                fresh_crypto_bucket["asset"] = target.get("asset")
                fresh_crypto_bucket["target_date"] = target.get("target_date")
            continue
        source_is_viable = bool(
            source_path
            and Path(source_path).exists()
            and (ticker is None or _snapshot_contains_ticker(Path(source_path), ticker))
        )
        if not source_is_viable:
            if missing_source_bucket is None:
                missing_source_bucket = {
                    "snapshot_input_path": None,
                    "source_files_where_crypto_rows_came_from": source_files,
                    "kalshi_market_count": 0,
                    "top_event_ticker": event_ticker,
                    "top_kalshi_ticker": ticker,
                    "asset": target.get("asset"),
                    "target_date": target.get("target_date"),
                    "safe_command": None,
                    "safe_command_invocation": None,
                    "blockers": [B_MISSING_KALSHI_ORDERBOOK_QUOTE, B_KALSHI_ORDERBOOK_INPUT_SNAPSHOT_MISSING],
                    "diagnostic_only": True,
                    "top_priority_score": target.get("priority_score") or 0,
                }
            missing_source_bucket["kalshi_market_count"] += 1
            score = target.get("priority_score") or 0
            if score > (missing_source_bucket.get("top_priority_score") or 0):
                missing_source_bucket["top_priority_score"] = score
                missing_source_bucket["top_event_ticker"] = event_ticker
                missing_source_bucket["top_kalshi_ticker"] = ticker
            continue
        output_path = _orderbook_output_path_for_snapshot(source_path)
        bucket = by_source.setdefault(
            source_path,
            {
                "snapshot_input_path": source_path,
                "source_files_where_crypto_rows_came_from": source_files,
                "top_event_ticker": event_ticker,
                "kalshi_market_count": 0,
                "top_kalshi_ticker": None,
                "asset": target.get("asset"),
                "target_date": target.get("target_date"),
                "safe_command": "enrich-kalshi-orderbooks",
                "safe_command_invocation": (
                    "python scan.py enrich-kalshi-orderbooks "
                    f"--snapshot {source_path} "
                    f"--output {output_path} "
                    f"--max-snapshot-age-hours {KALSHI_ORDERBOOK_STALE_SNAPSHOT_AGE_OVERRIDE_HOURS}"
                ),
                "requires_stale_snapshot_age_override": True,
                "fresh_fetch_guidance": (
                    "Default enrich-kalshi-orderbooks skips live read-only orderbook fetches when the saved "
                    "source snapshot is older than --max-snapshot-age-hours. This explicit override treats the "
                    "saved snapshot as a ticker list so the command can attempt public read-only book refresh."
                ),
                "blockers": [B_MISSING_KALSHI_ORDERBOOK_QUOTE],
                "diagnostic_only": True,
                "top_priority_score": target.get("priority_score") or 0,
            },
        )
        bucket["kalshi_market_count"] += 1
        if bucket["top_kalshi_ticker"] is None:
            bucket["top_kalshi_ticker"] = target.get("kalshi_ticker")
        score = target.get("priority_score") or 0
        if score > bucket["top_priority_score"]:
            bucket["top_priority_score"] = score
            bucket["top_kalshi_ticker"] = target.get("kalshi_ticker")
            bucket["top_event_ticker"] = event_ticker
    plan = sorted(by_source.values(), key=lambda b: -float(b.get("top_priority_score") or 0))
    if fresh_crypto_bucket is not None:
        plan.insert(0, fresh_crypto_bucket)
    if missing_source_bucket is not None:
        plan.append(missing_source_bucket)
    return plan


def _target_needs_fresh_kalshi_crypto_snapshot(target: dict[str, Any]) -> bool:
    asset = str(target.get("asset") or "").upper()
    if asset not in {"BTC", "ETH"}:
        return False
    quote = target.get("kalshi_quote") if isinstance(target.get("kalshi_quote"), dict) else {}
    if quote.get("fresh_orderbook") is True or quote.get("present") is True:
        return False
    source_path = _repo_relative_path(_string_or_none(target.get("kalshi_raw_source_file")) or "")
    if "live_readonly/crypto/kalshi_live_readonly_snapshot.json" in source_path:
        return False
    stale_or_missing = bool(
        quote.get("stale_top_of_book")
        or quote.get("full_orderbook_missing")
        or quote.get("market_settled")
        or quote.get("orderbook_failure_reason") == "closed_or_settled_empty_book"
    )
    return stale_or_missing or "reports/live_readonly/sweep/overlap_crypto" in source_path


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _summary(
    *,
    targets: list[dict[str, Any]],
    polymarket_queries: list[dict[str, Any]],
    cdna_targets: list[dict[str, Any]],
    kalshi_orderbook_targets: list[dict[str, Any]],
    polymarket_clob_targets: list[dict[str, Any]],
    kalshi_payload: Any,
    core_trio: Any,
    command_validation_errors: list[dict[str, Any]],
) -> dict[str, Any]:
    assets: Counter[str] = Counter()
    dates: Counter[str] = Counter()
    thresholds: set[float] = set()
    blockers: Counter[str] = Counter()
    safe_commands_referenced: set[str] = set()
    grid_by_asset_date: dict[tuple[str, str], dict[str, Any]] = {}
    for target in targets:
        asset = (target.get("asset") or "UNKNOWN").upper()
        target_date = target.get("target_date") or "unknown_date"
        threshold = target.get("threshold")
        assets[asset] += 1
        dates[target_date] += 1
        if isinstance(threshold, (int, float)):
            thresholds.add(float(threshold))
        for blocker in target.get("blockers") or []:
            blockers[blocker] += 1
        action = target.get("recommended_next_action") or {}
        cmd = action.get("safe_command")
        if cmd:
            safe_commands_referenced.add(cmd)
        bucket = grid_by_asset_date.setdefault(
            (asset, target_date),
            {
                "asset": asset,
                "target_date": target_date,
                "market_count": 0,
                "min_threshold": None,
                "max_threshold": None,
                "any_cdna_peer": False,
                "any_polymarket_peer": False,
            },
        )
        bucket["market_count"] += 1
        if isinstance(threshold, (int, float)):
            if bucket["min_threshold"] is None or threshold < bucket["min_threshold"]:
                bucket["min_threshold"] = threshold
            if bucket["max_threshold"] is None or threshold > bucket["max_threshold"]:
                bucket["max_threshold"] = threshold
        if target.get("has_cdna_peer"):
            bucket["any_cdna_peer"] = True
        if target.get("has_polymarket_peer"):
            bucket["any_polymarket_peer"] = True

    for plan_list in (polymarket_queries, cdna_targets, polymarket_clob_targets, kalshi_orderbook_targets):
        for item in plan_list:
            cmd = item.get("safe_command")
            if cmd:
                safe_commands_referenced.add(cmd)
            for blocker in item.get("blockers") or []:
                blockers[blocker] += 1

    safe_commands_missing: list[str] = []
    if any(q.get("targeted_query_command_available") is False for q in polymarket_queries):
        safe_commands_missing.append("polymarket_targeted_query")
        blockers[B_POLYMARKET_TARGETED_QUERY_COMMAND_MISSING] += 1
    if not polymarket_queries:
        # No queries means we either have peers everywhere or no targets at all.
        # That isn't a missing command — the existing command was still recognized.
        pass
    if any(not q.get("safe_command") for q in polymarket_queries):
        safe_commands_missing.append("polymarket_discovery")
    if any(not c.get("safe_command") for c in cdna_targets):
        safe_commands_missing.append("cdna_fixture_acquisition")
    if any(not k.get("safe_command") for k in kalshi_orderbook_targets):
        safe_commands_missing.append("kalshi_orderbook_refresh")
    if any(B_KALSHI_ORDERBOOK_INPUT_SNAPSHOT_MISSING in (k.get("blockers") or []) for k in kalshi_orderbook_targets):
        blockers[B_KALSHI_ORDERBOOK_INPUT_SNAPSHOT_MISSING] += 1
    if command_validation_errors:
        blockers[B_UNSUPPORTED_COMMAND_FLAG] += len(command_validation_errors)
    if safe_commands_missing:
        blockers[B_NO_SAFE_FETCH_COMMAND_FOUND] += len(safe_commands_missing)

    top_asset_items = [{"asset": a, "count": c} for a, c in assets.most_common(5)]
    top_date_items = [{"target_date": d, "count": c} for d, c in dates.most_common(10)]
    top_blockers = [{"blocker": b, "count": c} for b, c in blockers.most_common(15)]
    grid_summary_sorted = sorted(
        grid_by_asset_date.values(),
        key=lambda v: (-(v.get("market_count") or 0), v.get("asset"), v.get("target_date")),
    )
    top_20_targets = [
        {
            "kalshi_ticker": t.get("kalshi_ticker"),
            "asset": t.get("asset"),
            "target_date": t.get("target_date"),
            "target_time": t.get("target_time"),
            "timezone": t.get("timezone"),
            "threshold": t.get("threshold"),
            "comparator": t.get("comparator"),
            "priority_score": t.get("priority_score"),
            "blockers": t.get("blockers"),
            "recommended_next_action": (t.get("recommended_next_action") or {}).get("action"),
            "safe_command": (t.get("recommended_next_action") or {}).get("safe_command"),
            "has_cdna_peer": t.get("has_cdna_peer"),
            "has_polymarket_peer": t.get("has_polymarket_peer"),
            "has_kalshi_orderbook_quote": t.get("has_kalshi_orderbook_quote"),
            "asset_date_threshold_density": t.get("asset_date_threshold_density"),
        }
        for t in targets[:20]
    ]

    return {
        "kalshi_typed_complete_grid_rows": len(targets),
        "unique_assets": sum(1 for a in assets.keys() if a and a != "UNKNOWN"),
        "unique_dates": len(dates),
        "unique_thresholds": len(thresholds),
        "top_target_assets": top_asset_items,
        "top_target_dates": top_date_items,
        "polymarket_queries_recommended": len(polymarket_queries),
        "polymarket_clob_refresh_recommended": len(polymarket_clob_targets),
        "cdna_targets_recommended": len(cdna_targets),
        "kalshi_orderbook_targets_recommended": len(kalshi_orderbook_targets),
        "kalshi_fresh_crypto_snapshot_recommended": any(
            item.get("safe_command") == "fetch-kalshi-crypto-readonly" for item in kalshi_orderbook_targets
        ),
        "kalshi_orderbook_targets_requiring_snapshot_age_override": sum(
            1 for item in kalshi_orderbook_targets if item.get("requires_stale_snapshot_age_override")
        ),
        "safe_commands_referenced": sorted(safe_commands_referenced),
        "safe_commands_missing": safe_commands_missing,
        "command_validation_error_count": len(command_validation_errors),
        "command_validation_errors": command_validation_errors,
        "polymarket_targeted_query_command_missing": any(
            q.get("targeted_query_command_available") is False for q in polymarket_queries
        ),
        "kalshi_orderbook_input_snapshot_missing_for_crypto_grid": any(
            B_KALSHI_ORDERBOOK_INPUT_SNAPSHOT_MISSING in (k.get("blockers") or []) for k in kalshi_orderbook_targets
        ),
        "kalshi_orderbook_input_snapshot_paths": [
            str(item.get("snapshot_input_path"))
            for item in kalshi_orderbook_targets
            if item.get("snapshot_input_path")
        ],
        "kalshi_crypto_grid_source_files": _kalshi_crypto_grid_source_files(kalshi_payload),
        "exact_ready_rows": 0,
        "paper_candidate_rows": 0,
        "execution_ready_rows": 0,
        "top_blockers": top_blockers,
        "grid_asset_date_summary": grid_summary_sorted,
        "top_20_targets": top_20_targets,
        "asset_counts": dict(assets),
        "date_counts": dict(dates),
        "core_trio_strongest_overlap_family": (
            (core_trio or {}).get("summary", {}).get("strongest_overlap_family")
            if isinstance(core_trio, dict)
            else None
        ),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safety_block() -> dict[str, Any]:
    return {
        "diagnostic_only": True,
        "saved_files_only": True,
        "live_fetch_attempted": False,
        "execution_or_order_logic_added": False,
        "account_or_auth_logic_added": False,
        "wallet_or_signing_or_account_logic_added": False,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "exact_ready": False,
        "execution_ready": False,
        "paper_candidate": False,
        "affects_evaluator_gates": False,
        "source_registry_unchanged": True,
        "source_exact_payoff_compatible_with_kalshi": False,
        "treats_title_similarity_as_settlement_equivalence": False,
        "treats_deadline_or_range_hit_as_point_in_time": False,
        "infers_bid_or_ask_from_midpoint_or_complement": False,
        "invents_command_names_when_unknown": False,
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


def _validate_plan_commands(*plan_lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for plan in plan_lists:
        for item in plan:
            invocation = _string_or_none(item.get("safe_command_invocation"))
            if not invocation:
                continue
            item_errors = _validate_safe_command_invocation(invocation)
            item["command_validation"] = {
                "valid": not item_errors,
                "errors": item_errors,
            }
            errors.extend(item_errors)
    return errors


def _validate_safe_command_invocation(invocation: str) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    try:
        tokens = shlex.split(invocation)
    except ValueError as exc:
        return [{"command": None, "flag": None, "blocker": B_UNSUPPORTED_COMMAND_FLAG, "reason": str(exc)}]
    command = _scan_command_from_tokens(tokens)
    if not command:
        return [{"command": None, "flag": None, "blocker": B_UNSUPPORTED_COMMAND_FLAG, "reason": "scan_command_not_found"}]
    spec = _SAFE_COMMANDS.get(command)
    if not spec:
        return [{"command": command, "flag": None, "blocker": B_UNSUPPORTED_COMMAND_FLAG, "reason": "command_not_whitelisted"}]
    allowed = set(spec.get("allowed_flags") or [])
    for token in tokens:
        if not token.startswith("--"):
            continue
        flag = token.split("=", 1)[0]
        if flag not in allowed:
            errors.append(
                {
                    "command": command,
                    "flag": flag,
                    "blocker": B_UNSUPPORTED_COMMAND_FLAG,
                    "reason": "flag_not_supported_by_scan_parser",
                }
            )
    return errors


def _scan_command_from_tokens(tokens: list[str]) -> str | None:
    for index, token in enumerate(tokens):
        if token.endswith("scan.py") and index + 1 < len(tokens):
            return tokens[index + 1]
    if tokens and tokens[0] == "scan.py" and len(tokens) > 1:
        return tokens[1]
    return None


def _kalshi_crypto_grid_source_files(kalshi_payload: Any) -> list[str]:
    if not isinstance(kalshi_payload, dict):
        return []
    rows = kalshi_payload.get("rows")
    if not isinstance(rows, list):
        return []
    counts: Counter[str] = Counter()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("market_shape") != "point_in_time_threshold" or not row.get("typed_complete"):
            continue
        source_file = _string_or_none(row.get("raw_source_file") or row.get("source_file") or row.get("source_path"))
        if source_file:
            counts[_repo_relative_path(source_file)] += 1
    return [path for path, _count in counts.most_common()]


def _repo_relative_path(path_value: str) -> str:
    path_text = str(path_value).replace("\\", "/")
    marker = "relative-value-scanner/"
    if marker in path_text:
        path_text = path_text.split(marker, 1)[1]
    return path_text


def _snapshot_contains_ticker(path: Path, ticker: str) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    for row in _rows_from_any_payload(payload):
        if not isinstance(row, dict):
            continue
        if str(row.get("ticker") or row.get("market_id") or "").strip() == ticker:
            return True
    return False


def _rows_from_any_payload(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("markets", "rows", "normalized_markets", "data", "records"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _orderbook_output_path_for_snapshot(source_path: str) -> str:
    path = Path(source_path)
    stem = path.stem
    parent_name = path.parent.name or "kalshi"
    safe_parent = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in parent_name)
    safe_stem = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in stem)
    return f"reports/{safe_parent}_{safe_stem}_orderbook_enriched.json"


def _normalize_date_token(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip().rstrip(",")
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError:
        pass
    months = {
        "january": "01", "february": "02", "march": "03", "april": "04",
        "may": "05", "june": "06", "july": "07", "august": "08",
        "september": "09", "october": "10", "november": "11", "december": "12",
    }
    import re

    match = re.match(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", text)
    if match:
        month_word = match.group(1).lower()
        if month_word in months:
            return f"{match.group(3)}-{months[month_word]}-{int(match.group(2)):02d}"
    match2 = re.match(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text)
    if match2:
        month_word = match2.group(2).lower()
        if month_word in months:
            return f"{match2.group(3)}-{months[month_word]}-{int(match2.group(1)):02d}"
    return text


def _parse_date_only(value: Any) -> date | None:
    iso = _normalize_date_token(value)
    if not iso:
        return None
    try:
        return datetime.strptime(iso, "%Y-%m-%d").date()
    except ValueError:
        return None


def _readable_date(value: str | None) -> str | None:
    parsed = _parse_date_only(value)
    if parsed is None:
        return None
    return parsed.strftime("%B %d, %Y")


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _md_cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "/").replace("\n", " ")


def _quote_display(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".") or "0"
    return str(value)


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
