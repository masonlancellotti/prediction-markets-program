from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from relative_value.kalshi_event_metadata import KALSHI_EVENT_METADATA_EVIDENCE_SOURCE
from relative_value.structural_basket_detector import STATUS_STOP_FOR_REVIEW
from relative_value.structural_basket_dry_run import (
    DRY_RUN_SOURCE,
    METADATA_IMPORT_SOURCE,
    PAPER_SIMULATION_SKIP_DISABLED_BY_CALLER,
    PAPER_SIMULATION_SKIP_NO_STOP_FOR_REVIEW,
    import_kalshi_event_metadata_file,
    import_kalshi_event_metadata_files,
    render_dry_run_summary_markdown,
    render_metadata_importer_markdown,
    run_structural_basket_dry_run,
    run_structural_basket_dry_run_files,
)


NOW = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kalshi_event_metadata"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _load(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _e2e_snapshot() -> dict:
    return _load("e2e_snapshot.json")


def _e2e_metadata() -> dict:
    return _load("e2e_event_metadata.json")


def _run_e2e(
    *,
    snapshot: dict | None = None,
    metadata: list[dict] | None = None,
    max_quote_age_seconds: float = 1800.0,
    min_depth: float = 1.0,
    desired_quantity: float = 1.0,
    simulate: bool = True,
) -> dict:
    return run_structural_basket_dry_run(
        snapshot_payload=snapshot if snapshot is not None else _e2e_snapshot(),
        metadata_payloads=metadata if metadata is not None else [_e2e_metadata()],
        snapshot_path="e2e_snapshot.json",
        metadata_source_paths=["e2e_event_metadata.json"],
        generated_at=NOW,
        max_quote_age_seconds=max_quote_age_seconds,
        min_depth=min_depth,
        desired_quantity=desired_quantity,
        simulate_paper_fills_on_stop_for_review=simulate,
    )


# ---------------------------------------------------------------------------
# Happy path: e2e fixture surfaces STOP_FOR_REVIEW and runs paper simulation
# ---------------------------------------------------------------------------


def test_dry_run_on_e2e_fixture_reaches_stop_for_review_and_simulates_paper_fills() -> None:
    report = _run_e2e()
    summary = report["summary"]

    assert report["source"] == DRY_RUN_SOURCE
    assert summary["metadata_events"] == 1
    assert summary["trusted_metadata_events"] == 1
    assert summary["matched_events"] == 1
    assert summary["trusted_after_join_events"] == 1
    assert summary["enriched_normalized_market_rows"] == 3
    assert summary["structural_groups_evaluated"] == 1
    assert summary["stop_for_review_count"] == 1
    assert summary["paper_simulation_skipped"] is False
    assert summary["paper_simulation_skip_reason"] is None
    assert summary["paper_fill_rows"] == 1
    # Paper fills can be either simulated or blocked, but they MUST have been
    # invoked (paper_fill_journal is present). Either outcome is acceptable here;
    # what matters is that the gate-protected simulator is the only path that
    # produced a paper-fill row and that no PAPER_CANDIDATE was promoted.
    assert summary["paper_candidate_count"] == 0
    assert report["paper_fill_journal"] is not None
    # Always: paper_candidate is never created.
    assert report["paper_fill_journal"]["safety"]["paper_candidate_created"] is False

    structural_row = report["structural_basket_report"]["rows"][0]
    assert structural_row["status"] == STATUS_STOP_FOR_REVIEW
    assert structural_row["evidence"]["source"] == KALSHI_EVENT_METADATA_EVIDENCE_SOURCE
    assert structural_row["evidence"]["venue_native"] is True
    assert structural_row["paper_candidate_emitted"] is False


def test_dry_run_summary_markdown_includes_safety_and_top_blockers_table() -> None:
    report = _run_e2e()
    rendered = render_dry_run_summary_markdown(report)

    assert "Structural Basket Dry Run Summary" in rendered
    assert "Saved-file-only" in rendered
    assert "STOP_FOR_REVIEW" in rendered
    assert "review/report-only" in rendered.lower() or "review-only" in rendered.lower()
    assert "places_orders: false" in rendered
    assert "paper_candidate_emitted: false" in rendered
    assert "uses_midpoint: false" in rendered
    assert "uses_title_similarity_for_exhaustiveness: false" in rendered
    assert "uses_graph_hints_for_exhaustiveness: false" in rendered
    assert "uses_count_only_evidence: false" in rendered
    # Counts surface verbatim.
    assert "stop_for_review_count: 1" in rendered
    assert "enriched_normalized_market_rows: 3" in rendered


