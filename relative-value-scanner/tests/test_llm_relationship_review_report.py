import json
from datetime import datetime, timezone
from pathlib import Path

import scan
from relative_value.llm_relationship_classifier import StubLLMRelationshipClient
from relative_value.llm_relationship_review_report import review_relationship_report_file


NOW = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)


def _relationship() -> dict:
    return {
        "relationship": "SUBSET",
        "same_payoff": False,
        "confidence": 0.95,
        "blocking_reasons": ["sports_competition_scope_mismatch"],
        "manual_review_required": True,
        "source": "deterministic_rules",
    }


def _matcher_report() -> dict:
    return {
        "schema_version": 1,
        "source": "live_snapshot_matcher",
        "pair_count": 1,
        "pairs": [
            {
                "action": "WATCH",
                "polymarket": {"market_id": "poly-1", "question": "Will Team win ALCS?"},
                "kalshi": {"ticker": "KXTEAM", "question": "Will Team win championship?"},
                "contract_relationship": _relationship(),
            }
        ],
    }


def _evaluator_report() -> dict:
    return {
        "schema_version": 1,
        "source": "paper_candidate_evaluator",
        "counts_by_action": {"PAPER_CANDIDATE": 1, "WATCH": 0, "MANUAL_REVIEW": 0},
        "ledger": [
            {
                "candidate_id": "candidate-1",
                "action": "PAPER_CANDIDATE",
                "polymarket": {"market_id": "poly-1", "question": "Will Team win?"},
                "kalshi": {"ticker": "KXTEAM", "question": "Will Team win?"},
                "contract_relationship": {
                    "relationship": "NEAR_EQUIVALENT",
                    "same_payoff": False,
                    "confidence": 0.4,
                    "blocking_reasons": [],
                    "manual_review_required": True,
                    "source": "deterministic_rules",
                },
            }
        ],
    }


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_matcher_report_gets_llm_review_sidecars(tmp_path: Path) -> None:
    input_path = _write(tmp_path / "pairs.json", _matcher_report())
    output_path = tmp_path / "reviewed.json"
    markdown_path = tmp_path / "reviewed.md"

    reviewed = review_relationship_report_file(
        input_path=input_path,
        output_path=output_path,
        markdown_output_path=markdown_path,
        timestamp=NOW,
    )

    pair = reviewed["pairs"][0]
    assert output_path.exists()
    assert markdown_path.exists()
    assert reviewed["llm_relationship_review"]["rows_reviewed"] == 1
    assert pair["contract_relationship"] == _relationship()
    assert pair["action"] == "WATCH"
    assert pair["llm_review"]["source"] == "llm_review_proposal"
    assert pair["llm_review"]["proposal"]["proposed_relationship"] == "NEAR_EQUIVALENT"
    assert pair["llm_review"]["validation_errors"] == []


def test_evaluator_ledger_gets_llm_review_sidecars_and_action_unchanged(tmp_path: Path) -> None:
    input_payload = _evaluator_report()
    input_path = _write(tmp_path / "ledger.json", input_payload)
    output_path = tmp_path / "reviewed.json"

    reviewed = review_relationship_report_file(input_path=input_path, output_path=output_path, timestamp=NOW)

    row = reviewed["ledger"][0]
    assert row["action"] == input_payload["ledger"][0]["action"]
    assert row["contract_relationship"] == input_payload["ledger"][0]["contract_relationship"]
    assert row["llm_review"]["proposal"]["proposed_relationship"] == "NEAR_EQUIVALENT"
    assert reviewed["counts_by_action"] == input_payload["counts_by_action"]


def test_invalid_llm_proposal_forces_manual_review_required(tmp_path: Path) -> None:
    payload = _matcher_report()
    payload["pairs"][0]["contract_relationship"]["manual_review_required"] = False
    input_path = _write(tmp_path / "pairs.json", payload)
    output_path = tmp_path / "reviewed.json"
    client = StubLLMRelationshipClient(response={"same_payoff": True})

    reviewed = review_relationship_report_file(
        input_path=input_path,
        output_path=output_path,
        client=client,
        timestamp=NOW,
    )

    llm_review = reviewed["pairs"][0]["llm_review"]
    assert "forbidden_field:same_payoff" in llm_review["validation_errors"]
    assert llm_review["proposal"] is None
    assert llm_review["combined_manual_review_required"] is True
    assert reviewed["llm_relationship_review"]["validation_error_count"] == 1
    assert reviewed["llm_relationship_review"]["manual_review_escalation_count"] == 1
    assert reviewed["pairs"][0]["contract_relationship"]["manual_review_required"] is False


def test_non_stub_client_is_rejected_clearly(tmp_path: Path) -> None:
    class NonStubClient:
        model_id = "not-stub"
        model_version = "v1"

        def propose_relationship(self, payload):
            return {}

    input_path = _write(tmp_path / "pairs.json", _matcher_report())

    try:
        review_relationship_report_file(
            input_path=input_path,
            output_path=tmp_path / "reviewed.json",
            client=NonStubClient(),
            timestamp=NOW,
        )
    except ValueError as exc:
        assert "only StubLLMRelationshipClient is supported" in str(exc)
    else:
        raise AssertionError("expected non-stub client rejection")


def test_cli_reviews_saved_report_with_stub_only(tmp_path: Path, capsys) -> None:
    input_path = _write(tmp_path / "pairs.json", _matcher_report())
    output_path = tmp_path / "reviewed.json"

    result = scan.main(
        [
            "llm-review-relationships",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--stub",
        ]
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert result == 0
    assert payload["pairs"][0]["action"] == "WATCH"
    assert payload["pairs"][0]["llm_review"]["audit"]["model_id"] == "stub-llm-relationship-classifier"
    assert "llm_review_relationships_status=OK rows_reviewed=1" in capsys.readouterr().out


def test_cli_refuses_non_stub_mode(tmp_path: Path, capsys) -> None:
    input_path = _write(tmp_path / "pairs.json", _matcher_report())

    result = scan.main(["llm-review-relationships", "--input", str(input_path), "--output", str(tmp_path / "out.json")])

    assert result == 1
    assert "only --stub mode is supported" in capsys.readouterr().out


def test_no_paper_or_possible_arb_behavior_changes(tmp_path: Path) -> None:
    input_payload = _evaluator_report()
    input_path = _write(tmp_path / "ledger.json", input_payload)
    output_path = tmp_path / "reviewed.json"

    reviewed = review_relationship_report_file(input_path=input_path, output_path=output_path, timestamp=NOW)

    assert [row["action"] for row in reviewed["ledger"]] == [row["action"] for row in input_payload["ledger"]]
    assert "POSSIBLE_ARB" not in json.dumps(reviewed["llm_relationship_review"])
    assert "PAPER_CANDIDATE" not in json.dumps(reviewed["ledger"][0]["llm_review"])


def test_default_scan_output_remains_unchanged(capsys) -> None:
    result = scan.main([])

    assert result == 0
    assert "relative_value_scan_status=OFFLINE_COMPLETE candidates=7 possible_arbs=0" in capsys.readouterr().out
