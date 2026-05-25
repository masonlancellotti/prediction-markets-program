from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
DISCLAIMER = (
    "Saved-file market expansion planner only. Suggested commands are read-only examples for refreshing inventory; "
    "this report does not fetch by default, execute, place orders, create trusted relationships, or emit PAPER_CANDIDATE."
)
REVIEW_ONLY_STATUS_NOTE = (
    "REVIEW_ONLY_EXACT_GROUPS_PRESENT means exact groups exist for human review only; "
    "it is not paperable, not evaluator-ready, and not a PAPER_CANDIDATE path."
)


@dataclass(frozen=True)
class MarketFamilySpec:
    family_id: str
    label: str
    required_exact_keys: tuple[str, ...]
    suggested_readonly_commands: tuple[str, ...]
    required_parser_improvements: tuple[str, ...]


def default_market_family_specs() -> list[MarketFamilySpec]:
    return [
        MarketFamilySpec(
            family_id="crypto_thresholds",
            label="Crypto thresholds",
            required_exact_keys=("asset", "source/settlement basis", "date/window", "comparator/threshold", "units", "side", "venue"),
            suggested_readonly_commands=(
                "python scan.py fetch-live-overlap-universe --category crypto --query BTC --output-dir reports/live_readonly/btc --report-dir reports/live_readonly/btc --label btc",
                "python scan.py fetch-live-overlap-universe --category crypto --query ETH --output-dir reports/live_readonly/eth --report-dir reports/live_readonly/eth --label eth",
            ),
            required_parser_improvements=(
                "Canonicalize exchange/index settlement sources from venue rules.",
                "Separate threshold ladders and range buckets from exact threshold markets.",
                "Normalize observation windows and settlement timestamps before same-payoff board review.",
            ),
        ),
        MarketFamilySpec(
            family_id="fed_fomc_target_ranges",
            label="Fed/FOMC target ranges",
            required_exact_keys=("meeting date", "target range lower/upper", "units", "source/settlement basis", "settlement timing", "side", "venue"),
            suggested_readonly_commands=(
                "python scan.py fetch-live-overlap-universe --category macro --query Fed --output-dir reports/live_readonly/fed --report-dir reports/live_readonly/fed --label fed",
                "python scan.py fetch-live-overlap-universe --category macro --query FOMC --output-dir reports/live_readonly/fed_fomc --report-dir reports/live_readonly/fed_fomc --label fed_fomc",
            ),
            required_parser_improvements=(
                "Convert upper-bound threshold ladders into diagnostics without treating them as exact ranges.",
                "Canonicalize meeting identity, statement timing, and Federal Reserve settlement basis.",
                "Add fixture coverage for exact target-range buckets if both venues list them.",
            ),
        ),
        MarketFamilySpec(
            family_id="sports_champions_winners",
            label="Sports champions/winners",
            required_exact_keys=("league", "season/event", "team/entity", "winner/champion scope", "source/settlement basis", "date/window", "side", "venue"),
            suggested_readonly_commands=(
                "python scan.py fetch-live-overlap-universe --category sports --query MLB --max-markets 1000 --kalshi-max-pages 20 --output-dir reports/live_readonly/mlb --report-dir reports/live_readonly/mlb --label mlb",
                "python scan.py fetch-live-overlap-universe --category sports --query NBA --output-dir reports/live_readonly/nba --report-dir reports/live_readonly/nba --label nba",
                "python scan.py fetch-live-overlap-universe --category sports --query NHL --output-dir reports/live_readonly/nhl --report-dir reports/live_readonly/nhl --label nhl",
            ),
            required_parser_improvements=(
                "Keep settlement/source mismatch fail-closed until exact venue terms prove same payoff.",
                "Separate same-scope inventory from trusted same-payoff evidence.",
                "Add season and event identity checks for each league-specific runner.",
            ),
        ),
        MarketFamilySpec(
            family_id="election_exhaustive_groups",
            label="Election exhaustive groups",
            required_exact_keys=("office/event", "jurisdiction", "candidate/party", "exhaustive group identity", "source/settlement basis", "date/window", "side", "venue"),
            suggested_readonly_commands=(
                "python scan.py fetch-live-overlap-universe --category politics --query election --output-dir reports/live_readonly/election --report-dir reports/live_readonly/election --label election",
            ),
            required_parser_improvements=(
                "Identify mutually exclusive exhaustive candidate groups without title-similarity promotion.",
                "Normalize jurisdiction, office, party/candidate identity, and settlement source.",
            ),
        ),
        MarketFamilySpec(
            family_id="weather_thresholds_ranges",
            label="Weather thresholds/ranges",
            required_exact_keys=("location/station", "measurement source", "date/window", "comparator/range/threshold", "units", "side", "venue"),
            suggested_readonly_commands=(
                "python scan.py fetch-live-overlap-universe --category weather --query weather --output-dir reports/live_readonly/weather --report-dir reports/live_readonly/weather --label weather",
            ),
            required_parser_improvements=(
                "Canonicalize station/location and measurement source.",
                "Normalize threshold units, accumulation windows, and observation timestamps.",
            ),
        ),
    ]


