import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError

import pytest
import scan
from relative_value.orderbook_enrichment import enrich_orderbook_snapshot
from relative_value.paper_candidate_evaluator import ACTION_MANUAL_REVIEW, ACTION_PAPER_CANDIDATE, ACTION_WATCH
from venues.orderbooks import (
    KalshiOrderbookClient,
    PolymarketOrderbookClient,
    parse_kalshi_orderbook_metrics,
    parse_polymarket_orderbook_metrics,
)


NOW = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)


def _kalshi_snapshot() -> dict:
    return {
        "schema_version": 1,
        "source": "kalshi_markets",
        "captured_at": "2026-05-20T11:30:00+00:00",
        "market_count": 1,
        "normalized_count": 1,
        "normalized_markets": [
            {
                "venue": "kalshi",
                "market_id": "KXTEST-YES",
                "ticker": "KXTEST-YES",
                "question": "Will the test happen?",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "active": True,
                "closed": False,
            }
        ],
    }


def _polymarket_snapshot(include_token_ids: bool = True) -> dict:
    raw = {
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.43", "0.57"]',
    }
    if include_token_ids:
        raw["clobTokenIds"] = '["yes-token", "no-token"]'
    return {
        "schema_version": 1,
        "source": "polymarket_gamma",
        "captured_at": "2026-05-20T11:30:00+00:00",
        "market_count": 1,
        "normalized_count": 1,
        "normalized_markets": [
            {
                "venue": "polymarket",
                "market_id": "poly-test",
                "question": "Will the test happen?",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "active": True,
                "closed": False,
                "raw": raw,
            }
        ],
    }


class FakeKalshiOrderbookClient:
    def endpoint_for(self, ticker: str) -> str:
        return f"https://example.test/markets/{ticker}/orderbook"

    def fetch_orderbook(self, ticker: str) -> dict:
        assert ticker == "KXTEST-YES"
        return {
            "orderbook": {
                "yes": [[0.42, 10], [0.40, 20]],
                "no": [[0.56, 7], [0.53, 5]],
            }
        }


class FakePolymarketOrderbookClient:
    def endpoint_for(self, token_id: str) -> str:
        return f"https://example.test/book?token_id={token_id}"

    def fetch_orderbook(self, token_id: str) -> dict:
        assert token_id == "yes-token"
        return {
            "bids": [{"price": "0.42", "size": "10"}, {"price": "0.40", "size": "20"}],
            "asks": [{"price": "0.44", "size": "7"}, {"price": "0.47", "size": "5"}],
        }


class _FakeResponse:
    def __init__(self, payload: str) -> None:
        self.payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self.payload.encode("utf-8")


def test_parse_kalshi_orderbook_binary_yes_space_depth() -> None:
    metrics = parse_kalshi_orderbook_metrics(
        {
            "orderbook": {
                "yes": [[0.42, 10], [0.40, 20]],
                "no": [[0.56, 7], [0.53, 5]],
            }
        },
        captured_at=NOW,
        source_endpoint="https://example.test/kalshi",
    )

    assert metrics["best_bid"] == 0.42
    assert metrics["best_ask"] == 0.44
    assert metrics["spread"] == 0.02
    assert metrics["depth_at_best_bid"] == 10
    assert metrics["depth_at_best_ask"] == 7
    assert metrics["depth_within_3c"] == {"bid": 30, "ask": 12, "total": 42}
    assert metrics["enrichment_status"] == "enriched"


def test_parse_polymarket_orderbook_depth() -> None:
    metrics = parse_polymarket_orderbook_metrics(
        {
            "bids": [{"price": "0.42", "size": "10"}, {"price": "0.40", "size": "20"}],
            "asks": [{"price": "0.44", "size": "7"}, {"price": "0.47", "size": "5"}],
        },
        captured_at=NOW,
        source_endpoint="https://example.test/poly",
    )

    assert metrics["best_bid"] == 0.42
    assert metrics["best_ask"] == 0.44
    assert metrics["depth_within_1c"] == {"bid": 10, "ask": 7, "total": 17}
    assert metrics["depth_within_5c"] == {"bid": 30, "ask": 12, "total": 42}


