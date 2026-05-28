from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from relative_value.kalshi_event_metadata import KALSHI_EVENT_METADATA_EVIDENCE_SOURCE
from relative_value.local_manifest_v1 import (
    LOCAL_MANIFEST_SOURCE,
    validate_local_manifest_v1_group,
)
from relative_value.structural_basket_detector import (
    STATUS_STOP_FOR_REVIEW,
    build_structural_basket_review_report,
)
from relative_value.structural_basket_hunter import (
    DO_NOT_PAPER_SIMULATE_WARNING,
    HUNTER_SOURCE,
    LADDER_FEES_KILL,
    LADDER_NEEDS_DEPTH,
    LADDER_NEEDS_EVENT_METADATA,
    LADDER_NEEDS_FRESH_QUOTES,
    LADDER_NEEDS_VALID_MANIFEST,
    LADDER_NOT_EXHAUSTIVE_EVIDENCE,
    LADDER_ORDER,
    LADDER_READY_STOP_FOR_REVIEW,
    LADDER_REFERENCE_ONLY_BLOCKED,
    MANIFEST_TEMPLATE_SOURCE,
    PAPER_SIMULATION_SKIP_DISABLED_BY_CALLER,
    PAPER_SIMULATION_SKIP_NO_STOP_FOR_REVIEW,
    REVIEW_ONLY_WARNING,
    build_manifest_template,
    hunt_structural_basket_candidates,
    hunt_structural_basket_candidates_files,
    render_hunter_markdown,
)


NOW = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kalshi_event_metadata"
HUNTER_MODULE_PATH = (
    Path(__file__).parent.parent / "relative_value" / "structural_basket_hunter.py"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stage(tmp_path: Path, files: dict[str, Path | str]) -> dict[str, Path]:
    """Copy / write the given files into tmp_path and return their absolute paths."""
    target_paths: dict[str, Path] = {}
    for name, content in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, Path):
            shutil.copyfile(content, target)
        else:
            target.write_text(content, encoding="utf-8")
        target_paths[name] = target
    return target_paths


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Saved-file-only safety guards
# ---------------------------------------------------------------------------


def test_hunter_source_is_saved_file_only_and_does_not_mention_live_or_auth_or_secrets() -> None:
    """Static check: the hunter module MUST NOT mention live API, order
    placement, account, auth, secrets, browser automation, or wallet logic
    in a way that would let those slip in."""
    source = HUNTER_MODULE_PATH.read_text(encoding="utf-8")
    lowered = source.lower()
    banned = (
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
        'open(".env',
        'os.environ["kalshi_',
        'os.environ.get("kalshi_',
    )
    for needle in banned:
        assert needle not in lowered, f"banned reference found: {needle!r}"


def test_hunter_safety_block_locks_down_live_surface(tmp_path: Path) -> None:
    """The report's safety block must explicitly assert no live calls."""
    report = hunt_structural_basket_candidates(
        snapshot_paths=[],
        metadata_paths=[],
        generated_at=NOW,
        manifest_template_output_dir=tmp_path,
    )
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
    assert safety["infers_exhaustiveness_from_title"] is False
    assert safety["infers_exhaustiveness_from_ticker"] is False
    assert safety["infers_exhaustiveness_from_market_count"] is False
    assert safety["templates_are_valid_by_default"] is False
    assert "WATCH" in safety["allowed_actions"]
    assert safety["allowed_evidence_source"] == KALSHI_EVENT_METADATA_EVIDENCE_SOURCE


