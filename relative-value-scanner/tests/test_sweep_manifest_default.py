import pytest

import scan


DEFAULT_MANIFEST = scan.PROJECT_ROOT / "reports" / "sweep_manifests" / "default.json"


def test_default_sweep_manifest_loads_and_targets_are_valid() -> None:
    universes = scan._load_sweep_manifest(DEFAULT_MANIFEST)

    assert len(universes) >= 4
    labels = [row["label"] for row in universes]
    assert len(labels) == len(set(labels))
    for row in universes:
        scan._validate_pipeline_target(
            row.get("polymarket_tag_slug"),
            row.get("polymarket_tag_id"),
            row.get("kalshi_series_ticker"),
            row.get("kalshi_event_ticker"),
        )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"universes": []}, "version must be 1"),
        ({"version": 2, "universes": []}, "version must be 1"),
        ({"version": 1, "universes": []}, "universes list must not be empty"),
        (
            {"version": 1, "universes": [{"polymarket_tag_slug": "nba", "kalshi_series_ticker": "KXNBA"}]},
            "non-empty label",
        ),
        (
            {
                "version": 1,
                "universes": [
                    {"label": "bad label", "polymarket_tag_slug": "nba", "kalshi_series_ticker": "KXNBA"}
                ],
            },
            "label at index 0 is invalid",
        ),
        (
            {
                "version": 1,
                "universes": [
                    {"label": "nba_kxnba", "polymarket_tag_slug": "nba", "kalshi_series_ticker": "KXNBA"},
                    {"label": "nba_kxnba", "polymarket_tag_slug": "nba", "kalshi_series_ticker": "KXNBA"},
                ],
            },
            "duplicate label: nba_kxnba",
        ),
        (
            {"version": 1, "universes": [{"label": "nba_kxnba", "kalshi_series_ticker": "KXNBA"}]},
            "provide --polymarket-tag-slug and/or --polymarket-tag-id",
        ),
        (
            {"version": 1, "universes": [{"label": "nba_kxnba", "polymarket_tag_slug": "nba"}]},
            "provide --kalshi-series-ticker and/or --kalshi-event-ticker",
        ),
    ],
)
def test_sweep_manifest_validator_rejects_invalid_payloads(payload: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        scan._validate_sweep_manifest_structure(payload)
