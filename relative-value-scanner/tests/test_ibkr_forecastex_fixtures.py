from __future__ import annotations

import json
from pathlib import Path

from venues.ibkr_forecastex import IBKR_FORECASTEX_RESEARCH_SCHEMA_KIND, load_ibkr_forecastex_research_fixtures


def test_ibkr_forecastex_saved_fixtures_remain_research_only() -> None:
    root = Path(__file__).parents[1]
    snapshot = load_ibkr_forecastex_research_fixtures(
        instruments_path=root / "venues" / "fixtures" / "ibkr_forecastex_instruments_sample.json",
        quotes_path=root / "venues" / "fixtures" / "ibkr_forecastex_quotes_sample.json",
        settlement_path=root / "venues" / "fixtures" / "ibkr_forecastex_settlement_sample.json",
    )

    assert snapshot["schema_kind"] == IBKR_FORECASTEX_RESEARCH_SCHEMA_KIND
    assert snapshot["source_id"] == "forecastex_ibkr"
    assert snapshot["live_fetch_attempted"] is False
    assert snapshot["execution_allowed_in_project_now"] is False
    assert snapshot["can_create_candidate_pair"] is False
    assert snapshot["can_create_paper_candidate"] is False
    assert snapshot["research_market_count"] >= 1
    assert "PAPER_CANDIDATE" not in json.dumps(snapshot)
