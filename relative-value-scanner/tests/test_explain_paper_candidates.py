import json
from pathlib import Path

import pytest

import scan


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _candidate(candidate_id: str, action: str, missed_fill_reason=None) -> dict:
    return {
        "schema_version": 1,
        "candidate_id": candidate_id,
        "action": action,
        "opportunity_class": "strict_cross_venue_equivalent" if action == "PAPER_CANDIDATE" else "ineligible",
        "polymarket": {
            "market_id": f"poly-{candidate_id}",
            "question": "Will Team A win?",
            "venue": "polymarket",
            "would_enter_side": "BUY_YES",
            "would_enter_price": 0.44,
            "best_bid": 0.42,
            "best_ask": 0.44,
            "depth_at_best_bid": 50.0,
            "depth_at_best_ask": 40.0,
        },
        "kalshi": {
            "ticker": f"KX{candidate_id.upper()}",
            "question": "Will Team A win?",
            "venue": "kalshi",
            "would_enter_side": "SELL_YES",
            "would_enter_price": 0.49,
            "best_bid": 0.49,
            "best_ask": 0.51,
            "depth_at_best_bid": 25.0,
            "depth_at_best_ask": 20.0,
        },
        "gap": {
            "gross_gap": 0.05,
            "polymarket_fee": 0.0,
            "kalshi_fee": 0.01,
            "estimated_net_gap": 0.04,
            "settlement_delta_seconds": 1800.0,
            "size_unit_warning": "polymarket_shares_vs_kalshi_contracts_not_normalized",
        },
        "missed_fill_reason": missed_fill_reason,
        "ineligibility_reasons": [missed_fill_reason] if missed_fill_reason else [],
        "markouts": {
            "t_plus_30s": {"estimated_net_gap": None},
            "t_plus_5m": {"markout_status": "filled", "estimated_net_gap": 0.02},
        },
    }


def _ledger_payload() -> dict:
    return {
        "schema_version": 1,
        "source": "paper_candidate_evaluator",
        "ledger_count": 2,
        "ledger": [
            _candidate("watch-1", "WATCH", "no_positive_bid_ask_gap"),
            _candidate("paper-1", "PAPER_CANDIDATE"),
        ],
    }


def test_explain_paper_candidates_prints_sorted_candidate_blocks(tmp_path: Path, capsys) -> None:
    path = tmp_path / "paper_candidates.json"
    _write(path, _ledger_payload())

    result = scan.main(["explain-paper-candidates", "--ledger", str(path)])

    assert result == 0
    output = capsys.readouterr().out
    assert output.index("Candidate: paper-1") < output.index("Candidate: watch-1")
    assert "Paper candidate ledger explanation: research review only; PAPER_CANDIDATE is not a trade signal." in output
    assert "action: PAPER_CANDIDATE" in output
    assert "opportunity_class: strict_cross_venue_equivalent" in output
    assert "Polymarket: market_id=poly-paper-1 question=Will Team A win? venue=polymarket" in output
    assert "Kalshi: ticker=KXPAPER-1 question=Will Team A win? venue=kalshi" in output
    assert "would_enter: side=BUY_YES price=0.44" in output
    assert "quote: best_bid=0.42 best_ask=0.44" in output
    assert "depth: best_bid=50.0 best_ask=40.0" in output
    assert "gross_gap: 0.05" in output
    assert "polymarket_fee: 0.0" in output
    assert "kalshi_fee: 0.01" in output
    assert "estimated_net_gap: 0.04" in output
    assert "settlement_delta_seconds: 1800.0" in output
    assert "size_unit_warning: polymarket_shares_vs_kalshi_contracts_not_normalized" in output
    assert "missed_fill_reason: no_positive_bid_ask_gap" in output
    assert "ineligibility_reasons: no_positive_bid_ask_gap" in output
    assert "markouts: t_plus_30s:placeholder, t_plus_5m:filled" in output
    assert "explain_paper_candidates_status=OK candidates_shown=2" in output
    lower_output = output.lower()
    assert "profit" not in lower_output
    assert "executable" not in lower_output


def test_explain_paper_candidates_action_filter(tmp_path: Path, capsys) -> None:
    path = tmp_path / "paper_candidates.json"
    _write(path, _ledger_payload())

    result = scan.main(["explain-paper-candidates", "--ledger", str(path), "--action", "PAPER_CANDIDATE"])

    assert result == 0
    output = capsys.readouterr().out
    assert "Candidate: paper-1" in output
    assert "Candidate: watch-1" not in output
    assert "candidates_shown=1" in output


def test_explain_paper_candidates_limit(tmp_path: Path, capsys) -> None:
    path = tmp_path / "paper_candidates.json"
    _write(path, _ledger_payload())

    result = scan.main(["explain-paper-candidates", "--ledger", str(path), "--limit", "1"])

    assert result == 0
    output = capsys.readouterr().out
    assert "Candidate: paper-1" in output
    assert "Candidate: watch-1" not in output
    assert "candidates_shown=1" in output


def test_explain_paper_candidates_missing_file_returns_clean_failure(tmp_path: Path, capsys) -> None:
    path = tmp_path / "missing.json"

    result = scan.main(["explain-paper-candidates", "--ledger", str(path)])

    assert result == 1
    output = capsys.readouterr().out
    assert "explain_paper_candidates_status=FAILED" in output
    assert "paper_candidates file not found" in output


@pytest.mark.parametrize(
    ("payload_update", "message"),
    [
        ({"schema_version": 2}, "schema_version must be 1"),
        ({"source": "other"}, "source must be paper_candidate_evaluator"),
    ],
)
def test_explain_paper_candidates_invalid_schema_or_source_returns_clean_failure(
    tmp_path: Path,
    capsys,
    payload_update: dict,
    message: str,
) -> None:
    path = tmp_path / "bad.json"
    payload = _ledger_payload()
    payload.update(payload_update)
    _write(path, payload)

    result = scan.main(["explain-paper-candidates", "--ledger", str(path)])

    assert result == 1
    output = capsys.readouterr().out
    assert "explain_paper_candidates_status=FAILED" in output
    assert message in output
