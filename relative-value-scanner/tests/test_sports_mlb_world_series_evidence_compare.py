from __future__ import annotations

import json
from pathlib import Path

import scan
from relative_value.sports_mlb_world_series_evidence_compare import (
    build_sports_mlb_world_series_evidence_comparison,
    write_sports_mlb_world_series_evidence_compare_files,
)


def test_scope_validation_accepts_mlb_championship_futures() -> None:
    report = _build()

    assert report["scope_validation"]["valid"] is True
    assert report["matched_team_rows"] == 1
    assert report["rows"][0]["action"] == "SOURCE_REVIEW"


def test_scope_validation_rejects_daily_games() -> None:
    kalshi = {"platform": "Kalshi", "league": "MLB", "date_label": "2026-05-28", "games": []}
    report = build_sports_mlb_world_series_evidence_comparison(
        kalshi_payload=kalshi,
        polymarket_payload=_polymarket_payload(),
    )

    assert report["scope_validation"]["valid"] is False
    assert "unsupported_market_scope" in report["scope_validation"]["kalshi"]["blockers"]
    assert "not_championship_futures_scope" in report["scope_validation"]["kalshi"]["blockers"]


def test_scope_validation_rejects_non_mlb() -> None:
    kalshi = _kalshi_payload()
    kalshi["league"] = "NHL"

    report = build_sports_mlb_world_series_evidence_comparison(
        kalshi_payload=kalshi,
        polymarket_payload=_polymarket_payload(),
    )

    assert report["scope_validation"]["valid"] is False
    assert "not_mlb_scope" in report["scope_validation"]["kalshi"]["blockers"]
    assert report["rows"][0]["action"] == "IGNORE_BLOCKED"


def test_alias_matching_works_for_required_team_aliases() -> None:
    kalshi = _kalshi_payload(
        outcomes=[
            _kalshi_outcome("LAD", "Los Angeles D", "KXMLB-26-LAD"),
            _kalshi_outcome("NYY", "Yankees", "KXMLB-26-NYY"),
            _kalshi_outcome("ATL", "Braves", "KXMLB-26-ATL"),
            _kalshi_outcome("TOR", "Blue Jays", "KXMLB-26-TOR"),
            _kalshi_outcome("CWS", "White Sox", "KXMLB-26-CWS"),
            _kalshi_outcome("ATH", "Athletics", "KXMLB-26-ATH"),
            _kalshi_outcome("LAA", "Angels", "KXMLB-26-LAA"),
        ]
    )
    poly = _polymarket_payload(
        outcomes=[
            _poly_outcome("Los Angeles Dodgers", "m-lad", "yes-lad", "no-lad", aliases=["Dodgers"]),
            _poly_outcome("New York Yankees", "m-nyy", "yes-nyy", "no-nyy", aliases=["NYY"]),
            _poly_outcome("Atlanta Braves", "m-atl", "yes-atl", "no-atl", aliases=["ATL"]),
            _poly_outcome("Toronto Blue Jays", "m-tor", "yes-tor", "no-tor", aliases=["TOR"]),
            _poly_outcome("Chicago White Sox", "m-cws", "yes-cws", "no-cws", aliases=["CWS"]),
            _poly_outcome("Oakland A's", "m-ath", "yes-ath", "no-ath", aliases=["A's"]),
            _poly_outcome("Los Angeles Angels", "m-laa", "yes-laa", "no-laa", aliases=["LAA"]),
        ]
    )

    report = build_sports_mlb_world_series_evidence_comparison(kalshi_payload=kalshi, polymarket_payload=poly)

    assert report["matched_team_rows"] == 7
    assert {row["canonical_team_key"] for row in report["rows"]} == {"LAD", "NYY", "ATL", "TOR", "CWS", "ATH", "LAA"}


def test_cancellation_no_champion_mismatch_adds_blockers() -> None:
    report = _build()
    blockers = report["rows"][0]["blockers"]

    assert "proportional_payout_vs_other_outcome_mismatch" in blockers
    assert "remote_tail_risk_review_required" in blockers


def test_accept_world_series_remote_tail_risk_records_human_flag_but_does_not_clear_exact_or_paper() -> None:
    report = _build(accept=True)
    row = report["rows"][0]

    assert report["human_accepted_remote_tail_risk"] is True
    assert report["residual_risk_type"] == "mlb_world_series_no_champion_other_vs_proportional_tail_risk"
    assert "remote_tail_risk_human_accepted_but_not_exact" in row["blockers"]
    assert row["exact_ready"] is False
    assert row["paper_candidate"] is False
    assert report["exact_ready_rows"] == 0
    assert report["paper_candidate_rows"] == 0


def test_missing_polymarket_token_ids_blocks_source_review() -> None:
    poly = _polymarket_payload(outcomes=[_poly_outcome("Los Angeles Dodgers", "m-lad", None, None)])

    report = build_sports_mlb_world_series_evidence_comparison(
        kalshi_payload=_kalshi_payload(),
        polymarket_payload=poly,
    )

    row = report["rows"][0]
    assert "missing_polymarket_token_ids" in row["blockers"]
    assert row["action"] == "MANUAL_REVIEW"


def test_missing_kalshi_ticker_blocks_source_review() -> None:
    kalshi = _kalshi_payload(outcomes=[_kalshi_outcome("LAD", "Los Angeles Dodgers", None)])

    report = build_sports_mlb_world_series_evidence_comparison(
        kalshi_payload=kalshi,
        polymarket_payload=_polymarket_payload(),
    )

    row = report["rows"][0]
    assert "missing_kalshi_ticker" in row["blockers"]
    assert row["action"] == "MANUAL_REVIEW"