def build_exact_market_expansion_plan(
    *,
    project_root: Path,
    readiness_payload: dict[str, Any],
    generated_at: datetime | None = None,
    family_specs: list[MarketFamilySpec] | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    rows = readiness_payload.get("universes") if isinstance(readiness_payload.get("universes"), list) else []
    families = [_family_row(spec, rows) for spec in (family_specs or default_market_family_specs())]
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "exact_market_expansion_planner_v1",
        "generated_at": generated.isoformat(),
        "summary": {
            "family_count": len(families),
            "paper_candidate_count": 0,
            "cross_venue_exact_group_count": sum(int(row.get("cross_venue_exact_group_count") or 0) for row in families),
            "not_exact_pipeline_count": sum(1 for row in families if row["paperability_status"] == "NOT_EXACT_PIPELINE"),
        },
        "families": families,
        "next_suggested_readonly_market_refreshes": _next_readonly_refreshes(families, limit=5),
        "safety": {
            "saved_files_only": True,
            "live_fetch_attempted": False,
            "suggested_commands_are_examples_only": True,
            "execution_logic_added": False,
            "trusted_relationships_created": False,
            "title_similarity_used_as_exactness": False,
            "paper_candidate_emitted": False,
            "affects_evaluator_gates": False,
        },
        "status_notes": {
            "REVIEW_ONLY_EXACT_GROUPS_PRESENT": REVIEW_ONLY_STATUS_NOTE,
        },
        "disclaimer": DISCLAIMER,
    }


