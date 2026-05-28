from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.local_manifest_v1 import LOCAL_MANIFEST_SOURCE, validate_local_manifest_v1_group


REPORT_SOURCE = "kalshi_event_evidence_summary_v1"
DEFAULT_EVENT_TICKER = "KXMLB-26"
DEFAULT_MAX_QUOTE_AGE_SECONDS = 1800.0

_SKIP_FILENAMES = {
    "kalshi_kxmlb26_event_evidence_summary.json",
    "kalshi_kxmlb26_event_evidence_summary.md",
}


def build_kalshi_event_evidence_summary(
    *,
    input_dir: Path,
    event_ticker: str = DEFAULT_EVENT_TICKER,
    generated_at: datetime | None = None,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    target = event_ticker.upper()

    evidence = _empty_evidence()
    files_read = 0
    warnings: list[dict[str, str]] = []
    for path in _candidate_json_paths(input_dir):
        payload, warning = _load_json(path)
        if warning is not None:
            warnings.append(warning)
            continue
        files_read += 1
        _collect_payload_evidence(payload, path=path, target=target, evidence=evidence)

    market_tickers = sorted(evidence["market_tickers"])
    apparent_outcomes = sorted(evidence["apparent_outcomes"])
    explicit_outcome_lists = evidence["explicit_outcome_lists"]
    explicit_outcome_list = explicit_outcome_lists[0]["outcomes"] if explicit_outcome_lists else []
    explicit_outcome_list_exists = bool(explicit_outcome_list)
    explicit_completeness_exists = bool(evidence["completeness_evidence"])
    settlement_source_exists = bool(evidence["settlement_source_evidence"])
    rules_source_exists = bool(evidence["event_level_rules_evidence"])
    shared_rules_evidence_exists = bool(evidence["shared_rules_evidence"])
    event_level_market_list_exists = bool(evidence["event_level_market_lists"])

    quote_depth = _quote_depth_summary(
        evidence["quote_records"],
        generated_at=generated,
        max_quote_age_seconds=max_quote_age_seconds,
        expected_market_count=len(market_tickers),
    )
    hypothetical_manifest = _hypothetical_manifest(
        event_ticker=target,
        market_tickers=market_tickers,
        explicit_outcome_list=explicit_outcome_list,
        explicit_completeness_exists=explicit_completeness_exists,
        settlement_source=evidence["settlement_source_evidence"][0]["value"] if evidence["settlement_source_evidence"] else None,
        rules_evidence=evidence["event_level_rules_evidence"][0]["value"] if evidence["event_level_rules_evidence"] else None,
        generated_at=generated,
    )
    manifest_blockers = validate_local_manifest_v1_group(hypothetical_manifest)

    missing_fields = _missing_fields(
        explicit_outcome_list_exists=explicit_outcome_list_exists,
        explicit_completeness_exists=explicit_completeness_exists,
        settlement_source_exists=settlement_source_exists,
        rules_source_exists=rules_source_exists,
        shared_rules_evidence_exists=shared_rules_evidence_exists,
        fresh_orderbook_depth_exists=quote_depth["fresh_orderbook_depth_exists"],
        manifest_blockers=manifest_blockers,
    )
    ready_for_human_manifest_review = not missing_fields
    blockers = sorted(
        set(missing_fields)
        | set(quote_depth["blockers"])
        | ({"not_ready_for_human_manifest_review"} if not ready_for_human_manifest_review else set())
    )

    source_files = sorted(evidence["source_files"].values(), key=lambda item: (item["path"], item["evidence_kind"]))
    summary = {
        "event_ticker": target,
        "files_considered": len(_candidate_json_paths(input_dir)),
        "files_read": files_read,
        "source_files_with_evidence": len(source_files),
        "market_count": len(market_tickers),
        "apparent_outcome_count": len(apparent_outcomes),
        "explicit_outcome_list_exists": explicit_outcome_list_exists,
        "explicit_completeness_evidence_exists": explicit_completeness_exists,
        "settlement_rules_source_evidence_exists": settlement_source_exists,
        "shared_rules_source_evidence_exists": shared_rules_evidence_exists,
        "event_level_market_list_exists": event_level_market_list_exists,
        "fresh_orderbook_depth_exists": quote_depth["fresh_orderbook_depth_exists"],
        "local_manifest_v1_would_pass_if_reviewer_fields_added": not manifest_blockers,
        "ready_for_human_manifest_review": ready_for_human_manifest_review,
        "paper_candidate_count": 0,
        "top_blockers": _counter_rows(Counter(blockers)),
    }
    return {
        "schema_version": 1,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "input_dir": str(input_dir),
        "event_ticker": target,
        "summary": summary,
        "event_identity": {
            "event_ticker": target,
            "event_id_values_seen": sorted(evidence["event_ids"]),
            "series_ticker_values_seen": sorted(evidence["series_tickers"]),
            "series_ticker_present": bool(evidence["series_tickers"]),
        },
        "market_tickers": market_tickers,
        "apparent_per_market_outcomes_not_event_level_evidence": apparent_outcomes,
        "event_level_evidence": {
            "explicit_outcome_list": explicit_outcome_list,
            "explicit_outcome_list_sources": explicit_outcome_lists,
            "completeness_evidence": evidence["completeness_evidence"],
            "event_level_market_lists": evidence["event_level_market_lists"],
            "settlement_source_evidence": evidence["settlement_source_evidence"],
            "event_level_rules_evidence": evidence["event_level_rules_evidence"],
            "shared_rules_evidence": evidence["shared_rules_evidence"],
            "per_market_rules_samples": evidence["per_market_rules_samples"][:10],
        },
        "quote_depth_evidence": quote_depth,
        "local_manifest_v1_hypothetical": {
            "assumption": "reviewer/reviewed_at/trusted_local_manifest were populated only for this validation probe",
            "would_pass_if_reviewer_fields_added": not manifest_blockers,
            "remaining_blockers": manifest_blockers,
        },
        "ready_for_human_manifest_review": ready_for_human_manifest_review,
        "missing_fields": missing_fields,
        "blockers": blockers,
        "source_files": source_files,
        "warnings": warnings,
        "safety": {
            "saved_file_only": True,
            "diagnostic_only": True,
            "live_fetch_attempted": False,
            "manifest_written": False,
            "manifest_approved": False,
            "manifest_gates_lowered": False,
            "evaluator_gates_lowered": False,
            "uses_title_similarity_for_exhaustiveness": False,
            "uses_ticker_pattern_for_exhaustiveness": False,
            "uses_market_count_for_exhaustiveness": False,
            "affects_evaluator_gates": False,
            "paper_candidate_emitted": False,
        },
    }


def write_kalshi_event_evidence_summary_files(
    *,
    input_dir: Path,
    json_output: Path,
    markdown_output: Path,
    event_ticker: str = DEFAULT_EVENT_TICKER,
    generated_at: datetime | None = None,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
) -> dict[str, Any]:
    report = build_kalshi_event_evidence_summary(
        input_dir=input_dir,
        event_ticker=event_ticker,
        generated_at=generated_at,
        max_quote_age_seconds=max_quote_age_seconds,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_kalshi_event_evidence_summary_markdown(report), encoding="utf-8")
    return report


def render_kalshi_event_evidence_summary_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    identity = report.get("event_identity") or {}
    quote = report.get("quote_depth_evidence") or {}
    manifest = report.get("local_manifest_v1_hypothetical") or {}
    lines = [
        "# Kalshi KXMLB Event Evidence Summary",
        "",
        "Saved-file-only diagnostic. No manifest was written or approved, and no manifest/evaluator gate was changed.",
        "",
        "## Summary",
        "",
        f"- event_ticker: `{report.get('event_ticker')}`",
        f"- market_count: `{summary.get('market_count', 0)}`",
        f"- apparent_outcome_count: `{summary.get('apparent_outcome_count', 0)}`",
        f"- explicit_outcome_list_exists: `{str(bool(summary.get('explicit_outcome_list_exists'))).lower()}`",
        f"- explicit_completeness_evidence_exists: `{str(bool(summary.get('explicit_completeness_evidence_exists'))).lower()}`",
        f"- settlement_rules_source_evidence_exists: `{str(bool(summary.get('settlement_rules_source_evidence_exists'))).lower()}`",
        f"- shared_rules_source_evidence_exists: `{str(bool(summary.get('shared_rules_source_evidence_exists'))).lower()}`",
        f"- event_level_market_list_exists: `{str(bool(summary.get('event_level_market_list_exists'))).lower()}`",
        f"- fresh_orderbook_depth_exists: `{str(bool(summary.get('fresh_orderbook_depth_exists'))).lower()}`",
        f"- local_manifest_v1_would_pass_if_reviewer_fields_added: `{str(bool(summary.get('local_manifest_v1_would_pass_if_reviewer_fields_added'))).lower()}`",
        f"- ready_for_human_manifest_review: `{str(bool(summary.get('ready_for_human_manifest_review'))).lower()}`",
        "",
        "## Identity",
        "",
        f"- event_id_values_seen: `{', '.join(identity.get('event_id_values_seen') or []) or 'none'}`",
        f"- series_ticker_values_seen: `{', '.join(identity.get('series_ticker_values_seen') or []) or 'none'}`",
        "",
        "## Missing Fields",
        "",
    ]
    missing = report.get("missing_fields") or []
    if missing:
        lines.extend(f"- `{item}`" for item in missing)
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Quote/Depth",
            "",
            f"- quote_records_seen: `{quote.get('quote_records_seen', 0)}`",
            f"- records_with_depth: `{quote.get('records_with_depth', 0)}`",
            f"- oldest_quote_captured_at: `{quote.get('oldest_quote_captured_at')}`",
            f"- newest_quote_captured_at: `{quote.get('newest_quote_captured_at')}`",
            f"- max_quote_age_seconds: `{quote.get('max_quote_age_seconds')}`",
            f"- max_quote_age_threshold_seconds: `{quote.get('max_quote_age_threshold_seconds')}`",
            f"- blockers: `{', '.join(quote.get('blockers') or []) or 'none'}`",
            "",
            "## Manifest Probe",
            "",
            "This validation probe only asks whether the currently saved evidence would pass if reviewer fields were filled. It does not write a manifest.",
            "",
            f"- would_pass_if_reviewer_fields_added: `{str(bool(manifest.get('would_pass_if_reviewer_fields_added'))).lower()}`",
            f"- remaining_blockers: `{', '.join(manifest.get('remaining_blockers') or []) or 'none'}`",
            "",
            "## Evidence Notes",
            "",
            "- Apparent per-market outcomes are listed for reviewer convenience only; they are not event-level outcome-list evidence.",
            "- Title, ticker pattern, and market count are not used as completeness evidence.",
            "- Per-market rules with one team substituted are not accepted as shared event-level settlement/source evidence.",
            "",
            "## Source Files",
            "",
            "| Path | Evidence kind | Records |",
            "|---|---|---:|",
        ]
    )
    for item in report.get("source_files") or []:
        lines.append(
            "| {path} | {kind} | {count} |".format(
                path=_md(item.get("path")),
                kind=_md(item.get("evidence_kind")),
                count=item.get("record_count", 0),
            )
        )
    return "\n".join(lines) + "\n"


