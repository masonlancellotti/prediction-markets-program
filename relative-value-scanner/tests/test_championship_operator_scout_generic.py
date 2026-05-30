from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import scan
from relative_value.championship_operator_scout_generic import build_championship_operator_scout_generic_report


NOW = datetime(2026, 5, 29, 9, 30, tzinfo=timezone.utc)


def test_generic_championship_kalshi_polymarket_paper_candidate_with_real_depth(tmp_path: Path) -> None:
    folder = _write_family(tmp_path)

    report = build_championship_operator_scout_generic_report(
        family_folder=folder,
        accept_operator_risk=True,
        operator_risk_mode="standard",
        generated_at=NOW,
    )
    row = next(row for row in report["rows"] if row["direction"] == "KALSHI_YES_POLYMARKET_NO")

    assert row["gross_edge"] == 0.04
    assert row["action"] == "PAPER_CANDIDATE"
    assert row["paper_candidate"] is True
    assert row["paper_candidate_class"] == "OPERATOR_ACCEPTED_RISK"
    assert row["strict_exact_arb"] is False
    assert row["exact_ready"] is False


def test_generic_championship_cdna_leg_is_fill_first_paper_candidate_in_aggressive(tmp_path: Path) -> None:
    folder = _write_family(tmp_path)

    report = build_championship_operator_scout_generic_report(
        family_folder=folder,
        accept_operator_risk=True,
        include_cdna_fill_first=True,
        operator_accept_cdna_display_price_risk=True,
        operator_risk_mode="aggressive",
        generated_at=NOW,
    )
    cdna_rows = [row for row in report["rows"] if row["direction"].startswith("CDNA_")]
    paper_cdna = [row for row in cdna_rows if row.get("paper_candidate")]

    assert paper_cdna, f"expected at least one cdna fill-first paper candidate, got {cdna_rows}"
    row = paper_cdna[0]
    assert row["action"] == "PAPER_CANDIDATE"
    assert row["paper_candidate_class"] == "CDNA_FILL_FIRST"
    assert row["candidate_action"] == "FILL_CDNA_FIRST_THEN_HEDGE_EXACT_FILLED_QUANTITY"
    assert "cdna_display_price_only" in row["blockers"]
    assert row["strict_exact_arb"] is False
    assert row["exact_ready"] is False


def test_generic_championship_other_or_team_count_mismatch_blocks_operator(tmp_path: Path) -> None:
    folder = _write_family(tmp_path, add_extra_polymarket=True)

    report = build_championship_operator_scout_generic_report(
        family_folder=folder,
        accept_operator_risk=True,
        generated_at=NOW,
    )

    assert any("other_outcome_unmapped" in row["blockers"] for row in report["rows"])
    assert report["summary_counts"]["operator_review_rows"] == 0


def test_scan_command_writes_generic_championship_report(tmp_path: Path) -> None:
    folder = _write_family(tmp_path)
    json_output = tmp_path / "generic.json"
    md_output = tmp_path / "generic.md"

    rc = scan.main(
        [
            "championship-operator-scout-generic",
            "--family-folder",
            str(folder),
            "--accept-operator-risk",
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(md_output),
        ]
    )

    assert rc == 0
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["schema_kind"] == "championship_operator_scout_generic_v1"
    assert payload["standard_paper_candidate_rows"] == 0


def _write_family(tmp_path: Path, *, add_extra_polymarket: bool = False) -> Path:
    folder = tmp_path / "nba_champion_2026"
    folder.mkdir()
    poly_outcomes = [_poly_outcome()]
    if add_extra_polymarket:
        for idx in range(31):
            extra = _poly_outcome()
            extra["team_name"] = f"Other Team {idx}"
            extra["yes_ask"] = "0.01"
            extra["no_ask"] = "0.99"
            poly_outcomes.append(extra)
    (folder / "kalshi.json").write_text(json.dumps(_payload("Kalshi", [_kalshi_outcome()])), encoding="utf-8")
    (folder / "polymarket.json").write_text(json.dumps(_payload("Polymarket", poly_outcomes)), encoding="utf-8")
    (folder / "cdna.json").write_text(json.dumps(_payload("Crypto.com Predict / CDNA", [_cdna_outcome()])), encoding="utf-8")
    return folder


def _payload(platform: str, outcomes: list[dict]) -> dict:
    return {
        "schema_kind": "test_championship_evidence_v1",
        "diagnostic_only": True,
        "platform": platform,
        "batch": "championship_futures",
        "league": "NBA",
        "season": "2026",
        "market_family": "nba_champion_2026",
        "outcomes": outcomes,
    }


def _kalshi_outcome() -> dict:
    return {
        "team_name": "Oklahoma City Thunder",
        "market_ticker": "KXNBA-26-OKC",
        "yes_bid": "0.38",
        "yes_ask": "0.40",
        "yes_ask_size": "100",
        "no_bid": "0.60",
        "no_ask": "0.62",
        "no_ask_size": "100",
        "depth_status": "full_clob",
        "quote_timestamp": "2026-05-29T09:25:00Z",
    }


def _poly_outcome() -> dict:
    return {
        "team_name": "Oklahoma City Thunder",
        "market_id": "poly-okc",
        "yes_bid": "0.42",
        "yes_ask": "0.43",
        "yes_ask_size": "100",
        "no_bid": "0.55",
        "no_ask": "0.56",
        "no_ask_size": "100",
        "depth_status": "full_clob",
        "quote_timestamp": "2026-05-29T09:25:00Z",
    }


def _cdna_outcome() -> dict:
    return {
        "team_name": "Oklahoma City Thunder",
        "contract_id": "cdna-okc",
        "symbol": "OKC",
        "outcome_status": "active",
        "display_price": "0.30",
        "display_no_price": "0.64",
        "quote_timestamp": "2026-05-29T09:25:00Z",
        "depth_status": "display_price_only",
    }
