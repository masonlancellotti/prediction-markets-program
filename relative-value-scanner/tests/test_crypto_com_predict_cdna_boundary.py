import json
from datetime import datetime, timezone
from pathlib import Path

from relative_value.crypto_com_predict_cdna_read_only_boundary import (
    crypto_com_predict_cdna_read_only_boundary_report,
)
from relative_value.platform_api_expansion import (
    PLATFORM_ROLE_EXECUTABLE_EVENT_CONTRACT_EXCHANGE,
    REQUIRES_AUTH_REVIEW,
    build_platform_api_expansion_report,
)
from relative_value.source_registry import (
    ImplementationStatus,
    SourceType,
    can_create_tradable_candidate_pair,
    get_source_entry,
)
from venues.crypto_com_predict_cdna import (
    CRYPTO_COM_PREDICT_CDNA_RESEARCH_SCHEMA_KIND,
    CRYPTO_COM_PREDICT_CDNA_REQUIRED_BLOCKERS,
    load_crypto_com_predict_cdna_research_fixtures,
)


NOW = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
PROJECT_ROOT = Path(__file__).parents[1]


def test_crypto_com_predict_cdna_source_registry_is_planned_and_empty_effects() -> None:
    entry = get_source_entry("crypto_com_predict_cdna")

    assert entry.display_name == "Crypto.com Predict / CDNA"
    assert entry.source_type == SourceType.EXECUTABLE_VENUE
    assert entry.implementation_status == ImplementationStatus.PLANNED_NOT_IMPLEMENTED
    assert entry.allowed_effects == ()
    assert entry.can_create_candidate_pair is False
    assert can_create_tradable_candidate_pair("crypto_com_predict_cdna", "kalshi") is False
    assert "not a generic crypto exchange" in entry.notes


def test_crypto_com_predict_cdna_boundary_report_is_inert_and_fail_closed() -> None:
    report = crypto_com_predict_cdna_read_only_boundary_report()

    assert report["source_id"] == "crypto_com_predict_cdna"
    assert report["source_type"] == "EXECUTABLE_VENUE"
    assert report["implementation_status"] == "PLANNED_NOT_IMPLEMENTED"
    assert report["status"] == "boundary_and_fixture_schema_only_no_live_transport"
    assert report["expected_env_vars"] == []
    assert report["fixture_research_schema_exists"] is True
    assert report["execution_allowed_in_project_now"] is False
    assert report["can_create_candidate_pair"] is False
    assert report["can_create_paper_candidate"] is False

    category_names = {row["name"] for row in report["data_categories"]}
    assert {
        "market_discovery",
        "orderbook_depth",
        "settlement_metadata",
        "fee_metadata",
        "region_eligibility",
        "account_balances_positions_or_orders",
    }.issubset(category_names)
    forbidden = next(row for row in report["data_categories"] if row["name"] == "account_balances_positions_or_orders")
    assert forbidden["allowed_read_only_research"] is False
    assert forbidden["forbidden_account_or_execution_surface"] is True

    stages = {row["name"]: row for row in report["stages"]}
    assert stages["boundary_and_fixture_schema_only"]["allowed"] is True
    assert stages["fixture_backed_research_schema"]["allowed"] is True
    assert stages["live_read_only_transport_after_separate_review"]["allowed"] is False
    assert stages["matcher_or_evaluator_integration_after_separate_review"]["allowed"] is False
    assert report["raw_redaction_policy"]["allow_raw_network_echo"] is False

    serialized = json.dumps(report)
    assert "PAPER_CANDIDATE" not in serialized
    assert "POSSIBLE_ARB" not in serialized


def test_crypto_com_predict_cdna_boundary_adds_no_transport_imports() -> None:
    source = (PROJECT_ROOT / "relative_value" / "crypto_com_predict_cdna_read_only_boundary.py").read_text(
        encoding="utf-8"
    )

    forbidden_terms = (
        "requests",
        "httpx",
        "aiohttp",
        "socket",
        "websocket",
        "urllib",
        "http.client",
        "ssl",
        "clientportal",
        "webapi",
    )
    assert all(term not in source for term in forbidden_terms)


