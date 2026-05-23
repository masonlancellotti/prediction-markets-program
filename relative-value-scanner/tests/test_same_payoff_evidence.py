from __future__ import annotations

import copy
import json
from pathlib import Path

import scan
from relative_value.same_payoff_evidence import (
    SAME_PAYOFF_BOARD_CLASSIFIER_VERSION,
    SAME_PAYOFF_BOARD_SOURCE,
    attach_same_payoff_evidence,
)


def _pair_payload() -> dict:
    return {
        "schema_version": 1,
        "source": "live_snapshot_matcher",
        "generated_at": "2026-05-23T11:59:00+00:00",
        "pair_count": 2,
        "pairs": [
            {
                "action": "MANUAL_REVIEW",
                "polymarket": {"market_id": "poly-pass", "question": "Will Team A win?"},
                "kalshi": {"ticker": "KXTEAMA", "question": "Will Team A win?"},
                "contract_relationship": {
                    "relationship": "NEAR_EQUIVALENT",
                    "same_payoff": False,
                    "confidence": 0.4,
                    "blocking_reasons": [],
                    "manual_review_required": True,
                    "source": "deterministic_rules",
                },
            },
            {
                "action": "WATCH",
                "polymarket": {"market_id": "poly-block", "question": "Will Team B win?"},
                "kalshi": {"ticker": "KXTEAMB", "question": "Will Team B win?"},
                "contract_relationship": {
                    "relationship": "NEAR_EQUIVALENT",
                    "same_payoff": False,
                    "confidence": 0.4,
                    "blocking_reasons": ["settlement_source_mismatch"],
                    "manual_review_required": True,
                    "source": "deterministic_rules",
                },
            },
        ],
    }


def _board_payload() -> dict:
    return {
        "schema_version": 1,
        "source": "same_payoff_candidate_board",
        "generated_at": "2026-05-23T12:00:00+00:00",
        "row_count": 2,
        "rows": [
            _board_row("poly-pass", "KXTEAMA", same_payoff=True),
            _board_row(
                "poly-block",
                "KXTEAMB",
                same_payoff=False,
                blockers=["settlement_source_mismatch"],
                missing_fields=[],
            ),
        ],
    }


def _board_row(
    poly_id: str,
    kalshi_ticker: str,
    *,
    same_payoff: bool,
    blockers: list[str] | None = None,
    missing_fields: list[str] | None = None,
    strict_blockers: list[str] | None = None,
    strict_missing_fields: list[str] | None = None,
    info_blockers: list[str] | None = None,
    info_missing_fields: list[str] | None = None,
    include_strict_fields: bool = True,
    extra_evidence: dict | None = None,
) -> dict:
    blockers = blockers or []
    missing_fields = missing_fields or []
    strict_count = 11
    evidence = {
        "reference_only_blocker": {
            "name": "reference_only_blocker",
            "status": "PASS",
            "strict": True,
            "blockers": [],
            "missing_fields": [],
            "values": {},
        }
    }
    if extra_evidence:
        evidence.update(extra_evidence)
    row = {
        "polymarket": {"market_id": poly_id, "question": "Will Team win?"},
        "kalshi": {"ticker": kalshi_ticker, "question": "Will Team win?"},
        "same_payoff": same_payoff,
        "strict_pass_count": strict_count if same_payoff else strict_count - 1,
        "strict_comparator_count": strict_count,
        "blockers": blockers,
        "missing_fields": missing_fields,
        "same_payoff_evidence": evidence,
    }
    if include_strict_fields:
        row["strict_blockers"] = strict_blockers or []
        row["strict_missing_fields"] = strict_missing_fields or []
        row["info_blockers"] = info_blockers or []
        row["info_missing_fields"] = info_missing_fields or []
    return row


def test_attach_same_payoff_evidence_returns_derived_payload_without_mutating_original() -> None:
    pairs = _pair_payload()
    before = copy.deepcopy(pairs)

    derived = attach_same_payoff_evidence(pairs_payload=pairs, board_payload=_board_payload())

    assert pairs == before
    assert derived is not pairs
    assert derived["same_payoff_evidence_attachment"]["original_pairs_mutated"] is False
    assert derived["pairs"][0]["contract_relationship"]["relationship"] == "EQUIVALENT"
    assert pairs["pairs"][0]["contract_relationship"]["relationship"] == "NEAR_EQUIVALENT"


def test_board_cleared_pair_gets_trusted_equivalent_only_in_derived_payload() -> None:
    derived = attach_same_payoff_evidence(pairs_payload=_pair_payload(), board_payload=_board_payload())

    relationship = derived["pairs"][0]["contract_relationship"]
    assert relationship["relationship"] == "EQUIVALENT"
    assert relationship["same_payoff"] is True
    assert relationship["blocking_reasons"] == []
    assert relationship["source"] == SAME_PAYOFF_BOARD_SOURCE
    evidence = relationship["same_payoff_board_evidence"]
    assert evidence["classifier_version"] == SAME_PAYOFF_BOARD_CLASSIFIER_VERSION
    assert evidence["strict_pass_count"] == evidence["strict_comparator_count"]
    assert evidence["board_row_id"] == "poly-pass__KXTEAMA"
    assert evidence["evidence_hash"]


