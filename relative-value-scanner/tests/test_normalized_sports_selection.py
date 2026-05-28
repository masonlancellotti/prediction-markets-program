import json

from relative_value.normalized_sports_selection import SCHEMA_VERSION, NormalizedSportsSelection


def test_normalized_sports_selection_round_trips_through_to_dict() -> None:
    selection = NormalizedSportsSelection(
        venue="sx_bet",
        event_id="event-1",
        selection_id="selection-1",
        market_type="spread",
        participants=("Boston Celtics", "New York Knicks"),
        home_team="Boston Celtics",
        away_team="New York Knicks",
        odds_format="american",
        stake_payout_mechanics="sportsbook_reference_unreviewed",
        void_rules="fixture void rules",
        cancellation_rules="fixture cancellation rules",
        limits_max_stake=100.0,
        market_suspension_state="open",
        bet_acceptance_risk="unreviewed",
        region_restrictions="unreviewed",
        settlement_source="fixture source",
        fee_or_commission="fixture fee",
        odds_timestamp="2026-05-25T12:00:00Z",
        depth_or_max_stake=100.0,
        currency="USD",
        line=-2.5,
        threshold=-2.5,
        operator="handicap",
        payout_mechanics_class="sportsbook_selection",
        raw_evidence_paths=("fixture.json:$.markets[0]",),
    )

    payload = selection.to_dict()
    encoded = json.dumps(payload)

    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["venue"] == "sx_bet"
    assert payload["participants"] == ["Boston Celtics", "New York Knicks"]
    assert payload["raw_evidence_paths"] == ["fixture.json:$.markets[0]"]
    assert payload["diagnostic_only"] is True
    assert "PAPER_CANDIDATE" not in encoded


def test_normalized_sports_selection_is_diagnostic_only_by_default() -> None:
    selection = NormalizedSportsSelection()

    assert selection.diagnostic_only is True
    assert selection.to_dict()["diagnostic_only"] is True
