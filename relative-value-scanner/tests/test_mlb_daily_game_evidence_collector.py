from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import scan
from relative_value.sports_mlb_daily_game_evidence_collector import (
    convert_kalshi_orderbook,
    make_cross_platform_game_key,
    parse_kalshi_mlb_daily_markets,
    parse_polymarket_mlb_daily_markets,
    write_mlb_daily_game_evidence_files,
)


NOW = datetime(2026, 5, 28, 5, 0, tzinfo=timezone.utc)


def test_command_can_run_with_date_and_produce_output_paths(tmp_path: Path) -> None:
    report = write_mlb_daily_game_evidence_files(
        target_date="2026-05-28",
        output_dir=tmp_path / "raw",
        normalized_output_dir=tmp_path / "normalized",
        max_games=20,
        timeout_seconds=1.0,
        generated_at=NOW,
        http_get=_fake_http_get,
        polymarket_gamma_base_url="https://gamma.test",
        polymarket_clob_base_url="https://clob.test",
        kalshi_base_url="https://kalshi.test/trade-api/v2",
    )

    outputs = report["outputs"]
    assert Path(outputs["polymarket_normalized"]).exists()
    assert Path(outputs["kalshi_normalized"]).exists()
    assert Path(outputs["summary_json"]).exists()
    assert Path(outputs["summary_markdown"]).exists()
    assert "20260528_050000Z" in outputs["raw_root"]
    assert report["summary_counts"]["matched_games"] == 1


def test_polymarket_parser_excludes_spreads_totals_and_player_props() -> None:
    payload = {
        "events": [
            _poly_event(
                market=_poly_market(
                    slug="mlb-laa-det-2026-05-28",
                    question="MLB: Los Angeles Angels vs Detroit Tigers, May 28",
                    outcomes=["Los Angeles Angels", "Detroit Tigers"],
                )
            ),
            _poly_event(
                market=_poly_market(
                    slug="mlb-laa-det-spread-2026-05-28",
                    question="MLB LAA vs DET spread May 28",
                    outcomes=["Los Angeles Angels -1.5", "Detroit Tigers +1.5"],
                )
            ),
            _poly_event(
                market=_poly_market(
                    slug="mlb-laa-det-total-2026-05-28",
                    question="MLB LAA vs DET total runs May 28",
                    outcomes=["Over", "Under"],
                )
            ),
            _poly_event(
                market=_poly_market(
                    slug="mlb-player-prop-2026-05-28",
                    question="Will Mike Trout hit a home run May 28?",
                    outcomes=["Yes", "No"],
                )
            ),
        ]
    }

    rows = parse_polymarket_mlb_daily_markets([payload], date_label="2026-05-28", max_games=20)

    assert len(rows) == 1
    assert rows[0]["cross_platform_game_key"] == "MLB-2026-05-28-LAA-DET"


def test_kalshi_parser_extracts_kxmlbgame_event_and_tickers() -> None:
    payload = {
        "markets": [
            _kalshi_market("KXMLBGAME-26MAY281310LAADET-LAA", title="Will the Angels win?"),
            _kalshi_market("KXMLBGAME-26MAY281310LAADET-DET", title="Will the Tigers win?"),
        ]
    }

    rows = parse_kalshi_mlb_daily_markets([payload], date_label="2026-05-28", max_games=20)

    assert len(rows) == 1
    assert rows[0]["event_ticker"] == "KXMLBGAME-26MAY281310LAADET"
    assert rows[0]["cross_platform_game_key"] == "MLB-2026-05-28-LAA-DET"
    assert {row["ticker"] for row in rows[0]["markets"]} == {
        "KXMLBGAME-26MAY281310LAADET-LAA",
        "KXMLBGAME-26MAY281310LAADET-DET",
    }


def test_kalshi_orderbook_conversion_computes_yes_no_bid_ask_and_sizes() -> None:
    converted = convert_kalshi_orderbook(
        {
            "orderbook": {
                "yes": [[44, 10], [45, 11]],
                "no": [[54, 12], [53, 13]],
            }
        }
    )

    assert converted["yes_bid"] == 0.45
    assert converted["yes_bid_size"] == 11
    assert converted["yes_ask"] == 0.46
    assert converted["yes_ask_size"] == 12
    assert converted["no_bid"] == 0.54
    assert converted["no_bid_size"] == 12
    assert converted["no_ask"] == 0.55
    assert converted["no_ask_size"] == 11
    assert converted["partial_book"] is False


