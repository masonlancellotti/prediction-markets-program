import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

import scan
from relative_value.live_snapshot_matcher import load_reference_snapshot, match_snapshot_files
from relative_value.models import Action, NormalizedMarket, SourceKind
from relative_value.scoring import score_pair
from venues.the_odds_api import (
    TheOddsApiReadOnlyClient,
    build_the_odds_api_reference_snapshot,
)


class _FakeResponse:
    def __init__(self, payload: str) -> None:
        self.payload = payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def _sample_response() -> list[dict]:
    return [
        {
            "id": "event-1",
            "sport_key": "basketball_nba",
            "sport_title": "NBA",
            "commence_time": "2026-05-21T23:00:00Z",
            "home_team": "Boston Celtics",
            "away_team": "New York Knicks",
            "bookmakers": [
                {
                    "key": "draftkings",
                    "title": "DraftKings",
                    "last_update": "2026-05-21T12:00:00Z",
                    "markets": [
                        {
                            "key": "h2h",
                            "last_update": "2026-05-21T12:00:00Z",
                            "outcomes": [
                                {"name": "Boston Celtics", "price": -120},
                                {"name": "New York Knicks", "price": 110},
                            ],
                        },
                        {
                            "key": "totals",
                            "last_update": "2026-05-21T12:00:00Z",
                            "outcomes": [
                                {"name": "Over", "price": -105, "point": 215.5},
                                {"name": "Under", "price": -115, "point": 215.5},
                            ],
                        },
                    ],
                }
            ],
        }
    ]


def _executable_polymarket_snapshot() -> dict:
    return {
        "schema_version": 1,
        "source": "polymarket_gamma",
        "captured_at": "2026-05-21T12:00:00+00:00",
        "normalized_markets": [
            {
                "venue": "polymarket",
                "market_id": "poly-celtics",
                "question": "Will the Boston Celtics beat the New York Knicks?",
                "event_title": "New York Knicks at Boston Celtics",
                "end_date": "2026-05-21T23:00:00+00:00",
                "active": True,
                "closed": False,
                "liquidity": 100.0,
                "raw": {},
            }
        ],
    }


def _executable_kalshi_snapshot() -> dict:
    return {
        "schema_version": 1,
        "source": "kalshi_markets",
        "captured_at": "2026-05-21T12:00:00+00:00",
        "normalized_markets": [
            {
                "venue": "kalshi",
                "ticker": "KXNBA-CELTICS",
                "question": "Will the Boston Celtics beat the New York Knicks?",
                "event_title": "New York Knicks at Boston Celtics",
                "close_time": "2026-05-21T23:00:00+00:00",
                "active": True,
                "closed": False,
                "status": "active",
                "liquidity": 100.0,
                "raw": {},
            }
        ],
    }


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_successful_response_normalizes_reference_records() -> None:
    snapshot = build_the_odds_api_reference_snapshot(
        _sample_response(),
        sport_key="basketball_nba",
        regions="us",
        markets="h2h,totals",
        odds_format="american",
        retrieved_at=datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc),
        stale_after_seconds=900,
    )

    assert snapshot["schema_version"] == 1
    assert snapshot["schema_kind"] == "reference_snapshot_v1"
    assert snapshot["source_id"] == "the_odds_api"
    assert snapshot["source_type"] == "REFERENCE_ONLY"
    assert snapshot["permission"] == "REFERENCE_ONLY"
    assert snapshot["record_count"] == 4
    assert snapshot["normalized_count"] == 4
    assert snapshot["skipped_count"] == 0
    row = snapshot["normalized_records"][0]
    assert row["event_title"] == "New York Knicks at Boston Celtics"
    assert row["bookmaker"] == "DraftKings"
    assert row["market_type"] == "h2h"
    assert row["odds_format"] == "american"
    assert row["american_odds"] == -120.0
    assert row["implied_probability"] == pytest.approx(0.545455)
    assert row["no_vig_probability"] is not None
    assert row["source_type"] == "REFERENCE_ONLY"
    assert row["permission"] == "REFERENCE_ONLY"
    assert row["is_executable"] is False
    assert row["usable_for_trade_decision"] is False


def test_malformed_event_is_skipped_safely() -> None:
    snapshot = build_the_odds_api_reference_snapshot(
        [{"id": "bad-event", "bookmakers": "not-a-list"}],
        sport_key="basketball_nba",
        regions="us",
        markets="h2h",
        odds_format="american",
        retrieved_at=datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc),
    )

    assert snapshot["normalized_count"] == 0
    assert snapshot["skipped_count"] == 1


def test_non_list_response_has_clear_error() -> None:
    with pytest.raises(ValueError, match="The Odds API response must be a list"):
        build_the_odds_api_reference_snapshot(
            {"message": "bad shape"},
            sport_key="basketball_nba",
            regions="us",
            markets="h2h",
            odds_format="american",
        )


