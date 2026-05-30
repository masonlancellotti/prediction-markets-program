from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import scan
from relative_value.cdna_fill_first_scout import (
    ACTION_DISPLAY_REVIEW,
    ACTION_FILL_CONFIRMED,
    ACTION_FILL_FIRST,
    ACTION_HEDGED_COMPLETE,
    ACTION_IGNORE,
    ACTION_REFERENCE,
    ACTION_WATCH,
    B_EXECUTABLE_SIZE_UNVERIFIED,
    B_FILL_HISTORY_INSUFFICIENT,
    B_PARTNER_COMPLEMENT_MISSING,
    B_QUOTE_STALE,
    build_cdna_fill_first_scout_report,
    write_cdna_fill_first_scout_files,
)


NOW = datetime(2026, 5, 29, 15, 5, tzinfo=timezone.utc)


def test_indicative_edge_math_negative_edge_reference_only(tmp_path: Path) -> None:
    cdna, partner = _write_evidence(tmp_path, partner_outcomes=[_partner_outcome(no_ask="0.56")])

    report = _build(cdna, partner, accept=True)
    row = _row(report, "CDNA_YES_PARTNER_NO")

    assert row["cdna_leg"]["all_in_cost_per_contract"] == 0.45
    assert row["partner_leg"]["ask"] == 0.56
    assert row["pre_fill_edge"]["gross"] == -0.01
    assert row["recommended_action"] == ACTION_REFERENCE


def test_positive_edge_without_operator_acceptance_is_display_price_review(tmp_path: Path) -> None:
    cdna, partner = _write_evidence(tmp_path)

    report = _build(cdna, partner, accept=False)
    row = _row(report, "CDNA_YES_PARTNER_NO")

    assert row["pre_fill_edge"]["gross"] == 0.03
    assert row["pre_fill_edge"]["net"] == 0.01
    assert row["recommended_action"] == ACTION_DISPLAY_REVIEW


def test_positive_edge_with_acceptance_and_cap_is_fill_first_review(tmp_path: Path) -> None:
    cdna, partner = _write_evidence(tmp_path)

    report = _build(cdna, partner, accept=True)
    row = _row(report, "CDNA_YES_PARTNER_NO")

    assert row["recommended_action"] == ACTION_FILL_FIRST
    assert B_EXECUTABLE_SIZE_UNVERIFIED in row["blockers"]
    assert B_FILL_HISTORY_INSUFFICIENT in row["blockers"]
    assert row["strict_exact_arb"] is False
    assert row["exact_ready"] is False
    assert row["paper_candidate"] is False


def test_partner_complement_missing_is_ignore_blocked(tmp_path: Path) -> None:
    cdna, partner = _write_evidence(tmp_path, partner_outcomes=[])

    report = _build(cdna, partner, accept=True)
    row = _row(report, "CDNA_YES_PARTNER_NO")

    assert row["recommended_action"] == ACTION_IGNORE
    assert B_PARTNER_COMPLEMENT_MISSING in row["blockers"]


def test_mirror_liquidity_cap_uses_min_partner_quantity_and_operator_cap(tmp_path: Path) -> None:
    cdna, partner = _write_evidence(tmp_path, partner_outcomes=[_partner_outcome(no_ask_size="0.4")])

    report = _build(cdna, partner, accept=True, size_cap=1.0)
    row = _row(report, "CDNA_YES_PARTNER_NO")

    assert row["mirror_liquidity_cap"]["partner_executable_quantity_within_slippage_cap"] == 0.4
    assert row["mirror_liquidity_cap"]["cdna_assumed_fill_quantity"] == 1.0
    assert row["mirror_liquidity_cap"]["max_operator_quantity"] == 0.4


