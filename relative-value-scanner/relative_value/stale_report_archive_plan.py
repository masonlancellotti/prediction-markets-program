from __future__ import annotations

import json
import shutil
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
REPORT_SOURCE = "stale_report_archive_plan_v1"
APPLIED_REPORT_SOURCE = "stale_report_archive_applied_v1"

STALE_AFTER = timedelta(hours=24)

CLASS_STALE_EVALUATOR_OUTPUT = "stale_evaluator_output"
CLASS_STALE_PIPELINE_SUMMARY = "stale_pipeline_summary"
CLASS_LEGACY_CANDIDATE_ARTIFACT = "legacy_candidate_artifact"
CLASS_CURRENT_REPORT_KEEP = "current_report_keep"
CLASS_GENERATED_CURRENT_REPORT = "generated_current_report"
CLASS_UNKNOWN_KEEP = "unknown_keep"

ARCHIVE_CLASSES = {
    CLASS_STALE_EVALUATOR_OUTPUT,
    CLASS_STALE_PIPELINE_SUMMARY,
    CLASS_LEGACY_CANDIDATE_ARTIFACT,
}

EVALUATOR_ACTION = "PAPER" + "_CANDIDATE"

KNOWN_STALE_RELATIVE_PATHS = {
    "mlb_kxmlb_48h_unitok_paper_candidates.json",
}

KNOWN_STALE_DIR_PREFIXES = (
    "paper_hit_20260523_163959/",
)

CURRENT_REPORT_FILENAMES = {
    "venue_metadata_coverage.json",
    "venue_metadata_coverage.csv",
    "venue_metadata_coverage.md",
    "normalized_markets_v0.json",
    "normalized_markets_v0_coverage.json",
    "settlement_evidence_burden.json",
    "cross_platform_opportunity_triage.json",
    "cross_platform_opportunity_triage.csv",
    "cross_platform_opportunity_triage.md",
    "standardized_family_candidates.json",
    "standardized_family_candidates.csv",
    "standardized_family_candidates.md",
    "existing_paper_candidate_audit.json",
    "existing_paper_candidate_audit.md",
    "relative_value_ops_status.json",
    "relative_value_ops_status.md",
    "mlb_world_series_revival_status.json",
    "mlb_world_series_revival_status.md",
    "platform_api_expansion.json",
    "platform_api_expansion.md",
    "stale_report_archive_plan.json",
    "stale_report_archive_plan.md",
}

CURRENT_REPORT_SOURCES = {
    "venue_metadata_coverage_audit_v1",
    "normalized_market_contract_v0",
    "normalized_market_contract_v0_coverage",
    "settlement_evidence_burden_v1",
    "cross_platform_opportunity_triage_v1",
    "standardized_family_candidates_v1",
}

GENERATED_CURRENT_REPORT_SOURCES = {
    "existing_paper_candidate_audit_v1",
    "relative_value_ops_status_v1",
    "mlb_world_series_revival_status_v1",
    "platform_api_expansion_audit_v1",
    REPORT_SOURCE,
}


def write_stale_report_archive_plan_files(
    *,
    input_dir: Path,
    json_output: Path,
    markdown_output: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_stale_report_archive_plan(input_dir=input_dir, generated_at=generated_at)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_stale_report_archive_plan_markdown(report), encoding="utf-8")
    return report


