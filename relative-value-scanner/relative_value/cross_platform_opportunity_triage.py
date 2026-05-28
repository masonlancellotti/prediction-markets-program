from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.venue_identity import (
    IBKR_KALSHI_FAKE_EDGE_BLOCKERS,
    broker_route_fake_edge_blockers,
    executable_venue_identity_from_mapping,
)


SCHEMA_VERSION = 1

EXACT_EQUALITY_CANDIDATE = "EXACT_EQUALITY_CANDIDATE"
COMPLEMENT_CANDIDATE = "COMPLEMENT_CANDIDATE"
SUBSET_SUPERSET_CANDIDATE = "SUBSET_SUPERSET_CANDIDATE"
THRESHOLD_LADDER_CANDIDATE = "THRESHOLD_LADDER_CANDIDATE"
RANGE_OR_PARTITION_CANDIDATE = "RANGE_OR_PARTITION_CANDIDATE"
STALE_OR_LAG_CANDIDATE = "STALE_OR_LAG_CANDIDATE"
BTC_BASIS_RISK_REVIEW = "BTC_BASIS_RISK_REVIEW"
CRYPTO_RELATED_FV_WATCH = "CRYPTO_RELATED_FV_WATCH"
GRAPH_ADVISORY_CANDIDATE = "GRAPH_ADVISORY_CANDIDATE"
SIMILARITY_ONLY_RESEARCH = "SIMILARITY_ONLY_RESEARCH"

RELATIONSHIP_CLASSES = {
    EXACT_EQUALITY_CANDIDATE,
    COMPLEMENT_CANDIDATE,
    SUBSET_SUPERSET_CANDIDATE,
    THRESHOLD_LADDER_CANDIDATE,
    RANGE_OR_PARTITION_CANDIDATE,
    STALE_OR_LAG_CANDIDATE,
    BTC_BASIS_RISK_REVIEW,
    CRYPTO_RELATED_FV_WATCH,
    GRAPH_ADVISORY_CANDIDATE,
    SIMILARITY_ONLY_RESEARCH,
}

TRUSTED_DIAGNOSTIC_EXACT_EVIDENCE_SOURCES = {
    "same_payoff_board_v1",
    "typed_exact_key_match_v1",
    "typed_btc_exact_key_match_v1",
    "typed_fed_exact_key_match_v1",
}

CSV_FIELDS = [
    "rank",
    "venue_a",
    "source_platform_a",
    "access_platform_a",
    "exchange_venue_a",
    "executable_venue_a",
    "market_id_a",
    "ticker_a",
    "venue_b",
    "source_platform_b",
    "access_platform_b",
    "exchange_venue_b",
    "executable_venue_b",
    "market_id_b",
    "ticker_b",
    "relationship_class",
    "confidence_tier",
    "diagnostic_only",
    "allowed_next_action",
    "blockers",
    "evidence_summary",
    "source_files",
    "reason_codes",
]


def build_cross_platform_opportunity_triage_report(
    *,
    input_dir: Path,
    graph_hints_path: Path | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    rows: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if input_dir.exists():
        for path in sorted(input_dir.rglob("*.json")):
            if graph_hints_path is not None and path.resolve() == graph_hints_path.resolve():
                continue
            payload, warning = _load_json(path)
            if warning is not None:
                warnings.append(warning)
                continue
            rows.extend(_rows_from_payload(payload, source_file=path))
    else:
        warnings.append(
            {
                "source_file": str(input_dir),
                "reason_code": "input_dir_missing",
                "blocker": "saved_input_directory_missing",
            }
        )

    graph_rows, graph_warnings = _graph_rows(graph_hints_path)
    rows.extend(graph_rows)
    warnings.extend(graph_warnings)

    deduped = _dedupe_pairs(rows)
    ranked = _rank_rows(deduped)
    summary = _summary(ranked, warnings)
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "cross_platform_opportunity_triage_v1",
        "generated_at": generated.isoformat(),
        "diagnostic_only": True,
        "input_dir": str(input_dir),
        "graph_hints_path": str(graph_hints_path) if graph_hints_path is not None else None,
        "summary": summary,
        "rows": ranked,
        "warnings": warnings,
        "safety": {
            "saved_files_only": True,
            "live_fetch_attempted": False,
            "execution_or_order_logic_added": False,
            "account_or_auth_logic_added": False,
            "paper_candidate_emitted": False,
            "affects_evaluator_gates": False,
            "text_similarity_can_create_exact_candidate": False,
            "graph_hints_can_create_exact_candidate": False,
            "reference_only_sources_are_executable_legs": False,
        },
    }


