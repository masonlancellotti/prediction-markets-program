import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError

import scan
import venues.sx_bet as sx_bet_module
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
    SX_BET_DEFAULT_USER_AGENT,
    SX_BET_RESEARCH_SCHEMA_KIND,
    SXBetReadOnlyClient,
    SXBetReadOnlyFetchError,
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


def test_sx_bet_research_snapshot_derives_event_title_from_team_names() -> None:
    snapshot = build_sx_bet_research_snapshot(
        {
            "markets": [
                {
                    "marketHash": "0xteamnames",
                    "teamOneName": "France",
                    "teamTwoName": "The Field",
                    "outcomeOneName": "France",
                    "outcomeTwoName": "The Field",
                }
            ],
            "orders": [],
        },
        captured_at=NOW,
    )

    market = snapshot["research_markets"][0]
    assert market["event_title"] == "France vs The Field"
    assert snapshot["is_executable"] is False
    assert snapshot["execution_allowed_in_project_now"] is False
    assert snapshot["can_create_candidate_pair"] is False
    assert snapshot["can_create_paper_candidate"] is False


def test_sx_bet_readonly_client_builds_non_executable_research_snapshot_without_auth() -> None:
    class FixtureClient(SXBetReadOnlyClient):
        def _fetch_active_markets(self, *, max_markets: int) -> dict:
            return {
                "status": "success",
                "data": {
                    "markets": [
                        {
                            "marketHash": "0xlivefixture",
                            "eventName": "Fixture Team A vs Fixture Team B",
                            "outcomeOneName": "Fixture Team A",
                            "outcomeTwoName": "Fixture Team B",
                            "maker": "must-not-persist",
                        }
                    ][:max_markets]
                },
            }

        def _fetch_orders(self, *, market_hashes: list[str]) -> dict:
            assert market_hashes == ["0xlivefixture"]
            return {
                "status": "success",
                "data": [
                    {
                        "marketHash": "0xlivefixture",
                        "isMakerBettingOutcomeOne": False,
                        "percentageOdds": "46000000000000000000",
                        "totalBetSize": "250000000",
                        "fillAmount": "0",
                        "signature": "must-not-persist",
                    }
                ],
            }

    snapshot = FixtureClient(timeout_seconds=1.0).fetch_research_snapshot(max_markets=1, captured_at=NOW)

    assert snapshot["schema_kind"] == SX_BET_RESEARCH_SCHEMA_KIND
    assert snapshot["source_id"] == "sx_bet"
    assert snapshot["live_fetch_attempted"] is True
    assert snapshot["live_fetch_succeeded"] is True
    assert snapshot["is_executable"] is False
    assert snapshot["execution_allowed_in_project_now"] is False
    assert snapshot["can_create_candidate_pair"] is False
    assert snapshot["can_create_paper_candidate"] is False
    assert "normalized_markets" not in snapshot
    assert snapshot["endpoint_metadata"]["auth_used"] is False
    assert snapshot["endpoint_metadata"]["wallet_or_signing_used"] is False
    assert snapshot["research_markets"][0]["raw"]["maker"] == "[REDACTED]"
    assert snapshot["research_markets"][0]["research_orderbook"]["outcome_one_taker_levels"][0]["raw"]["signature"] == "[REDACTED]"


