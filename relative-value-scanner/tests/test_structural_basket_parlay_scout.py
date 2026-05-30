from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import scan
from relative_value.structural_basket_parlay_scout import (
    ACTION_CDNA_FILL_FIRST,
    ACTION_MANUAL_REVIEW,
    ACTION_STRUCTURAL_REVIEW,
    ACTION_WATCH,
    B_OTHER_UNMAPPED,
    B_PARLAY_RULES,
    B_STALE_QUOTE,
    build_structural_basket_parlay_scout_report,
    write_structural_basket_parlay_scout_files,
)


NOW = datetime(2026, 5, 29, 15, 0, tzinfo=timezone.utc)


def test_two_team_game_complement_basket_prices_correctly(tmp_path: Path) -> None:
    _write(tmp_path, "kalshi_daily.json", _daily_payload("Kalshi"))
    _write(tmp_path, "poly_daily.json", _daily_payload("Polymarket"))

    report = _build(tmp_path)
    row = next(row for row in report["rows"] if row["basket_type"] == "two_outcome_complement")

    assert row["entry_cost"] == 0.95
    assert row["gross_edge"] == 0.05
    assert row["net_edge"] > 0
    assert row["action"] == ACTION_STRUCTURAL_REVIEW


def test_championship_team_yes_no_cross_venue_basket_prices_correctly(tmp_path: Path) -> None:
    _write(tmp_path, "kalshi_champ.json", _champ_payload("Kalshi"))
    _write(tmp_path, "poly_champ.json", _champ_payload("Polymarket"))

    report = _build(tmp_path)
    rows = [row for row in report["rows"] if row["basket_type"] == "championship_family_synthetic_complement"]

    assert rows
    row = next(row for row in rows if "kalshi YES + polymarket NO" in row["description"])
    assert row["entry_cost"] == 0.97
    assert row["gross_edge"] == 0.03
    assert row["action"] == ACTION_STRUCTURAL_REVIEW


def test_thirty_team_sum_blocked_when_other_no_champion_unmapped(tmp_path: Path) -> None:
    payload = _champ_payload("Polymarket", outcomes=[_outcome(f"Team {i}", f"T{i}", yes_ask="0.01") for i in range(30)])
    payload["other_or_no_champion_rule"] = "Other/no-champion remainder exists but has no mapped quote."
    _write(tmp_path, "poly_champ.json", payload)

    report = _build(tmp_path)
    row = next(row for row in report["rows"] if row["basket_type"] == "mutually_exclusive_family_sum")

    assert B_OTHER_UNMAPPED in row["blockers"]
    assert row["action"] in {ACTION_MANUAL_REVIEW, ACTION_WATCH}


def test_native_parlay_blocked_when_rules_missing(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "parlay.json",
        {
            "platform": "Polymarket",
            "league": "NBA",
            "market_title": "Native same game parlay display",
            "rules_text": "",
            "outcomes": [],
        },
    )

    report = _build(tmp_path)
    row = next(row for row in report["rows"] if row["basket_type"] == "native_parlay_vs_synthetic_parlay")

    assert B_PARLAY_RULES in row["blockers"]
    assert row["action"] == ACTION_MANUAL_REVIEW


def test_cdna_leg_is_fill_first_review_only_never_standard_candidate(tmp_path: Path) -> None:
    _write(tmp_path, "cdna.json", _cdna_payload())
    _write(tmp_path, "kalshi.json", _champ_payload("Kalshi", outcomes=[_outcome("Oklahoma City Thunder", "OKC", no_ask="0.50")]))

    report = _build(tmp_path)
    row = next(row for row in report["rows"] if row["action"] == ACTION_CDNA_FILL_FIRST)

    assert row["basket_type"] == "championship_family_synthetic_complement"
    assert "cdna_executable_size_unverified" in row["blockers"]
    assert row["strict_exact_arb"] is False
    assert row["exact_ready"] is False
    assert row["standard_paper_candidate"] is False
    assert report["standard_paper_candidate_emitted"] is False


def test_no_midpoint_used_for_entry_or_exit(tmp_path: Path) -> None:
    _write(tmp_path, "kalshi_daily.json", _daily_payload("Kalshi", hou_yes_bid="0.01", hou_yes_ask="0.40"))
    _write(tmp_path, "poly_daily.json", _daily_payload("Polymarket", tex_yes_bid="0.02", tex_yes_ask="0.55"))

    report = _build(tmp_path)
    row = next(row for row in report["rows"] if "Kalshi HOU YES + Polymarket TEX YES" in row["description"])

    assert row["entry_cost"] == 0.95
    assert row["current_exit_value"] == 0.03


def test_stale_quote_blocks_review(tmp_path: Path) -> None:
    _write(tmp_path, "kalshi_daily.json", _daily_payload("Kalshi", quote_timestamp="2026-05-29T14:00:00Z"))
    _write(tmp_path, "poly_daily.json", _daily_payload("Polymarket"))

    report = _build(tmp_path)
    row = next(row for row in report["rows"] if row["basket_type"] == "two_outcome_complement")

    assert B_STALE_QUOTE in row["blockers"]
    assert row["action"] == ACTION_WATCH


