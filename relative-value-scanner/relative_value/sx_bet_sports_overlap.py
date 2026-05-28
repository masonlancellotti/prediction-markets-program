from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
REPORT_SOURCE = "sx_bet_sports_overlap_v1"

MANUAL_SPORTS_TYPED_KEY_REVIEW = "MANUAL_SPORTS_TYPED_KEY_REVIEW"
NEEDS_VOID_RULE_REVIEW = "NEEDS_VOID_RULE_REVIEW"
NEEDS_SETTLEMENT_SOURCE_REVIEW = "NEEDS_SETTLEMENT_SOURCE_REVIEW"
REFERENCE_ONLY = "REFERENCE_ONLY"
NO_ACTION = "NO_ACTION"

EXACT_TYPED_KEY_MATCH = "EXACT_TYPED_KEY_MATCH"
PARTIAL_TYPED_KEY_MATCH = "PARTIAL_TYPED_KEY_MATCH"
BLOCKED_TYPED_KEY_MISMATCH = "BLOCKED_TYPED_KEY_MISMATCH"

FORBIDDEN_TIER_LITERALS = ("PAPER" + "_CANDIDATE", "EXACT_PAYOFF" + "_REVIEW_READY", "EXECUTION" + "_EVALUATION_READY")
GAME_LEVEL_MARKET_TYPES = {"moneyline", "spread", "total"}
FUTURES_OR_CHAMPIONSHIP_MARKET_TYPES = {"future", "futures", "championship", "futures/championship"}


