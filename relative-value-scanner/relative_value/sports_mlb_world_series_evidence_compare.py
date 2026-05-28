from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from relative_value.sports_mlb_world_series_evidence_collector import TEAM_CODE_TO_NAME, team_code


SCHEMA_VERSION = 1
SCHEMA_KIND = "sports_mlb_world_series_evidence_compare_v1"
REPORT_SOURCE = "sports_mlb_world_series_evidence_compare_v1"

TAIL_RISK_BLOCKERS = (
    "proportional_payout_vs_other_outcome_mismatch",
    "remote_tail_risk_review_required",
)

_UNSUPPORTED_SCOPE_RE = re.compile(
    r"\b(daily|game[_ -]?winner|spread|total|player[_ -]?prop|prop|run[_ -]?line|inning|innings)\b",
    re.IGNORECASE,
)


def write_sports_mlb_world_series_evidence_compare_files(
    *,
    kalshi_evidence: Path,
    polymarket_evidence: Path,
    json_output: Path,
    markdown_output: Path,
    accept_world_series_remote_tail_risk: bool = False,
) -> dict[str, Any]:
    kalshi_payload = _read_json(kalshi_evidence)
    polymarket_payload = _read_json(polymarket_evidence)
    report = build_sports_mlb_world_series_evidence_comparison(
        kalshi_payload=kalshi_payload,
        polymarket_payload=polymarket_payload,
        accept_world_series_remote_tail_risk=accept_world_series_remote_tail_risk,
        kalshi_input=str(kalshi_evidence),
        polymarket_input=str(polymarket_evidence),
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_sports_mlb_world_series_evidence_compare_markdown(report), encoding="utf-8")
    return report


def build_sports_mlb_world_series_evidence_comparison(
    *,
    kalshi_payload: dict[str, Any],
    polymarket_payload: dict[str, Any],
    accept_world_series_remote_tail_risk: bool = False,
    kalshi_input: str | None = None,
    polymarket_input: str | None = None,
) -> dict[str, Any]:
    kalshi_scope = validate_evidence_scope(kalshi_payload, expected_platform="Kalshi")
    polymarket_scope = validate_evidence_scope(polymarket_payload, expected_platform="Polymarket")
    season = _evidence_season(kalshi_payload) or _evidence_season(polymarket_payload)
    scope_blockers = sorted(set(kalshi_scope["blockers"] + polymarket_scope["blockers"]))

    kalshi_rows = _extract_kalshi_rows(kalshi_payload)
    polymarket_rows = _extract_polymarket_rows(polymarket_payload)
    kalshi_by_code = {row["canonical_team_key"]: row for row in kalshi_rows if row.get("canonical_team_key")}
    polymarket_by_code = {row["canonical_team_key"]: row for row in polymarket_rows if row.get("canonical_team_key")}
    matched_codes = sorted(set(kalshi_by_code) & set(polymarket_by_code), key=_team_sort_key)
    unmatched_kalshi = sorted(set(kalshi_by_code) - set(polymarket_by_code), key=_team_sort_key)
    unmatched_polymarket = sorted(set(polymarket_by_code) - set(kalshi_by_code), key=_team_sort_key)

    rows: list[dict[str, Any]] = []
    for code in matched_codes:
        rows.append(
            _build_matched_row(
                code=code,
                kalshi=kalshi_by_code[code],
                polymarket=polymarket_by_code[code],
                scope_blockers=scope_blockers,
                accept_world_series_remote_tail_risk=accept_world_series_remote_tail_risk,
            )
        )
    for code in unmatched_kalshi:
        rows.append(_build_unmatched_row(code=code, kalshi=kalshi_by_code[code], polymarket=None, scope_blockers=scope_blockers))
    for code in unmatched_polymarket:
        rows.append(_build_unmatched_row(code=code, kalshi=None, polymarket=polymarket_by_code[code], scope_blockers=scope_blockers))

    blockers = Counter()
    actions = Counter()
    for row in rows:
        blockers.update(row.get("blockers") or [])
        actions[row.get("action") or "UNKNOWN"] += 1
    for blocker in scope_blockers:
        blockers[blocker] += 1

    summary_counts = {
        "rows": len(rows),
        "source_review_rows": actions.get("SOURCE_REVIEW", 0),
        "manual_review_rows": actions.get("MANUAL_REVIEW", 0),
        "watch_rows": actions.get("WATCH", 0),
        "ignore_blocked_rows": actions.get("IGNORE_BLOCKED", 0),
        "exact_ready_rows": 0,
        "paper_candidate_rows": 0,
        "top_blockers": [{"blocker": key, "count": value} for key, value in blockers.most_common(15)],
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "schema_kind": SCHEMA_KIND,
        "source": REPORT_SOURCE,
        "diagnostic_only": True,
        "strict_exact_arb": False,
        "exact_ready_rows": 0,
        "paper_candidate_rows": 0,
        "paper_candidate_emitted": False,
        "human_accepted_remote_tail_risk": bool(accept_world_series_remote_tail_risk),
        "residual_risk_type": (
            "mlb_world_series_no_champion_other_vs_proportional_tail_risk"
            if accept_world_series_remote_tail_risk
            else None
        ),
        "season": season,
        "inputs": {
            "kalshi_evidence": kalshi_input,
            "polymarket_evidence": polymarket_input,
        },
        "scope_validation": {
            "kalshi": kalshi_scope,
            "polymarket": polymarket_scope,
            "valid": not scope_blockers,
        },
        "kalshi_rows_loaded": len(kalshi_rows),
        "polymarket_rows_loaded": len(polymarket_rows),
        "matched_team_rows": len(matched_codes),
        "unmatched_kalshi_rows": len(unmatched_kalshi),
        "unmatched_polymarket_rows": len(unmatched_polymarket),
        "rows": rows,
        "summary_counts": summary_counts,
        "top_blockers": summary_counts["top_blockers"],
        "safety": {
            "diagnostic_only": True,
            "candidate_pair_creation": False,
            "evaluator_invoked": False,
            "strict_exact_arb": False,
            "exact_ready": False,
            "paper_candidate_emitted": False,
        },
    }