def test_kalshi_orderbook_enrichment_preserves_rows_and_adds_metrics() -> None:
    enriched = enrich_orderbook_snapshot(
        _kalshi_snapshot(),
        venue="kalshi",
        captured_at=NOW,
        kalshi_client=FakeKalshiOrderbookClient(),
        polymarket_client=FakePolymarketOrderbookClient(),
    )

    row = enriched["normalized_markets"][0]
    assert row["ticker"] == "KXTEST-YES"
    assert row["orderbook_enrichment"]["best_bid"] == 0.42
    assert row["orderbook_enrichment"]["enrichment_status"] == "enriched"
    assert enriched["orderbook_enrichment"]["enriched_count"] == 1


def test_polymarket_orderbook_enrichment_uses_yes_token_id() -> None:
    enriched = enrich_orderbook_snapshot(
        _polymarket_snapshot(),
        venue="polymarket",
        captured_at=NOW,
        kalshi_client=FakeKalshiOrderbookClient(),
        polymarket_client=FakePolymarketOrderbookClient(),
    )

    row = enriched["normalized_markets"][0]
    assert row["orderbook_enrichment"]["source_endpoint"].endswith("token_id=yes-token")
    assert row["orderbook_enrichment"]["depth_at_best_ask"] == 7
    assert enriched["orderbook_enrichment"]["enriched_count"] == 1


def test_polymarket_missing_token_id_is_unenriched_without_guessing() -> None:
    enriched = enrich_orderbook_snapshot(
        _polymarket_snapshot(include_token_ids=False),
        venue="polymarket",
        captured_at=NOW,
        kalshi_client=FakeKalshiOrderbookClient(),
        polymarket_client=FakePolymarketOrderbookClient(),
    )

    enrichment = enriched["normalized_markets"][0]["orderbook_enrichment"]
    assert enrichment["enrichment_status"] == "unenriched"
    assert enrichment["enrichment_warnings"] == ["missing_token_id"]


def test_stale_snapshot_marks_market_unenriched() -> None:
    snapshot = _kalshi_snapshot()
    snapshot["captured_at"] = "2026-05-18T11:30:00+00:00"
    snapshot["normalized_markets"][0]["best_bid"] = 0.42
    snapshot["normalized_markets"][0]["best_ask"] = 0.44

    enriched = enrich_orderbook_snapshot(
        snapshot,
        venue="kalshi",
        captured_at=NOW,
        max_snapshot_age_hours=24,
        kalshi_client=FakeKalshiOrderbookClient(),
        polymarket_client=FakePolymarketOrderbookClient(),
    )

    enrichment = enriched["normalized_markets"][0]["orderbook_enrichment"]
    assert enrichment["enrichment_status"] == "unenriched"
    assert enrichment["enrichment_warnings"] == ["stale_snapshot"]
    summary = enriched["orderbook_enrichment"]
    assert summary["fresh_orderbook_fetch_enriched_count"] == 0
    assert summary["existing_top_of_book_present_count"] == 1
    assert summary["full_orderbook_missing_count"] == 1
    assert summary["stale_existing_top_of_book_count"] == 1


