import json
from pathlib import Path

import pytest

from relative_value.reference_odds import (
    american_to_implied_probability,
    load_saved_reference_odds_rows,
    no_vig_probabilities,
    normalize_saved_reference_odds_file,
)


def test_american_to_implied_probability() -> None:
    assert american_to_implied_probability(-110) == pytest.approx(0.5238095)
    assert american_to_implied_probability(150) == pytest.approx(0.4)


def test_no_vig_probabilities_sum_to_one() -> None:
    probabilities = no_vig_probabilities({"A": -120, "B": 100})
    assert sum(probabilities.values()) == pytest.approx(1.0)
    assert set(probabilities) == {"A", "B"}


def test_no_vig_requires_two_outcomes() -> None:
    with pytest.raises(ValueError):
        no_vig_probabilities({"A": -120})


def _odds_event() -> list[dict]:
    return [
        {
            "id": "event-1",
            "sport_key": "baseball_mlb",
            "sport_title": "MLB",
            "commence_time": "2026-06-01T23:00:00Z",
            "home_team": "Boston Red Sox",
            "away_team": "New York Yankees",
            "bookmakers": [
                {
                    "key": "draftkings",
                    "title": "DraftKings",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Boston Red Sox", "price": -120},
                                {"name": "New York Yankees", "price": 110},
                            ],
                        },
                        {
                            "key": "spreads",
                            "outcomes": [
                                {"name": "Boston Red Sox", "price": -110, "point": -1.5},
                                {"name": "New York Yankees", "price": -110, "point": 1.5},
                            ],
                        },
                        {
                            "key": "totals",
                            "outcomes": [
                                {"name": "Over", "price": -105, "point": 8.5},
                                {"name": "Under", "price": -115, "point": 8.5},
                            ],
                        },
                    ],
                }
            ],
        }
    ]


def _write_odds_file(root: Path, payload: object) -> Path:
    path = root / "reports" / "manual_snapshots" / "the_odds_api" / "20260526" / "oddsapi_mlb_odds.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_parse_saved_h2h_odds(tmp_path: Path) -> None:
    path = _write_odds_file(
        tmp_path,
        {"captured_at": "2026-05-26T12:00:00+00:00", "raw_response": _odds_event()},
    )

    parsed = normalize_saved_reference_odds_file(path)

    assert parsed["event_count"] == 1
    h2h = [row for row in parsed["rows"] if row["market_type"] == "h2h"]
    assert len(h2h) == 2
    assert h2h[0]["venue"] == "the_odds_api"
    assert h2h[0]["reference_only"] is True
    assert h2h[0]["executable"] is False
    assert h2h[0]["captured_at"] == "2026-05-26T12:00:00+00:00"
    assert h2h[0]["no_vig_probability"] is not None


def test_parse_spreads_and_totals_with_point(tmp_path: Path) -> None:
    path = _write_odds_file(tmp_path, _odds_event())

    parsed = normalize_saved_reference_odds_file(path)
    spread = next(row for row in parsed["rows"] if row["market_type"] == "spreads")
    total = next(row for row in parsed["rows"] if row["market_type"] == "totals")

    assert spread["point"] == -1.5
    assert spread["line"] == -1.5
    assert total["point"] == 8.5
    assert total["line"] == 8.5


def test_saved_reference_odds_loader_uses_snapshot_glob(tmp_path: Path) -> None:
    _write_odds_file(tmp_path, _odds_event())

    payload = load_saved_reference_odds_rows(tmp_path / "reports")

    assert payload["files_read"] == 1
    assert payload["odds_events_read"] == 1
    assert len(payload["rows"]) == 6