# ---------------------------------------------------------------------------
# Skip-when-no-STOP_FOR_REVIEW paths
# ---------------------------------------------------------------------------


def test_dry_run_skips_paper_simulation_when_no_metadata_provided() -> None:
    report = _run_e2e(metadata=[])
    summary = report["summary"]

    assert summary["metadata_events"] == 0
    assert summary["trusted_metadata_events"] == 0
    assert summary["enriched_normalized_market_rows"] == 0
    assert summary["structural_groups_evaluated"] == 0
    assert summary["stop_for_review_count"] == 0
    assert summary["paper_simulation_skipped"] is True
    assert summary["paper_simulation_skip_reason"] == PAPER_SIMULATION_SKIP_NO_STOP_FOR_REVIEW
    assert report["paper_fill_journal"] is None


def test_dry_run_skips_paper_simulation_when_metadata_is_reference_only() -> None:
    metadata = dict(_e2e_metadata())
    metadata["reference_only"] = True
    report = _run_e2e(metadata=[metadata])
    summary = report["summary"]

    assert summary["trusted_metadata_events"] == 0
    assert summary["trusted_after_join_events"] == 0
    assert summary["enriched_normalized_market_rows"] == 0
    assert summary["stop_for_review_count"] == 0
    assert summary["paper_simulation_skipped"] is True
    assert summary["paper_simulation_skip_reason"] == PAPER_SIMULATION_SKIP_NO_STOP_FOR_REVIEW


def test_dry_run_skips_paper_simulation_when_metadata_is_title_only() -> None:
    """Title-only metadata may not be promoted to completeness — even if a
    snapshot exists for an event with a matching title. The dry-run must
    stop before paper simulation."""
    metadata = {
        "title": "Title-only event",
        "outcome_list": ["A", "B", "C"],
        "complete": True,
        "rules_primary": "rule",
        "settlement_source_raw_evidence": "source",
    }
    snapshot = {"events": [{"title": "Title-only event", "markets": []}]}
    report = run_structural_basket_dry_run(
        snapshot_payload=snapshot,
        metadata_payloads=[metadata],
        generated_at=NOW,
    )
    summary = report["summary"]

    assert summary["trusted_metadata_events"] == 0
    assert summary["enriched_normalized_market_rows"] == 0
    assert summary["stop_for_review_count"] == 0
    assert summary["paper_simulation_skipped"] is True
    assert summary["paper_simulation_skip_reason"] == PAPER_SIMULATION_SKIP_NO_STOP_FOR_REVIEW


def test_dry_run_skips_paper_simulation_when_only_per_market_yes_no_outcomes_exist() -> None:
    metadata = {
        "event_ticker": "KXBTC-26MAY",
        "event_id": "kxbtc-26may",
        "outcomes": ["Yes", "No"],
        "complete": True,
        "rules_primary": "threshold rule",
        "settlement_source_raw_evidence": "cf benchmarks btc index",
        "markets": [
            {"market_ticker": "KXBTC-26MAY-T86000", "outcomes": ["Yes", "No"]},
            {"market_ticker": "KXBTC-26MAY-T87000", "outcomes": ["Yes", "No"]},
        ],
    }
    snapshot = {
        "events": [
            {
                "event_ticker": "KXBTC-26MAY",
                "event_id": "kxbtc-26may",
                "markets": [
                    {
                        "ticker": ticker,
                        "market_ticker": ticker,
                        "rules_primary": "threshold rule",
                        "outcomes": ["Yes", "No"],
                        "orderbook_enrichment": {
                            "best_ask": 0.20,
                            "depth_at_best_ask": 25.0,
                            "orderbook_captured_at": "2026-05-24T11:59:30+00:00",
                        },
                    }
                    for ticker in ("KXBTC-26MAY-T86000", "KXBTC-26MAY-T87000")
                ],
            }
        ]
    }
    report = run_structural_basket_dry_run(
        snapshot_payload=snapshot,
        metadata_payloads=[metadata],
        generated_at=NOW,
    )

    assert report["summary"]["trusted_metadata_events"] == 0
    assert report["summary"]["stop_for_review_count"] == 0
    assert report["paper_fill_journal"] is None