def test_fill_record_present_requires_partner_hedge_for_actual_filled_quantity(tmp_path: Path) -> None:
    cdna, partner = _write_evidence(tmp_path)
    fill_log = tmp_path / "fills.json"
    fill_log.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "schema_kind": "cdna_manual_fill_record_v1",
                        "event_key": "NBA_CHAMPION_2026",
                        "team": "Oklahoma City Thunder",
                        "side": "YES",
                        "contract_id": "contract-okc",
                        "symbol": "NBA-OKC",
                        "requested_quantity": 2,
                        "filled_quantity": 1,
                        "filled_price_per_contract": 0.43,
                        "fill_fee_per_contract": 0.02,
                        "all_in_filled_cost": 0.45,
                        "residual_unhedged_cdna_quantity": 1,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = _build(cdna, partner, accept=True, fill_log=fill_log)
    row = _row(report, "CDNA_YES_PARTNER_NO")

    assert row["recommended_action"] == ACTION_FILL_CONFIRMED
    assert row["fill_record"]["filled_quantity"] == 1
    assert row["fill_record"]["residual_unhedged_cdna_quantity"] == 1


def test_fill_and_hedge_record_is_hedged_complete(tmp_path: Path) -> None:
    cdna, partner = _write_evidence(tmp_path)
    fill_log = tmp_path / "fills.json"
    fill_log.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "schema_kind": "cdna_manual_fill_record_v1",
                        "event_key": "NBA_CHAMPION_2026",
                        "team": "Oklahoma City Thunder",
                        "side": "YES",
                        "contract_id": "contract-okc",
                        "symbol": "NBA-OKC",
                        "requested_quantity": 1,
                        "filled_quantity": 1,
                        "filled_price_per_contract": 0.43,
                        "fill_fee_per_contract": 0.02,
                        "all_in_filled_cost": 0.45,
                        "residual_unhedged_cdna_quantity": 0,
                        "partner_hedge_record": {
                            "schema_kind": "manual_partner_hedge_record_v1",
                            "filled_quantity": 1,
                            "hedge_price": 0.52,
                            "fee_per_contract": 0.02,
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = _build(cdna, partner, accept=True, fill_log=fill_log)
    row = _row(report, "CDNA_YES_PARTNER_NO")

    assert row["recommended_action"] == ACTION_HEDGED_COMPLETE
    assert row["realized_edge"]["status"] == "calculated"


def test_stale_quote_is_watch(tmp_path: Path) -> None:
    cdna, partner = _write_evidence(tmp_path, cdna_quote_timestamp="2026-05-29T14:00:00Z")

    report = _build(cdna, partner, accept=True)
    row = _row(report, "CDNA_YES_PARTNER_NO")

    assert row["recommended_action"] == ACTION_WATCH
    assert B_QUOTE_STALE in row["blockers"]


def test_missing_partner_quote_or_depth_watches(tmp_path: Path) -> None:
    cdna, partner = _write_evidence(tmp_path, partner_outcomes=[_partner_outcome(no_ask_size=None)])

    report = _build(cdna, partner, accept=True)
    row = _row(report, "CDNA_YES_PARTNER_NO")

    assert row["recommended_action"] == ACTION_WATCH
    assert "missing_partner_depth" in row["blockers"]


def test_outputs_never_emit_standard_candidate_literal(tmp_path: Path) -> None:
    cdna, partner = _write_evidence(tmp_path)
    json_output = tmp_path / "out.json"
    markdown_output = tmp_path / "out.md"

    report = write_cdna_fill_first_scout_files(
        cdna_evidence=cdna,
        partner_evidence=partner,
        partner_platform="kalshi",
        market_family="sports_championship_futures",
        league="NBA",
        season="2026",
        operator_accept_display_price_risk=True,
        cdna_operator_size_cap=1,
        max_partner_hedge_slippage=0.01,
        max_quote_age_seconds=900,
        json_output=json_output,
        markdown_output=markdown_output,
        generated_at=NOW,
    )

    forbidden = "PAPER" + "_CANDIDATE"
    assert forbidden not in json.dumps(report)
    assert forbidden not in json_output.read_text(encoding="utf-8")
    assert forbidden not in markdown_output.read_text(encoding="utf-8")
    assert report["exact_ready"] is False
    assert report["paper_candidate"] is False


def test_scan_command_wires_cdna_fill_first_scout(tmp_path: Path) -> None:
    cdna, partner = _write_evidence(tmp_path)
    json_output = tmp_path / "out.json"
    markdown_output = tmp_path / "out.md"

    rc = scan.main(
        [
            "cdna-fill-first-scout",
            "--cdna-evidence",
            str(cdna),
            "--partner-evidence",
            str(partner),
            "--partner-platform",
            "kalshi",
            "--market-family",
            "sports_championship_futures",
            "--league",
            "NBA",
            "--season",
            "2026",
            "--operator-accept-display-price-risk",
            "--cdna-operator-size-cap",
            "1",
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )

    assert rc == 0
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["schema_kind"] == "cdna_fill_first_scout_v1"


def _build(
    cdna: Path,
    partner: Path,
    *,
    accept: bool,
    size_cap: float = 1.0,
    fill_log: Path | None = None,
) -> dict:
    return build_cdna_fill_first_scout_report(
        cdna_evidence=cdna,
        partner_evidence=partner,
        partner_platform="kalshi",
        market_family="sports_championship_futures",
        league="NBA",
        season="2026",
        operator_accept_display_price_risk=accept,
        cdna_operator_size_cap=size_cap,
        max_partner_hedge_slippage=0.01,
        max_quote_age_seconds=900,
        fill_log=fill_log,
        generated_at=NOW,
    )


def _write_evidence(
    tmp_path: Path,
    *,
    partner_outcomes: list[dict] | None = None,
    cdna_quote_timestamp: str = "2026-05-29T15:00:00Z",
) -> tuple[Path, Path]:
    cdna = tmp_path / "cdna.json"
    partner = tmp_path / "partner.json"
    cdna.write_text(json.dumps(_cdna_payload(quote_timestamp=cdna_quote_timestamp)), encoding="utf-8")
    partner.write_text(json.dumps(_partner_payload(outcomes=partner_outcomes)), encoding="utf-8")
    return cdna, partner


def _cdna_payload(*, quote_timestamp: str) -> dict:
    return {
        "schema_kind": "sports_nba_champion_raw_evidence_v1",
        "diagnostic_only": True,
        "platform": "Crypto.com Predict / CDNA",
        "league": "NBA",
        "batch": "championship_futures",
        "settlement_source": "NBA",
        "rules_text": "Champion market rules",
        "outcomes": [
            {
                "team_name": "Oklahoma City Thunder",
                "team_aliases": ["OKC", "Thunder"],
                "contract_id": "contract-okc",
                "symbol": "NBA-OKC",
                "outcome_status": "active",
                "display_price": "0.43",
                "display_no_price": "0.58",
                "quote_timestamp": quote_timestamp,
                "depth_status": "display_price_only",
                "settlement_source": "NBA",
                "rules_text": "Team resolves yes if champion",
            }
        ],
    }


def _partner_payload(outcomes: list[dict] | None = None) -> dict:
    return {
        "schema_kind": "sports_nba_champion_raw_evidence_v1",
        "diagnostic_only": True,
        "platform": "Kalshi",
        "league": "NBA",
        "batch": "championship_futures",
        "outcomes": outcomes if outcomes is not None else [_partner_outcome()],
    }


def _partner_outcome(*, no_ask: str | None = "0.52", no_ask_size: str | None = "100") -> dict:
    return {
        "team_name": "Oklahoma City Thunder",
        "team_aliases": ["OKC", "Thunder"],
        "market_ticker": "KXNBA-26-OKC",
        "yes_bid": "0.42",
        "yes_ask": "0.43",
        "yes_bid_size": "90",
        "yes_ask_size": "100",
        "no_bid": "0.47",
        "no_ask": no_ask,
        "no_bid_size": "100",
        "no_ask_size": no_ask_size,
        "depth_status": "full_clob",
        "quote_timestamp": "2026-05-29T15:00:00Z",
    }


def _row(report: dict, direction: str) -> dict:
    return next(row for row in report["rows"] if row["direction"] == direction)