def test_stable_game_key_and_missing_peer_flags(tmp_path: Path) -> None:
    assert make_cross_platform_game_key("2026-05-28", "LAA", "DET") == "MLB-2026-05-28-LAA-DET"

    def fake_missing_peer(url: str, timeout: float):
        if "gamma.test" in url:
            return {"events": [_poly_event(market=_poly_market())]}
        if "kalshi.test" in url and "/markets?" in url:
            return {"markets": []}
        if "clob.test" in url:
            return _poly_book()
        raise AssertionError(url)

    report = write_mlb_daily_game_evidence_files(
        target_date="2026-05-28",
        output_dir=tmp_path / "raw",
        normalized_output_dir=tmp_path / "normalized",
        max_games=20,
        timeout_seconds=1.0,
        generated_at=NOW,
        http_get=fake_missing_peer,
        polymarket_gamma_base_url="https://gamma.test",
        polymarket_clob_base_url="https://clob.test",
        kalshi_base_url="https://kalshi.test/trade-api/v2",
    )

    assert report["summary_counts"]["missing_kalshi_peer"] == 1
    assert report["top_blockers"][0]["blocker"] == "missing_kalshi_peer"


def test_polymarket_slug_seed_from_kalshi_finds_games_when_search_empty(tmp_path: Path) -> None:
    def fake_slug_only(url: str, timeout: float):
        parsed = urlparse(url)
        if "gamma.test" in parsed.netloc:
            query = parse_qs(parsed.query)
            if query.get("slug") == ["mlb-laa-det-2026-05-28"]:
                return [_poly_market()]
            return []
        if "clob.test" in parsed.netloc:
            token = parse_qs(parsed.query).get("token_id", [""])[0]
            return _poly_book(token)
        if "kalshi.test" in parsed.netloc and parsed.path.endswith("/markets"):
            return {
                "markets": [
                    _kalshi_market("KXMLBGAME-26MAY281310LAADET-LAA", title="Will the Angels win?"),
                    _kalshi_market("KXMLBGAME-26MAY281310LAADET-DET", title="Will the Tigers win?"),
                ]
            }
        if "kalshi.test" in parsed.netloc and parsed.path.endswith("/orderbook"):
            return {"orderbook": {"yes": [[45, 10]], "no": [[54, 12]]}}
        raise AssertionError(f"unexpected URL {url}")

    report = write_mlb_daily_game_evidence_files(
        target_date="2026-05-28",
        output_dir=tmp_path / "raw",
        normalized_output_dir=tmp_path / "normalized",
        max_games=20,
        timeout_seconds=1.0,
        generated_at=NOW,
        http_get=fake_slug_only,
        polymarket_gamma_base_url="https://gamma.test",
        polymarket_clob_base_url="https://clob.test",
        kalshi_base_url="https://kalshi.test/trade-api/v2",
    )

    assert report["summary_counts"]["polymarket_games"] == 1
    assert report["summary_counts"]["kalshi_games"] == 1
    assert report["summary_counts"]["matched_games"] == 1
    assert any("gamma_markets_slug" in path for path in report["raw_files_written"])


def test_missing_polymarket_suspended_and_extra_innings_text_remains_blocker(tmp_path: Path) -> None:
    report = write_mlb_daily_game_evidence_files(
        target_date="2026-05-28",
        output_dir=tmp_path / "raw",
        normalized_output_dir=tmp_path / "normalized",
        max_games=20,
        timeout_seconds=1.0,
        generated_at=NOW,
        http_get=_fake_http_get,
        polymarket_gamma_base_url="https://gamma.test",
        polymarket_clob_base_url="https://clob.test",
        kalshi_base_url="https://kalshi.test/trade-api/v2",
    )

    poly = json.loads(Path(report["outputs"]["polymarket_normalized"]).read_text(encoding="utf-8"))
    blockers = poly["games"][0]["blockers_remaining"]
    assert "missing_suspended_or_shortened_game_rules" in blockers
    assert "missing_extra_innings_rules" in blockers


def test_outputs_do_not_contain_forbidden_upper_candidate_literal_and_exact_stays_false(tmp_path: Path) -> None:
    report = write_mlb_daily_game_evidence_files(
        target_date="2026-05-28",
        output_dir=tmp_path / "raw",
        normalized_output_dir=tmp_path / "normalized",
        max_games=20,
        timeout_seconds=1.0,
        generated_at=NOW,
        http_get=_fake_http_get,
        polymarket_gamma_base_url="https://gamma.test",
        polymarket_clob_base_url="https://clob.test",
        kalshi_base_url="https://kalshi.test/trade-api/v2",
    )

    texts = []
    for path in report["outputs"].values():
        if str(path).endswith((".json", ".md")):
            texts.append(Path(path).read_text(encoding="utf-8"))
    combined = "\n".join(texts)
    forbidden = "PAPER" + "_CANDIDATE"
    assert forbidden not in combined
    kalshi = json.loads(Path(report["outputs"]["kalshi_normalized"]).read_text(encoding="utf-8"))
    polymarket = json.loads(Path(report["outputs"]["polymarket_normalized"]).read_text(encoding="utf-8"))
    assert kalshi["exact_ready"] is False
    assert polymarket["exact_ready"] is False
    assert all(game["exact_ready"] is False for game in kalshi["games"] + polymarket["games"])
    assert report["summary_counts"]["exact_ready_rows"] == 0
    assert report["summary_counts"]["paper_candidate_rows"] == 0


