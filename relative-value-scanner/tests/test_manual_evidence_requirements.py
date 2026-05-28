from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import scan
from relative_value.manual_evidence_requirements import (
    build_manual_evidence_requirements_report,
    write_manual_evidence_requirements_files,
)


_NOW = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)


def _empty_report_dir(tmp_path: Path) -> Path:
    # The builder tolerates missing inputs; just point at an empty dir.
    return tmp_path


def test_report_generated_with_required_fields(tmp_path: Path) -> None:
    report = build_manual_evidence_requirements_report(
        input_dir=_empty_report_dir(tmp_path), generated_at=_NOW
    )
    items = report["items"]
    assert items
    for item in items:
        assert item["evidence_id"]
        assert item["vertical"] in {"crypto", "economics", "sports", "cross_cutting"}
        assert item["platform"]
        assert item["platform_status"] in {"active", "queued", "reference", "official_source"}
        assert item["market_family"]
        assert item["required_field"]
        assert item["why_it_matters"]
        # Every item must declare what blocker it clears.
        assert item.get("blocker_it_clears"), f"{item['evidence_id']} missing blocker_it_clears"
        # Every item must declare its repeat cadence.
        assert item.get("repeat_cadence") in {
            "one_time_per_family",
            "per_market",
            "per_contract_month",
            "per_event",
            "per_rules_version",
            "per_fee_schedule_change",
            "per_trading_session",
        }
        # Priority + status must be from the allowed vocabularies.
        assert item.get("priority") in {"P0", "P1", "P2", "P3", "P4"}
        assert item.get("status") in {
            "missing",
            "partially_captured",
            "captured_unreviewed",
            "validated_diagnostic",
            "reviewed_usable",
            "blocked",
        }
        # Every item must spell out the manual collection steps.
        assert isinstance(item.get("exact_manual_steps"), list) and item["exact_manual_steps"]
        # Safety flags must remain false.
        assert item["paper_candidate"] is False
        assert item["exact_ready"] is False
        assert item["can_create_paper_candidate"] is False
        assert item["can_create_candidate_pair"] is False
        assert item["clears_evaluator_gate_alone"] is False


def test_no_item_clears_evaluator_gates_or_emits_paper_candidate(tmp_path: Path) -> None:
    json_output = tmp_path / "out.json"
    md_output = tmp_path / "out.md"
    report = write_manual_evidence_requirements_files(
        input_dir=tmp_path, json_output=json_output, markdown_output=md_output, generated_at=_NOW
    )
    forbidden = "PAPER" + "_CANDIDATE"
    for path in (json_output, md_output):
        text = path.read_text(encoding="utf-8")
        assert forbidden not in text
    summary = report["summary"]
    assert summary["exact_ready_rows"] == 0
    assert summary["paper_candidate_rows"] == 0
    assert summary["no_manual_evidence_clears_evaluator_gates_on_its_own"] is True
    assert summary["no_manual_evidence_creates_paper_candidate"] is True


def test_queued_platforms_stay_queued_and_not_reviewed_usable(tmp_path: Path) -> None:
    report = build_manual_evidence_requirements_report(input_dir=tmp_path, generated_at=_NOW)
    queued_items = [it for it in report["items"] if it["platform_status"] == "queued"]
    # The catalogue at minimum includes the IBKR queued guard item.
    assert queued_items
    for item in queued_items:
        assert item["status"] != "reviewed_usable", (
            f"queued item {item['evidence_id']} cannot be reviewed_usable yet"
        )


def test_reference_only_platforms_cannot_affect_exact_review(tmp_path: Path) -> None:
    report = build_manual_evidence_requirements_report(input_dir=tmp_path, generated_at=_NOW)
    reference_items = [it for it in report["items"] if it["platform_status"] == "reference"]
    assert reference_items
    for item in reference_items:
        assert item["can_ever_affect_exact_review"] is False


def test_summary_has_top_10_actions_and_no_paper_candidate(tmp_path: Path) -> None:
    report = build_manual_evidence_requirements_report(input_dir=tmp_path, generated_at=_NOW)
    s = report["summary"]
    assert len(s["top_10_this_week"]) <= 10 and s["top_10_this_week"]
    assert len(s["top_10_unlock_most_rows"]) <= 10 and s["top_10_unlock_most_rows"]
    assert len(s["top_10_reduce_fake_edge_risk"]) <= 10 and s["top_10_reduce_fake_edge_risk"]
    assert s["paper_candidate_rows"] == 0
    assert s["exact_ready_rows"] == 0
    # Every short-form item also carries the blocker + cadence.
    for short in s["top_10_this_week"]:
        assert short["blocker_it_clears"]
        assert short["repeat_cadence"]


