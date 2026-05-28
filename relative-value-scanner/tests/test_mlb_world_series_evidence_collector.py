from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import scan
from relative_value.sports_mlb_world_series_evidence_collector import (
    convert_kalshi_orderbook,
    parse_kalshi_world_series_markets,
    parse_polymarket_clob_book,
    parse_polymarket_world_series_event,
    write_mlb_world_series_evidence_files,
)


NOW = datetime(2026, 5, 28, 6, 0, tzinfo=timezone.utc)

TEAM_CODES = [
    "LAD",
    "NYY",
    "ATL",
    "PHI",
    "HOU",
    "BAL",
    "BOS",
    "CHC",
    "CIN",
    "CLE",
    "COL",
    "CWS",
    "DET",
    "KC",
    "LAA",
    "MIA",
    "MIL",
    "MIN",
    "NYM",
    "PIT",
    "SD",
    "SEA",
    "SF",
    "STL",
    "TB",
    "TEX",
    "TOR",
    "WSH",
    "ATH",
    "AZ",
]


def test_kalshi_event_parser_extracts_30_team_tickers_from_fixture() -> None:
    payload = {"markets": [_kalshi_market(code) for code in TEAM_CODES]}

    rows = parse_kalshi_world_series_markets([payload], season=2026)

    assert len(rows) == 30
    assert {row["market_ticker"] for row in rows} == {f"KXMLB-26-{code}" for code in TEAM_CODES}
    assert {row["series_ticker"] for row in rows} == {"KXMLB"}
    assert {row["event_ticker"] for row in rows} == {"KXMLB-26"}


def test_kalshi_orderbook_conversion_computes_yes_no_bid_ask_and_sizes() -> None:
    converted = convert_kalshi_orderbook(
        {
            "orderbook_fp": {
                "yes_dollars": [["0.2500", "10"], ["0.2600", "12"]],
                "no_dollars": [["0.7300", "8"], ["0.7400", "9"]],
            }
        }
    )

    assert converted["yes_bid"] == 0.26
    assert converted["yes_bid_size"] == 12
    assert converted["yes_ask"] == 0.26
    assert converted["yes_ask_size"] == 9
    assert converted["no_bid"] == 0.74
    assert converted["no_bid_size"] == 9
    assert converted["no_ask"] == 0.74
    assert converted["no_ask_size"] == 12
    assert converted["partial_book"] is False


def test_polymarket_event_parser_extracts_market_condition_and_token_ids() -> None:
    payload = [_poly_event(markets=[_poly_market("Los Angeles Dodgers", "m1", "c1", "yes-lad", "no-lad")])]

    event = parse_polymarket_world_series_event(payload, season=2026)

    assert event["event_slug"] == "mlb-world-series-champion-2026"
    assert event["parent_event_id"] == "event-2026"
    assert len(event["outcomes"]) == 1
    row = event["outcomes"][0]
    assert row["team_name"] == "Los Angeles Dodgers"
    assert row["market_id"] == "m1"
    assert row["condition_id"] == "c1"
    assert row["token_id_yes"] == "yes-lad"
    assert row["token_id_no"] == "no-lad"


def test_polymarket_clob_quote_parser_maps_bids_and_asks_without_midpoint_inference() -> None:
    metrics = parse_polymarket_clob_book(
        {
            "timestamp": "1779947852140",
            "bids": [{"price": "0.25", "size": "100"}, {"price": "0.26", "size": "12"}],
            "asks": [{"price": "0.29", "size": "3"}, {"price": "0.28", "size": "4"}],
        }
    )

    assert metrics["bid"] == 0.26
    assert metrics["bid_size"] == 12
    assert metrics["ask"] == 0.28
    assert metrics["ask_size"] == 4
    assert metrics["quote_timestamp"] == "1779947852140"


def test_other_outcome_rule_preserved_when_ids_and_books_missing(tmp_path: Path) -> None:
    report = write_mlb_world_series_evidence_files(
        season=2026,
        output_dir=tmp_path / "raw",
        normalized_output_dir=tmp_path / "normalized",
        timeout_seconds=1.0,
        generated_at=NOW,
        http_get=_fake_http_get,
        polymarket_gamma_base_url="https://gamma.test",
        polymarket_clob_base_url="https://clob.test",
        kalshi_base_url="https://kalshi.test/trade-api/v2",
    )

    poly = json.loads(Path(report["outputs"]["polymarket_normalized"]).read_text(encoding="utf-8"))
    structure = poly["market_structure"]
    assert structure["other_outcome_exists"] is True
    assert structure["other_outcome_ids_provided"] is False
    assert structure["other_quote_provided"] is False