def write_cross_platform_opportunity_triage_files(
    *,
    input_dir: Path,
    json_output: Path,
    csv_output: Path,
    graph_hints_path: Path | None = None,
    markdown_output: Path | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_cross_platform_opportunity_triage_report(
        input_dir=input_dir,
        graph_hints_path=graph_hints_path,
        generated_at=generated_at,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    csv_output.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(report["rows"], csv_output)
    if markdown_output is not None:
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(render_cross_platform_opportunity_triage_markdown(report), encoding="utf-8")
    return report


def render_cross_platform_opportunity_triage_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Cross-Platform Opportunity Triage",
        "",
        "Saved-file-only diagnostic report. Rows are review targets only and never PAPER_CANDIDATE output.",
        "",
        "| Rank | Class | Venues | Markets | Confidence | Action | Blockers | Reasons |",
        "|---:|---|---|---|---|---|---|---|",
    ]
    for row in report.get("rows") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("rank")),
                    _md(row.get("relationship_class")),
                    _md(f"{row.get('venue_a') or ''} / {row.get('venue_b') or ''}"),
                    _md(f"{row.get('ticker_a') or row.get('market_id_a') or ''} / {row.get('ticker_b') or row.get('market_id_b') or ''}"),
                    _md(row.get("confidence_tier")),
                    _md(row.get("allowed_next_action")),
                    _md(",".join(row.get("blockers") or []) or "none"),
                    _md(",".join(row.get("reason_codes") or []) or "none"),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _rows_from_payload(payload: Any, *, source_file: Path) -> list[dict[str, Any]]:
    return [
        row
        for index, raw in enumerate(_candidate_objects(payload), start=1)
        if isinstance(raw, dict)
        for row in [_triage_row(raw, source_file=source_file, index=index)]
        if row is not None
    ]