def test_dry_run_skips_paper_simulation_when_only_count_evidence_attempted() -> None:
    metadata = {
        "event_ticker": "KXEVT-COUNT",
        "event_id": "kxevt-count",
        "rules_primary": "rule",
        "settlement_source_raw_evidence": "source",
        "expected_outcome_count": 3,
        "markets": [
            {"market_ticker": f"KXEVT-COUNT-{letter}"} for letter in ("A", "B", "C")
        ],
    }
    snapshot = {
        "events": [
            {
                "event_ticker": "KXEVT-COUNT",
                "event_id": "kxevt-count",
                "markets": [
                    {
                        "market_ticker": f"KXEVT-COUNT-{letter}",
                        "ticker": f"KXEVT-COUNT-{letter}",
                        "rules_primary": "rule",
                        "orderbook_enrichment": {
                            "best_ask": 0.25,
                            "depth_at_best_ask": 25.0,
                            "orderbook_captured_at": "2026-05-24T11:59:30+00:00",
                        },
                    }
                    for letter in ("A", "B", "C")
                ],
            }
        ]
    }
    report = run_structural_basket_dry_run(
        snapshot_payload=snapshot,
        metadata_payloads=[metadata],
        generated_at=NOW,
    )

    assert report["summary"]["stop_for_review_count"] == 0
    assert report["paper_fill_journal"] is None


# ---------------------------------------------------------------------------
# Gate enforcement (after a valid trusted join)
# ---------------------------------------------------------------------------


def test_dry_run_blocks_paper_simulation_when_orderbook_stale() -> None:
    snapshot = _e2e_snapshot()
    snapshot["events"][0]["markets"][0]["orderbook_enrichment"]["orderbook_captured_at"] = (
        "2026-05-24T00:00:00+00:00"
    )
    report = _run_e2e(snapshot=snapshot, max_quote_age_seconds=60.0)

    assert report["summary"]["enriched_normalized_market_rows"] == 3
    assert report["summary"]["stop_for_review_count"] == 0
    assert report["paper_fill_journal"] is None
    assert report["summary"]["paper_simulation_skip_reason"] == PAPER_SIMULATION_SKIP_NO_STOP_FOR_REVIEW
    statuses = {row["status"] for row in report["structural_basket_report"]["rows"]}
    assert "STALE_ORDERBOOK" in statuses


def test_dry_run_blocks_paper_simulation_when_depth_below_minimum() -> None:
    snapshot = _e2e_snapshot()
    for market in snapshot["events"][0]["markets"]:
        market["orderbook_enrichment"]["depth_at_best_ask"] = 0.5
    report = _run_e2e(snapshot=snapshot, min_depth=1.0)

    assert report["summary"]["enriched_normalized_market_rows"] == 3
    assert report["summary"]["stop_for_review_count"] == 0
    assert report["paper_fill_journal"] is None
    statuses = {row["status"] for row in report["structural_basket_report"]["rows"]}
    assert "INSUFFICIENT_DEPTH" in statuses


def test_dry_run_fees_still_applied_to_structural_basket_review() -> None:
    """Make the basket break-even at the ask level; conservative Kalshi fees
    should push it over 1.0 and the detector should bin it as FEES_KILL,
    skipping paper simulation."""
    snapshot = _e2e_snapshot()
    asks = [0.34, 0.33, 0.33]
    for market, ask in zip(snapshot["events"][0]["markets"], asks):
        market["orderbook_enrichment"]["best_ask"] = ask
        # Drop L2 ladder so the detector uses top-of-book ask only.
        market["orderbook_enrichment"].pop("yes_asks", None)
    report = _run_e2e(snapshot=snapshot)
    statuses = {row["status"] for row in report["structural_basket_report"]["rows"]}

    assert "FEES_KILL" in statuses, "fees must be applied to the structural basket review"
    assert report["summary"]["stop_for_review_count"] == 0
    assert report["paper_fill_journal"] is None
    structural_row = report["structural_basket_report"]["rows"][0]
    assert structural_row["conservative_fees"] > 0.0


def test_dry_run_blocks_when_metadata_ticker_missing_from_snapshot() -> None:
    snapshot = _e2e_snapshot()
    snapshot["events"][0]["markets"] = snapshot["events"][0]["markets"][:2]
    report = _run_e2e(snapshot=snapshot)

    assert report["summary"]["enriched_normalized_market_rows"] == 0
    assert report["summary"]["structural_groups_evaluated"] == 0
    assert report["summary"]["stop_for_review_count"] == 0
    assert report["paper_fill_journal"] is None


