from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import scan
from relative_value.canonical_convention_registry import REGISTRY_VERSION
from relative_value.canonical_registry_expiry_audit import build_canonical_registry_expiry_audit


NOW = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)


def test_future_review_until_is_valid_current_review(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path / "registry.json", [_entry(review_until="2026-06-30")])

    report = build_canonical_registry_expiry_audit(registry_path=registry, generated_at=NOW)
    row = report["entries"][0]

    assert row["valid_current_review"] is True
    assert row["review_expiring_soon"] is False
    assert row["review_expired"] is False
    assert row["blockers"] == []
    assert report["summary"]["registry_entries_valid_current_review"] == 1


def test_review_until_within_seven_days_is_expiring_soon(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path / "registry.json", [_entry(review_until="2026-05-30")])

    report = build_canonical_registry_expiry_audit(registry_path=registry, generated_at=NOW)
    row = report["entries"][0]

    assert row["valid_current_review"] is True
    assert row["review_expiring_soon"] is True
    assert row["review_expired"] is False
    assert report["summary"]["registry_entries_expiring_soon"] == 1


def test_past_review_until_is_expired_and_not_current(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path / "registry.json", [_entry(review_until="2026-05-24")])

    report = build_canonical_registry_expiry_audit(registry_path=registry, generated_at=NOW)
    row = report["entries"][0]

    assert row["valid_current_review"] is False
    assert row["review_expiring_soon"] is False
    assert row["review_expired"] is True
    assert "review_expired" in row["blockers"]
    assert report["summary"]["registry_entries_expired"] == 1


def test_missing_review_until_blocks_current_review(tmp_path: Path) -> None:
    entry = _entry()
    entry.pop("review_until")
    registry = _write_registry(tmp_path / "registry.json", [entry])

    report = build_canonical_registry_expiry_audit(registry_path=registry, generated_at=NOW)
    row = report["entries"][0]

    assert row["valid_current_review"] is False
    assert row["review_expired"] is False
    assert "missing_review_until" in row["blockers"]
    assert "missing_expires_at_or_review_until" in row["blockers"]
    assert report["summary"]["registry_entries_missing_review_until"] == 1


def test_expired_entry_does_not_silently_look_safe(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path / "registry.json", [_entry(review_until="2026-05-24")])

    report = build_canonical_registry_expiry_audit(registry_path=registry, generated_at=NOW)

    assert report["entries"][0]["valid_current_review"] is False
    assert report["safety"]["expired_registry_entries_treated_as_current_review"] is False
    assert "PAPER_CANDIDATE" not in json.dumps(report)


def test_audit_canonical_registry_expiry_cli_writes_outputs(tmp_path: Path, capsys) -> None:
    registry = _write_registry(tmp_path / "registry.json", [_entry(review_until="2026-06-30")])
    json_output = tmp_path / "expiry.json"
    markdown_output = tmp_path / "expiry.md"

    result = scan.main(
        [
            "audit-canonical-registry-expiry",
            "--registry",
            str(registry),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )

    assert result == 0
    stdout = capsys.readouterr().out
    assert "canonical_registry_expiry_audit_status=OK" in stdout
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["source"] == "canonical_registry_expiry_audit_v1"
    assert payload["summary"]["registry_entries_valid_current_review"] == 1
    assert markdown_output.exists()


def _write_registry(path: Path, entries: list[dict]) -> Path:
    path.write_text(json.dumps({"registry_version": REGISTRY_VERSION, "entries": entries}), encoding="utf-8")
    return path


def _entry(**overrides) -> dict:
    entry = {
        "registry_version": REGISTRY_VERSION,
        "entry_id": "fed-fomc-source-convention",
        "family": "FED_FOMC",
        "reviewer": "manual_reviewer",
        "reviewed_at": "2026-05-25T00:00:00+00:00",
        "applies_to_scope": {"venue": "kalshi", "event_ticker_prefix": "KXFED"},
        "typed_key_requirements": {
            "required": ["meeting_date", "rate_bound", "threshold_percent", "source_convention"],
            "match": {"source_convention": "federal_reserve_official_website"},
        },
        "canonical_source_kind": "official_source_url",
        "canonical_source_url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
        "evidence_quote_or_excerpt": {
            "kind": "official_source_excerpt",
            "text": "The Federal Reserve publishes FOMC calendars and policy decisions on its official website.",
        },
        "limitations": ["Manual registry convention only."],
        "review_until": "2026-12-31",
        "confidence": "high",
    }
    entry.update(overrides)
    return entry