def _candidate_objects(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in (
        "rows",
        "opportunities",
        "candidates",
        "pairs",
        "matches",
        "hints",
        "edges",
        "basis_risk_rows",
        "crypto_related_fv_watch_rows",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _triage_row(raw: dict[str, Any], *, source_file: Path, index: int) -> dict[str, Any] | None:
    if raw.get("diagnostic_only") is False:
        return None
    left = _leg(raw, "a", "left", "source", "polymarket")
    right = _leg(raw, "b", "right", "target", "kalshi")
    if not (_has_leg_id(left) and _has_leg_id(right)):
        return None

    requested_class = _requested_relationship_class(raw)
    has_similarity = _has_text_similarity(raw)
    has_graph = _has_graph_marker(raw)
    exact_evidence_source = _exact_evidence_source(raw)
    blockers = _string_list(raw.get("blockers"))
    reason_codes = _string_list(raw.get("reason_codes"))

    if has_graph:
        relationship_class = GRAPH_ADVISORY_CANDIDATE
        _append_unique(blockers, "graph_advisory_only")
        _append_unique(reason_codes, "graph_hint_not_trusted_for_exact_payoff")
        if _relation_text_is_exact_like(raw):
            _append_unique(blockers, "graph_exact_label_not_trusted")
    elif requested_class == EXACT_EQUALITY_CANDIDATE:
        if exact_evidence_source in TRUSTED_DIAGNOSTIC_EXACT_EVIDENCE_SOURCES:
            relationship_class = EXACT_EQUALITY_CANDIDATE
            _append_unique(blockers, "requires_existing_evaluator_gates_before_paper")
            _append_unique(reason_codes, f"explicit_exact_evidence:{exact_evidence_source}")
        else:
            relationship_class = SIMILARITY_ONLY_RESEARCH if has_similarity else EXACT_EQUALITY_CANDIDATE
            _append_unique(blockers, "missing_explicit_typed_evidence")
            _append_unique(reason_codes, "exact_candidate_requires_stronger_evidence")
            if has_similarity:
                _append_unique(blockers, "text_similarity_not_exact_payoff")
    elif requested_class in RELATIONSHIP_CLASSES:
        relationship_class = requested_class
    elif _looks_like_ladder(raw):
        relationship_class = THRESHOLD_LADDER_CANDIDATE
        _append_unique(reason_codes, "typed_threshold_or_ladder_indicator")
    elif _looks_like_subset(raw):
        relationship_class = SUBSET_SUPERSET_CANDIDATE
        _append_unique(reason_codes, "subset_superset_indicator")
    elif _looks_like_range_or_partition(raw):
        relationship_class = RANGE_OR_PARTITION_CANDIDATE
        _append_unique(reason_codes, "range_or_partition_indicator")
    elif _looks_like_stale_or_lag(raw):
        relationship_class = STALE_OR_LAG_CANDIDATE
        _append_unique(reason_codes, "stale_or_lag_indicator")
    else:
        relationship_class = SIMILARITY_ONLY_RESEARCH
        if has_similarity:
            _append_unique(blockers, "text_similarity_not_exact_payoff")
        _append_unique(blockers, "missing_explicit_typed_evidence")
        _append_unique(reason_codes, "similarity_or_untyped_overlap_only")

    if relationship_class == GRAPH_ADVISORY_CANDIDATE and requested_class == EXACT_EQUALITY_CANDIDATE:
        _append_unique(blockers, "graph_exact_label_not_trusted")
    if relationship_class == SIMILARITY_ONLY_RESEARCH and requested_class == EXACT_EQUALITY_CANDIDATE:
        _append_unique(blockers, "exactness_request_downgraded")
    fake_edge_blockers = broker_route_fake_edge_blockers(left, right)
    for blocker in fake_edge_blockers:
        _append_unique(blockers, blocker)
    if fake_edge_blockers:
        _append_unique(reason_codes, "broker_route_duplicate_exchange")

    return {
        "rank": None,
        "row_id": str(raw.get("row_id") or raw.get("finding_id") or f"{source_file.name}:{index}"),
        "venue_a": left.get("venue"),
        "source_platform_a": left.get("source_platform"),
        "access_platform_a": left.get("access_platform"),
        "exchange_venue_a": left.get("exchange_venue"),
        "executable_venue_a": left.get("executable_venue") or executable_venue_identity_from_mapping(left),
        "market_id_a": left.get("market_id"),
        "ticker_a": left.get("ticker"),
        "venue_b": right.get("venue"),
        "source_platform_b": right.get("source_platform"),
        "access_platform_b": right.get("access_platform"),
        "exchange_venue_b": right.get("exchange_venue"),
        "executable_venue_b": right.get("executable_venue") or executable_venue_identity_from_mapping(right),
        "market_id_b": right.get("market_id"),
        "ticker_b": right.get("ticker"),
        "relationship_class": relationship_class,
        "confidence_tier": _confidence_tier(raw, relationship_class, blockers),
        "diagnostic_only": True,
        "allowed_next_action": _allowed_next_action(relationship_class, blockers),
        "blockers": blockers,
        "evidence_summary": _evidence_summary(raw, exact_evidence_source, has_graph, has_similarity),
        "source_files": [str(source_file)],
        "reason_codes": reason_codes,
        "paper_candidate_emitted": False,
        "affects_evaluator_gates": False,
    }


def _graph_rows(graph_hints_path: Path | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if graph_hints_path is None:
        return [], []
    payload, warning = _load_json(graph_hints_path)
    if warning is not None:
        warning["reason_code"] = "graph_hints_file_missing" if warning["reason_code"] == "json_file_missing" else "graph_hints_unreadable"
        return [], [warning]
    graph_items = []
    if isinstance(payload, dict) and isinstance(payload.get("hints"), list):
        graph_items = payload["hints"]
    elif isinstance(payload, dict) and isinstance(payload.get("edges"), list):
        graph_items = payload["edges"]
    rows = []
    for index, item in enumerate(graph_items, start=1):
        if not isinstance(item, dict):
            continue
        normalized = dict(item)
        normalized["relationship_class"] = GRAPH_ADVISORY_CANDIDATE
        normalized["graph_advisory"] = True
        normalized.setdefault("row_id", normalized.get("finding_id") or f"graph_hint:{index}")
        normalized.setdefault("venue_a", normalized.get("source_venue"))
        normalized.setdefault("market_id_a", normalized.get("source_market_id"))
        normalized.setdefault("venue_b", normalized.get("target_venue"))
        normalized.setdefault("market_id_b", normalized.get("target_market_id"))
        normalized.setdefault("reason_codes", ["graph_hint_advisory_input"])
        row = _triage_row(normalized, source_file=graph_hints_path, index=index)
        if row is not None:
            rows.append(row)
    return rows, []


def _leg(raw: dict[str, Any], side_key: str, alt_key: str, graph_key: str, venue_key: str) -> dict[str, Any]:
    nested = raw.get(side_key)
    if not isinstance(nested, dict):
        nested = raw.get(alt_key)
    if not isinstance(nested, dict):
        nested = raw.get(f"market_{side_key}")
    if not isinstance(nested, dict):
        nested = raw.get(venue_key)
    if not isinstance(nested, dict):
        nested = {}
    suffix = f"_{side_key}"
    leg = {
        "venue": _first_str(
            nested.get("venue"),
            raw.get(f"venue{suffix}"),
            raw.get(f"venue_{side_key}"),
            raw.get(f"{graph_key}_venue"),
            venue_key if raw.get(venue_key) is not None else None,
        ),
        "source_platform": _first_str(
            nested.get("source_platform"),
            raw.get(f"source_platform{suffix}"),
            raw.get(f"source_platform_{side_key}"),
        ),
        "access_platform": _first_str(
            nested.get("access_platform"),
            raw.get(f"access_platform{suffix}"),
            raw.get(f"access_platform_{side_key}"),
        ),
        "exchange_venue": _first_str(
            nested.get("exchange_venue"),
            raw.get(f"exchange_venue{suffix}"),
            raw.get(f"exchange_venue_{side_key}"),
        ),
        "executable_venue": _first_str(
            nested.get("executable_venue"),
            raw.get(f"executable_venue{suffix}"),
            raw.get(f"executable_venue_{side_key}"),
        ),
        "market_id": _first_str(
            nested.get("market_id"),
            nested.get("id"),
            raw.get(f"market_id{suffix}"),
            raw.get(f"{graph_key}_market_id"),
        ),
        "ticker": _first_str(
            nested.get("ticker"),
            nested.get("market_ticker"),
            raw.get(f"ticker{suffix}"),
            raw.get(f"{graph_key}_ticker"),
        ),
    }
    leg["executable_venue"] = leg.get("executable_venue") or executable_venue_identity_from_mapping(leg)
    return leg


def _requested_relationship_class(raw: dict[str, Any]) -> str | None:
    values = [
        raw.get("relationship_class"),
        raw.get("candidate_class"),
        raw.get("relation_type"),
        raw.get("relationship"),
    ]
    relationship = raw.get("contract_relationship")
    if isinstance(relationship, dict):
        values.extend([relationship.get("relationship_class"), relationship.get("relationship"), relationship.get("relation_type")])
    for value in values:
        normalized = str(value or "").strip().upper()
        if not normalized:
            continue
        if normalized in RELATIONSHIP_CLASSES:
            return normalized
        if normalized in {"EQUIVALENT", "SAME_PAYOFF", "EXACT_SAME_PAYOFF", "EXACT"}:
            return EXACT_EQUALITY_CANDIDATE
        if normalized in {"COMPLEMENT", "MUTUALLY_EXCLUSIVE"}:
            return COMPLEMENT_CANDIDATE
        if normalized in {"SUBSET", "SUPERSET"}:
            return SUBSET_SUPERSET_CANDIDATE
        if normalized in {"THRESHOLD_LADDER", "MONOTONIC_THRESHOLD"}:
            return THRESHOLD_LADDER_CANDIDATE
    return None


def _exact_evidence_source(raw: dict[str, Any]) -> str | None:
    candidates: list[Any] = [raw.get("source"), raw.get("evidence_source")]
    for key in ("same_payoff_evidence", "contract_relationship", "typed_exact_key_evidence"):
        value = raw.get(key)
        if isinstance(value, dict):
            candidates.extend([value.get("source"), value.get("evidence_source")])
            if value.get("same_payoff") is True and value.get("source") is None:
                candidates.append(raw.get("source"))
    for candidate in candidates:
        normalized = str(candidate or "").strip()
        if normalized:
            return normalized
    return None


def _has_text_similarity(raw: dict[str, Any]) -> bool:
    text = json.dumps(raw, sort_keys=True).lower()
    return any(token in text for token in ("similarity", "title_match", "text_overlap", "fuzzy"))


def _has_graph_marker(raw: dict[str, Any]) -> bool:
    if raw.get("graph_advisory") is True or raw.get("info_only_hint") is True:
        return True
    source = str(raw.get("source") or raw.get("evidence_source") or "").lower()
    return "graph" in source


def _looks_like_ladder(raw: dict[str, Any]) -> bool:
    text = json.dumps(raw, sort_keys=True).lower()
    return any(token in text for token in ("threshold_ladder", "ladder", "threshold"))


def _looks_like_subset(raw: dict[str, Any]) -> bool:
    text = json.dumps(raw, sort_keys=True).lower()
    return "subset" in text or "superset" in text


def _looks_like_range_or_partition(raw: dict[str, Any]) -> bool:
    text = json.dumps(raw, sort_keys=True).lower()
    return "range" in text or "partition" in text


def _looks_like_stale_or_lag(raw: dict[str, Any]) -> bool:
    text = json.dumps(raw, sort_keys=True).lower()
    return any(token in text for token in ("stale", "lag", "quote_age"))


def _relation_text_is_exact_like(raw: dict[str, Any]) -> bool:
    values = [raw.get("relation_type"), raw.get("relationship"), raw.get("relationship_class")]
    relationship = raw.get("contract_relationship")
    if isinstance(relationship, dict):
        values.extend([relationship.get("relation_type"), relationship.get("relationship"), relationship.get("relationship_class")])
    return any(
        str(value or "").strip().upper() in {"EXACT", "EXACT_SAME_PAYOFF", "SAME_PAYOFF", "EQUIVALENT", EXACT_EQUALITY_CANDIDATE}
        for value in values
    )


def _confidence_tier(raw: dict[str, Any], relationship_class: str, blockers: list[str]) -> str:
    explicit = str(raw.get("confidence_tier") or "").strip().upper()
    if explicit:
        return explicit
    if relationship_class == EXACT_EQUALITY_CANDIDATE and "missing_explicit_typed_evidence" not in blockers:
        return "HIGH_DIAGNOSTIC"
    if relationship_class in {GRAPH_ADVISORY_CANDIDATE, SIMILARITY_ONLY_RESEARCH}:
        return "ADVISORY_ONLY"
    return "MEDIUM_DIAGNOSTIC" if len(blockers) <= 1 else "LOW_DIAGNOSTIC"


def _allowed_next_action(relationship_class: str, blockers: list[str]) -> str:
    if set(blockers) & set(IBKR_KALSHI_FAKE_EDGE_BLOCKERS):
        return "WATCH"
    if relationship_class == EXACT_EQUALITY_CANDIDATE and "missing_explicit_typed_evidence" not in blockers:
        return "RUN_EXACT_EVIDENCE_REVIEW"
    if relationship_class == BTC_BASIS_RISK_REVIEW:
        return "MANUAL_BASIS_RISK_REVIEW"
    if relationship_class == CRYPTO_RELATED_FV_WATCH:
        return "FAIR_VALUE_WATCH"
    if relationship_class in {GRAPH_ADVISORY_CANDIDATE, SIMILARITY_ONLY_RESEARCH}:
        return "WATCH"
    return "MANUAL_REVIEW"


def _evidence_summary(raw: dict[str, Any], exact_source: str | None, has_graph: bool, has_similarity: bool) -> str:
    if exact_source is not None:
        return f"explicit_evidence_source={exact_source}"
    if has_graph:
        return "graph advisory input only; independent exact-payoff evidence required"
    if has_similarity:
        return "text/title similarity only; not settlement equivalence"
    return str(raw.get("evidence_summary") or "untyped saved-file diagnostic row")


def _dedupe_pairs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Same logical (venue, leg, leg) pair often appears in multiple saved files
    # (snapshot + enriched + paper-hit replay etc). Merge into one row so counts
    # reflect logical opportunities, not file copies. Order-insensitive on legs.
    priority = {
        EXACT_EQUALITY_CANDIDATE: 0,
        COMPLEMENT_CANDIDATE: 1,
        SUBSET_SUPERSET_CANDIDATE: 2,
        THRESHOLD_LADDER_CANDIDATE: 3,
        RANGE_OR_PARTITION_CANDIDATE: 4,
        BTC_BASIS_RISK_REVIEW: 5,
        CRYPTO_RELATED_FV_WATCH: 6,
        STALE_OR_LAG_CANDIDATE: 7,
        GRAPH_ADVISORY_CANDIDATE: 8,
        SIMILARITY_ONLY_RESEARCH: 9,
    }

    def leg_key(venue: Any, market_id: Any, ticker: Any) -> tuple[str, str]:
        return (str(venue or ""), str(market_id or ticker or ""))

    def pair_key(row: dict[str, Any]) -> tuple[tuple[str, str], tuple[str, str]] | None:
        a = leg_key(row.get("venue_a"), row.get("market_id_a"), row.get("ticker_a"))
        b = leg_key(row.get("venue_b"), row.get("market_id_b"), row.get("ticker_b"))
        if not (a[0] and a[1] and b[0] and b[1]):
            return None
        return tuple(sorted((a, b)))  # type: ignore[return-value]

    merged: dict[tuple[tuple[str, str], tuple[str, str]], dict[str, Any]] = {}
    standalone: list[dict[str, Any]] = []
    for row in rows:
        key = pair_key(row)
        if key is None:
            standalone.append(row)
            continue
        existing = merged.get(key)
        if existing is None:
            merged[key] = dict(row)
            continue
        existing_priority = priority.get(str(existing.get("relationship_class")), 99)
        candidate_priority = priority.get(str(row.get("relationship_class")), 99)
        if candidate_priority < existing_priority or (
            candidate_priority == existing_priority
            and len(row.get("blockers") or []) < len(existing.get("blockers") or [])
        ):
            new_sources = list(existing.get("source_files") or [])
            for path in row.get("source_files") or []:
                if path not in new_sources:
                    new_sources.append(path)
            merged[key] = dict(row)
            merged[key]["source_files"] = new_sources
        else:
            for path in row.get("source_files") or []:
                if path not in (existing.get("source_files") or []):
                    existing.setdefault("source_files", []).append(path)
    return list(merged.values()) + standalone


def _rank_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    priority = {
        EXACT_EQUALITY_CANDIDATE: 0,
        COMPLEMENT_CANDIDATE: 1,
        SUBSET_SUPERSET_CANDIDATE: 2,
        THRESHOLD_LADDER_CANDIDATE: 3,
        RANGE_OR_PARTITION_CANDIDATE: 4,
        BTC_BASIS_RISK_REVIEW: 5,
        CRYPTO_RELATED_FV_WATCH: 6,
        STALE_OR_LAG_CANDIDATE: 7,
        GRAPH_ADVISORY_CANDIDATE: 8,
        SIMILARITY_ONLY_RESEARCH: 9,
    }
    ranked = sorted(
        rows,
        key=lambda row: (
            priority.get(str(row.get("relationship_class")), 99),
            len(row.get("blockers") or []),
            str(row.get("venue_a") or ""),
            str(row.get("market_id_a") or row.get("ticker_a") or ""),
        ),
    )
    for index, row in enumerate(ranked, start=1):
        row["rank"] = index
    return ranked


def _summary(rows: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    by_class = Counter(str(row.get("relationship_class")) for row in rows)
    blockers = Counter(blocker for row in rows for blocker in row.get("blockers") or [])
    return {
        "row_count": len(rows),
        "paper_candidate_count": 0,
        "diagnostic_only_count": sum(1 for row in rows if row.get("diagnostic_only") is True),
        "relationship_class_counts": dict(sorted(by_class.items())),
        "top_blockers": [{"blocker": key, "count": value} for key, value in blockers.most_common(10)],
        "warning_count": len(warnings),
        "warning_reason_codes": dict(sorted(Counter(str(item.get("reason_code")) for item in warnings).items())),
    }


def _load_json(path: Path) -> tuple[Any, dict[str, Any] | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, {"source_file": str(path), "reason_code": "json_file_missing", "blocker": "saved_json_file_missing"}
    except json.JSONDecodeError:
        return None, {"source_file": str(path), "reason_code": "invalid_json", "blocker": "saved_json_invalid"}
    except OSError as exc:
        return None, {"source_file": str(path), "reason_code": "json_read_error", "blocker": f"saved_json_read_error:{type(exc).__name__}"}


def _write_csv(rows: list[dict[str, Any]], csv_output: Path) -> None:
    with csv_output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    field: _csv_value(row.get(field))
                    for field in CSV_FIELDS
                }
            )


def _csv_value(value: Any) -> Any:
    if isinstance(value, list):
        return ";".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    return value


def _has_leg_id(leg: dict[str, Any]) -> bool:
    return bool(leg.get("venue") and (leg.get("market_id") or leg.get("ticker")))


def _first_str(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
