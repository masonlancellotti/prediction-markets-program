from __future__ import annotations

from argparse import Namespace

import pytest

from scan import _load_snapshot_mode
from graph_engine.snapshot_loader import (
    NoUsableSnapshotsFound,
    SnapshotLoadError,
    load_schema_v1_snapshots,
)
from tests.conftest import PROJECT_ROOT


SNAPSHOT_FIXTURES = PROJECT_ROOT / "tests" / "fixtures"


def test_schema_v1_snapshot_loader_reads_explicit_paths() -> None:
    snapshot, metadata = load_schema_v1_snapshots(
        snapshot_paths=[
            SNAPSHOT_FIXTURES / "schema_v1_snapshot_polymarket.json",
            SNAPSHOT_FIXTURES / "schema_v1_snapshot_kalshi.json",
        ]
    )

    assert snapshot.snapshot_id == "saved-schema-v1-snapshot-20260520T140500Z"
    assert len(snapshot.nodes) == 3
    assert snapshot.edges == []
    assert snapshot.exclusion_sets == []
    assert snapshot.nodes["polymarket:openai_value_over_1t_2027"].yes_price == 0.48
    assert snapshot.nodes["kalshi:msft-ai-revenue-leader-2027"].themes == ["ai", "revenue"]
    assert {item["venue"] for item in metadata} == {"polymarket", "kalshi"}


def test_schema_v1_snapshot_loader_reads_directory_and_ignores_other_json() -> None:
    snapshot, metadata = load_schema_v1_snapshots(snapshots_dir=SNAPSHOT_FIXTURES)

    assert len(snapshot.nodes) == 3
    assert len(metadata) == 2


def test_schema_v1_snapshot_loader_validates_schema_version_for_explicit_path(tmp_path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(
        """
{
  "schema_version": 2,
  "as_of": "2026-05-20T14:00:00+00:00",
  "venue": "polymarket",
  "normalized_markets": []
}
""",
        encoding="utf-8",
    )

    with pytest.raises(SnapshotLoadError, match="schema_version"):
        load_schema_v1_snapshots(snapshot_paths=[bad])


def test_schema_v1_snapshot_loader_validates_normalized_markets_list(tmp_path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(
        """
{
  "schema_version": 1,
  "as_of": "2026-05-20T14:00:00+00:00",
  "venue": "polymarket",
  "normalized_markets": {}
}
""",
        encoding="utf-8",
    )

    with pytest.raises(SnapshotLoadError, match="normalized_markets"):
        load_schema_v1_snapshots(snapshot_paths=[bad])


def test_schema_v1_snapshot_loader_requires_source_or_venue_per_row(tmp_path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(
        """
{
  "schema_version": 1,
  "as_of": "2026-05-20T14:00:00+00:00",
  "normalized_markets": [
    {
      "market_id": "missing_venue",
      "title": "Missing venue",
      "yes_price": 0.4
    }
  ]
}
""",
        encoding="utf-8",
    )

    with pytest.raises(SnapshotLoadError, match="source/venue"):
        load_schema_v1_snapshots(snapshot_paths=[bad])


def test_schema_v1_snapshot_loader_reports_no_usable_directory(tmp_path) -> None:
    (tmp_path / "report.json").write_text('{"schema_version": 99}', encoding="utf-8")

    with pytest.raises(NoUsableSnapshotsFound):
        load_schema_v1_snapshots(snapshots_dir=tmp_path)


def test_scan_snapshot_mode_falls_back_to_fixtures_when_no_usable_snapshots(tmp_path, capsys) -> None:
    (tmp_path / "report.json").write_text('{"schema_version": 99}', encoding="utf-8")

    snapshot, metadata, mode = _load_snapshot_mode(
        Namespace(snapshots_dir=tmp_path, snapshot_file=[])
    )

    captured = capsys.readouterr().out
    assert "falling back to bundled fixtures" in captured
    assert mode == "fixtures"
    assert len(snapshot.nodes) == 35
    assert len(snapshot.edges) == 14
    assert len(metadata) == 9
