from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from graph_engine.models import GraphSnapshot, RelationshipEdge, RelationshipType
from graph_engine.reporting.stale_lag_watchlist import (
    FRESHNESS_BUCKETS,
    FRESHNESS_BUCKET_FRESH,
    FRESHNESS_BUCKET_MAYBE_STALE,
    FRESHNESS_BUCKET_MISSING_TIMESTAMP,
    FRESHNESS_BUCKET_STALE,
    FRESHNESS_BUCKET_UNIFORM_SUSPICIOUS,
    UNIFORM_TIMESTAMP_BLOCKER,
    build_stale_lag_watchlist_report,
    validate_stale_lag_watchlist_report,
    write_stale_lag_watchlist_report,
)
from graph_engine.reporting.schema_validation import SchemaValidationError
from tests.conftest import make_node


BASE_TIME = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)


def _node(market_id: str, probability: float, *, age_seconds: int, raw: dict | None = None, bid=None, ask=None):
    return make_node(
        market_id,
        probability,
        bid=bid,
        ask=ask,
        as_of=BASE_TIME - timedelta(seconds=age_seconds),
        raw=raw or {},
    )


def _edge(src: str, dst: str) -> RelationshipEdge:
    return RelationshipEdge(
        edge_id=f"edge_{src.replace(':', '_')}_{dst.replace(':', '_')}",
        src_market_id=src,
        dst_market_id=dst,
        relation=RelationshipType.SUBSET,
        confidence=0.95,
        source="fixture",
        rationale="deterministic fixture relation",
        evidence=["fixture"],
        created_at="2026-05-25T12:00:00+00:00",
        reviewed_by="fixture-reviewer",
    )


def _snapshot(nodes, edges=None) -> GraphSnapshot:
    return GraphSnapshot(
        snapshot_id="stale-lag-test",
        as_of=BASE_TIME,
        nodes={node.market_id: node for node in nodes},
        edges=list(edges or []),
    )


def _first_row(report: dict) -> dict:
    assert report["stale_lag_watchlist"]
    return report["stale_lag_watchlist"][0]


def test_only_quote_age_does_not_create_watch_row() -> None:
    stale = _node("test:stale", 0.50, age_seconds=3601)
    fresh = _node("test:fresh", 0.55, age_seconds=60)
    report = build_stale_lag_watchlist_report(_snapshot([stale, fresh], [_edge(stale.market_id, fresh.market_id)]))
    row = _first_row(report)

    assert report["stale_lag_watch_count"] == 0
    assert row["deterministic_lag_evidence"] is False
    assert "probability_delta_below_threshold" in row["blockers"]
    validate_stale_lag_watchlist_report(report)


def test_only_price_delta_does_not_create_watch_row() -> None:
    stale = _node("test:left", 0.30, age_seconds=120)
    fresh = _node("test:right", 0.55, age_seconds=60)
    report = build_stale_lag_watchlist_report(_snapshot([stale, fresh], [_edge(stale.market_id, fresh.market_id)]))
    row = _first_row(report)

    assert report["stale_lag_watch_count"] == 0
    assert row["probability_delta"] == 0.25
    assert "timestamp_skew_below_threshold" in row["blockers"]
    assert UNIFORM_TIMESTAMP_BLOCKER in row["blockers"]
    assert report["uniform_timestamps_blocked_count"] == 1


def test_uniform_fixture_timestamps_trigger_blocker_and_summary_count() -> None:
    left = _node("test:left", 0.30, age_seconds=1800)
    right = _node("test:right", 0.55, age_seconds=1800)
    report = build_stale_lag_watchlist_report(_snapshot([left, right], [_edge(left.market_id, right.market_id)]))
    row = _first_row(report)

    assert report["stale_lag_watch_count"] == 0
    assert report["uniform_timestamps_blocked_count"] == 1
    assert "timestamp_skew_below_threshold" in row["blockers"]
    assert UNIFORM_TIMESTAMP_BLOCKER in row["blockers"]