def test_no_forbidden_upper_candidate_literal_in_outputs_and_exact_ready_rows_remain_zero(tmp_path: Path) -> None:
    kalshi_path = tmp_path / "kalshi.json"
    poly_path = tmp_path / "poly.json"
    kalshi_path.write_text(json.dumps(_kalshi_payload()), encoding="utf-8")
    poly_path.write_text(json.dumps(_polymarket_payload()), encoding="utf-8")

    report = write_sports_mlb_world_series_evidence_compare_files(
        kalshi_evidence=kalshi_path,
        polymarket_evidence=poly_path,
        json_output=tmp_path / "report.json",
        markdown_output=tmp_path / "report.md",
    )

    combined = (tmp_path / "report.json").read_text(encoding="utf-8") + (tmp_path / "report.md").read_text(encoding="utf-8")
    forbidden = "PAPER" + "_CANDIDATE"
    assert forbidden not in combined
    assert report["exact_ready_rows"] == 0
    assert report["summary_counts"]["exact_ready_rows"] == 0
    assert report["paper_candidate_emitted"] is False


def test_cli_writes_report_with_fake_writer(tmp_path: Path, monkeypatch, capsys) -> None:
    def fake_writer(**kwargs):
        report = {
            "summary_counts": {
                "source_review_rows": 1,
                "manual_review_rows": 0,
                "watch_rows": 0,
                "ignore_blocked_rows": 0,
            },
            "top_blockers": [{"blocker": "remote_tail_risk_review_required", "count": 1}],
            "human_accepted_remote_tail_risk": kwargs["accept_world_series_remote_tail_risk"],
            "kalshi_rows_loaded": 1,
            "polymarket_rows_loaded": 1,
            "matched_team_rows": 1,
            "unmatched_kalshi_rows": 0,
            "unmatched_polymarket_rows": 0,
        }
        kwargs["json_output"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["json_output"].write_text(json.dumps(report), encoding="utf-8")
        kwargs["markdown_output"].write_text("# report\n", encoding="utf-8")
        return report

    monkeypatch.setattr(scan, "write_sports_mlb_world_series_evidence_compare_files", fake_writer)
    result = scan.main(
        [
            "sports-mlb-world-series-evidence-compare",
            "--kalshi-evidence",
            str(tmp_path / "kalshi.json"),
            "--polymarket-evidence",
            str(tmp_path / "poly.json"),
            "--json-output",
            str(tmp_path / "report.json"),
            "--markdown-output",
            str(tmp_path / "report.md"),
        ]
    )

    assert result == 0
    stdout = capsys.readouterr().out
    assert "sports_mlb_world_series_evidence_compare_status=OK" in stdout
    assert "diagnostic_only=true" in stdout
    assert "matched_team_rows=1" in stdout
    assert "exact_ready_rows=0" in stdout


def _build(*, accept: bool = False) -> dict:
    return build_sports_mlb_world_series_evidence_comparison(
        kalshi_payload=_kalshi_payload(),
        polymarket_payload=_polymarket_payload(),
        accept_world_series_remote_tail_risk=accept,
    )


def _kalshi_payload(*, outcomes: list[dict] | None = None) -> dict:
    return {
        "schema_kind": "kalshi_championship_futures_normalized_evidence_v1",
        "diagnostic_only": True,
        "platform": "Kalshi",
        "batch": "championship_futures",
        "league": "MLB",
        "season": "2026",
        "market": {
            "platform": "Kalshi",
            "batch": "championship_futures",
            "league": "MLB",
            "season": "2026",
            "event_ticker": "KXMLB-26",
        },
        "outcomes": outcomes or [_kalshi_outcome("LAD", "Los Angeles Dodgers", "KXMLB-26-LAD")],
    }


def _polymarket_payload(*, outcomes: list[dict] | None = None) -> dict:
    return {
        "schema_kind": "polymarket_mlb_world_series_2026_normalized_evidence_v1",
        "diagnostic_only": True,
        "platform": "Polymarket",
        "batch": "championship_futures",
        "league": "MLB",
        "season": "2026",
        "market_title": "MLB World Series Champion 2026",
        "market_structure": {
            "other_outcome_exists": True,
            "other_outcome_ids_provided": False,
            "other_quote_provided": False,
        },
        "outcomes": outcomes or [_poly_outcome("Los Angeles Dodgers", "m-lad", "yes-lad", "no-lad")],
    }


def _kalshi_outcome(code: str, team: str, ticker: str | None) -> dict:
    return {
        "team_name": team,
        "team_aliases": [code, team.split()[-1]],
        "market_ticker": ticker,
        "quote_status": "present_full_clob",
        "quote": {
            "yes_bid": "0.25",
            "yes_ask": "0.26",
            "yes_bid_size": "100",
            "yes_ask_size": "90",
            "depth_status": "full_clob",
            "quote_timestamp": "1779948392804",
            "required_quote_fields_present": True,
        },
        "blockers_remaining": [],
    }


def _poly_outcome(team: str, market_id: str, yes_token: str | None, no_token: str | None, *, aliases: list[str] | None = None) -> dict:
    return {
        "team_name": team,
        "outcome_name": team,
        "team_aliases": aliases or [team.split()[-1]],
        "market_id": market_id,
        "condition_id": f"condition-{market_id}",
        "token_id_yes": yes_token,
        "token_id_no": no_token,
        "quote_status": "present",
        "quote": {
            "yes_bid": "0.24",
            "yes_ask": "0.27",
            "yes_bid_size": "100",
            "yes_ask_size": "90",
            "depth_status": "full_clob",
            "quote_timestamp": "1779948384596",
            "required_quote_fields_present": True,
        },
        "blockers_remaining": [],
    }
