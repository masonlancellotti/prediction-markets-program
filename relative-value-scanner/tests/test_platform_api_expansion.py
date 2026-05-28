import json
from datetime import datetime, timezone

from relative_value.platform_api_expansion import (
    PLATFORM_ROLE_DISCOVERY_ONLY,
    PLATFORM_ROLE_EXECUTABLE_EVENT_CONTRACT_EXCHANGE,
    PLATFORM_ROLE_EXECUTABLE_PREDICTION_MARKET,
    PLATFORM_ROLE_EXECUTABLE_SPORTSBOOK_OR_BETTING_EXCHANGE,
    PLATFORM_ROLE_REFERENCE_ONLY_TRUTH_FEED,
    PLATFORM_ROLE_UNKNOWN,
    REFERENCE_ONLY,
    REQUIRES_AUTH_REVIEW,
    UNKNOWN_STATUS,
    build_platform_api_expansion_report,
    render_platform_api_expansion_markdown,
)


NOW = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)


def test_matrix_includes_current_known_platforms(tmp_path) -> None:
    report = build_platform_api_expansion_report(project_root=tmp_path, input_dir=tmp_path, generated_at=NOW)
    rows = {row["platform_id"]: row for row in report["platforms"]}

    for platform_id in ("kalshi", "polymarket", "ibkr_forecastex", "manifold", "prophetx", "sx_bet"):
        assert platform_id in rows

    assert rows["kalshi"]["read_only_api_available"] is True
    assert rows["polymarket"]["read_only_api_available"] is True
    assert rows["sx_bet"]["adapter_priority"] == "P0"
    assert report["summary"]["platform_count"] >= 6


def test_unknown_platforms_do_not_crash_and_fail_closed(tmp_path) -> None:
    report = build_platform_api_expansion_report(
        project_root=tmp_path,
        input_dir=tmp_path,
        generated_at=NOW,
        extra_platforms=[{"platform_id": "future venue", "display_name": "Future Venue"}],
    )
    rows = {row["platform_id"]: row for row in report["platforms"]}
    row = rows["future_venue"]

    assert row["execution_status"] == UNKNOWN_STATUS
    assert row["adapter_priority"] == "P3"
    assert row["automatic_adapter_use_allowed"] is False
    assert "unknown_or_unreviewed_platform_contract" in row["blockers"]


def test_auth_required_platforms_are_blocked_from_automatic_adapter_use(tmp_path) -> None:
    report = build_platform_api_expansion_report(project_root=tmp_path, input_dir=tmp_path, generated_at=NOW)
    rows = {row["platform_id"]: row for row in report["platforms"]}

    for platform_id in ("ibkr_forecastex", "prophetx"):
        row = rows[platform_id]
        assert row["execution_status"] == REQUIRES_AUTH_REVIEW
        assert row["automatic_adapter_use_allowed"] is False
        assert "auth_required_for_market_data" in row["blockers"]
        assert "automatic_adapter_use_blocked" in row["blockers"]
        assert row["candidate_actions_allowed"] is False


def test_reference_only_platforms_cannot_become_executable(tmp_path) -> None:
    report = build_platform_api_expansion_report(project_root=tmp_path, input_dir=tmp_path, generated_at=NOW)
    rows = {row["platform_id"]: row for row in report["platforms"]}

    for platform_id in ("manifold", "the_odds_api", "sportsbooks"):
        row = rows[platform_id]
        assert row["execution_status"] == REFERENCE_ONLY
        assert row["candidate_actions_allowed"] is False
        assert row["affects_evaluator_gates"] is False
        assert "reference_only_not_executable" in row["blockers"]


def test_adapter_priority_is_deterministic(tmp_path) -> None:
    first = build_platform_api_expansion_report(project_root=tmp_path, input_dir=tmp_path, generated_at=NOW)
    second = build_platform_api_expansion_report(project_root=tmp_path, input_dir=tmp_path, generated_at=NOW)

    first_ids = [row["platform_id"] for row in first["platforms"]]
    second_ids = [row["platform_id"] for row in second["platforms"]]
    assert first_ids == second_ids
    assert first["platforms"][0]["platform_id"] == "sx_bet"
    assert first["recommendations"]["best_next_platform_adapter"] == "sx_bet"
    assert first["recommendations"]["best_next_family_universe"] == "SPORTS"


def test_report_emits_no_new_candidate_status(tmp_path) -> None:
    report = build_platform_api_expansion_report(project_root=tmp_path, input_dir=tmp_path, generated_at=NOW)
    markdown = render_platform_api_expansion_markdown(report)
    encoded = json.dumps(report)

    assert report["summary"]["new_paper_actions_created"] == 0
    assert report["safety"]["candidate_actions_created"] is False
    assert all(row["candidate_actions_allowed"] is False for row in report["platforms"])
    assert "PAPER_CANDIDATE" not in encoded
    assert "PAPER_CANDIDATE" not in markdown