def test_fetch_sx_bet_readonly_command_is_explicit_and_research_only(tmp_path: Path, monkeypatch, capsys) -> None:
    class FixtureClient:
        def __init__(self, *, timeout_seconds: float = 10.0) -> None:
            self.timeout_seconds = timeout_seconds

        def fetch_research_snapshot(self, *, max_markets: int, sport=None, league=None, query=None) -> dict:
            assert max_markets == 1
            assert sport is None
            assert league is None
            assert query is None
            snapshot = build_sx_bet_research_snapshot(
                {
                    "markets": [
                        {
                            "marketHash": "0xcli",
                            "eventName": "CLI Fixture A vs CLI Fixture B",
                            "outcomeOneName": "CLI Fixture A",
                            "outcomeTwoName": "CLI Fixture B",
                        }
                    ],
                    "orders": [],
                },
                captured_at=NOW,
            )
            snapshot["live_fetch_attempted"] = True
            snapshot["live_fetch_succeeded"] = True
            snapshot["unresolved_blockers"] = ["not_integrated_with_matcher_or_evaluator"]
            return snapshot

    output_path = tmp_path / "sx_bet_research_snapshot.json"
    monkeypatch.setattr(scan, "SXBetReadOnlyClient", FixtureClient)

    result = scan.main(["fetch-sx-bet-readonly", "--max-markets", "1", "--output", str(output_path)])

    stdout = capsys.readouterr().out
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert result == 0
    assert "sx_bet_readonly_fetch_status=OK" in stdout
    assert "PAPER_CANDIDATE" not in stdout
    assert "POSSIBLE_ARB" not in stdout
    assert "PAPER_CANDIDATE" not in json.dumps(payload)
    assert "POSSIBLE_ARB" not in json.dumps(payload)
    assert payload["schema_kind"] == SX_BET_RESEARCH_SCHEMA_KIND
    assert payload["is_executable"] is False
    assert payload["execution_allowed_in_project_now"] is False
    assert payload["can_create_candidate_pair"] is False
    assert payload["can_create_paper_candidate"] is False
    assert "normalized_markets" not in payload