def test_hunter_does_not_hit_network_when_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Belt-and-braces: monkeypatch socket/http modules and confirm a real run
    over the e2e fixtures never opens a socket."""
    import socket

    class _Sentinel(Exception):
        pass

    def _no_network(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise _Sentinel("hunter must not open a socket")

    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", _no_network)

    snapshot_dir = tmp_path / "snapshots"
    metadata_dir = tmp_path / "metadata"
    _stage(
        snapshot_dir,
        {
            "e2e_snapshot.json": FIXTURE_DIR / "e2e_snapshot.json",
        },
    )
    _stage(
        metadata_dir,
        {
            "e2e_event_metadata.json": FIXTURE_DIR / "e2e_event_metadata.json",
        },
    )

    report = hunt_structural_basket_candidates_files(
        snapshots_dir=snapshot_dir,
        metadata_dir=metadata_dir,
        manifest_dir=None,
        json_output=tmp_path / "hunt.json",
        markdown_output=tmp_path / "hunt.md",
        manifest_template_output_dir=tmp_path / "templates",
        generated_at=NOW,
    )
    assert report["source"] == HUNTER_SOURCE


# ---------------------------------------------------------------------------
# e2e fixture happy path: STOP_FOR_REVIEW is surfaced; paper simulation runs
# ---------------------------------------------------------------------------


def test_hunter_finds_e2e_fixture_and_produces_stop_for_review(tmp_path: Path) -> None:
    snapshot_dir = tmp_path / "snapshots"
    metadata_dir = tmp_path / "metadata"
    _stage(
        snapshot_dir,
        {"e2e_snapshot.json": FIXTURE_DIR / "e2e_snapshot.json"},
    )
    _stage(
        metadata_dir,
        {"e2e_event_metadata.json": FIXTURE_DIR / "e2e_event_metadata.json"},
    )

    report = hunt_structural_basket_candidates_files(
        snapshots_dir=snapshot_dir,
        metadata_dir=metadata_dir,
        manifest_dir=None,
        json_output=tmp_path / "hunt.json",
        markdown_output=tmp_path / "hunt.md",
        manifest_template_output_dir=tmp_path / "templates",
        generated_at=NOW,
    )
    summary = report["summary"]
    assert summary["snapshots_considered"] == 1
    assert summary["metadata_files_considered"] == 1
    assert summary["joined_snapshots_created"] == 1
    assert summary["structural_groups_evaluated"] == 1
    assert summary["stop_for_review_count"] == 1
    assert summary["paper_fill_rows"] == 1
    assert summary["paper_candidate_count"] == 0
    closest = report["closest_groups_to_review"][0]
    assert closest["status"] == STATUS_STOP_FOR_REVIEW
    assert closest["stop_for_review"] is True
    assert closest["group_id"] == "KXE2E-2026-DEMO"
    # Paper-fill journal exists in the pairing
    pairing = report["pairings"][0]
    assert pairing["paper_fill_journal"] is not None
    assert pairing["paper_fill_journal"]["safety"]["paper_candidate_created"] is False


def test_hunter_e2e_files_writes_json_md_and_report(tmp_path: Path) -> None:
    snapshot_dir = tmp_path / "snapshots"
    metadata_dir = tmp_path / "metadata"
    template_dir = tmp_path / "templates"
    _stage(snapshot_dir, {"e2e_snapshot.json": FIXTURE_DIR / "e2e_snapshot.json"})
    _stage(metadata_dir, {"e2e_event_metadata.json": FIXTURE_DIR / "e2e_event_metadata.json"})

    json_output = tmp_path / "hunt.json"
    markdown_output = tmp_path / "hunt.md"
    report = hunt_structural_basket_candidates_files(
        snapshots_dir=snapshot_dir,
        metadata_dir=metadata_dir,
        manifest_dir=None,
        json_output=json_output,
        markdown_output=markdown_output,
        manifest_template_output_dir=template_dir,
        generated_at=NOW,
    )
    assert json_output.exists()
    assert markdown_output.exists()
    text = markdown_output.read_text(encoding="utf-8")
    assert "Structural Basket Hunt" in text
    assert "places_orders: false" in text
    assert "uses_midpoint: false" in text
    assert "templates_are_valid_by_default: false" in text
    assert "uses_title_similarity_for_exhaustiveness: false" in text
    # JSON round-trip
    on_disk = _read_json(json_output)
    assert on_disk["summary"]["stop_for_review_count"] == 1
    assert on_disk["safety"]["paper_candidate_emitted"] is False
    # Report parity
    assert report["summary"]["stop_for_review_count"] == 1


# ---------------------------------------------------------------------------
# Quote age controls whether STOP_FOR_REVIEW appears
# ---------------------------------------------------------------------------


def test_hunter_blocks_stop_for_review_when_quote_age_exceeded(tmp_path: Path) -> None:
    snapshot_dir = tmp_path / "snapshots"
    metadata_dir = tmp_path / "metadata"
    _stage(snapshot_dir, {"e2e_snapshot.json": FIXTURE_DIR / "e2e_snapshot.json"})
    _stage(metadata_dir, {"e2e_event_metadata.json": FIXTURE_DIR / "e2e_event_metadata.json"})

    report = hunt_structural_basket_candidates_files(
        snapshots_dir=snapshot_dir,
        metadata_dir=metadata_dir,
        manifest_dir=None,
        json_output=tmp_path / "hunt.json",
        markdown_output=tmp_path / "hunt.md",
        manifest_template_output_dir=tmp_path / "templates",
        generated_at=NOW,
        max_quote_age_seconds=1.0,
    )
    summary = report["summary"]
    assert summary["stop_for_review_count"] == 0
    pairing = report["pairings"][0]
    assert pairing["paper_simulation_skipped"] is True
    assert pairing["paper_simulation_skip_reason"] == PAPER_SIMULATION_SKIP_NO_STOP_FOR_REVIEW
    statuses = {row["status"] for row in pairing["structural_rows"]}
    assert "STALE_ORDERBOOK" in statuses


def test_hunter_caller_can_explicitly_skip_paper_simulation(tmp_path: Path) -> None:
    snapshot_dir = tmp_path / "snapshots"
    metadata_dir = tmp_path / "metadata"
    _stage(snapshot_dir, {"e2e_snapshot.json": FIXTURE_DIR / "e2e_snapshot.json"})
    _stage(metadata_dir, {"e2e_event_metadata.json": FIXTURE_DIR / "e2e_event_metadata.json"})

    report = hunt_structural_basket_candidates_files(
        snapshots_dir=snapshot_dir,
        metadata_dir=metadata_dir,
        manifest_dir=None,
        json_output=tmp_path / "hunt.json",
        markdown_output=tmp_path / "hunt.md",
        manifest_template_output_dir=tmp_path / "templates",
        generated_at=NOW,
        simulate_paper_fills_on_stop_for_review=False,
    )
    pairing = report["pairings"][0]
    assert pairing["stop_for_review_count"] == 1
    assert pairing["paper_simulation_skipped"] is True
    assert pairing["paper_simulation_skip_reason"] == PAPER_SIMULATION_SKIP_DISABLED_BY_CALLER
    assert pairing["paper_fill_journal"] is None
    assert report["summary"]["paper_candidate_count"] == 0


# ---------------------------------------------------------------------------
# Reference-only fail-closed
# ---------------------------------------------------------------------------


def test_hunter_fails_closed_when_metadata_is_reference_only(tmp_path: Path) -> None:
    snapshot_dir = tmp_path / "snapshots"
    metadata_dir = tmp_path / "metadata"
    metadata_payload = _read_json(FIXTURE_DIR / "e2e_event_metadata.json")
    metadata_payload["reference_only"] = True
    _stage(snapshot_dir, {"e2e_snapshot.json": FIXTURE_DIR / "e2e_snapshot.json"})
    _stage(metadata_dir, {"meta.json": json.dumps(metadata_payload)})

    report = hunt_structural_basket_candidates_files(
        snapshots_dir=snapshot_dir,
        metadata_dir=metadata_dir,
        manifest_dir=None,
        json_output=tmp_path / "hunt.json",
        markdown_output=tmp_path / "hunt.md",
        manifest_template_output_dir=tmp_path / "templates",
        generated_at=NOW,
    )
    summary = report["summary"]
    assert summary["stop_for_review_count"] == 0
    assert summary["paper_candidate_count"] == 0
    pairing = report["pairings"][0]
    assert pairing["paper_fill_journal"] is None


def test_hunter_fails_closed_when_snapshot_market_is_reference_only(tmp_path: Path) -> None:
    """Snapshot-side reference_only/source_kind/venue_type reference flags
    must fail closed (regression for the reference-only propagation bug)."""
    snapshot_dir = tmp_path / "snapshots"
    metadata_dir = tmp_path / "metadata"
    snapshot_payload = _read_json(FIXTURE_DIR / "e2e_snapshot.json")
    snapshot_payload["events"][0]["markets"][0]["reference_only"] = True
    snapshot_payload["events"][0]["markets"][1]["source_kind"] = "reference"
    snapshot_payload["events"][0]["markets"][2]["venue_type"] = "reference_only"
    _stage(snapshot_dir, {"snapshot.json": json.dumps(snapshot_payload)})
    _stage(metadata_dir, {"meta.json": FIXTURE_DIR / "e2e_event_metadata.json"})

    report = hunt_structural_basket_candidates_files(
        snapshots_dir=snapshot_dir,
        metadata_dir=metadata_dir,
        manifest_dir=None,
        json_output=tmp_path / "hunt.json",
        markdown_output=tmp_path / "hunt.md",
        manifest_template_output_dir=tmp_path / "templates",
        generated_at=NOW,
    )
    summary = report["summary"]
    assert summary["stop_for_review_count"] == 0
    assert summary["paper_candidate_count"] == 0
    pairing = report["pairings"][0]
    assert pairing["paper_fill_journal"] is None


# ---------------------------------------------------------------------------
# No metadata: blocker report, never paper output
# ---------------------------------------------------------------------------


def test_hunter_with_no_metadata_produces_blockers_not_paper_output(tmp_path: Path) -> None:
    snapshot_dir = tmp_path / "snapshots"
    metadata_dir = tmp_path / "metadata_empty"
    metadata_dir.mkdir()
    _stage(snapshot_dir, {"snapshot.json": FIXTURE_DIR / "e2e_snapshot.json"})

    report = hunt_structural_basket_candidates_files(
        snapshots_dir=snapshot_dir,
        metadata_dir=metadata_dir,
        manifest_dir=None,
        json_output=tmp_path / "hunt.json",
        markdown_output=tmp_path / "hunt.md",
        manifest_template_output_dir=tmp_path / "templates",
        generated_at=NOW,
    )
    summary = report["summary"]
    assert summary["metadata_files_considered"] == 0
    assert summary["structural_groups_evaluated"] == 0
    assert summary["stop_for_review_count"] == 0
    assert summary["paper_fill_rows"] == 0
    assert summary["paper_candidate_count"] == 0
    pairing = report["pairings"][0]
    assert pairing["paper_fill_journal"] is None
    assert pairing["native_candidates"], "expected native scout to surface a candidate group"
    assert report["missing_metadata_requirements"], "expected missing metadata requirements to be listed"
    # The hunter should suggest writing a manifest template for the candidate.
    assert report["manifest_template_suggestions"], "expected at least one manifest template suggestion"


# ---------------------------------------------------------------------------
# Manifest template generator: invalid by default; cannot promote
# ---------------------------------------------------------------------------


def test_manifest_template_is_invalid_by_default() -> None:
    template = build_manifest_template(
        group_id="KXEVT-DEMO",
        venue="kalshi",
        snapshot_path="path/to/snapshot.json",
        snapshot_market_tickers=["KXEVT-DEMO-A", "KXEVT-DEMO-B", "KXEVT-DEMO-C"],
    )
    group = template["exhaustive_groups"][0]
    blockers = validate_local_manifest_v1_group(group)
    # The validator must fail with multiple specific blockers — the template
    # MUST never be silently accepted.
    assert blockers, "template must produce validation blockers by default"
    expected = {
        "trusted_local_manifest_required",
        "missing_manifest_reviewer",
        "missing_manifest_reviewed_at",
        "missing_manifest_outcome_list",
        "missing_manifest_evidence_text",
        "missing_manifest_settlement_source_evidence",
        "missing_manifest_rules_evidence",
        "manifest_not_marked_complete",
    }
    missing = expected - set(blockers)
    assert not missing, f"template missing expected blockers: {missing}"
    # The template is explicit about NOT being a trusted source.
    assert template["safety"]["valid_for_stop_for_review"] is False
    assert template["safety"]["do_not_load_until_edited"] is True
    assert group["trusted_local_manifest"] is False
    assert group["manifest_template"] is True
    assert template["manifest_template_source"] == MANIFEST_TEMPLATE_SOURCE


def test_template_when_loaded_as_manifest_cannot_promote_to_stop_for_review(tmp_path: Path) -> None:
    """A snapshot + the hunter's untouched template must never yield
    STOP_FOR_REVIEW. The detector should report the manifest as FAIL and the
    structural row's status should NOT be STOP_FOR_REVIEW."""
    snapshot_payload = _read_json(FIXTURE_DIR / "e2e_snapshot.json")
    template = build_manifest_template(
        group_id="KXE2E-2026-DEMO",
        venue="kalshi",
        snapshot_path="e2e_snapshot.json",
        snapshot_market_tickers=[
            "KXE2E-2026-DEMO-ALPHA",
            "KXE2E-2026-DEMO-BETA",
            "KXE2E-2026-DEMO-GAMMA",
        ],
    )
    structural_report = build_structural_basket_review_report(
        snapshot_payloads=[snapshot_payload],
        manifest_payload=template,
        detected_at=NOW,
    )
    statuses = {row["status"] for row in structural_report["rows"]}
    assert STATUS_STOP_FOR_REVIEW not in statuses, (
        "untouched manifest template must NOT pass exhaustive evidence gates"
    )
    # The detector marks the manifest evidence as FAIL.
    assert any(
        row.get("manifest_evidence_status") == "FAIL"
        for row in structural_report["rows"]
    )