def test_no_private_or_auth_strings_in_module_or_outputs(tmp_path: Path) -> None:
    json_output = tmp_path / "out.json"
    md_output = tmp_path / "out.md"
    write_manual_evidence_requirements_files(
        input_dir=tmp_path,
        json_output=json_output,
        markdown_output=md_output,
        generated_at=_NOW,
    )
    source_text = Path("relative_value/manual_evidence_requirements.py").read_text(encoding="utf-8")
    output_text = json_output.read_text(encoding="utf-8") + md_output.read_text(encoding="utf-8")
    forbidden_patterns = (
        '"Authorization"',
        "'Authorization'",
        "Bearer ",
        "X-API-Key",
        "PRIVATE_KEY",
        "private_key=",
        "signTypedData",
        "mnemonic_phrase",
        "seed_phrase=",
        'method="POST"',
        "method='POST'",
        'method="DELETE"',
        "method='DELETE'",
        "urlopen(",
        "requests.post(",
        "requests.put(",
        "requests.delete(",
    )
    text = source_text + output_text
    for forbidden in forbidden_patterns:
        assert forbidden not in text, f"forbidden token found: {forbidden}"


def test_cli_writes_outputs_with_safe_summary_line(tmp_path: Path, capsys) -> None:
    result = scan.main(
        [
            "manual-evidence-requirements",
            "--input-dir",
            str(tmp_path),
            "--json-output",
            str(tmp_path / "out.json"),
            "--markdown-output",
            str(tmp_path / "out.md"),
        ]
    )
    assert result == 0
    stdout = capsys.readouterr().out
    assert "manual_evidence_requirements=OK" in stdout
    assert "diagnostic_only=true" in stdout
    assert "saved_files_only=true" in stdout
    assert "exact_ready_rows=0" in stdout
    assert "paper_candidate_rows=0" in stdout
    assert "queued_platforms_remain_queued=true" in stdout
    assert "reference_only_platforms_never_become_pair_side=true" in stdout
    payload = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert payload["schema_kind"] == "manual_evidence_requirements_v1"
    assert payload["summary"]["total_items"] >= 30
    # Three verticals + cross_cutting must all be present in saved catalogue.
    assert set(payload["summary"]["verticals"]) >= {"crypto", "economics", "sports", "cross_cutting"}


def test_ops_status_surfaces_manual_evidence_block(tmp_path: Path) -> None:
    # Build the real report into the tmp reports dir, then run ops-status pointing at it.
    write_manual_evidence_requirements_files(
        input_dir=tmp_path,
        json_output=tmp_path / "manual_evidence_requirements.json",
        markdown_output=tmp_path / "manual_evidence_requirements.md",
        generated_at=_NOW,
    )
    from relative_value.relative_value_ops_status import (
        build_relative_value_ops_status_report,
        render_relative_value_ops_status_markdown,
    )

    report = build_relative_value_ops_status_report(input_dir=tmp_path, generated_at=_NOW)
    block = report["summary"]["manual_evidence_requirements"]
    assert block["present"] is True
    assert block["total_items"] >= 30
    assert block["exact_ready_rows"] == 0
    assert block["paper_candidate_rows"] == 0
    assert block["queued_platforms_remain_queued"] is True
    assert block["reference_only_platforms_never_become_pair_side"] is True
    md = render_relative_value_ops_status_markdown(report)
    assert "manual_evidence_requirements" in md
    forbidden = "PAPER" + "_CANDIDATE"
    assert forbidden not in md


def test_validation_blocks_paper_candidate_promotion(tmp_path: Path) -> None:
    """Defence-in-depth: mutate the catalogue inputs and confirm the
    normalizer rejects any item that tries to set paper_candidate or
    exact_ready true, or that tries to mark a queued item reviewed_usable."""
    from relative_value.manual_evidence_requirements import _normalize_item

    bad = {
        "evidence_id": "bad-paper",
        "vertical": "crypto",
        "platform": "kalshi",
        "platform_status": "active",
        "market_family": "test",
        "required_field": "x",
        "missing_or_uncertain_value": "x",
        "why_it_matters": "x",
        "blocker_it_clears": "x",
        "exact_gate_affected": "none",
        "collection_method": "public_page",
        "exact_manual_steps": ["x"],
        "repeat_cadence": "per_market",
        "priority": "P0",
        "status": "missing",
        "fake_edge_risk": "x",
        "paper_candidate": True,
    }
    with pytest.raises(ValueError, match="paper_candidate"):
        _normalize_item(dict(bad))
    bad["paper_candidate"] = False
    bad["can_create_paper_candidate"] = True
    with pytest.raises(ValueError, match="can_create_paper_candidate"):
        _normalize_item(dict(bad))
    bad["can_create_paper_candidate"] = False
    bad["exact_ready"] = True
    with pytest.raises(ValueError, match="exact_ready"):
        _normalize_item(dict(bad))
    bad["exact_ready"] = False
    bad["platform_status"] = "queued"
    bad["status"] = "reviewed_usable"
    with pytest.raises(ValueError, match="queued"):
        _normalize_item(dict(bad))