def test_fetch_odds_url_contract_and_user_agent(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["user_agent"] = request.get_header("User-agent")
        captured["timeout"] = timeout
        return _FakeResponse(json.dumps(_sample_response()))

    monkeypatch.setattr("venues.the_odds_api.urlopen", fake_urlopen)

    result = TheOddsApiReadOnlyClient(
        api_key="test-key",
        base_url="https://example.test/v4",
        timeout_seconds=4.0,
    ).fetch_odds(sport_key="basketball_nba", regions="us", markets="h2h", odds_format="american")

    query = parse_qs(urlparse(captured["url"]).query)
    assert result == _sample_response()
    assert captured["url"].startswith("https://example.test/v4/sports/basketball_nba/odds?")
    assert captured["method"] == "GET"
    assert captured["user_agent"] == "relative-value-scanner/0.1 read-only"
    assert captured["timeout"] == 4.0
    assert query["apiKey"] == ["test-key"]
    assert query["regions"] == ["us"]
    assert query["markets"] == ["h2h"]
    assert query["oddsFormat"] == ["american"]


def test_fetch_the_odds_api_cli_missing_key_returns_clean_failure(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)

    result = scan.main(
        [
            "fetch-the-odds-api",
            "--sport-key",
            "basketball_nba",
            "--output",
            str(tmp_path / "odds.json"),
        ]
    )

    assert result == 1
    assert "the_odds_api_fetch_status=FAILED message=missing API key" in capsys.readouterr().out


def test_fetch_the_odds_api_cli_uses_client_without_network(monkeypatch, tmp_path: Path, capsys) -> None:
    output = tmp_path / "odds.json"

    class FakeClient:
        def __init__(self, api_key: str, timeout_seconds: float) -> None:
            assert api_key == "test-key"
            assert timeout_seconds == 3.0

        def fetch_reference_snapshot(
            self,
            *,
            sport_key: str,
            regions: str,
            markets: str,
            odds_format: str,
            stale_after_seconds: int,
        ) -> dict:
            assert sport_key == "basketball_nba"
            assert regions == "us"
            assert markets == "h2h"
            assert odds_format == "american"
            assert stale_after_seconds == 600
            return build_the_odds_api_reference_snapshot(
                _sample_response(),
                sport_key=sport_key,
                regions=regions,
                markets=markets,
                odds_format=odds_format,
                retrieved_at=datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc),
                stale_after_seconds=stale_after_seconds,
            )

    monkeypatch.setattr(scan, "TheOddsApiReadOnlyClient", FakeClient)

    result = scan.main(
        [
            "fetch-the-odds-api",
            "--sport-key",
            "basketball_nba",
            "--markets",
            "h2h",
            "--api-key",
            "test-key",
            "--timeout-seconds",
            "3",
            "--stale-after-seconds",
            "600",
            "--output",
            str(output),
        ]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert result == 0
    assert payload["schema_kind"] == "reference_snapshot_v1"
    assert payload["source_type"] == "REFERENCE_ONLY"
    assert payload["normalized_count"] == 4
    assert "PAPER_CANDIDATE" not in json.dumps(payload)
    assert "POSSIBLE_ARB" not in json.dumps(payload)
    assert "the_odds_api_fetch_status=OK record_count=4 normalized=4 skipped=0" in capsys.readouterr().out


def test_sportsbook_reference_row_cannot_promote_to_paper_or_possible_arb() -> None:
    exchange = NormalizedMarket(
        venue="kalshi",
        market_id="kalshi-1",
        event_name="Boston Celtics vs New York Knicks",
        outcome_name="Boston Celtics",
        source_kind=SourceKind.EXCHANGE,
        yes_bid=0.10,
        yes_ask=0.12,
        liquidity_top_contracts=100.0,
        is_executable=True,
    )
    sportsbook = NormalizedMarket(
        venue="draftkings",
        market_id="event-1:Boston Celtics",
        event_name="Boston Celtics vs New York Knicks",
        outcome_name="Boston Celtics",
        source_kind=SourceKind.SPORTSBOOK_REFERENCE,
        yes_reference_probability=0.90,
        is_executable=False,
    )

    candidate = score_pair(exchange, sportsbook)

    assert candidate.action not in {Action.PAPER, Action.POSSIBLE_ARB}
    assert "sportsbook odds are reference-only" in candidate.reasons


def test_reference_snapshot_fails_closed_in_live_snapshot_matcher_path(tmp_path: Path) -> None:
    reference_snapshot = build_the_odds_api_reference_snapshot(
        _sample_response(),
        sport_key="basketball_nba",
        regions="us",
        markets="h2h",
        odds_format="american",
        retrieved_at=datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc),
    )
    executable_snapshot = {
        "schema_version": 1,
        "source": "kalshi",
        "captured_at": "2026-05-21T12:00:00+00:00",
        "normalized_markets": [
            {
                "ticker": "KXNBA-26-BOS",
                "title": "Will Boston win?",
                "event_title": "Boston Celtics vs New York Knicks",
                "close_time": "2026-05-21T23:00:00+00:00",
                "end_date": "2026-05-21T23:00:00+00:00",
                "status": "active",
            }
        ],
    }
    reference_path = tmp_path / "the_odds_api_reference_snapshot.json"
    executable_path = tmp_path / "kalshi_snapshot.json"
    output_path = tmp_path / "pairs.json"
    reference_path.write_text(json.dumps(reference_snapshot), encoding="utf-8")
    executable_path.write_text(json.dumps(executable_snapshot), encoding="utf-8")

    result = match_snapshot_files(reference_path, executable_path, output_path)

    assert result["pair_count"] == 0
    assert result["pairs"] == []
    assert "unsupported_schema_kind" in result["snapshot_issues"]["polymarket"]
    assert "missing_normalized_markets" in result["snapshot_issues"]["polymarket"]