def test_hunter_template_suggestion_records_validation_blockers(tmp_path: Path) -> None:
    snapshot_dir = tmp_path / "snapshots"
    metadata_dir = tmp_path / "metadata_empty"
    metadata_dir.mkdir()
    _stage(snapshot_dir, {"snapshot.json": FIXTURE_DIR / "e2e_snapshot.json"})

    template_dir = tmp_path / "templates"
    report = hunt_structural_basket_candidates_files(
        snapshots_dir=snapshot_dir,
        metadata_dir=metadata_dir,
        manifest_dir=None,
        json_output=tmp_path / "hunt.json",
        markdown_output=tmp_path / "hunt.md",
        manifest_template_output_dir=template_dir,
        generated_at=NOW,
    )
    suggestions = report["manifest_template_suggestions"]
    assert suggestions, "expected template suggestions for snapshot without metadata"
    for suggestion in suggestions:
        assert suggestion["valid_by_default"] is False
        assert suggestion["validation_blockers_expected"], "templates must list expected blockers"
        # The file was actually written and exists on disk.
        assert Path(suggestion["template_path"]).exists()
        # And the on-disk file fails validation when loaded.
        loaded = _read_json(Path(suggestion["template_path"]))
        group = loaded["exhaustive_groups"][0]
        assert validate_local_manifest_v1_group(group), (
            "template on disk must fail validation by default"
        )