def test_crypto_com_predict_cdna_fixture_loader_returns_saved_fixture_record() -> None:
    fixture_dir = PROJECT_ROOT / "venues" / "fixtures" / "crypto_com_predict_cdna"
    records = load_crypto_com_predict_cdna_research_fixtures(fixture_dir)

    assert len(records) == 1
    record = records[0]
    assert record["schema_kind"] == CRYPTO_COM_PREDICT_CDNA_RESEARCH_SCHEMA_KIND
    assert record["source_id"] == "crypto_com_predict_cdna"
    assert record["market_id"] == "CDNA-FAKE-MARKET-001"
    assert record["event_id"] == "CDNA-FAKE-EVENT-001"
    assert record["permission"] == "FIXTURE_RESEARCH_ONLY"
    assert record["live_fetch_attempted"] is False
    assert record["is_executable"] is False
    assert record["execution_allowed_in_project_now"] is False
    assert record["can_create_candidate_pair"] is False
    assert record["can_create_paper_candidate"] is False
    assert record["diagnostic_only"] is True
    assert record["affects_evaluator_gates"] is False
    assert record["raw_source_file"].endswith("example_market.json")
    assert record["raw_row_index"] == 0
    assert set(CRYPTO_COM_PREDICT_CDNA_REQUIRED_BLOCKERS).issubset(record["unresolved_blockers"])

    serialized = json.dumps(record)
    assert "PAPER_CANDIDATE" not in serialized
    assert "POSSIBLE_ARB" not in serialized


def test_crypto_com_predict_cdna_fixture_loader_adds_no_transport_imports() -> None:
    source = (PROJECT_ROOT / "venues" / "crypto_com_predict_cdna.py").read_text(encoding="utf-8")

    forbidden_terms = (
        "requests",
        "httpx",
        "aiohttp",
        "socket",
        "websocket",
        "urllib",
        "http.client",
        "ssl",
        "clientportal",
        "webapi",
    )
    assert all(term not in source for term in forbidden_terms)


def test_platform_api_expansion_merges_crypto_registry_entry_with_bespoke_profile(tmp_path) -> None:
    report = build_platform_api_expansion_report(project_root=PROJECT_ROOT, input_dir=tmp_path, generated_at=NOW)
    rows = [row for row in report["platforms"] if row["platform_id"] == "crypto_com_predict_cdna"]

    assert len(rows) == 1
    row = rows[0]
    assert row["display_name"] == "Crypto.com Predict / CDNA"
    assert row["source_registry_id"] == "crypto_com_predict_cdna"
    assert row["platform_role"] == PLATFORM_ROLE_EXECUTABLE_EVENT_CONTRACT_EXCHANGE
    assert row["execution_status"] == REQUIRES_AUTH_REVIEW
    assert row["adapter_priority"] == "P2"
    assert row["automatic_adapter_use_allowed"] is False
    assert row["candidate_actions_allowed"] is False
    assert row["affects_evaluator_gates"] is False
    assert row["current_repo_evidence"]["source_registry"]["implementation_status"] == "PLANNED_NOT_IMPLEMENTED"
    assert row["current_repo_evidence"]["source_registry"]["allowed_effects"] == []
    assert row["current_repo_evidence"]["source_registry"]["can_create_candidate_pair"] is False
    assert row["current_repo_evidence"]["saved_files"]["fixture_file_count"] >= 1
    assert "source_registry_planned_not_implemented" in row["blockers"]
    assert "auth_or_account_permission_review_required" in row["blockers"]

    serialized = json.dumps(row)
    assert "PAPER_CANDIDATE" not in serialized
    assert "POSSIBLE_ARB" not in serialized
