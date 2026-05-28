from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.canonical_convention_registry import (
    FIELD_ALIASES,
    REPORT_SOURCE as REGISTRY_AUDIT_SOURCE,
    audit_registry_review_until_status,
    load_canonical_convention_registry,
)


SCHEMA_VERSION = 1
REPORT_SOURCE = "canonical_registry_expiry_audit_v1"
DEFAULT_EXPIRING_SOON_DAYS = 7


def build_canonical_registry_expiry_audit(
    *,
    registry_path: Path,
    generated_at: datetime | None = None,
    expiring_soon_days: int = DEFAULT_EXPIRING_SOON_DAYS,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    entries, raw_warning = _registry_entries(registry_path)
    loaded = load_canonical_convention_registry(registry_path)
    warnings = list(loaded.warnings)
    if raw_warning is not None:
        warnings.append(raw_warning)

    audit_rows = loaded.entries
    rows = [
        _expiry_row(
            entry,
            registry_audit=audit_rows[index] if index < len(audit_rows) else {},
            index=index,
            now=generated,
            expiring_soon_days=expiring_soon_days,
        )
        for index, entry in enumerate(entries)
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "registry_audit_source": REGISTRY_AUDIT_SOURCE,
        "generated_at": generated.isoformat(),
        "registry_path": str(registry_path),
        "expiring_soon_days": expiring_soon_days,
        "summary": _summary(rows, warnings),
        "entries": rows,
        "warnings": warnings,
        "safety": {
            "saved_files_only": True,
            "live_fetch_attempted": False,
            "execution_or_order_logic_added": False,
            "account_or_auth_logic_added": False,
            "paper_candidate_emitted": False,
            "affects_evaluator_gates": False,
            "expired_registry_entries_treated_as_current_review": False,
        },
    }


def write_canonical_registry_expiry_audit_files(
    *,
    registry_path: Path,
    json_output: Path,
    markdown_output: Path,
    generated_at: datetime | None = None,
    expiring_soon_days: int = DEFAULT_EXPIRING_SOON_DAYS,
) -> dict[str, Any]:
    report = build_canonical_registry_expiry_audit(
        registry_path=registry_path,
        generated_at=generated_at,
        expiring_soon_days=expiring_soon_days,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_canonical_registry_expiry_audit_markdown(report), encoding="utf-8")
    return report


def render_canonical_registry_expiry_audit_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Canonical Registry Expiry Audit",
        "",
        "Saved-file-only review_until audit. Expired entries are not current reviewed evidence.",
        "",
        "## Summary",
        "",
        f"- registry_entries_total: `{summary.get('registry_entries_total', 0)}`",
        f"- registry_entries_valid_current_review: `{summary.get('registry_entries_valid_current_review', 0)}`",
        f"- registry_entries_expiring_soon: `{summary.get('registry_entries_expiring_soon', 0)}`",
        f"- registry_entries_expired: `{summary.get('registry_entries_expired', 0)}`",
        f"- registry_entries_missing_review_until: `{summary.get('registry_entries_missing_review_until', 0)}`",
        f"- invalid_or_blocked_entries: `{summary.get('invalid_or_blocked_entries', 0)}`",
        "",
        "## Entries",
        "",
        "| Entry | Family | Valid current | Expiring soon | Expired | Review until | Blockers |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for row in report.get("entries") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("entry_id")),
                    _md(row.get("family")),
                    _md(str(bool(row.get("valid_current_review"))).lower()),
                    _md(str(bool(row.get("review_expiring_soon"))).lower()),
                    _md(str(bool(row.get("review_expired"))).lower()),
                    _md(row.get("review_until")),
                    _md(",".join(row.get("blockers") or []) or "none"),
                ]
            )
            + " |"
        )
    if not report.get("entries"):
        lines.append("| (none) | (none) | false | false | false | (none) | no_registry_entries |")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- saved_files_only: `true`",
            "- live_fetch_attempted: `false`",
            "- paper_candidate_emitted: `false`",
            "- affects_evaluator_gates: `false`",
            "- expired_registry_entries_treated_as_current_review: `false`",
        ]
    )
    return "\n".join(lines) + "\n"


