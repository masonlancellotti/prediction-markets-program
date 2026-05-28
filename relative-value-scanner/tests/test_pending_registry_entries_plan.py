from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import scan
from relative_value.pending_registry_entries_plan import (
    audit_pending_registry_entries_for_promotion,
    build_pending_registry_entries_plan,
    write_pending_registry_entries_plan,
)


NOW = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)


def test_skeleton_files_carry_todo_placeholders(tmp_path: Path) -> None:
    coverage = _write_coverage(tmp_path, [_scope("CRYPTO_PRICE_THRESHOLD|kalshi|KXBTC|BTC|May 28, 2026")])
    output_dir = tmp_path / "pending"
    plan = write_pending_registry_entries_plan(
        coverage_path=coverage,
        output_dir=output_dir,
        json_output=tmp_path / "plan.json",
        generated_at=NOW,
    )

    assert plan["summary"]["pending_files_written"] == 1
    payload = json.loads(Path(plan["written_files"][0]).read_text(encoding="utf-8"))

    assert payload["reviewer_must_validate"] is True
    assert payload["registry_proposal_is_trust"] is False
    assert payload["reviewed"] is False
    skeleton = payload["registry_entry_skeleton"]
    assert skeleton["canonical_source_url"] == "<TODO: canonical_source_url>"
    assert skeleton["evidence_quote_or_excerpt"] == "<TODO: evidence_quote_or_excerpt>"
    assert skeleton["reviewer"] == "<TODO: reviewer>"
    assert payload["limitations"] == "<TODO: limitations>"
    assert payload["scope_key"] == "CRYPTO_PRICE_THRESHOLD|kalshi|KXBTC|BTC|May 28, 2026"
    assert payload["row_count"] == 12
    assert payload["leverage"] == 12
    assert payload["source_url_candidate_status"] == "hint_unreviewed_must_validate_against_venue_rules"


def test_plan_never_claims_paper_candidate_or_exact_payoff(tmp_path: Path) -> None:
    coverage = _write_coverage(tmp_path, [_scope("CRYPTO_PRICE_THRESHOLD|kalshi|KXBTC|BTC|May 28, 2026")])
    plan = write_pending_registry_entries_plan(
        coverage_path=coverage,
        output_dir=tmp_path / "pending",
        json_output=tmp_path / "plan.json",
        generated_at=NOW,
    )
    encoded_plan = json.dumps(json.loads((tmp_path / "plan.json").read_text(encoding="utf-8")))
    encoded_files = "".join(Path(path).read_text(encoding="utf-8") for path in plan["written_files"])

    assert "PAPER_CANDIDATE" not in encoded_plan
    assert "paper_candidate" not in encoded_plan
    assert "exact_payoff" not in encoded_plan
    assert "PAPER_CANDIDATE" not in encoded_files
    assert "paper_candidate" not in encoded_files
    assert "exact_payoff" not in encoded_files


def test_rerun_with_reviewed_scope_does_not_regenerate_file(tmp_path: Path) -> None:
    scope_key = "CRYPTO_PRICE_THRESHOLD|kalshi|KXBTC|BTC|May 28, 2026"
    coverage = _write_coverage(tmp_path, [_scope(scope_key)])
    output_dir = tmp_path / "pending"
    first = write_pending_registry_entries_plan(
        coverage_path=coverage,
        output_dir=output_dir,
        json_output=tmp_path / "plan.json",
        generated_at=NOW,
    )
    written = Path(first["written_files"][0])
    written.write_text("sentinel", encoding="utf-8")

    reviewed_coverage = _write_coverage(tmp_path, [_scope(scope_key, reviewed=True)])
    second = write_pending_registry_entries_plan(
        coverage_path=reviewed_coverage,
        output_dir=output_dir,
        json_output=tmp_path / "plan_reviewed.json",
        generated_at=NOW,
    )

    assert second["summary"]["pending_files_written"] == 0
    assert second["summary"]["skipped_reviewed_scopes"] == 1
    assert written.read_text(encoding="utf-8") == "sentinel"