def test_hunter_can_skip_writing_templates(tmp_path: Path) -> None:
    snapshot_dir = tmp_path / "snapshots"
    metadata_dir = tmp_path / "metadata_empty"
    metadata_dir.mkdir()
    _stage(snapshot_dir, {"snapshot.json": FIXTURE_DIR / "e2e_snapshot.json"})

    template_dir = tmp_path / "templates"
    report = hunt_structural_basket_candidates_files(
        snapshots_dir=snapshot_dir,
        metadata_dir=metadata_dir,
        manifest_dir=None,
        json_output=tmp_path / "hunt.json",
        markdown_output=tmp_path / "hunt.md",
        manifest_template_output_dir=template_dir,
        write_templates=False,
        generated_at=NOW,
    )
    for suggestion in report["manifest_template_suggestions"]:
        assert suggestion["written"] is False
    assert not list(template_dir.glob("*.template.json"))


# ---------------------------------------------------------------------------
# Paper fill simulator only runs after a gated STOP_FOR_REVIEW row
# ---------------------------------------------------------------------------


def test_paper_fill_simulator_only_runs_when_a_stop_for_review_row_exists(tmp_path: Path) -> None:
    snapshot_dir = tmp_path / "snapshots"
    metadata_dir = tmp_path / "metadata"
    metadata_payload = _read_json(FIXTURE_DIR / "e2e_event_metadata.json")
    # Remove a market from metadata so the join is incomplete and the
    # detector falls to NOT_EXHAUSTIVE_EVIDENCE.
    metadata_payload["markets"] = metadata_payload["markets"][:2]
    metadata_payload["outcome_list"] = metadata_payload["outcome_list"][:2]
    _stage(snapshot_dir, {"snapshot.json": FIXTURE_DIR / "e2e_snapshot.json"})
    _stage(metadata_dir, {"meta.json": json.dumps(metadata_payload)})

    report = hunt_structural_basket_candidates_files(
        snapshots_dir=snapshot_dir,
        metadata_dir=metadata_dir,
        manifest_dir=None,
        json_output=tmp_path / "hunt.json",
        markdown_output=tmp_path / "hunt.md",
        manifest_template_output_dir=tmp_path / "templates",
        generated_at=NOW,
    )
    pairing = report["pairings"][0]
    assert pairing["stop_for_review_count"] == 0
    assert pairing["paper_fill_journal"] is None
    assert pairing["paper_simulation_skip_reason"] == PAPER_SIMULATION_SKIP_NO_STOP_FOR_REVIEW
    assert report["summary"]["paper_fill_rows"] == 0


# ---------------------------------------------------------------------------
# Hunter never emits PAPER_CANDIDATE (literal string)
# ---------------------------------------------------------------------------


def test_hunter_report_never_promotes_a_paper_candidate(tmp_path: Path) -> None:
    """Belt-and-braces: the hunter must never PROMOTE a row to PAPER_CANDIDATE.

    Note: explanatory text inside the report (e.g. a review-only warning
    saying "never emits PAPER_CANDIDATE") may legitimately mention the
    literal string. The invariant we care about is structural: no entry's
    status / candidate_type / action / paper_candidate_emitted field
    indicates promotion.
    """
    snapshot_dir = tmp_path / "snapshots"
    metadata_dir = tmp_path / "metadata"
    _stage(snapshot_dir, {"snapshot.json": FIXTURE_DIR / "e2e_snapshot.json"})
    _stage(metadata_dir, {"meta.json": FIXTURE_DIR / "e2e_event_metadata.json"})

    report = hunt_structural_basket_candidates_files(
        snapshots_dir=snapshot_dir,
        metadata_dir=metadata_dir,
        manifest_dir=None,
        json_output=tmp_path / "hunt.json",
        markdown_output=tmp_path / "hunt.md",
        manifest_template_output_dir=tmp_path / "templates",
        generated_at=NOW,
    )

    # No promotion counter.
    assert report["summary"]["paper_candidate_count"] == 0
    assert report["safety"]["paper_candidate_emitted"] is False
    assert report["safety"].get("paper_candidate_count", 0) == 0

    # No closest_group / ladder / next_action carries a PAPER_CANDIDATE
    # status / candidate_type / action.
    for group in report.get("closest_groups_to_review") or []:
        assert group.get("status") != "PAPER_CANDIDATE"
        assert group.get("profit_readiness") != "PAPER_CANDIDATE"
        assert group.get("kind") != "PAPER_CANDIDATE"
    for ladder in report.get("profit_readiness_ladder") or []:
        assert ladder.get("status") != "PAPER_CANDIDATE"
    for action in report.get("next_5_actions") or []:
        assert action.get("action") != "promote_to_paper_candidate"
        assert action.get("label", "").lower().find("promote") == -1 or "candidate" not in action.get("label", "").lower()

    # Any embedded paper-fill journal in pairings must also assert no promotion.
    for pairing in report.get("pairings") or []:
        journal = pairing.get("paper_fill_journal")
        if isinstance(journal, dict):
            assert journal["safety"]["paper_candidate_created"] is False
            assert journal["summary"]["paper_candidate_count_created"] == 0


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def test_render_hunter_markdown_includes_safety_blocker_and_command_sections(tmp_path: Path) -> None:
    snapshot_dir = tmp_path / "snapshots"
    metadata_dir = tmp_path / "metadata"
    _stage(snapshot_dir, {"snapshot.json": FIXTURE_DIR / "e2e_snapshot.json"})
    _stage(metadata_dir, {"meta.json": FIXTURE_DIR / "e2e_event_metadata.json"})

    report = hunt_structural_basket_candidates_files(
        snapshots_dir=snapshot_dir,
        metadata_dir=metadata_dir,
        manifest_dir=None,
        json_output=tmp_path / "hunt.json",
        markdown_output=tmp_path / "hunt.md",
        manifest_template_output_dir=tmp_path / "templates",
        generated_at=NOW,
    )
    rendered = render_hunter_markdown(report)
    assert "# Structural Basket Hunt" in rendered
    assert "Top blockers (normalized)" in rendered
    assert "Closest groups to review" in rendered
    assert "Suggested next commands" in rendered
    assert "Safety" in rendered
    assert "places_orders: false" in rendered
    assert "paper_candidate_emitted: false" in rendered
    assert "templates_are_valid_by_default: false" in rendered


