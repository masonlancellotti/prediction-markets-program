from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import scan
from relative_value.three_venue_operator_scout import build_three_venue_operator_scout_report


NOW = datetime(2026, 5, 29, 20, 15, tzinfo=timezone.utc)


def test_scout_evaluates_all_three_venues_in_same_pass(tmp_path: Path) -> None:
    folder = _write_family(tmp_path)

    report = _build(folder, accept_cdna=True)

    lanes = {row["basket_type"] for row in report["rows"]}
    assert {"kalshi_poly", "cdna_kalshi", "cdna_poly"} <= lanes
    assert report["summary_counts"]["kalshi_poly_rows"] == 2
    assert report["summary_counts"]["cdna_kalshi_rows"] == 2
    assert report["summary_counts"]["cdna_poly_rows"] == 2


def test_positive_cdna_kalshi_edge_is_fill_first_with_acceptance(tmp_path: Path) -> None:
    folder = _write_family(tmp_path)

    report = _build(folder, accept_cdna=True, operator_risk_mode="aggressive")
    row = next(row for row in report["rows"] if row["direction"] == "CDNA_YES_KALSHI_NO")

    assert row["action"] == "PAPER_CANDIDATE"
    assert row["paper_candidate"] is True
    assert row["paper_candidate_class"] == "CDNA_FILL_FIRST"
    assert row["candidate_action"] == "FILL_CDNA_FIRST_THEN_HEDGE_EXACT_FILLED_QUANTITY"
    assert row["execution_plan"] == "FILL_CDNA_FIRST_THEN_HEDGE_EXACT_FILLED_QUANTITY"
    assert row["requires_cdna_fill_first"] is True
    assert "cdna_executable_size_unverified" in row["blockers"]
    assert row["strict_exact_arb"] is False
    assert row["exact_ready"] is False


def test_positive_cdna_polymarket_edge_is_fill_first_with_acceptance(tmp_path: Path) -> None:
    folder = _write_family(tmp_path)

    report = _build(folder, accept_cdna=True, operator_risk_mode="aggressive")
    row = next(row for row in report["rows"] if row["direction"] == "CDNA_YES_POLYMARKET_NO")

    assert row["action"] == "PAPER_CANDIDATE"
    assert row["paper_candidate_class"] == "CDNA_FILL_FIRST"
    assert row["basket_type"] == "cdna_poly"
    assert row["strict_exact_arb"] is False
    assert row["exact_ready"] is False


def test_cdna_without_operator_acceptance_is_watch(tmp_path: Path) -> None:
    folder = _write_family(tmp_path)

    report = _build(folder, accept_cdna=False, operator_risk_mode="aggressive")
    row = next(row for row in report["rows"] if row["direction"] == "CDNA_YES_KALSHI_NO")

    assert row["action"] == "WATCH"
    assert row["paper_candidate"] is False
    assert "cdna_operator_acceptance_required" in row["blockers"]


def test_kalshi_polymarket_becomes_paper_candidate_operator_class_but_cdna_never_does(tmp_path: Path) -> None:
    folder = _write_family(tmp_path)

    report = _build(folder, accept_cdna=True, operator_risk_mode="standard")
    kp = next(row for row in report["rows"] if row["direction"] == "KALSHI_YES_POLYMARKET_NO")
    cdna_rows = [row for row in report["rows"] if row["basket_type"].startswith("cdna_")]

    assert kp["action"] == "PAPER_CANDIDATE"
    assert kp["paper_candidate_class"] == "OPERATOR_ACCEPTED_RISK"
    assert all(row["paper_candidate_class"] != "STRICT_EXACT" for row in cdna_rows)
    assert all(row["paper_candidate_class"] != "OPERATOR_ACCEPTED_RISK" for row in cdna_rows)


def test_stale_quotes_block_all_lanes(tmp_path: Path) -> None:
    folder = _write_family(tmp_path, timestamp="2026-05-29T18:00:00Z")

    report = _build(folder, accept_cdna=True, max_age=60, operator_risk_mode="aggressive")

    assert all(row["paper_candidate"] is False for row in report["rows"])
    assert any("stale_quote" in row["blockers"] for row in report["rows"])


