import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import scan
from relative_value.executable_venue_plan import recommended_next_executable_adapter, venue_capability
from relative_value.live_snapshot_matcher import match_snapshot_files
from relative_value.source_registry import (
    ImplementationStatus,
    SourceType,
    can_create_tradable_candidate_pair,
    get_source_entry,
)
from relative_value.sx_bet_live_read_only_boundary import sx_bet_live_read_only_boundary_report
from venues.sx_bet import (
    SX_BET_RESEARCH_SCHEMA_KIND,
    build_sx_bet_research_snapshot,
    load_sx_bet_research_fixture,
)


NOW = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)


def test_sx_bet_research_fixture_builds_non_executable_snapshot() -> None:
    fixture = Path("venues/fixtures/sx_bet_research_sample.json")

    snapshot = load_sx_bet_research_fixture(fixture, captured_at=NOW)

    assert snapshot["schema_version"] == 1
    assert snapshot["schema_kind"] == SX_BET_RESEARCH_SCHEMA_KIND
    assert snapshot["source_id"] == "sx_bet"
    assert snapshot["source_type"] == "EXECUTABLE_VENUE"
    assert snapshot["implementation_status"] == "PLANNED_NOT_IMPLEMENTED"
    assert snapshot["permission"] == "READ_ONLY_RESEARCH"
    assert snapshot["is_executable"] is False
    assert snapshot["can_create_candidate_pair"] is False
    assert snapshot["can_create_paper_candidate"] is False
    assert "normalized_markets" not in snapshot
    assert snapshot["research_market_count"] == 1


def test_sx_bet_research_orderbook_derives_taker_prices_and_depth() -> None:
    snapshot = load_sx_bet_research_fixture(Path("venues/fixtures/sx_bet_research_sample.json"), captured_at=NOW)
    orderbook = snapshot["research_markets"][0]["research_orderbook"]

    assert orderbook["best_taker_price_outcome_one"] == 0.54
    assert orderbook["depth_usdc_at_best_outcome_one"] == 500.0
    assert orderbook["best_taker_price_outcome_two"] == 0.48
    assert orderbook["depth_usdc_at_best_outcome_two"] == 999.65
    assert "not normalized prediction-market contracts" in orderbook["unit_warning"]


def test_sx_bet_research_snapshot_keeps_settlement_fee_and_restriction_metadata() -> None:
    snapshot = load_sx_bet_research_fixture(Path("venues/fixtures/sx_bet_research_sample.json"), captured_at=NOW)
    market = snapshot["research_markets"][0]

    assert market["market_hash"] == "0xabc123"
    assert market["event_title"] == "Boston Celtics vs New York Knicks"
    assert market["settlement_metadata"]["settlement_source"] == "official league result"
    assert market["fee_metadata"]["fee_model_status"] == "not_normalized"
    assert market["restrictions"]["requires_wallet_or_private_key_for_execution"] is True
    assert market["restrictions"]["execution_allowed_in_project_now"] is False
    assert market["restrictions"]["candidate_pair_allowed"] is False


def test_sx_bet_parser_handles_bad_orders_as_skipped_research_only() -> None:
    payload = {
        "markets": [
            {
                "marketHash": "0xmarket",
                "eventName": "Fixture A vs Fixture B",
                "outcomeOneName": "Fixture A",
                "outcomeTwoName": "Fixture B",
            }
        ],
        "orders": [
            {"marketHash": "0xmarket", "percentageOdds": "not-an-int", "totalBetSize": "1"},
            {"marketHash": "0xmarket", "isMakerBettingOutcomeOne": False, "percentageOdds": "42000000000000000000"},
        ],
    }

    snapshot = build_sx_bet_research_snapshot(payload, captured_at=NOW)

    orderbook = snapshot["research_markets"][0]["research_orderbook"]
    assert orderbook["order_count"] == 2
    assert orderbook["skipped_order_count"] == 2
    assert orderbook["best_taker_price_outcome_one"] is None


def test_sx_bet_registry_and_capability_remain_not_implemented_and_not_candidate_enabled() -> None:
    entry = get_source_entry("sx_bet")
    capability = venue_capability("sx_bet")

    assert entry.source_type == SourceType.EXECUTABLE_VENUE
    assert entry.implementation_status == ImplementationStatus.PLANNED_NOT_IMPLEMENTED
    assert entry.can_create_candidate_pair is False
    assert capability.execution_allowed_in_project_now is False
    assert capability.can_create_paper_candidate is False
    assert recommended_next_executable_adapter().source_id == "sx_bet"
    assert can_create_tradable_candidate_pair("sx_bet", "kalshi") is False