# ---------------------------------------------------------------------------
# Manifest in manifest_dir is honored
# ---------------------------------------------------------------------------


def test_hunter_picks_up_trusted_manifest_from_manifest_dir(tmp_path: Path) -> None:
    snapshot_dir = tmp_path / "snapshots"
    metadata_dir = tmp_path / "metadata_empty"
    manifest_dir = tmp_path / "manifests"
    metadata_dir.mkdir()
    manifest_dir.mkdir()
    # The structural basket detector reads from normalized_markets / top-level
    # markets; not from events[].markets. Build a snapshot the detector can
    # actually evaluate when paired with a trusted local manifest (no metadata).
    snapshot_payload = {
        "schema_version": 1,
        "events": [
            {
                "event_ticker": "KXE2E-2026-DEMO",
                "event_id": "kxe2e-2026-demo",
                "markets": [],
            }
        ],
        "normalized_markets": [
            {
                "venue": "kalshi",
                "market_id": ticker,
                "ticker": ticker,
                "market_ticker": ticker,
                "event_id": "KXE2E-2026-DEMO",
                "group_id": "KXE2E-2026-DEMO",
                "title": f"{ticker} wins",
                "rules": "Resolution source is the official Kalshi event metadata.",
                "rules_primary": "Resolution source is the official Kalshi event metadata.",
                "resolution_criteria": "Resolution source is the official Kalshi event metadata.",
                "settlement_source": "Official Kalshi event metadata resolves every listed outcome from the same source.",
                "settlement_source_raw_evidence": "Official Kalshi event metadata resolves every listed outcome from the same source.",
                "close_time": "2026-12-31T22:00:00+00:00",
                "expected_expiration_time": "2026-12-31T23:00:00+00:00",
                "expiration_time": "2027-01-01T00:00:00+00:00",
                "latest_expiration_time": "2027-01-01T00:00:00+00:00",
                "settlement_time": "2027-01-01T00:00:00+00:00",
                "resolution_date": "2026-12-31",
                "orderbook_enrichment": {
                    "best_ask": ask,
                    "depth_at_best_ask": 25.0,
                    "yes_asks": [[ask, 25.0]],
                    "orderbook_captured_at": "2026-05-24T11:59:30+00:00",
                },
            }
            for ticker, ask in (
                ("KXE2E-2026-DEMO-ALPHA", 0.30),
                ("KXE2E-2026-DEMO-BETA", 0.28),
                ("KXE2E-2026-DEMO-GAMMA", 0.27),
            )
        ],
    }
    _stage(snapshot_dir, {"snapshot.json": json.dumps(snapshot_payload)})

    manifest_payload = {
        "exhaustive_groups": [
            {
                "venue": "kalshi",
                "source": LOCAL_MANIFEST_SOURCE,
                "trusted_local_manifest": True,
                "group_id": "KXE2E-2026-DEMO",
                "venue_native_event_id": "KXE2E-2026-DEMO",
                "complete": True,
                "is_exhaustive": True,
                "reviewer": "unit-test-reviewer",
                "reviewed_at": "2026-05-24T12:00:00+00:00",
                "evidence_text": "hand-reviewed venue event metadata states all outcomes included",
                "settlement_source_evidence": "Official Kalshi event metadata resolves every listed outcome from the same source.",
                "rules_evidence": "Resolution source is the official Kalshi event metadata.",
                "market_tickers": [
                    "KXE2E-2026-DEMO-ALPHA",
                    "KXE2E-2026-DEMO-BETA",
                    "KXE2E-2026-DEMO-GAMMA",
                ],
                "outcome_list": [
                    "Outcome Alpha",
                    "Outcome Beta",
                    "Outcome Gamma",
                ],
                "outcomes": [
                    "Outcome Alpha",
                    "Outcome Beta",
                    "Outcome Gamma",
                ],
                "expected_outcome_count": 3,
            }
        ]
    }
    (manifest_dir / "trusted_manifest.json").write_text(
        json.dumps(manifest_payload),
        encoding="utf-8",
    )

    report = hunt_structural_basket_candidates_files(
        snapshots_dir=snapshot_dir,
        metadata_dir=metadata_dir,
        manifest_dir=manifest_dir,
        json_output=tmp_path / "hunt.json",
        markdown_output=tmp_path / "hunt.md",
        manifest_template_output_dir=tmp_path / "templates",
        generated_at=NOW,
    )
    summary = report["summary"]
    assert summary["manifests_considered"] == 1
    assert summary["structural_groups_evaluated"] >= 1
    # Detector should surface STOP_FOR_REVIEW based on the trusted local manifest.
    statuses = {row["status"] for row in report["pairings"][0]["structural_rows"]}
    assert STATUS_STOP_FOR_REVIEW in statuses


# ---------------------------------------------------------------------------
# Top-blockers normalization
# ---------------------------------------------------------------------------


def test_hunter_top_blockers_normalize_into_named_categories(tmp_path: Path) -> None:
    """When the snapshot has no metadata, the missing_outcome_list and
    missing_completeness_evidence categories should both surface in
    top_blockers, plus manifest_required."""
    snapshot_dir = tmp_path / "snapshots"
    metadata_dir = tmp_path / "metadata_empty"
    metadata_dir.mkdir()
    _stage(snapshot_dir, {"snapshot.json": FIXTURE_DIR / "e2e_snapshot.json"})

    report = hunt_structural_basket_candidates_files(
        snapshots_dir=snapshot_dir,
        metadata_dir=metadata_dir,
        manifest_dir=None,
        json_output=tmp_path / "hunt.json",
        markdown_output=tmp_path / "hunt.md",
        manifest_template_output_dir=tmp_path / "templates",
        generated_at=NOW,
    )
    categories = {entry["category"] for entry in report["top_blockers"]}
    # The native scout reports missing_outcome_list and partial_event_metadata
    # which map to missing_outcome_list and missing_completeness_evidence.
    assert "missing_outcome_list" in categories
    assert "missing_completeness_evidence" in categories


# ---------------------------------------------------------------------------
# Profit-readiness ladder and operator-loop reporting
# ---------------------------------------------------------------------------