def test_timestamp_skew_price_delta_and_deterministic_relation_create_watch_row() -> None:
    stale = _node("test:stale", 0.30, age_seconds=3601)
    fresh = _node("test:fresh", 0.55, age_seconds=60)
    report = build_stale_lag_watchlist_report(_snapshot([stale, fresh], [_edge(stale.market_id, fresh.market_id)]))
    row = _first_row(report)

    assert report["stale_lag_watch_count"] == 1
    assert report["stale_lag_blocked_count"] == 0
    assert row["markets_involved"] == ["test:stale", "test:fresh"]
    assert row["quote_age_seconds"] == 3601
    assert row["related_market_quote_age_seconds"] == 60
    assert row["probability_delta"] == 0.25
    assert row["deterministic_lag_evidence"] is True
    assert row["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    assert UNIFORM_TIMESTAMP_BLOCKER not in row["blockers"]
    assert report["uniform_timestamps_blocked_count"] == 0


def test_same_fixture_declared_family_can_create_watch_row() -> None:
    stale = _node("test:family_stale", 0.30, age_seconds=3601, raw={"stale_lag_family": "btc_june"})
    fresh = _node("test:family_fresh", 0.55, age_seconds=60, raw={"stale_lag_family": "btc_june"})
    report = build_stale_lag_watchlist_report(_snapshot([stale, fresh]))
    row = _first_row(report)

    assert report["stale_lag_watch_count"] == 1
    assert row["deterministic_lag_evidence"] is True
    assert row["blockers"] == []


def test_missing_related_market_creates_blocker() -> None:
    stale = _node(
        "test:stale",
        0.30,
        age_seconds=3601,
        raw={"stale_lag_related_market_id": "test:missing", "stale_lag_family": "test-family"},
    )
    report = build_stale_lag_watchlist_report(_snapshot([stale]))
    row = _first_row(report)

    assert report["stale_lag_watch_count"] == 0
    assert row["deterministic_lag_evidence"] is False
    assert "missing_related_market" in row["blockers"]


def test_missing_timestamps_create_blockers() -> None:
    stale = _node("test:stale", 0.30, age_seconds=3601, raw={"quote_timestamp_missing": True})
    fresh = _node("test:fresh", 0.55, age_seconds=60)
    report = build_stale_lag_watchlist_report(_snapshot([stale, fresh], [_edge(stale.market_id, fresh.market_id)]))
    row = _first_row(report)

    assert report["stale_lag_watch_count"] == 0
    assert row["deterministic_lag_evidence"] is False
    assert "missing_quote_timestamp" in row["blockers"]


def test_llm_only_stale_lag_is_cowitness_not_watch() -> None:
    left = _node("test:left", 0.30, age_seconds=3601)
    right = _node("test:right", 0.55, age_seconds=60)
    llm_report = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "validated_hypotheses": [
            {
                "hypothesis_id": "hyp:stale",
                "relationship_type": "STALE_OR_LAG_HYPOTHESIS",
                "source_market_ids": ["test:left", "test:right"],
            }
        ],
    }
    report = build_stale_lag_watchlist_report(_snapshot([left, right]), llm_hypotheses_report=llm_report)
    row = _first_row(report)

    assert report["stale_lag_watch_count"] == 0
    assert row["llm_stale_lag_cowitness"] is True
    assert row["deterministic_lag_evidence"] is False
    assert "missing_deterministic_relationship_evidence" in row["blockers"]


def test_synthetic_yes_price_or_midpoint_input_is_blocked_non_actionable() -> None:
    stale = _node("test:stale", 0.50, age_seconds=3601, bid=0.48, ask=0.52)
    fresh = _node("test:fresh", 0.75, age_seconds=60)
    report = build_stale_lag_watchlist_report(_snapshot([stale, fresh], [_edge(stale.market_id, fresh.market_id)]))
    row = _first_row(report)

    assert report["stale_lag_watch_count"] == 0
    assert row["probability_delta"] == 0.25
    assert row["deterministic_lag_evidence"] is False
    assert "non_actionable_probability_input" in row["blockers"]


def test_report_writes_json_and_markdown(tmp_path) -> None:
    stale = _node("test:stale", 0.30, age_seconds=3601)
    fresh = _node("test:fresh", 0.55, age_seconds=60)
    json_output = tmp_path / "market_graph_stale_lag_watchlist.json"
    markdown_output = tmp_path / "market_graph_stale_lag_watchlist.md"

    report = write_stale_lag_watchlist_report(
        _snapshot([stale, fresh], [_edge(stale.market_id, fresh.market_id)]),
        json_output,
        markdown_output,
    )

    assert json.loads(json_output.read_text(encoding="utf-8")) == report
    markdown = markdown_output.read_text(encoding="utf-8")
    assert "# Market Graph Stale Lag Watchlist" in markdown
    assert "Uniform-timestamp blocked rows" in markdown
    assert "## Freshness buckets" in markdown
    validate_stale_lag_watchlist_report(report)