def test_sx_bet_research_snapshot_fails_closed_in_live_matcher_path(tmp_path: Path) -> None:
    sx_snapshot = load_sx_bet_research_fixture(Path("venues/fixtures/sx_bet_research_sample.json"), captured_at=NOW)
    kalshi_snapshot = {
        "schema_version": 1,
        "source": "kalshi_markets",
        "captured_at": NOW.isoformat(),
        "normalized_markets": [
            {
                "venue": "kalshi",
                "ticker": "KXNBA-CELTICS",
                "question": "Will the Boston Celtics beat the New York Knicks?",
                "event_title": "Boston Celtics vs New York Knicks",
                "close_time": "2026-05-21T23:00:00+00:00",
                "active": True,
                "closed": False,
                "status": "active",
                "liquidity": 100.0,
                "raw": {},
            }
        ],
    }
    sx_path = tmp_path / "sx_bet_research.json"
    kalshi_path = tmp_path / "kalshi.json"
    sx_path.write_text(json.dumps(sx_snapshot), encoding="utf-8")
    kalshi_path.write_text(json.dumps(kalshi_snapshot), encoding="utf-8")

    result = match_snapshot_files(sx_path, kalshi_path, now=NOW)

    assert result["pair_count"] == 0
    assert result["pairs"] == []
    assert "unsupported_schema_kind" in result["snapshot_issues"]["polymarket"]
    assert "missing_normalized_markets" in result["snapshot_issues"]["polymarket"]
    serialized = json.dumps(result)
    assert "PAPER_CANDIDATE" not in serialized
    assert "POSSIBLE_ARB" not in serialized


def test_default_scan_output_remains_unchanged(capsys) -> None:
    result = scan.main([])

    assert result == 0
    assert "relative_value_scan_status=OFFLINE_COMPLETE candidates=7 possible_arbs=0" in capsys.readouterr().out


def test_sx_bet_live_read_only_boundary_is_inert_and_non_candidate_enabled() -> None:
    report = sx_bet_live_read_only_boundary_report()

    assert report["status"] == "design_only_no_network"
    assert report["execution_allowed_in_project_now"] is False
    assert report["can_create_candidate_pair"] is False
    assert report["can_create_paper_candidate"] is False
    assert report["raw_redaction_policy"]["allow_raw_network_echo"] is False
    assert "markets" in {row["name"] for row in report["endpoint_categories"]}
    assert "active_orders" in {row["name"] for row in report["endpoint_categories"]}
    assert any(row["forbidden_execution_surface"] is True for row in report["endpoint_categories"])
    assert all(row["allowed"] is False for row in report["stages"] if row["stage"] > 0)
    assert any(row["name"] == "normalized_snapshot_manual_review_only" for row in report["stages"])
    assert all(row["name"] != "candidate_normalized_snapshot_manual_review_only" for row in report["stages"])


def test_sx_bet_boundary_introduces_no_live_transport_dependencies() -> None:
    forbidden_modules = {"requests", "httpx", "aiohttp", "web3", "websocket", "websockets", "eth_account"}

    assert forbidden_modules.isdisjoint(sys.modules)


def test_sx_bet_endpoint_category_safety_properties_are_pinned() -> None:
    categories = {row["name"]: row for row in sx_bet_live_read_only_boundary_report()["endpoint_categories"]}

    expected = {
        "markets": (True, False, False),
        "active_orders": (True, False, False),
        "trade_history": (True, False, False),
        "realtime_orderbook": (False, True, False),
        "post_or_fill_order": (False, True, True),
    }
    for name, (public_read_only, requires_auth, forbidden_execution_surface) in expected.items():
        assert categories[name]["public_read_only"] is public_read_only
        assert categories[name]["requires_auth"] is requires_auth
        assert categories[name]["forbidden_execution_surface"] is forbidden_execution_surface


def test_sx_bet_future_raw_redaction_fields_include_execution_adjacent_values() -> None:
    redaction_fields = set(sx_bet_live_read_only_boundary_report()["raw_redaction_policy"]["must_redact_fields"])

    assert {
        "authorization",
        "authToken",
        "token",
        "signature",
        "privateKey",
        "wallet",
        "maker",
        "taker",
        "session",
        "executor",
        "salt",
        "nonce",
        "affiliateAddress",
        "eip712Signature",
        "relayer",
    }.issubset(redaction_fields)