def _hunt_e2e(tmp_path: Path, **overrides) -> dict:
    snapshot_dir = tmp_path / "snapshots"
    metadata_dir = tmp_path / "metadata"
    _stage(snapshot_dir, {"snapshot.json": FIXTURE_DIR / "e2e_snapshot.json"})
    _stage(metadata_dir, {"meta.json": FIXTURE_DIR / "e2e_event_metadata.json"})
    kwargs = dict(
        snapshots_dir=snapshot_dir,
        metadata_dir=metadata_dir,
        manifest_dir=None,
        json_output=tmp_path / "hunt.json",
        markdown_output=tmp_path / "hunt.md",
        manifest_template_output_dir=tmp_path / "templates",
        generated_at=NOW,
    )
    kwargs.update(overrides)
    return hunt_structural_basket_candidates_files(**kwargs)


def test_ladder_lists_ready_first_then_remediation_rungs(tmp_path: Path) -> None:
    report = _hunt_e2e(tmp_path)
    ladder = report["profit_readiness_ladder"]
    # The ladder must list rungs in the canonical order.
    assert [entry["status"] for entry in ladder] == list(LADDER_ORDER)
    # And READY_STOP_FOR_REVIEW must be the first non-empty rung when the
    # e2e fixture surfaces a STOP_FOR_REVIEW row.
    populated = [entry for entry in ladder if entry["count"] > 0]
    assert populated, "expected at least one populated rung"
    assert populated[0]["status"] == LADDER_READY_STOP_FOR_REVIEW


def test_closest_groups_rank_stop_for_review_first(tmp_path: Path) -> None:
    report = _hunt_e2e(tmp_path)
    closest = report["closest_groups_to_review"]
    assert closest, "expected at least one closest group"
    first = closest[0]
    assert first["profit_readiness"] == LADDER_READY_STOP_FOR_REVIEW
    assert first["stop_for_review"] is True
    assert first["do_not_paper_simulate_yet"] is False
    assert first["paper_simulate_command"] is not None
    cmd = first["paper_simulate_command"]
    # The command pair must contain the literal `simulate-paper-fills` invocation
    # and must NOT contain any execution / order / wallet wording.
    joined = " ".join(cmd.get("command_lines") or []).lower()
    assert "simulate-paper-fills" in joined
    assert "place_order" not in joined
    assert "submit_order" not in joined
    assert "cancel_order" not in joined
    assert "wallet" not in joined
    assert "private_key" not in joined
    assert cmd["paper_candidate_emitted"] is False
    assert cmd["places_orders"] is False


def test_stale_orderbook_maps_to_needs_fresh_quotes_ladder(tmp_path: Path) -> None:
    report = _hunt_e2e(tmp_path, max_quote_age_seconds=1.0)
    ladder_statuses = {
        entry["status"]
        for entry in report["profit_readiness_ladder"]
        if entry["count"] > 0
    }
    assert LADDER_NEEDS_FRESH_QUOTES in ladder_statuses
    # The first closest_group must say do_not_paper_simulate_yet and have no
    # paper_simulate_command.
    first = report["closest_groups_to_review"][0]
    assert first["profit_readiness"] == LADDER_NEEDS_FRESH_QUOTES
    assert first["do_not_paper_simulate_yet"] is True
    assert first["paper_simulate_command"] is None
    assert first["do_not_paper_simulate_yet_reason"] == DO_NOT_PAPER_SIMULATE_WARNING


def test_shallow_depth_maps_to_needs_depth_ladder(tmp_path: Path) -> None:
    snapshot_payload = _read_json(FIXTURE_DIR / "e2e_snapshot.json")
    for market in snapshot_payload["events"][0]["markets"]:
        market["orderbook_enrichment"]["depth_at_best_ask"] = 0.5
        market["orderbook_enrichment"]["yes_asks"] = [[market["orderbook_enrichment"]["best_ask"], 0.5]]
    snapshot_dir = tmp_path / "snapshots"
    metadata_dir = tmp_path / "metadata"
    _stage(snapshot_dir, {"snap.json": json.dumps(snapshot_payload)})
    _stage(metadata_dir, {"meta.json": FIXTURE_DIR / "e2e_event_metadata.json"})

    report = hunt_structural_basket_candidates_files(
        snapshots_dir=snapshot_dir,
        metadata_dir=metadata_dir,
        manifest_dir=None,
        json_output=tmp_path / "hunt.json",
        markdown_output=tmp_path / "hunt.md",
        manifest_template_output_dir=tmp_path / "templates",
        generated_at=NOW,
        min_depth=1.0,
    )
    statuses = {entry["status"] for entry in report["profit_readiness_ladder"] if entry["count"] > 0}
    assert LADDER_NEEDS_DEPTH in statuses
    first = report["closest_groups_to_review"][0]
    assert first["profit_readiness"] == LADDER_NEEDS_DEPTH
    assert first["do_not_paper_simulate_yet"] is True


def test_fees_kill_maps_to_fees_kill_ladder(tmp_path: Path) -> None:
    snapshot_payload = _read_json(FIXTURE_DIR / "e2e_snapshot.json")
    asks = [0.34, 0.33, 0.33]
    for market, ask in zip(snapshot_payload["events"][0]["markets"], asks):
        market["orderbook_enrichment"]["best_ask"] = ask
        market["orderbook_enrichment"].pop("yes_asks", None)
    snapshot_dir = tmp_path / "snapshots"
    metadata_dir = tmp_path / "metadata"
    _stage(snapshot_dir, {"snap.json": json.dumps(snapshot_payload)})
    _stage(metadata_dir, {"meta.json": FIXTURE_DIR / "e2e_event_metadata.json"})

    report = hunt_structural_basket_candidates_files(
        snapshots_dir=snapshot_dir,
        metadata_dir=metadata_dir,
        manifest_dir=None,
        json_output=tmp_path / "hunt.json",
        markdown_output=tmp_path / "hunt.md",
        manifest_template_output_dir=tmp_path / "templates",
        generated_at=NOW,
    )
    statuses = {entry["status"] for entry in report["profit_readiness_ladder"] if entry["count"] > 0}
    assert LADDER_FEES_KILL in statuses
    first = report["closest_groups_to_review"][0]
    assert first["profit_readiness"] == LADDER_FEES_KILL
    assert first["do_not_paper_simulate_yet"] is True