def test_collection_summary_reports_missing_teams_tickers_and_books(tmp_path: Path) -> None:
    report = write_mlb_world_series_evidence_files(
        season=2026,
        output_dir=tmp_path / "raw",
        normalized_output_dir=tmp_path / "normalized",
        timeout_seconds=1.0,
        generated_at=NOW,
        http_get=_fake_http_get_missing_books,
        polymarket_gamma_base_url="https://gamma.test",
        polymarket_clob_base_url="https://clob.test",
        kalshi_base_url="https://kalshi.test/trade-api/v2",
    )

    missing = report["missing_fields_or_blockers"]
    assert missing["kalshi_missing_tickers"] == 28
    assert missing["kalshi_missing_orderbooks"] == 2
    assert missing["polymarket_missing_team_outcomes"] == 28
    assert missing["polymarket_missing_books"] == 4


def test_outputs_do_not_contain_forbidden_upper_candidate_literal_and_exact_stays_false(tmp_path: Path) -> None:
    report = write_mlb_world_series_evidence_files(
        season=2026,
        output_dir=tmp_path / "raw",
        normalized_output_dir=tmp_path / "normalized",
        timeout_seconds=1.0,
        generated_at=NOW,
        http_get=_fake_http_get,
        polymarket_gamma_base_url="https://gamma.test",
        polymarket_clob_base_url="https://clob.test",
        kalshi_base_url="https://kalshi.test/trade-api/v2",
    )

    combined = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in report["outputs"].values()
        if str(path).endswith((".json", ".md"))
    )
    forbidden = "PAPER" + "_CANDIDATE"
    assert forbidden not in combined
    kalshi = json.loads(Path(report["outputs"]["kalshi_normalized"]).read_text(encoding="utf-8"))
    polymarket = json.loads(Path(report["outputs"]["polymarket_normalized"]).read_text(encoding="utf-8"))
    assert kalshi["exact_ready"] is False
    assert polymarket["exact_ready"] is False
    assert kalshi["gates_cleared"] is False
    assert polymarket["gates_cleared"] is False
    assert report["summary_counts"]["exact_ready_rows"] == 0
    assert report["summary_counts"]["paper_candidate_rows"] == 0


def test_no_forbidden_endpoint_strings_introduced() -> None:
    source = Path("relative_value/sports_mlb_world_series_evidence_collector.py").read_text(encoding="utf-8")
    forbidden = (
        "/orders",
        "/portfolio",
        "/positions",
        "/balance",
        "/auth",
        "/session",
        "private-key",
        "private_key",
        "Authorization",
        "Bearer ",
        "wallet",
        "signing",
        "Cloudflare",
    )
    for token in forbidden:
        assert token not in source


def test_cli_writes_expected_output_paths_with_fake_writer(tmp_path: Path, monkeypatch, capsys) -> None:
    def fake_writer(**kwargs):
        normalized = kwargs["normalized_output_dir"]
        normalized.mkdir(parents=True, exist_ok=True)
        summary = {
            "summary_counts": {
                "kalshi_team_outcomes": 30,
                "kalshi_tickers": 30,
                "kalshi_orderbooks_requested": 30,
                "polymarket_team_outcomes": 30,
                "polymarket_token_ids": 60,
                "polymarket_books_requested": 60,
                "raw_files_written": 93,
            },
            "missing_fields_or_blockers": {
                "kalshi_missing_orderbooks": 0,
                "polymarket_missing_books": 0,
            },
            "top_blockers": [],
            "outputs": {
                "summary_json": str(normalized / "summary.json"),
                "summary_markdown": str(normalized / "summary.md"),
            },
        }
        Path(summary["outputs"]["summary_json"]).write_text(json.dumps(summary), encoding="utf-8")
        Path(summary["outputs"]["summary_markdown"]).write_text("# summary\n", encoding="utf-8")
        return summary

    monkeypatch.setattr(scan, "write_mlb_world_series_evidence_files", fake_writer)

    result = scan.main(
        [
            "fetch-mlb-world-series-evidence",
            "--season",
            "2026",
            "--output-dir",
            str(tmp_path / "raw"),
            "--normalized-output-dir",
            str(tmp_path / "normalized"),
        ]
    )

    assert result == 0
    stdout = capsys.readouterr().out
    assert "fetch_mlb_world_series_evidence_status=OK" in stdout
    assert "public_no_auth_only=true" in stdout
    assert "kalshi_team_outcomes=30" in stdout
    assert "exact_ready_rows=0" in stdout


