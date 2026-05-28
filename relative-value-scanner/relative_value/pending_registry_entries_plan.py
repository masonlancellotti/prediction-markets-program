from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.canonical_convention_registry import (
    REGISTRY_VERSION,
    load_canonical_convention_registry,
)


SCHEMA_VERSION = 1
REPORT_SOURCE = "pending_registry_entries_plan_v1"
PROMOTION_AUDIT_SOURCE = "pending_registry_entries_promotion_audit_v1"
_TODO_PATTERN = re.compile(r"<\s*TODO\b", re.IGNORECASE)


def build_pending_registry_entries_plan(
    *,
    coverage_path: Path,
    output_dir: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    payload, warning = _load_json(coverage_path)
    warnings = [warning] if warning is not None else []
    reviewed_scope_keys = _reviewed_scope_keys(payload)
    top_scopes = _top_unreviewed_scopes(payload)
    planned: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for item in top_scopes:
        scope_key = _string_or_none(item.get("scope_key"))
        if scope_key is None:
            skipped.append({"scope_key": None, "reason": "missing_scope_key"})
            continue
        if scope_key in reviewed_scope_keys:
            skipped.append({"scope_key": scope_key, "reason": "scope_already_reviewed"})
            continue
        filename = _safe_scope_filename(scope_key)
        path = _safe_output_path(output_dir, filename)
        pending_entry = _pending_entry_payload(item, output_path=path)
        planned.append(
            {
                "scope_key": scope_key,
                "row_count": _int(item.get("row_count")),
                "leverage": _int(item.get("rows_eligible_to_upgrade_to_exact_review_if_reviewed")),
                "output_file": str(path),
                "source_url_candidate_status": item.get("source_url_candidate_status"),
                "pending_entry": pending_entry,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "coverage": str(coverage_path),
        "output_dir": str(output_dir),
        "summary": {
            "pending_files_planned": len(planned),
            "pending_files_written": 0,
            "skipped_reviewed_scopes": sum(1 for item in skipped if item.get("reason") == "scope_already_reviewed"),
            "skipped_scope_count": len(skipped),
            "top_scopes": [item["scope_key"] for item in planned],
            "output_dir": str(output_dir),
        },
        "planned_files": planned,
        "skipped_scopes": skipped,
        "warnings": warnings,
        "safety": {
            "saved_files_only": True,
            "live_fetch_attempted": False,
            "registry_file_modified": False,
            "reviewed_registry_entry_created": False,
            "registry_proposal_is_trust": False,
            "reviewer_must_validate": True,
            "affects_evaluator_gates": False,
        },
    }


def write_pending_registry_entries_plan(
    *,
    coverage_path: Path,
    output_dir: Path,
    json_output: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    plan = build_pending_registry_entries_plan(
        coverage_path=coverage_path,
        output_dir=output_dir,
        generated_at=generated_at,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for item in plan.get("planned_files") or []:
        path = Path(str(item["output_file"]))
        _assert_within(path, output_dir)
        path.write_text(json.dumps(item["pending_entry"], indent=2, sort_keys=True), encoding="utf-8")
        written.append(str(path))
    plan["summary"]["pending_files_written"] = len(written)
    plan["written_files"] = written

    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(_plan_without_embedded_payloads(plan), indent=2, sort_keys=True), encoding="utf-8")
    return plan


def _pending_entry_payload(item: dict[str, Any], *, output_path: Path) -> dict[str, Any]:
    skeleton = item.get("registry_entry_skeleton") if isinstance(item.get("registry_entry_skeleton"), dict) else {}
    leverage = _int(item.get("rows_eligible_to_upgrade_to_exact_review_if_reviewed"))
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "pending_registry_entry_skeleton_v1",
        "scope_key": item.get("scope_key"),
        "family": item.get("family"),
        "row_count": _int(item.get("row_count")),
        "leverage": leverage,
        "source_url_candidate": item.get("source_url_candidate"),
        "source_url_candidate_status": item.get("source_url_candidate_status"),
        "registry_entry_skeleton": skeleton,
        "reviewer_must_validate": True,
        "registry_proposal_is_trust": False,
        "reviewed": False,
        "limitations": skeleton.get("limitations") or "<TODO: limitations>",
        "output_file": str(output_path),
        "safety": {
            "saved_files_only": True,
            "registry_file_modified": False,
            "reviewed_registry_entry_created": False,
            "registry_proposal_is_trust": False,
            "reviewer_must_validate": True,
            "affects_evaluator_gates": False,
        },
    }


def _plan_without_embedded_payloads(plan: dict[str, Any]) -> dict[str, Any]:
    copied = dict(plan)
    copied["planned_files"] = [
        {key: value for key, value in item.items() if key != "pending_entry"}
        for item in plan.get("planned_files") or []
    ]
    return copied


def _top_unreviewed_scopes(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("source") != "canonical_registry_coverage_v1":
        return []
    manual = payload.get("next_manual_review") if isinstance(payload.get("next_manual_review"), dict) else {}
    rows = manual.get("top_unreviewed_scopes")
    return [item for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []


def _reviewed_scope_keys(payload: Any) -> set[str]:
    if not isinstance(payload, dict):
        return set()
    reviewed = set()
    scopes = payload.get("scopes")
    if not isinstance(scopes, list):
        return reviewed
    for scope in scopes:
        if not isinstance(scope, dict):
            continue
        if _int(scope.get("registry_match_count")) > 0 or scope.get("review_status") == "reviewed":
            scope_key = _string_or_none(scope.get("scope_key"))
            if scope_key:
                reviewed.add(scope_key)
    return reviewed


def _safe_scope_filename(scope_key: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", scope_key).strip("._")
    safe = re.sub(r"_+", "_", safe)
    if not safe:
        safe = "scope"
    return f"{safe[:160]}.json"


def _safe_output_path(output_dir: Path, filename: str) -> Path:
    path = output_dir / filename
    _assert_within(path, output_dir)
    return path


def _assert_within(path: Path, output_dir: Path) -> None:
    try:
        path.resolve().relative_to(output_dir.resolve())
    except ValueError as exc:
        raise ValueError(f"refusing to write outside output_dir: {path}") from exc


def audit_pending_registry_entries_for_promotion(
    *,
    pending_dir: Path,
    registry_path: Path | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Diagnostic-only audit. Reads docs/pending_registry_entries/*.json files and
    reports per-file whether the skeleton has been filled in and is structurally
    compatible with a canonical_convention_registry_v0 entry. NEVER mutates the
    canonical registry, NEVER auto-promotes; it only tells the operator which
    files are ready for manual merge.
    """
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    files: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not pending_dir.exists():
        warnings.append(
            {
                "source_file": str(pending_dir),
                "reason_code": "pending_dir_missing",
                "blocker": "saved_pending_dir_missing",
            }
        )
        pending_paths: list[Path] = []
    else:
        pending_paths = sorted(p for p in pending_dir.glob("*.json") if p.is_file())

    existing_entry_ids: set[str] = set()
    if registry_path is not None and registry_path.exists():
        loaded = load_canonical_convention_registry(registry_path)
        existing_entry_ids = {
            str(entry.get("entry_id")) for entry in loaded.valid_entries if entry.get("entry_id")
        }

    for path in pending_paths:
        payload, warning = _load_json(path)
        if warning is not None:
            warnings.append(warning)
            continue
        if not isinstance(payload, dict):
            files.append({"path": str(path), "ready_to_promote": False, "blockers": ["pending_file_not_object"]})
            continue
        files.append(_audit_pending_file(path, payload, existing_entry_ids=existing_entry_ids))

    ready = [item for item in files if item.get("ready_to_promote")]
    return {
        "schema_version": SCHEMA_VERSION,
        "source": PROMOTION_AUDIT_SOURCE,
        "generated_at": generated.isoformat(),
        "pending_dir": str(pending_dir),
        "registry_path": str(registry_path) if registry_path is not None else None,
        "summary": {
            "pending_file_count": len(files),
            "ready_to_promote_count": len(ready),
            "blocked_count": sum(1 for item in files if not item.get("ready_to_promote")),
            "warning_count": len(warnings),
        },
        "files": files,
        "warnings": warnings,
        "safety": {
            "saved_files_only": True,
            "registry_file_modified": False,
            "reviewed_registry_entry_created": False,
            "registry_proposal_is_trust": False,
            "reviewer_must_validate": True,
            "affects_evaluator_gates": False,
        },
    }


def _audit_pending_file(
    path: Path,
    payload: dict[str, Any],
    *,
    existing_entry_ids: set[str],
) -> dict[str, Any]:
    blockers: list[str] = []
    if payload.get("source") != "pending_registry_entry_skeleton_v1":
        blockers.append("not_pending_registry_skeleton")
    if payload.get("registry_proposal_is_trust") is not False:
        blockers.append("registry_proposal_is_trust_must_be_false")
    skeleton = payload.get("registry_entry_skeleton") if isinstance(payload.get("registry_entry_skeleton"), dict) else None
    if skeleton is None:
        blockers.append("missing_registry_entry_skeleton")
        return {
            "path": str(path),
            "scope_key": payload.get("scope_key"),
            "ready_to_promote": False,
            "reviewed_flag_value": payload.get("reviewed"),
            "blockers": _unique(blockers),
        }
    if _has_todo(skeleton):
        blockers.append("skeleton_contains_todo_placeholders")
    required = ("entry_id", "family", "reviewer", "reviewed_at", "applies_to_scope", "typed_key_requirements",
                "canonical_source_kind", "evidence_quote_or_excerpt", "limitations", "confidence")
    for field in required:
        if _is_blank(skeleton.get(field)):
            blockers.append(f"missing_{field}")
    if skeleton.get("registry_version") != REGISTRY_VERSION:
        blockers.append("unsupported_registry_version")
    if _is_blank(skeleton.get("canonical_source_url")) and _is_blank(skeleton.get("official_source_description")):
        blockers.append("missing_canonical_source")
    if _is_blank(skeleton.get("expires_at")) and _is_blank(skeleton.get("review_until")):
        blockers.append("missing_expires_at_or_review_until")
    entry_id = skeleton.get("entry_id")
    if entry_id and str(entry_id) in existing_entry_ids:
        blockers.append("entry_id_already_in_canonical_registry")
    if payload.get("reviewed") is not True:
        blockers.append("reviewed_flag_still_false")
    return {
        "path": str(path),
        "scope_key": payload.get("scope_key"),
        "entry_id": entry_id,
        "reviewed_flag_value": payload.get("reviewed"),
        "ready_to_promote": not blockers,
        "blockers": _unique(blockers),
    }


def _has_todo(value: Any) -> bool:
    if isinstance(value, str):
        return bool(_TODO_PATTERN.search(value))
    if isinstance(value, dict):
        return any(_has_todo(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_todo(item) for item in value)
    return False


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip() or _TODO_PATTERN.search(value) is not None
    if isinstance(value, (list, dict, tuple, set)):
        return not value
    return False


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _load_json(path: Path) -> tuple[Any, dict[str, Any] | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, {"source_file": str(path), "reason_code": "json_file_missing", "blocker": "saved_json_file_missing"}
    except json.JSONDecodeError:
        return None, {"source_file": str(path), "reason_code": "invalid_json", "blocker": "saved_json_invalid"}
    except OSError as exc:
        return None, {"source_file": str(path), "reason_code": "json_read_error", "blocker": f"saved_json_read_error:{type(exc).__name__}"}


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