def test_reference_snapshot_loads_through_reference_context_path(tmp_path: Path) -> None:
    snapshot = build_the_odds_api_reference_snapshot(
        _sample_response(),
        sport_key="basketball_nba",
        regions="us",
        markets="h2h",
        odds_format="american",
        retrieved_at=datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc),
    )
    path = _write_json(tmp_path / "reference.json", snapshot)

    loaded = load_reference_snapshot(path)

    assert loaded.issues == ()
    assert loaded.payload["schema_kind"] == "reference_snapshot_v1"
    assert loaded.payload["source_type"] == "REFERENCE_ONLY"


def test_reference_snapshot_context_does_not_change_pair_counts_or_actions(tmp_path: Path) -> None:
    poly_path = _write_json(tmp_path / "polymarket.json", _executable_polymarket_snapshot())
    kalshi_path = _write_json(tmp_path / "kalshi.json", _executable_kalshi_snapshot())
    reference = build_the_odds_api_reference_snapshot(
        _sample_response(),
        sport_key="basketball_nba",
        regions="us",
        markets="h2h",
        odds_format="american",
        retrieved_at=datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc),
    )
    reference_path = _write_json(tmp_path / "reference.json", reference)
    now = datetime(2026, 5, 21, 12, 5, tzinfo=timezone.utc)

    baseline = match_snapshot_files(poly_path, kalshi_path, now=now)
    with_reference = match_snapshot_files(poly_path, kalshi_path, now=now, reference_snapshot_paths=[reference_path])

    assert baseline["pair_count"] == 1
    assert with_reference["pair_count"] == baseline["pair_count"]
    assert [pair["action"] for pair in with_reference["pairs"]] == [pair["action"] for pair in baseline["pairs"]]
    assert with_reference["reference_context"]["snapshot_count"] == 1
    assert with_reference["reference_context"]["valid_record_count"] == 4
    assert "PAPER_CANDIDATE" not in json.dumps(with_reference)
    assert "POSSIBLE_ARB" not in json.dumps(with_reference)


def test_stale_and_malformed_reference_rows_are_diagnostics_only(tmp_path: Path) -> None:
    poly_path = _write_json(tmp_path / "polymarket.json", _executable_polymarket_snapshot())
    kalshi_path = _write_json(tmp_path / "kalshi.json", _executable_kalshi_snapshot())
    reference = build_the_odds_api_reference_snapshot(
        _sample_response(),
        sport_key="basketball_nba",
        regions="us",
        markets="h2h",
        odds_format="american",
        retrieved_at=datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc),
        stale_after_seconds=60,
    )
    reference["normalized_records"].append({"source_type": "REFERENCE_ONLY"})
    reference_path = _write_json(tmp_path / "reference.json", reference)

    result = match_snapshot_files(
        poly_path,
        kalshi_path,
        now=datetime(2026, 5, 21, 12, 5, tzinfo=timezone.utc),
        reference_snapshot_paths=[reference_path],
    )

    assert result["pair_count"] == 1
    assert result["pairs"][0]["action"] == "MANUAL_REVIEW"
    assert result["pairs"][0]["ineligibility_reasons"] == []
    assert "stale_reference_record" in result["reference_context"]["diagnostics"]
    assert "malformed_reference_record" in result["reference_context"]["diagnostics"]
    assert result["reference_context"]["stale_record_count"] == 4
    assert result["reference_context"]["malformed_record_count"] == 1


def test_default_scan_output_remains_offline_fixture_scan(capsys) -> None:
    result = scan.main([])

    assert result == 0
    output = capsys.readouterr().out
    assert "relative_value_scan_status=OFFLINE_COMPLETE candidates=7 possible_arbs=0" in output
