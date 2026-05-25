from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
KNOWN_FEE_MODEL = "known_conservative"
UNKNOWN_FEE_MODEL = "unknown_blocks_paperability"
KNOWN_SETTLEMENT_METADATA = "known_from_market_rules"
UNKNOWN_SETTLEMENT_METADATA = "unknown_blocks_exactness"
PLANNING_DISCLAIMER = (
    "Planning-only platform/API expansion matrix. This report does not fetch live APIs, authenticate, "
    "call private endpoints, execute trades, create trusted relationships, or emit PAPER_CANDIDATE."
)


@dataclass(frozen=True)
class PlatformCapability:
    venue_id: str
    display_name: str
    executable_orderbook_available: bool
    fee_model_status: str
    settlement_metadata_quality: str
    market_metadata_quality: str
    auth_required: bool
    private_api_required: bool
    supported_family_ids: tuple[str, ...]
    reference_only: bool
    blockers: tuple[str, ...]


def default_platform_capabilities() -> list[PlatformCapability]:
    return [
        PlatformCapability(
            venue_id="kalshi",
            display_name="Kalshi",
            executable_orderbook_available=True,
            fee_model_status=KNOWN_FEE_MODEL,
            settlement_metadata_quality=KNOWN_SETTLEMENT_METADATA,
            market_metadata_quality="implemented_read_only_snapshot",
            auth_required=False,
            private_api_required=False,
            supported_family_ids=("crypto_thresholds", "fed_fomc_target_ranges", "sports_champions_winners"),
            reference_only=False,
            blockers=(),
        ),
        PlatformCapability(
            venue_id="polymarket",
            display_name="Polymarket",
            executable_orderbook_available=True,
            fee_model_status=KNOWN_FEE_MODEL,
            settlement_metadata_quality=KNOWN_SETTLEMENT_METADATA,
            market_metadata_quality="implemented_read_only_snapshot",
            auth_required=False,
            private_api_required=False,
            supported_family_ids=("crypto_thresholds", "fed_fomc_target_ranges", "sports_champions_winners", "election_exhaustive_groups"),
            reference_only=False,
            blockers=(),
        ),
        PlatformCapability(
            venue_id="manifold_reference",
            display_name="Manifold / reference",
            executable_orderbook_available=False,
            fee_model_status=UNKNOWN_FEE_MODEL,
            settlement_metadata_quality=UNKNOWN_SETTLEMENT_METADATA,
            market_metadata_quality="reference_or_signal_only",
            auth_required=False,
            private_api_required=False,
            supported_family_ids=("election_exhaustive_groups", "weather_thresholds_ranges", "crypto_thresholds"),
            reference_only=True,
            blockers=("reference_only_not_executable", "unknown_fee_model_blocks_paperability", "unknown_settlement_metadata_blocks_exactness"),
        ),
        PlatformCapability(
            venue_id="prediction_market_aggregators_reference",
            display_name="Prediction market aggregators / reference",
            executable_orderbook_available=False,
            fee_model_status=UNKNOWN_FEE_MODEL,
            settlement_metadata_quality=UNKNOWN_SETTLEMENT_METADATA,
            market_metadata_quality="aggregated_reference_metadata_only",
            auth_required=False,
            private_api_required=False,
            supported_family_ids=("crypto_thresholds", "fed_fomc_target_ranges", "sports_champions_winners", "election_exhaustive_groups", "weather_thresholds_ranges"),
            reference_only=True,
            blockers=("reference_only_not_executable", "aggregated_prices_not_executable_orderbook", "unknown_fee_model_blocks_paperability", "unknown_settlement_metadata_blocks_exactness"),
        ),
        PlatformCapability(
            venue_id="sportsbook_reference",
            display_name="Sportsbook / reference",
            executable_orderbook_available=False,
            fee_model_status=UNKNOWN_FEE_MODEL,
            settlement_metadata_quality=UNKNOWN_SETTLEMENT_METADATA,
            market_metadata_quality="reference_odds_only",
            auth_required=False,
            private_api_required=False,
            supported_family_ids=("sports_champions_winners", "weather_thresholds_ranges"),
            reference_only=True,
            blockers=("reference_only_not_executable", "sportsbook_odds_not_prediction_market_leg", "unknown_fee_model_blocks_paperability", "unknown_settlement_metadata_blocks_exactness"),
        ),
    ]