def validate_evidence_scope(payload: dict[str, Any], *, expected_platform: str) -> dict[str, Any]:
    blockers: list[str] = []
    platform = _payload_field(payload, "platform")
    if platform != expected_platform:
        blockers.append("wrong_platform_scope")
    league = _payload_field(payload, "league")
    if league != "MLB":
        blockers.append("not_mlb_scope")
    batch = _payload_field(payload, "batch")
    if batch != "championship_futures":
        blockers.append("not_championship_futures_scope")
    text = " ".join(
        str(value or "")
        for value in (
            payload.get("schema_kind"),
            payload.get("date_label"),
            payload.get("market_title"),
            _payload_field(payload, "market_title"),
            batch,
        )
    )
    if "games" in payload or _UNSUPPORTED_SCOPE_RE.search(text):
        blockers.append("unsupported_market_scope")
    for outcome in payload.get("outcomes") or []:
        if isinstance(outcome, dict) and _UNSUPPORTED_SCOPE_RE.search(
            " ".join(str(outcome.get(key) or "") for key in ("market_type", "team_name", "outcome_name"))
        ):
            blockers.append("unsupported_market_scope")
            break
    return {
        "platform": platform,
        "league": league,
        "season": _evidence_season(payload),
        "batch": batch,
        "valid": not blockers,
        "blockers": sorted(set(blockers)),
    }


