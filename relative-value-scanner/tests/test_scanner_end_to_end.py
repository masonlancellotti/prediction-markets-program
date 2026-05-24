import json
from pathlib import Path

from relative_value.models import Action
from relative_value.scanner import RelativeValueScanner
from scan import build_fixture_adapters, main


def test_fixture_end_to_end_scan_produces_conservative_actions(tmp_path: Path) -> None:
    result = main(["--output-dir", str(tmp_path)])
    assert result == 0

    json_path = tmp_path / "relative_value_candidates.json"
    md_path = tmp_path / "relative_value_candidates.md"
    assert json_path.exists()
    assert md_path.exists()

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    actions = [candidate["action"] for candidate in payload["candidates"]]
    assert actions
    assert payload["count"] == 7
    assert payload["provenance"]["data_source_mode"] == "STATIC_FIXTURE"
    assert payload["provenance"]["live_fetch_attempted"] is False
    assert payload["provenance"]["live_fetch_succeeded"] is False
    assert {source["source_id"] for source in payload["provenance"]["sources"]} == {
        "kalshi",
        "polymarket",
        "the_odds_api",
    }
    assert Action.POSSIBLE_ARB.value not in actions
    assert all("opposite_reference_outcome_inverted" not in candidate["reasons"] for candidate in payload["candidates"])
    for candidate in payload["candidates"]:
        if candidate["action"] in {Action.PAPER.value, Action.POSSIBLE_ARB.value}:
            assert "stale_quote" not in candidate["reasons"]
            assert "quote_freshness_unverified" not in candidate["reasons"]
    watch_or_manual = sum(action in {Action.WATCH.value, Action.MANUAL_REVIEW.value} for action in actions)
    assert watch_or_manual >= max(1, int(0.75 * len(actions)))

    all_candidates = RelativeValueScanner().scan_from_adapters(
        build_fixture_adapters(Path("venues") / "fixtures"),
        include_ignore=True,
    )
    opposite_fixture_candidates = [
        candidate
        for candidate in all_candidates
        if candidate.left.market_id == "KAL_FAKE_TOTAL_OVER_100"
        and candidate.right.market_id == "POLY_FAKE_TOTAL_UNDER_100"
    ]
    assert opposite_fixture_candidates
    assert opposite_fixture_candidates[0].action == Action.IGNORE
    assert any("opposite_polarity_detected" in reason for reason in opposite_fixture_candidates[0].match.reasons)


def test_default_scan_suppresses_redundant_opposite_reference_candidates() -> None:
    scanner = RelativeValueScanner()
    adapters = build_fixture_adapters(Path("venues") / "fixtures")
    default_candidates = scanner.scan_from_adapters(adapters)
    debug_candidates = scanner.scan_from_adapters(adapters, include_ignore=True)

    assert not any("opposite_reference_outcome_inverted" in candidate.reasons for candidate in default_candidates)
    assert any("opposite_reference_outcome_inverted" in candidate.reasons for candidate in debug_candidates)
    assert len(default_candidates) < len([candidate for candidate in debug_candidates if candidate.action != Action.IGNORE])