def test_freshness_buckets_classify_per_row_and_aggregate() -> None:
    # Deterministic WATCH row is stale-bucket because the worst quote exceeds
    # the configured stale_seconds threshold.
    deterministic_stale = _node("test:stale_left", 0.30, age_seconds=3601)
    deterministic_fresh = _node("test:fresh_right", 0.55, age_seconds=60)
    # Maybe-stale pair: both ages below stale threshold but worst > related_fresh.
    maybe_stale_left = _node(
        "test:maybe_left",
        0.30,
        age_seconds=900,
        raw={"stale_lag_family": "test-maybe"},
    )
    maybe_stale_right = _node(
        "test:maybe_right",
        0.32,
        age_seconds=10,
        raw={"stale_lag_family": "test-maybe"},
    )
    # Missing timestamp via explicit quote_timestamp_missing.
    missing_left = _node(
        "test:missing_left",
        0.40,
        age_seconds=3601,
        raw={"quote_timestamp_missing": True, "stale_lag_family": "test-missing"},
    )
    missing_right = _node(
        "test:missing_right",
        0.10,
        age_seconds=60,
        raw={"stale_lag_family": "test-missing"},
    )
    # Uniform-timestamp suspicious pair (skew <= 60s).
    uniform_left = _node(
        "test:uniform_left",
        0.20,
        age_seconds=1200,
        raw={"stale_lag_family": "test-uniform"},
    )
    uniform_right = _node(
        "test:uniform_right",
        0.80,
        age_seconds=1240,
        raw={"stale_lag_family": "test-uniform"},
    )
    snapshot = _snapshot(
        [
            deterministic_stale,
            deterministic_fresh,
            maybe_stale_left,
            maybe_stale_right,
            missing_left,
            missing_right,
            uniform_left,
            uniform_right,
        ],
        [_edge(deterministic_stale.market_id, deterministic_fresh.market_id)],
    )

    report = build_stale_lag_watchlist_report(snapshot)
    buckets = report["freshness_buckets"]

    assert set(buckets) == set(FRESHNESS_BUCKETS)
    assert buckets[FRESHNESS_BUCKET_STALE] >= 1
    assert buckets[FRESHNESS_BUCKET_MAYBE_STALE] >= 1
    assert buckets[FRESHNESS_BUCKET_UNIFORM_SUSPICIOUS] >= 1
    assert buckets[FRESHNESS_BUCKET_MISSING_TIMESTAMP] >= 1
    total_rows = len(report["stale_lag_watchlist"])
    assert sum(buckets.values()) == total_rows
    # Every uniform-timestamp blocked row must collapse to the suspicious bucket
    # so operators do not see a fresh/maybe-stale/stale label on noise.
    for row in report["stale_lag_watchlist"]:
        if UNIFORM_TIMESTAMP_BLOCKER in row["blockers"]:
            assert row["freshness_bucket"] == FRESHNESS_BUCKET_UNIFORM_SUSPICIOUS
    # Every deterministic WATCH row must be in the stale bucket so the headline
    # and the freshness label agree.
    for row in report["stale_lag_watchlist"]:
        if row["deterministic_lag_evidence"] is True:
            assert row["freshness_bucket"] == FRESHNESS_BUCKET_STALE
    validate_stale_lag_watchlist_report(report)


def test_fresh_pair_with_no_signals_falls_in_fresh_bucket() -> None:
    left = _node(
        "test:fresh_a",
        0.10,
        age_seconds=120,
        raw={"stale_lag_family": "test-fresh"},
    )
    right = _node(
        "test:fresh_b",
        0.90,
        age_seconds=200,
        raw={"stale_lag_family": "test-fresh"},
    )

    report = build_stale_lag_watchlist_report(_snapshot([left, right]))
    row = _first_row(report)

    assert row["freshness_bucket"] == FRESHNESS_BUCKET_FRESH
    assert report["freshness_buckets"][FRESHNESS_BUCKET_FRESH] == 1
    validate_stale_lag_watchlist_report(report)


def test_freshness_bucket_mismatch_with_deterministic_evidence_fails_validation() -> None:
    stale = _node("test:stale", 0.30, age_seconds=3601)
    fresh = _node("test:fresh", 0.55, age_seconds=60)
    report = build_stale_lag_watchlist_report(
        _snapshot([stale, fresh], [_edge(stale.market_id, fresh.market_id)])
    )
    # Tamper with the freshness bucket: a deterministic WATCH row must remain
    # in the stale bucket. Forcing it to ``fresh`` should be rejected.
    report["stale_lag_watchlist"][0]["freshness_bucket"] = FRESHNESS_BUCKET_FRESH
    report["freshness_buckets"][FRESHNESS_BUCKET_STALE] -= 1
    report["freshness_buckets"][FRESHNESS_BUCKET_FRESH] += 1

    with pytest.raises(SchemaValidationError):
        validate_stale_lag_watchlist_report(report)