def _fake_http_get(url: str, timeout: float):
    parsed = urlparse(url)
    if "gamma.test" in parsed.netloc:
        return [_poly_event(markets=_poly_markets())]
    if "clob.test" in parsed.netloc:
        token = parse_qs(parsed.query).get("token_id", [""])[0]
        return _poly_book(token)
    if "kalshi.test" in parsed.netloc and parsed.path.endswith("/markets"):
        return {"markets": [_kalshi_market("LAD"), _kalshi_market("NYY")]}
    if "kalshi.test" in parsed.netloc and parsed.path.endswith("/orderbook"):
        return _kalshi_book()
    raise AssertionError(f"unexpected URL {url}")


def _fake_http_get_missing_books(url: str, timeout: float):
    parsed = urlparse(url)
    if "gamma.test" in parsed.netloc:
        return [_poly_event(markets=_poly_markets())]
    if "clob.test" in parsed.netloc:
        raise RuntimeError("book unavailable")
    if "kalshi.test" in parsed.netloc and parsed.path.endswith("/markets"):
        return {"markets": [_kalshi_market("LAD"), _kalshi_market("NYY")]}
    if "kalshi.test" in parsed.netloc and parsed.path.endswith("/orderbook"):
        raise RuntimeError("book unavailable")
    raise AssertionError(f"unexpected URL {url}")


def _kalshi_market(code: str) -> dict:
    team = {
        "AZ": "Arizona",
        "ATH": "Athletics",
        "LAD": "Los Angeles D",
        "NYY": "New York Y",
    }.get(code, code)
    return {
        "ticker": f"KXMLB-26-{code}",
        "event_ticker": "KXMLB-26",
        "title": f"Will {team} win the 2026 Pro Baseball Championship?",
        "yes_sub_title": team,
        "status": "active",
        "last_price_dollars": "0.0500",
        "rules_primary": "Underlying: winner of Pro Baseball Champion. Source Agency: league and credible reporting.",
    }


def _kalshi_book() -> dict:
    return {
        "orderbook_fp": {
            "yes_dollars": [["0.2500", "10"], ["0.2600", "12"]],
            "no_dollars": [["0.7300", "8"], ["0.7400", "9"]],
        }
    }


def _poly_event(markets: list[dict]) -> dict:
    return {
        "id": "event-2026",
        "slug": "mlb-world-series-champion-2026",
        "title": "MLB World Series Champion 2026",
        "description": "This market resolves to the team that wins the 2026 MLB World Series. If there is no winner by December 31, 2026, this resolves to Other.",
        "resolutionSource": "Official information from MLB (https://www.mlb.com/).",
        "enableNegRisk": True,
        "markets": markets,
    }


def _poly_markets() -> list[dict]:
    return [
        _poly_market("Los Angeles Dodgers", "m-lad", "c-lad", "yes-lad", "no-lad"),
        _poly_market("New York Yankees", "m-nyy", "c-nyy", "yes-nyy", "no-nyy"),
    ]


def _poly_market(team: str, market_id: str, condition_id: str, yes_token: str, no_token: str) -> dict:
    slug = team.lower().replace(" ", "-")
    return {
        "id": market_id,
        "conditionId": condition_id,
        "slug": f"will-the-{slug}-win-the-2026-world-series",
        "question": f"Will the {team} win the 2026 World Series?",
        "outcomes": json.dumps(["Yes", "No"]),
        "clobTokenIds": json.dumps([yes_token, no_token]),
        "active": True,
        "closed": False,
        "lastTradePrice": "0.25",
        "makerBaseFee": "1000",
        "takerBaseFee": "1000",
    }


def _poly_book(token: str) -> dict:
    return {
        "asset_id": token,
        "timestamp": "1779947852140",
        "bids": [{"price": "0.25", "size": "100"}, {"price": "0.26", "size": "12"}],
        "asks": [{"price": "0.29", "size": "3"}, {"price": "0.28", "size": "4"}],
    }
