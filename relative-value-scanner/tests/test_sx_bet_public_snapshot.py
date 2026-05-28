from __future__ import annotations

import json
from pathlib import Path

import scan
from venues.sx_bet import SXBetReadOnlyClient, build_sx_bet_research_snapshot


def test_fetch_sx_bet_public_snapshot_is_parser_choice(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict] = []

    def fake_fetch(**kwargs):
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(scan, "fetch_sx_bet_readonly", fake_fetch)

    result = scan.main(
        [
            "fetch-sx-bet-public-snapshot",
            "--output-dir",
            str(tmp_path / "manual_snapshots" / "sx_bet"),
            "--json-output",
            str(tmp_path / "sx_bet_normalized_draft.json"),
        ]
    )

    assert result == 0
    assert calls
    assert calls[0]["output_dir"] == tmp_path / "manual_snapshots" / "sx_bet"
    assert calls[0]["json_output"] == tmp_path / "sx_bet_normalized_draft.json"


def test_alias_and_readonly_command_call_same_function(monkeypatch, tmp_path: Path) -> None:
    commands: list[dict] = []

    def fake_fetch(**kwargs):
        commands.append(kwargs)
        return 0

    monkeypatch.setattr(scan, "fetch_sx_bet_readonly", fake_fetch)

    for command in ("fetch-sx-bet-readonly", "fetch-sx-bet-public-snapshot"):
        result = scan.main(
            [
                command,
                "--output-dir",
                str(tmp_path / command),
                "--json-output",
                str(tmp_path / f"{command}.json"),
            ]
        )
        assert result == 0

    assert len(commands) == 2
    assert set(commands[0].keys()) == set(commands[1].keys())


def test_fake_public_fetch_writes_raw_snapshot_and_normalized_draft(tmp_path: Path) -> None:
    output_dir = tmp_path / "reports" / "manual_snapshots" / "sx_bet"
    json_output = tmp_path / "reports" / "sx_bet_normalized_draft.json"
    coverage_output = tmp_path / "reports" / "sx_bet_normalized_draft_coverage.json"

    result = scan.fetch_sx_bet_readonly(
        max_markets=25,
        timeout_seconds=1.0,
        output_dir=output_dir,
        json_output=json_output,
        coverage_output=coverage_output,
        client_factory=FakeSXBetClient,
    )

    assert result == 0
    raw_files = list(output_dir.rglob("sx_bet_research_snapshot.json"))
    assert len(raw_files) == 1
    raw_payload = json.loads(raw_files[0].read_text(encoding="utf-8"))
    assert raw_payload["schema_kind"] == "sx_bet_research_snapshot_v1"
    assert raw_payload["execution_allowed_in_project_now"] is False
    assert raw_payload["can_create_candidate_pair"] is False
    assert raw_payload["can_create_paper_candidate"] is False

    draft = json.loads(json_output.read_text(encoding="utf-8"))
    coverage = json.loads(coverage_output.read_text(encoding="utf-8"))
    assert draft["source"] == "sx_bet_normalized_draft_v1"
    assert len(draft["records"]) == 1
    assert draft["records"][0]["venue"] == "sx_bet"
    assert draft["records"][0]["diagnostic_only"] is True
    assert draft["records"][0]["affects_evaluator_gates"] is False
    assert coverage["summary"]["normalized_records"] == 1
    assert "PAPER_CANDIDATE" not in json.dumps(draft)


def test_sx_bet_public_client_uses_no_auth_or_private_execution_endpoints() -> None:
    source = Path("venues/sx_bet.py").read_text(encoding="utf-8")
    public_headers = {
        "User-Agent": SXBetReadOnlyClient().user_agent,
        "Accept": "application/json",
    }

    assert "Authorization" not in public_headers
    assert "X-API-KEY" not in {key.upper() for key in public_headers}
    assert "privateKey" not in source.split("class SXBetReadOnlyClient", 1)[1].split("def build_sx_bet_research_snapshot", 1)[0]
    assert "method=\"POST\"" not in source
    assert "/fill" not in source
    assert "/cancel" not in source
    assert "/orders/fill" not in source
    assert "/orders/cancel" not in source


class FakeSXBetClient:
    def __init__(self, *, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds

    def fetch_research_snapshot(self, **kwargs):
        snapshot = build_sx_bet_research_snapshot(_raw_fixture(), captured_at=kwargs.get("captured_at"))
        snapshot["live_fetch_attempted"] = True
        snapshot["live_fetch_succeeded"] = True
        snapshot["execution_allowed_in_project_now"] = False
        snapshot["can_create_candidate_pair"] = False
        snapshot["can_create_paper_candidate"] = False
        snapshot["endpoint_metadata"] = {
            "markets_endpoint": "/markets/active",
            "orders_endpoint": "/orders",
            "auth_used": False,
            "wallet_or_signing_used": False,
        }
        snapshot["targeting"] = {
            "targeting_method": "unfiltered",
            "requested_sport": None,
            "requested_league": None,
            "requested_query": None,
        }
        snapshot["sx_bet_fetched_count"] = snapshot["market_count"]
        snapshot["sx_bet_retained_count"] = snapshot["research_market_count"]
        return snapshot


def _raw_fixture() -> dict:
    return {
        "markets": [
            {
                "marketHash": "0xabc123",
                "eventName": "Boston Celtics vs New York Knicks",
                "leagueLabel": "NBA",
                "sportLabel": "Basketball",
                "sportXeventId": "S1779385200:celtics:knicks",
                "type": 52,
                "line": None,
                "mainLine": True,
                "status": "ACTIVE",
                "gameTime": "2026-05-21T23:00:00Z",
                "outcomeOneName": "Boston Celtics",
                "outcomeTwoName": "New York Knicks",
                "outcomeVoidName": "Game cancelled or voided",
                "settlementSource": "official league result",
                "settlementRule": "Moneyline market; void if event is cancelled or neither outcome is valid.",
            }
        ],
        "orders": [
            {
                "orderHash": "0xorder1",
                "marketHash": "0xabc123",
                "isMakerBettingOutcomeOne": False,
                "percentageOdds": "42000000000000000000",
                "totalBetSize": "758990000",
                "fillAmount": "0",
            }
        ],
    }