def apply_stale_report_archive_plan(
    *,
    plan_path: Path,
    applied_output: Path | None = None,
    apply: bool = False,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    plan, warning = _load_json(plan_path)
    if warning is not None or not isinstance(plan, dict):
        report = _applied_report(
            generated_at=generated,
            plan_path=plan_path,
            mode="apply" if apply else "dry_run",
            status="REFUSED",
            input_dir=None,
            archive_dir=None,
            planned_moves=[],
            applied_moves=[],
            noop_moves=[],
            refused_moves=[
                {
                    "reason_code": (warning or {}).get("reason_code", "invalid_archive_plan"),
                    "blocker": (warning or {}).get("blocker", "saved_archive_plan_invalid"),
                    "source_file": str(plan_path),
                }
            ],
        )
        if apply and applied_output is not None:
            _write_applied_report(report, applied_output)
        return report
    if plan.get("source") != REPORT_SOURCE:
        report = _applied_report(
            generated_at=generated,
            plan_path=plan_path,
            mode="apply" if apply else "dry_run",
            status="REFUSED",
            input_dir=None,
            archive_dir=None,
            planned_moves=[],
            applied_moves=[],
            noop_moves=[],
            refused_moves=[
                {
                    "reason_code": "wrong_archive_plan_source",
                    "blocker": "saved_archive_plan_wrong_source",
                    "source": plan.get("source"),
                }
            ],
        )
        if apply and applied_output is not None:
            _write_applied_report(report, applied_output)
        return report

    input_dir = Path(str(plan.get("input_dir") or plan_path.parent))
    archive_dir = Path(str(plan.get("archive_dir") or ""))
    rows = [row for row in plan.get("files") or [] if isinstance(row, dict) and row.get("archive_recommended")]
    planned_moves, refused_moves = _validated_archive_moves(rows, input_dir=input_dir, archive_dir=archive_dir)
    status = "REFUSED" if refused_moves else "DRY_RUN" if not apply else "APPLIED"
    applied_moves: list[dict[str, Any]] = []
    noop_moves: list[dict[str, Any]] = []

    if apply and not refused_moves:
        for move in planned_moves:
            source = Path(move["source_file"])
            destination = Path(move["destination_file"])
            if source.exists():
                if destination.exists():
                    refused_moves.append(
                        {
                            **move,
                            "reason_code": "destination_exists",
                            "blocker": "archive_destination_already_exists",
                        }
                    )
                    status = "REFUSED"
                    break
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
                applied_moves.append(
                    {
                        **move,
                        "applied_at": generated.isoformat(),
                        "result": "moved",
                    }
                )
            elif destination.exists():
                noop_moves.append(
                    {
                        **move,
                        "applied_at": generated.isoformat(),
                        "result": "already_archived",
                    }
                )
            else:
                refused_moves.append(
                    {
                        **move,
                        "reason_code": "source_missing",
                        "blocker": "archive_source_missing",
                    }
                )
                status = "REFUSED"
                break
        if refused_moves and applied_moves:
            status = "PARTIAL_REFUSED"
        elif refused_moves:
            status = "REFUSED"
        elif not applied_moves:
            status = "NOOP"

    report = _applied_report(
        generated_at=generated,
        plan_path=plan_path,
        mode="apply" if apply else "dry_run",
        status=status,
        input_dir=input_dir,
        archive_dir=archive_dir,
        planned_moves=planned_moves,
        applied_moves=applied_moves,
        noop_moves=noop_moves,
        refused_moves=refused_moves,
    )
    if apply and applied_output is not None:
        _write_applied_report(report, applied_output)
    return report


def _write_applied_report(report: dict[str, Any], applied_output: Path) -> None:
    applied_output.parent.mkdir(parents=True, exist_ok=True)
    applied_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def build_stale_report_archive_plan(
    *,
    input_dir: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    input_dir = Path(input_dir)
    archive_dir = input_dir / "archive" / generated.date().isoformat()
    warnings: list[dict[str, Any]] = []

    file_infos = _scan_file_infos(input_dir=input_dir, generated_at=generated, warnings=warnings)
    audit_stale_sources = _audit_stale_sources(input_dir / "existing_paper_candidate_audit.json")
    initial_stale_refs = _initial_stale_reference_set(file_infos, audit_stale_sources)

    rows = []
    for info in file_infos:
        row = _plan_row(
            info,
            input_dir=input_dir,
            archive_dir=archive_dir,
            generated_at=generated,
            stale_reference_set=initial_stale_refs,
        )
        rows.append(row)

    archive_reference_set = {
        row["relative_path"]
        for row in rows
        if row["classification"] in ARCHIVE_CLASSES
    } | {
        Path(row["relative_path"]).name
        for row in rows
        if row["classification"] in ARCHIVE_CLASSES
    }

    # Second pass: pipeline summaries can become stale because they point at a
    # stale ledger discovered elsewhere in the same scan.
    rows = [
        _reclassify_pipeline_reference(row, archive_reference_set, input_dir=input_dir, archive_dir=archive_dir)
        for row in rows
    ]

    commands = _suggested_commands(rows, archive_dir=archive_dir, input_dir=input_dir)
    class_counts = Counter(row["classification"] for row in rows)
    summary = {
        "scanned_file_count": len(rows),
        "archive_candidate_count": sum(1 for row in rows if row["archive_recommended"]),
        "suggested_command_count": len(commands),
        "archive_dir": _display_path(archive_dir),
        "classification_counts": dict(sorted(class_counts.items())),
        "stale_evaluator_output_count": class_counts.get(CLASS_STALE_EVALUATOR_OUTPUT, 0),
        "stale_pipeline_summary_count": class_counts.get(CLASS_STALE_PIPELINE_SUMMARY, 0),
        "legacy_candidate_artifact_count": class_counts.get(CLASS_LEGACY_CANDIDATE_ARTIFACT, 0),
        "current_report_keep_count": class_counts.get(CLASS_CURRENT_REPORT_KEEP, 0),
        "generated_current_report_count": class_counts.get(CLASS_GENERATED_CURRENT_REPORT, 0),
        "unknown_keep_count": class_counts.get(CLASS_UNKNOWN_KEEP, 0),
        "known_stale_mlb_files_flagged": sorted(
            row["relative_path"]
            for row in rows
            if row["archive_recommended"] and _known_stale_path(row["relative_path"])
        ),
        "non_mutating": True,
        "files_moved_or_deleted": False,
        "paper_candidate_rows_created": False,
        "candidate_promotion": False,
        "warning_count": len(warnings),
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "input_dir": _display_path(input_dir),
        "archive_dir": _display_path(archive_dir),
        "summary": summary,
        "files": rows,
        "suggested_commands": commands,
        "suggested_move_commands": [
            command["command"]
            for command in commands
            if command.get("kind") == "move_file"
        ],
        "warnings": warnings,
        "safety": {
            "saved_files_only": True,
            "plan_only": True,
            "files_moved_or_deleted": False,
            "move_commands_executed": False,
            "live_fetch_attempted": False,
            "execution_or_order_logic_added": False,
            "account_or_auth_logic_added": False,
            "candidate_rows_created": False,
            "affects_evaluator_gates": False,
        },
    }


def _validated_archive_moves(
    rows: list[dict[str, Any]],
    *,
    input_dir: Path,
    archive_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    planned: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    input_root = input_dir.resolve()
    archive_root = archive_dir.resolve()
    expected_archive_parent = (input_dir / "archive").resolve()
    if not _path_under(archive_root, expected_archive_parent):
        refused.append(
            {
                "reason_code": "archive_dir_outside_reports_archive",
                "blocker": "archive_dir_not_under_reports_archive",
                "archive_dir": str(archive_dir),
            }
        )
        return planned, refused
    for row in rows:
        relative_path = _string_or_none(row.get("relative_path"))
        if not relative_path:
            refused.append({"reason_code": "missing_relative_path", "blocker": "archive_plan_row_missing_relative_path"})
            continue
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            refused.append(
                {
                    "relative_path": relative_path,
                    "reason_code": "unknown_relative_path",
                    "blocker": "archive_plan_unknown_path_refused",
                }
            )
            continue
        expected_source = (input_dir / relative).resolve()
        expected_destination = (archive_dir / relative).resolve()
        source_file = Path(str(row.get("source_file") or input_dir / relative)).resolve()
        destination_file = Path(str(row.get("suggested_archive_path") or archive_dir / relative)).resolve()
        if source_file != expected_source or destination_file != expected_destination:
            refused.append(
                {
                    "relative_path": relative_path,
                    "source_file": str(source_file),
                    "destination_file": str(destination_file),
                    "reason_code": "source_or_destination_not_listed_shape",
                    "blocker": "archive_plan_unknown_path_refused",
                }
            )
            continue
        if not _path_under(source_file, input_root) or _is_under_archive(source_file, input_dir):
            refused.append(
                {
                    "relative_path": relative_path,
                    "source_file": str(source_file),
                    "reason_code": "source_outside_input_dir",
                    "blocker": "archive_source_unknown_path_refused",
                }
            )
            continue
        if not _path_under(destination_file, archive_root):
            refused.append(
                {
                    "relative_path": relative_path,
                    "destination_file": str(destination_file),
                    "reason_code": "destination_outside_archive_dir",
                    "blocker": "archive_destination_unknown_path_refused",
                }
            )
            continue
        planned.append(
            {
                "relative_path": relative_path.replace("\\", "/"),
                "classification": row.get("classification"),
                "source_file": str(source_file),
                "destination_file": str(destination_file),
                "suggested_move_command": row.get("suggested_move_command"),
            }
        )
    return planned, refused


def _applied_report(
    *,
    generated_at: datetime,
    plan_path: Path,
    mode: str,
    status: str,
    input_dir: Path | None,
    archive_dir: Path | None,
    planned_moves: list[dict[str, Any]],
    applied_moves: list[dict[str, Any]],
    noop_moves: list[dict[str, Any]],
    refused_moves: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source": APPLIED_REPORT_SOURCE,
        "generated_at": generated_at.isoformat(),
        "plan_path": str(plan_path),
        "input_dir": str(input_dir) if input_dir is not None else None,
        "archive_dir": str(archive_dir) if archive_dir is not None else None,
        "mode": mode,
        "status": status,
        "summary": {
            "planned_move_count": len(planned_moves),
            "applied_move_count": len(applied_moves),
            "noop_move_count": len(noop_moves),
            "refused_move_count": len(refused_moves),
            "files_deleted": 0,
            "dry_run": mode == "dry_run",
            "idempotent_noop": mode == "apply" and not applied_moves and bool(noop_moves) and not refused_moves,
            "covers_stale_archive_plan": mode == "apply" and not refused_moves,
            "candidate_promotion": False,
        },
        "planned_moves": planned_moves,
        "applied_moves": applied_moves,
        "noop_moves": noop_moves,
        "refused_moves": refused_moves,
        "safety": {
            "saved_files_only": True,
            "live_fetch_attempted": False,
            "delete_attempted": False,
            "files_deleted": False,
            "uses_git_mv": False,
            "uses_shutil_move": mode == "apply",
            "candidate_rows_created": False,
            "affects_evaluator_gates": False,
            "paper_candidate_emitted": False,
        },
    }


def render_stale_report_archive_plan_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Stale Report Archive Plan",
        "",
        "Saved-file-only archive plan. No files were moved or deleted.",
        "",
        "## Summary",
        "",
        f"- scanned_file_count: `{summary.get('scanned_file_count', 0)}`",
        f"- archive_candidate_count: `{summary.get('archive_candidate_count', 0)}`",
        f"- stale_evaluator_output_count: `{summary.get('stale_evaluator_output_count', 0)}`",
        f"- stale_pipeline_summary_count: `{summary.get('stale_pipeline_summary_count', 0)}`",
        f"- legacy_candidate_artifact_count: `{summary.get('legacy_candidate_artifact_count', 0)}`",
        f"- archive_dir: `{summary.get('archive_dir')}`",
        "",
        "## Archive Candidates",
        "",
    ]
    archive_rows = [row for row in report.get("files") or [] if row.get("archive_recommended")]
    if archive_rows:
        lines.extend(["| Classification | Source | Destination | Reasons |", "|---|---|---|---|"])
        for row in archive_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md(row.get("classification")),
                        _md(row.get("relative_path")),
                        _md(row.get("suggested_archive_path")),
                        _md(", ".join(row.get("reasons") or [])),
                    ]
                )
                + " |"
            )
    else:
        lines.append("(none)")
    lines.extend(["", "## Suggested Commands", ""])
    commands = report.get("suggested_commands") or []
    if commands:
        for command in commands:
            lines.append(f"```powershell\n{command.get('command')}\n```")
    else:
        lines.append("(none)")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- plan_only: `true`",
            "- files_moved_or_deleted: `false`",
            "- move_commands_executed: `false`",
            "- live_fetch_attempted: `false`",
            "- candidate_rows_created: `false`",
            "- affects_evaluator_gates: `false`",
        ]
    )
    return "\n".join(lines) + "\n"