def test_dry_run_caller_can_explicitly_skip_paper_simulation() -> None:
    report = _run_e2e(simulate=False)

    assert report["summary"]["stop_for_review_count"] == 1
    assert report["summary"]["paper_simulation_skipped"] is True
    assert report["summary"]["paper_simulation_skip_reason"] == PAPER_SIMULATION_SKIP_DISABLED_BY_CALLER
    assert report["paper_fill_journal"] is None


# ---------------------------------------------------------------------------
# Safety surface: no live API / auth / order / secret references
# ---------------------------------------------------------------------------


def test_dry_run_safety_block_locks_down_live_and_execution_surface() -> None:
    report = _run_e2e()
    safety = report["safety"]

    assert safety["saved_file_only"] is True
    assert safety["live_fetch_attempted"] is False
    assert safety["places_orders"] is False
    assert safety["auth_used"] is False
    assert safety["private_endpoints_used"] is False
    assert safety["secrets_read"] is False
    assert safety["browser_automation_used"] is False
    assert safety["wallet_used"] is False
    assert safety["paper_candidate_emitted"] is False
    assert safety["stop_for_review_means_review_only"] is True
    assert safety["uses_midpoint"] is False
    assert safety["uses_title_similarity_for_exhaustiveness"] is False
    assert safety["uses_graph_hints_for_exhaustiveness"] is False
    assert safety["uses_count_only_evidence"] is False
    assert safety["affects_evaluator_gates"] is False
    assert "WATCH" in safety["allowed_actions"]


def test_dry_run_module_does_not_mention_live_or_auth_or_order_or_secrets() -> None:
    """Static check: the module source MUST NOT mention live API, order
    placement, account, auth, secrets, browser automation, or wallet logic.
    This is a structural guard, not a behavior test."""
    source = Path(
        Path(__file__).parent.parent
        / "relative_value"
        / "structural_basket_dry_run.py"
    ).read_text(encoding="utf-8")

    lowered = source.lower()
    # Note: we ALLOW the words to appear in comments/docstrings explaining
    # that we don't use them. So we look for actual code patterns.
    banned_substrings = (
        "requests.get",
        "requests.post",
        "urllib.request",
        "httpx.get",
        "httpx.post",
        "http.client",
        "place_order",
        "cancel_order",
        "submit_order",
        "api_key",
        "private_key",
        "wallet.",
        "selenium",
        "playwright",
        "puppeteer",
        "open(.env",
        "os.environ[\"kalshi_",
        "os.environ.get(\"kalshi_",
    )
    for needle in banned_substrings:
        assert needle not in lowered, f"banned reference found: {needle!r}"


def test_dry_run_summary_json_has_no_paper_candidate_promotion() -> None:
    report = _run_e2e()
    encoded = json.dumps(report["summary"])

    assert "PAPER_CANDIDATE" not in encoded
    # We allow STOP_FOR_REVIEW in the summary because it IS a real counter value.
    assert report["summary"]["paper_candidate_count"] == 0


# ---------------------------------------------------------------------------
# File-level helper exercises all output paths
# ---------------------------------------------------------------------------


