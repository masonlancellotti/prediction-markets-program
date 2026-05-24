import json
from datetime import datetime, timezone
from pathlib import Path

import scan
from relative_value.exact_paper_candidate_universes import (
    UniverseSpec,
    build_exact_paper_candidate_universe_report,
    build_exact_paper_candidate_universe_report_files,
    default_exact_paper_candidate_universe_specs,
    render_exact_paper_candidate_universe_markdown,
)
from relative_value.btc_fed_exact_contracts import (
    BTCThresholdContract,
    FedFomcMeetingContract,
    broad_title_overlap_diagnostic,
    btc_threshold_contract_diagnostic,
    default_btc_fed_contract_diagnostics,
    fed_fomc_contract_diagnostic,
)


NOW = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)


def _snapshot(count: int, venue: str = "polymarket") -> dict:
    return {
        "schema_version": 1,
        "normalized_markets": [
            {
                "venue": venue,
                "market_id": f"{venue}-{index}",
                "ticker": f"KX-{index}" if venue == "kalshi" else None,
                "question": "Will the test happen?",
            }
            for index in range(count)
        ],
    }


def _pairs(count: int = 1) -> dict:
    return {
        "schema_version": 1,
        "pair_count": count,
        "pairs": [
            {
                "polymarket": {"market_id": f"poly-{index}"},
                "kalshi": {"ticker": f"KX-{index}"},
                "ineligibility_reasons": [],
            }
            for index in range(count)
        ],
    }


def _board(blockers: list[str] | None = None, strict_passes: int = 1) -> dict:
    return {
        "schema_version": 1,
        "strict_same_payoff_pass_count": strict_passes,
        "top_blockers": [{"blocker": blocker, "count": 1} for blocker in blockers or []],
        "rows": [],
    }


def _derived(trusted: int = 1) -> dict:
    return {
        "schema_version": 1,
        "pairs": [],
        "same_payoff_evidence_attachment": {
            "trusted_relationship_attached_count": trusted,
        },
    }


def _enriched(count: int = 1, venue: str = "polymarket") -> dict:
    return {
        "schema_version": 1,
        "normalized_markets": _snapshot(count, venue)["normalized_markets"],
        "orderbook_enrichment": {
            "market_count": count,
            "enriched_count": count,
            "unenriched_count": 0,
            "snapshot_warnings": [],
        },
    }


def _stale_enriched(count: int = 1, venue: str = "polymarket") -> dict:
    payload = _enriched(count, venue)
    payload["orderbook_enrichment"]["snapshot_warnings"] = ["stale_snapshot"]
    return payload


def _evaluator(action: str = "WATCH", reason: str = "estimated_net_gap_below_minimum") -> dict:
    counts = {"PAPER_CANDIDATE": 0, "MANUAL_REVIEW": 0, "WATCH": 0}
    counts[action] = 1
    return {
        "schema_version": 1,
        "counts_by_action": counts,
        "ledger": [
            {
                "candidate_id": "poly-1__KX-1",
                "action": action,
                "missed_fill_reason": reason,
                "ineligibility_reasons": [reason] if reason else [],
            }
        ],
    }


