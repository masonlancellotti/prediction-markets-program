import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import scan
from relative_value.markout_replay import MarkoutReplayConfig, replay_paper_candidate_markouts


NOW = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)


def _ledger_payload() -> dict:
    return {
        "schema_version": 1,
        "source": "paper_candidate_evaluator",
        "generated_at": NOW.isoformat(),
        "inputs": {},
        "ledger_count": 1,
        "counts_by_action": {
            "WATCH": 0,
            "MANUAL_REVIEW": 1,
            "PAPER_CANDIDATE": 0,
        },
        "ledger": [
            {
                "schema_version": 1,
                "candidate_id": "poly-1__KXKNICKS",
                "detected_at": NOW.isoformat(),
                "action": "MANUAL_REVIEW",
                "opportunity_class": "near_equivalent_manual_review",
                "polymarket": {
                    "market_id": "poly-1",
                    "would_enter_side": "SELL_YES",
                    "would_enter_price": 0.66,
                },
                "kalshi": {
                    "ticker": "KXKNICKS",
                    "would_enter_side": "BUY_YES",
                    "would_enter_price": 0.60,
                },
                "gap": {
                    "gross_gap": 0.06,
                    "polymarket_fee": 0.0,
                    "kalshi_fee": 0.02,
                    "estimated_net_gap": 0.04,
                },
                "ineligibility_reasons": ["unit_mismatch_not_accepted"],
                "missed_fill_reason": "unit_mismatch_not_accepted",
                "markouts": {
                    "t_plus_30s": {"estimated_net_gap": None},
                    "t_plus_5m": {"estimated_net_gap": None},
                    "t_plus_30m": {"estimated_net_gap": None},
                    "t_plus_2h": {"estimated_net_gap": None},
                },
                "disclaimer": "Read-only paper candidate ledger.",
            }
        ],
        "disclaimer": "Read-only paper candidate ledger.",
    }


def _polymarket_later(captured_at: str = "2026-05-20T12:00:30+00:00", **enrichment_overrides) -> dict:
    enrichment = {
        "orderbook_captured_at": captured_at,
        "best_bid": 0.64,
        "best_ask": 0.66,
        "depth_at_best_bid": 10.0,
        "depth_at_best_ask": 9.0,
        "enrichment_status": "enriched",
        "enrichment_warnings": [],
    }
    enrichment.update(enrichment_overrides)
    return {
        "schema_version": 1,
        "source": "polymarket_gamma",
        "captured_at": captured_at,
        "market_count": 1,
        "normalized_count": 1,
        "normalized_markets": [
            {
                "venue": "polymarket",
                "market_id": "poly-1",
                "question": "Will the New York Knicks win?",
                "orderbook_enrichment": enrichment,
            }
        ],
    }


def _kalshi_later(captured_at: str = "2026-05-20T12:00:30+00:00", **enrichment_overrides) -> dict:
    enrichment = {
        "orderbook_captured_at": captured_at,
        "best_bid": 0.58,
        "best_ask": 0.61,
        "depth_at_best_bid": 7.0,
        "depth_at_best_ask": 8.0,
        "enrichment_status": "enriched",
        "enrichment_warnings": [],
    }
    enrichment.update(enrichment_overrides)
    return {
        "schema_version": 1,
        "source": "kalshi_markets",
        "captured_at": captured_at,
        "market_count": 1,
        "normalized_count": 1,
        "normalized_markets": [
            {
                "venue": "kalshi",
                "market_id": "KXKNICKS",
                "ticker": "KXKNICKS",
                "question": "Will the New York Knicks win?",
                "orderbook_enrichment": enrichment,
            }
        ],
    }


def _replay(
    *,
    ledger: dict | None = None,
    poly: dict | None = None,
    kalshi: dict | None = None,
    tolerance: float = 10.0,
) -> dict:
    return replay_paper_candidate_markouts(
        ledger_payload=ledger or _ledger_payload(),
        polymarket_later_payload=poly or _polymarket_later(),
        kalshi_later_payload=kalshi or _kalshi_later(),
        replayed_at=NOW,
        config=MarkoutReplayConfig(window_tolerance_seconds=tolerance),
    )


def test_fills_correct_window_when_later_snapshot_is_within_tolerance() -> None:
    payload = _replay()
    row = payload["ledger"][0]

    markout = row["markouts"]["t_plus_30s"]
    assert markout["markout_status"] == "filled"
    assert markout["later_polymarket_quote_captured_at"] == "2026-05-20T12:00:30+00:00"
    assert markout["later_kalshi_quote_captured_at"] == "2026-05-20T12:00:30+00:00"
    assert markout["later_polymarket_best_bid"] == 0.64
    assert markout["later_polymarket_best_ask"] == 0.66
    assert markout["later_kalshi_best_bid"] == 0.58
    assert markout["later_kalshi_best_ask"] == 0.61
    assert markout["later_gross_gap"] == pytest.approx(0.03)
    assert markout["later_polymarket_fee"] == pytest.approx(0.01152)
    assert markout["later_kalshi_fee"] == 0.02
    assert markout["later_estimated_net_gap"] == pytest.approx(-0.00152)
    assert markout["change_in_estimated_net_gap"] == pytest.approx(-0.04152)
    assert markout["spread_closed_boolean"] is True