def test_run_structural_basket_dry_run_files_writes_summary_and_sub_reports(tmp_path: Path) -> None:
    snapshot_path = FIXTURE_DIR / "e2e_snapshot.json"
    metadata_path = FIXTURE_DIR / "e2e_event_metadata.json"

    summary_json = tmp_path / "summary.json"
    summary_md = tmp_path / "summary.md"
    audit_json = tmp_path / "audit.json"
    audit_md = tmp_path / "audit.md"
    join_json = tmp_path / "join.json"
    join_md = tmp_path / "join.md"
    enriched = tmp_path / "enriched.json"
    structural_json = tmp_path / "structural.json"
    structural_md = tmp_path / "structural.md"
    paper_json = tmp_path / "paper.json"
    paper_md = tmp_path / "paper.md"

    report = run_structural_basket_dry_run_files(
        snapshot_path=snapshot_path,
        metadata_paths=[metadata_path],
        summary_json_output=summary_json,
        summary_markdown_output=summary_md,
        audit_json_output=audit_json,
        audit_markdown_output=audit_md,
        join_json_output=join_json,
        join_markdown_output=join_md,
        enriched_snapshot_output=enriched,
        structural_json_output=structural_json,
        structural_markdown_output=structural_md,
        paper_fill_json_output=paper_json,
        paper_fill_markdown_output=paper_md,
        generated_at=NOW,
        max_quote_age_seconds=1800.0,
        min_depth=1.0,
    )

    assert summary_json.exists()
    assert summary_md.exists()
    assert audit_json.exists()
    assert audit_md.exists()
    assert join_json.exists()
    assert join_md.exists()
    assert enriched.exists()
    assert structural_json.exists()
    assert structural_md.exists()
    assert paper_json.exists()
    assert paper_md.exists()

    summary_payload = json.loads(summary_json.read_text(encoding="utf-8"))
    assert summary_payload["source"] == DRY_RUN_SOURCE
    # Summary JSON must NOT contain the embedded sub-reports — they live in
    # dedicated files when requested.
    assert "audit_report" not in summary_payload
    assert "join_report" not in summary_payload
    assert "structural_basket_report" not in summary_payload
    assert "paper_fill_journal" not in summary_payload
    assert summary_payload["summary"]["stop_for_review_count"] == 1
    assert summary_payload["summary"]["paper_simulation_skipped"] is False
    assert summary_payload["safety"]["paper_candidate_emitted"] is False

    enriched_payload = json.loads(enriched.read_text(encoding="utf-8"))
    assert len(enriched_payload.get("normalized_markets") or []) == 3

    paper_payload = json.loads(paper_json.read_text(encoding="utf-8"))
    assert paper_payload["safety"]["paper_candidate_created"] is False

    # And the in-memory report is identical to the disk summary shape (modulo
    # the embedded sub-reports).
    assert report["summary"]["stop_for_review_count"] == 1
    assert report["paper_fill_journal"] is not None


def test_run_structural_basket_dry_run_files_omits_paper_outputs_when_skipped(tmp_path: Path) -> None:
    snapshot_path = FIXTURE_DIR / "e2e_snapshot.json"
    metadata_path = FIXTURE_DIR / "e2e_event_metadata.json"

    summary_json = tmp_path / "summary.json"
    summary_md = tmp_path / "summary.md"
    paper_json = tmp_path / "paper.json"
    paper_md = tmp_path / "paper.md"

    run_structural_basket_dry_run_files(
        snapshot_path=snapshot_path,
        metadata_paths=[metadata_path],
        summary_json_output=summary_json,
        summary_markdown_output=summary_md,
        paper_fill_json_output=paper_json,
        paper_fill_markdown_output=paper_md,
        generated_at=NOW,
        simulate_paper_fills_on_stop_for_review=False,
    )

    assert summary_json.exists()
    # When paper simulation is skipped, the paper-fill outputs MUST NOT be
    # written — there is no journal to write.
    assert not paper_json.exists()
    assert not paper_md.exists()


# ---------------------------------------------------------------------------
# Saved-file metadata importer
# ---------------------------------------------------------------------------


def test_metadata_importer_accepts_and_copies_trusted_file(tmp_path: Path) -> None:
    source = FIXTURE_DIR / "complete_event.json"
    destination = tmp_path / "metadata"

    report = import_kalshi_event_metadata_file(source=source, destination_dir=destination)

    assert report["source"] == METADATA_IMPORT_SOURCE
    assert report["summary"]["files_seen"] == 1
    assert report["summary"]["files_written"] == 1
    assert report["summary"]["trusted_event_count"] == 1
    assert report["summary"]["blocked_event_count"] == 0
    row = report["rows"][0]
    assert row["accepted"] is True
    assert row["trusted_for_completeness"] is True
    assert row["destination_path"] == str(destination / source.name)
    assert Path(row["destination_path"]).exists()
    assert row["event_tickers"] == ["KXEVT-2026-EXAMPLE"]


def test_metadata_importer_does_not_overwrite_existing_file_by_default(tmp_path: Path) -> None:
    source = FIXTURE_DIR / "complete_event.json"
    destination = tmp_path / "metadata"
    destination.mkdir()
    target = destination / source.name
    target.write_text("PLACEHOLDER", encoding="utf-8")

    report = import_kalshi_event_metadata_file(source=source, destination_dir=destination)
    row = report["rows"][0]

    assert row["destination_path"] == str(target)
    assert report["summary"]["files_written"] == 0
    assert target.read_text(encoding="utf-8") == "PLACEHOLDER"