def _write(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _btc_snapshot(rows: list[dict], venue: str) -> dict:
    normalized = []
    for index, row in enumerate(rows):
        item = {
            "venue": venue,
            "market_id": row.get("market_id", f"{venue}-btc-{index}"),
            "ticker": row.get("ticker", f"KXBTC-{index}" if venue == "kalshi" else None),
            "question": row["question"],
        }
        for key in ("title", "end_date", "close_time", "settlement_time", "settlement_source", "rules", "description", "raw"):
            if key in row:
                item[key] = row[key]
        normalized.append(item)
    return {"schema_version": 1, "normalized_markets": normalized}


def _btc_spec(tmp_path: Path, polymarket_rows: list[dict], kalshi_rows: list[dict]) -> UniverseSpec:
    return UniverseSpec(
        universe_id="btc_thresholds",
        label="BTC threshold markets",
        category="threshold_binary",
        polymarket_snapshot=_write(tmp_path / "reports" / "live_readonly" / "btc" / "polymarket_live_readonly_snapshot.json", _btc_snapshot(polymarket_rows, "polymarket")),
        kalshi_snapshot=_write(tmp_path / "reports" / "live_readonly" / "btc" / "kalshi_live_readonly_snapshot.json", _btc_snapshot(kalshi_rows, "kalshi")),
        exact_scope={
            "status": "NOT_EXACT_PIPELINE",
            "source_basis": "Saved executable venue inventory only.",
            "date_or_deadline": "UNRESOLVED_FROM_INVENTORY",
            "fed_meeting_or_fomc_event": "NOT_APPLICABLE",
            "threshold_or_numeric_condition": "UNRESOLVED_FROM_INVENTORY",
            "required_exact_keys_present": False,
            "pipeline_classification": "NOT_EXACT_PIPELINE",
            "unresolved_ambiguity": ["BTC exact keys must match before board review."],
            "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
            "title_similarity_settlement_equivalence": False,
            "paper_candidate_emitted": False,
        },
    )


def _btc_row(report: dict) -> dict:
    return next(row for row in report["universes"] if row["universe_id"] == "btc_thresholds")


def _fed_snapshot(rows: list[dict], venue: str) -> dict:
    normalized = []
    for index, row in enumerate(rows):
        item = {
            "venue": venue,
            "market_id": row.get("market_id", f"{venue}-fed-{index}"),
            "ticker": row.get("ticker", f"KXFED-{index}" if venue == "kalshi" else None),
            "question": row["question"],
        }
        for key in ("title", "meeting_date", "decision_date", "settlement_time", "settlement_source", "rules", "description", "raw"):
            if key in row:
                item[key] = row[key]
        normalized.append(item)
    return {"schema_version": 1, "normalized_markets": normalized}


def _fed_spec(tmp_path: Path, polymarket_rows: list[dict], kalshi_rows: list[dict]) -> UniverseSpec:
    return UniverseSpec(
        universe_id="fed_fomc_decisions",
        label="Fed / FOMC exact decision markets",
        category="macro_policy_decision",
        polymarket_snapshot=_write(tmp_path / "reports" / "live_readonly" / "fed" / "polymarket_live_readonly_snapshot.json", _fed_snapshot(polymarket_rows, "polymarket")),
        kalshi_snapshot=_write(tmp_path / "reports" / "live_readonly" / "fed" / "kalshi_live_readonly_snapshot.json", _fed_snapshot(kalshi_rows, "kalshi")),
        exact_scope={
            "status": "NOT_EXACT_PIPELINE",
            "source_basis": "Saved executable venue inventory only.",
            "date_or_deadline": "UNRESOLVED_FROM_INVENTORY",
            "fed_meeting_or_fomc_event": "UNRESOLVED_FROM_INVENTORY",
            "threshold_or_numeric_condition": "UNRESOLVED_FROM_INVENTORY",
            "required_exact_keys_present": False,
            "pipeline_classification": "NOT_EXACT_PIPELINE",
            "unresolved_ambiguity": ["Fed/FOMC exact keys must match before board review."],
            "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
            "title_similarity_settlement_equivalence": False,
            "paper_candidate_emitted": False,
        },
    )


def _fed_row(report: dict) -> dict:
    return next(row for row in report["universes"] if row["universe_id"] == "fed_fomc_decisions")


def test_mlb_universe_with_trusted_relationship_and_fee_gap_blocker(tmp_path: Path) -> None:
    spec = UniverseSpec(
        universe_id="mlb",
        label="MLB",
        category="sports",
        polymarket_snapshot=_write(tmp_path / "poly.json", _snapshot(2, "polymarket")),
        kalshi_snapshot=_write(tmp_path / "kalshi.json", _snapshot(2, "kalshi")),
        pairs=_write(tmp_path / "pairs.json", _pairs()),
        board=_write(tmp_path / "board.json", _board()),
        derived_pairs=_write(tmp_path / "derived.json", _derived(1)),
        evaluator=_write(tmp_path / "eval.json", _evaluator()),
        polymarket_enriched=_write(tmp_path / "poly_enriched.json", _enriched(1, "polymarket")),
        kalshi_enriched=_write(tmp_path / "kalshi_enriched.json", _enriched(1, "kalshi")),
    )

    report = build_exact_paper_candidate_universe_report(specs=[spec], generated_at=NOW)
    row = report["universes"][0]

    assert row["readiness"] == "EXECUTION_DATA_AVAILABLE"
    assert row["inventory_available"] is True
    assert row["same_scope_pairs_available"] is True
    assert row["strict_same_payoff_passes"] == 1
    assert row["trusted_relationships_attached"] == 1
    assert row["fresh_orderbook_enrichment_available"] is True
    assert row["evaluator_ready"] is True
    assert row["paper_candidates_count"] == 0
    assert row["paper_review_notice"] is None
    assert row["preflight"]["universe"] == "mlb"
    assert row["preflight"]["paper_count"] == 0
    assert row["preflight"]["watch_manual_review_count"] == 1
    assert row["preflight"]["fee_model_names"] == {
        "polymarket": "PolymarketConservativeFeeModel",
        "kalshi": "KalshiTieredFeeModel",
    }
    assert row["preflight"]["quote_freshness_status"]["status"] == "available"
    assert row["preflight"]["settlement_normalization_trust"]["status"] == "absent"
    assert row["trusted_relationship_count"] == 1
    assert row["evaluator_counts"]["WATCH"] == 1
    assert "fee_adjusted_gap_below_minimum" in row["blockers"]
    assert row["top_fail_closed_reasons"] == ["fee_adjusted_gap_below_minimum"]
    assert report["summary"]["closest_universe_id"] == "mlb"
    assert report["summary"]["next_universe_by_strict_criteria"] == "mlb"


def test_closest_universe_ranking_contract_is_conservative_and_fail_closed(tmp_path: Path) -> None:
    inventory_only = UniverseSpec(
        universe_id="inventory_only",
        label="Inventory only",
        category="sports",
        polymarket_snapshot=_write(tmp_path / "inventory_poly.json", _snapshot(3, "polymarket")),
    )
    blocked_same_scope = UniverseSpec(
        universe_id="blocked_same_scope",
        label="Blocked same scope",
        category="sports",
        polymarket_snapshot=_write(tmp_path / "blocked_poly.json", _snapshot(2, "polymarket")),
        kalshi_snapshot=_write(tmp_path / "blocked_kalshi.json", _snapshot(2, "kalshi")),
        pairs=_write(tmp_path / "blocked_pairs.json", _pairs(2)),
        board=_write(tmp_path / "blocked_board.json", _board(["settlement_source_mismatch"], strict_passes=0)),
        evaluator=_write(tmp_path / "blocked_eval.json", _evaluator("MANUAL_REVIEW", "relationship_same_payoff_not_proven")),
    )

    report = build_exact_paper_candidate_universe_report(specs=[inventory_only, blocked_same_scope], generated_at=NOW)
    rows = report["universes"]

    assert report["summary"]["closest_universe_id"] == "blocked_same_scope"
    assert report["summary"]["closest_readiness"] == "SAME_SCOPE_PAIRS_AVAILABLE"
    blocked_row = next(row for row in rows if row["universe_id"] == "blocked_same_scope")
    inventory_row = next(row for row in rows if row["universe_id"] == "inventory_only")

    assert blocked_row["readiness"] == "SAME_SCOPE_PAIRS_AVAILABLE"
    assert blocked_row["evaluator_counts"]["MANUAL_REVIEW"] == 1
    assert blocked_row["evaluator_counts"]["PAPER_CANDIDATE"] == 0
    assert blocked_row["trusted_relationship_count"] == 0
    assert "same_payoff_board_blockers" in blocked_row["blockers"]
    assert inventory_row["readiness"] == "INVENTORY_ONLY"
    assert report["summary"]["paper_candidate_count"] == 0


def test_mlb_execution_data_with_stale_orderbooks_recommends_paper_check_refresh(tmp_path: Path) -> None:
    spec = UniverseSpec(
        universe_id="mlb_world_series_kxmlb",
        label="MLB World Series / KXMLB",
        category="sports",
        polymarket_snapshot=_write(tmp_path / "poly.json", _snapshot(2, "polymarket")),
        kalshi_snapshot=_write(tmp_path / "kalshi.json", _snapshot(2, "kalshi")),
        pairs=_write(tmp_path / "pairs.json", _pairs()),
        board=_write(tmp_path / "board.json", _board()),
        derived_pairs=_write(tmp_path / "derived.json", _derived(1)),
        evaluator=_write(tmp_path / "eval.json", _evaluator()),
        polymarket_enriched=_write(tmp_path / "poly_enriched.json", _stale_enriched(1, "polymarket")),
        kalshi_enriched=_write(tmp_path / "kalshi_enriched.json", _enriched(1, "kalshi")),
    )

    row = build_exact_paper_candidate_universe_report(specs=[spec], generated_at=NOW)["universes"][0]

    assert row["readiness"] == "EXECUTION_DATA_AVAILABLE"
    assert "stale_orderbooks" in row["blockers"]
    assert row["recommended_next_commands"] == [
        "python scan.py run-mlb-world-series-paper-check "
        "--polymarket-snapshot reports/live_readonly/mlb/polymarket_live_readonly_snapshot.json "
        "--kalshi-snapshot reports/live_readonly/mlb/kalshi_live_readonly_snapshot.json "
        "--rebuild-pairs-from-snapshots "
        "--accept-unit-mismatch --trust-settlement-normalization mlb_world_series_timezone_convention_drift"
    ]


def test_same_scope_inventory_without_trusted_relationship_is_reported(tmp_path: Path) -> None:
    spec = UniverseSpec(
        universe_id="nba",
        label="NBA",
        category="sports",
        polymarket_snapshot=_write(tmp_path / "poly.json", _snapshot(1, "polymarket")),
        kalshi_snapshot=_write(tmp_path / "kalshi.json", _snapshot(1, "kalshi")),
        pairs=_write(tmp_path / "pairs.json", _pairs()),
        board=_write(tmp_path / "board.json", _board(["settlement_source_mismatch"], strict_passes=0)),
        derived_pairs=_write(tmp_path / "derived.json", _derived(0)),
        polymarket_enriched=_write(tmp_path / "poly_enriched.json", _enriched(1, "polymarket")),
        kalshi_enriched=_write(tmp_path / "kalshi_enriched.json", _enriched(1, "kalshi")),
    )

    row = build_exact_paper_candidate_universe_report(specs=[spec], generated_at=NOW)["universes"][0]

    assert row["readiness"] == "SAME_SCOPE_PAIRS_AVAILABLE"
    assert row["inventory_available"] is True
    assert row["same_scope_pairs_available"] is True
    assert row["strict_same_payoff_passes"] == 0
    assert row["trusted_relationships_attached"] == 0
    assert row["fresh_orderbook_enrichment_available"] is True
    assert row["evaluator_ready"] is False
    assert row["paper_candidates_count"] == 0
    assert row["same_scope_pair_count"] == 1
    assert row["trusted_relationship_count"] == 0
    assert "same_payoff_board_blockers" in row["blockers"]
    assert row["recommended_next_commands"] == [
        f"python scan.py same-payoff-board --pairs {spec.pairs} --polymarket-enriched {spec.polymarket_enriched} --kalshi-enriched {spec.kalshi_enriched}"
    ]


def test_nhl_same_scope_without_trusted_relationship_remains_fail_closed(tmp_path: Path) -> None:
    spec = UniverseSpec(
        universe_id="nhl_stanley_cup_kxnhl",
        label="NHL Stanley Cup / KXNHL",
        category="sports_championship_outright",
        polymarket_snapshot=_write(tmp_path / "poly.json", _snapshot(2, "polymarket")),
        kalshi_snapshot=_write(tmp_path / "kalshi.json", _snapshot(2, "kalshi")),
        pairs=_write(tmp_path / "pairs.json", _pairs(2)),
        board=_write(tmp_path / "board.json", _board(["settlement_time_mismatch"], strict_passes=0)),
        derived_pairs=_write(tmp_path / "derived.json", _derived(0)),
        polymarket_enriched=_write(tmp_path / "poly_enriched.json", _enriched(2, "polymarket")),
        kalshi_enriched=_write(tmp_path / "kalshi_enriched.json", _enriched(2, "kalshi")),
    )

    report = build_exact_paper_candidate_universe_report(specs=[spec], generated_at=NOW)
    row = report["universes"][0]

    assert row["readiness"] == "SAME_SCOPE_PAIRS_AVAILABLE"
    assert row["inventory_available"] is True
    assert row["same_scope_pairs_available"] is True
    assert row["strict_same_payoff_passes"] == 0
    assert row["trusted_relationships_attached"] == 0
    assert row["evaluator_ready"] is False
    assert row["paper_candidates_count"] == 0
    assert "same_payoff_board_blockers" in row["top_fail_closed_reasons"]
    assert report["summary"]["next_universe_by_strict_criteria"] is None


def test_execution_rows_without_trusted_relationship_stays_same_scope_available(tmp_path: Path) -> None:
    spec = UniverseSpec(
        universe_id="nba",
        label="NBA Champion / KXNBA",
        category="sports_championship_outright",
        polymarket_snapshot=_write(tmp_path / "poly.json", _snapshot(4, "polymarket")),
        kalshi_snapshot=_write(tmp_path / "kalshi.json", _snapshot(4, "kalshi")),
        pairs=_write(tmp_path / "pairs.json", _pairs(4)),
        board=_write(tmp_path / "board.json", _board(["settlement_wording_mismatch"], strict_passes=0)),
        derived_pairs=_write(tmp_path / "derived.json", _derived(0)),
        evaluator=_write(tmp_path / "eval.json", _evaluator()),
        polymarket_enriched=_write(tmp_path / "poly_enriched.json", _enriched(4, "polymarket")),
        kalshi_enriched=_write(tmp_path / "kalshi_enriched.json", _enriched(4, "kalshi")),
    )

    row = build_exact_paper_candidate_universe_report(specs=[spec], generated_at=NOW)["universes"][0]

    assert row["readiness"] == "SAME_SCOPE_PAIRS_AVAILABLE"
    assert row["same_scope_pair_count"] == 4
    assert row["execution_data_row_count"] == 1
    assert row["trusted_relationship_count"] == 0
    assert "same_payoff_board_blockers" in row["blockers"]
    assert row["recommended_next_commands"] == [
        f"python scan.py same-payoff-board --pairs {spec.pairs} --polymarket-enriched {spec.polymarket_enriched} --kalshi-enriched {spec.kalshi_enriched}"
    ]


def test_reference_only_sources_cannot_be_executable_legs(tmp_path: Path) -> None:
    spec = UniverseSpec(
        universe_id="reference",
        label="Reference",
        category="sports",
        polymarket_snapshot=_write(tmp_path / "poly.json", _snapshot(1, "polymarket")),
        kalshi_snapshot=_write(tmp_path / "kalshi.json", _snapshot(1, "kalshi")),
        pairs=_write(tmp_path / "pairs.json", _pairs()),
        board=_write(tmp_path / "board.json", _board(["polymarket_not_executable_kalshi_polymarket_leg"], strict_passes=0)),
    )

    row = build_exact_paper_candidate_universe_report(specs=[spec], generated_at=NOW)["universes"][0]

    assert row["readiness"] == "SAME_SCOPE_PAIRS_AVAILABLE"
    assert "reference_only_source" in row["blockers"]


def test_subset_superset_stays_non_paper_readiness(tmp_path: Path) -> None:
    spec = UniverseSpec(
        universe_id="btc",
        label="BTC",
        category="threshold",
        polymarket_snapshot=_write(tmp_path / "poly.json", _snapshot(1, "polymarket")),
        kalshi_snapshot=_write(tmp_path / "kalshi.json", _snapshot(1, "kalshi")),
        pairs=_write(tmp_path / "pairs.json", _pairs()),
        board=_write(tmp_path / "board.json", _board(["relationship_shape_subset_or_superset"], strict_passes=0)),
        evaluator=_write(tmp_path / "eval.json", _evaluator("MANUAL_REVIEW", "relationship_same_payoff_not_proven")),
    )

    row = build_exact_paper_candidate_universe_report(specs=[spec], generated_at=NOW)["universes"][0]

    assert row["readiness"] == "SAME_SCOPE_PAIRS_AVAILABLE"
    assert row["evaluator_counts"]["MANUAL_REVIEW"] == 1
    assert "scope_mismatch" in row["blockers"]
    assert row["evaluator_counts"]["PAPER_CANDIDATE"] == 0


def test_report_does_not_emit_disallowed_action_labels_without_existing_candidate(tmp_path: Path) -> None:
    spec = UniverseSpec(
        universe_id="empty",
        label="Empty",
        category="sports",
    )

    report = build_exact_paper_candidate_universe_report(specs=[spec], generated_at=NOW)
    encoded = json.dumps(report)

    assert '"PAPER"' not in encoded
    assert "POSSIBLE_ARB" not in encoded
    assert "trade" not in encoded.lower()


def test_command_writes_reports_and_default_scan_remains_static_fixture(tmp_path: Path, capsys) -> None:
    result = build_exact_paper_candidate_universe_report_files(
        project_root=tmp_path,
        json_output_path=tmp_path / "universes.json",
        markdown_output_path=tmp_path / "universes.md",
        specs=[UniverseSpec(universe_id="empty", label="Empty", category="sports")],
        generated_at=NOW,
    )

    assert result["universes"][0]["readiness"] == "NO_INVENTORY"
    assert (tmp_path / "universes.json").exists()
    assert (tmp_path / "universes.md").exists()

    scan_result = scan.main([])
    assert scan_result == 0
    assert "data_source_mode=STATIC_FIXTURE" in capsys.readouterr().out


def test_discover_exact_universes_cli_prints_compact_readiness_table(tmp_path: Path, monkeypatch, capsys) -> None:
    def fake_report_files(**kwargs):
        payload = build_exact_paper_candidate_universe_report(
            specs=[
                UniverseSpec(
                    universe_id="mlb_world_series_kxmlb",
                    label="MLB World Series / KXMLB",
                    category="sports",
                    polymarket_snapshot=_write(tmp_path / "poly.json", _snapshot(1, "polymarket")),
                    kalshi_snapshot=_write(tmp_path / "kalshi.json", _snapshot(1, "kalshi")),
                    pairs=_write(tmp_path / "pairs.json", _pairs()),
                    board=_write(tmp_path / "board.json", _board()),
                    derived_pairs=_write(tmp_path / "derived.json", _derived(1)),
                    evaluator=_write(tmp_path / "eval.json", _evaluator()),
                    polymarket_enriched=_write(tmp_path / "poly_enriched.json", _enriched(1, "polymarket")),
                    kalshi_enriched=_write(tmp_path / "kalshi_enriched.json", _enriched(1, "kalshi")),
                )
            ],
            generated_at=NOW,
        )
        kwargs["json_output_path"].write_text(json.dumps(payload), encoding="utf-8")
        kwargs["markdown_output_path"].write_text(render_exact_paper_candidate_universe_markdown(payload), encoding="utf-8")
        return payload

    monkeypatch.setattr(scan, "build_exact_paper_candidate_universe_report_files", fake_report_files)

    result = scan.main(
        [
            "discover-exact-paper-candidate-universes",
            "--json-output",
            str(tmp_path / "out.json"),
            "--markdown-output",
            str(tmp_path / "out.md"),
        ]
    )

    assert result == 0
    stdout = capsys.readouterr().out
    assert "next_strict=mlb_world_series_kxmlb" in stdout
    assert "universe | inventory | universe_paths | pairs | strict | trusted | fresh_ob | evaluator | paper | review | top_fail_closed" in stdout
    assert "mlb_world_series_kxmlb | true | true | 1 | 1 | 1 | true | true | 0 | none | fee_adjusted_gap_below_minimum" in stdout


def test_exact_readiness_preflight_warns_on_generic_live_readonly_paths(tmp_path: Path) -> None:
    generic_dir = tmp_path / "reports" / "live_readonly"
    spec = UniverseSpec(
        universe_id="mlb_world_series_kxmlb",
        label="MLB World Series / KXMLB",
        category="sports",
        polymarket_snapshot=_write(generic_dir / "polymarket_live_readonly_snapshot.json", _snapshot(1, "polymarket")),
        kalshi_snapshot=_write(generic_dir / "kalshi_live_readonly_snapshot.json", _snapshot(1, "kalshi")),
        pairs=_write(tmp_path / "pairs.json", _pairs()),
        evaluator=_write(tmp_path / "eval.json", _evaluator("PAPER_CANDIDATE", None)),
    )

    report = build_exact_paper_candidate_universe_report(specs=[spec], generated_at=NOW)
    row = report["universes"][0]
    markdown = render_exact_paper_candidate_universe_markdown(report)

    assert row["paper_review_notice"] == "STOP_FOR_REVIEW"
    assert row["preflight"]["paths_are_universe_specific"] is False
    assert row["preflight"]["generic_live_readonly_warning"].startswith("GENERIC_LIVE_READONLY_PATH_USED")
    assert "STOP_FOR_REVIEW" in markdown
    assert "Generic live_readonly warning" in markdown


def test_exact_readiness_surfaces_stale_legacy_top_level_report_sources(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    spec = UniverseSpec(
        universe_id="mlb_world_series_kxmlb",
        label="MLB World Series / KXMLB",
        category="sports",
        polymarket_snapshot=_write(reports / "mlb_old_polymarket_snapshot.json", _snapshot(1, "polymarket")),
        kalshi_snapshot=_write(reports / "mlb_old_kalshi_snapshot.json", _snapshot(1, "kalshi")),
        pairs=_write(reports / "mlb_world_series_pairs_fresh.json", _pairs()),
        board=_write(reports / "mlb_world_series_same_payoff_board.json", _board()),
        derived_pairs=_write(reports / "mlb_world_series_pairs_with_evidence.json", _derived(1)),
        evaluator=_write(reports / "mlb_world_series_evaluator_fresh_trust_settlement.json", _evaluator()),
    )

    row = build_exact_paper_candidate_universe_report(specs=[spec], generated_at=NOW)["universes"][0]

    assert row["preflight"]["legacy_top_level_report_warning"].startswith("LEGACY_TOP_LEVEL_REPORT_PATHS")
    assert "discover_exact_readiness_is_reading_legacy_top_level_reports" in row["preflight"]["authoritative_fresh_run_hint"]
    assert row["preflight"]["report_source_paths"]["evaluator"]["exists"] is True
    assert row["preflight"]["report_source_paths"]["evaluator"]["modified_at"] is not None


def test_default_recommended_commands_use_universe_specific_live_readonly_dirs(tmp_path: Path) -> None:
    specs = default_exact_paper_candidate_universe_specs(tmp_path)
    commands = " ".join(command for spec in specs for command in (spec.recommended_fetch_command, spec.recommended_pair_command) if command)

    assert "reports/live_readonly/mlb" in commands
    assert "reports/live_readonly/nba" in commands
    assert "reports/live_readonly/nhl" in commands
    assert "reports/live_readonly/btc" in commands
    assert "reports/live_readonly/fed" in commands
    assert "--output-dir reports/live_readonly --report-dir reports/live_readonly" not in commands


def test_btc_and_fed_default_inventory_exposes_exact_scope_fields(tmp_path: Path) -> None:
    report = build_exact_paper_candidate_universe_report(
        specs=default_exact_paper_candidate_universe_specs(tmp_path),
        generated_at=NOW,
    )
    rows = {row["universe_id"]: row for row in report["universes"]}

    for universe_id in ("btc_thresholds", "fed_fomc_decisions"):
        scope = rows[universe_id]["exact_scope"]
        assert scope["status"] == "NOT_EXACT_PIPELINE"
        assert scope["pipeline_classification"] == "NOT_EXACT_PIPELINE"
        assert scope["required_exact_keys_present"] is False
        assert scope["source_basis"]
        assert scope["date_or_deadline"]
        assert scope["fed_meeting_or_fomc_event"]
        assert scope["threshold_or_numeric_condition"]
        assert scope["unresolved_ambiguity"]
        assert scope["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
        assert scope["title_similarity_settlement_equivalence"] is False
        assert scope["paper_candidate_emitted"] is False

    assert rows["btc_thresholds"]["exact_scope"]["threshold_or_numeric_condition"] == "UNRESOLVED_FROM_INVENTORY"
    assert rows["fed_fomc_decisions"]["exact_scope"]["fed_meeting_or_fomc_event"] == "UNRESOLVED_FROM_INVENTORY"
    assert report["summary"]["paper_candidate_count"] == 0
    assert report["summary"]["exact_scope_status_counts"] == {"NOT_EXACT_PIPELINE": 2, "WATCH": 4}
    assert report["summary"]["exact_scope_unresolved_count"] == 2
    assert report["safety"]["title_similarity_used_as_settlement_equivalence"] is False


def test_btc_fed_exact_scope_markdown_stays_inventory_only(tmp_path: Path) -> None:
    report = build_exact_paper_candidate_universe_report(
        specs=default_exact_paper_candidate_universe_specs(tmp_path),
        generated_at=NOW,
    )
    markdown = render_exact_paper_candidate_universe_markdown(report)

    assert "BTC / Fed Exact Scope Inventory" in markdown
    assert "Exact-scope status counts" in markdown
    assert "Exact-scope unresolved inventories" in markdown
    assert "UNRESOLVED_FROM_INVENTORY" in markdown
    assert "NOT_EXACT_PIPELINE" in markdown
    assert "Broad Fed/FOMC title overlap is inventory evidence only, not settlement equivalence." in markdown
    assert '"PAPER_CANDIDATE"' not in json.dumps([row["exact_scope"] for row in report["universes"]])
    assert "POSSIBLE_ARB" not in markdown


def test_btc_exact_threshold_key_match_is_ready_for_board_not_paper(tmp_path: Path) -> None:
    spec = _btc_spec(
        tmp_path,
        [
            {
                "question": "Will Bitcoin be above $100,000 on June 30, 2026?",
                "end_date": "2026-06-30T23:59:00+00:00",
                "settlement_source": "Coinbase BTC/USD",
            }
        ],
        [
            {
                "question": "Will BTC be above $100,000 on June 30, 2026?",
                "ticker": "KXBTC-100K-26JUN30",
                "close_time": "2026-06-30T23:59:00+00:00",
                "settlement_source": "Coinbase BTC/USD",
            }
        ],
    )

    row = _btc_row(build_exact_paper_candidate_universe_report(specs=[spec], generated_at=NOW))
    btc = row["btc_exact_threshold_readiness"]

    assert btc["summary"]["btc_inventory_count"] == 2
    assert btc["summary"]["typed_btc_formula_count"] == 2
    assert btc["summary"]["exact_key_match_count"] == 1
    assert btc["summary"]["paper_candidate_count"] == 0
    assert btc["exact_key_matches"][0]["classification"] == "READY_FOR_BOARD"
    assert btc["exact_key_matches"][0]["paper_candidate_emitted"] is False
    assert row["exact_scope"]["status"] == "MANUAL_REVIEW"
    assert row["exact_scope"]["pipeline_classification"] == "READY_FOR_BOARD"
    assert row["paper_candidates_count"] == 0
    assert row["evaluator_counts"]["PAPER_CANDIDATE"] == 0


def test_btc_same_threshold_different_date_or_source_rejected(tmp_path: Path) -> None:
    spec = _btc_spec(
        tmp_path,
        [
            {
                "question": "Will Bitcoin be above $100,000 on June 30, 2026?",
                "end_date": "2026-06-30T23:59:00+00:00",
                "settlement_source": "Coinbase BTC/USD",
            }
        ],
        [
            {
                "question": "Will BTC be above $100,000 on July 31, 2026?",
                "close_time": "2026-07-31T23:59:00+00:00",
                "settlement_source": "Binance BTC/USDT",
            }
        ],
    )

    row = _btc_row(build_exact_paper_candidate_universe_report(specs=[spec], generated_at=NOW))
    btc = row["btc_exact_threshold_readiness"]

    assert btc["summary"]["typed_btc_formula_count"] == 2
    assert btc["summary"]["exact_key_match_count"] == 0
    assert btc["summary"]["threshold_ladder_count"] == 0
    assert row["exact_scope"]["pipeline_classification"] == "NOT_EXACT_PIPELINE"
    assert row["exact_scope"]["paper_candidate_emitted"] is False


def test_btc_different_threshold_same_date_source_is_ladder_not_exact(tmp_path: Path) -> None:
    spec = _btc_spec(
        tmp_path,
        [
            {
                "question": "Will Bitcoin be above $100,000 on June 30, 2026?",
                "end_date": "2026-06-30T23:59:00+00:00",
                "settlement_source": "Coinbase BTC/USD",
            }
        ],
        [
            {
                "question": "Will BTC be above $120,000 on June 30, 2026?",
                "close_time": "2026-06-30T23:59:00+00:00",
                "settlement_source": "Coinbase BTC/USD",
            }
        ],
    )

    row = _btc_row(build_exact_paper_candidate_universe_report(specs=[spec], generated_at=NOW))
    btc = row["btc_exact_threshold_readiness"]

    assert btc["summary"]["exact_key_match_count"] == 0
    assert btc["summary"]["threshold_ladder_count"] == 1
    assert btc["summary"]["not_exact_pipeline_count"] == 1
    assert btc["threshold_ladder_examples"][0]["classification"] == "NOT_EXACT_PIPELINE"
    assert "threshold_ladder_not_exact_payoff" in btc["threshold_ladder_examples"][0]["blockers"]
    assert row["paper_candidates_count"] == 0


def test_btc_missing_source_and_date_are_blocked_separately(tmp_path: Path) -> None:
    spec = _btc_spec(
        tmp_path,
        [{"question": "Will Bitcoin be above $100,000?"}],
        [{"question": "Will BTC be above $100,000?"}],
    )

    btc = _btc_row(build_exact_paper_candidate_universe_report(specs=[spec], generated_at=NOW))["btc_exact_threshold_readiness"]

    assert btc["summary"]["btc_inventory_count"] == 2
    assert btc["summary"]["typed_btc_formula_count"] == 2
    assert btc["summary"]["exact_key_match_count"] == 0
    assert btc["summary"]["missing_source_count"] == 2
    assert btc["summary"]["missing_date_count"] == 2
    blockers = {item["blocker"] for item in btc["top_blockers"]}
    assert {"missing_source_index", "missing_date_window"}.issubset(blockers)


def test_btc_broad_text_overlap_remains_not_exact_pipeline(tmp_path: Path) -> None:
    spec = _btc_spec(
        tmp_path,
        [{"question": "Bitcoin price by year-end"}],
        [{"question": "BTC above X by date Y"}],
    )

    row = _btc_row(build_exact_paper_candidate_universe_report(specs=[spec], generated_at=NOW))
    btc = row["btc_exact_threshold_readiness"]

    assert btc["summary"]["exact_key_match_count"] == 0
    assert btc["summary"]["typed_btc_formula_count"] == 0
    assert btc["summary"]["not_exact_pipeline_count"] == 2
    assert all(contract["classification"] == "NOT_EXACT_PIPELINE" for contract in btc["contracts"])
    assert "broad_text_overlap_not_exact_pipeline" in {blocker for contract in btc["contracts"] for blocker in contract["blockers"]}
    assert row["exact_scope"]["title_similarity_settlement_equivalence"] is False
    assert row["exact_scope"]["paper_candidate_emitted"] is False


def test_btc_threshold_scaffolding_never_emits_paper_candidate(tmp_path: Path) -> None:
    spec = _btc_spec(
        tmp_path,
        [
            {
                "question": "Will Bitcoin be above $100,000 on June 30, 2026?",
                "end_date": "2026-06-30T23:59:00+00:00",
                "settlement_source": "Coinbase BTC/USD",
            }
        ],
        [
            {
                "question": "Will BTC be above $100,000 on June 30, 2026?",
                "close_time": "2026-06-30T23:59:00+00:00",
                "settlement_source": "Coinbase BTC/USD",
            }
        ],
    )

    report = build_exact_paper_candidate_universe_report(specs=[spec], generated_at=NOW)
    encoded = json.dumps(report)
    row = _btc_row(report)

    assert report["summary"]["paper_candidate_count"] == 0
    assert row["paper_candidates_count"] == 0
    assert row["btc_exact_threshold_readiness"]["safety"]["paper_candidate_count"] == 0
    assert row["btc_exact_threshold_readiness"]["safety"]["affects_evaluator_gates"] is False
    assert '"paper_candidate_emitted": true' not in encoded


def test_fed_exact_meeting_range_match_is_ready_for_board_not_paper(tmp_path: Path) -> None:
    spec = _fed_spec(
        tmp_path,
        [
            {
                "question": "Will the Federal Reserve target rate range be 4.25 to 4.50% after the June 17, 2026 FOMC meeting?",
                "meeting_date": "2026-06-17T18:00:00+00:00",
                "settlement_time": "2026-06-17T18:00:00+00:00",
                "settlement_source": "Federal Reserve FOMC target range",
            }
        ],
        [
            {
                "question": "Will the Fed funds target range be 4.25-4.50% for the June 17, 2026 FOMC meeting?",
                "ticker": "KXFED-26JUN17-425-450",
                "meeting_date": "2026-06-17T18:00:00+00:00",
                "settlement_time": "2026-06-17T18:00:00+00:00",
                "settlement_source": "Federal Reserve FOMC target range",
            }
        ],
    )

    row = _fed_row(build_exact_paper_candidate_universe_report(specs=[spec], generated_at=NOW))
    fed = row["fed_fomc_exact_range_readiness"]

    assert fed["summary"]["fed_inventory_count"] == 2
    assert fed["summary"]["typed_fed_formula_count"] == 2
    assert fed["summary"]["exact_meeting_range_match_count"] == 1
    assert fed["summary"]["paper_candidate_count"] == 0
    assert fed["exact_meeting_range_matches"][0]["classification"] == "READY_FOR_BOARD"
    assert fed["exact_meeting_range_matches"][0]["paper_candidate_emitted"] is False
    assert row["exact_scope"]["status"] == "MANUAL_REVIEW"
    assert row["exact_scope"]["pipeline_classification"] == "READY_FOR_BOARD"
    assert row["paper_candidates_count"] == 0
    assert row["evaluator_counts"]["PAPER_CANDIDATE"] == 0


def test_fed_different_meeting_rejected(tmp_path: Path) -> None:
    spec = _fed_spec(
        tmp_path,
        [
            {
                "question": "Will the Federal Reserve target rate range be 4.25 to 4.50% after the June 17, 2026 FOMC meeting?",
                "meeting_date": "2026-06-17T18:00:00+00:00",
                "settlement_source": "Federal Reserve FOMC target range",
            }
        ],
        [
            {
                "question": "Will the Fed funds target range be 4.25-4.50% for the July 29, 2026 FOMC meeting?",
                "meeting_date": "2026-07-29T18:00:00+00:00",
                "settlement_source": "Federal Reserve FOMC target range",
            }
        ],
    )

    row = _fed_row(build_exact_paper_candidate_universe_report(specs=[spec], generated_at=NOW))
    fed = row["fed_fomc_exact_range_readiness"]

    assert fed["summary"]["typed_fed_formula_count"] == 2
    assert fed["summary"]["exact_meeting_range_match_count"] == 0
    assert fed["summary"]["different_meeting_count"] == 1
    assert fed["different_meeting_examples"][0]["classification"] == "NOT_EXACT_PIPELINE"
    assert "different_meeting_date" in fed["different_meeting_examples"][0]["blockers"]
    assert row["exact_scope"]["pipeline_classification"] == "NOT_EXACT_PIPELINE"


def test_fed_overlapping_non_identical_range_is_diagnostic_only(tmp_path: Path) -> None:
    spec = _fed_spec(
        tmp_path,
        [
            {
                "question": "Will the Federal Reserve target rate range be 4.25 to 4.75% after the June 17, 2026 FOMC meeting?",
                "meeting_date": "2026-06-17T18:00:00+00:00",
                "settlement_source": "Federal Reserve FOMC target range",
            }
        ],
        [
            {
                "question": "Will the Fed funds target range be 4.50-5.00% for the June 17, 2026 FOMC meeting?",
                "meeting_date": "2026-06-17T18:00:00+00:00",
                "settlement_source": "Federal Reserve FOMC target range",
            }
        ],
    )

    row = _fed_row(build_exact_paper_candidate_universe_report(specs=[spec], generated_at=NOW))
    fed = row["fed_fomc_exact_range_readiness"]

    assert fed["summary"]["exact_meeting_range_match_count"] == 0
    assert fed["summary"]["overlapping_range_count"] == 1
    assert fed["summary"]["not_exact_pipeline_count"] == 1
    assert fed["overlapping_range_examples"][0]["classification"] == "NOT_EXACT_PIPELINE"
    assert "overlap_not_identical_range" in fed["overlapping_range_examples"][0]["blockers"]
    assert row["paper_candidates_count"] == 0


def test_fed_missing_meeting_and_range_are_blocked_separately(tmp_path: Path) -> None:
    spec = _fed_spec(
        tmp_path,
        [{"question": "Will the Fed target rate change after the next FOMC meeting?"}],
        [{"question": "Will the Federal Reserve set interest rates after the next FOMC?"}],
    )

    fed = _fed_row(build_exact_paper_candidate_universe_report(specs=[spec], generated_at=NOW))["fed_fomc_exact_range_readiness"]

    assert fed["summary"]["fed_inventory_count"] == 2
    assert fed["summary"]["typed_fed_formula_count"] == 0
    assert fed["summary"]["exact_meeting_range_match_count"] == 0
    assert fed["summary"]["missing_meeting_count"] == 2
    assert fed["summary"]["missing_range_count"] == 2
    blockers = {item["blocker"] for item in fed["top_blockers"]}
    assert {"missing_meeting_date", "missing_range"}.issubset(blockers)


def test_fed_broad_text_overlap_remains_not_exact_pipeline(tmp_path: Path) -> None:
    spec = _fed_spec(
        tmp_path,
        [{"question": "Fed decision after next FOMC"}],
        [{"question": "Interest rates after meeting"}],
    )

    row = _fed_row(build_exact_paper_candidate_universe_report(specs=[spec], generated_at=NOW))
    fed = row["fed_fomc_exact_range_readiness"]

    assert fed["summary"]["exact_meeting_range_match_count"] == 0
    assert fed["summary"]["typed_fed_formula_count"] == 0
    assert fed["summary"]["not_exact_pipeline_count"] == 2
    assert all(contract["classification"] == "NOT_EXACT_PIPELINE" for contract in fed["contracts"])
    assert "broad_text_overlap_not_exact_pipeline" in {blocker for contract in fed["contracts"] for blocker in contract["blockers"]}
    assert row["exact_scope"]["title_similarity_settlement_equivalence"] is False
    assert row["exact_scope"]["paper_candidate_emitted"] is False


def test_fed_range_scaffolding_never_emits_paper_candidate(tmp_path: Path) -> None:
    spec = _fed_spec(
        tmp_path,
        [
            {
                "question": "Will the Federal Reserve target rate range be 4.25 to 4.50% after the June 17, 2026 FOMC meeting?",
                "meeting_date": "2026-06-17T18:00:00+00:00",
                "settlement_source": "Federal Reserve FOMC target range",
            }
        ],
        [
            {
                "question": "Will the Fed funds target range be 4.25-4.50% for the June 17, 2026 FOMC meeting?",
                "meeting_date": "2026-06-17T18:00:00+00:00",
                "settlement_source": "Federal Reserve FOMC target range",
            }
        ],
    )

    report = build_exact_paper_candidate_universe_report(specs=[spec], generated_at=NOW)
    encoded = json.dumps(report)
    row = _fed_row(report)

    assert report["summary"]["paper_candidate_count"] == 0
    assert row["paper_candidates_count"] == 0
    assert row["fed_fomc_exact_range_readiness"]["safety"]["paper_candidate_count"] == 0
    assert row["fed_fomc_exact_range_readiness"]["safety"]["affects_evaluator_gates"] is False
    assert '"paper_candidate_emitted": true' not in encoded


def test_incomplete_btc_contract_stays_fail_closed_and_non_paper() -> None:
    diagnostic = btc_threshold_contract_diagnostic(
        BTCThresholdContract(
            date_or_deadline="2026-12-31 23:59 America/New_York",
            comparator="above",
            observation_window="close at deadline",
        )
    )

    assert diagnostic["status"] in {"WATCH", "MANUAL_REVIEW"}
    assert set(diagnostic["missing_required_fields"]) == {"source_basis", "threshold", "reference_price_index"}
    assert diagnostic["paper_candidate_emitted"] is False
    assert diagnostic["possible_arbitrage_claim"] is False
    assert diagnostic["executable_leg_claim"] is False
    assert diagnostic["tradable_result_claim"] is False


def test_incomplete_fed_fomc_contract_stays_fail_closed_and_non_paper() -> None:
    diagnostic = fed_fomc_contract_diagnostic(
        FedFomcMeetingContract(decision_date_or_deadline="2026-06-17 14:00 America/New_York")
    )

    assert diagnostic["status"] in {"WATCH", "MANUAL_REVIEW"}
    assert set(diagnostic["missing_required_fields"]) == {
        "fomc_meeting_identity",
        "source_basis",
        "rate_or_bp_condition",
        "settlement_wording",
    }
    assert diagnostic["paper_candidate_emitted"] is False
    assert diagnostic["possible_arbitrage_claim"] is False
    assert diagnostic["executable_leg_claim"] is False
    assert diagnostic["tradable_result_claim"] is False


def test_broad_title_overlap_alone_cannot_promote_contracts() -> None:
    diagnostic = broad_title_overlap_diagnostic(
        "Bitcoin price by year-end",
        "BTC above X by date Y",
    )

    assert diagnostic["status"] == "MANUAL_REVIEW"
    assert diagnostic["title_similarity_settlement_equivalence"] is False
    assert diagnostic["paper_candidate_emitted"] is False
    assert diagnostic["possible_arbitrage_claim"] is False


def test_reference_only_or_sportsbook_source_never_claims_executable_leg() -> None:
    btc = btc_threshold_contract_diagnostic(
        BTCThresholdContract(
            source_basis="Sportsbook reference odds",
            date_or_deadline="2026-12-31 23:59 UTC",
            threshold="100000",
            comparator="above",
            observation_window="deadline print",
            reference_price_index="reference feed",
            source_kind="sportsbook",
        )
    )
    fed = fed_fomc_contract_diagnostic(
        FedFomcMeetingContract(
            fomc_meeting_identity="June 2026 FOMC",
            decision_date_or_deadline="2026-06-17 14:00 America/New_York",
            source_basis="Reference-only macro calendar",
            rate_or_bp_condition="25 bp cut",
            settlement_wording="reference text only",
            source_kind="reference_only",
        )
    )

    for diagnostic in (btc, fed):
        assert diagnostic["status"] == "MANUAL_REVIEW"
        assert "reference_only_source" in diagnostic["blockers"]
        assert diagnostic["paper_candidate_emitted"] is False
        assert diagnostic["executable_leg_claim"] is False


def test_default_btc_fed_diagnostics_do_not_claim_candidates_or_tradable_results() -> None:
    payload = default_btc_fed_contract_diagnostics()
    encoded = json.dumps(payload).lower()

    assert payload["paper_candidate_count"] == 0
    assert payload["safety"]["paper_candidate_emitted"] is False
    assert payload["safety"]["possible_arbitrage_claim"] is False
    assert payload["safety"]["executable_signal_claim"] is False
    assert payload["safety"]["tradable_result_claim"] is False
    assert "possible arbitrage" not in encoded
    assert "executable signal" not in encoded
    assert "tradable result" not in encoded