def test_outputs_do_not_emit_standard_candidate_literal(tmp_path: Path) -> None:
    _write(tmp_path, "kalshi_daily.json", _daily_payload("Kalshi"))
    _write(tmp_path, "poly_daily.json", _daily_payload("Polymarket"))
    json_output = tmp_path / "out.json"
    md_output = tmp_path / "out.md"

    report = write_structural_basket_parlay_scout_files(
        input_dir=tmp_path,
        json_output=json_output,
        markdown_output=md_output,
        generated_at=NOW,
    )

    forbidden = "PAPER" + "_CANDIDATE"
    assert forbidden not in json.dumps(report)
    assert forbidden not in json_output.read_text(encoding="utf-8")
    assert forbidden not in md_output.read_text(encoding="utf-8")
    assert report["exact_ready"] is False
    assert report["exact_ready_rows"] == 0


def test_scan_command_writes_report(tmp_path: Path) -> None:
    _write(tmp_path, "kalshi_daily.json", _daily_payload("Kalshi"))
    _write(tmp_path, "poly_daily.json", _daily_payload("Polymarket"))
    json_output = tmp_path / "out.json"
    md_output = tmp_path / "out.md"

    rc = scan.main(
        [
            "structural-basket-parlay-scout",
            "--input-dir",
            str(tmp_path),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(md_output),
        ]
    )

    assert rc == 0
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["schema_kind"] == "structural_basket_parlay_scout_v1"


def _build(input_dir: Path) -> dict:
    return build_structural_basket_parlay_scout_report(input_dir=input_dir, generated_at=NOW)


def _write(root: Path, name: str, payload: dict) -> Path:
    path = root / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _daily_payload(
    platform: str,
    *,
    quote_timestamp: str = "2026-05-29T14:59:00Z",
    hou_yes_bid: str = "0.39",
    hou_yes_ask: str = "0.40",
    tex_yes_bid: str = "0.54",
    tex_yes_ask: str = "0.55",
) -> dict:
    return {
        "platform": platform,
        "league": "MLB",
        "date_label": "2026-05-29",
        "games": [
            {
                "cross_platform_game_key": "MLB-2026-05-29-HOU-TEX",
                "market_type": "game_winner",
                "away_team": "Houston Astros",
                "home_team": "Texas Rangers",
                "teams": [
                    _game_outcome("Houston Astros", "HOU", yes_bid=hou_yes_bid, yes_ask=hou_yes_ask, quote_timestamp=quote_timestamp),
                    _game_outcome("Texas Rangers", "TEX", yes_bid=tex_yes_bid, yes_ask=tex_yes_ask, quote_timestamp=quote_timestamp),
                ],
            }
        ],
    }


def _game_outcome(team: str, code: str, *, yes_bid: str, yes_ask: str, quote_timestamp: str) -> dict:
    return {
        "team_name": team,
        "team_aliases": [code],
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "yes_bid_size": "100",
        "yes_ask_size": "100",
        "depth_status": "full_clob",
        "quote_timestamp": quote_timestamp,
    }


def _champ_payload(platform: str, *, outcomes: list[dict] | None = None) -> dict:
    return {
        "platform": platform,
        "league": "NBA",
        "requested_season": "2026",
        "actual_market_season": "2026",
        "batch": "championship_futures",
        "market_title": "Pro Basketball Champion 2026",
        "rules_text": "Team wins championship.",
        "settlement_source": "NBA",
        "outcomes": outcomes if outcomes is not None else [_outcome("Oklahoma City Thunder", "OKC")],
    }


def _cdna_payload() -> dict:
    return {
        "platform": "Crypto.com Predict / CDNA",
        "league": "NBA",
        "requested_season": "2026",
        "actual_market_season": "2026",
        "batch": "championship_futures",
        "market_title": "Pro Basketball Champion 2026",
        "outcomes": [
            {
                "team_name": "Oklahoma City Thunder",
                "team_aliases": ["OKC"],
                "outcome_status": "active",
                "contract_id": "contract-okc",
                "display_price": "0.43",
                "display_no_price": "0.58",
                "quote_timestamp": "2026-05-29T14:59:00Z",
                "depth_status": "display_price_only",
            }
        ],
    }


def _outcome(team: str, code: str, *, yes_ask: str = "0.43", no_ask: str = "0.54") -> dict:
    return {
        "team_name": team,
        "team_aliases": [code],
        "outcome_status": "active",
        "market_ticker": f"KXNBA-26-{code}",
        "market_id": f"m-{code}",
        "token_id_yes": f"yes-{code}",
        "token_id_no": f"no-{code}",
        "yes_bid": "0.42",
        "yes_ask": yes_ask,
        "yes_bid_size": "100",
        "yes_ask_size": "100",
        "no_bid": "0.45",
        "no_ask": no_ask,
        "no_bid_size": "100",
        "no_ask_size": "100",
        "depth_status": "full_clob",
        "quote_timestamp": "2026-05-29T14:59:00Z",
    }