def test_missing_complement_quote_blocks_candidate(tmp_path: Path) -> None:
    folder = _write_family(tmp_path, remove_poly_no=True)

    report = _build(folder, accept_cdna=True)
    row = next(row for row in report["rows"] if row["direction"] == "KALSHI_YES_POLYMARKET_NO")

    assert row["action"] == "WATCH"
    assert "missing_quote" in row["blockers"]
    assert "missing_complement_quote" in row["blockers"]


def test_no_midpoint_use_for_entry_cost(tmp_path: Path) -> None:
    folder = _write_family(tmp_path)

    report = _build(folder, accept_cdna=True)
    row = next(row for row in report["rows"] if row["direction"] == "KALSHI_YES_POLYMARKET_NO")

    assert row["leg_1"]["entry_price"] == 0.2
    assert row["leg_2"]["entry_price"] == 0.6
    assert row["entry_cost"] == 0.8
    assert row["gross_edge"] == 0.2


def test_scan_command_writes_three_venue_report(tmp_path: Path) -> None:
    folder = _write_family(tmp_path)
    json_output = tmp_path / "three.json"
    md_output = tmp_path / "three.md"

    rc = scan.main(
        [
            "three-venue-operator-scout",
            "--family-folder",
            str(folder),
            "--include-cdna",
            "--operator-accept-cdna-display-price-risk",
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(md_output),
        ]
    )

    assert rc == 0
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["schema_kind"] == "three_venue_operator_scout_v1"
    assert payload["standard_paper_candidate_rows"] == 0
    assert payload["exact_ready_rows"] == 0