def render_sports_mlb_world_series_evidence_compare_markdown(report: dict[str, Any]) -> str:
    counts = report.get("summary_counts") or {}
    lines = [
        "# MLB World Series Cross-Venue Evidence Comparison",
        "",
        "Diagnostic/source-review only. This report does not create candidate pairs, does not invoke evaluator gates, and does not mark strict riskless arb.",
        "",
        "## Summary",
        "",
        f"- season: `{_md(report.get('season'))}`",
        f"- human_accepted_remote_tail_risk: `{str(bool(report.get('human_accepted_remote_tail_risk'))).lower()}`",
        f"- kalshi_rows_loaded: `{report.get('kalshi_rows_loaded', 0)}`",
        f"- polymarket_rows_loaded: `{report.get('polymarket_rows_loaded', 0)}`",
        f"- matched_team_rows: `{report.get('matched_team_rows', 0)}`",
        f"- unmatched_kalshi_rows: `{report.get('unmatched_kalshi_rows', 0)}`",
        f"- unmatched_polymarket_rows: `{report.get('unmatched_polymarket_rows', 0)}`",
        f"- exact_ready_rows: `0`",
        f"- paper_candidate_rows: `0`",
        "",
        "## Matched Teams",
        "",
        "| Team | Kalshi ticker | Polymarket market | Kalshi yes ask | Polymarket yes ask | Quote status | Action | Blockers |",
        "|---|---|---|---:|---:|---|---|---|",
    ]
    matched_rows = [row for row in report.get("rows") or [] if row.get("kalshi_market_ticker") and row.get("polymarket_market_id")]
    if matched_rows:
        for row in matched_rows:
            blockers = ", ".join(row.get("blockers") or [])
            lines.append(
                "| "
                f"{_md(row.get('team_name'))} | "
                f"{_md(row.get('kalshi_market_ticker'))} | "
                f"{_md(row.get('polymarket_market_id'))} | "
                f"{_md(row.get('kalshi_yes_ask'))} | "
                f"{_md(row.get('polymarket_ask'))} | "
                f"{_md(row.get('quote_timestamp_status'))} | "
                f"{_md(row.get('action'))} | "
                f"{_md(blockers)} |"
            )
    else:
        lines.append("| none |  |  |  |  |  |  |  |")
    lines.extend(["", "## Unmatched Rows", ""])
    unmatched = [
        row
        for row in report.get("rows") or []
        if not (row.get("kalshi_market_ticker") and row.get("polymarket_market_id"))
    ]
    if unmatched:
        for row in unmatched:
            lines.append(
                f"- `{_md(row.get('canonical_team_key'))}` {_md(row.get('team_name'))}: "
                f"kalshi=`{_md(row.get('kalshi_market_ticker'))}` polymarket=`{_md(row.get('polymarket_market_id'))}` "
                f"blockers=`{_md(', '.join(row.get('blockers') or []))}`"
            )
    else:
        lines.append("_None._")
    lines.extend(["", "## Top Blockers", "", "| Blocker | Count |", "|---|---:|"])
    blockers = report.get("top_blockers") or []
    if blockers:
        for item in blockers:
            lines.append(f"| {_md(item.get('blocker'))} | {_md(item.get('count'))} |")
    else:
        lines.append("| none | 0 |")
    lines.extend(
        [
            "",
            "## Warning",
            "",
            "Kalshi proportional cancellation/no-contest payout and Polymarket Other/no-champion handling remain a remote tail-risk mismatch. This is diagnostic/source-review only.",
            "",
            "## Safety",
            "",
            "- diagnostic_only: `true`",
            "- strict_exact_arb: `false`",
            "- candidate_pair_creation: `false`",
            "- exact_ready: `false`",
            "- paper_candidate_emitted: `false`",
        ]
    )
    return "\n".join(lines) + "\n"


def _build_matched_row(
    *,
    code: str,
    kalshi: dict[str, Any],
    polymarket: dict[str, Any],
    scope_blockers: list[str],
    accept_world_series_remote_tail_risk: bool,
) -> dict[str, Any]:
    blockers = list(scope_blockers)
    blockers.extend(TAIL_RISK_BLOCKERS)
    if accept_world_series_remote_tail_risk:
        blockers.append("remote_tail_risk_human_accepted_but_not_exact")
    blockers.extend(_quote_blockers("kalshi", kalshi))
    blockers.extend(_quote_blockers("polymarket", polymarket))
    if not polymarket.get("polymarket_yes_token_id") or not polymarket.get("polymarket_no_token_id"):
        blockers.append("missing_polymarket_token_ids")
    if not kalshi.get("kalshi_market_ticker"):
        blockers.append("missing_kalshi_ticker")
    blockers = sorted(set(blockers))
    action = _action_for_blockers(blockers, matched=True)
    quote_timestamp_status = _quote_timestamp_status(kalshi, polymarket)
    return {
        "canonical_team_key": code,
        "team_name": _canonical_team_name(code),
        "kalshi_team_name": kalshi.get("team_name"),
        "kalshi_market_ticker": kalshi.get("kalshi_market_ticker"),
        "polymarket_team_name": polymarket.get("team_name"),
        "polymarket_market_id": polymarket.get("polymarket_market_id"),
        "polymarket_condition_id": polymarket.get("polymarket_condition_id"),
        "polymarket_yes_token_id": polymarket.get("polymarket_yes_token_id"),
        "polymarket_no_token_id": polymarket.get("polymarket_no_token_id"),
        "kalshi_yes_bid": kalshi.get("kalshi_yes_bid"),
        "kalshi_yes_ask": kalshi.get("kalshi_yes_ask"),
        "kalshi_depth_status": kalshi.get("kalshi_depth_status"),
        "polymarket_bid": polymarket.get("polymarket_bid"),
        "polymarket_ask": polymarket.get("polymarket_ask"),
        "polymarket_depth_status": polymarket.get("polymarket_depth_status"),
        "quote_timestamp_status": quote_timestamp_status,
        "rules_match_status": "source_review_required",
        "settlement_source_status": "source_review_required",
        "cancellation_tail_risk_status": (
            "accepted_for_diagnostic_review_only"
            if accept_world_series_remote_tail_risk
            else "blocked_remote_tail_risk"
        ),
        "blockers": blockers,
        "action": action,
        "strict_exact_arb": False,
        "exact_ready": False,
        "paper_candidate": False,
    }