def test_metadata_importer_overwrites_when_flag_passed(tmp_path: Path) -> None:
    source = FIXTURE_DIR / "complete_event.json"
    destination = tmp_path / "metadata"
    destination.mkdir()
    target = destination / source.name
    target.write_text("PLACEHOLDER", encoding="utf-8")

    report = import_kalshi_event_metadata_file(
        source=source,
        destination_dir=destination,
        overwrite=True,
    )

    assert report["summary"]["files_written"] == 1
    assert target.read_text(encoding="utf-8") != "PLACEHOLDER"


def test_metadata_importer_reports_blocked_metadata_without_failing(tmp_path: Path) -> None:
    blocked = {
        "event_ticker": "KXEVT-BLOCKED",
        "outcomes": ["Yes", "No"],
        "complete": True,
        "markets": [{"market_ticker": "KXEVT-BLOCKED-A", "outcomes": ["Yes", "No"]}],
    }
    source = tmp_path / "blocked.json"
    source.write_text(json.dumps(blocked), encoding="utf-8")

    report = import_kalshi_event_metadata_file(source=source, destination_dir=tmp_path / "out")
    row = report["rows"][0]

    assert report["summary"]["blocked_event_count"] == 1
    assert report["summary"]["trusted_event_count"] == 0
    assert "missing_event_outcome_list" in row["blockers"]
    # File was still copied — the importer records blockers but does not
    # silently strip a saved file just because the metadata is incomplete.
    assert row["destination_path"] == str(tmp_path / "out" / source.name)
    assert Path(row["destination_path"]).exists()


def test_metadata_importer_reports_invalid_json_without_writing(tmp_path: Path) -> None:
    source = tmp_path / "bad.json"
    source.write_text("not json {", encoding="utf-8")
    destination = tmp_path / "out"

    report = import_kalshi_event_metadata_file(source=source, destination_dir=destination)
    row = report["rows"][0]

    assert row["accepted"] is False
    assert row["destination_path"] is None
    assert any(blocker.startswith("invalid_json") for blocker in row["blockers"])
    assert not (destination / source.name).exists()


def test_metadata_importer_files_helper_aggregates_multiple_inputs(tmp_path: Path) -> None:
    sources = [FIXTURE_DIR / "complete_event.json", FIXTURE_DIR / "e2e_event_metadata.json"]
    destination = tmp_path / "metadata"

    report = import_kalshi_event_metadata_files(sources=sources, destination_dir=destination)

    assert report["summary"]["files_seen"] == 2
    assert report["summary"]["files_written"] == 2
    assert report["summary"]["trusted_event_count"] == 2
    rendered = render_metadata_importer_markdown(report)
    assert "Kalshi Event Metadata Importer" in rendered
    assert "Saved-file-only" in rendered
    assert "trusted_event_count: 2" in rendered


def test_metadata_importer_safety_block_locks_down_live_surface(tmp_path: Path) -> None:
    report = import_kalshi_event_metadata_file(
        source=FIXTURE_DIR / "complete_event.json",
        destination_dir=tmp_path,
    )
    safety = report["safety"]

    assert safety["saved_file_only"] is True
    assert safety["live_fetch_attempted"] is False
    assert safety["auth_used"] is False
    assert safety["private_endpoints_used"] is False
    assert safety["secrets_read"] is False
    assert safety["places_orders"] is False


# ---------------------------------------------------------------------------
# Belt-and-braces: paper-fill simulator must independently reject ungated
# rows even if upstream gating ever drifts.
# ---------------------------------------------------------------------------


def test_paper_fill_simulator_independently_rejects_ungated_structural_rows() -> None:
    """Sanity check that even if a future refactor accidentally fed a non-
    gated row from the dry-run into the simulator, the simulator itself
    would block it. This makes the dry-run safe even if the orchestration
    layer is bypassed."""
    from relative_value.paper_fill_simulator import simulate_paper_fill_journal

    report = _run_e2e()
    structural_rows = report["structural_basket_report"]["rows"]
    ungated_input = []
    for row in structural_rows:
        ungated = dict(row)
        ungated["status"] = "WATCH"
        ungated_input.append(ungated)

    journal = simulate_paper_fill_journal(
        input_payload={"rows": ungated_input},
        generated_at=NOW,
    )
    assert journal["summary"]["simulated_fill_count"] == 0
    assert any("ungated_structural_basket_row" in row["blockers"] for row in journal["journal"])