def _empty_evidence() -> dict[str, Any]:
    return {
        "event_ids": set(),
        "series_tickers": set(),
        "market_tickers": set(),
        "apparent_outcomes": set(),
        "explicit_outcome_lists": [],
        "completeness_evidence": [],
        "event_level_market_lists": [],
        "settlement_source_evidence": [],
        "event_level_rules_evidence": [],
        "shared_rules_evidence": [],
        "per_market_rules_samples": [],
        "quote_records": [],
        "source_files": {},
    }


def _candidate_json_paths(input_dir: Path) -> list[Path]:
    if not input_dir.exists():
        return []
    roots = [
        input_dir / "manifest_templates",
        input_dir / "manifest_scouts",
        input_dir / "native_group_audits",
        input_dir / "live_readonly" / "mlb",
    ]
    explicit = [
        input_dir / "structural_basket_hunt.json",
        input_dir / "structural_basket_dry_run_structural.json",
        input_dir / "mlb_kxmlb_kalshi_snapshot.json",
        input_dir / "mlb_kxmlb_kalshi_enriched.json",
        input_dir / "mlb_kxmlb_48h_unitok_kalshi_snapshot.json",
        input_dir / "mlb_kxmlb_48h_unitok_kalshi_enriched.json",
        input_dir / "mlb_kxmlb_48h_unitok_after_guardrails_kalshi_snapshot.json",
        input_dir / "mlb_kxmlb_48h_unitok_after_guardrails_kalshi_enriched.json",
    ]
    paths: set[Path] = {path for path in explicit if path.exists()}
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.json"):
            lowered = path.name.lower()
            if lowered in _SKIP_FILENAMES:
                continue
            if "polymarket" in lowered:
                continue
            paths.add(path)
    for path in input_dir.glob("*kxmlb*.json"):
        if path.name.lower() not in _SKIP_FILENAMES:
            paths.add(path)
    return sorted(paths)


