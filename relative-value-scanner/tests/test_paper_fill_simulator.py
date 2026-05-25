from __future__ import annotations

import json
from datetime import datetime, timezone

from relative_value.fees import FlatFeeModel
from relative_value.paper_fill_simulator import simulate_paper_fill_journal, render_paper_fill_journal_markdown


NOW = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)


def _leg(ask: float = 0.30, depth: float = 5.0, captured_at: str = "2026-05-24T11:59:30+00:00", **ob) -> dict:
    payload = {
        "best_ask": ask,
        "depth_at_best_ask": depth,
        "midpoint": 0.01,
        "orderbook_captured_at": captured_at,
        "snapshot_id": "snap-1",
    }
    payload.update(ob)
    return {"venue": "kalshi", "side": "BUY_YES", "orderbook_enrichment": payload}


def _payload(row: dict) -> dict:
    return {"rows": [row]}


def _structural_row(**overrides) -> dict:
    row = {
        "source_candidate_id": "basket-1",
        "candidate_type": "structural_basket",
        "status": "STOP_FOR_REVIEW",
        "legs": [_leg(0.20), _leg(0.25), _leg(0.30)],
        "gross_payout_cents": 100.0,
    }
    row.update(overrides)
    return row


def _journal(row: dict, fee: float = 0.0, **kwargs) -> dict:
    return simulate_paper_fill_journal(
        input_payload=_payload(row),
        generated_at=NOW,
        fee_models={"kalshi": FlatFeeModel(fee)},
        **kwargs,
    )


def test_simulator_refuses_ungated_rows() -> None:
    journal = _journal(_structural_row(status="WATCH"))
    row = journal["journal"][0]

    assert row["status"] == "blocked"
    assert "ungated_structural_basket_row" in row["blockers"]
    assert journal["summary"]["simulated_fill_count"] == 0


def test_reference_only_row_cannot_be_simulated_as_gated_row() -> None:
    journal = _journal(_structural_row(reference_only=True))
    row = journal["journal"][0]

    assert row["status"] == "blocked"
    assert "reference_only_source" in row["blockers"]
    assert journal["summary"]["simulated_fill_count"] == 0


def test_no_midpoint_fills_are_used() -> None:
    journal = _journal(_structural_row(legs=[_leg(0.90, midpoint=0.01), _leg(0.05)]))
    row = journal["journal"][0]

    assert row["fill_prices"][0]["average_price"] == 0.90
    assert row["uses_midpoint"] is False
    assert all(fill["uses_midpoint"] is False for fill in row["fill_prices"])
    assert journal["safety"]["uses_midpoint"] is False


def test_depth_walking_uses_ladder_levels() -> None:
    journal = _journal(
        _structural_row(
            legs=[
                _leg(yes_asks=[[0.20, 0.4], [0.30, 0.6]]),
                _leg(0.25),
            ]
        )
    )
    row = journal["journal"][0]

    assert row["status"] == "paper_simulation"
    assert row["fill_prices"][0]["average_price"] == 0.26
    assert row["fill_prices"][0]["levels"] == [{"price": 0.2, "quantity": 0.4}, {"price": 0.3, "quantity": 0.6}]
    assert row["cumulative_depth_used"][0]["quantity"] == 1.0


def test_insufficient_depth_blocks_and_reduces_simulated_quantity() -> None:
    journal = _journal(_structural_row(legs=[_leg(yes_asks=[[0.20, 0.4]]), _leg(0.25)]))
    row = journal["journal"][0]

    assert row["status"] == "blocked"
    assert "leg_0_insufficient_executable_depth" in row["blockers"]
    assert row["simulated_quantity"] == 0.4


def test_fees_can_turn_apparent_positive_negative() -> None:
    journal = _journal(_structural_row(legs=[_leg(0.49), _leg(0.49)]), fee=0.02)
    row = journal["journal"][0]

    assert row["gross_cost_cents"] == 98.0
    assert row["conservative_fee_cents"] == 4.0
    assert row["conservative_net_edge_cents"] == -2.0
    assert "conservative_net_edge_not_positive" in row["blockers"]


def test_stale_quote_input_fails_closed() -> None:
    journal = _journal(_structural_row(legs=[_leg(0.20, captured_at="2026-05-24T10:00:00+00:00")]), max_quote_age_seconds=60.0)
    row = journal["journal"][0]

    assert row["status"] == "blocked"
    assert "leg_0_stale_quote" in row["blockers"]


def test_exact_rows_must_already_be_paper_candidate() -> None:
    blocked = _journal({"candidate_type": "exact_same_payoff", "action": "MANUAL_REVIEW", "legs": [_leg(0.20), _leg(0.25)]})
    allowed = _journal({"candidate_type": "exact_same_payoff", "action": "PAPER_CANDIDATE", "legs": [_leg(0.20), _leg(0.25)]})

    assert "ungated_exact_same_payoff_row" in blocked["journal"][0]["blockers"]
    assert allowed["journal"][0]["status"] == "paper_simulation"


def test_output_is_paper_only_and_avoids_execution_language() -> None:
    journal = _journal(_structural_row())
    encoded = json.dumps(journal).lower()
    markdown = render_paper_fill_journal_markdown(journal).lower()

    assert "paper_simulation" in encoded
    assert "simulated_fill" not in encoded or "simulated_fill_count" in encoded
    assert "review_only" in encoded
    assert "account" not in encoded
    assert "private" not in encoded
    assert "guaranteed" not in encoded
    assert "place order" not in encoded
    assert "live execution" not in encoded
    assert "place order" not in markdown
    assert journal["safety"]["paper_candidate_created"] is False
