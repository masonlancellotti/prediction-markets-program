from __future__ import annotations

import json
from pathlib import Path

import pytest

from graph_engine.relationships.rv_edge_taxonomy import make_rv_edge
from graph_engine.reporting.rv_review_worklist import (
    ALLOWED_WORKLIST_ACTIONS,
    build_rv_review_worklist_report,
    render_rv_review_worklist_markdown,
    validate_rv_review_worklist_report,
    write_rv_review_worklist_report,
)
from graph_engine.reporting.safety import find_prohibited_rendered_text


def _fake_edges_report(tmp_path: Path, include_ibkr: bool = False) -> Path:
    edges = [
        make_rv_edge(
            edge_id="rv-edge:basis-1",
            left_market_id="kalshi:KXBTC-26MAY2207-T68200",
            right_market_id="polymarket:1299974",
            left_venue="kalshi",
            right_venue="polymarket",
            relationship_type="DEADLINE_TOUCH_VS_POINT_IN_TIME",
            evidence_fields={
                "kalshi_market_id": "KXBTC-26MAY2207-T68200",
                "kalshi_target_date": "2026-05-22",
                "polymarket_market_id": "1299974",
            },
        ),
        make_rv_edge(
            edge_id="rv-edge:near-exact-1",
            left_market_id="kalshi:KXETH-26MAY2207-T1380",
            right_market_id="cdna:eth-22",
            left_venue="kalshi",
            right_venue="cdna",
            relationship_type="SAME_EVENT_DIFFERENT_SOURCE_REVIEW",
            evidence_fields={
                "kalshi_market_id": "KXETH-26MAY2207-T1380",
                "kalshi_target_date": "2026-05-22",
                "cdna_threshold": 2022.0,
            },
        ),
        make_rv_edge(
            edge_id="rv-edge:title-only-1",
            left_market_id="kalshi:KXNFL-26-1",
            right_market_id="polymarket:nfl-1",
            left_venue="kalshi",
            right_venue="polymarket",
            relationship_type="TITLE_SIMILARITY_ONLY",
            evidence_fields={"polymarket_market_id": "nfl-1"},
        ),
        make_rv_edge(
            edge_id="rv-edge:no-peer-1",
            left_market_id="polymarket:lonely",
            right_market_id=None,
            right_reference_id="manual_discovery_required",
            left_venue="polymarket",
            right_venue="manual_discovery",
            relationship_type="NO_CURRENT_PEER",
        ),
    ]
    if include_ibkr:
        edges.append(
            make_rv_edge(
                edge_id="rv-edge:ibkr-1",
                left_market_id="ibkr_forecastex:779027722",
                right_market_id="kalshi:KXFED-26JUN-T2.75",
                left_venue="ibkr_forecastex",
                right_venue="kalshi",
                relationship_type="SAME_EVENT_SAME_THRESHOLD_REVIEW",
                evidence_fields={"lane": "IBKR_FORECASTX_FED_FOMC_vs_KALSHI_FED_FOMC"},
            )
        )
    payload = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "edges": edges,
    }
    path = tmp_path / "edges.json"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def test_worklist_excludes_title_and_no_peer(tmp_path: Path) -> None:
    edges_path = _fake_edges_report(tmp_path)
    report = build_rv_review_worklist_report(edges_report_path=edges_path)
    edge_ids = {row["edge_id"] for row in report["rows"]}
    assert "rv-edge:title-only-1" not in edge_ids
    assert "rv-edge:no-peer-1" not in edge_ids
    assert "rv-edge:basis-1" in edge_ids
    assert "rv-edge:near-exact-1" in edge_ids


def test_worklist_excludes_queued_ibkr_by_default(tmp_path: Path) -> None:
    edges_path = _fake_edges_report(tmp_path, include_ibkr=True)
    report = build_rv_review_worklist_report(edges_report_path=edges_path)
    assert "rv-edge:ibkr-1" not in {row["edge_id"] for row in report["rows"]}
    included = build_rv_review_worklist_report(
        edges_report_path=edges_path, include_queued_ibkr=True
    )
    assert "rv-edge:ibkr-1" in {row["edge_id"] for row in included["rows"]}


def test_worklist_actions_only_use_allowed_set(tmp_path: Path) -> None:
    edges_path = _fake_edges_report(tmp_path)
    report = build_rv_review_worklist_report(edges_report_path=edges_path)
    actions = {row["allowed_next_action"] for row in report["rows"]}
    assert actions.issubset(set(ALLOWED_WORKLIST_ACTIONS))


def test_worklist_writes_markdown_with_no_prohibited_vocab(tmp_path: Path) -> None:
    edges_path = _fake_edges_report(tmp_path)
    json_out = tmp_path / "worklist.json"
    md_out = tmp_path / "worklist.md"
    report = write_rv_review_worklist_report(
        edges_report_path=edges_path,
        json_output=json_out,
        markdown_output=md_out,
    )
    text = md_out.read_text(encoding="utf-8")
    assert "# RV Review Worklist" in text
    assert find_prohibited_rendered_text(text) == []
    validate_rv_review_worklist_report(report)


def test_worklist_empty_when_input_missing(tmp_path: Path) -> None:
    report = build_rv_review_worklist_report(
        edges_report_path=tmp_path / "missing.json"
    )
    assert report["rows"] == []
    assert report["summary"]["total_rows"] == 0
    assert report["inputs"]["missing_input_report"] is True


def test_worklist_markdown_rendering_is_safe(tmp_path: Path) -> None:
    edges_path = _fake_edges_report(tmp_path)
    report = build_rv_review_worklist_report(edges_report_path=edges_path)
    md = render_rv_review_worklist_markdown(report)
    assert find_prohibited_rendered_text(md) == []