def test_no_forbidden_endpoint_strings_introduced() -> None:
    source = Path("relative_value/sports_mlb_daily_game_evidence_collector.py").read_text(encoding="utf-8")
    forbidden = (
        "/orders",
        "/order/",
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


def test_cli_uses_defaults_and_writes_summary_with_fake_writer(tmp_path: Path, monkeypatch, capsys) -> None:
    def fake_writer(**kwargs):
        normalized = kwargs["normalized_output_dir"]
        normalized.mkdir(parents=True, exist_ok=True)
        summary = {
            "summary_counts": {
                "polymarket_games": 1,
                "kalshi_games": 1,
                "matched_games": 1,
                "missing_kalshi_peer": 0,
                "missing_polymarket_peer": 0,
                "raw_files_written": 2,
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

    monkeypatch.setattr(scan, "write_mlb_daily_game_evidence_files", fake_writer)

    result = scan.main(
        [
            "fetch-mlb-daily-game-evidence",
            "--date",
            "2026-05-28",
            "--output-dir",
            str(tmp_path / "raw"),
            "--normalized-output-dir",
            str(tmp_path / "normalized"),
            "--max-games",
            "2",
        ]
    )

    assert result == 0
    stdout = capsys.readouterr().out
    assert "fetch_mlb_daily_game_evidence_status=OK" in stdout
    assert "public_no_auth_only=true" in stdout
    assert "exact_ready_rows=0" in stdout


def _fake_http_get(url: str, timeout: float):
    parsed = urlparse(url)
    if "gamma.test" in parsed.netloc:
        query = parse_qs(parsed.query)
        if "spread" in " ".join(query.get("search", [])).lower():
            return {"events": []}
        return {"events": [_poly_event(market=_poly_market())]}
    if "clob.test" in parsed.netloc:
        token = parse_qs(parsed.query).get("token_id", [""])[0]
        return _poly_book(token)
    if "kalshi.test" in parsed.netloc and parsed.path.endswith("/markets"):
        return {
            "markets": [
                _kalshi_market("KXMLBGAME-26MAY281310LAADET-LAA", title="Will the Angels win?"),
                _kalshi_market("KXMLBGAME-26MAY281310LAADET-DET", title="Will the Tigers win?"),
            ]
        }
    if "kalshi.test" in parsed.netloc and parsed.path.endswith("/orderbook"):
        return {
            "orderbook": {
                "yes": [[45, 10]],
                "no": [[54, 12]],
            }
        }
    raise AssertionError(f"unexpected URL {url}")


def _poly_event(*, market: dict) -> dict:
    return {
        "id": "event-1",
        "slug": "mlb-laa-det-2026-05-28",
        "title": "MLB: Los Angeles Angels vs Detroit Tigers May 28, 2026",
        "markets": [market],
    }


def _poly_market(
    *,
    slug: str = "mlb-laa-det-2026-05-28",
    question: str = "MLB: Los Angeles Angels vs Detroit Tigers, May 28, 2026",
    outcomes: list[str] | None = None,
) -> dict:
    return {
        "id": "2331779",
        "conditionId": "0xabc",
        "slug": slug,
        "question": question,
        "description": "Resolves to the winning team. If postponed, market remains open until completed. If canceled entirely with no make-up game, resolves 50-50.",
        "outcomes": json.dumps(outcomes or ["Los Angeles Angels", "Detroit Tigers"]),
        "clobTokenIds": json.dumps(["token-laa", "token-det"]),
        "active": True,
        "closed": False,
    }


def _poly_book(token: str = "token") -> dict:
    return {
        "asset_id": token,
        "bids": [{"price": "0.45", "size": "100"}],
        "asks": [{"price": "0.46", "size": "90"}],
    }


def _kalshi_market(ticker: str, *, title: str) -> dict:
    event_ticker = ticker.rsplit("-", 1)[0]
    return {
        "series_ticker": "KXMLBGAME",
        "event_ticker": event_ticker,
        "ticker": ticker,
        "title": title,
        "status": "open",
    }