def _scan_file_infos(
    *,
    input_dir: Path,
    generated_at: datetime,
    warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not input_dir.exists():
        warnings.append({"source_file": _display_path(input_dir), "reason_code": "input_dir_missing", "blocker": "saved_input_directory_missing"})
        return []
    infos = []
    for path in sorted(input_dir.rglob("*.json")):
        if _is_under_archive(path, input_dir):
            continue
        payload, warning = _load_json(path)
        if warning is not None:
            warnings.append(warning)
            payload = None
        mtime = _mtime(path)
        generated_value = _parse_dt(payload.get("generated_at") if isinstance(payload, dict) else None)
        infos.append(
            {
                "path": path,
                "relative_path": _relative_path(path, input_dir),
                "payload": payload,
                "source": payload.get("source") if isinstance(payload, dict) else None,
                "generated_at": generated_value,
                "file_modified_time": mtime,
                "effective_time": generated_value or mtime,
                "is_stale_by_time": _is_stale(generated_value or mtime, generated_at),
            }
        )
    return infos


def _plan_row(
    info: dict[str, Any],
    *,
    input_dir: Path,
    archive_dir: Path,
    generated_at: datetime,
    stale_reference_set: set[str],
) -> dict[str, Any]:
    relative_path = info["relative_path"]
    payload = info.get("payload")
    source = info.get("source")
    reasons: list[str] = []
    referenced = _referenced_stale_files(payload, stale_reference_set)

    if _generated_current_report(relative_path, source):
        classification = CLASS_GENERATED_CURRENT_REPORT
        reasons.append("generated_current_report_source")
    elif _current_report_keep(relative_path, source):
        classification = CLASS_CURRENT_REPORT_KEEP
        reasons.append("current_report_filename_or_source")
    elif _stale_evaluator_output(relative_path, payload, info):
        classification = CLASS_STALE_EVALUATOR_OUTPUT
        reasons.extend(_stale_evaluator_reasons(relative_path, payload, info))
    elif _stale_pipeline_summary(relative_path, payload, info, referenced):
        classification = CLASS_STALE_PIPELINE_SUMMARY
        reasons.extend(_stale_pipeline_reasons(payload, info, referenced))
    elif _legacy_candidate_artifact(relative_path, payload, info):
        classification = CLASS_LEGACY_CANDIDATE_ARTIFACT
        reasons.extend(_legacy_artifact_reasons(relative_path, payload, info))
    else:
        classification = CLASS_UNKNOWN_KEEP
        reasons.append("not_currently_classified_as_stale_candidate_artifact")

    archive_recommended = classification in ARCHIVE_CLASSES
    suggested_path = _archive_destination(input_dir / relative_path, input_dir=input_dir, archive_dir=archive_dir) if archive_recommended else None
    command = _move_command(input_dir / relative_path, suggested_path) if suggested_path is not None else None
    return {
        "relative_path": relative_path,
        "source_file": _display_path(input_dir / relative_path),
        "classification": classification,
        "archive_recommended": archive_recommended,
        "reasons": sorted(set(reasons)),
        "source": source,
        "generated_at": info["generated_at"].isoformat() if info.get("generated_at") else None,
        "source_file_modified_time": info["file_modified_time"].isoformat() if info.get("file_modified_time") else None,
        "effective_age_seconds": _age_seconds(info.get("effective_time"), generated_at),
        "referenced_stale_files": referenced,
        "suggested_archive_path": _display_path(suggested_path) if suggested_path is not None else None,
        "suggested_move_command": command,
        "command_is_suggestion_only": command is not None,
        "move_executed": False,
    }


def _reclassify_pipeline_reference(
    row: dict[str, Any],
    archive_reference_set: set[str],
    *,
    input_dir: Path,
    archive_dir: Path,
) -> dict[str, Any]:
    if row["archive_recommended"]:
        return row
    path = input_dir / row["relative_path"]
    payload = _load_json(path)[0]
    referenced = _referenced_stale_files(payload, archive_reference_set)
    if not referenced:
        return row
    if not _looks_like_pipeline_summary(row["relative_path"], payload):
        return row
    suggested_path = _archive_destination(path, input_dir=input_dir, archive_dir=archive_dir)
    updated = dict(row)
    updated.update(
        {
            "classification": CLASS_STALE_PIPELINE_SUMMARY,
            "archive_recommended": True,
            "reasons": sorted(set(row.get("reasons") or []) | {"pipeline_summary_references_stale_candidate_artifact"}),
            "referenced_stale_files": referenced,
            "suggested_archive_path": _display_path(suggested_path),
            "suggested_move_command": _move_command(path, suggested_path),
            "command_is_suggestion_only": True,
            "move_executed": False,
        }
    )
    return updated


def _suggested_commands(rows: list[dict[str, Any]], *, archive_dir: Path, input_dir: Path) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    archive_rows = [row for row in rows if row.get("archive_recommended")]
    if not archive_rows:
        return commands
    dirs = sorted({str(Path(row["suggested_archive_path"]).parent) for row in archive_rows if row.get("suggested_archive_path")})
    for destination_dir in dirs:
        command = f'New-Item -ItemType Directory -Force -Path "{_ps_escape(destination_dir)}" | Out-Null'
        commands.append(
            {
                "kind": "create_directory",
                "command": command,
                "destination_dir": destination_dir,
                "executes_in_plan": False,
                "safe_string_only": _safe_command_string(command),
            }
        )
    for row in archive_rows:
        command = row.get("suggested_move_command")
        if not command:
            continue
        commands.append(
            {
                "kind": "move_file",
                "command": command,
                "source": row.get("source_file"),
                "destination": row.get("suggested_archive_path"),
                "classification": row.get("classification"),
                "executes_in_plan": False,
                "safe_string_only": _safe_command_string(command),
                "destination_under_archive": _path_under(Path(row["suggested_archive_path"]), archive_dir),
                "source_under_input_dir": _path_under(input_dir / row["relative_path"], input_dir),
            }
        )
    return commands


def _initial_stale_reference_set(file_infos: list[dict[str, Any]], audit_stale_sources: set[str]) -> set[str]:
    refs = set(audit_stale_sources)
    for info in file_infos:
        relative_path = info["relative_path"]
        payload = info.get("payload")
        if _known_stale_path(relative_path) or _stale_evaluator_output(relative_path, payload, info) or _legacy_candidate_artifact(relative_path, payload, info):
            refs.add(relative_path)
            refs.add(Path(relative_path).name)
    return refs


def _audit_stale_sources(audit_path: Path) -> set[str]:
    payload, _ = _load_json(audit_path)
    refs: set[str] = set()
    if not isinstance(payload, dict):
        return refs
    for row in payload.get("candidates") or []:
        if not isinstance(row, dict):
            continue
        classifications = set(row.get("classifications") or [])
        if not classifications & {"STALE_SOURCE_FILE", "FAILS_CURRENT_NORMALIZED_GATES", "POSSIBLE_FAKE_EDGE"}:
            continue
        source_file = _string_or_none(row.get("source_file"))
        if not source_file:
            continue
        refs.add(_normalize_ref(source_file))
        refs.add(Path(source_file).name)
    return refs


def _current_report_keep(relative_path: str, source: Any) -> bool:
    name = Path(relative_path).name
    return name in CURRENT_REPORT_FILENAMES or str(source or "") in CURRENT_REPORT_SOURCES


def _generated_current_report(relative_path: str, source: Any) -> bool:
    return str(source or "") in GENERATED_CURRENT_REPORT_SOURCES


def _stale_evaluator_output(relative_path: str, payload: Any, info: dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("source") != "paper_candidate_evaluator":
        return False
    return bool(info.get("is_stale_by_time") or _paper_row_count(payload) > 0 or _known_stale_path(relative_path))


def _stale_evaluator_reasons(relative_path: str, payload: Any, info: dict[str, Any]) -> list[str]:
    reasons = ["paper_candidate_evaluator_output"]
    if info.get("is_stale_by_time"):
        reasons.append("generated_at_or_modified_time_older_than_24h")
    if _paper_row_count(payload) > 0:
        reasons.append("contains_existing_positive_evaluator_rows")
    if _known_stale_path(relative_path):
        reasons.append("known_stale_mlb_artifact")
    return reasons


def _stale_pipeline_summary(relative_path: str, payload: Any, info: dict[str, Any], referenced: list[str]) -> bool:
    if not _looks_like_pipeline_summary(relative_path, payload):
        return False
    if referenced:
        return True
    summary = payload.get("summary") if isinstance(payload, dict) else {}
    counts = summary.get("evaluator_counts") if isinstance(summary, dict) else {}
    return bool(info.get("is_stale_by_time") and _positive_count_from_mapping(counts) > 0)


def _stale_pipeline_reasons(payload: Any, info: dict[str, Any], referenced: list[str]) -> list[str]:
    reasons = ["pipeline_summary"]
    if referenced:
        reasons.append("pipeline_summary_references_stale_candidate_artifact")
    if info.get("is_stale_by_time"):
        reasons.append("generated_at_or_modified_time_older_than_24h")
    summary = payload.get("summary") if isinstance(payload, dict) else {}
    counts = summary.get("evaluator_counts") if isinstance(summary, dict) else {}
    if _positive_count_from_mapping(counts) > 0:
        reasons.append("pipeline_summary_reports_existing_positive_evaluator_rows")
    return reasons


def _legacy_candidate_artifact(relative_path: str, payload: Any, info: dict[str, Any]) -> bool:
    lowered = relative_path.lower()
    if _known_stale_path(relative_path):
        return True
    if "paper_candidate" in lowered or "paper_candidates" in lowered:
        return bool(info.get("is_stale_by_time") or _paper_row_count(payload) > 0 or _positive_summary_count(payload) > 0)
    return False


def _legacy_artifact_reasons(relative_path: str, payload: Any, info: dict[str, Any]) -> list[str]:
    reasons = ["paper_candidate_related_filename"]
    if _known_stale_path(relative_path):
        reasons.append("known_stale_mlb_artifact")
    if info.get("is_stale_by_time"):
        reasons.append("generated_at_or_modified_time_older_than_24h")
    if _paper_row_count(payload) > 0:
        reasons.append("contains_existing_positive_evaluator_rows")
    if _positive_summary_count(payload) > 0:
        reasons.append("summary_reports_existing_positive_evaluator_rows")
    return reasons


def _looks_like_pipeline_summary(relative_path: str, payload: Any) -> bool:
    return (
        relative_path.lower().endswith("pipeline_summary.json")
        or (isinstance(payload, dict) and payload.get("source") == "targeted_pipeline_runner")
    )


def _known_stale_path(relative_path: str) -> bool:
    normalized = relative_path.replace("\\", "/")
    if normalized in KNOWN_STALE_RELATIVE_PATHS or Path(normalized).name in KNOWN_STALE_RELATIVE_PATHS:
        return True
    return any(normalized.startswith(prefix) for prefix in KNOWN_STALE_DIR_PREFIXES)


def _referenced_stale_files(payload: Any, stale_reference_set: set[str]) -> list[str]:
    if not stale_reference_set:
        return []
    text_values = [value.replace("\\", "/") for value in _walk_strings(payload)]
    found: set[str] = set()
    normalized_refs = {_normalize_ref(ref) for ref in stale_reference_set if ref}
    basenames = {Path(ref).name for ref in normalized_refs if ref}
    for value in text_values:
        for ref in normalized_refs:
            if ref and ref in value:
                found.add(ref)
        for basename in basenames:
            if basename and basename in value:
                found.add(basename)
    return sorted(found)


def _paper_row_count(payload: Any) -> int:
    return sum(1 for row in _walk_dicts(payload) if row.get("action") == EVALUATOR_ACTION)


def _positive_summary_count(payload: Any) -> int:
    count = 0
    for mapping in _walk_dicts(payload):
        count += _positive_count_from_mapping(mapping)
    return count


def _positive_count_from_mapping(mapping: Any) -> int:
    if not isinstance(mapping, dict):
        return 0
    return _int(mapping.get(EVALUATOR_ACTION))


def _archive_destination(path: Path, *, input_dir: Path, archive_dir: Path) -> Path:
    relative = Path(_relative_path(path, input_dir))
    return archive_dir / relative


def _move_command(source: Path, destination: Path) -> str:
    return f'Move-Item -LiteralPath "{_ps_escape(_display_path(source))}" -Destination "{_ps_escape(_display_path(destination))}"'


def _safe_command_string(command: str) -> bool:
    forbidden = ["&&", "||", ";", "`n", "\n", "\r", "*", "?"]
    return not any(token in command for token in forbidden)


def _ps_escape(value: str) -> str:
    return value.replace('"', '`"')


def _load_json(path: Path) -> tuple[Any, dict[str, Any] | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, {"source_file": _display_path(path), "reason_code": "json_file_missing", "blocker": "saved_json_file_missing"}
    except json.JSONDecodeError:
        return None, {"source_file": _display_path(path), "reason_code": "invalid_json", "blocker": "saved_json_invalid"}
    except OSError as exc:
        return None, {"source_file": _display_path(path), "reason_code": "json_read_error", "blocker": f"saved_json_read_error:{type(exc).__name__}"}


def _mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        return None


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _is_stale(value: datetime | None, generated_at: datetime) -> bool:
    return bool(value is not None and generated_at - value > STALE_AFTER)


def _age_seconds(value: datetime | None, generated_at: datetime) -> float | None:
    if value is None:
        return None
    return round((generated_at - value).total_seconds(), 3)


def _walk_dicts(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        rows = [payload]
        for value in payload.values():
            rows.extend(_walk_dicts(value))
        return rows
    if isinstance(payload, list):
        rows: list[dict[str, Any]] = []
        for value in payload:
            rows.extend(_walk_dicts(value))
        return rows
    return []


def _walk_strings(payload: Any) -> list[str]:
    if isinstance(payload, str):
        return [payload]
    if isinstance(payload, dict):
        rows: list[str] = []
        for value in payload.values():
            rows.extend(_walk_strings(value))
        return rows
    if isinstance(payload, list):
        rows: list[str] = []
        for value in payload:
            rows.extend(_walk_strings(value))
        return rows
    return []


def _relative_path(path: Path, input_dir: Path) -> str:
    try:
        return str(path.relative_to(input_dir)).replace("\\", "/")
    except ValueError:
        return path.name


def _normalize_ref(value: str) -> str:
    normalized = value.replace("\\", "/")
    marker = "/reports/"
    if marker in normalized:
        return normalized.split(marker, 1)[1]
    if normalized.startswith("reports/"):
        return normalized[len("reports/") :]
    return normalized


def _is_under_archive(path: Path, input_dir: Path) -> bool:
    relative = _relative_path(path, input_dir)
    return relative == "archive" or relative.startswith("archive/")


def _path_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _display_path(path: Path | None) -> str | None:
    if path is None:
        return None
    return str(path)


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


def _require_tz_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _md(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")