def test_crypto_com_predict_cdna_is_event_contract_exchange_requiring_review(tmp_path) -> None:
    report = build_platform_api_expansion_report(project_root=tmp_path, input_dir=tmp_path, generated_at=NOW)
    rows = {row["platform_id"]: row for row in report["platforms"]}

    assert "crypto_com_predict_cdna" in rows
    row = rows["crypto_com_predict_cdna"]
    assert row["platform_role"] == PLATFORM_ROLE_EXECUTABLE_EVENT_CONTRACT_EXCHANGE
    assert row["execution_status"] == REQUIRES_AUTH_REVIEW
    assert row["review_flags"]["requires_auth_review"] is True
    assert row["review_flags"]["requires_region_review"] is True
    assert row["review_flags"]["requires_execution_mechanics_review"] is True
    assert row["automatic_adapter_use_allowed"] is False
    assert row["candidate_actions_allowed"] is False
    assert "region_eligibility_review_required" in row["blockers"]
    assert "execution_mechanics_review_required" in row["blockers"]


def test_platform_role_taxonomy_is_set_for_known_platforms(tmp_path) -> None:
    report = build_platform_api_expansion_report(project_root=tmp_path, input_dir=tmp_path, generated_at=NOW)
    rows = {row["platform_id"]: row for row in report["platforms"]}

    expected_roles = {
        "kalshi": PLATFORM_ROLE_EXECUTABLE_EVENT_CONTRACT_EXCHANGE,
        "polymarket": PLATFORM_ROLE_EXECUTABLE_PREDICTION_MARKET,
        "ibkr_forecastex": PLATFORM_ROLE_EXECUTABLE_EVENT_CONTRACT_EXCHANGE,
        "crypto_com_predict_cdna": PLATFORM_ROLE_EXECUTABLE_EVENT_CONTRACT_EXCHANGE,
        "sx_bet": PLATFORM_ROLE_EXECUTABLE_SPORTSBOOK_OR_BETTING_EXCHANGE,
        "prophetx": PLATFORM_ROLE_EXECUTABLE_SPORTSBOOK_OR_BETTING_EXCHANGE,
        "the_odds_api": PLATFORM_ROLE_REFERENCE_ONLY_TRUTH_FEED,
        "manifold": PLATFORM_ROLE_DISCOVERY_ONLY,
    }
    for platform_id, role in expected_roles.items():
        assert rows[platform_id]["platform_role"] == role, platform_id


def test_sportsbook_or_exchange_rows_carry_mechanics_review_blocker(tmp_path) -> None:
    report = build_platform_api_expansion_report(project_root=tmp_path, input_dir=tmp_path, generated_at=NOW)
    rows = {row["platform_id"]: row for row in report["platforms"]}

    for platform_id in ("sx_bet", "prophetx"):
        blockers = rows[platform_id]["blockers"]
        assert "sportsbook_or_exchange_mechanics_review_required" in blockers
        assert "execution_mechanics_review_required" in blockers


def test_summary_exposes_role_counts_and_review_counts(tmp_path) -> None:
    report = build_platform_api_expansion_report(project_root=tmp_path, input_dir=tmp_path, generated_at=NOW)
    summary = report["summary"]

    role_counts = summary["platform_role_counts"]
    assert role_counts.get(PLATFORM_ROLE_EXECUTABLE_EVENT_CONTRACT_EXCHANGE, 0) >= 3
    assert role_counts.get(PLATFORM_ROLE_EXECUTABLE_SPORTSBOOK_OR_BETTING_EXCHANGE, 0) >= 2
    assert summary["requires_region_review_count"] >= 4
    assert summary["requires_execution_mechanics_review_count"] >= 3


def test_source_registry_only_rows_get_classified_role(tmp_path) -> None:
    # metaculus and sportsbooks come from source_registry only — they used to
    # land with platform_role=UNKNOWN, which made the platforms_to_avoid list
    # ambiguous. _profile_from_source_entry now picks the right role from
    # source_type so the platform expansion report tells operators what each
    # row is for.
    report = build_platform_api_expansion_report(project_root=tmp_path, input_dir=tmp_path, generated_at=NOW)
    rows = {row["platform_id"]: row for row in report["platforms"]}

    assert rows["metaculus"]["platform_role"] == PLATFORM_ROLE_DISCOVERY_ONLY
    assert rows["sportsbooks"]["platform_role"] == PLATFORM_ROLE_REFERENCE_ONLY_TRUTH_FEED


def test_unknown_extra_platform_defaults_to_unknown_role(tmp_path) -> None:
    report = build_platform_api_expansion_report(
        project_root=tmp_path,
        input_dir=tmp_path,
        generated_at=NOW,
        extra_platforms=[{"platform_id": "mystery_venue"}],
    )
    rows = {row["platform_id"]: row for row in report["platforms"]}

    row = rows["mystery_venue"]
    assert row["platform_role"] == PLATFORM_ROLE_UNKNOWN
    assert "platform_role_unclassified" in row["blockers"]