def test_non_cleared_pair_keeps_existing_relationship() -> None:
    derived = attach_same_payoff_evidence(pairs_payload=_pair_payload(), board_payload=_board_payload())

    relationship = derived["pairs"][1]["contract_relationship"]
    assert relationship["relationship"] == "NEAR_EQUIVALENT"
    assert relationship["blocking_reasons"] == ["settlement_source_mismatch"]
    assert relationship["same_payoff_board_evidence"]["passed"] is False


def test_info_blockers_do_not_block_trusted_relationship_attachment() -> None:
    board = _board_payload()
    board["rows"][0] = _board_row(
        "poly-pass",
        "KXTEAMA",
        same_payoff=True,
        blockers=["polymarket_stale_quote"],
        missing_fields=["kalshi_fee_model_or_rate"],
        strict_blockers=[],
        strict_missing_fields=[],
        info_blockers=["polymarket_stale_quote"],
        info_missing_fields=["kalshi_fee_model_or_rate"],
    )

    derived = attach_same_payoff_evidence(pairs_payload=_pair_payload(), board_payload=board)

    relationship = derived["pairs"][0]["contract_relationship"]
    assert relationship["relationship"] == "EQUIVALENT"
    assert relationship["same_payoff"] is True
    assert relationship["blocking_reasons"] == []
    assert derived["same_payoff_evidence_attachment"]["trusted_relationship_attached_count"] == 1


def test_strict_missing_field_blocks_trusted_relationship() -> None:
    board = _board_payload()
    board["rows"][0] = _board_row(
        "poly-pass",
        "KXTEAMA",
        same_payoff=True,
        missing_fields=["settlement_source"],
        strict_missing_fields=["settlement_source"],
    )

    derived = attach_same_payoff_evidence(pairs_payload=_pair_payload(), board_payload=board)

    relationship = derived["pairs"][0]["contract_relationship"]
    assert relationship["relationship"] == "NEAR_EQUIVALENT"
    assert relationship["same_payoff_board_evidence"]["passed"] is False


def test_legacy_board_row_derives_strict_blockers_from_comparator_flags() -> None:
    board = _board_payload()
    board["rows"][0] = _board_row(
        "poly-pass",
        "KXTEAMA",
        same_payoff=True,
        blockers=["polymarket_stale_quote"],
        include_strict_fields=False,
        extra_evidence={
            "polymarket_quote_depth": {
                "name": "polymarket_quote_depth",
                "status": "INFO_BLOCKED",
                "strict": False,
                "blockers": ["polymarket_stale_quote"],
                "missing_fields": [],
                "values": {},
            }
        },
    )

    derived = attach_same_payoff_evidence(pairs_payload=_pair_payload(), board_payload=board)

    assert derived["pairs"][0]["contract_relationship"]["relationship"] == "EQUIVALENT"
    assert derived["same_payoff_evidence_attachment"]["trusted_relationship_attached_count"] == 1


def test_ambiguous_board_identity_does_not_promote() -> None:
    board = _board_payload()
    board["rows"].append(copy.deepcopy(board["rows"][0]))

    derived = attach_same_payoff_evidence(pairs_payload=_pair_payload(), board_payload=board)

    assert derived["pairs"][0]["contract_relationship"]["relationship"] == "NEAR_EQUIVALENT"
    assert derived["same_payoff_evidence_attachment"]["ambiguous_identity_count"] == 1


def test_attach_same_payoff_evidence_cli_writes_derived_file_not_original(tmp_path: Path, capsys) -> None:
    pairs_path = tmp_path / "pairs.json"
    board_path = tmp_path / "board.json"
    output_path = tmp_path / "derived_pairs.json"
    pairs_payload = _pair_payload()
    pairs_path.write_text(json.dumps(pairs_payload, indent=2), encoding="utf-8")
    board_path.write_text(json.dumps(_board_payload(), indent=2), encoding="utf-8")
    original_text = pairs_path.read_text(encoding="utf-8")

    result = scan.main(
        [
            "attach-same-payoff-evidence",
            "--pairs",
            str(pairs_path),
            "--board",
            str(board_path),
            "--output",
            str(output_path),
        ]
    )

    assert result == 0
    assert pairs_path.read_text(encoding="utf-8") == original_text
    derived = json.loads(output_path.read_text(encoding="utf-8"))
    assert derived["same_payoff_evidence_attachment"]["trusted_relationship_attached_count"] == 1
    stdout = capsys.readouterr().out
    assert "same_payoff_evidence_attach_status=OK" in stdout
    assert "PAPER" not in stdout
    assert "POSSIBLE_ARB" not in stdout
    assert "trade" not in stdout.lower()