def test_safe_filename_generation_prevents_path_traversal(tmp_path: Path) -> None:
    malicious_scope = "../../escape|CRYPTO_PRICE_THRESHOLD|kalshi|BTC"
    coverage = _write_coverage(tmp_path, [_scope(malicious_scope)])
    output_dir = tmp_path / "pending"
    plan = write_pending_registry_entries_plan(
        coverage_path=coverage,
        output_dir=output_dir,
        json_output=tmp_path / "plan.json",
        generated_at=NOW,
    )

    assert plan["summary"]["pending_files_written"] == 1
    output_file = Path(plan["written_files"][0]).resolve()
    output_file.relative_to(output_dir.resolve())
    assert ".." not in output_file.name
    assert "/" not in output_file.name
    assert "\\" not in output_file.name


def test_plan_pending_registry_entries_cli_writes_outputs(tmp_path: Path, capsys) -> None:
    coverage = _write_coverage(tmp_path, [_scope("CRYPTO_PRICE_THRESHOLD|kalshi|KXBTC|BTC|May 28, 2026")])
    output_dir = tmp_path / "pending"
    json_output = tmp_path / "pending_plan.json"

    rc = scan.main(
        [
            "plan-pending-registry-entries",
            "--coverage",
            str(coverage),
            "--output-dir",
            str(output_dir),
            "--json-output",
            str(json_output),
        ]
    )

    stdout = capsys.readouterr().out
    assert rc == 0
    assert "pending_registry_entries_plan_status=OK" in stdout
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["source"] == "pending_registry_entries_plan_v1"
    assert payload["summary"]["pending_files_written"] == 1
    assert len(list(output_dir.glob("*.json"))) == 1


def test_promotion_audit_flags_skeleton_with_todos_as_not_ready(tmp_path: Path) -> None:
    coverage = _write_coverage(tmp_path, [_scope("CRYPTO_PRICE_THRESHOLD|kalshi|KXBTC|BTC|May 28, 2026")])
    output_dir = tmp_path / "pending"
    write_pending_registry_entries_plan(
        coverage_path=coverage,
        output_dir=output_dir,
        json_output=tmp_path / "plan.json",
        generated_at=NOW,
    )

    audit = audit_pending_registry_entries_for_promotion(
        pending_dir=output_dir,
        registry_path=None,
        generated_at=NOW,
    )

    assert audit["summary"]["pending_file_count"] == 1
    assert audit["summary"]["ready_to_promote_count"] == 0
    blockers = set(audit["files"][0]["blockers"])
    assert "skeleton_contains_todo_placeholders" in blockers
    assert "reviewed_flag_still_false" in blockers


def test_promotion_audit_marks_filled_skeleton_ready(tmp_path: Path) -> None:
    output_dir = tmp_path / "pending"
    output_dir.mkdir()
    filled = {
        "schema_version": 1,
        "source": "pending_registry_entry_skeleton_v1",
        "scope_key": "CRYPTO_PRICE_THRESHOLD|kalshi|KXBTC|BTC|May 28, 2026",
        "family": "CRYPTO_PRICE_THRESHOLD",
        "reviewer_must_validate": True,
        "registry_proposal_is_trust": False,
        "reviewed": True,
        "limitations": "Only KXBTC May 28 BRTI scope.",
        "output_file": str(output_dir / "filled.json"),
        "registry_entry_skeleton": {
            "registry_version": "canonical_convention_registry_v0",
            "entry_id": "filled-entry-id",
            "family": "CRYPTO_PRICE_THRESHOLD",
            "reviewer": "operator-manual-review",
            "reviewed_at": "2026-05-25T00:00:00+00:00",
            "review_until": "2026-06-01",
            "applies_to_scope": {"venue": "kalshi", "event_ticker_prefix": "KXBTC"},
            "typed_key_requirements": {
                "required": ["asset", "measurement_date"],
                "match": {"asset": "BTC", "measurement_date": "May 28, 2026"},
            },
            "canonical_source_kind": "crypto_index_official",
            "canonical_source_url": "https://www.cfbenchmarks.com/data/indices/BRTI",
            "official_source_description": "BRTI methodology.",
            "evidence_quote_or_excerpt": {"kind": "manual_review_paraphrase", "text": "BRTI 60-second pre-time average."},
            "limitations": "Only KXBTC May 28 BRTI scope.",
            "confidence": "high",
        },
    }
    (output_dir / "filled.json").write_text(json.dumps(filled), encoding="utf-8")

    audit = audit_pending_registry_entries_for_promotion(
        pending_dir=output_dir,
        registry_path=None,
        generated_at=NOW,
    )

    assert audit["summary"]["ready_to_promote_count"] == 1
    assert audit["files"][0]["blockers"] == []
    assert audit["safety"]["registry_file_modified"] is False