def test_fetch_sx_bet_readonly_label_writes_labelled_snapshot_without_default_overwrite(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    class FixtureClient:
        def __init__(self, *, timeout_seconds: float = 10.0) -> None:
            self.timeout_seconds = timeout_seconds

        def fetch_research_snapshot(self, *, max_markets: int, sport=None, league=None, query=None) -> dict:
            assert sport == "baseball"
            assert league is None
            assert query == "Dodgers"
            snapshot = build_sx_bet_research_snapshot(
                {
                    "markets": [
                        {
                            "marketHash": "0xlabel",
                            "sportLabel": "Baseball",
                            "leagueLabel": "MLB",
                            "teamOneName": "Los Angeles Dodgers",
                            "teamTwoName": "San Diego Padres",
                            "outcomeOneName": "Los Angeles Dodgers",
                            "outcomeTwoName": "San Diego Padres",
                        }
                    ],
                    "orders": [],
                },
                captured_at=NOW,
            )
            snapshot["live_fetch_attempted"] = True
            snapshot["live_fetch_succeeded"] = True
            snapshot["targeting"] = {
                "targeting_method": "local",
                "sx_bet_fetched_count": 1,
                "sx_bet_retained_count": 1,
                "rejected_count_by_reason": {},
            }
            snapshot["sx_bet_fetched_count"] = 1
            snapshot["sx_bet_retained_count"] = 1
            return snapshot

    monkeypatch.setattr(scan, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(scan, "SXBetReadOnlyClient", FixtureClient)

    result = scan.main(
        [
            "fetch-sx-bet-readonly",
            "--max-markets",
            "1",
            "--sport",
            "baseball",
            "--query",
            "Dodgers",
            "--label",
            "mlb_test",
        ]
    )

    stdout = capsys.readouterr().out
    labelled_path = tmp_path / "reports" / "sx_bet" / "mlb_test" / "sx_bet_research_snapshot.json"
    default_path = tmp_path / "reports" / "sx_bet_research_snapshot.json"
    payload = json.loads(labelled_path.read_text(encoding="utf-8"))
    assert result == 0
    assert labelled_path.exists()
    assert not default_path.exists()
    assert "label=mlb_test" in stdout
    assert payload["targeting"]["label"] == "mlb_test"
    assert payload["targeting"]["targeting_method"] == "local"
    assert payload["is_executable"] is False
    assert payload["can_create_candidate_pair"] is False
    assert payload["can_create_paper_candidate"] is False


def test_sx_bet_readonly_local_filter_keeps_same_sport_league_and_rejects_mismatches() -> None:
    class FixtureClient(SXBetReadOnlyClient):
        def _fetch_active_markets(self, *, max_markets: int) -> dict:
            return {
                "status": "success",
                "data": {
                    "markets": [
                        {
                            "marketHash": "0xmlb",
                            "sportLabel": "Baseball",
                            "leagueLabel": "MLB",
                            "teamOneName": "Los Angeles Dodgers",
                            "teamTwoName": "San Diego Padres",
                            "outcomeOneName": "Los Angeles Dodgers",
                            "outcomeTwoName": "San Diego Padres",
                        },
                        {
                            "marketHash": "0xnfl",
                            "sportLabel": "Football",
                            "leagueLabel": "NFL",
                            "teamOneName": "Jacksonville Jaguars",
                            "teamTwoName": "Cleveland Browns",
                            "outcomeOneName": "Jacksonville Jaguars",
                            "outcomeTwoName": "Cleveland Browns",
                        },
                    ]
                },
            }

        def _fetch_orders(self, *, market_hashes: list[str]) -> dict:
            assert market_hashes == ["0xmlb"]
            return {"status": "success", "data": []}

    snapshot = FixtureClient(timeout_seconds=1.0).fetch_research_snapshot(
        max_markets=2,
        sport="baseball",
        league="MLB",
        captured_at=NOW,
    )

    assert snapshot["research_market_count"] == 1
    assert snapshot["research_markets"][0]["market_hash"] == "0xmlb"
    assert snapshot["targeting"]["targeting_method"] == "local"
    assert snapshot["targeting"]["api_side_filtering_used"] is False
    assert snapshot["targeting"]["sx_bet_fetched_count"] == 2
    assert snapshot["targeting"]["sx_bet_retained_count"] == 1
    assert snapshot["targeting"]["retained_sport_counts"] == {"Baseball": 1}
    assert snapshot["targeting"]["retained_league_counts"] == {"MLB": 1}
    assert snapshot["targeting"]["rejected_count_by_reason"] == {"sport_mismatch": 1, "league_mismatch": 1}
    assert snapshot["targeting"]["sample_retained_events"] == [
        {
            "market_hash": "0xmlb",
            "event_title": "Los Angeles Dodgers vs San Diego Padres",
            "sport": "Baseball",
            "league": "MLB",
            "outcome_one_name": "Los Angeles Dodgers",
            "outcome_two_name": "San Diego Padres",
            "rejection_reasons": [],
        }
    ]
    assert snapshot["targeting"]["sample_rejected_events"] == [
        {
            "market_hash": "0xnfl",
            "event_title": "Jacksonville Jaguars vs Cleveland Browns",
            "sport": "Football",
            "league": "NFL",
            "outcome_one_name": "Jacksonville Jaguars",
            "outcome_two_name": "Cleveland Browns",
            "rejection_reasons": ["sport_mismatch", "league_mismatch"],
        }
    ]
    assert snapshot["is_executable"] is False
    assert snapshot["can_create_candidate_pair"] is False
    assert snapshot["can_create_paper_candidate"] is False


def test_sx_bet_http_403_is_classified_as_readonly_fetch_blocked(monkeypatch) -> None:
    def blocked_urlopen(*args, **kwargs):
        request = args[0]
        assert request.get_header("User-agent") == SX_BET_DEFAULT_USER_AGENT
        assert request.get_header("Accept") == "application/json"
        raise HTTPError(
            url="https://api.sx.bet/markets/active",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(sx_bet_module, "urlopen", blocked_urlopen)

    try:
        SXBetReadOnlyClient(timeout_seconds=1.0).fetch_research_snapshot(max_markets=1, captured_at=NOW)
    except SXBetReadOnlyFetchError as exc:
        assert exc.error_category == "READ_ONLY_FETCH_BLOCKED"
        assert "HTTP 403" in str(exc)
    else:
        raise AssertionError("expected SXBetReadOnlyFetchError")


def test_sx_bet_readonly_request_uses_honest_non_browser_headers(monkeypatch) -> None:
    captured_requests = []

    class FixtureResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self) -> bytes:
            return b'{"status":"success","data":{"markets":[]}}'

    def fake_urlopen(request, timeout):
        captured_requests.append(request)
        return FixtureResponse()

    monkeypatch.setattr(sx_bet_module, "urlopen", fake_urlopen)

    SXBetReadOnlyClient(timeout_seconds=1.0)._fetch_active_markets(max_markets=1)

    assert len(captured_requests) == 1
    request = captured_requests[0]
    user_agent = request.get_header("User-agent")
    assert user_agent == SX_BET_DEFAULT_USER_AGENT
    assert request.get_header("Accept") == "application/json"
    for browser_token in ("Mozilla", "Chrome", "Safari", "Firefox", "Edge"):
        assert browser_token not in user_agent


def test_fetch_sx_bet_readonly_writes_safe_failure_snapshot_on_403(tmp_path: Path, monkeypatch, capsys) -> None:
    class BlockedClient:
        def __init__(self, *, timeout_seconds: float = 10.0) -> None:
            self.timeout_seconds = timeout_seconds

        def fetch_research_snapshot(self, *, max_markets: int, sport=None, league=None, query=None) -> dict:
            raise SXBetReadOnlyFetchError(
                "SX Bet public read-only fetch blocked with HTTP 403 for /markets/active",
                error_category="READ_ONLY_FETCH_BLOCKED",
            )

    output_path = tmp_path / "sx_bet_failure_snapshot.json"
    monkeypatch.setattr(scan, "SXBetReadOnlyClient", BlockedClient)

    result = scan.main(["fetch-sx-bet-readonly", "--max-markets", "1", "--output", str(output_path)])

    stdout = capsys.readouterr().out
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert result == 1
    assert "sx_bet_readonly_fetch_status=FAILED" in stdout
    assert "error_category=READ_ONLY_FETCH_BLOCKED" in stdout
    assert payload["source_id"] == "sx_bet"
    assert payload["live_fetch_attempted"] is True
    assert payload["live_fetch_succeeded"] is False
    assert payload["error_category"] == "READ_ONLY_FETCH_BLOCKED"
    assert payload["is_executable"] is False
    assert payload["can_create_candidate_pair"] is False
    assert payload["can_create_paper_candidate"] is False
    serialized = json.dumps(payload)
    assert "PAPER_CANDIDATE" not in serialized
    assert "POSSIBLE_ARB" not in serialized


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


def test_compare_sx_bet_reference_is_saved_file_only_and_diagnostic(tmp_path: Path, monkeypatch, capsys) -> None:
    def fail_if_fetch_is_attempted(*args, **kwargs):
        raise AssertionError("compare-sx-bet-reference must not fetch live data")

    monkeypatch.setattr(scan, "SXBetReadOnlyClient", fail_if_fetch_is_attempted)
    sx_snapshot = build_sx_bet_research_snapshot(
        {
            "markets": [
                {
                    "marketHash": "0xcompare",
                    "teamOneName": "Boston Celtics",
                    "teamTwoName": "New York Knicks",
                    "leagueLabel": "NBA",
                    "sportLabel": "Basketball",
                    "type": "moneyline",
                    "gameTime": "2026-05-21T23:00:00Z",
                    "outcomeOneName": "Boston Celtics",
                    "outcomeTwoName": "New York Knicks",
                }
            ],
            "orders": [
                {
                    "marketHash": "0xcompare",
                    "isMakerBettingOutcomeOne": False,
                    "percentageOdds": "46000000000000000000",
                    "totalBetSize": "250000000",
                    "fillAmount": "0",
                }
            ],
        },
        captured_at=NOW,
    )
    kalshi_snapshot = {
        "schema_version": 1,
        "source_id": "kalshi",
        "captured_at": NOW.isoformat(),
        "normalized_markets": [
            {
                "venue": "kalshi",
                "ticker": "KXNBA-CELTICS-KNICKS",
                "market_id": "KXNBA-CELTICS-KNICKS",
                "question": "Will the Boston Celtics beat the New York Knicks?",
                "event_title": "Boston Celtics vs New York Knicks",
                "close_time": "2026-05-21T23:00:00+00:00",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
            }
        ],
    }
    polymarket_snapshot = {
        "schema_version": 1,
        "source_id": "polymarket",
        "captured_at": NOW.isoformat(),
        "normalized_markets": [
            {
                "venue": "polymarket",
                "market_id": "pm-1",
                "question": "Will a different team win?",
                "event_title": "Different fixture",
                "end_date": "2026-05-21T23:00:00+00:00",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
            }
        ],
    }
    sx_path = tmp_path / "sx.json"
    kalshi_path = tmp_path / "kalshi.json"
    polymarket_path = tmp_path / "polymarket.json"
    json_output = tmp_path / "sx_reference.json"
    markdown_output = tmp_path / "sx_reference.md"
    sx_path.write_text(json.dumps(sx_snapshot), encoding="utf-8")
    kalshi_path.write_text(json.dumps(kalshi_snapshot), encoding="utf-8")
    polymarket_path.write_text(json.dumps(polymarket_snapshot), encoding="utf-8")

    result = scan.main(
        [
            "compare-sx-bet-reference",
            "--sx-bet-snapshot",
            str(sx_path),
            "--kalshi-snapshot",
            str(kalshi_path),
            "--polymarket-snapshot",
            str(polymarket_path),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )

    stdout = capsys.readouterr().out
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    markdown = markdown_output.read_text(encoding="utf-8")
    assert result == 0
    assert "live_fetch_attempted=false" in stdout
    assert payload["diagnostic_only"] is True
    assert payload["is_reference_only"] is True
    assert payload["same_payoff_asserted"] is False
    assert payload["can_create_candidate_pair"] is False
    assert payload["can_create_paper_candidate"] is False
    assert payload["readiness_promotion"] == "none"
    assert payload["source_rows"]["sx_bet"]["status"] == "OK"
    assert payload["source_rows"]["kalshi"]["status"] == "OK"
    assert payload["summary"]["sx_bet_markets_inspected"] == 1
    assert payload["summary"]["sx_bet_event_title_coverage_ratio"] == 1.0
    assert payload["summary"]["kalshi_records_inspected"] == 1
    assert payload["summary"]["structured_pairs_considered"] == 2
    assert payload["summary"]["structured_pairs_rejected"] == 0
    assert payload["summary"]["top_similarity_after_structured_filter"] == payload["summary"]["top_similarity"]
    assert payload["top_overlap_candidates"]
    assert len(payload["top_overlap_candidates"]) == 1
    top = payload["top_overlap_candidates"][0]
    assert top["diagnostic_only"] is True
    assert top["is_reference_only"] is True
    assert top["same_payoff_asserted"] is False
    assert top["sx_bet_market"]["event_title"] == "Boston Celtics vs New York Knicks"
    assert top["sx_bet_research_orderbook"]["depth_units_not_normalized"] is True
    assert top["sx_bet_research_orderbook"]["maker_stake_usdc_at_best_outcome_one"] == 250.0
    serialized = json.dumps(payload) + markdown + stdout
    assert "PAPER_CANDIDATE" not in serialized
    assert "POSSIBLE_ARB" not in serialized


def test_sx_bet_reference_rejects_sport_league_mismatch_before_similarity() -> None:
    sx_market = {
        "market_hash": "0xnfl",
        "event_title": "Jacksonville Jaguars vs Cleveland Browns",
        "league": "NFL",
        "sport": "Football",
        "outcome_one_name": "Jacksonville Jaguars",
        "outcome_two_name": "Cleveland Browns",
        "research_orderbook": {},
    }
    executable_markets = [
        {
            "source_id": "polymarket",
            "market": {
                "market_id": "pm-mlb",
                "question": "Cleveland Guardians vs Philadelphia Phillies: O/U 5.5",
                "event_title": "Cleveland Guardians vs Philadelphia Phillies",
                "raw": {"description": "MLB baseball total market"},
            },
        }
    ]

    comparison_payload = scan._sx_bet_reference_comparison_payload([sx_market], executable_markets, top_limit=5)
    comparisons = comparison_payload["top_overlap_candidates"]

    assert comparisons == []
    assert comparison_payload["structured_pairs_considered"] == 1
    assert comparison_payload["structured_pairs_rejected"] == 1
    assert comparison_payload["sport_or_league_mismatch_rejections"] == 1
    rejection = comparison_payload["structured_rejections_sample"][0]
    assert rejection["structured_compatibility"]["sport_league_compatibility"]["sx_bet_sport_league_key"] == "nfl"
    assert rejection["structured_compatibility"]["sport_league_compatibility"]["executable_sport_league_key"] == "mlb"
    assert rejection["structured_compatibility"]["hard_rejected"] is True
    assert "sport_mismatch" in rejection["blockers"]
    assert "league_mismatch" in rejection["blockers"]
    serialized = json.dumps(comparison_payload)
    assert "PAPER_CANDIDATE" not in serialized
    assert "POSSIBLE_ARB" not in serialized


def test_sx_bet_reference_same_sport_league_survives_to_similarity_ranking() -> None:
    sx_market = {
        "market_hash": "0xnba",
        "event_title": "Boston Celtics vs New York Knicks",
        "league": "NBA",
        "sport": "Basketball",
        "market_type": "moneyline",
        "outcome_one_name": "Boston Celtics",
        "outcome_two_name": "New York Knicks",
        "research_orderbook": {},
    }
    executable_markets = [
        {
            "source_id": "kalshi",
            "market": {
                "market_id": "kx-nba",
                "ticker": "KXNBA-CELTICS-KNICKS",
                "question": "Will the Boston Celtics beat the New York Knicks?",
                "event_title": "Boston Celtics vs New York Knicks",
                "raw": {"rules_primary": "NBA basketball moneyline market"},
            },
        }
    ]

    comparisons = scan._sx_bet_reference_comparisons([sx_market], executable_markets, top_limit=5)

    assert len(comparisons) == 1
    assert comparisons[0]["structured_compatibility"]["hard_rejected"] is False
    assert comparisons[0]["structured_compatibility"]["sport_league_compatibility"]["compatible"] is True
    assert comparisons[0]["similarity_score"] > 0


def test_sx_bet_reference_top_candidates_dedupe_by_market_hash() -> None:
    sx_market = {
        "market_hash": "0xdupe",
        "event_title": "Boston Celtics vs New York Knicks",
        "league": "NBA",
        "sport": "Basketball",
        "outcome_one_name": "Boston Celtics",
        "outcome_two_name": "New York Knicks",
        "research_orderbook": {},
    }
    executable_markets = [
        {
            "source_id": "polymarket",
            "market": {
                "market_id": "pm-1",
                "question": "Boston Celtics vs New York Knicks: O/U 220.5",
                "event_title": "Boston Celtics vs New York Knicks",
                "raw": {"description": "NBA basketball total market"},
            },
        },
        {
            "source_id": "polymarket",
            "market": {
                "market_id": "pm-2",
                "question": "Boston Celtics vs New York Knicks spread",
                "event_title": "Boston Celtics vs New York Knicks",
                "raw": {"description": "NBA basketball spread market"},
            },
        },
    ]

    comparisons = scan._sx_bet_reference_comparisons([sx_market], executable_markets, top_limit=20)

    assert len(comparisons) == 1
    assert comparisons[0]["sx_bet_market"]["market_hash"] == "0xdupe"


def test_compare_sx_bet_reference_reports_missing_inputs_cleanly(tmp_path: Path) -> None:
    json_output = tmp_path / "missing_sx_reference.json"
    markdown_output = tmp_path / "missing_sx_reference.md"

    result = scan.main(
        [
            "compare-sx-bet-reference",
            "--sx-bet-snapshot",
            str(tmp_path / "missing_sx.json"),
            "--kalshi-snapshot",
            str(tmp_path / "missing_kalshi.json"),
            "--polymarket-snapshot",
            str(tmp_path / "missing_polymarket.json"),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )

    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert result == 0
    assert payload["source_rows"]["sx_bet"]["status"] == "NOT_FOUND"
    assert payload["source_rows"]["kalshi"]["status"] == "NOT_FOUND"
    assert payload["source_rows"]["polymarket"]["status"] == "NOT_FOUND"
    assert payload["top_overlap_candidates"] == []
    assert payload["same_payoff_asserted"] is False
    assert payload["can_create_candidate_pair"] is False
    assert payload["can_create_paper_candidate"] is False


def test_compare_sx_bet_reference_reports_asymmetric_executable_universe(
    tmp_path: Path,
    capsys,
) -> None:
    sx_snapshot = build_sx_bet_research_snapshot(
        {
            "markets": [
                {
                    "marketHash": "0xasym",
                    "teamOneName": "Boston Celtics",
                    "teamTwoName": "New York Knicks",
                    "leagueLabel": "NBA",
                    "sportLabel": "Basketball",
                }
            ],
            "orders": [],
        },
        captured_at=NOW,
    )
    kalshi_snapshot = {"schema_version": 1, "source_id": "kalshi", "normalized_markets": []}
    polymarket_snapshot = {
        "schema_version": 1,
        "source_id": "polymarket",
        "normalized_markets": [
            {
                "venue": "polymarket",
                "market_id": "pm-nba",
                "question": "Will Boston beat New York?",
                "event_title": "Boston Celtics vs New York Knicks",
                "raw": {"description": "NBA basketball moneyline market"},
            }
        ],
    }
    sx_path = tmp_path / "sx.json"
    kalshi_path = tmp_path / "kalshi_empty.json"
    polymarket_path = tmp_path / "polymarket.json"
    json_output = tmp_path / "sx_reference.json"
    markdown_output = tmp_path / "sx_reference.md"
    sx_path.write_text(json.dumps(sx_snapshot), encoding="utf-8")
    kalshi_path.write_text(json.dumps(kalshi_snapshot), encoding="utf-8")
    polymarket_path.write_text(json.dumps(polymarket_snapshot), encoding="utf-8")

    result = scan.main(
        [
            "compare-sx-bet-reference",
            "--sx-bet-snapshot",
            str(sx_path),
            "--kalshi-snapshot",
            str(kalshi_path),
            "--polymarket-snapshot",
            str(polymarket_path),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )

    stdout = capsys.readouterr().out
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    markdown = markdown_output.read_text(encoding="utf-8")
    assert result == 0
    assert payload["source_rows"]["kalshi"]["status"] == "MISSING_NORMALIZED_MARKETS"
    assert payload["summary"]["asymmetric_universe_warning"] == "ASYMMETRIC_EXECUTABLE_UNIVERSE"
    assert payload["summary"]["warnings"]
    assert "kalshi status=MISSING_NORMALIZED_MARKETS records=0" in payload["summary"]["warnings"][0]
    assert "asymmetric_universe_warning=ASYMMETRIC_EXECUTABLE_UNIVERSE" in stdout
    assert "## Warnings" in markdown
    assert "ASYMMETRIC_EXECUTABLE_UNIVERSE" in markdown
    serialized = json.dumps(payload) + markdown + stdout
    assert "PAPER_CANDIDATE" not in serialized
    assert "POSSIBLE_ARB" not in serialized


def test_compare_sx_bet_reference_label_writes_labelled_reports_with_explicit_snapshots(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(scan, "PROJECT_ROOT", tmp_path)
    sx_snapshot = build_sx_bet_research_snapshot(
        {
            "markets": [
                {
                    "marketHash": "0xlabelcompare",
                    "sportLabel": "Baseball",
                    "leagueLabel": "MLB",
                    "teamOneName": "Los Angeles Dodgers",
                    "teamTwoName": "San Diego Padres",
                    "outcomeOneName": "Los Angeles Dodgers",
                    "outcomeTwoName": "San Diego Padres",
                }
            ],
            "orders": [],
        },
        captured_at=NOW,
    )
    kalshi_snapshot = {
        "schema_version": 1,
        "source_id": "kalshi",
        "normalized_markets": [
            {
                "venue": "kalshi",
                "ticker": "KXMLB-LAD-SD",
                "market_id": "KXMLB-LAD-SD",
                "question": "Will Los Angeles Dodgers beat San Diego Padres?",
                "event_title": "Los Angeles Dodgers vs San Diego Padres",
                "raw": {"rules_primary": "MLB baseball market"},
            }
        ],
    }
    polymarket_snapshot = {"schema_version": 1, "source_id": "polymarket", "normalized_markets": []}
    sx_path = tmp_path / "input_sx.json"
    kalshi_path = tmp_path / "input_kalshi.json"
    polymarket_path = tmp_path / "input_polymarket.json"
    sx_path.write_text(json.dumps(sx_snapshot), encoding="utf-8")
    kalshi_path.write_text(json.dumps(kalshi_snapshot), encoding="utf-8")
    polymarket_path.write_text(json.dumps(polymarket_snapshot), encoding="utf-8")

    result = scan.main(
        [
            "compare-sx-bet-reference",
            "--sx-bet-snapshot",
            str(sx_path),
            "--kalshi-snapshot",
            str(kalshi_path),
            "--polymarket-snapshot",
            str(polymarket_path),
            "--label",
            "mlb_compare",
        ]
    )

    labelled_json = tmp_path / "reports" / "sx_bet_reference" / "mlb_compare" / "sx_bet_reference_context.json"
    labelled_md = tmp_path / "reports" / "sx_bet_reference" / "mlb_compare" / "sx_bet_reference_context.md"
    default_json = tmp_path / "reports" / "sx_bet_reference_context.json"
    payload = json.loads(labelled_json.read_text(encoding="utf-8"))
    assert result == 0
    assert labelled_json.exists()
    assert labelled_md.exists()
    assert not default_json.exists()
    assert payload["source_rows"]["sx_bet"]["path"] == str(sx_path)
    assert payload["source_rows"]["kalshi"]["path"] == str(kalshi_path)
    assert payload["is_reference_only"] is True
    assert payload["same_payoff_asserted"] is False
    assert payload["can_create_candidate_pair"] is False
    assert payload["can_create_paper_candidate"] is False


def test_default_scan_output_remains_unchanged(capsys) -> None:
    result = scan.main([])

    assert result == 0
    assert "relative_value_scan_status=OFFLINE_COMPLETE candidates=7 possible_arbs=0" in capsys.readouterr().out


def test_sx_bet_live_read_only_boundary_is_inert_and_non_candidate_enabled() -> None:
    report = sx_bet_live_read_only_boundary_report()

    assert report["status"] == "live_readonly_fetch_succeeded_research_only"
    assert report["execution_allowed_in_project_now"] is False
    assert report["can_create_candidate_pair"] is False
    assert report["can_create_paper_candidate"] is False
    assert report["raw_redaction_policy"]["allow_raw_network_echo"] is False
    assert "markets" in {row["name"] for row in report["endpoint_categories"]}
    assert "active_orders" in {row["name"] for row in report["endpoint_categories"]}
    assert any(row["forbidden_execution_surface"] is True for row in report["endpoint_categories"])
    assert all(row["allowed"] is False for row in report["stages"] if row["stage"] > 0)
    assert any(row["name"] == "live_read_only_raw_fetcher_implemented_research_only" for row in report["stages"])
    assert any(row["name"] == "normalized_snapshot_manual_review_only" for row in report["stages"])
    assert all(row["name"] != "candidate_normalized_snapshot_manual_review_only" for row in report["stages"])
    assert report["rate_limit_and_retry_policy"]["live_fetcher_implemented"] is True
    assert report["rate_limit_and_retry_policy"]["current_live_fetch_status"] == "READ_ONLY_FETCH_SUCCEEDED_RESEARCH_ONLY"


def test_sx_bet_boundary_introduces_no_live_transport_dependencies() -> None:
    forbidden_modules = {
        "aiohttp",
        "cloudscraper",
        "eth_account",
        "httpx",
        "playwright",
        "pyppeteer",
        "requests",
        "selenium",
        "undetected_chromedriver",
        "web3",
        "websocket",
        "websockets",
    }

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