def build_sx_bet_sports_overlap_report(
    *,
    sx_bet_typed_keys_path: Path,
    input_dir: Path,
    require_game_level_target: bool = False,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    warnings: list[dict[str, Any]] = []
    sx_payload, warning = _load_sx_typed_keys(sx_bet_typed_keys_path)
    if warning is not None:
        warnings.append(warning)
        sx_rows: list[dict[str, Any]] = []
    else:
        sx_rows = _sx_rows(sx_payload)
    target_rows, target_warnings = _load_target_rows(input_dir)
    warnings.extend(target_warnings)
    targets = [_target_market(row) for row in target_rows]
    targets = [target for target in targets if target is not None]
    scope_breakdown = _scope_mismatch_breakdown(sx_rows, targets)
    reasons: list[str] = []
    matching_targets = targets
    if require_game_level_target:
        matching_targets = [target for target in targets if _is_game_level_market_type(target["typed_key"].get("market_type"))]
        if not matching_targets:
            reason = "no_game_level_kalshi_polymarket_targets_in_input_dir"
            reasons.append(reason)
            warnings.append({"source_file": str(input_dir), "blocker": reason})
    target_index = _index_targets(matching_targets)
    overlap_rows = _overlap_rows(sx_rows, target_index)
    return {
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "sx_bet_typed_keys": str(sx_bet_typed_keys_path),
        "input_dir": str(input_dir),
        "require_game_level_target": require_game_level_target,
        "reasons": reasons,
        "summary": _summary(sx_rows, overlap_rows, warnings, scope_breakdown=scope_breakdown, reasons=reasons),
        "rows": overlap_rows,
        "warnings": warnings,
        "target_report_inventory": _target_inventory(input_dir),
        "safety": _safety_block(),
    }


def write_sx_bet_sports_overlap_files(
    *,
    sx_bet_typed_keys_path: Path,
    input_dir: Path,
    json_output: Path,
    markdown_output: Path,
    require_game_level_target: bool = False,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_sx_bet_sports_overlap_report(
        sx_bet_typed_keys_path=sx_bet_typed_keys_path,
        input_dir=input_dir,
        require_game_level_target=require_game_level_target,
        generated_at=generated_at,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_sx_bet_sports_overlap_markdown(report), encoding="utf-8")
    return report


def render_sx_bet_sports_overlap_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# SX Bet Sports Overlap Diagnostics",
        "",
        "Saved-file-only typed-key overlap report. It creates no candidates, asserts no exact payoff, and does not affect evaluator gates.",
        "",
        "## Summary",
        "",
        f"- sx_bet_rows_considered: `{summary.get('sx_bet_rows_considered', 0)}`",
        f"- overlap_rows: `{summary.get('overlap_rows', 0)}`",
        f"- exact_typed_key_matches: `{summary.get('exact_typed_key_matches', 0)}`",
        f"- partial_matches: `{summary.get('partial_matches', 0)}`",
        f"- blocked_reference_only: `{summary.get('blocked_reference_only', 0)}`",
        f"- candidate_count: `{summary.get('candidate_count', 0)}`",
        f"- pair_count: `{summary.get('pair_count', 0)}`",
        f"- require_game_level_target: `{str(report.get('require_game_level_target', False)).lower()}`",
        "",
        "## Scope Mismatch",
        "",
    ]
    scope = summary.get("scope_mismatch_breakdown") or {}
    lines.extend(
        [
            f"- sx_bet_rows_total: `{scope.get('sx_bet_rows_total', 0)}`",
            f"- sx_bet_rows_usable: `{scope.get('sx_bet_rows_usable', 0)}`",
            f"- kalshi_polymarket_targets_total: `{scope.get('kalshi_polymarket_targets_total', 0)}`",
            f"- kalshi_polymarket_targets_game_level: `{scope.get('kalshi_polymarket_targets_game_level', 0)}`",
            f"- kalshi_polymarket_targets_futures_or_championship: `{scope.get('kalshi_polymarket_targets_futures_or_championship', 0)}`",
            f"- leagues_with_sx_but_no_targets: `{', '.join(scope.get('leagues_with_sx_but_no_targets') or []) or 'none'}`",
            f"- leagues_with_targets_but_no_sx: `{', '.join(scope.get('leagues_with_targets_but_no_sx') or []) or 'none'}`",
        ]
    )
    reasons = report.get("reasons") or []
    if reasons:
        lines.extend(["", "## Reasons", ""])
        for reason in reasons:
            lines.append(f"- `{_md(reason)}`")
    lines.extend(
        [
            "",
        "## Top Blockers",
        "",
        ]
    )
    blockers = summary.get("top_blockers") or []
    if blockers:
        lines.extend(["| Blocker | Count |", "|---|---:|"])
        for blocker in blockers[:12]:
            lines.append(f"| {_md(blocker.get('blocker'))} | {_md(blocker.get('count'))} |")
    else:
        lines.append("(none)")
    lines.extend(["", "## Overlap Rows", "", "| SX Bet market | Venue | Matched market | Confidence | Action | Blockers |", "|---|---|---|---|---|---|"])
    for row in (report.get("rows") or [])[:50]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("sx_bet_market_id")),
                    _md(row.get("matched_venue")),
                    _md(row.get("matched_market_id") or row.get("matched_ticker")),
                    _md(row.get("confidence_tier")),
                    _md(row.get("allowed_next_action")),
                    _md(",".join(row.get("blockers") or [])),
                ]
            )
            + " |"
        )
    if not report.get("rows"):
        lines.append("| (none) | (none) | (none) | (none) | NO_ACTION | (none) |")
    lines.extend(["", "## By League", ""])
    lines.extend(_count_table(summary.get("by_league") or [], "league"))
    lines.extend(["", "## By Market Type", ""])
    lines.extend(_count_table(summary.get("by_market_type") or [], "market_type"))
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- saved_files_only: `true`",
            "- live_api_calls_attempted: `false`",
            "- candidates_or_pairs_created: `false`",
            "- affects_evaluator_gates: `false`",
        ]
    )
    return "\n".join(lines) + "\n"