def test_promotion_audit_cli_writes_report(tmp_path: Path, capsys) -> None:
    coverage = _write_coverage(tmp_path, [_scope("CRYPTO_PRICE_THRESHOLD|kalshi|KXBTC|BTC|May 28, 2026")])
    output_dir = tmp_path / "pending"
    write_pending_registry_entries_plan(
        coverage_path=coverage,
        output_dir=output_dir,
        json_output=tmp_path / "plan.json",
        generated_at=NOW,
    )
    audit_json = tmp_path / "promotion_audit.json"

    rc = scan.main(
        [
            "audit-pending-registry-entries-promotion",
            "--pending-dir",
            str(output_dir),
            "--json-output",
            str(audit_json),
        ]
    )

    stdout = capsys.readouterr().out
    assert rc == 0
    assert "pending_registry_entries_promotion_audit_status=OK" in stdout
    payload = json.loads(audit_json.read_text(encoding="utf-8"))
    assert payload["source"] == "pending_registry_entries_promotion_audit_v1"
    assert payload["summary"]["pending_file_count"] == 1
    assert payload["safety"]["registry_file_modified"] is False


def test_build_plan_handles_missing_coverage_without_crashing(tmp_path: Path) -> None:
    plan = build_pending_registry_entries_plan(
        coverage_path=tmp_path / "missing.json",
        output_dir=tmp_path / "pending",
        generated_at=NOW,
    )

    assert plan["summary"]["pending_files_planned"] == 0
    assert plan["warnings"][0]["reason_code"] == "json_file_missing"


def _write_coverage(tmp_path: Path, scopes: list[dict]) -> Path:
    coverage = tmp_path / "canonical_registry_coverage.json"
    coverage.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "canonical_registry_coverage_v1",
                "scopes": scopes,
                "next_manual_review": {
                    "top_unreviewed_scopes": scopes,
                    "registry_proposal_is_trust": False,
                    "human_review_required": True,
                },
            }
        ),
        encoding="utf-8",
    )
    return coverage


def _scope(scope_key: str, *, reviewed: bool = False) -> dict:
    return {
        "scope_key": scope_key,
        "family": "CRYPTO_PRICE_THRESHOLD",
        "row_count": 12,
        "registry_match_count": 1 if reviewed else 0,
        "review_status": "reviewed" if reviewed else "unreviewed",
        "rows_eligible_to_upgrade_to_exact_review_if_reviewed": 12,
        "source_url_candidate": "https://www.cfbenchmarks.com/data/indices/BRTI",
        "source_url_candidate_status": "hint_unreviewed_must_validate_against_venue_rules",
        "registry_entry_skeleton": {
            "registry_version": "canonical_convention_registry_v0",
            "entry_id": "<TODO: stable_entry_id>",
            "family": "CRYPTO_PRICE_THRESHOLD",
            "reviewer": "<TODO: reviewer>",
            "reviewed_at": "<TODO: reviewed_at>",
            "applies_to_scope": {"venue": "kalshi", "event_ticker_prefix": "KXBTC"},
            "typed_key_requirements": {
                "required": ["asset", "measurement_date"],
                "match": {"asset": "BTC", "measurement_date": "May 28, 2026"},
            },
            "canonical_source_kind": "crypto_index_official",
            "canonical_source_url": "<TODO: canonical_source_url>",
            "official_source_description": "Fixture official source description.",
            "evidence_quote_or_excerpt": "<TODO: evidence_quote_or_excerpt>",
            "limitations": "<TODO: limitations>",
            "review_until": "<TODO: review_until>",
            "confidence": "<TODO: confidence>",
        },
    }