def write_exact_market_expansion_plan_files(
    *,
    project_root: Path,
    readiness_payload: dict[str, Any],
    json_output_path: Path | None = None,
    markdown_output_path: Path | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    json_path = json_output_path or project_root / "reports" / "exact_market_expansion_plan.json"
    markdown_path = markdown_output_path or project_root / "reports" / "exact_market_expansion_plan.md"
    payload = build_exact_market_expansion_plan(
        project_root=project_root,
        readiness_payload=readiness_payload,
        generated_at=generated_at,
    )
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(render_exact_market_expansion_plan_markdown(payload), encoding="utf-8")
    return payload


def render_exact_market_expansion_plan_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Exact Market Expansion Plan",
        "",
        payload["disclaimer"],
        "",
        "## Families",
        "",
        "| Family | Inventory | Typed | Exact groups | Cross-venue groups | Paperability | Top blockers |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for row in payload["families"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row["label"]),
                    _md(row["current_saved_inventory_count"]),
                    _md(row["typed_formula_count"]),
                    _md(row["exact_group_count"]),
                    _md(row["cross_venue_exact_group_count"]),
                    _md(row["paperability_status"]),
                    _md(",".join(item["blocker"] for item in row["top_blockers"]) or "none"),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Read-Only Refresh Examples", ""])
    for command in payload["next_suggested_readonly_market_refreshes"]:
        lines.extend(["```powershell", command["command"], "```", ""])
    lines.extend(["", "## Status Notes", "", f"- {REVIEW_ONLY_STATUS_NOTE}"])
    return "\n".join(lines)


def _family_row(spec: MarketFamilySpec, universe_rows: list[Any]) -> dict[str, Any]:
    if spec.family_id == "crypto_thresholds":
        metrics = _btc_metrics(universe_rows)
    elif spec.family_id == "fed_fomc_target_ranges":
        metrics = _fed_metrics(universe_rows)
    elif spec.family_id == "sports_champions_winners":
        metrics = _sports_metrics(universe_rows)
    else:
        metrics = _empty_metrics()
    paperability = "REVIEW_ONLY_EXACT_GROUPS_PRESENT" if metrics["cross_venue_exact_group_count"] > 0 else "NOT_EXACT_PIPELINE"
    return {
        "family": spec.family_id,
        "label": spec.label,
        "required_exact_keys": list(spec.required_exact_keys),
        "current_saved_inventory_count": metrics["inventory"],
        "typed_formula_count": metrics["typed"],
        "exact_group_count": metrics["exact_groups"],
        "cross_venue_exact_group_count": metrics["cross_venue_exact_group_count"],
        "top_blockers": metrics["top_blockers"],
        "next_suggested_readonly_fetch_commands": [
            {"command": command, "read_only": True, "example_only": True}
            for command in spec.suggested_readonly_commands
        ],
        "required_parser_improvements": list(spec.required_parser_improvements),
        "paperability_status": paperability,
        "paperability_status_note": REVIEW_ONLY_STATUS_NOTE if paperability == "REVIEW_ONLY_EXACT_GROUPS_PRESENT" else "Not exact pipeline; no paperability implied.",
        "paper_candidate_count": 0,
        "trusted_relationships_created": False,
        "affects_evaluator_readiness": False,
    }


def _btc_metrics(rows: list[Any]) -> dict[str, Any]:
    row = _find_row(rows, "btc_thresholds")
    scope = row.get("exact_scope") if isinstance(row.get("exact_scope"), dict) else {}
    counts = scope.get("btc_exact_threshold_counts") if isinstance(scope.get("btc_exact_threshold_counts"), dict) else {}
    diagnostic = scope.get("btc_exact_threshold_diagnostic") if isinstance(scope.get("btc_exact_threshold_diagnostic"), dict) else {}
    return {
        "inventory": int(counts.get("btc_inventory_count") or 0),
        "typed": int(counts.get("typed_btc_formula_count") or 0),
        "exact_groups": int(counts.get("exact_key_group_count") or 0),
        "cross_venue_exact_group_count": int(counts.get("exact_cross_venue_key_group_count") or 0),
        "top_blockers": _blockers(diagnostic),
    }


def _fed_metrics(rows: list[Any]) -> dict[str, Any]:
    row = _find_row(rows, "fed_fomc_decisions")
    scope = row.get("exact_scope") if isinstance(row.get("exact_scope"), dict) else {}
    counts = scope.get("fed_fomc_exact_range_counts") if isinstance(scope.get("fed_fomc_exact_range_counts"), dict) else {}
    diagnostic = scope.get("fed_fomc_exact_range_diagnostic") if isinstance(scope.get("fed_fomc_exact_range_diagnostic"), dict) else {}
    return {
        "inventory": int(counts.get("fed_inventory_count") or 0),
        "typed": int(counts.get("typed_fed_formula_count") or 0),
        "exact_groups": int(counts.get("exact_meeting_range_group_count") or 0),
        "cross_venue_exact_group_count": int(counts.get("exact_cross_venue_meeting_range_group_count") or 0),
        "top_blockers": _blockers(diagnostic),
    }


def _sports_metrics(rows: list[Any]) -> dict[str, Any]:
    sports = [
        row for row in rows
        if isinstance(row, dict) and str(row.get("category") or "").startswith("sports")
    ]
    return {
        "inventory": sum(int((row.get("inventory") or {}).get("polymarket_count") or 0) + int((row.get("inventory") or {}).get("kalshi_count") or 0) for row in sports),
        "typed": sum(int(row.get("same_scope_pair_count") or 0) for row in sports),
        "exact_groups": sum(int(row.get("strict_same_payoff_passes") or row.get("strict_same_payoff_pass_count") or 0) for row in sports),
        "cross_venue_exact_group_count": sum(int(row.get("trusted_relationships_attached") or row.get("trusted_relationship_count") or 0) for row in sports),
        "top_blockers": _merge_top_blockers(sports),
    }


def _empty_metrics() -> dict[str, Any]:
    return {
        "inventory": 0,
        "typed": 0,
        "exact_groups": 0,
        "cross_venue_exact_group_count": 0,
        "top_blockers": [{"blocker": "no_saved_inventory", "count": 1}],
    }


def _find_row(rows: list[Any], universe_id: str) -> dict[str, Any]:
    for row in rows:
        if isinstance(row, dict) and row.get("universe_id") == universe_id:
            return row
    return {}


def _blockers(diagnostic: dict[str, Any]) -> list[dict[str, Any]]:
    raw = diagnostic.get("top_blockers") if isinstance(diagnostic.get("top_blockers"), list) else []
    return [item for item in raw if isinstance(item, dict)][:5] or [{"blocker": "no_saved_inventory_or_no_parser_output", "count": 1}]


def _merge_top_blockers(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for row in rows:
        for blocker in row.get("top_fail_closed_reasons") or row.get("blockers") or []:
            counts[str(blocker)] = counts.get(str(blocker), 0) + 1
    if not counts:
        return [{"blocker": "no_strict_same_payoff_proof", "count": 1}]
    return [
        {"blocker": blocker, "count": count}
        for blocker, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:5]
    ]


def _next_readonly_refreshes(families: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    commands = []
    for family in families:
        for command in family.get("next_suggested_readonly_fetch_commands") or []:
            if len(commands) >= limit:
                return commands
            commands.append(
                {
                    "family": family["family"],
                    "command": command["command"],
                    "read_only": True,
                    "example_only": True,
                }
            )
    return commands


def _md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