def build_platform_expansion_matrix(
    *,
    generated_at: datetime | None = None,
    capabilities: list[PlatformCapability] | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    venues = [_venue_row(capability) for capability in (capabilities or default_platform_capabilities())]
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "platform_expansion_matrix_v1",
        "generated_at": generated.isoformat(),
        "summary": {
            "venue_count": len(venues),
            "reference_only_count": sum(1 for row in venues if row["reference_only"]),
            "executable_orderbook_known_count": sum(1 for row in venues if row["executable_orderbook_available"]),
            "paperable_platform_count": sum(1 for row in venues if row["paperability_status"] == "PLANNING_READY_NOT_PAPERABLE_BY_ITSELF"),
            "paper_candidate_count": 0,
        },
        "venues": venues,
        "top_platform_expansion_blockers": _top_blockers(venues),
        "safety": {
            "planning_only": True,
            "live_api_calls_added": False,
            "auth_or_private_endpoint_logic_added": False,
            "execution_logic_added": False,
            "reference_only_platforms_claimed_executable": False,
            "paper_candidate_emitted": False,
            "trusted_relationships_created": False,
            "affects_evaluator_gates": False,
        },
        "disclaimer": PLANNING_DISCLAIMER,
    }


def write_platform_expansion_matrix_files(
    *,
    project_root: Path,
    json_output_path: Path | None = None,
    markdown_output_path: Path | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    json_path = json_output_path or project_root / "reports" / "platform_expansion_matrix.json"
    markdown_path = markdown_output_path or project_root / "reports" / "platform_expansion_matrix.md"
    payload = build_platform_expansion_matrix(generated_at=generated_at)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(render_platform_expansion_matrix_markdown(payload), encoding="utf-8")
    return payload


def render_platform_expansion_matrix_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Platform Expansion Matrix",
        "",
        payload["disclaimer"],
        "",
        "## Venues",
        "",
        "| Venue | Orderbook | Fee model | Settlement metadata | Reference only | Paperability | Blockers |",
        "|---|---:|---|---|---:|---|---|",
    ]
    for row in payload["venues"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row["display_name"]),
                    _md(str(row["executable_orderbook_available"]).lower()),
                    _md(row["fee_model_status"]),
                    _md(row["settlement_metadata_quality"]),
                    _md(str(row["reference_only"]).lower()),
                    _md(row["paperability_status"]),
                    _md(",".join(row["blockers"]) or "none"),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Planning Notes", ""])
    lines.append("- A venue is not a paperable leg unless executable orderbook, fee model, and settlement metadata are all known.")
    lines.append("- Reference-only platforms can inform WATCH/MANUAL_REVIEW research only.")
    lines.append("- This matrix does not create candidates, trusted evidence, auth, private endpoint access, or execution logic.")
    return "\n".join(lines)


def _venue_row(capability: PlatformCapability) -> dict[str, Any]:
    blockers = list(capability.blockers)
    if capability.reference_only and "reference_only_not_executable" not in blockers:
        blockers.append("reference_only_not_executable")
    if not capability.executable_orderbook_available and "no_executable_orderbook" not in blockers:
        blockers.append("no_executable_orderbook")
    if capability.fee_model_status != KNOWN_FEE_MODEL and "unknown_fee_model_blocks_paperability" not in blockers:
        blockers.append("unknown_fee_model_blocks_paperability")
    if capability.settlement_metadata_quality != KNOWN_SETTLEMENT_METADATA and "unknown_settlement_metadata_blocks_exactness" not in blockers:
        blockers.append("unknown_settlement_metadata_blocks_exactness")
    paperability = (
        "PLANNING_READY_NOT_PAPERABLE_BY_ITSELF"
        if not blockers
        and capability.executable_orderbook_available
        and capability.fee_model_status == KNOWN_FEE_MODEL
        and capability.settlement_metadata_quality == KNOWN_SETTLEMENT_METADATA
        else "NOT_PAPERABLE_PLATFORM_PLANNING_ONLY"
    )
    return {
        "venue_id": capability.venue_id,
        "display_name": capability.display_name,
        "executable_orderbook_available": capability.executable_orderbook_available,
        "fee_model_status": capability.fee_model_status,
        "settlement_metadata_quality": capability.settlement_metadata_quality,
        "market_metadata_quality": capability.market_metadata_quality,
        "auth_required": capability.auth_required,
        "private_api_required": capability.private_api_required,
        "supported_family_ids": list(capability.supported_family_ids),
        "reference_only": capability.reference_only,
        "blockers": blockers,
        "paperability_status": paperability,
        "can_be_executable_leg": paperability == "PLANNING_READY_NOT_PAPERABLE_BY_ITSELF",
        "paper_candidate_emitted": False,
        "trusted_relationships_created": False,
    }


def _top_blockers(venues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for row in venues:
        for blocker in row["blockers"]:
            counts[blocker] = counts.get(blocker, 0) + 1
    return [
        {"blocker": blocker, "count": count}
        for blocker, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:10]
    ]


def _md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")