def _expiry_row(
    entry: dict[str, Any],
    *,
    registry_audit: dict[str, Any],
    index: int,
    now: datetime,
    expiring_soon_days: int,
) -> dict[str, Any]:
    expiry = audit_registry_review_until_status(entry, now=now, expiring_soon_days=expiring_soon_days)
    registry_valid = bool(registry_audit.get("valid"))
    blockers = sorted(set((registry_audit.get("blockers") or []) + (expiry.get("blockers") or [])))
    return {
        "index": index,
        "entry_id": entry.get("entry_id"),
        "family": entry.get("family"),
        "reviewer": entry.get("reviewer"),
        "reviewed_at": entry.get("reviewed_at"),
        "review_until": entry.get("review_until"),
        "review_until_parsed": expiry.get("review_until_parsed"),
        "registry_entry_valid": registry_valid,
        "valid_current_review": bool(registry_valid and expiry.get("valid_current_review")),
        "review_expiring_soon": bool(registry_valid and expiry.get("review_expiring_soon")),
        "review_expired": bool(expiry.get("review_expired")),
        "seconds_until_expiry": expiry.get("seconds_until_expiry"),
        "blockers": blockers,
        "applies_to_scope": entry.get("applies_to_scope"),
        "typed_key_requirements": entry.get("typed_key_requirements"),
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
    }


def _summary(rows: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    blockers = Counter(blocker for row in rows for blocker in row.get("blockers") or [])
    for warning in warnings:
        blockers[str(warning.get("blocker") or warning.get("reason_code") or "warning")] += 1
    return {
        "registry_entries_total": len(rows),
        "registry_entries_valid_current_review": sum(1 for row in rows if row.get("valid_current_review")),
        "registry_entries_expiring_soon": sum(1 for row in rows if row.get("review_expiring_soon")),
        "registry_entries_expired": sum(1 for row in rows if row.get("review_expired")),
        "registry_entries_missing_review_until": sum(
            1 for row in rows if "missing_review_until" in (row.get("blockers") or [])
        ),
        "invalid_or_blocked_entries": sum(1 for row in rows if row.get("blockers")),
        "warning_count": len(warnings),
        "top_blockers": [{"blocker": key, "count": value} for key, value in blockers.most_common(10)],
        "paper_candidate_count": 0,
    }


def _registry_entries(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    if not path.exists():
        return [], {"source_file": str(path), "reason_code": "registry_file_missing", "blocker": "saved_registry_missing"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return [], {"source_file": str(path), "reason_code": "registry_invalid_json", "blocker": "saved_registry_invalid"}
    except OSError as exc:
        return [], {
            "source_file": str(path),
            "reason_code": "registry_read_error",
            "blocker": f"saved_registry_read_error:{type(exc).__name__}",
        }
    raw_entries = payload.get("entries") if isinstance(payload, dict) else payload
    if not isinstance(raw_entries, list):
        return [], {"source_file": str(path), "reason_code": "registry_no_entries", "blocker": "saved_registry_no_entries"}
    top_version = payload.get("registry_version") if isinstance(payload, dict) else None
    entries = [_canonicalize_for_expiry(entry, top_version=top_version) for entry in raw_entries]
    return entries, None


def _canonicalize_for_expiry(raw_entry: Any, *, top_version: Any) -> dict[str, Any]:
    if not isinstance(raw_entry, dict):
        return {"_raw_type": type(raw_entry).__name__}
    entry = dict(raw_entry)
    for old, new in FIELD_ALIASES.items():
        if new not in entry and old in entry:
            entry[new] = entry[old]
    if "registry_version" not in entry and top_version is not None:
        entry["registry_version"] = top_version
    return entry


def _md(value: Any) -> str:
    text = str(value if value is not None else "")
    return text.replace("|", "\\|").replace("\n", " ")


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
