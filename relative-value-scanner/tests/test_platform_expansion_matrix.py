import json
from datetime import datetime, timezone

from relative_value.platform_expansion_matrix import (
    PlatformCapability,
    UNKNOWN_FEE_MODEL,
    UNKNOWN_SETTLEMENT_METADATA,
    build_platform_expansion_matrix,
    render_platform_expansion_matrix_markdown,
)


NOW = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)


def test_reference_only_venues_cannot_be_executable_legs() -> None:
    report = build_platform_expansion_matrix(generated_at=NOW)
    rows = {row["venue_id"]: row for row in report["venues"]}

    for venue_id in ("manifold_reference", "prediction_market_aggregators_reference", "sportsbook_reference"):
        row = rows[venue_id]
        assert row["reference_only"] is True
        assert row["can_be_executable_leg"] is False
        assert "reference_only_not_executable" in row["blockers"]
        assert row["paperability_status"] == "NOT_PAPERABLE_PLATFORM_PLANNING_ONLY"


def test_unknown_fee_model_blocks_paperability() -> None:
    report = build_platform_expansion_matrix(
        generated_at=NOW,
        capabilities=[
            PlatformCapability(
                venue_id="test_unknown_fee",
                display_name="Test Unknown Fee",
                executable_orderbook_available=True,
                fee_model_status=UNKNOWN_FEE_MODEL,
                settlement_metadata_quality="known_from_market_rules",
                market_metadata_quality="fixture",
                auth_required=False,
                private_api_required=False,
                supported_family_ids=("crypto_thresholds",),
                reference_only=False,
                blockers=(),
            )
        ],
    )
    row = report["venues"][0]

    assert row["can_be_executable_leg"] is False
    assert "unknown_fee_model_blocks_paperability" in row["blockers"]
    assert row["paperability_status"] == "NOT_PAPERABLE_PLATFORM_PLANNING_ONLY"


def test_unknown_settlement_metadata_blocks_exactness() -> None:
    report = build_platform_expansion_matrix(
        generated_at=NOW,
        capabilities=[
            PlatformCapability(
                venue_id="test_unknown_settlement",
                display_name="Test Unknown Settlement",
                executable_orderbook_available=True,
                fee_model_status="known_conservative",
                settlement_metadata_quality=UNKNOWN_SETTLEMENT_METADATA,
                market_metadata_quality="fixture",
                auth_required=False,
                private_api_required=False,
                supported_family_ids=("fed_fomc_target_ranges",),
                reference_only=False,
                blockers=(),
            )
        ],
    )
    row = report["venues"][0]

    assert row["can_be_executable_leg"] is False
    assert "unknown_settlement_metadata_blocks_exactness" in row["blockers"]
    assert row["paperability_status"] == "NOT_PAPERABLE_PLATFORM_PLANNING_ONLY"


def test_platform_matrix_emits_planning_language_only() -> None:
    report = build_platform_expansion_matrix(generated_at=NOW)
    markdown = render_platform_expansion_matrix_markdown(report)

    assert "Planning-only platform/API expansion matrix" in report["disclaimer"]
    assert "planning-only" in markdown.lower()
    assert "Reference-only platforms can inform WATCH/MANUAL_REVIEW research only." in markdown
    assert report["safety"]["live_api_calls_added"] is False
    assert report["safety"]["auth_or_private_endpoint_logic_added"] is False
    assert report["safety"]["execution_logic_added"] is False


def test_platform_matrix_never_emits_paper_candidate() -> None:
    report = build_platform_expansion_matrix(generated_at=NOW)
    encoded = json.dumps(report)

    assert report["summary"]["paper_candidate_count"] == 0
    assert report["safety"]["paper_candidate_emitted"] is False
    assert all(row["paper_candidate_emitted"] is False for row in report["venues"])
    assert '"paper_candidate_emitted": true' not in encoded