def test_reference_only_metadata_maps_to_reference_only_blocked_ladder(tmp_path: Path) -> None:
    snapshot_payload = _read_json(FIXTURE_DIR / "e2e_snapshot.json")
    snapshot_payload["events"][0]["markets"][0]["reference_only"] = True
    snapshot_dir = tmp_path / "snapshots"
    metadata_dir = tmp_path / "metadata"
    _stage(snapshot_dir, {"snap.json": json.dumps(snapshot_payload)})
    _stage(metadata_dir, {"meta.json": FIXTURE_DIR / "e2e_event_metadata.json"})

    report = hunt_structural_basket_candidates_files(
        snapshots_dir=snapshot_dir,
        metadata_dir=metadata_dir,
        manifest_dir=None,
        json_output=tmp_path / "hunt.json",
        markdown_output=tmp_path / "hunt.md",
        manifest_template_output_dir=tmp_path / "templates",
        generated_at=NOW,
    )
    # When reference-only fails closed, structural detector may produce a
    # NOT_EXHAUSTIVE_EVIDENCE row whose first blocker normalizes to
    # reference_only_source — so the ladder must record REFERENCE_ONLY_BLOCKED
    # for that row.
    statuses = {entry["status"] for entry in report["profit_readiness_ladder"] if entry["count"] > 0}
    assert LADDER_REFERENCE_ONLY_BLOCKED in statuses or LADDER_NEEDS_EVENT_METADATA in statuses
    # Whatever rung it lands on, the row must be marked do_not_paper_simulate_yet.
    for group in report["closest_groups_to_review"]:
        if group["profit_readiness"] == LADDER_READY_STOP_FOR_REVIEW:
            continue
        assert group["do_not_paper_simulate_yet"] is True


def test_no_metadata_snapshot_lands_on_needs_valid_manifest_when_template_written(tmp_path: Path) -> None:
    snapshot_dir = tmp_path / "snapshots"
    metadata_dir = tmp_path / "metadata_empty"
    metadata_dir.mkdir()
    template_dir = tmp_path / "templates"
    _stage(snapshot_dir, {"snap.json": FIXTURE_DIR / "e2e_snapshot.json"})

    report = hunt_structural_basket_candidates_files(
        snapshots_dir=snapshot_dir,
        metadata_dir=metadata_dir,
        manifest_dir=None,
        json_output=tmp_path / "hunt.json",
        markdown_output=tmp_path / "hunt.md",
        manifest_template_output_dir=template_dir,
        generated_at=NOW,
    )
    statuses = {entry["status"] for entry in report["profit_readiness_ladder"] if entry["count"] > 0}
    assert LADDER_NEEDS_VALID_MANIFEST in statuses
    matched = [
        group
        for group in report["closest_groups_to_review"]
        if group["profit_readiness"] == LADDER_NEEDS_VALID_MANIFEST
    ]
    assert matched, "expected at least one NEEDS_VALID_MANIFEST entry"
    sample = matched[0]
    assert sample["manifest_template_path"]
    assert sample["manifest_template_exists"] is True
    assert sample["manifest_template_still_invalid"] is True
    assert sample["do_not_paper_simulate_yet"] is True
    assert "do not paper simulate" in (sample["do_not_paper_simulate_yet_reason"] or "").lower()


def test_no_metadata_snapshot_lands_on_needs_event_metadata_when_no_template_written(tmp_path: Path) -> None:
    snapshot_dir = tmp_path / "snapshots"
    metadata_dir = tmp_path / "metadata_empty"
    metadata_dir.mkdir()
    template_dir = tmp_path / "templates"
    _stage(snapshot_dir, {"snap.json": FIXTURE_DIR / "e2e_snapshot.json"})

    report = hunt_structural_basket_candidates_files(
        snapshots_dir=snapshot_dir,
        metadata_dir=metadata_dir,
        manifest_dir=None,
        json_output=tmp_path / "hunt.json",
        markdown_output=tmp_path / "hunt.md",
        manifest_template_output_dir=template_dir,
        write_templates=False,
        generated_at=NOW,
    )
    statuses = {entry["status"] for entry in report["profit_readiness_ladder"] if entry["count"] > 0}
    assert LADDER_NEEDS_EVENT_METADATA in statuses


def test_next_5_actions_are_actionable_and_review_only(tmp_path: Path) -> None:
    report = _hunt_e2e(tmp_path)
    actions = report["next_5_actions"]
    assert actions, "expected at least one action"
    assert len(actions) <= 5
    # The first action when STOP_FOR_REVIEW exists must be run_paper_simulate.
    first = actions[0]
    assert first["action"] == "run_paper_simulate"
    assert first["review_only_warning"] == REVIEW_ONLY_WARNING
    joined = " ".join(first.get("command_lines") or []).lower()
    assert "simulate-paper-fills" in joined
    # No action ever mentions order placement / wallet / auth / private_key.
    for action in actions:
        text = (action.get("label") or "") + " " + " ".join(action.get("command_lines") or [])
        lowered = text.lower()
        for needle in ("place_order", "submit_order", "cancel_order", "wallet", "private_key", "api_key"):
            assert needle not in lowered, f"action contains banned wording: {needle}"


def test_no_stop_for_review_produces_shortest_blocker_chain(tmp_path: Path) -> None:
    snapshot_dir = tmp_path / "snapshots"
    metadata_dir = tmp_path / "metadata_empty"
    metadata_dir.mkdir()
    _stage(snapshot_dir, {"snap.json": FIXTURE_DIR / "e2e_snapshot.json"})

    report = hunt_structural_basket_candidates_files(
        snapshots_dir=snapshot_dir,
        metadata_dir=metadata_dir,
        manifest_dir=None,
        json_output=tmp_path / "hunt.json",
        markdown_output=tmp_path / "hunt.md",
        manifest_template_output_dir=tmp_path / "templates",
        generated_at=NOW,
    )
    chain = report["shortest_blocker_chain_to_stop_for_review"]
    assert chain is not None
    assert chain["profit_readiness"] in {LADDER_NEEDS_VALID_MANIFEST, LADDER_NEEDS_EVENT_METADATA}
    assert chain["chain"], "expected non-empty step chain"
    assert chain["warning"] == DO_NOT_PAPER_SIMULATE_WARNING
    # No paper-fill simulation occurred because no STOP_FOR_REVIEW.
    assert report["summary"]["paper_fill_rows"] == 0
    assert report["summary"]["ready_stop_for_review_count"] == 0