def _build_unmatched_row(
    *,
    code: str,
    kalshi: dict[str, Any] | None,
    polymarket: dict[str, Any] | None,
    scope_blockers: list[str],
) -> dict[str, Any]:
    blockers = list(scope_blockers)
    if kalshi is None:
        blockers.append("missing_kalshi_team_row")
    if polymarket is None:
        blockers.append("missing_polymarket_team_row")
    if kalshi and not kalshi.get("kalshi_market_ticker"):
        blockers.append("missing_kalshi_ticker")
    if polymarket and (not polymarket.get("polymarket_yes_token_id") or not polymarket.get("polymarket_no_token_id")):
        blockers.append("missing_polymarket_token_ids")
    blockers = sorted(set(blockers))
    return {
        "canonical_team_key": code,
        "team_name": _canonical_team_name(code),
        "kalshi_team_name": kalshi.get("team_name") if kalshi else None,
        "kalshi_market_ticker": kalshi.get("kalshi_market_ticker") if kalshi else None,
        "polymarket_team_name": polymarket.get("team_name") if polymarket else None,
        "polymarket_market_id": polymarket.get("polymarket_market_id") if polymarket else None,
        "polymarket_condition_id": polymarket.get("polymarket_condition_id") if polymarket else None,
        "polymarket_yes_token_id": polymarket.get("polymarket_yes_token_id") if polymarket else None,
        "polymarket_no_token_id": polymarket.get("polymarket_no_token_id") if polymarket else None,
        "kalshi_yes_bid": kalshi.get("kalshi_yes_bid") if kalshi else None,
        "kalshi_yes_ask": kalshi.get("kalshi_yes_ask") if kalshi else None,
        "kalshi_depth_status": kalshi.get("kalshi_depth_status") if kalshi else None,
        "polymarket_bid": polymarket.get("polymarket_bid") if polymarket else None,
        "polymarket_ask": polymarket.get("polymarket_ask") if polymarket else None,
        "polymarket_depth_status": polymarket.get("polymarket_depth_status") if polymarket else None,
        "quote_timestamp_status": "unmatched",
        "rules_match_status": "unmatched",
        "settlement_source_status": "unmatched",
        "cancellation_tail_risk_status": "unmatched",
        "blockers": blockers,
        "action": "MANUAL_REVIEW" if not scope_blockers else "IGNORE_BLOCKED",
        "strict_exact_arb": False,
        "exact_ready": False,
        "paper_candidate": False,
    }


def _extract_kalshi_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for outcome in payload.get("outcomes") or []:
        if not isinstance(outcome, dict):
            continue
        code = _team_code_from_outcome(outcome)
        if not code:
            continue
        quote = outcome.get("quote") if isinstance(outcome.get("quote"), dict) else {}
        rows.append(
            {
                "canonical_team_key": code,
                "team_name": outcome.get("team_name") or _canonical_team_name(code),
                "kalshi_market_ticker": _string_or_none(outcome.get("market_ticker")),
                "kalshi_yes_bid": _string_or_none(quote.get("yes_bid")),
                "kalshi_yes_ask": _string_or_none(quote.get("yes_ask")),
                "kalshi_depth_status": _string_or_none(quote.get("depth_status") or outcome.get("quote_status")),
                "quote_timestamp": _string_or_none(quote.get("quote_timestamp") or quote.get("fetch_time_utc")),
                "quote_required_fields_present": bool(quote.get("required_quote_fields_present")),
                "blockers": outcome.get("blockers_remaining") or [],
            }
        )
    return rows