def test_leaves_other_windows_null_when_snapshot_is_too_early() -> None:
    payload = _replay()

    for window in ("t_plus_5m", "t_plus_30m", "t_plus_2h"):
        markout = payload["ledger"][0]["markouts"][window]
        assert markout["markout_status"] == "no_data"
        assert markout["later_estimated_net_gap"] is None
        assert markout["spread_closed_boolean"] is None


def test_no_midpoint_use_in_markout_gap() -> None:
    poly = _polymarket_later(best_bid=0.50, best_ask=0.90)
    kalshi = _kalshi_later(best_bid=0.49, best_ask=0.51)

    markout = _replay(poly=poly, kalshi=kalshi)["ledger"][0]["markouts"]["t_plus_30s"]

    assert markout["markout_status"] == "filled"
    assert markout["later_gross_gap"] == pytest.approx(-0.01)
    assert markout["later_estimated_net_gap"] == pytest.approx(-0.0425)
    assert markout["spread_closed_boolean"] is True


def test_missing_later_market_produces_missing_market() -> None:
    poly = _polymarket_later()
    poly["normalized_markets"] = []

    markouts = _replay(poly=poly)["ledger"][0]["markouts"]

    assert {markout["markout_status"] for markout in markouts.values()} == {"missing_market"}


def test_stale_later_quote_produces_stale() -> None:
    payload = _replay(
        poly=_polymarket_later("2026-05-20T12:02:01+00:00"),
        kalshi=_kalshi_later("2026-05-20T12:02:01+00:00"),
        tolerance=30.0,
    )

    assert payload["ledger"][0]["markouts"]["t_plus_30s"]["markout_status"] == "stale"
    assert payload["ledger"][0]["markouts"]["t_plus_30s"]["later_estimated_net_gap"] is None


def test_missing_later_orderbook_produces_missing_orderbook() -> None:
    poly = _polymarket_later(enrichment_status="unenriched", best_bid=None, best_ask=None)

    markouts = _replay(poly=poly)["ledger"][0]["markouts"]

    assert {markout["markout_status"] for markout in markouts.values()} == {"missing_orderbook"}


def test_net_gap_uses_same_default_fee_config_as_evaluator() -> None:
    markout = _replay()["ledger"][0]["markouts"]["t_plus_30s"]

    assert markout["later_polymarket_fee"] == pytest.approx(0.01152)
    assert markout["later_kalshi_fee"] == 0.02
    assert markout["later_estimated_net_gap"] == pytest.approx(
        markout["later_gross_gap"] - markout["later_polymarket_fee"] - markout["later_kalshi_fee"]
    )


def test_payload_actions_do_not_add_paper_or_possible_arb() -> None:
    payload = _replay()

    actions = {row["action"] for row in payload["ledger"]}
    assert "PAPER" not in actions
    assert "POSSIBLE_ARB" not in actions
    assert '"PAPER"' not in json.dumps(payload)
    assert "POSSIBLE_ARB" not in json.dumps(payload)


def test_inputs_are_deep_copied_and_not_mutated() -> None:
    ledger = _ledger_payload()
    poly = _polymarket_later()
    kalshi = _kalshi_later()
    before = (copy.deepcopy(ledger), copy.deepcopy(poly), copy.deepcopy(kalshi))

    _replay(ledger=ledger, poly=poly, kalshi=kalshi)

    assert (ledger, poly, kalshi) == before


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_replay_markouts_cli_reads_saved_files_only(tmp_path: Path, capsys) -> None:
    output = tmp_path / "marked.json"

    result = scan.main(
        [
            "replay-paper-candidate-markouts",
            "--ledger",
            str(_write(tmp_path / "ledger.json", _ledger_payload())),
            "--polymarket-enriched-later",
            str(_write(tmp_path / "poly_later.json", _polymarket_later())),
            "--kalshi-enriched-later",
            str(_write(tmp_path / "kalshi_later.json", _kalshi_later())),
            "--output",
            str(output),
            "--window-tolerance-seconds",
            "10",
        ]
    )

    assert result == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["markout_replay"]["counts_by_status"]["filled"] == 1
    assert payload["markout_replay"]["counts_by_status"]["no_data"] == 3
    output_text = capsys.readouterr().out
    assert "paper_candidate_markout_replay_status=OK candidates=1 windows=4 filled=1" in output_text