def _load_json(path: Path) -> tuple[Any, dict[str, str] | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except json.JSONDecodeError:
        return None, {"path": str(path), "warning": "invalid_json"}
    except OSError as exc:
        return None, {"path": str(path), "warning": f"read_error:{type(exc).__name__}"}


def _collect_payload_evidence(payload: Any, *, path: Path, target: str, evidence: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        return
    for group in _iter_group_like(payload):
        if not _matches_target(group, target):
            continue
        _note_source(evidence, path, "event_or_group_metadata")
        _collect_identity(group, target, evidence)
        _collect_event_level_fields(group, path, evidence)
    markets = _matching_markets(payload, target)
    if markets:
        _note_source(evidence, path, "market_rows", len(markets))
    for market in markets:
        _collect_market_fields(market, path, target, evidence)
    for scout in _matching_scout_rows(payload, target):
        _note_source(evidence, path, "manifest_or_hunt_scout")
        _collect_identity(scout, target, evidence)
        if scout.get("has_shared_rules") is True:
            _add_evidence_value(evidence["shared_rules_evidence"], "scout_has_shared_rules=true", path, "has_shared_rules")
        for market in scout.get("markets") if isinstance(scout.get("markets"), list) else []:
            if isinstance(market, dict):
                _collect_market_fields(market, path, target, evidence)


def _iter_group_like(payload: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for key in ("events", "exhaustive_groups", "groups"):
        value = payload.get(key)
        if isinstance(value, list):
            output.extend(item for item in value if isinstance(item, dict))
    event = payload.get("event")
    if isinstance(event, dict):
        output.append(event)
    if any(key in payload for key in ("event_ticker", "event_id", "venue_native_event_id", "group_id")):
        output.append(payload)
    return output


def _matching_markets(payload: dict[str, Any], target: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    events = payload.get("events")
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict) or not _matches_target(event, target):
                continue
            markets = event.get("markets")
            if isinstance(markets, list):
                _add_event_market_list(event, markets, target, payload_path=None)
                for market in markets:
                    if isinstance(market, dict):
                        merged = dict(market)
                        merged.setdefault("event_ticker", _string(event.get("event_ticker") or event.get("event_id")))
                        rows.append(merged)
    for key in ("normalized_markets", "markets", "records"):
        value = payload.get(key)
        if not isinstance(value, list):
            continue
        for market in value:
            if isinstance(market, dict) and _market_matches_target(market, target):
                rows.append(market)
    return rows


def _matching_scout_rows(payload: dict[str, Any], target: str) -> list[dict[str, Any]]:
    rows = []
    for key in ("rows", "closest_groups_to_review"):
        value = payload.get(key)
        if not isinstance(value, list):
            continue
        for row in value:
            if isinstance(row, dict) and _matches_target(row, target):
                rows.append(row)
    return rows


def _matches_target(row: dict[str, Any], target: str) -> bool:
    fields = (
        "event_ticker",
        "event_id",
        "venue_native_event_id",
        "venue_native_group_id",
        "group_id",
        "ticker",
        "market_ticker",
    )
    for key in fields:
        value = row.get(key)
        if isinstance(value, str) and value.upper() == target:
            return True
    return _market_matches_target(row, target)


def _market_matches_target(row: dict[str, Any], target: str) -> bool:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    for item in (row, raw):
        for key in ("event_ticker", "event_id", "venue_native_event_id", "venue_native_group_id"):
            value = item.get(key)
            if isinstance(value, str) and value.upper() == target:
                return True
        for key in ("ticker", "market_ticker", "market_id"):
            value = item.get(key)
            if isinstance(value, str) and value.upper().startswith(target + "-"):
                return True
    return False


def _collect_identity(row: dict[str, Any], target: str, evidence: dict[str, Any]) -> None:
    for key in ("event_ticker", "event_id", "venue_native_event_id", "venue_native_group_id", "group_id"):
        value = _string(row.get(key))
        if value and (value.upper() == target or value.upper().startswith(target)):
            evidence["event_ids"].add(value)
    series = _string(row.get("series_ticker") or row.get("series"))
    if series:
        evidence["series_tickers"].add(series)


def _collect_event_level_fields(row: dict[str, Any], path: Path, evidence: dict[str, Any]) -> None:
    outcomes = _string_list(row.get("outcome_list") or row.get("outcomes") or row.get("complete_outcome_list"))
    if outcomes and not _binary_yes_no(outcomes):
        evidence["explicit_outcome_lists"].append({"source_file": str(path), "outcomes": outcomes})
    market_tickers = _string_list(row.get("market_tickers") or row.get("exact_market_tickers") or row.get("market_ids"))
    event_markets = row.get("markets")
    if isinstance(event_markets, list):
        event_market_tickers = [
            ticker
            for ticker in (
                _string(market.get("market_ticker") or market.get("ticker") or market.get("id"))
                for market in event_markets
                if isinstance(market, dict)
            )
            if ticker
        ]
        if event_market_tickers:
            market_tickers = list(dict.fromkeys([*market_tickers, *event_market_tickers]))
    explicit_event_list = bool(outcomes) or row.get("complete") is True or row.get("is_exhaustive") is True or row.get("all_outcomes_included") is True
    if market_tickers:
        evidence["market_tickers"].update(market_tickers)
        if (
            explicit_event_list
            and row.get("manifest_template") is not True
            and row.get("manifest_template_source") != "structural_basket_hunter_template_v1"
        ):
            evidence["event_level_market_lists"].append({"source_file": str(path), "market_tickers": market_tickers})
    if row.get("complete") is True or row.get("is_exhaustive") is True or row.get("all_outcomes_included") is True:
        _add_evidence_value(evidence["completeness_evidence"], "complete/is_exhaustive/all_outcomes_included=true", path, "complete")
    for key in ("settlement_source_evidence", "settlement_source_raw_evidence", "settlement_source", "resolution_source"):
        _add_if_string(evidence["settlement_source_evidence"], row, key, path)
    for key in ("rules_evidence", "resolution_rules_evidence", "rules_primary", "rules_secondary", "rules", "resolution_text"):
        _add_if_string(evidence["event_level_rules_evidence"], row, key, path)


def _collect_market_fields(row: dict[str, Any], path: Path, target: str, evidence: dict[str, Any]) -> None:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    _collect_identity({**raw, **row}, target, evidence)
    ticker = _string(row.get("market_ticker") or row.get("ticker") or row.get("market_id") or raw.get("ticker"))
    if ticker:
        evidence["market_tickers"].add(ticker)
    outcome = _string(
        row.get("outcome")
        or row.get("yes_sub_title")
        or row.get("outcome_label")
        or raw.get("yes_sub_title")
        or raw.get("sub_title")
    )
    if outcome and outcome.lower() not in {"yes", "no"}:
        evidence["apparent_outcomes"].add(outcome)
    rules = _string(row.get("rules_primary") or row.get("rules") or raw.get("rules_primary") or raw.get("rules"))
    if rules and len(evidence["per_market_rules_samples"]) < 25:
        evidence["per_market_rules_samples"].append({"source_file": str(path), "market_ticker": ticker, "rules": rules})
    quote = _quote_record(row, raw, path=path, ticker=ticker)
    if quote:
        evidence["quote_records"].append(quote)


def _quote_record(row: dict[str, Any], raw: dict[str, Any], *, path: Path, ticker: str | None) -> dict[str, Any] | None:
    orderbook = row.get("orderbook_enrichment") if isinstance(row.get("orderbook_enrichment"), dict) else {}
    captured_at = _string(orderbook.get("orderbook_captured_at") or row.get("orderbook_captured_at"))
    best_ask = _float(orderbook.get("best_ask") or row.get("best_ask"))
    depth = _float(orderbook.get("depth_at_best_ask") or row.get("depth_at_best_ask"))
    if depth is None:
        depth = _float(orderbook.get("ask_depth") or row.get("ask_depth"))
    if not captured_at and best_ask is None and depth is None:
        return None
    return {
        "source_file": str(path),
        "market_ticker": ticker,
        "best_ask": best_ask,
        "depth_at_best_ask": depth,
        "orderbook_captured_at": captured_at,
        "raw_updated_time_seen_not_counted": raw.get("updated_time") if isinstance(raw.get("updated_time"), str) else None,
    }


def _quote_depth_summary(
    quote_records: list[dict[str, Any]],
    *,
    generated_at: datetime,
    max_quote_age_seconds: float,
    expected_market_count: int,
) -> dict[str, Any]:
    parsed_times = []
    blockers: list[str] = []
    records_with_depth = 0
    for record in quote_records:
        if record.get("depth_at_best_ask") is not None:
            records_with_depth += 1
        parsed = _parse_datetime(record.get("orderbook_captured_at"))
        if parsed is not None:
            parsed_times.append(parsed)
    if not quote_records:
        blockers.append("missing_orderbook_depth_evidence")
    if expected_market_count and len(quote_records) < expected_market_count:
        blockers.append("missing_orderbook_depth_for_some_markets")
    if records_with_depth < expected_market_count:
        blockers.append("missing_depth_for_some_markets")
    if len(parsed_times) < expected_market_count:
        blockers.append("missing_quote_timestamp_for_some_markets")
    max_age = None
    if parsed_times:
        max_age = max(max(0.0, (generated_at - value).total_seconds()) for value in parsed_times)
        if max_age > max_quote_age_seconds:
            blockers.append("stale_orderbook_depth")
    fresh = bool(expected_market_count and len(parsed_times) >= expected_market_count and records_with_depth >= expected_market_count and max_age is not None and max_age <= max_quote_age_seconds)
    return {
        "quote_records_seen": len(quote_records),
        "records_with_depth": records_with_depth,
        "expected_market_count": expected_market_count,
        "oldest_quote_captured_at": min(parsed_times).isoformat() if parsed_times else None,
        "newest_quote_captured_at": max(parsed_times).isoformat() if parsed_times else None,
        "max_quote_age_seconds": None if max_age is None else round(max_age, 6),
        "max_quote_age_threshold_seconds": max_quote_age_seconds,
        "fresh_orderbook_depth_exists": fresh,
        "blockers": sorted(set(blockers)),
        "sample_quote_records": quote_records[:5],
    }


def _hypothetical_manifest(
    *,
    event_ticker: str,
    market_tickers: list[str],
    explicit_outcome_list: list[str],
    explicit_completeness_exists: bool,
    settlement_source: str | None,
    rules_evidence: str | None,
    generated_at: datetime,
) -> dict[str, Any]:
    return {
        "source": LOCAL_MANIFEST_SOURCE,
        "trusted_local_manifest": True,
        "reviewer": "hypothetical-reviewer-fields-added",
        "reviewed_at": generated_at.isoformat(),
        "venue": "kalshi",
        "group_id": event_ticker,
        "venue_native_event_id": event_ticker,
        "venue_native_group_id": event_ticker,
        "market_tickers": market_tickers,
        "outcome_list": explicit_outcome_list,
        "complete": explicit_completeness_exists,
        "is_exhaustive": explicit_completeness_exists,
        "evidence_text": "explicit saved event evidence found" if explicit_outcome_list and explicit_completeness_exists else None,
        "settlement_source_evidence": settlement_source,
        "rules_evidence": rules_evidence,
    }


def _missing_fields(
    *,
    explicit_outcome_list_exists: bool,
    explicit_completeness_exists: bool,
    settlement_source_exists: bool,
    rules_source_exists: bool,
    shared_rules_evidence_exists: bool,
    fresh_orderbook_depth_exists: bool,
    manifest_blockers: list[str],
) -> list[str]:
    missing: list[str] = []
    if not explicit_outcome_list_exists:
        missing.append("explicit_event_level_outcome_list")
    if not explicit_completeness_exists:
        missing.append("explicit_completeness_or_exhaustiveness_evidence")
    if not settlement_source_exists:
        missing.append("settlement_source_evidence")
    if not rules_source_exists:
        missing.append("event_level_rules_or_resolution_source_evidence")
    if not shared_rules_evidence_exists:
        missing.append("shared_rules_source_evidence_across_markets")
    if not fresh_orderbook_depth_exists:
        missing.append("fresh_orderbook_depth")
    for blocker in manifest_blockers:
        if blocker == "missing_manifest_reviewer" or blocker == "missing_manifest_reviewed_at":
            continue
        missing.append(f"local_manifest_v1:{blocker}")
    return sorted(set(missing))


def _add_event_market_list(event: dict[str, Any], markets: list[Any], target: str, payload_path: Path | None) -> None:
    # Kept as a placeholder for future event-object shape handling; evidence is
    # collected from actual market rows by _matching_markets.
    return None


def _add_if_string(output: list[dict[str, Any]], row: dict[str, Any], key: str, path: Path) -> None:
    value = _string(row.get(key))
    if value:
        _add_evidence_value(output, value, path, key)


def _add_evidence_value(output: list[dict[str, Any]], value: str, path: Path, field: str) -> None:
    item = {"source_file": str(path), "field": field, "value": value}
    if item not in output:
        output.append(item)


def _note_source(evidence: dict[str, Any], path: Path, kind: str, count: int = 1) -> None:
    key = f"{path}|{kind}"
    row = evidence["source_files"].setdefault(
        key,
        {"path": str(path), "evidence_kind": kind, "record_count": 0},
    )
    row["record_count"] += count


def _counter_rows(counter: Counter[str]) -> list[dict[str, Any]]:
    return [{"blocker": key, "count": value} for key, value in counter.most_common()]


def _string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _binary_yes_no(values: list[str]) -> bool:
    return {value.strip().lower() for value in values} <= {"yes", "no"}


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _md(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