def test_kalshi_orderbook_client_url_contract_and_user_agent(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["user_agent"] = request.get_header("User-agent")
        captured["timeout"] = timeout
        return _FakeResponse('{"orderbook": {"yes": [], "no": []}}')

    monkeypatch.setattr("venues.orderbooks.urlopen", fake_urlopen)

    result = KalshiOrderbookClient(base_url="https://example.test/trade-api/v2", timeout_seconds=4).fetch_orderbook("KXTEST")

    assert result == {"orderbook": {"yes": [], "no": []}}
    assert captured["url"].endswith("/markets/KXTEST/orderbook")
    assert captured["method"] == "GET"
    assert captured["user_agent"] == "relative-value-scanner/0.1 read-only"
    assert captured["timeout"] == 4


def test_polymarket_orderbook_client_url_contract_and_user_agent(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["user_agent"] = request.get_header("User-agent")
        captured["timeout"] = timeout
        return _FakeResponse('{"bids": [], "asks": []}')

    monkeypatch.setattr("venues.orderbooks.urlopen", fake_urlopen)

    result = PolymarketOrderbookClient(base_url="https://example.test", timeout_seconds=4).fetch_orderbook("token-1")

    assert result == {"bids": [], "asks": []}
    assert captured["url"].endswith("/book?token_id=token-1")
    assert captured["method"] == "GET"
    assert captured["user_agent"] == "relative-value-scanner/0.1 read-only"
    assert captured["timeout"] == 4


def test_orderbook_clients_surface_http_errors(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        raise HTTPError(request.full_url, 404, "Not Found", hdrs=None, fp=None)

    monkeypatch.setattr("venues.orderbooks.urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="Kalshi orderbook API returned HTTP 404"):
        KalshiOrderbookClient().fetch_orderbook("KXTEST")
    with pytest.raises(RuntimeError, match="Polymarket CLOB orderbook API returned HTTP 404"):
        PolymarketOrderbookClient().fetch_orderbook("token")


def test_enrich_orderbooks_cli_uses_saved_json_without_network(monkeypatch, tmp_path: Path, capsys) -> None:
    snapshot_path = tmp_path / "kalshi_snapshot.json"
    output = tmp_path / "enriched.json"
    snapshot_path.write_text(json.dumps(_kalshi_snapshot()), encoding="utf-8")

    def fake_enrich_orderbook_snapshot_file(**kwargs):
        assert kwargs["snapshot_path"] == snapshot_path
        payload = _kalshi_snapshot()
        payload["orderbook_enrichment"] = {
            "market_count": 1,
            "enriched_count": 1,
            "unenriched_count": 0,
            "fresh_orderbook_fetch_enriched_count": 1,
            "existing_top_of_book_present_count": 1,
            "full_orderbook_missing_count": 0,
            "fetch_failed_count": 0,
            "stale_existing_top_of_book_count": 0,
        }
        kwargs["output_path"].write_text(json.dumps(payload), encoding="utf-8")
        return payload

    monkeypatch.setattr(scan, "enrich_orderbook_snapshot_file", fake_enrich_orderbook_snapshot_file)

    result = scan.main(
        [
            "enrich-orderbooks",
            "--snapshot",
            str(snapshot_path),
            "--venue",
            "kalshi",
            "--output",
            str(output),
        ]
    )

    assert result == 0
    assert output.exists()
    stdout = capsys.readouterr().out
    assert "orderbook_enrichment_status=OK venue=kalshi markets=1 enriched=1 unenriched=0" in stdout
    assert "fresh_orderbook_fetch_enriched=1" in stdout
    assert "existing_top_of_book_present=1" in stdout
    assert "full_orderbook_missing=0" in stdout


def _paper_check_pairs() -> dict:
    return {
        "schema_version": 1,
        "source": "mlb_world_series_saved_pair_generator_v1",
        "pair_count": 1,
        "pairs": [
            {
                "action": "WATCH",
                "polymarket": {"market_id": "poly-ws", "question": "Will Team win the World Series?"},
                "kalshi": {"ticker": "KXMLB-TEAM", "question": "Will Team win the Pro Baseball Championship?"},
                "ineligibility_reasons": [],
            }
        ],
    }


def _paper_check_snapshot(venue: str) -> dict:
    market_id = "poly-ws" if venue == "polymarket" else "KXMLB-TEAM"
    row = {
        "venue": venue,
        "market_id": market_id,
        "question": "Will Team win the championship?",
        "outcomes": [{"name": "Yes"}, {"name": "No"}],
        "active": True,
        "closed": False,
    }
    if venue == "polymarket":
        row["raw"] = {"outcomes": '["Yes", "No"]', "clobTokenIds": '["yes-token", "no-token"]'}
    else:
        row["ticker"] = market_id
    return {
        "schema_version": 1,
        "source": f"{venue}_snapshot",
        "captured_at": "2026-05-20T11:59:00+00:00",
        "market_count": 1,
        "normalized_count": 1,
        "normalized_markets": [row],
    }


def _paper_check_enriched(venue: str) -> dict:
    payload = _paper_check_snapshot(venue)
    payload["normalized_markets"][0]["orderbook_enrichment"] = {
        "orderbook_captured_at": "2026-05-20T12:00:00+00:00",
        "best_bid": 0.55,
        "best_ask": 0.57,
        "depth_at_best_bid": 10,
        "depth_at_best_ask": 11,
        "enrichment_status": "enriched",
        "enrichment_warnings": [],
    }
    payload["orderbook_enrichment"] = {
        "market_count": 1,
        "enriched_count": 1,
        "unenriched_count": 0,
        "fresh_orderbook_fetch_enriched_count": 1,
        "existing_top_of_book_present_count": 0,
        "full_orderbook_missing_count": 0,
        "fetch_failed_count": 0,
        "stale_existing_top_of_book_count": 0,
        "snapshot_warnings": [],
    }
    return payload


def _install_paper_check_fakes(monkeypatch, *, action: str = ACTION_WATCH, missed_fill_reason: str | None = None) -> list[str]:
    calls: list[str] = []

    def fake_enrich_orderbook_snapshot_file(**kwargs):
        calls.append(f"enrich:{kwargs['venue']}")
        payload = _paper_check_enriched(kwargs["venue"])
        kwargs["output_path"].write_text(json.dumps(payload), encoding="utf-8")
        return payload

    def fake_build_same_payoff_board_files(**kwargs):
        calls.append("board")
        payload = {
            "schema_version": 1,
            "strict_same_payoff_pass_count": 1,
            "row_count": 1,
            "rows": [],
            "counts_by_recommended_next_action": {},
        }
        kwargs["json_output_path"].write_text(json.dumps(payload), encoding="utf-8")
        kwargs["markdown_output_path"].write_text("# board\n", encoding="utf-8")
        return payload

    def fake_attach_same_payoff_evidence_files(pairs_path, board_path, output_path):
        calls.append("attach")
        payload = _paper_check_pairs()
        payload["same_payoff_evidence_attachment"] = {
            "trusted_relationship_attached_count": 1,
            "pair_count": 1,
            "diagnostic_evidence_attached_count": 0,
            "ambiguous_identity_count": 0,
            "unmatched_pair_count": 0,
        }
        output_path.write_text(json.dumps(payload), encoding="utf-8")
        return payload

    def fake_evaluate_paper_candidate_files(**kwargs):
        calls.append("evaluate")
        counts = {ACTION_PAPER_CANDIDATE: 0, ACTION_MANUAL_REVIEW: 0, ACTION_WATCH: 0}
        counts[action] = 1
        reasons = ["polymarket_stale_quote"] if missed_fill_reason == "stale_or_missing_quote_time" else []
        payload = {
            "schema_version": 1,
            "source": "paper_candidate_evaluator",
            "generated_at": "2026-05-20T12:00:00+00:00",
            "ledger_count": 1,
            "counts_by_action": counts,
            "ledger": [
                {
                    "candidate_id": "poly-ws__KXMLB-TEAM",
                    "action": action,
                    "missed_fill_reason": missed_fill_reason,
                    "ineligibility_reasons": reasons,
                    "polymarket": {"quote_captured_at": "2026-05-20T11:00:00+00:00"},
                    "kalshi": {"quote_captured_at": "2026-05-20T11:59:30+00:00"},
                    "gap": {},
                }
            ],
        }
        kwargs["output_path"].write_text(json.dumps(payload), encoding="utf-8")
        return payload

    monkeypatch.setattr(scan, "enrich_orderbook_snapshot_file", fake_enrich_orderbook_snapshot_file)
    monkeypatch.setattr(scan, "build_same_payoff_board_files", fake_build_same_payoff_board_files)
    monkeypatch.setattr(scan, "attach_same_payoff_evidence_files", fake_attach_same_payoff_evidence_files)
    monkeypatch.setattr(scan, "evaluate_paper_candidate_files", fake_evaluate_paper_candidate_files)
    return calls


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_mlb_world_series_paper_check_requires_explicit_inputs(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        scan.main(["run-mlb-world-series-paper-check"])

    assert exc.value.code == 2
    stderr = capsys.readouterr().err
    assert "--polymarket-snapshot" in stderr
    assert "--kalshi-snapshot" in stderr
    assert "--pairs" in stderr


def test_nba_championship_paper_check_requires_explicit_inputs(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        scan.main(["run-nba-championship-paper-check"])

    assert exc.value.code == 2
    stderr = capsys.readouterr().err
    assert "--polymarket-snapshot" in stderr
    assert "--kalshi-snapshot" in stderr
    assert "--pairs" in stderr


def test_default_scan_remains_static_fixture(capsys) -> None:
    result = scan.main([])

    assert result == 0
    assert "data_source_mode=STATIC_FIXTURE" in capsys.readouterr().out


def test_nba_championship_paper_check_stop_and_review_for_paper_candidate(monkeypatch, tmp_path: Path, capsys) -> None:
    _install_paper_check_fakes(monkeypatch, action=ACTION_PAPER_CANDIDATE)
    poly_path = _write(tmp_path / "poly_snapshot.json", _paper_check_snapshot("polymarket"))
    kalshi_path = _write(tmp_path / "kalshi_snapshot.json", _paper_check_snapshot("kalshi"))
    pairs_path = _write(tmp_path / "pairs.json", _paper_check_pairs())

    result = scan.main(
        [
            "run-nba-championship-paper-check",
            "--polymarket-snapshot",
            str(poly_path),
            "--kalshi-snapshot",
            str(kalshi_path),
            "--pairs",
            str(pairs_path),
            "--summary-json-output",
            str(tmp_path / "summary.json"),
            "--summary-markdown-output",
            str(tmp_path / "summary.md"),
        ]
    )

    assert result == 0
    stdout = capsys.readouterr().out
    assert "nba_championship_paper_check_status=OK" in stdout
    assert "paper=1" in stdout
    assert "STOP_AND_REVIEW" in stdout
    assert "no_trading_or_execution_performed=true" in stdout
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["source"] == "nba_championship_paper_check_runner"
    assert summary["safety"]["trading_or_execution_performed"] is False


def test_nba_championship_paper_check_reports_stale_quote_warning(monkeypatch, tmp_path: Path, capsys) -> None:
    _install_paper_check_fakes(monkeypatch, action=ACTION_WATCH, missed_fill_reason="stale_or_missing_quote_time")
    poly_path = _write(tmp_path / "poly_snapshot.json", _paper_check_snapshot("polymarket"))
    kalshi_path = _write(tmp_path / "kalshi_snapshot.json", _paper_check_snapshot("kalshi"))
    pairs_path = _write(tmp_path / "pairs.json", _paper_check_pairs())

    result = scan.main(
        [
            "run-nba-championship-paper-check",
            "--polymarket-snapshot",
            str(poly_path),
            "--kalshi-snapshot",
            str(kalshi_path),
            "--pairs",
            str(pairs_path),
            "--max-quote-age-seconds",
            "1800",
            "--summary-json-output",
            str(tmp_path / "summary.json"),
            "--summary-markdown-output",
            str(tmp_path / "summary.md"),
        ]
    )

    assert result == 0
    stdout = capsys.readouterr().out
    assert "dominant_blocker=" in stdout
    assert "stale" in stdout
    assert "stale_quote_warning=true" in stdout
    assert "STALE_QUOTE_WARNING" in stdout


def test_mlb_world_series_paper_check_composes_steps_without_mutating_inputs(monkeypatch, tmp_path: Path, capsys) -> None:
    calls = _install_paper_check_fakes(monkeypatch, action=ACTION_MANUAL_REVIEW)
    poly_path = tmp_path / "poly_snapshot.json"
    kalshi_path = tmp_path / "kalshi_snapshot.json"
    pairs_path = tmp_path / "pairs.json"
    poly_path.write_text(json.dumps(_paper_check_snapshot("polymarket"), sort_keys=True), encoding="utf-8")
    kalshi_path.write_text(json.dumps(_paper_check_snapshot("kalshi"), sort_keys=True), encoding="utf-8")
    pairs_path.write_text(json.dumps(_paper_check_pairs(), sort_keys=True), encoding="utf-8")
    before_hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (poly_path, kalshi_path, pairs_path)}

    result = scan.main(
        [
            "run-mlb-world-series-paper-check",
            "--polymarket-snapshot",
            str(poly_path),
            "--kalshi-snapshot",
            str(kalshi_path),
            "--pairs",
            str(pairs_path),
            "--polymarket-enriched-output",
            str(tmp_path / "poly_enriched.json"),
            "--kalshi-enriched-output",
            str(tmp_path / "kalshi_enriched.json"),
            "--board-json-output",
            str(tmp_path / "board.json"),
            "--board-markdown-output",
            str(tmp_path / "board.md"),
            "--derived-pairs-output",
            str(tmp_path / "derived_pairs.json"),
            "--evaluator-output",
            str(tmp_path / "evaluator.json"),
            "--summary-json-output",
            str(tmp_path / "summary.json"),
            "--summary-markdown-output",
            str(tmp_path / "summary.md"),
            "--accept-unit-mismatch",
            "--trust-settlement-normalization",
            "mlb_world_series_timezone_convention_drift",
        ]
    )

    assert result == 0
    assert calls == ["enrich:polymarket", "enrich:kalshi", "board", "attach", "evaluate"]
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before_hashes} == before_hashes
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["evaluator_counts"] == {ACTION_PAPER_CANDIDATE: 0, ACTION_MANUAL_REVIEW: 1, ACTION_WATCH: 0}
    assert summary["strict_same_payoff_passes"] == 1
    assert summary["trusted_relationships"] == 1
    stdout = capsys.readouterr().out
    assert "manual_review=1" in stdout
    assert "watch=0" in stdout
    assert "paper=0" in stdout


def test_mlb_world_series_paper_check_stop_and_review_for_paper_candidate(monkeypatch, tmp_path: Path, capsys) -> None:
    _install_paper_check_fakes(monkeypatch, action=ACTION_PAPER_CANDIDATE)
    poly_path = _write(tmp_path / "poly_snapshot.json", _paper_check_snapshot("polymarket"))
    kalshi_path = _write(tmp_path / "kalshi_snapshot.json", _paper_check_snapshot("kalshi"))
    pairs_path = _write(tmp_path / "pairs.json", _paper_check_pairs())

    result = scan.main(
        [
            "run-mlb-world-series-paper-check",
            "--polymarket-snapshot",
            str(poly_path),
            "--kalshi-snapshot",
            str(kalshi_path),
            "--pairs",
            str(pairs_path),
            "--summary-json-output",
            str(tmp_path / "summary.json"),
            "--summary-markdown-output",
            str(tmp_path / "summary.md"),
        ]
    )

    assert result == 0
    stdout = capsys.readouterr().out
    assert "paper=1" in stdout
    assert "STOP_AND_REVIEW" in stdout
    assert "no_trading_or_execution_performed=true" in stdout
    assert "poly-ws__KXMLB-TEAM" in stdout


def test_mlb_world_series_paper_check_reports_stale_quote_warning(monkeypatch, tmp_path: Path, capsys) -> None:
    _install_paper_check_fakes(monkeypatch, action=ACTION_WATCH, missed_fill_reason="stale_or_missing_quote_time")
    poly_path = _write(tmp_path / "poly_snapshot.json", _paper_check_snapshot("polymarket"))
    kalshi_path = _write(tmp_path / "kalshi_snapshot.json", _paper_check_snapshot("kalshi"))
    pairs_path = _write(tmp_path / "pairs.json", _paper_check_pairs())

    result = scan.main(
        [
            "run-mlb-world-series-paper-check",
            "--polymarket-snapshot",
            str(poly_path),
            "--kalshi-snapshot",
            str(kalshi_path),
            "--pairs",
            str(pairs_path),
            "--max-quote-age-seconds",
            "1800",
            "--summary-json-output",
            str(tmp_path / "summary.json"),
            "--summary-markdown-output",
            str(tmp_path / "summary.md"),
        ]
    )

    assert result == 0
    stdout = capsys.readouterr().out
    assert "stale_quote_warning=true" in stdout
    assert "STALE_QUOTE_WARNING" in stdout
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["quote_freshness"]["stale_quote_warning"] is True


def test_mlb_world_series_paper_check_help_shows_required_arguments(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        scan.main(["run-mlb-world-series-paper-check", "--help"])

    assert exc.value.code == 0
    stdout = capsys.readouterr().out
    assert "--polymarket-snapshot POLYMARKET_SNAPSHOT" in stdout
    assert "--kalshi-snapshot KALSHI_SNAPSHOT" in stdout
    assert "--pairs PAIRS" in stdout