def _extract_polymarket_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for outcome in payload.get("outcomes") or []:
        if not isinstance(outcome, dict):
            continue
        code = _team_code_from_outcome(outcome)
        if not code:
            continue
        quote = outcome.get("quote") if isinstance(outcome.get("quote"), dict) else {}
        rows.append(
            {
                "canonical_team_key": code,
                "team_name": outcome.get("team_name") or outcome.get("outcome_name") or _canonical_team_name(code),
                "polymarket_market_id": _string_or_none(outcome.get("market_id")),
                "polymarket_condition_id": _string_or_none(outcome.get("condition_id")),
                "polymarket_yes_token_id": _string_or_none(outcome.get("token_id_yes")),
                "polymarket_no_token_id": _string_or_none(outcome.get("token_id_no")),
                "polymarket_bid": _string_or_none(quote.get("yes_bid")),
                "polymarket_ask": _string_or_none(quote.get("yes_ask")),
                "polymarket_depth_status": _string_or_none(quote.get("depth_status") or outcome.get("quote_status")),
                "quote_timestamp": _string_or_none(quote.get("quote_timestamp")),
                "quote_required_fields_present": bool(quote.get("required_quote_fields_present")),
                "blockers": outcome.get("blockers_remaining") or [],
            }
        )
    return rows


def _team_code_from_outcome(outcome: dict[str, Any]) -> str | None:
    for value in (
        outcome.get("team_name"),
        outcome.get("outcome_name"),
        outcome.get("market_ticker"),
        *(outcome.get("team_aliases") if isinstance(outcome.get("team_aliases"), list) else []),
    ):
        code = _team_code_any(value)
        if code:
            return code
    return None


def _team_code_any(value: Any) -> str | None:
    text = _string_or_none(value)
    if not text:
        return None
    if "-" in text and text.upper().startswith("KXMLB-"):
        text = text.rsplit("-", 1)[-1]
    cleaned = text.replace("A's", "Athletics").replace("As", "Athletics")
    return team_code(cleaned)


def _quote_blockers(prefix: str, row: dict[str, Any]) -> list[str]:
    blockers = [f"{prefix}_{blocker}" for blocker in row.get("blockers") or []]
    if not row.get("quote_timestamp"):
        blockers.append(f"{prefix}_missing_quote_timestamp")
    if not row.get("quote_required_fields_present"):
        blockers.append(f"{prefix}_incomplete_quote_depth")
    return blockers


def _quote_timestamp_status(kalshi: dict[str, Any], polymarket: dict[str, Any]) -> str:
    has_kalshi = bool(kalshi.get("quote_timestamp"))
    has_polymarket = bool(polymarket.get("quote_timestamp"))
    if has_kalshi and has_polymarket:
        return "complete"
    if has_kalshi or has_polymarket:
        return "partial"
    return "missing"


def _action_for_blockers(blockers: list[str], *, matched: bool) -> str:
    if any(blocker in blockers for blocker in ("wrong_platform_scope", "not_mlb_scope", "not_championship_futures_scope", "unsupported_market_scope")):
        return "IGNORE_BLOCKED"
    if not matched:
        return "MANUAL_REVIEW"
    if any("missing_" in blocker or "incomplete_quote_depth" in blocker for blocker in blockers):
        return "MANUAL_REVIEW"
    return "SOURCE_REVIEW"


def _payload_field(payload: dict[str, Any], key: str) -> Any:
    value = payload.get(key)
    if value is not None:
        return value
    market = payload.get("market")
    if isinstance(market, dict):
        return market.get(key)
    return None


def _evidence_season(payload: dict[str, Any]) -> str | None:
    value = _payload_field(payload, "season")
    return _string_or_none(value)


def _canonical_team_name(code: str) -> str:
    return TEAM_CODE_TO_NAME.get(code, code)


def _team_sort_key(code: str) -> str:
    return _canonical_team_name(code)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _md(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")