def _overlap_rows(sx_rows: list[dict[str, Any]], target_index: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sx in sx_rows:
        sx_key = sx.get("typed_key") if isinstance(sx.get("typed_key"), dict) else {}
        if not sx.get("usable_for_future_overlap_review"):
            continue
        league = _norm(sx_key.get("league"))
        if not league:
            continue
        for target in target_index.get(league, []):
            if _is_scope_mismatch(sx_key, target["typed_key"]):
                continue
            comparison = _compare_keys(sx_key, target["typed_key"])
            if not comparison["should_output"]:
                continue
            blockers = sorted(
                set(
                    comparison["blockers"]
                    + ["reference_only_no_executable_market"]
                    + _source_blockers(sx, target)
                )
            )
            confidence = EXACT_TYPED_KEY_MATCH if not comparison["missing_keys"] and not comparison["mismatched_keys"] else PARTIAL_TYPED_KEY_MATCH
            if comparison["mismatched_keys"]:
                confidence = BLOCKED_TYPED_KEY_MISMATCH
            rows.append(
                {
                    "sx_bet_market_id": sx.get("market_id"),
                    "sx_bet_source_row": {
                        "row_index": sx.get("row_index"),
                        "raw_source_file": sx.get("raw_source_file"),
                        "raw_row_index": sx.get("raw_row_index"),
                    },
                    "matched_venue": target.get("venue"),
                    "matched_market_id": target.get("market_id"),
                    "matched_ticker": target.get("ticker"),
                    "matched_source_file": target.get("source_file"),
                    "matched_typed_keys": {
                        "sx_bet": _typed_key_subset(sx_key),
                        "matched": _typed_key_subset(target["typed_key"]),
                    },
                    "missing_keys": comparison["missing_keys"],
                    "mismatched_keys": comparison["mismatched_keys"],
                    "confidence_tier": confidence,
                    "blockers": blockers,
                    "allowed_next_action": _allowed_next_action(blockers, confidence),
                    "diagnostic_only": True,
                    "affects_evaluator_gates": False,
                    "candidate_or_pair_created": False,
                    "usable_as_executable_market": False,
                }
            )
    return rows


def _compare_keys(sx_key: dict[str, Any], target_key: dict[str, Any]) -> dict[str, Any]:
    required = ["league", "event_time", "participants", "market_type", "side"]
    missing: list[str] = []
    mismatched: list[str] = []
    matched: list[str] = []
    for key in required:
        sx_value = sx_key.get(key)
        target_value = target_key.get(key)
        if sx_value in (None, [], "") or target_value in (None, [], ""):
            missing.append(key)
            continue
        if _key_equal(key, sx_value, target_value):
            matched.append(key)
        else:
            mismatched.append(key)
    market_type = _norm(sx_key.get("market_type"))
    if market_type in {"spread", "total", "prop"}:
        sx_line = sx_key.get("line", sx_key.get("threshold"))
        target_line = target_key.get("line", target_key.get("threshold"))
        if sx_line is None or target_line is None:
            missing.append("line")
        elif _number(sx_line) == _number(target_line):
            matched.append("line")
        else:
            mismatched.append("line")
    blockers = [f"missing_{key}" for key in missing] + [f"{key}_mismatch" for key in mismatched]
    should_output = False
    if not missing and not mismatched:
        should_output = True
    elif _norm(sx_key.get("league")) == _norm(target_key.get("league")) and _participants_equal(
        sx_key.get("participants"), target_key.get("participants")
    ):
        # Emit structured near-overlaps when explicit team/league keys align,
        # even if event time or line blocks the row. Title text is never used.
        should_output = True
    elif _norm(sx_key.get("league")) == _norm(target_key.get("league")) and _event_date(sx_key.get("event_time")) == _event_date(
        target_key.get("event_time")
    ) and _norm(sx_key.get("market_type")) == _norm(target_key.get("market_type")):
        should_output = True
    return {
        "matched_keys": matched,
        "missing_keys": sorted(set(missing)),
        "mismatched_keys": sorted(set(mismatched)),
        "blockers": sorted(set(blockers)),
        "should_output": should_output,
    }


def _key_equal(key: str, left: Any, right: Any) -> bool:
    if key == "event_time":
        return _event_time_equal(left, right)
    if key == "participants":
        return _participants_equal(left, right)
    return _norm(left) == _norm(right)


def _event_time_equal(left: Any, right: Any) -> bool:
    left_text = _string_or_none(left)
    right_text = _string_or_none(right)
    if not left_text or not right_text:
        return False
    if left_text == right_text:
        return True
    return _event_date(left_text) == _event_date(right_text)


def _event_date(value: Any) -> str | None:
    text = _string_or_none(value)
    if not text:
        return None
    return text[:10] if len(text) >= 10 else text


def _participants_equal(left: Any, right: Any) -> bool:
    left_values = {_norm(item) for item in _list(left)}
    right_values = {_norm(item) for item in _list(right)}
    left_values.discard("")
    right_values.discard("")
    return bool(left_values) and left_values == right_values


def _source_blockers(sx: dict[str, Any], target: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if "missing_void_rules" in (sx.get("blockers") or []):
        blockers.append("missing_void_rules")
    target_blockers = target.get("source_blockers") or []
    if "missing_settlement_source_url" in target_blockers or "source_evidence_missing" in target_blockers:
        blockers.append("missing_settlement_source_or_registry")
    if "missing_settlement_rules_source_text" in (sx.get("blockers") or []):
        blockers.append("missing_sx_bet_settlement_source_text")
    return blockers


def _allowed_next_action(blockers: list[str], confidence: str) -> str:
    if "reference_only_no_executable_market" in blockers:
        return REFERENCE_ONLY
    if "missing_void_rules" in blockers:
        return NEEDS_VOID_RULE_REVIEW
    if "missing_settlement_source_or_registry" in blockers:
        return NEEDS_SETTLEMENT_SOURCE_REVIEW
    if confidence == EXACT_TYPED_KEY_MATCH:
        return MANUAL_SPORTS_TYPED_KEY_REVIEW
    return NO_ACTION


def _summary(
    sx_rows: list[dict[str, Any]],
    overlap_rows: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    *,
    scope_breakdown: dict[str, Any],
    reasons: list[str],
) -> dict[str, Any]:
    by_league = Counter()
    by_market_type = Counter()
    blockers = Counter()
    usable_sx_rows = sum(1 for row in sx_rows if row.get("usable_for_future_overlap_review"))
    for row in overlap_rows:
        sx_key = (row.get("matched_typed_keys") or {}).get("sx_bet") or {}
        if sx_key.get("league"):
            by_league[str(sx_key["league"])] += 1
        if sx_key.get("market_type"):
            by_market_type[str(sx_key["market_type"])] += 1
        blockers.update(row.get("blockers") or [])
    if not overlap_rows and usable_sx_rows:
        blockers["no_explicit_typed_key_overlap"] = usable_sx_rows
    for reason in reasons:
        blockers[reason] += 1
    return {
        "sx_bet_rows_considered": len(sx_rows),
        "sx_bet_rows_usable_for_future_overlap_review": usable_sx_rows,
        "overlap_rows": len(overlap_rows),
        "exact_typed_key_matches": sum(1 for row in overlap_rows if row.get("confidence_tier") == EXACT_TYPED_KEY_MATCH),
        "partial_matches": sum(1 for row in overlap_rows if row.get("confidence_tier") in {PARTIAL_TYPED_KEY_MATCH, BLOCKED_TYPED_KEY_MISMATCH}),
        "blocked_reference_only": sum(1 for row in overlap_rows if "reference_only_no_executable_market" in (row.get("blockers") or [])),
        "candidate_count": 0,
        "pair_count": 0,
        "by_league": [{"league": key, "count": count} for key, count in sorted(by_league.items())],
        "by_market_type": [{"market_type": key, "count": count} for key, count in sorted(by_market_type.items())],
        "scope_mismatch_breakdown": scope_breakdown,
        "reasons": list(reasons),
        "top_blockers": [{"blocker": key, "count": count} for key, count in blockers.most_common()],
        "warning_count": len(warnings),
    }


def _scope_mismatch_breakdown(sx_rows: list[dict[str, Any]], targets: list[dict[str, Any]]) -> dict[str, Any]:
    usable_sx = [row for row in sx_rows if row.get("usable_for_future_overlap_review")]
    sx_leagues = {
        _norm(((row.get("typed_key") or {}) if isinstance(row.get("typed_key"), dict) else {}).get("league"))
        for row in usable_sx
    }
    sx_leagues.discard("")
    target_leagues = {_norm((target.get("typed_key") or {}).get("league")) for target in targets}
    target_leagues.discard("")
    return {
        "sx_bet_rows_total": len(sx_rows),
        "sx_bet_rows_usable": len(usable_sx),
        "kalshi_polymarket_targets_total": len(targets),
        "kalshi_polymarket_targets_game_level": sum(
            1 for target in targets if _is_game_level_market_type((target.get("typed_key") or {}).get("market_type"))
        ),
        "kalshi_polymarket_targets_futures_or_championship": sum(
            1 for target in targets if _is_futures_or_championship_market_type((target.get("typed_key") or {}).get("market_type"))
        ),
        "leagues_with_sx_but_no_targets": sorted(sx_leagues - target_leagues),
        "leagues_with_targets_but_no_sx": sorted(target_leagues - sx_leagues),
    }


def _load_target_rows(input_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    normalized, warning = _load_optional_json(input_dir / "normalized_markets_v0.json")
    if warning:
        warnings.append(warning)
    elif isinstance(normalized, dict):
        for index, row in enumerate(normalized.get("normalized_markets") if isinstance(normalized.get("normalized_markets"), list) else []):
            if isinstance(row, dict):
                item = dict(row)
                item["_source_report"] = "normalized_markets_v0"
                item["_source_row_index"] = index
                rows.append(item)
    burden, warning = _load_optional_json(input_dir / "settlement_evidence_burden.json")
    if warning:
        warnings.append(warning)
    elif isinstance(burden, dict):
        for index, row in enumerate(burden.get("markets") if isinstance(burden.get("markets"), list) else []):
            if isinstance(row, dict):
                item = dict(row)
                item["_source_report"] = "settlement_evidence_burden"
                item["_source_row_index"] = index
                rows.append(item)
    return rows, warnings


def _target_market(row: dict[str, Any]) -> dict[str, Any] | None:
    venue = _string_or_none(row.get("venue"))
    if venue not in {"kalshi", "polymarket"}:
        return None
    typed_key = _target_typed_key(row)
    if not typed_key or not _norm(typed_key.get("league")):
        return None
    if not any(typed_key.get(key) not in (None, [], "") for key in ("event_time", "participants", "market_type", "side", "line", "threshold")):
        return None
    return {
        "venue": venue,
        "market_id": row.get("market_id") or row.get("ticker"),
        "ticker": row.get("ticker"),
        "source_file": row.get("source_file") or row.get("_source_report"),
        "typed_key": typed_key,
        "source_blockers": list(row.get("blockers") or []),
    }


def _target_typed_key(row: dict[str, Any]) -> dict[str, Any]:
    for key in ("sports_typed_key", "typed_key"):
        value = row.get(key)
        if isinstance(value, dict):
            return _normalize_typed_key(value)
    evidence = row.get("typed_key_evidence") if isinstance(row.get("typed_key_evidence"), dict) else {}
    typed = {key: _evidence_value(evidence.get(key)) for key in ("league", "season", "team", "event_time", "market_type", "line", "threshold", "side")}
    if typed.get("team"):
        typed["participants"] = [typed["team"]]
        typed["side"] = typed.get("side") or "winner_or_field_sides"
    if row.get("family") == "SPORTS_FUTURES_CHAMPIONSHIP":
        typed["market_type"] = typed.get("market_type") or "futures/championship"
    for key in ("league", "event_time", "market_type", "line", "threshold", "side"):
        if row.get(key) is not None and typed.get(key) in (None, ""):
            typed[key] = row.get(key)
    if isinstance(row.get("participants"), list):
        typed["participants"] = row.get("participants")
    elif isinstance(row.get("teams"), list):
        typed["participants"] = row.get("teams")
    return _normalize_typed_key(typed)


def _is_scope_mismatch(sx_key: dict[str, Any], target_key: dict[str, Any]) -> bool:
    sx_type = sx_key.get("market_type")
    target_type = target_key.get("market_type")
    return (
        _is_game_level_market_type(sx_type)
        and _is_futures_or_championship_market_type(target_type)
    ) or (
        _is_futures_or_championship_market_type(sx_type)
        and _is_game_level_market_type(target_type)
    )


def _is_game_level_market_type(value: Any) -> bool:
    return _norm(value) in GAME_LEVEL_MARKET_TYPES


def _is_futures_or_championship_market_type(value: Any) -> bool:
    normalized = _norm(value)
    return normalized in FUTURES_OR_CHAMPIONSHIP_MARKET_TYPES or (
        "future" in normalized or "championship" in normalized
    )


def _normalize_typed_key(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    if "threshold" not in result and "line" in result:
        result["threshold"] = result.get("line")
    if "line" not in result and "threshold" in result:
        result["line"] = result.get("threshold")
    if "participants" in result:
        result["participants"] = _list(result.get("participants"))
    return result


def _index_targets(targets: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for target in targets:
        league = _norm(target["typed_key"].get("league"))
        if league:
            result[league].append(target)
    return result


def _typed_key_subset(value: dict[str, Any]) -> dict[str, Any]:
    keys = ("league", "event_time", "participants", "market_type", "side", "line", "threshold", "season")
    return {key: value.get(key) for key in keys if key in value}


def _sx_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and row.get("venue") == "sx_bet"]


def _load_sx_typed_keys(path: Path) -> tuple[Any, dict[str, Any] | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, {"source_file": str(path), "blocker": "sx_bet_typed_keys_missing"}
    except json.JSONDecodeError:
        return None, {"source_file": str(path), "blocker": "sx_bet_typed_keys_invalid_json"}
    except OSError as exc:
        return None, {"source_file": str(path), "blocker": "sx_bet_typed_keys_read_failed", "message": str(exc)}
    if not isinstance(payload, dict) or payload.get("source") != "sx_bet_sports_typed_keys_v1":
        return None, {"source_file": str(path), "blocker": "unsupported_sx_bet_typed_keys_source"}
    return payload, None


def _load_optional_json(path: Path) -> tuple[Any, dict[str, Any] | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, {"source_file": str(path), "blocker": "saved_report_missing"}
    except json.JSONDecodeError:
        return None, {"source_file": str(path), "blocker": "saved_report_invalid_json"}
    except OSError as exc:
        return None, {"source_file": str(path), "blocker": "saved_report_read_failed", "message": str(exc)}


def _target_inventory(input_dir: Path) -> list[dict[str, Any]]:
    return [
        {"path": str(input_dir / "normalized_markets_v0.json"), "expected": True, "exists": (input_dir / "normalized_markets_v0.json").exists()},
        {"path": str(input_dir / "settlement_evidence_burden.json"), "expected": True, "exists": (input_dir / "settlement_evidence_burden.json").exists()},
    ]


def _evidence_value(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("value")
    return None


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _norm(value: Any) -> str:
    text = _string_or_none(value)
    return " ".join(text.lower().replace("_", " ").split()) if text else ""


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _count_table(rows: list[dict[str, Any]], label: str) -> list[str]:
    if not rows:
        return ["(none)"]
    lines = [f"| {label} | Count |", "|---|---:|"]
    for row in rows:
        lines.append(f"| {_md(row.get(label))} | {_md(row.get('count'))} |")
    return lines


def _safety_block() -> dict[str, Any]:
    return {
        "saved_files_only": True,
        "live_api_calls_attempted": False,
        "auth_or_account_flow_added": False,
        "wallet_or_signing_logic_added": False,
        "order_or_execution_logic_added": False,
        "candidates_or_pairs_created": False,
        "affects_evaluator_gates": False,
        "paper_candidate_emitted": False,
        "forbidden_readiness_tiers_emitted": False,
    }


def _md(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