def test_stop_for_review_present_means_no_shortest_chain(tmp_path: Path) -> None:
    report = _hunt_e2e(tmp_path)
    assert report["summary"]["ready_stop_for_review_count"] >= 1
    # When at least one READY row exists, the chain block is None — the
    # operator should paper-simulate the READY row first.
    assert report["shortest_blocker_chain_to_stop_for_review"] is None


def test_markdown_renders_ladder_next_actions_and_warning_when_no_ready(tmp_path: Path) -> None:
    snapshot_dir = tmp_path / "snapshots"
    metadata_dir = tmp_path / "metadata_empty"
    metadata_dir.mkdir()
    _stage(snapshot_dir, {"snap.json": FIXTURE_DIR / "e2e_snapshot.json"})

    report = hunt_structural_basket_candidates_files(
        snapshots_dir=snapshot_dir,
        metadata_dir=metadata_dir,
        manifest_dir=None,
        json_output=tmp_path / "hunt.json",
        markdown_output=tmp_path / "hunt.md",
        manifest_template_output_dir=tmp_path / "templates",
        generated_at=NOW,
    )
    rendered = render_hunter_markdown(report)
    assert "Profit-readiness ladder" in rendered
    assert "Next 5 actions" in rendered
    assert "Shortest blocker chain" in rendered
    assert "DO NOT paper simulate yet" in rendered
    # No order/auth wording in the rendered markdown.
    lowered = rendered.lower()
    for needle in ("place_order", "submit_order", "cancel_order", "wallet.", "private_key", "api_key"):
        assert needle not in lowered, f"banned wording in markdown: {needle}"


def test_markdown_renders_simulate_command_pair_on_ready_rows(tmp_path: Path) -> None:
    report = _hunt_e2e(tmp_path)
    rendered = render_hunter_markdown(report)
    assert "STOP_FOR_REVIEW — exact saved-file paper-fill commands" in rendered
    assert "simulate-paper-fills" in rendered
    assert REVIEW_ONLY_WARNING in rendered


def test_template_completion_is_detected_re_validates_on_disk(tmp_path: Path) -> None:
    """Once a reviewer manually completes the template, the next hunt run
    must mark currently_invalid=False and required_template_fields=[]. The
    hunter itself never promotes the template — only the validator does."""
    snapshot_dir = tmp_path / "snapshots"
    metadata_dir = tmp_path / "metadata_empty"
    metadata_dir.mkdir()
    template_dir = tmp_path / "templates"
    _stage(snapshot_dir, {"snap.json": FIXTURE_DIR / "e2e_snapshot.json"})

    # First run: write the templates.
    hunt_structural_basket_candidates_files(
        snapshots_dir=snapshot_dir,
        metadata_dir=metadata_dir,
        manifest_dir=None,
        json_output=tmp_path / "hunt1.json",
        markdown_output=tmp_path / "hunt1.md",
        manifest_template_output_dir=template_dir,
        generated_at=NOW,
    )
    # Pick the template for the matching event ticker and rewrite it as a
    # completed local_manifest_v1 manifest (reviewer-validated).
    template_files = list(template_dir.glob("*.template.json"))
    assert template_files, "expected at least one template file"
    target = next(t for t in template_files if "kxe2e-2026-demo" in t.name.lower())
    completed = {
        "schema_version": 1,
        "source": LOCAL_MANIFEST_SOURCE,
        "exhaustive_groups": [
            {
                "venue": "kalshi",
                "source": LOCAL_MANIFEST_SOURCE,
                "trusted_local_manifest": True,
                "group_id": "KXE2E-2026-DEMO",
                "venue_native_event_id": "KXE2E-2026-DEMO",
                "complete": True,
                "is_exhaustive": True,
                "reviewer": "test-reviewer",
                "reviewed_at": "2026-05-24T12:00:00+00:00",
                "evidence_text": "explicit event metadata states all outcomes included",
                "settlement_source_evidence": "Official Kalshi event metadata resolves every listed outcome from the same source.",
                "rules_evidence": "Resolution source is the official Kalshi event metadata.",
                "market_tickers": [
                    "KXE2E-2026-DEMO-ALPHA",
                    "KXE2E-2026-DEMO-BETA",
                    "KXE2E-2026-DEMO-GAMMA",
                ],
                "outcome_list": ["Outcome Alpha", "Outcome Beta", "Outcome Gamma"],
                "outcomes": ["Outcome Alpha", "Outcome Beta", "Outcome Gamma"],
                "expected_outcome_count": 3,
            }
        ],
    }
    target.write_text(json.dumps(completed, indent=2, sort_keys=True), encoding="utf-8")

    # Second run: the same template path now re-validates as currently_invalid=False.
    second = hunt_structural_basket_candidates_files(
        snapshots_dir=snapshot_dir,
        metadata_dir=metadata_dir,
        manifest_dir=None,
        json_output=tmp_path / "hunt2.json",
        markdown_output=tmp_path / "hunt2.md",
        manifest_template_output_dir=template_dir,
        generated_at=NOW,
    )
    completed_suggestions = [
        suggestion
        for suggestion in second["manifest_template_suggestions"]
        if suggestion["template_path"] == str(target)
    ]
    assert completed_suggestions, "expected suggestion for the completed template"
    assert completed_suggestions[0]["currently_invalid"] is False
    assert completed_suggestions[0]["validation_blockers_current"] == []
    assert completed_suggestions[0]["required_template_fields"] == []


def test_safety_block_still_locked_down_after_ladder_addition(tmp_path: Path) -> None:
    report = _hunt_e2e(tmp_path)
    safety = report["safety"]
    assert safety["saved_file_only"] is True
    assert safety["places_orders"] is False
    assert safety["auth_used"] is False
    assert safety["paper_candidate_emitted"] is False
    assert safety["stop_for_review_means_review_only"] is True
    assert safety["templates_are_valid_by_default"] is False


def test_ready_row_is_first_action_and_uses_simulate_paper_fills_command(tmp_path: Path) -> None:
    """Combined: a single READY row must surface as the #1 next-action with
    a simulate-paper-fills command in the rendered markdown."""
    report = _hunt_e2e(tmp_path)
    actions = report["next_5_actions"]
    assert actions[0]["action"] == "run_paper_simulate"
    rendered = render_hunter_markdown(report)
    assert "1. **run_paper_simulate**" in rendered
    assert "simulate-paper-fills" in rendered