def test_missing_folder_produces_load_diagnostics_and_no_candidate_blocker(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"

    report = build_three_venue_operator_scout_report(
        family_folders=[missing],
        include_cdna=True,
        operator_accept_cdna_display_price_risk=True,
        generated_at=NOW,
    )

    assert report["summary_counts"]["rows"] == 0
    assert report["load_diagnostics"][0]["files_found_count"] == 0
    assert "no_candidate_rows_generated" in report["top_blockers"][0]["blocker"]


def test_unsupported_schema_is_reported_in_load_diagnostics(tmp_path: Path) -> None:
    folder = tmp_path / "unsupported"
    folder.mkdir()
    (folder / "kalshi_raw_evidence.json").write_text(
        json.dumps({"schema_kind": "mystery_schema_v9", "platform": "kalshi", "outcomes": []}),
        encoding="utf-8",
    )

    report = build_three_venue_operator_scout_report(
        family_folders=[folder],
        include_cdna=True,
        operator_accept_cdna_display_price_risk=True,
        generated_at=NOW,
    )

    assert "unsupported_schema:mystery_schema_v9" in report["load_diagnostics"][0]["parse_warnings"]
    assert "unsupported_schema" in report["load_diagnostics"][0]["top_level_blockers"]
    assert "no_candidate_rows_generated" in {item["blocker"] for item in report["top_blockers"]}


def test_ucl_raw_folder_shape_parses_psg_arsenal_outcomes(tmp_path: Path) -> None:
    folder = tmp_path / "ucl"
    folder.mkdir()
    timestamp = "2026-05-29T20:27:00Z"
    (folder / "kalshi_raw_evidence.json").write_text(json.dumps(_ucl_kalshi(timestamp)), encoding="utf-8")
    (folder / "polymarket_raw_evidence.json").write_text(json.dumps(_ucl_polymarket(timestamp)), encoding="utf-8")
    (folder / "cdna_raw_evidence.json").write_text(json.dumps(_ucl_cdna(timestamp)), encoding="utf-8")

    report = build_three_venue_operator_scout_report(
        family_folders=[folder],
        include_cdna=True,
        operator_accept_cdna_display_price_risk=True,
        cdna_operator_size_cap=1,
        generated_at=NOW,
    )

    diag = report["load_diagnostics"][0]
    assert diag["kalshi_outcomes_loaded"] == 2
    assert diag["polymarket_outcomes_loaded"] == 2
    assert diag["cdna_outcomes_loaded"] == 2
    assert {row["outcome_key"] for row in report["rows"]} >= {"psg", "arsenal"}
    assert report["summary_counts"]["rows"] > 0


def _build(folder: Path, *, accept_cdna: bool, max_age: float = 900, operator_risk_mode: str = "conservative") -> dict:
    return build_three_venue_operator_scout_report(
        family_folders=[folder],
        include_cdna=True,
        operator_accept_cdna_display_price_risk=accept_cdna,
        cdna_operator_size_cap=1,
        max_quote_age_seconds=max_age,
        min_available_notional=10,
        operator_risk_mode=operator_risk_mode,
        generated_at=NOW,
    )


def _write_family(tmp_path: Path, *, timestamp: str = "2026-05-29T20:12:00Z", remove_poly_no: bool = False) -> Path:
    folder = tmp_path / "family"
    folder.mkdir()
    poly = _poly_payload(timestamp)
    if remove_poly_no:
        poly["outcomes"][0]["no_ask"] = None
        poly["outcomes"][0]["no_ask_size"] = None
    (folder / "kalshi_raw_evidence.json").write_text(json.dumps(_kalshi_payload(timestamp)), encoding="utf-8")
    (folder / "polymarket_raw_evidence.json").write_text(json.dumps(poly), encoding="utf-8")
    (folder / "cdna_raw_evidence.json").write_text(json.dumps(_cdna_payload(timestamp)), encoding="utf-8")
    return folder


def _base(platform: str, timestamp: str) -> dict:
    return {
        "schema_kind": "test_three_venue_evidence_v1",
        "diagnostic_only": True,
        "platform": platform,
        "category": "sports",
        "market_family": "NBA Champion 2026",
        "market_found": True,
        "quotes": {"quote_timestamp_utc": timestamp},
    }


def _kalshi_payload(timestamp: str) -> dict:
    payload = _base("kalshi", timestamp)
    payload["outcomes"] = [
        {
            "team": "Oklahoma City Thunder",
            "yes_ask": 0.2,
            "yes_ask_size": 100,
            "no_ask": 0.7,
            "no_ask_size": 100,
            "depth_status": "full_clob",
            "quote_timestamp": timestamp,
        }
    ]
    return payload


def _ucl_kalshi(timestamp: str) -> dict:
    payload = _base("kalshi", timestamp)
    payload["market_family"] = "UEFA Champions League Winner 2026"
    payload["market_title"] = "Champions League Winner: PSG vs Arsenal (final)"
    payload["outcomes"] = [
        {"ticker": "KXUCL-26-PSG", "team": "PSG", "yes_bid": 0.55, "status": "active"},
        {"ticker": "KXUCL-26-ARS", "team": "Arsenal", "status": "active"},
    ]
    payload["quotes"] = {
        "quote_timestamp_utc": timestamp,
        "market": "KXUCL-26-PSG",
        "orderbook_fp": {
            "yes_dollars_bids": [["0.55", "100"]],
            "no_dollars_bids": [["0.40", "100"]],
        },
    }
    return payload


def _ucl_polymarket(timestamp: str) -> dict:
    payload = _base("polymarket", timestamp)
    payload["market_family"] = "UEFA Champions League Winner 2026"
    payload["market_title"] = "UEFA Champions League Winner"
    payload["outcomes"] = [
        {"team": "PSG", "bestBid": 0.57, "bestAsk": 0.58},
        {"team": "Arsenal", "bestBid": 0.43, "bestAsk": 0.44},
    ]
    return payload


def _ucl_cdna(timestamp: str) -> dict:
    payload = _base("cdna", timestamp)
    payload["market_family"] = "UEFA Champions League Winner 2026"
    payload["market_title"] = "Champions League Winner 2026"
    payload["outcomes"] = [
        {"team": "Paris Saint-Germain", "yes": "0.58", "no": "0.44", "status": "active"},
        {"team": "Arsenal", "yes": "0.45", "no": "0.57", "status": "active"},
    ]
    return payload


def _poly_payload(timestamp: str) -> dict:
    payload = _base("polymarket", timestamp)
    payload["outcomes"] = [
        {
            "team": "Oklahoma City Thunder",
            "yes_ask": 0.25,
            "yes_ask_size": 100,
            "no_ask": 0.6,
            "no_ask_size": 100,
            "depth_status": "full_clob",
            "quote_timestamp": timestamp,
        }
    ]
    return payload


def _cdna_payload(timestamp: str) -> dict:
    payload = _base("cdna", timestamp)
    payload["outcomes"] = [
        {
            "team": "Oklahoma City Thunder",
            "yes": 0.1,
            "no": 0.72,
            "status": "active",
            "symbol": "OKC",
            "quote_timestamp": timestamp,
            "depth_status": "display_price_only",
        }
    ]
    return payload
